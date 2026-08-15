"""Append-only SQLite evidence store for the Last Mile experiment."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import quote

from ..config import BotConfig, DATA_CONTRACT, StorageConfig
from ..utils.retry import canonical_json, iso_utc


GIB = 1024**3
SCHEMA_VERSION = 1


SCHEMA = r"""
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_contracts (
    job_name TEXT PRIMARY KEY CHECK (job_name = 'strawberry-shadow-one'),
    strategy_name TEXT NOT NULL CHECK (strategy_name = 'golden-strawberry'),
    data_contract TEXT NOT NULL CHECK (data_contract = 'last-mile-clob-v1'),
    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode = 'archive_only'),
    cadence_minutes INTEGER NOT NULL CHECK (cadence_minutes = 10),
    cadence_offset_minute INTEGER NOT NULL CHECK (cadence_offset_minute = 7),
    entry_start TEXT NOT NULL,
    entry_end TEXT NOT NULL,
    followup_end TEXT NOT NULL,
    entry_thresholds_json TEXT NOT NULL,
    stop_thresholds_json TEXT NOT NULL,
    target_thresholds_json TEXT NOT NULL,
    primary_entry_threshold REAL NOT NULL CHECK (primary_entry_threshold = 0.95),
    primary_stop_threshold REAL NOT NULL CHECK (primary_stop_threshold = 0.85),
    preregistration_sha256 TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_config_versions (
    config_hash TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL CHECK (strategy_name = 'golden-strawberry'),
    job_name TEXT NOT NULL CHECK (job_name = 'strawberry-shadow-one'),
    mode TEXT NOT NULL CHECK (mode = 'sim'),
    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode = 'archive_only'),
    data_contract TEXT NOT NULL CHECK (data_contract = 'last-mile-clob-v1'),
    strategy_source_digest TEXT NOT NULL,
    preregistration_sha256 TEXT NOT NULL,
    config_json TEXT NOT NULL,
    git_commit TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_run_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    config_hash TEXT NOT NULL REFERENCES research_config_versions(config_hash),
    strategy_name TEXT NOT NULL CHECK (strategy_name = 'golden-strawberry'),
    job_name TEXT NOT NULL CHECK (job_name = 'strawberry-shadow-one'),
    mode TEXT NOT NULL CHECK (mode = 'sim'),
    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED','SUCCEEDED','FAILED')),
    event_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS research_run_events_run_idx
    ON research_run_events(run_id, event_at);
CREATE INDEX IF NOT EXISTS research_run_events_time_idx
    ON research_run_events(event_at, event_type);

CREATE TABLE IF NOT EXISTS api_requests (
    request_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    request_kind TEXT NOT NULL,
    page_number INTEGER,
    attempt_number INTEGER NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('GET','POST')),
    url TEXT NOT NULL,
    params_json TEXT NOT NULL,
    body_sha256 TEXT,
    request_hash TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    elapsed_ms REAL,
    status TEXT NOT NULL CHECK (status IN ('SUCCESS','ERROR')),
    http_status INTEGER,
    retryable INTEGER NOT NULL CHECK (retryable IN (0,1)),
    retry_after_seconds REAL,
    response_sha256 TEXT,
    response_bytes INTEGER,
    error_type TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS api_requests_run_idx
    ON api_requests(run_id, request_kind, page_number);
CREATE INDEX IF NOT EXISTS api_requests_hash_idx ON api_requests(request_hash);

CREATE TABLE IF NOT EXISTS raw_payloads (
    payload_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    request_id TEXT NOT NULL UNIQUE REFERENCES api_requests(request_id),
    payload_kind TEXT NOT NULL,
    source_received_at TEXT NOT NULL,
    content_encoding TEXT NOT NULL CHECK (content_encoding = 'gzip'),
    payload_sha256 TEXT NOT NULL,
    uncompressed_bytes INTEGER NOT NULL,
    compressed_bytes INTEGER NOT NULL,
    payload_blob BLOB NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS raw_payloads_hash_idx ON raw_payloads(payload_sha256);

CREATE TABLE IF NOT EXISTS market_sweeps (
    sweep_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    cycle_number INTEGER NOT NULL UNIQUE,
    config_hash TEXT NOT NULL REFERENCES research_config_versions(config_hash),
    strategy_source_digest TEXT NOT NULL,
    data_contract TEXT NOT NULL CHECK (data_contract = 'last-mile-clob-v1'),
    source_name TEXT NOT NULL CHECK (source_name = 'clob_sampling_markets'),
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    published_at TEXT NOT NULL,
    cursor_complete INTEGER NOT NULL CHECK (cursor_complete = 1),
    page_count INTEGER NOT NULL,
    membership_count INTEGER NOT NULL,
    unique_condition_count INTEGER NOT NULL,
    aligned_outcome_count INTEGER NOT NULL,
    tradable_market_count INTEGER NOT NULL,
    evidence_catalog_count INTEGER NOT NULL,
    evidence_outcome_count INTEGER NOT NULL,
    membership_sha256 TEXT NOT NULL,
    request_lineage_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS market_sweeps_time_idx ON market_sweeps(completed_at);

CREATE TABLE IF NOT EXISTS market_membership_blobs (
    membership_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL UNIQUE REFERENCES market_sweeps(sweep_id),
    encoding TEXT NOT NULL CHECK (encoding = 'gzip-json-v1'),
    membership_sha256 TEXT NOT NULL,
    uncompressed_bytes INTEGER NOT NULL,
    compressed_bytes INTEGER NOT NULL,
    membership_blob BLOB NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_page_lineage (
    page_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    cursor_in TEXT,
    cursor_out TEXT,
    market_count INTEGER NOT NULL,
    request_id TEXT NOT NULL REFERENCES api_requests(request_id),
    raw_payload_id TEXT NOT NULL REFERENCES raw_payloads(payload_id),
    request_hash TEXT NOT NULL,
    source_received_at TEXT NOT NULL,
    response_sha256 TEXT NOT NULL,
    UNIQUE (sweep_id, page_number)
);

CREATE TABLE IF NOT EXISTS market_catalog_versions (
    catalog_version_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    item_number INTEGER NOT NULL,
    source_received_at TEXT NOT NULL,
    source_request_id TEXT NOT NULL REFERENCES api_requests(request_id),
    condition_id TEXT,
    market_id TEXT,
    event_id TEXT,
    event_ids_json TEXT NOT NULL,
    event_cluster_id TEXT,
    market_slug TEXT,
    question TEXT,
    active INTEGER,
    closed INTEGER,
    orderbook_enabled INTEGER,
    accepting_orders INTEGER,
    tradable INTEGER NOT NULL CHECK (tradable IN (0,1)),
    exclusion_reason TEXT NOT NULL,
    outcome_type TEXT,
    neg_risk INTEGER,
    sports_classification TEXT NOT NULL,
    sports_classifier_version TEXT NOT NULL,
    liquidity REAL,
    volume_total REAL,
    volume_24h REAL,
    end_date TEXT,
    category TEXT,
    tags_json TEXT NOT NULL,
    outcome_labels_json TEXT NOT NULL,
    token_ids_json TEXT NOT NULL,
    outcome_prices_json TEXT NOT NULL,
    raw_market_sha256 TEXT NOT NULL,
    normalized_market_json TEXT NOT NULL,
    UNIQUE (sweep_id, page_number, item_number)
);
CREATE INDEX IF NOT EXISTS market_catalog_condition_idx
    ON market_catalog_versions(condition_id, source_received_at);
CREATE INDEX IF NOT EXISTS market_catalog_cluster_idx
    ON market_catalog_versions(event_cluster_id, source_received_at);

CREATE TABLE IF NOT EXISTS outcome_observations (
    observation_id TEXT PRIMARY KEY,
    catalog_version_id TEXT NOT NULL REFERENCES market_catalog_versions(catalog_version_id),
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    market_id TEXT,
    event_id TEXT,
    event_cluster_id TEXT,
    token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    outcome_label TEXT NOT NULL,
    probability REAL NOT NULL CHECK (probability >= 0 AND probability <= 1),
    observed_at TEXT NOT NULL,
    outcome_type TEXT NOT NULL CHECK (outcome_type IN ('BINARY','MULTI')),
    neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),
    sports_classification TEXT NOT NULL CHECK (sports_classification IN ('SPORTS','NON_SPORTS','UNKNOWN')),
    sports_classifier_version TEXT NOT NULL,
    liquidity REAL,
    volume_total REAL,
    volume_24h REAL,
    end_date TEXT,
    category TEXT,
    tags_json TEXT NOT NULL,
    raw_market_sha256 TEXT NOT NULL,
    UNIQUE (sweep_id, token_id)
);
CREATE INDEX IF NOT EXISTS outcome_token_history_idx
    ON outcome_observations(token_id, observed_at);
CREATE INDEX IF NOT EXISTS outcome_condition_idx
    ON outcome_observations(condition_id, observed_at);

CREATE TABLE IF NOT EXISTS crossing_decisions (
    decision_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES outcome_observations(observation_id),
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    entry_threshold REAL NOT NULL,
    decided_at TEXT NOT NULL,
    prior_condition_id TEXT,
    prior_probability REAL,
    prior_observed_at TEXT,
    prior_gap_minutes REAL,
    current_probability REAL NOT NULL,
    decision_status TEXT NOT NULL,
    interval_censored INTEGER NOT NULL CHECK (interval_censored IN (0,1)),
    jump_size REAL,
    crossed_threshold_count INTEGER NOT NULL,
    episode_id TEXT,
    details_json TEXT NOT NULL,
    UNIQUE (sweep_id, token_id, entry_threshold)
);
CREATE INDEX IF NOT EXISTS crossing_decision_status_idx
    ON crossing_decisions(decision_status, decided_at, entry_threshold);

CREATE TABLE IF NOT EXISTS candidate_metadata_observations (
    metadata_observation_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    lookup_status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    request_id TEXT REFERENCES api_requests(request_id),
    raw_market_sha256 TEXT,
    market_id TEXT,
    event_id TEXT,
    event_ids_json TEXT NOT NULL,
    event_cluster_id TEXT,
    liquidity REAL,
    volume_total REAL,
    volume_24h REAL,
    end_date TEXT,
    category TEXT,
    tags_json TEXT NOT NULL,
    enrichment_lag_seconds REAL,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (sweep_id, condition_id)
);
CREATE INDEX IF NOT EXISTS candidate_metadata_condition_idx
    ON candidate_metadata_observations(condition_id, observed_at);

CREATE TABLE IF NOT EXISTS clob_token_attempts (
    attempt_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    attempt_role TEXT NOT NULL CHECK (attempt_role IN ('CROSSING','EPISODE','BOTH')),
    status TEXT NOT NULL CHECK (status IN ('OBSERVED','EMPTY_BOOK','MISSING','MALFORMED','ERROR')),
    request_id TEXT REFERENCES api_requests(request_id),
    request_started_at TEXT,
    source_received_at TEXT,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (sweep_id, token_id)
);
CREATE INDEX IF NOT EXISTS clob_attempt_status_idx
    ON clob_token_attempts(status, source_received_at);

CREATE TABLE IF NOT EXISTS clob_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    request_id TEXT NOT NULL REFERENCES api_requests(request_id),
    source_received_at TEXT NOT NULL,
    raw_book_sha256 TEXT NOT NULL,
    source_timestamp TEXT,
    tick_size REAL,
    min_order_size REAL,
    fee_rate_bps REAL,
    source_metadata_json TEXT NOT NULL,
    bid_level_count INTEGER NOT NULL,
    ask_level_count INTEGER NOT NULL,
    best_bid REAL,
    best_ask REAL,
    spread REAL,
    bid_depth_notional REAL NOT NULL,
    ask_depth_notional REAL NOT NULL,
    UNIQUE (sweep_id, token_id)
);
CREATE INDEX IF NOT EXISTS clob_snapshot_token_idx
    ON clob_snapshots(token_id, source_received_at);

CREATE TABLE IF NOT EXISTS clob_levels (
    level_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES clob_snapshots(snapshot_id),
    side TEXT NOT NULL CHECK (side IN ('BID','ASK')),
    level_index INTEGER NOT NULL,
    price REAL NOT NULL CHECK (price > 0 AND price <= 1),
    size REAL NOT NULL CHECK (size > 0),
    UNIQUE (snapshot_id, side, level_index)
);

CREATE TABLE IF NOT EXISTS hypothetical_episodes (
    episode_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE REFERENCES crossing_decisions(decision_id),
    originating_sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    market_id TEXT,
    event_id TEXT,
    event_cluster_id TEXT,
    token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    outcome_label TEXT NOT NULL,
    outcome_type TEXT NOT NULL CHECK (outcome_type IN ('BINARY','MULTI')),
    neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),
    sports_classification TEXT NOT NULL CHECK (sports_classification IN ('SPORTS','NON_SPORTS','UNKNOWN')),
    metadata_observation_id TEXT REFERENCES candidate_metadata_observations(metadata_observation_id),
    metadata_status TEXT NOT NULL,
    entry_threshold REAL NOT NULL,
    crossing_prior_probability REAL NOT NULL,
    crossing_probability REAL NOT NULL,
    crossing_gap_minutes REAL NOT NULL,
    interval_censored INTEGER NOT NULL CHECK (interval_censored = 1),
    entry_observed_at TEXT NOT NULL,
    entry_status TEXT NOT NULL,
    entry_censor_reason TEXT,
    entry_snapshot_id TEXT REFERENCES clob_snapshots(snapshot_id),
    entry_notional_usdc REAL NOT NULL CHECK (entry_notional_usdc = 5),
    entry_ask_vwap REAL,
    fixed_shares REAL,
    best_ask REAL,
    spread REAL,
    ask_depth_notional REAL,
    source_tick_size REAL,
    source_min_order_size REAL,
    source_fee_rate_bps REAL,
    liquidity REAL,
    volume_total REAL,
    volume_24h REAL,
    end_date TEXT,
    category TEXT,
    tags_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (token_id, entry_threshold)
);
CREATE INDEX IF NOT EXISTS episode_entry_idx
    ON hypothetical_episodes(entry_status, entry_observed_at, entry_threshold);
CREATE INDEX IF NOT EXISTS episode_cluster_idx
    ON hypothetical_episodes(event_cluster_id, entry_observed_at);

CREATE TABLE IF NOT EXISTS episode_path_observations (
    path_observation_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    snapshot_id TEXT REFERENCES clob_snapshots(snapshot_id),
    observed_at TEXT NOT NULL,
    path_status TEXT NOT NULL,
    censor_reason TEXT,
    fixed_shares REAL NOT NULL,
    best_bid REAL,
    exit_bid_vwap REAL,
    exit_proceeds_usdc REAL,
    bid_depth_notional REAL,
    prior_executable_bid_vwap REAL,
    interval_censored INTEGER NOT NULL CHECK (interval_censored IN (0,1)),
    entry_cycle_baseline INTEGER NOT NULL CHECK (entry_cycle_baseline IN (0,1)),
    details_json TEXT NOT NULL,
    UNIQUE (episode_id, sweep_id)
);
CREATE INDEX IF NOT EXISTS episode_path_time_idx
    ON episode_path_observations(episode_id, observed_at);

CREATE TABLE IF NOT EXISTS episode_threshold_events (
    threshold_event_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),
    path_observation_id TEXT NOT NULL REFERENCES episode_path_observations(path_observation_id),
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    event_kind TEXT NOT NULL CHECK (event_kind IN ('STOP','TARGET')),
    threshold REAL NOT NULL,
    observed_at TEXT NOT NULL,
    executable_bid_vwap REAL NOT NULL,
    prior_executable_bid_vwap REAL,
    interval_censored INTEGER NOT NULL CHECK (interval_censored IN (0,1)),
    conservative_priority INTEGER NOT NULL,
    UNIQUE (episode_id, event_kind, threshold)
);

CREATE TABLE IF NOT EXISTS resolution_observations (
    resolution_observation_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    lookup_status TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK (resolution_status IN ('RESOLVED','UNRESOLVED','MISSING','MALFORMED','ERROR')),
    request_id TEXT REFERENCES api_requests(request_id),
    raw_market_sha256 TEXT,
    winning_outcome_index INTEGER,
    winning_outcome_label TEXT,
    winning_token_id TEXT,
    token_payouts_json TEXT NOT NULL,
    resolution_jump_without_target_json TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (sweep_id, condition_id)
);
CREATE INDEX IF NOT EXISTS resolution_condition_idx
    ON resolution_observations(condition_id, observed_at, resolution_status);

CREATE TABLE IF NOT EXISTS cycle_stats (
    cycle_stat_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    sweep_id TEXT NOT NULL UNIQUE REFERENCES market_sweeps(sweep_id),
    cycle_number INTEGER NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    runtime_seconds REAL NOT NULL,
    page_count INTEGER NOT NULL,
    membership_count INTEGER NOT NULL,
    crossing_count INTEGER NOT NULL,
    executable_episode_count INTEGER NOT NULL,
    clob_requested_count INTEGER NOT NULL,
    path_observation_count INTEGER NOT NULL,
    resolution_observation_count INTEGER NOT NULL,
    stats_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    issue_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    sweep_id TEXT,
    severity TEXT NOT NULL CHECK (severity IN ('INFO','WARN','HIGH','CRITICAL')),
    issue_code TEXT NOT NULL,
    details_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quality_issue_idx
    ON data_quality_issues(severity, recorded_at, issue_code);

CREATE TABLE IF NOT EXISTS storage_metrics (
    metric_id TEXT PRIMARY KEY,
    run_id TEXT,
    phase TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    db_bytes INTEGER NOT NULL,
    journal_bytes INTEGER NOT NULL,
    filesystem_total_bytes INTEGER NOT NULL,
    filesystem_used_bytes INTEGER NOT NULL,
    filesystem_free_bytes INTEGER NOT NULL,
    filesystem_used_ratio REAL NOT NULL,
    guard_state TEXT NOT NULL CHECK (guard_state IN ('OK','WARN','STOP'))
);
CREATE INDEX IF NOT EXISTS storage_metric_time_idx ON storage_metrics(recorded_at);

-- The sole mutable table. It is a labeled latest-state cache used only to
-- detect sampled crossings; all source and decision evidence above is immutable.
CREATE TABLE IF NOT EXISTS latest_outcome_state (
    token_id TEXT PRIMARY KEY,
    condition_id TEXT NOT NULL,
    probability REAL NOT NULL CHECK (probability >= 0 AND probability <= 1),
    observed_at TEXT NOT NULL,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    source_request_id TEXT NOT NULL REFERENCES api_requests(request_id),
    source_page_number INTEGER NOT NULL,
    source_item_number INTEGER NOT NULL,
    raw_market_sha256 TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


APPEND_ONLY_TABLES = (
    "schema_metadata",
    "experiment_contracts",
    "research_config_versions",
    "research_run_events",
    "api_requests",
    "raw_payloads",
    "market_sweeps",
    "market_membership_blobs",
    "market_page_lineage",
    "market_catalog_versions",
    "outcome_observations",
    "crossing_decisions",
    "candidate_metadata_observations",
    "clob_token_attempts",
    "clob_snapshots",
    "clob_levels",
    "hypothetical_episodes",
    "episode_path_observations",
    "episode_threshold_events",
    "resolution_observations",
    "cycle_stats",
    "data_quality_issues",
    "storage_metrics",
)


def _append_only_triggers() -> str:
    statements: list[str] = []
    for table in APPEND_ONLY_TABLES:
        statements.extend(
            (
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_update "
                f"BEFORE UPDATE ON {table} BEGIN "
                "SELECT RAISE(ABORT, 'append-only evidence'); END;",
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete "
                f"BEFORE DELETE ON {table} BEGIN "
                "SELECT RAISE(ABORT, 'append-only evidence'); END;",
            )
        )
    return "\n".join(statements)


class ResearchRepository:
    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 30_000) -> None:
        self.db_path = Path(db_path)
        self.busy_timeout_ms = busy_timeout_ms

    @contextmanager
    def _connect(self, *, create: bool = True) -> Iterator[sqlite3.Connection]:
        if create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.db_path,
            timeout=self.busy_timeout_ms / 1000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _read_connect(self, *, immutable: bool = False) -> Iterator[sqlite3.Connection]:
        if not self.db_path.is_file():
            raise FileNotFoundError(self.db_path)
        suffix = "&immutable=1" if immutable else ""
        uri = f"file:{quote(str(self.db_path.resolve()))}?mode=ro{suffix}"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self, config: BotConfig) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            existing = connection.execute(
                "SELECT value FROM schema_metadata WHERE key='schema_version'"
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO schema_metadata(key,value) VALUES('schema_version',?)",
                    (str(SCHEMA_VERSION),),
                )
                connection.execute(
                    "INSERT INTO schema_metadata(key,value) VALUES('data_contract',?)",
                    (DATA_CONTRACT,),
                )
                connection.execute(
                    "INSERT INTO schema_metadata(key,value) VALUES('mutable_cache_table',"
                    "'latest_outcome_state')"
                )
            elif existing["value"] != str(SCHEMA_VERSION):
                raise RuntimeError("unsupported Golden Strawberry schema version")

            contract = self._experiment_contract(config)
            canonical = canonical_json(contract)
            row = connection.execute(
                "SELECT contract_json FROM experiment_contracts WHERE job_name=?",
                (config.job_name,),
            ).fetchone()
            if row is None:
                experiment = config.trading.experiment
                connection.execute(
                    """
                    INSERT INTO experiment_contracts(
                        job_name,strategy_name,data_contract,lifecycle_mode,
                        cadence_minutes,cadence_offset_minute,
                        entry_start,entry_end,followup_end,
                        entry_thresholds_json,stop_thresholds_json,
                        target_thresholds_json,primary_entry_threshold,
                        primary_stop_threshold,preregistration_sha256,
                        contract_json,created_at
                    ) VALUES(?, 'golden-strawberry', ?, 'archive_only', ?, ?, ?, ?, ?,
                             ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        config.job_name,
                        DATA_CONTRACT,
                        config.trading.cadence_minutes,
                        config.trading.cadence_offset_minute,
                        iso_utc(experiment.entry_start_utc),
                        iso_utc(experiment.entry_end_utc),
                        iso_utc(experiment.followup_end_utc),
                        canonical_json(experiment.entry_thresholds),
                        canonical_json(experiment.stop_thresholds),
                        canonical_json(experiment.target_thresholds),
                        experiment.primary_entry_threshold,
                        experiment.primary_stop_threshold,
                        experiment.preregistration_sha256,
                        canonical,
                        iso_utc(),
                    ),
                )
            elif row["contract_json"] != canonical:
                raise RuntimeError(
                    "existing DB has a different immutable experiment contract"
                )
            connection.executescript(_append_only_triggers())
            connection.commit()

    @staticmethod
    def _experiment_contract(config: BotConfig) -> dict[str, Any]:
        experiment = config.trading.experiment
        return {
            "strategy_name": "golden-strawberry",
            "job_name": config.job_name,
            "data_contract": config.trading.data_contract,
            "lifecycle_mode": config.trading.lifecycle_mode,
            "cadence_minutes": config.trading.cadence_minutes,
            "cadence_offset_minute": config.trading.cadence_offset_minute,
            "entry_start": iso_utc(experiment.entry_start_utc),
            "entry_end": iso_utc(experiment.entry_end_utc),
            "followup_end": iso_utc(experiment.followup_end_utc),
            "entry_thresholds": experiment.entry_thresholds,
            "stop_thresholds": experiment.stop_thresholds,
            "target_thresholds": experiment.target_thresholds,
            "primary_entry_threshold": experiment.primary_entry_threshold,
            "primary_stop_threshold": experiment.primary_stop_threshold,
            "simulated_notional_usdc": experiment.simulated_notional_usdc,
            "prior_gap_max_minutes": experiment.prior_gap_max_minutes,
            "sports_classifier_version": experiment.sports_classifier_version,
            "base_cost_stress_bps": experiment.base_cost_stress_bps,
            "severe_cost_stress_bps": experiment.severe_cost_stress_bps,
            "sampling_source": "clob_sampling_markets",
            "sampling_page_size": config.trading.sampling.page_size,
            "sampling_max_pages": config.trading.sampling.max_pages,
            "preregistration_sha256": experiment.preregistration_sha256,
        }

    def register_config(self, config: BotConfig, *, git_commit: str | None) -> None:
        payload = canonical_json(config.redacted_dict())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT config_json FROM research_config_versions WHERE config_hash=?",
                (config.config_hash,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO research_config_versions(
                        config_hash,strategy_name,job_name,mode,lifecycle_mode,
                        data_contract,strategy_source_digest,
                        preregistration_sha256,config_json,git_commit,created_at
                    ) VALUES(?, 'golden-strawberry', ?, 'sim', 'archive_only', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        config.config_hash,
                        config.job_name,
                        DATA_CONTRACT,
                        config.trading.strategy_source_digest,
                        config.trading.experiment.preregistration_sha256,
                        payload,
                        git_commit,
                        iso_utc(),
                    ),
                )
            elif row["config_json"] != payload:
                raise RuntimeError(
                    "config hash collision with different resolved config"
                )
            connection.commit()

    def record_research_run_event(self, row: Mapping[str, Any]) -> None:
        self._insert_one("research_run_events", row)

    def record_api_request(self, row: Mapping[str, Any]) -> None:
        self._insert_one("api_requests", row)

    def _insert_one(self, table: str, row: Mapping[str, Any]) -> None:
        if table not in APPEND_ONLY_TABLES:
            raise ValueError("table is not an approved append-only evidence table")
        columns = tuple(row)
        placeholders = ",".join("?" for _ in columns)
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})",
                tuple(row[column] for column in columns),
            )
            connection.commit()

    @staticmethod
    def _insert_many(
        connection: sqlite3.Connection,
        table: str,
        rows: Iterable[Mapping[str, Any]],
    ) -> None:
        materialized = list(rows)
        if not materialized:
            return
        columns = tuple(materialized[0])
        if any(tuple(row) != columns for row in materialized):
            raise ValueError(f"inconsistent row columns for {table}")
        placeholders = ",".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})",
            [tuple(row[column] for column in columns) for row in materialized],
        )

    def next_cycle_number(self) -> int:
        if not self.db_path.exists():
            return 1
        with self._read_connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(cycle_number),0)+1 AS value FROM market_sweeps"
            ).fetchone()
            return int(row["value"])

    def latest_states(self, token_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        unique = list(dict.fromkeys(str(value) for value in token_ids if str(value)))
        if not unique or not self.db_path.exists():
            return {}
        result: dict[str, dict[str, Any]] = {}
        with self._read_connect() as connection:
            for offset in range(0, len(unique), 500):
                chunk = unique[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"SELECT * FROM latest_outcome_state WHERE token_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                result.update({row["token_id"]: dict(row) for row in rows})
        return result

    def episode_keys(self) -> set[tuple[str, float]]:
        if not self.db_path.exists():
            return set()
        with self._read_connect() as connection:
            return {
                (str(row["token_id"]), float(row["entry_threshold"]))
                for row in connection.execute(
                    "SELECT token_id,entry_threshold FROM hypothetical_episodes"
                )
            }

    def unresolved_episodes(self) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        with self._read_connect() as connection:
            rows = connection.execute(
                """
                SELECT e.*
                FROM hypothetical_episodes e
                WHERE e.entry_status='EXECUTABLE'
                  AND NOT EXISTS (
                      SELECT 1 FROM resolution_observations r
                      WHERE r.condition_id=e.condition_id
                        AND r.resolution_status='RESOLVED'
                  )
                ORDER BY e.entry_observed_at,e.episode_id
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def threshold_event_keys(
        self, episode_ids: Sequence[str]
    ) -> set[tuple[str, str, float]]:
        unique = list(dict.fromkeys(episode_ids))
        if not unique or not self.db_path.exists():
            return set()
        result: set[tuple[str, str, float]] = set()
        with self._read_connect() as connection:
            for offset in range(0, len(unique), 500):
                chunk = unique[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    "SELECT episode_id,event_kind,threshold "
                    f"FROM episode_threshold_events WHERE episode_id IN ({placeholders})",
                    chunk,
                )
                result.update(
                    (
                        str(row["episode_id"]),
                        str(row["event_kind"]),
                        float(row["threshold"]),
                    )
                    for row in rows
                )
        return result

    def latest_path_vwaps(self, episode_ids: Sequence[str]) -> dict[str, float]:
        unique = list(dict.fromkeys(episode_ids))
        if not unique or not self.db_path.exists():
            return {}
        result: dict[str, float] = {}
        with self._read_connect() as connection:
            for offset in range(0, len(unique), 500):
                chunk = unique[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    """
                    SELECT p.episode_id,p.exit_bid_vwap
                    FROM episode_path_observations p
                    JOIN (
                        SELECT episode_id,MAX(observed_at) AS observed_at
                        FROM episode_path_observations
                        WHERE path_status='EXECUTABLE'
                          AND episode_id IN ("""
                    + placeholders
                    + """
                        ) GROUP BY episode_id
                    ) latest
                      ON latest.episode_id=p.episode_id
                     AND latest.observed_at=p.observed_at
                    WHERE p.path_status='EXECUTABLE'
                    """,
                    chunk,
                ).fetchall()
                result.update(
                    {
                        str(row["episode_id"]): float(row["exit_bid_vwap"])
                        for row in rows
                        if row["exit_bid_vwap"] is not None
                    }
                )
        return result

    @staticmethod
    def _validate_payload_rows(rows: Sequence[Mapping[str, Any]]) -> None:
        request_ids: set[str] = set()
        for row in rows:
            raw = gzip.decompress(bytes(row["payload_blob"]))
            if len(raw) != int(row["uncompressed_bytes"]):
                raise ValueError("raw payload byte count mismatch")
            if hashlib.sha256(raw).hexdigest() != row["payload_sha256"]:
                raise ValueError("raw payload SHA-256 mismatch")
            request_id = str(row["request_id"])
            if request_id in request_ids:
                raise ValueError("duplicate raw payload request_id in cycle")
            request_ids.add(request_id)

    @staticmethod
    def _validate_membership(row: Mapping[str, Any], expected_count: int) -> None:
        raw = gzip.decompress(bytes(row["membership_blob"]))
        if len(raw) != int(row["uncompressed_bytes"]):
            raise ValueError("membership byte count mismatch")
        if hashlib.sha256(raw).hexdigest() != row["membership_sha256"]:
            raise ValueError("membership SHA-256 mismatch")
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) != expected_count:
            raise ValueError("membership blob does not match full census count")

    def publish_cycle(self, bundle: Mapping[str, Any]) -> None:
        sweep = bundle["sweep"]
        if int(sweep.get("cursor_complete", 0)) != 1:
            raise ValueError("partial sampling sweeps cannot be published")
        pages = list(bundle.get("pages", ()))
        catalog = list(bundle.get("catalog", ()))
        outcomes = list(bundle.get("outcomes", ()))
        raw_payloads = list(bundle.get("raw_payloads", ()))
        membership = bundle["membership"]
        if int(sweep["page_count"]) != len(pages):
            raise ValueError("sampling page count does not match lineage")
        if int(sweep["evidence_catalog_count"]) != len(catalog):
            raise ValueError("evidence catalog count does not match rows")
        if int(sweep["evidence_outcome_count"]) != len(outcomes):
            raise ValueError("evidence outcome count does not match rows")
        self._validate_payload_rows(raw_payloads)
        self._validate_membership(membership, int(sweep["membership_count"]))
        raw_request_ids = {str(row["request_id"]) for row in raw_payloads}
        if any(str(row["request_id"]) not in raw_request_ids for row in pages):
            raise ValueError("sampling page is missing raw payload linkage")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._insert_many(connection, "market_sweeps", [sweep])
                self._insert_many(connection, "market_membership_blobs", [membership])
                self._insert_many(connection, "raw_payloads", raw_payloads)
                self._insert_many(connection, "market_page_lineage", pages)
                self._insert_many(connection, "market_catalog_versions", catalog)
                self._insert_many(connection, "outcome_observations", outcomes)
                self._insert_many(
                    connection,
                    "crossing_decisions",
                    bundle.get("crossing_decisions", ()),
                )
                self._insert_many(
                    connection,
                    "candidate_metadata_observations",
                    bundle.get("candidate_metadata", ()),
                )
                self._insert_many(
                    connection,
                    "clob_token_attempts",
                    bundle.get("clob_attempts", ()),
                )
                self._insert_many(
                    connection, "clob_snapshots", bundle.get("clob_snapshots", ())
                )
                self._insert_many(
                    connection, "clob_levels", bundle.get("clob_levels", ())
                )
                self._insert_many(
                    connection,
                    "hypothetical_episodes",
                    bundle.get("episodes", ()),
                )
                self._insert_many(
                    connection,
                    "episode_path_observations",
                    bundle.get("paths", ()),
                )
                self._insert_many(
                    connection,
                    "episode_threshold_events",
                    bundle.get("threshold_events", ()),
                )
                self._insert_many(
                    connection,
                    "resolution_observations",
                    bundle.get("resolutions", ()),
                )
                self._insert_many(
                    connection,
                    "data_quality_issues",
                    bundle.get("quality_issues", ()),
                )
                self._insert_many(connection, "cycle_stats", [bundle["cycle_stats"]])
                for state in bundle.get("latest_states", ()):
                    connection.execute(
                        """
                        INSERT INTO latest_outcome_state(
                            token_id,condition_id,probability,observed_at,
                            sweep_id,source_request_id,source_page_number,
                            source_item_number,raw_market_sha256,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(token_id) DO UPDATE SET
                            condition_id=excluded.condition_id,
                            probability=excluded.probability,
                            observed_at=excluded.observed_at,
                            sweep_id=excluded.sweep_id,
                            source_request_id=excluded.source_request_id,
                            source_page_number=excluded.source_page_number,
                            source_item_number=excluded.source_item_number,
                            raw_market_sha256=excluded.raw_market_sha256,
                            updated_at=excluded.updated_at
                        """,
                        (
                            state["token_id"],
                            state["condition_id"],
                            state["probability"],
                            state["observed_at"],
                            state["sweep_id"],
                            state["source_request_id"],
                            state["source_page_number"],
                            state["source_item_number"],
                            state["raw_market_sha256"],
                            state["updated_at"],
                        ),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def record_storage_metric(
        self,
        *,
        phase: str,
        storage: StorageConfig,
        metric_id: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        usage_path = self.db_path.parent
        while not usage_path.exists() and usage_path != usage_path.parent:
            usage_path = usage_path.parent
        usage = shutil.disk_usage(usage_path)
        used_ratio = usage.used / usage.total if usage.total else 1.0
        if (
            usage.free < storage.min_free_gib * GIB
            or used_ratio >= storage.stop_used_ratio
        ):
            state = "STOP"
        elif used_ratio >= storage.warn_used_ratio:
            state = "WARN"
        else:
            state = "OK"
        row = {
            "metric_id": metric_id,
            "run_id": run_id,
            "phase": phase,
            "recorded_at": iso_utc(),
            "db_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "journal_bytes": (
                self.db_path.with_name(self.db_path.name + "-journal").stat().st_size
                if self.db_path.with_name(self.db_path.name + "-journal").exists()
                else 0
            ),
            "filesystem_total_bytes": usage.total,
            "filesystem_used_bytes": usage.used,
            "filesystem_free_bytes": usage.free,
            "filesystem_used_ratio": used_ratio,
            "guard_state": state,
        }
        self._insert_one("storage_metrics", row)
        return row

    def status(self) -> dict[str, Any]:
        if not self.db_path.is_file():
            return {
                "healthy": False,
                "database_exists": False,
                "db_path": str(self.db_path),
                "data_contract": DATA_CONTRACT,
            }
        with self._read_connect() as connection:
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            latest_sweep = connection.execute(
                "SELECT * FROM market_sweeps ORDER BY cycle_number DESC LIMIT 1"
            ).fetchone()
            latest_event = connection.execute(
                "SELECT * FROM research_run_events ORDER BY event_at DESC,event_id DESC LIMIT 1"
            ).fetchone()
            table_counts = {
                table: int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                for table in (
                    "market_sweeps",
                    "market_catalog_versions",
                    "outcome_observations",
                    "crossing_decisions",
                    "candidate_metadata_observations",
                    "hypothetical_episodes",
                    "episode_path_observations",
                    "resolution_observations",
                    "data_quality_issues",
                )
            }
            unresolved = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM hypothetical_episodes e
                    WHERE e.entry_status='EXECUTABLE'
                      AND NOT EXISTS (
                          SELECT 1 FROM resolution_observations r
                          WHERE r.condition_id=e.condition_id
                            AND r.resolution_status='RESOLVED'
                      )
                    """
                ).fetchone()[0]
            )
            return {
                "healthy": quick_check == "ok" and latest_event is not None,
                "database_exists": True,
                "db_path": str(self.db_path),
                "db_bytes": self.db_path.stat().st_size,
                "quick_check": quick_check,
                "data_contract": DATA_CONTRACT,
                "latest_sweep": dict(latest_sweep) if latest_sweep else None,
                "latest_run_event": dict(latest_event) if latest_event else None,
                "unresolved_executable_episodes": unresolved,
                "table_counts": table_counts,
            }

    def health(self, *, cadence_minutes: int) -> dict[str, Any]:
        status = self.status()
        if not status.get("database_exists"):
            return status
        with self._read_connect() as connection:
            now = datetime.now(timezone.utc)
            since = iso_utc(now - timedelta(hours=24))
            run_rows = connection.execute(
                """
                WITH ranked AS (
                    SELECT run_id,event_type,event_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY run_id ORDER BY event_at DESC,event_id DESC
                           ) AS position
                    FROM research_run_events WHERE event_at>=?
                )
                SELECT event_type,COUNT(*) AS count
                FROM ranked WHERE position=1 GROUP BY event_type
                """,
                (since,),
            ).fetchall()
            terminal = {str(row["event_type"]): int(row["count"]) for row in run_rows}
            latest_success = connection.execute(
                "SELECT MAX(event_at) FROM research_run_events WHERE event_type='SUCCEEDED'"
            ).fetchone()[0]
            high_issues = int(
                connection.execute(
                    "SELECT COUNT(*) FROM data_quality_issues "
                    "WHERE recorded_at>=? AND severity IN ('HIGH','CRITICAL')",
                    (since,),
                ).fetchone()[0]
            )
            latest_storage = connection.execute(
                "SELECT * FROM storage_metrics ORDER BY recorded_at DESC LIMIT 1"
            ).fetchone()
        age_minutes: float | None = None
        if latest_success:
            parsed = datetime.fromisoformat(str(latest_success).replace("Z", "+00:00"))
            age_minutes = (now - parsed.astimezone(timezone.utc)).total_seconds() / 60
        healthy = bool(
            status.get("quick_check") == "ok"
            and (status.get("latest_run_event") or {}).get("event_type") == "SUCCEEDED"
            and latest_success
            and age_minutes is not None
            and age_minutes <= cadence_minutes * 2.5
            and high_issues == 0
            and (latest_storage is None or latest_storage["guard_state"] != "STOP")
        )
        return {
            **status,
            "healthy": healthy,
            "cadence_minutes": cadence_minutes,
            "latest_success_at": latest_success,
            "latest_success_age_minutes": age_minutes,
            "terminal_runs_last_24h": terminal,
            "high_or_critical_issues_last_24h": high_issues,
            "latest_storage": dict(latest_storage) if latest_storage else None,
        }


__all__ = [
    "APPEND_ONLY_TABLES",
    "GIB",
    "ResearchRepository",
    "SCHEMA",
    "SCHEMA_VERSION",
]
