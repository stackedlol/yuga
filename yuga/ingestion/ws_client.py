"""WebSocket client for real-time Polymarket order book updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("yuga.ingestion.ws")


@dataclass
class WSConnectionState:
    connected: bool = False
    last_message_at: float = 0
    reconnect_count: int = 0
    latency_ms: float = 0
    subscribed_assets: set[str] = field(default_factory=set)
    error: str = ""


class WebSocketClient:
    """Manages WebSocket connections to Polymarket for real-time book updates."""

    def __init__(self, ws_url: str, on_book_update: Callable[[dict], Awaitable[None]] | None = None):
        self.ws_url = ws_url
        self.on_book_update = on_book_update
        self.state = WSConnectionState()
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._subscriptions: dict[str, set[str]] = {}  # market_id -> {token_ids}

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._connection_loop())
        logger.info("WebSocket client starting: %s", self.ws_url)

    async def stop(self) -> None:
        self._running = False
        if self._ping_task:
            self._ping_task.cancel()
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.state.connected = False
        logger.info("WebSocket client stopped")

    async def subscribe(self, market_id: str, token_ids: list[str]) -> None:
        """Subscribe to order book updates for given token IDs."""
        self._subscriptions[market_id] = set(token_ids)
        if self._ws and self.state.connected:
            await self._send_subscribe(token_ids)

    async def unsubscribe(self, market_id: str) -> None:
        token_ids = self._subscriptions.pop(market_id, set())
        if self._ws and self.state.connected and token_ids:
            try:
                msg = {"type": "unsubscribe", "assets_ids": list(token_ids)}
                await self._ws.send(json.dumps(msg))
                self.state.subscribed_assets -= token_ids
            except Exception as e:
                logger.warning("Unsubscribe failed: %s", e)

    async def _send_subscribe(self, token_ids: list[str] | set[str]) -> None:
        if not self._ws:
            return
        msg = {
            "type": "subscribe",
            "assets_ids": list(token_ids),
        }
        await self._ws.send(json.dumps(msg))
        self.state.subscribed_assets.update(token_ids)
        logger.debug("Subscribed to %d assets", len(token_ids))

    async def _connection_loop(self) -> None:
        while self._running:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    self._ws = ws
                    self.state.connected = True
                    self.state.error = ""
                    logger.info("WebSocket connected")

                    # Resubscribe to all tracked markets
                    all_tokens: set[str] = set()
                    for tokens in self._subscriptions.values():
                        all_tokens.update(tokens)
                    if all_tokens:
                        await self._send_subscribe(all_tokens)

                    self._ping_task = asyncio.create_task(self._ping_loop())

                    async for raw_msg in ws:
                        self.state.last_message_at = time.time()
                        try:
                            msg = json.loads(raw_msg)
                            if self.on_book_update and isinstance(msg, list):
                                for update in msg:
                                    await self.on_book_update(update)
                            elif self.on_book_update and isinstance(msg, dict):
                                await self.on_book_update(msg)
                        except json.JSONDecodeError:
                            logger.warning("Non-JSON WS message: %s", raw_msg[:200])
                        except Exception as e:
                            logger.error("Error processing WS message: %s", e)

            except ConnectionClosed as e:
                self.state.error = f"Connection closed: {e.code}"
                logger.warning("WebSocket closed: %s", e)
            except Exception as e:
                self.state.error = str(e)
                logger.error("WebSocket error: %s", e)
            finally:
                self.state.connected = False
                self._ws = None
                if self._ping_task:
                    self._ping_task.cancel()

            if self._running:
                self.state.reconnect_count += 1
                backoff = min(2 ** min(self.state.reconnect_count, 6), 60)
                logger.info("Reconnecting in %ds (attempt %d)", backoff, self.state.reconnect_count)
                await asyncio.sleep(backoff)

    async def _ping_loop(self) -> None:
        """Periodic ping to measure latency."""
        while self._running and self._ws:
            try:
                t0 = time.monotonic()
                pong = await self._ws.ping()
                await asyncio.wait_for(pong, timeout=5)
                self.state.latency_ms = (time.monotonic() - t0) * 1000
            except Exception:
                pass
            await asyncio.sleep(10)
