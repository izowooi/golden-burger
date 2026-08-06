"""Deterministic, accountless sampling of exact public CLOB orderbook data."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

import requests
from requests.exceptions import RequestException

from ..config import OrderBookConfig
from ..utils.retry import canonical_json, post_json_once, utc_now


def _list_field(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _validated_book_side(value: Any, *, side: str) -> tuple[list[Any], str | None]:
    """Return a lossless level list or an explicit structural/numeric error."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [], f"{side}_is_invalid_json"
    if not isinstance(value, list):
        return [], f"{side}_is_not_a_list"
    for index, level in enumerate(value):
        if not isinstance(level, Mapping):
            return [], f"{side}[{index}]_is_not_an_object"
        for field_name in ("price", "size"):
            raw = level.get(field_name)
            if raw is None or isinstance(raw, bool):
                return [], f"{side}[{index}].{field_name}_is_not_numeric"
            try:
                number = float(raw)
            except (TypeError, ValueError, OverflowError):
                return [], f"{side}[{index}].{field_name}_is_not_numeric"
            if not math.isfinite(number):
                return [], f"{side}[{index}].{field_name}_is_not_finite"
            if field_name == "price" and not 0 <= number <= 1:
                return [], f"{side}[{index}].price_out_of_range"
            if field_name == "size" and number < 0:
                return [], f"{side}[{index}].size_is_negative"
    return value, None


def _book_validation_error(raw_book: Mapping[str, Any]) -> str | None:
    for side in ("bids", "asks"):
        _, error = _validated_book_side(raw_book.get(side), side=side)
        if error is not None:
            return error
    return None


def _market_key(market: Mapping[str, Any]) -> str:
    for key in ("conditionId", "condition_id", "id", "marketId", "slug"):
        value = market.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "raw:" + hashlib.sha256(canonical_json(dict(market)).encode()).hexdigest()


@dataclass(frozen=True)
class BookCollection:
    collection_id: str
    started_at: str
    completed_at: str
    selections: tuple[Mapping[str, Any], ...]
    books: tuple[Mapping[str, Any], ...]
    token_attempts: tuple[Mapping[str, Any], ...]
    status: str
    error_count: int
    sampler_metadata: Mapping[str, Any] = field(default_factory=dict)


class ClobPublicClient:
    """Read only ``POST /books`` client; it has no order or credential surface."""

    def __init__(
        self,
        config: OrderBookConfig | None = None,
        *,
        evidence_sink: Callable[[Mapping[str, Any]], None] | None = None,
        raw_payload_sink: Callable[..., str] | None = None,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (3.05, 30.0),
    ) -> None:
        self.config = config or OrderBookConfig()
        self.evidence_sink = evidence_sink
        self.raw_payload_sink = raw_payload_sink
        self.timeout = timeout
        self._selection_metadata: dict[str, Any] = {}
        self.session = session or requests.Session()
        # Do not inherit credential-bearing proxies or ``.netrc`` auth from the
        # host running this accountless public collector.
        self.session.trust_env = False
        if hasattr(self.session, "headers"):
            self.session.headers.update(
                {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "GoldenPomegranate-Research/1.0",
                }
            )

    def select_rotating_sample(
        self,
        markets: Sequence[Mapping[str, Any]],
        *,
        cycle_number: int,
        sampler_slot: int | None = None,
    ) -> list[dict[str, Any]]:
        """Choose a stable hash bucket and retain every outcome token per market."""
        # ``cycle_number`` is shard-local and resets at UTC rotation. Use a
        # wall-clock cadence slot in production so an overfull bucket keeps
        # rotating across daily shards. Tests and offline callers can omit it.
        slot = (
            max(0, int(sampler_slot)) if sampler_slot is not None else cycle_number - 1
        )
        bucket = slot % self.config.bucket_count
        candidates: list[tuple[str, Mapping[str, Any], int]] = []
        for market in markets:
            key = _market_key(market)
            market_bucket = (
                int(hashlib.sha256(key.encode()).hexdigest(), 16)
                % self.config.bucket_count
            )
            if market_bucket == bucket:
                rank = int(hashlib.sha256((key + ":rank").encode()).hexdigest(), 16)
                candidates.append((key, market, rank))
        candidates.sort(key=lambda item: (item[2], item[0]))
        bucket_visit_index = slot // self.config.bucket_count
        sample_count = min(len(candidates), self.config.max_markets_per_cycle)
        rotation_offset = (
            (bucket_visit_index * self.config.max_markets_per_cycle) % len(candidates)
            if candidates
            else 0
        )
        selected = (
            [
                candidates[(rotation_offset + index) % len(candidates)]
                for index in range(sample_count)
            ]
            if candidates
            else []
        )
        wrap_around = int(
            bool(candidates) and rotation_offset + sample_count > len(candidates)
        )
        truncated_count = max(0, len(candidates) - len(selected))
        sampler_metadata = {
            "sampler_version": "sha256-market-bucket-v1",
            "frame_market_count": len(markets),
            "bucket_number": bucket,
            "bucket_count": self.config.bucket_count,
            "bucket_candidate_count": len(candidates),
            "bucket_visit_index": bucket_visit_index,
            "sampler_slot": slot,
            "rotation_offset": rotation_offset,
            "wrap_around": wrap_around,
            "sample_max": self.config.max_markets_per_cycle,
            "sampled_market_count": len(selected),
            "truncated_count": truncated_count,
            "truncation_applied": int(truncated_count > 0),
            "inclusion_probability_basis": (
                "condition/market key SHA-256 modulo bucket_count; one rotating "
                "bucket per cycle; deterministic SHA-256 rank with a cyclic sample_max "
                "window advanced on each bucket visit"
            ),
            "long_run_coverage_basis": (
                "cyclic window guarantees every bucket candidate inclusion within "
                "ceil(bucket_candidate_count/sample_max) visits when frame is stable"
            ),
        }
        self._selection_metadata = sampler_metadata
        rows: list[dict[str, Any]] = []
        for key, market, rank in selected:
            raw_tokens = _list_field(
                market.get("clobTokenIds") or market.get("clob_token_ids")
            )
            labels = _list_field(market.get("outcomes"))
            token_ids: list[str] = []
            token_outcomes: list[dict[str, Any]] = []
            for outcome_index, raw_token in enumerate(raw_tokens):
                if raw_token is None or not str(raw_token).strip():
                    continue
                token = str(raw_token).strip()
                if token in token_ids:
                    continue
                token_ids.append(token)
                token_outcomes.append(
                    {
                        "token_id": token,
                        "outcome_index": outcome_index,
                        "outcome_label": (
                            str(labels[outcome_index])
                            if outcome_index < len(labels)
                            and labels[outcome_index] is not None
                            else None
                        ),
                    }
                )
            rows.append(
                {
                    "selection_id": str(uuid4()),
                    "source_market_key": key,
                    "condition_id": market.get("conditionId")
                    or market.get("condition_id"),
                    "market_id": market.get("id") or market.get("marketId"),
                    "selection_reason": "deterministic_rotating_bucket",
                    **sampler_metadata,
                    "bucket_number": bucket,
                    "bucket_count": self.config.bucket_count,
                    "selection_rank": str(rank),
                    "token_ids": token_ids,
                    "token_outcomes": token_outcomes,
                    "outcome_labels": labels,
                    "expected_token_count": len(token_ids),
                }
            )
        return rows

    def fetch_books(
        self,
        selections: Sequence[Mapping[str, Any]],
        cycle_number: int,
        run_id: str | None = None,
    ) -> BookCollection:
        """Fetch exact full books in public batches and make every gap explicit."""
        del cycle_number  # books are preserved on every sampled cycle
        collection_id = str(uuid4())
        started_at = utc_now()
        mutable = [dict(selection) for selection in selections]
        token_to_selection: dict[str, list[int]] = {}
        all_tokens: list[str] = []
        for index, selection in enumerate(mutable):
            for token_id in selection.get("token_ids", []):
                token = str(token_id)
                token_to_selection.setdefault(token, []).append(index)
                if token not in all_tokens:
                    all_tokens.append(token)

        observed: dict[str, dict[str, Any]] = {}
        token_errors: dict[str, str] = {}
        token_error_types: dict[str, str] = {}
        token_receipts: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(all_tokens), self.config.batch_token_limit):
            chunk = all_tokens[offset : offset + self.config.batch_token_limit]
            body = [{"token_id": token_id} for token_id in chunk]
            try:
                result = post_json_once(
                    self.session,
                    f"{self.config.base_url}/books",
                    body=body,
                    timeout=self.timeout,
                    evidence_sink=self.evidence_sink,
                    run_id=run_id,
                    sweep_attempt_id=collection_id,
                    request_kind="clob_books_batch",
                    page_number=offset // self.config.batch_token_limit + 1,
                )
                payload = result.payload
                if isinstance(payload, Mapping):
                    raw_books = payload.get("books", [])
                else:
                    raw_books = payload
                if not isinstance(raw_books, list) or any(
                    not isinstance(book, Mapping) for book in raw_books
                ):
                    raise ValueError("CLOB /books payload must be a list")
                raw_payload_id = None
                if self.raw_payload_sink is not None:
                    raw_payload_id = self.raw_payload_sink(
                        request_id=result.request_id,
                        kind="clob_books_exact_batch",
                        content=result.content,
                        store_blob=True,
                    )
                for raw_book in raw_books:
                    token_id = raw_book.get("asset_id") or raw_book.get("assetId")
                    if token_id is None:
                        continue
                    token = str(token_id)
                    validation_error = _book_validation_error(raw_book)
                    if validation_error is not None:
                        observed.pop(token, None)
                        token_error_types[token] = "MalformedBook"
                        token_errors[token] = validation_error
                        continue
                    observed[token] = {
                        "token_id": token,
                        "received_at": result.received_at,
                        "request_id": result.request_id,
                        "raw_payload_id": raw_payload_id,
                        "raw_book": dict(raw_book),
                    }
                    token_error_types.pop(token, None)
                    token_errors.pop(token, None)
                for token in chunk:
                    token_receipts[token] = {
                        "received_at": result.received_at,
                        "request_id": result.request_id,
                        "raw_payload_id": raw_payload_id,
                    }
                    if token not in observed and token not in token_errors:
                        token_error_types[token] = "MissingBook"
                        token_errors[token] = "book_missing_from_batch_response"
            except (RequestException, ValueError) as error:
                message = (
                    f"{type(error).__name__}: {' '.join(str(error).splitlines())[:400]}"
                )
                for token in chunk:
                    token_error_types[token] = type(error).__name__
                    token_errors[token] = message

        books: list[Mapping[str, Any]] = []
        token_attempts: list[Mapping[str, Any]] = []
        error_count = 0
        for selection in mutable:
            expected = [str(token) for token in selection.get("token_ids", [])]
            found = [token for token in expected if token in observed]
            errors = [
                token_errors[token] for token in expected if token in token_errors
            ]
            if not expected:
                status = "ERROR"
                error = "market_has_no_token_ids"
            elif len(found) == len(expected):
                status = "COMPLETE"
                error = None
            elif found:
                status = "PARTIAL"
                error = "; ".join(sorted(set(errors))) or "partial_book_response"
            else:
                status = "ERROR"
                error = "; ".join(sorted(set(errors))) or "no_books_observed"
            if status != "COMPLETE":
                error_count += 1
            selection.update(
                {
                    "observed_token_count": len(found),
                    "coverage_ratio": len(found) / len(expected) if expected else 0.0,
                    "status": status,
                    "error_message": error,
                }
            )
            for token in found:
                books.append(
                    {**observed[token], "selection_id": selection["selection_id"]}
                )
            outcome_by_token = {
                str(item.get("token_id")): item
                for item in selection.get("token_outcomes", [])
                if isinstance(item, Mapping) and item.get("token_id") is not None
            }
            for token in expected:
                raw_book = observed.get(token, {}).get("raw_book")
                receipt = token_receipts.get(token, {})
                if isinstance(raw_book, Mapping):
                    bid_count = len(_list_field(raw_book.get("bids")))
                    ask_count = len(_list_field(raw_book.get("asks")))
                    token_status = (
                        "EMPTY_BOOK"
                        if bid_count == 0 and ask_count == 0
                        else "OBSERVED"
                    )
                    error_type = None
                    error_message = None
                elif token_error_types.get(token) == "MalformedBook":
                    bid_count = 0
                    ask_count = 0
                    token_status = "ERROR"
                    error_type = "MalformedBook"
                    error_message = token_errors.get(token)
                elif token in token_receipts:
                    bid_count = 0
                    ask_count = 0
                    token_status = "MISSING"
                    error_type = "MissingBook"
                    error_message = token_errors.get(token)
                else:
                    bid_count = 0
                    ask_count = 0
                    token_status = "ERROR"
                    message = token_errors.get(token) or "book_request_failed"
                    error_type = (
                        token_error_types.get(token) or message.split(":", 1)[0]
                    )
                    error_message = message
                outcome = outcome_by_token.get(token, {})
                token_attempts.append(
                    {
                        "token_attempt_id": str(uuid4()),
                        "selection_id": selection["selection_id"],
                        "collection_id": collection_id,
                        "token_id": token,
                        "outcome_index": outcome.get("outcome_index"),
                        "outcome_label": outcome.get("outcome_label"),
                        "status": token_status,
                        "request_id": receipt.get("request_id"),
                        "raw_payload_id": receipt.get("raw_payload_id"),
                        "received_at": receipt.get("received_at") or utc_now(),
                        "bid_level_count": bid_count,
                        "ask_level_count": ask_count,
                        "error_type": error_type,
                        "error_message": error_message,
                    }
                )

        if not mutable:
            component_status = "EMPTY"
        elif error_count == 0:
            component_status = "SUCCESS"
        elif error_count == len(mutable):
            component_status = "ERROR"
        else:
            component_status = "PARTIAL"
        return BookCollection(
            collection_id=collection_id,
            started_at=started_at,
            completed_at=utc_now(),
            selections=tuple(mutable),
            books=tuple(books),
            token_attempts=tuple(token_attempts),
            status=component_status,
            error_count=error_count,
            sampler_metadata=dict(self._selection_metadata),
        )


__all__ = ["BookCollection", "ClobPublicClient"]
