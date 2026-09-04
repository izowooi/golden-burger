"""Repository operations for Golden Plum trades and evidence."""

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
    BUY_ISOLATION_REASONS,
    EntryEpisode,
    EventCycleEvidence,
    ExitExecutionObservation,
    MarketCatalog,
    MarketSnapshot,
    MarketSweep,
    MarketSweepMembership,
    ResolutionObservation,
    SkippedMarket,
    STOP_SELL_ISOLATION_REASONS,
    TrackedResolutionObservation,
    Trade,
    TradeStatus,
)
from ..strategy.filters import (
    get_aligned_binary_outcomes,
    get_event_metadata,
    get_proven_resolution,
)


logger = logging.getLogger(__name__)
_VOID_WINNER_INDEX = -1
_VOID_WINNER_TOKEN_ID = "__VOID__"
_VOID_WINNER_OUTCOME = "VOID"
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

_EVIDENCE_CONTEXT_FIELDS = (
    "sport_family",
    "sport_profile_version",
    "protocol_sha256",
    "classifier_version",
    "league_mapping_sha256",
    "strategy_source_digest",
    "book_shape",
)


def _normalize_evidence_context(value: Optional[Dict[str, Any]]) -> Dict[str, str]:
    context = dict(value or {})
    normalized = {
        field: str(context.get(field) or "").strip()
        for field in _EVIDENCE_CONTEXT_FIELDS
    }
    expected_kinds = context.get("expected_result_kinds") or ()
    normalized["expected_result_kinds_json"] = json.dumps(
        sorted({str(item).strip().upper() for item in expected_kinds if str(item).strip()}),
        separators=(",", ":"),
    )
    for field in ("expected_market_count", "expected_token_count"):
        raw = context.get(field, 0)
        if isinstance(raw, bool):
            raise ValueError(f"{field} must be an integer")
        normalized[field] = str(int(raw))
    return normalized


def _canonical_payload(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
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

    def _current_config_hash(self) -> Optional[str]:
        run_id = current_run_id()
        if not run_id:
            return None
        value = self.session.execute(
            text("SELECT config_hash FROM run_audits WHERE run_id = :run_id"),
            {"run_id": run_id},
        ).scalar()
        normalized = str(value or "").strip()
        return normalized or None

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
        """Permit no second filled/uncertain entry for the same event.

        Proven zero-fill rows are deliberately excluded so an opportunity can
        be retried.  Every state that can represent exposure or a completed
        trade permanently closes the event, including a confirmed stop or TP.
        """
        _ = cooldown_hours, now, token_id
        normalized_event_id = str(event_id or "").strip()
        if not normalized_event_id:
            return False, "event_id_missing"
        prior = (
            self.session.query(Trade.id)
            .filter(
                Trade.event_id == normalized_event_id,
                Trade.status.in_(
                    (
                        TradeStatus.PENDING_BUY,
                        TradeStatus.HOLDING,
                        TradeStatus.PENDING_SELL,
                        TradeStatus.COMPLETED,
                        TradeStatus.RESOLVED,
                        TradeStatus.QUARANTINED,
                    )
                ),
            )
            .first()
        )
        if prior is not None:
            return False, "event_already_traded"
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

    def record_exit_execution_observation(
        self,
        *,
        trade: Trade,
        observed_at: datetime,
        signal: str,
        trigger_price: float,
        position_shares: float,
        selected_shares: float,
        remaining_shares: float,
        max_executable_shares: float,
        selected_notional_usdc: float,
        max_executable_notional_usdc: float,
        best_bid: float,
        best_ask: Optional[float],
        spread: Optional[float],
        vwap: float,
        limit_price: float,
        levels_used: int,
        fallback_reason: str,
        full_position_required: bool,
        book_json: str,
    ) -> ExitExecutionObservation:
        """Commit immutable fresh-book evidence before an external SELL POST."""

        normalized_signal = str(signal or "").strip()
        normalized_reason = str(fallback_reason or "").strip()
        if normalized_signal not in {"take_profit", "absolute_stop"}:
            raise ValueError("exit execution signal is invalid")
        if not normalized_reason:
            raise ValueError("exit execution fallback reason is required")
        numeric_values = {
            "trigger_price": trigger_price,
            "position_shares": position_shares,
            "selected_shares": selected_shares,
            "remaining_shares": remaining_shares,
            "max_executable_shares": max_executable_shares,
            "selected_notional_usdc": selected_notional_usdc,
            "max_executable_notional_usdc": max_executable_notional_usdc,
            "best_bid": best_bid,
            "vwap": vwap,
            "limit_price": limit_price,
        }
        normalized_numbers: Dict[str, float] = {}
        for field, raw in numeric_values.items():
            try:
                value = float(raw)
            except (TypeError, ValueError) as error:
                raise ValueError(f"{field} must be numeric") from error
            if not math.isfinite(value):
                raise ValueError(f"{field} must be finite")
            normalized_numbers[field] = value
        normalized_best_ask = None
        normalized_spread = None
        if best_ask is not None:
            try:
                normalized_best_ask = float(best_ask)
            except (TypeError, ValueError) as error:
                raise ValueError("best_ask must be numeric") from error
            if not math.isfinite(normalized_best_ask):
                raise ValueError("best_ask must be finite")
        if spread is not None:
            try:
                normalized_spread = float(spread)
            except (TypeError, ValueError) as error:
                raise ValueError("spread must be numeric") from error
            if not math.isfinite(normalized_spread):
                raise ValueError("spread must be finite")
        if (
            normalized_numbers["position_shares"] <= 0
            or normalized_numbers["selected_shares"] <= 0
            or normalized_numbers["remaining_shares"] < 0
            or normalized_numbers["max_executable_shares"] <= 0
            or normalized_numbers["selected_notional_usdc"] <= 0
            or normalized_numbers["max_executable_notional_usdc"] <= 0
            or not 0 < normalized_numbers["best_bid"] < 1
            or not 0 < normalized_numbers["vwap"] < 1
            or not 0 < normalized_numbers["limit_price"] < 1
            or not 0 < normalized_numbers["trigger_price"] < 1
            or normalized_numbers["selected_shares"]
            > normalized_numbers["position_shares"] + _FILL_SIZE_TOLERANCE
            or normalized_numbers["selected_shares"]
            > normalized_numbers["max_executable_shares"]
            + _FILL_SIZE_TOLERANCE
            or not math.isclose(
                normalized_numbers["selected_shares"]
                + normalized_numbers["remaining_shares"],
                normalized_numbers["position_shares"],
                rel_tol=0,
                abs_tol=_FILL_SIZE_TOLERANCE,
            )
            or (
                normalized_best_ask is not None
                and (
                    not 0 < normalized_best_ask <= 1
                    or normalized_numbers["best_bid"]
                    > normalized_best_ask + _FILL_SIZE_TOLERANCE
                )
            )
            or (
                normalized_spread is not None
                and (
                    normalized_spread < 0
                    or normalized_best_ask is None
                    or not math.isclose(
                        normalized_spread,
                        normalized_best_ask - normalized_numbers["best_bid"],
                        rel_tol=0,
                        abs_tol=_FILL_SIZE_TOLERANCE,
                    )
                )
            )
        ):
            raise ValueError("exit execution observation is outside its domain")
        if (
            not isinstance(levels_used, int)
            or isinstance(levels_used, bool)
            or levels_used <= 0
        ):
            raise ValueError("levels_used must be a positive integer")
        try:
            decoded_book = json.loads(book_json)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("exit book evidence JSON is invalid") from error
        if not isinstance(decoded_book, dict):
            raise ValueError("exit book evidence must be an object")
        if str(decoded_book.get("token_id") or "").strip() != str(
            trade.token_id
        ):
            raise ValueError("exit book evidence token does not match trade")
        canonical_book = json.dumps(
            decoded_book,
            sort_keys=True,
            separators=(",", ":"),
        )
        book_sha256 = hashlib.sha256(canonical_book.encode()).hexdigest()
        observation = ExitExecutionObservation(
            run_id=current_run_id(),
            config_hash=self._current_config_hash(),
            trade_id=int(trade.id),
            condition_id=str(trade.condition_id),
            event_id=str(getattr(trade, "event_id", "") or "") or None,
            token_id=str(trade.token_id),
            observed_at=observed_at,
            signal=normalized_signal,
            trigger_price=normalized_numbers["trigger_price"],
            sport_family=str(getattr(trade, "sport_family", "") or "") or None,
            league_code=str(getattr(trade, "league_code", "") or "") or None,
            position_shares=normalized_numbers["position_shares"],
            selected_shares=normalized_numbers["selected_shares"],
            remaining_shares=normalized_numbers["remaining_shares"],
            max_executable_shares=normalized_numbers["max_executable_shares"],
            selected_notional_usdc=normalized_numbers["selected_notional_usdc"],
            max_executable_notional_usdc=normalized_numbers[
                "max_executable_notional_usdc"
            ],
            best_bid=normalized_numbers["best_bid"],
            best_ask=normalized_best_ask,
            spread=normalized_spread,
            vwap=normalized_numbers["vwap"],
            limit_price=normalized_numbers["limit_price"],
            levels_used=levels_used,
            fallback_reason=normalized_reason,
            full_position_required=1 if full_position_required else 0,
            book_sha256=book_sha256,
            book_json=canonical_book,
        )
        try:
            self.session.add(observation)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return observation

    def stage_clob_resolution_observation(
        self,
        *,
        trade_id: int,
        condition_id: str,
        observed_at: datetime,
        winner_index: Optional[int],
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
        if isinstance(selected_payout, bool) or selected_payout not in (
            0.0,
            0.5,
            1.0,
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
            or price not in (0.0, 0.5, 1.0)
            or not isinstance(token.get("winner"), bool)
            for token, price in zip(tokens, prices)
        ):
            raise ValueError(
                "resolution evidence token payouts/winners are invalid"
            )
        winners = [
            index for index, token in enumerate(tokens) if token.get("winner") is True
        ]
        is_void = prices == [0.5, 0.5] and not winners
        if is_void:
            if winner_index not in (None, _VOID_WINNER_INDEX) or (
                str(winner_token_id) != _VOID_WINNER_TOKEN_ID
                or str(winner_outcome) != _VOID_WINNER_OUTCOME
            ):
                raise ValueError("void resolution identity is not explicit")
            stored_winner_index = _VOID_WINNER_INDEX
            stored_winner_token_id = _VOID_WINNER_TOKEN_ID
            stored_winner_outcome = _VOID_WINNER_OUTCOME
        else:
            if isinstance(winner_index, bool) or winner_index not in (0, 1):
                raise ValueError("resolution winner is outside the binary domain")
            expected_prices = [0.0, 0.0]
            expected_prices[winner_index] = 1.0
            if prices != expected_prices or winners != [winner_index]:
                raise ValueError(
                    "resolution evidence must contain one aligned exact 0/1 winner"
                )
            winner = tokens[winner_index]
            if (
                str(winner.get("token_id") or "") != str(winner_token_id)
                or str(winner.get("outcome") or "") != str(winner_outcome)
            ):
                raise ValueError(
                    "resolution winner does not match normalized evidence"
                )
            stored_winner_index = winner_index
            stored_winner_token_id = str(winner_token_id)
            stored_winner_outcome = str(winner_outcome)
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
            winner_index=stored_winner_index,
            winner_token_id=stored_winner_token_id,
            winner_outcome=stored_winner_outcome,
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

    def get_isolated_buy_trades(self) -> List[Trade]:
        """Return 3-hour BUY uncertainties that remain evidence-monitored.

        They keep one economic-capacity reservation and permanently close the
        event to re-entry, but they must not stop unrelated games from using
        the remaining bounded capacity.
        """
        return (
            self.session.query(Trade)
            .filter(
                Trade.status == TradeStatus.QUARANTINED,
                Trade.exit_reason.in_(BUY_ISOLATION_REASONS),
            )
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
        isolated_buy = (
            self.session.query(func.count(Trade.id))
            .filter(
                Trade.status == TradeStatus.QUARANTINED,
                Trade.exit_reason.in_(BUY_ISOLATION_REASONS),
            )
            .scalar()
            or 0
        )
        return {
            "total": int(total),
            "isolated_buy": int(isolated_buy),
            "isolated_stop_sell": int(isolated_stop_sell),
            "blocking": max(
                0,
                int(total) - int(isolated_buy) - int(isolated_stop_sell),
            ),
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

    def get_open_buy_evidence_gap_count(self, *, mode: Optional[str] = None) -> int:
        """Count unsafe BUY evidence gaps, excluding bounded BUY isolation.

        A timed-out BUY quarantine is intentionally incomplete evidence, but
        it is already represented as one event-local economic reservation.
        Counting that same row as a global evidence blocker would defeat the
        isolation contract and stop every unrelated match. ``mode`` permits
        the cycle guard to distinguish expected simulation omissions without
        exempting live or unclassified rows in the same database.
        """
        if mode not in (None, "live", "sim"):
            raise ValueError("mode must be live, sim, or None")
        query = (
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
                    Trade.status != TradeStatus.QUARANTINED,
                    Trade.exit_reason.is_(None),
                    ~Trade.exit_reason.in_(BUY_ISOLATION_REASONS),
                ),
                or_(
                    Trade.buy_order_id.is_(None),
                    Trade.buy_confirmed_size.is_(None),
                    Trade.buy_confirmed_vwap.is_(None),
                    Trade.buy_confirmed_fee_usdc.is_(None),
                ),
            )
        )
        if mode is not None:
            query = query.filter(Trade.mode == mode)
        result = query.scalar() or 0
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
        event_id: Optional[str] = None,
        outcome_side: Optional[str] = None,
        result_kind: Optional[str] = None,
        midpoint: Optional[float] = None,
        liquidity: Optional[float] = None,
        volume_24h: Optional[float] = None,
        best_bid: Optional[float] = None,
        best_ask: Optional[float] = None,
        spread: Optional[float] = None,
        source_updated_at: Optional[str] = None,
        source_elapsed_minutes: Optional[float] = None,
        source_clock_reason: Optional[str] = None,
        book_json: Optional[str] = None,
        execution_capacity_json: Optional[str] = None,
        league_code: Optional[str] = None,
        league_name: Optional[str] = None,
        market_tags_json: Optional[str] = None,
        evidence_context: Optional[Dict[str, Any]] = None,
        event_cycle_id: Optional[str] = None,
        event_set_complete: Optional[bool] = None,
        event_set_reason: Optional[str] = None,
        market: Optional[Dict[str, Any]] = None,
        commit: bool = True,
    ) -> MarketSnapshot:
        context = _normalize_evidence_context(evidence_context)
        if market is not None:
            self._upsert_market_catalog(
                condition_id,
                market,
                evidence_context=evidence_context,
            )
        snapshot = MarketSnapshot(
            condition_id=condition_id,
            event_id=event_id,
            token_id=token_id,
            outcome=outcome,
            outcome_side=outcome_side,
            result_kind=result_kind,
            probability=probability,
            midpoint=midpoint,
            liquidity=liquidity,
            volume_24h=volume_24h,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            source_updated_at=source_updated_at,
            source_elapsed_minutes=source_elapsed_minutes,
            source_clock_reason=source_clock_reason,
            book_json=book_json,
            execution_capacity_json=execution_capacity_json,
            run_id=current_run_id(),
            config_hash=self._current_config_hash(),
            sport_family=context["sport_family"],
            league_code=str(league_code or "") or None,
            league_name=str(league_name or "") or None,
            market_tags_json=market_tags_json,
            sport_profile_version=context["sport_profile_version"],
            protocol_sha256=context["protocol_sha256"],
            classifier_version=context["classifier_version"],
            league_mapping_sha256=context["league_mapping_sha256"],
            strategy_source_digest=context["strategy_source_digest"],
            book_shape=context["book_shape"],
            event_cycle_id=str(event_cycle_id or "") or None,
            event_set_complete=(
                None if event_set_complete is None else int(event_set_complete)
            ),
            event_set_reason=str(event_set_reason or "") or None,
        )
        self.session.add(snapshot)
        self.session.flush()
        if commit:
            self.session.commit()
        return snapshot

    def finalize_staged_event_cycle_health(
        self,
        event_results: Dict[str, Dict[str, Any]],
    ) -> None:
        """Apply final book-coverage health before the enclosing transaction commits."""

        for item in event_results.values():
            event_cycle_id = str(item.get("event_cycle_id") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if not event_cycle_id or not reason:
                raise ValueError("final event-cycle health is incomplete")
            complete = int(item.get("complete") is True)
            self.session.query(MarketSnapshot).filter(
                MarketSnapshot.event_cycle_id == event_cycle_id
            ).update(
                {
                    MarketSnapshot.event_set_complete: complete,
                    MarketSnapshot.event_set_reason: reason,
                },
                synchronize_session="fetch",
            )
            condition_ids = [str(value) for value in item.get("condition_ids", [])]
            if condition_ids:
                self.session.query(MarketCatalog).filter(
                    MarketCatalog.condition_id.in_(condition_ids)
                ).update(
                    {
                        MarketCatalog.last_event_set_complete: complete,
                        MarketCatalog.last_event_set_reason: reason,
                    },
                    synchronize_session="fetch",
                )
        self.session.flush()

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
        source_elapsed_minutes: Optional[float] = None,
        trend_start_snapshot_id: Optional[int] = None,
        trend_middle_snapshot_id: Optional[int] = None,
        trend_observations: Optional[int] = None,
        trend_cumulative_move: Optional[float] = None,
        trend_max_pullback: Optional[float] = None,
        trend_elapsed_seconds: Optional[float] = None,
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
            if existing.trade_id is not None:
                linked = self.session.get(Trade, int(existing.trade_id))
                if linked is not None and linked.status == TradeStatus.UNFILLED:
                    existing.trade_id = None
                    existing.execution_state = "PROVEN_ZERO_FILL_RETRYABLE"
                    existing.execution_reason = (
                        "prior linked BUY has exact terminal zero-fill evidence"
                    )
            if (
                existing.trade_id is None
                and (
                    str(existing.execution_state or "").upper()
                    in _RETRYABLE_PROVEN_NO_POST_EPISODE_STATES
                    or str(existing.execution_state or "").upper()
                    == "PROVEN_ZERO_FILL_RETRYABLE"
                )
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
                existing.source_elapsed_minutes = source_elapsed_minutes
                existing.trend_start_snapshot_id = trend_start_snapshot_id
                existing.trend_middle_snapshot_id = trend_middle_snapshot_id
                existing.trend_observations = trend_observations
                existing.trend_cumulative_move = trend_cumulative_move
                existing.trend_max_pullback = trend_max_pullback
                existing.trend_elapsed_seconds = trend_elapsed_seconds
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
            source_elapsed_minutes=source_elapsed_minutes,
            trend_start_snapshot_id=trend_start_snapshot_id,
            trend_middle_snapshot_id=trend_middle_snapshot_id,
            trend_observations=trend_observations,
            trend_cumulative_move=trend_cumulative_move,
            trend_max_pullback=trend_max_pullback,
            trend_elapsed_seconds=trend_elapsed_seconds,
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

    def get_snapshots_by_ids(
        self,
        snapshot_ids: List[int],
    ) -> List[MarketSnapshot]:
        """Return an exact, chronological snapshot lineage or an empty list."""
        if not snapshot_ids or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in snapshot_ids
        ):
            return []
        unique_ids = list(dict.fromkeys(snapshot_ids))
        if len(unique_ids) != len(snapshot_ids):
            return []
        rows = (
            self.session.query(MarketSnapshot)
            .filter(MarketSnapshot.id.in_(unique_ids))
            .order_by(MarketSnapshot.timestamp.asc(), MarketSnapshot.id.asc())
            .all()
        )
        return rows if len(rows) == len(unique_ids) else []

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

    def get_recent_token_snapshots(
        self,
        token_id: str,
        *,
        limit: int,
    ) -> List[MarketSnapshot]:
        """Return the latest direct-book observations for exactly one token.

        Condition-level history is unsafe for a six-token event because it can
        interleave YES and NO prices.  The trend contract is therefore bound to
        one immutable CLOB token identity and ordered by timestamp plus row ID.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("snapshot limit must be a positive integer")
        rows = (
            self.session.query(MarketSnapshot)
            .filter(MarketSnapshot.token_id == str(token_id))
            .order_by(MarketSnapshot.timestamp.desc(), MarketSnapshot.id.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(rows))

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
        evidence_context: Optional[Dict[str, Any]] = None,
        event_cycle: Optional[Dict[str, Any]] = None,
        live_sweep_id: Optional[str] = None,
        seen_at: Optional[datetime] = None,
        commit: bool = False,
    ) -> MarketCatalog:
        observed_at = seen_at or datetime.utcnow()
        catalog = self._upsert_market_catalog(
            condition_id,
            market,
            evidence_context=evidence_context,
            observed_at=observed_at,
        )
        if event_cycle is not None:
            catalog.last_event_cycle_id = str(
                event_cycle.get("event_cycle_id") or ""
            ) or None
            catalog.last_event_set_complete = int(
                event_cycle.get("complete") is True
            )
            catalog.last_event_set_reason = str(
                event_cycle.get("reason") or ""
            ) or None
        if live_sweep_id:
            catalog.last_live_sweep_id = str(live_sweep_id)
            catalog.last_live_seen_at = observed_at
            if catalog.resolution_evidence_sha256:
                catalog.followup_status = "TERMINAL"
                catalog.followup_next_attempt_at = None
            else:
                catalog.followup_status = "TRACKED_LIVE"
                catalog.followup_next_attempt_at = observed_at + timedelta(minutes=1)
                catalog.followup_last_error = None
        if commit:
            self.session.commit()
        return catalog

    @staticmethod
    def _terminal_catalog_evidence(
        condition_id: str,
        market: Dict[str, Any],
        *,
        event_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        resolution = get_proven_resolution(market)
        if resolution is None:
            return None
        outcomes = get_aligned_binary_outcomes(market)
        if len(outcomes) != 2:
            raise ValueError("terminal market must have two aligned outcomes")
        tokens = [
            {
                "index": int(item["token_index"]),
                "token_id": str(item["token_id"]),
                "outcome": str(item["outcome"]),
                "payout": float(item["probability"]),
            }
            for item in outcomes
        ]
        winners = [item for item in tokens if item["payout"] == 1.0]
        losers = [item for item in tokens if item["payout"] == 0.0]
        is_void = (
            resolution.get("settlement_kind") == "VOID"
            and [item["payout"] for item in tokens] == [0.5, 0.5]
        )
        if not is_void and (len(winners) != 1 or len(losers) != 1):
            raise ValueError(
                "terminal market payouts must be unique one-hot 0/1 or exact void"
            )
        payload = {
            "schema_version": 1,
            "source": "GAMMA_CONDITION_FOLLOWUP",
            "condition_id": str(condition_id),
            "event_id": event_id,
            "closed": True,
            "status": str(resolution["status"]),
            "source_updated_at": market.get("updatedAt"),
            "tokens": tokens,
        }
        evidence_json = _canonical_payload(payload)
        winner_index = _VOID_WINNER_INDEX if is_void else int(winners[0]["index"])
        winner_token_id = (
            _VOID_WINNER_TOKEN_ID if is_void else str(winners[0]["token_id"])
        )
        winner_outcome = (
            _VOID_WINNER_OUTCOME if is_void else str(winners[0]["outcome"])
        )
        return {
            "winner_index": winner_index,
            "winner_token_id": winner_token_id,
            "winner_outcome": winner_outcome,
            "payouts_json": _canonical_payload(
                {
                    item["token_id"]: item["payout"]
                    for item in tokens
                }
            ),
            "evidence_json": evidence_json,
            "evidence_sha256": hashlib.sha256(evidence_json.encode()).hexdigest(),
            "status": str(resolution["status"]),
            "resolved_outcome": str(resolution["outcome"]),
            "resolved_value": float(resolution["first_outcome_payout"]),
        }

    def _stage_tracked_resolution(
        self,
        *,
        condition_id: str,
        event_id: Optional[str],
        evidence_context: Optional[Dict[str, Any]],
        terminal: Dict[str, Any],
        observed_at: datetime,
    ) -> TrackedResolutionObservation:
        context = _normalize_evidence_context(evidence_context)
        source = str(
            terminal.get("source") or "GAMMA_CONDITION_FOLLOWUP"
        ).strip()
        identity_namespace = (
            "gamma" if source == "GAMMA_CONDITION_FOLLOWUP" else "clob"
        )
        identity = hashlib.sha256(
            f"{identity_namespace}:{condition_id}:"
            f"{terminal['evidence_sha256']}".encode()
        ).hexdigest()
        existing = self.session.get(TrackedResolutionObservation, identity)
        if existing is not None:
            return existing
        row = TrackedResolutionObservation(
            resolution_id=identity,
            condition_id=str(condition_id),
            event_id=event_id,
            run_id=current_run_id(),
            config_hash=self._current_config_hash(),
            sport_family=context["sport_family"],
            sport_profile_version=context["sport_profile_version"],
            protocol_sha256=context["protocol_sha256"],
            classifier_version=context["classifier_version"],
            league_mapping_sha256=context["league_mapping_sha256"],
            strategy_source_digest=context["strategy_source_digest"],
            observed_at=observed_at,
            source=source,
            winner_index=int(terminal["winner_index"]),
            winner_token_id=str(terminal["winner_token_id"]),
            winner_outcome=str(terminal["winner_outcome"]),
            payouts_json=str(terminal["payouts_json"]),
            evidence_sha256=str(terminal["evidence_sha256"]),
            evidence_json=str(terminal["evidence_json"]),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _upsert_market_catalog(
        self,
        condition_id: str,
        market: Dict[str, Any],
        *,
        evidence_context: Optional[Dict[str, Any]] = None,
        observed_at: Optional[datetime] = None,
    ) -> MarketCatalog:
        normalized_condition = str(condition_id or "").strip()
        returned_condition = str(
            market.get("conditionId") or market.get("condition_id") or ""
        ).strip()
        if not normalized_condition:
            raise ValueError("catalog condition_id is required")
        if returned_condition and returned_condition != normalized_condition:
            raise ValueError("catalog market condition_id mismatch")
        observed_at = observed_at or datetime.utcnow()
        context = _normalize_evidence_context(evidence_context)
        events = market.get("events") or []
        event = (
            events[0]
            if isinstance(events, list) and events and isinstance(events[0], dict)
            else {}
        )
        event_meta = get_event_metadata(market)
        tags = market.get("tags") or []
        fee_schedule = market.get("feeSchedule") or {}
        terminal = self._terminal_catalog_evidence(
            normalized_condition,
            market,
            event_id=event_meta["event_id"],
        )

        def bool_int(value: Any) -> Optional[int]:
            return None if not isinstance(value, bool) else int(value)

        values: Dict[str, Any] = {
            "market_id": str(market.get("id") or "") or None,
            "market_slug": market.get("slug"),
            "question": market.get("question"),
            "event_id": event_meta["event_id"],
            "event_slug": event_meta["event_slug"],
            "event_title": event.get("title"),
            "event_market_count": len(event.get("markets") or []) or None,
            "end_date": market.get("endDate"),
            "outcomes_json": (
                _canonical_json_list(market.get("outcomes"), field_name="outcomes")
                if market.get("outcomes") is not None
                else None
            ),
            "outcome_prices_json": (
                _canonical_json_list(
                    market.get("outcomePrices"), field_name="outcomePrices"
                )
                if market.get("outcomePrices") is not None
                else None
            ),
            "token_ids_json": (
                _canonical_json_list(
                    market.get("clobTokenIds"), field_name="clobTokenIds"
                )
                if market.get("clobTokenIds") is not None
                else None
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
                terminal["status"]
                if terminal
                else market.get("umaResolutionStatus")
            ),
            "resolved_outcome": (
                terminal["resolved_outcome"] if terminal else None
            ),
            # Legacy catalog column name stores the first listed outcome payout.
            "resolved_value": (
                terminal["resolved_value"] if terminal else None
            ),
            "resolved_at": market.get("resolvedAt") or market.get("closedTime"),
            "source_updated_at": market.get("updatedAt"),
            "config_hash": self._current_config_hash(),
            "sport_family": context["sport_family"],
            "league_code": str(market.get("leagueCode") or "")
            or context["sport_family"]
            or None,
            "league_name": str(market.get("leagueName") or "")
            or context["sport_family"].upper()
            or None,
            "sport_profile_version": context["sport_profile_version"],
            "protocol_sha256": context["protocol_sha256"],
            "classifier_version": context["classifier_version"],
            "league_mapping_sha256": context["league_mapping_sha256"],
            "strategy_source_digest": context["strategy_source_digest"],
            "book_shape": context["book_shape"],
            "resolution_evidence_json": (
                terminal["evidence_json"] if terminal else None
            ),
            "resolution_evidence_sha256": (
                terminal["evidence_sha256"] if terminal else None
            ),
            "resolution_observed_at": observed_at if terminal else None,
            "last_seen_at": observed_at,
        }
        catalog = self.session.get(MarketCatalog, normalized_condition)
        if catalog is None:
            required_defaults = {
                "outcomes_json": "[]",
                "outcome_prices_json": "[]",
                "token_ids_json": "[]",
                "tags_json": "[]",
            }
            for key, default in required_defaults.items():
                if values.get(key) is None:
                    values[key] = default
            catalog = MarketCatalog(condition_id=normalized_condition, **values)
            self.session.add(catalog)
        else:
            for key, value in values.items():
                if value is not None:
                    setattr(catalog, key, value)
        if terminal is not None:
            self._stage_tracked_resolution(
                condition_id=normalized_condition,
                event_id=(event_meta["event_id"] or catalog.event_id),
                evidence_context=evidence_context,
                terminal=terminal,
                observed_at=observed_at,
            )
            catalog.followup_status = "TERMINAL"
            catalog.followup_next_attempt_at = None
            catalog.followup_last_error = None
        self.session.flush()
        return catalog

    def get_due_followup_catalogs(
        self,
        *,
        now: datetime,
        evidence_context: Dict[str, Any],
        exclude_condition_ids: set[str],
        limit: int,
    ) -> List[MarketCatalog]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("follow-up limit must be a positive integer")
        context = _normalize_evidence_context(evidence_context)
        query = self.session.query(MarketCatalog).filter(
            MarketCatalog.sport_family == context["sport_family"],
            MarketCatalog.sport_profile_version
            == context["sport_profile_version"],
            MarketCatalog.protocol_sha256 == context["protocol_sha256"],
            MarketCatalog.resolution_evidence_sha256.is_(None),
            or_(
                MarketCatalog.followup_next_attempt_at.is_(None),
                MarketCatalog.followup_next_attempt_at <= now,
            ),
        )
        normalized_excluded = {
            str(item).strip() for item in exclude_condition_ids if str(item).strip()
        }
        if normalized_excluded:
            query = query.filter(
                ~MarketCatalog.condition_id.in_(normalized_excluded)
            )
        return (
            query.order_by(
                MarketCatalog.followup_next_attempt_at.asc(),
                MarketCatalog.last_live_seen_at.asc(),
                MarketCatalog.condition_id.asc(),
            )
            .limit(limit)
            .all()
        )

    def record_followup_clob_resolution(
        self,
        condition_id: str,
        proof: Any,
        *,
        attempted_at: datetime,
        evidence_context: Dict[str, Any],
        commit: bool = True,
    ) -> MarketCatalog:
        """Store exact CLOB one-hot or void evidence after a Gamma gap.

        The result is accepted only when its condition, token, outcome and
        terminal identities exactly match the catalog captured while live. It
        is order-independent collector evidence, not a simulated fill or P&L.
        """

        normalized_condition = str(condition_id or "").strip()
        catalog = self.session.get(MarketCatalog, normalized_condition)
        if catalog is None:
            raise ValueError("follow-up catalog condition is missing")
        proof_condition = str(
            getattr(proof, "condition_id", "") or ""
        ).strip()
        if proof_condition != normalized_condition:
            raise ValueError("CLOB follow-up condition_id mismatch")
        proof_status = str(getattr(proof, "status", "") or "")
        if proof_status not in {"RESOLVED", "VOID"}:
            raise ValueError("CLOB follow-up proof is not terminal")

        try:
            expected_token_ids = [
                str(value).strip() for value in json.loads(catalog.token_ids_json)
            ]
            expected_outcomes = [
                str(value).strip() for value in json.loads(catalog.outcomes_json)
            ]
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("catalog token/outcome identity is invalid") from error
        if (
            len(expected_token_ids) != 2
            or len(expected_outcomes) != 2
            or len(set(expected_token_ids)) != 2
            or len(set(expected_outcomes)) != 2
            or any(not value for value in expected_token_ids + expected_outcomes)
        ):
            raise ValueError(
                "catalog must contain two distinct token/outcome identities"
            )

        raw_tokens = list(getattr(proof, "tokens", ()) or ())
        if len(raw_tokens) != 2:
            raise ValueError("CLOB follow-up proof must contain two tokens")
        proof_by_token: Dict[str, Dict[str, Any]] = {}
        for raw_token in raw_tokens:
            token_id = str(getattr(raw_token, "token_id", "") or "").strip()
            outcome = str(getattr(raw_token, "outcome", "") or "").strip()
            winner = getattr(raw_token, "winner", None)
            try:
                payout = float(getattr(raw_token, "price"))
            except (TypeError, ValueError) as error:
                raise ValueError("CLOB follow-up payout is invalid") from error
            if (
                not token_id
                or not outcome
                or not isinstance(winner, bool)
                or not math.isfinite(payout)
                or payout not in (0.0, 0.5, 1.0)
                or token_id in proof_by_token
            ):
                raise ValueError("CLOB follow-up token evidence is invalid")
            proof_by_token[token_id] = {
                "token_id": token_id,
                "outcome": outcome,
                "winner": winner,
                "payout": payout,
            }
        if set(proof_by_token) != set(expected_token_ids):
            raise ValueError("CLOB follow-up token set differs from live catalog")

        tokens: List[Dict[str, Any]] = []
        for index, (token_id, expected_outcome) in enumerate(
            zip(expected_token_ids, expected_outcomes)
        ):
            item = proof_by_token[token_id]
            if item["outcome"] != expected_outcome:
                raise ValueError("CLOB follow-up outcome/token alignment mismatch")
            tokens.append({"index": index, **item})
        winners = [item for item in tokens if item["winner"]]
        is_void = proof_status == "VOID"
        if is_void:
            if winners or [item["payout"] for item in tokens] != [0.5, 0.5]:
                raise ValueError("CLOB follow-up void payout is not exact 0.5/0.5")
        elif (
            len(winners) != 1
            or winners[0]["payout"] != 1.0
            or any(item["winner"] != (item["payout"] == 1.0) for item in tokens)
        ):
            raise ValueError("CLOB follow-up payout is not unique one-hot")

        raw_evidence_sha256 = str(
            getattr(proof, "evidence_sha256", "") or ""
        ).strip()
        raw_evidence_json = str(getattr(proof, "evidence_json", "") or "").strip()
        if (
            len(raw_evidence_sha256) != 64
            or hashlib.sha256(raw_evidence_json.encode()).hexdigest()
            != raw_evidence_sha256
        ):
            raise ValueError("CLOB follow-up evidence checksum mismatch")
        resolution_status = (
            "clob_closed_void_0_5_0_5"
            if is_void
            else "clob_closed_unique_winner"
        )
        payload = {
            "schema_version": 1,
            "source": "CLOB_CONDITION_FOLLOWUP",
            "condition_id": normalized_condition,
            "event_id": catalog.event_id,
            "closed": True,
            "status": resolution_status,
            "source_observed_at": str(getattr(proof, "observed_at", "") or ""),
            "clob_evidence_sha256": raw_evidence_sha256,
            "tokens": tokens,
        }
        evidence_json = _canonical_payload(payload)
        winner_index = _VOID_WINNER_INDEX if is_void else int(winners[0]["index"])
        winner_token_id = (
            _VOID_WINNER_TOKEN_ID if is_void else str(winners[0]["token_id"])
        )
        winner_outcome = (
            _VOID_WINNER_OUTCOME if is_void else str(winners[0]["outcome"])
        )
        terminal = {
            "source": "CLOB_CONDITION_FOLLOWUP",
            "winner_index": winner_index,
            "winner_token_id": winner_token_id,
            "winner_outcome": winner_outcome,
            "payouts_json": _canonical_payload(
                {item["token_id"]: item["payout"] for item in tokens}
            ),
            "evidence_json": evidence_json,
            "evidence_sha256": hashlib.sha256(evidence_json.encode()).hexdigest(),
            "status": resolution_status,
            "resolved_outcome": winner_outcome,
            "resolved_value": float(tokens[0]["payout"]),
        }
        self._stage_tracked_resolution(
            condition_id=normalized_condition,
            event_id=catalog.event_id,
            evidence_context=evidence_context,
            terminal=terminal,
            observed_at=attempted_at,
        )
        catalog.closed = 1
        catalog.active = 0
        catalog.accepting_orders = 0
        catalog.resolution_status = terminal["status"]
        catalog.resolved_outcome = terminal["resolved_outcome"]
        catalog.resolved_value = terminal["resolved_value"]
        catalog.resolved_at = str(getattr(proof, "observed_at", "") or "") or None
        catalog.resolution_evidence_json = terminal["evidence_json"]
        catalog.resolution_evidence_sha256 = terminal["evidence_sha256"]
        catalog.resolution_observed_at = attempted_at
        catalog.followup_attempt_count = int(catalog.followup_attempt_count or 0) + 1
        catalog.followup_last_attempt_at = attempted_at
        catalog.followup_status = "TERMINAL"
        catalog.followup_next_attempt_at = None
        catalog.followup_last_error = None
        if commit:
            self.session.commit()
        return catalog

    @staticmethod
    def _followup_delay_minutes(attempt_count: int) -> int:
        return min(15, 2 ** min(max(0, attempt_count - 1), 4))

    def record_followup_missing(
        self,
        condition_id: str,
        *,
        attempted_at: datetime,
        reason: str = "gamma_condition_not_found",
        commit: bool = True,
    ) -> MarketCatalog:
        catalog = self.session.get(MarketCatalog, str(condition_id))
        if catalog is None:
            raise ValueError("follow-up catalog condition is missing")
        catalog.followup_attempt_count = int(catalog.followup_attempt_count or 0) + 1
        catalog.followup_last_attempt_at = attempted_at
        catalog.followup_status = "SOURCE_MISSING"
        catalog.followup_last_error = str(reason or "source_missing")
        catalog.followup_next_attempt_at = attempted_at + timedelta(
            minutes=self._followup_delay_minutes(catalog.followup_attempt_count)
        )
        if commit:
            self.session.commit()
        return catalog

    def record_followup_market(
        self,
        condition_id: str,
        market: Dict[str, Any],
        *,
        attempted_at: datetime,
        evidence_context: Dict[str, Any],
        commit: bool = True,
    ) -> MarketCatalog:
        catalog = self._upsert_market_catalog(
            condition_id,
            market,
            evidence_context=evidence_context,
            observed_at=attempted_at,
        )
        catalog.followup_attempt_count = int(catalog.followup_attempt_count or 0) + 1
        catalog.followup_last_attempt_at = attempted_at
        if catalog.resolution_evidence_sha256:
            catalog.followup_status = "TERMINAL"
            catalog.followup_next_attempt_at = None
            catalog.followup_last_error = None
        else:
            catalog.followup_status = "FOLLOWING"
            catalog.followup_last_error = None
            catalog.followup_next_attempt_at = attempted_at + timedelta(
                minutes=self._followup_delay_minutes(catalog.followup_attempt_count)
            )
        if commit:
            self.session.commit()
        return catalog

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
        event_results: Dict[str, Dict[str, Any]],
        *,
        evidence_context: Dict[str, Any],
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
        context = _normalize_evidence_context(evidence_context)
        if str(attestation.get("sport_family") or "").strip().lower() != context[
            "sport_family"
        ]:
            raise ValueError("Gamma sweep sport family does not match runtime profile")

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

        if not isinstance(event_results, dict):
            raise ValueError("event-cycle evidence must be a mapping")
        expected_market_count = int(context["expected_market_count"])
        expected_token_count = int(context["expected_token_count"])
        expected_result_kinds = json.loads(context["expected_result_kinds_json"])
        normalized_events: List[Dict[str, Any]] = []
        condition_to_event: Dict[str, Dict[str, Any]] = {}
        for key, raw_event in event_results.items():
            if not isinstance(raw_event, dict):
                raise ValueError("event-cycle evidence must contain objects")
            event_id = str(raw_event.get("event_id") or "").strip()
            if not event_id or event_id != str(key):
                raise ValueError("event-cycle identity mismatch")
            condition_ids = sorted(
                str(item).strip()
                for item in raw_event.get("condition_ids", [])
                if str(item).strip()
            )
            token_ids = sorted(
                str(item).strip()
                for item in raw_event.get("token_ids", [])
                if str(item).strip()
            )
            if len(condition_ids) != len(set(condition_ids)):
                raise ValueError("event-cycle condition list is not unique")
            if len(token_ids) != len(set(token_ids)):
                raise ValueError("event-cycle token list is not unique")
            observed_kinds = sorted(
                {
                    str(item).strip().upper()
                    for item in raw_event.get("observed_result_kinds", [])
                    if str(item).strip()
                }
            )
            missing_kinds = sorted(set(expected_result_kinds) - set(observed_kinds))
            if missing_kinds != sorted(raw_event.get("missing_result_kinds", [])):
                raise ValueError("event-cycle missing result kinds mismatch")
            duplicate_condition_count = int(
                raw_event.get("duplicate_condition_count", 0)
            )
            duplicate_token_count = int(raw_event.get("duplicate_token_count", 0))
            duplicate_identity_count = int(
                raw_event.get("duplicate_identity_count", 0)
            )
            observed_market_count = int(raw_event.get("observed_market_count", -1))
            observed_token_count = int(raw_event.get("observed_token_count", -1))
            identity_complete = raw_event.get("identity_complete") is True
            structure_complete = bool(
                observed_market_count == expected_market_count
                and observed_token_count == expected_token_count
                and duplicate_condition_count == 0
                and duplicate_token_count == 0
                and duplicate_identity_count == 0
                and not missing_kinds
                and identity_complete
            )
            if raw_event.get("structure_complete") is not structure_complete:
                raise ValueError("event-cycle structure-complete flag is not derivable")
            if not isinstance(raw_event.get("book_complete"), bool):
                raise ValueError("event-cycle book-complete flag must be boolean")
            book_complete = raw_event["book_complete"]
            complete = bool(structure_complete and book_complete)
            if raw_event.get("complete") is not complete:
                raise ValueError("event-cycle complete flag is not derivable")
            if observed_market_count != len(condition_ids):
                raise ValueError("event-cycle market count mismatch")
            if observed_token_count != len(token_ids):
                raise ValueError("event-cycle token count mismatch")
            reason = str(raw_event.get("reason") or "").strip()
            if not reason:
                raise ValueError("event-cycle reason is required")
            event_cycle_id = hashlib.sha256(
                f"{attestation.get('sweep_id')}:{event_id}".encode()
            ).hexdigest()
            if str(raw_event.get("event_cycle_id") or "") != event_cycle_id:
                raise ValueError("event-cycle digest identity mismatch")
            payload = {
                "event_cycle_id": event_cycle_id,
                "event_id": event_id,
                "condition_ids": condition_ids,
                "token_ids": token_ids,
                "expected_result_kinds": expected_result_kinds,
                "observed_result_kinds": observed_kinds,
                "missing_result_kinds": missing_kinds,
                "expected_market_count": expected_market_count,
                "observed_market_count": observed_market_count,
                "expected_token_count": expected_token_count,
                "observed_token_count": observed_token_count,
                "duplicate_condition_count": duplicate_condition_count,
                "duplicate_token_count": duplicate_token_count,
                "duplicate_identity_count": duplicate_identity_count,
                "identity_complete": identity_complete,
                "structure_complete": structure_complete,
                "book_complete": book_complete,
                "complete": complete,
                "reason": reason,
            }
            evidence_sha256 = hashlib.sha256(
                _canonical_payload(payload).encode()
            ).hexdigest()
            if str(raw_event.get("evidence_sha256") or "") != evidence_sha256:
                raise ValueError("event-cycle evidence SHA-256 mismatch")
            normalized = {**payload, "evidence_sha256": evidence_sha256}
            normalized_events.append(normalized)
            for condition_id in condition_ids:
                if condition_id in condition_to_event:
                    raise ValueError("condition belongs to multiple event cycles")
                condition_to_event[condition_id] = normalized
        if set(condition_to_event) != qualified_ids:
            raise ValueError(
                "event-cycle evidence must cover every qualified condition exactly once"
            )
        normalized_events.sort(key=lambda item: item["event_id"])
        event_evidence_digest = hashlib.sha256(
            _canonical_payload(
                [
                    {
                        "event_id": item["event_id"],
                        "evidence_sha256": item["evidence_sha256"],
                    }
                    for item in normalized_events
                ]
            ).encode()
        ).hexdigest()

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
            event_result = condition_to_event.get(membership["condition_id"])
            enriched.append(
                (membership, eligible, snapshotted, reason, event_result)
            )

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
            "golden-plum",
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
            config_hash=self._current_config_hash(),
            sport_family=context["sport_family"],
            sport_profile_version=context["sport_profile_version"],
            protocol_sha256=context["protocol_sha256"],
            classifier_version=context["classifier_version"],
            league_mapping_sha256=context["league_mapping_sha256"],
            strategy_source_digest=context["strategy_source_digest"],
            book_shape=context["book_shape"],
            expected_result_kinds_json=context["expected_result_kinds_json"],
            expected_market_count=expected_market_count,
            expected_token_count=expected_token_count,
            event_count=len(normalized_events),
            complete_event_count=sum(int(item["complete"]) for item in normalized_events),
            incomplete_event_count=sum(
                int(not item["complete"]) for item in normalized_events
            ),
            event_evidence_digest_sha256=event_evidence_digest,
        )
        self.session.add(sweep)
        observed_at = completed
        for item in normalized_events:
            self.session.add(
                EventCycleEvidence(
                    event_cycle_id=item["event_cycle_id"],
                    sweep_id=sweep_id,
                    run_id=current_run_id(),
                    config_hash=self._current_config_hash(),
                    event_id=item["event_id"],
                    observed_at=observed_at,
                    sport_family=context["sport_family"],
                    sport_profile_version=context["sport_profile_version"],
                    protocol_sha256=context["protocol_sha256"],
                    classifier_version=context["classifier_version"],
                    league_mapping_sha256=context["league_mapping_sha256"],
                    strategy_source_digest=context["strategy_source_digest"],
                    book_shape=context["book_shape"],
                    expected_result_kinds_json=context[
                        "expected_result_kinds_json"
                    ],
                    observed_result_kinds_json=_canonical_payload(
                        item["observed_result_kinds"]
                    ),
                    missing_result_kinds_json=_canonical_payload(
                        item["missing_result_kinds"]
                    ),
                    condition_ids_json=_canonical_payload(item["condition_ids"]),
                    token_ids_json=_canonical_payload(item["token_ids"]),
                    expected_market_count=expected_market_count,
                    observed_market_count=item["observed_market_count"],
                    expected_token_count=expected_token_count,
                    observed_token_count=item["observed_token_count"],
                    duplicate_condition_count=item[
                        "duplicate_condition_count"
                    ],
                    duplicate_token_count=item["duplicate_token_count"],
                    duplicate_identity_count=item[
                        "duplicate_identity_count"
                    ],
                    complete=int(item["complete"]),
                    reason=item["reason"],
                    evidence_sha256=item["evidence_sha256"],
                )
            )
        if store_membership_details:
            for membership, eligible, snapshotted, reason, event_result in enriched:
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
                        event_id=(event_result or {}).get("event_id"),
                        event_cycle_id=(event_result or {}).get("event_cycle_id"),
                        event_set_complete=(
                            None
                            if event_result is None
                            else int(event_result["complete"])
                        ),
                        event_set_reason=(event_result or {}).get("reason"),
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
        if compact_maintenance_active(self.session, "golden-plum"):
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
            self.session.query(EventCycleEvidence).filter(
                EventCycleEvidence.sweep_id.in_(expired_sweeps)
            ).delete(synchronize_session=False)
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
                settlement_basis = str(
                    trade.settlement_assumption_basis or ""
                )
                if settlement_basis.startswith("remaining_position_resolution_"):
                    # v6 stores payout economics only for the shares still held
                    # after confirmed partial sells.  Those sold shares are
                    # already absent and must not be invalidated a second time.
                    pass
                else:
                    # Legacy settlement P&L covers the original confirmed BUY.
                    # Invalidate only the shares later proven sold.
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
