"""Repository pattern for database operations."""
import csv
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
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

    def get_latest_by_condition_id(self, condition_id: str) -> Optional[Trade]:
        """같은 시장의 가장 최근 trade 조회 (재진입 허용으로 다건 존재 가능)."""
        return self.session.query(Trade).filter(
            Trade.condition_id == condition_id
        ).order_by(Trade.id.desc()).first()

    def can_reenter(
        self,
        condition_id: str,
        cooldown_hours: float,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """재진입 가능 여부 판정 (§3.3 - 영구 one-shot 제거).

        - HOLDING 포지션이 있으면 불가
        - 마지막 COMPLETED sell_timestamp가 쿨다운 이내면 불가
        - 마지막 skip 기록(skipped_at)이 쿨다운 이내면 불가
        - 그 외 재진입 허용

        Returns:
            (재진입 가능 여부, 사유)
        """
        now = now or datetime.utcnow()

        holding = self.session.query(Trade).filter(
            Trade.condition_id == condition_id,
            Trade.status == TradeStatus.HOLDING,
        ).first()
        if holding is not None:
            return False, "holding"

        cutoff = now - timedelta(hours=cooldown_hours)

        last_sell = self.session.query(func.max(Trade.sell_timestamp)).filter(
            Trade.condition_id == condition_id,
            Trade.status == TradeStatus.COMPLETED,
        ).scalar()
        if last_sell is not None and last_sell >= cutoff:
            return False, "sell_cooldown"

        last_skip = self.session.query(func.max(SkippedMarket.skipped_at)).filter(
            SkippedMarket.condition_id == condition_id
        ).scalar()
        if last_skip is not None and last_skip >= cutoff:
            return False, "skip_cooldown"

        return True, "ok"

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
        """Get all trades currently in HOLDING status (EXPIRED 제외)."""
        return self.session.query(Trade).filter(
            Trade.status == TradeStatus.HOLDING
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
        """Skip 기록 추가 (skipped_at timestamp 기준으로 쿨다운 후 재평가됨)."""
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
        """Save a market snapshot (probability는 YES 가격 기준)."""
        snapshot = MarketSnapshot(
            condition_id=condition_id,
            probability=probability,
            liquidity=liquidity,
            volume_24h=volume_24h,
        )
        self.session.add(snapshot)
        self.session.commit()
        return snapshot

    def get_snapshots_since(
        self,
        condition_id: str,
        since: datetime,
    ) -> List[MarketSnapshot]:
        """특정 마켓의 since 이후 스냅샷 조회 (시간 오름차순).

        개수 기반 limit 대신 timestamp 기반 조회 (§3.2).

        Args:
            condition_id: 마켓 condition ID
            since: 이 시각 이후의 스냅샷만 반환

        Returns:
            시간순 정렬된 스냅샷 리스트 (오래된 것 먼저)
        """
        return self.session.query(MarketSnapshot).filter(
            MarketSnapshot.condition_id == condition_id,
            MarketSnapshot.timestamp >= since,
        ).order_by(
            MarketSnapshot.timestamp.asc()
        ).all()

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

        expired = self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.EXPIRED
        ).scalar() or 0

        total_pnl = self.session.query(func.sum(Trade.realized_pnl)).filter(
            Trade.realized_pnl.isnot(None)
        ).scalar() or 0.0

        skipped = self.session.query(func.count(SkippedMarket.id)).scalar() or 0

        return {
            "total_trades": total,
            "holding": holding,
            "completed": completed,
            "expired": expired,
            "skipped": skipped,
            "total_pnl": round(total_pnl, 4),
        }

    def get_position_count(self) -> int:
        """Get current number of open positions."""
        return self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.HOLDING
        ).scalar() or 0

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
