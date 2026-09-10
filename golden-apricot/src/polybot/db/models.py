"""SQLite evidence models for Golden Apricot."""

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
_RAW_TABLE_NAMES = frozenset({"raw_book_cycles", "raw_book_observations", "raw_event_observations", "raw_tracked_events"})
STRATEGY_NAME = "golden-apricot"
STOP_SELL_QUARANTINE_REASON = (
    "stop_sell_reconciliation_timeout_3h_unknown_exposure"
)
STOP_SELL_LEDGER_QUARANTINE_REASON = (
    "stop_sell_execution_ledger_failure_unknown_exposure"
)
STOP_SELL_ISOLATION_REASONS = (
    STOP_SELL_QUARANTINE_REASON,
    STOP_SELL_LEDGER_QUARANTINE_REASON,
)
BUY_RECONCILIATION_QUARANTINE_REASON = (
    "buy_reconciliation_timeout_3h_unknown_exposure"
)
BUY_ISOLATION_REASONS = (BUY_RECONCILIATION_QUARANTINE_REASON,)


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
    """One submitted in-play match-result position and its observed evidence."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    market_slug = Column(String)
    question = Column(String)
    event_id = Column(String, index=True)
    event_slug = Column(String, index=True)
    outcome = Column(String, nullable=False, default="Yes")
    outcome_side = Column(String)
    result_kind = Column(String)
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
    # py-clob-client-v2 signs SELL maker size at two decimal shares.  Exact-$5
    # BUYs can create finer share precision, so an unavoidable sub-cent-share
    # residual must be explicit rather than disguised as a full close.
    sell_residual_shares = Column(Float)

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
    sport_family = Column(String, index=True)
    league_code = Column(String, index=True)
    league_name = Column(String)
    market_tags_json = Column(String)
    target_buy_amount_usdc = Column(Float)
    selected_buy_amount_usdc = Column(Float)
    max_executable_buy_notional_usdc = Column(Float)
    buy_notional_fallback_reason = Column(String)

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
    source_elapsed_minutes_at_buy = Column(Float)
    take_profit_delta_at_buy = Column(Float)
    stop_loss_delta_at_buy = Column(Float)
    late_exit_minute_at_buy = Column(Float)

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
    """Baseline-$5 outcome VWAP with sport, token, and sizing evidence."""

    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, nullable=False)
    event_id = Column(String, index=True)
    token_id = Column(String, nullable=False, default="legacy-unknown")
    outcome = Column(String, nullable=False, default="Unknown")
    outcome_side = Column(String)
    result_kind = Column(String)
    probability = Column(Float, nullable=False)
    midpoint = Column(Float)
    liquidity = Column(Float)
    volume_24h = Column(Float)
    best_bid = Column(Float)
    best_ask = Column(Float)
    spread = Column(Float)
    source_updated_at = Column(String)
    source_elapsed_minutes = Column(Float)
    source_clock_reason = Column(String)
    book_json = Column(String)
    execution_capacity_json = Column(String)
    run_id = Column(String)
    sport_family = Column(String, index=True)
    league_code = Column(String, index=True)
    league_name = Column(String)
    market_tags_json = Column(String)
    sport_profile_version = Column(String)
    book_shape = Column(String)
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
    game_start_time = Column(DateTime)
    in_play_hours = Column(Float)
    source_elapsed_minutes = Column(Float)
    execution_state = Column(String, nullable=False, default="OBSERVED")
    execution_reason = Column(String)
    last_attempted_at = Column(DateTime)


class ResolutionObservation(Base):
    """Append-only normalized CLOB one-hot settlement evidence."""

    __tablename__ = "resolution_observations"

    resolution_id = Column(String, primary_key=True)
    run_id = Column(String, index=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False, index=True)
    condition_id = Column(String, nullable=False, index=True)
    observed_at = Column(DateTime, nullable=False, index=True)
    source = Column(String, nullable=False)
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
    sport_family = Column(String, index=True)
    league_code = Column(String, index=True)
    league_name = Column(String)
    neg_risk = Column(Integer)
    active = Column(Integer)
    closed = Column(Integer)
    accepting_orders = Column(Integer)
    enable_order_book = Column(Integer)
    fees_enabled = Column(Integer)
    fee_rate = Column(Float)
    fee_exponent = Column(Integer)
    fee_taker_only = Column(Integer)
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


class RawBookCycle(Base):
    """Atomic raw collection publication; its run can independently fail later."""
    __tablename__ = "raw_book_cycles"
    run_id = Column(String, primary_key=True)
    config_hash = Column(String, nullable=False, index=True)
    strategy_source_digest = Column(String, nullable=False)
    job_name = Column(String, nullable=False)
    sport_family = Column(String, nullable=False)
    contract = Column(String, nullable=False)
    observed_at = Column(DateTime, nullable=False)
    published_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False)
    expected_events = Column(Integer, nullable=False)
    expected_tokens = Column(Integer, nullable=False)
    observed_tokens = Column(Integer, nullable=False)
    evidence_json = Column(String, nullable=False)


class RawBookObservation(Base):
    __tablename__ = "raw_book_observations"
    observation_id = Column(String, primary_key=True)
    run_id = Column(String, ForeignKey("raw_book_cycles.run_id"), nullable=False, index=True)
    event_id = Column(String, nullable=False, index=True)
    slot = Column(String, nullable=False)
    condition_id = Column(String)
    token_id = Column(String, index=True)
    status = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    requested_at = Column(DateTime)
    received_at = Column(DateTime)
    book_json = Column(String)
    book_sha256 = Column(String)


class RawEventObservation(Base):
    __tablename__ = "raw_event_observations"
    observation_id = Column(String, primary_key=True)
    run_id = Column(String, ForeignKey("raw_book_cycles.run_id"), nullable=False, index=True)
    event_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    expected_tokens = Column(Integer, nullable=False)
    identified_tokens = Column(Integer, nullable=False)
    observed_at = Column(DateTime, nullable=False)
    evidence_json = Column(String, nullable=False)


class RawTrackedEvent(Base):
    """Mutable scheduling index; every transition also has append-only evidence."""
    __tablename__ = "raw_tracked_events"
    tracking_id = Column(String, primary_key=True)
    job_name = Column(String, nullable=False, index=True)
    sport_family = Column(String, nullable=False)
    event_id = Column(String, nullable=False)
    state = Column(String, nullable=False, index=True)
    slots_json = Column(String, nullable=False)
    first_seen_at = Column(DateTime, nullable=False)
    last_checked_at = Column(DateTime, nullable=False, index=True)
    missing_count = Column(Integer, nullable=False, default=0)
    identity_attempt_count = Column(Integer, nullable=False, default=0)


def _install_raw_evidence_guards(connection) -> None:
    for table in ("raw_book_cycles", "raw_book_observations", "raw_event_observations"):
        _verify_model_columns(connection, table)
        primary_key = "run_id" if table == "raw_book_cycles" else "observation_id"
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS {table}_forbid_replace "
            f"BEFORE INSERT ON {table} WHEN EXISTS "
            f"(SELECT 1 FROM {table} WHERE {primary_key}=NEW.{primary_key}) BEGIN "
            "SELECT RAISE(ABORT, 'append-only raw evidence'); END"
        )
        for operation in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS {table}_forbid_{operation.lower()} "
                f"BEFORE {operation} ON {table} BEGIN "
                "SELECT RAISE(ABORT, 'append-only raw evidence'); END"
            )
    _verify_model_columns(connection, "raw_tracked_events")
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS raw_tracked_events_due_idx "
        "ON raw_tracked_events(job_name,state,last_checked_at,event_id)"
    )


_TRADE_MIGRATION_COLUMNS = {
    # The first Golden Apricot schema predates the additive evidence columns.
    # Keep every base column explicit so sparse legacy DBs migrate
    # deterministically; validation below still rejects incompatible affinity.
    "condition_id": "TEXT NOT NULL DEFAULT 'legacy-unknown-condition'",
    "market_slug": "TEXT",
    "question": "TEXT",
    "outcome": "TEXT NOT NULL DEFAULT 'Unknown'",
    "token_id": "TEXT NOT NULL DEFAULT 'legacy-unknown-token'",
    "buy_price": "REAL",
    "buy_amount": "REAL",
    "buy_shares": "REAL",
    "buy_order_id": "TEXT",
    "buy_timestamp": "DATETIME",
    "buy_probability": "REAL",
    "sell_price": "REAL",
    "sell_shares": "REAL",
    "sell_order_id": "TEXT",
    "sell_timestamp": "DATETIME",
    "sell_probability": "REAL",
    "realized_pnl": "REAL",
    "status": "TEXT",
    "entry_reason": "TEXT",
    "exit_reason": "TEXT",
    "market_end_date": "DATETIME",
    "hours_until_resolution_at_buy": "REAL",
    "liquidity_at_buy": "REAL",
    "market_tags": "TEXT",
    "created_at": "DATETIME",
    "updated_at": "DATETIME",
    "event_id": "TEXT",
    "event_slug": "TEXT",
    "outcome_side": "TEXT",
    "result_kind": "TEXT",
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
    "sell_residual_shares": "REAL",
    "prior_yes_price_at_entry": "REAL",
    "yes_price_at_buy": "REAL",
    "stop_price_at_entry": "REAL",
    "entry_prob_min_at_buy": "REAL",
    "entry_prob_max_at_buy": "REAL",
    "entry_hours_min_at_buy": "REAL",
    "entry_hours_max_at_buy": "REAL",
    "prior_snapshot_id_at_entry": "INTEGER",
    "entry_snapshot_id": "INTEGER",
    "source_elapsed_minutes_at_buy": "REAL",
    "take_profit_delta_at_buy": "REAL",
    "stop_loss_delta_at_buy": "REAL",
    "late_exit_minute_at_buy": "REAL",
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
    "sport_family": "TEXT",
    "league_code": "TEXT",
    "league_name": "TEXT",
    "market_tags_json": "TEXT",
    "target_buy_amount_usdc": "REAL",
    "selected_buy_amount_usdc": "REAL",
    "max_executable_buy_notional_usdc": "REAL",
    "buy_notional_fallback_reason": "TEXT",
}

_SNAPSHOT_MIGRATION_COLUMNS = {
    "token_id": "TEXT",
    "outcome": "TEXT",
    "event_id": "TEXT",
    "outcome_side": "TEXT",
    "result_kind": "TEXT",
    "midpoint": "REAL",
    "best_bid": "REAL",
    "best_ask": "REAL",
    "spread": "REAL",
    "source_updated_at": "TEXT",
    "source_elapsed_minutes": "REAL",
    "source_clock_reason": "TEXT",
    "book_json": "TEXT",
    "execution_capacity_json": "TEXT",
    "run_id": "TEXT",
    "sport_family": "TEXT",
    "league_code": "TEXT",
    "league_name": "TEXT",
    "market_tags_json": "TEXT",
    "sport_profile_version": "TEXT",
    "book_shape": "TEXT",
}

_ENTRY_EPISODE_MIGRATION_COLUMNS = {
    "game_start_time": "DATETIME",
    "in_play_hours": "REAL",
    "source_elapsed_minutes": "REAL",
    "execution_state": "TEXT NOT NULL DEFAULT 'OBSERVED'",
    "execution_reason": "TEXT",
    "last_attempted_at": "DATETIME",
}

_SWEEP_MIGRATION_COLUMNS = {
    "membership_detail_stored": "INTEGER NOT NULL DEFAULT 1",
}

_CATALOG_MIGRATION_COLUMNS = {
    "fee_exponent": "INTEGER",
    "fee_taker_only": "INTEGER",
    "sport_family": "TEXT",
    "league_code": "TEXT",
    "league_name": "TEXT",
}


def _table_info(connection, table_name: str) -> dict[str, tuple]:
    rows = connection.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    if not rows:
        raise RuntimeError(f"required SQLite table is missing: {table_name}")
    return {str(row[1]): tuple(row) for row in rows}


def _table_columns(connection, table_name: str) -> set[str]:
    return set(_table_info(connection, table_name))


def _sqlite_affinity(declared_type: str) -> str:
    normalized = declared_type.upper()
    if "INT" in normalized:
        return "INTEGER"
    if any(marker in normalized for marker in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in normalized or not normalized:
        return "BLOB"
    if any(marker in normalized for marker in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _model_affinity(column) -> str:
    if isinstance(column.type, Integer):
        return "INTEGER"
    if isinstance(column.type, Float):
        return "REAL"
    if isinstance(column.type, (String, Enum)):
        return "TEXT"
    if isinstance(column.type, DateTime):
        return "NUMERIC"
    raise RuntimeError(
        f"unsupported SQLite model type for {column.table.name}.{column.name}: "
        f"{column.type!r}"
    )


def _ensure_columns(connection, table_name: str, columns: dict[str, str]) -> None:
    """Apply only known additive migrations and verify every result."""
    existing = _table_columns(connection, table_name)
    for name, sql_type in columns.items():
        if name in existing:
            continue
        connection.execute(
            text(f"ALTER TABLE {table_name} ADD COLUMN {name} {sql_type}")
        )
        existing.add(name)
    missing = set(columns) - _table_columns(connection, table_name)
    if missing:
        raise RuntimeError(
            f"SQLite migration did not create {table_name} columns: "
            f"{sorted(missing)}"
        )


def _verify_model_columns(connection, table_name: str) -> None:
    model_table = Base.metadata.tables[table_name]
    expected = {column.name for column in model_table.columns}
    table_info = _table_info(connection, table_name)
    missing = expected - set(table_info)
    if missing:
        raise RuntimeError(
            f"incompatible {table_name} schema; missing columns: {sorted(missing)}"
        )
    mismatched = []
    for column in model_table.columns:
        declared_type = str(table_info[column.name][2])
        expected_affinity = _model_affinity(column)
        actual_affinity = _sqlite_affinity(declared_type)
        if actual_affinity != expected_affinity:
            mismatched.append(
                f"{column.name}: expected {expected_affinity}, "
                f"found {actual_affinity} ({declared_type or 'untyped'})"
            )
    if mismatched:
        raise RuntimeError(
            f"incompatible {table_name} schema; type mismatches: {mismatched}"
        )


def _upgrade_database_schema(connection) -> None:
    _ensure_columns(connection, "trades", _TRADE_MIGRATION_COLUMNS)
    _ensure_columns(connection, "market_snapshots", _SNAPSHOT_MIGRATION_COLUMNS)
    _ensure_columns(
        connection, "entry_episodes", _ENTRY_EPISODE_MIGRATION_COLUMNS
    )
    _ensure_columns(connection, "market_sweeps", _SWEEP_MIGRATION_COLUMNS)
    _ensure_columns(connection, "market_catalog", _CATALOG_MIGRATION_COLUMNS)
    for table_name in Base.metadata.tables:
        if table_name not in _RAW_TABLE_NAMES:
            _verify_model_columns(connection, table_name)
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
            "CREATE INDEX IF NOT EXISTS market_snapshots_sport_league_time_idx "
            "ON market_snapshots(sport_family, league_code, timestamp)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS trades_sport_league_time_idx "
            "ON trades(sport_family, league_code, buy_timestamp)"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "resolution_observations_trade_evidence_idx "
            "ON resolution_observations(trade_id, evidence_sha256)"
        )
    )
    connection.execute(
        text(
            "CREATE TRIGGER IF NOT EXISTS resolution_observations_forbid_update "
            "BEFORE UPDATE ON resolution_observations BEGIN "
            "SELECT RAISE(ABORT, 'append-only evidence'); END"
        )
    )
    connection.execute(
        text(
            "CREATE TRIGGER IF NOT EXISTS resolution_observations_forbid_delete "
            "BEFORE DELETE ON resolution_observations BEGIN "
            "SELECT RAISE(ABORT, 'append-only evidence'); END"
        )
    )


def init_database(
    db_path: str,
    maintenance_requirements: SQLiteMaintenanceRequirements | None = None,
    *,
    activate_compact_on_create: bool = True,
    maintenance_on_start: bool = True,
    enable_research_raw: bool = False,
) -> sessionmaker:
    """Create the schema and fail closed on an incomplete additive upgrade."""
    if maintenance_on_start:
        prepare_database(
            db_path,
            "golden-apricot",
            requirements=maintenance_requirements,
            activate_compact_on_create=activate_compact_on_create,
        )
    # Raw simulation archives grow throughout the experiment. Their hourly
    # maintenance must not consume a one-minute collection slot before HTTP.
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    try:
        Base.metadata.create_all(
            engine, tables=[table for name, table in Base.metadata.tables.items()
                            if enable_research_raw or name not in _RAW_TABLE_NAMES],
        )
        with engine.connect() as connection:
            # Python's sqlite3 legacy transaction mode does not begin a
            # transaction for DDL. Begin explicitly so every additive ALTER,
            # validation, index, and trigger either commits together or rolls
            # back together on an incompatible legacy schema.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                _upgrade_database_schema(connection)
                if enable_research_raw:
                    _install_raw_evidence_guards(connection)
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()
    except Exception:
        engine.dispose()
        raise
    return sessionmaker(bind=engine)
