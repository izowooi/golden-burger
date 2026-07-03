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
    SKIPPED = "skipped"              # Skipped due to rapid price move
    EXPIRED = "expired"              # Market resolved before exit - manual redeem needed


class Trade(Base):
    """Trade record for tracking positions.

    condition_id는 unique가 아니다 - 재진입 허용 전략이므로 같은 시장에
    여러 거래 기록이 쌓일 수 있다 (쿨다운은 repository.can_enter가 판정).
    """
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Market information
    condition_id = Column(String, index=True, nullable=False)
    market_slug = Column(String)
    question = Column(String)
    outcome = Column(String)  # "Yes" or "No"
    token_id = Column(String, nullable=False)
    token_index = Column(Integer, nullable=True)  # 0=YES, 1=NO (스냅샷 방향 변환용)

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

    # Entry/Exit reasons (shock follow strategy)
    entry_reason = Column(String, nullable=True)   # "jump_up_0.14" 등
    exit_reason = Column(String, nullable=True)    # "take_profit", "stop_loss", "trailing_stop", "momentum_death", "time_exit", "resolved_unredeemed"

    # Trailing stop tracking
    max_price = Column(Float, nullable=True)       # Highest price since entry (for trailing stop)

    # Time-based strategy data
    market_end_date = Column(DateTime, nullable=True)  # Market resolution time
    hours_until_resolution_at_buy = Column(Float, nullable=True)  # Hours left when bought

    # Shock follow strategy data
    jump_size_at_buy = Column(Float, nullable=True)   # 진입 시점 점프 폭
    base_price_at_buy = Column(Float, nullable=True)  # 점프 시작 기준가 (윈도우 최저가)

    # Metadata
    liquidity_at_buy = Column(Float, nullable=True)
    market_tags = Column(String, nullable=True)  # Gamma API tags, e.g. "Politics, US Elections"

    # Retrospective A/B logging (포스트모템 계약 - 교차 봇 UNION 쿼리용)
    strategy_name = Column(String, nullable=True)     # 봇 식별 상수 "lime"
    mode = Column(String, nullable=True)              # "live" or "sim" (config.simulation_mode)
    volume_24h_at_buy = Column(Float, nullable=True)  # 매수 시점 gamma volume24hr
    vol_mult_at_buy = Column(Float, nullable=True)    # 매수 시점 거래량 배수 (현재/윈도우 평균)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Trade {self.id}: {self.outcome} @ {self.buy_price:.2%} -> {self.status.value}>"


class MarketSnapshot(Base):
    """Historical market data snapshots for jump/volume analysis.

    probability는 항상 YES(index 0) 가격 기준으로 저장한다.
    NO 방향 판정은 strategy/signals.py의 invert_series()가 담당.
    """
    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    probability = Column(Float, nullable=False)
    liquidity = Column(Float, nullable=True)
    volume_24h = Column(Float, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return f"<Snapshot {self.condition_id}: {self.probability:.2%}>"


class SkippedMarket(Base):
    """Markets skipped with timestamp - 재진입 쿨다운 판정에 사용.

    condition_id는 unique가 아니다 - 시간이 지나면 같은 시장이 다시 skip될 수 있다.
    영구 밴이 아니라 skipped_at 기준 쿨다운으로만 동작한다 (cherry의 영구
    rapid_jump 밴 수정).
    """
    __tablename__ = "skipped_markets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    reason = Column(String, nullable=False)  # "post_scan_jump" 등
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

    # 기존 DB 파일 호환: 신규 컬럼 best-effort ALTER (cherry의 market_tags 패턴).
    # 이미 컬럼이 있으면 실패하고 조용히 넘어간다.
    retro_columns = [
        ("strategy_name", "TEXT"),
        ("mode", "TEXT"),
        ("volume_24h_at_buy", "REAL"),
        ("vol_mult_at_buy", "REAL"),
    ]
    with engine.connect() as conn:
        for name, col_type in retro_columns:
            try:
                conn.execute(text(f"ALTER TABLE trades ADD COLUMN {name} {col_type}"))
                conn.commit()
            except Exception:
                pass  # Column already exists

    return sessionmaker(bind=engine)
