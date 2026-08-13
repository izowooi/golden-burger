"""Append-only SQLite evidence store for Queue Echo."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Iterable, Iterator, Mapping

from ..config import BotConfig, DATA_CONTRACT, StorageConfig
from ..utils.retry import iso_utc


GIB = 1024**3
SCHEMA_VERSION = 1


SCHEMA = r"""
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_contracts (
    job_name TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    data_contract TEXT NOT NULL CHECK (data_contract = 'queue-echo-v1'),
    shard_index INTEGER NOT NULL CHECK (shard_index BETWEEN 0 AND 2),
    shard_count INTEGER NOT NULL CHECK (shard_count = 3),
    cadence_minutes INTEGER NOT NULL CHECK (cadence_minutes = 5),
    cadence_offset_minute INTEGER NOT NULL CHECK (cadence_offset_minute BETWEEN 0 AND 2),
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    preregistration_sha256 TEXT NOT NULL,
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
    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED', 'SUCCEEDED', 'FAILED')),
    event_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS research_run_events_run_idx
    ON research_run_events(run_id, event_at);

CREATE TABLE IF NOT EXISTS api_requests (
    request_id TEXT PRIMARY KEY,
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
    data_contract TEXT NOT NULL CHECK (data_contract = 'queue-echo-v1')
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
    payload_kind TEXT NOT NULL CHECK (payload_kind = 'clob_books'),
    content_encoding TEXT NOT NULL CHECK (content_encoding = 'gzip'),
    payload_sha256 TEXT NOT NULL,
    uncompressed_bytes INTEGER NOT NULL,
    compressed_bytes INTEGER NOT NULL,
    payload_blob BLOB NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orderbook_token_attempts (
    attempt_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    condition_id TEXT,
    token_id TEXT NOT NULL,
    outcome_index INTEGER,
    outcome_label TEXT,
    attempt_role TEXT NOT NULL CHECK (attempt_role IN ('UNIVERSE', 'FOLLOWUP_ONLY')),
    status TEXT NOT NULL CHECK (status IN ('OBSERVED', 'EMPTY_BOOK', 'MISSING', 'MALFORMED', 'ERROR')),
    request_id TEXT,
    request_started_at TEXT,
    received_at TEXT,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (sweep_id, token_id)
);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    strategy_source_digest TEXT NOT NULL,
    condition_id TEXT,
    market_id TEXT,
    event_id TEXT,
    token_id TEXT NOT NULL,
    outcome_index INTEGER,
    outcome_label TEXT,
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
    UNIQUE (sweep_id, token_id)
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

CREATE TABLE IF NOT EXISTS followup_attempts (
    followup_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES research_cases(case_id),
    observing_run_id TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('QUOTE_COMPLETE', 'SOURCE_MISSING', 'INVALID_QUOTE', 'WINDOW_EXPIRED')),
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
    "api_requests",
    "market_sweeps",
    "market_observations",
    "raw_payloads",
    "orderbook_token_attempts",
    "orderbook_snapshots",
    "orderbook_levels",
    "signal_decisions",
    "research_cases",
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


class ResearchRepository:
    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 30_000) -> None:
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
        with self._connect() as connection:
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
                        job_name, strategy_name, data_contract, shard_index,
                        shard_count, cadence_minutes, cadence_offset_minute,
                        window_start, window_end, preregistration_sha256,
                        contract_json, created_at
                    ) VALUES (?, 'golden-raspberry', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        config.job_name,
                        DATA_CONTRACT,
                        exp.shard_index,
                        exp.shard_count,
                        config.trading.cadence_minutes,
                        exp.cadence_offset_minute,
                        iso_utc(exp.start_utc),
                        iso_utc(exp.end_utc),
                        exp.preregistration_sha256,
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
            "shard_index": exp.shard_index,
            "shard_count": exp.shard_count,
            "cadence_minutes": config.trading.cadence_minutes,
            "cadence_offset_minute": exp.cadence_offset_minute,
            "window_start": iso_utc(exp.start_utc),
            "window_end": iso_utc(exp.end_utc),
            "preregistration_sha256": exp.preregistration_sha256,
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
                        lifecycle_mode, strategy_source_digest,
                        preregistration_sha256, config_json, git_commit, created_at
                    ) VALUES (?, 'golden-raspberry', ?, ?, ?, 'sim', 'archive_only', ?, ?, ?, ?, ?)
                    """,
                    (
                        config.config_hash,
                        config.job_name,
                        exp.shard_index,
                        exp.shard_count,
                        config.trading.strategy_source_digest,
                        exp.preregistration_sha256,
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

    def pending_cases(self, *, now: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        terminal = "SELECT 1 FROM followup_attempts f WHERE f.case_id=c.case_id"
        with self._connect() as connection:
            due = connection.execute(
                f"""
                SELECT c.* FROM research_cases c
                WHERE c.target_at <= ? AND c.window_end >= ?
                  AND NOT EXISTS ({terminal})
                ORDER BY c.target_at, c.case_id
                """,
                (now, now),
            ).fetchall()
            expired = connection.execute(
                f"""
                SELECT c.* FROM research_cases c
                WHERE c.window_end < ? AND NOT EXISTS ({terminal})
                ORDER BY c.window_end, c.case_id
                """,
                (now,),
            ).fetchall()
            return [dict(row) for row in due], [dict(row) for row in expired]

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
            "followup_attempts",
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
