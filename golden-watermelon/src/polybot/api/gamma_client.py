"""Cursor-complete top-level sports moneyline discovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from ..config import GammaConfig
from ..utils.retry import PublicJsonTransport


@dataclass(frozen=True)
class MarketPage:
    page_number: int
    request_id: str
    received_at: str
    response_sha256: str
    raw: bytes
    markets: tuple[dict[str, Any], ...]
    after_cursor: str | None
    next_cursor: str | None


@dataclass(frozen=True)
class MarketSweep:
    pages: tuple[MarketPage, ...]
    cursor_complete: bool


class GammaClient:
    ENDPOINT = "/markets/keyset"

    def __init__(self, config: GammaConfig, transport: PublicJsonTransport) -> None:
        self.config = config
        self.transport = transport

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def fetch_moneyline_markets(
        self, run_id: str, *, observed_at: datetime
    ) -> MarketSweep:
        observed_at = observed_at.astimezone(timezone.utc)
        after_cursor: str | None = None
        pages: list[MarketPage] = []
        seen_cursors: set[str] = set()
        for page_number in range(1, self.config.max_pages + 1):
            params: dict[str, Any] = {
                "limit": self.config.page_size,
                "closed": "false",
                "sports_market_types": list(self.config.sports_market_types),
                "end_date_min": self._iso(
                    observed_at - timedelta(hours=self.config.lookback_hours)
                ),
                "end_date_max": self._iso(
                    observed_at + timedelta(hours=self.config.lookahead_hours)
                ),
            }
            if after_cursor:
                params["after_cursor"] = after_cursor
            response = self.transport.request_json(
                "GET",
                f"{self.config.base_url}{self.ENDPOINT}",
                request_kind="gamma_moneyline_markets_keyset",
                run_id=run_id,
                page_number=page_number,
                params=params,
            )
            payload = response.payload
            if not isinstance(payload, Mapping):
                raise ValueError("Gamma keyset response must be an object")
            markets = payload.get("markets")
            if not isinstance(markets, list) or any(
                not isinstance(item, Mapping) for item in markets
            ):
                raise ValueError("Gamma keyset markets must be an array of objects")
            next_cursor_raw = payload.get("next_cursor")
            next_cursor = str(next_cursor_raw) if next_cursor_raw else None
            pages.append(
                MarketPage(
                    page_number=page_number,
                    request_id=response.request_id,
                    received_at=response.received_at,
                    response_sha256=response.response_sha256,
                    raw=response.raw,
                    markets=tuple(dict(item) for item in markets),
                    after_cursor=after_cursor,
                    next_cursor=next_cursor,
                )
            )
            if next_cursor is None:
                return MarketSweep(tuple(pages), True)
            if next_cursor in seen_cursors or next_cursor == after_cursor:
                raise ValueError("Gamma keyset cursor did not advance")
            seen_cursors.add(next_cursor)
            after_cursor = next_cursor
        return MarketSweep(tuple(pages), False)
