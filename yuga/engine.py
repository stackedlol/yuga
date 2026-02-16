"""Core engine: orchestrates ingestion, strategy, execution, and risk."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Awaitable

from yuga.config import AppConfig
from yuga.db import Database
from yuga.execution.controller import ExecutionController, PipelineStage
from yuga.ingestion.clob_client import CLOBClient
from yuga.ingestion.ws_client import WebSocketClient
from yuga.risk.manager import RiskManager
from yuga.strategy.arbitrage import ArbitrageEngine, MarketState

logger = logging.getLogger("yuga.engine")


class Engine:
    """Main bot engine orchestrating all subsystems."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.db = Database(config.database.path)
        self.clob = CLOBClient(
            base_url=config.polymarket.clob_base_url,
            api_key=config.polymarket.api_key,
            api_secret=config.polymarket.api_secret,
            api_passphrase=config.polymarket.api_passphrase,
            funder=config.polymarket.funder,
        )
        self.ws = WebSocketClient(
            ws_url=config.polymarket.ws_url,
            on_book_update=self._on_book_update,
        )
        self.risk = RiskManager(config.risk, self.db)
        self.arb = ArbitrageEngine(
            min_spread_bps=config.strategy.min_spread_bps,
            min_liquidity=config.strategy.min_liquidity_usdc,
        )
        self.executor = ExecutionController(
            clob=self.clob, risk=self.risk, db=self.db,
            order_size=config.strategy.order_size_usdc,
            max_order_size=config.strategy.max_order_size_usdc,
            order_timeout_ms=config.execution.order_timeout_ms,
            cancel_stale_ms=config.execution.cancel_stale_after_ms,
        )
        self._running = False
        self._scan_task: asyncio.Task | None = None
        self._discovery_task: asyncio.Task | None = None
        self._event_listeners: list[Callable[[str, Any], Awaitable[None]]] = []
        self._start_time = 0.0
        self._log_buffer: list[dict] = []

    def add_event_listener(self, cb: Callable[[str, Any], Awaitable[None]]) -> None:
        self._event_listeners.append(cb)

    async def _emit(self, event: str, data: Any = None) -> None:
        for cb in self._event_listeners:
            try:
                await cb(event, data)
            except Exception as e:
                logger.debug("Event listener error: %s", e)

    async def start(self) -> None:
        logger.info("Engine starting...")
        self._start_time = time.time()
        await self.db.connect()
        await self.clob.start()
        await self.ws.start()
        self._running = True
        self._discovery_task = asyncio.create_task(self._market_discovery_loop())
        self._scan_task = asyncio.create_task(self._scan_loop())
        await self.db.log_event("ENGINE_START", "")
        await self._emit("engine_started")
        logger.info("Engine started")

    async def stop(self) -> None:
        logger.info("Engine stopping...")
        self._running = False
        if self._scan_task:
            self._scan_task.cancel()
        if self._discovery_task:
            self._discovery_task.cancel()
        await self.executor.cancel_all()
        await self.ws.stop()
        await self.clob.stop()
        await self.db.log_event("ENGINE_STOP", "")
        await self.db.close()
        await self._emit("engine_stopped")
        logger.info("Engine stopped")

    async def pause(self) -> None:
        self.executor.paused = True
        await self.db.log_event("ENGINE_PAUSE", "")
        await self._emit("engine_paused")

    async def resume(self) -> None:
        self.executor.paused = False
        self.risk.reset_circuit_breaker()
        await self.db.log_event("ENGINE_RESUME", "")
        await self._emit("engine_resumed")

    async def reload_config(self, path: str = "config.yaml") -> None:
        from yuga.config import load_config
        new_cfg = load_config(path)
        self.config = new_cfg
        self.arb.min_spread_bps = new_cfg.strategy.min_spread_bps
        self.arb.min_liquidity = new_cfg.strategy.min_liquidity_usdc
        self.executor.order_size = new_cfg.strategy.order_size_usdc
        self.executor.max_order_size = new_cfg.strategy.max_order_size_usdc
        await self.db.log_event("CONFIG_RELOAD", "")
        await self._emit("config_reloaded")

    async def cancel_all(self) -> int:
        n = await self.executor.cancel_all()
        await self._emit("orders_cancelled", n)
        return n

    # -- Market Discovery --

    async def _market_discovery_loop(self) -> None:
        """Periodically discover and subscribe to new markets."""
        while self._running:
            try:
                self.executor.pipeline_stage = PipelineStage.SCANNING
                await self._discover_markets()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Market discovery error: %s", e)
                self.add_log("ERROR", f"Discovery: {e}")
            await asyncio.sleep(60)  # Re-discover every 60s

    async def _discover_markets(self) -> None:
        """Fetch active binary markets from Gamma API and set up tracking.

        NOTE: The CLOB /markets endpoint contains many non-binary / non-orderbook markets.
        Gamma exposes `enableOrderBook` + `clobTokenIds` for tradable YES/NO markets.
        """
        try:
            markets_data = await self.clob.get_gamma_markets(
                gamma_url=self.config.polymarket.gamma_url,
                limit=max(self.config.strategy.max_markets * 5, 200),
                active=True,
                closed=False,
            )
        except Exception as e:
            logger.error("Failed to fetch Gamma markets: %s", e)
            return

        count = 0

        for m in markets_data:
            if count >= self.config.strategy.max_markets:
                break

            if not m.get("enableOrderBook") or not m.get("acceptingOrders", True):
                continue

            condition_id = m.get("conditionId", "")
            if not condition_id or condition_id in self.arb.markets:
                continue

            # Gamma returns outcomes + clobTokenIds as JSON-encoded arrays
            try:
                outcomes = json.loads(m.get("outcomes", "[]"))
                token_ids = json.loads(m.get("clobTokenIds", "[]"))
            except Exception:
                continue

            if len(outcomes) != 2 or len(token_ids) != 2:
                continue
            if set(outcomes) != {"Yes", "No"}:
                continue

            # Map token IDs to YES/NO based on outcomes order
            yes_idx = outcomes.index("Yes")
            no_idx = outcomes.index("No")
            yes_token_id = token_ids[yes_idx]
            no_token_id = token_ids[no_idx]

            market_state = MarketState(
                market_id=m.get("slug", condition_id[:12]),
                condition_id=condition_id,
                question=m.get("question", m.get("slug", "Unknown")),
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
            )
            self.arb.add_market(market_state)

            # Subscribe to WS updates
            await self.ws.subscribe(condition_id, [
                market_state.yes_token_id,
                market_state.no_token_id,
            ])

            # Fetch initial order book snapshots
            try:
                yes_book, no_book = await asyncio.gather(
                    self.clob.get_order_book(market_state.yes_token_id),
                    self.clob.get_order_book(market_state.no_token_id),
                    return_exceptions=True,
                )
                if not isinstance(yes_book, Exception):
                    self.arb.update_book(market_state.yes_token_id, yes_book)
                if not isinstance(no_book, Exception):
                    self.arb.update_book(market_state.no_token_id, no_book)
            except Exception as e:
                logger.debug("Initial book fetch failed for %s: %s", condition_id[:8], e)

            count += 1

        await self._emit("markets_updated", self.arb.stats)
        self.add_log("INFO", f"Tracking {len(self.arb.markets)} markets")

    async def _on_book_update(self, update: dict) -> None:
        """Handle real-time order book updates from WebSocket."""
        asset_id = update.get("asset_id", "")
        if not asset_id:
            return

        # Build book data from update
        book_data = {}
        if "bids" in update:
            book_data["bids"] = update["bids"]
        if "asks" in update:
            book_data["asks"] = update["asks"]
        if "buys" in update:
            book_data["bids"] = update["buys"]
        if "sells" in update:
            book_data["asks"] = update["sells"]

        if book_data:
            self.arb.update_book(asset_id, book_data)

    # -- Strategy Scan Loop --

    async def _scan_loop(self) -> None:
        """Main loop: scan for arb opportunities and execute."""
        interval = self.config.strategy.scan_interval_ms / 1000
        while self._running:
            try:
                if not self.executor.paused:
                    self.executor.pipeline_stage = PipelineStage.SCANNING
                    signals = self.arb.scan_all()

                    if signals:
                        self.executor.pipeline_stage = PipelineStage.CANDIDATE
                        await self._emit("signals_detected", [
                            {"id": s.id, "market": s.market_id,
                             "type": s.signal_type, "spread_bps": s.spread_bps,
                             "cost": s.combined_cost}
                            for s in signals
                        ])

                        for signal in signals:
                            cycle = await self.executor.execute_signal(signal)
                            if cycle:
                                await self._emit("cycle_complete", {
                                    "id": cycle.id, "status": cycle.status,
                                    "pnl": cycle.pnl,
                                })
                    else:
                        self.executor.pipeline_stage = PipelineStage.IDLE

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Scan loop error: %s", e)
                self.add_log("ERROR", f"Scan: {e}")

            await asyncio.sleep(interval)

    # -- Log Buffer --

    def add_log(self, level: str, message: str) -> None:
        entry = {"ts": time.time(), "level": level, "msg": message}
        self._log_buffer.append(entry)
        if len(self._log_buffer) > 500:
            self._log_buffer = self._log_buffer[-500:]

    @property
    def recent_logs(self) -> list[dict]:
        return list(self._log_buffer[-100:])

    # -- State Snapshot for TUI --

    def get_state(self) -> dict[str, Any]:
        """Return full state snapshot for TUI rendering."""
        return {
            "running": self._running,
            "paused": self.executor.paused,
            "uptime_s": time.time() - self._start_time if self._start_time else 0,
            "pipeline_stage": self.executor.pipeline_stage.value,
            "arb_stats": self.arb.stats,
            "exec_stats": self.executor.stats,
            "risk_status": self.risk.status,
            "ws_state": {
                "connected": self.ws.state.connected,
                "latency_ms": self.ws.state.latency_ms,
                "reconnects": self.ws.state.reconnect_count,
                "subscribed": len(self.ws.state.subscribed_assets),
                "error": self.ws.state.error,
                "last_msg_age_s": (time.time() - self.ws.state.last_message_at
                                   if self.ws.state.last_message_at else 0),
            },
            "clob_latency_ms": self.clob.last_latency_ms,
            "active_signals": {
                cid: {
                    "market": s.market_id,
                    "type": s.signal_type,
                    "spread_bps": s.spread_bps,
                    "yes_price": s.yes_price,
                    "no_price": s.no_price,
                    "max_size": s.max_size,
                }
                for cid, s in self.arb.active_signals.items()
            },
            "recent_orders": [
                {
                    "id": o.id[:8], "side": o.side, "outcome": o.outcome,
                    "price": o.price, "size": o.size, "filled": o.filled_size,
                    "status": o.status, "latency_ms": o.latency_ms,
                    "age_s": time.time() - o.created_at,
                }
                for o in self.executor.recent_orders[-30:]
            ],
            "markets": [
                {
                    "id": m.condition_id[:8],
                    "question": m.question[:50],
                    "yes_bid": m.yes_book.best_bid if m.yes_book else 0,
                    "yes_ask": m.yes_book.best_ask if m.yes_book else 0,
                    "no_bid": m.no_book.best_bid if m.no_book else 0,
                    "no_ask": m.no_book.best_ask if m.no_book else 0,
                    "spread": ((m.yes_book.best_ask if m.yes_book else 0) +
                               (m.no_book.best_ask if m.no_book else 0)),
                    "ready": m.is_ready,
                }
                for m in list(self.arb.markets.values())[:20]
            ],
            "pnl_history": self.executor.pnl_history[-100:],
        }
