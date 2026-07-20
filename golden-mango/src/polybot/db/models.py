"""SQLite database models for trade tracking (Patience Premium + 회고 로깅 표준 §A)."""
import enum
import logging
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Enum, create_engine, text
)
from sqlalchemy.orm import declarative_base, sessionmaker
from polybot_observability import SQLiteMaintenanceRequirements, prepare_database

logger = logging.getLogger(__name__)

Base = declarative_base()

# 봇 식별 상수 - 교차 봇 UNION 쿼리용 (trades.strategy_name에 기록)
STRATEGY_NAME = "mango"


class TradeStatus(enum.Enum):
    """Trade lifecycle status."""
    PENDING_BUY = "pending_buy"      # Waiting for buy order to fill
    HOLDING = "holding"              # Position held
    PENDING_SELL = "pending_sell"    # Waiting for sell order to fill
    COMPLETED = "completed"          # Trade closed with profit/loss
    SKIPPED = "skipped"              # Skipped due to rapid price jump
    EXPIRED = "expired"              # 해결됐지만 청산 못한 포지션 (수동 redeem 필요, §3.4)
    UNFILLED = "unfilled"            # 매수 GTC가 체결된 적 없음이 확인된 유령 포지션
                                     # (매도 시 balance 0 거절 -> 재시도 중단, P&L 제외)


class Trade(Base):
    """Trade record for tracking positions.

    재진입 허용(§3.3)을 위해 condition_id는 unique가 아니다.
    같은 시장을 쿨다운 이후 다시 거래하면 새 row가 생긴다.

    회고 로깅 표준(부록 §A): strategy_name/mode/volume_24h_at_buy와
    전략 고유 시그널을 수치 컬럼으로 기록해 A/B 포스트모템 쿼리를 지원한다.
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
    entry_reason = Column(String, nullable=True)   # e.g. "carry_y4.21_48.0h_mom+0.005"
    exit_reason = Column(String, nullable=True)    # "take_profit", "stop_loss", "time_exit", "resolved_unredeemed"

    # 회고 로깅 표준 (부록 §A - 교차 봇 포스트모템 계약)
    strategy_name = Column(String, nullable=True)      # 봇 식별 상수 "mango"
    mode = Column(String, nullable=True)               # "live" 또는 "sim"
    volume_24h_at_buy = Column(Float, nullable=True)   # 매수 시점 gamma volume24hr

    # 전략 고유 시그널 (수치 컬럼 - entry_reason 문자열 내장 금지)
    carry_yield_at_buy = Column(Float, nullable=True)   # 진입 판정 y = ((1-p)/p)*(8760/h)
    momentum_6h_at_buy = Column(Float, nullable=True)   # 진입 시점 최근 6h favorite 변화
    carry_yield_at_exit = Column(Float, nullable=True)  # 청산 시점 재계산 y (midpoint 기준)

    # Trailing stop 없음 - max_price는 회고(고점 대비 경로) 분석용으로만 기록
    max_price = Column(Float, nullable=True)       # Highest price since entry

    # Time-based strategy data
    market_end_date = Column(DateTime, nullable=True)  # Market resolution time
    hours_until_resolution_at_buy = Column(Float, nullable=True)  # Hours left when bought

    # Metadata
    liquidity_at_buy = Column(Float, nullable=True)
    market_tags = Column(String, nullable=True)  # Gamma API tags, e.g. "Politics, US Elections"
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Trade {self.id}: {self.outcome} @ {self.buy_price:.2%} -> {self.status.value}>"


class MarketSnapshot(Base):
    """Historical market data snapshots (YES 가격 기준, 모멘텀 가드용)."""
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


# init_database가 best-effort ALTER를 시도할 컬럼 (기존 로컬 DB 호환, cherry 패턴)
_ALTER_COLUMNS = [
    ("market_tags", "TEXT"),
    ("strategy_name", "TEXT"),
    ("mode", "TEXT"),
    ("volume_24h_at_buy", "REAL"),
    ("carry_yield_at_buy", "REAL"),
    ("momentum_6h_at_buy", "REAL"),
    ("carry_yield_at_exit", "REAL"),
]


def init_database(
    db_path: str,
    maintenance_requirements: SQLiteMaintenanceRequirements | None = None,
) -> sessionmaker:
    """Initialize database and return session factory.

    새 DB는 create_all이 최신 스키마로 만들고, 구버전 스키마의 기존 로컬 DB에는
    회고 로깅 컬럼을 best-effort ALTER로 추가한다 (이미 있으면 조용히 skip).

    Args:
        db_path: Path to SQLite database file

    Returns:
        SQLAlchemy sessionmaker instance
    """
    prepare_database(
        db_path,
        "golden-mango",
        requirements=maintenance_requirements,
    )
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        for column, col_type in _ALTER_COLUMNS:
            try:
                conn.execute(text(f"ALTER TABLE trades ADD COLUMN {column} {col_type}"))
                conn.commit()
            except Exception:
                pass  # Column already exists
    return sessionmaker(bind=engine)
