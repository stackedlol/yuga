"""Polymarket CLOB REST API client for market discovery and order management."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger("yuga.ingestion.clob")


class CLOBClient:
    """Async client for Polymarket's CLOB (Central Limit Order Book) API."""

    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "",
                 api_passphrase: str = "", funder: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.funder = funder
        self._session: aiohttp.ClientSession | None = None
        self._request_count = 0
        self._last_latency_ms: float = 0

    @property
    def last_latency_ms(self) -> float:
        return self._last_latency_ms

    async def start(self) -> None:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["POLY_API_KEY"] = self.api_key
            headers["POLY_API_SECRET"] = self.api_secret
            headers["POLY_PASSPHRASE"] = self.api_passphrase
        self._session = aiohttp.ClientSession(headers=headers)
        logger.info("CLOB client started: %s", self.base_url)

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _get(self, path: str, params: dict | None = None) -> Any:
        assert self._session, "Client not started"
        url = f"{self.base_url}{path}"
        t0 = time.monotonic()
        try:
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                self._last_latency_ms = (time.monotonic() - t0) * 1000
                self._request_count += 1
                if resp.status == 429:
                    logger.warning("Rate limited on %s, backing off", path)
                    await asyncio.sleep(1)
                    return await self._get(path, params)
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            logger.error("CLOB GET %s failed: %s", path, e)
            raise

    async def _post(self, path: str, data: dict | None = None) -> Any:
        assert self._session, "Client not started"
        url = f"{self.base_url}{path}"
        t0 = time.monotonic()
        try:
            async with self._session.post(url, json=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                self._last_latency_ms = (time.monotonic() - t0) * 1000
                self._request_count += 1
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            logger.error("CLOB POST %s failed: %s", path, e)
            raise

    async def _delete(self, path: str, data: dict | None = None) -> Any:
        assert self._session, "Client not started"
        url = f"{self.base_url}{path}"
        t0 = time.monotonic()
        try:
            async with self._session.delete(url, json=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                self._last_latency_ms = (time.monotonic() - t0) * 1000
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            logger.error("CLOB DELETE %s failed: %s", path, e)
            raise

    # -- Market Discovery --

    async def get_markets(self, next_cursor: str = "") -> dict:
        """Fetch paginated list of active markets."""
        params = {}
        if next_cursor:
            params["next_cursor"] = next_cursor
        return await self._get("/markets", params)

    async def get_market(self, condition_id: str) -> dict:
        """Fetch a single market by condition ID."""
        return await self._get(f"/markets/{condition_id}")

    async def get_simplified_markets(self, next_cursor: str = "") -> dict:
        """Fetch simplified market data (faster)."""
        params = {}
        if next_cursor:
            params["next_cursor"] = next_cursor
        return await self._get("/simplified-markets", params)

    # -- Order Book --

    async def get_order_book(self, token_id: str) -> dict:
        """Fetch full order book for a token (YES or NO outcome token)."""
        return await self._get("/book", params={"token_id": token_id})

    async def get_midpoint(self, token_id: str) -> dict:
        """Fetch midpoint price for a token."""
        return await self._get("/midpoint", params={"token_id": token_id})

    async def get_price(self, token_id: str, side: str) -> dict:
        """Fetch best bid/ask price for a token."""
        return await self._get("/price", params={"token_id": token_id, "side": side})

    async def get_prices_history(self, token_id: str, interval: str = "1h",
                                  fidelity: int = 60) -> list:
        """Fetch historical prices."""
        return await self._get("/prices-history", params={
            "market": token_id, "interval": interval, "fidelity": fidelity,
        })

    # -- Order Management --

    async def post_order(self, order: dict) -> dict:
        """Submit a new order to the CLOB."""
        return await self._post("/order", data=order)

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order."""
        return await self._delete("/order", data={"id": order_id})

    async def cancel_all_orders(self) -> dict:
        """Cancel all open orders."""
        return await self._delete("/cancel-all")

    async def get_order(self, order_id: str) -> dict:
        """Get order status by ID."""
        return await self._get(f"/order/{order_id}")

    async def get_open_orders(self) -> list:
        """Get all open orders for the authenticated user."""
        resp = await self._get("/orders")
        return resp if isinstance(resp, list) else resp.get("orders", [])

    # -- Gamma API (market metadata) --

    async def get_gamma_markets(self, gamma_url: str, limit: int = 100,
                                 active: bool = True, closed: bool = False) -> list:
        """Fetch markets from Gamma API for metadata (titles, categories)."""
        assert self._session
        params = {"limit": limit, "active": str(active).lower(), "closed": str(closed).lower()}
        url = f"{gamma_url}/markets"
        async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            return await resp.json()
