"""SQLite evidence models for Golden Kiwi / Micro-Cascade."""

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
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker


Base = declarative_base()
STRATEGY_NAME = "kiwi"


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
    """One Micro-Cascade simulation position and its observed evidence."""

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
    entry_run_id = Column(String, index=True)
    exit_run_id = Column(String, index=True)

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
    trend_start_snapshot_id_at_entry = Column(Integer)
    entry_snapshot_id = Column(Integer)
    signal_timestamp_at_entry = Column(DateTime)
    trend_snapshot_ids_json = Column(String)
    trend_snapshot_timestamps_json = Column(String)
    trend_persisted_prices_json = Column(String)
    trend_decision_prices_json = Column(String)
    trend_gap_minutes_json = Column(String)
    trend_decision_timestamps_json = Column(String)
    trend_decision_gap_minutes_json = Column(String)
    decision_observed_at_at_entry = Column(DateTime)
    decision_price_source_at_entry = Column(String)
    trend_start_yes_price_at_entry = Column(Float)
    confirmation_steps_at_entry = Column(Integer)
    cumulative_move_at_entry = Column(Float)
    min_step_move_at_entry = Column(Float)
    max_step_move_at_entry = Column(Float)
    min_snapshot_gap_minutes_at_entry = Column(Float)
    max_snapshot_gap_minutes_at_entry = Column(Float)
    signal_best_bid_at_entry = Column(Float)
    signal_best_ask_at_entry = Column(Float)
    signal_spread_at_entry = Column(Float)
    hold_minutes_target_at_entry = Column(Float)
    hold_minutes_observed_at_exit = Column(Float)
    exit_delay_minutes = Column(Float)
    promotion_eligible = Column(Integer)
    promotion_exclusion_reason = Column(String)
    csv_exported_at = Column(DateTime)

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
    """Micro-Cascade research observation, always expressed as a YES price."""

    __tablename__ = "market_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "condition_id",
            name="uq_market_snapshots_run_condition",
        ),
    )

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
    # Immutable point-in-time catalog evidence. ``market_catalog`` remains a
    # latest-state convenience table and must never be used to replay history.
    catalog_event_id = Column(String)
    catalog_event_slug = Column(String)
    catalog_event_market_count = Column(Integer)
    catalog_end_date = Column(String)
    catalog_outcomes_json = Column(String)
    catalog_outcome_prices_json = Column(String)
    catalog_token_ids_json = Column(String)
    catalog_tags_json = Column(String)
    catalog_neg_risk = Column(Integer)
    catalog_active = Column(Integer)
    catalog_closed = Column(Integer)
    catalog_accepting_orders = Column(Integer)
    catalog_enable_order_book = Column(Integer)
    catalog_source_updated_at = Column(String)
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


class ExperimentState(Base):
    """Durable, append-by-key research controls.

    A drawdown stop is a finite-study stopping rule, not a live reflection of
    the current P&L.  Once the key exists, later settlement gains must not
    silently re-enable entries in a new process.
    """

    __tablename__ = "experiment_state"

    key = Column(String, primary_key=True)
    value_json = Column(String, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MicroCascadeExperimentContract(Base):
    """One immutable preregistered collection contract per canonical DB."""

    __tablename__ = "micro_cascade_experiment_contracts"

    canonical_job = Column(String, primary_key=True)
    schema_version = Column(Integer, nullable=False)
    analyzer_version = Column(Integer, nullable=False)
    preregistration_sha256 = Column(String, nullable=False)
    arm = Column(String, nullable=False)
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)
    expected_cadence_minutes = Column(Integer, nullable=False)
    expected_offset_minute = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MicroCascadeSignalDecision(Base):
    """Append-only raw signal, ranking, portfolio gate, and fresh-attempt proof."""

    __tablename__ = "micro_cascade_signal_decisions"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "condition_id",
            name="uq_micro_cascade_signal_run_condition",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, nullable=False, index=True)
    condition_id = Column(String, nullable=False, index=True)
    event_id = Column(String, nullable=False, index=True)
    token_id = Column(String, nullable=False)
    arm = Column(String, nullable=False)
    canonical_job = Column(String, nullable=False)
    collection_eligible = Column(Integer, nullable=False)
    scan_evaluated_at = Column(DateTime, nullable=False, index=True)

    trend_snapshot_ids_json = Column(String, nullable=False)
    trend_snapshot_timestamps_json = Column(String, nullable=False)
    trend_prices_json = Column(String, nullable=False)
    trend_gap_minutes_json = Column(String, nullable=False)
    entry_snapshot_id = Column(Integer, nullable=False)
    snapshot_probability = Column(Float, nullable=False)
    snapshot_best_bid = Column(Float, nullable=False)
    snapshot_best_ask = Column(Float, nullable=False)
    snapshot_spread = Column(Float, nullable=False)
    snapshot_liquidity = Column(Float, nullable=False)
    snapshot_volume_24h = Column(Float, nullable=False)
    market_end_date = Column(DateTime, nullable=False)

    event_sibling_count = Column(Integer, nullable=False)
    event_rank = Column(Integer, nullable=False)
    event_selected = Column(Integer, nullable=False)
    global_rank = Column(Integer)
    cooldown_allowed = Column(Integer, nullable=False)
    cooldown_reason = Column(String, nullable=False)
    position_count = Column(Integer, nullable=False)
    open_notional_usdc = Column(Float, nullable=False)
    drawdown_tripped = Column(Integer, nullable=False)
    raw_selected = Column(Integer, nullable=False, index=True)

    fresh_attempt_order = Column(Integer)
    fresh_attempted = Column(Integer, nullable=False)
    fresh_observed_at = Column(DateTime)
    fresh_best_bid = Column(Float)
    fresh_best_ask = Column(Float)
    fresh_spread = Column(Float)
    fresh_depth_shares = Column(Float)
    fresh_depth_limit_price = Column(Float)
    fresh_gate_passed = Column(Integer)
    fresh_fail_reason = Column(String, nullable=False)
    execution_selected = Column(Integer, nullable=False)
    trade_id = Column(Integer, ForeignKey("trades.id"))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MicroCascadeFollowupObservation(Base):
    """Append-only +60..75m raw Gamma follow-up for one raw-selected signal."""

    __tablename__ = "micro_cascade_followup_observations"
    __table_args__ = (
        UniqueConstraint(
            "decision_id",
            "observing_run_id",
            name="uq_micro_cascade_followup_decision_run",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    decision_id = Column(
        Integer,
        ForeignKey("micro_cascade_signal_decisions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    observing_run_id = Column(String, nullable=False, index=True)
    condition_id = Column(String, nullable=False, index=True)
    target_at = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)
    observed_at = Column(DateTime, nullable=False, index=True)
    market_seen = Column(Integer, nullable=False)
    source_available = Column(Integer, nullable=False)
    source_reason = Column(String, nullable=False)
    probability = Column(Float)
    best_bid = Column(Float)
    best_ask = Column(Float)
    liquidity = Column(Float)
    volume_24h = Column(Float)
    source_updated_at = Column(String)
    valid_quote = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


_TRADE_MIGRATION_COLUMNS = {
    "event_id": "TEXT",
    "event_slug": "TEXT",
    "strategy_name": "TEXT",
    "mode": "TEXT",
    "entry_run_id": "TEXT",
    "exit_run_id": "TEXT",
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
    "trend_start_snapshot_id_at_entry": "INTEGER",
    "entry_snapshot_id": "INTEGER",
    "signal_timestamp_at_entry": "DATETIME",
    "trend_snapshot_ids_json": "TEXT",
    "trend_snapshot_timestamps_json": "TEXT",
    "trend_persisted_prices_json": "TEXT",
    "trend_decision_prices_json": "TEXT",
    "trend_gap_minutes_json": "TEXT",
    "trend_decision_timestamps_json": "TEXT",
    "trend_decision_gap_minutes_json": "TEXT",
    "decision_observed_at_at_entry": "DATETIME",
    "decision_price_source_at_entry": "TEXT",
    "trend_start_yes_price_at_entry": "REAL",
    "confirmation_steps_at_entry": "INTEGER",
    "cumulative_move_at_entry": "REAL",
    "min_step_move_at_entry": "REAL",
    "max_step_move_at_entry": "REAL",
    "min_snapshot_gap_minutes_at_entry": "REAL",
    "max_snapshot_gap_minutes_at_entry": "REAL",
    "signal_best_bid_at_entry": "REAL",
    "signal_best_ask_at_entry": "REAL",
    "signal_spread_at_entry": "REAL",
    "hold_minutes_target_at_entry": "REAL",
    "hold_minutes_observed_at_exit": "REAL",
    "exit_delay_minutes": "REAL",
    "promotion_eligible": "INTEGER",
    "promotion_exclusion_reason": "TEXT",
    "csv_exported_at": "DATETIME",
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
    *,
    activate_compact_on_create: bool = True,
) -> sessionmaker:
    """Create the schema and best-effort upgrade an existing local DB."""
    prepare_database(
        db_path,
        "golden-kiwi",
        requirements=maintenance_requirements,
        activate_compact_on_create=activate_compact_on_create,
    )
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
            "catalog_event_id": "TEXT",
            "catalog_event_slug": "TEXT",
            "catalog_event_market_count": "INTEGER",
            "catalog_end_date": "TEXT",
            "catalog_outcomes_json": "TEXT",
            "catalog_outcome_prices_json": "TEXT",
            "catalog_token_ids_json": "TEXT",
            "catalog_tags_json": "TEXT",
            "catalog_neg_risk": "INTEGER",
            "catalog_active": "INTEGER",
            "catalog_closed": "INTEGER",
            "catalog_accepting_orders": "INTEGER",
            "catalog_enable_order_book": "INTEGER",
            "catalog_source_updated_at": "TEXT",
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
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS trades_entry_run_idx "
                "ON trades(entry_run_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS trades_exit_run_idx "
                "ON trades(exit_run_id)"
            )
        )
        for table in (
            "micro_cascade_experiment_contracts",
            "micro_cascade_signal_decisions",
            "micro_cascade_followup_observations",
        ):
            connection.execute(
                text(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_append_only_update "
                    f"BEFORE UPDATE ON {table} BEGIN "
                    "SELECT RAISE(ABORT, 'append-only evidence'); END"
                )
            )
            connection.execute(
                text(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_append_only_delete "
                    f"BEFORE DELETE ON {table} BEGIN "
                    "SELECT RAISE(ABORT, 'append-only evidence'); END"
                )
            )
        connection.commit()
    return sessionmaker(bind=engine)
