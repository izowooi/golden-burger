"""Public full-book batching with explicit per-token missingness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..config import OrderBookConfig
from ..utils.retry import PublicApiError, PublicJsonTransport


@dataclass(frozen=True)
class BookAttempt:
    token_id: str
    status: str
    request_id: str | None
    request_started_at: str | None
    received_at: str | None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RawBookPayload:
    request_id: str
    received_at: str
    response_sha256: str
    raw: bytes


@dataclass(frozen=True)
class BookCollection:
    books: dict[str, dict[str, Any]]
    attempts: dict[str, BookAttempt]
    raw_payloads: tuple[RawBookPayload, ...]


class ClobBookClient:
    ENDPOINT = "/books"

    def __init__(self, config: OrderBookConfig, transport: PublicJsonTransport) -> None:
        self.config = config
        self.transport = transport

    def fetch_books(self, run_id: str, token_ids: list[str]) -> BookCollection:
        unique = list(dict.fromkeys(str(token) for token in token_ids if str(token)))
        books: dict[str, dict[str, Any]] = {}
        attempts: dict[str, BookAttempt] = {}
        raw_payloads: list[RawBookPayload] = []
        for offset in range(0, len(unique), self.config.batch_token_limit):
            chunk = unique[offset : offset + self.config.batch_token_limit]
            try:
                response = self.transport.request_json(
                    "POST",
                    f"{self.config.base_url}{self.ENDPOINT}",
                    request_kind="clob_books",
                    run_id=run_id,
                    page_number=offset // self.config.batch_token_limit + 1,
                    json_body=[{"token_id": token} for token in chunk],
                )
            except PublicApiError as error:
                for token in chunk:
                    attempts[token] = BookAttempt(
                        token_id=token,
                        status="ERROR",
                        request_id=error.request_id,
                        request_started_at=None,
                        received_at=None,
                        error_type=type(error).__name__,
                        error_message=str(error)[:500],
                    )
                continue
            raw_payloads.append(
                RawBookPayload(
                    request_id=response.request_id,
                    received_at=response.received_at,
                    response_sha256=response.response_sha256,
                    raw=response.raw,
                )
            )
            payload = response.payload
            if not isinstance(payload, list) or any(
                not isinstance(item, Mapping) for item in payload
            ):
                for token in chunk:
                    attempts[token] = BookAttempt(
                        token_id=token,
                        status="MALFORMED",
                        request_id=response.request_id,
                        request_started_at=response.started_at,
                        received_at=response.received_at,
                        error_type="PayloadShapeError",
                        error_message="CLOB /books response must be a list of objects",
                    )
                continue
            expected = set(chunk)
            returned: set[str] = set()
            response_invalid = False
            for raw_item in payload:
                item = dict(raw_item)
                token = str(item.get("asset_id") or "")
                if not token or token not in expected or token in returned:
                    response_invalid = True
                    continue
                returned.add(token)
                bids = item.get("bids")
                asks = item.get("asks")
                if not isinstance(bids, list) or not isinstance(asks, list):
                    status = "MALFORMED"
                    error_type = "BookShapeError"
                    error_message = "book bids and asks must both be arrays"
                elif not bids and not asks:
                    status = "EMPTY_BOOK"
                    error_type = None
                    error_message = None
                else:
                    status = "OBSERVED"
                    error_type = None
                    error_message = None
                    books[token] = item
                attempts[token] = BookAttempt(
                    token_id=token,
                    status=status,
                    request_id=response.request_id,
                    request_started_at=response.started_at,
                    received_at=response.received_at,
                    error_type=error_type,
                    error_message=error_message,
                )
            for token in expected - returned:
                attempts[token] = BookAttempt(
                    token_id=token,
                    status="MALFORMED" if response_invalid else "MISSING",
                    request_id=response.request_id,
                    request_started_at=response.started_at,
                    received_at=response.received_at,
                    error_type="UnexpectedAssetError" if response_invalid else None,
                    error_message=(
                        "response contained an unexpected or duplicate asset_id"
                        if response_invalid
                        else None
                    ),
                )
        return BookCollection(
            books=books,
            attempts=attempts,
            raw_payloads=tuple(raw_payloads),
        )


__all__ = [
    "BookAttempt",
    "BookCollection",
    "ClobBookClient",
    "RawBookPayload",
]
