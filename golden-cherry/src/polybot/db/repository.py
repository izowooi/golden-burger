"""Repository pattern for database operations."""
import csv
import logging
import math
from polybot_observability import compact_maintenance_active
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, inspect, or_, text
from .models import Trade, TradeStatus, SkippedMarket, MarketSnapshot
from .fill_evidence import ExactFillEvidence, get_exact_order_fill_evidence
from .exposure_reservations import UNTRACKED_BUY_RESERVATIONS_SQL

logger = logging.getLogger(__name__)

OPEN_EXPOSURE_STATUSES = (
    TradeStatus.PENDING_BUY,
    TradeStatus.HOLDING,
    TradeStatus.PENDING_SELL,
    TradeStatus.QUARANTINED,
)

class TradeRepository:
    """CRUD operations for trades."""

    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, trade_id: int) -> Optional[Trade]:
        """Get trade by ID."""
        return self.session.query(Trade).get(trade_id)

    def get_by_condition_id(self, condition_id: str) -> Optional[Trade]:
        """Get trade by market condition ID."""
        return self.session.query(Trade).filter(
            Trade.condition_id == condition_id
        ).first()

    def is_already_traded(self, condition_id: str) -> bool:
        """Check if market has already been traded or skipped.

        Returns True if:
        - A trade exists for this condition_id
        - The market was previously skipped
        """
        trade = self.get_by_condition_id(condition_id)
        if trade:
            return True

        skipped = self.session.query(SkippedMarket).filter(
            SkippedMarket.condition_id == condition_id
        ).first()
        return skipped is not None

    def create_trade(self, **kwargs) -> Trade:
        """Create a new trade record."""
        trade = Trade(**kwargs)
        self.session.add(trade)
        self.session.commit()
        return trade

    def update_trade(self, trade_id: int, **kwargs) -> Trade:
        """Update an existing trade."""
        trade = self.session.query(Trade).get(trade_id)
        if trade is None:
            raise ValueError(f"Trade {trade_id} not found")

        for key, value in kwargs.items():
            if hasattr(trade, key):
                setattr(trade, key, value)

        trade.updated_at = datetime.utcnow()
        self.session.commit()
        return trade

    def get_holding_trades(self) -> List[Trade]:
        """Get all trades currently in HOLDING status."""
        return self.session.query(Trade).filter(
            Trade.status == TradeStatus.HOLDING
        ).all()

    def get_pending_buy_trades(self) -> List[Trade]:
        """Get all trades waiting for buy fill."""
        return self.session.query(Trade).filter(
            Trade.status == TradeStatus.PENDING_BUY
        ).all()

    def get_pending_sell_trades(self) -> List[Trade]:
        """Get all trades waiting for exact sell-fill reconciliation."""
        return self.session.query(Trade).filter(
            Trade.status == TradeStatus.PENDING_SELL
        ).all()

    def get_exact_buy_fill_evidence(
        self,
        order_id: Optional[str],
        token_id: Optional[str] = None,
    ) -> ExactFillEvidence:
        """Return fail-closed exact BUY fill evidence for one order."""
        return get_exact_order_fill_evidence(
            self.session,
            order_id,
            expected_side="BUY",
            expected_token_id=token_id,
        )

    def get_exact_sell_fill_evidence(
        self,
        order_id: Optional[str],
        token_id: Optional[str] = None,
    ) -> ExactFillEvidence:
        """Return fail-closed exact SELL fill evidence for one order."""
        return get_exact_order_fill_evidence(
            self.session,
            order_id,
            expected_side="SELL",
            expected_token_id=token_id,
        )

    def get_trades_by_date(self, target_date: date) -> List[Trade]:
        """Get trades executed on a specific date."""
        start = datetime.combine(target_date, datetime.min.time())
        end = datetime.combine(target_date, datetime.max.time())
        return self.session.query(Trade).filter(
            Trade.buy_timestamp >= start,
            Trade.buy_timestamp <= end
        ).all()

    def get_all_trades(self) -> List[Trade]:
        """Get all trades."""
        return self.session.query(Trade).all()

    def mark_as_skipped(self, condition_id: str, reason: str) -> SkippedMarket:
        """Mark a market as skipped."""
        skipped = SkippedMarket(condition_id=condition_id, reason=reason)
        self.session.add(skipped)
        self.session.commit()
        return skipped

    def save_snapshot(
        self,
        condition_id: str,
        probability: float,
        liquidity: float = None,
        volume_24h: float = None,
    ) -> MarketSnapshot:
        """Save a market snapshot."""
        snapshot = MarketSnapshot(
            condition_id=condition_id,
            probability=probability,
            liquidity=liquidity,
            volume_24h=volume_24h,
        )
        self.session.add(snapshot)
        self.session.commit()
        return snapshot

    def get_snapshots_for_condition(
        self,
        condition_id: str,
        limit: int = 100
    ) -> List[MarketSnapshot]:
        """특정 마켓의 스냅샷 조회 (시간순 정렬).

        Args:
            condition_id: 마켓 condition ID
            limit: 최대 조회 수 (기본 100개, 약 8시간 분량)

        Returns:
            시간순 정렬된 스냅샷 리스트 (오래된 것 먼저)
        """
        return self.session.query(MarketSnapshot).filter(
            MarketSnapshot.condition_id == condition_id
        ).order_by(
            MarketSnapshot.timestamp.asc()
        ).limit(limit).all()

    def get_latest_snapshot(
        self,
        condition_id: str
    ) -> Optional[MarketSnapshot]:
        """마켓의 최신 스냅샷 조회."""
        return self.session.query(MarketSnapshot).filter(
            MarketSnapshot.condition_id == condition_id
        ).order_by(
            MarketSnapshot.timestamp.desc()
        ).first()

    def cleanup_old_snapshots(self, days: int = 7) -> int:
        """오래된 스냅샷 정리 (디스크 공간 관리).

        Args:
            days: 보관 일수 (기본 7일)

        Returns:
            삭제된 스냅샷 수
        """
        if compact_maintenance_active(self.session, "golden-cherry"):
            return 0
        cutoff = datetime.utcnow() - timedelta(days=days)
        deleted = self.session.query(MarketSnapshot).filter(
            MarketSnapshot.timestamp < cutoff
        ).delete()
        self.session.commit()
        if deleted > 0:
            logger.info(f"오래된 스냅샷 {deleted}개 삭제 (기준: {days}일)")
        return deleted

    def get_stats(self) -> Dict[str, Any]:
        """Get trading statistics."""
        total = self.session.query(func.count(Trade.id)).scalar() or 0

        holding = self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.HOLDING
        ).scalar() or 0

        pending_buy = self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.PENDING_BUY
        ).scalar() or 0

        pending_sell = self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.PENDING_SELL
        ).scalar() or 0

        completed = self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.COMPLETED
        ).scalar() or 0

        resolved = self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.RESOLVED
        ).scalar() or 0

        quarantined = self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.QUARANTINED
        ).scalar() or 0

        exact_pnl_basis = (
            "exact_reconciled_buy_sell_confirmed_fills_net_known_fees"
        )
        total_pnl = self.session.query(func.sum(Trade.realized_pnl)).filter(
            Trade.realized_pnl.isnot(None),
            Trade.pnl_basis == exact_pnl_basis,
        ).scalar() or 0.0

        unproven_pnl = self.session.query(func.sum(Trade.realized_pnl)).filter(
            Trade.realized_pnl.isnot(None),
            or_(Trade.pnl_basis.is_(None), Trade.pnl_basis != exact_pnl_basis),
        ).scalar() or 0.0
        unproven_pnl_count = self.session.query(func.count(Trade.id)).filter(
            Trade.realized_pnl.isnot(None),
            or_(Trade.pnl_basis.is_(None), Trade.pnl_basis != exact_pnl_basis),
        ).scalar() or 0

        settlement_pnl_assumption = self.session.query(
            func.sum(Trade.settlement_pnl_assumption)
        ).filter(
            Trade.settlement_pnl_assumption.isnot(None)
        ).scalar() or 0.0

        skipped = self.session.query(func.count(SkippedMarket.id)).scalar() or 0

        exposure = self.get_exposure_summary()

        return {
            "total_trades": total,
            "holding": holding,
            "pending_buy": pending_buy,
            "pending_sell": pending_sell,
            "completed": completed,
            "resolved": resolved,
            "quarantined": quarantined,
            "skipped": skipped,
            "total_pnl": round(total_pnl, 4),
            "unproven_pnl": round(unproven_pnl, 4),
            "unproven_pnl_count": unproven_pnl_count,
            "settlement_pnl_assumption": round(settlement_pnl_assumption, 4),
            **exposure,
        }

    def get_buy_exposure_reservations(self) -> Dict[str, Any]:
        """Return live BUY submissions not linked to a managed ``Trade``.

        A process can die after its durable pre-POST intent or accepted order is
        written and before ``trades`` is updated.  Such rows reserve one position
        and their requested notional until exact terminal zero-fill or explicit
        ``NO_ORDER_CREATED`` evidence exists.  Open-order absence is deliberately
        not a release condition.
        """
        tables = set(inspect(self.session.get_bind()).get_table_names())
        if "order_submissions" not in tables:
            return {
                "untracked_buy_reservation_count": 0,
                "untracked_buy_reservation_notional_usdc": 0.0,
                "untracked_buy_unknown_outcome_count": 0,
                "untracked_buy_reconciliation_count": 0,
            }

        required = {
            "submission_id",
            "order_id",
            "side",
            "requested_price",
            "requested_size",
            "submitted_at",
            "simulation",
            "response_status",
            "latest_order_status",
            "latest_size_matched",
            "needs_reconciliation",
            "outcome_resolution",
            "outcome_resolved_at",
            "outcome_resolution_reason",
        }
        columns = {
            column["name"]
            for column in inspect(self.session.get_bind()).get_columns(
                "order_submissions"
            )
        }
        missing = required - columns
        if missing:
            raise RuntimeError(
                "order_submissions exposure schema is incomplete: "
                f"{sorted(missing)}"
            )

        rows = self.session.execute(
            text(UNTRACKED_BUY_RESERVATIONS_SQL)
        ).mappings().all()

        notional = 0.0
        unknown = 0
        reconciling = 0
        for row in rows:
            price = float(row["requested_price"])
            size = float(row["requested_size"])
            if not math.isfinite(price) or not 0 < price < 1:
                raise RuntimeError(
                    "untracked BUY reservation has invalid requested_price"
                )
            if not math.isfinite(size) or size <= 0:
                raise RuntimeError(
                    "untracked BUY reservation has invalid requested_size"
                )
            notional += price * size
            if row["response_status"] in {
                "INTENT",
                "SUBMIT_OUTCOME_UNKNOWN",
                "EVIDENCE_WRITE_FAILED",
            }:
                unknown += 1
            if int(row["needs_reconciliation"] or 0):
                reconciling += 1

        return {
            "untracked_buy_reservation_count": len(rows),
            "untracked_buy_reservation_notional_usdc": round(notional, 6),
            "untracked_buy_unknown_outcome_count": unknown,
            "untracked_buy_reconciliation_count": reconciling,
        }

    def get_exposure_summary(self) -> Dict[str, Any]:
        """Return managed plus untracked BUY capacity reservations."""
        managed_count = self.session.query(func.count(Trade.id)).filter(
            Trade.status.in_(OPEN_EXPOSURE_STATUSES)
        ).scalar() or 0
        managed_notional = self.session.query(func.sum(Trade.buy_amount)).filter(
            Trade.status.in_(OPEN_EXPOSURE_STATUSES)
        ).scalar() or 0.0
        reservations = self.get_buy_exposure_reservations()
        total_count = int(managed_count) + int(
            reservations["untracked_buy_reservation_count"]
        )
        total_notional = float(managed_notional or 0.0) + float(
            reservations["untracked_buy_reservation_notional_usdc"]
        )
        return {
            "managed_open_position_count": int(managed_count),
            "managed_open_notional_usdc": round(float(managed_notional or 0.0), 6),
            **reservations,
            "reserved_position_count": total_count,
            "reserved_open_notional_usdc": round(total_notional, 6),
        }

    def get_entry_guard(
        self,
        drawdown_floor_usdc: float,
        *,
        simulation_mode: bool = False,
    ) -> Dict[str, Any]:
        """Return a fail-closed exact-economic gate for new BUY submissions.

        Economic P&L is intentionally limited to exact confirmed BUY/SELL P&L
        and exact one-hot resolution settlement with complete BUY fee evidence.
        Legacy ``realized_pnl`` is never included. Existing position management
        does not consult this gate.
        """
        floor = float(drawdown_floor_usdc)
        if not math.isfinite(floor) or floor >= 0:
            raise ValueError("drawdown_floor_usdc must be finite and negative")
        if simulation_mode:
            return {
                "entry_allowed": True,
                "drawdown_floor_usdc": round(floor, 6),
                "exact_confirmed_sell_pnl_usdc": 0.0,
                "exact_proven_resolution_settlement_usdc": 0.0,
                "exact_economic_pnl_usdc": 0.0,
                "legacy_realized_pnl_included": False,
                "unknown_buy_evidence_count": 0,
                "incomplete_fee_evidence_count": 0,
                "resolution_evidence_gap_count": 0,
                "blockers": [],
                "simulation_guard_not_applicable": True,
            }
        exact_pnl_basis = (
            "exact_reconciled_buy_sell_confirmed_fills_net_known_fees"
        )
        exact_sell_pnl = self.session.query(func.sum(Trade.realized_pnl)).filter(
            Trade.realized_pnl.isnot(None),
            Trade.pnl_basis == exact_pnl_basis,
        ).scalar() or 0.0

        exact_settlement = 0.0
        resolution_evidence_gap_count = 0
        incomplete_fee_evidence_count = 0
        settlement_rows = self.session.query(Trade).filter(
            Trade.settlement_pnl_assumption.isnot(None)
        ).all()
        for trade in settlement_rows:
            if trade.settlement_assumption_basis != (
                "exact_confirmed_buy_remaining_position_net_known_buy_fee"
            ):
                incomplete_fee_evidence_count += 1
                continue
            evidence = self.get_exact_buy_fill_evidence(
                trade.buy_order_id, token_id=trade.token_id
            )
            stored_size = float(trade.resolution_confirmed_buy_size or 0.0)
            stored_vwap = float(trade.resolution_confirmed_buy_vwap or 0.0)
            stored_fee = trade.resolution_confirmed_buy_fee_usdc
            position_size = float(trade.resolution_position_size or 0.0)
            payout = trade.resolution_value
            assumption = float(trade.settlement_pnl_assumption)
            valid = (
                trade.status == TradeStatus.RESOLVED
                and str(trade.resolution_status or "").strip().lower() == "resolved"
                and payout in {0.0, 1.0}
                and bool(str(trade.resolution_outcome or "").strip())
                and evidence.has_reconciled_matched_fill
                and evidence.fee_complete
                and evidence.confirmed_size is not None
                and evidence.confirmed_vwap is not None
                and evidence.confirmed_fee_usdc is not None
                and stored_fee is not None
                and stored_size > 0
                and 0 < stored_vwap <= 1
                and position_size > 0
                and position_size <= stored_size + 0.010001
                and math.isclose(
                    float(evidence.confirmed_size), stored_size,
                    rel_tol=0, abs_tol=1e-6,
                )
                and math.isclose(
                    float(evidence.confirmed_vwap), stored_vwap,
                    rel_tol=0, abs_tol=1e-9,
                )
                and math.isclose(
                    float(evidence.confirmed_fee_usdc), float(stored_fee),
                    rel_tol=0, abs_tol=1e-9,
                )
            )
            if valid:
                expected = (float(payout) - stored_vwap) * position_size
                expected -= float(stored_fee) * min(1.0, position_size / stored_size)
                valid = math.isclose(
                    expected, assumption, rel_tol=0, abs_tol=1e-6
                )
            if not valid:
                resolution_evidence_gap_count += 1
                continue
            exact_settlement += assumption

        reservations = self.get_buy_exposure_reservations()
        unknown_buy_evidence_count = int(
            reservations["untracked_buy_reservation_count"]
        )
        open_trades = self.session.query(Trade).filter(
            Trade.status.in_(OPEN_EXPOSURE_STATUSES)
        ).all()
        for trade in open_trades:
            order_id = str(trade.buy_order_id or "").strip()
            if not order_id or order_id.startswith("SIM"):
                unknown_buy_evidence_count += 1
                continue
            evidence = self.get_exact_buy_fill_evidence(
                order_id, token_id=trade.token_id
            )
            if not evidence.has_reconciled_matched_fill:
                unknown_buy_evidence_count += 1
            elif not evidence.fee_complete:
                incomplete_fee_evidence_count += 1

        economic_pnl = float(exact_sell_pnl) + exact_settlement
        blockers = []
        if economic_pnl <= floor + 1e-9:
            blockers.append("exact_economic_drawdown_floor_breached")
        if unknown_buy_evidence_count:
            blockers.append("unknown_buy_evidence")
        if incomplete_fee_evidence_count:
            blockers.append("incomplete_fee_evidence")
        if resolution_evidence_gap_count:
            blockers.append("resolution_evidence_gap")
        return {
            "entry_allowed": not blockers,
            "drawdown_floor_usdc": round(floor, 6),
            "exact_confirmed_sell_pnl_usdc": round(float(exact_sell_pnl), 6),
            "exact_proven_resolution_settlement_usdc": round(exact_settlement, 6),
            "exact_economic_pnl_usdc": round(economic_pnl, 6),
            "legacy_realized_pnl_included": False,
            "unknown_buy_evidence_count": unknown_buy_evidence_count,
            "incomplete_fee_evidence_count": incomplete_fee_evidence_count,
            "resolution_evidence_gap_count": resolution_evidence_gap_count,
            "blockers": blockers,
        }

    def get_position_count(self) -> int:
        """Get all managed and untracked positions that may carry exposure."""
        return int(self.get_exposure_summary()["reserved_position_count"])

    def get_open_notional_usdc(self) -> float:
        """Return conservative managed plus untracked requested BUY notional."""
        return float(self.get_exposure_summary()["reserved_open_notional_usdc"])

    def append_trade_to_csv(self, trade: Trade, db_dir) -> None:
        """완료된 거래를 월별 CSV 파일에 추가.

        파일: data/{job_name}/trades_YYYY-MM.csv
        파일이 없으면 헤더 포함 생성, 있으면 행 추가.

        Args:
            trade: 완료된 Trade 객체
            db_dir: DB 파일이 있는 디렉토리 (Path 또는 str)
        """
        sell_ts = trade.sell_timestamp or datetime.utcnow()
        month_str = sell_ts.strftime("%Y-%m")
        csv_path = Path(db_dir) / f"trades_{month_str}.csv"

        headers = [
            "id", "question", "outcome", "market_slug",
            "buy_price", "sell_price", "realized_pnl",
            "buy_timestamp", "sell_timestamp",
            "exit_reason", "entry_reason",
            "hours_until_resolution_at_buy",
            "entry_time_reference", "hours_until_entry_deadline_at_buy",
            "market_game_start_time", "minutes_until_game_start_at_buy",
            "sports_market_type",
            "sports_phase_at_buy",
            "buy_probability", "sell_probability",
            "market_tags",
        ]

        row = {
            "id": trade.id,
            "question": trade.question,
            "outcome": trade.outcome,
            "market_slug": trade.market_slug or "",
            "buy_price": trade.buy_price,
            "sell_price": trade.sell_price or "",
            "realized_pnl": round(trade.realized_pnl, 6) if trade.realized_pnl is not None else "",
            "buy_timestamp": trade.buy_timestamp.isoformat() if trade.buy_timestamp else "",
            "sell_timestamp": trade.sell_timestamp.isoformat() if trade.sell_timestamp else "",
            "exit_reason": trade.exit_reason or "",
            "entry_reason": trade.entry_reason or "",
            "hours_until_resolution_at_buy": trade.hours_until_resolution_at_buy or "",
            "entry_time_reference": trade.entry_time_reference or "",
            "hours_until_entry_deadline_at_buy": trade.hours_until_entry_deadline_at_buy or "",
            "market_game_start_time": (
                trade.market_game_start_time.isoformat()
                if trade.market_game_start_time else ""
            ),
            "minutes_until_game_start_at_buy": trade.minutes_until_game_start_at_buy or "",
            "sports_market_type": trade.sports_market_type or "",
            "sports_phase_at_buy": trade.sports_phase_at_buy or "",
            "buy_probability": trade.buy_probability or "",
            "sell_probability": trade.sell_probability or "",
            "market_tags": trade.market_tags or "",
        }

        file_exists = csv_path.exists()
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

        logger.info(f"거래 이력 CSV 저장: {csv_path}")
