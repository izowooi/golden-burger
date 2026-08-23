"""Batch public CLOB book collection with explicit per-token missingness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..config import OrderBookConfig
from ..utils.retry import PublicApiError, PublicJsonTransport


@dataclass(frozen=True)
class BookAttempt:
    token_id: str
    status: str
    request_id: str | None
    started_at: str | None
    received_at: str | None
    logical_request_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RawBookPayload:
    request_id: str
    logical_request_id: str
    received_at: str
    response_sha256: str
    raw: bytes


@dataclass(frozen=True)
class BookCollection:
    books: dict[str, dict[str, Any]]
    attempts: dict[str, BookAttempt]
    raw_payloads: list[RawBookPayload]


class ClobBookClient:
    ENDPOINT = "/books"

    def __init__(self, config: OrderBookConfig, transport: PublicJsonTransport) -> None:
        self.config = config
        self.transport = transport

    def fetch_books(
        self,
        run_id: str,
        token_ids: list[str],
        *,
        atomic_pairs: list[tuple[str, str]] | None = None,
        request_kind: str = "clob_universe_books",
        before_request: Callable[[tuple[str, ...], str, str], None] | None = None,
    ) -> BookCollection:
        unique = list(dict.fromkeys(str(token) for token in token_ids if str(token)))
        books: dict[str, dict[str, Any]] = {}
        attempts: dict[str, BookAttempt] = {}
        payloads: list[RawBookPayload] = []
        expected = set(unique)
        claimed: set[str] = set()
        units: list[tuple[str, ...]] = []
        for raw_pair in atomic_pairs or []:
            pair = tuple(str(token) for token in raw_pair)
            if (
                len(pair) != 2
                or pair[0] == pair[1]
                or any(token not in expected for token in pair)
                or any(token in claimed for token in pair)
            ):
                raise ValueError("atomic CLOB pairs must be distinct requested tokens")
            units.append(pair)
            claimed.update(pair)
        units.extend((token,) for token in unique if token not in claimed)

        chunk: list[tuple[str, ...]] = []
        chunk_size = 0
        for unit in units:
            if len(unit) > self.config.batch_token_limit:
                raise ValueError("atomic CLOB pair exceeds batch token limit")
            if chunk and chunk_size + len(unit) > self.config.batch_token_limit:
                self._fetch_chunk(
                    run_id,
                    chunk,
                    books,
                    attempts,
                    payloads,
                    request_kind=request_kind,
                    before_request=before_request,
                )
                chunk = []
                chunk_size = 0
            chunk.append(unit)
            chunk_size += len(unit)
        if chunk:
            self._fetch_chunk(
                run_id,
                chunk,
                books,
                attempts,
                payloads,
                request_kind=request_kind,
                before_request=before_request,
            )
        return BookCollection(books=books, attempts=attempts, raw_payloads=payloads)

    def _fetch_chunk(
        self,
        run_id: str,
        units: list[tuple[str, ...]],
        books: dict[str, dict[str, Any]],
        attempts: dict[str, BookAttempt],
        payloads: list[RawBookPayload],
        *,
        request_kind: str,
        before_request: Callable[[tuple[str, ...], str, str], None] | None,
    ) -> None:
        if not units:
            return
        tokens = [token for unit in units for token in unit]
        try:
            response = self.transport.request_json(
                "POST",
                f"{self.config.base_url}{self.ENDPOINT}",
                request_kind=request_kind,
                run_id=run_id,
                json_body=[{"token_id": token} for token in tokens],
                before_first_attempt=(
                    lambda logical_id, started_at: before_request(
                        tuple(tokens), logical_id, started_at
                    )
                    if before_request is not None
                    else None
                ),
            )
        except PublicApiError as error:
            # Error bisection is safe for the UNIVERSE census because each
            # YES/NO pair remains one unit. A FOLLOWUP_ONLY 4xx has already
            # durably consumed every case's first logical request, so sending
            # split replacement requests would violate the first-attempt
            # contract and is deliberately forbidden.
            if (
                request_kind == "clob_universe_books"
                and error.http_status in {400, 404, 422}
                and len(units) > 1
            ):
                midpoint = len(units) // 2
                self._fetch_chunk(
                    run_id,
                    units[:midpoint],
                    books,
                    attempts,
                    payloads,
                    request_kind=request_kind,
                    before_request=before_request,
                )
                self._fetch_chunk(
                    run_id,
                    units[midpoint:],
                    books,
                    attempts,
                    payloads,
                    request_kind=request_kind,
                    before_request=before_request,
                )
                return
            for token in tokens:
                attempts[token] = BookAttempt(
                    token_id=token,
                    status="ERROR",
                    request_id=None,
                    started_at=None,
                    received_at=None,
                    logical_request_id=None,
                    error_type=type(error).__name__,
                    error_message=str(error)[:500],
                )
            return
        payload = response.payload
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise ValueError("CLOB /books response must be a list of objects")
        payloads.append(
            RawBookPayload(
                request_id=response.request_id,
                logical_request_id=response.logical_request_id or response.request_id,
                received_at=response.received_at,
                response_sha256=response.response_sha256,
                raw=response.raw,
            )
        )
        returned: set[str] = set()
        expected = set(tokens)
        for item in payload:
            token = str(item.get("asset_id") or "")
            if not token or token not in expected:
                raise ValueError("CLOB /books returned an unexpected asset_id")
            if token in returned:
                raise ValueError("CLOB /books returned a duplicate asset_id")
            returned.add(token)
            books[token] = item
            bids = item.get("bids")
            asks = item.get("asks")
            status = "OBSERVED" if isinstance(bids, list) and isinstance(asks, list) else "MALFORMED"
            if status == "OBSERVED" and not bids and not asks:
                status = "EMPTY_BOOK"
            attempts[token] = BookAttempt(
                token_id=token,
                status=status,
                request_id=response.request_id,
                started_at=response.started_at,
                received_at=response.received_at,
                logical_request_id=response.logical_request_id,
            )
        for token in expected - returned:
            attempts[token] = BookAttempt(
                token_id=token,
                status="MISSING",
                request_id=response.request_id,
                started_at=response.started_at,
                received_at=response.received_at,
                logical_request_id=response.logical_request_id,
            )


__all__ = [
    "BookAttempt",
    "BookCollection",
    "ClobBookClient",
    "RawBookPayload",
]
