"""Repository operations for Micro-Cascade trades and research evidence."""

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
from typing import Any, Callable, Dict, List, Optional, Tuple

from polybot_observability import (
    compact_maintenance_active,
    current_run_id,
    membership_details_due,
)
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.orm import Session

from .models import (
    ExperimentState,
    MarketCatalog,
    MarketSnapshot,
    MarketSweep,
    MarketSweepMembership,
    MicroCascadeExperimentContract,
    MicroCascadeFollowupObservation,
    MicroCascadeSignalDecision,
    SkippedMarket,
    Trade,
    TradeStatus,
)
from ..config import CANONICAL_JOB_ARMS, CANONICAL_JOB_OFFSETS
from ..strategy.filters import (
    get_event_metadata,
    get_proven_resolution,
    get_strict_binary_yes,
)


logger = logging.getLogger(__name__)
_DRAWDOWN_KILL_SWITCH_KEY = "drawdown_kill_switch"
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


@dataclass(frozen=True)
class DrawdownEvaluation:
    """Strict terminal economic path for the current canonical cohort."""

    economic_pnl: float
    tripped: bool
    trip_economic_pnl: Optional[float] = None
    source_terminal_run_id: Optional[str] = None
    terminal_trade_count: int = 0


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
        recent_signal = (
            self.session.query(Trade.id)
            .filter(
                Trade.condition_id == condition_id,
                Trade.status.in_(
                    (
                        TradeStatus.HOLDING,
                        TradeStatus.PENDING_BUY,
                        TradeStatus.PENDING_SELL,
                        TradeStatus.COMPLETED,
                        TradeStatus.RESOLVED,
                    )
                ),
                func.coalesce(
                    Trade.signal_timestamp_at_entry,
                    Trade.buy_timestamp,
                )
                >= cutoff,
            )
            .first()
        )
        if recent_signal:
            return False, "signal_cooldown"
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

    def can_enter_event(
        self,
        event_id: Optional[str],
        cooldown_hours: float,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """Enforce one position per event and cooldown from the prior signal."""
        if not event_id:
            return False, "missing_event_id"
        if self.get_event_position_count(event_id) > 0:
            return False, "event_holding"
        now = now or datetime.utcnow()
        cutoff = now - timedelta(hours=cooldown_hours)
        recent = (
            self.session.query(Trade.id)
            .filter(
                Trade.event_id == event_id,
                Trade.status.in_(
                    (
                        TradeStatus.HOLDING,
                        TradeStatus.PENDING_BUY,
                        TradeStatus.PENDING_SELL,
                        TradeStatus.COMPLETED,
                        TradeStatus.RESOLVED,
                    )
                ),
                func.coalesce(
                    Trade.signal_timestamp_at_entry,
                    Trade.buy_timestamp,
                )
                >= cutoff,
            )
            .first()
        )
        if recent:
            return False, "event_signal_cooldown"
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

    @staticmethod
    def _validate_drawdown_kill_switch_payload(
        payload: Any,
    ) -> Dict[str, Any]:
        """Validate the durable latch instead of treating corruption as OFF."""
        if not isinstance(payload, dict):
            raise RuntimeError(
                "drawdown kill-switch state가 객체가 아닙니다; 신규 진입을 "
                "fail closed 합니다"
            )
        required = {
            "schema_version",
            "tripped",
            "tripped_at",
            "tripped_run_id",
            "economic_pnl",
            "loss_limit_usdc",
            "experiment_capital_usdc",
            "max_drawdown_stop",
        }
        if set(payload) != required:
            raise RuntimeError(
                "drawdown kill-switch state 필드가 계약과 다릅니다; 신규 "
                "진입을 fail closed 합니다"
            )
        if payload.get("schema_version") != 1 or payload.get("tripped") is not True:
            raise RuntimeError(
                "drawdown kill-switch state 값이 유효하지 않습니다; 신규 "
                "진입을 fail closed 합니다"
            )
        try:
            tripped_at = datetime.fromisoformat(
                str(payload["tripped_at"]).replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                "drawdown kill-switch tripped_at이 유효하지 않습니다; 신규 "
                "진입을 fail closed 합니다"
            ) from error
        if tripped_at.tzinfo is None:
            raise RuntimeError(
                "drawdown kill-switch tripped_at에 timezone이 없습니다; 신규 "
                "진입을 fail closed 합니다"
            )
        if not str(payload.get("tripped_run_id") or "").strip():
            raise RuntimeError(
                "drawdown kill-switch run 증거가 없습니다; 신규 진입을 "
                "fail closed 합니다"
            )
        numeric: Dict[str, float] = {}
        for name in (
            "economic_pnl",
            "loss_limit_usdc",
            "experiment_capital_usdc",
            "max_drawdown_stop",
        ):
            value = payload.get(name)
            if isinstance(value, bool):
                raise RuntimeError(
                    "drawdown kill-switch 수치가 유효하지 않습니다; 신규 "
                    "진입을 fail closed 합니다"
                )
            try:
                number = float(value)
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    "drawdown kill-switch 수치가 유효하지 않습니다; 신규 "
                    "진입을 fail closed 합니다"
                ) from error
            if not math.isfinite(number):
                raise RuntimeError(
                    "drawdown kill-switch 수치가 유효하지 않습니다; 신규 "
                    "진입을 fail closed 합니다"
                )
            numeric[name] = number
        if (
            numeric["loss_limit_usdc"] <= 0
            or numeric["experiment_capital_usdc"] <= 0
            or not 0 < numeric["max_drawdown_stop"] <= 1
            or not math.isclose(
                numeric["loss_limit_usdc"],
                numeric["experiment_capital_usdc"]
                * numeric["max_drawdown_stop"],
                rel_tol=0,
                abs_tol=1e-9,
            )
            or numeric["economic_pnl"] > -numeric["loss_limit_usdc"] + 1e-9
        ):
            raise RuntimeError(
                "drawdown kill-switch trip 수치가 stopping rule과 맞지 "
                "않습니다; 신규 진입을 fail closed 합니다"
            )
        return dict(payload)

    def get_drawdown_kill_switch(self) -> Optional[Dict[str, Any]]:
        """Return the validated permanent latch, or ``None`` before trip."""
        row = self.session.get(ExperimentState, _DRAWDOWN_KILL_SWITCH_KEY)
        if row is None:
            return None
        try:
            payload = json.loads(row.value_json)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "drawdown kill-switch JSON이 손상되었습니다; 신규 진입을 "
                "fail closed 합니다"
            ) from error
        return self._validate_drawdown_kill_switch_payload(payload)

    def latch_drawdown_kill_switch(
        self,
        *,
        economic_pnl: float,
        loss_limit_usdc: float,
        experiment_capital_usdc: float,
        max_drawdown_stop: float,
        run_id: str,
        tripped_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Persist a proven SUCCESS-run trip permanently.

        Runtime collection uses the two-phase ``stage``/``finalize`` methods
        below.  This direct method is retained for migrations and tests, but it
        still requires the supplied source run to be terminal SUCCESS.
        """
        existing = self.get_drawdown_kill_switch()
        if existing is not None:
            return existing
        status = self.session.execute(
            text("SELECT status FROM run_audits WHERE run_id = :run_id"),
            {"run_id": str(run_id or "").strip()},
        ).scalar_one_or_none()
        if status != "SUCCESS":
            raise RuntimeError(
                "drawdown permanent latch는 SUCCESS source run만 참조할 수 "
                "있습니다"
            )
        observed_at = tripped_at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        else:
            observed_at = observed_at.astimezone(timezone.utc)
        payload: Dict[str, Any] = {
            "schema_version": 1,
            "tripped": True,
            "tripped_at": observed_at.isoformat().replace("+00:00", "Z"),
            "tripped_run_id": str(run_id or "").strip(),
            "economic_pnl": float(economic_pnl),
            "loss_limit_usdc": float(loss_limit_usdc),
            "experiment_capital_usdc": float(experiment_capital_usdc),
            "max_drawdown_stop": float(max_drawdown_stop),
        }
        validated = self._validate_drawdown_kill_switch_payload(payload)
        self.session.add(
            ExperimentState(
                key=_DRAWDOWN_KILL_SWITCH_KEY,
                value_json=json.dumps(
                    validated,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                updated_at=observed_at.replace(tzinfo=None),
            )
        )
        self.session.commit()
        return validated

    @staticmethod
    def _pending_drawdown_key(run_id: str) -> str:
        normalized = str(run_id or "").strip()
        if not normalized:
            raise RuntimeError("drawdown detection run ID가 없습니다")
        return f"drawdown_kill_switch_pending:{normalized}"

    def stage_drawdown_kill_switch(
        self,
        *,
        detection_run_id: str,
        source_terminal_run_id: str,
        economic_pnl: float,
        loss_limit_usdc: float,
        experiment_capital_usdc: float,
        max_drawdown_stop: float,
        detected_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Stage the first crossing without creating a failed-run latch."""
        existing = self.get_drawdown_kill_switch()
        if existing is not None:
            return existing
        detection_status = self.session.execute(
            text("SELECT status FROM run_audits WHERE run_id = :run_id"),
            {"run_id": str(detection_run_id or "").strip()},
        ).scalar_one_or_none()
        source_status = self.session.execute(
            text("SELECT status FROM run_audits WHERE run_id = :run_id"),
            {"run_id": str(source_terminal_run_id or "").strip()},
        ).scalar_one_or_none()
        if detection_status != "RUNNING" or source_status != "SUCCESS":
            raise RuntimeError(
                "drawdown pending latch는 RUNNING detector와 SUCCESS terminal "
                "source를 모두 요구합니다"
            )
        observed_at = detected_at or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        else:
            observed_at = observed_at.astimezone(timezone.utc)
        permanent_shape = {
            "schema_version": 1,
            "tripped": True,
            "tripped_at": observed_at.isoformat().replace("+00:00", "Z"),
            "tripped_run_id": str(source_terminal_run_id).strip(),
            "economic_pnl": float(economic_pnl),
            "loss_limit_usdc": float(loss_limit_usdc),
            "experiment_capital_usdc": float(experiment_capital_usdc),
            "max_drawdown_stop": float(max_drawdown_stop),
        }
        self._validate_drawdown_kill_switch_payload(permanent_shape)
        payload = {
            "schema_version": 1,
            "state": "PENDING",
            "detection_run_id": str(detection_run_id).strip(),
            "source_terminal_run_id": str(source_terminal_run_id).strip(),
            "detected_at": observed_at.isoformat().replace("+00:00", "Z"),
            "economic_pnl": float(economic_pnl),
            "loss_limit_usdc": float(loss_limit_usdc),
            "experiment_capital_usdc": float(experiment_capital_usdc),
            "max_drawdown_stop": float(max_drawdown_stop),
        }
        key = self._pending_drawdown_key(detection_run_id)
        row = self.session.get(ExperimentState, key)
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if row is not None:
            if row.value_json != canonical:
                raise RuntimeError("drawdown pending latch가 불변 계약과 다릅니다")
            return payload
        self.session.add(
            ExperimentState(
                key=key,
                value_json=canonical,
                updated_at=observed_at.replace(tzinfo=None),
            )
        )
        self.session.commit()
        return payload

    def finalize_staged_drawdown_kill_switch(
        self,
        detection_run_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Atomically promote a pending latch only after detector SUCCESS."""
        existing = self.get_drawdown_kill_switch()
        key = self._pending_drawdown_key(detection_run_id)
        row = self.session.get(ExperimentState, key)
        if row is None:
            return existing
        detection_status = self.session.execute(
            text("SELECT status FROM run_audits WHERE run_id = :run_id"),
            {"run_id": str(detection_run_id).strip()},
        ).scalar_one_or_none()
        if detection_status != "SUCCESS":
            raise RuntimeError(
                "drawdown pending latch는 detector SUCCESS 이후에만 확정됩니다"
            )
        try:
            pending = json.loads(row.value_json)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("drawdown pending latch JSON이 손상되었습니다") from error
        required = {
            "schema_version",
            "state",
            "detection_run_id",
            "source_terminal_run_id",
            "detected_at",
            "economic_pnl",
            "loss_limit_usdc",
            "experiment_capital_usdc",
            "max_drawdown_stop",
        }
        if (
            not isinstance(pending, dict)
            or set(pending) != required
            or pending.get("schema_version") != 1
            or pending.get("state") != "PENDING"
            or pending.get("detection_run_id") != str(detection_run_id).strip()
        ):
            raise RuntimeError("drawdown pending latch 계약이 유효하지 않습니다")
        source_run_id = str(pending["source_terminal_run_id"] or "").strip()
        source_status = self.session.execute(
            text("SELECT status FROM run_audits WHERE run_id = :run_id"),
            {"run_id": source_run_id},
        ).scalar_one_or_none()
        if source_status != "SUCCESS":
            raise RuntimeError("drawdown terminal source run이 SUCCESS가 아닙니다")
        payload = {
            "schema_version": 1,
            "tripped": True,
            "tripped_at": pending["detected_at"],
            "tripped_run_id": source_run_id,
            "economic_pnl": pending["economic_pnl"],
            "loss_limit_usdc": pending["loss_limit_usdc"],
            "experiment_capital_usdc": pending["experiment_capital_usdc"],
            "max_drawdown_stop": pending["max_drawdown_stop"],
        }
        validated = self._validate_drawdown_kill_switch_payload(payload)
        if existing is None:
            self.session.add(
                ExperimentState(
                    key=_DRAWDOWN_KILL_SWITCH_KEY,
                    value_json=json.dumps(
                        validated,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    updated_at=datetime.utcnow(),
                )
            )
            self.session.flush()
        self.session.delete(row)
        self.session.commit()
        return existing or validated

    def discard_staged_drawdown_kill_switch(self, detection_run_id: str) -> bool:
        """Remove a detector-local pending row after the run becomes FAILED."""
        key = self._pending_drawdown_key(detection_run_id)
        row = self.session.get(ExperimentState, key)
        if row is None:
            return False
        status = self.session.execute(
            text("SELECT status FROM run_audits WHERE run_id = :run_id"),
            {"run_id": str(detection_run_id).strip()},
        ).scalar_one_or_none()
        if status != "FAILED":
            raise RuntimeError(
                "drawdown pending latch는 FAILED detector에서만 폐기할 수 있습니다"
            )
        self.session.delete(row)
        self.session.commit()
        return True

    def reconcile_staged_drawdown_kill_switch(
        self,
        *,
        current_detection_run_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Finalize prior SUCCESS stages and remove failed/stale detector rows."""
        rows = (
            self.session.query(ExperimentState)
            .filter(
                ExperimentState.key.like("drawdown_kill_switch_pending:%")
            )
            .all()
        )
        result = {"finalized": 0, "discarded": 0, "current": 0}
        for row in rows:
            detection_run_id = row.key.split(":", 1)[1]
            if detection_run_id == current_detection_run_id:
                result["current"] += 1
                continue
            status = self.session.execute(
                text("SELECT status FROM run_audits WHERE run_id = :run_id"),
                {"run_id": detection_run_id},
            ).scalar_one_or_none()
            if status == "SUCCESS":
                self.finalize_staged_drawdown_kill_switch(detection_run_id)
                result["finalized"] += 1
                continue
            # A per-job OS lock proves no older RUNNING process is still
            # writing this DB.  Such a row is stale crash evidence, not a
            # permanent stopping decision.
            self.session.delete(row)
            self.session.commit()
            result["discarded"] += 1
        return result

    def strict_terminal_economic_path(
        self,
        *,
        current_run_id_value: str,
        loss_limit_usdc: float,
    ) -> DrawdownEvaluation:
        """Compute first drawdown crossing from finite same-cohort SUCCESS rows."""
        current = self.session.execute(
            text(
                """
                SELECT config_hash, mode, job_name, status
                FROM run_audits
                WHERE run_id = :run_id
                  AND strategy_name = 'golden-kiwi'
                """
            ),
            {"run_id": current_run_id_value},
        ).mappings().one_or_none()
        if current is None or current["status"] != "RUNNING":
            raise RuntimeError("strict drawdown 계산에는 current RUNNING run이 필요합니다")
        rows = self.session.execute(
            text(
                """
                SELECT trades.id, trades.status, trades.hypothetical_pnl,
                       trades.settlement_pnl_assumption,
                       COALESCE(trades.sell_timestamp,
                                trades.resolution_observed_at) AS terminal_at,
                       trades.exit_run_id AS terminal_run_id
                FROM trades
                JOIN run_audits AS entry_run
                  ON entry_run.run_id = trades.entry_run_id
                JOIN run_audits AS terminal_run
                  ON terminal_run.run_id = trades.exit_run_id
                WHERE trades.status IN ('COMPLETED', 'RESOLVED')
                  AND entry_run.status = 'SUCCESS'
                  AND terminal_run.status = 'SUCCESS'
                  AND entry_run.strategy_name = 'golden-kiwi'
                  AND terminal_run.strategy_name = 'golden-kiwi'
                  AND entry_run.config_hash = :config_hash
                  AND entry_run.mode = :mode
                  AND entry_run.job_name = :job_name
                  AND terminal_run.config_hash = :config_hash
                  AND terminal_run.mode = :mode
                  AND terminal_run.job_name = :job_name
                ORDER BY terminal_at, trades.id
                """
            ),
            {
                "config_hash": current["config_hash"],
                "mode": current["mode"],
                "job_name": current["job_name"],
            },
        ).mappings().all()
        total = 0.0
        for index, row in enumerate(rows, start=1):
            status = str(row["status"] or "").upper()
            value = (
                row["hypothetical_pnl"]
                if status == "COMPLETED"
                else row["settlement_pnl_assumption"]
            )
            terminal_at = row["terminal_at"]
            terminal_run_id = str(row["terminal_run_id"] or "").strip()
            if (
                value is None
                or isinstance(value, bool)
                or terminal_at is None
                or not terminal_run_id
            ):
                raise RuntimeError(
                    f"terminal trade {row['id']}의 strict P&L 증거가 불완전합니다"
                )
            try:
                contribution = float(value)
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    f"terminal trade {row['id']}의 P&L이 수치가 아닙니다"
                ) from error
            if not math.isfinite(contribution):
                raise RuntimeError(
                    f"terminal trade {row['id']}의 P&L이 finite가 아닙니다"
                )
            total += contribution
            if total <= -loss_limit_usdc + 1e-9:
                return DrawdownEvaluation(
                    economic_pnl=total,
                    tripped=True,
                    trip_economic_pnl=total,
                    source_terminal_run_id=terminal_run_id,
                    terminal_trade_count=index,
                )
        return DrawdownEvaluation(
            economic_pnl=total,
            tripped=False,
            terminal_trade_count=len(rows),
        )

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
        catalog_values: Dict[str, Any] = {}
        if market is not None:
            self._upsert_market_catalog(condition_id, market)
            events = market.get("events") or []
            event = (
                events[0]
                if isinstance(events, list)
                and events
                and isinstance(events[0], dict)
                else {}
            )
            event_meta = get_event_metadata(market)
            tags = market.get("tags") or []

            def bool_int(value: Any) -> Optional[int]:
                return None if not isinstance(value, bool) else int(value)

            catalog_values = {
                "catalog_event_id": event_meta["event_id"],
                "catalog_event_slug": event_meta["event_slug"],
                "catalog_event_market_count": (
                    len(event.get("markets") or []) or None
                ),
                "catalog_end_date": market.get("endDate"),
                "catalog_outcomes_json": json.dumps(
                    market.get("outcomes") or [],
                    ensure_ascii=False,
                ),
                "catalog_outcome_prices_json": json.dumps(
                    market.get("outcomePrices") or []
                ),
                "catalog_token_ids_json": json.dumps(
                    market.get("clobTokenIds") or []
                ),
                "catalog_tags_json": json.dumps(
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
                "catalog_neg_risk": bool_int(market.get("negRisk")),
                "catalog_active": bool_int(market.get("active")),
                "catalog_closed": bool_int(market.get("closed")),
                "catalog_accepting_orders": bool_int(
                    market.get("acceptingOrders")
                ),
                "catalog_enable_order_book": bool_int(
                    market.get("enableOrderBook")
                ),
                "catalog_source_updated_at": market.get("updatedAt"),
            }
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
            **catalog_values,
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

    def get_entry_lineage_snapshots(
        self,
        condition_id: str,
        since: datetime,
        run_id: str,
    ) -> List[MarketSnapshot]:
        """Return current RUNNING row plus same-cohort successful prior rows.

        Every included run must have a cursor-complete sweep.  The current row
        cannot yet be SUCCESS because entry occurs before ``RunAudit.succeed``.
        """
        current = self.session.execute(
            text(
                """
                SELECT config_hash, mode, job_name, status
                FROM run_audits
                WHERE run_id = :run_id
                  AND strategy_name = 'golden-kiwi'
                """
            ),
            {"run_id": run_id},
        ).mappings().one_or_none()
        if current is None or current["status"] != "RUNNING":
            return []
        rows = self.session.execute(
            text(
                """
                SELECT snapshots.id
                FROM market_snapshots AS snapshots
                JOIN run_audits AS runs ON runs.run_id = snapshots.run_id
                WHERE snapshots.condition_id = :condition_id
                  AND snapshots.timestamp >= :since
                  AND runs.strategy_name = 'golden-kiwi'
                  AND EXISTS (
                      SELECT 1
                      FROM market_sweeps AS sweeps
                      WHERE sweeps.run_id = snapshots.run_id
                        AND sweeps.cursor_complete = 1
                  )
                  AND (
                      (
                          snapshots.run_id = :run_id
                          AND runs.status = 'RUNNING'
                      )
                      OR (
                          snapshots.run_id <> :run_id
                          AND runs.status = 'SUCCESS'
                          AND runs.config_hash = :config_hash
                          AND runs.mode = :mode
                          AND runs.job_name = :job_name
                      )
                  )
                ORDER BY snapshots.timestamp, snapshots.id
                """
            ),
            {
                "condition_id": condition_id,
                "since": since,
                "run_id": run_id,
                "config_hash": current["config_hash"],
                "mode": current["mode"],
                "job_name": current["job_name"],
            },
        ).all()
        ids = [int(row[0]) for row in rows]
        if not ids:
            return []
        by_id = {
            row.id: row
            for row in self.session.query(MarketSnapshot)
            .filter(MarketSnapshot.id.in_(ids))
            .all()
        }
        return [by_id[row_id] for row_id in ids if row_id in by_id]

    def invalidate_non_successful_run_evidence(
        self,
        *,
        exclude_run_id: Optional[str] = None,
        only_run_id: Optional[str] = None,
    ) -> int:
        """Quarantine entries and reopen exits from FAILED/stale RUNNING runs."""
        statuses = {
            str(row[0]): str(row[1])
            for row in self.session.execute(
                text("SELECT run_id, status FROM run_audits")
            ).all()
        }
        changed = 0
        query = self.session.query(Trade).filter(
            or_(Trade.entry_run_id.isnot(None), Trade.exit_run_id.isnot(None))
        )
        if only_run_id is not None:
            query = query.filter(
                or_(
                    Trade.entry_run_id == only_run_id,
                    Trade.exit_run_id == only_run_id,
                )
            )
        for trade in query.all():
            if (
                trade.entry_run_id
                and trade.entry_run_id != exclude_run_id
                and statuses.get(trade.entry_run_id) != "SUCCESS"
            ):
                trade.status = TradeStatus.QUARANTINED
                trade.exit_reason = "entry_run_not_successful"
                trade.hypothetical_pnl = None
                trade.settlement_pnl_assumption = None
                trade.promotion_eligible = 0
                trade.promotion_exclusion_reason = "entry_run_not_successful"
                changed += 1
                continue
            if (
                trade.exit_run_id
                and trade.exit_run_id != exclude_run_id
                and statuses.get(trade.exit_run_id) != "SUCCESS"
            ):
                trade.status = TradeStatus.HOLDING
                trade.sell_price = None
                trade.sell_shares = None
                trade.sell_order_id = None
                trade.sell_timestamp = None
                trade.sell_probability = None
                trade.hypothetical_pnl = None
                trade.exit_reason = "failed_exit_run_reopened"
                trade.exit_run_id = None
                trade.hold_minutes_observed_at_exit = None
                trade.exit_delay_minutes = None
                trade.best_bid_at_exit = None
                trade.best_ask_at_exit = None
                trade.spread_at_exit = None
                trade.promotion_eligible = None
                trade.promotion_exclusion_reason = None
                trade.resolution_outcome = None
                trade.resolution_value = None
                trade.resolution_status = None
                trade.resolution_observed_at = None
                trade.resolution_source_updated_at = None
                trade.resolution_evidence = None
                trade.settlement_pnl_assumption = None
                trade.settlement_assumption_basis = None
                changed += 1
        if changed:
            self.session.commit()
        return changed

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
            "golden-kiwi",
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

    def ensure_experiment_contract(
        self,
        *,
        canonical_job: str,
        arm: str,
        window_start: datetime,
        window_end: datetime,
        expected_cadence_minutes: int,
        expected_offset_minute: int,
        preregistration_sha256: str,
        analyzer_version: int,
    ) -> MicroCascadeExperimentContract:
        """Insert once or require byte-for-byte semantic equality thereafter."""
        expected_jobs = {
            job: treatment[2]
            for job, treatment in CANONICAL_JOB_ARMS.items()
        }

        def naive_utc(value: datetime) -> datetime:
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError(
                    "experiment contract window는 timezone-aware UTC 시각이어야 "
                    "합니다"
                )
            return value.astimezone(timezone.utc).replace(tzinfo=None)

        normalized_job = str(canonical_job or "").strip()
        normalized_arm = str(arm or "").strip().upper()
        start = naive_utc(window_start)
        end = naive_utc(window_end)
        if expected_jobs.get(normalized_job) != normalized_arm:
            raise ValueError("canonical job과 Micro-Cascade arm 매핑이 다릅니다")
        if (
            start.second
            or start.microsecond
            or end.second
            or end.microsecond
            or end - start != timedelta(days=30)
        ):
            raise ValueError(
                "experiment contract는 정확한 UTC minute 경계의 30일 "
                "반개구간이어야 합니다"
            )
        if (
            isinstance(expected_cadence_minutes, bool)
            or int(expected_cadence_minutes) != 5
        ):
            raise ValueError("experiment cadence는 5분으로 고정됩니다")
        if not isinstance(expected_offset_minute, int) or isinstance(
            expected_offset_minute, bool
        ):
            raise ValueError("experiment cadence offset은 정수여야 합니다")
        if not 0 <= expected_offset_minute < 5:
            raise ValueError("experiment cadence offset은 0~4분이어야 합니다")
        canonical_offset = CANONICAL_JOB_OFFSETS[normalized_job]
        if expected_offset_minute != canonical_offset:
            raise ValueError(
                f"{normalized_job}의 experiment cadence offset은 "
                f"{canonical_offset}분으로 고정됩니다"
            )
        normalized_hash = str(preregistration_sha256 or "").strip().lower()
        try:
            hash_is_hex = len(normalized_hash) == 64 and int(
                normalized_hash, 16
            ) >= 0
        except ValueError:
            hash_is_hex = False
        if not hash_is_hex:
            raise ValueError("preregistration SHA-256 형식이 유효하지 않습니다")
        if isinstance(analyzer_version, bool) or int(analyzer_version) != 2:
            raise ValueError("experiment analyzer version은 v2로 고정됩니다")

        values = {
            "canonical_job": normalized_job,
            "schema_version": 1,
            "analyzer_version": int(analyzer_version),
            "preregistration_sha256": normalized_hash,
            "arm": normalized_arm,
            "window_start": start,
            "window_end": end,
            "expected_cadence_minutes": int(expected_cadence_minutes),
            "expected_offset_minute": expected_offset_minute,
        }
        existing = self.session.get(
            MicroCascadeExperimentContract, normalized_job
        )
        if existing is not None:
            actual = {
                key: getattr(existing, key)
                for key in values
            }
            if actual != values:
                raise RuntimeError(
                    "persisted Micro-Cascade experiment contract와 현재 "
                    "collection 설정이 다릅니다"
                )
            return existing
        other = self.session.query(MicroCascadeExperimentContract).first()
        if other is not None:
            raise RuntimeError(
                "canonical job이 다른 experiment contract DB를 재사용할 수 "
                "없습니다"
            )
        contract = MicroCascadeExperimentContract(**values)
        self.session.add(contract)
        self.session.commit()
        return contract

    def latest_successful_raw_selected_at(
        self,
        event_id: str,
    ) -> Optional[datetime]:
        """Return the last successful raw selection for event cooldown."""
        value = self.session.execute(
            text(
                """
                SELECT decisions.scan_evaluated_at
                FROM micro_cascade_signal_decisions AS decisions
                JOIN run_audits AS runs ON runs.run_id = decisions.run_id
                WHERE decisions.event_id = :event_id
                  AND decisions.raw_selected = 1
                  AND decisions.collection_eligible = 1
                  AND runs.status = 'SUCCESS'
                  AND runs.strategy_name = 'golden-kiwi'
                ORDER BY decisions.scan_evaluated_at DESC, decisions.id DESC
                LIMIT 1
                """
            ),
            {"event_id": str(event_id)},
        ).scalar_one_or_none()
        return self._coerce_sqlite_datetime(value)

    def append_signal_decisions(
        self,
        decisions: List[Dict[str, Any]],
    ) -> List[MicroCascadeSignalDecision]:
        """Append the fully classified funnel once; UPDATE/DELETE are blocked."""
        run_id = current_run_id()
        if not run_id:
            raise RuntimeError("signal decision 기록에는 current RunAudit가 필요합니다")
        rows: List[MicroCascadeSignalDecision] = []
        try:
            for payload in decisions:
                if str(payload.get("run_id") or "") != run_id:
                    raise RuntimeError("signal decision run identity가 다릅니다")
                row = MicroCascadeSignalDecision(**payload)
                self.session.add(row)
                rows.append(row)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return rows

    def append_due_followup_observations(
        self,
        markets: List[Dict[str, Any]],
        *,
        observed_at: datetime,
        fetch_market: Optional[
            Callable[[str], Optional[Dict[str, Any]]]
        ] = None,
    ) -> int:
        """Append independent raw Gamma evidence for every mature selection.

        Production passes a condition lookup callback so closed, out-of-band,
        or now-illiquid targets cannot disappear behind the main sweep's
        filters.  ``markets`` remains a deterministic fallback for tests.
        """
        observing_run_id = current_run_id()
        if not observing_run_id:
            raise RuntimeError("follow-up 기록에는 current RunAudit가 필요합니다")
        if observed_at.tzinfo is None:
            reference = observed_at.replace(tzinfo=timezone.utc)
        else:
            reference = observed_at.astimezone(timezone.utc)
        reference_naive = reference.replace(tzinfo=None)
        by_condition = {
            str(market.get("conditionId") or ""): market
            for market in markets
            if str(market.get("conditionId") or "")
        }
        due = self.session.execute(
            text(
                """
                SELECT decisions.id, decisions.condition_id,
                       decisions.scan_evaluated_at
                FROM micro_cascade_signal_decisions AS decisions
                JOIN run_audits AS source_run
                  ON source_run.run_id = decisions.run_id
                WHERE decisions.raw_selected = 1
                  AND decisions.collection_eligible = 1
                  AND source_run.status = 'SUCCESS'
                  AND datetime(decisions.scan_evaluated_at, '+60 minutes')
                      <= :observed_at
                  AND datetime(decisions.scan_evaluated_at, '+75 minutes')
                      >= :observed_at
                  AND NOT EXISTS (
                      SELECT 1
                      FROM micro_cascade_followup_observations AS prior
                      JOIN run_audits AS prior_run
                        ON prior_run.run_id = prior.observing_run_id
                      WHERE prior.decision_id = decisions.id
                        AND prior.valid_quote = 1
                        AND prior_run.status = 'SUCCESS'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM micro_cascade_followup_observations AS same_run
                      WHERE same_run.decision_id = decisions.id
                        AND same_run.observing_run_id = :observing_run_id
                  )
                ORDER BY decisions.scan_evaluated_at, decisions.id
                """
            ),
            {
                "observed_at": reference_naive,
                "observing_run_id": observing_run_id,
            },
        ).mappings().all()
        appended = 0
        try:
            for decision in due:
                signal_at = self._coerce_sqlite_datetime(
                    decision["scan_evaluated_at"]
                )
                if signal_at is None:
                    raise RuntimeError("signal decision 시각이 datetime이 아닙니다")
                target_at = signal_at + timedelta(minutes=60)
                window_end = signal_at + timedelta(minutes=75)
                condition_id = str(decision["condition_id"])
                if fetch_market is not None:
                    try:
                        market = fetch_market(condition_id)
                    except Exception as error:
                        market = None
                        lookup_error = type(error).__name__
                    else:
                        lookup_error = None
                else:
                    market = by_condition.get(condition_id)
                    lookup_error = None
                market_seen = market is not None
                condition_mismatch = (
                    market is not None
                    and market.get("conditionId") != condition_id
                )
                if lookup_error:
                    source_reason = (
                        f"gamma_condition_lookup_error:{lookup_error}"
                    )
                elif condition_mismatch:
                    source_reason = "gamma_condition_id_mismatch"
                elif market is None:
                    source_reason = (
                        "market_missing_from_gamma_condition_lookup"
                        if fetch_market is not None
                        else "market_missing_from_gamma_sweep"
                    )
                else:
                    source_reason = "market_evidence_pending_validation"
                values: Dict[str, Optional[float]] = {
                    "probability": None,
                    "best_bid": None,
                    "best_ask": None,
                    "liquidity": None,
                    "volume_24h": None,
                }
                source_updated_at = None
                source_available = False
                valid_quote = False
                observation_clock = reference
                if market is not None and not condition_mismatch:
                    raw_clock = market.get("_gammaObservedAt")
                    try:
                        parsed_clock = datetime.fromisoformat(
                            str(raw_clock).replace("Z", "+00:00")
                        )
                        if parsed_clock.tzinfo is None:
                            parsed_clock = parsed_clock.replace(tzinfo=timezone.utc)
                        observation_clock = parsed_clock.astimezone(timezone.utc)
                    except (TypeError, ValueError):
                        source_reason = "market_observation_clock_invalid"
                    else:
                        yes = get_strict_binary_yes(market)
                        probability = (
                            None if yes is None else float(yes["probability"])
                        )
                        bid = self._finite_optional(market.get("bestBid"))
                        ask = self._finite_optional(market.get("bestAsk"))
                        liquidity = self._finite_optional(market.get("liquidity"))
                        volume = self._finite_optional(market.get("volume24hr"))
                        values = {
                            "probability": probability,
                            "best_bid": bid,
                            "best_ask": ask,
                            "liquidity": liquidity,
                            "volume_24h": volume,
                        }
                        source_updated_at = market.get("updatedAt")
                        source_available = yes is not None
                        if yes is None:
                            source_reason = "market_not_strict_binary_yes"
                        elif bid is None or not 0 < bid < 1:
                            source_reason = "best_bid_missing_or_invalid"
                        elif not target_at <= observation_clock.replace(
                            tzinfo=None
                        ) <= window_end:
                            source_reason = "market_observation_outside_60_75m"
                        else:
                            source_reason = "valid_raw_gamma_followup"
                            valid_quote = True
                row = MicroCascadeFollowupObservation(
                    decision_id=int(decision["id"]),
                    observing_run_id=observing_run_id,
                    condition_id=condition_id,
                    target_at=target_at,
                    window_end=window_end,
                    observed_at=observation_clock.replace(tzinfo=None),
                    market_seen=int(market_seen),
                    source_available=int(source_available),
                    source_reason=source_reason,
                    valid_quote=int(valid_quote),
                    source_updated_at=source_updated_at,
                    **values,
                )
                self.session.add(row)
                appended += 1
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return appended

    @staticmethod
    def _finite_optional(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _coerce_sqlite_datetime(value: Any) -> Optional[datetime]:
        """Normalize DateTime values returned by ORM or textual SQLite SQL."""
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            )
        except ValueError:
            return None

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def cleanup_old_snapshots(self, days: int = 60) -> int:
        if compact_maintenance_active(self.session, "golden-kiwi"):
            return 0
        cutoff = datetime.utcnow() - timedelta(days=days)
        # Entry crossing evidence is immutable, even after the telemetry
        # retention horizon.  Build the protected set before deleting so a
        # legacy trade's inferred immediate-prior row cannot shift while the
        # DELETE statement is running.
        self.session.execute(
            text(
                "CREATE TEMP TABLE IF NOT EXISTS "
                "_polybot_kiwi_protected_snapshots "
                "(id INTEGER PRIMARY KEY) WITHOUT ROWID"
            )
        )
        self.session.execute(text("DELETE FROM _polybot_kiwi_protected_snapshots"))
        self.session.execute(
            text(
                "INSERT OR IGNORE INTO _polybot_kiwi_protected_snapshots(id) "
                "SELECT entry_snapshot_id FROM trades "
                "WHERE entry_snapshot_id IS NOT NULL"
            )
        )
        self.session.execute(
            text(
                "INSERT OR IGNORE INTO _polybot_kiwi_protected_snapshots(id) "
                "SELECT prior_snapshot_id_at_entry FROM trades "
                "WHERE prior_snapshot_id_at_entry IS NOT NULL"
            )
        )
        self.session.execute(
            text(
                "INSERT OR IGNORE INTO _polybot_kiwi_protected_snapshots(id) "
                "SELECT trend_start_snapshot_id_at_entry FROM trades "
                "WHERE trend_start_snapshot_id_at_entry IS NOT NULL"
            )
        )
        for trade_id, raw_ids in self.session.query(
            Trade.id,
            Trade.trend_snapshot_ids_json,
        ).filter(Trade.trend_snapshot_ids_json.isnot(None)):
            try:
                snapshot_ids = json.loads(raw_ids)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"Trade {trade_id}의 trend_snapshot_ids_json이 유효하지 않습니다"
                ) from error
            if (
                not isinstance(snapshot_ids, list)
                or not snapshot_ids
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value <= 0
                    for value in snapshot_ids
                )
            ):
                raise ValueError(
                    f"Trade {trade_id}의 full snapshot lineage가 유효하지 않습니다"
                )
            for snapshot_id in snapshot_ids:
                self.session.execute(
                    text(
                        "INSERT OR IGNORE INTO "
                        "_polybot_kiwi_protected_snapshots(id) VALUES (:id)"
                    ),
                    {"id": snapshot_id},
                )
        self.session.execute(
            text(
                "INSERT OR IGNORE INTO _polybot_kiwi_protected_snapshots(id) "
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
                "SELECT id FROM _polybot_kiwi_protected_snapshots)"
            ),
            {"cutoff": cutoff},
        ).rowcount
        self.session.execute(text("DROP TABLE _polybot_kiwi_protected_snapshots"))
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
        hypothetical_pnl = (
            self.session.query(func.sum(Trade.hypothetical_pnl))
            .filter(Trade.hypothetical_pnl.isnot(None))
            .scalar()
            or 0.0
        )
        settlement_pnl = (
            self.session.query(func.sum(Trade.settlement_pnl_assumption))
            .filter(Trade.settlement_pnl_assumption.isnot(None))
            .scalar()
            or 0.0
        )
        drawdown_state = self.get_drawdown_kill_switch()
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
            "hypothetical_pnl": round(hypothetical_pnl, 4),
            "settlement_pnl_assumption": round(settlement_pnl, 4),
            "research_economic_pnl": round(
                hypothetical_pnl + settlement_pnl, 4
            ),
            "drawdown_kill_switch_tripped": drawdown_state is not None,
            "drawdown_kill_switch": drawdown_state,
        }

    def append_trade_to_csv(self, trade: Trade, db_dir) -> None:
        """Append one SUCCESS-audited simulated time-exit row idempotently."""
        timestamp = trade.sell_timestamp or datetime.utcnow()
        path = Path(db_dir) / f"trades_{timestamp:%Y-%m}.csv"
        headers = [
            "id",
            "strategy_name",
            "mode",
            "entry_run_id",
            "exit_run_id",
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
            "trend_start_snapshot_id_at_entry",
            "entry_snapshot_id",
            "signal_timestamp_at_entry",
            "trend_snapshot_ids_json",
            "trend_snapshot_timestamps_json",
            "trend_persisted_prices_json",
            "trend_decision_prices_json",
            "trend_gap_minutes_json",
            "trend_decision_timestamps_json",
            "trend_decision_gap_minutes_json",
            "decision_observed_at_at_entry",
            "decision_price_source_at_entry",
            "trend_start_yes_price_at_entry",
            "confirmation_steps_at_entry",
            "cumulative_move_at_entry",
            "min_step_move_at_entry",
            "max_step_move_at_entry",
            "min_snapshot_gap_minutes_at_entry",
            "max_snapshot_gap_minutes_at_entry",
            "signal_best_bid_at_entry",
            "signal_best_ask_at_entry",
            "signal_spread_at_entry",
            "hold_minutes_target_at_entry",
            "hold_minutes_observed_at_exit",
            "exit_delay_minutes",
            "promotion_eligible",
            "promotion_exclusion_reason",
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
            if any(str(existing.get("id")) == str(trade.id) for existing in existing_rows):
                logger.info("거래 이력 CSV idempotent skip: %s trade=%s", path, trade.id)
                return
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

    def export_unmaterialized_successful_exits(self, db_dir) -> int:
        """Materialize CSV rows only after the exit run is audited SUCCESS."""
        ids = [
            int(row[0])
            for row in self.session.execute(
                text(
                    """
                    SELECT trades.id
                    FROM trades
                    JOIN run_audits ON run_audits.run_id = trades.exit_run_id
                    WHERE trades.status = 'COMPLETED'
                      AND trades.sell_timestamp IS NOT NULL
                      AND trades.csv_exported_at IS NULL
                      AND run_audits.status = 'SUCCESS'
                    ORDER BY trades.id
                    """
                )
            ).all()
        ]
        exported = 0
        for trade_id in ids:
            trade = self.session.get(Trade, trade_id)
            if trade is None:
                continue
            self.append_trade_to_csv(trade, db_dir)
            trade.csv_exported_at = datetime.utcnow()
            self.session.commit()
            exported += 1
        return exported
