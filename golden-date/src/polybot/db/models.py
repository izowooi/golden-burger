"""SQLite database models for trade tracking."""
import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Enum, create_engine, text
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
    EXPIRED = "expired"              # 해결됐지만 청산 못한 포지션 (수동 redeem 필요, §3.4)


class Trade(Base):
    """Trade record for tracking positions.

    재진입 허용(§3.3)을 위해 condition_id는 unique가 아니다.
    같은 시장을 쿨다운 이후 다시 거래하면 새 row가 생긴다.
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

    # Entry/Exit reasons
    entry_reason = Column(String, nullable=True)   # e.g. "ladder1_12.3h_mom+0.005"
    exit_reason = Column(String, nullable=True)    # "take_profit", "stop_loss", "trailing_stop", "time_exit", "resolved_unredeemed"

    # Trailing stop tracking
    max_price = Column(Float, nullable=True)       # Highest price since entry (for trailing stop)

    # Time-based strategy data
    market_end_date = Column(DateTime, nullable=True)  # Market resolution time
    hours_until_resolution_at_buy = Column(Float, nullable=True)  # Hours left when bought

    # Metadata
    liquidity_at_buy = Column(Float, nullable=True)
    market_tags = Column(String, nullable=True)  # Gamma API tags, e.g. "Politics, US Elections"

    # A/B 포스트모템 회고 컬럼 (부록 스펙 §D — 교차 봇 UNION 쿼리·시그널 수치 분석용)
    strategy_name = Column(String, nullable=True)      # 봇 식별 상수 "date"
    mode = Column(String, nullable=True)               # "live" / "sim" (config.simulation_mode)
    volume_24h_at_buy = Column(Float, nullable=True)   # 매수 시점 gamma volume24hr
    ladder_band_at_buy = Column(Integer, nullable=True)  # 사다리 밴드 1/2/3
    momentum_at_buy = Column(Float, nullable=True)     # 진입 시 favorite 모멘텀 변화
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Trade {self.id}: {self.outcome} @ {self.buy_price:.2%} -> {self.status.value}>"


class MarketSnapshot(Base):
    """Historical market data snapshots (YES 가격 기준, 모멘텀 게이트용)."""
    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    probability = Column(Float, nullable=False)  # YES 토큰 가격
    liquidity = Column(Float, nullable=True)
    volume_24h = Column(Float, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return f"<Snapshot {self.condition_id}: {self.probability:.2%}>"


class SkippedMarket(Base):
    """Skip 이력. skipped_at 기준으로 재진입 쿨다운을 판정한다 (영구 밴 아님, §3.3).

    같은 시장이 시점을 달리해 여러 번 skip될 수 있으므로 condition_id는 unique가 아니다.
    """
    __tablename__ = "skipped_markets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    reason = Column(String, nullable=False)  # "rapid_jump", etc.
    skipped_at = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return f"<Skipped {self.condition_id}: {self.reason} @ {self.skipped_at}>"


def init_database(db_path: str) -> sessionmaker:
    """Initialize database and return session factory.

    Args:
        db_path: Path to SQLite database file

    Returns:
        SQLAlchemy sessionmaker instance
    """
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    # 기존 DB 파일 호환: 신규 컬럼 best-effort ALTER (cherry의 market_tags 패턴).
    # 컬럼이 이미 있으면 ALTER가 실패하므로 컬럼별로 개별 try (일부만 없는 DB도 마이그레이션됨).
    retro_columns = [
        "strategy_name TEXT",
        "mode TEXT",
        "volume_24h_at_buy REAL",
        "ladder_band_at_buy INTEGER",
        "momentum_at_buy REAL",
    ]
    with engine.connect() as conn:
        for column_ddl in retro_columns:
            try:
                conn.execute(text(f"ALTER TABLE trades ADD COLUMN {column_ddl}"))
                conn.commit()
            except Exception:
                pass  # Column already exists
    return sessionmaker(bind=engine)
