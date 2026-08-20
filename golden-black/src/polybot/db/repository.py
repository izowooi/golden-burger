"""SQLite append-only evidence repository for the paired experiment."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Iterable, Iterator, Mapping
from uuid import uuid4


SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_metadata (
    data_contract TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_config_versions (
    config_hash TEXT PRIMARY KEY,
    strategy_source_digest TEXT NOT NULL,
    preregistration_sha256 TEXT NOT NULL,
    job_name TEXT NOT NULL,
    mode TEXT NOT NULL,
    config_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_run_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('STARTED','SUCCEEDED','FAILED')),
    observed_at TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    strategy_source_digest TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS run_events_run_idx ON research_run_events(run_id, observed_at);

CREATE TABLE IF NOT EXISTS api_requests (
    request_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    request_kind TEXT NOT NULL,
    page_number INTEGER,
    attempt_number INTEGER NOT NULL,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    params_json TEXT NOT NULL,
    body_sha256 TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    elapsed_ms REAL NOT NULL,
    status TEXT NOT NULL,
    http_status INTEGER,
    response_sha256 TEXT,
    response_bytes INTEGER NOT NULL,
    error_type TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS raw_payloads (
    payload_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    payload_kind TEXT NOT NULL,
    request_id TEXT,
    observed_at TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    raw_bytes INTEGER NOT NULL,
    gzip_bytes INTEGER NOT NULL,
    payload_gzip BLOB NOT NULL,
    UNIQUE (run_id, payload_kind, request_id, sha256)
);

CREATE TABLE IF NOT EXISTS market_sweeps (
    sweep_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    event_count INTEGER NOT NULL,
    market_count INTEGER NOT NULL,
    eligible_market_count INTEGER NOT NULL,
    cursor_complete INTEGER NOT NULL CHECK (cursor_complete IN (0,1)),
    request_envelope_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_observations (
    observation_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES market_sweeps(sweep_id),
    run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_title TEXT,
    condition_id TEXT,
    market_id TEXT,
    question TEXT,
    observed_at TEXT NOT NULL,
    end_date TEXT,
    game_start_time TEXT,
    hours_until_end REAL,
    sports_phase TEXT NOT NULL,
    liquidity REAL,
    volume_total REAL,
    active INTEGER,
    closed INTEGER,
    accepting_orders INTEGER,
    enable_order_book INTEGER,
    neg_risk INTEGER,
    fee_rate REAL,
    fee_schedule_json TEXT NOT NULL,
    outcome_labels_json TEXT NOT NULL,
    token_ids_json TEXT NOT NULL,
    outcome_prices_json TEXT NOT NULL,
    eligible INTEGER NOT NULL CHECK (eligible IN (0,1)),
    exclusion_reason TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    UNIQUE (sweep_id, event_id, condition_id)
);
CREATE INDEX IF NOT EXISTS market_condition_time_idx ON market_observations(condition_id, observed_at);

CREATE TABLE IF NOT EXISTS outcome_observations (
    outcome_observation_id TEXT PRIMARY KEY,
    market_observation_id TEXT NOT NULL REFERENCES market_observations(observation_id),
    sweep_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    outcome_label TEXT NOT NULL,
    gamma_probability REAL,
    observed_at TEXT NOT NULL,
    UNIQUE (sweep_id, token_id)
);
CREATE INDEX IF NOT EXISTS outcome_token_time_idx ON outcome_observations(token_id, observed_at);

CREATE TABLE IF NOT EXISTS orderbook_token_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    status TEXT NOT NULL,
    request_id TEXT,
    observed_at TEXT,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (run_id, token_id)
);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    raw_book_sha256 TEXT NOT NULL,
    best_bid REAL,
    best_ask REAL,
    bid_level_count INTEGER NOT NULL,
    ask_level_count INTEGER NOT NULL,
    source_timestamp TEXT,
    tick_size REAL,
    min_order_size REAL,
    UNIQUE (run_id, token_id)
);
CREATE INDEX IF NOT EXISTS book_token_time_idx ON orderbook_snapshots(token_id, observed_at);

CREATE TABLE IF NOT EXISTS orderbook_levels (
    level_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),
    side TEXT NOT NULL CHECK (side IN ('BID','ASK')),
    level_index INTEGER NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    UNIQUE (snapshot_id, side, level_index)
);

CREATE TABLE IF NOT EXISTS signal_decisions (
    decision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    market_observation_id TEXT NOT NULL REFERENCES market_observations(observation_id),
    snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),
    condition_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    threshold REAL NOT NULL,
    decided_at TEXT NOT NULL,
    best_ask REAL,
    entry_vwap REAL,
    entry_shares REAL,
    entry_cost REAL,
    decision_status TEXT NOT NULL,
    details_json TEXT NOT NULL,
    episode_id TEXT,
    UNIQUE (run_id, token_id, threshold)
);
CREATE INDEX IF NOT EXISTS decisions_status_idx ON signal_decisions(decision_status, decided_at, threshold);

CREATE TABLE IF NOT EXISTS hypothetical_episodes (
    episode_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE REFERENCES signal_decisions(decision_id),
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_title TEXT,
    question TEXT,
    token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    outcome_label TEXT NOT NULL,
    threshold REAL NOT NULL,
    entered_at TEXT NOT NULL,
    end_date TEXT NOT NULL,
    game_start_time TEXT,
    sports_phase TEXT NOT NULL,
    liquidity REAL NOT NULL,
    volume_total REAL NOT NULL,
    fee_rate REAL NOT NULL,
    entry_best_ask REAL NOT NULL,
    entry_vwap REAL NOT NULL,
    entry_shares REAL NOT NULL,
    entry_cost REAL NOT NULL,
    UNIQUE (condition_id, token_id, threshold)
);
CREATE INDEX IF NOT EXISTS episodes_threshold_time_idx ON hypothetical_episodes(threshold, entered_at);

CREATE TABLE IF NOT EXISTS counterfactual_exit_policies (
    policy_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),
    created_run_id TEXT NOT NULL,
    policy_key TEXT NOT NULL,
    stop_price REAL,
    created_at TEXT NOT NULL,
    CHECK (
        (policy_key = 'HOLD_TO_RESOLUTION' AND stop_price IS NULL)
        OR (policy_key LIKE 'STOP_%' AND stop_price > 0 AND stop_price < 1)
    ),
    UNIQUE (episode_id, policy_key)
);
CREATE INDEX IF NOT EXISTS exit_policy_episode_idx ON counterfactual_exit_policies(episode_id, policy_key);

CREATE TABLE IF NOT EXISTS episode_path_observations (
    path_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),
    run_id TEXT NOT NULL,
    snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),
    observed_at TEXT NOT NULL,
    best_bid REAL,
    executable_bid_vwap REAL,
    executable_proceeds REAL,
    status TEXT NOT NULL,
    UNIQUE (episode_id, run_id)
);

CREATE TABLE IF NOT EXISTS stop_execution_attempts (
    attempt_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL REFERENCES counterfactual_exit_policies(policy_id),
    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),
    run_id TEXT NOT NULL,
    snapshot_id TEXT REFERENCES orderbook_snapshots(snapshot_id),
    observed_at TEXT NOT NULL,
    stop_price REAL NOT NULL,
    prior_best_bid REAL,
    trigger_best_bid REAL,
    requested_shares REAL NOT NULL,
    filled_shares REAL NOT NULL,
    remaining_shares REAL NOT NULL,
    exit_vwap REAL,
    gross_proceeds REAL NOT NULL,
    fee_rate REAL NOT NULL,
    estimated_fee REAL NOT NULL,
    net_proceeds REAL NOT NULL,
    levels_used INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('FULL_EXIT','PARTIAL_FILL','NO_BID_DEPTH')),
    gap_from_stop REAL,
    drop_from_prior REAL,
    UNIQUE (policy_id, run_id)
);
CREATE INDEX IF NOT EXISTS stop_attempt_policy_time_idx ON stop_execution_attempts(policy_id, observed_at);

CREATE TABLE IF NOT EXISTS counterfactual_stop_exits (
    exit_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL UNIQUE REFERENCES counterfactual_exit_policies(policy_id),
    episode_id TEXT NOT NULL REFERENCES hypothetical_episodes(episode_id),
    completed_run_id TEXT NOT NULL,
    completed_attempt_id TEXT NOT NULL UNIQUE REFERENCES stop_execution_attempts(attempt_id),
    first_triggered_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    stop_price REAL NOT NULL,
    first_trigger_best_bid REAL,
    exit_vwap REAL NOT NULL,
    requested_shares REAL NOT NULL,
    filled_shares REAL NOT NULL,
    gross_proceeds REAL NOT NULL,
    estimated_fee REAL NOT NULL,
    net_proceeds REAL NOT NULL,
    attempt_count INTEGER NOT NULL,
    gap_from_stop REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS stop_exit_episode_idx ON counterfactual_stop_exits(episode_id, stop_price);

CREATE TABLE IF NOT EXISTS resolution_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    request_id TEXT,
    winner_index INTEGER,
    error_type TEXT,
    error_message TEXT,
    UNIQUE (run_id, condition_id)
);
CREATE INDEX IF NOT EXISTS resolution_attempt_time_idx ON resolution_attempts(condition_id, attempted_at);

CREATE TABLE IF NOT EXISTS resolution_observations (
    resolution_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    condition_id TEXT NOT NULL UNIQUE,
    observed_at TEXT NOT NULL,
    winner_index INTEGER NOT NULL CHECK (winner_index IN (0,1)),
    request_id TEXT NOT NULL,
    raw_market_sha256 TEXT NOT NULL,
    evidence_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    issue_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    severity TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    detail_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_metrics (
    metric_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    db_bytes INTEGER NOT NULL,
    free_bytes INTEGER NOT NULL,
    total_bytes INTEGER NOT NULL,
    used_ratio REAL NOT NULL
);
"""

APPEND_ONLY_TABLES = (
    "research_config_versions", "research_run_events", "api_requests", "raw_payloads",
    "market_sweeps", "market_observations", "outcome_observations",
    "orderbook_token_attempts", "orderbook_snapshots", "orderbook_levels",
    "signal_decisions", "hypothetical_episodes", "episode_path_observations",
    "counterfactual_exit_policies", "stop_execution_attempts",
    "counterfactual_stop_exits",
    "resolution_attempts", "resolution_observations", "data_quality_issues",
    "storage_metrics",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ResearchRepository:
    def __init__(self, path: Path, *, busy_timeout_ms: int, data_contract: str) -> None:
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms
        self.data_contract = data_contract
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            market_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(market_observations)"
                )
            }
            if "neg_risk" not in market_columns:
                # The pre-entry-window build used the same two-outcome
                # population but retained negRisk only in raw payloads.  Add a
                # normalized stratum without rewriting append-only rows.
                connection.execute(
                    "ALTER TABLE market_observations ADD COLUMN neg_risk INTEGER"
                )
            connection.execute(
                "INSERT OR IGNORE INTO schema_metadata(data_contract,created_at) VALUES(?,?)",
                (data_contract, _now()),
            )
            actual = connection.execute("SELECT data_contract FROM schema_metadata").fetchone()
            if actual is None or actual[0] != data_contract:
                raise RuntimeError(f"database contract mismatch: {actual}")
            for table in APPEND_ONLY_TABLES:
                for operation in ("UPDATE", "DELETE"):
                    trigger = f"{table}_forbid_{operation.lower()}"
                    connection.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {trigger} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"
                    )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_config(self, row: Mapping[str, Any]) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT OR IGNORE INTO research_config_versions VALUES(?,?,?,?,?,?,?)",
                tuple(row[key] for key in ("config_hash","strategy_source_digest","preregistration_sha256","job_name","mode","config_json","first_seen_at")),
            )

    def record_run_event(self, row: Mapping[str, Any]) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO research_run_events VALUES(?,?,?,?,?,?,?)",
                tuple(row[key] for key in ("event_id","run_id","event_type","observed_at","config_hash","strategy_source_digest","detail_json")),
            )

    def record_api_request(self, row: Mapping[str, Any]) -> None:
        keys = ("request_id","run_id","request_kind","page_number","attempt_number","method","url","params_json","body_sha256","started_at","completed_at","elapsed_ms","status","http_status","response_sha256","response_bytes","error_type","error_message")
        with self.connect() as c:
            c.execute(f"INSERT INTO api_requests({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", tuple(row.get(key) for key in keys))

    @staticmethod
    def payload_row(*, run_id: str, kind: str, request_id: str | None, observed_at: str, raw: bytes) -> dict[str, Any]:
        compressed = gzip.compress(raw, compresslevel=6, mtime=0)
        digest = hashlib.sha256(raw).hexdigest()
        return {
            "payload_id": uuid4().hex, "run_id": run_id, "payload_kind": kind,
            "request_id": request_id, "observed_at": observed_at, "sha256": digest,
            "raw_bytes": len(raw), "gzip_bytes": len(compressed), "payload_gzip": compressed,
        }

    def record_collection(
        self,
        *,
        sweep: Mapping[str, Any],
        payloads: Iterable[Mapping[str, Any]],
        markets: Iterable[Mapping[str, Any]],
        outcomes: Iterable[Mapping[str, Any]],
        attempts: Iterable[Mapping[str, Any]],
        snapshots: Iterable[Mapping[str, Any]],
        levels: Iterable[Mapping[str, Any]],
        decisions: Iterable[Mapping[str, Any]],
        episodes: Iterable[Mapping[str, Any]],
        policies: Iterable[Mapping[str, Any]],
        paths: Iterable[Mapping[str, Any]],
        stop_attempts: Iterable[Mapping[str, Any]],
        stop_exits: Iterable[Mapping[str, Any]],
    ) -> None:
        with self.connect() as c:
            self._insert(c, "market_sweeps", sweep)
            self._insert_many(c, "raw_payloads", payloads)
            self._insert_many(c, "market_observations", markets)
            self._insert_many(c, "outcome_observations", outcomes)
            self._insert_many(c, "orderbook_token_attempts", attempts)
            self._insert_many(c, "orderbook_snapshots", snapshots)
            self._insert_many(c, "orderbook_levels", levels)
            self._insert_many(c, "signal_decisions", decisions)
            self._insert_many(c, "hypothetical_episodes", episodes)
            self._insert_many(c, "counterfactual_exit_policies", policies)
            self._insert_many(c, "episode_path_observations", paths)
            self._insert_many(c, "stop_execution_attempts", stop_attempts)
            self._insert_many(c, "counterfactual_stop_exits", stop_exits)

    @staticmethod
    def _insert(connection: sqlite3.Connection, table: str, row: Mapping[str, Any]) -> None:
        keys = tuple(row)
        connection.execute(
            f"INSERT INTO {table}({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",
            tuple(row[key] for key in keys),
        )

    @classmethod
    def _insert_many(cls, connection: sqlite3.Connection, table: str, rows: Iterable[Mapping[str, Any]]) -> None:
        for row in rows:
            cls._insert(connection, table, row)

    def existing_episode_keys(self) -> set[tuple[str, str, float]]:
        with self.connect() as c:
            return {(str(r[0]), str(r[1]), float(r[2])) for r in c.execute("SELECT condition_id,token_id,threshold FROM hypothetical_episodes")}

    def open_episodes(self) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute(
                """
                SELECT e.* FROM hypothetical_episodes e
                LEFT JOIN resolution_observations r ON r.condition_id=e.condition_id
                WHERE r.condition_id IS NULL ORDER BY e.entered_at
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def active_stop_policies(self) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute(
                """
                SELECT
                    p.policy_id,p.policy_key,p.stop_price,p.created_at,
                    e.*,
                    COUNT(a.attempt_id) AS prior_attempt_count,
                    COALESCE(SUM(a.filled_shares),0) AS prior_filled_shares,
                    COALESCE(SUM(a.gross_proceeds),0) AS prior_gross_proceeds,
                    COALESCE(SUM(a.estimated_fee),0) AS prior_estimated_fee,
                    COALESCE(SUM(a.net_proceeds),0) AS prior_net_proceeds,
                    MIN(a.observed_at) AS first_triggered_at,
                    (
                        SELECT first_attempt.trigger_best_bid
                        FROM stop_execution_attempts first_attempt
                        WHERE first_attempt.policy_id=p.policy_id
                        ORDER BY first_attempt.observed_at,first_attempt.attempt_id LIMIT 1
                    ) AS first_trigger_best_bid,
                    (
                        SELECT path.best_bid FROM episode_path_observations path
                        WHERE path.episode_id=e.episode_id
                        ORDER BY path.observed_at DESC,path.path_id DESC LIMIT 1
                    ) AS prior_best_bid
                FROM counterfactual_exit_policies p
                JOIN hypothetical_episodes e USING(episode_id)
                LEFT JOIN stop_execution_attempts a USING(policy_id)
                LEFT JOIN counterfactual_stop_exits x USING(policy_id)
                LEFT JOIN resolution_observations r ON r.condition_id=e.condition_id
                WHERE p.stop_price IS NOT NULL
                  AND x.policy_id IS NULL
                  AND r.condition_id IS NULL
                GROUP BY p.policy_id
                ORDER BY e.entered_at,p.stop_price DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def resolution_due(self, condition_id: str, *, now: datetime, interval_minutes: int = 30) -> bool:
        with self.connect() as c:
            row = c.execute("SELECT MAX(attempted_at) FROM resolution_attempts WHERE condition_id=?", (condition_id,)).fetchone()
        if not row or row[0] is None:
            return True
        prior = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        return (now.astimezone(timezone.utc) - prior.astimezone(timezone.utc)).total_seconds() >= interval_minutes * 60

    def record_resolution(
        self,
        *,
        attempt: Mapping[str, Any],
        resolution: Mapping[str, Any] | None,
        payload: Mapping[str, Any] | None,
    ) -> None:
        with self.connect() as c:
            self._insert(c, "resolution_attempts", attempt)
            if payload is not None:
                self._insert(c, "raw_payloads", payload)
            if resolution is not None:
                self._insert(c, "resolution_observations", resolution)

    def record_issue(self, *, run_id: str, severity: str, issue_type: str, detail: Mapping[str, Any]) -> None:
        with self.connect() as c:
            self._insert(c, "data_quality_issues", {
                "issue_id": uuid4().hex, "run_id": run_id, "observed_at": _now(),
                "severity": severity, "issue_type": issue_type,
                "detail_json": json.dumps(detail, sort_keys=True, separators=(",", ":")),
            })

    def record_storage_metric(self, run_id: str) -> dict[str, Any]:
        usage = shutil.disk_usage(self.path.parent)
        row = {
            "metric_id": uuid4().hex, "run_id": run_id, "observed_at": _now(),
            "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "free_bytes": usage.free, "total_bytes": usage.total,
            "used_ratio": (usage.total - usage.free) / usage.total,
        }
        with self.connect() as c:
            self._insert(c, "storage_metrics", row)
        return row

    def quick_check(self) -> str:
        with self.connect() as c:
            return str(c.execute("PRAGMA quick_check").fetchone()[0])

    def summary(self) -> dict[str, Any]:
        with self.connect() as c:
            result = {
                "quick_check": c.execute("PRAGMA quick_check").fetchone()[0],
                "runs": c.execute("SELECT COUNT(DISTINCT run_id) FROM research_run_events WHERE event_type='SUCCEEDED'").fetchone()[0],
                "sweeps": c.execute("SELECT COUNT(*) FROM market_sweeps").fetchone()[0],
                "episodes": c.execute("SELECT COUNT(*) FROM hypothetical_episodes").fetchone()[0],
                "exit_policies": c.execute("SELECT COUNT(*) FROM counterfactual_exit_policies").fetchone()[0],
                "stop_attempts": c.execute("SELECT COUNT(*) FROM stop_execution_attempts").fetchone()[0],
                "stop_exits": c.execute("SELECT COUNT(*) FROM counterfactual_stop_exits").fetchone()[0],
                "resolutions": c.execute("SELECT COUNT(*) FROM resolution_observations").fetchone()[0],
                "issues": c.execute("SELECT COUNT(*) FROM data_quality_issues").fetchone()[0],
                "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
            }
            result["arms"] = {str(row[0]): {"episodes": row[1], "resolved": row[2]} for row in c.execute(
                """SELECT e.threshold,COUNT(*),SUM(CASE WHEN r.condition_id IS NOT NULL THEN 1 ELSE 0 END)
                FROM hypothetical_episodes e LEFT JOIN resolution_observations r USING(condition_id)
                GROUP BY e.threshold ORDER BY e.threshold"""
            )}
            result["stop_policies"] = {
                str(row[0]): {
                    "policies": row[1], "triggered": row[2], "completed": row[3],
                }
                for row in c.execute(
                    """
                    SELECT p.policy_key,COUNT(*),
                           SUM(CASE WHEN a.policy_id IS NOT NULL THEN 1 ELSE 0 END),
                           SUM(CASE WHEN x.policy_id IS NOT NULL THEN 1 ELSE 0 END)
                    FROM counterfactual_exit_policies p
                    LEFT JOIN (
                        SELECT DISTINCT policy_id FROM stop_execution_attempts
                    ) a USING(policy_id)
                    LEFT JOIN counterfactual_stop_exits x USING(policy_id)
                    GROUP BY p.policy_key ORDER BY p.policy_key
                    """
                )
            }
            return result
