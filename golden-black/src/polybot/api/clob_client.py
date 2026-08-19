"""Exact public CLOB book, fee, and one-hot resolution reads."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from ..config import OrderBookConfig
from ..utils.retry import PublicApiError, PublicJsonTransport


@dataclass(frozen=True)
class BookAttempt:
    token_id: str
    status: str
    request_id: str | None
    received_at: str | None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RawPayload:
    request_id: str
    received_at: str
    response_sha256: str
    raw: bytes


@dataclass(frozen=True)
class BookCollection:
    books: dict[str, dict[str, Any]]
    attempts: dict[str, BookAttempt]
    raw_payloads: tuple[RawPayload, ...]


@dataclass(frozen=True)
class BookWalk:
    best_ask: float
    vwap: float
    shares: float
    cost: float
    levels_used: int


@dataclass(frozen=True)
class BidWalk:
    best_bid: float
    vwap: float
    requested_shares: float
    filled_shares: float
    remaining_shares: float
    proceeds: float
    levels_used: int

    @property
    def complete(self) -> bool:
        return self.remaining_shares <= 1e-7


@dataclass(frozen=True)
class ResolutionResult:
    condition_id: str
    status: str
    observed_at: str | None
    request_id: str | None
    winner_index: int | None
    market: dict[str, Any] | None
    raw_payload: RawPayload | None
    error_type: str | None = None
    error_message: str | None = None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalized_levels(book: Mapping[str, Any], side: str) -> list[tuple[float, float]]:
    raw = book.get(side)
    if not isinstance(raw, list):
        return []
    rows: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        price = _number(item.get("price"))
        size = _number(item.get("size"))
        if price is not None and size is not None and 0 < price <= 1 and size > 0:
            rows.append((price, size))
    return sorted(rows, key=lambda row: row[0], reverse=side == "bids")


def walk_asks(book: Mapping[str, Any], notional: float) -> BookWalk | None:
    if notional <= 0:
        raise ValueError("notional must be positive")
    remaining = notional
    shares = 0.0
    levels_used = 0
    asks = normalized_levels(book, "asks")
    for price, size in asks:
        available_cost = price * size
        spent = min(remaining, available_cost)
        shares += spent / price
        remaining -= spent
        levels_used += 1
        if remaining <= 1e-9:
            break
    if remaining > 1e-7 or shares <= 0 or not asks:
        return None
    return BookWalk(
        best_ask=asks[0][0],
        vwap=notional / shares,
        shares=shares,
        cost=notional,
        levels_used=levels_used,
    )


def walk_bids_partial(book: Mapping[str, Any], shares: float) -> BidWalk | None:
    if shares <= 0:
        raise ValueError("shares must be positive")
    remaining = shares
    proceeds = 0.0
    consumed = 0.0
    levels_used = 0
    bids = normalized_levels(book, "bids")
    for price, size in bids:
        sold = min(remaining, size)
        proceeds += sold * price
        consumed += sold
        remaining -= sold
        levels_used += 1
        if remaining <= 1e-9:
            break
    if consumed <= 0 or not bids:
        return None
    return BidWalk(
        best_bid=bids[0][0],
        vwap=proceeds / consumed,
        requested_shares=shares,
        filled_shares=consumed,
        remaining_shares=max(0.0, remaining),
        proceeds=proceeds,
        levels_used=levels_used,
    )


def walk_bids(book: Mapping[str, Any], shares: float) -> BookWalk | None:
    result = walk_bids_partial(book, shares)
    if result is None or not result.complete:
        return None
    return BookWalk(
        best_ask=result.best_bid,
        vwap=result.vwap,
        shares=result.filled_shares,
        cost=result.proceeds,
        levels_used=result.levels_used,
    )


class ClobClient:
    def __init__(self, config: OrderBookConfig, transport: PublicJsonTransport) -> None:
        self.config = config
        self.transport = transport

    def fetch_books(self, run_id: str, token_ids: list[str]) -> BookCollection:
        unique = list(dict.fromkeys(str(value) for value in token_ids if str(value)))
        books: dict[str, dict[str, Any]] = {}
        attempts: dict[str, BookAttempt] = {}
        payloads: list[RawPayload] = []
        for offset in range(0, len(unique), self.config.batch_token_limit):
            chunk = unique[offset : offset + self.config.batch_token_limit]
            try:
                response = self.transport.request_json(
                    "POST", f"{self.config.base_url}/books",
                    request_kind="clob_books", run_id=run_id,
                    page_number=offset // self.config.batch_token_limit + 1,
                    json_body=[{"token_id": token} for token in chunk],
                )
            except PublicApiError as error:
                for token in chunk:
                    attempts[token] = BookAttempt(token, "ERROR", error.request_id, None, type(error).__name__, str(error)[:500])
                continue
            payloads.append(RawPayload(response.request_id, response.received_at, response.response_sha256, response.raw))
            payload = response.payload
            if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
                for token in chunk:
                    attempts[token] = BookAttempt(token, "MALFORMED", response.request_id, response.received_at, "PayloadShapeError", "CLOB /books response must be an array")
                continue
            returned: set[str] = set()
            for raw in payload:
                item = dict(raw)
                token = str(item.get("asset_id") or "")
                if token not in chunk or token in returned:
                    continue
                returned.add(token)
                bids = item.get("bids")
                asks = item.get("asks")
                if not isinstance(bids, list) or not isinstance(asks, list):
                    attempts[token] = BookAttempt(token, "MALFORMED", response.request_id, response.received_at, "BookShapeError", "bids/asks missing")
                elif not bids and not asks:
                    attempts[token] = BookAttempt(token, "EMPTY_BOOK", response.request_id, response.received_at)
                else:
                    attempts[token] = BookAttempt(token, "OBSERVED", response.request_id, response.received_at)
                    books[token] = item
            for token in set(chunk) - returned:
                attempts[token] = BookAttempt(token, "MISSING", response.request_id, response.received_at)
        return BookCollection(books, attempts, tuple(payloads))

    def fetch_resolution(self, run_id: str, condition_id: str) -> ResolutionResult:
        try:
            response = self.transport.request_json(
                "GET", f"{self.config.base_url}/markets/{condition_id}",
                request_kind="clob_market_resolution", run_id=run_id,
            )
            payload = response.payload
            if not isinstance(payload, Mapping):
                raise ValueError("CLOB market info must be an object")
            market = dict(payload)
            tokens = market.get("tokens")
            winners = [
                index for index, token in enumerate(tokens or [])
                if isinstance(token, Mapping) and token.get("winner") is True
            ]
            closed = market.get("closed") is True
            status = "RESOLVED" if closed and len(tokens or []) == 2 and len(winners) == 1 else ("CLOSED_UNRESOLVED" if closed else "OPEN")
            raw_payload = RawPayload(response.request_id, response.received_at, response.response_sha256, response.raw)
            return ResolutionResult(condition_id, status, response.received_at, response.request_id, winners[0] if status == "RESOLVED" else None, market, raw_payload)
        except (PublicApiError, ValueError) as error:
            return ResolutionResult(condition_id, "ERROR", None, getattr(error, "request_id", None), None, None, None, type(error).__name__, str(error)[:500])
