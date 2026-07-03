"""Repository pattern for database operations."""
import csv
import logging
from datetime import datetime, timedelta
from pathlib import Path
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
        return self.session.get(Trade, trade_id)

    def create_trade(self, **kwargs) -> Trade:
        """Create a new trade record."""
        trade = Trade(**kwargs)
        self.session.add(trade)
        self.session.commit()
        return trade

    def update_trade(self, trade_id: int, **kwargs) -> Trade:
        """Update an existing trade."""
        trade = self.session.get(Trade, trade_id)
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

    def get_all_trades(self) -> List[Trade]:
        """Get all trades."""
        return self.session.query(Trade).all()

    def has_holding(self, condition_id: str) -> bool:
        """해당 시장에 HOLDING 포지션이 있는지 확인."""
        return self.session.query(Trade).filter(
            Trade.condition_id == condition_id,
            Trade.status == TradeStatus.HOLDING,
        ).first() is not None

    def get_reentry_block(
        self,
        condition_id: str,
        cooldown_hours: int,
        now: Optional[datetime] = None,
    ) -> Optional[str]:
        """재진입 차단 사유 확인 (영구 one-shot 제거, 쿨다운 기반).

        - HOLDING 포지션 존재 → "holding"
        - 마지막 COMPLETED sell_timestamp가 쿨다운 이내 → "cooldown_completed"
        - 마지막 skipped_at이 쿨다운 이내 → "cooldown_skipped"
        - 그 외 → None (재진입 허용)
        """
        if self.has_holding(condition_id):
            return "holding"

        now = now or datetime.utcnow()
        cutoff = now - timedelta(hours=cooldown_hours)

        last_sell = self.session.query(func.max(Trade.sell_timestamp)).filter(
            Trade.condition_id == condition_id,
            Trade.status == TradeStatus.COMPLETED,
        ).scalar()
        if last_sell is not None and last_sell >= cutoff:
            return "cooldown_completed"

        last_skip = self.session.query(func.max(SkippedMarket.skipped_at)).filter(
            SkippedMarket.condition_id == condition_id
        ).scalar()
        if last_skip is not None and last_skip >= cutoff:
            return "cooldown_skipped"

        return None

    def mark_as_skipped(self, condition_id: str, reason: str) -> SkippedMarket:
        """Mark a market as skipped (timestamp 기록, 쿨다운 후 재진입 가능)."""
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
        """Save a market snapshot (probability = YES 가격)."""
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
        """특정 마켓의 timestamp 기반 스냅샷 조회 (시간 오름차순).

        banana의 "최신 N개" 방식과 달리 시간 조건으로 조회한다.
        """
        return self.session.query(MarketSnapshot).filter(
            MarketSnapshot.condition_id == condition_id,
            MarketSnapshot.timestamp >= since,
        ).order_by(
            MarketSnapshot.timestamp.asc()
        ).all()

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
            "drift_at_buy", "consistency_at_buy", "vol_accel_at_buy",
            "buy_probability", "sell_probability",
            "market_tags",
            "strategy_name", "mode", "volume_24h_at_buy", "drift_at_exit",
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
            "drift_at_buy": trade.drift_at_buy if trade.drift_at_buy is not None else "",
            "consistency_at_buy": trade.consistency_at_buy if trade.consistency_at_buy is not None else "",
            "vol_accel_at_buy": trade.vol_accel_at_buy if trade.vol_accel_at_buy is not None else "",
            "buy_probability": trade.buy_probability or "",
            "sell_probability": trade.sell_probability or "",
            "market_tags": trade.market_tags or "",
            "strategy_name": trade.strategy_name or "",
            "mode": trade.mode or "",
            "volume_24h_at_buy": trade.volume_24h_at_buy if trade.volume_24h_at_buy is not None else "",
            "drift_at_exit": trade.drift_at_exit if trade.drift_at_exit is not None else "",
        }

        file_exists = csv_path.exists()
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

        logger.info(f"거래 이력 CSV 저장: {csv_path}")
