"""Market making engine for Polymarket YES/NO order books."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


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

    def is_stale(self, max_age_ms: int = 2000) -> bool:
        return (time.time() - self.timestamp) > (max_age_ms / 1000)


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
    last_quote: float = 0

    def is_ready(self, max_age_ms: int = 2000) -> bool:
        return (
            self.yes_book is not None
            and self.no_book is not None
            and not self.yes_book.is_stale(max_age_ms)
            and not self.no_book.is_stale(max_age_ms)
        )


@dataclass
class QuoteOrder:
    token_id: str
    outcome: str  # YES or NO
    side: str  # BUY or SELL
    price: float
    size: float


@dataclass
class QuoteSignal:
    id: str
    market_id: str
    condition_id: str
    spread_bps: float
    mid_yes: float
    mid_no: float
    orders: list[QuoteOrder]
    max_size: float
    timestamp: float = field(default_factory=time.time)


class MarketMakerEngine:
    """Generates continuous bid/ask quotes around the midpoint."""

    def __init__(
        self,
        quote_spread_bps: int = 20,
        min_liquidity: float = 50.0,
        price_staleness_ms: int = 2000,
    ):
        self.quote_spread_bps = quote_spread_bps
        self.min_liquidity = min_liquidity
        self.price_staleness_ms = price_staleness_ms
        self.markets: dict[str, MarketState] = {}
        self.active_quotes: dict[str, QuoteSignal] = {}
        self._scan_count = 0
        self._quote_count = 0

    def add_market(self, market: MarketState) -> None:
        self.markets[market.condition_id] = market

    def remove_market(self, condition_id: str) -> None:
        self.markets.pop(condition_id, None)
        self.active_quotes.pop(condition_id, None)

    def update_book(self, token_id: str, book_data: dict) -> OrderBookSnapshot | None:
        bids = [(float(b["price"]), float(b["size"])) for b in book_data.get("bids", [])]
        asks = [(float(a["price"]), float(a["size"])) for a in book_data.get("asks", [])]
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])

        snapshot = OrderBookSnapshot(
            token_id=token_id,
            outcome="",
            bids=bids,
            asks=asks,
            timestamp=time.time(),
        )

        for mkt in self.markets.values():
            if mkt.yes_token_id == token_id:
                snapshot.outcome = "YES"
                mkt.yes_book = snapshot
                return snapshot
            if mkt.no_token_id == token_id:
                snapshot.outcome = "NO"
                mkt.no_book = snapshot
                return snapshot

        return None

    def generate_quotes(
        self,
        inventory: dict[str, dict[str, float]] | None = None,
        inventory_limit: float = 0,
    ) -> list[QuoteSignal]:
        """Generate bid/ask quotes for all markets."""
        inventory = inventory or {}
        self._scan_count += 1
        signals: list[QuoteSignal] = []

        for mkt in self.markets.values():
            if not mkt.active or not mkt.is_ready(self.price_staleness_ms):
                continue

            inv = inventory.get(mkt.condition_id, {})
            signal = self._quote_market(mkt, inv, inventory_limit)
            if signal:
                signals.append(signal)
                self.active_quotes[mkt.condition_id] = signal
                self._quote_count += 1
                mkt.last_quote = time.time()
            else:
                self.active_quotes.pop(mkt.condition_id, None)

        return signals

    def _quote_market(
        self,
        mkt: MarketState,
        inv: dict[str, float],
        inventory_limit: float,
    ) -> QuoteSignal | None:
        assert mkt.yes_book and mkt.no_book

        # Ensure basic liquidity
        yes_liq = min(mkt.yes_book.best_bid_size, mkt.yes_book.best_ask_size) * mkt.yes_book.mid
        no_liq = min(mkt.no_book.best_bid_size, mkt.no_book.best_ask_size) * mkt.no_book.mid
        if min(yes_liq, no_liq) < self.min_liquidity:
            return None

        # Inventory-aware spread/size adjustment
        yes_pos = inv.get("YES", 0)
        no_pos = inv.get("NO", 0)
        skew = max(abs(yes_pos), abs(no_pos))
        skew_ratio = min(skew / inventory_limit, 1.0) if inventory_limit > 0 else 0

        spread_scale = 1.0 + skew_ratio
        size_scale = max(0.2, 1.0 - skew_ratio)

        half_spread_yes = (self.quote_spread_bps / 20000) * mkt.yes_book.mid * spread_scale
        half_spread_no = (self.quote_spread_bps / 20000) * mkt.no_book.mid * spread_scale

        def _px(p: float) -> float:
            return round(min(max(p, 0.01), 0.99), 3)

        yes_bid = _px(mkt.yes_book.mid - half_spread_yes)
        yes_ask = _px(mkt.yes_book.mid + half_spread_yes)
        no_bid = _px(mkt.no_book.mid - half_spread_no)
        no_ask = _px(mkt.no_book.mid + half_spread_no)

        if yes_bid >= yes_ask or no_bid >= no_ask:
            return None

        max_size = min(
            mkt.yes_book.best_bid_size,
            mkt.yes_book.best_ask_size,
            mkt.no_book.best_bid_size,
            mkt.no_book.best_ask_size,
        )
        max_size = max_size * size_scale

        spread_bps = (yes_ask - yes_bid) / max(yes_bid, 0.0001) * 10000

        orders = [
            QuoteOrder(mkt.yes_token_id, "YES", "BUY", yes_bid, max_size),
            QuoteOrder(mkt.yes_token_id, "YES", "SELL", yes_ask, max_size),
            QuoteOrder(mkt.no_token_id, "NO", "BUY", no_bid, max_size),
            QuoteOrder(mkt.no_token_id, "NO", "SELL", no_ask, max_size),
        ]

        return QuoteSignal(
            id=str(uuid.uuid4())[:8],
            market_id=mkt.market_id,
            condition_id=mkt.condition_id,
            spread_bps=spread_bps,
            mid_yes=mkt.yes_book.mid,
            mid_no=mkt.no_book.mid,
            orders=orders,
            max_size=max_size,
        )

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "markets_tracked": len(self.markets),
            "markets_ready": sum(1 for m in self.markets.values()
                                 if m.is_ready(self.price_staleness_ms)),
            "active_quotes": len(self.active_quotes),
            "total_scans": self._scan_count,
            "total_quotes": self._quote_count,
        }
