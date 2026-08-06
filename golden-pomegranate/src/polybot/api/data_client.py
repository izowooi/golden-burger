"""Incremental, window-complete public Data API trade tape collector."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import math
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

import requests
from requests.exceptions import RequestException

from ..config import DataApiConfig, GammaConfig
from ..utils.retry import canonical_json, get_json_with_retry, utc_now


_ECONOMIC_FIELDS = (
    "side",
    "asset",
    "conditionId",
    "size",
    "price",
    "timestamp",
    "transactionHash",
    "proxyWallet",
    "outcome",
    "outcomeIndex",
)


def sanitize_trade(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Keep research economics while dropping display profile/name/bio/image."""
    return {field: raw.get(field) for field in _ECONOMIC_FIELDS}


def canonical_trade_hash(trade: Mapping[str, Any]) -> str:
    typed = dict(trade)
    for field in ("size", "price"):
        if typed.get(field) is not None:
            try:
                numeric = Decimal(str(typed[field]))
            except (InvalidOperation, ValueError) as error:
                raise ValueError(f"trade {field} is not a canonical decimal") from error
            if not numeric.is_finite():
                raise ValueError(f"trade {field} must be finite")
            typed[field] = (
                "0" if numeric.is_zero() else format(numeric.normalize(), "f")
            )
    for field in ("timestamp", "outcomeIndex"):
        if typed.get(field) is None:
            continue
        try:
            numeric = Decimal(str(typed[field]))
        except (InvalidOperation, ValueError) as error:
            raise ValueError(f"trade {field} is not a canonical integer") from error
        if not numeric.is_finite() or numeric != numeric.to_integral_value():
            raise ValueError(f"trade {field} must be an integer")
        typed[field] = str(int(numeric))
    for field in (
        "side",
        "asset",
        "conditionId",
        "transactionHash",
        "proxyWallet",
        "outcome",
    ):
        if typed.get(field) is not None:
            typed[field] = str(typed[field])
    return hashlib.sha256(canonical_json(typed).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DataTradeCollection:
    collection_id: str
    started_at: str
    completed_at: str
    target_start_epoch: int
    source_target_end_epoch: int
    bounded_target_end_epoch: int
    watermark_before_epoch: int | None
    watermark_advance_to_epoch: int | None
    status: str
    possible_gap: bool
    windows: tuple[Mapping[str, Any], ...]
    memberships: tuple[Mapping[str, Any], ...]
    trades: tuple[Mapping[str, Any], ...]
    error_message: str | None

    @property
    def target_end_epoch(self) -> int:
        """Backward-compatible alias for the explicitly bounded worker end."""
        return self.bounded_target_end_epoch


class _DataTradeBudgetExceeded(RuntimeError):
    """Internal signal that preserves an incomplete source window as evidence."""

    def __init__(
        self,
        *,
        kind: str,
        used: float,
        limit: float,
        request_attempts: int,
        windows_started: int,
        elapsed_seconds: float,
        start_epoch: int,
        end_epoch: int,
        split_depth: int,
        parent_window_id: str | None,
        source_target_end_epoch: int,
        bounded_target_end_epoch: int,
        window_id: str | None = None,
        request_id: str | None = None,
        raw_payload_id: str | None = None,
        received_at: str | None = None,
    ) -> None:
        self.kind = kind
        self.start_epoch = start_epoch
        self.end_epoch = end_epoch
        self.split_depth = split_depth
        self.parent_window_id = parent_window_id
        self.window_id = window_id
        self.request_id = request_id
        self.raw_payload_id = raw_payload_id
        self.received_at = received_at
        message = (
            "data trade cycle budget exhausted: "
            f"kind={kind} used={used:.3f} limit={limit:.3f} "
            f"request_attempts={request_attempts} windows_started={windows_started} "
            f"elapsed_seconds={elapsed_seconds:.3f} "
            f"remaining_window=[{start_epoch},{end_epoch}] "
            f"source_target_end_epoch={source_target_end_epoch} "
            f"bounded_target_end_epoch={bounded_target_end_epoch}"
        )
        super().__init__(message)


class _DataTradeContractError(ValueError):
    """Schema/economic failure retaining successful HTTP request lineage."""

    def __init__(
        self,
        message: str,
        *,
        request_id: str,
        received_at: str,
        raw_payload_id: str | None,
    ) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.received_at = received_at
        self.raw_payload_id = raw_payload_id


class DataApiClient:
    """Fetch a complete stabilized trade interval via recursive time splits."""

    def __init__(
        self,
        config: DataApiConfig,
        retry_config: GammaConfig,
        *,
        evidence_sink: Callable[[Mapping[str, Any]], None] | None = None,
        raw_payload_sink: Callable[..., str] | None = None,
        session: requests.Session | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.retry_config = retry_config
        self.evidence_sink = evidence_sink
        self.raw_payload_sink = raw_payload_sink
        self.session = session or requests.Session()
        # Accountless collection must not inherit .netrc credentials or
        # credential-bearing proxy settings from the Jenkins environment.
        self.session.trust_env = False
        self._monotonic = monotonic
        if hasattr(self.session, "headers"):
            self.session.headers.update(
                {
                    "Accept": "application/json",
                    "User-Agent": "GoldenPomegranate-Research/1.0",
                }
            )

    def _get_window(
        self,
        *,
        collection_id: str,
        window_id: str,
        start_epoch: int,
        end_epoch: int,
        page_number: int,
        run_id: str | None,
        attempt_evidence_sink: Callable[[Mapping[str, Any]], None],
    ) -> tuple[Any, str, str, str | None, int, int | None, int | None]:
        result = get_json_with_retry(
            self.session,
            f"{self.config.base_url}/trades",
            params={
                "start": start_epoch,
                "end": end_epoch,
                "limit": self.config.trade_limit,
                "offset": 0,
                "takerOnly": "true",
            },
            timeout=(
                self.retry_config.connect_timeout_seconds,
                self.retry_config.read_timeout_seconds,
            ),
            max_attempts=self.retry_config.max_retries,
            base_delay_seconds=self.retry_config.retry_base_seconds,
            max_delay_seconds=self.retry_config.retry_max_seconds,
            evidence_sink=attempt_evidence_sink,
            run_id=run_id,
            sweep_attempt_id=collection_id,
            request_kind="data_trades_window",
            page_number=page_number,
        )
        raw_payload_id = None
        try:
            payload = result.payload
            if isinstance(payload, Mapping):
                if "data" in payload:
                    rows = payload["data"]
                elif "trades" in payload:
                    rows = payload["trades"]
                else:
                    raise ValueError(
                        "Data API /trades mapping must contain an explicit data or trades array"
                    )
            else:
                rows = payload
            if not isinstance(rows, list) or any(
                not isinstance(row, Mapping) for row in rows
            ):
                raise ValueError("Data API /trades payload must be a list of mappings")
            sanitized = [sanitize_trade(row) for row in rows]
            # Preserve the safe economic projection before validating it.  If
            # schema drift is detected, the logical ERROR window can still join
            # to both the successful HTTP request and the exact sanitized body.
            if self.raw_payload_sink is not None:
                sanitized_bytes = canonical_json(sanitized).encode("utf-8")
                raw_payload_id = self.raw_payload_sink(
                    request_id=result.request_id,
                    kind="data_trades_sanitized_window",
                    content=sanitized_bytes,
                    store_blob=True,
                )
            timestamps: list[int] = []
            out_of_bounds_count = 0
            for index, trade in enumerate(sanitized):
                missing = [
                    field
                    for field in (
                        "side",
                        "asset",
                        "conditionId",
                        "size",
                        "price",
                        "timestamp",
                    )
                    if trade.get(field) is None or str(trade.get(field)).strip() == ""
                ]
                if missing:
                    raise ValueError(
                        f"Data API trade row {index} is missing required economic fields: {missing}"
                    )
                try:
                    price = float(trade["price"])
                    size = float(trade["size"])
                    timestamp = float(trade["timestamp"])
                except (TypeError, ValueError, OverflowError) as error:
                    raise ValueError(
                        f"Data API trade row {index} has invalid numeric economics"
                    ) from error
                if not all(math.isfinite(value) for value in (price, size, timestamp)):
                    raise ValueError(
                        f"Data API trade row {index} has non-finite economics"
                    )
                if not 0 <= price <= 1 or size <= 0:
                    raise ValueError(
                        f"Data API trade row {index} has out-of-range price/size"
                    )
                timestamp_decimal = Decimal(str(trade["timestamp"]))
                if timestamp_decimal != timestamp_decimal.to_integral_value():
                    raise ValueError(
                        f"Data API trade row {index} timestamp must be an integer"
                    )
                outcome_index = trade.get("outcomeIndex")
                if outcome_index is not None:
                    outcome_index_decimal = Decimal(str(outcome_index))
                    if (
                        not outcome_index_decimal.is_finite()
                        or outcome_index_decimal
                        != outcome_index_decimal.to_integral_value()
                    ):
                        raise ValueError(
                            f"Data API trade row {index} outcomeIndex must be an integer"
                        )
                timestamp_epoch = int(timestamp_decimal)
                timestamps.append(timestamp_epoch)
                if not start_epoch <= timestamp_epoch <= end_epoch:
                    out_of_bounds_count += 1
        except ValueError as error:
            raise _DataTradeContractError(
                str(error),
                request_id=result.request_id,
                received_at=result.received_at,
                raw_payload_id=raw_payload_id,
            ) from error
        return (
            sanitized,
            result.request_id,
            result.received_at,
            raw_payload_id,
            out_of_bounds_count,
            min(timestamps) if timestamps else None,
            max(timestamps) if timestamps else None,
        )

    def fetch_incremental(
        self,
        *,
        watermark_epoch: int | None,
        bootstrap_start_epoch: int | None = None,
        now_epoch: int,
        cycle_number: int,
        run_id: str | None,
    ) -> DataTradeCollection:
        del cycle_number
        collection_id = str(uuid4())
        started_at = utc_now()
        fetch_started_clock = self._monotonic()
        source_target_end = max(0, int(now_epoch) - self.config.safety_lag_seconds)
        if watermark_epoch is not None and source_target_end < int(watermark_epoch):
            message = (
                "clock regression: stabilized target end precedes persisted watermark"
            )
            return DataTradeCollection(
                collection_id=collection_id,
                started_at=started_at,
                completed_at=utc_now(),
                target_start_epoch=source_target_end,
                source_target_end_epoch=source_target_end,
                bounded_target_end_epoch=source_target_end,
                watermark_before_epoch=watermark_epoch,
                watermark_advance_to_epoch=None,
                status="ERROR",
                possible_gap=True,
                windows=(
                    {
                        "window_id": str(uuid4()),
                        "parent_window_id": None,
                        "start_epoch": source_target_end,
                        "end_epoch": source_target_end,
                        "split_depth": 0,
                        "offset": 0,
                        "request_id": None,
                        "raw_payload_id": None,
                        "received_at": utc_now(),
                        "row_count": 0,
                        "membership_count": 0,
                        "economic_unique_count": 0,
                        "duplicate_economic_row_count": 0,
                        "membership_digest_sha256": hashlib.sha256(b"[]").hexdigest(),
                        "hit_cap": 0,
                        "status": "ERROR",
                        "possible_gap": 1,
                        "error_message": message,
                    },
                ),
                memberships=(),
                trades=(),
                error_message=message,
            )
        if watermark_epoch is None:
            target_start = (
                max(0, int(bootstrap_start_epoch))
                if bootstrap_start_epoch is not None
                else max(
                    0,
                    source_target_end - self.config.initial_lookback_hours * 3600,
                )
            )
            target_end = min(
                source_target_end,
                target_start + self.config.catchup_chunk_seconds,
            )
        else:
            progress_epoch = max(0, int(watermark_epoch))
            target_start = max(0, int(watermark_epoch) - self.config.overlap_seconds)
            target_end = min(
                source_target_end,
                progress_epoch + self.config.catchup_chunk_seconds,
            )
        if target_end < target_start:
            target_start = target_end

        windows: list[dict[str, Any]] = []
        memberships: list[dict[str, Any]] = []
        unique_trades: dict[str, dict[str, Any]] = {}
        possible_gap = False
        page_counter = 0
        request_attempts = 0
        windows_started = 0

        def budget_error(
            *,
            kind: str,
            used: float,
            limit: float,
            start_epoch: int,
            end_epoch: int,
            depth: int,
            parent_window_id: str | None,
            window_id: str | None = None,
            request_id: str | None = None,
            raw_payload_id: str | None = None,
            received_at: str | None = None,
        ) -> _DataTradeBudgetExceeded:
            return _DataTradeBudgetExceeded(
                kind=kind,
                used=used,
                limit=limit,
                request_attempts=request_attempts,
                windows_started=windows_started,
                elapsed_seconds=max(0.0, self._monotonic() - fetch_started_clock),
                start_epoch=start_epoch,
                end_epoch=end_epoch,
                split_depth=depth,
                parent_window_id=parent_window_id,
                source_target_end_epoch=source_target_end,
                bounded_target_end_epoch=target_end,
                window_id=window_id,
                request_id=request_id,
                raw_payload_id=raw_payload_id,
                received_at=received_at,
            )

        def assert_budget(
            start_epoch: int,
            end_epoch: int,
            depth: int,
            parent_window_id: str | None,
        ) -> None:
            elapsed = max(0.0, self._monotonic() - fetch_started_clock)
            if elapsed >= self.config.runtime_budget_seconds:
                raise budget_error(
                    kind="runtime_seconds",
                    used=elapsed,
                    limit=self.config.runtime_budget_seconds,
                    start_epoch=start_epoch,
                    end_epoch=end_epoch,
                    depth=depth,
                    parent_window_id=parent_window_id,
                )
            if request_attempts >= self.config.max_request_attempts_per_cycle:
                raise budget_error(
                    kind="request_attempts",
                    used=float(request_attempts),
                    limit=float(self.config.max_request_attempts_per_cycle),
                    start_epoch=start_epoch,
                    end_epoch=end_epoch,
                    depth=depth,
                    parent_window_id=parent_window_id,
                )
            if windows_started >= self.config.max_windows_per_cycle:
                raise budget_error(
                    kind="windows",
                    used=float(windows_started),
                    limit=float(self.config.max_windows_per_cycle),
                    start_epoch=start_epoch,
                    end_epoch=end_epoch,
                    depth=depth,
                    parent_window_id=parent_window_id,
                )

        def record_request_attempt(
            record: Mapping[str, Any],
            *,
            start_epoch: int,
            end_epoch: int,
            depth: int,
            parent_window_id: str | None,
        ) -> None:
            nonlocal request_attempts
            request_attempts += 1
            if self.evidence_sink is not None:
                self.evidence_sink(record)
            # A successful response must first return through ``_get_window``
            # so its sanitized raw payload and request lineage can be linked to
            # the BUDGET_EXHAUSTED logical window. The post-response check in
            # ``collect_window`` still freezes the watermark.
            if record.get("status") == "SUCCESS":
                return
            elapsed = max(0.0, self._monotonic() - fetch_started_clock)
            if elapsed >= self.config.runtime_budget_seconds:
                raise budget_error(
                    kind="runtime_seconds",
                    used=elapsed,
                    limit=self.config.runtime_budget_seconds,
                    start_epoch=start_epoch,
                    end_epoch=end_epoch,
                    depth=depth,
                    parent_window_id=parent_window_id,
                    request_id=(
                        str(record["request_id"])
                        if record.get("request_id") is not None
                        else None
                    ),
                    received_at=(
                        str(record["completed_at"])
                        if record.get("completed_at") is not None
                        else None
                    ),
                )
            if request_attempts >= self.config.max_request_attempts_per_cycle:
                raise budget_error(
                    kind="request_attempts",
                    used=float(request_attempts),
                    limit=float(self.config.max_request_attempts_per_cycle),
                    start_epoch=start_epoch,
                    end_epoch=end_epoch,
                    depth=depth,
                    parent_window_id=parent_window_id,
                )

        error_message: str | None = None

        def collect_window(
            start_epoch: int,
            end_epoch: int,
            depth: int,
            parent_window_id: str | None,
        ) -> None:
            nonlocal error_message, possible_gap, page_counter, windows_started
            if depth > 40:
                raise RuntimeError("Data trade window split depth exceeded")
            assert_budget(start_epoch, end_epoch, depth, parent_window_id)
            windows_started += 1
            window_id = str(uuid4())
            page_counter += 1
            try:
                (
                    rows,
                    request_id,
                    received_at,
                    raw_payload_id,
                    out_of_bounds_count,
                    observed_min_epoch,
                    observed_max_epoch,
                ) = self._get_window(
                    collection_id=collection_id,
                    window_id=window_id,
                    start_epoch=start_epoch,
                    end_epoch=end_epoch,
                    page_number=page_counter,
                    run_id=run_id,
                    attempt_evidence_sink=lambda record: record_request_attempt(
                        record,
                        start_epoch=start_epoch,
                        end_epoch=end_epoch,
                        depth=depth,
                        parent_window_id=parent_window_id,
                    ),
                )
                elapsed_after_response = max(
                    0.0, self._monotonic() - fetch_started_clock
                )
                if elapsed_after_response >= self.config.runtime_budget_seconds:
                    raise budget_error(
                        kind="runtime_seconds",
                        used=elapsed_after_response,
                        limit=self.config.runtime_budget_seconds,
                        start_epoch=start_epoch,
                        end_epoch=end_epoch,
                        depth=depth,
                        parent_window_id=parent_window_id,
                        window_id=window_id,
                        request_id=request_id,
                        raw_payload_id=raw_payload_id,
                        received_at=received_at,
                    )
            except _DataTradeBudgetExceeded:
                raise
            except _DataTradeContractError as error:
                possible_gap = True
                message = (
                    f"{type(error).__name__}: {' '.join(str(error).splitlines())[:500]}"
                )
                windows.append(
                    {
                        "window_id": window_id,
                        "parent_window_id": parent_window_id,
                        "start_epoch": start_epoch,
                        "end_epoch": end_epoch,
                        "split_depth": depth,
                        "offset": 0,
                        "request_id": error.request_id,
                        "raw_payload_id": error.raw_payload_id,
                        "received_at": error.received_at,
                        "row_count": 0,
                        "membership_count": 0,
                        "economic_unique_count": 0,
                        "duplicate_economic_row_count": 0,
                        "membership_digest_sha256": hashlib.sha256(b"[]").hexdigest(),
                        "hit_cap": 0,
                        "status": "ERROR",
                        "possible_gap": 1,
                        "error_message": message,
                    }
                )
                raise
            except (RequestException, ValueError, RuntimeError) as error:
                possible_gap = True
                message = (
                    f"{type(error).__name__}: {' '.join(str(error).splitlines())[:500]}"
                )
                windows.append(
                    {
                        "window_id": window_id,
                        "parent_window_id": parent_window_id,
                        "start_epoch": start_epoch,
                        "end_epoch": end_epoch,
                        "split_depth": depth,
                        "offset": 0,
                        "request_id": getattr(error, "request_id", None),
                        "raw_payload_id": getattr(error, "raw_payload_id", None),
                        "received_at": getattr(error, "received_at", utc_now()),
                        "row_count": 0,
                        "membership_count": 0,
                        "economic_unique_count": 0,
                        "duplicate_economic_row_count": 0,
                        "membership_digest_sha256": hashlib.sha256(b"[]").hexdigest(),
                        "hit_cap": 0,
                        "status": "ERROR",
                        "possible_gap": 1,
                        "error_message": message,
                    }
                )
                raise
            hit_cap = len(rows) >= self.config.trade_limit
            duration = end_epoch - start_epoch
            window = {
                "window_id": window_id,
                "parent_window_id": parent_window_id,
                "start_epoch": start_epoch,
                "end_epoch": end_epoch,
                "split_depth": depth,
                "offset": 0,
                "request_id": request_id,
                "raw_payload_id": raw_payload_id,
                "received_at": received_at,
                "row_count": len(rows),
                "membership_count": 0,
                "economic_unique_count": 0,
                "duplicate_economic_row_count": 0,
                "membership_digest_sha256": hashlib.sha256(b"[]").hexdigest(),
                "hit_cap": int(hit_cap),
                "status": "EMPTY" if not rows else "COMPLETE",
                "possible_gap": 0,
                "error_message": None,
            }
            if out_of_bounds_count:
                # The public unscoped endpoint has been observed returning its
                # current global head while ignoring documented start/end
                # bounds.  The exact sanitized response is already retained as
                # one compressed raw payload.  Do not also expand thousands of
                # rows into normalized observations and per-cycle memberships:
                # they are outside the requested evidence interval and the API
                # has repeatedly returned the same moving global head.  Keep a
                # compact economic digest/count plus request/raw lineage, never
                # claim interval completeness, and never advance the watermark.
                # Splitting a response that ignored the parent bounds would only
                # repeat the same head payload and consume the cycle budget.
                possible_gap = True
                window["status"] = "SOURCE_BOUNDS_VIOLATION"
                window["possible_gap"] = 1
                window["error_message"] = (
                    "Data API returned rows outside requested bounds: "
                    f"count={out_of_bounds_count} "
                    f"requested=[{start_epoch},{end_epoch}] "
                    f"observed=[{observed_min_epoch},{observed_max_epoch}]"
                )
                error_message = window["error_message"]
                economic_hashes = [canonical_trade_hash(row) for row in rows]
                unique_economic_hashes = set(economic_hashes)
                window["economic_unique_count"] = len(unique_economic_hashes)
                window["duplicate_economic_row_count"] = max(
                    0, len(economic_hashes) - len(unique_economic_hashes)
                )
                # No normalized memberships are published for this window, so
                # its membership digest must attest the empty membership set.
                # The compact source-response digest lives on raw_payloads.
                window["membership_digest_sha256"] = hashlib.sha256(b"[]").hexdigest()
                windows.append(window)
                return
            elif hit_cap and duration >= 1:
                midpoint = start_epoch + duration // 2
                window["status"] = "SPLIT"
                windows.append(window)
                collect_window(start_epoch, midpoint, depth + 1, window_id)
                collect_window(midpoint + 1, end_epoch, depth + 1, window_id)
                return
            if hit_cap and not out_of_bounds_count:
                possible_gap = True
                window["status"] = "POSSIBLE_GAP"
                window["possible_gap"] = 1
                window["error_message"] = (
                    "10000-row cap reached in an indivisible single-timestamp window"
                )
            windows.append(window)
            grouped: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
            for original_index, trade in enumerate(rows):
                economic_hash = canonical_trade_hash(trade)
                grouped.setdefault(economic_hash, []).append((original_index, trade))
            ordered: list[tuple[int, str, int, Mapping[str, Any], str]] = []
            for economic_hash, group in sorted(grouped.items()):
                # Values within one economic hash are byte-identical after
                # sanitization. Original index is only a deterministic tie-break
                # for assigning the stable multiplicity index 0..n-1.
                for occurrence_index, (original_index, trade) in enumerate(
                    sorted(group, key=lambda item: (canonical_json(item[1]), item[0]))
                ):
                    trade_id = hashlib.sha256(
                        f"{economic_hash}|{occurrence_index}".encode()
                    ).hexdigest()
                    ordered.append(
                        (
                            original_index,
                            economic_hash,
                            occurrence_index,
                            trade,
                            trade_id,
                        )
                    )
            window_memberships: list[dict[str, Any]] = []
            for (
                original_index,
                economic_hash,
                occurrence_index,
                trade,
                trade_id,
            ) in sorted(ordered):
                normalized = {
                    **trade,
                    "trade_id": trade_id,
                    "economic_row_hash": economic_hash,
                    "occurrence_index": occurrence_index,
                    "first_received_at": received_at,
                }
                unique_trades.setdefault(trade_id, normalized)
                membership = {
                    "membership_id": str(uuid4()),
                    "window_id": window_id,
                    "trade_id": trade_id,
                    "economic_row_hash": economic_hash,
                    "occurrence_index": occurrence_index,
                    "item_number": original_index,
                    "received_at": received_at,
                }
                memberships.append(membership)
                window_memberships.append(membership)
            digest_scope = [
                {
                    "item": row["item_number"],
                    "economic_hash": row["economic_row_hash"],
                    "occurrence": row["occurrence_index"],
                    "trade_id": row["trade_id"],
                }
                for row in sorted(
                    window_memberships, key=lambda item: int(item["item_number"])
                )
            ]
            window["membership_count"] = len(window_memberships)
            window["economic_unique_count"] = len(grouped)
            window["duplicate_economic_row_count"] = max(0, len(rows) - len(grouped))
            window["membership_digest_sha256"] = hashlib.sha256(
                canonical_json(digest_scope).encode()
            ).hexdigest()

        try:
            if target_start < target_end:
                collect_window(target_start, target_end, 0, None)
                status = (
                    "POSSIBLE_GAP"
                    if possible_gap
                    else ("EMPTY" if not unique_trades else "SUCCESS")
                )
            else:
                windows.append(
                    {
                        "window_id": str(uuid4()),
                        "parent_window_id": None,
                        "start_epoch": target_start,
                        "end_epoch": target_end,
                        "split_depth": 0,
                        "offset": 0,
                        "request_id": None,
                        "raw_payload_id": None,
                        "received_at": utc_now(),
                        "row_count": 0,
                        "membership_count": 0,
                        "economic_unique_count": 0,
                        "duplicate_economic_row_count": 0,
                        "membership_digest_sha256": hashlib.sha256(b"[]").hexdigest(),
                        "hit_cap": 0,
                        "status": "NO_NEW_INTERVAL",
                        "possible_gap": 0,
                        "error_message": None,
                    }
                )
                status = "EMPTY"
        except _DataTradeBudgetExceeded as error:
            status = "ERROR"
            possible_gap = True
            error_message = f"{type(error).__name__}: {error}"
            windows.append(
                {
                    "window_id": error.window_id or str(uuid4()),
                    "parent_window_id": error.parent_window_id,
                    "start_epoch": error.start_epoch,
                    "end_epoch": error.end_epoch,
                    "split_depth": error.split_depth,
                    "offset": 0,
                    "request_id": error.request_id,
                    "raw_payload_id": error.raw_payload_id,
                    "received_at": error.received_at or utc_now(),
                    "row_count": 0,
                    "membership_count": 0,
                    "economic_unique_count": 0,
                    "duplicate_economic_row_count": 0,
                    "membership_digest_sha256": hashlib.sha256(b"[]").hexdigest(),
                    "hit_cap": 0,
                    "status": "BUDGET_EXHAUSTED",
                    "possible_gap": 1,
                    "error_message": error_message,
                }
            )
        except (RequestException, ValueError, RuntimeError) as error:
            status = "ERROR"
            error_message = (
                f"{type(error).__name__}: {' '.join(str(error).splitlines())[:500]}"
            )
            if not any(window.get("status") == "ERROR" for window in windows):
                windows.append(
                    {
                        "window_id": str(uuid4()),
                        "parent_window_id": None,
                        "start_epoch": target_start,
                        "end_epoch": target_end,
                        "split_depth": 0,
                        "offset": 0,
                        "request_id": None,
                        "raw_payload_id": None,
                        "received_at": utc_now(),
                        "row_count": 0,
                        "membership_count": 0,
                        "economic_unique_count": 0,
                        "duplicate_economic_row_count": 0,
                        "membership_digest_sha256": hashlib.sha256(b"[]").hexdigest(),
                        "hit_cap": 0,
                        "status": "ERROR",
                        "possible_gap": 1,
                        "error_message": error_message,
                    }
                )
            possible_gap = True

        watermark_advance = target_end if status in {"SUCCESS", "EMPTY"} else None
        return DataTradeCollection(
            collection_id=collection_id,
            started_at=started_at,
            completed_at=utc_now(),
            target_start_epoch=target_start,
            source_target_end_epoch=source_target_end,
            bounded_target_end_epoch=target_end,
            watermark_before_epoch=watermark_epoch,
            watermark_advance_to_epoch=watermark_advance,
            status=status,
            possible_gap=possible_gap,
            windows=tuple(windows),
            memberships=tuple(memberships),
            trades=tuple(unique_trades.values()),
            error_message=error_message,
        )


__all__ = [
    "DataApiClient",
    "DataTradeCollection",
    "canonical_trade_hash",
    "sanitize_trade",
]
