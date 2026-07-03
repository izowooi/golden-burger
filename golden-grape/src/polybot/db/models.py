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
    SKIPPED = "skipped"              # Skipped due to rapid price jump
    EXPIRED = "expired"              # 시장 해결 후 미청산 - 수동 redeem 필요


class Trade(Base):
    """Trade record for tracking positions.

    재진입 허용 전략이므로 condition_id는 unique가 아니다
    (같은 시장에 쿨다운 후 다시 진입할 수 있다).
    """
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Market information
    condition_id = Column(String, index=True, nullable=False)
    market_slug = Column(String)
    question = Column(String)
    outcome = Column(String)  # "Yes" or "No"
    token_index = Column(Integer, nullable=True)  # 0=YES, 1=NO (드리프트 방향 판정용)
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

    # Entry/Exit reasons (cascade rider strategy)
    entry_reason = Column(String, nullable=True)   # "cascade_up", "cascade_down"
    exit_reason = Column(String, nullable=True)    # "take_profit", "stop_loss", "drift_death", "trailing_stop", "time_exit", "resolved_unredeemed"

    # Trailing stop tracking
    max_price = Column(Float, nullable=True)       # Highest price since entry (for trailing stop)

    # Time-based strategy data
    market_end_date = Column(DateTime, nullable=True)  # Market resolution time
    hours_until_resolution_at_buy = Column(Float, nullable=True)  # Hours left when bought

    # Cascade signal data at buy (for analysis)
    drift_at_buy = Column(Float, nullable=True)        # 매수 토큰 기준 24h 드리프트
    consistency_at_buy = Column(Float, nullable=True)  # 비음 버킷 비율
    vol_accel_at_buy = Column(Float, nullable=True)    # 거래량 가속 배수

    # Metadata
    liquidity_at_buy = Column(Float, nullable=True)
    market_tags = Column(String, nullable=True)  # Gamma API tags, e.g. "Politics, US Elections"
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        price = f"{self.buy_price:.2%}" if self.buy_price is not None else "N/A"
        return f"<Trade {self.id}: {self.outcome} @ {price} -> {self.status.value}>"


class MarketSnapshot(Base):
    """Historical market data snapshots for drift analysis.

    probability는 항상 YES 가격(outcomePrices[0]) 기준으로 저장한다.
    """
    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    probability = Column(Float, nullable=False)  # YES 가격
    liquidity = Column(Float, nullable=True)
    volume_24h = Column(Float, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return f"<Snapshot {self.condition_id}: {self.probability:.2%}>"


class SkippedMarket(Base):
    """Markets that were skipped (timestamp 기반 쿨다운용, 영구 차단 아님)."""
    __tablename__ = "skipped_markets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    reason = Column(String, nullable=False)  # "rapid_jump", etc.
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
