"""Core engine: orchestrates ingestion, strategy, execution, and risk."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from typing import Any, Callable, Awaitable

from yuga.config import AppConfig
from yuga.db import Database
from yuga.execution.controller import ExecutionController, PipelineStage
from yuga.ingestion.clob_client import CLOBClient
from yuga.ingestion.ws_client import WebSocketClient
from yuga.risk.manager import RiskManager
from yuga.strategy.market_maker import MarketMakerEngine, MarketState

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
        self.mm = MarketMakerEngine(
            quote_spread_bps=config.strategy.quote_spread_bps,
            min_liquidity=config.strategy.min_liquidity_usdc,
            price_staleness_ms=config.strategy.price_staleness_ms,
        )
        self.executor = ExecutionController(
            clob=self.clob, risk=self.risk, db=self.db,
            order_size=config.strategy.order_size_usdc,
            max_order_size=config.strategy.max_order_size_usdc,
            order_timeout_ms=config.execution.order_timeout_ms,
            cancel_stale_ms=config.execution.cancel_stale_after_ms,
            quote_refresh_ms=config.strategy.quote_refresh_ms,
            quote_ttl_ms=config.strategy.quote_ttl_ms,
            reprice_threshold_bps=config.strategy.reprice_threshold_bps,
        )
        self._running = False
        self._scan_task: asyncio.Task | None = None
        self._discovery_task: asyncio.Task | None = None
        self._event_listeners: list[Callable[[str, Any], Awaitable[None]]] = []
        self._start_time = 0.0
        self._log_buffer: list[dict] = []
        self._ob_selected_condition_id = ""
        self._ob_selected_until = 0.0
        self._ob_rotate_idx = 0
        self._ob_auto_rotate = bool(config.strategy.orderbook_auto_rotate)
        self._last_book_refresh_at = 0.0
        self._odds_history: dict[str, deque[tuple[float, float, float]]] = defaultdict(
            lambda: deque(maxlen=240)
        )

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
        await self.executor.load_positions()
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
        self.mm.quote_spread_bps = new_cfg.strategy.quote_spread_bps
        self.mm.min_liquidity = new_cfg.strategy.min_liquidity_usdc
        self.mm.price_staleness_ms = new_cfg.strategy.price_staleness_ms
        self.executor.order_size = new_cfg.strategy.order_size_usdc
        self.executor.max_order_size = new_cfg.strategy.max_order_size_usdc
        self.executor.quote_refresh_ms = new_cfg.strategy.quote_refresh_ms
        self.executor.quote_ttl_ms = new_cfg.strategy.quote_ttl_ms
        self.executor.reprice_threshold_bps = new_cfg.strategy.reprice_threshold_bps
        self._ob_auto_rotate = bool(new_cfg.strategy.orderbook_auto_rotate)
        await self.db.log_event("CONFIG_RELOAD", "")
        await self._emit("config_reloaded")

    async def cancel_all(self) -> int:
        n = await self.executor.cancel_all()
        await self._emit("orders_cancelled", n)
        return n

    def _available_orderbook_markets(self) -> list[MarketState]:
        markets = [
            m for m in self.mm.markets.values()
            if m.yes_book is not None and m.no_book is not None
        ]
        markets.sort(key=lambda m: m.condition_id)
        return markets

    def _set_orderbook_selected(self, market: MarketState) -> None:
        self._ob_selected_condition_id = market.condition_id
        dwell_s = max(1.0, float(self.config.strategy.orderbook_dwell_s))
        self._ob_selected_until = time.time() + dwell_s

    def cycle_orderbook(self, step: int = 1) -> bool:
        available = self._available_orderbook_markets()
        if not available:
            return False
        current_idx = next(
            (i for i, m in enumerate(available)
             if m.condition_id == self._ob_selected_condition_id),
            -1,
        )
        if current_idx < 0:
            current_idx = 0
        next_idx = (current_idx + step) % len(available)
        self._set_orderbook_selected(available[next_idx])
        return True

    def set_orderbook_auto_rotate(self, enabled: bool) -> None:
        self._ob_auto_rotate = enabled

    def toggle_orderbook_auto_rotate(self) -> bool:
        self._ob_auto_rotate = not self._ob_auto_rotate
        return self._ob_auto_rotate

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
            if self.config.strategy.require_fee_enabled:
                fee_enabled = bool(m.get("feeEnabled")) or bool(m.get("takerFee"))
                if not fee_enabled:
                    continue

            condition_id = m.get("conditionId", "")
            if not condition_id or condition_id in self.mm.markets:
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
            self.mm.add_market(market_state)

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
                    self.mm.update_book(market_state.yes_token_id, yes_book)
                if not isinstance(no_book, Exception):
                    self.mm.update_book(market_state.no_token_id, no_book)
            except Exception as e:
                logger.debug("Initial book fetch failed for %s: %s", condition_id[:8], e)

            count += 1

        await self._emit("markets_updated", self.mm.stats)
        self.add_log("INFO", f"Tracking {len(self.mm.markets)} markets")

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
            self.mm.update_book(asset_id, book_data)

    # -- Strategy Scan Loop --

    async def _scan_loop(self) -> None:
        """Main loop: generate quotes and keep them fresh."""
        interval = self.config.strategy.scan_interval_ms / 1000
        while self._running:
            try:
                # Keep book backfill running even while quoting is paused.
                await self._refresh_stale_books()
                if not self.executor.paused:
                    self.executor.pipeline_stage = PipelineStage.SCANNING
                    signals = self.mm.generate_quotes(
                        inventory=self.executor.inventory_by_condition(),
                        inventory_limit=self.risk.config.position_limit_per_outcome,
                    )
                    if signals:
                        self.executor.pipeline_stage = PipelineStage.QUOTING
                        await self.executor.sync_quotes(signals)
                    await self.executor.refresh_open_orders()
                    self.executor.pipeline_stage = PipelineStage.IDLE

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Scan loop error: %s", e)
                self.add_log("ERROR", f"Scan: {e}")

            await asyncio.sleep(interval)

    async def _refresh_stale_books(self) -> None:
        """Backfill books via REST when WS updates lag to keep UI/strategy fed."""
        now = time.time()
        # Keep backfill rate low; WS should remain the primary source.
        if now - self._last_book_refresh_at < 3.0:
            return
        self._last_book_refresh_at = now

        stale_cutoff_s = max(2.0, self.config.strategy.price_staleness_ms / 1000)

        def _book_age_s(m: MarketState) -> float:
            yes_age = now - m.yes_book.timestamp if m.yes_book else 1e9
            no_age = now - m.no_book.timestamp if m.no_book else 1e9
            return max(yes_age, no_age)

        candidates = sorted(
            (m for m in self.mm.markets.values() if _book_age_s(m) >= stale_cutoff_s),
            key=_book_age_s,
            reverse=True,
        )[:3]
        if not candidates:
            return

        for m in candidates:
            try:
                yes_book, no_book = await asyncio.gather(
                    self.clob.get_order_book(m.yes_token_id),
                    self.clob.get_order_book(m.no_token_id),
                    return_exceptions=True,
                )
                if not isinstance(yes_book, Exception):
                    self.mm.update_book(m.yes_token_id, yes_book)
                if not isinstance(no_book, Exception):
                    self.mm.update_book(m.no_token_id, no_book)
            except Exception:
                continue

    # -- Log Buffer --

    def add_log(self, level: str, message: str) -> None:
        entry = {"ts": time.time(), "level": level, "msg": message}
        self._log_buffer.append(entry)
        if len(self._log_buffer) > 500:
            self._log_buffer = self._log_buffer[-500:]

    @property
    def recent_logs(self) -> list[dict]:
        return list(self._log_buffer[-100:])

    def _capture_odds_samples(self) -> None:
        now = time.time()
        for m in self.mm.markets.values():
            if not m.yes_book or not m.no_book:
                continue
            yes_mid = float(m.yes_book.mid)
            no_mid = float(m.no_book.mid)
            if yes_mid <= 0 or no_mid <= 0:
                continue
            hist = self._odds_history[m.condition_id]
            if not hist:
                hist.append((now, yes_mid, no_mid))
                continue
            last_ts, last_yes, last_no = hist[-1]
            if (now - last_ts) >= 1.0 or abs(yes_mid - last_yes) >= 0.0005 or abs(no_mid - last_no) >= 0.0005:
                hist.append((now, yes_mid, no_mid))

    def _odds_view_from_orderbook(self, ob_view: dict[str, Any]) -> dict[str, Any]:
        condition_id = ob_view.get("condition_id", "")
        if not condition_id:
            return {
                "condition_id": "",
                "question": "",
                "yes": [],
                "no": [],
                "yes_now": 0.0,
                "no_now": 0.0,
                "is_live": False,
                "stale_age_s": 0.0,
                "samples": 0,
            }
        hist = list(self._odds_history.get(condition_id, []))[-120:]
        yes = [round(v[1] * 100, 2) for v in hist]
        no = [round(v[2] * 100, 2) for v in hist]
        yes_now = float(ob_view.get("yes_mid", 0.0)) * 100
        no_now = float(ob_view.get("no_mid", 0.0)) * 100
        if not yes and yes_now > 0:
            yes = [round(yes_now, 2)]
        if not no and no_now > 0:
            no = [round(no_now, 2)]
        return {
            "condition_id": condition_id,
            "question": ob_view.get("question", ""),
            "yes": yes,
            "no": no,
            "yes_now": round(yes_now, 2),
            "no_now": round(no_now, 2),
            "is_live": bool(ob_view.get("is_live", False)),
            "stale_age_s": float(ob_view.get("stale_age_s", 0.0)),
            "samples": len(hist),
        }

    # -- State Snapshot for TUI --

    def get_state(self) -> dict[str, Any]:
        """Return full state snapshot for TUI rendering."""
        self._capture_odds_samples()
        ob_view = self._orderbook_view()
        return {
            "running": self._running,
            "paused": self.executor.paused,
            "uptime_s": time.time() - self._start_time if self._start_time else 0,
            "pipeline_stage": self.executor.pipeline_stage.value,
            "mm_stats": self.mm.stats,
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
            "active_quotes": {
                cid[:8]: {
                    "market": s.market_id,
                    "spread_bps": s.spread_bps,
                    "mid_yes": s.mid_yes,
                    "mid_no": s.mid_no,
                    "max_size": s.max_size,
                }
                for cid, s in self.mm.active_quotes.items()
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
                    "yes_mid": m.yes_book.mid if m.yes_book else 0,
                    "no_mid": m.no_book.mid if m.no_book else 0,
                    "spread_bps": (m.yes_book.spread_bps if m.yes_book else 0),
                    "ready": m.is_ready(self.config.strategy.price_staleness_ms),
                }
                for m in list(self.mm.markets.values())[:20]
            ],
            "orderbook_view": ob_view,
            "odds_view": self._odds_view_from_orderbook(ob_view),
            "inventory": self.executor.inventory_summary(),
            "pnl_history": self.executor.pnl_history[-100:],
        }

    def _orderbook_view(self) -> dict[str, Any]:
        now = time.time()
        dwell_s = max(1.0, float(self.config.strategy.orderbook_dwell_s))
        ready = [
            m for m in self.mm.markets.values()
            if m.is_ready(self.config.strategy.price_staleness_ms)
        ]
        available = self._available_orderbook_markets()
        ready.sort(key=lambda m: m.condition_id)
        if available:
            selected: MarketState | None = next(
                (m for m in available if m.condition_id == self._ob_selected_condition_id),
                None,
            )
            if selected is None:
                self._ob_rotate_idx %= len(available)
                selected = available[self._ob_rotate_idx]
                self._set_orderbook_selected(selected)

            # Auto mode rotates across fresh books on dwell timer.
            # Manual mode keeps the selected book until the user cycles it.
            if self._ob_auto_rotate and ready:
                selected_ready = (
                    selected is not None
                    and any(m.condition_id == selected.condition_id for m in ready)
                )
                if not selected_ready or now >= self._ob_selected_until:
                    if selected_ready and len(ready) > 1:
                        idx = next(i for i, m in enumerate(ready)
                                   if m.condition_id == selected.condition_id)
                        selected = ready[(idx + 1) % len(ready)]
                    else:
                        self._ob_rotate_idx %= len(ready)
                        selected = ready[self._ob_rotate_idx]
                        self._ob_rotate_idx = (self._ob_rotate_idx + 1) % len(ready)
                    self._set_orderbook_selected(selected)

            assert selected is not None
            is_live = selected.is_ready(self.config.strategy.price_staleness_ms)
            stale_age_s = max(
                now - selected.yes_book.timestamp,
                now - selected.no_book.timestamp,
            )
            selected_pos = next(
                (i for i, m in enumerate(available)
                 if m.condition_id == selected.condition_id),
                0,
            )

            quotes = self.executor.active_quotes_for(selected.condition_id)
            return {
                "market_id": selected.market_id,
                "condition_id": selected.condition_id,
                "question": selected.question,
                "yes_mid": selected.yes_book.mid if selected.yes_book else 0.0,
                "no_mid": selected.no_book.mid if selected.no_book else 0.0,
                "yes_bids": (selected.yes_book.bids[:5] if selected.yes_book else []),
                "yes_asks": (selected.yes_book.asks[:5] if selected.yes_book else []),
                "no_bids": (selected.no_book.bids[:5] if selected.no_book else []),
                "no_asks": (selected.no_book.asks[:5] if selected.no_book else []),
                "quotes": quotes,
                "rotate_in_s": (
                    max(0.0, self._ob_selected_until - now) if self._ob_auto_rotate else 0.0
                ),
                "rotate_mode": "AUTO" if self._ob_auto_rotate else "MANUAL",
                "book_pos": selected_pos + 1,
                "book_total": len(available),
                "ready_total": len(ready),
                "is_live": is_live,
                "stale_age_s": stale_age_s,
            }
        self._ob_selected_condition_id = ""
        self._ob_selected_until = 0.0
        return {"market_id": "", "condition_id": "", "question": "", "quotes": []}
