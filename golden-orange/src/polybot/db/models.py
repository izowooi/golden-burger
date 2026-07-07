"""SQLite database models for trade tracking (부록 §A DB 회고 로깅 표준 반영)."""
import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Enum, create_engine, text
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

# 봇 식별 상수: 교차 봇 UNION 쿼리용 (부록 §A.1)
STRATEGY_NAME = "orange"


class TradeStatus(enum.Enum):
    """Trade lifecycle status."""
    PENDING_BUY = "pending_buy"      # Waiting for buy order to fill
    HOLDING = "holding"              # Position held
    PENDING_SELL = "pending_sell"    # Waiting for sell order to fill
    COMPLETED = "completed"          # Trade closed with profit/loss
    SKIPPED = "skipped"              # Skipped due to rapid price jump
    EXPIRED = "expired"              # 시장 해결 후 매도 불가 - 수동 redeem 필요
    UNFILLED = "unfilled"            # 매수 GTC가 체결된 적 없음이 확인된 유령 포지션
                                     # (매도 시 balance 0 거절 -> 재시도 중단, P&L 제외)


class Trade(Base):
    """Trade record for tracking positions.

    재진입 허용 정책: condition_id는 unique가 아니다.
    같은 시장에 쿨다운(reentry_cooldown_hours) 경과 후 다시 진입할 수 있다.
    """
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Market information
    condition_id = Column(String, index=True, nullable=False)
    market_slug = Column(String)
    question = Column(String)
    outcome = Column(String)  # Fear Spike Fade는 항상 "No" 쪽 토큰
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
    entry_reason = Column(String, nullable=True)   # "fear_spike_fade_..."
    exit_reason = Column(String, nullable=True)    # "stop_loss"|"retrace_target"|"take_profit"|"max_holding"|"time_exit"|"resolved_unredeemed"

    # Price tracking (분석용 - Fear Spike Fade는 trailing stop 없음)
    max_price = Column(Float, nullable=True)       # Highest NO price since entry

    # Time-based strategy data
    market_end_date = Column(DateTime, nullable=True)  # Market resolution time
    hours_until_resolution_at_buy = Column(Float, nullable=True)  # Hours left when bought

    # --- 부록 §A DB 회고 로깅 표준 (A/B 포스트모템 계약) ---
    strategy_name = Column(String, nullable=True)      # 항상 "orange"
    mode = Column(String, nullable=True)               # "live" 또는 "sim"
    volume_24h_at_buy = Column(Float, nullable=True)   # 매수 시점 gamma volume24hr

    # 전략 고유 시그널 수치 컬럼 (*_at_buy) - entry_reason 문자열에만 내장 금지
    yes_price_at_buy = Column(Float, nullable=True)         # 매수 시점 YES 가격
    base_price_at_buy = Column(Float, nullable=True)        # base (7d 중앙값)
    spike_peak_at_buy = Column(Float, nullable=True)        # 진입 시점까지의 스파이크 고점
    spike_age_minutes_at_buy = Column(Float, nullable=True) # 스파이크 시작 후 경과 분
    vol_mult_at_buy = Column(Float, nullable=True)          # volume24h / 윈도우 평균

    # 청산 판정에 쓴 시그널 값 (*_at_exit) - execute_sell의 update_trade에서 기록
    yes_price_at_exit = Column(Float, nullable=True)        # 청산 시점 스냅샷 최신 YES

    # Metadata
    liquidity_at_buy = Column(Float, nullable=True)
    market_tags = Column(String, nullable=True)  # Gamma API tags, e.g. "Politics, US Elections"
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Trade {self.id}: {self.outcome} @ {self.buy_price:.2%} -> {self.status.value}>"


class MarketSnapshot(Base):
    """Historical market data snapshots for signal calculation.

    probability는 항상 YES 가격 기준으로 저장한다 (Phase 0).
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
    """Markets that were skipped (재진입 쿨다운 판정용 timestamp 필수).

    condition_id는 unique가 아니다: 쿨다운이 지나면 같은 시장이 다시
    skip 기록될 수 있다. 쿨다운 판정은 가장 최근 skipped_at 기준.
    """
    __tablename__ = "skipped_markets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    reason = Column(String, nullable=False)  # "spike_collapsed", etc.
    skipped_at = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return f"<Skipped {self.condition_id}: {self.reason}>"


# init_database가 best-effort ALTER로 보정하는 신규 컬럼 (기존 로컬 DB 호환,
# cherry의 market_tags ALTER 패턴)
_ALTER_COLUMNS = [
    ("trades", "market_tags", "TEXT"),
    ("trades", "strategy_name", "TEXT"),
    ("trades", "mode", "TEXT"),
    ("trades", "volume_24h_at_buy", "REAL"),
    ("trades", "yes_price_at_buy", "REAL"),
    ("trades", "base_price_at_buy", "REAL"),
    ("trades", "spike_peak_at_buy", "REAL"),
    ("trades", "spike_age_minutes_at_buy", "REAL"),
    ("trades", "vol_mult_at_buy", "REAL"),
    ("trades", "yes_price_at_exit", "REAL"),
]


def init_database(db_path: str) -> sessionmaker:
    """Initialize database and return session factory.

    Args:
        db_path: Path to SQLite database file

    Returns:
        SQLAlchemy sessionmaker instance
    """
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        for table, column, col_type in _ALTER_COLUMNS:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
            except Exception:
                pass  # Column already exists
    return sessionmaker(bind=engine)
