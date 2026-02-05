"""Repository pattern for database operations."""
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func
from .models import Trade, TradeStatus, SkippedMarket, MarketSnapshot

logger = logging.getLogger(__name__)


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
        """특정 마켓의 최근 스냅샷 조회 (시간순 정렬).

        Args:
            condition_id: 마켓 condition ID
            limit: 최대 조회 수 (기본 100개, 약 8시간 분량)

        Returns:
            시간순 정렬된 스냅샷 리스트 (오래된 것 먼저)
        """
        # 최근 limit개를 가져오기 위해 desc로 정렬 후 limit, 다시 asc로 정렬
        snapshots = self.session.query(MarketSnapshot).filter(
            MarketSnapshot.condition_id == condition_id
        ).order_by(
            MarketSnapshot.timestamp.desc()  # 최신 것 먼저
        ).limit(limit).all()

        # 시간순 정렬 (오래된 것 먼저)로 반환
        return list(reversed(snapshots))

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

        completed = self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.COMPLETED
        ).scalar() or 0

        total_pnl = self.session.query(func.sum(Trade.realized_pnl)).filter(
            Trade.realized_pnl.isnot(None)
        ).scalar() or 0.0

        skipped = self.session.query(func.count(SkippedMarket.id)).scalar() or 0

        return {
            "total_trades": total,
            "holding": holding,
            "completed": completed,
            "skipped": skipped,
            "total_pnl": round(total_pnl, 4),
        }

    def get_position_count(self) -> int:
        """Get current number of open positions."""
        return self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.HOLDING
        ).scalar() or 0
