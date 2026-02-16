"""Risk management module: position limits, exposure caps, circuit breakers."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from yuga.config import RiskConfig
from yuga.db import Database
from yuga.strategy.arbitrage import ArbSignal

logger = logging.getLogger("yuga.risk")


@dataclass
class CircuitBreaker:
    triggered: bool = False
    triggered_at: float = 0
    cooldown_s: int = 300
    reason: str = ""

    @property
    def is_active(self) -> bool:
        if not self.triggered:
            return False
        if time.time() - self.triggered_at > self.cooldown_s:
            self.triggered = False
            self.reason = ""
            return False
        return True

    @property
    def remaining_s(self) -> float:
        if not self.is_active:
            return 0
        return max(0, self.cooldown_s - (time.time() - self.triggered_at))


class RiskManager:
    """Enforces risk limits, position caps, and circuit breakers."""

    def __init__(self, config: RiskConfig, db: Database):
        self.config = config
        self.db = db
        self.circuit_breaker = CircuitBreaker(cooldown_s=config.circuit_breaker_cooldown_s)
        self._consecutive_losses = 0
        self._daily_pnl = 0.0
        self._daily_reset_date: str = ""
        self._total_checks = 0
        self._total_rejections = 0
        self._rejection_reasons: dict[str, int] = {}

    async def check_signal(self, signal: ArbSignal) -> tuple[bool, str]:
        """Validate whether a signal should be executed given current risk state."""
        self._total_checks += 1

        # Circuit breaker
        if self.circuit_breaker.is_active:
            return self._reject("CIRCUIT_BREAKER",
                                f"Circuit breaker active: {self.circuit_breaker.reason} "
                                f"({self.circuit_breaker.remaining_s:.0f}s remaining)")

        # Daily loss limit
        self._maybe_reset_daily()
        if self._daily_pnl <= -self.config.max_daily_loss_usdc:
            self._trip_circuit_breaker("Daily loss limit exceeded")
            return self._reject("DAILY_LOSS", f"Daily PnL {self._daily_pnl:.2f} exceeds limit")

        # Consecutive losses
        if self._consecutive_losses >= self.config.max_consecutive_losses:
            self._trip_circuit_breaker(f"{self._consecutive_losses} consecutive losses")
            return self._reject("CONSEC_LOSSES",
                                f"{self._consecutive_losses} consecutive losses")

        # Total exposure
        total_exp = await self.db.get_total_exposure()
        order_cost = signal.yes_price * signal.max_size + signal.no_price * signal.max_size
        if total_exp + order_cost > self.config.max_total_exposure_usdc:
            return self._reject("TOTAL_EXPOSURE",
                                f"Would exceed total exposure limit: "
                                f"{total_exp:.2f} + {order_cost:.2f} > {self.config.max_total_exposure_usdc}")

        # Per-market exposure
        mkt_exp = await self.db.get_market_exposure(signal.market_id)
        if mkt_exp + order_cost > self.config.max_per_market_exposure_usdc:
            return self._reject("MARKET_EXPOSURE",
                                f"Would exceed market exposure: {mkt_exp:.2f} + {order_cost:.2f}")

        # Open orders limit
        open_orders = await self.db.get_open_orders()
        if len(open_orders) >= self.config.max_open_orders:
            return self._reject("MAX_ORDERS", f"Open orders at limit: {len(open_orders)}")

        return True, "OK"

    async def record_cycle_result(self, pnl: float) -> None:
        """Record the result of an arb cycle for risk tracking."""
        self._maybe_reset_daily()
        self._daily_pnl += pnl
        await self.db.set_metric("daily_pnl", self._daily_pnl)

        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        await self.db.set_metric("consecutive_losses", self._consecutive_losses)

    def reset_circuit_breaker(self) -> None:
        """Manually reset the circuit breaker."""
        self.circuit_breaker.triggered = False
        self.circuit_breaker.reason = ""
        logger.info("Circuit breaker manually reset")

    def _trip_circuit_breaker(self, reason: str) -> None:
        self.circuit_breaker.triggered = True
        self.circuit_breaker.triggered_at = time.time()
        self.circuit_breaker.reason = reason
        logger.warning("CIRCUIT BREAKER TRIPPED: %s", reason)

    def _reject(self, code: str, reason: str) -> tuple[bool, str]:
        self._total_rejections += 1
        self._rejection_reasons[code] = self._rejection_reasons.get(code, 0) + 1
        logger.info("Risk rejection [%s]: %s", code, reason)
        return False, reason

    def _maybe_reset_daily(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if self._daily_reset_date != today:
            self._daily_pnl = 0
            self._daily_reset_date = today
            self._consecutive_losses = 0

    @property
    def status(self) -> dict[str, Any]:
        return {
            "circuit_breaker_active": self.circuit_breaker.is_active,
            "circuit_breaker_reason": self.circuit_breaker.reason,
            "circuit_breaker_remaining_s": self.circuit_breaker.remaining_s,
            "consecutive_losses": self._consecutive_losses,
            "daily_pnl": self._daily_pnl,
            "total_checks": self._total_checks,
            "total_rejections": self._total_rejections,
            "rejection_reasons": dict(self._rejection_reasons),
            "max_daily_loss": self.config.max_daily_loss_usdc,
            "max_total_exposure": self.config.max_total_exposure_usdc,
            "max_per_market_exposure": self.config.max_per_market_exposure_usdc,
        }
