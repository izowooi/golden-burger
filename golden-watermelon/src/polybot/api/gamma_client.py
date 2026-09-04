"""Cursor-complete server-filtered live event discovery."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import math
from typing import Any, Mapping

from ..config import GammaConfig
from ..utils.retry import NetworkBudgetExceeded, PublicApiError, PublicJsonTransport


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
    incomplete_families: tuple[str, ...] = ()
    incomplete_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketResolution:
    condition_id: str
    status: str
    winner_index: int | None
    payouts: tuple[float, float] | None
    outcomes: tuple[str, str] | None
    token_ids: tuple[str, str] | None
    market: dict[str, Any] | None
    request_id: str | None
    received_at: str | None
    response_sha256: str | None
    raw: bytes | None
    error_type: str | None = None
    error_message: str | None = None


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
            try:
                response = self.transport.request_json(
                    "GET",
                    f"{self.config.base_url}{self.ENDPOINT}",
                    request_kind=(
                        "gamma_live_events_keyset:"
                        f"{self.config.sport_family}"
                    ),
                    run_id=run_id,
                    page_number=page_number,
                    params=params,
                )
            except (NetworkBudgetExceeded, PublicApiError) as error:
                return EventSweep(
                    tuple(pages),
                    False,
                    (self.config.sport_family,),
                    (f"{type(error).__name__}:{str(error)[:300]}",),
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
        return EventSweep(
            tuple(pages),
            False,
            (self.config.sport_family,),
            ("page_cap_exhausted",),
        )

    def fetch_live_families(
        self, run_id: str, *, observed_at: datetime
    ) -> EventSweep:
        """Collect each frozen family with an independent numeric-tag cursor."""
        families = tuple(self.config.sport_families)
        if len(families) != 5 or len(set(families)) != 5:
            raise ValueError("Gamma fan-out requires five distinct frozen families")
        fork = getattr(self.transport, "fork", None)
        if not callable(fork):
            raise ValueError("parallel Gamma fan-out requires isolated transports")

        clients: dict[str, tuple[GammaClient, Any]] = {}
        for family in families:
            tag_id = self.config.family_tags[family]
            family_config = replace(
                self.config,
                tag_id=tag_id,
                sport_family=family,
                required_common_tag_ids=(1, 100639, tag_id),
            )
            family_transport = fork()
            if family_transport is self.transport or any(
                family_transport is registered_transport
                for _client, registered_transport in clients.values()
            ):
                raise ValueError(
                    "parallel Gamma fan-out requires one transport per family"
                )
            clients[family] = (
                GammaClient(family_config, family_transport),
                family_transport,
            )

        def fetch(family: str) -> EventSweep:
            client, family_transport = clients[family]
            try:
                return client.fetch_live_events(run_id, observed_at=observed_at)
            finally:
                close = getattr(family_transport, "close", None)
                if callable(close):
                    close()

        with ThreadPoolExecutor(
            max_workers=len(families),
            thread_name_prefix="watermelon-gamma-family",
        ) as executor:
            futures = {
                family: executor.submit(fetch, family) for family in families
            }
            # Resolve in frozen family order even when completion order differs.
            family_sweeps = tuple(futures[family].result() for family in families)

        pages: list[EventPage] = []
        global_page = 0
        incomplete_families: list[str] = []
        incomplete_reasons: list[str] = []
        for family, sweep in zip(families, family_sweeps, strict=True):
            if not sweep.cursor_complete:
                incomplete_families.append(family)
                incomplete_reasons.extend(sweep.incomplete_reasons)
            for page in sweep.pages:
                global_page += 1
                pages.append(replace(page, page_number=global_page))
        return EventSweep(
            tuple(pages),
            not incomplete_families,
            tuple(incomplete_families),
            tuple(incomplete_reasons),
        )

    @staticmethod
    def _binary_array(value: Any) -> list[Any] | None:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, list) else None
        return None

    def fetch_market_resolution(
        self, run_id: str, condition_id: str
    ) -> MarketResolution:
        """Read current then closed Gamma views with exact token alignment."""
        normalized = str(condition_id or "").strip()
        if not normalized:
            raise ValueError("condition_id is required")
        try:
            for closed in (False, True):
                response = self.transport.request_json(
                    "GET",
                    f"{self.config.base_url}/markets",
                    request_kind="gamma_market_resolution",
                    run_id=run_id,
                    params={
                        "condition_ids": normalized,
                        "closed": str(closed).lower(),
                        "limit": 2,
                    },
                )
                payload = response.payload
                if not isinstance(payload, list) or any(
                    not isinstance(item, Mapping) for item in payload
                ):
                    raise ValueError("Gamma market lookup must return an array")
                matches = [
                    dict(item)
                    for item in payload
                    if str(
                        item.get("conditionId") or item.get("condition_id") or ""
                    ).strip()
                    == normalized
                ]
                if len(matches) > 1:
                    raise ValueError("Gamma returned duplicate exact condition rows")
                if payload and len(matches) != len(payload):
                    raise ValueError("Gamma condition lookup identity mismatch")
                if not matches:
                    continue
                market = matches[0]
                if market.get("closed") is not True:
                    return MarketResolution(
                        normalized, "OPEN", None, None, None, None, market,
                        response.request_id, response.received_at,
                        response.response_sha256, response.raw,
                    )
                labels = self._binary_array(market.get("outcomes"))
                tokens = self._binary_array(market.get("clobTokenIds"))
                raw_prices = self._binary_array(market.get("outcomePrices"))
                if not (
                    isinstance(labels, list) and len(labels) == 2
                    and isinstance(tokens, list) and len(tokens) == 2
                    and isinstance(raw_prices, list) and len(raw_prices) == 2
                ):
                    status = "CLOSED_UNRESOLVED"
                    normalized_labels = normalized_tokens = normalized_prices = None
                else:
                    normalized_labels = tuple(str(item).strip() for item in labels)
                    normalized_tokens = tuple(str(item).strip() for item in tokens)
                    try:
                        normalized_prices = tuple(float(item) for item in raw_prices)
                    except (TypeError, ValueError):
                        normalized_prices = None
                    aligned = (
                        all(normalized_labels)
                        and all(normalized_tokens)
                        and len(set(normalized_tokens)) == 2
                        and normalized_prices is not None
                        and all(
                            math.isfinite(item) and 0 <= item <= 1
                            for item in normalized_prices
                        )
                    )
                    if not aligned:
                        status = "CLOSED_UNRESOLVED"
                    elif normalized_prices == (1.0, 0.0):
                        status = "RESOLVED"
                    elif normalized_prices == (0.0, 1.0):
                        status = "RESOLVED"
                    elif (
                        normalized_prices == (0.5, 0.5)
                        and str(
                            market.get("umaResolutionStatus") or ""
                        ).strip().casefold()
                        == "resolved"
                    ):
                        status = "RESOLVED_VOID"
                    else:
                        status = "CLOSED_UNRESOLVED"
                winner_index = (
                    0 if normalized_prices == (1.0, 0.0)
                    else 1 if normalized_prices == (0.0, 1.0)
                    else None
                )
                return MarketResolution(
                    normalized, status, winner_index, normalized_prices,
                    normalized_labels, normalized_tokens, market,
                    response.request_id, response.received_at,
                    response.response_sha256, response.raw,
                )
            return MarketResolution(
                normalized, "NOT_FOUND", None, None, None, None, None,
                None, None, None, None,
            )
        except (NetworkBudgetExceeded, PublicApiError, ValueError) as error:
            return MarketResolution(
                normalized, "ERROR", None, None, None, None, None,
                getattr(error, "request_id", None), None, None, None,
                type(error).__name__, str(error)[:500],
            )
