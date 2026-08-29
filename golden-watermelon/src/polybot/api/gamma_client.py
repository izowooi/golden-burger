"""Cursor-complete server-filtered live event discovery."""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    sport_family: str = "soccer"


@dataclass(frozen=True)
class EventSweep:
    pages: tuple[EventPage, ...]
    cursor_complete: bool


class GammaClient:
    ENDPOINT = "/events/keyset"
    # Legacy v3 used tag_slug; v3a deliberately sends only numeric tag_id.

    def __init__(self, config: GammaConfig, transport: PublicJsonTransport) -> None:
        self.config = config
        self.transport = transport

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def fetch_live_events(
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
                "tag_id": self.config.tag_id,
                "related_tags": "false",
            }
            if after_cursor:
                params["after_cursor"] = after_cursor
            response = self.transport.request_json(
                "GET",
                f"{self.config.base_url}{self.ENDPOINT}",
                request_kind=f"gamma_live_events_keyset:{self.config.sport_family}",
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
                    sport_family=self.config.sport_family,
                )
            )
            if next_cursor is None:
                return EventSweep(tuple(pages), True)
            if next_cursor in seen_cursors or next_cursor == after_cursor:
                raise ValueError("Gamma keyset cursor did not advance")
            seen_cursors.add(next_cursor)
            after_cursor = next_cursor
        return EventSweep(tuple(pages), False)

    def fetch_live_families(
        self, run_id: str, *, observed_at: datetime
    ) -> EventSweep:
        """Collect each frozen family with an independent numeric-tag cursor."""
        pages: list[EventPage] = []
        cursor_complete = True
        global_page = 0
        for family in self.config.sport_families:
            tag_id = self.config.family_tags[family]
            family_config = replace(
                self.config,
                tag_id=tag_id,
                sport_family=family,
                required_common_tag_ids=(1, 100639, tag_id),
            )
            sweep = GammaClient(family_config, self.transport).fetch_live_events(
                run_id,
                observed_at=observed_at,
            )
            cursor_complete = cursor_complete and sweep.cursor_complete
            for page in sweep.pages:
                global_page += 1
                pages.append(replace(page, page_number=global_page))
        return EventSweep(tuple(pages), cursor_complete)
