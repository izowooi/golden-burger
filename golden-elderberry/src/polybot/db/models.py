"""SQLite database models for trade tracking."""
import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Enum, create_engine
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class TradeStatus(enum.Enum):
    """Trade lifecycle status."""
    PENDING_BUY = "pending_buy"      # Waiting for buy order to fill
    HOLDING = "holding"              # Position held
    PENDING_SELL = "pending_sell"    # Waiting for sell order to fill
    COMPLETED = "completed"          # Trade closed with profit/loss
    SKIPPED = "skipped"              # Skipped due to rapid price move
    EXPIRED = "expired"              # 시장이 해결됐지만 청산 못함 (수동 redeem 필요)


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
    exit_reason = Column(String, nullable=True)    # "take_profit", "stop_loss", "max_holding", "time_exit", "resolved_unredeemed"

    # Panic fade strategy data
    ref_price_at_buy = Column(Float, nullable=True)   # 급락 전 기준가 (매수 토큰 기준)
    drop_at_buy = Column(Float, nullable=True)        # 진입 시점 낙폭 (ref - price)
    max_price = Column(Float, nullable=True)          # 진입 후 최고가 (분석용)

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


def init_database(db_path: str) -> sessionmaker:
    """Initialize database and return session factory.

    Args:
        db_path: Path to SQLite database file

    Returns:
        SQLAlchemy sessionmaker instance
    """
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
