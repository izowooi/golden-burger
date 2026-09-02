"""SQLite evidence models for Golden Plum."""

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
    Index,
    Integer,
    String,
    create_engine,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker


Base = declarative_base()
STRATEGY_NAME = "golden-plum"
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
    # ``sell_shares`` and ``realized_pnl`` are cumulative confirmed values.
    # These fields describe only the latest in-flight SELL request so a
    # confirmed partial TP can return to HOLDING without losing its remainder.
    pending_sell_requested_shares = Column(Float)
    pending_sell_remaining_shares = Column(Float)
    confirmed_sell_count = Column(Integer, nullable=False, default=0)
    cumulative_sell_proceeds_usdc = Column(Float)
    cumulative_sell_fee_usdc = Column(Float)
    cumulative_buy_fee_allocated_usdc = Column(Float)
    last_exit_observation_id = Column(Integer)

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
    take_profit_price_at_buy = Column(Float)
    stop_loss_delta_at_buy = Column(Float)
    late_exit_minute_at_buy = Column(Float)
    force_exit_minute_at_buy = Column(Float)
    trend_start_snapshot_id = Column(Integer)
    trend_middle_snapshot_id = Column(Integer)
    trend_observations = Column(Integer)
    trend_cumulative_move = Column(Float)
    trend_max_pullback = Column(Float)
    trend_elapsed_seconds = Column(Float)

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
    resolution_remaining_shares = Column(Float)
    settlement_pnl_assumption = Column(Float)
    settlement_assumption_basis = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        price = f"{self.buy_price:.2%}" if self.buy_price is not None else "N/A"
        status = self.status.value if self.status is not None else "unknown"
        return f"<Trade {self.id}: {self.outcome} @ {price} -> {status}>"


class MarketSnapshot(Base):
    """Exact-$5 outcome VWAP with explicit token/outcome identity."""

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
    config_hash = Column(String, index=True)
    sport_family = Column(String, index=True)
    league_code = Column(String, index=True)
    league_name = Column(String)
    market_tags_json = Column(String)
    sport_profile_version = Column(String)
    protocol_sha256 = Column(String)
    classifier_version = Column(String)
    league_mapping_sha256 = Column(String)
    strategy_source_digest = Column(String)
    book_shape = Column(String)
    event_cycle_id = Column(String, index=True)
    event_set_complete = Column(Integer)
    event_set_reason = Column(String)
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
    trend_start_snapshot_id = Column(Integer)
    trend_middle_snapshot_id = Column(Integer)
    trend_observations = Column(Integer)
    trend_cumulative_move = Column(Float)
    trend_max_pullback = Column(Float)
    trend_elapsed_seconds = Column(Float)
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


class ExitExecutionObservation(Base):
    """Append-only fresh-book evidence for one TP or full-stop decision."""

    __tablename__ = "exit_execution_observations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, index=True)
    config_hash = Column(String, index=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False, index=True)
    condition_id = Column(String, nullable=False, index=True)
    event_id = Column(String, index=True)
    token_id = Column(String, nullable=False, index=True)
    observed_at = Column(DateTime, nullable=False, index=True)
    signal = Column(String, nullable=False, index=True)
    trigger_price = Column(Float, nullable=False)
    sport_family = Column(String, index=True)
    league_code = Column(String, index=True)
    position_shares = Column(Float, nullable=False)
    selected_shares = Column(Float, nullable=False)
    remaining_shares = Column(Float, nullable=False)
    max_executable_shares = Column(Float, nullable=False)
    selected_notional_usdc = Column(Float, nullable=False)
    max_executable_notional_usdc = Column(Float, nullable=False)
    best_bid = Column(Float, nullable=False)
    best_ask = Column(Float)
    spread = Column(Float)
    vwap = Column(Float, nullable=False)
    limit_price = Column(Float, nullable=False)
    levels_used = Column(Integer, nullable=False)
    fallback_reason = Column(String, nullable=False)
    full_position_required = Column(Integer, nullable=False)
    book_sha256 = Column(String, nullable=False)
    book_json = Column(String, nullable=False)


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
    fee_exponent = Column(Integer)
    fee_taker_only = Column(Integer)
    resolution_status = Column(String)
    resolved_outcome = Column(String)
    resolved_value = Column(Float)
    resolved_at = Column(String)
    source_updated_at = Column(String)
    config_hash = Column(String, index=True)
    sport_family = Column(String, index=True)
    league_code = Column(String, index=True)
    league_name = Column(String)
    sport_profile_version = Column(String)
    protocol_sha256 = Column(String)
    classifier_version = Column(String)
    league_mapping_sha256 = Column(String)
    strategy_source_digest = Column(String)
    book_shape = Column(String)
    last_event_cycle_id = Column(String)
    last_event_set_complete = Column(Integer)
    last_event_set_reason = Column(String)
    last_live_sweep_id = Column(String)
    last_live_seen_at = Column(DateTime, index=True)
    followup_status = Column(String, index=True)
    followup_attempt_count = Column(Integer, nullable=False, default=0)
    followup_last_attempt_at = Column(DateTime)
    followup_next_attempt_at = Column(DateTime, index=True)
    followup_last_error = Column(String)
    resolution_evidence_json = Column(String)
    resolution_evidence_sha256 = Column(String)
    resolution_observed_at = Column(DateTime)
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
    config_hash = Column(String, index=True)
    sport_family = Column(String, index=True)
    sport_profile_version = Column(String)
    protocol_sha256 = Column(String)
    classifier_version = Column(String)
    league_mapping_sha256 = Column(String)
    strategy_source_digest = Column(String)
    book_shape = Column(String)
    expected_result_kinds_json = Column(String)
    expected_market_count = Column(Integer)
    expected_token_count = Column(Integer)
    event_count = Column(Integer)
    complete_event_count = Column(Integer)
    incomplete_event_count = Column(Integer)
    event_evidence_digest_sha256 = Column(String)


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
    event_id = Column(String, index=True)
    event_cycle_id = Column(String, index=True)
    event_set_complete = Column(Integer)
    event_set_reason = Column(String)


class EventCycleEvidence(Base):
    """One fail-closed complete-set decision for a sweep event.

    A condition-level snapshot is not sufficient evidence for a soccer triad or
    a direct two-team moneyline.  This row preserves the exact event population
    seen in one cycle, including missing and duplicate identities.
    """

    __tablename__ = "event_cycle_evidence"

    event_cycle_id = Column(String, primary_key=True)
    sweep_id = Column(
        String,
        ForeignKey("market_sweeps.sweep_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = Column(String, index=True)
    config_hash = Column(String, index=True)
    event_id = Column(String, nullable=False, index=True)
    observed_at = Column(DateTime, nullable=False, index=True)
    sport_family = Column(String, nullable=False, index=True)
    sport_profile_version = Column(String, nullable=False)
    protocol_sha256 = Column(String, nullable=False)
    classifier_version = Column(String, nullable=False)
    league_mapping_sha256 = Column(String, nullable=False)
    strategy_source_digest = Column(String, nullable=False)
    book_shape = Column(String, nullable=False)
    expected_result_kinds_json = Column(String, nullable=False)
    observed_result_kinds_json = Column(String, nullable=False)
    missing_result_kinds_json = Column(String, nullable=False)
    condition_ids_json = Column(String, nullable=False)
    token_ids_json = Column(String, nullable=False)
    expected_market_count = Column(Integer, nullable=False)
    observed_market_count = Column(Integer, nullable=False)
    expected_token_count = Column(Integer, nullable=False)
    observed_token_count = Column(Integer, nullable=False)
    duplicate_condition_count = Column(Integer, nullable=False)
    duplicate_token_count = Column(Integer, nullable=False)
    duplicate_identity_count = Column(Integer, nullable=False)
    complete = Column(Integer, nullable=False, index=True)
    reason = Column(String, nullable=False)
    evidence_sha256 = Column(String, nullable=False)

    __table_args__ = (
        Index(
            "event_cycle_evidence_sweep_event_idx",
            "sweep_id",
            "event_id",
            unique=True,
        ),
    )


class TrackedResolutionObservation(Base):
    """Append-only terminal one-hot evidence independent of a bot order."""

    __tablename__ = "tracked_resolution_observations"

    resolution_id = Column(String, primary_key=True)
    condition_id = Column(String, nullable=False, index=True)
    event_id = Column(String, index=True)
    run_id = Column(String, index=True)
    config_hash = Column(String, index=True)
    sport_family = Column(String, nullable=False, index=True)
    sport_profile_version = Column(String, nullable=False)
    protocol_sha256 = Column(String, nullable=False)
    classifier_version = Column(String, nullable=False)
    league_mapping_sha256 = Column(String, nullable=False)
    strategy_source_digest = Column(String, nullable=False)
    observed_at = Column(DateTime, nullable=False, index=True)
    source = Column(String, nullable=False)
    winner_index = Column(Integer, nullable=False)
    winner_token_id = Column(String, nullable=False)
    winner_outcome = Column(String, nullable=False)
    payouts_json = Column(String, nullable=False)
    evidence_sha256 = Column(String, nullable=False)
    evidence_json = Column(String, nullable=False)

    __table_args__ = (
        Index(
            "tracked_resolution_condition_evidence_idx",
            "condition_id",
            "evidence_sha256",
            unique=True,
        ),
    )


class SkippedMarket(Base):
    __tablename__ = "skipped_markets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    condition_id = Column(String, index=True, nullable=False)
    reason = Column(String, nullable=False)
    skipped_at = Column(DateTime, default=datetime.utcnow, index=True)


_TRADE_MIGRATION_COLUMNS = {
    # The first Golden Plum schema predates the additive lineage migrations.
    # Keep its base columns explicit here so even a sparse legacy fixture is
    # upgraded deterministically instead of relying on create_all() (which
    # never alters an existing table).
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
    "pending_sell_requested_shares": "REAL",
    "pending_sell_remaining_shares": "REAL",
    "confirmed_sell_count": "INTEGER NOT NULL DEFAULT 0",
    "cumulative_sell_proceeds_usdc": "REAL",
    "cumulative_sell_fee_usdc": "REAL",
    "cumulative_buy_fee_allocated_usdc": "REAL",
    "last_exit_observation_id": "INTEGER",
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
    "take_profit_price_at_buy": "REAL",
    "stop_loss_delta_at_buy": "REAL",
    "late_exit_minute_at_buy": "REAL",
    "force_exit_minute_at_buy": "REAL",
    "trend_start_snapshot_id": "INTEGER",
    "trend_middle_snapshot_id": "INTEGER",
    "trend_observations": "INTEGER",
    "trend_cumulative_move": "REAL",
    "trend_max_pullback": "REAL",
    "trend_elapsed_seconds": "REAL",
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
    "resolution_remaining_shares": "REAL",
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
    "config_hash": "TEXT",
    "sport_family": "TEXT",
    "league_code": "TEXT",
    "league_name": "TEXT",
    "market_tags_json": "TEXT",
    "sport_profile_version": "TEXT",
    "protocol_sha256": "TEXT",
    "classifier_version": "TEXT",
    "league_mapping_sha256": "TEXT",
    "strategy_source_digest": "TEXT",
    "book_shape": "TEXT",
    "event_cycle_id": "TEXT",
    "event_set_complete": "INTEGER",
    "event_set_reason": "TEXT",
}

_ENTRY_EPISODE_MIGRATION_COLUMNS = {
    "game_start_time": "DATETIME",
    "in_play_hours": "REAL",
    "source_elapsed_minutes": "REAL",
    "trend_start_snapshot_id": "INTEGER",
    "trend_middle_snapshot_id": "INTEGER",
    "trend_observations": "INTEGER",
    "trend_cumulative_move": "REAL",
    "trend_max_pullback": "REAL",
    "trend_elapsed_seconds": "REAL",
    "execution_state": "TEXT NOT NULL DEFAULT 'OBSERVED'",
    "execution_reason": "TEXT",
    "last_attempted_at": "DATETIME",
}

_SWEEP_MIGRATION_COLUMNS = {
    "membership_detail_stored": "INTEGER NOT NULL DEFAULT 1",
    "config_hash": "TEXT",
    "sport_family": "TEXT",
    "sport_profile_version": "TEXT",
    "protocol_sha256": "TEXT",
    "classifier_version": "TEXT",
    "league_mapping_sha256": "TEXT",
    "strategy_source_digest": "TEXT",
    "book_shape": "TEXT",
    "expected_result_kinds_json": "TEXT",
    "expected_market_count": "INTEGER",
    "expected_token_count": "INTEGER",
    "event_count": "INTEGER",
    "complete_event_count": "INTEGER",
    "incomplete_event_count": "INTEGER",
    "event_evidence_digest_sha256": "TEXT",
}

_SWEEP_MEMBERSHIP_MIGRATION_COLUMNS = {
    "event_id": "TEXT",
    "event_cycle_id": "TEXT",
    "event_set_complete": "INTEGER",
    "event_set_reason": "TEXT",
}

_CATALOG_MIGRATION_COLUMNS = {
    "fee_exponent": "INTEGER",
    "fee_taker_only": "INTEGER",
    "config_hash": "TEXT",
    "sport_family": "TEXT",
    "league_code": "TEXT",
    "league_name": "TEXT",
    "sport_profile_version": "TEXT",
    "protocol_sha256": "TEXT",
    "classifier_version": "TEXT",
    "league_mapping_sha256": "TEXT",
    "strategy_source_digest": "TEXT",
    "book_shape": "TEXT",
    "last_event_cycle_id": "TEXT",
    "last_event_set_complete": "INTEGER",
    "last_event_set_reason": "TEXT",
    "last_live_sweep_id": "TEXT",
    "last_live_seen_at": "DATETIME",
    "followup_status": "TEXT",
    "followup_attempt_count": "INTEGER NOT NULL DEFAULT 0",
    "followup_last_attempt_at": "DATETIME",
    "followup_next_attempt_at": "DATETIME",
    "followup_last_error": "TEXT",
    "resolution_evidence_json": "TEXT",
    "resolution_evidence_sha256": "TEXT",
    "resolution_observed_at": "DATETIME",
}

_EVENT_CYCLE_MIGRATION_COLUMNS = {
    "duplicate_identity_count": "INTEGER NOT NULL DEFAULT 0",
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
    """Apply only known additive migrations and verify every result.

    The previous broad ``except Exception: pass`` treated permission errors,
    malformed tables, and a genuinely failed migration as if a column already
    existed.  Inspecting first makes duplicate-column handling deterministic;
    any other SQLite failure now aborts startup.
    """

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


def init_database(
    db_path: str,
    maintenance_requirements: SQLiteMaintenanceRequirements | None = None,
    *,
    activate_compact_on_create: bool = True,
) -> sessionmaker:
    """Create the schema and fail closed on an incomplete additive upgrade."""
    prepare_database(
        db_path,
        "golden-plum",
        requirements=maintenance_requirements,
        activate_compact_on_create=activate_compact_on_create,
    )
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        _ensure_columns(connection, "trades", _TRADE_MIGRATION_COLUMNS)
        _ensure_columns(
            connection, "market_snapshots", _SNAPSHOT_MIGRATION_COLUMNS
        )
        _ensure_columns(
            connection, "entry_episodes", _ENTRY_EPISODE_MIGRATION_COLUMNS
        )
        _ensure_columns(connection, "market_sweeps", _SWEEP_MIGRATION_COLUMNS)
        _ensure_columns(
            connection,
            "market_sweep_memberships",
            _SWEEP_MEMBERSHIP_MIGRATION_COLUMNS,
        )
        _ensure_columns(connection, "market_catalog", _CATALOG_MIGRATION_COLUMNS)
        _ensure_columns(
            connection,
            "event_cycle_evidence",
            _EVENT_CYCLE_MIGRATION_COLUMNS,
        )
        for table_name in (
            "trades",
            "market_snapshots",
            "entry_episodes",
            "market_catalog",
            "market_sweeps",
            "market_sweep_memberships",
            "event_cycle_evidence",
            "tracked_resolution_observations",
            "exit_execution_observations",
        ):
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
                "CREATE INDEX IF NOT EXISTS trades_sport_league_buy_time_idx "
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
        connection.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS tracked_resolution_forbid_update "
                "BEFORE UPDATE ON tracked_resolution_observations BEGIN "
                "SELECT RAISE(ABORT, 'append-only evidence'); END"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS tracked_resolution_forbid_delete "
                "BEFORE DELETE ON tracked_resolution_observations BEGIN "
                "SELECT RAISE(ABORT, 'append-only evidence'); END"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS exit_execution_forbid_update "
                "BEFORE UPDATE ON exit_execution_observations BEGIN "
                "SELECT RAISE(ABORT, 'append-only evidence'); END"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS exit_execution_forbid_delete "
                "BEFORE DELETE ON exit_execution_observations BEGIN "
                "SELECT RAISE(ABORT, 'append-only evidence'); END"
            )
        )
    return sessionmaker(bind=engine)
