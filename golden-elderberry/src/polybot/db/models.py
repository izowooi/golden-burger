"""SQLite database models for trade tracking."""
import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Enum, create_engine, text
)
from sqlalchemy.orm import declarative_base, sessionmaker
from polybot_observability import SQLiteMaintenanceRequirements, prepare_database

Base = declarative_base()

# 봇 식별 상수 - 교차 봇 UNION 쿼리용 (A/B 포스트모템 계약)
STRATEGY_NAME = "elderberry"


class TradeStatus(enum.Enum):
    """Trade lifecycle status."""
    PENDING_BUY = "pending_buy"      # Waiting for buy order to fill
    HOLDING = "holding"              # Position held
    PENDING_SELL = "pending_sell"    # Waiting for sell order to fill
    COMPLETED = "completed"          # Trade closed with profit/loss
    SKIPPED = "skipped"              # Skipped due to rapid price move
    EXPIRED = "expired"              # 시장이 해결됐지만 청산 못함 (수동 redeem 필요)
    UNFILLED = "unfilled"            # 매수 GTC가 체결된 적 없음이 확인된 유령 포지션
                                     # (매도 시 balance 0 거절 -> 재시도 중단, P&L 제외)


class Trade(Base):
    """Trade record for tracking positions.

    condition_id는 unique가 아니다 - 재진입 허용(쿨다운 기반)이므로
    같은 시장에 여러 거래 레코드가 존재할 수 있다.
    """
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Market information
    condition_id = Column(String, index=True, nullable=False)
    market_slug = Column(String)
    question = Column(String)
    outcome = Column(String)  # "Yes" or "No"
    token_id = Column(String, nullable=False)

    # Buy information
    buy_price = Column(Float, nullable=True)
    buy_amount = Column(Float, nullable=True)  # USDC spent
    buy_shares = Column(Float, nullable=True)  # Shares received
    buy_order_id = Column(String, nullable=True)
    buy_timestamp = Column(DateTime, nullable=True)
    buy_probability = Column(Float, nullable=True)

    # Sell information
    sell_price = Column(Float, nullable=True)
    sell_shares = Column(Float, nullable=True)
    sell_order_id = Column(String, nullable=True)
    sell_timestamp = Column(DateTime, nullable=True)
    sell_probability = Column(Float, nullable=True)

    # Profit/Loss
    realized_pnl = Column(Float, nullable=True)

    # Status
    status = Column(Enum(TradeStatus), default=TradeStatus.PENDING_BUY)

    # Entry/Exit reasons (panic fade strategy)
    entry_reason = Column(String, nullable=True)   # "panic_fade_ref0.82_drop0.15"
    exit_reason = Column(String, nullable=True)    # "take_profit", "stop_loss", "max_holding", "time_exit", "resolved_unredeemed", "buy_unfilled"

    # Panic fade strategy data
    ref_price_at_buy = Column(Float, nullable=True)   # 급락 전 기준가 (매수 토큰 기준)
    drop_at_buy = Column(Float, nullable=True)        # 진입 시점 낙폭 (ref - price)
    max_price = Column(Float, nullable=True)          # 진입 후 최고가 (분석용)
    stabilization_range_at_buy = Column(Float, nullable=True)  # 진입 판정에 쓴 안정화 구간 고저폭 (max-min)

    # A/B 포스트모템용 공통 회고 컬럼
    strategy_name = Column(String, nullable=True)  # 봇 식별 상수 "elderberry"
    mode = Column(String, nullable=True)           # "live" 또는 "sim" (config.simulation_mode 기준)

    # Time-based strategy data
    market_end_date = Column(DateTime, nullable=True)  # Market resolution time
    hours_until_resolution_at_buy = Column(Float, nullable=True)  # Hours left when bought

    # Metadata
    liquidity_at_buy = Column(Float, nullable=True)
    volume_24h_at_buy = Column(Float, nullable=True)
    market_tags = Column(String, nullable=True)  # Gamma API tags, e.g. "Politics, US Elections"
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Trade {self.id}: {self.outcome} @ {self.buy_price:.2%} -> {self.status.value}>"


class MarketSnapshot(Base):
    """Historical market data snapshots for signal calculation.

    probability는 항상 **YES 가격** 기준으로 저장한다.
    신호 계산 시 favorite이 NO쪽이면 1-p로 환산해 평가한다 (signals.py 참조).
    """
    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    probability = Column(Float, nullable=False)  # YES price
    liquidity = Column(Float, nullable=True)
    volume_24h = Column(Float, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return f"<Snapshot {self.condition_id}: {self.probability:.2%}>"


class SkippedMarket(Base):
    """Markets that were skipped (reentry cooldown source).

    condition_id는 unique가 아니다 - skip은 영구 밴이 아니라
    reentry_cooldown_hours 동안만 유효한 기록이다.
    """
    __tablename__ = "skipped_markets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    reason = Column(String, nullable=False)  # "rebound_before_entry", etc.
    skipped_at = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return f"<Skipped {self.condition_id}: {self.reason}>"


def init_database(
    db_path: str,
    maintenance_requirements: SQLiteMaintenanceRequirements | None = None,
) -> sessionmaker:
    """Initialize database and return session factory.

    Args:
        db_path: Path to SQLite database file

    Returns:
        SQLAlchemy sessionmaker instance
    """
    prepare_database(
        db_path,
        "golden-elderberry",
        requirements=maintenance_requirements,
    )
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    # 기존 DB 파일 호환: 신규 컬럼 best-effort ALTER (이미 있으면 조용히 skip)
    with engine.connect() as conn:
        for ddl in (
            "ALTER TABLE trades ADD COLUMN strategy_name TEXT",
            "ALTER TABLE trades ADD COLUMN mode TEXT",
            "ALTER TABLE trades ADD COLUMN volume_24h_at_buy REAL",
            "ALTER TABLE trades ADD COLUMN stabilization_range_at_buy REAL",
        ):
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception:
                conn.rollback()  # Column already exists - 다음 ALTER를 위해 트랜잭션 복구
    return sessionmaker(bind=engine)
