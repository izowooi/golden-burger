"""Repository operations for Crown Momentum trades and research evidence."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from polybot_observability import (
    compact_maintenance_active,
    current_run_id,
    membership_details_due,
)
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.orm import Session

from .models import (
    MarketCatalog,
    MarketSnapshot,
    MarketSweep,
    MarketSweepMembership,
    SkippedMarket,
    Trade,
    TradeStatus,
)
from ..strategy.filters import get_event_metadata, get_proven_resolution


logger = logging.getLogger(__name__)
_OPEN_STATUSES = (
    TradeStatus.PENDING_BUY,
    TradeStatus.HOLDING,
    TradeStatus.PENDING_SELL,
)

_TERMINAL_ZERO_FILL_ORDER_STATUSES = {
    "CANCELED",
    "CANCELLED",
    "CANCELED_MARKET_RESOLVED",
    "INVALID",
}


@dataclass(frozen=True)
class ExactFillEvidence:
    """Exact-order fill evidence used for live position/P&L transitions.

    ``state`` is one of ``confirmed``, ``terminal_zero_fill``, ``pending``, or
    ``unavailable``.  Only ``confirmed`` authorizes live settlement accounting.
    """

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
        return self.state == "confirmed" and self.reconciled_full_fill


class TradeRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, trade_id: int) -> Optional[Trade]:
        return self.session.get(Trade, trade_id)

    def get_by_condition_id(self, condition_id: str) -> Optional[Trade]:
        return (
            self.session.query(Trade)
            .filter(Trade.condition_id == condition_id)
            .order_by(Trade.id.desc())
            .first()
        )

    get_latest_by_condition_id = get_by_condition_id

    def has_holding(self, condition_id: str) -> bool:
        return (
            self.session.query(Trade.id)
            .filter(
                Trade.condition_id == condition_id,
                Trade.status.in_(_OPEN_STATUSES),
            )
            .first()
            is not None
        )

    def can_reenter(
        self,
        condition_id: str,
        cooldown_hours: float,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        now = now or datetime.utcnow()
        if self.has_holding(condition_id):
            return False, "holding"
        cutoff = now - timedelta(hours=cooldown_hours)
        recent_close = (
            self.session.query(Trade.id)
            .filter(
                Trade.condition_id == condition_id,
                Trade.status.in_((TradeStatus.COMPLETED, TradeStatus.RESOLVED)),
                or_(
                    Trade.sell_timestamp >= cutoff,
                    Trade.resolution_observed_at >= cutoff,
                ),
            )
            .first()
        )
        if recent_close:
            return False, "close_cooldown"
        recent_skip = (
            self.session.query(SkippedMarket)
            .filter(
                SkippedMarket.condition_id == condition_id,
                SkippedMarket.skipped_at >= cutoff,
            )
            .order_by(SkippedMarket.skipped_at.desc())
            .first()
        )
        if recent_skip:
            return False, f"skip_cooldown_{recent_skip.reason}"
        return True, "ok"

    def is_in_reentry_cooldown(self, condition_id: str, cooldown_hours: float) -> bool:
        allowed, _ = self.can_reenter(condition_id, cooldown_hours)
        return not allowed

    def create_trade(self, **kwargs) -> Trade:
        trade = Trade(**kwargs)
        self.session.add(trade)
        self.session.commit()
        return trade

    def update_trade(self, trade_id: int, **kwargs) -> Trade:
        trade = self.session.get(Trade, trade_id)
        if trade is None:
            raise ValueError(f"Trade {trade_id} not found")
        for key, value in kwargs.items():
            if not hasattr(trade, key):
                raise ValueError(f"Unknown Trade field: {key}")
            setattr(trade, key, value)
        trade.updated_at = datetime.utcnow()
        self.session.commit()
        return trade

    def get_holding_trades(self) -> List[Trade]:
        return (
            self.session.query(Trade).filter(Trade.status == TradeStatus.HOLDING).all()
        )

    def get_pending_buy_trades(self) -> List[Trade]:
        return (
            self.session.query(Trade)
            .filter(Trade.status == TradeStatus.PENDING_BUY)
            .all()
        )

    def get_pending_sell_trades(self) -> List[Trade]:
        return (
            self.session.query(Trade)
            .filter(Trade.status == TradeStatus.PENDING_SELL)
            .all()
        )

    def get_trades_by_date(self, target_date: date) -> List[Trade]:
        start = datetime.combine(target_date, datetime.min.time())
        end = datetime.combine(target_date, datetime.max.time())
        return (
            self.session.query(Trade)
            .filter(Trade.buy_timestamp >= start, Trade.buy_timestamp <= end)
            .all()
        )

    def get_all_trades(self) -> List[Trade]:
        return self.session.query(Trade).all()

    def get_position_count(self) -> int:
        return (
            self.session.query(func.count(Trade.id))
            .filter(Trade.status.in_(_OPEN_STATUSES))
            .scalar()
            or 0
        )

    def get_open_notional_usdc(self) -> float:
        """Return requested BUY notional for currently open strategy records."""
        value = (
            self.session.query(func.sum(Trade.buy_amount))
            .filter(Trade.status.in_(_OPEN_STATUSES))
            .scalar()
        )
        try:
            result = float(value or 0.0)
        except (TypeError, ValueError):
            return float("inf")
        return result if math.isfinite(result) and result >= 0 else float("inf")

    def get_event_position_count(self, event_id: Optional[str]) -> int:
        if not event_id:
            return 0
        return (
            self.session.query(func.count(Trade.id))
            .filter(
                Trade.event_id == event_id,
                Trade.status.in_(_OPEN_STATUSES),
            )
            .scalar()
            or 0
        )

    get_open_event_position_count = get_event_position_count

    def mark_as_skipped(self, condition_id: str, reason: str) -> SkippedMarket:
        skipped = SkippedMarket(condition_id=condition_id, reason=reason)
        self.session.add(skipped)
        self.session.commit()
        return skipped

    @staticmethod
    def _normalize_order_status(value: Any) -> str:
        status = str(value or "").strip().upper()
        prefix = "ORDER_STATUS_"
        return status[len(prefix) :] if status.startswith(prefix) else status

    def get_exact_order_fill_evidence(
        self,
        order_id: Optional[str],
        *,
        expected_side: str,
    ) -> ExactFillEvidence:
        """Read exact CONFIRMED fills from the co-located execution ledger.

        This helper never treats an accepted GTC order, an approximate token
        match, or a non-terminal empty catalog as a position.  Missing/ambiguous
        schema and malformed fill rows return ``unavailable`` rather than
        guessing.
        """
        normalized_side = str(expected_side or "").strip().upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError("expected_side must be BUY or SELL")
        normalized_order_id = str(order_id or "").strip()
        if not normalized_order_id:
            return ExactFillEvidence(
                "unavailable",
                normalized_order_id,
                side=normalized_side,
                detail="missing_order_id",
            )
        try:
            table_names = set(inspect(self.session.get_bind()).get_table_names())
        except Exception as error:
            return ExactFillEvidence(
                "unavailable",
                normalized_order_id,
                detail=f"schema_inspection_{type(error).__name__}",
            )
        if not {"order_submissions", "order_fills"}.issubset(table_names):
            return ExactFillEvidence(
                "unavailable", normalized_order_id, detail="ledger_tables_missing"
            )

        try:
            submissions = (
                self.session.execute(
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
        except Exception as error:
            return ExactFillEvidence(
                "unavailable",
                normalized_order_id,
                detail=f"submission_query_{type(error).__name__}",
            )
        if len(submissions) != 1:
            detail = "submission_missing" if not submissions else "submission_ambiguous"
            return ExactFillEvidence("unavailable", normalized_order_id, detail=detail)
        submission = submissions[0]
        order_status = self._normalize_order_status(submission["latest_order_status"])
        if str(submission["side"] or "").strip().upper() != normalized_side:
            return ExactFillEvidence(
                "unavailable",
                normalized_order_id,
                order_status=order_status,
                side=normalized_side,
                detail="submission_side_mismatch",
            )
        if int(submission["simulation"] or 0):
            return ExactFillEvidence(
                "unavailable",
                normalized_order_id,
                order_status=order_status,
                side=normalized_side,
                detail="simulation_submission_has_no_live_fill",
            )
        try:
            requested_size = float(submission["requested_size"])
        except (TypeError, ValueError):
            requested_size = float("nan")
        if not math.isfinite(requested_size) or requested_size <= 0:
            return ExactFillEvidence(
                "unavailable",
                normalized_order_id,
                order_status=order_status,
                side=normalized_side,
                detail="submission_requested_size_invalid",
            )
        try:
            matched_size = (
                float(submission["latest_size_matched"])
                if submission["latest_size_matched"] is not None
                else None
            )
        except (TypeError, ValueError):
            matched_size = None
        raw_needs_reconciliation = submission["needs_reconciliation"]
        if raw_needs_reconciliation not in (0, 1, False, True):
            return ExactFillEvidence(
                "unavailable",
                normalized_order_id,
                order_status=order_status,
                side=normalized_side,
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
                side=normalized_side,
                requested_size=requested_size,
                latest_size_matched=matched_size,
                needs_reconciliation=needs_reconciliation,
                detail="submission_reconciliation_domain_error",
            )

        try:
            fills = (
                self.session.execute(
                    text(
                        "SELECT status, side, size, price, fee_amount_usdc, "
                        "matched_at, domain_error FROM order_fills "
                        "WHERE submission_id = :submission_id AND order_id = :order_id"
                    ),
                    {
                        "submission_id": submission["submission_id"],
                        "order_id": normalized_order_id,
                    },
                )
                .mappings()
                .all()
            )
        except Exception as error:
            return ExactFillEvidence(
                "unavailable",
                normalized_order_id,
                order_status=order_status,
                detail=f"fill_query_{type(error).__name__}",
            )
        confirmed = [
            row
            for row in fills
            if str(row["status"] or "").strip().upper().removeprefix("TRADE_STATUS_")
            == "CONFIRMED"
        ]
        if confirmed:
            size_total = 0.0
            notional_total = 0.0
            fee_total = 0.0
            fee_complete = True
            matched_values: List[str] = []
            for row in confirmed:
                try:
                    size = float(row["size"])
                    price = float(row["price"])
                except (TypeError, ValueError):
                    return ExactFillEvidence(
                        "unavailable",
                        normalized_order_id,
                        order_status=order_status,
                        detail="confirmed_fill_numeric_invalid",
                    )
                if (
                    str(row["side"] or "").strip().upper() != normalized_side
                    or not math.isfinite(size)
                    or size <= 0
                    or not math.isfinite(price)
                    or not 0 < price <= 1
                    or str(row["domain_error"] or "").strip()
                ):
                    return ExactFillEvidence(
                        "unavailable",
                        normalized_order_id,
                        order_status=order_status,
                        detail="confirmed_fill_contract_invalid",
                    )
                size_total += size
                notional_total += size * price
                raw_fee = row["fee_amount_usdc"]
                if raw_fee is None:
                    fee_complete = False
                else:
                    try:
                        fee = float(raw_fee)
                    except (TypeError, ValueError):
                        return ExactFillEvidence(
                            "unavailable",
                            normalized_order_id,
                            order_status=order_status,
                            detail="confirmed_fill_fee_invalid",
                        )
                    if not math.isfinite(fee) or fee < 0:
                        return ExactFillEvidence(
                            "unavailable",
                            normalized_order_id,
                            order_status=order_status,
                            detail="confirmed_fill_fee_invalid",
                        )
                    fee_total += fee
                if row["matched_at"]:
                    matched_values.append(str(row["matched_at"]))
            reconciled_full_fill = (
                not needs_reconciliation
                and matched_size is not None
                and math.isfinite(matched_size)
                and matched_size > 0
                and math.isclose(size_total, matched_size, rel_tol=1e-9, abs_tol=1e-6)
                and math.isclose(
                    matched_size, requested_size, rel_tol=1e-9, abs_tol=1e-6
                )
            )
            return ExactFillEvidence(
                "confirmed",
                normalized_order_id,
                order_status=order_status,
                side=normalized_side,
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
            and matched_size is not None
            and math.isfinite(matched_size)
            and matched_size == 0.0
            and not needs_reconciliation
        ):
            return ExactFillEvidence(
                "terminal_zero_fill",
                normalized_order_id,
                order_status=order_status,
                side=normalized_side,
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
            side=normalized_side,
            requested_size=requested_size,
            latest_size_matched=matched_size,
            needs_reconciliation=needs_reconciliation,
            detail=(
                "reconciliation_pending"
                if needs_reconciliation
                else "no_exact_confirmed_fill"
            ),
        )

    def get_exact_buy_fill_evidence(self, order_id: Optional[str]) -> ExactFillEvidence:
        return self.get_exact_order_fill_evidence(order_id, expected_side="BUY")

    def get_exact_sell_fill_evidence(
        self, order_id: Optional[str]
    ) -> ExactFillEvidence:
        return self.get_exact_order_fill_evidence(order_id, expected_side="SELL")

    def save_snapshot(
        self,
        condition_id: str,
        probability: float,
        liquidity: Optional[float] = None,
        volume_24h: Optional[float] = None,
        best_bid: Optional[float] = None,
        best_ask: Optional[float] = None,
        spread: Optional[float] = None,
        source_updated_at: Optional[str] = None,
        market: Optional[Dict[str, Any]] = None,
        commit: bool = True,
    ) -> MarketSnapshot:
        if market is not None:
            self._upsert_market_catalog(condition_id, market)
        snapshot = MarketSnapshot(
            condition_id=condition_id,
            probability=probability,
            liquidity=liquidity,
            volume_24h=volume_24h,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            source_updated_at=source_updated_at,
            run_id=current_run_id(),
        )
        self.session.add(snapshot)
        self.session.flush()
        if commit:
            self.session.commit()
        return snapshot

    def get_snapshots_since(
        self, condition_id: str, since: datetime
    ) -> List[MarketSnapshot]:
        return (
            self.session.query(MarketSnapshot)
            .filter(
                MarketSnapshot.condition_id == condition_id,
                MarketSnapshot.timestamp >= since,
            )
            .order_by(MarketSnapshot.timestamp.asc(), MarketSnapshot.id.asc())
            .all()
        )

    def get_latest_snapshot(self, condition_id: str) -> Optional[MarketSnapshot]:
        return (
            self.session.query(MarketSnapshot)
            .filter(MarketSnapshot.condition_id == condition_id)
            .order_by(MarketSnapshot.timestamp.desc(), MarketSnapshot.id.desc())
            .first()
        )

    def get_latest_snapshot_before_run(
        self,
        condition_id: str,
        run_id: Optional[str] = None,
        before: Optional[datetime] = None,
    ) -> Optional[MarketSnapshot]:
        """Return prior-cycle evidence, never a row from the supplied run."""
        query = self.session.query(MarketSnapshot).filter(
            MarketSnapshot.condition_id == condition_id
        )
        if run_id:
            query = query.filter(
                or_(MarketSnapshot.run_id.is_(None), MarketSnapshot.run_id != run_id)
            )
        if before is not None:
            query = query.filter(MarketSnapshot.timestamp < before)
        return query.order_by(
            MarketSnapshot.timestamp.desc(), MarketSnapshot.id.desc()
        ).first()

    def save_market_catalog(
        self,
        condition_id: str,
        market: Dict[str, Any],
        *,
        commit: bool = False,
    ) -> None:
        self._upsert_market_catalog(condition_id, market)
        if commit:
            self.session.commit()

    def _upsert_market_catalog(self, condition_id: str, market: Dict[str, Any]) -> None:
        events = market.get("events") or []
        event = (
            events[0]
            if isinstance(events, list) and events and isinstance(events[0], dict)
            else {}
        )
        event_meta = get_event_metadata(market)
        tags = market.get("tags") or []
        fee_schedule = market.get("feeSchedule") or {}
        resolution = get_proven_resolution(market)

        def bool_int(value: Any) -> Optional[int]:
            return None if not isinstance(value, bool) else int(value)

        values = {
            "market_id": str(market.get("id") or "") or None,
            "market_slug": market.get("slug"),
            "question": market.get("question"),
            "event_id": event_meta["event_id"],
            "event_slug": event_meta["event_slug"],
            "event_title": event.get("title"),
            "event_market_count": len(event.get("markets") or []) or None,
            "end_date": market.get("endDate"),
            "outcomes_json": json.dumps(
                market.get("outcomes") or [], ensure_ascii=False
            ),
            "outcome_prices_json": json.dumps(market.get("outcomePrices") or []),
            "token_ids_json": json.dumps(market.get("clobTokenIds") or []),
            "tags_json": json.dumps(
                [
                    {
                        "id": tag.get("id"),
                        "slug": tag.get("slug"),
                        "label": tag.get("label"),
                    }
                    for tag in tags
                    if isinstance(tag, dict)
                ],
                ensure_ascii=False,
            ),
            "neg_risk": bool_int(market.get("negRisk")),
            "active": bool_int(market.get("active")),
            "closed": bool_int(market.get("closed")),
            "accepting_orders": bool_int(market.get("acceptingOrders")),
            "enable_order_book": bool_int(market.get("enableOrderBook")),
            "fees_enabled": bool_int(market.get("feesEnabled")),
            "fee_rate": fee_schedule.get("rate")
            if isinstance(fee_schedule, dict)
            else None,
            "resolution_status": (
                resolution["status"]
                if resolution
                else market.get("umaResolutionStatus")
            ),
            "resolved_outcome": resolution["outcome"] if resolution else None,
            "resolved_value": resolution["yes_payout"] if resolution else None,
            "resolved_at": market.get("resolvedAt") or market.get("closedTime"),
            "source_updated_at": market.get("updatedAt"),
            "last_seen_at": datetime.utcnow(),
        }
        catalog = self.session.get(MarketCatalog, condition_id)
        if catalog is None:
            self.session.add(MarketCatalog(condition_id=condition_id, **values))
        else:
            for key, value in values.items():
                setattr(catalog, key, value)

    @staticmethod
    def _attestation_datetime(value: Any) -> datetime:
        parsed = (
            value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        )
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    def record_market_sweep(
        self,
        attestation: Dict[str, Any],
        snapshot_results: Dict[str, Dict[str, Any]],
        *,
        commit: bool = False,
    ) -> MarketSweep:
        """Validate and persist derived sweep membership atomically."""
        if not attestation or attestation.get("cursor_complete") is not True:
            raise ValueError("only a completed Gamma sweep may be persisted")
        if int(attestation.get("schema_version", 0)) != 1:
            raise ValueError("unsupported Gamma sweep schema")
        if int(attestation.get("pages", 0)) < 1:
            raise ValueError("Gamma sweep pages must be positive")
        memberships = attestation.get("memberships")
        if not isinstance(memberships, list):
            raise ValueError("Gamma sweep memberships must be a list")
        if attestation.get("membership_digest_scope") != "qualified_only":
            raise ValueError("Gamma digest scope must be qualified_only")

        canonical: List[Dict[str, Any]] = []
        for item in memberships:
            if not isinstance(item, dict):
                raise ValueError("Gamma membership must be an object")
            condition_id = str(item.get("condition_id") or "")
            raw_seen = item.get("raw_seen_count")
            qualified = item.get("qualified")
            reason = item.get("qualification_reason")
            if (
                not condition_id
                or isinstance(raw_seen, bool)
                or not isinstance(raw_seen, int)
                or raw_seen < 1
            ):
                raise ValueError("invalid Gamma membership identity/count")
            if (
                not isinstance(qualified, bool)
                or not isinstance(reason, str)
                or not reason
            ):
                raise ValueError("invalid Gamma membership qualification")
            canonical.append(
                {
                    "condition_id": condition_id,
                    "raw_seen_count": raw_seen,
                    "qualified": qualified,
                    "qualification_reason": reason,
                }
            )
        canonical.sort(key=lambda item: item["condition_id"])
        ids = [item["condition_id"] for item in canonical]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate Gamma membership condition_id")
        qualified_rows = [item for item in canonical if item["qualified"]]
        digest = hashlib.sha256(
            json.dumps(
                qualified_rows,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        if digest != attestation.get("membership_digest_sha256"):
            raise ValueError("Gamma membership digest mismatch")
        qualified_ids = {item["condition_id"] for item in qualified_rows}
        if set(snapshot_results) != qualified_ids:
            raise ValueError("every qualified condition requires an archive decision")

        exclusion_counts: Dict[str, int] = {}
        for item in canonical:
            if not item["qualified"]:
                reason = item["qualification_reason"]
                exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
        if attestation.get("exclusion_counts") != dict(
            sorted(exclusion_counts.items())
        ):
            raise ValueError("Gamma exclusion counts mismatch")
        missing = int(attestation.get("missing_condition_id_count", 0))
        raw_count = sum(item["raw_seen_count"] for item in canonical) + missing
        expected = {
            "raw_market_count": raw_count,
            "unique_condition_count": len(canonical),
            "qualified_market_count": len(qualified_rows),
            "excluded_condition_count": len(canonical) - len(qualified_rows),
            "duplicate_raw_count": raw_count - missing - len(canonical),
        }
        for field, value in expected.items():
            if int(attestation.get(field, -1)) != value:
                raise ValueError(f"Gamma {field} mismatch")

        enriched = []
        for membership in qualified_rows:
            result = snapshot_results[membership["condition_id"]]
            eligible = result.get("snapshot_eligible") is True
            snapshotted = result.get("snapshotted") is True
            reason = str(result.get("snapshot_reason") or "")
            if not reason or (snapshotted and not eligible):
                raise ValueError("invalid derived snapshot evidence")
            enriched.append((membership, eligible, snapshotted, reason))

        started = self._attestation_datetime(attestation["started_at"])
        completed = self._attestation_datetime(attestation["completed_at"])
        if completed < started:
            raise ValueError("Gamma sweep completion precedes start")
        min_liquidity = float(attestation.get("min_liquidity", 0))
        min_volume = float(attestation.get("min_volume", 0))
        if any(
            not math.isfinite(value) or value < 0
            for value in (min_liquidity, min_volume)
        ):
            raise ValueError("Gamma sweep filters must be finite/non-negative")
        sweep_id = str(attestation.get("sweep_id") or "")
        if not sweep_id:
            raise ValueError("Gamma sweep_id is required")
        store_membership_details = membership_details_due(
            self.session,
            "golden-queen",
        )

        sweep = MarketSweep(
            sweep_id=sweep_id,
            schema_version=1,
            run_id=current_run_id(),
            started_at=started,
            completed_at=completed,
            cursor_complete=1,
            pages=int(attestation["pages"]),
            raw_market_count=raw_count,
            unique_condition_count=len(canonical),
            qualified_market_count=len(qualified_rows),
            excluded_condition_count=len(canonical) - len(qualified_rows),
            exclusion_counts_json=json.dumps(exclusion_counts, sort_keys=True),
            missing_condition_id_count=missing,
            duplicate_raw_count=expected["duplicate_raw_count"],
            min_liquidity=min_liquidity,
            min_volume=min_volume,
            membership_digest_sha256=digest,
            snapshot_eligible_count=sum(int(row[1]) for row in enriched),
            snapshotted_market_count=sum(int(row[2]) for row in enriched),
            membership_detail_stored=int(store_membership_details),
        )
        self.session.add(sweep)
        if store_membership_details:
            for membership, eligible, snapshotted, reason in enriched:
                self.session.add(
                    MarketSweepMembership(
                        sweep_id=sweep_id,
                        condition_id=membership["condition_id"],
                        raw_seen_count=membership["raw_seen_count"],
                        qualified=1,
                        qualification_reason=membership["qualification_reason"],
                        snapshot_eligible=int(eligible),
                        snapshotted=int(snapshotted),
                        snapshot_reason=reason,
                    )
                )
        if commit:
            self.session.commit()
        return sweep

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def cleanup_old_snapshots(self, days: int = 60) -> int:
        if compact_maintenance_active(self.session, "golden-queen"):
            return 0
        cutoff = datetime.utcnow() - timedelta(days=days)
        # Entry crossing evidence is immutable, even after the telemetry
        # retention horizon.  Build the protected set before deleting so a
        # legacy trade's inferred immediate-prior row cannot shift while the
        # DELETE statement is running.
        self.session.execute(
            text(
                "CREATE TEMP TABLE IF NOT EXISTS "
                "_polybot_queen_protected_snapshots "
                "(id INTEGER PRIMARY KEY) WITHOUT ROWID"
            )
        )
        self.session.execute(text("DELETE FROM _polybot_queen_protected_snapshots"))
        self.session.execute(
            text(
                "INSERT OR IGNORE INTO _polybot_queen_protected_snapshots(id) "
                "SELECT entry_snapshot_id FROM trades "
                "WHERE entry_snapshot_id IS NOT NULL"
            )
        )
        self.session.execute(
            text(
                "INSERT OR IGNORE INTO _polybot_queen_protected_snapshots(id) "
                "SELECT prior_snapshot_id_at_entry FROM trades "
                "WHERE prior_snapshot_id_at_entry IS NOT NULL"
            )
        )
        self.session.execute(
            text(
                "INSERT OR IGNORE INTO _polybot_queen_protected_snapshots(id) "
                "SELECT prior_id FROM ("
                "SELECT (SELECT prior.id FROM market_snapshots AS prior "
                "WHERE prior.condition_id = entry.condition_id AND ("
                "prior.timestamp < entry.timestamp OR "
                "(prior.timestamp = entry.timestamp AND prior.id < entry.id)) "
                "ORDER BY prior.timestamp DESC, prior.id DESC LIMIT 1) AS prior_id "
                "FROM trades AS trade JOIN market_snapshots AS entry "
                "ON entry.id = trade.entry_snapshot_id "
                "WHERE trade.entry_snapshot_id IS NOT NULL "
                "AND trade.prior_snapshot_id_at_entry IS NULL"
                ") inferred WHERE prior_id IS NOT NULL"
            )
        )
        deleted = self.session.execute(
            text(
                "DELETE FROM market_snapshots WHERE timestamp < :cutoff "
                "AND id NOT IN ("
                "SELECT id FROM _polybot_queen_protected_snapshots)"
            ),
            {"cutoff": cutoff},
        ).rowcount
        self.session.execute(text("DROP TABLE _polybot_queen_protected_snapshots"))
        expired_sweeps = [
            row[0]
            for row in self.session.query(MarketSweep.sweep_id)
            .filter(MarketSweep.completed_at < cutoff)
            .all()
        ]
        if expired_sweeps:
            self.session.query(MarketSweepMembership).filter(
                MarketSweepMembership.sweep_id.in_(expired_sweeps)
            ).delete(synchronize_session=False)
            self.session.query(MarketSweep).filter(
                MarketSweep.sweep_id.in_(expired_sweeps)
            ).delete(synchronize_session=False)
        self.session.commit()
        return max(0, int(deleted or 0))

    def get_stats(self) -> Dict[str, Any]:
        def count(status: TradeStatus) -> int:
            return (
                self.session.query(func.count(Trade.id))
                .filter(Trade.status == status)
                .scalar()
                or 0
            )

        total_pnl = (
            self.session.query(func.sum(Trade.realized_pnl))
            .filter(Trade.realized_pnl.isnot(None))
            .scalar()
            or 0.0
        )
        return {
            "total_trades": self.session.query(func.count(Trade.id)).scalar() or 0,
            "holding": count(TradeStatus.HOLDING),
            "pending_buy": count(TradeStatus.PENDING_BUY),
            "pending_sell": count(TradeStatus.PENDING_SELL),
            "completed": count(TradeStatus.COMPLETED),
            "resolved": count(TradeStatus.RESOLVED),
            "unfilled": count(TradeStatus.UNFILLED),
            "quarantined": count(TradeStatus.QUARANTINED),
            "skipped": self.session.query(func.count(SkippedMarket.id)).scalar() or 0,
            "total_pnl": round(total_pnl, 4),
        }

    def append_trade_to_csv(self, trade: Trade, db_dir) -> None:
        """Append an actual confirmed stop sale; settlement rows remain in DB."""
        timestamp = trade.sell_timestamp or datetime.utcnow()
        path = Path(db_dir) / f"trades_{timestamp:%Y-%m}.csv"
        headers = [
            "id",
            "strategy_name",
            "mode",
            "condition_id",
            "event_id",
            "question",
            "outcome",
            "buy_price",
            "sell_price",
            "realized_pnl",
            "hypothetical_pnl",
            "pnl_basis",
            "buy_confirmed_size",
            "buy_confirmed_vwap",
            "buy_confirmed_fee_usdc",
            "sell_confirmed_size",
            "sell_confirmed_vwap",
            "sell_confirmed_fee_usdc",
            "sell_fill_matched_at",
            "buy_timestamp",
            "sell_timestamp",
            "entry_reason",
            "exit_reason",
            "prior_yes_price_at_entry",
            "yes_price_at_buy",
            "yes_price_at_exit",
            "prior_snapshot_id_at_entry",
            "entry_snapshot_id",
            "stop_price_at_entry",
            "best_bid_at_buy",
            "best_ask_at_buy",
            "spread_at_buy",
            "best_bid_at_exit",
            "best_ask_at_exit",
            "spread_at_exit",
            "hours_until_resolution_at_buy",
        ]
        row = {
            field: (
                getattr(trade, field).isoformat()
                if isinstance(getattr(trade, field, None), datetime)
                else getattr(trade, field, "")
            )
            for field in headers
        }
        exists = path.exists() and path.stat().st_size > 0
        if exists:
            with path.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                existing_headers = list(reader.fieldnames or [])
                existing_rows = list(reader)
            if existing_headers != headers:
                unknown = [field for field in existing_headers if field not in headers]
                if not existing_headers or unknown:
                    raise RuntimeError(
                        "기존 거래 CSV header가 현재 schema와 호환되지 않습니다: "
                        f"unknown={unknown}"
                    )
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{path.name}.", suffix=".upgrade", dir=path.parent
                )
                temporary_path = Path(temporary_name)
                try:
                    with os.fdopen(
                        descriptor, "w", newline="", encoding="utf-8"
                    ) as handle:
                        writer = csv.DictWriter(handle, fieldnames=headers)
                        writer.writeheader()
                        writer.writerows(existing_rows)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary_path, path)
                finally:
                    temporary_path.unlink(missing_ok=True)
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            if not exists:
                writer.writeheader()
            writer.writerow(row)
        logger.info("거래 이력 CSV 저장: %s", path)
