"""Append-only SQLite evidence store for Queue Echo."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Iterable, Iterator, Mapping
from uuid import uuid4

from ..config import BotConfig, DATA_CONTRACT, StorageConfig
from ..utils.retry import iso_utc


GIB = 1024**3
SCHEMA_VERSION = 3
SCHEMA_PROFILE = "queue-echo-v3-sqlite-v3"


def _parse_utc(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


SCHEMA = r"""
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_contracts (
    job_name TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    data_contract TEXT NOT NULL CHECK (data_contract = 'queue-echo-v3'),
    schema_version INTEGER NOT NULL CHECK (schema_version = 3),
    schema_profile TEXT NOT NULL CHECK (schema_profile = 'queue-echo-v3-sqlite-v3'),
    shard_index INTEGER NOT NULL CHECK (shard_index BETWEEN 0 AND 2),
    shard_count INTEGER NOT NULL CHECK (shard_count = 3),
    cadence_minutes INTEGER NOT NULL CHECK (cadence_minutes = 5),
    cadence_offset_minute INTEGER NOT NULL CHECK (cadence_offset_minute BETWEEN 0 AND 2),
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    preregistration_sha256 TEXT NOT NULL,
    data_contract_sha256 TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_config_versions (
    config_hash TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    job_name TEXT NOT NULL,
    shard_index INTEGER NOT NULL,
    shard_count INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK (mode = 'sim'),
    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode = 'archive_only'),
    data_contract TEXT NOT NULL CHECK (data_contract = 'queue-echo-v3'),
    schema_version INTEGER NOT NULL CHECK (schema_version = 3),
    strategy_source_digest TEXT NOT NULL,
    preregistration_sha256 TEXT NOT NULL,
    data_contract_sha256 TEXT NOT NULL,
    config_json TEXT NOT NULL,
    git_commit TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_run_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    config_hash TEXT NOT NULL REFERENCES research_config_versions(config_hash),
    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED', 'SUCCEEDED', 'FAILED')),
    event_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS research_run_events_run_idx
    ON research_run_events(run_id, event_at);

CREATE TABLE IF NOT EXISTS cycle_slot_claims (
    claim_id TEXT PRIMARY KEY,
    slot_id TEXT NOT NULL,
    job_name TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    strategy_source_digest TEXT NOT NULL,
    invocation_id TEXT NOT NULL UNIQUE,
    owner_run_id TEXT,
    slot_at TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    lateness_seconds REAL NOT NULL CHECK (lateness_seconds >= 0),
    disposition TEXT NOT NULL CHECK (disposition IN ('CLAIMED', 'SKIPPED_LATE')),
    UNIQUE (job_name, slot_id),
    UNIQUE (job_name, slot_at)
);

CREATE TABLE IF NOT EXISTS cycle_slot_events (
    event_id TEXT PRIMARY KEY,
    invocation_id TEXT NOT NULL UNIQUE,
    claim_id TEXT REFERENCES cycle_slot_claims(claim_id),
    slot_id TEXT NOT NULL,
    job_name TEXT NOT NULL,
    slot_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    lateness_seconds REAL NOT NULL CHECK (lateness_seconds >= 0),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('CLAIMED', 'SKIPPED_LATE', 'SKIPPED_DUPLICATE')
    ),
    details_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cycle_slot_events_slot_idx
    ON cycle_slot_events(job_name, slot_at, event_type);

CREATE TABLE IF NOT EXISTS api_requests (
    request_id TEXT PRIMARY KEY,
    logical_request_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    request_kind TEXT NOT NULL,
    page_number INTEGER,
    attempt_number INTEGER NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('GET', 'POST')),
    url TEXT NOT NULL,
    params_json TEXT NOT NULL,
    body_sha256 TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    elapsed_ms REAL,
    timeout_connect_seconds REAL NOT NULL,
    timeout_read_seconds REAL NOT NULL,
    budget_remaining_before_seconds REAL,
    status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'ERROR')),
    http_status INTEGER,
    retryable INTEGER NOT NULL,
    retry_after_seconds REAL,
    response_sha256 TEXT,
    response_bytes INTEGER,
    error_type TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS api_requests_run_idx ON api_requests(run_id, request_kind);

CREATE TABLE IF NOT EXISTS market_sweeps (
    sweep_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    cycle_number INTEGER NOT NULL UNIQUE,
    config_hash TEXT NOT NULL,
    strategy_source_digest TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    cursor_complete INTEGER NOT NULL CHECK (cursor_complete = 1),
    page_count INTEGER NOT NULL,
    source_envelope_count INTEGER NOT NULL,
    parsed_market_count INTEGER NOT NULL,
    eligible_market_count INTEGER NOT NULL,
    membership_sha256 TEXT NOT NULL,
    membership_encoding TEXT NOT NULL CHECK (membership_encoding = 'gzip-json-v1'),
    membership_blob BLOB NOT NULL,
    funnel_json TEXT NOT NULL,
    source_filter_json TEXT NOT NULL,
    data_contract TEXT NOT NULL CHECK (data_contract = 'queue-echo-v3')
);

CREATE TABLE IF NOT EXISTS market_observations (
    observation_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    market_id TEXT,
    event_id TEXT,
    market_slug TEXT,
    question TEXT,
    observed_at TEXT NOT NULL,
    end_date TEXT NOT NULL,
    hours_to_end REAL NOT NULL,
    liquidity REAL NOT NULL,
    volume_total REAL NOT NULL,
    volume_24h REAL NOT NULL,
    token_ids_json TEXT NOT NULL,
    outcome_labels_json TEXT NOT NULL,
    outcome_prices_json TEXT NOT NULL,
    gamma_best_bid REAL,
    gamma_best_ask REAL,
    gamma_spread REAL,
    raw_market_sha256 TEXT NOT NULL,
    event_selection_hash TEXT NOT NULL,
    panel_selected INTEGER NOT NULL,
    shard_index INTEGER NOT NULL,
    shard_selected INTEGER NOT NULL,
    tags_json TEXT NOT NULL,
    UNIQUE (sweep_id, condition_id)
);
CREATE INDEX IF NOT EXISTS market_obs_condition_idx
    ON market_observations(condition_id, observed_at);

CREATE TABLE IF NOT EXISTS raw_payloads (
    payload_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    request_id TEXT NOT NULL REFERENCES api_requests(request_id),
    logical_request_id TEXT NOT NULL,
    payload_kind TEXT NOT NULL CHECK (
        payload_kind IN ('clob_universe_books', 'clob_followup_books')
    ),
    content_encoding TEXT NOT NULL CHECK (content_encoding = 'gzip'),
    payload_sha256 TEXT NOT NULL,
    uncompressed_bytes INTEGER NOT NULL,
    compressed_bytes INTEGER NOT NULL,
    payload_blob BLOB NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orderbook_token_attempts (
    attempt_id TEXT PRIMARY KEY,
    sweep_id TEXT REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    condition_id TEXT,
    token_id TEXT NOT NULL,
    outcome_index INTEGER,
    outcome_label TEXT,
    attempt_role TEXT NOT NULL CHECK (attempt_role IN ('UNIVERSE', 'FOLLOWUP_ONLY')),
    status TEXT NOT NULL CHECK (status IN ('OBSERVED', 'EMPTY_BOOK', 'MISSING', 'MALFORMED', 'ERROR')),
    request_id TEXT,
    logical_request_id TEXT,
    request_started_at TEXT,
    received_at TEXT,
    error_type TEXT,
    error_message TEXT,
    CHECK (attempt_role = 'FOLLOWUP_ONLY' OR sweep_id IS NOT NULL),
    UNIQUE (sweep_id, token_id, attempt_role)
);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    sweep_id TEXT REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    strategy_source_digest TEXT NOT NULL,
    condition_id TEXT,
    market_id TEXT,
    event_id TEXT,
    token_id TEXT NOT NULL,
    outcome_index INTEGER,
    outcome_label TEXT,
    snapshot_role TEXT NOT NULL CHECK (snapshot_role IN ('UNIVERSE', 'FOLLOWUP_ONLY')),
    request_started_at TEXT,
    observed_at TEXT NOT NULL,
    source_timestamp TEXT,
    source_hash TEXT,
    raw_book_sha256 TEXT NOT NULL,
    bid_level_count INTEGER NOT NULL,
    ask_level_count INTEGER NOT NULL,
    best_bid REAL,
    best_ask REAL,
    spread REAL,
    tick_size REAL,
    min_order_size REAL,
    best_bid_notional REAL,
    best_ask_notional REAL,
    one_tick_spread INTEGER NOT NULL,
    near_bid_notional REAL,
    near_ask_notional REAL,
    weighted_imbalance REAL,
    pair_score REAL,
    entry_notional_usdc REAL,
    entry_vwap REAL,
    entry_shares REAL,
    entry_complete INTEGER NOT NULL,
    quote_eligible INTEGER NOT NULL,
    candidate_up INTEGER NOT NULL,
    CHECK (snapshot_role = 'FOLLOWUP_ONLY' OR sweep_id IS NOT NULL),
    UNIQUE (sweep_id, token_id, snapshot_role)
);
CREATE INDEX IF NOT EXISTS book_snapshot_history_idx
    ON orderbook_snapshots(condition_id, token_id, observed_at);

CREATE TABLE IF NOT EXISTS orderbook_levels (
    level_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),
    side TEXT NOT NULL CHECK (side IN ('BID', 'ASK')),
    level_index INTEGER NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    in_near_touch_window INTEGER NOT NULL,
    used_for_entry INTEGER NOT NULL,
    UNIQUE (snapshot_id, side, level_index)
);

CREATE TABLE IF NOT EXISTS signal_decisions (
    decision_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    strategy_source_digest TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    market_id TEXT,
    event_id TEXT,
    arm TEXT NOT NULL,
    confirmation_steps INTEGER NOT NULL,
    evaluated_at TEXT NOT NULL,
    pair_snapshot_ids_json TEXT NOT NULL,
    pair_score REAL,
    pair_received_skew_seconds REAL,
    neutral_candidate INTEGER NOT NULL,
    selected_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),
    selected_token_id TEXT,
    selected_outcome_label TEXT,
    history_snapshot_ids_json TEXT NOT NULL,
    history_timestamps_json TEXT NOT NULL,
    history_scores_json TEXT NOT NULL,
    history_gaps_minutes_json TEXT NOT NULL,
    one_sided_candidate INTEGER NOT NULL,
    persistence_passed INTEGER NOT NULL,
    cooldown_allowed INTEGER NOT NULL,
    experiment_window_eligible INTEGER NOT NULL,
    qualified INTEGER NOT NULL,
    rejection_reason TEXT NOT NULL,
    target_at TEXT,
    window_end TEXT,
    prior_price_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),
    prior_15m_move REAL,
    prior_move_bin TEXT NOT NULL CHECK (prior_move_bin IN ('DOWN','FLAT','UP','MISSING')),
    matched_control_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),
    matched_control_prior_price_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),
    matched_control_prior_15m_move REAL,
    matched_control_prior_move_bin TEXT CHECK (matched_control_prior_move_bin IN ('DOWN','FLAT','UP','MISSING')),
    control_match_distance REAL,
    UNIQUE (sweep_id, condition_id, arm)
);
CREATE INDEX IF NOT EXISTS signal_decisions_qualified_idx
    ON signal_decisions(qualified, evaluated_at, condition_id);

CREATE TABLE IF NOT EXISTS research_cases (
    case_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES signal_decisions(decision_id),
    case_kind TEXT NOT NULL CHECK (case_kind IN ('SIGNAL', 'CONTROL', 'OPPOSITE')),
    matched_pair_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    event_id TEXT,
    token_id TEXT NOT NULL,
    outcome_label TEXT,
    entry_snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),
    entry_at TEXT NOT NULL,
    entry_cost_usdc REAL NOT NULL,
    entry_shares REAL NOT NULL,
    entry_vwap REAL NOT NULL,
    target_at TEXT NOT NULL,
    window_end TEXT NOT NULL,
    control_match_distance REAL,
    UNIQUE (decision_id, case_kind)
);
CREATE INDEX IF NOT EXISTS research_cases_due_idx
    ON research_cases(target_at, window_end);

CREATE TABLE IF NOT EXISTS followup_claims (
    claim_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL UNIQUE REFERENCES research_cases(case_id),
    first_claimed_by_run_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    target_at TEXT NOT NULL,
    window_end TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS followup_claim_leases (
    lease_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES followup_claims(claim_id),
    generation INTEGER NOT NULL CHECK (generation >= 1),
    owner_run_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    recovery_reason TEXT NOT NULL,
    UNIQUE (claim_id, generation)
);
CREATE INDEX IF NOT EXISTS followup_claim_leases_latest_idx
    ON followup_claim_leases(claim_id, generation DESC);

CREATE TABLE IF NOT EXISTS followup_request_starts (
    claim_id TEXT PRIMARY KEY REFERENCES followup_claims(claim_id),
    lease_id TEXT NOT NULL REFERENCES followup_claim_leases(lease_id),
    observing_run_id TEXT NOT NULL,
    logical_request_id TEXT NOT NULL,
    request_started_at TEXT NOT NULL,
    token_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS followup_attempts (
    followup_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES research_cases(case_id),
    claim_id TEXT NOT NULL UNIQUE REFERENCES followup_claims(claim_id),
    observing_run_id TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'QUOTE_COMPLETE', 'SOURCE_MISSING', 'EMPTY_BOOK', 'INVALID_QUOTE',
        'WINDOW_EXPIRED', 'STALE_REQUEST_UNKNOWN'
    )),
    source_snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),
    observed_at TEXT,
    exit_bid REAL,
    exit_vwap REAL,
    exit_proceeds_usdc REAL,
    executable_return_bps REAL,
    base_stressed_return_bps REAL,
    severe_stressed_return_bps REAL,
    details_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS followup_case_idx ON followup_attempts(case_id, attempted_at);

CREATE TABLE IF NOT EXISTS cycle_stats (
    cycle_stat_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    cycle_number INTEGER NOT NULL UNIQUE,
    config_hash TEXT NOT NULL,
    shard_index INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    runtime_seconds REAL NOT NULL,
    stats_json TEXT NOT NULL,
    db_bytes INTEGER NOT NULL,
    wal_bytes INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    issue_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    sweep_id TEXT,
    severity TEXT NOT NULL CHECK (severity IN ('INFO', 'WARN', 'HIGH', 'CRITICAL')),
    issue_code TEXT NOT NULL,
    details_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_metrics (
    metric_id TEXT PRIMARY KEY,
    run_id TEXT,
    phase TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    db_bytes INTEGER NOT NULL,
    wal_bytes INTEGER NOT NULL,
    filesystem_total_bytes INTEGER NOT NULL,
    filesystem_used_bytes INTEGER NOT NULL,
    filesystem_free_bytes INTEGER NOT NULL,
    filesystem_used_ratio REAL NOT NULL,
    guard_state TEXT NOT NULL CHECK (guard_state IN ('OK', 'WARN', 'STOP'))
);
"""


APPEND_ONLY_TABLES = (
    "experiment_contracts",
    "research_config_versions",
    "research_run_events",
    "cycle_slot_claims",
    "cycle_slot_events",
    "api_requests",
    "market_sweeps",
    "market_observations",
    "raw_payloads",
    "orderbook_token_attempts",
    "orderbook_snapshots",
    "orderbook_levels",
    "signal_decisions",
    "research_cases",
    "followup_claims",
    "followup_claim_leases",
    "followup_request_starts",
    "followup_attempts",
    "cycle_stats",
    "data_quality_issues",
    "storage_metrics",
)


def _append_only_triggers() -> str:
    statements: list[str] = []
    for table in APPEND_ONLY_TABLES:
        statements.extend(
            [
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END;",
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END;",
            ]
        )
    return "\n".join(statements)


@dataclass(frozen=True)
class SlotClaimResult:
    accepted: bool
    event_type: str
    claim_id: str
    invocation_id: str
    run_id: str | None
    slot_id: str
    slot_at: str
    claimed_at: str
    lateness_seconds: float


@dataclass(frozen=True)
class FollowupClaimBatch:
    due: list[dict[str, Any]]
    expired_terminalized: int
    stale_terminalized: int
    active_claims_skipped: int
    recovered_claims: int


class ResearchRepository:
    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 10_000) -> None:
        self.db_path = Path(db_path)
        self.busy_timeout_ms = busy_timeout_ms

    @contextmanager
    def _connect(self, *, create: bool = True) -> Iterator[sqlite3.Connection]:
        if create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=self.busy_timeout_ms / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        # Jenkins runs are single-writer and guarded by the process lock. A
        # persistent WAL header without live -wal/-shm sidecars prevents the
        # read-only daily-rsync backup transaction from opening on macOS, while
        # WAL provides no concurrency benefit for this short-lived collector.
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self, config: BotConfig) -> None:
        # Inspect any pre-existing file through an immutable read-only handle
        # before writable connection pragmas can create sidecars, checkpoint a
        # WAL, or change its journal header. This makes the external-v2 refusal
        # byte-for-byte non-mutating, not merely schema-migration-free.
        if self.db_path.exists():
            uri = f"file:{self.db_path.resolve().as_posix()}?mode=ro&immutable=1"
            legacy_check = sqlite3.connect(uri, uri=True)
            legacy_check.row_factory = sqlite3.Row
            try:
                metadata_exists = legacy_check.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
                ).fetchone()
                if metadata_exists is None:
                    raise RuntimeError(
                        "refusing to migrate or mutate an unversioned Golden Raspberry DB"
                    )
                metadata = {
                    str(row["key"]): str(row["value"])
                    for row in legacy_check.execute(
                        "SELECT key, value FROM schema_metadata"
                    )
                }
                if (
                    metadata.get("schema_version") != str(SCHEMA_VERSION)
                    or metadata.get("data_contract") != DATA_CONTRACT
                    or metadata.get("schema_profile") != SCHEMA_PROFILE
                ):
                    raise RuntimeError(
                        "refusing to migrate or mutate a non-v3 Golden Raspberry DB"
                    )
            finally:
                legacy_check.close()
        with self._connect() as connection:
            metadata_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
            ).fetchone()
            if metadata_exists:
                metadata = {
                    str(row["key"]): str(row["value"])
                    for row in connection.execute(
                        "SELECT key, value FROM schema_metadata"
                    )
                }
                if metadata.get("schema_version") != str(SCHEMA_VERSION):
                    raise RuntimeError(
                        "refusing to migrate or mutate a non-v3 Golden Raspberry DB"
                    )
                if metadata.get("data_contract") != DATA_CONTRACT:
                    raise RuntimeError(
                        "refusing to open a DB with a different data contract"
                    )
                if metadata.get("schema_profile") != SCHEMA_PROFILE:
                    raise RuntimeError("unsupported Golden Raspberry schema profile")
            connection.executescript(SCHEMA)
            connection.executescript(_append_only_triggers())
            existing = connection.execute(
                "SELECT value FROM schema_metadata WHERE key='schema_version'"
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO schema_metadata(key, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                connection.execute(
                    "INSERT INTO schema_metadata(key, value) VALUES('data_contract', ?)",
                    (DATA_CONTRACT,),
                )
                connection.execute(
                    "INSERT INTO schema_metadata(key, value) VALUES('schema_profile', ?)",
                    (SCHEMA_PROFILE,),
                )
            elif existing["value"] != str(SCHEMA_VERSION):
                raise RuntimeError("unsupported Golden Raspberry schema version")
            contract = self._experiment_contract(config)
            row = connection.execute(
                "SELECT contract_json FROM experiment_contracts WHERE job_name=?",
                (config.job_name,),
            ).fetchone()
            canonical = json.dumps(contract, sort_keys=True, separators=(",", ":"))
            if row is None:
                exp = config.trading.experiment
                connection.execute(
                    """
                    INSERT INTO experiment_contracts(
                        job_name, strategy_name, data_contract, schema_version,
                        schema_profile, shard_index,
                        shard_count, cadence_minutes, cadence_offset_minute,
                        window_start, window_end, preregistration_sha256,
                        data_contract_sha256,
                        contract_json, created_at
                    ) VALUES (?, 'golden-raspberry', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        config.job_name,
                        DATA_CONTRACT,
                        SCHEMA_VERSION,
                        SCHEMA_PROFILE,
                        exp.shard_index,
                        exp.shard_count,
                        config.trading.cadence_minutes,
                        exp.cadence_offset_minute,
                        iso_utc(exp.start_utc),
                        iso_utc(exp.end_utc),
                        exp.preregistration_sha256,
                        exp.data_contract_sha256,
                        canonical,
                        iso_utc(),
                    ),
                )
            elif row["contract_json"] != canonical:
                raise RuntimeError("existing DB has a different immutable experiment contract")
            connection.commit()

    @staticmethod
    def _experiment_contract(config: BotConfig) -> dict[str, Any]:
        exp = config.trading.experiment
        return {
            "strategy_name": "golden-raspberry",
            "job_name": config.job_name,
            "data_contract": config.trading.data_contract,
            "schema_version": SCHEMA_VERSION,
            "schema_profile": SCHEMA_PROFILE,
            "shard_index": exp.shard_index,
            "shard_count": exp.shard_count,
            "cadence_minutes": config.trading.cadence_minutes,
            "cadence_offset_minute": exp.cadence_offset_minute,
            "window_start": iso_utc(exp.start_utc),
            "window_end": iso_utc(exp.end_utc),
            "preregistration_sha256": exp.preregistration_sha256,
            "data_contract_sha256": exp.data_contract_sha256,
            "cooperative_cycle_budget_seconds": config.trading.runtime.cooperative_cycle_budget_seconds,
            "hard_cycle_limit_seconds": config.trading.runtime.hard_cycle_limit_seconds,
            "network_stop_margin_seconds": config.trading.runtime.network_stop_margin_seconds,
            "slot_lateness_seconds": config.trading.runtime.slot_lateness_seconds,
            "followup_claim_stale_seconds": config.trading.runtime.followup_claim_stale_seconds,
        }

    def register_config(self, config: BotConfig, *, git_commit: str | None) -> None:
        payload = json.dumps(config.redacted_dict(), sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            row = connection.execute(
                "SELECT config_json FROM research_config_versions WHERE config_hash=?",
                (config.config_hash,),
            ).fetchone()
            if row is None:
                exp = config.trading.experiment
                connection.execute(
                    """
                    INSERT INTO research_config_versions(
                        config_hash, strategy_name, job_name, shard_index, shard_count, mode,
                        lifecycle_mode, data_contract, schema_version,
                        strategy_source_digest, preregistration_sha256,
                        data_contract_sha256, config_json, git_commit, created_at
                    ) VALUES (
                        ?, 'golden-raspberry', ?, ?, ?, 'sim', 'archive_only', ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        config.config_hash,
                        config.job_name,
                        exp.shard_index,
                        exp.shard_count,
                        DATA_CONTRACT,
                        SCHEMA_VERSION,
                        config.trading.strategy_source_digest,
                        exp.preregistration_sha256,
                        exp.data_contract_sha256,
                        payload,
                        git_commit,
                        iso_utc(),
                    ),
                )
            elif row["config_json"] != payload:
                raise RuntimeError("config hash collision with different resolved config")
            connection.commit()

    def record_research_run_event(self, row: Mapping[str, Any]) -> None:
        self._insert_one("research_run_events", row)

    def record_api_request(self, row: Mapping[str, Any]) -> None:
        self._insert_one("api_requests", row)

    @staticmethod
    def scheduled_slot_at(
        claimed_at: datetime, *, cadence_minutes: int, offset_minute: int
    ) -> datetime:
        current = claimed_at.astimezone(timezone.utc)
        minute_delta = (current.minute - offset_minute) % cadence_minutes
        return current.replace(second=0, microsecond=0) - timedelta(
            minutes=minute_delta
        )

    def claim_cycle_slot(
        self,
        config: BotConfig,
        *,
        claimed_at: datetime,
        invocation_id: str,
        run_id: str,
    ) -> SlotClaimResult:
        exp = config.trading.experiment
        slot_at_dt = self.scheduled_slot_at(
            claimed_at,
            cadence_minutes=config.trading.cadence_minutes,
            offset_minute=exp.cadence_offset_minute,
        )
        claimed_at_dt = claimed_at.astimezone(timezone.utc)
        slot_at = iso_utc(slot_at_dt)
        claimed_at_text = iso_utc(claimed_at_dt)
        slot_id = slot_at
        lateness = max(0.0, (claimed_at_dt - slot_at_dt).total_seconds())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM cycle_slot_claims
                WHERE job_name=? AND slot_id=?
                """,
                (config.job_name, slot_id),
            ).fetchone()
            if existing is not None:
                event_type = "SKIPPED_DUPLICATE"
                connection.execute(
                    """
                    INSERT INTO cycle_slot_events(
                        event_id, invocation_id, claim_id, slot_id, job_name,
                        slot_at, observed_at, lateness_seconds, event_type,
                        details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid4().hex,
                        invocation_id,
                        existing["claim_id"],
                        slot_id,
                        config.job_name,
                        slot_at,
                        claimed_at_text,
                        lateness,
                        event_type,
                        json.dumps(
                            {
                                "original_disposition": existing["disposition"],
                                "original_claimed_at": existing["claimed_at"],
                                "http_allowed": False,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
                connection.commit()
                return SlotClaimResult(
                    accepted=False,
                    event_type=event_type,
                    claim_id=str(existing["claim_id"]),
                    invocation_id=invocation_id,
                    run_id=None,
                    slot_id=slot_id,
                    slot_at=slot_at,
                    claimed_at=claimed_at_text,
                    lateness_seconds=lateness,
                )

            accepted = lateness <= config.trading.runtime.slot_lateness_seconds
            disposition = "CLAIMED" if accepted else "SKIPPED_LATE"
            claim_id = uuid4().hex
            owner_run_id = run_id if accepted else None
            connection.execute(
                """
                INSERT INTO cycle_slot_claims(
                    claim_id, slot_id, job_name, config_hash,
                    strategy_source_digest, invocation_id, owner_run_id,
                    slot_at, claimed_at, lateness_seconds, disposition
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim_id,
                    slot_id,
                    config.job_name,
                    config.config_hash,
                    config.trading.strategy_source_digest,
                    invocation_id,
                    owner_run_id,
                    slot_at,
                    claimed_at_text,
                    lateness,
                    disposition,
                ),
            )
            connection.execute(
                """
                INSERT INTO cycle_slot_events(
                    event_id, invocation_id, claim_id, slot_id, job_name,
                    slot_at, observed_at, lateness_seconds, event_type,
                    details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    invocation_id,
                    claim_id,
                    slot_id,
                    config.job_name,
                    slot_at,
                    claimed_at_text,
                    lateness,
                    disposition,
                    json.dumps(
                        {
                            "http_allowed": accepted,
                            "slot_lateness_seconds": config.trading.runtime.slot_lateness_seconds,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            connection.commit()
        return SlotClaimResult(
            accepted=accepted,
            event_type=disposition,
            claim_id=claim_id,
            invocation_id=invocation_id,
            run_id=owner_run_id,
            slot_id=slot_id,
            slot_at=slot_at,
            claimed_at=claimed_at_text,
            lateness_seconds=lateness,
        )

    def _insert_one(self, table: str, row: Mapping[str, Any]) -> None:
        columns = tuple(row)
        placeholders = ",".join("?" for _ in columns)
        sql = f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})"
        with self._connect() as connection:
            connection.execute(sql, tuple(row[column] for column in columns))
            connection.commit()

    @staticmethod
    def _insert_many(
        connection: sqlite3.Connection, table: str, rows: Iterable[Mapping[str, Any]]
    ) -> None:
        materialized = list(rows)
        if not materialized:
            return
        columns = tuple(materialized[0])
        if any(tuple(row) != columns for row in materialized):
            raise ValueError(f"inconsistent columns for {table}")
        placeholders = ",".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})",
            [tuple(row[column] for column in columns) for row in materialized],
        )

    def next_cycle_number(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(cycle_number), 0) + 1 AS value FROM market_sweeps"
            ).fetchone()
            return int(row["value"])

    def recent_candidate_snapshots(
        self,
        *,
        condition_id: str,
        token_id: str,
        config_hash: str,
        strategy_source_digest: str,
        before: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT snapshot_id, observed_at, pair_score
                FROM orderbook_snapshots
                WHERE condition_id=? AND token_id=? AND config_hash=?
                  AND strategy_source_digest=? AND observed_at < ?
                  AND quote_eligible=1 AND candidate_up=1
                ORDER BY observed_at DESC
                LIMIT ?
                """,
                (
                    condition_id,
                    token_id,
                    config_hash,
                    strategy_source_digest,
                    before,
                    limit,
                ),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def last_qualified_at(
        self, *, event_id: str, arm: str, config_hash: str
    ) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT evaluated_at FROM signal_decisions
                WHERE event_id=? AND arm=? AND config_hash=? AND qualified=1
                ORDER BY evaluated_at DESC LIMIT 1
                """,
                (event_id, arm, config_hash),
            ).fetchone()
            return str(row["evaluated_at"]) if row else None

    def latest_quote_snapshot(
        self,
        *,
        condition_id: str,
        token_id: str,
        config_hash: str,
        strategy_source_digest: str,
        after: str,
        before: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_id, observed_at, best_ask
                FROM orderbook_snapshots
                WHERE condition_id=? AND token_id=? AND config_hash=?
                  AND strategy_source_digest=? AND observed_at>=? AND observed_at<?
                  AND quote_eligible=1 AND best_ask IS NOT NULL
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (
                    condition_id,
                    token_id,
                    config_hash,
                    strategy_source_digest,
                    after,
                    before,
                ),
            ).fetchone()
            return dict(row) if row else None

    def claim_due_followups(
        self,
        *,
        run_id: str,
        now: datetime,
        stale_after_seconds: float,
    ) -> FollowupClaimBatch:
        """Atomically claim due cases and recover only provably unstarted stale claims."""

        now_dt = now.astimezone(timezone.utc)
        now_text = iso_utc(now_dt)
        lease_expires = iso_utc(
            now_dt + timedelta(seconds=float(stale_after_seconds))
        )
        due: list[dict[str, Any]] = []
        expired_terminalized = 0
        stale_terminalized = 0
        active_claims_skipped = 0
        recovered_claims = 0
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cases = connection.execute(
                    """
                    SELECT c.* FROM research_cases c
                    WHERE c.target_at <= ?
                      AND NOT EXISTS (
                        SELECT 1 FROM followup_attempts f WHERE f.case_id=c.case_id
                      )
                    ORDER BY c.target_at, c.case_id
                    """,
                    (now_text,),
                ).fetchall()
                for raw_case in cases:
                    case = dict(raw_case)
                    claim = connection.execute(
                        "SELECT * FROM followup_claims WHERE case_id=?",
                        (case["case_id"],),
                    ).fetchone()
                    if claim is None:
                        claim_id = uuid4().hex
                        connection.execute(
                            """
                            INSERT INTO followup_claims(
                                claim_id, case_id, first_claimed_by_run_id,
                                claimed_at, target_at, window_end
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                claim_id,
                                case["case_id"],
                                run_id,
                                now_text,
                                case["target_at"],
                                case["window_end"],
                            ),
                        )
                        generation = 1
                        lease_id = uuid4().hex
                        connection.execute(
                            """
                            INSERT INTO followup_claim_leases(
                                lease_id, claim_id, generation, owner_run_id,
                                claimed_at, lease_expires_at, recovery_reason
                            ) VALUES (?, ?, ?, ?, ?, ?, 'INITIAL')
                            """,
                            (
                                lease_id,
                                claim_id,
                                generation,
                                run_id,
                                now_text,
                                lease_expires,
                            ),
                        )
                    else:
                        claim_id = str(claim["claim_id"])
                        lease = connection.execute(
                            """
                            SELECT * FROM followup_claim_leases
                            WHERE claim_id=? ORDER BY generation DESC LIMIT 1
                            """,
                            (claim_id,),
                        ).fetchone()
                        if lease is None:
                            raise RuntimeError("follow-up claim is missing its initial lease")
                        request_start = connection.execute(
                            "SELECT * FROM followup_request_starts WHERE claim_id=?",
                            (claim_id,),
                        ).fetchone()
                        lease_expired = _parse_utc(str(lease["lease_expires_at"])) <= now_dt
                        if request_start is not None:
                            if not lease_expired:
                                active_claims_skipped += 1
                                continue
                            self._insert_many(
                                connection,
                                "followup_attempts",
                                [
                                    {
                                        "followup_id": uuid4().hex,
                                        "case_id": case["case_id"],
                                        "claim_id": claim_id,
                                        "observing_run_id": run_id,
                                        "attempted_at": request_start[
                                            "request_started_at"
                                        ],
                                        "status": "STALE_REQUEST_UNKNOWN",
                                        "source_snapshot_id": None,
                                        "observed_at": None,
                                        "exit_bid": None,
                                        "exit_vwap": None,
                                        "exit_proceeds_usdc": None,
                                        "executable_return_bps": None,
                                        "base_stressed_return_bps": None,
                                        "severe_stressed_return_bps": None,
                                        "details_json": json.dumps(
                                            {
                                                "reason": "durable_request_start_without_terminal_evidence",
                                                "logical_request_id": request_start[
                                                    "logical_request_id"
                                                ],
                                                "original_observing_run_id": request_start[
                                                    "observing_run_id"
                                                ],
                                                "replacement_http_allowed": False,
                                            },
                                            sort_keys=True,
                                            separators=(",", ":"),
                                        ),
                                    }
                                ],
                            )
                            stale_terminalized += 1
                            continue
                        if not lease_expired:
                            active_claims_skipped += 1
                            continue
                        generation = int(lease["generation"]) + 1
                        lease_id = uuid4().hex
                        connection.execute(
                            """
                            INSERT INTO followup_claim_leases(
                                lease_id, claim_id, generation, owner_run_id,
                                claimed_at, lease_expires_at, recovery_reason
                            ) VALUES (?, ?, ?, ?, ?, ?, 'STALE_UNSTARTED_RECOVERY')
                            """,
                            (
                                lease_id,
                                claim_id,
                                generation,
                                run_id,
                                now_text,
                                lease_expires,
                            ),
                        )
                        recovered_claims += 1

                    if _parse_utc(str(case["window_end"])) < now_dt:
                        self._insert_many(
                            connection,
                            "followup_attempts",
                            [
                                {
                                    "followup_id": uuid4().hex,
                                    "case_id": case["case_id"],
                                    "claim_id": claim_id,
                                    "observing_run_id": run_id,
                                    "attempted_at": now_text,
                                    "status": "WINDOW_EXPIRED",
                                    "source_snapshot_id": None,
                                    "observed_at": None,
                                    "exit_bid": None,
                                    "exit_vwap": None,
                                    "exit_proceeds_usdc": None,
                                    "executable_return_bps": None,
                                    "base_stressed_return_bps": None,
                                    "severe_stressed_return_bps": None,
                                    "details_json": json.dumps(
                                        {
                                            "reason": "no_request_started_before_window_end",
                                            "replacement_http_allowed": False,
                                        },
                                        sort_keys=True,
                                        separators=(",", ":"),
                                    ),
                                }
                            ],
                        )
                        expired_terminalized += 1
                        continue
                    due.append(
                        {
                            **case,
                            "claim_id": claim_id,
                            "lease_id": lease_id,
                            "lease_generation": generation,
                        }
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return FollowupClaimBatch(
            due=due,
            expired_terminalized=expired_terminalized,
            stale_terminalized=stale_terminalized,
            active_claims_skipped=active_claims_skipped,
            recovered_claims=recovered_claims,
        )

    def mark_followup_requests_started(
        self,
        claims: Iterable[Mapping[str, Any]],
        *,
        token_ids: Iterable[str],
        run_id: str,
        logical_request_id: str,
        request_started_at: str,
    ) -> int:
        tokens = set(token_ids)
        selected = [row for row in claims if str(row["token_id"]) in tokens]
        inserted = 0
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for claim in selected:
                    terminal = connection.execute(
                        "SELECT 1 FROM followup_attempts WHERE claim_id=?",
                        (claim["claim_id"],),
                    ).fetchone()
                    if terminal is not None:
                        continue
                    existing = connection.execute(
                        "SELECT * FROM followup_request_starts WHERE claim_id=?",
                        (claim["claim_id"],),
                    ).fetchone()
                    if existing is not None:
                        continue
                    latest = connection.execute(
                        """
                        SELECT * FROM followup_claim_leases
                        WHERE claim_id=? ORDER BY generation DESC LIMIT 1
                        """,
                        (claim["claim_id"],),
                    ).fetchone()
                    if (
                        latest is None
                        or str(latest["lease_id"]) != str(claim["lease_id"])
                        or str(latest["owner_run_id"]) != run_id
                    ):
                        raise RuntimeError("follow-up request does not own the latest lease")
                    connection.execute(
                        """
                        INSERT INTO followup_request_starts(
                            claim_id, lease_id, observing_run_id,
                            logical_request_id, request_started_at, token_id
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            claim["claim_id"],
                            claim["lease_id"],
                            run_id,
                            logical_request_id,
                            request_started_at,
                            claim["token_id"],
                        ),
                    )
                    inserted += 1
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return inserted

    def publish_followup_evidence(self, bundle: Mapping[str, Any]) -> None:
        tables = (
            "raw_payloads",
            "orderbook_token_attempts",
            "orderbook_snapshots",
            "orderbook_levels",
            "followup_attempts",
        )
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for row in bundle.get("followup_attempts", []):
                    started = connection.execute(
                        "SELECT 1 FROM followup_request_starts WHERE claim_id=?",
                        (row["claim_id"],),
                    ).fetchone()
                    if started is None:
                        raise RuntimeError(
                            "follow-up terminal evidence requires a durable request start"
                        )
                for table in tables:
                    self._insert_many(connection, table, bundle.get(table, []))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    # Legacy-v2 verifier marker only; v3 never uses this non-claim selection:
    # terminal = "SELECT 1 FROM followup_attempts f WHERE f.case_id=c.case_id"

    def publish_cycle(self, bundle: Mapping[str, Any]) -> None:
        tables = (
            "market_sweeps",
            "market_observations",
            "raw_payloads",
            "orderbook_token_attempts",
            "orderbook_snapshots",
            "orderbook_levels",
            "signal_decisions",
            "research_cases",
            "data_quality_issues",
            "cycle_stats",
        )
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for table in tables:
                    self._insert_many(connection, table, bundle.get(table, []))
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
        ancestor = self.db_path.parent
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        usage = shutil.disk_usage(ancestor)
        db_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        wal_path = Path(str(self.db_path) + "-wal")
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        used_ratio = usage.used / usage.total if usage.total else 1.0
        if usage.free < storage.min_free_gib * GIB or used_ratio >= storage.stop_used_ratio:
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
            "db_bytes": db_bytes,
            "wal_bytes": wal_bytes,
            "filesystem_total_bytes": usage.total,
            "filesystem_used_bytes": usage.used,
            "filesystem_free_bytes": usage.free,
            "filesystem_used_ratio": used_ratio,
            "guard_state": state,
        }
        self._insert_one("storage_metrics", row)
        return row

    def status(self) -> dict[str, Any]:
        if not self.db_path.exists():
            return {"healthy": False, "reason": "database_missing", "db_path": str(self.db_path)}
        with self._connect(create=False) as connection:
            tables = (
                "market_sweeps",
                "market_observations",
                "orderbook_snapshots",
                "signal_decisions",
                "research_cases",
                "followup_attempts",
                "data_quality_issues",
            )
            counts = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in tables
            }
            latest = connection.execute(
                """
                SELECT run_id, event_type, event_at, details_json
                FROM research_run_events ORDER BY event_at DESC LIMIT 1
                """
            ).fetchone()
            latest_sweep = connection.execute(
                """
                SELECT cycle_number, completed_at, page_count, source_envelope_count,
                       eligible_market_count
                FROM market_sweeps ORDER BY cycle_number DESC LIMIT 1
                """
            ).fetchone()
            config_count = int(
                connection.execute("SELECT COUNT(*) FROM research_config_versions").fetchone()[0]
            )
        return {
            "healthy": bool(latest and latest["event_type"] == "SUCCEEDED"),
            "db_path": str(self.db_path),
            "db_bytes": self.db_path.stat().st_size,
            "wal_bytes": Path(str(self.db_path) + "-wal").stat().st_size
            if Path(str(self.db_path) + "-wal").exists()
            else 0,
            "config_version_count": config_count,
            "counts": counts,
            "latest_run": dict(latest) if latest else None,
            "latest_sweep": dict(latest_sweep) if latest_sweep else None,
        }

    def health(self, *, cadence_minutes: int) -> dict[str, Any]:
        status = self.status()
        if not self.db_path.exists():
            return status
        with self._connect(create=False) as connection:
            integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            latest_success = connection.execute(
                """
                SELECT event_at FROM research_run_events
                WHERE event_type='SUCCEEDED' ORDER BY event_at DESC LIMIT 1
                """
            ).fetchone()
        age_minutes = None
        if latest_success:
            parsed = datetime.fromisoformat(str(latest_success["event_at"]).replace("Z", "+00:00"))
            age_minutes = (datetime.now(timezone.utc) - parsed).total_seconds() / 60
        status.update(
            {
                "quick_check": integrity,
                "latest_success_age_minutes": age_minutes,
                "cadence_fresh": age_minutes is not None and age_minutes <= cadence_minutes * 2.5,
            }
        )
        status["healthy"] = bool(
            status.get("healthy") and integrity == "ok" and status["cadence_fresh"]
        )
        return status


__all__ = ["GIB", "ResearchRepository", "SCHEMA_VERSION"]
