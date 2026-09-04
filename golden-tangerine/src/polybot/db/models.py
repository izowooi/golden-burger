"""SQLite evidence models for Golden Tangerine / Sports Resolution Hold Live."""

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
STRATEGY_NAME = "golden-tangerine"


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
    """One submitted Sports Resolution Hold Live position and its observed evidence."""

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
    entry_prob_min_at_buy = Column(Float)
    entry_prob_max_at_buy = Column(Float)
    entry_hours_min_at_buy = Column(Float)
    entry_hours_max_at_buy = Column(Float)
    prior_snapshot_id_at_entry = Column(Integer)
    entry_snapshot_id = Column(Integer)

    # Fresh executable-book observations.
    best_bid_at_buy = Column(Float)
    best_ask_at_buy = Column(Float)
    spread_at_buy = Column(Float)
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
        return f"<Trade {self.id}: {self.outcome} @ {price} -> {status}>"


class MarketSnapshot(Base):
    """Configured-notional outcome VWAP with explicit token/outcome identity."""

    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, nullable=False)
    token_id = Column(String, nullable=False, default="legacy-unknown")
    outcome = Column(String, nullable=False, default="Unknown")
    probability = Column(Float, nullable=False)
    liquidity = Column(Float)
    volume_24h = Column(Float)
    best_bid = Column(Float)
    best_ask = Column(Float)
    spread = Column(Float)
    source_updated_at = Column(String)
    run_id = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)


class EntryEpisode(Base):
    """Durable first observation in this job's pre-registered price arm."""

    __tablename__ = "entry_episodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_id = Column(String, unique=True, nullable=False, index=True)
    condition_id = Column(String, nullable=False, index=True)
    event_id = Column(String, index=True)
    outcome = Column(String, nullable=False)
    entry_snapshot_id = Column(Integer, nullable=False)
    exact_vwap = Column(Float, nullable=False)
    arm_prob_min = Column(Float, nullable=False)
    arm_prob_max = Column(Float, nullable=False)
    observed_at = Column(DateTime, nullable=False)
    trade_id = Column(Integer)
    # The latest queue decision is denormalized for restart-safe capacity and
    # operator inspection.  Every transition is also written append-only to
    # ``entry_candidate_events``.
    execution_state = Column(String, nullable=False, default="QUEUED_PROVEN_NO_POST")
    execution_reason = Column(String)
    execution_updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EntryCandidateEvent(Base):
    """Append-only evidence for each first-band candidate and POST boundary."""

    __tablename__ = "entry_candidate_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    episode_id = Column(Integer, ForeignKey("entry_episodes.id"), nullable=False, index=True)
    run_id = Column(String, index=True)
    observed_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    state = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    proven_no_post = Column(Integer, nullable=False)
    post_may_have_occurred = Column(Integer, nullable=False)
    trade_id = Column(Integer)
    order_id = Column(String)


class ResolutionObservation(Base):
    """Append-only normalized CLOB one-hot settlement evidence."""

    __tablename__ = "resolution_observations"

    resolution_id = Column(String, primary_key=True)
    run_id = Column(String, index=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False, index=True)
    condition_id = Column(String, nullable=False, index=True)
    observed_at = Column(DateTime, nullable=False, index=True)
    source = Column(String, nullable=False)
    settlement_kind = Column(String, nullable=False, default="ONE_HOT")
    winner_index = Column(Integer, nullable=False)
    winner_token_id = Column(String, nullable=False)
    winner_outcome = Column(String, nullable=False)
    selected_token_id = Column(String, nullable=False)
    selected_outcome = Column(String, nullable=False)
    selected_payout = Column(Float, nullable=False)
    evidence_sha256 = Column(String, nullable=False)
    evidence_json = Column(String, nullable=False)


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


class CycleRuntimeEvent(Base):
    """Append-only wall-clock telemetry; it never enforces a process kill."""

    __tablename__ = "cycle_runtime_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cycle_id = Column(String, nullable=False, index=True)
    run_id = Column(String, index=True)
    phase = Column(String, nullable=False)
    status = Column(String, nullable=False)
    observed_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    elapsed_seconds = Column(Float, nullable=False)
    detail_json = Column(String, nullable=False, default="{}")


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
    "entry_prob_min_at_buy": "REAL",
    "entry_prob_max_at_buy": "REAL",
    "entry_hours_min_at_buy": "REAL",
    "entry_hours_max_at_buy": "REAL",
    "prior_snapshot_id_at_entry": "INTEGER",
    "entry_snapshot_id": "INTEGER",
    "best_bid_at_buy": "REAL",
    "best_ask_at_buy": "REAL",
    "spread_at_buy": "REAL",
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

_ENTRY_EPISODE_MIGRATION_COLUMNS = {
    "execution_state": "TEXT NOT NULL DEFAULT 'QUEUED_PROVEN_NO_POST'",
    "execution_reason": "TEXT",
    "execution_updated_at": "DATETIME",
}

_RESOLUTION_OBSERVATION_MIGRATION_COLUMNS = {
    "settlement_kind": "TEXT NOT NULL DEFAULT 'ONE_HOT'",
}


def _sqlite_affinity(declared_type: str) -> str:
    value = str(declared_type or "").upper()
    if "INT" in value:
        return "INTEGER"
    if any(item in value for item in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in value or not value:
        return "BLOB"
    if any(item in value for item in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _ensure_columns(connection, table_name: str, columns: dict[str, str]) -> None:
    """Apply additive migrations and reject incompatible existing columns."""
    info = {
        str(row[1]): str(row[2] or "")
        for row in connection.execute(text(f"PRAGMA table_info({table_name})"))
    }
    if not info:
        raise RuntimeError(f"required table is unavailable after create_all: {table_name}")
    for name, declaration in columns.items():
        expected_type = declaration.split()[0]
        if name not in info:
            connection.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {name} {declaration}")
            )
            continue
        if _sqlite_affinity(info[name]) != _sqlite_affinity(expected_type):
            raise RuntimeError(
                f"incompatible schema for {table_name}.{name}: "
                f"{info[name]} != {expected_type}"
            )


def _validate_model_schema(connection) -> None:
    """Fail closed when an existing same-name table cannot host this model."""
    for table in Base.metadata.sorted_tables:
        info = {
            str(row[1]): str(row[2] or "")
            for row in connection.execute(text(f"PRAGMA table_info({table.name})"))
        }
        if not info:
            raise RuntimeError(f"required table is missing: {table.name}")
        missing = [column.name for column in table.columns if column.name not in info]
        if missing:
            raise RuntimeError(
                f"incompatible schema for {table.name}; missing columns: {missing}"
            )
        for column in table.columns:
            expected = str(column.type)
            if _sqlite_affinity(info[column.name]) != _sqlite_affinity(expected):
                raise RuntimeError(
                    f"incompatible schema for {table.name}.{column.name}: "
                    f"{info[column.name]} != {expected}"
                )


def init_database(
    db_path: str,
    maintenance_requirements: SQLiteMaintenanceRequirements | None = None,
    *,
    activate_compact_on_create: bool = True,
) -> sessionmaker:
    """Create the schema and fail closed on incompatible existing layouts."""
    prepare_database(
        db_path,
        "golden-tangerine",
        requirements=maintenance_requirements,
        activate_compact_on_create=activate_compact_on_create,
    )
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        _ensure_columns(connection, "trades", _TRADE_MIGRATION_COLUMNS)
        _ensure_columns(connection, "market_snapshots", {
            "token_id": "TEXT",
            "outcome": "TEXT",
            "best_bid": "REAL",
            "best_ask": "REAL",
            "spread": "REAL",
            "source_updated_at": "TEXT",
            "run_id": "TEXT",
        })
        _ensure_columns(
            connection,
            "market_sweeps",
            {"membership_detail_stored": "INTEGER NOT NULL DEFAULT 1"},
        )
        _ensure_columns(connection, "entry_episodes", _ENTRY_EPISODE_MIGRATION_COLUMNS)
        _ensure_columns(
            connection,
            "resolution_observations",
            _RESOLUTION_OBSERVATION_MIGRATION_COLUMNS,
        )
        # Existing rows predate the queue timestamp but are still legitimate.
        connection.execute(
            text(
                "UPDATE entry_episodes SET execution_updated_at = "
                "COALESCE(execution_updated_at, observed_at)"
            )
        )
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
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "resolution_observations_trade_evidence_idx "
                "ON resolution_observations(trade_id, evidence_sha256)"
            )
        )
        for table_name in ("resolution_observations", "entry_candidate_events", "cycle_runtime_events"):
            connection.execute(
                text(
                    f"CREATE TRIGGER IF NOT EXISTS {table_name}_forbid_update "
                    f"BEFORE UPDATE ON {table_name} BEGIN "
                    "SELECT RAISE(ABORT, 'append-only evidence'); END"
                )
            )
            connection.execute(
                text(
                    f"CREATE TRIGGER IF NOT EXISTS {table_name}_forbid_delete "
                    f"BEFORE DELETE ON {table_name} BEGIN "
                    "SELECT RAISE(ABORT, 'append-only evidence'); END"
                )
            )
        _validate_model_schema(connection)
        connection.commit()
    return sessionmaker(bind=engine)
