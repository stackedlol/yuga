"""SQLite persistence layer for events, orders, positions, and metrics."""

from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    side TEXT NOT NULL,           -- 'BUY' or 'SELL'
    outcome TEXT NOT NULL,        -- 'YES' or 'NO'
    price REAL NOT NULL,
    size REAL NOT NULL,
    filled_size REAL DEFAULT 0,
    status TEXT DEFAULT 'PENDING', -- PENDING, OPEN, FILLED, PARTIAL, CANCELLED, REJECTED
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    latency_ms REAL DEFAULT 0,
    arb_cycle_id TEXT
);

CREATE TABLE IF NOT EXISTS arb_cycles (
    id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    yes_price REAL,
    no_price REAL,
    spread_bps REAL,
    status TEXT DEFAULT 'DETECTED', -- DETECTED, EXECUTING, FILLED, PARTIAL, FAILED, EXPIRED
    pnl REAL DEFAULT 0,
    created_at REAL NOT NULL,
    completed_at REAL
);

CREATE TABLE IF NOT EXISTS positions (
    condition_id TEXT NOT NULL,
    outcome TEXT NOT NULL,         -- 'YES' or 'NO'
    size REAL DEFAULT 0,
    avg_price REAL DEFAULT 0,
    market_id TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (condition_id, outcome)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    payload TEXT,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS quote_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT,
    market_id TEXT,
    condition_id TEXT,
    outcome TEXT,
    side TEXT,
    price REAL,
    size REAL,
    action TEXT,             -- PLACE, CANCEL, REPRICE
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT,
    market_id TEXT,
    condition_id TEXT,
    outcome TEXT,
    side TEXT,
    price REAL,
    size REAL,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rebates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT,
    amount_usdc REAL,
    ts REAL NOT NULL,
    source TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_market ON orders(market_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_arb_market ON arb_cycles(market_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_quote_events_market ON quote_events(market_id);
CREATE INDEX IF NOT EXISTS idx_fills_market ON fills(market_id);
CREATE INDEX IF NOT EXISTS idx_rebates_market ON rebates(market_id);
"""


class Database:
    def __init__(self, path: str | Path = "yuga.db"):
        self.path = str(path)
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Database not connected"
        return self._db

    # -- Orders --
    async def insert_order(self, order: dict) -> None:
        now = time.time()
        await self.db.execute(
            """INSERT OR REPLACE INTO orders
               (id, market_id, condition_id, side, outcome, price, size,
                filled_size, status, created_at, updated_at, latency_ms, arb_cycle_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                order["id"], order["market_id"], order["condition_id"],
                order["side"], order["outcome"], order["price"], order["size"],
                order.get("filled_size", 0), order.get("status", "PENDING"),
                order.get("created_at", now), now,
                order.get("latency_ms", 0), order.get("arb_cycle_id"),
            ),
        )
        await self.db.commit()

    async def update_order_status(self, order_id: str, status: str, filled_size: float = 0) -> None:
        await self.db.execute(
            "UPDATE orders SET status=?, filled_size=?, updated_at=? WHERE id=?",
            (status, filled_size, time.time(), order_id),
        )
        await self.db.commit()

    async def get_open_orders(self) -> list[dict]:
        cur = await self.db.execute(
            "SELECT * FROM orders WHERE status IN ('PENDING','OPEN','PARTIAL') ORDER BY created_at DESC"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_recent_orders(self, limit: int = 50) -> list[dict]:
        cur = await self.db.execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]

    # -- Arb Cycles --
    async def insert_arb_cycle(self, cycle: dict) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO arb_cycles
               (id, market_id, yes_price, no_price, spread_bps, status, pnl, created_at, completed_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                cycle["id"], cycle["market_id"], cycle.get("yes_price"),
                cycle.get("no_price"), cycle.get("spread_bps"),
                cycle.get("status", "DETECTED"), cycle.get("pnl", 0),
                cycle.get("created_at", time.time()), cycle.get("completed_at"),
            ),
        )
        await self.db.commit()

    async def update_arb_cycle(self, cycle_id: str, **kwargs) -> None:
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values())
        vals.append(cycle_id)
        await self.db.execute(f"UPDATE arb_cycles SET {sets} WHERE id=?", vals)
        await self.db.commit()

    async def get_arb_stats(self) -> dict:
        cur = await self.db.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins, "
            "SUM(pnl) as total_pnl, AVG(pnl) as avg_pnl "
            "FROM arb_cycles WHERE status IN ('FILLED','PARTIAL','FAILED')"
        )
        row = await cur.fetchone()
        if row and row["total"]:
            return {
                "total": row["total"],
                "wins": row["wins"] or 0,
                "total_pnl": row["total_pnl"] or 0,
                "avg_pnl": row["avg_pnl"] or 0,
                "win_rate": (row["wins"] or 0) / row["total"] * 100,
            }
        return {"total": 0, "wins": 0, "total_pnl": 0, "avg_pnl": 0, "win_rate": 0}

    # -- Positions --
    async def upsert_position(self, condition_id: str, outcome: str, size: float,
                              avg_price: float, market_id: str) -> None:
        await self.db.execute(
            """INSERT INTO positions (condition_id, outcome, size, avg_price, market_id, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(condition_id, outcome) DO UPDATE SET
               size=?, avg_price=?, updated_at=?""",
            (condition_id, outcome, size, avg_price, market_id, time.time(),
             size, avg_price, time.time()),
        )
        await self.db.commit()

    async def get_positions(self) -> list[dict]:
        cur = await self.db.execute("SELECT * FROM positions WHERE size != 0")
        return [dict(r) for r in await cur.fetchall()]

    async def get_total_exposure(self) -> float:
        cur = await self.db.execute("SELECT SUM(ABS(size * avg_price)) as exp FROM positions WHERE size != 0")
        row = await cur.fetchone()
        return row["exp"] or 0 if row else 0

    async def get_market_exposure(self, market_id: str) -> float:
        cur = await self.db.execute(
            "SELECT SUM(ABS(size * avg_price)) as exp FROM positions WHERE market_id=? AND size != 0",
            (market_id,),
        )
        row = await cur.fetchone()
        return row["exp"] or 0 if row else 0

    async def get_position_size(self, condition_id: str, outcome: str) -> float:
        cur = await self.db.execute(
            "SELECT size FROM positions WHERE condition_id=? AND outcome=?",
            (condition_id, outcome),
        )
        row = await cur.fetchone()
        return row["size"] if row else 0.0

    # -- Metrics --
    async def set_metric(self, key: str, value: float) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO metrics (key, value, updated_at) VALUES (?,?,?)",
            (key, value, time.time()),
        )
        await self.db.commit()

    async def get_metric(self, key: str, default: float = 0) -> float:
        cur = await self.db.execute("SELECT value FROM metrics WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else default

    async def get_all_metrics(self) -> dict[str, float]:
        cur = await self.db.execute("SELECT key, value FROM metrics")
        return {r["key"]: r["value"] for r in await cur.fetchall()}

    # -- Events --
    async def log_event(self, event_type: str, payload: str = "") -> None:
        await self.db.execute(
            "INSERT INTO events (event_type, payload, ts) VALUES (?,?,?)",
            (event_type, payload, time.time()),
        )
        await self.db.commit()

    async def insert_quote_event(self, event: dict) -> None:
        await self.db.execute(
            """INSERT INTO quote_events
               (order_id, market_id, condition_id, outcome, side, price, size, action, ts)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                event.get("order_id"),
                event.get("market_id"),
                event.get("condition_id"),
                event.get("outcome"),
                event.get("side"),
                event.get("price"),
                event.get("size"),
                event.get("action"),
                event.get("ts", time.time()),
            ),
        )
        await self.db.commit()

    async def insert_fill(self, fill: dict) -> None:
        await self.db.execute(
            """INSERT INTO fills
               (order_id, market_id, condition_id, outcome, side, price, size, ts)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                fill.get("order_id"),
                fill.get("market_id"),
                fill.get("condition_id"),
                fill.get("outcome"),
                fill.get("side"),
                fill.get("price"),
                fill.get("size"),
                fill.get("ts", time.time()),
            ),
        )
        await self.db.commit()

    async def insert_rebate(self, rebate: dict) -> None:
        await self.db.execute(
            """INSERT INTO rebates (market_id, amount_usdc, ts, source)
               VALUES (?,?,?,?)""",
            (
                rebate.get("market_id"),
                rebate.get("amount_usdc", 0),
                rebate.get("ts", time.time()),
                rebate.get("source", "manual"),
            ),
        )
        await self.db.commit()

    async def get_rebate_stats(self) -> dict:
        cur = await self.db.execute(
            "SELECT SUM(amount_usdc) as total, COUNT(*) as count FROM rebates"
        )
        row = await cur.fetchone()
        return {
            "total": row["total"] or 0,
            "count": row["count"] or 0,
        }

    async def get_recent_events(self, limit: int = 100) -> list[dict]:
        cur = await self.db.execute(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]
