from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import RemoteArtifact

SCHEMA_VERSION = 1


class Catalog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._ensure_schema()
        self.path.chmod(0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    source TEXT NOT NULL,
                    jenkins_job TEXT NOT NULL,
                    current_strategy TEXT,
                    last_scanned_at TEXT NOT NULL,
                    inventory_json TEXT NOT NULL,
                    PRIMARY KEY(source, jenkins_job)
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    source_key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    jenkins_job TEXT NOT NULL,
                    strategy TEXT,
                    runtime_job TEXT,
                    kind TEXT NOT NULL,
                    build_number INTEGER,
                    remote_path TEXT NOT NULL,
                    remote_size_bytes INTEGER NOT NULL,
                    remote_mtime_ns INTEGER NOT NULL,
                    remote_sha256 TEXT,
                    local_path TEXT,
                    local_sha256 TEXT,
                    status TEXT NOT NULL,
                    synced_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS artifacts_job_strategy_idx
                    ON artifacts(jenkins_job, strategy, kind);

                CREATE TABLE IF NOT EXISTS sync_runs (
                    run_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    jenkins_job TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    transferred INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    bytes_written INTEGER NOT NULL DEFAULT 0,
                    error_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS account_epochs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    jenkins_job TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    account_alias TEXT NOT NULL,
                    first_build INTEGER NOT NULL,
                    last_build INTEGER,
                    UNIQUE(source, jenkins_job, strategy, first_build)
                );

                CREATE TABLE IF NOT EXISTS pins (
                    pin_id TEXT PRIMARY KEY,
                    source_key TEXT NOT NULL REFERENCES artifacts(source_key),
                    pinned_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    manifest_json TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT INTO catalog_meta(key, value) VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

    def artifact_is_current(self, artifact: RemoteArtifact) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT remote_size_bytes, remote_mtime_ns, status, local_path
                FROM artifacts WHERE source_key = ?
                """,
                (artifact.source_key,),
            ).fetchone()
            if row is None or not row["local_path"]:
                return False
            matches = (
                int(row["remote_size_bytes"]) == artifact.size_bytes
                and int(row["remote_mtime_ns"]) == artifact.mtime_ns
                and Path(row["local_path"]).exists()
            )
            if matches and row["status"] == "SOURCE_MISSING":
                connection.execute(
                    "UPDATE artifacts SET status = 'SYNCED' WHERE source_key = ?",
                    (artifact.source_key,),
                )
            return matches and row["status"] in {"SYNCED", "SOURCE_MISSING"}

    def save_inventory(
        self, *, source: str, job: str, current_strategy: str | None, payload: dict[str, Any]
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs(source, jenkins_job, current_strategy, last_scanned_at,
                                 inventory_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, jenkins_job) DO UPDATE SET
                    current_strategy=excluded.current_strategy,
                    last_scanned_at=excluded.last_scanned_at,
                    inventory_json=excluded.inventory_json
                """,
                (source, job, current_strategy, now, json.dumps(payload, sort_keys=True)),
            )

    def begin_run(self, *, run_id: str, plan_id: str, source: str, job: str, strategy: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sync_runs(run_id, plan_id, source, jenkins_job, strategy,
                                      status, started_at)
                VALUES (?, ?, ?, ?, ?, 'RUNNING', ?)
                """,
                (
                    run_id,
                    plan_id,
                    source,
                    job,
                    strategy,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        transferred: int,
        skipped: int,
        failed: int,
        bytes_written: int,
        errors: list[str],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE sync_runs SET status=?, finished_at=?, transferred=?, skipped=?,
                    failed=?, bytes_written=?, error_json=? WHERE run_id=?
                """,
                (
                    status,
                    datetime.now(UTC).isoformat(),
                    transferred,
                    skipped,
                    failed,
                    bytes_written,
                    json.dumps(errors, ensure_ascii=False),
                    run_id,
                ),
            )

    def upsert_artifact(
        self,
        artifact: RemoteArtifact,
        *,
        source: str,
        local_path: Path,
        local_sha256: str,
        remote_sha256: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts(
                    source_key, source, jenkins_job, strategy, runtime_job, kind,
                    build_number, remote_path, remote_size_bytes, remote_mtime_ns,
                    remote_sha256, local_path, local_sha256, status, synced_at,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'SYNCED', ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    strategy=excluded.strategy,
                    runtime_job=excluded.runtime_job,
                    build_number=excluded.build_number,
                    remote_size_bytes=excluded.remote_size_bytes,
                    remote_mtime_ns=excluded.remote_mtime_ns,
                    remote_sha256=excluded.remote_sha256,
                    local_path=excluded.local_path,
                    local_sha256=excluded.local_sha256,
                    status='SYNCED',
                    synced_at=excluded.synced_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    artifact.source_key,
                    source,
                    artifact.jenkins_job,
                    artifact.strategy,
                    artifact.runtime_job,
                    artifact.kind,
                    artifact.build_number,
                    artifact.remote_path,
                    artifact.size_bytes,
                    artifact.mtime_ns,
                    remote_sha256,
                    str(local_path),
                    local_sha256,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                ),
            )

    def list_artifacts(
        self, *, job: str | None = None, strategy: str | None = None
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if job:
            clauses.append("jenkins_job = ?")
            parameters.append(job)
        if strategy:
            clauses.append("strategy = ?")
            parameters.append(strategy)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT * FROM artifacts {where}
                    ORDER BY jenkins_job, strategy, kind, build_number, remote_path
                    """,
                    parameters,
                )
            )

    def get_artifact(self, source_key: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM artifacts WHERE source_key = ?", (source_key,)
            ).fetchone()

    def mark_source_missing(
        self,
        *,
        source: str,
        job: str,
        observed_paths: set[str],
        log_cutoff_ns: int,
    ) -> int:
        """Mark scanned-scope artifacts missing without deleting local evidence."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT source_key, kind, remote_path, remote_mtime_ns
                FROM artifacts
                WHERE source = ? AND jenkins_job = ? AND status = 'SYNCED'
                """,
                (source, job),
            ).fetchall()
            missing = [
                row["source_key"]
                for row in rows
                if row["remote_path"] not in observed_paths
                and (
                    str(row["kind"]).startswith("database")
                    or int(row["remote_mtime_ns"]) >= log_cutoff_ns
                )
            ]
            connection.executemany(
                "UPDATE artifacts SET status = 'SOURCE_MISSING' WHERE source_key = ?",
                ((source_key,) for source_key in missing),
            )
        return len(missing)

    def mark_retention_deleted(self, source_key: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE artifacts
                SET status = 'RETENTION_DELETED', local_path = NULL
                WHERE source_key = ?
                """,
                (source_key,),
            )

    def add_pin(
        self,
        *,
        pin_id: str,
        source_key: str,
        pinned_path: Path,
        manifest: dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO pins(pin_id, source_key, pinned_path, created_at,
                                 manifest_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    pin_id,
                    source_key,
                    str(pinned_path),
                    datetime.now(UTC).isoformat(),
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                ),
            )

    def add_account_epoch(
        self,
        *,
        source: str,
        job: str,
        strategy: str,
        account_alias: str,
        first_build: int,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO account_epochs(
                    source, jenkins_job, strategy, account_alias, first_build
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, jenkins_job, strategy, first_build)
                DO UPDATE SET account_alias=excluded.account_alias
                """,
                (source, job, strategy, account_alias, first_build),
            )

    def list_account_epochs(self, *, job: str | None = None) -> list[sqlite3.Row]:
        where = "WHERE jenkins_job = ?" if job else ""
        parameters: tuple[str, ...] = (job,) if job else ()
        with self.connect() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT * FROM account_epochs {where}
                    ORDER BY jenkins_job, first_build
                    """,
                    parameters,
                )
            )

    def list_pins(
        self, *, job: str | None = None, strategy: str | None = None
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if job:
            clauses.append("a.jenkins_job = ?")
            parameters.append(job)
        if strategy:
            clauses.append("a.strategy = ?")
            parameters.append(strategy)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT p.*, a.jenkins_job, a.strategy, a.runtime_job, a.kind
                    FROM pins p
                    JOIN artifacts a ON a.source_key = p.source_key
                    {where}
                    ORDER BY p.created_at DESC
                    """,
                    parameters,
                )
            )

    def list_sync_runs(self, *, limit: int = 20) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT * FROM sync_runs
                    ORDER BY started_at DESC LIMIT ?
                    """,
                    (limit,),
                )
            )

    def dashboard_summary(self) -> dict[str, Any]:
        with self.connect() as connection:
            artifact_rows = connection.execute(
                """
                SELECT status, kind, COUNT(*) AS count,
                       COALESCE(SUM(remote_size_bytes), 0) AS bytes
                FROM artifacts GROUP BY status, kind
                """
            ).fetchall()
            job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            latest_run = connection.execute(
                """
                SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 1
                """
            ).fetchone()
            pin_count = connection.execute("SELECT COUNT(*) FROM pins").fetchone()[0]
        return {
            "jobs": int(job_count),
            "pins": int(pin_count),
            "artifacts": [{key: row[key] for key in row.keys()} for row in artifact_rows],
            "latest_run": (
                {key: latest_run[key] for key in latest_run.keys()} if latest_run else None
            ),
        }
