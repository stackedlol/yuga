"""Arbitrage detection engine for Polymarket YES/NO mispricing.

On Polymarket, each binary market has YES and NO outcome tokens.
In an efficient market: best_ask(YES) + best_ask(NO) = 1.00
When this sum < 1.00, there's an arbitrage opportunity (buy both sides for < $1,
guaranteed $1 payout regardless of outcome).
When best_bid(YES) + best_bid(NO) > 1.00, there's a reverse arb
(sell both sides for > $1).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("yuga.strategy.arb")


@dataclass
class OrderBookSnapshot:
    token_id: str
    outcome: str  # YES or NO
    bids: list[tuple[float, float]]  # [(price, size), ...] sorted desc
    asks: list[tuple[float, float]]  # [(price, size), ...] sorted asc
    timestamp: float = field(default_factory=time.time)

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 1.0

    @property
    def best_bid_size(self) -> float:
        return self.bids[0][1] if self.bids else 0

    @property
    def best_ask_size(self) -> float:
        return self.asks[0][1] if self.asks else 0

    @property
    def mid(self) -> float:
        if self.bids and self.asks:
            return (self.best_bid + self.best_ask) / 2
        return self.best_bid or self.best_ask

    @property
    def spread_bps(self) -> float:
        if self.best_bid > 0:
            return (self.best_ask - self.best_bid) / self.best_bid * 10000
        return 0

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.timestamp) > 2.0  # 2s staleness


@dataclass
class MarketState:
    market_id: str
    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    yes_book: OrderBookSnapshot | None = None
    no_book: OrderBookSnapshot | None = None
    active: bool = True
    last_scan: float = 0

    @property
    def is_ready(self) -> bool:
        return (
            self.yes_book is not None
            and self.no_book is not None
            and not self.yes_book.is_stale
            and not self.no_book.is_stale
        )


@dataclass
class ArbSignal:
    id: str
    market_id: str
    condition_id: str
    signal_type: str  # "BUY_BOTH" or "SELL_BOTH"
    yes_price: float
    no_price: float
    combined_cost: float
    spread_bps: float
    max_size: float
    yes_token_id: str
    no_token_id: str
    timestamp: float = field(default_factory=time.time)

    @property
    def expected_profit_per_unit(self) -> float:
        if self.signal_type == "BUY_BOTH":
            return 1.0 - self.combined_cost  # Payout is $1
        else:
            return self.combined_cost - 1.0  # Selling both > $1


class ArbitrageEngine:
    """Scans market states for YES/NO mispricing opportunities."""

    def __init__(self, min_spread_bps: int = 30, min_liquidity: float = 50.0):
        self.min_spread_bps = min_spread_bps
        self.min_liquidity = min_liquidity
        self.markets: dict[str, MarketState] = {}
        self.active_signals: dict[str, ArbSignal] = {}
        self._scan_count = 0
        self._signal_count = 0
        self._missed_count = 0

    def add_market(self, market: MarketState) -> None:
        self.markets[market.condition_id] = market

    def remove_market(self, condition_id: str) -> None:
        self.markets.pop(condition_id, None)
        self.active_signals.pop(condition_id, None)

    def update_book(self, token_id: str, book_data: dict) -> OrderBookSnapshot | None:
        """Update order book for a token and return snapshot."""
        bids = [(float(b["price"]), float(b["size"])) for b in book_data.get("bids", [])]
        asks = [(float(a["price"]), float(a["size"])) for a in book_data.get("asks", [])]
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])

        snapshot = OrderBookSnapshot(
            token_id=token_id,
            outcome="",  # Set by caller
            bids=bids,
            asks=asks,
            timestamp=time.time(),
        )

        # Find which market this token belongs to
        for mkt in self.markets.values():
            if mkt.yes_token_id == token_id:
                snapshot.outcome = "YES"
                mkt.yes_book = snapshot
                return snapshot
            elif mkt.no_token_id == token_id:
                snapshot.outcome = "NO"
                mkt.no_book = snapshot
                return snapshot

        return None

    def scan_all(self) -> list[ArbSignal]:
        """Scan all markets for arbitrage opportunities."""
        signals: list[ArbSignal] = []
        self._scan_count += 1

        for mkt in self.markets.values():
            if not mkt.active or not mkt.is_ready:
                continue

            signal = self._check_market(mkt)
            if signal:
                signals.append(signal)
                self.active_signals[mkt.condition_id] = signal
                self._signal_count += 1
            else:
                self.active_signals.pop(mkt.condition_id, None)

            mkt.last_scan = time.time()

        return signals

    def _check_market(self, mkt: MarketState) -> ArbSignal | None:
        assert mkt.yes_book and mkt.no_book

        # Check BUY_BOTH: buy YES ask + buy NO ask < 1.0
        yes_ask = mkt.yes_book.best_ask
        no_ask = mkt.no_book.best_ask
        buy_cost = yes_ask + no_ask

        if buy_cost < 1.0:
            spread_bps = (1.0 - buy_cost) / buy_cost * 10000
            if spread_bps >= self.min_spread_bps:
                max_size = min(
                    mkt.yes_book.best_ask_size,
                    mkt.no_book.best_ask_size,
                )
                min_liq = min(
                    mkt.yes_book.best_ask_size * yes_ask,
                    mkt.no_book.best_ask_size * no_ask,
                )
                if min_liq >= self.min_liquidity:
                    return ArbSignal(
                        id=str(uuid.uuid4())[:8],
                        market_id=mkt.market_id,
                        condition_id=mkt.condition_id,
                        signal_type="BUY_BOTH",
                        yes_price=yes_ask,
                        no_price=no_ask,
                        combined_cost=buy_cost,
                        spread_bps=spread_bps,
                        max_size=max_size,
                        yes_token_id=mkt.yes_token_id,
                        no_token_id=mkt.no_token_id,
                    )

        # Check SELL_BOTH: sell YES bid + sell NO bid > 1.0
        yes_bid = mkt.yes_book.best_bid
        no_bid = mkt.no_book.best_bid
        sell_proceeds = yes_bid + no_bid

        if sell_proceeds > 1.0:
            spread_bps = (sell_proceeds - 1.0) / 1.0 * 10000
            if spread_bps >= self.min_spread_bps:
                max_size = min(
                    mkt.yes_book.best_bid_size,
                    mkt.no_book.best_bid_size,
                )
                min_liq = min(
                    mkt.yes_book.best_bid_size * yes_bid,
                    mkt.no_book.best_bid_size * no_bid,
                )
                if min_liq >= self.min_liquidity:
                    return ArbSignal(
                        id=str(uuid.uuid4())[:8],
                        market_id=mkt.market_id,
                        condition_id=mkt.condition_id,
                        signal_type="SELL_BOTH",
                        yes_price=yes_bid,
                        no_price=no_bid,
                        combined_cost=sell_proceeds,
                        spread_bps=spread_bps,
                        max_size=max_size,
                        yes_token_id=mkt.yes_token_id,
                        no_token_id=mkt.no_token_id,
                    )

        return None

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "markets_tracked": len(self.markets),
            "markets_ready": sum(1 for m in self.markets.values() if m.is_ready),
            "active_signals": len(self.active_signals),
            "total_scans": self._scan_count,
            "total_signals": self._signal_count,
            "missed_opportunities": self._missed_count,
        }
