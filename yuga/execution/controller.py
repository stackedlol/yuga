"""Execution controller: manages order lifecycle from signal to fill/cancel."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from yuga.db import Database
from yuga.ingestion.clob_client import CLOBClient
from yuga.risk.manager import RiskManager
from yuga.strategy.market_maker import QuoteSignal, QuoteOrder

logger = logging.getLogger("yuga.execution")


class PipelineStage(str, Enum):
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    BOOK = "BOOK"
    QUOTING = "QUOTING"
    MONITORING = "MONITORING"
    RESOLVING = "RESOLVING"


@dataclass
class OrderRecord:
    id: str
    market_id: str
    condition_id: str
    token_id: str
    side: str
    outcome: str
    price: float
    size: float
    filled_size: float = 0
    status: str = "PENDING"
    created_at: float = field(default_factory=time.time)
    latency_ms: float = 0
    arb_cycle_id: str = ""


class ExecutionController:
    """Manages quote placement, cancel/replace, and fill tracking."""

    def __init__(self, clob: CLOBClient, risk: RiskManager, db: Database,
                 order_size: float = 10.0, max_order_size: float = 100.0,
                 order_timeout_ms: int = 5000, cancel_stale_ms: int = 3000,
                 quote_refresh_ms: int = 2000, quote_ttl_ms: int = 15000,
                 reprice_threshold_bps: int = 5):
        self.clob = clob
        self.risk = risk
        self.db = db
        self.order_size = order_size
        self.max_order_size = max_order_size
        self.order_timeout_ms = order_timeout_ms
        self.cancel_stale_ms = cancel_stale_ms
        self.quote_refresh_ms = quote_refresh_ms
        self.quote_ttl_ms = quote_ttl_ms
        self.reprice_threshold_bps = reprice_threshold_bps
        self.pipeline_stage = PipelineStage.IDLE
        self.active_quotes: dict[str, OrderRecord] = {}
        self.recent_orders: list[OrderRecord] = []
        self.paused = False
        self._total_orders = 0
        self._total_fills = 0
        self._total_rejects = 0
        self._total_cancels = 0
        self._cumulative_latency = 0.0
        self._pnl_history: list[tuple[float, float]] = []  # (timestamp, cumulative_pnl)
        self._cumulative_pnl = 0.0
        self._spread_capture_pnl = 0.0
        self._liquidity_rewards = 0.0
        self._positions: dict[tuple[str, str], dict[str, float | str]] = {}
        self._last_refresh = 0.0

    async def sync_quotes(self, signals: list[QuoteSignal]) -> None:
        """Cancel/replace quotes to match desired signals."""
        if self.paused:
            return

        for signal in signals:
            size = min(self.order_size, signal.max_size, self.max_order_size)
            if size <= 0:
                continue
            adjusted = QuoteSignal(
                id=signal.id,
                market_id=signal.market_id,
                condition_id=signal.condition_id,
                spread_bps=signal.spread_bps,
                mid_yes=signal.mid_yes,
                mid_no=signal.mid_no,
                orders=[
                    QuoteOrder(o.token_id, o.outcome, o.side, o.price, size)
                    for o in signal.orders
                ],
                max_size=size,
            )

            allowed, reason = await self.risk.check_signal(adjusted)
            if not allowed:
                logger.info("Quote %s rejected by risk: %s", signal.id, reason)
                await self.db.log_event("QUOTE_REJECTED", f"{signal.id}: {reason}")
                continue

            self.pipeline_stage = PipelineStage.QUOTING
            desired_keys: set[str] = set()

            for order in adjusted.orders:

                key = self._quote_key(adjusted.condition_id, order.token_id, order.side)
                desired_keys.add(key)
                existing = self.active_quotes.get(key)

                if existing and self._should_keep(existing, order):
                    continue

                if existing:
                    await self._cancel_order(existing)

                record = await self._place_order(
                    adjusted.id, adjusted.market_id, adjusted.condition_id,
                    order.token_id, order.side, order.outcome, order.price, order.size,
                )
                self.active_quotes[key] = record

            # Cancel quotes no longer desired for this market
            stale_keys = [
                k for k in self.active_quotes
                if k.startswith(f"{adjusted.condition_id}:") and k not in desired_keys
            ]
            for key in stale_keys:
                await self._cancel_order(self.active_quotes[key])
                self.active_quotes.pop(key, None)

        self.pipeline_stage = PipelineStage.MONITORING

    async def load_positions(self) -> None:
        rows = await self.db.get_positions()
        for row in rows:
            key = (row["condition_id"], row["outcome"])
            self._positions[key] = {
                "size": float(row["size"]),
                "avg_price": float(row["avg_price"]),
                "market_id": row.get("market_id", ""),
            }

    def _quote_key(self, condition_id: str, token_id: str, side: str) -> str:
        return f"{condition_id}:{token_id}:{side}"

    def _should_keep(self, existing: OrderRecord, desired: QuoteOrder) -> bool:
        if existing.status not in ("OPEN", "PARTIAL"):
            return False
        if abs(existing.size - desired.size) > 1e-6:
            return False
        if existing.price <= 0:
            return False
        drift_bps = abs(existing.price - desired.price) / existing.price * 10000
        if drift_bps >= self.reprice_threshold_bps:
            return False
        if time.time() - existing.created_at > (self.quote_ttl_ms / 1000):
            return False
        return True

    async def _cancel_order(self, order: OrderRecord) -> None:
        try:
            await self.clob.cancel_order(order.id)
            order.status = "CANCELLED"
            self._total_cancels += 1
            await self.db.update_order_status(order.id, "CANCELLED", order.filled_size)
            await self.db.insert_quote_event({
                "order_id": order.id,
                "market_id": order.market_id,
                "condition_id": order.condition_id,
                "outcome": order.outcome,
                "side": order.side,
                "price": order.price,
                "size": order.size,
                "action": "CANCEL",
            })
        except Exception as e:
            logger.warning("Cancel failed for %s: %s", order.id, e)

    async def _place_order(self, cycle_id: str, market_id: str, condition_id: str,
                           token_id: str, side: str, outcome: str,
                           price: float, size: float) -> OrderRecord:
        order_id = str(uuid.uuid4())[:12]
        record = OrderRecord(
            id=order_id, market_id=market_id, condition_id=condition_id,
            token_id=token_id, side=side, outcome=outcome,
            price=price, size=size, arb_cycle_id=cycle_id,
        )

        t0 = time.monotonic()
        try:
            resp = await self.clob.post_order({
                "tokenID": token_id,
                "price": price,
                "size": size,
                "side": side,
                "type": "GTC",  # Good Till Cancel
            })
            record.latency_ms = (time.monotonic() - t0) * 1000
            self._cumulative_latency += record.latency_ms

            if resp.get("orderID") or resp.get("success"):
                record.id = resp.get("orderID", order_id)
                record.status = "OPEN"
                self._total_orders += 1
                logger.info("Order placed: %s %s %s @ %.4f x %.1f (%.0fms)",
                           side, outcome, token_id[:8], price, size, record.latency_ms)
            else:
                record.status = "REJECTED"
                self._total_rejects += 1
                logger.warning("Order rejected: %s", resp)

        except Exception as e:
            record.latency_ms = (time.monotonic() - t0) * 1000
            record.status = "REJECTED"
            self._total_rejects += 1
            logger.error("Order placement failed: %s", e)

        self.recent_orders.append(record)
        if len(self.recent_orders) > 200:
            self.recent_orders = self.recent_orders[-200:]

        await self.db.insert_order({
            "id": record.id, "market_id": market_id, "condition_id": condition_id,
            "side": side, "outcome": outcome, "price": price, "size": size,
            "filled_size": record.filled_size, "status": record.status,
            "created_at": record.created_at, "latency_ms": record.latency_ms,
            "arb_cycle_id": cycle_id,
        })
        await self.db.insert_quote_event({
            "order_id": record.id,
            "market_id": market_id,
            "condition_id": condition_id,
            "outcome": outcome,
            "side": side,
            "price": price,
            "size": size,
            "action": "PLACE",
        })

        return record

    def active_quotes_for(self, condition_id: str) -> list[dict[str, Any]]:
        quotes: list[dict[str, Any]] = []
        for key, order in self.active_quotes.items():
            if key.startswith(f"{condition_id}:"):
                quotes.append({
                    "side": order.side,
                    "outcome": order.outcome,
                    "price": order.price,
                    "size": order.size,
                    "status": order.status,
                })
        return quotes

    def inventory_by_condition(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for (condition_id, outcome), pos in self._positions.items():
            out.setdefault(condition_id, {})[outcome] = float(pos["size"])
        return out

    def inventory_summary(self) -> list[dict[str, Any]]:
        summary: list[dict[str, Any]] = []
        for (condition_id, outcome), pos in self._positions.items():
            summary.append({
                "condition_id": condition_id,
                "outcome": outcome,
                "size": float(pos["size"]),
                "avg_price": float(pos["avg_price"]),
                "market_id": pos.get("market_id", ""),
            })
        return summary

    async def record_rebate(self, market_id: str, amount_usdc: float) -> None:
        self._liquidity_rewards += amount_usdc
        await self.db.insert_rebate({
            "market_id": market_id,
            "amount_usdc": amount_usdc,
            "source": "manual",
        })
        await self.db.set_metric("liquidity_rewards", self._liquidity_rewards)

    async def refresh_open_orders(self) -> None:
        """Refresh open quote orders and update fills/positions."""
        now = time.time()
        if now - self._last_refresh < (self.quote_refresh_ms / 1000):
            return
        self._last_refresh = now

        for key, order in list(self.active_quotes.items()):
            if order.status not in ("OPEN", "PARTIAL"):
                continue
            if now - order.created_at > (self.quote_ttl_ms / 1000):
                await self._cancel_order(order)
                self.active_quotes.pop(key, None)
                continue

            try:
                resp = await self.clob.get_order(order.id)
                status = resp.get("status", "").upper()
                filled = float(resp.get("size_matched", 0))
                delta = max(0.0, filled - order.filled_size)

                if status == "MATCHED" or filled >= order.size:
                    order.status = "FILLED"
                    order.filled_size = order.size
                    self._total_fills += 1
                    if delta > 0:
                        await self._apply_fill(order, delta)
                    self.active_quotes.pop(key, None)
                elif filled > 0:
                    order.status = "PARTIAL"
                    order.filled_size = filled
                    if delta > 0:
                        await self._apply_fill(order, delta, partial=True)
                elif status in ("CANCELLED", "EXPIRED"):
                    order.status = "CANCELLED"
                    self._total_cancels += 1
                    self.active_quotes.pop(key, None)

                await self.db.update_order_status(order.id, order.status, order.filled_size)
            except Exception as e:
                logger.debug("Fill check error for %s: %s", order.id, e)

    async def _apply_fill(self, order: OrderRecord, filled: float, partial: bool = False) -> None:
        """Update positions and PnL for a filled (or partially filled) order."""
        if filled <= 0:
            return

        signed = filled if order.side == "BUY" else -filled
        key = (order.condition_id, order.outcome)
        pos = self._positions.get(key, {
            "size": 0.0,
            "avg_price": 0.0,
            "market_id": order.market_id,
        })
        old_size = float(pos["size"])
        old_avg = float(pos["avg_price"])
        new_size = old_size + signed
        if order.side == "BUY" and new_size != 0:
            avg_price = (old_size * old_avg + filled * order.price) / new_size
        else:
            avg_price = old_avg if new_size != 0 else 0.0
        pos["size"] = new_size
        pos["avg_price"] = avg_price
        self._positions[key] = pos

        pnl = filled * order.price * (1 if order.side == "SELL" else -1)
        self._cumulative_pnl += pnl
        self._spread_capture_pnl += pnl
        self._pnl_history.append((time.time(), self._cumulative_pnl))

        await self.db.upsert_position(
            order.condition_id, order.outcome,
            new_size, order.price, order.market_id,
        )
        await self.db.insert_fill({
            "order_id": order.id,
            "market_id": order.market_id,
            "condition_id": order.condition_id,
            "outcome": order.outcome,
            "side": order.side,
            "price": order.price,
            "size": filled,
        })
        await self.db.set_metric("cumulative_pnl", self._cumulative_pnl)
        await self.db.set_metric("spread_capture_pnl", self._spread_capture_pnl)
        await self.risk.record_cycle_result(pnl)

        if not partial:
            await self.db.log_event("FILL",
                                    f"{order.id} {order.side} {order.outcome} "
                                    f"{filled}@{order.price:.3f}")

    async def cancel_all(self) -> int:
        """Cancel all open orders."""
        try:
            await self.clob.cancel_all_orders()
            cancelled = 0
            for order in self.recent_orders:
                if order.status in ("OPEN", "PENDING"):
                    order.status = "CANCELLED"
                    cancelled += 1
            self.active_quotes.clear()
            return cancelled
        except Exception as e:
            logger.error("Cancel all failed: %s", e)
            return 0

    @property
    def stats(self) -> dict[str, Any]:
        avg_latency = (self._cumulative_latency / self._total_orders
                       if self._total_orders > 0 else 0)
        return {
            "pipeline_stage": self.pipeline_stage.value,
            "paused": self.paused,
            "total_orders": self._total_orders,
            "total_fills": self._total_fills,
            "total_rejects": self._total_rejects,
            "total_cancels": self._total_cancels,
            "active_quotes": len(self.active_quotes),
            "avg_latency_ms": avg_latency,
            "cumulative_pnl": self._cumulative_pnl,
            "spread_capture_pnl": self._spread_capture_pnl,
            "liquidity_rewards": self._liquidity_rewards,
            "fill_rate": (self._total_fills / self._total_orders * 100
                         if self._total_orders > 0 else 0),
        }

    @property
    def pnl_history(self) -> list[tuple[float, float]]:
        return list(self._pnl_history)
