"""Public Gamma and CLOB reads for the order-free shadow runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .config import ClobSettings, GammaSettings
from .transport import PublicGetTransport, PublicRequestError


@dataclass(frozen=True)
class GammaPage:
    page_number: int
    after_cursor: str | None
    next_cursor: str | None
    request_id: str
    received_at: str
    response_sha256: str
    raw: bytes
    markets: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class GammaSweep:
    pages: tuple[GammaPage, ...]
    cursor_complete: bool


@dataclass(frozen=True)
class BookRead:
    token_id: str
    status: str
    request_id: str | None
    received_at: str | None
    response_sha256: str | None
    raw: bytes | None
    book: dict[str, Any] | None
    error_type: str | None = None


@dataclass(frozen=True)
class ResolutionRead:
    condition_id: str
    status: str
    request_id: str | None
    received_at: str | None
    response_sha256: str | None
    raw: bytes | None
    market: dict[str, Any] | None


class ShadowGammaClient:
    def __init__(self, settings: GammaSettings, transport: PublicGetTransport) -> None:
        self.settings = settings
        self.transport = transport

    def fetch_sweep(self, run_id: str) -> GammaSweep:
        pages: list[GammaPage] = []
        cursor: str | None = None
        seen: set[str] = set()
        for page_number in range(1, self.settings.max_pages + 1):
            params: dict[str, Any] = {
                "closed": "false",
                "include_tag": "true",
                "limit": self.settings.page_size,
                "liquidity_num_min": self.settings.min_liquidity,
                "volume_num_min": self.settings.min_total_volume,
            }
            if cursor:
                params["after_cursor"] = cursor
            response = self.transport.get_json(
                f"{self.settings.base_url}/markets/keyset",
                request_kind="gamma_markets_keyset",
                run_id=run_id,
                page_number=page_number,
                params=params,
            )
            if not isinstance(response.payload, Mapping):
                raise ValueError("Gamma keyset response must be an object")
            raw_markets = response.payload.get("markets")
            if not isinstance(raw_markets, list) or any(
                not isinstance(item, Mapping) for item in raw_markets
            ):
                raise ValueError("Gamma keyset markets must be an array of objects")
            next_raw = response.payload.get("next_cursor")
            next_cursor = str(next_raw) if next_raw else None
            pages.append(
                GammaPage(
                    page_number=page_number,
                    after_cursor=cursor,
                    next_cursor=next_cursor,
                    request_id=response.request_id,
                    received_at=response.received_at,
                    response_sha256=response.response_sha256,
                    raw=response.raw,
                    markets=tuple(dict(item) for item in raw_markets),
                )
            )
            if next_cursor is None:
                return GammaSweep(tuple(pages), True)
            if next_cursor == cursor or next_cursor in seen:
                raise ValueError("Gamma keyset cursor repeated")
            seen.add(next_cursor)
            cursor = next_cursor
        return GammaSweep(tuple(pages), False)

    def fetch_resolution(self, run_id: str, condition_id: str) -> ResolutionRead:
        response = self.transport.get_json(
            f"{self.settings.base_url}/markets",
            request_kind="gamma_exact_resolution",
            run_id=run_id,
            params={"condition_ids": condition_id, "closed": "true", "limit": 2},
        )
        payload = response.payload
        if not isinstance(payload, list):
            raise ValueError("Gamma resolution response must be an array")
        matches = [
            dict(item)
            for item in payload
            if isinstance(item, Mapping)
            and str(item.get("conditionId") or "") == str(condition_id)
            and item.get("closed") is True
        ]
        if len(matches) > 1:
            raise ValueError("Gamma resolution returned duplicate exact condition IDs")
        return ResolutionRead(
            condition_id=condition_id,
            status="OBSERVED" if matches else "NOT_FINAL",
            request_id=response.request_id,
            received_at=response.received_at,
            response_sha256=response.response_sha256,
            raw=response.raw,
            market=matches[0] if matches else None,
        )


class ShadowClobClient:
    def __init__(self, settings: ClobSettings, transport: PublicGetTransport) -> None:
        self.settings = settings
        self.transport = transport

    def fetch_book(self, run_id: str, token_id: str) -> BookRead:
        try:
            response = self.transport.get_json(
                f"{self.settings.base_url}/book",
                request_kind="clob_full_book",
                run_id=run_id,
                params={"token_id": token_id},
            )
        except PublicRequestError as error:
            if error.status_code == 404:
                return BookRead(
                    token_id=token_id,
                    status="NO_BOOK",
                    request_id=error.request_id,
                    received_at=None,
                    response_sha256=None,
                    raw=None,
                    book=None,
                    error_type="HTTP_404",
                )
            raise
        if not isinstance(response.payload, Mapping):
            raise ValueError("CLOB book response must be an object")
        book = dict(response.payload)
        asset_id = str(book.get("asset_id") or "")
        if asset_id != str(token_id):
            raise ValueError("CLOB book asset_id does not match requested token")
        if not isinstance(book.get("bids"), list) or not isinstance(book.get("asks"), list):
            raise ValueError("CLOB full book must contain bids and asks arrays")
        return BookRead(
            token_id=token_id,
            status="OBSERVED",
            request_id=response.request_id,
            received_at=response.received_at,
            response_sha256=response.response_sha256,
            raw=response.raw,
            book=book,
        )
