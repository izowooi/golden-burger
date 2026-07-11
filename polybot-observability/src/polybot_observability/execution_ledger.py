"""Append-only order/trade reconciliation ledger for CLOB execution evidence."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .run_audit import _safe_error_message, current_run_id

_TERMINAL_ORDER_STATUSES = {
    "MATCHED",
    "CANCELED",
    "CANCELLED",
    "CANCELED_MARKET_RESOLVED",
    "INVALID",
}
_TERMINAL_TRADE_STATUSES = {"CONFIRMED", "FAILED"}
_FIXED_6_SCALE = 1_000_000
_QUANTITY_TOLERANCE = 0.000001


class SubmissionEvidenceError(RuntimeError):
    """Raised when an external order cannot be kept consistent with its ledger."""


class UnresolvedSubmissionOutcomeError(SubmissionEvidenceError):
    """Raised while a possible submitted order still lacks operator resolution."""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(
            "결과가 불확실한 CLOB 주문 intent가 "
            f"{count}건 남아 있어 새 trading cycle을 중단합니다"
        )


class _OrderResponseContractError(SubmissionEvidenceError):
    """Internal marker: anomalous response was persisted successfully."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _normalize_status(value: Any) -> str:
    status = str(value or "UNKNOWN").strip().upper()
    for prefix in ("ORDER_STATUS_", "TRADE_STATUS_"):
        if status.startswith(prefix):
            status = status[len(prefix) :]
    return status


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fixed_6_number(value: Any) -> float | None:
    """Decode CLOB v2 fixed-math amounts into human token/USDC units."""
    number = _number(value)
    return None if number is None else number / _FIXED_6_SCALE


def _bucket_index(value: Any) -> tuple[int, str | None]:
    if value is None:
        return 0, None
    parsed: int | None = None
    if isinstance(value, int) and not isinstance(value, bool):
        parsed = value
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            parsed = int(stripped)
    if parsed is None or parsed < 0:
        return -1, "bucket_index_invalid"
    return parsed, None


def _finite_positive(value: Any) -> bool:
    number = _number(value)
    return number is not None and math.isfinite(number) and number > 0


def _finite_nonnegative(value: Any) -> bool:
    number = _number(value)
    return number is not None and math.isfinite(number) and number >= 0


def _valid_fill_price(value: Any) -> bool:
    number = _number(value)
    return number is not None and math.isfinite(number) and 0 < number < 1


def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    """Return the first explicitly present value, preserving numeric zero."""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if item]


class ExecutionLedger:
    """Persist selected non-secret execution fields into the bot SQLite DB."""

    def __init__(self, db_path: str | Path, *, strategy_name: str) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.strategy_name = strategy_name
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
            self._bootstrap_legacy_orders(connection)

    def record_submission(
        self,
        *,
        token_id: str,
        side: str,
        requested_price: float,
        requested_size: float,
        result: Mapping[str, Any],
        simulation: bool,
    ) -> str:
        """Convenience wrapper used by simulations, tests, and legacy callers."""
        submission_id = self.record_intent(
            token_id=token_id,
            side=side,
            requested_price=requested_price,
            requested_size=requested_size,
            simulation=simulation,
        )
        self.record_submission_result(
            submission_id,
            result=result,
            simulation=simulation,
        )
        return submission_id

    def submit_and_record(
        self,
        *,
        token_id: str,
        side: str,
        requested_price: float,
        requested_size: float,
        submit: Callable[[], Mapping[str, Any]],
        cancel: Callable[[str], Any] | None = None,
    ) -> Mapping[str, Any]:
        """Persist intent, submit once, then persist the response on the same row.

        The intent write happens before the external side effect. If the venue
        accepts an order but the response cannot be persisted, a best-effort
        cancellation is attempted and a fail-closed exception is raised.
        """
        submission_id = self.record_intent(
            token_id=token_id,
            side=side,
            requested_price=requested_price,
            requested_size=requested_size,
            simulation=False,
        )
        try:
            result = submit()
        except Exception as error:
            try:
                response_status = self.record_submission_error(submission_id, error)
            except Exception as ledger_error:
                raise SubmissionEvidenceError(
                    "주문 실패 상태를 execution ledger에 기록하지 못했습니다"
                ) from ledger_error
            if response_status == "SUBMIT_OUTCOME_UNKNOWN":
                raise SubmissionEvidenceError(
                    "주문 POST 결과가 불확실하여 trading cycle을 중단합니다"
                ) from error
            raise

        if not isinstance(result, Mapping):
            error = TypeError("CLOB order response가 mapping이 아닙니다")
            self.record_submission_error(submission_id, error)
            raise error
        try:
            self.record_submission_result(
                submission_id,
                result=result,
                simulation=False,
            )
        except _OrderResponseContractError as response_error:
            order_id = str(result.get("orderID") or "")
            cancellation_failed = bool(order_id)
            if order_id and cancel is not None:
                try:
                    cancel_result = cancel(order_id)
                    canceled_ids = (
                        cancel_result.get("canceled", [])
                        if isinstance(cancel_result, Mapping)
                        else []
                    )
                    cancellation_failed = order_id not in {
                        str(value) for value in canceled_ids
                    }
                except Exception:
                    cancellation_failed = True
            suffix = " cancellation도 실패했습니다" if cancellation_failed else ""
            raise SubmissionEvidenceError(
                f"CLOB order response contract가 불확실하여 cycle을 중단합니다{suffix}"
            ) from response_error
        except Exception as ledger_error:
            order_id = str(result.get("orderID") or "")
            try:
                self.mark_evidence_write_failure(
                    submission_id, order_id=order_id, error=ledger_error
                )
            except Exception:
                pass
            cancellation_failed = bool(order_id)
            if order_id and cancel is not None:
                try:
                    cancel_result = cancel(order_id)
                    canceled_ids = (
                        cancel_result.get("canceled", [])
                        if isinstance(cancel_result, Mapping)
                        else []
                    )
                    cancellation_failed = order_id not in {
                        str(value) for value in canceled_ids
                    }
                except Exception:
                    cancellation_failed = True
            suffix = " cancellation도 실패했습니다" if cancellation_failed else ""
            raise SubmissionEvidenceError(
                "접수된 주문 응답을 execution ledger에 기록하지 못해 "
                f"best-effort cancel을 수행했습니다{suffix}"
            ) from ledger_error
        return result

    def record_intent(
        self,
        *,
        token_id: str,
        side: str,
        requested_price: float,
        requested_size: float,
        simulation: bool,
    ) -> str:
        """Durably record an order intent before the external POST occurs."""
        if not _valid_fill_price(requested_price):
            raise ValueError("requested_price는 finite 0 < price < 1 이어야 합니다")
        if not _finite_positive(requested_size):
            raise ValueError("requested_size는 finite positive 값이어야 합니다")
        submission_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO order_submissions (
                    submission_id, run_id, strategy_name, order_id, token_id, side,
                    requested_price, requested_size, submitted_at, simulation,
                    success, response_status, associated_trade_ids_json,
                    needs_reconciliation
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 0, 'INTENT', '[]', 0)
                """,
                (
                    submission_id,
                    current_run_id(),
                    self.strategy_name,
                    str(token_id),
                    str(side).upper(),
                    float(requested_price),
                    float(requested_size),
                    _utc_now(),
                    int(simulation),
                ),
            )
        return submission_id

    def record_submission_result(
        self,
        submission_id: str,
        *,
        result: Mapping[str, Any],
        simulation: bool,
    ) -> None:
        """Attach the venue response to a previously persisted intent."""
        order_id = str(result.get("orderID") or "") or None
        explicit_success = result.get("success")
        response_anomaly: str | None = None
        if not simulation:
            if explicit_success is False and order_id:
                response_anomaly = "success=false response에 orderID가 함께 있습니다"
            elif not order_id and explicit_success is not False:
                response_anomaly = "live success/accepted response에 orderID가 없습니다"
            elif "success" in result and not isinstance(explicit_success, bool):
                response_anomaly = "live response success 필드가 boolean이 아닙니다"
        success = bool(explicit_success or order_id) and response_anomaly is None
        response_status = _normalize_status(
            "SUBMIT_OUTCOME_UNKNOWN"
            if response_anomaly
            else result.get("status")
            or ("SIMULATED" if simulation else "ACCEPTED" if success else "FAILED")
        )
        trade_ids = _string_list(result.get("tradeIDs") or result.get("trade_ids"))
        needs_reconciliation = int(bool(success and order_id and not simulation))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE order_submissions
                SET order_id = ?, simulation = ?, success = ?, response_status = ?,
                    making_amount = ?, taking_amount = ?,
                    associated_trade_ids_json = ?, needs_reconciliation = ?,
                    error_type = ?, error_message = ?
                WHERE submission_id = ?
                """,
                (
                    order_id,
                    int(simulation),
                    int(success),
                    response_status,
                    _fixed_6_number(result.get("makingAmount")),
                    _fixed_6_number(result.get("takingAmount")),
                    json.dumps(trade_ids),
                    needs_reconciliation,
                    None
                    if success
                    else "OrderResponseContractError"
                    if response_anomaly
                    else "OrderSubmissionError",
                    None
                    if success
                    else _safe_error_message(
                        ValueError(
                            str(
                                response_anomaly
                                or result.get("error")
                                or result.get("errorMsg")
                                or "order rejected"
                            )
                        )
                    ),
                    submission_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"order intent를 찾을 수 없습니다: {submission_id}")
        if response_anomaly:
            raise _OrderResponseContractError(response_anomaly)

    def record_submission_error(
        self, submission_id: str, error: BaseException
    ) -> str:
        """Mark a pre-submit intent failed without creating another ledger row."""
        status_code = getattr(error, "status_code", "missing")
        ambiguous = type(error).__name__ in {
            "ConnectTimeout",
            "ConnectionError",
            "ReadTimeout",
            "Timeout",
        } or (
            status_code != "missing"
            and (
                status_code is None
                or (isinstance(status_code, int) and status_code >= 500)
            )
        )
        response_status = "SUBMIT_OUTCOME_UNKNOWN" if ambiguous else "FAILED"
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE order_submissions
                SET success = 0, response_status = ?,
                    needs_reconciliation = 0, error_type = ?, error_message = ?
                WHERE submission_id = ?
                """,
                (
                    response_status,
                    type(error).__name__,
                    _safe_error_message(error),
                    submission_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"order intent를 찾을 수 없습니다: {submission_id}")
        return response_status

    def mark_evidence_write_failure(
        self,
        submission_id: str,
        *,
        order_id: str,
        error: BaseException,
    ) -> None:
        """Best-effort emergency link for an accepted order after a write failure."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE order_submissions
                SET order_id = NULLIF(?, ''), success = 1,
                    response_status = 'EVIDENCE_WRITE_FAILED',
                    needs_reconciliation = CASE WHEN ? = '' THEN 0 ELSE 1 END,
                    error_type = ?, error_message = ?
                WHERE submission_id = ?
                """,
                (
                    order_id,
                    order_id,
                    type(error).__name__,
                    _safe_error_message(error),
                    submission_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"order intent를 찾을 수 없습니다: {submission_id}")

    def unresolved_submission_outcomes(
        self, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return secret-safe uncertain intents that require operator proof.

        These rows cannot be reconciled automatically because no trustworthy
        venue order ID was persisted. They remain a restart gate until an
        operator records either a proved non-submission or the discovered ID.
        """
        if limit < 1:
            raise ValueError("limit은 1 이상이어야 합니다")
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = self._unresolved_outcome_rows(connection, limit=limit)
        return [dict(row) for row in rows]

    def assert_execution_ready(self) -> None:
        """Fail closed when a prior ambiguous POST is unresolved across restart."""
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM order_submissions WHERE {self._unresolved_sql()}"
            ).fetchone()
        count = int(row[0] or 0)
        if count:
            raise UnresolvedSubmissionOutcomeError(count)

    def resolve_uncertain_submission(
        self,
        submission_id: str,
        *,
        resolution: str,
        reason: str,
        order_id: str | None = None,
    ) -> None:
        """Persist explicit operator proof without erasing the original outcome.

        ``NO_ORDER_CREATED`` requires venue/operator evidence that no order was
        accepted. ``ORDER_ID_LINKED`` requires the discovered venue order ID and
        moves the row into the normal reconciliation queue. Resolution is
        immutable; corrections require preserving and reviewing the DB evidence.
        """
        resolution = str(resolution or "").strip().upper()
        if resolution not in {"NO_ORDER_CREATED", "ORDER_ID_LINKED"}:
            raise ValueError(
                "resolution은 NO_ORDER_CREATED 또는 ORDER_ID_LINKED여야 합니다"
            )
        safe_reason = _safe_error_message(RuntimeError(str(reason or "").strip()))
        if not safe_reason:
            raise ValueError("operator resolution reason은 비어 있을 수 없습니다")
        normalized_order_id = str(order_id or "").strip()
        if resolution == "ORDER_ID_LINKED" and not normalized_order_id:
            raise ValueError("ORDER_ID_LINKED에는 venue order_id가 필요합니다")
        if resolution == "NO_ORDER_CREATED" and normalized_order_id:
            raise ValueError("NO_ORDER_CREATED에는 order_id를 지정할 수 없습니다")

        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT response_status, order_id, outcome_resolution "
                "FROM order_submissions WHERE submission_id = ? AND simulation = 0",
                (submission_id,),
            ).fetchone()
            if row is None:
                raise ValueError("live order intent를 찾을 수 없습니다")
            if row["outcome_resolution"] is not None:
                raise ValueError("order intent outcome은 이미 operator가 해결했습니다")
            unresolved = row["response_status"] in {
                "INTENT",
                "SUBMIT_OUTCOME_UNKNOWN",
            } or (
                row["response_status"] == "EVIDENCE_WRITE_FAILED"
                and row["order_id"] is None
            )
            if not unresolved:
                raise ValueError("불확실한 order intent 상태가 아닙니다")
            if resolution == "NO_ORDER_CREATED" and row["order_id"] is not None:
                raise ValueError("order_id가 존재하는 intent는 NO_ORDER_CREATED로 해결할 수 없습니다")
            if (
                resolution == "ORDER_ID_LINKED"
                and row["order_id"] is not None
                and str(row["order_id"]) != normalized_order_id
            ):
                raise ValueError("기존 response order_id와 operator order_id가 다릅니다")
            connection.execute(
                """
                UPDATE order_submissions
                SET outcome_resolution = ?, outcome_resolved_at = ?,
                    outcome_resolution_reason = ?,
                    order_id = CASE WHEN ? = 'ORDER_ID_LINKED' THEN ? ELSE order_id END,
                    success = CASE WHEN ? = 'ORDER_ID_LINKED' THEN 1 ELSE 0 END,
                    needs_reconciliation = CASE
                        WHEN ? = 'ORDER_ID_LINKED' THEN 1 ELSE 0 END
                WHERE submission_id = ?
                """,
                (
                    resolution,
                    _utc_now(),
                    safe_reason,
                    resolution,
                    normalized_order_id,
                    resolution,
                    resolution,
                    submission_id,
                ),
            )

    def pending_submissions(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            unresolved_count = connection.execute(
                f"SELECT COUNT(*) FROM order_submissions WHERE {self._unresolved_sql()}"
            ).fetchone()[0]
            if unresolved_count:
                raise UnresolvedSubmissionOutcomeError(int(unresolved_count))
            rows = connection.execute(
                """
                SELECT submission_id, order_id, associated_trade_ids_json
                FROM order_submissions
                WHERE needs_reconciliation = 1 AND order_id IS NOT NULL
                ORDER BY CASE WHEN last_reconciled_at IS NULL THEN 0 ELSE 1 END,
                         COALESCE(last_reconciled_at, submitted_at) ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _unresolved_sql() -> str:
        return (
            "simulation = 0 AND ("
            "response_status IN ('INTENT', 'SUBMIT_OUTCOME_UNKNOWN') OR "
            "(response_status = 'EVIDENCE_WRITE_FAILED' AND order_id IS NULL)) AND COALESCE(("
            "(outcome_resolution = 'NO_ORDER_CREATED' AND order_id IS NULL "
            "AND outcome_resolved_at IS NOT NULL "
            "AND NULLIF(TRIM(outcome_resolution_reason), '') IS NOT NULL) OR "
            "(outcome_resolution = 'ORDER_ID_LINKED' AND order_id IS NOT NULL "
            "AND outcome_resolved_at IS NOT NULL "
            "AND NULLIF(TRIM(outcome_resolution_reason), '') IS NOT NULL)), 0) = 0"
        )

    @classmethod
    def _unresolved_outcome_rows(
        cls, connection: sqlite3.Connection, *, limit: int
    ) -> list[sqlite3.Row]:
        return connection.execute(
            f"""
            SELECT submission_id, run_id, submitted_at, response_status,
                   side, requested_price, requested_size, error_type
            FROM order_submissions
            WHERE {cls._unresolved_sql()}
            ORDER BY submitted_at, submission_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def record_order_status(
        self, submission_id: str, detail: Mapping[str, Any]
    ) -> list[str]:
        status = _normalize_status(detail.get("status"))
        associated = _string_list(
            detail.get("associate_trades") or detail.get("associated_trades")
        )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT associated_trade_ids_json FROM order_submissions "
                "WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
        existing = json.loads(row[0] or "[]") if row else []
        associated = list(dict.fromkeys([*existing, *associated]))
        selected = {
            "status": status,
            "original_size": _fixed_6_number(detail.get("original_size")),
            "size_matched": _fixed_6_number(detail.get("size_matched")),
            "price": _number(detail.get("price")),
            "associated": associated,
        }
        domain_errors: list[str] = []
        if selected["original_size"] is not None and not _finite_nonnegative(
            selected["original_size"]
        ):
            domain_errors.append("original_size_invalid")
        if selected["size_matched"] is not None and not _finite_nonnegative(
            selected["size_matched"]
        ):
            domain_errors.append("size_matched_invalid")
        if (
            selected["original_size"] is not None
            and selected["size_matched"] is not None
            and _finite_nonnegative(selected["original_size"])
            and _finite_nonnegative(selected["size_matched"])
            and selected["size_matched"]
            > selected["original_size"] + _QUANTITY_TOLERANCE
        ):
            domain_errors.append("size_matched_exceeds_original")
        if selected["price"] is not None and not _valid_fill_price(selected["price"]):
            domain_errors.append("order_price_invalid")
        domain_error = ",".join(domain_errors) or None
        selected["domain_error"] = domain_error
        fingerprint = hashlib.sha256(
            json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO order_status_events (
                    submission_id, observed_at, status, original_size,
                    size_matched, price, associated_trade_ids_json, fingerprint,
                    domain_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_id,
                    _utc_now(),
                    status,
                    selected["original_size"],
                    selected["size_matched"],
                    selected["price"],
                    json.dumps(associated),
                    fingerprint,
                    domain_error,
                ),
            )
            connection.execute(
                """
                UPDATE order_submissions
                SET latest_order_status = ?, latest_size_matched = ?,
                    associated_trade_ids_json = ?, last_reconciled_at = ?,
                    latest_status_domain_error = ?, reconciliation_error = ?
                WHERE submission_id = ?
                """,
                (
                    status,
                    selected["size_matched"],
                    json.dumps(associated),
                    _utc_now(),
                    domain_error,
                    domain_error,
                    submission_id,
                ),
            )
        return associated

    def record_fill(
        self,
        submission_id: str,
        order_id: str,
        trade: Mapping[str, Any],
    ) -> None:
        trade_id = str(trade.get("id") or "")
        if not trade_id:
            return
        status = _normalize_status(trade.get("status"))
        maker_orders = trade.get("maker_orders") or []
        maker_match = next(
            (
                item
                for item in maker_orders
                if isinstance(item, Mapping) and str(item.get("order_id")) == str(order_id)
            ),
            None,
        )
        reported_role = str(trade.get("trader_side") or "").upper()
        taker_match = (
            (
                bool(trade.get("taker_order_id"))
                and str(trade.get("taker_order_id")) == str(order_id)
            )
            or (reported_role == "TAKER" and maker_match is None)
        )
        execution_payload_present = any(
            key in trade
            for key in (
                "maker_orders", "taker_order_id", "trader_side", "size", "price"
            )
        )
        domain_errors: list[str] = []
        if maker_match is not None and taker_match:
            domain_errors.append("order_fill_correlation_conflict")
        if maker_match is not None:
            size = _fixed_6_number(maker_match.get("matched_amount"))
            price = _number(maker_match.get("price"))
            side = str(maker_match.get("side") or "").upper()
            liquidity_role = "MAKER"
            fee_rate_raw = maker_match.get("fee_rate_bps")
        elif taker_match:
            size = _fixed_6_number(trade.get("size"))
            price = _number(trade.get("price"))
            side = str(trade.get("side") or "").upper()
            liquidity_role = "TAKER"
            fee_rate_raw = trade.get("fee_rate_bps")
        else:
            size = _fixed_6_number(trade.get("size"))
            price = _number(trade.get("price"))
            side = str(trade.get("side") or "").upper()
            liquidity_role = "UNKNOWN"
            fee_rate_raw = trade.get("fee_rate_bps")
            if execution_payload_present:
                domain_errors.append("order_fill_correlation_invalid")
        if (
            reported_role in {"TAKER", "MAKER"}
            and liquidity_role != "UNKNOWN"
            and reported_role != liquidity_role
        ):
            domain_errors.append("liquidity_role_conflict")
        bucket_index, bucket_error = _bucket_index(trade.get("bucket_index"))
        if bucket_error:
            domain_errors.append(bucket_error)
        if status == "CONFIRMED" and execution_payload_present:
            if not _finite_positive(size):
                domain_errors.append("confirmed_size_invalid")
            if not _valid_fill_price(price):
                domain_errors.append("confirmed_price_invalid")
        fee_rate_bps = _number(fee_rate_raw)
        if fee_rate_raw is not None and not _finite_nonnegative(fee_rate_bps):
            domain_errors.append("fee_rate_invalid")
        fee_amount_source = maker_match if maker_match is not None else trade
        fee_amount_raw = _first_present(
            fee_amount_source, "fee_amount_usdc", "fee_amount", "fee"
        )
        fee_amount_usdc = _fixed_6_number(fee_amount_raw)
        if fee_amount_raw is not None and not _finite_nonnegative(fee_amount_usdc):
            domain_errors.append("fee_amount_invalid")
        domain_error = ",".join(dict.fromkeys(domain_errors)) or None
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO order_fills (
                    submission_id, order_id, trade_id, bucket_index, status, side, size,
                    price, liquidity_role, fee_rate_bps, fee_amount_usdc,
                    matched_at, last_update, transaction_hash, domain_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(submission_id, trade_id, bucket_index) DO UPDATE SET
                    status = CASE WHEN excluded.status = 'UNKNOWN'
                                  THEN order_fills.status ELSE excluded.status END,
                    side = COALESCE(NULLIF(excluded.side, ''), order_fills.side),
                    size = COALESCE(excluded.size, order_fills.size),
                    price = COALESCE(excluded.price, order_fills.price),
                    liquidity_role = CASE WHEN excluded.liquidity_role = 'UNKNOWN'
                                          THEN order_fills.liquidity_role
                                          ELSE excluded.liquidity_role END,
                    fee_rate_bps = COALESCE(excluded.fee_rate_bps, order_fills.fee_rate_bps),
                    fee_amount_usdc = COALESCE(excluded.fee_amount_usdc, order_fills.fee_amount_usdc),
                    matched_at = COALESCE(excluded.matched_at, order_fills.matched_at),
                    last_update = COALESCE(excluded.last_update, order_fills.last_update),
                    transaction_hash = COALESCE(excluded.transaction_hash, order_fills.transaction_hash),
                    domain_error = COALESCE(excluded.domain_error, order_fills.domain_error)
                """,
                (
                    submission_id,
                    str(order_id),
                    trade_id,
                    bucket_index,
                    status,
                    side,
                    size,
                    price,
                    liquidity_role,
                    fee_rate_bps,
                    fee_amount_usdc,
                    str(
                        trade.get("match_time")
                        or trade.get("matchtime")
                        or trade.get("timestamp")
                        or ""
                    )
                    or None,
                    str(trade.get("last_update") or "") or None,
                    str(trade.get("transaction_hash") or "") or None,
                    domain_error,
                ),
            )

    def finish_reconciliation(self, submission_id: str) -> bool:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            submission = connection.execute(
                """
                SELECT latest_order_status, latest_size_matched,
                       associated_trade_ids_json, latest_status_domain_error,
                       requested_price, requested_size
                FROM order_submissions WHERE submission_id = ?
                """,
                (submission_id,),
            ).fetchone()
            if submission is None:
                return False
            order_status = _normalize_status(submission["latest_order_status"])
            trade_ids = json.loads(submission["associated_trade_ids_json"] or "[]")
            complete = False
            if (
                submission["latest_status_domain_error"] is not None
                or not _valid_fill_price(submission["requested_price"])
                or not _finite_positive(submission["requested_size"])
                or (
                    submission["latest_size_matched"] is not None
                    and not _finite_nonnegative(submission["latest_size_matched"])
                )
            ):
                connection.execute(
                    "UPDATE order_submissions SET reconciliation_error = ? "
                    "WHERE submission_id = ?",
                    ("order/submission domain invalid", submission_id),
                )
                return False
            if order_status in _TERMINAL_ORDER_STATUSES:
                trade_rows: dict[str, list[tuple[str, float | None]]] = {}
                invalid_confirmed_domain = False
                for row in connection.execute(
                    "SELECT trade_id, status, size, price, bucket_index, domain_error "
                    "FROM order_fills "
                    "WHERE submission_id = ?",
                    (submission_id,),
                ):
                    fill_status = _normalize_status(row[1])
                    trade_rows.setdefault(str(row[0]), []).append(
                        (fill_status, _number(row[2]))
                    )
                    if fill_status == "CONFIRMED" and (
                        not _finite_positive(row[2])
                        or not _valid_fill_price(row[3])
                        or type(row[4]) is not int
                        or row[4] < 0
                        or row[5] is not None
                    ):
                        invalid_confirmed_domain = True
                if invalid_confirmed_domain:
                    connection.execute(
                        "UPDATE order_submissions SET reconciliation_error = ? "
                        "WHERE submission_id = ?",
                        ("confirmed fill domain invalid", submission_id),
                    )
                    return False
                if trade_ids:
                    # Every trade advertised by the order endpoint must reach a
                    # terminal state. A single confirmed partial fill is not
                    # enough to close a multi-fill order.
                    every_bucket_terminal = all(
                        str(trade_id) in trade_rows
                        and all(
                            status in _TERMINAL_TRADE_STATUSES
                            for status, _ in trade_rows[str(trade_id)]
                        )
                        for trade_id in trade_ids
                    )
                    confirmed_size = sum(
                        size or 0.0
                        for trade_id in trade_ids
                        for status, size in trade_rows.get(str(trade_id), [])
                        if status == "CONFIRMED"
                    )
                    matched_size = _number(submission["latest_size_matched"])
                    complete = (
                        every_bucket_terminal
                        and matched_size is not None
                        and math.isclose(
                            confirmed_size,
                            matched_size,
                            rel_tol=0.0,
                            abs_tol=_QUANTITY_TOLERANCE,
                        )
                    )
                    if (
                        matched_size is not None
                        and confirmed_size > matched_size + _QUANTITY_TOLERANCE
                    ):
                        connection.execute(
                            "UPDATE order_submissions SET reconciliation_error = ? "
                            "WHERE submission_id = ?",
                            (
                                "confirmed fill quantity exceeds latest_size_matched",
                                submission_id,
                            ),
                        )
                elif order_status != "MATCHED":
                    # A canceled/invalid order is provably unfilled only when
                    # the venue explicitly reports zero matched size. Missing
                    # size evidence remains pending (fail closed).
                    matched_size = _number(submission["latest_size_matched"])
                    complete = matched_size == 0.0
            if complete:
                connection.execute(
                    "UPDATE order_submissions SET needs_reconciliation = 0, "
                    "reconciliation_error = NULL WHERE submission_id = ?",
                    (submission_id,),
                )
            return complete

    def record_reconciliation_error(
        self, submission_id: str, error: BaseException
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE order_submissions
                SET last_reconciled_at = ?, reconciliation_error = ?
                WHERE submission_id = ?
                """,
                (_utc_now(), _safe_error_message(error), submission_id),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _ensure_schema(
        connection: sqlite3.Connection,
        *,
        migration_hook: Callable[[str], None] | None = None,
    ) -> None:
        """Create or migrate the ledger in one explicit SQLite transaction.

        ``sqlite3.Connection.executescript`` commits an already-open transaction
        before running its script. Schema migration therefore uses individual DDL
        statements so a copy/drop/rename failure rolls back as one unit. The hook
        is deliberately private test injection for proving mid-migration rollback.
        """
        if connection.in_transaction:
            raise RuntimeError("execution ledger schema migration requires no transaction")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS polybot_schema_versions (
                    component TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS order_submissions (
                    submission_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    strategy_name TEXT NOT NULL,
                    order_id TEXT,
                    token_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    requested_price REAL NOT NULL,
                    requested_size REAL NOT NULL,
                    submitted_at TEXT NOT NULL,
                    simulation INTEGER NOT NULL,
                    success INTEGER NOT NULL,
                    response_status TEXT NOT NULL,
                    making_amount REAL,
                    taking_amount REAL,
                    associated_trade_ids_json TEXT NOT NULL DEFAULT '[]',
                    latest_order_status TEXT,
                    latest_size_matched REAL,
                    latest_status_domain_error TEXT,
                    last_reconciled_at TEXT,
                    needs_reconciliation INTEGER NOT NULL,
                    error_type TEXT,
                    error_message TEXT,
                    reconciliation_error TEXT,
                    outcome_resolution TEXT,
                    outcome_resolved_at TEXT,
                    outcome_resolution_reason TEXT
                )
                """
            )
            submission_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(order_submissions)")
            }
            for column_name in (
                "outcome_resolution",
                "outcome_resolved_at",
                "outcome_resolution_reason",
                "latest_status_domain_error",
            ):
                if column_name not in submission_columns:
                    connection.execute(
                        f"ALTER TABLE order_submissions ADD COLUMN {column_name} TEXT"
                    )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS order_status_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    submission_id TEXT NOT NULL
                        REFERENCES order_submissions(submission_id),
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    original_size REAL,
                    size_matched REAL,
                    price REAL,
                    associated_trade_ids_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    domain_error TEXT,
                    UNIQUE(submission_id, fingerprint)
                )
                """
            )
            status_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(order_status_events)")
            }
            if "domain_error" not in status_columns:
                connection.execute(
                    "ALTER TABLE order_status_events ADD COLUMN domain_error TEXT"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS order_submissions_reconcile_idx "
                "ON order_submissions(needs_reconciliation, submitted_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS order_submissions_order_idx "
                "ON order_submissions(order_id)"
            )

            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "order_fills" not in tables and "order_fills_v2" in tables:
                # Recover the only surviving table from an interrupted legacy
                # migration before CREATE IF NOT EXISTS can hide its rows.
                connection.execute("ALTER TABLE order_fills_v2 RENAME TO order_fills")
            elif "order_fills" not in tables:
                ExecutionLedger._create_order_fills_table(connection, "order_fills")

            fill_info = list(connection.execute("PRAGMA table_info(order_fills)"))
            fill_columns = {str(row[1]) for row in fill_info}
            for column_name, column_type in (
                ("liquidity_role", "TEXT"),
                ("fee_amount_usdc", "REAL"),
                ("bucket_index", "INTEGER NOT NULL DEFAULT 0"),
                ("domain_error", "TEXT"),
            ):
                if column_name not in fill_columns:
                    connection.execute(
                        f"ALTER TABLE order_fills ADD COLUMN {column_name} {column_type}"
                    )

            fill_info = list(connection.execute("PRAGMA table_info(order_fills)"))
            fill_columns = {str(row[1]) for row in fill_info}
            migration_columns = {
                "submission_id", "order_id", "trade_id", "bucket_index", "status",
                "side", "size", "price", "liquidity_role", "fee_rate_bps",
                "fee_amount_usdc", "matched_at", "last_update", "transaction_hash",
                "domain_error",
            }
            missing_migration_columns = migration_columns - fill_columns
            if missing_migration_columns:
                raise RuntimeError(
                    "order_fills schema를 안전하게 migration할 수 없습니다: "
                    f"{sorted(missing_migration_columns)}"
                )
            primary_key = [
                str(row[1])
                for row in sorted(fill_info, key=lambda item: item[5])
                if row[5]
            ]
            if primary_key != ["submission_id", "trade_id", "bucket_index"]:
                connection.execute("DROP TABLE IF EXISTS order_fills_v2")
                ExecutionLedger._create_order_fills_table(connection, "order_fills_v2")
                connection.execute(
                    """
                    INSERT INTO order_fills_v2 (
                        submission_id, order_id, trade_id, bucket_index, status,
                        side, size, price, liquidity_role, fee_rate_bps,
                        fee_amount_usdc, matched_at, last_update, transaction_hash,
                        domain_error
                    )
                    SELECT submission_id, order_id, trade_id,
                           COALESCE(bucket_index, 0), status, side, size, price,
                           liquidity_role, fee_rate_bps, fee_amount_usdc,
                           matched_at, last_update, transaction_hash, domain_error
                    FROM order_fills
                    """
                )
                if migration_hook is not None:
                    migration_hook("after_order_fills_copy")
                connection.execute("DROP TABLE order_fills")
                connection.execute("ALTER TABLE order_fills_v2 RENAME TO order_fills")
            else:
                # Both names can remain after the old non-atomic migration failed.
                connection.execute("DROP TABLE IF EXISTS order_fills_v2")

            connection.execute(
                "CREATE INDEX IF NOT EXISTS order_fills_order_idx "
                "ON order_fills(order_id)"
            )
            required_columns = {
                "order_submissions": {
                    "submission_id", "run_id", "order_id", "requested_price",
                    "requested_size", "simulation", "success", "response_status",
                    "needs_reconciliation", "outcome_resolution",
                    "outcome_resolved_at", "outcome_resolution_reason",
                    "latest_status_domain_error",
                },
                "order_status_events": {
                    "submission_id", "status", "original_size", "size_matched",
                    "domain_error",
                },
                "order_fills": {
                    "submission_id", "order_id", "trade_id", "bucket_index",
                    "status", "size", "price", "fee_rate_bps", "domain_error",
                },
            }
            for table, required in required_columns.items():
                columns = {
                    str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                missing = required - columns
                if missing:
                    raise RuntimeError(
                        f"{table} schema에 필수 컬럼이 없습니다: {sorted(missing)}"
                    )
            foreign_key_errors = list(connection.execute("PRAGMA foreign_key_check"))
            if foreign_key_errors:
                raise RuntimeError("execution ledger foreign key 검증에 실패했습니다")
            connection.execute(
                """
                INSERT INTO polybot_schema_versions(component, version, updated_at)
                VALUES ('execution_ledger', 6, ?)
                ON CONFLICT(component) DO UPDATE SET
                    version = excluded.version, updated_at = excluded.updated_at
                """,
                (_utc_now(),),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _create_order_fills_table(
        connection: sqlite3.Connection, table_name: str
    ) -> None:
        if table_name not in {"order_fills", "order_fills_v2"}:
            raise ValueError("unexpected order_fills table name")
        connection.execute(
            f"""
            CREATE TABLE {table_name} (
                submission_id TEXT NOT NULL
                    REFERENCES order_submissions(submission_id),
                order_id TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                bucket_index INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                side TEXT,
                size REAL,
                price REAL,
                liquidity_role TEXT,
                fee_rate_bps REAL,
                fee_amount_usdc REAL,
                matched_at TEXT,
                last_update TEXT,
                transaction_hash TEXT,
                domain_error TEXT,
                PRIMARY KEY(submission_id, trade_id, bucket_index)
            )
            """
        )

    def _bootstrap_legacy_orders(self, connection: sqlite3.Connection) -> None:
        """Register recent pre-ledger trade order IDs for best-effort reconciliation."""
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "trades" not in tables:
            return
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(trades)")
        }
        required = {
            "token_id",
            "buy_order_id",
            "buy_price",
            "buy_shares",
            "buy_timestamp",
            "status",
        }
        if not required.issubset(columns):
            return

        select_columns = [
            "token_id",
            "buy_order_id",
            "buy_price",
            "buy_shares",
            "buy_timestamp",
            "sell_order_id" if "sell_order_id" in columns else "NULL AS sell_order_id",
            "sell_price" if "sell_price" in columns else "NULL AS sell_price",
            "sell_shares" if "sell_shares" in columns else "NULL AS sell_shares",
            "sell_timestamp" if "sell_timestamp" in columns else "NULL AS sell_timestamp",
            "status" if "status" in columns else "NULL AS status",
        ]
        rows = connection.execute(
            f"SELECT {', '.join(select_columns)} FROM trades "
            "WHERE (datetime(buy_timestamp) >= datetime('now', '-90 days') "
            "OR status IN ('HOLDING', 'PENDING_BUY', 'PENDING_SELL'))"
        ).fetchall()
        now = _utc_now()
        for row in rows:
            token_id = str(row[0])
            candidates = (
                ("BUY", row[1], row[2], row[3], row[4]),
                ("SELL", row[5], row[6], row[7] or row[3], row[8]),
            )
            for side, order_id, price, size, submitted_at in candidates:
                if not order_id or str(order_id).startswith("SIM"):
                    continue
                exists = connection.execute(
                    "SELECT 1 FROM order_submissions WHERE order_id = ? LIMIT 1",
                    (str(order_id),),
                ).fetchone()
                if exists:
                    continue
                submission_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"polybot:{order_id}"))
                connection.execute(
                    """
                    INSERT INTO order_submissions (
                        submission_id, run_id, strategy_name, order_id, token_id,
                        side, requested_price, requested_size, submitted_at,
                        simulation, success, response_status,
                        associated_trade_ids_json, needs_reconciliation
                    ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, 0, 1,
                              'LEGACY_ASSUMED', '[]', 1)
                    """,
                    (
                        submission_id,
                        self.strategy_name,
                        str(order_id),
                        token_id,
                        side,
                        float(price or 0),
                        float(size or 0),
                        str(submitted_at or now),
                    ),
                )
