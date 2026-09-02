"""Repository operations for Golden Watermelon Live trades and evidence."""

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
    EntryEpisode,
    MarketCatalog,
    MarketSnapshot,
    MarketSweep,
    MarketSweepMembership,
    ResolutionObservation,
    SkippedMarket,
    STOP_SELL_ISOLATION_REASONS,
    Trade,
    TradeStatus,
)
from ..strategy.filters import get_event_metadata, get_proven_resolution


logger = logging.getLogger(__name__)
_OPEN_STATUSES = (
    TradeStatus.PENDING_BUY,
    TradeStatus.HOLDING,
    TradeStatus.PENDING_SELL,
    # QUARANTINED means economic exposure is unknown, not zero.  It must keep
    # consuming capacity until an operator/evidence-backed transition proves
    # otherwise.
    TradeStatus.QUARANTINED,
)

_UNTRACKED_BUY_RESERVATION_PREDICATE = """
    submission.simulation = 0
    AND UPPER(submission.side) = 'BUY'
    AND NOT EXISTS (
        SELECT 1
        FROM trades AS linked_trade
        WHERE linked_trade.buy_order_id = submission.order_id
    )
    AND NOT (
        submission.outcome_resolution = 'NO_ORDER_CREATED'
        AND submission.order_id IS NULL
        AND submission.outcome_resolved_at IS NOT NULL
        AND NULLIF(TRIM(submission.outcome_resolution_reason), '') IS NOT NULL
    )
    AND NOT (
        submission.order_id IS NULL
        AND submission.success = 0
        AND submission.needs_reconciliation = 0
        AND UPPER(COALESCE(submission.response_status, '')) = 'FAILED'
    )
    AND NOT (
        submission.order_id IS NOT NULL
        AND submission.needs_reconciliation = 0
        AND REPLACE(UPPER(COALESCE(submission.latest_order_status, '')),
                    'ORDER_STATUS_', '') IN (
            'CANCELED', 'CANCELLED', 'CANCELED_MARKET_RESOLVED', 'INVALID'
        )
        AND COALESCE(submission.latest_size_matched, 0) <= 0.000001
        AND NOT EXISTS (
            SELECT 1
            FROM order_fills AS positive_fill
            WHERE positive_fill.submission_id = submission.submission_id
              AND UPPER(COALESCE(positive_fill.status, '')) = 'CONFIRMED'
              AND COALESCE(positive_fill.size, 0) > 0.000001
        )
    )
"""

_TERMINAL_ZERO_FILL_ORDER_STATUSES = {
    "CANCELED",
    "CANCELLED",
    "CANCELED_MARKET_RESOLVED",
    "INVALID",
}
_FILL_SIZE_TOLERANCE = 1e-6
_RETRYABLE_PROVEN_NO_POST_EPISODE_STATES = frozenset(
    {
        "BLOCKED_GUARD",
        "PRE_SUBMISSION_CONTRACT_ERROR",
        "QUEUED_NO_POST",
        "NO_POST_RETRYABLE",
    }
)


def _canonical_json_list(value: Any, *, field_name: str) -> str:
    """Persist Gamma list fields exactly once, regardless of wire representation.

    Gamma currently returns some array-valued fields as JSON strings. Calling
    ``json.dumps`` on those strings produces a JSON scalar whose contents happen
    to be JSON, which breaks every later identity check. Accept the two wire
    representations we have observed and reject every other shape before it can
    enter the evidence catalog.
    """
    if value is None:
        parsed: Any = []
    elif isinstance(value, list):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"{field_name} must contain a JSON list") from error
    else:
        raise ValueError(f"{field_name} must be a list or JSON-list string")
    if not isinstance(parsed, list):
        raise ValueError(f"{field_name} must decode to a list")
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


_TERMINAL_ORDER_STATUSES = _TERMINAL_ZERO_FILL_ORDER_STATUSES | {"MATCHED"}


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

    @property
    def has_reconciled_executed_fill(self) -> bool:
        """Whether every share executed by a terminal order is proven."""
        if self.has_reconciled_full_fill:
            return True
        return (
            self.state == "confirmed"
            and self.order_status in _TERMINAL_ORDER_STATUSES
            and not self.needs_reconciliation
            and self.latest_size_matched is not None
            and self.confirmed_size is not None
            and math.isfinite(self.latest_size_matched)
            and math.isfinite(self.confirmed_size)
            and self.latest_size_matched > 0
            and self.confirmed_size > 0
            and math.isclose(
                self.confirmed_size,
                self.latest_size_matched,
                rel_tol=1e-9,
                abs_tol=1e-6,
            )
        )


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
        *,
        event_id: Optional[str] = None,
        token_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Allow at most one opposite-result entry after a confirmed stop.

        Direct MLB/NHL moneylines put both team tokens in one condition, while
        soccer uses separate result conditions under one event.  A condition-
        only cooldown therefore blocks the former and under-constrains the
        latter.  The stable identity is ``event_id × token_id``:

        * the same token remains in the 720-hour cooldown;
        * an event with a live position remains blocked;
        * one different token may enter after an exact confirmed stop; and
        * after two distinct event-result tokens have traded, the event is
          closed to further reversals.
        """
        now = now or datetime.utcnow()
        if self.has_holding(condition_id):
            return False, "holding"
        normalized_event_id = str(event_id or "").strip()
        normalized_token_id = str(token_id or "").strip()
        if normalized_event_id:
            if not normalized_token_id:
                return False, "result_token_missing"
            if self.get_event_position_count(normalized_event_id) > 0:
                return False, "event_holding"
        cutoff = now - timedelta(hours=cooldown_hours)
        close_identity_filter = (
            Trade.token_id == normalized_token_id
            if normalized_token_id
            else Trade.condition_id == condition_id
        )
        recent_close = (
            self.session.query(Trade.id)
            .filter(
                close_identity_filter,
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

        if normalized_event_id:
            recent_event_closes = (
                self.session.query(Trade)
                .filter(
                    Trade.event_id == normalized_event_id,
                    Trade.status.in_(
                        (TradeStatus.COMPLETED, TradeStatus.RESOLVED)
                    ),
                    or_(
                        Trade.sell_timestamp >= cutoff,
                        Trade.resolution_observed_at >= cutoff,
                    ),
                )
                .order_by(Trade.id.asc())
                .all()
            )
            prior_tokens = {
                str(trade.token_id or "").strip()
                for trade in recent_event_closes
                if str(trade.token_id or "").strip()
            }
            if len(prior_tokens) >= 2:
                return False, "event_reversal_limit"
            if prior_tokens:
                if normalized_token_id in prior_tokens:
                    return False, "close_cooldown"
                prior = recent_event_closes[-1]
                try:
                    confirmed_sell_size = float(prior.sell_confirmed_size)
                except (TypeError, ValueError):
                    confirmed_sell_size = 0.0
                confirmed_stop = (
                    prior.status == TradeStatus.COMPLETED
                    and str(prior.exit_reason or "").startswith(
                        "absolute_stop_confirmed_fill"
                    )
                    and math.isfinite(confirmed_sell_size)
                    and confirmed_sell_size > _FILL_SIZE_TOLERANCE
                )
                if not confirmed_stop:
                    return False, "event_close_not_reversible"
                return True, "opposite_result_after_confirmed_stop"
        return True, "ok"

    def is_in_reentry_cooldown(self, condition_id: str, cooldown_hours: float) -> bool:
        allowed, _ = self.can_reenter(condition_id, cooldown_hours)
        return not allowed

    def create_trade(
        self,
        *,
        entry_episode_id: Optional[int] = None,
        **kwargs: Any,
    ) -> Trade:
        """Create a trade and link its first-observation episode atomically.

        A live POST can succeed immediately before this write.  Committing the
        Trade and EntryEpisode in two transactions would leave a real position
        with an unlinked experimental denominator if the second commit failed.
        """
        try:
            episode = None
            if entry_episode_id is not None:
                episode = self.session.get(EntryEpisode, int(entry_episode_id))
                if episode is None:
                    raise ValueError(
                        f"entry episode not found: {entry_episode_id}"
                    )
                if episode.trade_id is not None:
                    raise ValueError(
                        "entry episode is already linked to another trade"
                    )

            trade = Trade(**kwargs)
            self.session.add(trade)
            self.session.flush()
            if episode is not None:
                episode.trade_id = trade.id
                episode.execution_state = "TRADE_CREATED"
                episode.execution_reason = "exact_order_submission_linked"
                episode.last_attempted_at = datetime.utcnow()
            self.session.commit()
            return trade
        except Exception:
            # A failed flush/episode link/commit must not remain pending in the
            # Session and later be committed by the caller's failure annotation.
            self.session.rollback()
            raise

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

    def stage_clob_resolution_observation(
        self,
        *,
        trade_id: int,
        condition_id: str,
        observed_at: datetime,
        winner_index: int,
        winner_token_id: str,
        winner_outcome: str,
        selected_token_id: str,
        selected_outcome: str,
        selected_payout: float,
        evidence_sha256: str,
        evidence_json: str,
    ) -> ResolutionObservation:
        """Stage one deterministic append-only CLOB settlement observation.

        The caller updates the trade next; ``update_trade`` commits both rows
        atomically on the same SQLAlchemy session.
        """
        trade = self.session.get(Trade, trade_id)
        if trade is None:
            raise ValueError(f"Trade {trade_id} not found")
        if str(trade.condition_id) != str(condition_id):
            raise ValueError("resolution condition does not match the trade")
        if (
            isinstance(winner_index, bool)
            or winner_index not in (0, 1)
            or isinstance(selected_payout, bool)
            or selected_payout not in (0.0, 1.0)
        ):
            raise ValueError("resolution winner/payout is outside the binary domain")
        normalized_hash = str(evidence_sha256 or "").strip().lower()
        if len(normalized_hash) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_hash
        ):
            raise ValueError("resolution evidence SHA-256 is invalid")
        try:
            decoded = json.loads(evidence_json)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("resolution evidence JSON is invalid") from error
        if not isinstance(decoded, dict) or decoded.get("closed") is not True:
            raise ValueError("resolution evidence must prove a closed market")
        if hashlib.sha256(evidence_json.encode()).hexdigest() != normalized_hash:
            raise ValueError("resolution evidence SHA-256 does not match JSON")
        tokens = decoded.get("tokens")
        if not isinstance(tokens, list) or len(tokens) != 2:
            raise ValueError("resolution evidence must contain exactly two tokens")
        if not all(isinstance(token, dict) for token in tokens):
            raise ValueError("resolution evidence tokens must be objects")
        if len({str(token.get("token_id") or "") for token in tokens}) != 2:
            raise ValueError("resolution evidence token IDs must be distinct")
        try:
            prices = [float(token.get("price")) for token in tokens]
        except (TypeError, ValueError) as error:
            raise ValueError("resolution evidence token payouts are invalid") from error
        if any(
            isinstance(token.get("price"), bool)
            or not math.isfinite(price)
            or price not in (0.0, 1.0)
            for token, price in zip(tokens, prices)
        ):
            raise ValueError("resolution evidence token payouts are not exact 0/1")
        winners = [
            index for index, token in enumerate(tokens) if token.get("winner") is True
        ]
        if winners != [winner_index]:
            raise ValueError("resolution evidence must contain one aligned winner")
        winner = tokens[winner_index]
        if (
            str(winner.get("token_id") or "") != str(winner_token_id)
            or str(winner.get("outcome") or "") != str(winner_outcome)
            or prices[winner_index] != 1.0
        ):
            raise ValueError("resolution winner does not match normalized evidence")
        selected = [
            token
            for token in tokens
            if isinstance(token, dict)
            and str(token.get("token_id") or "") == str(selected_token_id)
        ]
        if (
            len(selected) != 1
            or str(selected[0].get("outcome") or "") != str(selected_outcome)
            or prices[tokens.index(selected[0])] != float(selected_payout)
        ):
            raise ValueError("selected payout does not match normalized evidence")
        identity = hashlib.sha256(
            f"clob:{trade_id}:{condition_id}:{normalized_hash}".encode()
        ).hexdigest()
        existing = self.session.get(ResolutionObservation, identity)
        if existing is not None:
            return existing
        observation = ResolutionObservation(
            resolution_id=identity,
            run_id=current_run_id(),
            trade_id=trade_id,
            condition_id=str(condition_id),
            observed_at=observed_at,
            source="CLOB_MARKET",
            winner_index=winner_index,
            winner_token_id=str(winner_token_id),
            winner_outcome=str(winner_outcome),
            selected_token_id=str(selected_token_id),
            selected_outcome=str(selected_outcome),
            selected_payout=float(selected_payout),
            evidence_sha256=normalized_hash,
            evidence_json=evidence_json,
        )
        self.session.add(observation)
        self.session.flush()
        return observation

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

    def get_isolated_stop_sell_trades(self) -> List[Trade]:
        """Return only 3-hour stop failures that remain evidence-monitored.

        These rows are economically open until an exact SELL fill, exact
        zero-fill, or another authoritative lifecycle transition proves the
        outcome.  Keeping them separate from generic quarantine lets unrelated
        events continue without weakening BUY-side fail-closed guards.
        """
        return (
            self.session.query(Trade)
            .filter(
                Trade.status == TradeStatus.QUARANTINED,
                Trade.exit_reason.in_(STOP_SELL_ISOLATION_REASONS),
            )
            .all()
        )

    def get_quarantine_state(self) -> Dict[str, int]:
        total = (
            self.session.query(func.count(Trade.id))
            .filter(Trade.status == TradeStatus.QUARANTINED)
            .scalar()
            or 0
        )
        isolated_stop_sell = (
            self.session.query(func.count(Trade.id))
            .filter(
                Trade.status == TradeStatus.QUARANTINED,
                Trade.exit_reason.in_(STOP_SELL_ISOLATION_REASONS),
            )
            .scalar()
            or 0
        )
        return {
            "total": int(total),
            "isolated_stop_sell": int(isolated_stop_sell),
            "blocking": max(0, int(total) - int(isolated_stop_sell)),
        }

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

    def get_untracked_buy_reservation_count(
        self, *, event_id: Optional[str] = None
    ) -> int:
        """Count unresolved live BUY submissions not represented by an open trade.

        An accepted/uncertain venue request can exist in the execution ledger even
        when process failure prevented ``trades`` from being written.  Ignoring
        those rows would let the bot exceed the nominal max-position exposure.
        Rows already represented by an economically open trade are excluded to
        avoid counting one request twice. A synchronous, unambiguous venue
        rejection (``FAILED``, no order ID, no reconciliation requirement) is
        also terminal no-exposure evidence; unknown POST outcomes remain
        reserved until explicit proof resolves them.
        """
        event_clause = ""
        parameters: Dict[str, Any] = {}
        if event_id is not None:
            normalized_event_id = str(event_id).strip()
            if not normalized_event_id:
                return 0
            event_clause = """
                AND EXISTS (
                    SELECT 1
                    FROM entry_episodes AS episode
                    WHERE episode.token_id = submission.token_id
                      AND episode.event_id = :event_id
                )
            """
            parameters["event_id"] = normalized_event_id
        result = self.session.execute(
            text(
                "SELECT COUNT(*) FROM order_submissions AS submission WHERE "
                + _UNTRACKED_BUY_RESERVATION_PREDICATE
                + event_clause
            ),
            parameters,
        ).scalar()
        return int(result or 0)

    def get_untracked_buy_submissions(self) -> List[Dict[str, Any]]:
        """Return every live orphan/uncertain BUY that can still be exposure.

        A reconciled positive orphan fill remains here even when
        ``needs_reconciliation=0``.  Only an exact linked Trade, explicit
        operator proof of no order, or terminal zero-fill proof releases it.
        """
        rows = self.session.execute(
            text(
                """
                SELECT submission.submission_id, submission.run_id,
                       submission.strategy_name, submission.order_id,
                       submission.token_id, submission.requested_price,
                       submission.requested_size, submission.submitted_at,
                       submission.response_status,
                       submission.making_amount, submission.taking_amount,
                       submission.latest_order_status,
                       submission.latest_size_matched,
                       submission.needs_reconciliation,
                       submission.outcome_resolution
                FROM order_submissions AS submission
                WHERE
                """
                + _UNTRACKED_BUY_RESERVATION_PREDICATE
                + " ORDER BY submission.submitted_at, submission.submission_id"
            )
        ).mappings()
        return [dict(row) for row in rows]

    def get_entry_capacity_state(self) -> Dict[str, int]:
        """Return the conservative max-position reservation denominator."""
        open_positions = self.get_position_count()
        untracked_buy_reservations = self.get_untracked_buy_reservation_count()
        return {
            "open_positions": open_positions,
            "untracked_buy_reservations": untracked_buy_reservations,
            "total_reserved": open_positions + untracked_buy_reservations,
        }

    def get_open_buy_evidence_gap_count(self) -> int:
        """Count owned open positions missing exact BUY fill or fee evidence."""
        result = (
            self.session.query(func.count(Trade.id))
            .filter(
                Trade.status.in_(
                    (
                        TradeStatus.HOLDING,
                        TradeStatus.PENDING_SELL,
                        TradeStatus.QUARANTINED,
                    )
                ),
                or_(
                    Trade.buy_order_id.is_(None),
                    Trade.buy_confirmed_size.is_(None),
                    Trade.buy_confirmed_vwap.is_(None),
                    Trade.buy_confirmed_fee_usdc.is_(None),
                ),
            )
            .scalar()
            or 0
        )
        return int(result)

    def get_event_position_count(self, event_id: Optional[str]) -> int:
        if not event_id:
            return 0
        open_trades = (
            self.session.query(func.count(Trade.id))
            .filter(
                Trade.event_id == event_id,
                Trade.status.in_(_OPEN_STATUSES),
            )
            .scalar()
            or 0
        )
        return int(open_trades) + self.get_untracked_buy_reservation_count(
            event_id=str(event_id)
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
                        "reconciliation_error, reconciliation_proof, simulation "
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
                        "SELECT status, side, size, price, liquidity_role, fee_rate_bps, "
                        "fee_amount_usdc, "
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
                raw_fee_rate = row["fee_rate_bps"]
                fee_rate = None
                if raw_fee_rate is not None:
                    try:
                        fee_rate = float(raw_fee_rate)
                    except (TypeError, ValueError):
                        return ExactFillEvidence(
                            "unavailable",
                            normalized_order_id,
                            order_status=order_status,
                            detail="confirmed_fill_fee_rate_invalid",
                        )
                    if not math.isfinite(fee_rate) or fee_rate < 0:
                        return ExactFillEvidence(
                            "unavailable",
                            normalized_order_id,
                            order_status=order_status,
                            detail="confirmed_fill_fee_rate_invalid",
                        )
                raw_fee = row["fee_amount_usdc"]
                if raw_fee is None:
                    liquidity_role = str(row["liquidity_role"] or "").strip().upper()
                    # CLOB V2 keeps a legacy fee_rate_bps=0 placeholder even
                    # when a dynamic taker fee is charged at match time.  Only
                    # an explicit fee amount, or a maker role under the current
                    # maker-free contract, proves the fee.  Never promote a
                    # taker fill from the legacy zero-rate field alone.
                    known_zero_fee = liquidity_role == "MAKER"
                    if not known_zero_fee:
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
            authenticated_full_fill = (
                str(submission["reconciliation_proof"] or "").strip()
                == "AUTHENTICATED_TOKEN_TRADE_CATALOG_FULL_FILL"
            )
            reconciled_executed_fill = not needs_reconciliation and (
                authenticated_full_fill
                or (
                    matched_size is not None
                    and math.isfinite(matched_size)
                    and matched_size > 0
                    and math.isclose(
                        size_total, matched_size, rel_tol=1e-9, abs_tol=1e-6
                    )
                    and order_status in _TERMINAL_ORDER_STATUSES
                )
            )
            reconciled_full_fill = reconciled_executed_fill and (
                # MATCHED is the ledger's terminal full-order state.  Its
                # matched size is venue-quantized and can legitimately be
                # a few thousandths below the pre-quantization intent.
                authenticated_full_fill
                or order_status == "MATCHED"
                or math.isclose(
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
                    else (
                        "confirmed_reconciled_terminal_partial_fill"
                        if reconciled_executed_fill
                        else "confirmed_partial_or_unreconciled"
                    )
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
        token_id: str = "legacy-unknown",
        outcome: str = "Unknown",
        liquidity: Optional[float] = None,
        volume_24h: Optional[float] = None,
        best_bid: Optional[float] = None,
        best_ask: Optional[float] = None,
        spread: Optional[float] = None,
        source_updated_at: Optional[str] = None,
        sport_family: Optional[str] = None,
        league_code: Optional[str] = None,
        league_name: Optional[str] = None,
        market_tags_json: Optional[str] = None,
        market: Optional[Dict[str, Any]] = None,
        commit: bool = True,
    ) -> MarketSnapshot:
        if market is not None:
            self._upsert_market_catalog(condition_id, market)
        snapshot = MarketSnapshot(
            condition_id=condition_id,
            token_id=token_id,
            outcome=outcome,
            probability=probability,
            liquidity=liquidity,
            volume_24h=volume_24h,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            source_updated_at=source_updated_at,
            run_id=current_run_id(),
            sport_family=str(sport_family or "") or None,
            league_code=str(league_code or "") or None,
            league_name=str(league_name or "") or None,
            market_tags_json=market_tags_json,
        )
        self.session.add(snapshot)
        self.session.flush()
        if commit:
            self.session.commit()
        return snapshot

    def claim_entry_episode(
        self,
        *,
        token_id: str,
        condition_id: str,
        event_id: Optional[str],
        outcome: str,
        entry_snapshot_id: int,
        exact_vwap: float,
        arm_prob_min: float,
        arm_prob_max: float,
        observed_at: datetime,
        game_start_time: Optional[datetime] = None,
        in_play_hours: Optional[float] = None,
    ) -> Optional[EntryEpisode]:
        """Persist the first in-arm observation or reclaim a proven no-POST miss.

        A global entry guard and a pre-submission contract error both happen
        before an order can reach the venue. Those two states may be retried on
        a later fresh in-band snapshot. Every state that could follow a POST is
        deliberately terminal here to prevent duplicate live orders.
        """
        existing = (
            self.session.query(EntryEpisode)
            .filter(EntryEpisode.token_id == token_id)
            .first()
        )
        if existing is not None:
            if (
                existing.trade_id is None
                and str(existing.execution_state or "").upper()
                in _RETRYABLE_PROVEN_NO_POST_EPISODE_STATES
            ):
                existing.condition_id = condition_id
                existing.event_id = event_id
                existing.outcome = outcome
                existing.entry_snapshot_id = entry_snapshot_id
                existing.exact_vwap = exact_vwap
                existing.arm_prob_min = arm_prob_min
                existing.arm_prob_max = arm_prob_max
                existing.game_start_time = game_start_time
                existing.in_play_hours = in_play_hours
                existing.execution_state = "RETRY_OBSERVED"
                existing.execution_reason = "fresh_in_band_retry_after_proven_no_post"
                self.session.flush()
                return existing
            return None
        episode = EntryEpisode(
            token_id=token_id,
            condition_id=condition_id,
            event_id=event_id,
            outcome=outcome,
            entry_snapshot_id=entry_snapshot_id,
            exact_vwap=exact_vwap,
            arm_prob_min=arm_prob_min,
            arm_prob_max=arm_prob_max,
            observed_at=observed_at,
            game_start_time=game_start_time,
            in_play_hours=in_play_hours,
            execution_state="OBSERVED",
        )
        self.session.add(episode)
        self.session.flush()
        return episode

    def get_entry_episode_by_token(self, token_id: str) -> Optional[EntryEpisode]:
        return (
            self.session.query(EntryEpisode)
            .filter(EntryEpisode.token_id == str(token_id))
            .first()
        )

    def get_snapshot_by_id(self, snapshot_id: int) -> Optional[MarketSnapshot]:
        return self.session.get(MarketSnapshot, int(snapshot_id))

    def get_market_catalog_by_condition_id(
        self, condition_id: str
    ) -> Optional[MarketCatalog]:
        return self.session.get(MarketCatalog, str(condition_id))

    def mark_entry_episode_execution(
        self,
        episode_id: int,
        *,
        state: str,
        reason: Optional[str] = None,
    ) -> None:
        episode = self.session.get(EntryEpisode, int(episode_id))
        if episode is None:
            raise ValueError(f"entry episode not found: {episode_id}")
        normalized_state = str(state or "").strip().upper()
        if not normalized_state:
            raise ValueError("entry episode execution state is empty")
        episode.execution_state = normalized_state
        episode.execution_reason = str(reason or "").strip() or None
        episode.last_attempted_at = datetime.utcnow()
        self.session.commit()

    def mark_entry_episodes_queued_no_post(
        self,
        episode_ids: List[int],
        *,
        reason: str,
    ) -> None:
        """Durably queue fresh candidates before any one of them can POST.

        A cycle may terminate while processing an earlier candidate. Persisting
        this explicit no-POST state first makes every later candidate safely
        reclaimable on its next fresh in-band snapshot. The state is accepted
        only directly from this run's OBSERVED/RETRY_OBSERVED claims.
        """
        normalized = {
            episode_id
            for episode_id in episode_ids
            if not isinstance(episode_id, bool)
            and isinstance(episode_id, int)
            and episode_id > 0
        }
        normalized_ids = sorted(normalized)
        if not normalized_ids:
            return
        episodes = (
            self.session.query(EntryEpisode)
            .filter(EntryEpisode.id.in_(normalized_ids))
            .all()
        )
        if len(episodes) != len(normalized_ids):
            raise ValueError("one or more queued entry episodes are missing")
        for episode in episodes:
            current = str(episode.execution_state or "").upper()
            if episode.trade_id is not None or current not in {
                "OBSERVED",
                "RETRY_OBSERVED",
            }:
                raise ValueError(
                    "entry episode cannot enter a proven no-POST queue: "
                    f"id={episode.id} state={current} trade={episode.trade_id}"
                )
        queue_reason = str(reason or "").strip()
        for episode in episodes:
            episode.execution_state = "QUEUED_NO_POST"
            episode.execution_reason = queue_reason or None
            # Queueing is not an execution attempt. Preserve last_attempted_at
            # until this specific candidate is actually selected for execution.
        self.session.commit()

    def link_entry_episode_trade(self, episode_id: int, trade_id: int) -> None:
        episode = self.session.get(EntryEpisode, episode_id)
        if episode is None:
            raise ValueError(f"entry episode not found: {episode_id}")
        if episode.trade_id not in (None, trade_id):
            raise ValueError("entry episode is already linked to another trade")
        episode.trade_id = trade_id
        episode.execution_state = "TRADE_CREATED"
        episode.execution_reason = "exact_order_submission_linked"
        episode.last_attempted_at = datetime.utcnow()
        self.session.commit()

    def create_recovered_orphan_trade(
        self, episode_id: int, **trade_values: Any
    ) -> Trade:
        """Atomically reconstruct a ledger-proven orphan BUY and link its episode."""
        try:
            episode = self.session.get(EntryEpisode, int(episode_id))
            if episode is None:
                raise ValueError(f"entry episode not found: {episode_id}")
            if episode.trade_id is not None:
                raise ValueError("entry episode already has a linked trade")
            order_id = str(trade_values.get("buy_order_id") or "").strip()
            if not order_id:
                raise ValueError(
                    "recovered orphan trade requires an exact order ID"
                )
            existing = (
                self.session.query(Trade)
                .filter(Trade.buy_order_id == order_id)
                .first()
            )
            if existing is not None:
                raise ValueError("orphan order is already represented by a trade")
            trade = Trade(**trade_values)
            self.session.add(trade)
            self.session.flush()
            episode.trade_id = trade.id
            episode.execution_state = "ORPHAN_RECOVERED"
            episode.execution_reason = (
                "reconciled_positive_buy_fill_reconstructed"
            )
            episode.last_attempted_at = datetime.utcnow()
            self.session.commit()
            return trade
        except Exception:
            self.session.rollback()
            raise

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
        sport_family: Optional[str] = None,
        league_code: Optional[str] = None,
        league_name: Optional[str] = None,
        commit: bool = False,
    ) -> None:
        self._upsert_market_catalog(
            condition_id,
            market,
            sport_family=sport_family,
            league_code=league_code,
            league_name=league_name,
        )
        if commit:
            self.session.commit()

    def _upsert_market_catalog(
        self,
        condition_id: str,
        market: Dict[str, Any],
        *,
        sport_family: Optional[str] = None,
        league_code: Optional[str] = None,
        league_name: Optional[str] = None,
    ) -> None:
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
            "outcomes_json": _canonical_json_list(
                market.get("outcomes"), field_name="outcomes"
            ),
            "outcome_prices_json": _canonical_json_list(
                market.get("outcomePrices"), field_name="outcomePrices"
            ),
            "token_ids_json": _canonical_json_list(
                market.get("clobTokenIds"), field_name="clobTokenIds"
            ),
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
            "sport_family": str(sport_family or "") or None,
            "league_code": str(league_code or market.get("leagueCode") or "")
            or None,
            "league_name": str(league_name or market.get("leagueName") or "")
            or None,
            "neg_risk": bool_int(market.get("negRisk")),
            "active": bool_int(market.get("active")),
            "closed": bool_int(market.get("closed")),
            "accepting_orders": bool_int(market.get("acceptingOrders")),
            "enable_order_book": bool_int(market.get("enableOrderBook")),
            "fees_enabled": bool_int(market.get("feesEnabled")),
            "fee_rate": fee_schedule.get("rate")
            if isinstance(fee_schedule, dict)
            else None,
            "fee_exponent": fee_schedule.get("exponent")
            if isinstance(fee_schedule, dict)
            else None,
            "fee_taker_only": bool_int(fee_schedule.get("takerOnly"))
            if isinstance(fee_schedule, dict)
            else None,
            "resolution_status": (
                resolution["status"]
                if resolution
                else market.get("umaResolutionStatus")
            ),
            "resolved_outcome": resolution["outcome"] if resolution else None,
            # Legacy catalog column name stores the first listed outcome payout.
            "resolved_value": (
                resolution["first_outcome_payout"] if resolution else None
            ),
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
        schema_version = int(attestation.get("schema_version", 0))
        if schema_version != 2:
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
        for membership in canonical:
            if membership["qualified"]:
                result = snapshot_results[membership["condition_id"]]
                eligible = result.get("snapshot_eligible") is True
                snapshotted = result.get("snapshotted") is True
                reason = str(result.get("snapshot_reason") or "")
                if not reason or (snapshotted and not eligible):
                    raise ValueError("invalid derived snapshot evidence")
            else:
                eligible = False
                snapshotted = False
                reason = f"not_qualified:{membership['qualification_reason']}"
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
            "golden-watermelon-live",
        )
        if not store_membership_details:
            latest_detail = (
                self.session.query(MarketSweep)
                .filter(MarketSweep.membership_detail_stored == 1)
                .order_by(MarketSweep.completed_at.desc())
                .first()
            )
            if latest_detail is not None:
                stored_rows = (
                    self.session.query(func.count(MarketSweepMembership.condition_id))
                    .filter(MarketSweepMembership.sweep_id == latest_detail.sweep_id)
                    .scalar()
                    or 0
                )
                if int(stored_rows) != int(latest_detail.unique_condition_count):
                    logger.warning(
                        "incomplete membership checkpoint를 즉시 복구합니다 - "
                        "sweep=%s expected=%s stored=%s",
                        latest_detail.sweep_id,
                        latest_detail.unique_condition_count,
                        stored_rows,
                    )
                    store_membership_details = True

        sweep = MarketSweep(
            sweep_id=sweep_id,
            schema_version=schema_version,
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
                        qualified=int(membership["qualified"]),
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
        if compact_maintenance_active(self.session, "golden-watermelon-live"):
            return 0
        cutoff = datetime.utcnow() - timedelta(days=days)
        # Entry crossing evidence is immutable, even after the telemetry
        # retention horizon.  Build the protected set before deleting so a
        # legacy trade's inferred immediate-prior row cannot shift while the
        # DELETE statement is running.
        self.session.execute(
            text(
                "CREATE TEMP TABLE IF NOT EXISTS "
                "_polybot_papaya_protected_snapshots "
                "(id INTEGER PRIMARY KEY) WITHOUT ROWID"
            )
        )
        self.session.execute(text("DELETE FROM _polybot_papaya_protected_snapshots"))
        self.session.execute(
            text(
                "INSERT OR IGNORE INTO _polybot_papaya_protected_snapshots(id) "
                "SELECT entry_snapshot_id FROM trades "
                "WHERE entry_snapshot_id IS NOT NULL"
            )
        )
        self.session.execute(
            text(
                "INSERT OR IGNORE INTO _polybot_papaya_protected_snapshots(id) "
                "SELECT prior_snapshot_id_at_entry FROM trades "
                "WHERE prior_snapshot_id_at_entry IS NOT NULL"
            )
        )
        self.session.execute(
            text(
                "INSERT OR IGNORE INTO _polybot_papaya_protected_snapshots(id) "
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
                "SELECT id FROM _polybot_papaya_protected_snapshots)"
            ),
            {"cutoff": cutoff},
        ).rowcount
        self.session.execute(text("DROP TABLE _polybot_papaya_protected_snapshots"))
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
        settlement_pnl_assumption = (
            self.session.query(func.sum(Trade.settlement_pnl_assumption))
            .filter(Trade.settlement_pnl_assumption.isnot(None))
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
            "isolated_stop_sell": (
                self.session.query(func.count(Trade.id))
                .filter(
                    Trade.status == TradeStatus.QUARANTINED,
                    Trade.exit_reason.in_(STOP_SELL_ISOLATION_REASONS),
                )
                .scalar()
                or 0
            ),
            "skipped": self.session.query(func.count(SkippedMarket.id)).scalar() or 0,
            "total_pnl": round(total_pnl, 4),
            "settlement_pnl_assumption": round(
                settlement_pnl_assumption, 4
            ),
        }

    def get_economic_pnl_guard(self) -> Dict[str, Any]:
        """Return a conservative safety P&L with ledger SELL overrides.

        A legacy accepted SELL can have an exact CONFIRMED fill in the shared
        execution ledger while its Trade row still has no ``sell_order_id``.
        If that Trade is later marked RESOLVED, summing ``trades`` alone counts
        a synthetic payout and hides the real sale.  Do not rewrite history;
        replace that settlement assumption in this safety calculation only.
        """
        recorded = self.session.execute(
            text(
                "SELECT COALESCE(SUM(realized_pnl), 0) AS realized_pnl, "
                "COALESCE(SUM(settlement_pnl_assumption), 0) AS settlement_pnl "
                "FROM trades"
            )
        ).mappings().one()
        realized = float(recorded["realized_pnl"] or 0.0)
        settlement = float(recorded["settlement_pnl"] or 0.0)
        if not math.isfinite(realized) or not math.isfinite(settlement):
            logger.error("economic P&L guard found non-finite Trade aggregates")
            return {
                "economic_pnl": 0.0,
                "recorded_realized_pnl": 0.0,
                "recorded_settlement_pnl": 0.0,
                "confirmed_sell_pnl": 0.0,
                "proven_resolution_pnl": 0.0,
                "execution_adjustment_pnl": 0.0,
                "invalidated_settlement_pnl": 0.0,
                "execution_override_count": 0,
                "evidence_gaps": 1,
            }
        execution_adjustment = 0.0
        invalidated_settlement = 0.0
        evidence_gaps = 0
        override_count = 0
        try:
            matched_without_fill = int(
                self.session.execute(
                    text(
                        "SELECT COUNT(*) FROM order_submissions AS submission "
                        "WHERE submission.simulation = 0 "
                        "AND UPPER(submission.side) = 'SELL' "
                        "AND (COALESCE(submission.latest_size_matched, 0) > "
                        ":tolerance OR REPLACE(UPPER(COALESCE("
                        "submission.latest_order_status, '')), "
                        "'ORDER_STATUS_', '') = 'MATCHED') "
                        "AND NOT EXISTS (SELECT 1 FROM order_fills AS fill "
                        "WHERE fill.submission_id = submission.submission_id "
                        "AND UPPER(fill.status) = 'CONFIRMED')"
                    ),
                    {"tolerance": _FILL_SIZE_TOLERANCE},
                ).scalar()
                or 0
            )
            rows = self.session.execute(
                text(
                    "SELECT submission.order_id AS order_id, "
                    "submission.token_id AS token_id, "
                    "SUM(fill.size) AS confirmed_size, "
                    "SUM(fill.size * fill.price) AS gross_proceeds, "
                    "SUM(COALESCE(fill.fee_amount_usdc, 0)) AS sell_fee, "
                    "SUM(CASE WHEN fill.fee_amount_usdc IS NULL THEN 1 ELSE 0 END) "
                    "AS fee_gaps, "
                    "SUM(CASE WHEN UPPER(COALESCE(fill.side, '')) != 'SELL' "
                    "OR fill.size IS NULL OR fill.size <= 0 "
                    "OR fill.price IS NULL OR fill.price <= 0 OR fill.price > 1 "
                    "OR fill.fee_amount_usdc < 0 THEN 1 ELSE 0 END) AS domain_gaps, "
                    "COUNT(DISTINCT submission.submission_id) AS submissions "
                    "FROM order_submissions AS submission "
                    "JOIN order_fills AS fill "
                    "ON fill.submission_id = submission.submission_id "
                    "WHERE submission.simulation = 0 "
                    "AND UPPER(submission.side) = 'SELL' "
                    "AND UPPER(fill.status) = 'CONFIRMED' "
                    "GROUP BY submission.order_id, submission.token_id"
                )
            ).mappings().all()
        except Exception as error:
            logger.error(
                "economic P&L guard cannot read execution ledger - error=%s",
                type(error).__name__,
            )
            return {
                "economic_pnl": realized + settlement,
                "recorded_realized_pnl": realized,
                "recorded_settlement_pnl": settlement,
                "confirmed_sell_pnl": realized,
                "proven_resolution_pnl": settlement,
                "execution_adjustment_pnl": 0.0,
                "invalidated_settlement_pnl": 0.0,
                "execution_override_count": 0,
                "evidence_gaps": 1,
            }

        evidence_gaps += matched_without_fill

        matched_by_trade: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            order_id = str(row.get("order_id") or "").strip()
            token_id = str(row.get("token_id") or "").strip()
            try:
                confirmed_size = float(row.get("confirmed_size"))
                gross_proceeds = float(row.get("gross_proceeds"))
                sell_fee = float(row.get("sell_fee"))
                fee_gaps = int(row.get("fee_gaps") or 0)
                domain_gaps = int(row.get("domain_gaps") or 0)
                submissions = int(row.get("submissions") or 0)
            except (TypeError, ValueError):
                evidence_gaps += 1
                continue
            if (
                not order_id
                or not token_id
                or fee_gaps
                or domain_gaps
                or submissions != 1
                or not math.isfinite(confirmed_size)
                or confirmed_size <= 0
                or not math.isfinite(gross_proceeds)
                or gross_proceeds <= 0
                or gross_proceeds > confirmed_size + _FILL_SIZE_TOLERANCE
                or not math.isfinite(sell_fee)
                or sell_fee < 0
            ):
                evidence_gaps += 1
                continue

            order_candidates = (
                self.session.query(Trade)
                .filter(Trade.sell_order_id == order_id)
                .all()
            )
            if order_candidates:
                if (
                    len(order_candidates) != 1
                    or str(order_candidates[0].token_id) != token_id
                ):
                    evidence_gaps += 1
                    continue
                candidates = order_candidates
            else:
                candidates = (
                    self.session.query(Trade)
                    .filter(
                        Trade.token_id == token_id,
                        Trade.mode == "live",
                        Trade.buy_confirmed_size.isnot(None),
                        Trade.buy_confirmed_vwap.isnot(None),
                        Trade.buy_confirmed_fee_usdc.isnot(None),
                    )
                    .all()
                )
            if len(candidates) != 1:
                # An ambiguous confirmed SELL is real exposure but cannot be
                # assigned safely.  Block future entry instead of guessing.
                evidence_gaps += 1
                continue
            trade = candidates[0]
            bucket = matched_by_trade.setdefault(
                int(trade.id),
                {
                    "trade": trade,
                    "confirmed_size": 0.0,
                    "gross_proceeds": 0.0,
                    "sell_fee": 0.0,
                    "order_ids": set(),
                },
            )
            if order_id in bucket["order_ids"]:
                evidence_gaps += 1
                continue
            bucket["order_ids"].add(order_id)
            bucket["confirmed_size"] += confirmed_size
            bucket["gross_proceeds"] += gross_proceeds
            bucket["sell_fee"] += sell_fee

        for bucket in matched_by_trade.values():
            trade = bucket["trade"]
            try:
                buy_size = float(trade.buy_confirmed_size)
                buy_vwap = float(trade.buy_confirmed_vwap)
                buy_fee = float(trade.buy_confirmed_fee_usdc)
                confirmed_size = float(bucket["confirmed_size"])
                gross_proceeds = float(bucket["gross_proceeds"])
                sell_fee = float(bucket["sell_fee"])
            except (TypeError, ValueError):
                evidence_gaps += 1
                continue
            if (
                not math.isfinite(buy_size)
                or buy_size <= 0
                or confirmed_size > buy_size + _FILL_SIZE_TOLERANCE
                or not math.isfinite(buy_vwap)
                or not 0 < buy_vwap < 1
                or not math.isfinite(buy_fee)
                or buy_fee < 0
            ):
                evidence_gaps += 1
                continue
            allocated_buy_fee = buy_fee * confirmed_size / buy_size
            ledger_pnl = (
                gross_proceeds
                - sell_fee
                - buy_vwap * confirmed_size
                - allocated_buy_fee
            )
            recorded_pnl = float(trade.realized_pnl or 0.0)
            execution_adjustment += ledger_pnl - recorded_pnl
            if trade.settlement_pnl_assumption is not None:
                # Resolution P&L is linear in confirmed shares, including the
                # allocated BUY fee.  Invalidate only the shares proven sold;
                # any sub-cent residual keeps its proven payout economics.
                invalidated_settlement += (
                    float(trade.settlement_pnl_assumption)
                    * confirmed_size
                    / buy_size
                )
            if (
                trade.realized_pnl is None
                or trade.settlement_pnl_assumption is not None
                or not math.isclose(
                    ledger_pnl, recorded_pnl, rel_tol=0, abs_tol=1e-6
                )
            ):
                override_count += 1

        confirmed_sell_pnl = realized + execution_adjustment
        proven_resolution_pnl = settlement - invalidated_settlement
        economic = confirmed_sell_pnl + proven_resolution_pnl
        return {
            "economic_pnl": economic,
            "recorded_realized_pnl": realized,
            "recorded_settlement_pnl": settlement,
            "confirmed_sell_pnl": confirmed_sell_pnl,
            "proven_resolution_pnl": proven_resolution_pnl,
            "execution_adjustment_pnl": execution_adjustment,
            "invalidated_settlement_pnl": invalidated_settlement,
            "execution_override_count": override_count,
            "evidence_gaps": evidence_gaps,
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
