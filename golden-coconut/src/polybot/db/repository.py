"""Create-only, append-only UTC daily SQLite shard repository."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import quote
from uuid import uuid4

from ..api.transport import canonical_json, iso_utc
from ..config import BotConfig, StorageConfig
from ..registry import FAMILY_ORDER


MIGRATION_PATH = (
    Path(__file__).with_name("migrations") / "0004_major_sports_lifecycle_v4.sql"
)
APPLICATION_ID = 1195593521
SCHEMA_USER_VERSION = 4
GIB = 1024**3

APPEND_ONLY_TABLES = (
    "collection_contracts",
    "schema_metadata",
    "sports_registry_versions",
    "research_config_versions",
    "research_run_events",
    "slot_claims",
    "collection_cycles",
    "sport_sweeps",
    "api_requests",
    "raw_payloads",
    "event_observations",
    "game_lifecycle_observations",
    "tracked_game_carryovers",
    "schedule_revision_observations",
    "event_tag_observations",
    "event_series_observations",
    "event_team_observations",
    "market_observations",
    "outcome_observations",
    "book_token_attempts",
    "book_snapshots",
    "book_ladder_observations",
    "threshold_vectors",
    "threshold_state_carryovers",
    "threshold_episodes",
    "episode_carryovers",
    "episode_path_observations",
    "game_anchor_observations",
    "resolution_attempts",
    "resolution_observations",
    "sports_clock_observations",
    "data_quality_issues",
    "storage_metrics",
    "database_checks",
)

BUNDLE_TABLE_ORDER = (
    ("sweeps", "sport_sweeps"),
    ("raw_payloads", "raw_payloads"),
    ("events", "event_observations"),
    ("game_lifecycle", "game_lifecycle_observations"),
    ("schedule_revisions", "schedule_revision_observations"),
    ("tags", "event_tag_observations"),
    ("series", "event_series_observations"),
    ("teams", "event_team_observations"),
    ("markets", "market_observations"),
    ("outcomes", "outcome_observations"),
    ("book_attempts", "book_token_attempts"),
    ("book_snapshots", "book_snapshots"),
    ("book_ladder", "book_ladder_observations"),
    ("threshold_vectors", "threshold_vectors"),
    ("episodes", "threshold_episodes"),
    ("paths", "episode_path_observations"),
    ("anchors", "game_anchor_observations"),
    ("resolution_attempts", "resolution_attempts"),
    ("resolutions", "resolution_observations"),
    ("sports_clock", "sports_clock_observations"),
    ("quality_issues", "data_quality_issues"),
    ("storage_metrics", "storage_metrics"),
    ("database_checks", "database_checks"),
)


class SlotAlreadyClaimed(RuntimeError):
    """The exact UTC five-minute collection slot already has an owner."""


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
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _append_only_triggers(connection: sqlite3.Connection) -> None:
    for table in APPEND_ONLY_TABLES:
        for operation in ("UPDATE", "DELETE"):
            trigger = f"{table}_forbid_{operation.casefold()}"
            connection.execute(
                f"CREATE TRIGGER {trigger} BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"
            )


def slot_start_utc(value: datetime, cadence_minutes: int = 5) -> datetime:
    if cadence_minutes != 5:
        raise ValueError("Golden Coconut slot cadence must be five minutes")
    current = value.astimezone(timezone.utc)
    minute = current.minute - current.minute % cadence_minutes
    return current.replace(minute=minute, second=0, microsecond=0)


def classify_storage_guard(
    *,
    total_bytes: int,
    used_bytes: int,
    free_bytes: int,
    min_free_gib: float,
    warn_used_ratio: float,
    stop_used_ratio: float,
) -> tuple[str, float]:
    used_ratio = used_bytes / total_bytes if total_bytes else 1.0
    if free_bytes < min_free_gib * GIB or used_ratio >= stop_used_ratio:
        return "STOP", used_ratio
    if used_ratio >= warn_used_ratio:
        return "WARN", used_ratio
    return "OK", used_ratio


def inspect_storage(
    path: Path,
    storage: StorageConfig,
    *,
    disk_usage: Callable[[Path], Any] = shutil.disk_usage,
) -> dict[str, Any]:
    ancestor = path.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    usage = disk_usage(ancestor)
    state, used_ratio = classify_storage_guard(
        total_bytes=int(usage.total),
        used_bytes=int(usage.used),
        free_bytes=int(usage.free),
        min_free_gib=storage.min_free_gib,
        warn_used_ratio=storage.warn_used_ratio,
        stop_used_ratio=storage.stop_used_ratio,
    )
    wal_path = Path(f"{path}-wal")
    return {
        "database_bytes": path.stat().st_size if path.is_file() else 0,
        "wal_bytes": wal_path.stat().st_size if wal_path.is_file() else 0,
        "filesystem_total_bytes": int(usage.total),
        "filesystem_used_bytes": int(usage.used),
        "filesystem_free_bytes": int(usage.free),
        "filesystem_used_ratio": used_ratio,
        "guard_state": state,
    }


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ResearchRepository:
    def __init__(
        self,
        config: BotConfig,
        *,
        database_utc_date: str,
        path: Path | None = None,
        create: bool = True,
    ) -> None:
        self.config = config
        self.path = path or config.db_path
        self.database_utc_date = database_utc_date
        self.busy_timeout_ms = config.trading.storage.busy_timeout_ms
        self.registry_json = config.registry.canonical_json()
        if self.path.is_symlink():
            raise RuntimeError("database path cannot be a symlink")
        if self.path.exists():
            if not self.path.is_file():
                raise RuntimeError("database path must be a regular file")
            self._validate_existing_read_only()
        elif create:
            self._bootstrap_new_database()
            self._validate_existing_read_only()
        else:
            raise FileNotFoundError(self.path)

    @classmethod
    def prepare(
        cls,
        config: BotConfig,
        *,
        now: datetime | None = None,
    ) -> "ResearchRepository":
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        current_date = current.date().isoformat()
        path = config.db_path
        if not path.exists():
            archives = sorted(path.parent.glob("trades_sim_????????.db")) if path.parent.exists() else []
            if archives:
                raise RuntimeError(
                    "active database is missing while daily archives exist; refusing a silent empty lineage"
                )
            return cls(config, database_utc_date=current_date)
        old_date = cls._peek_database_date(path)
        if old_date > current_date:
            raise RuntimeError("active database UTC date is in the future")
        if old_date != current_date:
            cls._rotate(config, old_date=old_date, new_date=current_date)
        return cls(config, database_utc_date=current_date)

    @staticmethod
    def _peek_database_date(path: Path) -> str:
        uri = f"file:{quote(str(path.resolve()))}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            row = connection.execute(
                "SELECT database_utc_date FROM schema_metadata WHERE singleton=1"
            ).fetchone()
        except sqlite3.Error as error:
            raise RuntimeError("cannot read active database UTC shard date") from error
        finally:
            connection.close()
        if row is None:
            raise RuntimeError("active database has no UTC shard date")
        return str(row[0])

    def _metadata_values(self, schema_sha256: str) -> tuple[Any, ...]:
        trading = self.config.trading
        return (
            1,
            self.database_utc_date,
            trading.data_contract,
            trading.collection_contract,
            trading.schema_profile,
            trading.universe_profile,
            trading.classifier_version,
            trading.sports_registry_sha256,
            _migration_sha256(),
            schema_sha256,
            iso_utc(),
        )

    def _bootstrap_new_database(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("xb"):
                pass
        except FileExistsError:
            return
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.path, timeout=self.busy_timeout_ms / 1000
            )
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
            _append_only_triggers(connection)
            schema_sha256 = _schema_sha256(connection)
            connection.execute(
                "INSERT INTO collection_contracts VALUES(?,?,?)",
                (
                    1,
                    self.config.trading.collection_contract,
                    self.database_utc_date,
                ),
            )
            connection.execute(
                "INSERT INTO schema_metadata VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                self._metadata_values(schema_sha256),
            )
            connection.execute(
                "INSERT INTO sports_registry_versions VALUES(?,?,?,?,?)",
                (
                    self.config.trading.sports_registry_sha256,
                    self.config.trading.universe_profile,
                    self.config.trading.classifier_version,
                    self.registry_json,
                    iso_utc(),
                ),
            )
            quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            if quick != "ok":
                raise RuntimeError(f"new database quick_check failed: {quick}")
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
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
            raise
        finally:
            if connection is not None:
                connection.close()

    def _validate_existing_read_only(self) -> None:
        uri = f"file:{quote(str(self.path.resolve()))}?mode=ro"
        try:
            connection = sqlite3.connect(
                uri, uri=True, timeout=self.busy_timeout_ms / 1000
            )
        except sqlite3.Error as error:
            raise RuntimeError("database read-only preflight failed") from error
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            app_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if (app_id, user_version) != (APPLICATION_ID, SCHEMA_USER_VERSION):
                raise RuntimeError("database application/user version epoch mismatch")
            rows = connection.execute("SELECT * FROM schema_metadata").fetchall()
            if len(rows) != 1:
                raise RuntimeError("database must contain exactly one schema_metadata row")
            row = rows[0]
            expected = {
                "database_utc_date": self.database_utc_date,
                "data_contract": self.config.trading.data_contract,
                "collection_contract": self.config.trading.collection_contract,
                "schema_profile": self.config.trading.schema_profile,
                "universe_profile": self.config.trading.universe_profile,
                "classifier_version": self.config.trading.classifier_version,
                "sports_registry_sha256": self.config.trading.sports_registry_sha256,
                "migration_sha256": _migration_sha256(),
            }
            actual = {key: str(row[key]) for key in expected}
            if actual != expected:
                raise RuntimeError(f"database contract mismatch: {actual!r}")
            contracts = connection.execute(
                "SELECT * FROM collection_contracts"
            ).fetchall()
            if len(contracts) != 1:
                raise RuntimeError("database must contain one collection contract")
            contract = contracts[0]
            if (
                int(contract["singleton"]) != 1
                or str(contract["contract_name"])
                != self.config.trading.collection_contract
                or str(contract["database_utc_date"]) != self.database_utc_date
            ):
                raise RuntimeError("daily-rsync collection contract differs")
            if str(row["schema_sha256"]) != _schema_sha256(connection):
                raise RuntimeError("database live schema fingerprint changed")
            registry = connection.execute(
                "SELECT * FROM sports_registry_versions"
            ).fetchall()
            if len(registry) != 1:
                raise RuntimeError("database must contain one frozen sports registry")
            registry_row = registry[0]
            if (
                str(registry_row["sports_registry_sha256"])
                != self.config.trading.sports_registry_sha256
                or str(registry_row["universe_profile"])
                != self.config.trading.universe_profile
                or str(registry_row["classifier_version"])
                != self.config.trading.classifier_version
                or str(registry_row["registry_json"]) != self.registry_json
            ):
                raise RuntimeError("database frozen sports registry differs")
            trigger_names = {
                str(item[0])
                for item in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            expected_triggers = {
                f"{table}_forbid_{operation}"
                for table in APPEND_ONLY_TABLES
                for operation in ("update", "delete")
            }
            if trigger_names != expected_triggers:
                raise RuntimeError("append-only trigger set differs from schema contract")
        except sqlite3.Error as error:
            raise RuntimeError("database schema preflight failed") from error
        finally:
            connection.close()

    @contextmanager
    def write_connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.path, timeout=self.busy_timeout_ms / 1000
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def read_connect(self, *, immutable: bool = False) -> Iterator[sqlite3.Connection]:
        suffix = "&immutable=1" if immutable else ""
        uri = f"file:{quote(str(self.path.resolve()))}?mode=ro{suffix}"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _insert(
        connection: sqlite3.Connection, table: str, row: Mapping[str, Any]
    ) -> None:
        keys = tuple(row)
        placeholders = ",".join("?" for _ in keys)
        connection.execute(
            f"INSERT INTO {table}({','.join(keys)}) VALUES({placeholders})",
            tuple(row[key] for key in keys),
        )

    @classmethod
    def _insert_many(
        cls,
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

    def register_config(self) -> None:
        payload = canonical_json(self.config.redacted_dict())
        row = {
            "config_hash": self.config.config_hash,
            "strategy_source_digest": self.config.trading.strategy_source_digest,
            "preregistration_sha256": self.config.trading.preregistration_sha256,
            "sports_registry_sha256": self.config.trading.sports_registry_sha256,
            "job_name": self.config.job_name,
            "mode": self.config.mode,
            "lifecycle_mode": self.config.trading.lifecycle_mode,
            "config_json": payload,
            "first_seen_at": iso_utc(),
        }
        with self.write_connect() as connection:
            existing = connection.execute(
                "SELECT * FROM research_config_versions WHERE config_hash=?",
                (self.config.config_hash,),
            ).fetchone()
            if existing is None:
                self._insert(connection, "research_config_versions", row)
                connection.commit()
            elif any(str(existing[key]) != str(row[key]) for key in row if key != "first_seen_at"):
                raise RuntimeError("config hash collision or immutable config drift")

    def record_run_event(self, row: Mapping[str, Any]) -> None:
        with self.write_connect() as connection:
            self._insert(connection, "research_run_events", row)
            connection.commit()

    def record_api_request(self, row: Mapping[str, Any]) -> None:
        with self.write_connect() as connection:
            self._insert(connection, "api_requests", row)
            connection.commit()

    def claim_slot(self, *, run_id: str, now: datetime) -> str:
        slot = slot_start_utc(now, self.config.trading.cadence_minutes)
        row = {
            "slot_claim_id": uuid4().hex,
            "slot_start_utc": iso_utc(slot),
            "cadence_minutes": self.config.trading.cadence_minutes,
            "run_id": run_id,
            "job_name": self.config.job_name,
            "claimed_at": iso_utc(),
        }
        with self.write_connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._insert(connection, "slot_claims", row)
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise SlotAlreadyClaimed(row["slot_start_utc"]) from error
        return str(row["slot_start_utc"])

    @staticmethod
    def raw_payload_row(
        *,
        cycle_id: str,
        run_id: str,
        payload_kind: str,
        sport_family: str | None,
        logical_request_id: str | None,
        observed_at: str,
        raw: bytes,
    ) -> dict[str, Any]:
        compressed = gzip.compress(raw, compresslevel=6, mtime=0)
        return {
            "raw_payload_id": uuid4().hex,
            "cycle_id": cycle_id,
            "run_id": run_id,
            "payload_kind": payload_kind,
            "sport_family": sport_family,
            "logical_request_id": logical_request_id,
            "observed_at": observed_at,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "raw_bytes": len(raw),
            "gzip_bytes": len(compressed),
            "payload_gzip": compressed,
        }

    @staticmethod
    def _validate_bundle(bundle: Mapping[str, Any]) -> None:
        cycle = bundle.get("cycle")
        if not isinstance(cycle, Mapping):
            raise ValueError("collection bundle has no cycle row")
        sweeps = list(bundle.get("sweeps", ()))
        families = [str(row.get("sport_family")) for row in sweeps]
        if tuple(sorted(families)) != tuple(sorted(FAMILY_ORDER)) or len(families) != len(set(families)):
            raise ValueError("collection bundle must contain exactly five independent family sweeps")
        tokens = [
            str(row.get("token_id")) for row in bundle.get("book_snapshots", ())
        ]
        if len(tokens) != len(set(tokens)):
            raise ValueError("book_snapshots must contain one canonical row per token")
        vector_keys = [
            (str(row.get("token_id")), float(row.get("notional_usdc")))
            for row in bundle.get("threshold_vectors", ())
        ]
        if len(vector_keys) != len(set(vector_keys)):
            raise ValueError(
                "threshold_vectors must contain one row per token/notional per cycle"
            )
        snapshot_ids = [str(row.get("book_snapshot_id")) for row in bundle.get("book_snapshots", ())]
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("book snapshots contain duplicate primary evidence")
        if bool(cycle.get("all_families_cursor_complete")) != all(
            bool(row.get("cursor_complete")) for row in sweeps
        ):
            raise ValueError("cycle and family cursor-completion facts disagree")

    def _before_publish_commit(
        self, connection: sqlite3.Connection, bundle: Mapping[str, Any]
    ) -> None:
        """Test seam at the all-evidence pre-commit boundary."""

    def publish_cycle(
        self,
        bundle: Mapping[str, Any],
        *,
        terminal_event: Mapping[str, Any],
    ) -> None:
        self._validate_bundle(bundle)
        with self.write_connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._insert(connection, "collection_cycles", bundle["cycle"])
                for bundle_key, table in BUNDLE_TABLE_ORDER:
                    self._insert_many(connection, table, bundle.get(bundle_key, ()))
                self._insert(connection, "research_run_events", terminal_event)
                self._before_publish_commit(connection, bundle)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def latest_threshold_states(self) -> dict[tuple[str, float], dict[str, Any]]:
        with self.read_connect() as connection:
            vectors = connection.execute(
                """
                WITH ranked AS (
                    SELECT v.*,ROW_NUMBER() OVER (
                        PARTITION BY token_id,notional_usdc
                        ORDER BY observed_at DESC,threshold_vector_id DESC
                    ) AS position
                    FROM threshold_vectors v
                )
                SELECT * FROM ranked WHERE position=1
                """
            ).fetchall()
            carries = connection.execute(
                "SELECT * FROM threshold_state_carryovers"
            ).fetchall()
        result = {
            (str(row["token_id"]), float(row["notional_usdc"])): dict(row)
            for row in carries
        }
        result.update(
            {
                (str(row["token_id"]), float(row["notional_usdc"])): dict(row)
                for row in vectors
            }
        )
        return result

    def existing_episode_keys(self) -> set[tuple[str, str, float, float]]:
        with self.read_connect() as connection:
            rows = connection.execute(
                """
                SELECT condition_id,token_id,notional_usdc,threshold FROM threshold_episodes
                UNION ALL
                SELECT condition_id,token_id,notional_usdc,threshold FROM episode_carryovers
                """
            ).fetchall()
        return {
            (str(row[0]), str(row[1]), float(row[2]), float(row[3]))
            for row in rows
        }

    def open_episodes(self) -> list[dict[str, Any]]:
        with self.read_connect() as connection:
            rows = connection.execute(
                """
                WITH all_episode_rows AS (
                    SELECT episode_id,origin_utc_date,created_run_id,sport_family,
                           season_phase,lifecycle_state,competition_code,event_id,event_cluster_id,
                           condition_id,token_id,outcome_index,outcome_label,notional_usdc,threshold,
                           crossed_at,entry_ask_vwap,entry_shares,liquidity,
                           volume_num,volume_24hr
                    FROM threshold_episodes
                    UNION ALL
                    SELECT episode_id,origin_utc_date,created_run_id,sport_family,
                           season_phase,lifecycle_state,competition_code,event_id,event_cluster_id,
                           condition_id,token_id,outcome_index,outcome_label,notional_usdc,threshold,
                           crossed_at,entry_ask_vwap,entry_shares,liquidity,
                           volume_num,volume_24hr
                    FROM episode_carryovers
                )
                SELECT e.* FROM all_episode_rows e
                WHERE NOT EXISTS (
                    SELECT 1 FROM resolution_observations r
                    WHERE r.condition_id=e.condition_id
                      AND r.resolution_status IN ('RESOLVED','VOID','TIE')
                )
                ORDER BY e.crossed_at,e.episode_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_game_states(self) -> dict[str, dict[str, Any]]:
        with self.read_connect() as connection:
            rows = connection.execute(
                """
                WITH history AS (
                    SELECT sport_family,event_id,canonical_game_slug,game_id_alias,
                           event_cluster_id,lifecycle_state,scheduled_start_field,
                           scheduled_start_raw,scheduled_start_utc,observed_at,
                           game_lifecycle_observation_id AS identity
                    FROM game_lifecycle_observations
                    UNION ALL
                    SELECT sport_family,event_id,canonical_game_slug,game_id_alias,
                           event_cluster_id,lifecycle_state,scheduled_start_field,
                           scheduled_start_raw,scheduled_start_utc,carried_at AS observed_at,
                           tracked_game_carryover_id AS identity
                    FROM tracked_game_carryovers
                ), ranked AS (
                    SELECT history.*,ROW_NUMBER() OVER (
                        PARTITION BY event_cluster_id
                        ORDER BY observed_at DESC,identity DESC
                    ) AS position
                    FROM history
                )
                SELECT * FROM ranked WHERE position=1
                """
            ).fetchall()
        return {str(row["event_cluster_id"]): dict(row) for row in rows}

    def tracked_games(self) -> list[dict[str, Any]]:
        terminal = {"CANCELLED", "RESOLVED", "VOID", "TIE"}
        return [
            row
            for row in self.latest_game_states().values()
            if str(row["lifecycle_state"]) not in terminal
        ]

    def latest_resolution_statuses(self) -> dict[str, str]:
        with self.read_connect() as connection:
            rows = connection.execute(
                """
                WITH ranked AS (
                    SELECT condition_id,resolution_status,ROW_NUMBER() OVER (
                        PARTITION BY condition_id
                        ORDER BY observed_at DESC,resolution_observation_id DESC
                    ) AS position
                    FROM resolution_observations
                )
                SELECT condition_id,resolution_status FROM ranked WHERE position=1
                """
            ).fetchall()
        return {str(row[0]): str(row[1]) for row in rows}

    def resolution_due(
        self, condition_id: str, *, now: datetime, interval_minutes: int
    ) -> bool:
        with self.read_connect() as connection:
            row = connection.execute(
                "SELECT MAX(attempted_at) FROM resolution_attempts WHERE condition_id=?",
                (condition_id,),
            ).fetchone()
        if row is None or row[0] is None:
            return True
        prior = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        if prior.tzinfo is None:
            prior = prior.replace(tzinfo=timezone.utc)
        return (
            now.astimezone(timezone.utc) - prior.astimezone(timezone.utc)
        ).total_seconds() >= interval_minutes * 60

    def quick_check(self) -> str:
        with self.read_connect() as connection:
            return str(connection.execute("PRAGMA quick_check").fetchone()[0])

    def summary(self) -> dict[str, Any]:
        with self.read_connect() as connection:
            counts = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in (
                    "research_run_events", "collection_cycles", "sport_sweeps",
                    "event_observations", "game_lifecycle_observations",
                    "schedule_revision_observations", "market_observations", "book_snapshots",
                    "threshold_vectors", "threshold_episodes", "episode_path_observations",
                    "game_anchor_observations", "resolution_observations",
                    "data_quality_issues",
                )
            }
            metadata = dict(connection.execute("SELECT * FROM schema_metadata").fetchone())
            family_rows = connection.execute(
                """
                SELECT sport_family,COUNT(*) AS sweeps,SUM(cursor_complete) AS complete,
                       SUM(source_event_count) AS events
                FROM sport_sweeps GROUP BY sport_family ORDER BY sport_family
                """
            ).fetchall()
        return {
            "database": str(self.path),
            "quick_check": self.quick_check(),
            "metadata": metadata,
            "counts": counts,
            "family_sweeps": [dict(row) for row in family_rows],
        }

    def health(self) -> dict[str, Any]:
        self._validate_existing_read_only()
        storage = inspect_storage(self.path, self.config.trading.storage)
        return {
            "healthy": self.quick_check() == "ok" and storage["guard_state"] != "STOP",
            "quick_check": self.quick_check(),
            "append_only_table_count": len(APPEND_ONLY_TABLES),
            "database": str(self.path),
            "storage": storage,
            "daily_rsync_canonical_filename": self.path.name == "trades_sim.db",
            "daily_rsync_collection_contract": self.config.trading.collection_contract,
        }

    @classmethod
    def _rotation_carries(
        cls, repository: "ResearchRepository", old_date: str
    ) -> tuple[
        list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
    ]:
        now = iso_utc()
        threshold_rows: list[dict[str, Any]] = []
        for (token, notional), row in repository.latest_threshold_states().items():
            prior_payload = {
                "token_id": token,
                "notional_usdc": notional,
                "condition_id": row["condition_id"],
                "sport_family": row["sport_family"],
                "season_phase": row["season_phase"],
                "lifecycle_state": row["lifecycle_state"],
                "event_cluster_id": row["event_cluster_id"],
                "observed_at": row["observed_at"],
                "observation_status": row["observation_status"],
                "executable_ask_vwap": row["executable_ask_vwap"],
                "executable_ask_shares": row["executable_ask_shares"],
            }
            threshold_rows.append(
                {
                    "threshold_state_carryover_id": uuid4().hex,
                    "carried_from_utc_date": old_date,
                    **prior_payload,
                    "prior_vector_sha256": hashlib.sha256(
                        canonical_json(prior_payload).encode("utf-8")
                    ).hexdigest(),
                    "carried_at": now,
                }
            )
        episode_rows = [
            {
                "episode_carryover_id": uuid4().hex,
                "episode_id": row["episode_id"],
                "origin_utc_date": row["origin_utc_date"],
                "carried_from_utc_date": old_date,
                "created_run_id": row["created_run_id"],
                "sport_family": row["sport_family"],
                "season_phase": row["season_phase"],
                "lifecycle_state": row["lifecycle_state"],
                "competition_code": row["competition_code"],
                "event_id": row["event_id"],
                "event_cluster_id": row["event_cluster_id"],
                "condition_id": row["condition_id"],
                "token_id": row["token_id"],
                "outcome_index": row["outcome_index"],
                "outcome_label": row["outcome_label"],
                "notional_usdc": row["notional_usdc"],
                "threshold": row["threshold"],
                "crossed_at": row["crossed_at"],
                "entry_ask_vwap": row["entry_ask_vwap"],
                "entry_shares": row["entry_shares"],
                "liquidity": row["liquidity"],
                "volume_num": row["volume_num"],
                "volume_24hr": row["volume_24hr"],
                "carried_at": now,
            }
            for row in repository.open_episodes()
        ]
        game_rows: list[dict[str, Any]] = []
        for row in repository.tracked_games():
            prior_payload = {
                "sport_family": row["sport_family"],
                "event_id": row["event_id"],
                "canonical_game_slug": row["canonical_game_slug"],
                "game_id_alias": row["game_id_alias"],
                "event_cluster_id": row["event_cluster_id"],
                "lifecycle_state": row["lifecycle_state"],
                "scheduled_start_field": row["scheduled_start_field"],
                "scheduled_start_raw": row["scheduled_start_raw"],
                "scheduled_start_utc": row["scheduled_start_utc"],
            }
            game_rows.append(
                {
                    "tracked_game_carryover_id": uuid4().hex,
                    "carried_from_utc_date": old_date,
                    **prior_payload,
                    "prior_lifecycle_sha256": hashlib.sha256(
                        canonical_json(prior_payload).encode("utf-8")
                    ).hexdigest(),
                    "carried_at": now,
                }
            )
        return threshold_rows, episode_rows, game_rows

    @classmethod
    def _rotate(cls, config: BotConfig, *, old_date: str, new_date: str) -> None:
        path = config.db_path
        old_repository = cls(config, database_utc_date=old_date)
        threshold_carries, episode_carries, game_carries = cls._rotation_carries(
            old_repository, old_date
        )
        if old_repository.quick_check() != "ok":
            raise RuntimeError("refusing to rotate a corrupt active database")
        with old_repository.write_connect() as connection:
            checkpoint = tuple(connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
            if checkpoint != (0, 0, 0):
                raise RuntimeError(f"refusing rotation with incomplete WAL checkpoint: {checkpoint!r}")
        barrier = sqlite3.connect(path, timeout=0, isolation_level=None)
        try:
            barrier.execute("PRAGMA busy_timeout=0")
            mode = str(barrier.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).casefold()
            if mode != "delete":
                raise RuntimeError("rotation could not acquire the SQLite journal namespace")
        finally:
            barrier.close()
        archive = path.with_name(f"trades_sim_{old_date.replace('-', '')}.db")
        interrupted = False
        if archive.exists():
            try:
                interrupted = os.path.samefile(path, archive)
            except OSError:
                interrupted = False
            if not interrupted:
                raise FileExistsError(f"daily shard already exists: {archive}")
        temporary = path.with_name(f".{path.name}.rotate-{new_date}-{uuid4().hex}.tmp")
        try:
            next_repository = cls(
                config, database_utc_date=new_date, path=temporary, create=True
            )
            with next_repository.write_connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cls._insert_many(connection, "threshold_state_carryovers", threshold_carries)
                cls._insert_many(connection, "episode_carryovers", episode_carries)
                cls._insert_many(connection, "tracked_game_carryovers", game_carries)
                connection.commit()
            if next_repository.quick_check() != "ok":
                raise RuntimeError("new UTC shard quick_check failed")
            with next_repository.write_connect() as connection:
                checkpoint = tuple(connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
                if checkpoint != (0, 0, 0):
                    raise RuntimeError("new UTC shard WAL checkpoint failed")
                connection.execute("PRAGMA journal_mode=DELETE")
            _fsync_file(temporary)
            if not interrupted:
                os.link(path, archive)
                _fsync_directory(path.parent)
            os.replace(temporary, path)
            _fsync_directory(path.parent)
        finally:
            for candidate in (
                temporary,
                Path(f"{temporary}-wal"),
                Path(f"{temporary}-shm"),
                Path(f"{temporary}-journal"),
            ):
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass


def storage_metric_row(
    *,
    path: Path,
    storage: StorageConfig,
    phase: str,
    cycle_id: str | None,
    run_id: str | None,
    disk_usage: Callable[[Path], Any] = shutil.disk_usage,
) -> dict[str, Any]:
    metric = inspect_storage(path, storage, disk_usage=disk_usage)
    return {
        "storage_metric_id": uuid4().hex,
        "cycle_id": cycle_id,
        "run_id": run_id,
        "phase": phase,
        "observed_at": iso_utc(),
        **metric,
    }


__all__ = [
    "APPEND_ONLY_TABLES",
    "APPLICATION_ID",
    "BUNDLE_TABLE_ORDER",
    "GIB",
    "MIGRATION_PATH",
    "ResearchRepository",
    "SCHEMA_USER_VERSION",
    "SlotAlreadyClaimed",
    "classify_storage_guard",
    "inspect_storage",
    "slot_start_utc",
    "storage_metric_row",
]
