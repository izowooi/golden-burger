from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .catalog import Catalog
from .config import AppConfig
from .models import JobInventory, RemoteArtifact, SyncPlan, SyncResult
from .remote import RemoteClient

ProgressCallback = Callable[[dict[str, object]], None]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def gzip_content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def quick_check(path: Path) -> list[str]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        return [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    finally:
        connection.close()


def clone_or_copy(source: Path, destination: Path) -> None:
    destination.unlink(missing_ok=True)
    process = subprocess.run(
        ["cp", "-c", str(source), str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        shutil.copy2(source, destination)
    destination.chmod(0o600)


def _safe_component(value: str) -> str:
    if not value or value in {".", ".."}:
        raise ValueError("empty or unsafe path component")
    safe = "".join(
        character if character.isalnum() or character in "._-" else "_" for character in value
    )
    if safe in {".", ".."}:
        raise ValueError("unsafe path component")
    return safe


class SyncService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.catalog = Catalog(config.catalog_path)
        self.remote = RemoteClient(config)

    def doctor(self) -> dict[str, object]:
        payload = self.remote.doctor()
        local = shutil.disk_usage(self.config.data_root)
        payload.update(
            {
                "ssh_host": self.config.ssh_host,
                "expected_ip": self.config.expected_ip,
                "local_data_root": str(self.config.data_root),
                "local_free_bytes": local.free,
                "minimum_free_bytes": self.config.minimum_free_bytes,
            }
        )
        if not payload.get("jobs_exists") or not payload.get("workspace_exists"):
            raise RuntimeError("remote Jenkins home contract failed")
        return payload

    def scan(self, *, job: str | None = None, days: int | None = None) -> list[JobInventory]:
        cutoff = datetime.now(UTC) - timedelta(days=days or self.config.initial_log_days)
        inventories = self.remote.scan(job=job, cutoff_epoch=cutoff.timestamp())
        for inventory in inventories:
            self.catalog.save_inventory(
                source=self.config.ssh_host,
                job=inventory.name,
                current_strategy=inventory.current_strategy,
                payload=asdict(inventory),
            )
            self.catalog.mark_source_missing(
                source=self.config.ssh_host,
                job=inventory.name,
                observed_paths={artifact.remote_path for artifact in inventory.artifacts},
                log_cutoff_ns=int(cutoff.timestamp() * 1_000_000_000),
            )
        return inventories

    def create_plan(
        self,
        *,
        job: str,
        strategy: str | None = None,
        include_safety_databases: bool = False,
        days: int | None = None,
    ) -> SyncPlan:
        inventories = self.scan(job=job, days=days)
        if not inventories:
            raise ValueError(f"Jenkins job not found: {job}")
        inventory = inventories[0]
        selected_strategy = strategy or inventory.current_strategy
        if not selected_strategy:
            raise ValueError("strategy could not be inferred; pass --strategy")
        selected: list[RemoteArtifact] = []
        unchanged = 0
        for artifact in inventory.artifacts:
            if artifact.kind == "jenkins_console":
                if artifact.strategy not in {None, selected_strategy}:
                    continue
            elif artifact.strategy != selected_strategy:
                continue
            if artifact.kind == "database_safety" and not include_safety_databases:
                continue
            if self.catalog.artifact_is_current(artifact):
                unchanged += 1
            else:
                selected.append(artifact)
        selected.sort(
            key=lambda item: (
                0 if item.kind.startswith("database") else 1,
                item.build_number or 0,
                item.remote_path,
            )
        )
        plan = SyncPlan.create(
            source=self.config.ssh_host,
            jenkins_job=job,
            strategy=selected_strategy,
            artifacts=selected,
            skipped_unchanged=unchanged,
            include_safety_databases=include_safety_databases,
        )
        self._ensure_disk_capacity(plan.estimated_bytes)
        plan.write(self.config.plans_root)
        return plan

    def load_plan(self, plan_id: str) -> SyncPlan:
        path = self.config.plans_root / f"{plan_id}.json"
        if not path.is_file():
            raise ValueError(f"plan not found: {plan_id}")
        return SyncPlan.read(path)

    def execute(self, plan: SyncPlan, *, progress: ProgressCallback | None = None) -> SyncResult:
        self._ensure_disk_capacity(plan.estimated_bytes)
        run_id = uuid.uuid4().hex
        result = SyncResult(
            run_id=run_id,
            status="RUNNING",
            skipped=plan.skipped_unchanged,
        )
        self.catalog.begin_run(
            run_id=run_id,
            plan_id=plan.plan_id,
            source=plan.source,
            job=plan.jenkins_job,
            strategy=plan.strategy,
        )
        self._progress(
            progress,
            phase="start",
            run_id=run_id,
            total=len(plan.artifacts),
            completed=0,
        )
        console_cache: dict[str, Path] = {}
        console_artifacts = [
            artifact for artifact in plan.artifacts if artifact.kind == "jenkins_console"
        ]
        try:
            console_cache = self._prefetch_console_logs(console_artifacts, run_id, progress)
        except Exception as error:
            message = f"jenkins console batch: {type(error).__name__}: {error}"
            result.errors.append(message)
        for index, artifact in enumerate(plan.artifacts, start=1):
            try:
                self._progress(
                    progress,
                    phase="artifact",
                    current=artifact.remote_path,
                    kind=artifact.kind,
                    total=len(plan.artifacts),
                    completed=index - 1,
                )
                if artifact.kind.startswith("database"):
                    local_path, digest, remote_digest, written = self._sync_database(
                        artifact, run_id
                    )
                elif artifact.kind == "jenkins_console":
                    incoming = console_cache.get(artifact.remote_path)
                    if incoming is None:
                        raise RuntimeError("console log was not present in batch result")
                    local_path, digest, written = self._store_regular(artifact, incoming)
                    remote_digest = None
                else:
                    local_path, digest, written = self._sync_regular(artifact, run_id)
                    remote_digest = None
                self.catalog.upsert_artifact(
                    artifact,
                    source=plan.source,
                    local_path=local_path,
                    local_sha256=digest,
                    remote_sha256=remote_digest,
                    metadata={
                        "completed_at": artifact.completed_at,
                        "status": artifact.status,
                        "canonical": artifact.canonical,
                    },
                )
                result.transferred += 1
                result.bytes_written += written
            except Exception as error:
                result.failed += 1
                result.errors.append(
                    f"{artifact.kind} {artifact.remote_path}: {type(error).__name__}: {error}"
                )
            self._progress(
                progress,
                phase="progress",
                total=len(plan.artifacts),
                completed=index,
                transferred=result.transferred,
                failed=result.failed,
            )
        result.status = (
            "SUCCESS" if result.failed == 0 else "PARTIAL" if result.transferred else "FAILED"
        )
        self.catalog.finish_run(
            run_id=run_id,
            status=result.status,
            transferred=result.transferred,
            skipped=result.skipped,
            failed=result.failed,
            bytes_written=result.bytes_written,
            errors=result.errors,
        )
        self._progress(progress, phase="finished", **asdict(result))
        shutil.rmtree(
            self.config.incoming_root / f"{run_id}-console",
            ignore_errors=True,
        )
        return result

    def _prefetch_console_logs(
        self,
        artifacts: list[RemoteArtifact],
        run_id: str,
        progress: ProgressCallback | None,
    ) -> dict[str, Path]:
        if not artifacts:
            return {}
        root = self.config.incoming_root / f"{run_id}-console"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        result: dict[str, Path] = {}
        batches: list[list[RemoteArtifact]] = []
        current: list[RemoteArtifact] = []
        current_bytes = 0
        for artifact in artifacts:
            if current and (
                len(current) >= self.config.batch_file_limit
                or current_bytes + artifact.size_bytes > self.config.batch_byte_limit
            ):
                batches.append(current)
                current = []
                current_bytes = 0
            current.append(artifact)
            current_bytes += artifact.size_bytes
        if current:
            batches.append(current)
        for index, batch in enumerate(batches, start=1):
            self._progress(
                progress,
                phase="console_batch",
                batch=index,
                batches=len(batches),
                files=len(batch),
                bytes=sum(item.size_bytes for item in batch),
            )
            self.remote.rsync_files(
                remote_paths=[item.remote_path for item in batch],
                local_root=root,
            )
            for artifact in batch:
                relative = Path(artifact.remote_path).relative_to(
                    Path(self.config.remote_jenkins_home)
                )
                incoming = root / relative
                if not incoming.is_file():
                    raise RuntimeError(f"batch rsync omitted {artifact.remote_path}")
                result[artifact.remote_path] = incoming
        return result

    def _sync_database(self, artifact: RemoteArtifact, run_id: str) -> tuple[Path, str, str, int]:
        destination = self.local_path(artifact)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        incoming = self.config.incoming_root / f"{artifact.source_key}.db.partial"
        if not incoming.exists() and destination.is_file():
            clone_or_copy(destination, incoming)
        manifest = self.remote.snapshot_database(artifact.remote_path)
        snapshot_path = str(manifest["snapshot"])
        try:
            self.remote.rsync(
                remote_path=snapshot_path,
                local_path=incoming,
                compress=False,
            )
            incoming.chmod(0o600)
            digest = sha256(incoming)
            expected = str(manifest["sha256"])
            if digest != expected:
                raise RuntimeError(
                    f"database checksum mismatch expected={expected} actual={digest}"
                )
            integrity = quick_check(incoming)
            if integrity != ["ok"]:
                raise RuntimeError(f"local database quick_check failed: {integrity}")
            written = incoming.stat().st_size
            os.replace(incoming, destination)
            destination.chmod(0o600)
            manifest_path = destination.parent / "manifest.json"
            manifest_payload = {
                **manifest,
                "local_path": str(destination),
                "synced_at": datetime.now(UTC).isoformat(),
                "remote_source_mtime_ns": artifact.mtime_ns,
            }
            temporary = manifest_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            os.replace(temporary, manifest_path)
            return destination, digest, expected, written
        finally:
            try:
                self.remote.cleanup_snapshot(snapshot_path)
            except Exception:
                # A later doctor/sync can clean stale cache. Never mask a verified transfer.
                pass

    def _sync_regular(self, artifact: RemoteArtifact, run_id: str) -> tuple[Path, str, int]:
        destination = self.local_path(artifact)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        compress = destination.suffix == ".gz"
        incoming = self.config.incoming_root / f"{artifact.source_key}.raw.partial"
        if not incoming.exists() and not compress and destination.is_file():
            clone_or_copy(destination, incoming)
        self.remote.rsync(
            remote_path=artifact.remote_path,
            local_path=incoming,
            compress=True,
        )
        return self._store_regular(artifact, incoming)

    def _store_regular(self, artifact: RemoteArtifact, incoming: Path) -> tuple[Path, str, int]:
        destination = self.local_path(artifact)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        incoming.chmod(0o600)
        digest = sha256(incoming)
        written = incoming.stat().st_size
        compress = destination.suffix == ".gz"
        if compress:
            compressed = self.config.incoming_root / (f"{artifact.source_key}.gz.tmp")
            compressed.unlink(missing_ok=True)
            with incoming.open("rb") as source, compressed.open("wb") as raw_target:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    compresslevel=1,
                    fileobj=raw_target,
                    mtime=0,
                ) as target:
                    shutil.copyfileobj(source, target, length=8 * 1024 * 1024)
            compressed.chmod(0o600)
            if gzip_content_sha256(compressed) != digest:
                compressed.unlink(missing_ok=True)
                raise RuntimeError("gzip round-trip checksum mismatch")
            os.replace(compressed, destination)
            incoming.unlink(missing_ok=True)
        else:
            os.replace(incoming, destination)
        destination.chmod(0o600)
        return destination, digest, written

    def local_path(self, artifact: RemoteArtifact) -> Path:
        source_root = (
            self.config.data_root
            / "sources"
            / _safe_component(self.config.ssh_host)
            / "jobs"
            / _safe_component(artifact.jenkins_job)
        )
        if artifact.kind == "jenkins_console":
            number = artifact.build_number or 0
            shard = f"{(number // 1000) * 1000:06d}"
            return source_root / "builds" / shard / f"{number}.log.gz"
        strategy = _safe_component(artifact.strategy or "unknown")
        runtime = _safe_component(artifact.runtime_job or "default")
        root = source_root / "strategies" / strategy / "runtime" / runtime
        remote_name = Path(artifact.remote_path).name
        if artifact.kind in {"database_live", "database_sim"}:
            return root / "databases" / "latest" / remote_name
        if artifact.kind == "database_safety":
            return root / "databases" / "safety" / remote_name
        if artifact.kind == "trade_csv":
            return root / "csv" / remote_name
        timestamp = (
            datetime.fromisoformat(artifact.completed_at)
            if artifact.completed_at
            else datetime.now(UTC)
        )
        log_root = root / "logs" / f"{timestamp.year:04d}" / f"{timestamp.month:02d}"
        stable_before = datetime.now(UTC) - timedelta(hours=36)
        return (
            log_root / f"{remote_name}.gz" if timestamp < stable_before else log_root / remote_name
        )

    def verify(self, *, job: str | None = None, strategy: str | None = None) -> dict[str, object]:
        rows = self.catalog.list_artifacts(job=job, strategy=strategy)
        checked = 0
        failed: list[str] = []
        for row in rows:
            path = Path(row["local_path"] or "")
            if not path.is_file():
                failed.append(f"missing: {path}")
                continue
            try:
                if row["kind"].startswith("database"):
                    integrity = quick_check(path)
                    if integrity != ["ok"]:
                        raise RuntimeError(f"quick_check={integrity}")
                    digest = sha256(path)
                elif path.suffix == ".gz":
                    digest = gzip_content_sha256(path)
                else:
                    digest = sha256(path)
                if digest != row["local_sha256"]:
                    raise RuntimeError("checksum mismatch")
                checked += 1
            except Exception as error:
                failed.append(f"{path}: {error}")
        return {
            "checked": checked,
            "failed": len(failed),
            "errors": failed,
            "status": "SUCCESS" if not failed else "FAILED",
        }

    def pin_database(self, source_key: str) -> Path:
        row = self.catalog.get_artifact(source_key)
        if row is None:
            raise ValueError(f"artifact not found: {source_key}")
        if not str(row["kind"]).startswith("database"):
            raise ValueError("only database artifacts can be pinned")
        source = Path(row["local_path"] or "")
        if not source.is_file():
            raise RuntimeError("synchronized database file is missing")
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = source.parent.parent / "pinned" / timestamp / source.name
        destination.parent.mkdir(parents=True, exist_ok=False, mode=0o700)
        clone_or_copy(source, destination)
        digest = sha256(destination)
        manifest = {
            "schema_version": 1,
            "source_key": source_key,
            "source": str(source),
            "pinned_path": str(destination),
            "sha256": digest,
            "quick_check": quick_check(destination),
            "created_at": datetime.now(UTC).isoformat(),
        }
        manifest_path = destination.parent / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_path.chmod(0o600)
        self.catalog.add_pin(
            pin_id=f"{source_key[:12]}-{timestamp}",
            source_key=source_key,
            pinned_path=destination,
            manifest=manifest,
        )
        return destination

    def prune_retention(self, *, apply: bool = False) -> dict[str, object]:
        """Prune expired logs while retaining catalog provenance."""
        cutoff = datetime.now(UTC) - timedelta(days=self.config.log_retention_days)
        protected = self._bundle_source_keys()
        candidates: list[tuple[str, Path]] = []
        for row in self.catalog.list_artifacts():
            if row["kind"] not in {"bot_log", "jenkins_console"}:
                continue
            if row["status"] != "SYNCED" or row["source_key"] in protected:
                continue
            metadata = json.loads(row["metadata_json"] or "{}")
            completed_at = metadata.get("completed_at")
            timestamp = (
                datetime.fromisoformat(completed_at)
                if completed_at
                else datetime.fromtimestamp(int(row["remote_mtime_ns"]) / 1_000_000_000, UTC)
            )
            if timestamp >= cutoff:
                continue
            path = Path(row["local_path"] or "")
            if path.is_file():
                candidates.append((str(row["source_key"]), path))
        bytes_reclaimable = sum(path.stat().st_size for _, path in candidates)
        if apply:
            root = self.config.data_root.resolve()
            for source_key, path in candidates:
                resolved = path.resolve()
                if not resolved.is_relative_to(root):
                    raise RuntimeError(f"refusing to prune outside data root: {path}")
                resolved.unlink()
                self.catalog.mark_retention_deleted(source_key)
        return {
            "retention_days": self.config.log_retention_days,
            "candidates": len(candidates),
            "bytes_reclaimable": bytes_reclaimable,
            "protected_by_bundle": len(protected),
            "applied": apply,
        }

    def _bundle_source_keys(self) -> set[str]:
        protected: set[str] = set()
        for manifest_path in self.config.bundles_root.glob("*/manifest.json"):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                protected.update(
                    str(item["source_key"])
                    for item in payload.get("artifacts", [])
                    if item.get("source_key")
                )
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return protected

    def _ensure_disk_capacity(self, estimated_bytes: int) -> None:
        free = shutil.disk_usage(self.config.data_root).free
        if free - estimated_bytes < self.config.minimum_free_bytes:
            raise RuntimeError(
                "sync would violate local free-space floor: "
                f"free={free}, planned={estimated_bytes}, "
                f"floor={self.config.minimum_free_bytes}"
            )

    @staticmethod
    def _progress(callback: ProgressCallback | None, **payload: object) -> None:
        if callback:
            callback(payload)
