"""Sports WebSocket — wss://sports-api.polymarket.com/ws (public, no subscribe frame; all active games pushed).

Verified facts (docs/research/api-sources.md, fixtures ws_sports_capture*.json):
- frames are bare JSON objects: gameId, leagueAbbreviation, homeTeam, awayTeam, status, score, period,
  live, ended, elapsed (elapsed seen for soccer only). Cricket frames carry metadataGameId and no gameId.
- docs say the server sends text "ping" every 5 s and expects "pong"; 0 were observed in 5 minutes,
  so we answer text pings when they come but do NOT treat their absence as staleness (the SDK's
  30-s text-ping rule would churn). Liveness relies on protocol-level pings (websockets library).
- `gameId` joins to Gamma `event.gameId` (-> games.polymarket_game_id).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable

from polylab.api.http import SPORTS_WS_URL

log = logging.getLogger("polylab.ws_sports")


def parse_frame(text: str) -> list[dict]:
    if not text or text.strip().lower() in ("ping", "pong"):
        return []
    try:
        obj = json.loads(text)
    except ValueError:
        return []
    if isinstance(obj, list):
        return [o for o in obj if isinstance(o, dict)]
    return [obj] if isinstance(obj, dict) else []


def game_id_of(frame: dict) -> str | None:
    gid = frame.get("gameId")
    if gid is None:
        gid = frame.get("metadataGameId")
    return str(gid) if gid is not None else None


class SportsFeed:
    def __init__(self, on_text: Callable[[str, int], Awaitable[None] | None], url: str = SPORTS_WS_URL):
        self.on_text = on_text
        self.url = url
        self.connected = False
        self.reconnects = 0
        self.last_rx = 0.0

    async def run(self, stop: asyncio.Event) -> None:
        from websockets.asyncio.client import connect

        backoff = 1.0
        while not stop.is_set():
            try:
                async with connect(self.url, ping_interval=20, ping_timeout=30, max_size=4 * 2**20,
                                   open_timeout=15, close_timeout=5) as ws:
                    self.connected = True
                    backoff = 1.0
                    while not stop.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=5)
                        except asyncio.TimeoutError:
                            continue
                        self.last_rx = time.monotonic()
                        text = msg if isinstance(msg, str) else msg.decode("utf-8", "replace")
                        if text.strip().lower() == "ping":
                            await ws.send("pong")
                            continue
                        res = self.on_text(text, int(time.time() * 1000))
                        if asyncio.iscoroutine(res):
                            await res
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("sports ws error: %r", exc)
            finally:
                self.connected = False
            if stop.is_set():
                break
            self.reconnects += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 60.0)
