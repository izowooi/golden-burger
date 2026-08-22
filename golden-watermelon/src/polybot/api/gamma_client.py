"""Cursor-complete live sports event discovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from ..config import GammaConfig
from ..utils.retry import PublicJsonTransport


@dataclass(frozen=True)
class EventPage:
    page_number: int
    request_id: str
    received_at: str
    response_sha256: str
    raw: bytes
    events: tuple[dict[str, Any], ...]
    after_cursor: str | None
    next_cursor: str | None


@dataclass(frozen=True)
class EventSweep:
    pages: tuple[EventPage, ...]
    cursor_complete: bool


class GammaClient:
    ENDPOINT = "/events/keyset"

    def __init__(self, config: GammaConfig, transport: PublicJsonTransport) -> None:
        self.config = config
        self.transport = transport

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def fetch_live_sports_events(
        self, run_id: str, *, observed_at: datetime
    ) -> EventSweep:
        observed_at = observed_at.astimezone(timezone.utc)
        after_cursor: str | None = None
        pages: list[EventPage] = []
        seen_cursors: set[str] = set()
        for page_number in range(1, self.config.max_pages + 1):
            params: dict[str, Any] = {
                "limit": self.config.page_size,
                "closed": "false",
                "live": "true",
                "tag_slug": self.config.tag_slug,
            }
            if after_cursor:
                params["after_cursor"] = after_cursor
            response = self.transport.request_json(
                "GET",
                f"{self.config.base_url}{self.ENDPOINT}",
                request_kind="gamma_live_sports_events_keyset",
                run_id=run_id,
                page_number=page_number,
                params=params,
            )
            payload = response.payload
            if not isinstance(payload, Mapping):
                raise ValueError("Gamma event keyset response must be an object")
            events = payload.get("events")
            if not isinstance(events, list) or any(
                not isinstance(item, Mapping) for item in events
            ):
                raise ValueError("Gamma keyset events must be an array of objects")
            next_cursor_raw = payload.get("next_cursor")
            next_cursor = str(next_cursor_raw) if next_cursor_raw else None
            pages.append(
                EventPage(
                    page_number=page_number,
                    request_id=response.request_id,
                    received_at=response.received_at,
                    response_sha256=response.response_sha256,
                    raw=response.raw,
                    events=tuple(dict(item) for item in events),
                    after_cursor=after_cursor,
                    next_cursor=next_cursor,
                )
            )
            if next_cursor is None:
                return EventSweep(tuple(pages), True)
            if next_cursor in seen_cursors or next_cursor == after_cursor:
                raise ValueError("Gamma keyset cursor did not advance")
            seen_cursors.add(next_cursor)
            after_cursor = next_cursor
        return EventSweep(tuple(pages), False)
