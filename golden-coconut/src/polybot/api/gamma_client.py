"""Five independent cursor-complete Gamma event keyset sweeps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..config import GammaConfig
from ..registry import SportFamily
from .transport import CycleBudget, PublicJsonTransport


@dataclass(frozen=True)
class EventPage:
    family: str
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
    family: str
    tag_id: int
    pages: tuple[EventPage, ...]
    cursor_complete: bool
    terminal_cursor: str | None


class GammaClient:
    def __init__(self, config: GammaConfig, transport: PublicJsonTransport) -> None:
        self.config = config
        self.transport = transport

    def fetch_family_events(
        self,
        run_id: str,
        family: SportFamily,
        *,
        budget: CycleBudget,
    ) -> EventSweep:
        after_cursor: str | None = None
        seen: set[str] = set()
        pages: list[EventPage] = []
        for page_number in range(1, self.config.max_pages_per_family + 1):
            params: dict[str, Any] = {
                "limit": self.config.page_size,
                "closed": "false",
                "live": "true",
                "tag_id": family.tag_id,
                "related_tags": "false",
            }
            if after_cursor is not None:
                params["after_cursor"] = after_cursor
            response = self.transport.request_json(
                "GET",
                f"{self.config.base_url}{self.config.endpoint}",
                request_kind="gamma_events_keyset",
                run_id=run_id,
                family=family.code,
                page_number=page_number,
                params=params,
                budget=budget,
            )
            payload = response.payload
            if not isinstance(payload, Mapping):
                raise ValueError("Gamma /events/keyset response must be an object")
            raw_events = payload.get("events")
            if not isinstance(raw_events, list) or any(
                not isinstance(item, Mapping) for item in raw_events
            ):
                raise ValueError("Gamma keyset events must be an array of objects")
            raw_next = payload.get("next_cursor")
            next_cursor = str(raw_next).strip() if raw_next not in (None, "") else None
            pages.append(
                EventPage(
                    family=family.code,
                    page_number=page_number,
                    request_id=response.request_id,
                    received_at=response.received_at,
                    response_sha256=response.response_sha256,
                    raw=response.raw,
                    events=tuple(dict(item) for item in raw_events),
                    after_cursor=after_cursor,
                    next_cursor=next_cursor,
                )
            )
            if next_cursor is None:
                return EventSweep(family.code, family.tag_id, tuple(pages), True, after_cursor)
            if next_cursor == after_cursor or next_cursor in seen:
                raise ValueError(f"Gamma {family.code} keyset cursor repeated")
            seen.add(next_cursor)
            after_cursor = next_cursor
        return EventSweep(family.code, family.tag_id, tuple(pages), False, after_cursor)
