"""Compact append-only SQLite store for Last Mile follow-up v2."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import quote

from ..config import StorageConfig
from ..followup_config import (
    FOLLOWUP_CANONICAL_JOB,
    FOLLOWUP_DATA_CONTRACT,
    FollowupConfig,
)
from ..utils.retry import canonical_json, iso_utc
from ..v1_source import V1SeedSnapshot, compare_anchor


GIB = 1024**3
FOLLOWUP_SCHEMA_VERSION = 2


def _chunks(values: Sequence[str], size: int = 400) -> Iterator[Sequence[str]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


SCHEMA = r"""
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS followup_contracts (
    job_name TEXT PRIMARY KEY CHECK (job_name = 'strawberry-shadow-one-followup-v2'),
    strategy_name TEXT NOT NULL CHECK (strategy_name = 'golden-strawberry'),
    data_contract TEXT NOT NULL CHECK (data_contract = 'last-mile-clob-followup-v2'),
    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode = 'archive_only'),
    cadence_minutes INTEGER NOT NULL CHECK (cadence_minutes = 10),
    cadence_offset_minute INTEGER NOT NULL CHECK (cadence_offset_minute = 7),
    entry_start TEXT NOT NULL,
    entry_end TEXT NOT NULL,
    followup_end TEXT NOT NULL,
    preregistration_sha256 TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_config_versions (
    config_hash TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL CHECK (strategy_name = 'golden-strawberry'),
    job_name TEXT NOT NULL CHECK (job_name = 'strawberry-shadow-one-followup-v2'),
    mode TEXT NOT NULL CHECK (mode = 'sim'),
    lifecycle_mode TEXT NOT NULL CHECK (lifecycle_mode = 'archive_only'),
    data_contract TEXT NOT NULL CHECK (data_contract = 'last-mile-clob-followup-v2'),
    strategy_source_digest TEXT NOT NULL,
    preregistration_sha256 TEXT NOT NULL,
    config_json TEXT NOT NULL,
    git_commit TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_anchors (
    anchor_id TEXT PRIMARY KEY CHECK (anchor_id = 'v1-seed'),
    source_path TEXT NOT NULL,
    source_file_fingerprint_sha256 TEXT NOT NULL,
    source_db_size_bytes INTEGER NOT NULL,
    source_db_mtime_ns INTEGER NOT NULL,
    source_schema_version INTEGER NOT NULL CHECK (source_schema_version = 1),
    source_schema_sha256 TEXT NOT NULL,
    source_data_contract TEXT NOT NULL CHECK (source_data_contract = 'last-mile-clob-v1'),
    source_job_name TEXT NOT NULL CHECK (source_job_name = 'strawberry-shadow-one'),
    source_entry_start TEXT NOT NULL,
    source_entry_end TEXT NOT NULL,
    source_followup_end TEXT NOT NULL,
    source_sweep_id TEXT NOT NULL,
    source_cycle_number INTEGER NOT NULL,
    source_sweep_completed_at TEXT NOT NULL,
    source_successful_at TEXT NOT NULL,
    source_config_hash TEXT NOT NULL,
    source_strategy_digest TEXT NOT NULL,
    source_counts_json TEXT NOT NULL,
    episode_seed_sha256 TEXT NOT NULL,
    condition_seed_sha256 TEXT NOT NULL,
    threshold_seed_sha256 TEXT NOT NULL,
    executable_episode_count INTEGER NOT NULL,
    condition_count INTEGER NOT NULL,
    terminal_condition_count INTEGER NOT NULL,
    threshold_event_count INTEGER NOT NULL,
    anchor_sha256 TEXT NOT NULL UNIQUE,
    captured_at TEXT NOT NULL,
    seeded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imported_episodes (
    episode_id TEXT PRIMARY KEY,
    anchor_id TEXT NOT NULL REFERENCES source_anchors(anchor_id),
    source_decision_id TEXT NOT NULL,
    source_originating_sweep_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    market_id TEXT,
    event_id TEXT,
    event_cluster_id TEXT,
    token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    outcome_label TEXT NOT NULL,
    outcome_type TEXT NOT NULL,
    neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),
    sports_classification TEXT NOT NULL,
    entry_threshold REAL NOT NULL,
    entry_observed_at TEXT NOT NULL,
    entry_notional_usdc REAL NOT NULL CHECK (entry_notional_usdc = 5),
    entry_ask_vwap REAL NOT NULL,
    fixed_shares REAL NOT NULL CHECK (fixed_shares > 0),
    source_last_path_observation_id TEXT,
    source_last_path_observed_at TEXT,
    source_last_executable_bid_vwap REAL,
    episode_json TEXT NOT NULL,
    seed_row_sha256 TEXT NOT NULL UNIQUE,
    UNIQUE (token_id,entry_threshold)
);
CREATE INDEX IF NOT EXISTS imported_episode_condition_idx
    ON imported_episodes(condition_id,episode_id);
CREATE INDEX IF NOT EXISTS imported_episode_token_idx
    ON imported_episodes(token_id,episode_id);

CREATE TABLE IF NOT EXISTS imported_condition_status (
    condition_id TEXT PRIMARY KEY,
    anchor_id TEXT NOT NULL REFERENCES source_anchors(anchor_id),
    terminal_at_handoff INTEGER NOT NULL CHECK (terminal_at_handoff IN (0,1)),
    source_resolution_observation_id TEXT,
    source_sweep_id TEXT,
    source_run_id TEXT,
    observed_at TEXT,
    winning_outcome_index INTEGER,
    winning_outcome_label TEXT,
    winning_token_id TEXT,
    token_payouts_json TEXT NOT NULL,
    raw_market_sha256 TEXT,
    seed_row_sha256 TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS imported_threshold_events (
    source_threshold_event_id TEXT PRIMARY KEY,
    anchor_id TEXT NOT NULL REFERENCES source_anchors(anchor_id),
    episode_id TEXT NOT NULL REFERENCES imported_episodes(episode_id),
    event_kind TEXT NOT NULL CHECK (event_kind IN ('STOP','TARGET')),
    threshold REAL NOT NULL,
    observed_at TEXT NOT NULL,
    executable_bid_vwap REAL NOT NULL,
    prior_executable_bid_vwap REAL,
    seed_row_sha256 TEXT NOT NULL UNIQUE,
    UNIQUE (episode_id,event_kind,threshold)
);

CREATE TABLE IF NOT EXISTS research_run_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    config_hash TEXT NOT NULL REFERENCES research_config_versions(config_hash),
    strategy_name TEXT NOT NULL CHECK (strategy_name = 'golden-strawberry'),
    job_name TEXT NOT NULL CHECK (job_name = 'strawberry-shadow-one-followup-v2'),
    mode TEXT NOT NULL CHECK (mode = 'sim'),
    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED','SUCCEEDED','FAILED')),
    event_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS followup_run_events_idx
    ON research_run_events(run_id,event_at);

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
CREATE INDEX IF NOT EXISTS followup_api_run_idx
    ON api_requests(run_id,request_kind,page_number);

CREATE TABLE IF NOT EXISTS followup_cycles (
    cycle_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    cycle_number INTEGER NOT NULL UNIQUE,
    config_hash TEXT NOT NULL REFERENCES research_config_versions(config_hash),
    strategy_source_digest TEXT NOT NULL,
    anchor_id TEXT NOT NULL REFERENCES source_anchors(anchor_id),
    anchor_sha256 TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    published_at TEXT NOT NULL,
    unresolved_episode_count INTEGER NOT NULL,
    distinct_token_count INTEGER NOT NULL,
    distinct_condition_count INTEGER NOT NULL,
    book_observed_count INTEGER NOT NULL,
    path_observation_count INTEGER NOT NULL,
    resolution_observation_count INTEGER NOT NULL,
    newly_resolved_condition_count INTEGER NOT NULL,
    prepublication_seconds REAL NOT NULL,
    summary_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS followup_cycles_time_idx
    ON followup_cycles(completed_at);

CREATE TABLE IF NOT EXISTS book_token_attempts (
    attempt_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),
    run_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('OBSERVED','MISSING','EMPTY_BOOK','MALFORMED','ERROR')),
    request_id TEXT REFERENCES api_requests(request_id),
    request_started_at TEXT,
    received_at TEXT,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (cycle_id,token_id)
);

CREATE TABLE IF NOT EXISTS compact_books (
    book_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),
    run_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    request_id TEXT NOT NULL REFERENCES api_requests(request_id),
    source_received_at TEXT NOT NULL,
    source_response_sha256 TEXT NOT NULL,
    encoding TEXT NOT NULL CHECK (encoding = 'gzip-json-v1'),
    book_sha256 TEXT NOT NULL,
    uncompressed_bytes INTEGER NOT NULL,
    compressed_bytes INTEGER NOT NULL,
    book_blob BLOB NOT NULL,
    bid_level_count INTEGER NOT NULL,
    ask_level_count INTEGER NOT NULL,
    best_bid REAL,
    best_ask REAL,
    bid_depth_notional REAL NOT NULL,
    ask_depth_notional REAL NOT NULL,
    source_timestamp TEXT,
    tick_size REAL,
    min_order_size REAL,
    fee_rate_bps REAL,
    UNIQUE (cycle_id,token_id)
);
CREATE INDEX IF NOT EXISTS compact_books_token_idx
    ON compact_books(token_id,source_received_at);

CREATE TABLE IF NOT EXISTS episode_path_observations (
    path_observation_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),
    run_id TEXT NOT NULL,
    episode_id TEXT NOT NULL REFERENCES imported_episodes(episode_id),
    book_id TEXT REFERENCES compact_books(book_id),
    observed_at TEXT NOT NULL,
    path_status TEXT NOT NULL,
    censor_reason TEXT,
    fixed_shares REAL NOT NULL,
    best_bid REAL,
    exit_bid_vwap REAL,
    exit_proceeds_usdc REAL,
    covered_shares REAL,
    bid_depth_notional REAL,
    prior_executable_bid_vwap REAL,
    interval_censored INTEGER NOT NULL CHECK (interval_censored = 1),
    details_json TEXT NOT NULL,
    UNIQUE (cycle_id,episode_id)
);
CREATE INDEX IF NOT EXISTS followup_path_episode_idx
    ON episode_path_observations(episode_id,observed_at);

CREATE TABLE IF NOT EXISTS episode_threshold_events (
    threshold_event_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),
    episode_id TEXT NOT NULL REFERENCES imported_episodes(episode_id),
    path_observation_id TEXT NOT NULL REFERENCES episode_path_observations(path_observation_id),
    event_kind TEXT NOT NULL CHECK (event_kind IN ('STOP','TARGET')),
    threshold REAL NOT NULL,
    observed_at TEXT NOT NULL,
    executable_bid_vwap REAL NOT NULL,
    prior_executable_bid_vwap REAL,
    interval_censored INTEGER NOT NULL CHECK (interval_censored = 1),
    conservative_priority INTEGER NOT NULL CHECK (conservative_priority IN (0,1)),
    UNIQUE (episode_id,event_kind,threshold)
);

CREATE TABLE IF NOT EXISTS resolution_observations (
    resolution_observation_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    lookup_status TEXT NOT NULL,
    resolution_status TEXT NOT NULL,
    request_id TEXT REFERENCES api_requests(request_id),
    raw_market_sha256 TEXT,
    encoding TEXT,
    uncompressed_bytes INTEGER,
    compressed_bytes INTEGER,
    market_blob BLOB,
    winning_outcome_index INTEGER,
    winning_outcome_label TEXT,
    winning_token_id TEXT,
    token_payouts_json TEXT NOT NULL,
    resolution_jump_without_target_json TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (cycle_id,condition_id)
);
CREATE INDEX IF NOT EXISTS followup_resolution_condition_idx
    ON resolution_observations(condition_id,observed_at);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    issue_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),
    run_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('INFO','WARN','HIGH','CRITICAL')),
    issue_code TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phase_timings (
    phase_timing_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES followup_cycles(cycle_id),
    run_id TEXT NOT NULL,
    phase_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    elapsed_seconds REAL NOT NULL CHECK (elapsed_seconds >= 0),
    details_json TEXT NOT NULL,
    UNIQUE (cycle_id,phase_name)
);

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
"""


APPEND_ONLY_TABLES = frozenset(
    {
        "followup_contracts",
        "research_config_versions",
        "source_anchors",
        "imported_episodes",
        "imported_condition_status",
        "imported_threshold_events",
        "research_run_events",
        "api_requests",
        "followup_cycles",
        "book_token_attempts",
        "compact_books",
        "episode_path_observations",
        "episode_threshold_events",
        "resolution_observations",
        "data_quality_issues",
        "phase_timings",
        "storage_metrics",
    }
)


def _append_only_triggers() -> str:
    statements: list[str] = []
    for table in sorted(APPEND_ONLY_TABLES):
        statements.extend(
            (
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_update "
                f"BEFORE UPDATE ON {table} BEGIN "
                "SELECT RAISE(ABORT,'append-only follow-up evidence'); END;",
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete "
                f"BEFORE DELETE ON {table} BEGIN "
                "SELECT RAISE(ABORT,'append-only follow-up evidence'); END;",
            )
        )
    return "\n".join(statements)


class FollowupRepository:
    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 30_000) -> None:
        self.db_path = Path(db_path)
        self.busy_timeout_ms = busy_timeout_ms

    @contextmanager
    def _connect(self, *, create: bool = True) -> Iterator[sqlite3.Connection]:
        if create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.db_path, timeout=self.busy_timeout_ms / 1000
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
    def read_connect(self, *, immutable: bool = False) -> Iterator[sqlite3.Connection]:
        if not self.db_path.is_file():
            raise FileNotFoundError(self.db_path)
        suffix = "&immutable=1" if immutable else ""
        uri = f"file:{quote(str(self.db_path.resolve()))}?mode=ro{suffix}"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        try:
            yield connection
        finally:
            connection.close()

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
            raise ValueError(f"{table} rows do not share one canonical column order")
        placeholders = ",".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})",
            [tuple(row[column] for column in columns) for row in materialized],
        )

    def _insert_one(self, table: str, row: Mapping[str, Any]) -> None:
        if table not in APPEND_ONLY_TABLES:
            raise ValueError("table is not approved append-only follow-up evidence")
        with self._connect() as connection:
            self._insert_many(connection, table, [row])
            connection.commit()

    def initialize(self, config: FollowupConfig) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            connection.executescript(_append_only_triggers())
            metadata = dict(connection.execute("SELECT key,value FROM schema_metadata"))
            expected = {
                "schema_version": str(FOLLOWUP_SCHEMA_VERSION),
                "data_contract": FOLLOWUP_DATA_CONTRACT,
                "book_storage": "canonical-gzip-one-row-per-token-cycle",
                "v1_source_access": "mode=ro",
            }
            if not metadata:
                connection.executemany(
                    "INSERT INTO schema_metadata(key,value) VALUES(?,?)",
                    sorted(expected.items()),
                )
            elif metadata != expected:
                raise RuntimeError("follow-up DB schema metadata changed")
            contract = {
                "job_name": FOLLOWUP_CANONICAL_JOB,
                "strategy_name": "golden-strawberry",
                "data_contract": FOLLOWUP_DATA_CONTRACT,
                "lifecycle_mode": "archive_only",
                "cadence_minutes": config.trading.cadence_minutes,
                "cadence_offset_minute": config.trading.cadence_offset_minute,
                "entry_start": iso_utc(config.trading.experiment.entry_start_utc),
                "entry_end": iso_utc(config.trading.experiment.entry_end_utc),
                "followup_end": iso_utc(config.trading.experiment.followup_end_utc),
                "preregistration_sha256": (
                    config.trading.experiment.preregistration_sha256
                ),
                "contract_json": canonical_json(config.redacted_dict()),
                "created_at": iso_utc(),
            }
            existing = connection.execute(
                "SELECT * FROM followup_contracts WHERE job_name=?",
                (FOLLOWUP_CANONICAL_JOB,),
            ).fetchone()
            if existing is None:
                self._insert_many(connection, "followup_contracts", [contract])
            else:
                fixed = (
                    "data_contract",
                    "lifecycle_mode",
                    "cadence_minutes",
                    "cadence_offset_minute",
                    "entry_start",
                    "entry_end",
                    "followup_end",
                    "preregistration_sha256",
                )
                if any(str(existing[key]) != str(contract[key]) for key in fixed):
                    raise RuntimeError("follow-up experiment contract drift")
            connection.commit()

    def register_config(
        self, config: FollowupConfig, *, git_commit: str | None
    ) -> None:
        payload = canonical_json(config.redacted_dict())
        row = {
            "config_hash": config.config_hash,
            "strategy_name": "golden-strawberry",
            "job_name": config.job_name,
            "mode": "sim",
            "lifecycle_mode": config.trading.lifecycle_mode,
            "data_contract": config.trading.data_contract,
            "strategy_source_digest": config.trading.strategy_source_digest,
            "preregistration_sha256": (
                config.trading.experiment.preregistration_sha256
            ),
            "config_json": payload,
            "git_commit": git_commit,
            "created_at": iso_utc(),
        }
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT config_json FROM research_config_versions WHERE config_hash=?",
                (config.config_hash,),
            ).fetchone()
            if existing is None:
                self._insert_many(connection, "research_config_versions", [row])
            elif str(existing["config_json"]) != payload:
                raise RuntimeError("follow-up config hash collision")
            connection.commit()

    @staticmethod
    def _anchor_row(snapshot: V1SeedSnapshot) -> dict[str, Any]:
        return {
            "anchor_id": "v1-seed",
            **{key: snapshot.anchor[key] for key in snapshot.anchor},
            "seeded_at": iso_utc(),
        }

    @staticmethod
    def _episode_row(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "episode_id": row["episode_id"],
            "anchor_id": "v1-seed",
            "source_decision_id": row["decision_id"],
            "source_originating_sweep_id": row["originating_sweep_id"],
            "source_run_id": row["run_id"],
            "condition_id": row["condition_id"],
            "market_id": row["market_id"],
            "event_id": row["event_id"],
            "event_cluster_id": row["event_cluster_id"],
            "token_id": row["token_id"],
            "outcome_index": row["outcome_index"],
            "outcome_label": row["outcome_label"],
            "outcome_type": row["outcome_type"],
            "neg_risk": row["neg_risk"],
            "sports_classification": row["sports_classification"],
            "entry_threshold": row["entry_threshold"],
            "entry_observed_at": row["entry_observed_at"],
            "entry_notional_usdc": row["entry_notional_usdc"],
            "entry_ask_vwap": row["entry_ask_vwap"],
            "fixed_shares": row["fixed_shares"],
            "source_last_path_observation_id": row[
                "source_last_path_observation_id"
            ],
            "source_last_path_observed_at": row["source_last_path_observed_at"],
            "source_last_executable_bid_vwap": row[
                "source_last_executable_bid_vwap"
            ],
            "episode_json": canonical_json(dict(row)),
            "seed_row_sha256": row["seed_row_sha256"],
        }

    @staticmethod
    def _condition_row(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "condition_id": row["condition_id"],
            "anchor_id": "v1-seed",
            "terminal_at_handoff": row["terminal_at_handoff"],
            "source_resolution_observation_id": row[
                "source_resolution_observation_id"
            ],
            "source_sweep_id": row["source_sweep_id"],
            "source_run_id": row["source_run_id"],
            "observed_at": row["observed_at"],
            "winning_outcome_index": row["winning_outcome_index"],
            "winning_outcome_label": row["winning_outcome_label"],
            "winning_token_id": row["winning_token_id"],
            "token_payouts_json": row["token_payouts_json"],
            "raw_market_sha256": row["raw_market_sha256"],
            "seed_row_sha256": row["seed_row_sha256"],
        }

    @staticmethod
    def _threshold_row(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "source_threshold_event_id": row["threshold_event_id"],
            "anchor_id": "v1-seed",
            "episode_id": row["episode_id"],
            "event_kind": row["event_kind"],
            "threshold": row["threshold"],
            "observed_at": row["observed_at"],
            "executable_bid_vwap": row["executable_bid_vwap"],
            "prior_executable_bid_vwap": row["prior_executable_bid_vwap"],
            "seed_row_sha256": row["seed_row_sha256"],
        }

    def ensure_seed(self, snapshot: V1SeedSnapshot) -> Mapping[str, Any]:
        with self._connect() as connection:
            stored = connection.execute(
                "SELECT * FROM source_anchors WHERE anchor_id='v1-seed'"
            ).fetchone()
            if stored is None:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    self._insert_many(
                        connection, "source_anchors", [self._anchor_row(snapshot)]
                    )
                    self._insert_many(
                        connection,
                        "imported_episodes",
                        [self._episode_row(row) for row in snapshot.episodes],
                    )
                    self._insert_many(
                        connection,
                        "imported_condition_status",
                        [
                            self._condition_row(row)
                            for row in snapshot.condition_statuses
                        ],
                    )
                    self._insert_many(
                        connection,
                        "imported_threshold_events",
                        [
                            self._threshold_row(row)
                            for row in snapshot.threshold_events
                        ],
                    )
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                persisted = connection.execute(
                    "SELECT * FROM source_anchors WHERE anchor_id='v1-seed'"
                ).fetchone()
                if persisted is None:  # pragma: no cover - defensive SQLite guard
                    raise RuntimeError("follow-up source anchor was not persisted")
                return dict(persisted)
            stored_dict = dict(stored)
            compare_anchor(stored_dict, snapshot.anchor)
            counts = {
                "episodes": int(
                    connection.execute("SELECT COUNT(*) FROM imported_episodes").fetchone()[
                        0
                    ]
                ),
                "conditions": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM imported_condition_status"
                    ).fetchone()[0]
                ),
                "thresholds": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM imported_threshold_events"
                    ).fetchone()[0]
                ),
            }
            expected = {
                "episodes": int(stored["executable_episode_count"]),
                "conditions": int(stored["condition_count"]),
                "thresholds": int(stored["threshold_event_count"]),
            }
            if counts != expected:
                raise RuntimeError("follow-up seed table count drift")
            return stored_dict

    def stored_anchor(self) -> Mapping[str, Any] | None:
        if not self.db_path.is_file():
            return None
        with self.read_connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_anchors WHERE anchor_id='v1-seed'"
            ).fetchone()
            return dict(row) if row is not None else None

    def record_research_run_event(self, row: Mapping[str, Any]) -> None:
        self._insert_one("research_run_events", row)

    def record_api_request(self, row: Mapping[str, Any]) -> None:
        # A dedicated FULL commit per attempt preserves receipts for failed runs.
        self._insert_one("api_requests", row)

    def next_cycle_number(self) -> int:
        if not self.db_path.is_file():
            return 1
        with self.read_connect() as connection:
            return int(
                connection.execute(
                    "SELECT COALESCE(MAX(cycle_number),0)+1 FROM followup_cycles"
                ).fetchone()[0]
            )

    def unresolved_episodes(self) -> list[dict[str, Any]]:
        with self.read_connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT e.* FROM imported_episodes e
                    JOIN imported_condition_status s ON s.condition_id=e.condition_id
                    WHERE s.terminal_at_handoff=0
                      AND NOT EXISTS (
                        SELECT 1 FROM resolution_observations r
                        WHERE r.condition_id=e.condition_id
                          AND r.resolution_status='RESOLVED'
                      )
                    ORDER BY e.condition_id,e.token_id,e.entry_threshold,e.episode_id
                    """
                )
            ]

    def latest_path_vwaps(self, episode_ids: Sequence[str]) -> dict[str, float]:
        if not episode_ids:
            return {}
        result: dict[str, float] = {}
        with self.read_connect() as connection:
            for chunk in _chunks(episode_ids):
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT episode_id,source_last_executable_bid_vwap
                    FROM imported_episodes
                    WHERE episode_id IN ({placeholders})
                    """,
                    tuple(chunk),
                )
                for row in rows:
                    if row[1] is not None:
                        result[str(row[0])] = float(row[1])
                rows = connection.execute(
                    f"""
                    WITH ranked AS (
                        SELECT episode_id,exit_bid_vwap,
                               ROW_NUMBER() OVER (
                                   PARTITION BY episode_id
                                   ORDER BY observed_at DESC,path_observation_id DESC
                               ) AS position
                        FROM episode_path_observations
                        WHERE episode_id IN ({placeholders})
                          AND path_status='EXECUTABLE'
                          AND exit_bid_vwap IS NOT NULL
                    )
                    SELECT episode_id,exit_bid_vwap FROM ranked WHERE position=1
                    """,
                    tuple(chunk),
                )
                for row in rows:
                    result[str(row[0])] = float(row[1])
        return result

    def threshold_event_keys(
        self, episode_ids: Sequence[str]
    ) -> set[tuple[str, str, float]]:
        if not episode_ids:
            return set()
        result: set[tuple[str, str, float]] = set()
        with self.read_connect() as connection:
            for chunk in _chunks(episode_ids):
                placeholders = ",".join("?" for _ in chunk)
                for table in (
                    "imported_threshold_events",
                    "episode_threshold_events",
                ):
                    rows = connection.execute(
                        f"SELECT episode_id,event_kind,threshold FROM {table} "
                        f"WHERE episode_id IN ({placeholders})",
                        tuple(chunk),
                    )
                    result.update(
                        (str(row[0]), str(row[1]), float(row[2])) for row in rows
                    )
        return result

    def publish_cycle(self, bundle: Mapping[str, Any]) -> None:
        expected_tokens = tuple(bundle["expected_tokens"])
        expected_conditions = tuple(bundle["expected_conditions"])
        expected_episodes = tuple(bundle["expected_episode_ids"])
        attempts = list(bundle.get("book_attempts", ()))
        books = list(bundle.get("compact_books", ()))
        paths = list(bundle.get("paths", ()))
        resolutions = list(bundle.get("resolutions", ()))
        if {str(row["token_id"]) for row in attempts} != set(expected_tokens):
            raise ValueError("follow-up token attempts do not cover the frozen token set")
        if len(attempts) != len(expected_tokens):
            raise ValueError("follow-up token attempts contain duplicates")
        if len(books) != len({str(row["token_id"]) for row in books}):
            raise ValueError("follow-up compact books are not one row per token")
        observed_tokens = {
            str(row["token_id"])
            for row in attempts
            if row["status"] == "OBSERVED"
        }
        if {str(row["token_id"]) for row in books} != observed_tokens:
            raise ValueError("observed token attempts and compact books disagree")
        if {str(row["episode_id"]) for row in paths} != set(expected_episodes):
            raise ValueError("follow-up paths do not cover every unresolved episode")
        if len(paths) != len(expected_episodes):
            raise ValueError("follow-up paths contain duplicate episodes")
        if {str(row["condition_id"]) for row in resolutions} != set(
            expected_conditions
        ):
            raise ValueError("follow-up resolutions do not cover every condition")
        if len(resolutions) != len(expected_conditions):
            raise ValueError("follow-up resolutions contain duplicate conditions")
        cycle = dict(bundle["cycle"])
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._insert_many(connection, "followup_cycles", [cycle])
                self._insert_many(connection, "book_token_attempts", attempts)
                self._insert_many(connection, "compact_books", books)
                self._insert_many(connection, "episode_path_observations", paths)
                self._insert_many(
                    connection,
                    "episode_threshold_events",
                    bundle.get("threshold_events", ()),
                )
                self._insert_many(connection, "resolution_observations", resolutions)
                self._insert_many(
                    connection, "data_quality_issues", bundle.get("quality_issues", ())
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def record_phase_timings(self, rows: Iterable[Mapping[str, Any]]) -> None:
        with self._connect() as connection:
            self._insert_many(connection, "phase_timings", rows)
            connection.commit()

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
        if usage.free < storage.min_free_gib * GIB or used_ratio >= storage.stop_used_ratio:
            state = "STOP"
        elif used_ratio >= storage.warn_used_ratio:
            state = "WARN"
        else:
            state = "OK"
        journal = self.db_path.with_name(self.db_path.name + "-journal")
        row = {
            "metric_id": metric_id,
            "run_id": run_id,
            "phase": phase,
            "recorded_at": iso_utc(),
            "db_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "journal_bytes": journal.stat().st_size if journal.exists() else 0,
            "filesystem_total_bytes": usage.total,
            "filesystem_used_bytes": usage.used,
            "filesystem_free_bytes": usage.free,
            "filesystem_used_ratio": used_ratio,
            "guard_state": state,
        }
        self._insert_one("storage_metrics", row)
        return row

    def lightweight_status(self) -> dict[str, Any]:
        if not self.db_path.is_file():
            return {
                "healthy": False,
                "database_exists": False,
                "db_path": str(self.db_path),
                "data_contract": FOLLOWUP_DATA_CONTRACT,
                "deep_check_performed": False,
            }
        with self.read_connect() as connection:
            latest = connection.execute(
                "SELECT * FROM followup_cycles ORDER BY cycle_number DESC LIMIT 1"
            ).fetchone()
            terminal = connection.execute(
                "SELECT * FROM research_run_events ORDER BY event_at DESC,event_id DESC LIMIT 1"
            ).fetchone()
            unresolved = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM imported_episodes e
                    JOIN imported_condition_status s ON s.condition_id=e.condition_id
                    WHERE s.terminal_at_handoff=0 AND NOT EXISTS (
                      SELECT 1 FROM resolution_observations r
                      WHERE r.condition_id=e.condition_id
                        AND r.resolution_status='RESOLVED'
                    )
                    """
                ).fetchone()[0]
            )
        return {
            "healthy": bool(terminal and terminal["event_type"] == "SUCCEEDED"),
            "database_exists": True,
            "db_path": str(self.db_path),
            "db_bytes": self.db_path.stat().st_size,
            "data_contract": FOLLOWUP_DATA_CONTRACT,
            "deep_check_performed": False,
            "latest_cycle": dict(latest) if latest else None,
            "latest_run_event": dict(terminal) if terminal else None,
            "unresolved_episode_count": unresolved,
        }


__all__ = [
    "APPEND_ONLY_TABLES",
    "FOLLOWUP_SCHEMA_VERSION",
    "FollowupRepository",
    "GIB",
    "SCHEMA",
]
