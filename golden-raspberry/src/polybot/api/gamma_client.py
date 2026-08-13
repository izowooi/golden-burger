"""Cursor-complete Gamma market discovery for the frozen source envelope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import GammaConfig
from ..utils.retry import PublicJsonTransport


@dataclass(frozen=True)
class GammaPage:
    page_number: int
    received_at: str
    request_id: str
    cursor_in: str | None
    cursor_out: str | None
    markets: list[dict[str, Any]]


@dataclass(frozen=True)
class GammaSweep:
    pages: list[GammaPage]
    cursor_complete: bool

    @property
    def markets(self) -> list[dict[str, Any]]:
        return [market for page in self.pages for market in page.markets]


class GammaClient:
    ENDPOINT = "/markets/keyset"

    def __init__(self, config: GammaConfig, transport: PublicJsonTransport) -> None:
        self.config = config
        self.transport = transport

    def collect_market_sweep(self, run_id: str) -> GammaSweep:
        pages: list[GammaPage] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for page_number in range(1, self.config.max_pages + 1):
            params: dict[str, Any] = {
                "limit": self.config.page_size,
                "closed": "false",
                "liquidity_num_min": self.config.min_liquidity,
                "volume_num_min": self.config.min_total_volume,
            }
            if cursor is not None:
                params["after_cursor"] = cursor
            response = self.transport.request_json(
                "GET",
                f"{self.config.base_url}{self.ENDPOINT}",
                request_kind="gamma_markets_keyset",
                run_id=run_id,
                page_number=page_number,
                params=params,
            )
            payload = response.payload
            if not isinstance(payload, dict) or not isinstance(payload.get("markets"), list):
                raise ValueError("Gamma keyset page must contain a markets list")
            markets = payload["markets"]
            if not all(isinstance(item, dict) for item in markets):
                raise ValueError("Gamma markets list contains a non-object item")
            next_cursor = payload.get("next_cursor")
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise ValueError("Gamma next_cursor must be a string or null")
            pages.append(
                GammaPage(
                    page_number=page_number,
                    received_at=response.received_at,
                    request_id=response.request_id,
                    cursor_in=cursor,
                    cursor_out=next_cursor,
                    markets=markets,
                )
            )
            if not next_cursor:
                return GammaSweep(pages=pages, cursor_complete=True)
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise RuntimeError("Gamma keyset returned a repeated cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise RuntimeError("Gamma keyset exceeded max_pages before terminal cursor")


__all__ = ["GammaClient", "GammaPage", "GammaSweep"]
