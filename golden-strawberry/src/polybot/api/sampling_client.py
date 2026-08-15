"""Complete public CLOB sampling-market census with cursor lineage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..config import SamplingConfig
from ..utils.retry import PublicJsonTransport, iso_utc


@dataclass(frozen=True)
class SamplingPage:
    page_number: int
    cursor_in: str | None
    cursor_out: str | None
    request_id: str
    request_hash: str
    received_at: str
    response_sha256: str
    raw: bytes
    markets: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class SamplingSweep:
    started_at: str
    completed_at: str
    pages: tuple[SamplingPage, ...]
    cursor_complete: bool

    @property
    def markets(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in self.pages:
            for item_number, market in enumerate(page.markets):
                rows.append(
                    {
                        **market,
                        "_page_number": page.page_number,
                        "_item_number": item_number,
                        "_page_received_at": page.received_at,
                        "_page_request_id": page.request_id,
                    }
                )
        return rows


class SamplingMarketClient:
    ENDPOINT = "/sampling-markets"
    TERMINAL_CURSOR = "LTE="

    def __init__(self, config: SamplingConfig, transport: PublicJsonTransport) -> None:
        self.config = config
        self.transport = transport

    def collect_market_sweep(self, run_id: str) -> SamplingSweep:
        started_at = iso_utc()
        cursor: str | None = None
        seen_cursors: set[str] = set()
        pages: list[SamplingPage] = []
        for page_number in range(1, self.config.max_pages + 1):
            params: dict[str, Any] = {}
            if cursor is not None:
                params["next_cursor"] = cursor
            response = self.transport.request_json(
                "GET",
                f"{self.config.base_url}{self.ENDPOINT}",
                request_kind="clob_sampling_markets",
                run_id=run_id,
                page_number=page_number,
                params=params,
            )
            payload = response.payload
            if not isinstance(payload, Mapping):
                raise ValueError("CLOB sampling page must be an object")
            markets_raw = payload.get("data")
            if not isinstance(markets_raw, list) or any(
                not isinstance(item, Mapping) for item in markets_raw
            ):
                raise ValueError("CLOB sampling data must be market objects")
            source_limit = payload.get("limit")
            if source_limit is not None and int(source_limit) != self.config.page_size:
                raise ValueError("CLOB sampling page-size contract changed")
            source_count = payload.get("count")
            if source_count is not None and int(source_count) != len(markets_raw):
                raise ValueError("CLOB sampling count does not match page data")
            cursor_raw = payload.get("next_cursor")
            if cursor_raw is not None and not isinstance(cursor_raw, str):
                raise ValueError("CLOB sampling cursor must be a string or null")
            next_cursor = cursor_raw.strip() if isinstance(cursor_raw, str) else None
            if next_cursor in {"", self.TERMINAL_CURSOR}:
                next_cursor = None
            if next_cursor is None and len(markets_raw) >= self.config.page_size:
                raise ValueError(
                    "full CLOB sampling page is missing a continuation cursor"
                )
            if next_cursor is not None and not markets_raw:
                raise ValueError("empty CLOB sampling page has a continuation cursor")
            pages.append(
                SamplingPage(
                    page_number=page_number,
                    cursor_in=cursor,
                    cursor_out=next_cursor,
                    request_id=response.request_id,
                    request_hash=response.request_hash,
                    received_at=response.received_at,
                    response_sha256=response.response_sha256,
                    raw=response.raw,
                    markets=tuple(dict(item) for item in markets_raw),
                )
            )
            if next_cursor is None:
                return SamplingSweep(
                    started_at=started_at,
                    completed_at=iso_utc(),
                    pages=tuple(pages),
                    cursor_complete=True,
                )
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise RuntimeError("CLOB sampling returned a repeated cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise RuntimeError("CLOB sampling exceeded max_pages before terminal cursor")


__all__ = ["SamplingMarketClient", "SamplingPage", "SamplingSweep"]
