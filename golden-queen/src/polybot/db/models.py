"""SQLite evidence models for Golden Queen / Crown Momentum."""

from __future__ import annotations

import enum
from datetime import datetime

from polybot_observability import SQLiteMaintenanceRequirements, prepare_database
from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker


Base = declarative_base()
STRATEGY_NAME = "queen"


class TradeStatus(enum.Enum):
    PENDING_BUY = "pending_buy"
    HOLDING = "holding"
    PENDING_SELL = "pending_sell"
    COMPLETED = "completed"
    RESOLVED = "resolved"
    SKIPPED = "skipped"
    EXPIRED = "expired"
    UNFILLED = "unfilled"
    QUARANTINED = "quarantined"


class Trade(Base):
    """One submitted Crown Momentum position and its observed evidence."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    market_slug = Column(String)
    question = Column(String)
    event_id = Column(String, index=True)
    event_slug = Column(String, index=True)
    outcome = Column(String, nullable=False, default="Yes")
    token_id = Column(String, nullable=False)

    buy_price = Column(Float)
    buy_amount = Column(Float)
    buy_shares = Column(Float)
    buy_order_id = Column(String)
    buy_timestamp = Column(DateTime)
    buy_probability = Column(Float)

    sell_price = Column(Float)
    sell_shares = Column(Float)
    sell_order_id = Column(String)
    sell_timestamp = Column(DateTime)
    sell_probability = Column(Float)
    realized_pnl = Column(Float)
    hypothetical_pnl = Column(Float)
    pnl_basis = Column(String)

    # Exact execution-ledger evidence.  Requested order fields above remain
    # intent; these fields are populated only from reconciled CONFIRMED fills.
    buy_confirmed_size = Column(Float)
    buy_confirmed_vwap = Column(Float)
    buy_confirmed_fee_usdc = Column(Float)
    sell_confirmed_size = Column(Float)
    sell_confirmed_vwap = Column(Float)
    sell_confirmed_fee_usdc = Column(Float)
    sell_fill_matched_at = Column(String)

    status = Column(Enum(TradeStatus), default=TradeStatus.PENDING_BUY, index=True)
    entry_reason = Column(String)
    exit_reason = Column(String)
    strategy_name = Column(String, default=STRATEGY_NAME)
    mode = Column(String)

    market_end_date = Column(DateTime)
    hours_until_resolution_at_buy = Column(Float)
    liquidity_at_buy = Column(Float)
    volume_24h_at_buy = Column(Float)
    market_tags = Column(String)

    # Entry crossing and immutable strategy thresholds.
    prior_yes_price_at_entry = Column(Float)
    yes_price_at_buy = Column(Float)
    stop_price_at_entry = Column(Float)
    take_profit_price_at_entry = Column(Float)
    entry_prob_min_at_buy = Column(Float)
    entry_prob_max_at_buy = Column(Float)
    entry_hours_min_at_buy = Column(Float)
    entry_hours_max_at_buy = Column(Float)
    entry_time_reference = Column(String)
    entry_deadline_at_buy = Column(DateTime)
    hours_until_entry_deadline_at_buy = Column(Float)
    market_game_start_time = Column(DateTime)
    minutes_until_game_start_at_buy = Column(Float)
    sports_market_type = Column(String)
    sports_phase_at_buy = Column(String)
    prior_snapshot_id_at_entry = Column(Integer)
    entry_snapshot_id = Column(Integer)

    # Fresh executable-book observations.
    best_bid_at_buy = Column(Float)
    best_ask_at_buy = Column(Float)
    spread_at_buy = Column(Float)
    book_depth_shares_at_buy = Column(Float)
    depth_limit_price_at_buy = Column(Float)
    yes_price_at_exit = Column(Float)
    best_bid_at_exit = Column(Float)
    best_ask_at_exit = Column(Float)
    spread_at_exit = Column(Float)

    # Settlement evidence is intentionally distinct from realized SELL P&L.
    resolution_outcome = Column(String)
    resolution_value = Column(Float)
    resolution_status = Column(String)
    resolution_observed_at = Column(DateTime)
    resolution_source_updated_at = Column(String)
    resolution_evidence = Column(String)
    resolution_confirmed_buy_size = Column(Float)
    resolution_confirmed_buy_vwap = Column(Float)
    resolution_confirmed_buy_fee_usdc = Column(Float)
    settlement_pnl_assumption = Column(Float)
    settlement_assumption_basis = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        price = f"{self.buy_price:.2%}" if self.buy_price is not None else "N/A"
        status = self.status.value if self.status is not None else "unknown"
        return f"<Trade {self.id}: Yes @ {price} -> {status}>"


class MarketSnapshot(Base):
    """Crown Momentum research observation, always expressed as a YES price."""

    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, nullable=False)
    probability = Column(Float, nullable=False)
    liquidity = Column(Float)
    volume_24h = Column(Float)
    best_bid = Column(Float)
    best_ask = Column(Float)
    spread = Column(Float)
    source_updated_at = Column(String)
    run_id = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)


class MarketCatalog(Base):
    """Slow-changing metadata and resolution fields for replay."""

    __tablename__ = "market_catalog"

    condition_id = Column(String, primary_key=True)
    market_id = Column(String)
    market_slug = Column(String)
    question = Column(String)
    event_id = Column(String, index=True)
    event_slug = Column(String, index=True)
    event_title = Column(String)
    event_market_count = Column(Integer)
    end_date = Column(String)
    outcomes_json = Column(String, nullable=False, default="[]")
    outcome_prices_json = Column(String, nullable=False, default="[]")
    token_ids_json = Column(String, nullable=False, default="[]")
    tags_json = Column(String, nullable=False, default="[]")
    neg_risk = Column(Integer)
    active = Column(Integer)
    closed = Column(Integer)
    accepting_orders = Column(Integer)
    enable_order_book = Column(Integer)
    fees_enabled = Column(Integer)
    fee_rate = Column(Float)
    resolution_status = Column(String)
    resolved_outcome = Column(String)
    resolved_value = Column(Float)
    resolved_at = Column(String)
    source_updated_at = Column(String)
    first_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class MarketSweep(Base):
    """Aggregate proof of a complete Gamma keyset traversal."""

    __tablename__ = "market_sweeps"

    sweep_id = Column(String, primary_key=True)
    schema_version = Column(Integer, nullable=False)
    run_id = Column(String, index=True)
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
    snapshot_eligible_count = Column(Integer, nullable=False)
    snapshotted_market_count = Column(Integer, nullable=False)
    membership_detail_stored = Column(
        Integer, nullable=False, default=1, server_default=text("1")
    )


class MarketSweepMembership(Base):
    """Per-condition derived archive decision for one sweep."""

    __tablename__ = "market_sweep_memberships"

    sweep_id = Column(
        String,
        ForeignKey("market_sweeps.sweep_id", ondelete="CASCADE"),
        primary_key=True,
    )
    condition_id = Column(String, primary_key=True, index=True)
    raw_seen_count = Column(Integer, nullable=False)
    qualified = Column(Integer, nullable=False)
    qualification_reason = Column(String, nullable=False)
    snapshot_eligible = Column(Integer, nullable=False)
    snapshotted = Column(Integer, nullable=False)
    snapshot_reason = Column(String, nullable=False)


class SkippedMarket(Base):
    __tablename__ = "skipped_markets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    reason = Column(String, nullable=False)
    skipped_at = Column(DateTime, default=datetime.utcnow, index=True)


_TRADE_MIGRATION_COLUMNS = {
    "event_id": "TEXT",
    "event_slug": "TEXT",
    "strategy_name": "TEXT",
    "mode": "TEXT",
    "volume_24h_at_buy": "REAL",
    "hypothetical_pnl": "REAL",
    "pnl_basis": "TEXT",
    "buy_confirmed_size": "REAL",
    "buy_confirmed_vwap": "REAL",
    "buy_confirmed_fee_usdc": "REAL",
    "sell_confirmed_size": "REAL",
    "sell_confirmed_vwap": "REAL",
    "sell_confirmed_fee_usdc": "REAL",
    "sell_fill_matched_at": "TEXT",
    "prior_yes_price_at_entry": "REAL",
    "yes_price_at_buy": "REAL",
    "stop_price_at_entry": "REAL",
    "take_profit_price_at_entry": "REAL",
    "entry_prob_min_at_buy": "REAL",
    "entry_prob_max_at_buy": "REAL",
    "entry_hours_min_at_buy": "REAL",
    "entry_hours_max_at_buy": "REAL",
    "entry_time_reference": "TEXT",
    "entry_deadline_at_buy": "DATETIME",
    "hours_until_entry_deadline_at_buy": "REAL",
    "market_game_start_time": "DATETIME",
    "minutes_until_game_start_at_buy": "REAL",
    "sports_market_type": "TEXT",
    "sports_phase_at_buy": "TEXT",
    "prior_snapshot_id_at_entry": "INTEGER",
    "entry_snapshot_id": "INTEGER",
    "best_bid_at_buy": "REAL",
    "best_ask_at_buy": "REAL",
    "spread_at_buy": "REAL",
    "book_depth_shares_at_buy": "REAL",
    "depth_limit_price_at_buy": "REAL",
    "yes_price_at_exit": "REAL",
    "best_bid_at_exit": "REAL",
    "best_ask_at_exit": "REAL",
    "spread_at_exit": "REAL",
    "resolution_outcome": "TEXT",
    "resolution_value": "REAL",
    "resolution_status": "TEXT",
    "resolution_observed_at": "DATETIME",
    "resolution_source_updated_at": "TEXT",
    "resolution_evidence": "TEXT",
    "resolution_confirmed_buy_size": "REAL",
    "resolution_confirmed_buy_vwap": "REAL",
    "resolution_confirmed_buy_fee_usdc": "REAL",
    "settlement_pnl_assumption": "REAL",
    "settlement_assumption_basis": "TEXT",
}


def init_database(
    db_path: str,
    maintenance_requirements: SQLiteMaintenanceRequirements | None = None,
) -> sessionmaker:
    """Create the schema and best-effort upgrade an existing local DB."""
    prepare_database(db_path, "golden-queen", requirements=maintenance_requirements)
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        for name, sql_type in _TRADE_MIGRATION_COLUMNS.items():
            try:
                connection.execute(text(f"ALTER TABLE trades ADD COLUMN {name} {sql_type}"))
                connection.commit()
            except Exception:
                pass
        for name, sql_type in {
            "best_bid": "REAL",
            "best_ask": "REAL",
            "spread": "REAL",
            "source_updated_at": "TEXT",
            "run_id": "TEXT",
        }.items():
            try:
                connection.execute(
                    text(f"ALTER TABLE market_snapshots ADD COLUMN {name} {sql_type}")
                )
                connection.commit()
            except Exception:
                pass
        try:
            connection.execute(
                text(
                    "ALTER TABLE market_sweeps ADD COLUMN "
                    "membership_detail_stored INTEGER NOT NULL DEFAULT 1"
                )
            )
            connection.commit()
        except Exception:
            pass
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS market_snapshots_condition_timestamp_idx "
                "ON market_snapshots(condition_id, timestamp)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS market_snapshots_run_idx "
                "ON market_snapshots(run_id)"
            )
        )
        connection.commit()
    return sessionmaker(bind=engine)
