"""Repository pattern for database operations."""
import csv
import logging
from datetime import datetime, date, timedelta
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
        return self.session.query(Trade).get(trade_id)

    def has_holding(self, condition_id: str) -> bool:
        """해당 시장에 현재 보유(HOLDING) 포지션이 있는지 확인."""
        count = self.session.query(func.count(Trade.id)).filter(
            Trade.condition_id == condition_id,
            Trade.status == TradeStatus.HOLDING,
        ).scalar() or 0
        return count > 0

    def is_in_reentry_cooldown(
        self,
        condition_id: str,
        cooldown_hours: float,
        now: Optional[datetime] = None,
    ) -> bool:
        """재진입 쿨다운 판정 (영구 one-shot 대체).

        마지막 COMPLETED 거래의 sell_timestamp 또는 skipped_markets의
        skipped_at이 cooldown_hours 이내면 True (재진입 금지).

        Args:
            condition_id: 마켓 condition ID
            cooldown_hours: 쿨다운 시간
            now: 기준 시각 (기본: utcnow, 테스트 주입용)

        Returns:
            쿨다운 중이면 True
        """
        if now is None:
            now = datetime.utcnow()
        cutoff = now - timedelta(hours=cooldown_hours)

        recent_sell = self.session.query(Trade).filter(
            Trade.condition_id == condition_id,
            Trade.status == TradeStatus.COMPLETED,
            Trade.sell_timestamp.isnot(None),
            Trade.sell_timestamp >= cutoff,
        ).first()
        if recent_sell:
            return True

        recent_skip = self.session.query(SkippedMarket).filter(
            SkippedMarket.condition_id == condition_id,
            SkippedMarket.skipped_at >= cutoff,
        ).first()
        return recent_skip is not None

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
        """시장을 skip 기록 (쿨다운 기반 - 영구 금지가 아님)."""
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
        """마켓 스냅샷 저장 (probability = YES 가격 기준)."""
        snapshot = MarketSnapshot(
            condition_id=condition_id,
            probability=probability,
            liquidity=liquidity,
            volume_24h=volume_24h,
        )
        self.session.add(snapshot)
        self.session.commit()
        return snapshot

    def get_recent_snapshots(
        self,
        condition_id: str,
        hours_back: float,
        now: Optional[datetime] = None,
    ) -> List[MarketSnapshot]:
        """특정 마켓의 최근 hours_back 시간 내 스냅샷 조회 (시간순 오름차순).

        개수 기반이 아닌 timestamp 기반 조회 (banana 윈도우 버그 수정).

        Args:
            condition_id: 마켓 condition ID
            hours_back: 조회 시간 범위
            now: 기준 시각 (기본: utcnow)

        Returns:
            시간순 정렬된 스냅샷 리스트 (오래된 것 먼저)
        """
        if now is None:
            now = datetime.utcnow()
        cutoff = now - timedelta(hours=hours_back)
        return self.session.query(MarketSnapshot).filter(
            MarketSnapshot.condition_id == condition_id,
            MarketSnapshot.timestamp >= cutoff,
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
            days: 보관 일수 (기본 7일 - 전략 lookback 24h의 3배 이상)

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
            "yes_price_at_buy",
            "market_tags",
            # 신규 컬럼은 맨 뒤에 둔다: 기존 월 CSV 파일에 append돼도
            # 구 헤더의 컬럼 정렬이 깨지지 않는다 (추가 값만 뒤에 붙음)
            "volume_24h_at_buy", "yes_price_at_exit",
            "strategy_name", "mode",
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
            "yes_price_at_buy": trade.yes_price_at_buy or "",
            "volume_24h_at_buy": trade.volume_24h_at_buy if trade.volume_24h_at_buy is not None else "",
            "yes_price_at_exit": trade.yes_price_at_exit if trade.yes_price_at_exit is not None else "",
            "strategy_name": trade.strategy_name or "",
            "mode": trade.mode or "",
            "market_tags": trade.market_tags or "",
        }

        file_exists = csv_path.exists()
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

        logger.info(f"거래 이력 CSV 저장: {csv_path}")
