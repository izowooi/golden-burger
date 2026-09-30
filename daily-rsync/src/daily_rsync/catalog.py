from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .models import RemoteArtifact, artifact_source_key, research_archive_date
from polybot_observability.market_data_retirement import (
    RETIREMENT_TABLE_SQL, RETIREMENT_GUARD_SQL, RETIREMENT_CONTRACT, RETIREMENT_LOGICAL_ANCHOR_FIELDS,
    canonical_retirement_json, retirement_record_digest, validate_retirement_schema,
)

SCHEMA_VERSION = 6
SOURCE_KEY_VERSION = 2


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
                    remote_fingerprint TEXT,
                    remote_sha256 TEXT,
                    local_path TEXT,
                    local_sha256 TEXT,
                    status TEXT NOT NULL,
                    synced_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS artifacts_job_strategy_idx
                    ON artifacts(jenkins_job, strategy, kind);
                CREATE INDEX IF NOT EXISTS artifacts_source_job_strategy_idx
                    ON artifacts(source, jenkins_job, strategy, kind);
                CREATE INDEX IF NOT EXISTS artifacts_local_path_idx
                    ON artifacts(local_path);

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

                CREATE TABLE IF NOT EXISTS artifact_conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    detected_at TEXT NOT NULL,
                    conflict_type TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    jenkins_job TEXT NOT NULL,
                    strategy TEXT,
                    runtime_job TEXT,
                    kind TEXT NOT NULL,
                    remote_path TEXT NOT NULL,
                    archive_date TEXT,
                    local_path TEXT NOT NULL,
                    existing_source_key TEXT,
                    existing_remote_path TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'OPEN'
                );
                CREATE INDEX IF NOT EXISTS artifact_conflicts_open_idx
                    ON artifact_conflicts(status, jenkins_job, conflict_type);
                CREATE TABLE IF NOT EXISTS storage_generations (
                    source_key TEXT NOT NULL,
                    generation_sha256 TEXT NOT NULL,
                    original_sha256 TEXT NOT NULL,
                    original_path TEXT NOT NULL,
                    attestation_json TEXT NOT NULL,
                    PRIMARY KEY(source_key, generation_sha256)
                );
                """
            )
            connection.execute(RETIREMENT_TABLE_SQL.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))
            for sql in RETIREMENT_GUARD_SQL.values():
                connection.execute(sql.replace("CREATE TRIGGER ", "CREATE TRIGGER IF NOT EXISTS ", 1))
            validate_retirement_schema(connection)
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(artifacts)")}
            if "remote_fingerprint" not in columns:
                connection.execute("ALTER TABLE artifacts ADD COLUMN remote_fingerprint TEXT")
            conflict_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(artifact_conflicts)")
            }
            for name in ("strategy", "runtime_job", "archive_date"):
                if name not in conflict_columns:
                    connection.execute(f"ALTER TABLE artifact_conflicts ADD COLUMN {name} TEXT")
            self._migrate_source_keys(connection)
            connection.execute(
                """
                INSERT INTO catalog_meta(key, value) VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )
            connection.execute(
                """
                INSERT INTO catalog_meta(key, value) VALUES ('source_key_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(SOURCE_KEY_VERSION),),
            )

    @staticmethod
    def _migrate_source_keys(connection: sqlite3.Connection) -> None:
        """Canonicalize host-scoped keys without orphaning pins/conflicts.

        Some catalogs received source-aware rows before the v2 key formula was
        finalized.  Their version marker is already current, so checking only
        the marker leaves stale rows that point at the same mutable local file.
        Recompute the canonical key on every open and coalesce those rows to the
        most recently synchronized evidence.
        """

        rows = connection.execute("SELECT * FROM artifacts ORDER BY source_key").fetchall()
        groups: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            source = str(row["source"] or "")
            if not source:
                raise RuntimeError("catalog artifact is missing its SSH source identity")
            new_key = artifact_source_key(
                source=source,
                jenkins_job=str(row["jenkins_job"]),
                kind=str(row["kind"]),
                remote_path=str(row["remote_path"]),
            )
            groups.setdefault(new_key, []).append(row)

        columns = [str(row[1]) for row in connection.execute("PRAGMA table_info(artifacts)")]
        quoted_columns = ", ".join(columns)
        placeholders = ", ".join("?" for _column in columns)
        comparable = [column for column in columns if column != "source_key"]
        for new_key, candidates in groups.items():
            winner = max(
                candidates,
                key=lambda row: (
                    str(row["synced_at"] or ""),
                    int(row["remote_mtime_ns"] or 0),
                    str(row["source_key"]) == new_key,
                ),
            )
            existing = connection.execute(
                "SELECT * FROM artifacts WHERE source_key = ?", (new_key,)
            ).fetchone()
            if existing is None:
                values = [
                    new_key if column == "source_key" else winner[column]
                    for column in columns
                ]
                connection.execute(
                    f"INSERT INTO artifacts({quoted_columns}) VALUES ({placeholders})",
                    values,
                )
            elif str(winner["source_key"]) != new_key:
                assignments = ", ".join(f"{column} = ?" for column in comparable)
                connection.execute(
                    f"UPDATE artifacts SET {assignments} WHERE source_key = ?",
                    [winner[column] for column in comparable] + [new_key],
                )

            for row in candidates:
                old_key = str(row["source_key"])
                if old_key == new_key:
                    continue
                connection.execute(
                    "UPDATE pins SET source_key = ? WHERE source_key = ?", (new_key, old_key)
                )
                connection.execute(
                    "UPDATE artifact_conflicts SET source_key = ? WHERE source_key = ?",
                    (new_key, old_key),
                )
                connection.execute(
                    "UPDATE artifact_conflicts SET existing_source_key = ? "
                    "WHERE existing_source_key = ?",
                    (new_key, old_key),
                )
                connection.execute(
                    "DELETE FROM artifacts WHERE source_key = ?", (old_key,)
                )

    def artifact_is_current(self, artifact: RemoteArtifact) -> bool:
        if not artifact.source:
            raise ValueError("artifact source identity is required")
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT remote_size_bytes, remote_mtime_ns, remote_fingerprint,
                       status, local_path
                FROM artifacts WHERE source_key = ?
                """,
                (artifact.source_key,),
            ).fetchone()
            if row is None or not row["local_path"]:
                return False
            old_fingerprint = row["remote_fingerprint"]
            if old_fingerprint and artifact.fingerprint:
                remote_matches = str(old_fingerprint) == artifact.fingerprint
            else:
                remote_matches = (
                    int(row["remote_size_bytes"]) == artifact.size_bytes
                    and int(row["remote_mtime_ns"]) == artifact.mtime_ns
                )
            matches = remote_matches and Path(row["local_path"]).exists()
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

    def record_failed_attempt(
        self,
        *,
        source: str,
        job: str,
        strategy: str,
        phase: str,
        error: BaseException,
    ) -> str:
        """Persist a failed scan/plan attempt even when no SyncPlan exists."""

        boundary = f"{source}\0{job}\0{strategy}\0{phase}"
        plan_id = "no-plan-" + hashlib.sha256(boundary.encode()).hexdigest()[:16]
        run_id = uuid.uuid4().hex
        self.begin_run(
            run_id=run_id,
            plan_id=plan_id,
            source=source,
            job=job,
            strategy=strategy,
        )
        self.finish_run(
            run_id=run_id,
            status="FAILED",
            transferred=0,
            skipped=0,
            failed=1,
            bytes_written=0,
            errors=[f"{phase}: {type(error).__name__}: {error}"],
        )
        return run_id

    def current_strategy(self, *, source: str, job: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT current_strategy FROM jobs WHERE source = ? AND jenkins_job = ?",
                (source, job),
            ).fetchone()
        return str(row[0]) if row is not None and row[0] else None

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
        storage_generation: dict[str, Any] | None = None,
    ) -> None:
        if artifact.source != source:
            raise ValueError(
                "artifact source identity does not match catalog source: "
                f"{artifact.source!r} != {source!r}"
            )
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            if storage_generation is not None:
                if (
                    storage_generation.get("source_key") != artifact.source_key
                    or storage_generation.get("generation_sha256") != local_sha256
                    or storage_generation.get("status") != "VERIFIED"
                ):
                    raise RuntimeError("storage generation identity mismatch")
                connection.execute(
                    "INSERT INTO storage_generations VALUES (?,?,?,?,?)",
                    (
                        artifact.source_key,
                        local_sha256,
                        storage_generation["original_sha256"],
                        storage_generation["original_path"],
                        json.dumps(storage_generation, sort_keys=True),
                    ),
                )
                # These precise immutable conflicts are resolved only after the
                # caller independently verified original and decoded tables.
                conflicts = connection.execute(
                    "SELECT id,details_json FROM artifact_conflicts WHERE source_key=? "
                    "AND existing_source_key=? AND remote_path=? AND status='OPEN' "
                    "AND conflict_type IN ('IMMUTABLE_REMOTE_CHANGED','IMMUTABLE_LOCAL_EXISTS')",
                    (artifact.source_key, artifact.source_key, artifact.remote_path),
                ).fetchall()
                for conflict in conflicts:
                    details = json.loads(conflict["details_json"])
                    details["storage_migration_resolution"] = local_sha256
                    connection.execute(
                        "UPDATE artifact_conflicts SET status='RESOLVED', details_json=? "
                        "WHERE id=?",
                        (json.dumps(details, sort_keys=True), conflict["id"]),
                    )
            connection.execute(
                """
                INSERT INTO artifacts(
                    source_key, source, jenkins_job, strategy, runtime_job, kind,
                    build_number, remote_path, remote_size_bytes, remote_mtime_ns,
                    remote_fingerprint, remote_sha256, local_path, local_sha256, status, synced_at,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'SYNCED', ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    strategy=excluded.strategy,
                    runtime_job=excluded.runtime_job,
                    build_number=excluded.build_number,
                    remote_size_bytes=excluded.remote_size_bytes,
                    remote_mtime_ns=excluded.remote_mtime_ns,
                    remote_fingerprint=excluded.remote_fingerprint,
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
                    artifact.fingerprint,
                    remote_sha256,
                    str(local_path),
                    local_sha256,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                ),
            )

    def immutable_conflict(self, artifact: RemoteArtifact) -> sqlite3.Row | None:
        """Return the prior row when an immutable shard's remote identity changed."""
        if artifact.kind != "database_research_archive":
            return None
        row = self.get_artifact(artifact.source_key)
        if row is None or not row["local_path"] or not Path(row["local_path"]).is_file():
            return None
        old_fingerprint = row["remote_fingerprint"]
        if old_fingerprint and artifact.fingerprint:
            changed = str(old_fingerprint) != artifact.fingerprint
        else:
            changed = (
                int(row["remote_size_bytes"]) != artifact.size_bytes
                or int(row["remote_mtime_ns"]) != artifact.mtime_ns
            )
        return row if changed else None

    def storage_generation(self, source_key: str, digest: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT attestation_json FROM storage_generations WHERE source_key=? "
                "AND generation_sha256=?",
                (source_key, digest),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def record_source_retirement(self, record: dict) -> dict:
        """Bind a completed native proof to the independently verified catalog pin.

        This registers eligibility only. No original file is removed here.
        """
        if (not isinstance(record, dict) or record.get("contract") != RETIREMENT_CONTRACT
                or record.get("record_sha256") != retirement_record_digest(record)):
            raise RuntimeError("retirement record contract or checksum differs")
        body = canonical_retirement_json(record)
        if len(body) > 16 * 1024 * 1024:
            raise RuntimeError("retirement validation record is oversized")
        keys = tuple(record[key] for key in ("source_key", "generation_sha256", "anchor_sha256", "pin_path"))
        pin_path = Path(record["pin_path"])
        root = self.path.resolve().parent
        if (not pin_path.is_absolute() or pin_path.is_symlink() or not pin_path.is_file()
                or pin_path.resolve() != pin_path or not pin_path.is_relative_to(root / "sources")):
            raise RuntimeError("retirement pin is outside the trusted catalog root")
        before = pin_path.stat()
        digest = hashlib.sha256()
        with pin_path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(block)
        pin_manifest_path = pin_path.parent / "manifest.json"
        raw_manifest = pin_manifest_path.read_bytes()
        file_manifest = json.loads(raw_manifest)
        if (digest.hexdigest() != record["generation_sha256"]
                or hashlib.sha256(raw_manifest).hexdigest() != record["pin_manifest_sha256"]
                or hashlib.sha256(canonical_retirement_json(file_manifest)).hexdigest()
                   != record["pin_manifest_canonical_sha256"]):
            raise RuntimeError("retirement pin changed after native proof")
        registered = self.source_retirement_validation(*keys)
        original = Path(record["original_path"])
        if registered is None:
            if (not original.is_absolute() or original.is_symlink() or not original.is_file()
                    or original.resolve() != original or not original.is_relative_to(root / "review")):
                raise RuntimeError("retirement original is absent or outside the trusted review root")
            item = original.stat()
            attributes = {"device": item.st_dev, "inode": item.st_ino,
                          "size_bytes": item.st_size, "mtime_ns": item.st_mtime_ns}
            digest = hashlib.sha256()
            with original.open("rb") as stream:
                for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    digest.update(block)
            if (attributes != record["original_stat"]
                    or digest.hexdigest() != record["source_snapshot_sha256"]
                    or hashlib.sha256((original.parent / "manifest.json").read_bytes()).hexdigest()
                       != record["prior_manifest_sha256"]):
                raise RuntimeError("retirement original changed after native proof")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            validate_retirement_schema(connection)
            pins = connection.execute("SELECT manifest_json FROM pins WHERE source_key=? AND pinned_path=?",
                                      (keys[0], keys[3])).fetchall()
            generation = connection.execute(
                "SELECT original_sha256,attestation_json FROM storage_generations WHERE source_key=? AND generation_sha256=?",
                keys[:2],
            ).fetchone()
            if (len(pins) != 1 or generation is None
                    or json.loads(pins[0][0]) != file_manifest):
                raise RuntimeError("retirement has no matching verified catalog pin/generation")
            lineage = json.loads(generation["attestation_json"])
            projection = lineage.get("public_projection_records", {})
            receipt = lineage.get("source_storage_transition", {})
            verified = receipt.get("verification", {})
            anchor = receipt.get("original_anchor", {})
            semantic = {"logical_schema_sha256": anchor.get("source_schema_sha256"),
                        "anchor_core": {key: anchor.get(key) for key in RETIREMENT_LOGICAL_ANCHOR_FIELDS}}
            readback = {"physical_schema": verified.get("raw_schema_transition", {}).get("target"),
                        "tables": verified.get("tables"),
                        "private_storage": {table: {"rows": values.get("rows"), "columns": values.get("columns"),
                                                    "sha256": values.get("target_sha256")}
                                            for table, values in verified.get("private_storage", {}).items()}}
            if (hashlib.sha256(canonical_retirement_json(lineage)).hexdigest() != record["lineage_sha256"]
                    or lineage != file_manifest.get("storage_lineage")
                    or generation["original_sha256"] != record["source_snapshot_sha256"]
                    or lineage.get("source_file_sha256") != record["original_sha256"]
                    or lineage.get("original_path") != record["original_path"]
                    or receipt.get("original_anchor", {}).get("anchor_sha256") != keys[2]
                    or receipt.get("raw_manifest_sha256") != record["raw_manifest_sha256"]
                    or lineage.get("source_storage_transition_sha256") != record["source_transition_sha256"]
                    or receipt.get("proof_sha256") != record["source_transition_proof_sha256"]
                    or projection.get("namespace") != record["namespace"]
                    or projection.get("authority_uuid") != record["authority_uuid"]
                    or projection != record["public_projection_records"]
                    or projection.get("raw_profile_id") != record["raw_profile_id"]
                    or projection.get("raw_profile_version") != record["raw_profile_version"]
                    or projection.get("raw_logical_schema_sha256") != record["raw_logical_schema_sha256"]
                    or receipt.get("body_closure") != record["body_closure"]
                    or semantic != record["semantic_source"] or readback != record["raw_readback"]
                    or file_manifest.get("source_identity") != record["source_identity"]
                    or hashlib.sha256(canonical_retirement_json(receipt.get("verification"))).hexdigest()
                       != record["independent_verification_sha256"]):
                raise RuntimeError("retirement proof differs from independently verified catalog lineage")
            after = pin_path.stat()
            if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)
                    != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
                    or pin_manifest_path.read_bytes() != raw_manifest):
                raise RuntimeError("retirement pin changed while binding catalog proof")
            existing = connection.execute(
                "SELECT record_json FROM source_retirement_validations WHERE source_key=? AND generation_sha256=? AND anchor_sha256=? AND pin_path=?",
                keys,
            ).fetchone()
            if existing is not None:
                if existing[0] != body.decode("utf-8"):
                    raise RuntimeError("retirement validation is immutable and differs")
                return json.loads(existing[0])
            item = original.stat()
            if (record["original_stat"] != {"device": item.st_dev, "inode": item.st_ino,
                                           "size_bytes": item.st_size, "mtime_ns": item.st_mtime_ns}
                    or hashlib.sha256((original.parent / "manifest.json").read_bytes()).hexdigest()
                       != record["prior_manifest_sha256"]):
                raise RuntimeError("retirement original changed while binding catalog proof")
            connection.execute("INSERT INTO source_retirement_validations VALUES(?,?,?,?,?,?)",
                               (*keys, record["record_sha256"], body.decode("utf-8")))
        return record

    def source_retirement_validation(self, source_key, generation_sha256, anchor_sha256, pin_path):
        with self.connect() as connection:
            validate_retirement_schema(connection)
            row = connection.execute(
                "SELECT record_json,record_sha256 FROM source_retirement_validations WHERE source_key=? AND generation_sha256=? AND anchor_sha256=? AND pin_path=?",
                (source_key,generation_sha256,anchor_sha256,str(pin_path)),
            ).fetchone()
        if row is None:
            return None
        record = json.loads(row[0])
        if record.get("record_sha256") != row[1] or retirement_record_digest(record) != row[1]:
            raise RuntimeError("retirement catalog record checksum differs")
        return record

    def destination_conflict(
        self,
        *,
        artifact: RemoteArtifact,
        local_path: Path,
    ) -> sqlite3.Row | None:
        """Detect two remote workspace paths mapping to one local evidence path."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM artifacts
                WHERE local_path = ? AND source_key != ?
                  AND status != 'RETENTION_DELETED'
                """,
                (str(local_path), artifact.source_key),
            ).fetchall()
        return next(
            (
                row
                for row in rows
                if row["remote_path"] != artifact.remote_path
                and row["local_path"]
                and Path(row["local_path"]).is_file()
            ),
            None,
        )

    def record_conflict(
        self,
        *,
        conflict_type: str,
        source: str,
        artifact: RemoteArtifact,
        local_path: Path,
        existing: sqlite3.Row | None,
        details: dict[str, Any] | None = None,
        status: str = "OPEN",
    ) -> None:
        """Persist a fail-closed provenance conflict without replacing evidence."""
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            duplicate = connection.execute(
                """
                SELECT 1 FROM artifact_conflicts
                WHERE status = ? AND conflict_type = ? AND source_key = ?
                  AND local_path = ?
                  AND COALESCE(existing_source_key, '') = COALESCE(?, '')
                LIMIT 1
                """,
                (
                    status,
                    conflict_type,
                    artifact.source_key,
                    str(local_path),
                    existing["source_key"] if existing else None,
                ),
            ).fetchone()
            if duplicate is None:
                connection.execute(
                    """
                    INSERT INTO artifact_conflicts(
                        detected_at, conflict_type, source_key, source, jenkins_job,
                        strategy, runtime_job, kind, remote_path, archive_date,
                        local_path, existing_source_key,
                        existing_remote_path, details_json, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        conflict_type,
                        artifact.source_key,
                        source,
                        artifact.jenkins_job,
                        artifact.strategy,
                        artifact.runtime_job,
                        artifact.kind,
                        artifact.remote_path,
                        artifact.archive_date,
                        str(local_path),
                        existing["source_key"] if existing else None,
                        existing["remote_path"] if existing else None,
                        json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                        status,
                    ),
                )
            if existing is not None and status == "OPEN":
                connection.execute(
                    "UPDATE artifacts SET status = ? WHERE source_key = ?",
                    (
                        "IMMUTABLE_CONFLICT"
                        if conflict_type.startswith("IMMUTABLE_")
                        else "PROVENANCE_CONFLICT",
                        existing["source_key"],
                    ),
                )

    def resolve_source_path_collision(
        self,
        *,
        conflict_id: int,
        source_key: str,
        existing_source_key: str,
        resolution: dict[str, Any],
    ) -> None:
        """Resolve one explicitly epoch-routed workspace move.

        The caller must verify both the preserved local evidence and the new
        non-colliding destination first. This method keeps the conflict row as
        an audit record and marks the superseded remote source as historical.
        """

        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifact_conflicts WHERE id = ?",
                (conflict_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"artifact conflict not found: {conflict_id}")
            if row["status"] != "OPEN" or row["conflict_type"] != "SOURCE_PATH_COLLISION":
                raise RuntimeError("artifact conflict is not an open source-path collision")
            if row["source_key"] != source_key or row["existing_source_key"] != existing_source_key:
                raise RuntimeError("artifact conflict identity changed before resolution")

            try:
                details = json.loads(row["details_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                details = {}
            details["resolution"] = {
                **resolution,
                "resolved_at": datetime.now(UTC).isoformat(),
            }
            updated = connection.execute(
                """
                UPDATE artifact_conflicts
                SET status = 'RESOLVED', details_json = ?
                WHERE id = ? AND status = 'OPEN'
                """,
                (json.dumps(details, ensure_ascii=False, sort_keys=True), conflict_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("artifact conflict changed during resolution")
            remaining = connection.execute(
                """
                SELECT 1 FROM artifact_conflicts
                WHERE status = 'OPEN' AND existing_source_key = ?
                LIMIT 1
                """,
                (existing_source_key,),
            ).fetchone()
            if remaining is None:
                connection.execute(
                    """
                    UPDATE artifacts
                    SET status = 'SOURCE_MISSING'
                    WHERE source_key = ? AND status = 'PROVENANCE_CONFLICT'
                    """,
                    (existing_source_key,),
                )

    def list_conflicts(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute("SELECT * FROM artifact_conflicts ORDER BY detected_at, id")
            )

    def list_open_conflicts(
        self,
        *,
        source: str | None = None,
        job: str | None = None,
        strategy: str | None = None,
        source_keys: set[str] | None = None,
    ) -> list[sqlite3.Row]:
        clauses = ["status = 'OPEN'"]
        parameters: list[Any] = []
        if source:
            clauses.append("source = ?")
            parameters.append(source)
        if job:
            clauses.append("jenkins_job = ?")
            parameters.append(job)
        if strategy:
            clauses.append("(strategy = ? OR strategy IS NULL)")
            parameters.append(strategy)
        if source_keys:
            placeholders = ",".join("?" for _value in source_keys)
            clauses.append(f"source_key IN ({placeholders})")
            parameters.extend(sorted(source_keys))
        with self.connect() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT * FROM artifact_conflicts
                    WHERE {" AND ".join(clauses)}
                    ORDER BY detected_at, id
                    """,
                    parameters,
                )
            )

    def list_artifacts(
        self,
        *,
        source: str | None = None,
        job: str | None = None,
        strategy: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if source:
            clauses.append("source = ?")
            parameters.append(source)
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
        archive_from_date: date | None = None,
        archive_to_date: date | None = None,
        include_canonical_databases: bool = True,
    ) -> int:
        """Mark scanned-scope artifacts missing without deleting local evidence."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT source_key, kind, remote_path, remote_mtime_ns, metadata_json
                FROM artifacts
                WHERE source = ? AND jenkins_job = ? AND status = 'SYNCED'
                """,
                (source, job),
            ).fetchall()
            missing = []
            for row in rows:
                if row["remote_path"] in observed_paths:
                    continue
                kind = str(row["kind"])
                if kind == "database_research_archive":
                    archive_date = research_archive_date(str(row["remote_path"]))
                    if archive_date is None:
                        continue
                    if archive_from_date and archive_date < archive_from_date:
                        continue
                    if archive_to_date and archive_date > archive_to_date:
                        continue
                    missing.append(row["source_key"])
                elif kind == "database_sim" and archive_from_date and archive_to_date:
                    if not include_canonical_databases:
                        continue
                    try:
                        metadata = json.loads(row["metadata_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        metadata = {}
                    today = datetime.now(UTC).date()
                    if (
                        metadata.get("data_contract") == "research-full-v1"
                        and not archive_from_date <= today <= archive_to_date
                    ):
                        continue
                    missing.append(row["source_key"])
                elif kind.startswith("database"):
                    if include_canonical_databases:
                        missing.append(row["source_key"])
                elif int(row["remote_mtime_ns"]) >= log_cutoff_ns:
                    missing.append(row["source_key"])
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

    def record_remote_retention_deleted(
        self,
        artifact: RemoteArtifact,
        *,
        source: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Catalog a console log removed by Jenkins after planning.

        If a verified local copy already exists, preserve it as historical
        SOURCE_MISSING evidence. Otherwise retain an explicit
        RETENTION_DELETED row so verify can distinguish the race from an
        unexplained transfer failure.
        """

        if artifact.source != source:
            raise ValueError("artifact source identity does not match catalog source")
        if artifact.kind != "jenkins_console":
            raise ValueError("remote retention deletion is only valid for Jenkins console logs")
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT local_path FROM artifacts WHERE source_key = ?",
                (artifact.source_key,),
            ).fetchone()
            if row is not None and row["local_path"] and Path(row["local_path"]).is_file():
                connection.execute(
                    "UPDATE artifacts SET status = 'SOURCE_MISSING' WHERE source_key = ?",
                    (artifact.source_key,),
                )
                return "SOURCE_MISSING"
            connection.execute(
                """
                INSERT INTO artifacts(
                    source_key, source, jenkins_job, strategy, runtime_job, kind,
                    build_number, remote_path, remote_size_bytes, remote_mtime_ns,
                    remote_fingerprint, remote_sha256, local_path, local_sha256,
                    status, synced_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL,
                          'RETENTION_DELETED', ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    strategy=excluded.strategy,
                    runtime_job=excluded.runtime_job,
                    build_number=excluded.build_number,
                    remote_size_bytes=excluded.remote_size_bytes,
                    remote_mtime_ns=excluded.remote_mtime_ns,
                    remote_fingerprint=excluded.remote_fingerprint,
                    remote_sha256=NULL,
                    local_path=NULL,
                    local_sha256=NULL,
                    status='RETENTION_DELETED',
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
                    artifact.fingerprint,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
        return "RETENTION_DELETED"

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
        self,
        *,
        source: str | None = None,
        job: str | None = None,
        strategy: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if source:
            clauses.append("a.source = ?")
            parameters.append(source)
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

    def list_sync_runs(self, *, source: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
        where = "WHERE source = ?" if source else ""
        parameters: tuple[Any, ...] = (source, limit) if source else (limit,)
        with self.connect() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT * FROM sync_runs
                    {where}
                    ORDER BY started_at DESC LIMIT ?
                    """,
                    parameters,
                )
            )

    def latest_sync_run(
        self,
        *,
        source: str,
        job: str,
        strategy: str,
        successful_only: bool = False,
    ) -> sqlite3.Row | None:
        status_clause = "AND status = 'SUCCESS'" if successful_only else ""
        with self.connect() as connection:
            return connection.execute(
                f"""
                SELECT * FROM sync_runs
                WHERE source = ? AND jenkins_job = ? AND strategy = ?
                {status_clause}
                ORDER BY started_at DESC LIMIT 1
                """,
                (source, job, strategy),
            ).fetchone()

    def dashboard_summary(self, *, source: str | None = None) -> dict[str, Any]:
        artifact_where = "WHERE source = ?" if source else ""
        job_where = "WHERE source = ?" if source else ""
        run_where = "WHERE source = ?" if source else ""
        parameters: tuple[Any, ...] = (source,) if source else ()
        with self.connect() as connection:
            artifact_rows = connection.execute(
                f"""
                SELECT status, kind, COUNT(*) AS count,
                       COALESCE(SUM(remote_size_bytes), 0) AS bytes
                FROM artifacts {artifact_where} GROUP BY status, kind
                """,
                parameters,
            ).fetchall()
            job_count = connection.execute(
                f"SELECT COUNT(*) FROM jobs {job_where}", parameters
            ).fetchone()[0]
            latest_run = connection.execute(
                f"SELECT * FROM sync_runs {run_where} ORDER BY started_at DESC LIMIT 1",
                parameters,
            ).fetchone()
            if source:
                pin_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM pins p
                    JOIN artifacts a ON a.source_key = p.source_key
                    WHERE a.source = ?
                    """,
                    parameters,
                ).fetchone()[0]
            else:
                pin_count = connection.execute("SELECT COUNT(*) FROM pins").fetchone()[0]
        return {
            "jobs": int(job_count),
            "pins": int(pin_count),
            "artifacts": [{key: row[key] for key in row.keys()} for row in artifact_rows],
            "latest_run": (
                {key: latest_run[key] for key in latest_run.keys()} if latest_run else None
            ),
        }
