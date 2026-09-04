"""Append-only SQLite evidence store for Cherry Shadow Resolution v2."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator, Mapping
from uuid import uuid4

from . import DATA_CONTRACT
from .config import ShadowConfig, canonical_json
from .transport import iso_utc


SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS shadow_schema_metadata (
    data_contract TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shadow_config_versions (
    config_hash TEXT PRIMARY KEY,
    data_contract TEXT NOT NULL,
    runtime_job TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode = 'shadow'),
    strategy_source_digest TEXT NOT NULL,
    preregistration_id TEXT NOT NULL,
    preregistration_sha256 TEXT NOT NULL,
    config_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shadow_run_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED','SUCCEEDED','FAILED')),
    observed_at TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    strategy_source_digest TEXT NOT NULL,
    preregistration_sha256 TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS shadow_run_events_run_idx
    ON shadow_run_events(run_id, observed_at);

CREATE TABLE IF NOT EXISTS shadow_api_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    request_kind TEXT NOT NULL,
    page_number INTEGER,
    attempt_number INTEGER NOT NULL,
    method TEXT NOT NULL CHECK (method = 'GET'),
    url TEXT NOT NULL,
    params_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    elapsed_ms REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SUCCESS','FAILED')),
    http_status INTEGER,
    response_sha256 TEXT,
    response_bytes INTEGER NOT NULL,
    error_type TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS shadow_raw_payloads (
    payload_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    payload_kind TEXT NOT NULL,
    request_id TEXT,
    source_received_at TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    raw_bytes INTEGER NOT NULL,
    gzip_bytes INTEGER NOT NULL,
    payload_gzip BLOB NOT NULL,
    UNIQUE(run_id, payload_kind, request_id, sha256)
);

CREATE TABLE IF NOT EXISTS shadow_market_sweeps (
    sweep_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    raw_market_count INTEGER NOT NULL,
    unique_condition_count INTEGER NOT NULL,
    eligible_candidate_count INTEGER NOT NULL,
    selected_book_count INTEGER NOT NULL,
    capped_candidate_count INTEGER NOT NULL,
    cursor_complete INTEGER NOT NULL CHECK (cursor_complete = 1),
    membership_sha256 TEXT NOT NULL,
    request_envelope_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shadow_sweep_memberships (
    membership_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES shadow_market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    page_ordinal INTEGER NOT NULL,
    deterministic_ordinal INTEGER NOT NULL,
    condition_id TEXT,
    raw_market_sha256 TEXT NOT NULL,
    source_received_at TEXT NOT NULL,
    qualification_status TEXT NOT NULL,
    exclusion_reasons_json TEXT NOT NULL,
    UNIQUE(sweep_id, page_number, page_ordinal)
);
CREATE INDEX IF NOT EXISTS shadow_memberships_condition_idx
    ON shadow_sweep_memberships(condition_id, sweep_id);

CREATE TABLE IF NOT EXISTS shadow_market_observations (
    observation_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES shadow_market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    deterministic_ordinal INTEGER NOT NULL,
    event_cluster_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_slug TEXT,
    event_title TEXT,
    category TEXT,
    event_tags_json TEXT NOT NULL,
    market_tags_json TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    market_id TEXT,
    market_slug TEXT,
    question TEXT,
    source_received_at TEXT NOT NULL,
    end_date TEXT,
    game_start_time TEXT,
    entry_reference TEXT,
    hours_until_entry_reference REAL,
    sports_market_type TEXT,
    time_stratum TEXT NOT NULL,
    liquidity REAL,
    volume_total REAL,
    active INTEGER,
    closed INTEGER,
    accepting_orders INTEGER,
    enable_order_book INTEGER,
    outcomes_json TEXT NOT NULL,
    token_ids_json TEXT NOT NULL,
    gamma_probabilities_json TEXT NOT NULL,
    primary_outcome_index INTEGER,
    primary_outcome_label TEXT,
    primary_token_id TEXT,
    primary_gamma_probability REAL,
    identity_aligned INTEGER NOT NULL CHECK (identity_aligned IN (0,1)),
    eligibility_status TEXT NOT NULL,
    exclusion_reasons_json TEXT NOT NULL,
    book_selection_status TEXT NOT NULL,
    UNIQUE(sweep_id, condition_id)
);
CREATE INDEX IF NOT EXISTS shadow_market_event_idx
    ON shadow_market_observations(event_cluster_id, condition_id);

CREATE TABLE IF NOT EXISTS shadow_book_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    status TEXT NOT NULL,
    request_id TEXT,
    source_received_at TEXT,
    error_type TEXT,
    UNIQUE(run_id, token_id)
);

CREATE TABLE IF NOT EXISTS shadow_book_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    source_received_at TEXT NOT NULL,
    raw_book_sha256 TEXT NOT NULL,
    source_timestamp TEXT,
    market_hash TEXT,
    best_bid REAL,
    best_ask REAL,
    bid_level_count INTEGER NOT NULL,
    ask_level_count INTEGER NOT NULL,
    UNIQUE(run_id, token_id)
);

CREATE TABLE IF NOT EXISTS shadow_book_levels (
    level_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES shadow_book_snapshots(snapshot_id),
    side TEXT NOT NULL CHECK (side IN ('BID','ASK')),
    level_index INTEGER NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    UNIQUE(snapshot_id, side, level_index)
);

CREATE TABLE IF NOT EXISTS shadow_cell_decisions (
    decision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    market_observation_id TEXT NOT NULL REFERENCES shadow_market_observations(observation_id),
    snapshot_id TEXT REFERENCES shadow_book_snapshots(snapshot_id),
    event_cluster_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    band_id TEXT NOT NULL,
    band_role TEXT NOT NULL,
    band_low REAL NOT NULL,
    band_high REAL NOT NULL,
    decided_at TEXT NOT NULL,
    entry_best_ask REAL,
    entry_vwap REAL,
    entry_shares REAL,
    entry_cost REAL,
    decision_status TEXT NOT NULL,
    details_json TEXT NOT NULL,
    episode_id TEXT,
    UNIQUE(run_id, condition_id, token_id, band_id)
);

CREATE TABLE IF NOT EXISTS shadow_episodes (
    episode_id TEXT PRIMARY KEY,
    opened_run_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    strategy_source_digest TEXT NOT NULL,
    preregistration_sha256 TEXT NOT NULL,
    event_cluster_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    question TEXT,
    category TEXT,
    token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    outcome_label TEXT NOT NULL,
    band_id TEXT NOT NULL,
    band_role TEXT NOT NULL,
    entered_at TEXT NOT NULL,
    source_received_at TEXT NOT NULL,
    time_stratum TEXT NOT NULL,
    end_date TEXT,
    game_start_time TEXT,
    liquidity REAL NOT NULL,
    volume_total REAL NOT NULL,
    entry_best_ask REAL NOT NULL,
    entry_vwap REAL NOT NULL,
    entry_shares REAL NOT NULL,
    entry_cost REAL NOT NULL,
    UNIQUE(condition_id, token_id, band_id)
);
CREATE INDEX IF NOT EXISTS shadow_episodes_cluster_idx
    ON shadow_episodes(event_cluster_id, band_id, entered_at);

CREATE TABLE IF NOT EXISTS shadow_episode_policies (
    episode_policy_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES shadow_episodes(episode_id),
    policy_id TEXT NOT NULL,
    policy_role TEXT NOT NULL,
    take_profit REAL,
    stop_loss REAL,
    trailing REAL,
    created_at TEXT NOT NULL,
    UNIQUE(episode_id, policy_id)
);

CREATE TABLE IF NOT EXISTS shadow_path_observations (
    path_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES shadow_episodes(episode_id),
    run_id TEXT NOT NULL,
    snapshot_id TEXT REFERENCES shadow_book_snapshots(snapshot_id),
    observed_at TEXT NOT NULL,
    source_received_at TEXT,
    best_bid REAL,
    executable_bid_vwap REAL,
    executable_proceeds REAL,
    filled_shares REAL NOT NULL,
    remaining_shares REAL NOT NULL,
    depth_complete INTEGER NOT NULL CHECK (depth_complete IN (0,1)),
    peak_executable_bid_vwap REAL,
    path_status TEXT NOT NULL,
    UNIQUE(episode_id, run_id)
);

CREATE TABLE IF NOT EXISTS shadow_resolution_observations (
    resolution_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    source_received_at TEXT NOT NULL,
    resolution_status TEXT NOT NULL,
    outcomes_json TEXT NOT NULL,
    token_ids_json TEXT NOT NULL,
    final_prices_json TEXT NOT NULL,
    winner_index INTEGER,
    token_payout REAL,
    evidence_basis TEXT NOT NULL,
    UNIQUE(run_id, condition_id, token_id)
);

CREATE TABLE IF NOT EXISTS shadow_policy_exits (
    exit_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES shadow_episodes(episode_id),
    policy_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    exited_at TEXT NOT NULL,
    source_received_at TEXT,
    exit_kind TEXT NOT NULL,
    trigger_value REAL,
    exit_price_vwap REAL,
    exit_proceeds REAL NOT NULL,
    pnl_usdc REAL NOT NULL,
    roi REAL NOT NULL,
    resolution_id TEXT REFERENCES shadow_resolution_observations(resolution_id),
    evidence_basis TEXT NOT NULL,
    UNIQUE(episode_id, policy_id)
);

CREATE TABLE IF NOT EXISTS shadow_data_quality_issues (
    issue_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    severity TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    condition_id TEXT,
    token_id TEXT,
    detail_json TEXT NOT NULL
);
"""

_IMMUTABLE_TABLES = (
    "shadow_schema_metadata",
    "shadow_config_versions",
    "shadow_run_events",
    "shadow_api_attempts",
    "shadow_raw_payloads",
    "shadow_market_sweeps",
    "shadow_sweep_memberships",
    "shadow_market_observations",
    "shadow_book_attempts",
    "shadow_book_snapshots",
    "shadow_book_levels",
    "shadow_cell_decisions",
    "shadow_episodes",
    "shadow_episode_policies",
    "shadow_path_observations",
    "shadow_resolution_observations",
    "shadow_policy_exits",
    "shadow_data_quality_issues",
)


class ShadowRepository:
    def __init__(self, db_path: str | Path, config: ShadowConfig) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.config = config
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO shadow_schema_metadata VALUES (?, ?)",
                (DATA_CONTRACT, iso_utc()),
            )
            rows = connection.execute(
                "SELECT data_contract FROM shadow_schema_metadata"
            ).fetchall()
            if [row[0] for row in rows] != [DATA_CONTRACT]:
                raise RuntimeError("shadow DB data contract mismatch")
            for table in _IMMUTABLE_TABLES:
                for operation in ("UPDATE", "DELETE"):
                    trigger = f"{table}_deny_{operation.lower()}"
                    connection.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {trigger} "
                        f"BEFORE {operation} ON {table} BEGIN "
                        "SELECT RAISE(ABORT, 'append-only shadow evidence'); END"
                    )

    @contextmanager
    def connect(self, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        if read_only:
            uri = f"file:{self.db_path}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=30)
            connection.execute("PRAGMA query_only=ON")
        else:
            connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            if not read_only:
                connection.commit()
        except BaseException:
            if not read_only:
                connection.rollback()
            raise
        finally:
            connection.close()

    def record_config(self) -> None:
        row = (
            self.config.config_hash,
            self.config.data_contract,
            self.config.runtime_job,
            "shadow",
            self.config.strategy_source_digest,
            self.config.preregistration_id,
            self.config.preregistration_sha256,
            canonical_json(self.config.evidence_dict()),
            iso_utc(),
        )
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO shadow_config_versions (
                    config_hash, data_contract, runtime_job, mode,
                    strategy_source_digest, preregistration_id,
                    preregistration_sha256, config_json, first_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            stored = connection.execute(
                "SELECT * FROM shadow_config_versions WHERE config_hash = ?",
                (self.config.config_hash,),
            ).fetchone()
            if stored is None or tuple(stored)[:-1] != row[:-1]:
                raise RuntimeError("immutable shadow config evidence mismatch")

    def record_run_event(
        self, run_id: str, event_type: str, detail: Mapping[str, Any] | None = None
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO shadow_run_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    run_id,
                    event_type,
                    iso_utc(),
                    self.config.config_hash,
                    self.config.strategy_source_digest,
                    self.config.preregistration_sha256,
                    canonical_json(dict(detail or {})),
                ),
            )

    def record_api_attempt(self, row: Mapping[str, Any]) -> None:
        columns = (
            "attempt_id", "run_id", "request_kind", "page_number",
            "attempt_number", "method", "url", "params_json", "started_at",
            "completed_at", "elapsed_ms", "status", "http_status",
            "response_sha256", "response_bytes", "error_type", "error_message",
        )
        with self.connect() as connection:
            connection.execute(
                f"INSERT INTO shadow_api_attempts ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                tuple(row.get(column) for column in columns),
            )

    def open_episodes(self) -> list[dict[str, Any]]:
        with self.connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT episode.* FROM shadow_episodes AS episode
                WHERE EXISTS (
                    SELECT 1 FROM shadow_episode_policies AS policy
                    WHERE policy.episode_id = episode.episode_id
                      AND NOT EXISTS (
                          SELECT 1 FROM shadow_policy_exits AS outcome
                          WHERE outcome.episode_id = policy.episode_id
                            AND outcome.policy_id = policy.policy_id
                      )
                )
                ORDER BY episode.event_cluster_id, episode.condition_id,
                         episode.token_id, episode.band_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def existing_episode_keys(self) -> dict[tuple[str, str, str], str]:
        with self.connect(read_only=True) as connection:
            rows = connection.execute(
                "SELECT condition_id, token_id, band_id, episode_id FROM shadow_episodes"
            ).fetchall()
        return {(row[0], row[1], row[2]): row[3] for row in rows}

    def policy_exits(self) -> set[tuple[str, str]]:
        with self.connect(read_only=True) as connection:
            return {
                (row[0], row[1])
                for row in connection.execute(
                    "SELECT episode_id, policy_id FROM shadow_policy_exits"
                )
            }

    def prior_peak(self, episode_id: str, entry_vwap: float) -> float:
        with self.connect(read_only=True) as connection:
            value = connection.execute(
                "SELECT MAX(executable_bid_vwap) FROM shadow_path_observations "
                "WHERE episode_id = ? AND depth_complete = 1",
                (episode_id,),
            ).fetchone()[0]
        return max(float(entry_vwap), float(value or entry_vwap))

    @staticmethod
    def raw_payload_row(
        run_id: str,
        kind: str,
        request_id: str | None,
        received_at: str,
        raw: bytes,
    ) -> dict[str, Any]:
        compressed = gzip.compress(raw, compresslevel=6, mtime=0)
        return {
            "payload_id": uuid4().hex,
            "run_id": run_id,
            "payload_kind": kind,
            "request_id": request_id,
            "source_received_at": received_at,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "raw_bytes": len(raw),
            "gzip_bytes": len(compressed),
            "payload_gzip": compressed,
        }

    @staticmethod
    def _insert_rows(
        connection: sqlite3.Connection,
        table: str,
        rows: Iterable[Mapping[str, Any]],
    ) -> None:
        rows = list(rows)
        if not rows:
            return
        columns = tuple(rows[0])
        if any(tuple(row) != columns for row in rows):
            raise ValueError(f"{table} rows have inconsistent columns")
        connection.executemany(
            f"INSERT INTO {table} ({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            [tuple(row[column] for column in columns) for row in rows],
        )

    def publish(self, bundle: Mapping[str, Iterable[Mapping[str, Any]]]) -> None:
        order = (
            "shadow_raw_payloads",
            "shadow_market_sweeps",
            "shadow_sweep_memberships",
            "shadow_market_observations",
            "shadow_book_attempts",
            "shadow_book_snapshots",
            "shadow_book_levels",
            "shadow_cell_decisions",
            "shadow_episodes",
            "shadow_episode_policies",
            "shadow_path_observations",
            "shadow_resolution_observations",
            "shadow_policy_exits",
            "shadow_data_quality_issues",
        )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for table in order:
                self._insert_rows(connection, table, bundle.get(table, ()))

    def quick_check(self) -> str:
        with self.connect(read_only=True) as connection:
            return str(connection.execute("PRAGMA quick_check").fetchone()[0])

    def summary(self) -> dict[str, Any]:
        with self.connect(read_only=True) as connection:
            counts = {}
            for table in (
                "shadow_market_sweeps",
                "shadow_market_observations",
                "shadow_book_snapshots",
                "shadow_cell_decisions",
                "shadow_episodes",
                "shadow_path_observations",
                "shadow_resolution_observations",
                "shadow_policy_exits",
            ):
                counts[table] = int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
            run_counts = {
                row[0]: int(row[1])
                for row in connection.execute(
                    "SELECT event_type, COUNT(*) FROM shadow_run_events GROUP BY event_type"
                )
            }
        return {
            "data_contract": DATA_CONTRACT,
            "runtime_job": self.config.runtime_job,
            "mode": "shadow",
            "config_hash": self.config.config_hash,
            "strategy_source_digest": self.config.strategy_source_digest,
            "preregistration_sha256": self.config.preregistration_sha256,
            "db_path": str(self.db_path),
            "quick_check": self.quick_check(),
            "run_events": run_counts,
            "counts": counts,
        }
