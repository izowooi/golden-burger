"""Public full-book, optional fee, and terminal market observations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from ..config import ClobConfig
from .transport import CycleBudget, PublicApiError, PublicJsonTransport


class MalformedBookError(ValueError):
    """The public full-book payload cannot be safely replayed."""


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class ParsedBook:
    token_id: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    source_timestamp: str | None
    tick_size: float | None
    min_size: float | None


@dataclass(frozen=True)
class BookWalk:
    status: str
    requested: float
    filled: float
    remaining: float
    shares: float
    vwap: float | None
    worst_price: float | None
    levels_used: int


@dataclass(frozen=True)
class BookAttempt:
    token_id: str
    status: str
    request_id: str | None
    received_at: str | None
    raw: Mapping[str, Any] | None = None
    parsed: ParsedBook | None = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class FeeObservation:
    token_id: str
    status: str
    request_id: str | None
    received_at: str | None
    fee_rate_bps: float | None
    raw: bytes | None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ResolutionObservation:
    condition_id: str
    status: str
    request_id: str | None
    received_at: str | None
    winner_indices: tuple[int, ...]
    raw: bytes | None
    payload: Mapping[str, Any] | None
    error_type: str | None = None
    error_message: str | None = None


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _canonical_decimal(value: Any) -> str:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise MalformedBookError("book level is not decimal") from error
    if not decimal.is_finite():
        raise MalformedBookError("book level is not finite")
    return format(decimal.normalize(), "f")


def _levels(raw: Any, *, side: str) -> tuple[BookLevel, ...]:
    if not isinstance(raw, list):
        raise MalformedBookError(f"{side} must be an array")
    result: list[BookLevel] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise MalformedBookError(f"{side} level must be an object")
        price = _finite(item.get("price"))
        size = _finite(item.get("size"))
        if price is None or size is None or not 0 < price <= 1 or size <= 0:
            raise MalformedBookError(f"{side} level is outside the valid range")
        result.append(BookLevel(price, size))
    result.sort(key=lambda level: level.price, reverse=side == "bids")
    return tuple(result)


def parse_book(raw: Mapping[str, Any], expected_token: str) -> ParsedBook:
    token = str(raw.get("asset_id") or "")
    if token != expected_token:
        raise MalformedBookError("book asset_id does not match requested token")
    bids = _levels(raw.get("bids"), side="bids")
    asks = _levels(raw.get("asks"), side="asks")
    return ParsedBook(
        token_id=token,
        bids=bids,
        asks=asks,
        source_timestamp=str(raw.get("timestamp") or "") or None,
        tick_size=_finite(raw.get("tick_size")),
        min_size=_finite(raw.get("min_order_size")),
    )


def canonical_book_gzip(raw: Mapping[str, Any], expected_token: str) -> tuple[bytes, str, int]:
    parse_book(raw, expected_token)
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(canonical, compresslevel=6, mtime=0)
    return compressed, hashlib.sha256(canonical).hexdigest(), len(canonical)


def walk_asks(levels: Sequence[BookLevel], notional: float) -> BookWalk:
    if notional <= 0 or not math.isfinite(notional):
        raise ValueError("ask notional must be positive and finite")
    remaining = notional
    spent = 0.0
    shares = 0.0
    worst: float | None = None
    used = 0
    for level in levels:
        available = level.price * level.size
        consumed = min(remaining, available)
        if consumed <= 0:
            continue
        spent += consumed
        shares += consumed / level.price
        remaining -= consumed
        worst = level.price
        used += 1
        if remaining <= 1e-9:
            remaining = 0.0
            break
    status = "FULL" if remaining == 0 else ("PARTIAL" if spent > 0 else "EMPTY")
    return BookWalk(
        status=status,
        requested=notional,
        filled=spent,
        remaining=max(0.0, remaining),
        shares=shares,
        vwap=spent / shares if shares > 0 else None,
        worst_price=worst,
        levels_used=used,
    )


def walk_bids(levels: Sequence[BookLevel], shares: float) -> BookWalk:
    if shares <= 0 or not math.isfinite(shares):
        raise ValueError("bid shares must be positive and finite")
    remaining = shares
    sold = 0.0
    proceeds = 0.0
    worst: float | None = None
    used = 0
    for level in levels:
        consumed = min(remaining, level.size)
        if consumed <= 0:
            continue
        sold += consumed
        proceeds += consumed * level.price
        remaining -= consumed
        worst = level.price
        used += 1
        if remaining <= 1e-9:
            remaining = 0.0
            break
    status = "FULL" if remaining == 0 else ("PARTIAL" if sold > 0 else "EMPTY")
    return BookWalk(
        status=status,
        requested=shares,
        filled=sold,
        remaining=max(0.0, remaining),
        shares=sold,
        vwap=proceeds / sold if sold > 0 else None,
        worst_price=worst,
        levels_used=used,
    )


def classify_resolution(payload: Mapping[str, Any]) -> tuple[str, tuple[int, ...]]:
    tokens = payload.get("tokens")
    if not isinstance(tokens, list) or len(tokens) != 2 or any(
        not isinstance(item, Mapping) for item in tokens
    ):
        return "MALFORMED", ()
    closed = payload.get("closed") is True
    if not closed:
        return "OPEN", ()
    winners = tuple(index for index, item in enumerate(tokens) if item.get("winner") is True)
    if len(winners) == 1:
        return "RESOLVED", winners
    if len(winners) == 2 or str(payload.get("resolution") or "").casefold() in {"tie", "draw"}:
        return "TIE", winners
    status_text = " ".join(
        str(payload.get(key) or "").casefold()
        for key in ("resolution", "status", "umaResolutionStatus")
    )
    prices = [_finite(item.get("price")) for item in tokens]
    if "void" in status_text or "cancel" in status_text or prices == [0.5, 0.5]:
        return "VOID", ()
    return "CLOSED_UNRESOLVED", ()


class ClobClient:
    def __init__(self, config: ClobConfig, transport: PublicJsonTransport) -> None:
        self.config = config
        self.transport = transport

    def fetch_books(
        self, run_id: str, token_ids: Sequence[str], *, budget: CycleBudget
    ) -> dict[str, BookAttempt]:
        unique = list(dict.fromkeys(str(token) for token in token_ids if str(token)))
        result: dict[str, BookAttempt] = {}
        for offset in range(0, len(unique), self.config.batch_token_limit):
            chunk = unique[offset : offset + self.config.batch_token_limit]
            try:
                response = self.transport.request_json(
                    "POST",
                    f"{self.config.base_url}/books",
                    request_kind="clob_full_books",
                    run_id=run_id,
                    page_number=offset // self.config.batch_token_limit + 1,
                    json_body=[{"token_id": token} for token in chunk],
                    budget=budget,
                )
            except PublicApiError as error:
                for token in chunk:
                    result[token] = BookAttempt(
                        token, "ERROR", error.request_id, None,
                        error_type=type(error).__name__, error_message=str(error)[:500]
                    )
                continue
            payload = response.payload
            if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
                for token in chunk:
                    result[token] = BookAttempt(
                        token, "MALFORMED", response.request_id, response.received_at,
                        error_type="PayloadShapeError", error_message="/books response must be an array"
                    )
                continue
            returned: set[str] = set()
            for item in payload:
                raw = dict(item)
                token = str(raw.get("asset_id") or "")
                if token not in chunk or token in returned:
                    continue
                returned.add(token)
                try:
                    parsed = parse_book(raw, token)
                except MalformedBookError as error:
                    result[token] = BookAttempt(
                        token, "MALFORMED", response.request_id, response.received_at,
                        raw=raw, error_type=type(error).__name__, error_message=str(error)[:500]
                    )
                else:
                    status = "EMPTY" if not parsed.bids and not parsed.asks else "OBSERVED"
                    result[token] = BookAttempt(
                        token, status, response.request_id, response.received_at,
                        raw=raw, parsed=parsed
                    )
            for token in set(chunk) - returned:
                result[token] = BookAttempt(token, "MISSING", response.request_id, response.received_at)
        return result

    def fetch_fee(
        self, run_id: str, token_id: str, *, budget: CycleBudget
    ) -> FeeObservation:
        if not self.config.collect_public_fee:
            return FeeObservation(token_id, "NOT_REQUESTED", None, None, None, None)
        try:
            response = self.transport.request_json(
                "GET",
                f"{self.config.base_url}/fee-rate",
                request_kind="clob_public_fee",
                run_id=run_id,
                params={"token_id": token_id},
                budget=budget,
            )
            if not isinstance(response.payload, Mapping):
                raise ValueError("public fee response must be an object")
            raw_rate = response.payload.get("base_fee", response.payload.get("fee_rate_bps"))
            rate = _finite(raw_rate)
            if rate is None or rate < 0:
                raise ValueError("public fee response has no valid fee rate")
            return FeeObservation(
                token_id, "OBSERVED", response.request_id, response.received_at,
                rate, response.raw
            )
        except (PublicApiError, ValueError) as error:
            return FeeObservation(
                token_id, "UNAVAILABLE", getattr(error, "request_id", None), None,
                None, None, type(error).__name__, str(error)[:500]
            )

    def fetch_resolution(
        self, run_id: str, condition_id: str, *, budget: CycleBudget
    ) -> ResolutionObservation:
        try:
            response = self.transport.request_json(
                "GET",
                f"{self.config.base_url}/markets/{condition_id}",
                request_kind="clob_public_resolution",
                run_id=run_id,
                budget=budget,
            )
            if not isinstance(response.payload, Mapping):
                raise ValueError("public market response must be an object")
            status, winners = classify_resolution(response.payload)
            return ResolutionObservation(
                condition_id, status, response.request_id, response.received_at,
                winners, response.raw, dict(response.payload)
            )
        except (PublicApiError, ValueError) as error:
            return ResolutionObservation(
                condition_id, "ERROR", getattr(error, "request_id", None), None,
                (), None, None, type(error).__name__, str(error)[:500]
            )


class ClobClientPool:
    """Bounded parallel public CLOB reads with one session per worker."""

    def __init__(
        self,
        clients: Sequence[ClobClient],
        *,
        max_workers: int,
    ) -> None:
        self.clients = tuple(clients)
        self.max_workers = max_workers
        if not self.clients or self.max_workers != len(self.clients):
            raise ValueError("CLOB pool requires one isolated client per worker")

    def fetch_books(
        self, run_id: str, token_ids: Sequence[str], *, budget: CycleBudget
    ) -> dict[str, BookAttempt]:
        return self.clients[0].fetch_books(run_id, token_ids, budget=budget)

    def fetch_fees(
        self, run_id: str, token_ids: Sequence[str], *, budget: CycleBudget
    ) -> dict[str, FeeObservation]:
        unique = tuple(dict.fromkeys(str(token) for token in token_ids if str(token)))
        buckets: list[list[str]] = [[] for _ in self.clients]
        for index, token in enumerate(unique):
            buckets[index % len(buckets)].append(token)

        def fetch_worker(index: int) -> tuple[FeeObservation, ...]:
            return tuple(
                self.clients[index].fetch_fee(run_id, token, budget=budget)
                for token in buckets[index]
            )

        with ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="coconut-clob-fee",
        ) as executor:
            futures = {
                index: executor.submit(fetch_worker, index)
                for index, bucket in enumerate(buckets)
                if bucket
            }
            by_token = {
                row.token_id: row
                for index in sorted(futures)
                for row in futures[index].result()
            }
        return {token: by_token[token] for token in unique}

    def fetch_resolutions(
        self, run_id: str, condition_ids: Sequence[str], *, budget: CycleBudget
    ) -> dict[str, ResolutionObservation]:
        unique = tuple(
            dict.fromkeys(
                str(condition) for condition in condition_ids if str(condition)
            )
        )
        buckets: list[list[str]] = [[] for _ in self.clients]
        for index, condition in enumerate(unique):
            buckets[index % len(buckets)].append(condition)

        def fetch_worker(index: int) -> tuple[ResolutionObservation, ...]:
            return tuple(
                self.clients[index].fetch_resolution(
                    run_id, condition, budget=budget
                )
                for condition in buckets[index]
            )

        with ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="coconut-clob-resolution",
        ) as executor:
            futures = {
                index: executor.submit(fetch_worker, index)
                for index, bucket in enumerate(buckets)
                if bucket
            }
            by_condition = {
                row.condition_id: row
                for index in sorted(futures)
                for row in futures[index].result()
            }
        return {condition: by_condition[condition] for condition in unique}
