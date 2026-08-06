"""SQLite repository for immutable ``research-full-v1`` evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Callable, Iterator
from uuid import uuid4
import zlib

from ..config import RESEARCH_DATA_CONTRACT, StorageConfig
from ..utils.retry import canonical_json, utc_now


SCHEMA_VERSION = 4
GIB = 1024**3


def _fsync_file(path: Path) -> None:
    """Durably flush one already-created regular file."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    """Durably flush directory entries used by a shard handoff."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


FACT_TABLES = (
    "collection_contracts",
    "research_config_versions",
    "research_run_events",
    "api_requests",
    "raw_payloads",
    "source_component_runs",
    "market_sweeps",
    "market_sweep_memberships",
    "market_observations",
    "outcome_observations",
    "market_metadata_versions",
    "orderbook_selections",
    "orderbook_token_attempts",
    "orderbook_snapshots",
    "orderbook_levels",
    "orderbook_depth_metrics",
    "resolution_observations",
    "trade_tape_sweeps",
    "trade_tape_windows",
    "trade_tape_memberships",
    "trade_observations",
    "data_quality_issues",
    "storage_metrics",
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS collection_contracts (
    contract_name TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    database_utc_date TEXT NOT NULL,
    prior_trade_watermark_epoch INTEGER,
    prior_trade_bootstrap_start_epoch INTEGER,
    prior_census_condition_count INTEGER NOT NULL DEFAULT 0,
    prior_census_digest_sha256 TEXT,
    metadata_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (contract_name = 'research-full-v1')
);

CREATE TABLE IF NOT EXISTS research_config_versions (
    config_hash TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode = 'sim'),
    config_json TEXT NOT NULL,
    strategy_source_digest TEXT NOT NULL,
    git_commit TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_run_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED', 'SUCCEEDED', 'FAILED')),
    event_at TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    job_name TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode = 'sim'),
    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode = 'archive_only'),
    config_hash TEXT NOT NULL REFERENCES research_config_versions(config_hash),
    strategy_source_digest TEXT NOT NULL,
    git_commit TEXT NOT NULL,
    cycle_stats_json TEXT,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (run_id, event_type)
);
CREATE INDEX IF NOT EXISTS research_run_events_time_idx
    ON research_run_events(event_at, run_id);

CREATE TABLE IF NOT EXISTS api_requests (
    request_id TEXT PRIMARY KEY,
    run_id TEXT,
    sweep_attempt_id TEXT,
    request_kind TEXT NOT NULL,
    page_number INTEGER,
    attempt_number INTEGER NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('GET', 'POST')),
    url TEXT NOT NULL,
    params_json TEXT NOT NULL,
    body_sha256 TEXT,
    request_hash TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    elapsed_ms REAL,
    status TEXT NOT NULL,
    http_status INTEGER,
    retryable INTEGER NOT NULL,
    retry_after_seconds REAL,
    response_sha256 TEXT,
    response_bytes INTEGER,
    error_type TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS api_requests_attempt_idx
    ON api_requests(sweep_attempt_id, request_kind, page_number, attempt_number);
CREATE INDEX IF NOT EXISTS api_requests_hash_idx ON api_requests(request_hash);

CREATE TABLE IF NOT EXISTS raw_payloads (
    payload_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES api_requests(request_id),
    payload_kind TEXT NOT NULL,
    content_encoding TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    uncompressed_bytes INTEGER NOT NULL,
    compressed_bytes INTEGER,
    blob_stored INTEGER NOT NULL,
    payload_blob BLOB,
    recorded_at TEXT NOT NULL,
    CHECK ((blob_stored = 1 AND payload_blob IS NOT NULL) OR
           (blob_stored = 0 AND payload_blob IS NULL))
);

CREATE TABLE IF NOT EXISTS source_component_runs (
    component_run_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    cycle_number INTEGER NOT NULL,
    component TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    requested_count INTEGER,
    observed_count INTEGER,
    error_count INTEGER NOT NULL DEFAULT 0,
    possible_gap INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS source_component_run_idx
    ON source_component_runs(run_id, component);

CREATE TABLE IF NOT EXISTS market_sweeps (
    sweep_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    cycle_number INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    cursor_complete INTEGER NOT NULL CHECK (cursor_complete = 1),
    page_count INTEGER NOT NULL,
    raw_market_count INTEGER NOT NULL,
    unique_condition_count INTEGER NOT NULL,
    missing_condition_id_count INTEGER NOT NULL,
    duplicate_condition_count INTEGER NOT NULL,
    request_attestation_json TEXT NOT NULL,
    request_attestation_sha256 TEXT NOT NULL,
    membership_digest_sha256 TEXT NOT NULL,
    raw_payload_page_count INTEGER NOT NULL,
    data_contract TEXT NOT NULL CHECK (data_contract = 'research-full-v1')
);
CREATE UNIQUE INDEX IF NOT EXISTS market_sweeps_cycle_idx
    ON market_sweeps(cycle_number);

CREATE TABLE IF NOT EXISTS market_observations (
    observation_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    cycle_number INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    item_number INTEGER NOT NULL,
    page_received_at TEXT NOT NULL,
    page_request_id TEXT NOT NULL,
    source_market_key TEXT NOT NULL,
    condition_id TEXT,
    market_id TEXT,
    event_id TEXT,
    event_slug TEXT,
    market_slug TEXT,
    question TEXT,
    volume_total_raw TEXT,
    volume_total REAL,
    volume_24h_raw TEXT,
    volume_24h REAL,
    volume_1h_raw TEXT,
    volume_1h REAL,
    volume_week_raw TEXT,
    volume_week REAL,
    volume_month_raw TEXT,
    volume_month REAL,
    volume_year_raw TEXT,
    volume_year REAL,
    liquidity_raw TEXT,
    liquidity REAL,
    liquidity_variants_json TEXT NOT NULL,
    outcome_prices_json TEXT NOT NULL,
    best_bid REAL,
    best_ask REAL,
    spread REAL,
    last_trade_price REAL,
    price_changes_json TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    game_start_time TEXT,
    created_at_source TEXT,
    updated_at_source TEXT,
    tags_json TEXT NOT NULL,
    sports_json TEXT NOT NULL,
    category TEXT,
    active INTEGER,
    closed INTEGER,
    enable_order_book INTEGER,
    accepting_orders INTEGER,
    neg_risk INTEGER,
    fees_enabled INTEGER,
    fee_metadata_json TEXT NOT NULL,
    tick_size_raw TEXT,
    min_order_size_raw TEXT,
    source_clocks_json TEXT NOT NULL,
    parse_quality_json TEXT NOT NULL,
    raw_market_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS market_obs_condition_time_idx
    ON market_observations(condition_id, page_received_at);
CREATE INDEX IF NOT EXISTS market_obs_run_idx ON market_observations(run_id);

CREATE TABLE IF NOT EXISTS market_sweep_memberships (
    membership_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    observation_id TEXT NOT NULL REFERENCES market_observations(observation_id),
    membership_ordinal INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    item_number INTEGER NOT NULL,
    page_received_at TEXT NOT NULL,
    source_market_key TEXT NOT NULL,
    condition_id TEXT,
    market_id TEXT,
    event_id TEXT,
    raw_market_sha256 TEXT NOT NULL,
    duplicate_ordinal INTEGER NOT NULL,
    UNIQUE (sweep_id, membership_ordinal)
);

CREATE TABLE IF NOT EXISTS outcome_observations (
    outcome_observation_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES market_observations(observation_id),
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    outcome_index INTEGER NOT NULL,
    outcome_label TEXT,
    token_id TEXT,
    price_raw TEXT,
    price REAL,
    label_present INTEGER NOT NULL,
    token_present INTEGER NOT NULL,
    price_present INTEGER NOT NULL,
    UNIQUE (observation_id, outcome_index)
);

CREATE TABLE IF NOT EXISTS market_metadata_versions (
    metadata_version_id TEXT PRIMARY KEY,
    source_market_key TEXT NOT NULL,
    condition_id TEXT,
    market_id TEXT,
    content_sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    first_observed_sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    first_observed_at TEXT NOT NULL,
    UNIQUE (source_market_key, content_sha256)
);

CREATE TABLE IF NOT EXISTS orderbook_selections (
    selection_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    cycle_number INTEGER NOT NULL,
    collection_id TEXT NOT NULL,
    source_market_key TEXT NOT NULL,
    condition_id TEXT,
    market_id TEXT,
    selection_reason TEXT NOT NULL,
    sampler_version TEXT NOT NULL,
    frame_market_count INTEGER NOT NULL,
    bucket_candidate_count INTEGER NOT NULL,
    bucket_visit_index INTEGER NOT NULL,
    sampler_slot INTEGER NOT NULL,
    rotation_offset INTEGER NOT NULL,
    wrap_around INTEGER NOT NULL,
    sample_max INTEGER NOT NULL,
    sampled_market_count INTEGER NOT NULL,
    truncated_count INTEGER NOT NULL,
    truncation_applied INTEGER NOT NULL,
    inclusion_probability_basis TEXT NOT NULL,
    long_run_coverage_basis TEXT NOT NULL,
    bucket_number INTEGER NOT NULL,
    bucket_count INTEGER NOT NULL,
    selection_rank TEXT NOT NULL,
    token_ids_json TEXT NOT NULL,
    outcome_labels_json TEXT NOT NULL,
    expected_token_count INTEGER NOT NULL,
    observed_token_count INTEGER NOT NULL,
    coverage_ratio REAL NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    selected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orderbook_token_attempts (
    token_attempt_id TEXT PRIMARY KEY,
    selection_id TEXT NOT NULL REFERENCES orderbook_selections(selection_id),
    run_id TEXT NOT NULL,
    cycle_number INTEGER NOT NULL,
    collection_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    outcome_index INTEGER,
    outcome_label TEXT,
    status TEXT NOT NULL CHECK (status IN ('OBSERVED', 'EMPTY_BOOK', 'MISSING', 'ERROR')),
    request_id TEXT,
    raw_payload_id TEXT REFERENCES raw_payloads(payload_id),
    received_at TEXT NOT NULL,
    bid_level_count INTEGER NOT NULL,
    ask_level_count INTEGER NOT NULL,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (selection_id, token_id)
);
CREATE INDEX IF NOT EXISTS orderbook_token_attempt_status_idx
    ON orderbook_token_attempts(status, received_at);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    selection_id TEXT NOT NULL REFERENCES orderbook_selections(selection_id),
    run_id TEXT NOT NULL,
    cycle_number INTEGER NOT NULL,
    token_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    request_id TEXT NOT NULL,
    raw_payload_id TEXT NOT NULL REFERENCES raw_payloads(payload_id),
    source_timestamp TEXT,
    source_hash TEXT,
    market TEXT,
    best_bid REAL,
    best_ask REAL,
    spread REAL,
    last_trade_price REAL,
    tick_size_raw TEXT,
    min_order_size_raw TEXT,
    neg_risk INTEGER,
    raw_book_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS orderbook_snapshot_token_idx
    ON orderbook_snapshots(token_id, received_at);

CREATE TABLE IF NOT EXISTS orderbook_levels (
    level_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),
    side TEXT NOT NULL CHECK (side IN ('BID', 'ASK')),
    level_index INTEGER NOT NULL,
    price_raw TEXT NOT NULL,
    price REAL NOT NULL,
    size_raw TEXT NOT NULL,
    size REAL NOT NULL,
    UNIQUE (snapshot_id, side, level_index)
);

CREATE TABLE IF NOT EXISTS orderbook_depth_metrics (
    metric_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    target_notional REAL NOT NULL,
    filled_notional REAL NOT NULL,
    base_quantity REAL NOT NULL,
    vwap_price REAL,
    worst_price REAL,
    complete INTEGER NOT NULL,
    levels_consumed INTEGER NOT NULL,
    UNIQUE (snapshot_id, side, target_notional)
);

CREATE TABLE IF NOT EXISTS resolution_watchlist (
    condition_id TEXT PRIMARY KEY,
    market_id TEXT,
    source_market_key TEXT NOT NULL,
    first_seen_sweep_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    selection_reason TEXT NOT NULL
    ,carried_from_utc_date TEXT
    ,prior_state_json TEXT
    ,terminal INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS prior_census_conditions (
    condition_id TEXT PRIMARY KEY,
    source_market_key TEXT NOT NULL,
    market_id TEXT,
    prior_sweep_id TEXT NOT NULL,
    carried_from_utc_date TEXT NOT NULL,
    carried_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resolution_observations (
    resolution_observation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    cycle_number INTEGER NOT NULL,
    condition_id TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    lookup_status TEXT NOT NULL,
    request_id TEXT,
    market_id TEXT,
    resolved INTEGER,
    closed INTEGER,
    one_hot INTEGER,
    one_hot_outcome_index INTEGER,
    one_hot_outcome_label TEXT,
    resolution_value_raw TEXT,
    resolution_source_raw TEXT,
    redeemable INTEGER,
    source_updated_at TEXT,
    source_end_date TEXT,
    outcome_prices_json TEXT NOT NULL,
    raw_market_sha256 TEXT,
    raw_market_json TEXT,
    error_type TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS resolution_condition_time_idx
    ON resolution_observations(condition_id, observed_at);

CREATE TABLE IF NOT EXISTS trade_tape_sweeps (
    trade_sweep_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    cycle_number INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    target_start_epoch INTEGER NOT NULL,
    source_target_end_epoch INTEGER NOT NULL,
    bounded_target_end_epoch INTEGER NOT NULL,
    watermark_before_epoch INTEGER,
    watermark_advance_to_epoch INTEGER,
    status TEXT NOT NULL,
    possible_gap INTEGER NOT NULL,
    window_count INTEGER NOT NULL,
    membership_count INTEGER NOT NULL,
    unique_trade_count INTEGER NOT NULL,
    head_timestamp_raw TEXT,
    tail_timestamp_raw TEXT,
    membership_digest_sha256 TEXT NOT NULL,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS trade_tape_windows (
    window_id TEXT PRIMARY KEY,
    trade_sweep_id TEXT NOT NULL REFERENCES trade_tape_sweeps(trade_sweep_id),
    parent_window_id TEXT,
    start_epoch INTEGER NOT NULL,
    end_epoch INTEGER NOT NULL,
    split_depth INTEGER NOT NULL,
    offset INTEGER NOT NULL,
    request_id TEXT,
    raw_payload_id TEXT,
    received_at TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    membership_count INTEGER NOT NULL,
    economic_unique_count INTEGER NOT NULL,
    duplicate_economic_row_count INTEGER NOT NULL,
    membership_digest_sha256 TEXT NOT NULL,
    hit_cap INTEGER NOT NULL,
    status TEXT NOT NULL,
    possible_gap INTEGER NOT NULL,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS trade_observations (
    trade_id TEXT PRIMARY KEY,
    economic_row_hash TEXT NOT NULL,
    occurrence_index INTEGER NOT NULL,
    side TEXT,
    asset TEXT,
    condition_id TEXT,
    size_raw TEXT,
    size REAL,
    price_raw TEXT,
    price REAL,
    timestamp_raw TEXT,
    timestamp_epoch REAL,
    transaction_hash TEXT,
    proxy_wallet TEXT,
    outcome TEXT,
    outcome_index_raw TEXT,
    outcome_index INTEGER,
    sanitized_trade_json TEXT NOT NULL,
    first_received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trade_observation_economic_idx
    ON trade_observations(economic_row_hash, occurrence_index);

CREATE TABLE IF NOT EXISTS trade_tape_memberships (
    membership_id TEXT PRIMARY KEY,
    trade_sweep_id TEXT NOT NULL REFERENCES trade_tape_sweeps(trade_sweep_id),
    window_id TEXT NOT NULL REFERENCES trade_tape_windows(window_id),
    trade_id TEXT NOT NULL REFERENCES trade_observations(trade_id),
    item_number INTEGER NOT NULL,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trade_membership_hash_idx
    ON trade_tape_memberships(trade_id, received_at);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    issue_id TEXT PRIMARY KEY,
    run_id TEXT,
    cycle_number INTEGER,
    component TEXT NOT NULL,
    severity TEXT NOT NULL,
    issue_code TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_metrics (
    storage_metric_id TEXT PRIMARY KEY,
    run_id TEXT,
    cycle_number INTEGER,
    phase TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    db_bytes INTEGER NOT NULL,
    wal_bytes INTEGER NOT NULL,
    shm_bytes INTEGER NOT NULL,
    logical_bytes INTEGER NOT NULL,
    filesystem_total_bytes INTEGER NOT NULL,
    filesystem_used_bytes INTEGER NOT NULL,
    filesystem_free_bytes INTEGER NOT NULL,
    filesystem_used_ratio REAL NOT NULL,
    recent_growth_bytes_per_cycle REAL NOT NULL,
    forecast_next_day_bytes REAL NOT NULL,
    forecast_days_to_stop REAL,
    guard_state TEXT NOT NULL
);
"""


class ResearchRepository:
    """Single-writer repository with atomic census publication."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        busy_timeout_ms: int = 30_000,
        immutable_reads: bool = False,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.busy_timeout_ms = busy_timeout_ms
        self.immutable_reads = bool(immutable_reads)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self.immutable_reads:
            raise RuntimeError("immutable read-only repository cannot open a writer")
        connection = sqlite3.connect(
            self.db_path, timeout=self.busy_timeout_ms / 1000, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _read_connect(self) -> Iterator[sqlite3.Connection]:
        """Open an inspection connection that cannot mutate SQLite headers."""
        with self._read_connect_path(
            self.db_path, immutable=self.immutable_reads
        ) as connection:
            yield connection

    @contextmanager
    def _read_connect_path(
        self, path: Path, *, immutable: bool = False
    ) -> Iterator[sqlite3.Connection]:
        """Open any shard read-only without changing its journal state."""
        uri = f"{path.resolve().as_uri()}?mode=ro"
        if immutable:
            uri += "&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(
        self,
        *,
        database_utc_date: str | None = None,
        prior_trade_watermark_epoch: int | None = None,
        prior_trade_bootstrap_start_epoch: int | None = None,
        carried_watchlist: Iterable[Mapping[str, Any]] = (),
        carried_census: Iterable[Mapping[str, Any]] = (),
        carried_from_utc_date: str | None = None,
        contract_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            # Every fact table is immutable append-only evidence. Corrections are
            # new rows with explicit lineage; UPDATE/DELETE is never provenance.
            for table in FACT_TABLES:
                connection.executescript(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_append_only_update
                    BEFORE UPDATE ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, 'append-only evidence cannot be updated');
                    END;
                    CREATE TRIGGER IF NOT EXISTS {table}_append_only_delete
                    BEFORE DELETE ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, 'append-only evidence cannot be deleted');
                    END;
                    """
                )
            date_value = database_utc_date or self._now().date().isoformat()
            metadata = {
                **dict(contract_metadata or {}),
                "contract": RESEARCH_DATA_CONTRACT,
                "schema_version": SCHEMA_VERSION,
                "journal_mode": "WAL",
                "synchronous": "FULL",
                "fact_policy": "append-only evidence",
                "compact_v1": False,
            }
            metadata_json = canonical_json(metadata)
            census_rows = [dict(row) for row in carried_census]
            census_digest = (
                hashlib.sha256(
                    canonical_json(
                        [
                            {
                                "condition_id": row.get("condition_id"),
                                "source_market_key": row.get("source_market_key"),
                                "market_id": row.get("market_id"),
                            }
                            for row in sorted(
                                census_rows,
                                key=lambda item: str(item.get("condition_id")),
                            )
                        ]
                    ).encode()
                ).hexdigest()
                if census_rows
                else None
            )
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO collection_contracts
                    (contract_name, schema_version, database_utc_date,
                     prior_trade_watermark_epoch,
                     prior_trade_bootstrap_start_epoch,
                     prior_census_condition_count,
                     prior_census_digest_sha256, metadata_json, content_sha256,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    RESEARCH_DATA_CONTRACT,
                    SCHEMA_VERSION,
                    date_value,
                    prior_trade_watermark_epoch,
                    prior_trade_bootstrap_start_epoch,
                    len(census_rows),
                    census_digest,
                    metadata_json,
                    hashlib.sha256(metadata_json.encode()).hexdigest(),
                    utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM collection_contracts WHERE contract_name = ?",
                (RESEARCH_DATA_CONTRACT,),
            ).fetchone()
            if (
                row is None
                or row["schema_version"] != SCHEMA_VERSION
                or row["database_utc_date"] != date_value
            ):
                connection.rollback()
                raise RuntimeError("incompatible research collection contract")
            if contract_metadata is not None and row["metadata_json"] != metadata_json:
                connection.rollback()
                raise RuntimeError(
                    "research shard contract metadata changed within one UTC day; "
                    "use a new job/DB or wait for UTC shard rotation"
                )
            self._insert_many(
                connection,
                "prior_census_conditions",
                (
                    "condition_id",
                    "source_market_key",
                    "market_id",
                    "prior_sweep_id",
                    "carried_from_utc_date",
                    "carried_at",
                ),
                (
                    {
                        **item,
                        "carried_from_utc_date": carried_from_utc_date or date_value,
                        "carried_at": utc_now(),
                    }
                    for item in census_rows
                ),
                or_ignore=True,
            )
            self._insert_many(
                connection,
                "resolution_watchlist",
                (
                    "condition_id",
                    "market_id",
                    "source_market_key",
                    "first_seen_sweep_id",
                    "first_seen_at",
                    "selection_reason",
                    "carried_from_utc_date",
                    "prior_state_json",
                    "terminal",
                ),
                carried_watchlist,
                or_ignore=True,
            )
            connection.commit()

    def record_research_run_start(
        self,
        *,
        config_row: Mapping[str, Any],
        event_row: Mapping[str, Any],
    ) -> None:
        """Append a config version and STARTED event without mutable run rows."""
        config_columns = (
            "config_hash",
            "schema_version",
            "strategy_name",
            "mode",
            "config_json",
            "strategy_source_digest",
            "git_commit",
            "first_seen_at",
        )
        event_columns = (
            "event_id",
            "run_id",
            "event_type",
            "event_at",
            "strategy_name",
            "job_name",
            "mode",
            "lifecycle_mode",
            "config_hash",
            "strategy_source_digest",
            "git_commit",
            "cycle_stats_json",
            "error_type",
            "error_message",
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_many(
                connection,
                "research_config_versions",
                config_columns,
                [config_row],
                or_ignore=True,
            )
            self._insert_many(
                connection,
                "research_run_events",
                event_columns,
                [event_row],
            )
            connection.commit()

    def record_research_run_event(self, event_row: Mapping[str, Any]) -> None:
        """Append one terminal run event; prior events remain immutable."""
        columns = (
            "event_id",
            "run_id",
            "event_type",
            "event_at",
            "strategy_name",
            "job_name",
            "mode",
            "lifecycle_mode",
            "config_hash",
            "strategy_source_digest",
            "git_commit",
            "cycle_stats_json",
            "error_type",
            "error_message",
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_many(connection, "research_run_events", columns, [event_row])
            connection.commit()

    def record_api_request(self, record: Mapping[str, Any]) -> None:
        columns = (
            "request_id",
            "run_id",
            "sweep_attempt_id",
            "request_kind",
            "page_number",
            "attempt_number",
            "method",
            "url",
            "params_json",
            "body_sha256",
            "request_hash",
            "started_at",
            "completed_at",
            "elapsed_ms",
            "status",
            "http_status",
            "retryable",
            "retry_after_seconds",
            "response_sha256",
            "response_bytes",
            "error_type",
            "error_message",
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"INSERT INTO api_requests ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(record.get(column) for column in columns),
            )
            connection.commit()

    def record_raw_payload(
        self,
        *,
        request_id: str,
        kind: str,
        content: bytes,
        store_blob: bool,
    ) -> str:
        exact = bytes(content)
        compressed = zlib.compress(exact, level=6) if store_blob else None
        payload_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO raw_payloads
                    (payload_id, request_id, payload_kind, content_encoding,
                     payload_sha256, uncompressed_bytes, compressed_bytes,
                     blob_stored, payload_blob, recorded_at)
                VALUES (?, ?, ?, 'zlib', ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload_id,
                    request_id,
                    kind,
                    hashlib.sha256(exact).hexdigest(),
                    len(exact),
                    len(compressed) if compressed is not None else None,
                    int(store_blob),
                    compressed,
                    utc_now(),
                ),
            )
            connection.commit()
        return payload_id

    def record_quality_issue(
        self,
        *,
        component: str,
        severity: str,
        issue_code: str,
        details: Mapping[str, Any],
        run_id: str | None = None,
        cycle_number: int | None = None,
    ) -> str:
        issue_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO data_quality_issues
                    (issue_id, run_id, cycle_number, component, severity,
                     issue_code, observed_at, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    issue_id,
                    run_id,
                    cycle_number,
                    component,
                    severity,
                    issue_code,
                    utc_now(),
                    canonical_json(dict(details)),
                ),
            )
            connection.commit()
        return issue_id

    def next_cycle_number(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(cycle_number), 0) FROM source_component_runs"
            ).fetchone()
        return int(row[0]) + 1

    def latest_trade_watermark(self) -> int | None:
        with self._read_connect() as connection:
            row = connection.execute(
                """
                SELECT MAX(watermark_advance_to_epoch)
                FROM trade_tape_sweeps
                WHERE status IN ('SUCCESS', 'EMPTY') AND possible_gap = 0
                """
            ).fetchone()
            if row[0] is not None:
                return int(row[0])
            contract = connection.execute(
                "SELECT prior_trade_watermark_epoch FROM collection_contracts LIMIT 1"
            ).fetchone()
            return int(contract[0]) if contract and contract[0] is not None else None

    def latest_trade_bootstrap_start(self) -> int | None:
        """Return the stable first-lookback baseline until a watermark exists."""
        if self.latest_trade_watermark() is not None:
            return None
        with self._read_connect() as connection:
            row = connection.execute(
                "SELECT MIN(target_start_epoch) FROM trade_tape_sweeps "
                "WHERE watermark_before_epoch IS NULL"
            ).fetchone()
            if row and row[0] is not None:
                return int(row[0])
            contract = connection.execute(
                "SELECT prior_trade_bootstrap_start_epoch "
                "FROM collection_contracts LIMIT 1"
            ).fetchone()
            return int(contract[0]) if contract and contract[0] is not None else None

    def latest_census_conditions(self) -> dict[str, dict[str, Any]]:
        """Return the latest complete census identity baseline, including shard carry."""
        with self._read_connect() as connection:
            latest = connection.execute(
                "SELECT sweep_id FROM market_sweeps ORDER BY cycle_number DESC LIMIT 1"
            ).fetchone()
            if latest is not None:
                rows = connection.execute(
                    """
                    SELECT condition_id, MIN(source_market_key) AS source_market_key,
                           MIN(market_id) AS market_id, ? AS prior_sweep_id
                    FROM market_sweep_memberships
                    WHERE sweep_id = ? AND condition_id IS NOT NULL
                    GROUP BY condition_id
                    """,
                    (latest[0], latest[0]),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT condition_id, source_market_key, market_id, prior_sweep_id
                    FROM prior_census_conditions
                    """
                ).fetchall()
        return {
            str(row["condition_id"]): {
                "condition_id": str(row["condition_id"]),
                "source_market_key": str(row["source_market_key"]),
                "market_id": row["market_id"],
                "prior_sweep_id": str(row["prior_sweep_id"]),
            }
            for row in rows
        }

    def select_resolution_watchlist(
        self, limit: int, *, now: datetime | None = None
    ) -> list[str]:
        now_value = (now or self._now()).astimezone(timezone.utc)
        with self._read_connect() as connection:
            rows = connection.execute(
                """
                WITH ranked AS (
                    SELECT resolution.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY condition_id ORDER BY observed_at DESC
                           ) AS position
                    FROM resolution_observations AS resolution
                )
                SELECT watch.condition_id, watch.first_seen_at,
                       watch.prior_state_json, seen.observed_at,
                       seen.lookup_status, seen.one_hot, seen.redeemable
                FROM resolution_watchlist AS watch
                LEFT JOIN ranked AS seen
                  ON seen.condition_id = watch.condition_id AND seen.position = 1
                WHERE watch.terminal = 0
                  AND NOT (COALESCE(seen.one_hot, 0) = 1
                           AND COALESCE(seen.redeemable, 0) = 1)
                ORDER BY watch.first_seen_at, watch.condition_id
                """,
            ).fetchall()
        due: list[tuple[datetime, str]] = []
        for row in rows:
            observed_at = row["observed_at"]
            lookup_status = row["lookup_status"]
            one_hot = row["one_hot"]
            redeemable = row["redeemable"]
            if observed_at is None and row["prior_state_json"]:
                try:
                    prior = json.loads(str(row["prior_state_json"]))
                except (TypeError, json.JSONDecodeError):
                    prior = {}
                if isinstance(prior, Mapping):
                    observed_at = prior.get("observed_at")
                    lookup_status = prior.get("lookup_status")
                    one_hot = prior.get("one_hot")
                    redeemable = prior.get("redeemable")
            if one_hot == 1 and redeemable == 1:
                continue
            if observed_at is None:
                due.append(
                    (
                        datetime.min.replace(tzinfo=timezone.utc),
                        str(row["condition_id"]),
                    )
                )
                continue
            try:
                observed = datetime.fromisoformat(
                    str(observed_at).replace("Z", "+00:00")
                )
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=timezone.utc)
                observed = observed.astimezone(timezone.utc)
            except ValueError:
                due.append(
                    (
                        datetime.min.replace(tzinfo=timezone.utc),
                        str(row["condition_id"]),
                    )
                )
                continue
            backoff_hours = 1 if lookup_status in {"ERROR", "MISSING"} else 6
            if (now_value - observed).total_seconds() >= backoff_hours * 3600:
                due.append((observed, str(row["condition_id"])))
        due.sort(key=lambda item: (item[0], item[1]))
        return [condition_id for _, condition_id in due[:limit]]

    @staticmethod
    def _raw_array(raw: Any) -> list[Any]:
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return []
            return parsed if isinstance(parsed, list) else []
        return []

    @classmethod
    def _validate_trade_window_lineage(cls, bundle: Mapping[str, Any]) -> None:
        trade_windows = list(bundle.get("trade_windows", []))
        windows_by_id: dict[str, Mapping[str, Any]] = {}
        for window in trade_windows:
            if not isinstance(window, Mapping):
                raise ValueError("trade windows must be mappings")
            window_id = str(window.get("window_id") or "")
            if not window_id or window_id in windows_by_id:
                raise ValueError("trade window IDs must be non-empty and unique")
            windows_by_id[window_id] = window
        for window in windows_by_id.values():
            depth = int(window.get("split_depth", -1))
            parent_id_raw = window.get("parent_window_id")
            parent_id = (
                str(parent_id_raw)
                if parent_id_raw is not None and str(parent_id_raw).strip()
                else None
            )
            if depth == 0:
                if parent_id is not None:
                    raise ValueError("root trade windows cannot have a parent")
                continue
            if depth < 0 or parent_id is None:
                raise ValueError("non-root trade windows require a parent")
            parent = windows_by_id.get(parent_id)
            if parent is None:
                raise ValueError("trade window parent must be in the same sweep")
            if int(parent.get("split_depth", -1)) != depth - 1:
                raise ValueError(
                    "trade window parent depth must be child depth minus one"
                )
            if str(parent.get("status")) != "SPLIT":
                raise ValueError("only SPLIT trade windows may have children")

    @classmethod
    def _validate_cycle_bundle(cls, bundle: Mapping[str, Any]) -> None:
        """Reject any census whose full raw→normalized lineage is incomplete."""
        sweep = bundle.get("market_sweep")
        if not isinstance(sweep, Mapping) or sweep.get("cursor_complete") != 1:
            raise ValueError("publish_cycle requires one cursor-complete Gamma census")
        observations = list(bundle.get("market_observations", []))
        memberships = list(bundle.get("market_memberships", []))
        outcomes = list(bundle.get("outcome_observations", []))
        expected_count = int(sweep.get("raw_market_count", -1))
        if expected_count != len(observations) or expected_count != len(memberships):
            raise ValueError(
                "raw_market_count must equal full market observations and memberships"
            )
        observation_ids = [str(row.get("observation_id")) for row in observations]
        if len(set(observation_ids)) != len(observation_ids):
            raise ValueError("market observation IDs must be unique")
        membership_ids = [str(row.get("observation_id")) for row in memberships]
        if sorted(membership_ids) != sorted(observation_ids):
            raise ValueError(
                "every market observation must have exactly one membership"
            )
        ordinals = [int(row.get("membership_ordinal", -1)) for row in memberships]
        if sorted(ordinals) != list(range(expected_count)):
            raise ValueError(
                "market memberships must be full contiguous sweep ordinals"
            )

        outcomes_by_observation: dict[str, list[int]] = {}
        for row in outcomes:
            outcomes_by_observation.setdefault(
                str(row.get("observation_id")), []
            ).append(int(row.get("outcome_index", -1)))
        for observation in observations:
            try:
                parse_quality = json.loads(
                    str(observation.get("parse_quality_json") or "")
                )
            except json.JSONDecodeError as error:
                raise ValueError("parse_quality_json must be valid JSON") from error
            if not isinstance(parse_quality, Mapping):
                raise ValueError("parse_quality_json must encode a mapping")
            raw_json = str(observation.get("_raw_market_json") or "")
            raw_sha = hashlib.sha256(raw_json.encode()).hexdigest()
            if raw_sha != observation.get("raw_market_sha256"):
                raise ValueError("raw market content hash mismatch")
            try:
                raw_market = json.loads(raw_json)
            except json.JSONDecodeError as error:
                raise ValueError("raw_market_json must be valid JSON") from error
            if not isinstance(raw_market, Mapping):
                raise ValueError("raw_market_json must encode a mapping")
            labels = cls._raw_array(raw_market.get("outcomes"))
            tokens = cls._raw_array(
                raw_market.get("clobTokenIds", raw_market.get("clob_token_ids"))
            )
            prices = cls._raw_array(
                raw_market.get("outcomePrices", raw_market.get("outcome_prices"))
            )
            expected_indexes = list(range(max(len(labels), len(tokens), len(prices))))
            actual_indexes = sorted(
                outcomes_by_observation.pop(str(observation["observation_id"]), [])
            )
            if actual_indexes != expected_indexes:
                raise ValueError(
                    "outcome observations must cover the exact raw label/token/price width"
                )
        if outcomes_by_observation:
            raise ValueError(
                "outcome observation references unknown market observation"
            )

        digest_scope = [
            {
                "ordinal": row["membership_ordinal"],
                "page": row["page_number"],
                "item": row["item_number"],
                "key": row["source_market_key"],
                "raw_sha256": row["raw_market_sha256"],
            }
            for row in sorted(
                memberships, key=lambda item: int(item["membership_ordinal"])
            )
        ]
        digest = hashlib.sha256(canonical_json(digest_scope).encode()).hexdigest()
        if digest != sweep.get("membership_digest_sha256"):
            raise ValueError("market membership digest mismatch")
        attestation_json = str(sweep.get("request_attestation_json") or "")
        if hashlib.sha256(attestation_json.encode()).hexdigest() != sweep.get(
            "request_attestation_sha256"
        ):
            raise ValueError("request attestation hash mismatch")
        raw_pages = [
            row
            for row in bundle.get("raw_payloads", [])
            if row.get("payload_kind") == "gamma_markets_keyset_page"
        ]
        if len(raw_pages) != int(sweep.get("page_count", -1)):
            raise ValueError("research-full-v1 requires one raw payload per Gamma page")
        if any(
            row.get("blob_stored") != 1 or row.get("payload_blob") is None
            for row in raw_pages
        ):
            raise ValueError("every Gamma page must preserve its compressed raw blob")

    @staticmethod
    def _insert_many(
        connection: sqlite3.Connection,
        table: str,
        columns: tuple[str, ...],
        rows: Iterable[Mapping[str, Any]],
        *,
        or_ignore: bool = False,
    ) -> None:
        payload = [tuple(row.get(column) for column in columns) for row in rows]
        if not payload:
            return
        verb = "INSERT OR IGNORE" if or_ignore else "INSERT"
        connection.executemany(
            f"{verb} INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            payload,
        )

    def publish_cycle(self, bundle: Mapping[str, Any]) -> None:
        """Compatibility entry point: atomically publish one complete full bundle."""
        self._publish_bundle(bundle, validate_gamma=True, require_prior_gamma=False)

    def publish_gamma_census(self, bundle: Mapping[str, Any]) -> None:
        """Commit a cursor-complete Gamma census before optional secondary work."""
        self._publish_bundle(bundle, validate_gamma=True, require_prior_gamma=False)

    def publish_secondary_cycle(self, bundle: Mapping[str, Any]) -> None:
        """Commit secondary component evidence linked to an existing Gamma cycle."""
        self._publish_bundle(bundle, validate_gamma=False, require_prior_gamma=True)

    def _publish_bundle(
        self,
        bundle: Mapping[str, Any],
        *,
        validate_gamma: bool,
        require_prior_gamma: bool,
    ) -> None:
        self._validate_trade_window_lineage(bundle)
        if validate_gamma:
            self._validate_cycle_bundle(bundle)
        run_id = str(bundle.get("run_id") or "")
        cycle_number = bundle.get("cycle_number")
        if require_prior_gamma and (not run_id or cycle_number is None):
            raise ValueError("secondary publish requires run_id and cycle_number")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if require_prior_gamma:
                    prior = connection.execute(
                        """
                        SELECT 1 FROM market_sweeps
                        WHERE run_id = ? AND cycle_number = ?
                        """,
                        (run_id, int(cycle_number)),
                    ).fetchone()
                    if prior is None:
                        raise ValueError(
                            "secondary publish requires an already committed Gamma census"
                        )
                self._insert_many(
                    connection,
                    "raw_payloads",
                    (
                        "payload_id",
                        "request_id",
                        "payload_kind",
                        "content_encoding",
                        "payload_sha256",
                        "uncompressed_bytes",
                        "compressed_bytes",
                        "blob_stored",
                        "payload_blob",
                        "recorded_at",
                    ),
                    bundle.get("raw_payloads", []),
                )
                self._insert_many(
                    connection,
                    "source_component_runs",
                    (
                        "component_run_id",
                        "run_id",
                        "cycle_number",
                        "component",
                        "status",
                        "started_at",
                        "completed_at",
                        "requested_count",
                        "observed_count",
                        "error_count",
                        "possible_gap",
                        "details_json",
                        "error_message",
                    ),
                    bundle.get("components", []),
                )
                sweep = bundle.get("market_sweep")
                if sweep is not None:
                    self._insert_many(
                        connection,
                        "market_sweeps",
                        (
                            "sweep_id",
                            "run_id",
                            "cycle_number",
                            "started_at",
                            "completed_at",
                            "cursor_complete",
                            "page_count",
                            "raw_market_count",
                            "unique_condition_count",
                            "missing_condition_id_count",
                            "duplicate_condition_count",
                            "request_attestation_json",
                            "request_attestation_sha256",
                            "membership_digest_sha256",
                            "raw_payload_page_count",
                            "data_contract",
                        ),
                        [sweep],
                    )
                self._insert_many(
                    connection,
                    "market_observations",
                    tuple(bundle.get("market_observation_columns", ())),
                    bundle.get("market_observations", []),
                )
                self._insert_many(
                    connection,
                    "market_sweep_memberships",
                    (
                        "membership_id",
                        "sweep_id",
                        "observation_id",
                        "membership_ordinal",
                        "page_number",
                        "item_number",
                        "page_received_at",
                        "source_market_key",
                        "condition_id",
                        "market_id",
                        "event_id",
                        "raw_market_sha256",
                        "duplicate_ordinal",
                    ),
                    bundle.get("market_memberships", []),
                )
                self._insert_many(
                    connection,
                    "outcome_observations",
                    (
                        "outcome_observation_id",
                        "observation_id",
                        "sweep_id",
                        "outcome_index",
                        "outcome_label",
                        "token_id",
                        "price_raw",
                        "price",
                        "label_present",
                        "token_present",
                        "price_present",
                    ),
                    bundle.get("outcome_observations", []),
                )
                self._insert_many(
                    connection,
                    "market_metadata_versions",
                    (
                        "metadata_version_id",
                        "source_market_key",
                        "condition_id",
                        "market_id",
                        "content_sha256",
                        "metadata_json",
                        "first_observed_sweep_id",
                        "first_observed_at",
                    ),
                    bundle.get("metadata_versions", []),
                    or_ignore=True,
                )
                self._insert_many(
                    connection,
                    "resolution_watchlist",
                    (
                        "condition_id",
                        "market_id",
                        "source_market_key",
                        "first_seen_sweep_id",
                        "first_seen_at",
                        "selection_reason",
                        "carried_from_utc_date",
                        "prior_state_json",
                        "terminal",
                    ),
                    bundle.get("watchlist_additions", []),
                    or_ignore=True,
                )
                self._insert_many(
                    connection,
                    "orderbook_selections",
                    (
                        "selection_id",
                        "run_id",
                        "cycle_number",
                        "collection_id",
                        "source_market_key",
                        "condition_id",
                        "market_id",
                        "selection_reason",
                        "sampler_version",
                        "frame_market_count",
                        "bucket_candidate_count",
                        "bucket_visit_index",
                        "sampler_slot",
                        "rotation_offset",
                        "wrap_around",
                        "sample_max",
                        "sampled_market_count",
                        "truncated_count",
                        "truncation_applied",
                        "inclusion_probability_basis",
                        "long_run_coverage_basis",
                        "bucket_number",
                        "bucket_count",
                        "selection_rank",
                        "token_ids_json",
                        "outcome_labels_json",
                        "expected_token_count",
                        "observed_token_count",
                        "coverage_ratio",
                        "status",
                        "error_message",
                        "selected_at",
                    ),
                    bundle.get("orderbook_selections", []),
                )
                self._insert_many(
                    connection,
                    "orderbook_token_attempts",
                    (
                        "token_attempt_id",
                        "selection_id",
                        "run_id",
                        "cycle_number",
                        "collection_id",
                        "token_id",
                        "outcome_index",
                        "outcome_label",
                        "status",
                        "request_id",
                        "raw_payload_id",
                        "received_at",
                        "bid_level_count",
                        "ask_level_count",
                        "error_type",
                        "error_message",
                    ),
                    bundle.get("orderbook_token_attempts", []),
                )
                self._insert_many(
                    connection,
                    "orderbook_snapshots",
                    (
                        "snapshot_id",
                        "selection_id",
                        "run_id",
                        "cycle_number",
                        "token_id",
                        "received_at",
                        "request_id",
                        "raw_payload_id",
                        "source_timestamp",
                        "source_hash",
                        "market",
                        "best_bid",
                        "best_ask",
                        "spread",
                        "last_trade_price",
                        "tick_size_raw",
                        "min_order_size_raw",
                        "neg_risk",
                        "raw_book_sha256",
                    ),
                    bundle.get("orderbook_snapshots", []),
                )
                self._insert_many(
                    connection,
                    "orderbook_levels",
                    (
                        "level_id",
                        "snapshot_id",
                        "side",
                        "level_index",
                        "price_raw",
                        "price",
                        "size_raw",
                        "size",
                    ),
                    bundle.get("orderbook_levels", []),
                )
                self._insert_many(
                    connection,
                    "orderbook_depth_metrics",
                    (
                        "metric_id",
                        "snapshot_id",
                        "side",
                        "target_notional",
                        "filled_notional",
                        "base_quantity",
                        "vwap_price",
                        "worst_price",
                        "complete",
                        "levels_consumed",
                    ),
                    bundle.get("orderbook_depth_metrics", []),
                )
                self._insert_many(
                    connection,
                    "resolution_observations",
                    (
                        "resolution_observation_id",
                        "run_id",
                        "cycle_number",
                        "condition_id",
                        "requested_at",
                        "observed_at",
                        "lookup_status",
                        "request_id",
                        "market_id",
                        "resolved",
                        "closed",
                        "one_hot",
                        "one_hot_outcome_index",
                        "one_hot_outcome_label",
                        "resolution_value_raw",
                        "resolution_source_raw",
                        "redeemable",
                        "source_updated_at",
                        "source_end_date",
                        "outcome_prices_json",
                        "raw_market_sha256",
                        "raw_market_json",
                        "error_type",
                        "error_message",
                    ),
                    bundle.get("resolution_observations", []),
                )
                trade_sweep = bundle.get("trade_sweep")
                if trade_sweep is not None:
                    self._insert_many(
                        connection,
                        "trade_tape_sweeps",
                        (
                            "trade_sweep_id",
                            "run_id",
                            "cycle_number",
                            "started_at",
                            "completed_at",
                            "target_start_epoch",
                            "source_target_end_epoch",
                            "bounded_target_end_epoch",
                            "watermark_before_epoch",
                            "watermark_advance_to_epoch",
                            "status",
                            "possible_gap",
                            "window_count",
                            "membership_count",
                            "unique_trade_count",
                            "head_timestamp_raw",
                            "tail_timestamp_raw",
                            "membership_digest_sha256",
                            "error_message",
                        ),
                        [trade_sweep],
                    )
                self._insert_many(
                    connection,
                    "trade_tape_windows",
                    (
                        "window_id",
                        "trade_sweep_id",
                        "start_epoch",
                        "end_epoch",
                        "parent_window_id",
                        "split_depth",
                        "offset",
                        "request_id",
                        "raw_payload_id",
                        "received_at",
                        "row_count",
                        "membership_count",
                        "economic_unique_count",
                        "duplicate_economic_row_count",
                        "membership_digest_sha256",
                        "hit_cap",
                        "status",
                        "possible_gap",
                        "error_message",
                    ),
                    bundle.get("trade_windows", []),
                )
                self._insert_many(
                    connection,
                    "trade_observations",
                    (
                        "trade_id",
                        "economic_row_hash",
                        "occurrence_index",
                        "side",
                        "asset",
                        "condition_id",
                        "size_raw",
                        "size",
                        "price_raw",
                        "price",
                        "timestamp_raw",
                        "timestamp_epoch",
                        "transaction_hash",
                        "proxy_wallet",
                        "outcome",
                        "outcome_index_raw",
                        "outcome_index",
                        "sanitized_trade_json",
                        "first_received_at",
                    ),
                    bundle.get("trade_observations", []),
                    or_ignore=True,
                )
                self._insert_many(
                    connection,
                    "trade_tape_memberships",
                    (
                        "membership_id",
                        "trade_sweep_id",
                        "window_id",
                        "trade_id",
                        "item_number",
                        "received_at",
                    ),
                    bundle.get("trade_memberships", []),
                )
                self._insert_many(
                    connection,
                    "data_quality_issues",
                    (
                        "issue_id",
                        "run_id",
                        "cycle_number",
                        "component",
                        "severity",
                        "issue_code",
                        "observed_at",
                        "details_json",
                    ),
                    bundle.get("quality_issues", []),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def rotate_if_utc_day_changed(
        self,
        now: datetime | None = None,
        *,
        contract_metadata: Mapping[str, Any] | None = None,
    ) -> Path | None:
        """Atomically archive the prior UTC-day active DB before a new cycle."""
        if not self.db_path.exists():
            archives = sorted(
                self.db_path.parent.glob(
                    f"{self.db_path.stem}_[0-9]" + "[0-9]" * 7 + self.db_path.suffix
                )
            )
            if archives:
                raise RuntimeError(
                    "active research DB is missing while UTC archives exist; "
                    "refusing to create a silent empty lineage"
                )
            return None
        now_utc = (now or self._now()).astimezone(timezone.utc)
        with self._connect() as connection:
            if "collection_contracts" not in {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }:
                return None
            row = connection.execute(
                "SELECT database_utc_date FROM collection_contracts LIMIT 1"
            ).fetchone()
            if row is None or row[0] == now_utc.date().isoformat():
                return None
            archive_date = str(row[0])
            watermark_row = connection.execute(
                """
                SELECT MAX(watermark_advance_to_epoch)
                FROM trade_tape_sweeps
                WHERE status IN ('SUCCESS', 'EMPTY') AND possible_gap = 0
                """
            ).fetchone()
            if watermark_row and watermark_row[0] is not None:
                watermark = int(watermark_row[0])
            else:
                inherited = connection.execute(
                    "SELECT prior_trade_watermark_epoch FROM collection_contracts LIMIT 1"
                ).fetchone()
                watermark = (
                    int(inherited[0])
                    if inherited and inherited[0] is not None
                    else None
                )
            bootstrap_row = connection.execute(
                "SELECT MIN(target_start_epoch) FROM trade_tape_sweeps "
                "WHERE watermark_before_epoch IS NULL"
            ).fetchone()
            if bootstrap_row and bootstrap_row[0] is not None:
                bootstrap_start = int(bootstrap_row[0])
            else:
                inherited_bootstrap = connection.execute(
                    "SELECT prior_trade_bootstrap_start_epoch "
                    "FROM collection_contracts LIMIT 1"
                ).fetchone()
                bootstrap_start = (
                    int(inherited_bootstrap[0])
                    if inherited_bootstrap and inherited_bootstrap[0] is not None
                    else None
                )
            if watermark is not None:
                bootstrap_start = None
            latest_sweep = connection.execute(
                "SELECT sweep_id FROM market_sweeps ORDER BY cycle_number DESC LIMIT 1"
            ).fetchone()
            if latest_sweep is not None:
                census_carry = [
                    dict(item)
                    for item in connection.execute(
                        """
                        SELECT condition_id, MIN(source_market_key) AS source_market_key,
                               MIN(market_id) AS market_id, ? AS prior_sweep_id
                        FROM market_sweep_memberships
                        WHERE sweep_id = ? AND condition_id IS NOT NULL
                        GROUP BY condition_id
                        """,
                        (latest_sweep[0], latest_sweep[0]),
                    ).fetchall()
                ]
            else:
                census_carry = [
                    dict(item)
                    for item in connection.execute(
                        """
                        SELECT condition_id, source_market_key, market_id, prior_sweep_id
                        FROM prior_census_conditions
                        """
                    ).fetchall()
                ]
            watch_rows = connection.execute(
                """
                WITH ranked AS (
                    SELECT resolution.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY condition_id ORDER BY observed_at DESC
                           ) AS position
                    FROM resolution_observations AS resolution
                )
                SELECT watch.*, seen.lookup_status, seen.closed, seen.one_hot,
                       seen.one_hot_outcome_index, seen.one_hot_outcome_label,
                       seen.resolution_value_raw, seen.redeemable, seen.observed_at
                FROM resolution_watchlist AS watch
                LEFT JOIN ranked AS seen
                  ON seen.condition_id = watch.condition_id AND seen.position = 1
                """
            ).fetchall()
            carried_watchlist = []
            for watch in watch_rows:
                state = {
                    key: watch[key]
                    for key in (
                        "lookup_status",
                        "closed",
                        "one_hot",
                        "one_hot_outcome_index",
                        "one_hot_outcome_label",
                        "resolution_value_raw",
                        "redeemable",
                        "observed_at",
                    )
                }
                terminal = int(
                    bool(watch["terminal"])
                    or (watch["one_hot"] == 1 and watch["redeemable"] == 1)
                )
                carried_watchlist.append(
                    {
                        "condition_id": watch["condition_id"],
                        "market_id": watch["market_id"],
                        "source_market_key": watch["source_market_key"],
                        "first_seen_sweep_id": watch["first_seen_sweep_id"],
                        "first_seen_at": watch["first_seen_at"],
                        "selection_reason": watch["selection_reason"],
                        "carried_from_utc_date": archive_date,
                        "prior_state_json": canonical_json(state),
                        "terminal": terminal,
                    }
                )
            quick = connection.execute("PRAGMA quick_check").fetchone()[0]
            if quick != "ok":
                raise RuntimeError(f"refusing to rotate corrupt active DB: {quick}")
            checkpoint = tuple(
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            )
            # SQLite reports ``(busy, log_frames, checkpointed_frames)``.  A
            # reader pinned to an older WAL frame may leave committed rows only
            # in ``-wal``; archiving the main file in that state loses evidence.
            if checkpoint != (0, 0, 0):
                raise RuntimeError(
                    "refusing UTC rotation while WAL checkpoint is incomplete: "
                    f"busy={checkpoint[0]} log={checkpoint[1]} "
                    f"checkpointed={checkpoint[2]}"
                )
        # A zero-frame checkpoint alone is not an ownership handoff.  Close the
        # census/read connection first so its prepared statements cannot block
        # the mode transition, then use a fresh connection as the ownership
        # barrier.  An idle external reader keeps this WAL -> DELETE transition
        # locked.  Once it succeeds, the archived shard is a self-contained main
        # DB and later readers cannot attach the replacement active DB's WAL to
        # the old main file.
        barrier = sqlite3.connect(
            self.db_path,
            timeout=0,
            isolation_level=None,
        )
        try:
            barrier.execute("PRAGMA busy_timeout = 0")
            try:
                journal_mode = str(
                    barrier.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
                ).lower()
            except sqlite3.OperationalError as error:
                raise RuntimeError(
                    "refusing UTC rotation while a reader still owns the active "
                    "SQLite WAL namespace"
                ) from error
            if journal_mode != "delete":
                raise RuntimeError(
                    "refusing UTC rotation because WAL ownership handoff did not "
                    f"enter DELETE mode: {journal_mode}"
                )
        finally:
            barrier.close()
        archive_path = self.db_path.with_name(
            f"trades_sim_{archive_date.replace('-', '')}.db"
        )
        interrupted_handoff = False
        if archive_path.exists():
            try:
                interrupted_handoff = os.path.samefile(self.db_path, archive_path)
            except OSError:
                interrupted_handoff = False
            if not interrupted_handoff:
                raise FileExistsError(
                    f"daily research archive already exists: {archive_path}"
                )

        # Build the complete next-day shard before changing either canonical
        # name.  Both files are on the same APFS volume, so hard-link + replace
        # gives a constant-space, crash-recoverable handoff:
        #
        #   active only -> active+archive(same inode) -> new active+old archive
        #
        # A restart in the middle recognizes the same-inode state and resumes.
        temporary = self.db_path.with_name(
            f".{self.db_path.name}.rotate-{now_utc.date().isoformat()}-{uuid4().hex}.tmp"
        )
        temporary_repository = ResearchRepository(
            temporary,
            clock=self.clock,
            busy_timeout_ms=self.busy_timeout_ms,
        )
        try:
            temporary_repository.initialize(
                database_utc_date=now_utc.date().isoformat(),
                prior_trade_watermark_epoch=watermark,
                prior_trade_bootstrap_start_epoch=bootstrap_start,
                carried_watchlist=carried_watchlist,
                carried_census=census_carry,
                carried_from_utc_date=archive_date,
                contract_metadata=contract_metadata,
            )
            with temporary_repository._connect() as connection:
                quick = connection.execute("PRAGMA quick_check").fetchone()[0]
                if quick != "ok":
                    raise RuntimeError(
                        f"refusing to install corrupt next-day DB: {quick}"
                    )
                checkpoint = tuple(
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                )
                if checkpoint != (0, 0, 0):
                    raise RuntimeError(
                        "next-day DB WAL checkpoint is incomplete: "
                        f"busy={checkpoint[0]} log={checkpoint[1]} "
                        f"checkpointed={checkpoint[2]}"
                    )
            temporary_wal = Path(f"{temporary}-wal")
            if temporary_wal.exists() and temporary_wal.stat().st_size:
                raise RuntimeError(
                    "next-day DB still has non-empty WAL after checkpoint"
                )
            _fsync_file(temporary)

            active_wal = Path(f"{self.db_path}-wal")
            if active_wal.exists() and active_wal.stat().st_size:
                raise RuntimeError("active DB still has non-empty WAL after checkpoint")
            if not interrupted_handoff:
                os.link(self.db_path, archive_path)
                _fsync_directory(self.db_path.parent)
            os.replace(temporary, self.db_path)
            _fsync_directory(self.db_path.parent)
        finally:
            for candidate in (
                temporary,
                Path(f"{temporary}-wal"),
                Path(f"{temporary}-shm"),
            ):
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
        return archive_path

    def record_storage_metric(
        self,
        *,
        phase: str,
        storage: StorageConfig,
        cadence_minutes: int,
        run_id: str | None = None,
        cycle_number: int | None = None,
    ) -> dict[str, Any]:
        metric = self.inspect_storage(storage=storage, cadence_minutes=cadence_minutes)
        metric.update(
            {
                "storage_metric_id": str(uuid4()),
                "run_id": run_id,
                "cycle_number": cycle_number,
                "phase": phase,
                "observed_at": utc_now(),
            }
        )
        if metric["guard_state"] == "STOP":
            return metric
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_many(connection, "storage_metrics", tuple(metric), [metric])
            connection.commit()
        return metric

    def inspect_storage(
        self, *, storage: StorageConfig, cadence_minutes: int
    ) -> dict[str, Any]:
        """Compute guard and logical DB+WAL forecast without mutating the DB."""
        ancestor = self.db_path.parent
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        usage = shutil.disk_usage(ancestor)
        db_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        wal = Path(str(self.db_path) + "-wal")
        shm = Path(str(self.db_path) + "-shm")
        wal_bytes = wal.stat().st_size if wal.exists() else 0
        logical_bytes = db_bytes + wal_bytes
        used_ratio = usage.used / usage.total if usage.total else 1.0
        rows: list[sqlite3.Row] = []
        if self.db_path.exists():
            try:
                with self._read_connect() as connection:
                    if connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='storage_metrics'"
                    ).fetchone():
                        rows = connection.execute(
                            "SELECT logical_bytes FROM storage_metrics "
                            "WHERE phase = 'post_publish' "
                            "ORDER BY observed_at DESC LIMIT 10"
                        ).fetchall()
            except sqlite3.Error:
                rows = []
        prior_sizes = [int(row[0]) for row in reversed(rows)]
        deltas = [
            max(0, right - left) for left, right in zip(prior_sizes, prior_sizes[1:])
        ]
        if prior_sizes:
            deltas.append(max(0, logical_bytes - prior_sizes[-1]))
        # On the first successful cycle there is no prior post_publish row yet.
        # Treat the current logical DB size as the first-cycle growth estimate
        # instead of reporting a misleading zero-day forecast.  Later cycles
        # continue to use measured deltas between post-publish observations.
        growth = (
            sum(deltas) / len(deltas)
            if deltas
            else (float(logical_bytes) if logical_bytes > 0 else 0.0)
        )
        forecast = growth * (1440 / cadence_minutes)
        ratio_headroom = max(0.0, storage.stop_used_ratio * usage.total - usage.used)
        free_floor_headroom = max(0.0, usage.free - storage.min_free_gib * GIB)
        stop_headroom = min(ratio_headroom, free_floor_headroom)
        forecast_days_to_stop = stop_headroom / forecast if forecast > 0 else None
        if (
            usage.free < storage.min_free_gib * GIB
            or used_ratio >= storage.stop_used_ratio
        ):
            guard_state = "STOP"
        elif used_ratio >= storage.warn_used_ratio:
            guard_state = "WARN"
        else:
            guard_state = "OK"
        return {
            "db_bytes": db_bytes,
            "wal_bytes": wal_bytes,
            "shm_bytes": shm.stat().st_size if shm.exists() else 0,
            "logical_bytes": logical_bytes,
            "filesystem_total_bytes": usage.total,
            "filesystem_used_bytes": usage.used,
            "filesystem_free_bytes": usage.free,
            "filesystem_used_ratio": used_ratio,
            "recent_growth_bytes_per_cycle": growth,
            "forecast_next_day_bytes": forecast,
            "forecast_days_to_stop": forecast_days_to_stop,
            "guard_state": guard_state,
        }

    @staticmethod
    def _quoted_identifier(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    @staticmethod
    def _json_mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, str):
            return {}
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}

    @staticmethod
    def _utc_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _expected_slot_summary(
        self,
        connection: sqlite3.Connection,
        *,
        database_utc_date: str | None,
        cadence_minutes: int,
        coverage_end_utc: datetime | None = None,
    ) -> dict[str, Any]:
        cadence = max(1, int(cadence_minutes))
        if database_utc_date is None:
            return {
                "cadence_minutes": cadence,
                "expected_slots": 0,
                "observed_slots": 0,
                "gap_slots": 0,
                "started_runs": 0,
                "duplicate_or_extra_runs": 0,
                "coverage_ratio": None,
            }
        try:
            day = datetime.fromisoformat(database_utc_date).date()
        except ValueError:
            return {
                "cadence_minutes": cadence,
                "database_utc_date": database_utc_date,
                "error": "invalid_database_utc_date",
            }
        start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        day_end = start + timedelta(days=1)
        basis_clock = (
            coverage_end_utc.astimezone(timezone.utc)
            if coverage_end_utc is not None
            else self._now()
        )
        coverage_end = min(max(basis_clock, start), day_end)
        elapsed = max(0.0, (coverage_end - start).total_seconds())
        cadence_seconds = cadence * 60
        if coverage_end >= day_end:
            expected = int((elapsed + cadence_seconds - 1) // cadence_seconds)
        elif elapsed == 0:
            expected = 0 if coverage_end_utc is not None else 1
        else:
            expected = int(elapsed // cadence_seconds) + 1
        started_rows = connection.execute(
            "SELECT event_at FROM research_run_events "
            "WHERE event_type = 'STARTED' ORDER BY event_at"
        ).fetchall()
        slots: set[int] = set()
        started_in_contract_day = 0
        malformed_started_at = 0
        for row in started_rows:
            timestamp = self._utc_datetime(row[0])
            if timestamp is None:
                malformed_started_at += 1
                continue
            if start <= timestamp < day_end:
                started_in_contract_day += 1
                slots.add(int((timestamp - start).total_seconds() // cadence_seconds))
        observed = len({slot for slot in slots if slot < expected})
        gap = max(0, expected - observed)
        return {
            "coverage_semantics": "observed_start_bucket_coverage_not_jenkins_schedule",
            "coverage_clock_basis": (
                "persisted_source_cutoff"
                if coverage_end_utc is not None
                else "inspection_clock"
            ),
            "cadence_minutes": cadence,
            "database_utc_date": database_utc_date,
            "window_start_utc": start.isoformat(),
            "window_end_utc": coverage_end.isoformat(),
            "expected_slots": expected,
            "observed_slots": observed,
            "gap_slots": gap,
            "started_runs": started_in_contract_day,
            "duplicate_or_extra_runs": max(0, started_in_contract_day - observed),
            "malformed_started_at": malformed_started_at,
            "coverage_ratio": observed / expected if expected else None,
        }

    def _shard_summary(self) -> dict[str, Any]:
        if self.immutable_reads:
            name = self.db_path.name
            role = (
                "closed_archive"
                if name.startswith("trades_sim_") and name != "trades_sim.db"
                else "operator_supplied_verified_snapshot"
            )
            return {
                "selected_path": str(self.db_path),
                "selected_role": role,
                "selected_bytes": (
                    self.db_path.stat().st_size if self.db_path.exists() else 0
                ),
                "sibling_scan_performed": False,
            }
        archives = sorted(self.db_path.parent.glob("trades_sim_????????.db"))
        archive_rows = [
            {"path": str(path), "bytes": path.stat().st_size}
            for path in archives
            if path.is_file()
        ]
        return {
            "selected_path": str(self.db_path),
            "selected_role": "active",
            "sibling_scan_performed": True,
            "active_path": str(self.db_path),
            "active_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "archive_count": len(archive_rows),
            "archive_bytes": sum(int(row["bytes"]) for row in archive_rows),
            "archives": archive_rows,
        }

    def status(
        self,
        storage: StorageConfig | None = None,
        *,
        cadence_minutes: int = 15,
    ) -> dict[str, Any]:
        policy = storage or StorageConfig()
        shard_summary = self._shard_summary()
        inspection = self.inspect_storage(
            storage=policy, cadence_minutes=cadence_minutes
        )
        if not self.db_path.exists():
            return {
                "exists": False,
                "db_path": str(self.db_path),
                "cadence_minutes": int(cadence_minutes),
                "shards": shard_summary,
                "storage": inspection,
            }
        with self._read_connect() as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            counts = {
                table: int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {self._quoted_identifier(table)}"
                    ).fetchone()[0]
                )
                for table in (
                    "research_run_events",
                    "market_sweeps",
                    "market_observations",
                    "outcome_observations",
                    "orderbook_token_attempts",
                    "orderbook_snapshots",
                    "resolution_observations",
                    "trade_tape_sweeps",
                    "trade_observations",
                    "data_quality_issues",
                )
                if table in tables
            }
            latest = (
                connection.execute(
                    "SELECT completed_at FROM market_sweeps "
                    "ORDER BY completed_at DESC LIMIT 1"
                ).fetchone()
                if "market_sweeps" in tables
                else None
            )
            contract = (
                connection.execute(
                    "SELECT contract_name, schema_version, database_utc_date, "
                    "metadata_json FROM collection_contracts LIMIT 1"
                ).fetchone()
                if "collection_contracts" in tables
                else None
            )
            database_utc_date = str(contract[2]) if contract else None
            contract_profile = self._json_mapping(contract[3] if contract else None)
            stored_cadence = contract_profile.get("cadence_minutes")
            effective_cadence = (
                int(stored_cadence)
                if isinstance(stored_cadence, int)
                and not isinstance(stored_cadence, bool)
                and stored_cadence > 0
                else int(cadence_minutes)
            )
            started_count = terminal_count = orphan_count = 0
            latest_run: dict[str, Any] | None = None
            recent_components: list[dict[str, Any]] = []
            latest_runtime: dict[str, Any] | None = None
            latest_source_storage: dict[str, Any] | None = None
            latest_trade_tape: dict[str, Any] | None = None
            if "research_run_events" in tables:
                started_count = int(
                    connection.execute(
                        "SELECT COUNT(DISTINCT run_id) FROM research_run_events "
                        "WHERE event_type = 'STARTED'"
                    ).fetchone()[0]
                )
                terminal_count = int(
                    connection.execute(
                        "SELECT COUNT(DISTINCT run_id) FROM research_run_events "
                        "WHERE event_type IN ('SUCCEEDED', 'FAILED')"
                    ).fetchone()[0]
                )
                orphan_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM ("
                        "SELECT run_id FROM research_run_events "
                        "GROUP BY run_id "
                        "HAVING SUM(event_type = 'STARTED') > 0 "
                        "AND SUM(event_type IN ('SUCCEEDED', 'FAILED')) = 0)"
                    ).fetchone()[0]
                )
                latest_started = connection.execute(
                    "SELECT * FROM research_run_events "
                    "WHERE event_type = 'STARTED' "
                    "ORDER BY event_at DESC LIMIT 1"
                ).fetchone()
                if latest_started is not None:
                    terminal = connection.execute(
                        "SELECT * FROM research_run_events "
                        "WHERE run_id = ? AND event_type IN ('SUCCEEDED', 'FAILED') "
                        "ORDER BY event_at DESC LIMIT 1",
                        (latest_started["run_id"],),
                    ).fetchone()
                    latest_run = {
                        "run_id": latest_started["run_id"],
                        "started_at": latest_started["event_at"],
                        "terminal_state": terminal["event_type"] if terminal else None,
                        "terminal_at": terminal["event_at"] if terminal else None,
                        "error_type": terminal["error_type"] if terminal else None,
                        "error_message": terminal["error_message"]
                        if terminal
                        else None,
                        "cycle_stats": self._json_mapping(
                            terminal["cycle_stats_json"] if terminal else None
                        ),
                    }
                latest_success = connection.execute(
                    "SELECT run_id, event_at, cycle_stats_json "
                    "FROM research_run_events WHERE event_type = 'SUCCEEDED' "
                    "ORDER BY event_at DESC LIMIT 1"
                ).fetchone()
                if latest_success is not None:
                    stats = self._json_mapping(latest_success["cycle_stats_json"])
                    latest_runtime = {
                        "run_id": latest_success["run_id"],
                        "succeeded_at": latest_success["event_at"],
                        "runtime_seconds": stats.get("runtime_seconds"),
                        "storage": stats.get("storage"),
                        "storage_filesystem": stats.get("storage_filesystem"),
                    }
            if "source_component_runs" in tables:
                component_rows = connection.execute(
                    "WITH ranked AS ("
                    "SELECT component, run_id, status, started_at, completed_at, "
                    "requested_count, observed_count, error_count, possible_gap, "
                    "error_message, ROW_NUMBER() OVER ("
                    "PARTITION BY component ORDER BY completed_at DESC"
                    ") AS rank FROM source_component_runs) "
                    "SELECT * FROM ranked WHERE rank = 1 ORDER BY component"
                ).fetchall()
                recent_components = [
                    {
                        key: row[key]
                        for key in (
                            "component",
                            "run_id",
                            "status",
                            "started_at",
                            "completed_at",
                            "requested_count",
                            "observed_count",
                            "error_count",
                            "possible_gap",
                            "error_message",
                        )
                    }
                    for row in component_rows
                ]
            if "storage_metrics" in tables:
                storage_row = connection.execute(
                    "SELECT * FROM storage_metrics ORDER BY observed_at DESC LIMIT 1"
                ).fetchone()
                if storage_row is not None:
                    latest_source_storage = dict(storage_row)
            if "trade_tape_sweeps" in tables:
                trade_row = connection.execute(
                    "SELECT trade_sweep_id, completed_at, target_start_epoch, "
                    "source_target_end_epoch, bounded_target_end_epoch, "
                    "watermark_before_epoch, watermark_advance_to_epoch, status, "
                    "possible_gap FROM trade_tape_sweeps "
                    "ORDER BY completed_at DESC LIMIT 1"
                ).fetchone()
                if trade_row is not None:
                    latest_trade_tape = dict(trade_row)
                    latest_trade_tape["backlog_remaining_seconds"] = max(
                        0,
                        int(trade_row["source_target_end_epoch"])
                        - int(trade_row["bounded_target_end_epoch"]),
                    )
            cadence = self._expected_slot_summary(
                connection,
                database_utc_date=database_utc_date,
                cadence_minutes=effective_cadence,
                coverage_end_utc=(
                    (
                        datetime.combine(
                            datetime.fromisoformat(database_utc_date).date(),
                            datetime.min.time(),
                            tzinfo=timezone.utc,
                        )
                        + timedelta(days=1)
                    )
                    if self.immutable_reads
                    and database_utc_date is not None
                    and datetime.fromisoformat(database_utc_date).date()
                    < self._now().date()
                    else (
                        self._utc_datetime(
                            connection.execute(
                                "SELECT MAX(event_at) FROM research_run_events "
                                "WHERE event_type = 'STARTED'"
                            ).fetchone()[0]
                        )
                        or datetime.combine(
                            datetime.fromisoformat(database_utc_date).date(),
                            datetime.min.time(),
                            tzinfo=timezone.utc,
                        )
                    )
                    if self.immutable_reads and database_utc_date is not None
                    else None
                ),
            )
        return {
            "exists": True,
            "db_path": str(self.db_path),
            "bytes": self.db_path.stat().st_size,
            "cadence_minutes": effective_cadence,
            "requested_config_cadence_minutes": int(cadence_minutes),
            "contract": {
                "name": contract[0] if contract else None,
                "schema_version": contract[1] if contract else None,
                "database_utc_date": database_utc_date,
                "profile": contract_profile,
            },
            "latest_complete_market_sweep": latest[0] if latest else None,
            "latest_trade_watermark_epoch": self.latest_trade_watermark(),
            "counts": counts,
            "research_runs": {
                "started": started_count,
                "terminal": terminal_count,
                "orphan": orphan_count,
                "latest": latest_run,
            },
            "recent_components": recent_components,
            "runtime": latest_runtime,
            "latest_trade_tape": latest_trade_tape,
            "cadence_coverage": cadence,
            "shards": shard_summary,
            "storage": latest_source_storage if self.immutable_reads else inspection,
            "inspection_host_storage": inspection if self.immutable_reads else None,
        }

    def health(
        self,
        storage: StorageConfig | None = None,
        *,
        cadence_minutes: int = 15,
    ) -> dict[str, Any]:
        policy = storage or StorageConfig()
        inspection = self.inspect_storage(
            storage=policy, cadence_minutes=cadence_minutes
        )
        if not self.db_path.exists():
            ancestor = self.db_path.parent
            while not ancestor.exists() and ancestor != ancestor.parent:
                ancestor = ancestor.parent
            writable = os.access(ancestor, os.W_OK)
            healthy = writable and inspection["guard_state"] != "STOP"
            return {
                "healthy": healthy,
                "state": "NEW_DB_READY" if healthy else "NEW_DB_BLOCKED",
                "db_path": str(self.db_path),
                "db_exists": False,
                "parent_writable": writable,
                "storage": inspection,
            }
        try:
            with self._read_connect() as connection:
                quick = connection.execute("PRAGMA quick_check").fetchone()[0]
                journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
                synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
                contract = connection.execute(
                    "SELECT contract_name, schema_version, database_utc_date "
                    "FROM collection_contracts LIMIT 1"
                ).fetchone()
                trigger_count = connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE '%_append_only_%'"
                ).fetchone()[0]
            database_utc_date = str(contract[2]) if contract else None
            current_utc_date = self._now().date().isoformat()
            rotation_required = bool(
                database_utc_date and database_utc_date != current_utc_date
            )
            archive_path = (
                self.db_path.with_name(
                    f"trades_sim_{database_utc_date.replace('-', '')}.db"
                )
                if rotation_required and database_utc_date
                else None
            )
            rotation_collision = bool(archive_path and archive_path.exists())
            healthy = (
                quick == "ok"
                and str(journal).lower() == "wal"
                and int(synchronous) == 2
                and contract is not None
                and contract[0] == RESEARCH_DATA_CONTRACT
                and int(contract[1]) == SCHEMA_VERSION
                and trigger_count == len(FACT_TABLES) * 2
                and inspection["guard_state"] != "STOP"
                and not rotation_collision
            )
            return {
                "healthy": healthy,
                "state": (
                    "ROTATION_COLLISION"
                    if rotation_collision
                    else ("ROTATION_READY" if rotation_required else "READY")
                ),
                "db_path": str(self.db_path),
                "quick_check": quick,
                "journal_mode": journal,
                "synchronous": synchronous,
                "contract": contract[0] if contract else None,
                "schema_version": contract[1] if contract else None,
                "database_utc_date": database_utc_date,
                "current_utc_date": current_utc_date,
                "rotation_required": rotation_required,
                "rotation_archive_path": str(archive_path) if archive_path else None,
                "rotation_collision": rotation_collision,
                "append_only_trigger_count": trigger_count,
                "storage": inspection,
            }
        except sqlite3.Error as error:
            return {
                "healthy": False,
                "db_path": str(self.db_path),
                "reason": f"{type(error).__name__}: {error}",
            }

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, int] | None:
        if not path.exists():
            return None
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns

    def _manifest_database_entry(
        self,
        path: Path,
        *,
        cadence_minutes: int,
        active: bool,
    ) -> dict[str, Any]:
        wal_path = Path(f"{path}-wal")
        shm_path = Path(f"{path}-shm")
        # SHM is a volatile shared-memory index and may be created or touched by
        # a read-only SQLite open.  Snapshot stability is therefore defined by
        # the durable main DB + WAL bundle only; SHM remains informational.
        tracked_paths = [path, *([wal_path] if wal_path.exists() else [])]
        before = {str(item): self._file_identity(item) for item in tracked_paths}
        entry: dict[str, Any] = {
            "path": str(path),
            "role": "active" if active else "archive",
            "bytes": path.stat().st_size,
            "sha256": self._sha256_file(path),
            "cadence_minutes": int(cadence_minutes),
        }
        try:
            wal_has_frames = wal_path.exists() and wal_path.stat().st_size > 0
            with self._read_connect_path(
                path,
                immutable=self.immutable_reads or not wal_has_frames,
            ) as connection:
                quick_check = str(
                    connection.execute("PRAGMA quick_check").fetchone()[0]
                )
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                row_counts = {
                    table: int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {self._quoted_identifier(table)}"
                        ).fetchone()[0]
                    )
                    for table in sorted(tables)
                }
                append_only_trigger_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
                        "AND name LIKE '%_append_only_%'"
                    ).fetchone()[0]
                )
                contract = (
                    connection.execute(
                        "SELECT contract_name, schema_version, database_utc_date, "
                        "metadata_json, created_at FROM collection_contracts LIMIT 1"
                    ).fetchone()
                    if "collection_contracts" in tables
                    else None
                )
                contract_name = str(contract[0]) if contract else None
                schema_version = int(contract[1]) if contract else None
                database_utc_date = str(contract[2]) if contract else None
                profile = self._json_mapping(contract[3] if contract else None)
                profile_cadence = profile.get("cadence_minutes")
                effective_cadence = (
                    int(profile_cadence)
                    if isinstance(profile_cadence, int)
                    and not isinstance(profile_cadence, bool)
                    and profile_cadence > 0
                    else int(cadence_minutes)
                )
                utc_columns = {
                    "collection_contracts": ("created_at",),
                    "research_config_versions": ("first_seen_at",),
                    "research_run_events": ("event_at",),
                    "api_requests": ("started_at", "completed_at"),
                    "raw_payloads": ("recorded_at",),
                    "source_component_runs": ("started_at", "completed_at"),
                    "market_sweeps": ("started_at", "completed_at"),
                    "market_observations": ("page_received_at",),
                    "market_metadata_versions": ("first_observed_at",),
                    "orderbook_selections": ("selected_at",),
                    "orderbook_token_attempts": ("received_at",),
                    "orderbook_snapshots": ("received_at",),
                    "resolution_watchlist": ("first_seen_at",),
                    "prior_census_conditions": ("carried_at",),
                    "resolution_observations": ("requested_at", "observed_at"),
                    "trade_tape_sweeps": ("started_at", "completed_at"),
                    "trade_tape_windows": ("received_at",),
                    "trade_tape_memberships": ("received_at",),
                    "trade_observations": ("first_received_at",),
                    "data_quality_issues": ("observed_at",),
                    "storage_metrics": ("observed_at",),
                }
                earliest: datetime | None = None
                latest: datetime | None = None
                malformed_timestamps = 0
                for table, columns in utc_columns.items():
                    if table not in tables:
                        continue
                    table_columns = {
                        str(row[1])
                        for row in connection.execute(
                            f"PRAGMA table_info({self._quoted_identifier(table)})"
                        )
                    }
                    for column in columns:
                        if column not in table_columns:
                            continue
                        minimum, maximum = connection.execute(
                            f"SELECT MIN({self._quoted_identifier(column)}), "
                            f"MAX({self._quoted_identifier(column)}) "
                            f"FROM {self._quoted_identifier(table)}"
                        ).fetchone()
                        for value in (minimum, maximum):
                            if value is None:
                                continue
                            parsed = self._utc_datetime(value)
                            if parsed is None:
                                malformed_timestamps += 1
                                continue
                            earliest = (
                                parsed if earliest is None else min(earliest, parsed)
                            )
                            latest = parsed if latest is None else max(latest, parsed)
                entry.update(
                    {
                        "quick_check": quick_check,
                        "schema_version": schema_version,
                        "data_contract": contract_name,
                        "utc_contract": {
                            "database_utc_date": database_utc_date,
                            "created_at": contract[4] if contract else None,
                            "observed_range": {
                                "first": earliest.isoformat() if earliest else None,
                                "last": latest.isoformat() if latest else None,
                                "malformed_boundary_values": malformed_timestamps,
                            },
                        },
                        "cadence_minutes": effective_cadence,
                        "profile": profile,
                        "table_row_counts": row_counts,
                        "append_only_trigger_count": append_only_trigger_count,
                        "expected_append_only_trigger_count": len(FACT_TABLES) * 2,
                        "cadence_coverage": self._expected_slot_summary(
                            connection,
                            database_utc_date=database_utc_date,
                            cadence_minutes=effective_cadence,
                            coverage_end_utc=(
                                (
                                    datetime.combine(
                                        datetime.fromisoformat(
                                            database_utc_date
                                        ).date(),
                                        datetime.min.time(),
                                        tzinfo=timezone.utc,
                                    )
                                    + timedelta(days=1)
                                )
                                if not active
                                and database_utc_date is not None
                                and datetime.fromisoformat(database_utc_date).date()
                                < self._now().date()
                                else (
                                    self._utc_datetime(
                                        connection.execute(
                                            "SELECT MAX(event_at) "
                                            "FROM research_run_events "
                                            "WHERE event_type = 'STARTED'"
                                        ).fetchone()[0]
                                    )
                                    or datetime.combine(
                                        datetime.fromisoformat(
                                            database_utc_date
                                        ).date(),
                                        datetime.min.time(),
                                        tzinfo=timezone.utc,
                                    )
                                )
                                if not active and database_utc_date is not None
                                else None
                            ),
                        ),
                    }
                )
                schema_supported = bool(
                    schema_version is not None and 1 <= schema_version <= SCHEMA_VERSION
                )
                entry["healthy"] = bool(
                    quick_check == "ok"
                    and contract_name == RESEARCH_DATA_CONTRACT
                    and schema_supported
                    and append_only_trigger_count == len(FACT_TABLES) * 2
                )
        except (OSError, sqlite3.Error) as error:
            entry.update(
                {
                    "healthy": False,
                    "error_type": type(error).__name__,
                    "error_message": " ".join(str(error).splitlines())[:400],
                }
            )
        sidecars = []
        if wal_path.exists() and wal_path.stat().st_size > 0:
            sidecars.append(
                {
                    "path": str(wal_path),
                    "kind": "wal",
                    "bytes": wal_path.stat().st_size,
                    "sha256": self._sha256_file(wal_path),
                    "durability_role": "durable",
                }
            )
        volatile_coordination = []
        if shm_path.exists():
            volatile_coordination.append(
                {
                    "path": str(shm_path),
                    "kind": "shm",
                    "bytes": shm_path.stat().st_size,
                    "durability_role": "volatile",
                }
            )
        after_paths = {
            *tracked_paths,
            *([wal_path] if wal_path.exists() else []),
        }
        after = {str(item): self._file_identity(item) for item in after_paths}
        stable = before == after
        if active:
            consistency_state = (
                "LIVE_BUNDLE_STABLE_DURING_EXPORT"
                if stable
                else "LIVE_BUNDLE_CHANGED_DURING_EXPORT"
            )
        elif sidecars:
            consistency_state = "ARCHIVE_HAS_SQLITE_SIDECARS"
        else:
            consistency_state = "IMMUTABLE_ARCHIVE"
        entry["sqlite_sidecars"] = sidecars
        entry["volatile_coordination_files"] = volatile_coordination
        entry["consistency"] = {
            "state": consistency_state,
            "files_stable_during_export": stable,
            "portable_sqlite_snapshot": bool(not active and stable and not sidecars),
            "copy_requirement": (
                "Use SQLite online backup, or copy the main DB with its non-empty WAL "
                "as one stable bundle; the main DB hash alone is not a snapshot. "
                "The SHM file is volatile coordination state, not evidence."
                if active
                else (
                    "Copy the archive main DB; a WAL must be absent. SHM is a "
                    "volatile read index and is not part of the durable fingerprint."
                )
            ),
        }
        entry["healthy"] = bool(
            entry.get("healthy") and stable and (active or not sidecars)
        )
        return entry

    def validate_read_only_database(
        self, *, cadence_minutes: int = 15
    ) -> dict[str, Any]:
        """Validate one exact SQLite artifact without scanning sibling shards."""
        if not self.db_path.is_file():
            return {
                "path": str(self.db_path),
                "healthy": False,
                "error_type": "FileNotFoundError",
                "error_message": "selected SQLite artifact is not a regular file",
            }
        return self._manifest_database_entry(
            self.db_path,
            cadence_minutes=cadence_minutes,
            active=False,
        )

    def export_manifest(
        self,
        path: str | Path | None = None,
        *,
        storage: StorageConfig | None = None,
        cadence_minutes: int = 15,
        include_sibling_shards: bool = True,
    ) -> dict[str, Any]:
        database_paths = (
            sorted(
                candidate
                for candidate in self.db_path.parent.glob("trades_sim*.db")
                if candidate.is_file()
            )
            if include_sibling_shards
            else ([self.db_path] if self.db_path.is_file() else [])
        )
        if self.db_path.exists() and self.db_path not in database_paths:
            database_paths.append(self.db_path)
            database_paths.sort()
        files = [
            self._manifest_database_entry(
                candidate,
                cadence_minutes=cadence_minutes,
                active=(
                    candidate == self.db_path
                    and candidate.name == "trades_sim.db"
                    and not self.immutable_reads
                ),
            )
            for candidate in database_paths
        ]
        active_health = (
            self.health(storage, cadence_minutes=cadence_minutes)
            if include_sibling_shards
            else self.validate_read_only_database(cadence_minutes=cadence_minutes)
        )
        effective_manifest_cadence = next(
            (
                int(item["cadence_minutes"])
                for item in files
                if item.get("role") == "active"
                and item.get("cadence_minutes") is not None
            ),
            int(files[0]["cadence_minutes"]) if files else int(cadence_minutes),
        )
        manifest = {
            "schema": "golden-pomegranate-manifest-v2",
            "generated_at": utc_now(),
            "data_contract": RESEARCH_DATA_CONTRACT,
            "cadence_minutes": effective_manifest_cadence,
            "requested_config_cadence_minutes": int(cadence_minutes),
            "healthy": bool(
                active_health.get("healthy")
                and files
                and all(item.get("healthy") for item in files)
            ),
            "health": active_health,
            "status": self.status(storage, cadence_minutes=cadence_minutes),
            "files": files,
        }
        if path is not None:
            destination = Path(path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
            )
        return manifest


__all__ = ["FACT_TABLES", "ResearchRepository", "SCHEMA_VERSION"]
