"""SQLite append-only evidence repository for the cadence experiment."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any, Iterable, Iterator, Mapping
from uuid import uuid4


# Frozen legacy v3 schema reference. It is deliberately never executed by the
# v4b runtime; new databases are created only from MIGRATION_PATH below.  The
# physical append-only schema is unchanged from v4a; the runtime/data identity
# is new so five-family evidence never mixes into the preserved v4a DB.
LEGACY_V3_SCHEMA_REFERENCE = """
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
    eligible_outcome_count INTEGER NOT NULL,
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
    group_item_title TEXT,
    sports_market_type TEXT,
    sport_family TEXT,
    league_code TEXT,
    league_name TEXT,
    series_slug TEXT,
    event_tag_slugs_json TEXT NOT NULL,
    team_leagues_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    end_date TEXT,
    game_start_time TEXT,
    hours_until_end REAL,
    sports_phase TEXT NOT NULL,
    event_live INTEGER,
    event_ended INTEGER,
    event_game_status TEXT,
    liquidity REAL,
    volume_total REAL,
    active INTEGER,
    closed INTEGER,
    accepting_orders INTEGER,
    enable_order_book INTEGER,
    neg_risk INTEGER,
    match_winner_class TEXT NOT NULL,
    eligible_outcome_indices_json TEXT NOT NULL,
    classification_evidence_json TEXT NOT NULL,
    cadence_arm TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS market_league_time_idx ON market_observations(league_code, observed_at);

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
    entry_eligible INTEGER NOT NULL CHECK (entry_eligible IN (0,1)),
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
    prior_entry_vwap REAL,
    entry_provenance TEXT,
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
    cadence_arm TEXT NOT NULL,
    match_winner_class TEXT NOT NULL,
    sport_family TEXT NOT NULL,
    league_code TEXT NOT NULL,
    league_name TEXT NOT NULL,
    series_slug TEXT,
    entry_provenance TEXT NOT NULL,
    entered_at TEXT NOT NULL,
    end_date TEXT NOT NULL,
    game_start_time TEXT,
    sports_phase TEXT NOT NULL,
    liquidity REAL,
    volume_total REAL,
    fee_rate REAL NOT NULL,
    entry_best_ask REAL NOT NULL,
    entry_vwap REAL NOT NULL,
    entry_shares REAL NOT NULL,
    entry_cost REAL NOT NULL,
    UNIQUE (condition_id, token_id, threshold)
);
CREATE INDEX IF NOT EXISTS episodes_threshold_time_idx ON hypothetical_episodes(threshold, entered_at);
CREATE INDEX IF NOT EXISTS episodes_league_time_idx ON hypothetical_episodes(league_code, entered_at);

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

CREATE TABLE IF NOT EXISTS database_checks (
    check_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    check_type TEXT NOT NULL CHECK (check_type = 'QUICK_CHECK'),
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    elapsed_ms REAL NOT NULL,
    result TEXT NOT NULL,
    db_bytes INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS database_check_time_idx
ON database_checks(check_type, completed_at);
"""

MIGRATION_PATH = Path(__file__).with_name("migrations") / "0002_watermelon_major_sports_v4a.sql"
APPLICATION_ID = 1196903732  # ASCII "GWM4"
SCHEMA_USER_VERSION = 401
EXPECTED_SCHEMA_SHA256 = "70baef885a69b0200bb11c8325530cc88a49be2f1b78e27fb046c097a1716e32"

APPEND_ONLY_TABLES = (
    "schema_metadata", "league_registry_versions", "research_config_versions",
    "research_run_events", "api_requests", "raw_payloads",
    "market_sweeps", "event_observations", "market_observations", "outcome_observations",
    "orderbook_token_attempts", "orderbook_snapshots", "orderbook_levels",
    "signal_decisions", "hypothetical_episodes", "episode_path_observations",
    "counterfactual_exit_policies", "stop_execution_attempts",
    "counterfactual_stop_exits",
    "resolution_attempts", "resolution_observations", "data_quality_issues",
    "storage_metrics", "database_checks",
)

FULL_QUICK_CHECK_INTERVAL = timedelta(hours=24)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _migration_sha256() -> str:
    return hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest()


def _schema_sha256(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT type,name,tbl_name,sql
        FROM sqlite_master
        WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
        ORDER BY type,name,tbl_name
        """
    ).fetchall()
    payload = [tuple(str(value) for value in row) for row in rows]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class ResearchRepository:
    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_ms: int,
        data_contract: str,
        schema_profile: str,
        universe_profile: str,
        classifier_version: str,
        league_mapping_sha256: str,
        league_mapping_json: str,
    ) -> None:
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms
        self.data_contract = data_contract
        self.schema_profile = schema_profile
        self.universe_profile = universe_profile
        self.classifier_version = classifier_version
        self.league_mapping_sha256 = league_mapping_sha256
        try:
            mapping_payload = json.loads(league_mapping_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("league mapping must be valid JSON") from error
        if not isinstance(mapping_payload, dict):
            raise ValueError("league mapping JSON must be an object")
        if "classifier_version" in mapping_payload:
            raise ValueError("league mapping JSON contains a reserved key")
        self.league_mapping_json = json.dumps(
            mapping_payload, sort_keys=True, separators=(",", ":")
        )
        mapping_digest = hashlib.sha256(
            json.dumps(
                {**mapping_payload, "classifier_version": classifier_version},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if mapping_digest != league_mapping_sha256:
            raise ValueError("league mapping JSON does not match league_mapping_sha256")
        if path.is_symlink():
            raise RuntimeError("database path cannot be a symlink")
        if path.exists():
            if not path.is_file():
                raise RuntimeError("database path must be a regular file")
            self._validate_existing_read_only()
        else:
            self._bootstrap_new_database()
            self._validate_existing_read_only()

    def _expected_metadata(self) -> tuple[object, ...]:
        return (
            1,
            self.data_contract,
            self.schema_profile,
            self.universe_profile,
            self.classifier_version,
            self.league_mapping_sha256,
            _migration_sha256(),
        )

    def _bootstrap_new_database(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("xb"):
                pass
        except FileExistsError:
            self._validate_existing_read_only()
            return
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
            for table in APPEND_ONLY_TABLES:
                for operation in ("UPDATE", "DELETE"):
                    trigger = f"{table}_forbid_{operation.lower()}"
                    connection.execute(
                        f"CREATE TRIGGER {trigger} BEFORE {operation} ON {table} "
                        "BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"
                    )
            schema_sha256 = _schema_sha256(connection)
            if schema_sha256 != EXPECTED_SCHEMA_SHA256:
                raise RuntimeError("migration produced an unexpected v3a schema fingerprint")
            connection.execute(
                """
                INSERT INTO schema_metadata(
                    singleton,data_contract,schema_profile,universe_profile,
                    classifier_version,league_mapping_sha256,migration_sha256,
                    schema_sha256,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (*self._expected_metadata(), EXPECTED_SCHEMA_SHA256, _now()),
            )
            connection.execute(
                """
                INSERT INTO league_registry_versions(
                    league_mapping_sha256,classifier_version,universe_profile,
                    mapping_json,first_seen_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    self.league_mapping_sha256,
                    self.classifier_version,
                    self.universe_profile,
                    self.league_mapping_json,
                    _now(),
                ),
            )
            result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            if result != "ok":
                raise RuntimeError(f"new SQLite bootstrap quick_check failed: {result}")
            connection.commit()
        except BaseException:
            if connection is not None:
                connection.rollback()
                connection.close()
                connection = None
            for candidate in (
                self.path,
                Path(f"{self.path}-wal"),
                Path(f"{self.path}-shm"),
                Path(f"{self.path}-journal"),
            ):
                if candidate.exists():
                    candidate.unlink()
            raise
        finally:
            if connection is not None:
                connection.close()

    def _validate_existing_read_only(self) -> None:
        uri = self.path.resolve().as_uri() + "?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=self.busy_timeout_ms / 1000)
        except sqlite3.Error as error:
            raise RuntimeError("database read-only preflight failed") from error
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if (application_id, user_version) != (APPLICATION_ID, SCHEMA_USER_VERSION):
                raise RuntimeError(
                    "database epoch mismatch before writable open: "
                    f"application_id={application_id}, user_version={user_version}"
                )
            rows = connection.execute(
                """
                SELECT singleton,data_contract,schema_profile,universe_profile,
                       classifier_version,league_mapping_sha256,migration_sha256,
                       schema_sha256
                FROM schema_metadata
                """
            ).fetchall()
            if len(rows) != 1:
                raise RuntimeError("database must contain exactly one schema metadata row")
            row = rows[0]
            actual = tuple(row[index] for index in range(7))
            if actual != self._expected_metadata():
                raise RuntimeError(f"database contract/schema/mapping mismatch: {actual}")
            live_schema_sha256 = _schema_sha256(connection)
            if (
                str(row["schema_sha256"]) != EXPECTED_SCHEMA_SHA256
                or live_schema_sha256 != EXPECTED_SCHEMA_SHA256
            ):
                raise RuntimeError("database schema fingerprint mismatch")
            registry_rows = connection.execute(
                """
                SELECT league_mapping_sha256,classifier_version,universe_profile,
                       mapping_json
                FROM league_registry_versions
                """
            ).fetchall()
            expected_registry = (
                self.league_mapping_sha256,
                self.classifier_version,
                self.universe_profile,
                self.league_mapping_json,
            )
            if len(registry_rows) != 1 or tuple(registry_rows[0]) != expected_registry:
                raise RuntimeError("database league mapping registry mismatch")
        except sqlite3.Error as error:
            raise RuntimeError("database schema preflight failed before writable open") from error
        finally:
            connection.close()

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

    def record_league_registry(self, row: Mapping[str, Any]) -> None:
        with self.connect() as c:
            existing = c.execute(
                """
                SELECT league_mapping_sha256,classifier_version,universe_profile,
                       mapping_json
                FROM league_registry_versions
                """
            ).fetchall()
            expected = (
                self.league_mapping_sha256,
                self.classifier_version,
                self.universe_profile,
                self.league_mapping_json,
            )
            if len(existing) != 1 or tuple(existing[0]) != expected:
                raise RuntimeError("league mapping registry changed after preflight")
            supplied = (
                row.get("league_mapping_sha256"),
                row.get("classifier_version"),
                row.get("universe_profile"),
                json.dumps(
                    json.loads(str(row.get("mapping_json"))),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            if supplied != expected:
                raise RuntimeError("runtime league mapping conflicts with frozen registry")

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
        events: Iterable[Mapping[str, Any]],
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
            self._insert_many(c, "event_observations", events)
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

    def latest_entry_vwaps(self) -> dict[str, float]:
        """Return the last full-depth $5 ask VWAP observed for each token."""
        with self.connect() as c:
            rows = c.execute(
                """
                SELECT d.token_id,d.entry_vwap
                FROM signal_decisions d
                JOIN (
                    SELECT token_id,MAX(decided_at) AS latest_at
                    FROM signal_decisions
                    WHERE entry_vwap IS NOT NULL
                    GROUP BY token_id
                ) latest
                  ON latest.token_id=d.token_id
                 AND latest.latest_at=d.decided_at
                WHERE d.entry_vwap IS NOT NULL
                GROUP BY d.token_id
                """
            ).fetchall()
        return {str(row[0]): float(row[1]) for row in rows}

    def open_episodes(self) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute(
                """
                SELECT e.* FROM hypothetical_episodes e
                LEFT JOIN resolution_observations r ON r.condition_id=e.condition_id
                LEFT JOIN (
                    SELECT DISTINCT condition_id FROM resolution_attempts
                    WHERE status='RESOLVED_VOID'
                ) v ON v.condition_id=e.condition_id
                WHERE r.condition_id IS NULL AND v.condition_id IS NULL
                ORDER BY e.entered_at
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
                LEFT JOIN (
                    SELECT DISTINCT condition_id FROM resolution_attempts
                    WHERE status='RESOLVED_VOID'
                ) v ON v.condition_id=e.condition_id
                WHERE p.stop_price IS NOT NULL
                  AND x.policy_id IS NULL
                  AND r.condition_id IS NULL
                  AND v.condition_id IS NULL
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

    def scheduled_database_check(
        self,
        run_id: str,
        *,
        now: datetime | None = None,
        interval: timedelta = FULL_QUICK_CHECK_INTERVAL,
    ) -> dict[str, Any]:
        """Run a cheap probe every cycle and a full quick_check at most daily.

        A full ``PRAGMA quick_check`` scans the entire append-only research DB.
        Running it every five minutes eventually consumes the whole cadence as
        the DB grows.  The lightweight probe still fails closed on unreadable
        schema/contract pages, while explicit ``health`` and daily-rsync
        verification continue to run an unconditional full check.
        """
        if interval.total_seconds() <= 0:
            raise ValueError("database check interval must be positive")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self.connect() as connection:
            actual = connection.execute(
                "SELECT data_contract FROM schema_metadata"
            ).fetchone()
            if actual is None or str(actual[0]) != self.data_contract:
                raise RuntimeError(f"database contract mismatch: {actual}")
            schema_version = int(
                connection.execute("PRAGMA schema_version").fetchone()[0]
            )
            connection.execute(
                "SELECT event_id FROM research_run_events "
                "ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
            prior = connection.execute(
                "SELECT completed_at FROM database_checks "
                "WHERE check_type='QUICK_CHECK' AND result='ok' "
                "ORDER BY completed_at DESC LIMIT 1"
            ).fetchone()

        prior_at = None
        if prior is not None:
            prior_at = datetime.fromisoformat(
                str(prior[0]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        if prior_at is not None and current - prior_at < interval:
            return {
                "mode": "LIGHTWEIGHT_PROBE",
                "result": "ok",
                "full_check_performed": False,
                "schema_version": schema_version,
                "last_full_check_at": prior_at.isoformat().replace("+00:00", "Z"),
                "next_full_check_at": (prior_at + interval).isoformat().replace(
                    "+00:00", "Z"
                ),
            }

        started_at = current.isoformat().replace("+00:00", "Z")
        started_clock = time.monotonic()
        failure: BaseException | None = None
        try:
            result = self.quick_check()
        except BaseException as error:
            failure = error
            result = f"ERROR:{type(error).__name__}"
        elapsed_ms = round((time.monotonic() - started_clock) * 1000, 3)
        completed = current + timedelta(milliseconds=elapsed_ms)
        completed_at = completed.isoformat().replace("+00:00", "Z")
        with self.connect() as connection:
            self._insert(
                connection,
                "database_checks",
                {
                    "check_id": uuid4().hex,
                    "run_id": run_id,
                    "check_type": "QUICK_CHECK",
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "elapsed_ms": elapsed_ms,
                    "result": result,
                    "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
                },
            )
        if failure is not None:
            raise RuntimeError("SQLite quick_check failed") from failure
        if result != "ok":
            raise RuntimeError(f"SQLite quick_check failed: {result}")
        return {
            "mode": "FULL_QUICK_CHECK",
            "result": result,
            "full_check_performed": True,
            "schema_version": schema_version,
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_ms": elapsed_ms,
            "next_full_check_at": (completed + interval).isoformat().replace(
                "+00:00", "Z"
            ),
        }

    def summary(self) -> dict[str, Any]:
        with self.connect() as c:
            metadata = c.execute(
                "SELECT * FROM schema_metadata WHERE singleton=1"
            ).fetchone()
            result = {
                "quick_check": c.execute("PRAGMA quick_check").fetchone()[0],
                "schema_metadata": dict(metadata) if metadata is not None else None,
                "runs": c.execute("SELECT COUNT(DISTINCT run_id) FROM research_run_events WHERE event_type='SUCCEEDED'").fetchone()[0],
                "sweeps": c.execute("SELECT COUNT(*) FROM market_sweeps").fetchone()[0],
                "events": c.execute("SELECT COUNT(*) FROM event_observations").fetchone()[0],
                "episodes": c.execute("SELECT COUNT(*) FROM hypothetical_episodes").fetchone()[0],
                "exit_policies": c.execute("SELECT COUNT(*) FROM counterfactual_exit_policies").fetchone()[0],
                "stop_attempts": c.execute("SELECT COUNT(*) FROM stop_execution_attempts").fetchone()[0],
                "stop_exits": c.execute("SELECT COUNT(*) FROM counterfactual_stop_exits").fetchone()[0],
                "resolutions": c.execute("SELECT COUNT(*) FROM resolution_observations").fetchone()[0],
                "void_resolutions": c.execute(
                    "SELECT COUNT(DISTINCT condition_id) FROM resolution_attempts "
                    "WHERE status='RESOLVED_VOID'"
                ).fetchone()[0],
                "issues": c.execute("SELECT COUNT(*) FROM data_quality_issues").fetchone()[0],
                "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
            }
            result["event_classification"] = {
                str(row[0]): int(row[1])
                for row in c.execute(
                    "SELECT classification_status,COUNT(*) FROM event_observations "
                    "GROUP BY classification_status ORDER BY classification_status"
                )
            }
            result["arms"] = {
                str(row[0]): {"episodes": row[1], "resolved": row[2]}
                for row in c.execute(
                    """
                    SELECT e.threshold,COUNT(*),
                           SUM(CASE WHEN r.condition_id IS NOT NULL
                                          OR v.condition_id IS NOT NULL
                                    THEN 1 ELSE 0 END)
                    FROM hypothetical_episodes e
                    LEFT JOIN resolution_observations r USING(condition_id)
                    LEFT JOIN (
                        SELECT DISTINCT condition_id FROM resolution_attempts
                        WHERE status='RESOLVED_VOID'
                    ) v USING(condition_id)
                    GROUP BY e.threshold ORDER BY e.threshold
                    """
                )
            }
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
