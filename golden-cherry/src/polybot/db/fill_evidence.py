"""Fail-closed exact CLOB fill evidence for Golden Cherry lifecycle changes."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Optional

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session


_TERMINAL_ZERO_FILL_ORDER_STATUSES = {
    "CANCELED",
    "CANCELLED",
    "CANCELED_MARKET_RESOLVED",
    "INVALID",
}


@dataclass(frozen=True)
class ExactFillEvidence:
    """Exact-order evidence used to authorize live lifecycle transitions."""

    state: str
    order_id: str
    order_status: Optional[str] = None
    side: Optional[str] = None
    requested_size: Optional[float] = None
    latest_size_matched: Optional[float] = None
    needs_reconciliation: bool = True
    reconciled_full_fill: bool = False
    confirmed_size: Optional[float] = None
    confirmed_vwap: Optional[float] = None
    confirmed_fee_usdc: Optional[float] = None
    fee_complete: bool = False
    matched_at: Optional[str] = None
    detail: Optional[str] = None

    @property
    def has_confirmed_fill(self) -> bool:
        return self.state == "confirmed"

    @property
    def has_reconciled_full_fill(self) -> bool:
        return self.has_confirmed_fill and self.reconciled_full_fill


def _normalize_status(value: Any) -> str:
    status = str(value or "").strip().upper()
    prefix = "ORDER_STATUS_"
    return status[len(prefix) :] if status.startswith(prefix) else status


def _finite_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def get_exact_order_fill_evidence(
    session: Session,
    order_id: Optional[str],
    *,
    expected_side: str,
) -> ExactFillEvidence:
    """Return exact confirmed-fill evidence without inferring from acceptance."""

    side = str(expected_side or "").strip().upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("expected_side must be BUY or SELL")
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return ExactFillEvidence(
            "unavailable",
            normalized_order_id,
            side=side,
            detail="missing_order_id",
        )

    try:
        tables = set(inspect(session.get_bind()).get_table_names())
    except Exception as error:  # noqa: BLE001 - evidence must fail closed
        return ExactFillEvidence(
            "unavailable",
            normalized_order_id,
            side=side,
            detail=f"schema_inspection_{type(error).__name__}",
        )
    if not {"order_submissions", "order_fills"}.issubset(tables):
        return ExactFillEvidence(
            "unavailable",
            normalized_order_id,
            side=side,
            detail="ledger_tables_missing",
        )

    try:
        submissions = (
            session.execute(
                text(
                    "SELECT submission_id, side, requested_size, "
                    "latest_order_status, latest_size_matched, "
                    "latest_status_domain_error, needs_reconciliation, "
                    "reconciliation_error, simulation "
                    "FROM order_submissions WHERE order_id = :order_id"
                ),
                {"order_id": normalized_order_id},
            )
            .mappings()
            .all()
        )
    except Exception as error:  # noqa: BLE001
        return ExactFillEvidence(
            "unavailable",
            normalized_order_id,
            side=side,
            detail=f"submission_query_{type(error).__name__}",
        )
    if len(submissions) != 1:
        detail = "submission_missing" if not submissions else "submission_ambiguous"
        return ExactFillEvidence(
            "unavailable", normalized_order_id, side=side, detail=detail
        )

    submission = submissions[0]
    order_status = _normalize_status(submission["latest_order_status"])
    if str(submission["side"] or "").strip().upper() != side:
        return ExactFillEvidence(
            "unavailable",
            normalized_order_id,
            order_status=order_status,
            side=side,
            detail="submission_side_mismatch",
        )
    if int(submission["simulation"] or 0):
        return ExactFillEvidence(
            "unavailable",
            normalized_order_id,
            order_status=order_status,
            side=side,
            detail="simulation_submission_has_no_live_fill",
        )

    requested_size = _finite_float(submission["requested_size"])
    matched_size = _finite_float(submission["latest_size_matched"])
    if requested_size is None or requested_size <= 0:
        return ExactFillEvidence(
            "unavailable",
            normalized_order_id,
            order_status=order_status,
            side=side,
            detail="submission_requested_size_invalid",
        )
    raw_needs_reconciliation = submission["needs_reconciliation"]
    if raw_needs_reconciliation not in (0, 1, False, True):
        return ExactFillEvidence(
            "unavailable",
            normalized_order_id,
            order_status=order_status,
            side=side,
            requested_size=requested_size,
            latest_size_matched=matched_size,
            detail="submission_reconciliation_flag_invalid",
        )
    needs_reconciliation = bool(raw_needs_reconciliation)
    if (
        str(submission["latest_status_domain_error"] or "").strip()
        or str(submission["reconciliation_error"] or "").strip()
    ):
        return ExactFillEvidence(
            "unavailable",
            normalized_order_id,
            order_status=order_status,
            side=side,
            requested_size=requested_size,
            latest_size_matched=matched_size,
            needs_reconciliation=needs_reconciliation,
            detail="submission_reconciliation_domain_error",
        )

    try:
        fills = (
            session.execute(
                text(
                    "SELECT status, side, size, price, liquidity_role, "
                    "fee_rate_bps, fee_amount_usdc, matched_at, domain_error "
                    "FROM order_fills WHERE submission_id = :submission_id "
                    "AND order_id = :order_id"
                ),
                {
                    "submission_id": submission["submission_id"],
                    "order_id": normalized_order_id,
                },
            )
            .mappings()
            .all()
        )
    except Exception as error:  # noqa: BLE001
        return ExactFillEvidence(
            "unavailable",
            normalized_order_id,
            order_status=order_status,
            side=side,
            detail=f"fill_query_{type(error).__name__}",
        )

    confirmed = [
        row
        for row in fills
        if str(row["status"] or "")
        .strip()
        .upper()
        .removeprefix("TRADE_STATUS_")
        == "CONFIRMED"
    ]
    if confirmed:
        size_total = 0.0
        notional_total = 0.0
        fee_total = 0.0
        fee_complete = True
        matched_values: list[str] = []
        for row in confirmed:
            size = _finite_float(row["size"])
            price = _finite_float(row["price"])
            if (
                str(row["side"] or "").strip().upper() != side
                or size is None
                or size <= 0
                or price is None
                or not 0 < price <= 1
                or str(row["domain_error"] or "").strip()
            ):
                return ExactFillEvidence(
                    "unavailable",
                    normalized_order_id,
                    order_status=order_status,
                    side=side,
                    detail="confirmed_fill_contract_invalid",
                )
            size_total += size
            notional_total += size * price

            fee_rate = None
            if row["fee_rate_bps"] is not None:
                fee_rate = _finite_float(row["fee_rate_bps"])
                if fee_rate is None or fee_rate < 0:
                    return ExactFillEvidence(
                        "unavailable",
                        normalized_order_id,
                        order_status=order_status,
                        side=side,
                        detail="confirmed_fill_fee_rate_invalid",
                    )
            if row["fee_amount_usdc"] is None:
                liquidity_role = str(row["liquidity_role"] or "").strip().upper()
                # Polymarket platform maker fees are zero, and Golden Cherry
                # does not submit builder-fee orders.  The venue may omit both
                # fee fields for those fills.  Keep every other missing-fee
                # shape fail-closed, including TAKER and unknown roles.
                known_zero_fee = fee_rate == 0.0 or (
                    fee_rate is None and liquidity_role == "MAKER"
                )
                if not known_zero_fee:
                    fee_complete = False
            else:
                fee = _finite_float(row["fee_amount_usdc"])
                if fee is None or fee < 0:
                    return ExactFillEvidence(
                        "unavailable",
                        normalized_order_id,
                        order_status=order_status,
                        side=side,
                        detail="confirmed_fill_fee_invalid",
                    )
                fee_total += fee
            if row["matched_at"]:
                matched_values.append(str(row["matched_at"]))

        reconciled_full_fill = (
            not needs_reconciliation
            and matched_size is not None
            and matched_size > 0
            and math.isclose(size_total, matched_size, rel_tol=1e-9, abs_tol=1e-6)
            and (
                # MATCHED is terminal at the venue; its size may be quantized
                # slightly below the pre-quantization requested size.
                order_status == "MATCHED"
                or math.isclose(
                    matched_size, requested_size, rel_tol=1e-9, abs_tol=1e-6
                )
            )
        )
        return ExactFillEvidence(
            "confirmed",
            normalized_order_id,
            order_status=order_status,
            side=side,
            requested_size=requested_size,
            latest_size_matched=matched_size,
            needs_reconciliation=needs_reconciliation,
            reconciled_full_fill=reconciled_full_fill,
            confirmed_size=size_total,
            confirmed_vwap=notional_total / size_total,
            confirmed_fee_usdc=fee_total if fee_complete else None,
            fee_complete=fee_complete,
            matched_at=max(matched_values) if matched_values else None,
            detail=(
                "confirmed_reconciled_full_fill"
                if reconciled_full_fill
                else "confirmed_partial_or_unreconciled"
            ),
        )

    if (
        order_status in _TERMINAL_ZERO_FILL_ORDER_STATUSES
        and matched_size == 0.0
        and not needs_reconciliation
    ):
        return ExactFillEvidence(
            "terminal_zero_fill",
            normalized_order_id,
            order_status=order_status,
            side=side,
            requested_size=requested_size,
            latest_size_matched=matched_size,
            needs_reconciliation=False,
            confirmed_size=0.0,
            detail="terminal_status_and_zero_matched_size",
        )

    return ExactFillEvidence(
        "pending",
        normalized_order_id,
        order_status=order_status,
        side=side,
        requested_size=requested_size,
        latest_size_matched=matched_size,
        needs_reconciliation=needs_reconciliation,
        detail=(
            "reconciliation_pending"
            if needs_reconciliation
            else "no_exact_confirmed_fill"
        ),
    )
