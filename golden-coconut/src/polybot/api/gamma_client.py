"""Five independent cursor-complete Gamma event keyset sweeps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping

from ..config import GammaConfig
from ..lifecycle import parse_source_utc
from ..registry import SportFamily
from .transport import CycleBudget, PublicJsonTransport, iso_utc


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
    start_time_min: str
    start_time_max: str


@dataclass(frozen=True)
class EventFollowup:
    event_id: str
    request_id: str
    received_at: str
    response_sha256: str
    raw: bytes
    event: Mapping[str, Any]


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
        slot_start: str,
    ) -> EventSweep:
        slot = parse_source_utc(slot_start)
        if slot is None:
            raise ValueError("Gamma discovery slot_start must be exact UTC")
        start_time_min = iso_utc(
            slot - timedelta(hours=self.config.discovery_lookback_hours)
        )
        start_time_max = iso_utc(
            slot + timedelta(hours=self.config.discovery_lookahead_hours)
        )
        after_cursor: str | None = None
        seen: set[str] = set()
        pages: list[EventPage] = []
        for page_number in range(1, self.config.max_pages_per_family + 1):
            params: dict[str, Any] = {
                "limit": self.config.page_size,
                "closed": "false",
                "include_children": "false",
                "tag_id": family.tag_id,
                "related_tags": "false",
                "start_time_min": start_time_min,
                "start_time_max": start_time_max,
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
                return EventSweep(
                    family.code,
                    family.tag_id,
                    tuple(pages),
                    True,
                    after_cursor,
                    start_time_min,
                    start_time_max,
                )
            if next_cursor == after_cursor or next_cursor in seen:
                raise ValueError(f"Gamma {family.code} keyset cursor repeated")
            seen.add(next_cursor)
            after_cursor = next_cursor
        return EventSweep(
            family.code,
            family.tag_id,
            tuple(pages),
            False,
            after_cursor,
            start_time_min,
            start_time_max,
        )

    def fetch_event(
        self,
        run_id: str,
        event_id: str,
        family: str,
        *,
        budget: CycleBudget,
    ) -> EventFollowup:
        normalized = str(event_id).strip()
        if not normalized.isdecimal():
            raise ValueError("Gamma follow-up event_id must be a decimal identifier")
        endpoint = self.config.followup_endpoint_template.format(event_id=normalized)
        response = self.transport.request_json(
            "GET",
            f"{self.config.base_url}{endpoint}",
            request_kind="gamma_event_followup",
            run_id=run_id,
            family=family,
            params={},
            budget=budget,
        )
        if not isinstance(response.payload, Mapping):
            raise ValueError("Gamma event follow-up response must be an object")
        observed_id = str(response.payload.get("id") or "")
        if observed_id != normalized:
            raise ValueError("Gamma event follow-up returned a different event id")
        return EventFollowup(
            event_id=normalized,
            request_id=response.request_id,
            received_at=response.received_at,
            response_sha256=response.response_sha256,
            raw=response.raw,
            event=dict(response.payload),
        )
