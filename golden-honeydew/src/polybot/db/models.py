"""SQLite database models for trade tracking."""
import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Enum, ForeignKey, create_engine, text
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
    EXPIRED = "expired"              # 시장 해결 후 미청산 (§3.4: 수동 redeem 필요)
    UNFILLED = "unfilled"            # 매수 GTC가 체결된 적 없음이 확인된 유령 포지션
                                     # (매도 시 balance 0 거절 -> 재시도 중단, P&L 제외)


class Trade(Base):
    """Trade record for tracking positions.

    §3.3 재진입 허용을 위해 condition_id는 unique가 아니다
    (같은 시장에 쿨다운 이후 여러 trade 레코드가 생길 수 있음).
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

    # Entry/Exit reasons (night watch strategy)
    entry_reason = Column(String, nullable=True)   # "night_dislocation_dev-0.070" 등
    exit_reason = Column(String, nullable=True)    # "take_profit", "stop_loss", "max_holding", "time_exit", "resolved_unredeemed", "buy_unfilled"

    # Analytics tracking (no trailing stop in this strategy, kept for analysis)
    max_price = Column(Float, nullable=True)       # Highest price since entry

    # Night watch strategy data at buy
    deviation_at_buy = Column(Float, nullable=True)  # 진입 시 median 대비 편차 (YES 기준)
    median_at_buy = Column(Float, nullable=True)     # 진입 시 24h median (YES 기준)

    # Time-based strategy data
    market_end_date = Column(DateTime, nullable=True)  # Market resolution time
    hours_until_resolution_at_buy = Column(Float, nullable=True)  # Hours left when bought

    # Metadata
    liquidity_at_buy = Column(Float, nullable=True)
    market_tags = Column(String, nullable=True)  # Gamma API tags, e.g. "Politics, US Elections"

    # DB 회고 로깅 (A/B 포스트모템 공통 계약 — 부록 스펙 §D)
    strategy_name = Column(String, nullable=True)     # 봇 식별 상수 "honeydew" (교차 봇 UNION 쿼리용)
    mode = Column(String, nullable=True)              # "live" / "sim" (config.simulation_mode 기준)
    volume_24h_at_buy = Column(Float, nullable=True)  # 매수 시점 gamma volume24hr
    deviation_at_exit = Column(Float, nullable=True)  # 청산 시점 24h median 대비 편차 (YES 기준, 계산 불가 시 NULL)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Trade {self.id}: {self.outcome} @ {self.buy_price:.2%} -> {self.status.value}>"


class MarketSnapshot(Base):
    """Historical market data snapshots for analysis.

    probability는 YES 토큰 가격 기준으로 저장한다 (Phase 0 규칙).
    """
    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    probability = Column(Float, nullable=False)
    liquidity = Column(Float, nullable=True)
    volume_24h = Column(Float, nullable=True)
    best_bid = Column(Float, nullable=True)
    best_ask = Column(Float, nullable=True)
    spread = Column(Float, nullable=True)
    source_updated_at = Column(String, nullable=True)
    run_id = Column(String, index=True, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return f"<Snapshot {self.condition_id}: {self.probability:.2%}>"


class MarketCatalog(Base):
    """Slow-changing market metadata required for event-level replay."""
    __tablename__ = "market_catalog"

    condition_id = Column(String, primary_key=True)
    market_id = Column(String, nullable=True)
    market_slug = Column(String, nullable=True)
    question = Column(String, nullable=True)
    event_id = Column(String, index=True, nullable=True)
    event_slug = Column(String, index=True, nullable=True)
    end_date = Column(String, nullable=True)
    outcomes_json = Column(String, nullable=False, default="[]")
    token_ids_json = Column(String, nullable=False, default="[]")
    tags_json = Column(String, nullable=False, default="[]")
    fees_enabled = Column(Integer, nullable=True)
    fee_rate = Column(Float, nullable=True)
    first_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class MarketSweep(Base):
    """Proof that a complete Gamma keyset universe traversal finished.

    Aggregate counts are duplicated intentionally so the retro auditor can detect
    partial/corrupt membership sets before using them as a coverage denominator.
    """
    __tablename__ = "market_sweeps"

    sweep_id = Column(String, primary_key=True)
    schema_version = Column(Integer, nullable=False)
    run_id = Column(String, index=True, nullable=True)
    started_at = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=False, index=True)
    cursor_complete = Column(Integer, nullable=False)
    pages = Column(Integer, nullable=False)
    raw_market_count = Column(Integer, nullable=False)
    unique_condition_count = Column(Integer, nullable=False)
    qualified_market_count = Column(Integer, nullable=False)
    excluded_condition_count = Column(Integer, nullable=False)
    exclusion_counts_json = Column(String, nullable=False)
    missing_condition_id_count = Column(Integer, nullable=False)
    duplicate_raw_count = Column(Integer, nullable=False)
    min_liquidity = Column(Float, nullable=False)
    min_volume = Column(Float, nullable=False)
    membership_digest_sha256 = Column(String, nullable=False)
    snapshotted_market_count = Column(Integer, nullable=False)


class MarketSweepMembership(Base):
    """Qualified condition membership and archive outcome for one Gamma sweep."""
    __tablename__ = "market_sweep_memberships"

    sweep_id = Column(
        String,
        ForeignKey("market_sweeps.sweep_id", ondelete="CASCADE"),
        primary_key=True,
    )
    condition_id = Column(String, primary_key=True, index=True)
    raw_seen_count = Column(Integer, nullable=False)
    qualified = Column(Integer, nullable=False, index=True)
    qualification_reason = Column(String, nullable=False)
    snapshot_eligible = Column(Integer, nullable=False)
    snapshotted = Column(Integer, nullable=False, index=True)
    snapshot_reason = Column(String, nullable=False)


class SkippedMarket(Base):
    """Markets that were skipped (재진입 쿨다운 판정용 timestamp 포함).

    §3.3: condition_id는 unique가 아니다 — 같은 시장이 여러 번 skip될 수 있고,
    쿨다운은 가장 최근 skipped_at 기준으로 판정한다.
    """
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

    # 기존 로컬 DB 파일 호환: 신규 컬럼 best-effort ALTER (cherry의 market_tags 패턴).
    # create_all은 이미 존재하는 테이블에 컬럼을 추가하지 않으므로 직접 ALTER한다.
    retro_columns = [
        ("strategy_name", "TEXT"),
        ("mode", "TEXT"),
        ("volume_24h_at_buy", "REAL"),
        ("deviation_at_exit", "REAL"),
    ]
    with engine.connect() as conn:
        for column_name, column_type in retro_columns:
            try:
                conn.execute(
                    text(f"ALTER TABLE trades ADD COLUMN {column_name} {column_type}")
                )
                conn.commit()
            except Exception:
                pass  # Column already exists
        for column_name, column_type in (
            ("best_bid", "REAL"),
            ("best_ask", "REAL"),
            ("spread", "REAL"),
            ("source_updated_at", "TEXT"),
            ("run_id", "TEXT"),
        ):
            try:
                conn.execute(
                    text(
                        f"ALTER TABLE market_snapshots ADD COLUMN "
                        f"{column_name} {column_type}"
                    )
                )
                conn.commit()
            except Exception:
                pass
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS market_snapshots_condition_timestamp_idx "
                "ON market_snapshots(condition_id, timestamp)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS market_snapshots_run_idx "
                "ON market_snapshots(run_id)"
            )
        )
        conn.commit()

    return sessionmaker(bind=engine)
