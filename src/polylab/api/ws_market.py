"""CLOB market WebSocket — wss://ws-subscriptions-clob.polymarket.com/ws/market (public, no auth).

Verified protocol (docs/research/api-sources.md, fixture ws_market_capture.json):
- first frame  {"type":"market","assets_ids":[...],"custom_feature_enabled":true}
- later frames {"operation":"subscribe"|"unsubscribe","assets_ids":[...],"custom_feature_enabled":true}
- client sends text "PING" every 10 s, server answers "PONG"; >30 s of silence = stale connection.
- messages are flat JSON objects (sometimes a JSON array of them) keyed by `event_type`:
  book, price_change (price_changes[] incl. the complement token), last_trade_price, best_bid_ask,
  new_market (global broadcast), tick_size_change / market_resolved (SDK only, unobserved).
`timestamp` is a millisecond string.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable, Iterable

from polylab.api.http import MARKET_WS_URL

log = logging.getLogger("polylab.ws_market")

PING_EVERY_S = 10
STALE_AFTER_S = 30
MAX_ASSETS = 2000


def subscribe_frame(assets: Iterable[str]) -> str:
    return json.dumps({"type": "market", "assets_ids": sorted(assets), "custom_feature_enabled": True})


def update_frame(op: str, assets: Iterable[str]) -> str:
    assert op in ("subscribe", "unsubscribe")
    return json.dumps({"operation": op, "assets_ids": sorted(assets), "custom_feature_enabled": True})


def parse_frame(text: str) -> list[dict]:
    """Decode one WS text frame into event dicts ("PONG"/non-JSON -> [])."""
    if not text or text in ("PONG", "PING"):
        return []
    try:
        obj = json.loads(text)
    except ValueError:
        return []
    if isinstance(obj, list):
        return [o for o in obj if isinstance(o, dict)]
    return [obj] if isinstance(obj, dict) else []


def event_assets(ev: dict) -> list[str]:
    """Token ids an event refers to (price_change carries several)."""
    if ev.get("event_type") == "price_change":
        return [str(pc.get("asset_id")) for pc in ev.get("price_changes") or [] if pc.get("asset_id")]
    return [str(ev["asset_id"])] if ev.get("asset_id") else []


class MarketFeed:
    """Reconnecting market-channel client with a dynamic asset set.

    `desired()` returns the asset set to subscribe (re-evaluated every `refresh_s`);
    `on_text(text, received_ms)` receives every raw frame (including non-JSON ones).
    With an empty desired set no connection is held open.
    """

    def __init__(self, desired: Callable[[], set[str]], on_text: Callable[[str, int], Awaitable[None] | None],
                 refresh_s: float = 60.0, url: str = MARKET_WS_URL):
        self.desired = desired
        self.on_text = on_text
        self.refresh_s = refresh_s
        self.url = url
        self.subscribed: set[str] = set()
        self.connected = False
        self.reconnects = 0
        self.last_rx = 0.0

    async def run(self, stop: asyncio.Event) -> None:
        from websockets.asyncio.client import connect

        backoff = 1.0
        while not stop.is_set():
            assets = set(list(self.desired())[:MAX_ASSETS])
            if not assets:
                await _wait(stop, self.refresh_s)
                continue
            try:
                async with connect(self.url, ping_interval=20, ping_timeout=20, max_size=16 * 2**20,
                                   open_timeout=15, close_timeout=5) as ws:
                    await ws.send(subscribe_frame(assets))
                    self.subscribed = assets
                    self.connected = True
                    self.last_rx = time.monotonic()
                    backoff = 1.0
                    await self._session(ws, stop)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network errors, handshake failures, protocol errors
                log.warning("market ws error: %r", exc)
            finally:
                self.connected = False
            if stop.is_set():
                break
            self.reconnects += 1
            await _wait(stop, backoff)
            backoff = min(backoff * 2, 60.0)

    async def _session(self, ws, stop: asyncio.Event) -> None:
        next_ping = time.monotonic() + PING_EVERY_S
        next_refresh = time.monotonic() + self.refresh_s
        while not stop.is_set():
            timeout = max(0.1, min(next_ping, next_refresh) - time.monotonic())
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                msg = None
            now = time.monotonic()
            if msg is not None:
                self.last_rx = now
                text = msg if isinstance(msg, str) else msg.decode("utf-8", "replace")
                res = self.on_text(text, int(time.time() * 1000))
                if asyncio.iscoroutine(res):
                    await res
            if now >= next_ping:
                await ws.send("PING")
                next_ping = now + PING_EVERY_S
            if now - self.last_rx > STALE_AFTER_S:
                log.warning("market ws stale (%.0fs silent); reconnecting", now - self.last_rx)
                return
            if now >= next_refresh:
                next_refresh = now + self.refresh_s
                want = set(list(self.desired())[:MAX_ASSETS])
                if not want:
                    return                      # nothing to watch: drop the connection
                add, drop = want - self.subscribed, self.subscribed - want
                if add:
                    await ws.send(update_frame("subscribe", add))
                if drop:
                    await ws.send(update_frame("unsubscribe", drop))
                self.subscribed = want


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
