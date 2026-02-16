"""Execution controller: manages order lifecycle from signal to fill/cancel."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from yuga.db import Database
from yuga.ingestion.clob_client import CLOBClient
from yuga.risk.manager import RiskManager
from yuga.strategy.arbitrage import ArbSignal

logger = logging.getLogger("yuga.execution")


class PipelineStage(str, Enum):
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    SNAPSHOT = "SNAPSHOT"
    CANDIDATE = "CANDIDATE"
    PLACING = "PLACING"
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


@dataclass
class ArbCycle:
    id: str
    market_id: str
    signal: ArbSignal
    orders: list[OrderRecord] = field(default_factory=list)
    status: str = "DETECTED"
    pnl: float = 0
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None


class ExecutionController:
    """Manages the full execution pipeline for arbitrage signals."""

    def __init__(self, clob: CLOBClient, risk: RiskManager, db: Database,
                 order_size: float = 10.0, max_order_size: float = 100.0,
                 order_timeout_ms: int = 5000, cancel_stale_ms: int = 3000):
        self.clob = clob
        self.risk = risk
        self.db = db
        self.order_size = order_size
        self.max_order_size = max_order_size
        self.order_timeout_ms = order_timeout_ms
        self.cancel_stale_ms = cancel_stale_ms
        self.pipeline_stage = PipelineStage.IDLE
        self.active_cycles: dict[str, ArbCycle] = {}
        self.recent_orders: list[OrderRecord] = []
        self.paused = False
        self._total_orders = 0
        self._total_fills = 0
        self._total_rejects = 0
        self._total_cancels = 0
        self._cumulative_latency = 0.0
        self._pnl_history: list[tuple[float, float]] = []  # (timestamp, cumulative_pnl)
        self._cumulative_pnl = 0.0

    async def execute_signal(self, signal: ArbSignal) -> ArbCycle | None:
        """Execute an arbitrage signal by placing orders on both sides."""
        if self.paused:
            return None

        # Risk check
        allowed, reason = await self.risk.check_signal(signal)
        if not allowed:
            logger.info("Signal %s rejected by risk: %s", signal.id, reason)
            await self.db.log_event("SIGNAL_REJECTED", f"{signal.id}: {reason}")
            return None

        cycle_id = str(uuid.uuid4())[:12]
        cycle = ArbCycle(id=cycle_id, market_id=signal.market_id, signal=signal)
        self.active_cycles[cycle_id] = cycle
        self.pipeline_stage = PipelineStage.PLACING

        try:
            # Determine order size
            size = min(self.order_size, signal.max_size, self.max_order_size)
            if size <= 0:
                return None

            # Place both orders concurrently
            if signal.signal_type == "BUY_BOTH":
                yes_order, no_order = await asyncio.gather(
                    self._place_order(cycle_id, signal.market_id, signal.condition_id,
                                      signal.yes_token_id, "BUY", "YES",
                                      signal.yes_price, size),
                    self._place_order(cycle_id, signal.market_id, signal.condition_id,
                                      signal.no_token_id, "BUY", "NO",
                                      signal.no_price, size),
                    return_exceptions=True,
                )
            else:  # SELL_BOTH
                yes_order, no_order = await asyncio.gather(
                    self._place_order(cycle_id, signal.market_id, signal.condition_id,
                                      signal.yes_token_id, "SELL", "YES",
                                      signal.yes_price, size),
                    self._place_order(cycle_id, signal.market_id, signal.condition_id,
                                      signal.no_token_id, "SELL", "NO",
                                      signal.no_price, size),
                    return_exceptions=True,
                )

            for result in [yes_order, no_order]:
                if isinstance(result, OrderRecord):
                    cycle.orders.append(result)
                elif isinstance(result, Exception):
                    logger.error("Order placement failed: %s", result)

            if not cycle.orders:
                cycle.status = "FAILED"
                await self._finalize_cycle(cycle)
                return cycle

            # Monitor fills
            self.pipeline_stage = PipelineStage.MONITORING
            cycle.status = "EXECUTING"
            await self._monitor_fills(cycle)

            return cycle

        except Exception as e:
            logger.error("Execution error for signal %s: %s", signal.id, e)
            cycle.status = "FAILED"
            await self._finalize_cycle(cycle)
            return cycle
        finally:
            self.pipeline_stage = PipelineStage.IDLE

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

        return record

    async def _monitor_fills(self, cycle: ArbCycle) -> None:
        """Poll for order fills within timeout."""
        deadline = time.time() + self.order_timeout_ms / 1000
        open_orders = [o for o in cycle.orders if o.status == "OPEN"]

        while open_orders and time.time() < deadline:
            await asyncio.sleep(self.cancel_stale_ms / 1000 / 10)  # Poll interval

            for order in list(open_orders):
                try:
                    resp = await self.clob.get_order(order.id)
                    status = resp.get("status", "").upper()
                    filled = float(resp.get("size_matched", 0))

                    if status == "MATCHED" or filled >= order.size:
                        order.status = "FILLED"
                        order.filled_size = order.size
                        self._total_fills += 1
                        open_orders.remove(order)
                    elif filled > 0:
                        order.status = "PARTIAL"
                        order.filled_size = filled
                    elif status in ("CANCELLED", "EXPIRED"):
                        order.status = "CANCELLED"
                        self._total_cancels += 1
                        open_orders.remove(order)

                    await self.db.update_order_status(order.id, order.status, order.filled_size)

                except Exception as e:
                    logger.debug("Fill check error for %s: %s", order.id, e)

        # Cancel remaining open orders past timeout
        for order in open_orders:
            try:
                await self.clob.cancel_order(order.id)
                order.status = "CANCELLED"
                self._total_cancels += 1
                await self.db.update_order_status(order.id, "CANCELLED", order.filled_size)
            except Exception as e:
                logger.warning("Cancel failed for %s: %s", order.id, e)

        self.pipeline_stage = PipelineStage.RESOLVING
        await self._finalize_cycle(cycle)

    async def _finalize_cycle(self, cycle: ArbCycle) -> None:
        """Compute PnL and update positions for completed cycle."""
        total_filled_cost = 0
        total_filled_size = 0

        for order in cycle.orders:
            if order.filled_size > 0:
                total_filled_cost += order.filled_size * order.price
                total_filled_size += order.filled_size

                # Update position
                current_size = 0  # Would read from DB in production
                new_size = current_size + (order.filled_size if order.side == "BUY" else -order.filled_size)
                await self.db.upsert_position(
                    order.condition_id, order.outcome,
                    new_size, order.price, order.market_id,
                )

        # Calculate PnL for the cycle
        all_filled = all(o.status == "FILLED" for o in cycle.orders)
        if all_filled and cycle.signal.signal_type == "BUY_BOTH":
            # Bought both sides: cost = sum of prices * size, guaranteed payout = size * $1
            min_filled = min(o.filled_size for o in cycle.orders) if cycle.orders else 0
            cycle.pnl = min_filled * (1.0 - cycle.signal.combined_cost)
            cycle.status = "FILLED"
        elif all_filled and cycle.signal.signal_type == "SELL_BOTH":
            min_filled = min(o.filled_size for o in cycle.orders) if cycle.orders else 0
            cycle.pnl = min_filled * (cycle.signal.combined_cost - 1.0)
            cycle.status = "FILLED"
        elif any(o.filled_size > 0 for o in cycle.orders):
            cycle.status = "PARTIAL"
            # Partial fills may result in directional exposure
            cycle.pnl = 0  # Mark to market later
        else:
            cycle.status = "FAILED"

        cycle.completed_at = time.time()
        self._cumulative_pnl += cycle.pnl
        self._pnl_history.append((time.time(), self._cumulative_pnl))

        # Persist
        await self.db.insert_arb_cycle({
            "id": cycle.id, "market_id": cycle.market_id,
            "yes_price": cycle.signal.yes_price, "no_price": cycle.signal.no_price,
            "spread_bps": cycle.signal.spread_bps, "status": cycle.status,
            "pnl": cycle.pnl, "created_at": cycle.created_at,
            "completed_at": cycle.completed_at,
        })
        await self.db.set_metric("cumulative_pnl", self._cumulative_pnl)
        await self.db.log_event("CYCLE_COMPLETE",
                                f"{cycle.id} status={cycle.status} pnl={cycle.pnl:.4f}")

        # Update risk manager
        await self.risk.record_cycle_result(cycle.pnl)

        # Clean up
        self.active_cycles.pop(cycle.id, None)

    async def cancel_all(self) -> int:
        """Cancel all open orders."""
        try:
            await self.clob.cancel_all_orders()
            cancelled = 0
            for order in self.recent_orders:
                if order.status in ("OPEN", "PENDING"):
                    order.status = "CANCELLED"
                    cancelled += 1
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
            "active_cycles": len(self.active_cycles),
            "avg_latency_ms": avg_latency,
            "cumulative_pnl": self._cumulative_pnl,
            "fill_rate": (self._total_fills / self._total_orders * 100
                         if self._total_orders > 0 else 0),
        }

    @property
    def pnl_history(self) -> list[tuple[float, float]]:
        return list(self._pnl_history)
