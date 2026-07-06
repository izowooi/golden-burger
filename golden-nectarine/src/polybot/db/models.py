"""SQLite database models for trade tracking (DB 회고 로깅 표준 적용).

부록 §A 표준: strategy_name / mode / volume_24h_at_buy + 전략 고유 수치 컬럼
(rolling_min_at_buy, lookback_days_at_buy) + 청산 시그널 컬럼(hold_hours_at_exit).
교차 봇 UNION 쿼리로 A/B 포스트모템이 가능하도록 문자열이 아닌 수치 컬럼에 기록한다.
"""
import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Enum, create_engine, text
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

# 봇 식별 상수 - trades.strategy_name에 항상 기록 (교차 봇 UNION 쿼리용)
STRATEGY_NAME = "nectarine"


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

    # 회고 로깅 표준 (부록 §A - 교차 봇 포스트모템 계약)
    strategy_name = Column(String, nullable=True)  # 항상 "nectarine"
    mode = Column(String, nullable=True)           # "live" 또는 "sim"

    # Market information
    condition_id = Column(String, index=True, nullable=False)
    market_slug = Column(String)
    question = Column(String)
    outcome = Column(String)  # 항상 "Yes" (YES 매수 고정 전략)
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

    # Entry/Exit reasons (bottom fisher strategy)
    entry_reason = Column(String, nullable=True)   # "bottom_fisher_min0.180_p0.175"
    exit_reason = Column(String, nullable=True)    # "max_holding"(주 경로), "stop_loss", "take_profit", "time_exit", "resolved_unredeemed"

    # Bottom fisher 시그널 수치 컬럼 (entry_reason 문자열에만 내장 금지 - §A.4)
    rolling_min_at_buy = Column(Float, nullable=True)     # 진입 시점 20일(최근 24h 제외) 롤링 최저가
    lookback_days_at_buy = Column(Float, nullable=True)   # 룩백 윈도우 실제 커버 일수
    hold_hours_at_exit = Column(Float, nullable=True)     # 청산 시점 보유 시간 (§A.5)
    max_price = Column(Float, nullable=True)              # 진입 후 최고가 (분석용, trailing 미사용)

    # Time-based strategy data
    market_end_date = Column(DateTime, nullable=True)  # Market resolution time
    hours_until_resolution_at_buy = Column(Float, nullable=True)  # Hours left when bought

    # Metadata
    liquidity_at_buy = Column(Float, nullable=True)
    volume_24h_at_buy = Column(Float, nullable=True)  # 매수 시점 gamma volume24hr (§A.3)
    market_tags = Column(String, nullable=True)  # Gamma API tags, e.g. "Politics, US Elections"
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Trade {self.id}: {self.outcome} @ {self.buy_price:.2%} -> {self.status.value}>"


class MarketSnapshot(Base):
    """Historical market data snapshots for rolling-min calculation.

    probability는 항상 **YES 가격** 기준으로 저장한다.
    이 전략은 YES 매수 고정이라 환산 없이 그대로 평가한다 (signals.py 참조).
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
    reason = Column(String, nullable=False)  # "price_above_band" 등
    skipped_at = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return f"<Skipped {self.condition_id}: {self.reason}>"


class CycleStat(Base):
    """사이클 단위 매수 파이프라인 계측 (max_positions 튜닝 회고용).

    보유 궤적이 상한에 붙어 있었는지(cap_skips>0 빈도)를 보고
    한 달 뒤 상한/포지션 크기를 조정하는 근거 데이터.
    """
    __tablename__ = "cycle_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    markets_scanned = Column(Integer, nullable=False)
    buy_candidates = Column(Integer, nullable=False)
    holdings_before = Column(Integer, nullable=False)
    holdings_after = Column(Integer, nullable=False)
    max_positions = Column(Integer, nullable=False)  # 당시 설정값 (튜닝 이력 추적)
    buy_amount_usdc = Column(Float, nullable=False)  # 당시 포지션 크기
    bought = Column(Integer, nullable=False)
    cap_skips = Column(Integer, nullable=False)      # 상한 때문에 못 산 후보 수
    cooldown_skips = Column(Integer, nullable=False)
    failed_buys = Column(Integer, nullable=False)    # 주문 실패/트레이더 내부 skip

    def __repr__(self) -> str:
        return (f"<CycleStat {self.ts}: 보유 {self.holdings_after}/"
                f"{self.max_positions}, cap_skips={self.cap_skips}>")


class CappedCandidate(Base):
    """max_positions 상한 때문에 매수하지 못한 후보 (반사실 P&L 회고용).

    market_snapshots(60일 보존)와 join하면 '상한이 걸러낸 진입'의 가상
    calendar-exit(+120h) 수익률을 계산할 수 있다. 같은 시장이 사이클마다
    반복 기록되지 않도록 repository에서 24h dedup 한다.
    """
    __tablename__ = "capped_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=datetime.utcnow, index=True)
    condition_id = Column(String, index=True, nullable=False)
    question = Column(String)
    yes_price = Column(Float, nullable=False)   # 스킵 시점 가격 = 가상 매수가
    rolling_min = Column(Float, nullable=True)
    hours_left = Column(Float, nullable=True)

    def __repr__(self) -> str:
        return f"<Capped {self.condition_id}: {self.yes_price:.3f}>"


# 부록 §A.8: 기존 로컬 DB 호환용 best-effort ALTER 대상 컬럼
# (cherry의 market_tags ALTER 패턴 - 이미 존재하면 조용히 무시)
_ALTER_COLUMNS = [
    ("trades", "strategy_name", "TEXT"),
    ("trades", "mode", "TEXT"),
    ("trades", "volume_24h_at_buy", "REAL"),
    ("trades", "rolling_min_at_buy", "REAL"),
    ("trades", "lookback_days_at_buy", "REAL"),
    ("trades", "hold_hours_at_exit", "REAL"),
    ("trades", "market_tags", "TEXT"),
]


def init_database(db_path: str) -> sessionmaker:
    """Initialize database and return session factory.

    신규 컬럼은 best-effort ALTER로 기존 로컬 DB에도 추가한다 (§A.8).

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
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                )
                conn.commit()
            except Exception:
                pass  # Column already exists
    return sessionmaker(bind=engine)
