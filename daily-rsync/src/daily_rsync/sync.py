from __future__ import annotations

import gzip
import hashlib
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .catalog import Catalog
from .config import AppConfig
from .models import (
    JobInventory,
    RemoteArtifact,
    SyncPlan,
    SyncResult,
    read_research_database_contract,
    research_archive_date,
)
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
                "workspace_epochs": dict(self.config.workspace_epochs),
            }
        )
        if not payload.get("jobs_exists") or not payload.get("workspace_exists"):
            raise RuntimeError("remote Jenkins home contract failed")
        return payload

    def scan(
        self,
        *,
        job: str | None = None,
        days: int | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[JobInventory]:
        if (from_date is None) != (to_date is None):
            raise ValueError("from_date and to_date must be passed together")
        if from_date and to_date and from_date > to_date:
            raise ValueError("from_date must not be after to_date")
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=days or self.config.initial_log_days)
        archive_from_date = from_date or (cutoff.date() if days is not None else None)
        archive_to_date = to_date or (now.date() if days is not None else None)
        inventories = self.remote.scan(
            job=job,
            cutoff_epoch=cutoff.timestamp(),
            archive_from_date=archive_from_date,
            archive_to_date=archive_to_date,
        )
        inventories = [
            replace(
                inventory,
                artifacts=tuple(
                    replace(artifact, source=artifact.source or self.config.ssh_host)
                    for artifact in inventory.artifacts
                ),
            )
            for inventory in inventories
        ]
        for inventory in inventories:
            self.catalog.save_inventory(
                source=self.config.ssh_host,
                job=inventory.name,
                current_strategy=inventory.current_strategy,
                payload=asdict(inventory),
            )
            if job is not None:
                self.catalog.mark_source_missing(
                    source=self.config.ssh_host,
                    job=inventory.name,
                    observed_paths={artifact.remote_path for artifact in inventory.artifacts},
                    log_cutoff_ns=int(cutoff.timestamp() * 1_000_000_000),
                    archive_from_date=archive_from_date,
                    archive_to_date=archive_to_date,
                    include_canonical_databases=archive_from_date is None,
                )
        return inventories

    def create_plan(
        self,
        *,
        job: str,
        strategy: str | None = None,
        include_safety_databases: bool = False,
        days: int | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> SyncPlan:
        inventories = self.scan(
            job=job,
            days=days,
            from_date=from_date,
            to_date=to_date,
        )
        if not inventories:
            raise ValueError(f"Jenkins job not found: {job}")
        inventory = inventories[0]
        strategy_state = str(inventory.strategy_evidence.get("state") or "UNKNOWN")
        if strategy is None and strategy_state in {"AMBIGUOUS_CONFIG", "PENDING_DEPLOYMENT"}:
            raise ValueError(
                "strategy identity is not settled "
                f"({strategy_state}); inspect strategy_evidence and pass --strategy explicitly"
            )
        selected_strategy = strategy or inventory.current_strategy
        if not selected_strategy:
            raise ValueError("strategy could not be inferred; pass --strategy")
        workspace_epoch = self.config.workspace_epoch_for(inventory.workspace)
        if workspace_epoch and inventory.workspace_identity:
            self._resolve_workspace_epoch_conflicts(
                inventory=inventory,
                strategy=selected_strategy,
                workspace_epoch=workspace_epoch,
            )
        open_conflicts = self.catalog.list_open_conflicts(
            source=self.config.ssh_host,
            job=job,
            strategy=selected_strategy,
        )
        if open_conflicts:
            identifiers = ", ".join(
                f"#{row['id']}:{row['conflict_type']}" for row in open_conflicts
            )
            raise RuntimeError(
                "sync plan rejected because catalog has unresolved artifact conflict(s): "
                + identifiers
            )
        selected: list[RemoteArtifact] = []
        unchanged = 0
        conflicts: list[str] = []
        for raw_artifact in inventory.artifacts:
            artifact = replace(
                raw_artifact,
                source=raw_artifact.source or self.config.ssh_host,
            )
            self._validate_research_artifact_contract(
                artifact,
                from_date=from_date,
                to_date=to_date,
            )
            if artifact.kind == "jenkins_console":
                if artifact.strategy not in {None, selected_strategy}:
                    continue
            elif artifact.strategy != selected_strategy:
                continue
            if artifact.kind == "database_safety" and not include_safety_databases:
                continue
            destination = self.local_path(artifact, workspace_epoch=workspace_epoch)
            destination_conflict = self.catalog.destination_conflict(
                artifact=artifact,
                local_path=destination,
            )
            if destination_conflict is not None:
                self.catalog.record_conflict(
                    conflict_type="SOURCE_PATH_COLLISION",
                    source=self.config.ssh_host,
                    artifact=artifact,
                    local_path=destination,
                    existing=destination_conflict,
                    details={"workspace": inventory.workspace},
                )
                conflicts.append(
                    f"{artifact.remote_path} collides with "
                    f"{destination_conflict['remote_path']} at {destination}"
                )
                continue
            immutable_conflict = self.catalog.immutable_conflict(artifact)
            if immutable_conflict is not None:
                self.catalog.record_conflict(
                    conflict_type="IMMUTABLE_REMOTE_CHANGED",
                    source=self.config.ssh_host,
                    artifact=artifact,
                    local_path=destination,
                    existing=immutable_conflict,
                    details={
                        "old_fingerprint": immutable_conflict["remote_fingerprint"],
                        "new_fingerprint": artifact.fingerprint,
                    },
                )
                conflicts.append(f"immutable research archive changed: {artifact.remote_path}")
                continue
            if self.catalog.artifact_is_current(artifact):
                unchanged += 1
            else:
                selected.append(artifact)
        if conflicts:
            raise RuntimeError(
                "sync plan rejected due to evidence provenance conflict(s): " + "; ".join(conflicts)
            )
        selected.sort(
            key=lambda item: (
                0 if item.kind.startswith("database") else 1,
                item.build_number or 0,
                item.remote_path,
            )
        )
        if not inventory.workspace or not inventory.workspace_identity:
            raise RuntimeError("sync plan requires a validated workspace mount identity")
        plan = SyncPlan.create(
            source=self.config.ssh_host,
            jenkins_job=job,
            strategy=selected_strategy,
            workspace=inventory.workspace,
            workspace_identity=inventory.workspace_identity,
            workspace_epoch=workspace_epoch,
            artifacts=selected,
            skipped_unchanged=unchanged,
            include_safety_databases=include_safety_databases,
            from_date=from_date,
            to_date=to_date,
        )
        self._ensure_disk_capacity(plan.estimated_bytes)
        plan.write(self.config.plans_root)
        return plan

    def sync_job(
        self,
        *,
        job: str,
        strategy: str | None = None,
        include_safety_databases: bool = False,
        days: int | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        progress: ProgressCallback | None = None,
    ) -> SyncResult:
        """Plan and execute one job while cataloging pre-plan failures."""

        try:
            plan = self.create_plan(
                job=job,
                strategy=strategy,
                include_safety_databases=include_safety_databases,
                days=days,
                from_date=from_date,
                to_date=to_date,
            )
        except Exception as error:
            resolved_strategy = (
                strategy
                or self.catalog.current_strategy(source=self.config.ssh_host, job=job)
                or "unknown"
            )
            self.catalog.record_failed_attempt(
                source=self.config.ssh_host,
                job=job,
                strategy=resolved_strategy,
                phase="scan-plan",
                error=error,
            )
            raise
        return self.execute(plan, progress=progress)

    @staticmethod
    def _parse_artifact_date(value: str | None, *, label: str) -> date:
        try:
            parsed = date.fromisoformat(str(value))
        except (TypeError, ValueError):
            raise RuntimeError(f"research artifact has invalid {label}") from None
        if value != parsed.isoformat():
            raise RuntimeError(f"research artifact has non-canonical {label}")
        return parsed

    @classmethod
    def _validate_research_artifact_contract(
        cls,
        artifact: RemoteArtifact,
        *,
        from_date: date | None,
        to_date: date | None,
    ) -> None:
        if (
            artifact.kind == "database_research_archive"
            and artifact.data_contract != "research-full-v1"
        ):
            raise RuntimeError(
                "research archive is missing the required research-full-v1 contract"
            )
        if artifact.data_contract != "research-full-v1":
            return
        database_day = cls._parse_artifact_date(
            artifact.database_utc_date,
            label="database_utc_date",
        )
        declared_day = cls._parse_artifact_date(
            artifact.archive_date,
            label="archive_date",
        )
        if database_day != declared_day:
            raise RuntimeError(
                "research artifact archive_date does not match database_utc_date"
            )
        if artifact.kind == "database_research_archive":
            filename_day = research_archive_date(artifact.remote_path)
            if filename_day is None or filename_day != database_day:
                raise RuntimeError(
                    "research archive filename date does not match database_utc_date"
                )
        elif artifact.kind == "database_sim":
            if Path(artifact.remote_path).name != "trades_sim.db":
                raise RuntimeError("research active shard must be named trades_sim.db")
            if from_date is not None and to_date is not None:
                today = datetime.now(UTC).date()
                if database_day != today or not from_date <= today <= to_date:
                    raise RuntimeError(
                        "mutable active research shard cannot satisfy a historical UTC range"
                    )
        else:
            raise RuntimeError("research-full-v1 artifact has an unsupported database kind")

    def load_plan(self, plan_id: str) -> SyncPlan:
        path = self.config.plans_root / f"{plan_id}.json"
        if not path.is_file():
            raise ValueError(f"plan not found: {plan_id}")
        return SyncPlan.read(path)

    def execute(self, plan: SyncPlan, *, progress: ProgressCallback | None = None) -> SyncResult:
        run_id = uuid.uuid4().hex
        result = SyncResult(
            run_id=run_id,
            status="RUNNING",
            skipped=plan.skipped_unchanged,
        )
        self.catalog.begin_run(
            run_id=run_id,
            plan_id=plan.plan_id,
            source=self.config.ssh_host,
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
        try:
            try:
                self._preflight_plan(plan)
            except Exception as error:
                result.status = "FAILED"
                result.failed = 1
                result.errors.append(f"preflight: {type(error).__name__}: {error}")
                raise

            console_cache: dict[str, Path] = {}
            retention_deleted_console_paths: set[str] = set()
            console_artifacts = [
                artifact for artifact in plan.artifacts if artifact.kind == "jenkins_console"
            ]
            try:
                console_cache, retention_deleted_console_paths = self._prefetch_console_logs(
                    plan,
                    console_artifacts,
                    run_id,
                    progress,
                )
            except Exception as error:
                result.errors.append(
                    f"jenkins console batch: {type(error).__name__}: {error}"
                )
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
                    # Re-read marker and st_dev at every artifact boundary. For
                    # console batches this is an additional post-batch check;
                    # the transfer itself is protected inside _prefetch_console_logs.
                    self._validate_plan_workspace(plan)
                    catalog_artifact = artifact
                    if (
                        artifact.kind == "jenkins_console"
                        and artifact.remote_path in retention_deleted_console_paths
                    ):
                        self.catalog.record_remote_retention_deleted(
                            artifact,
                            source=plan.source,
                            metadata={
                                "completed_at": artifact.completed_at,
                                "status": artifact.status,
                                "retention_race_at": datetime.now(UTC).isoformat(),
                            },
                        )
                        result.skipped += 1
                    elif artifact.kind.startswith("database"):
                        (
                            local_path,
                            digest,
                            remote_digest,
                            written,
                            catalog_artifact,
                        ) = self._sync_database(
                            plan,
                            artifact,
                            run_id,
                        )
                    elif artifact.kind == "jenkins_console":
                        incoming = console_cache.get(artifact.remote_path)
                        if incoming is None:
                            raise RuntimeError("console log was not present in batch result")
                        local_path, digest, written = self._store_regular(
                            plan,
                            artifact,
                            incoming,
                        )
                        remote_digest = None
                    else:
                        local_path, digest, written = self._sync_regular(
                            plan,
                            artifact,
                            run_id,
                        )
                        remote_digest = None
                    if artifact.remote_path not in retention_deleted_console_paths:
                        self.catalog.upsert_artifact(
                            catalog_artifact,
                            source=plan.source,
                            local_path=local_path,
                            local_sha256=digest,
                            remote_sha256=remote_digest,
                            metadata={
                                "completed_at": catalog_artifact.completed_at,
                                "status": catalog_artifact.status,
                                "canonical": catalog_artifact.canonical,
                                "archive_date": catalog_artifact.archive_date,
                                "mode": catalog_artifact.mode,
                                "data_contract": catalog_artifact.data_contract,
                                "database_utc_date": catalog_artifact.database_utc_date,
                                "workspace": plan.workspace,
                                "workspace_epoch": plan.workspace_epoch,
                            },
                        )
                        result.transferred += 1
                        result.bytes_written += written
                except Exception as error:
                    result.failed += 1
                    result.errors.append(
                        f"{artifact.kind} {artifact.remote_path}: "
                        f"{type(error).__name__}: {error}"
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
                "SUCCESS"
                if result.failed == 0
                else "PARTIAL"
                if result.transferred
                else "FAILED"
            )
            return result
        except BaseException as error:
            if result.status == "RUNNING":
                result.status = "FAILED"
                result.failed += 1
                result.errors.append(f"aborted: {type(error).__name__}: {error}")
            raise
        finally:
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

    def _preflight_plan(self, plan: SyncPlan) -> None:
        if plan.source != self.config.ssh_host:
            raise RuntimeError(
                "persisted plan source does not match the configured SSH source: "
                f"{plan.source} != {self.config.ssh_host}"
            )
        self._ensure_disk_capacity(plan.estimated_bytes)
        if not plan.workspace or not plan.workspace_identity:
            raise RuntimeError("persisted plan lacks workspace mount identity; create a new plan")
        configured_epoch = self.config.workspace_epoch_for(plan.workspace)
        if plan.workspace_epoch != configured_epoch:
            raise RuntimeError(
                "persisted plan workspace epoch does not match current local configuration; "
                "create a new plan"
            )
        self._validate_plan_workspace(plan)
        open_conflicts = self.catalog.list_open_conflicts(
            source=self.config.ssh_host,
            job=plan.jenkins_job,
            strategy=plan.strategy,
        )
        if open_conflicts:
            raise RuntimeError(
                "catalog has unresolved artifact conflict(s): "
                + ", ".join(f"#{row['id']}:{row['conflict_type']}" for row in open_conflicts)
            )
        requested_from = date.fromisoformat(plan.from_date) if plan.from_date else None
        requested_to = date.fromisoformat(plan.to_date) if plan.to_date else None
        for artifact in plan.artifacts:
            if artifact.source != plan.source:
                raise RuntimeError("persisted plan contains an artifact from another SSH source")
            self._validate_research_artifact_contract(
                artifact,
                from_date=requested_from,
                to_date=requested_to,
            )
            self._ensure_artifact_provenance(plan, artifact)

    def _validate_plan_workspace(self, plan: SyncPlan) -> None:
        if not plan.workspace or not plan.workspace_identity:
            raise RuntimeError("persisted plan lacks workspace mount identity; create a new plan")
        self.remote.validate_workspace(
            job=plan.jenkins_job,
            expected_workspace=plan.workspace,
            expected_identity=plan.workspace_identity,
        )

    def _prefetch_console_logs(
        self,
        plan: SyncPlan,
        artifacts: list[RemoteArtifact],
        run_id: str,
        progress: ProgressCallback | None,
    ) -> tuple[dict[str, Path], set[str]]:
        if not artifacts:
            return {}, set()
        root = self.config.incoming_root / f"{run_id}-console"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        result: dict[str, Path] = {}
        retention_deleted: set[str] = set()
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
            self._validate_plan_workspace(plan)
            pending = [item.remote_path for item in batch]
            copied: set[str] = set()
            for attempt in range(3):
                existing = self.remote.existing_files(remote_paths=pending)
                retention_deleted.update(set(pending) - existing)
                if not existing:
                    break
                try:
                    self.remote.rsync_files(
                        remote_paths=[path for path in pending if path in existing],
                        local_root=root,
                    )
                except Exception:
                    refreshed = self.remote.existing_files(remote_paths=list(existing))
                    newly_missing = existing - refreshed
                    if not newly_missing or attempt == 2:
                        raise
                    retention_deleted.update(newly_missing)
                    pending = [path for path in pending if path in refreshed]
                    continue
                copied = existing
                break
            for artifact in batch:
                if artifact.remote_path in retention_deleted:
                    continue
                relative = Path(artifact.remote_path).relative_to(
                    Path(self.config.remote_jenkins_home)
                )
                incoming = root / relative
                if not incoming.is_file():
                    still_exists = self.remote.existing_files(
                        remote_paths=[artifact.remote_path]
                    )
                    if not still_exists:
                        retention_deleted.add(artifact.remote_path)
                        continue
                    raise RuntimeError(f"batch rsync omitted {artifact.remote_path}")
                if artifact.remote_path not in copied:
                    raise RuntimeError(f"batch rsync did not attest {artifact.remote_path}")
                result[artifact.remote_path] = incoming
        return result, retention_deleted

    def _sync_database(
        self,
        plan: SyncPlan,
        artifact: RemoteArtifact,
        run_id: str,
    ) -> tuple[Path, str, str, int, RemoteArtifact]:
        destination = self.local_path(
            artifact,
            workspace_epoch=plan.workspace_epoch,
        )
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        incoming = self.config.incoming_root / f"{artifact.source_key}.db.partial"
        if not incoming.exists() and destination.is_file():
            clone_or_copy(destination, incoming)
        try:
            manifest = self.remote.snapshot_database(
                artifact.remote_path,
                job=artifact.jenkins_job,
                expected_workspace=plan.workspace or "",
                expected_identity=plan.workspace_identity or {},
                expected_data_contract=artifact.data_contract,
                expected_database_utc_date=artifact.database_utc_date,
            )
        except Exception as error:
            message = str(error)
            if artifact.data_contract == "research-full-v1" and (
                "database UTC date changed" in message or "data contract changed" in message
            ):
                self.catalog.record_conflict(
                    conflict_type="RESEARCH_SNAPSHOT_DATE_CHANGED",
                    source=self.config.ssh_host,
                    artifact=artifact,
                    local_path=destination,
                    existing=self.catalog.get_artifact(artifact.source_key),
                    details={
                        "planned_data_contract": artifact.data_contract,
                        "planned_database_utc_date": artifact.database_utc_date,
                        "remote_snapshot_error": message,
                    },
                    status="OBSERVED",
                )
            raise
        snapshot_path = str(manifest["snapshot"])
        try:
            # A persisted plan can age while an active WAL grows. Re-check the
            # actual online-backup size before bringing it onto the local disk.
            self._ensure_disk_capacity(int(manifest.get("snapshot_size_bytes") or 0))
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
            self._validate_snapshot_research_contract(
                artifact=artifact,
                incoming=incoming,
                manifest=manifest,
                destination=destination,
            )
            written = incoming.stat().st_size
            observed_fingerprint = (
                str(manifest.get("source_fingerprint_after") or artifact.fingerprint or "") or None
            )
            source_members = manifest.get("source_members_after") or []
            observed_size = int(
                manifest.get("source_storage_bytes")
                or sum(int(item.get("size_bytes") or 0) for item in source_members)
                or artifact.size_bytes
            )
            observed_mtime_ns = max(
                [
                    artifact.mtime_ns,
                    *(int(item.get("mtime_ns") or 0) for item in source_members),
                ]
            )
            observed_completed_at = (
                artifact.completed_at
                if artifact.kind == "database_research_archive" and artifact.completed_at
                else datetime.fromtimestamp(
                    observed_mtime_ns / 1_000_000_000,
                    UTC,
                ).isoformat()
            )
            observed_artifact = replace(
                artifact,
                fingerprint=observed_fingerprint,
                size_bytes=observed_size,
                mtime_ns=observed_mtime_ns,
                completed_at=observed_completed_at,
            )
            if (
                artifact.kind == "database_research_archive"
                and artifact.fingerprint
                and observed_fingerprint
                and observed_fingerprint != artifact.fingerprint
            ):
                existing = self.catalog.get_artifact(artifact.source_key)
                self.catalog.record_conflict(
                    conflict_type="IMMUTABLE_REMOTE_CHANGED",
                    source=self.config.ssh_host,
                    artifact=observed_artifact,
                    local_path=destination,
                    existing=existing,
                    details={
                        "planned_fingerprint": artifact.fingerprint,
                        "snapshot_fingerprint": observed_fingerprint,
                    },
                )
                raise RuntimeError(
                    "immutable research archive changed after plan creation; "
                    "existing evidence was preserved"
                )
            if artifact.kind == "database_research_archive" and destination.is_file():
                existing = self.catalog.get_artifact(artifact.source_key)
                if existing is not None and sha256(destination) == digest:
                    old_fingerprint = existing["remote_fingerprint"]
                    if (
                        not old_fingerprint
                        or not observed_fingerprint
                        or (str(old_fingerprint) == observed_fingerprint)
                    ):
                        incoming.unlink(missing_ok=True)
                        return destination, digest, expected, 0, observed_artifact
                self.catalog.record_conflict(
                    conflict_type="IMMUTABLE_LOCAL_EXISTS",
                    source=self.config.ssh_host,
                    artifact=observed_artifact,
                    local_path=destination,
                    existing=existing,
                    details={
                        "existing_local_sha256": sha256(destination),
                        "incoming_sha256": digest,
                    },
                )
                raise RuntimeError("refusing to replace an existing immutable research archive")
            os.replace(incoming, destination)
            destination.chmod(0o600)
            manifest_path = destination.parent / "manifest.json"
            manifest_payload = {
                **manifest,
                "local_path": str(destination),
                "synced_at": datetime.now(UTC).isoformat(),
                "remote_source_mtime_ns": observed_artifact.mtime_ns,
                "remote_source_fingerprint": observed_fingerprint,
                "artifact_kind": artifact.kind,
                "canonical": artifact.canonical,
                "archive_date": artifact.archive_date,
                "mode": artifact.mode,
                "data_contract": artifact.data_contract,
                "database_utc_date": artifact.database_utc_date,
            }
            temporary = manifest_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            os.replace(temporary, manifest_path)
            return destination, digest, expected, written, observed_artifact
        finally:
            try:
                self.remote.cleanup_snapshot(snapshot_path)
            except Exception:
                # A later doctor/sync can clean stale cache. Never mask a verified transfer.
                pass

    def _validate_snapshot_research_contract(
        self,
        *,
        artifact: RemoteArtifact,
        incoming: Path,
        manifest: dict[str, Any],
        destination: Path,
    ) -> None:
        if artifact.data_contract != "research-full-v1":
            return
        contract = read_research_database_contract(incoming)
        planned_day = self._parse_artifact_date(
            artifact.database_utc_date,
            label="database_utc_date",
        )
        manifest_contract = manifest.get("data_contract")
        manifest_day = manifest.get("database_utc_date")
        mismatch = (
            contract is None
            or contract.contract_name != artifact.data_contract
            or contract.database_utc_date != planned_day
            or manifest_contract != artifact.data_contract
            or manifest_day != planned_day.isoformat()
        )
        if artifact.kind == "database_research_archive":
            mismatch = mismatch or research_archive_date(artifact.remote_path) != planned_day
        if not mismatch:
            return
        observed = replace(
            artifact,
            database_utc_date=(
                contract.database_utc_date.isoformat() if contract is not None else None
            ),
        )
        self.catalog.record_conflict(
            conflict_type="RESEARCH_SNAPSHOT_DATE_CHANGED",
            source=self.config.ssh_host,
            artifact=observed,
            local_path=destination,
            existing=self.catalog.get_artifact(artifact.source_key),
            details={
                "planned_data_contract": artifact.data_contract,
                "planned_database_utc_date": artifact.database_utc_date,
                "snapshot_data_contract": (
                    contract.contract_name if contract is not None else None
                ),
                "snapshot_database_utc_date": (
                    contract.database_utc_date.isoformat() if contract is not None else None
                ),
                "manifest_data_contract": manifest_contract,
                "manifest_database_utc_date": manifest_day,
            },
            # A midnight rollover is an observed failed attempt, not a permanent
            # immutable-evidence conflict. A fresh scan/plan can proceed.
            status="OBSERVED",
        )
        incoming.unlink(missing_ok=True)
        raise RuntimeError(
            "research database date/contract changed after plan creation; "
            "snapshot was rejected"
        )

    def _resolve_workspace_epoch_conflicts(
        self,
        *,
        inventory: JobInventory,
        strategy: str,
        workspace_epoch: str,
    ) -> None:
        """Acknowledge an explicit workspace move without merging its evidence.

        An epoch mapping is local operator intent. Existing evidence is checked
        in place and retained, while the new remote source is routed to a
        separate destination. Only previously recorded source-path collisions
        that exactly match the current validated inventory are resolved.
        """

        artifacts = {
            artifact.source_key: artifact
            for artifact in inventory.artifacts
            if artifact.source == self.config.ssh_host and artifact.strategy == strategy
        }
        observed_paths = {artifact.remote_path for artifact in inventory.artifacts}
        conflicts = self.catalog.list_open_conflicts(
            source=self.config.ssh_host,
            job=inventory.name,
            strategy=strategy,
        )
        for conflict in conflicts:
            if conflict["conflict_type"] != "SOURCE_PATH_COLLISION":
                continue
            artifact = artifacts.get(str(conflict["source_key"]))
            if artifact is None or artifact.kind == "jenkins_console":
                continue
            try:
                Path(artifact.remote_path).relative_to(Path(inventory.workspace))
            except ValueError:
                continue
            if conflict["existing_remote_path"] in observed_paths:
                continue

            destination = self.local_path(
                artifact,
                workspace_epoch=workspace_epoch,
            )
            if str(destination) == str(conflict["local_path"]):
                continue
            collision = self.catalog.destination_conflict(
                artifact=artifact,
                local_path=destination,
            )
            if collision is not None:
                continue
            current = self.catalog.get_artifact(artifact.source_key)
            if destination.exists() and (
                current is None or str(current["local_path"] or "") != str(destination)
            ):
                raise RuntimeError(
                    "workspace epoch destination already exists without matching provenance: "
                    f"{destination}"
                )

            existing_source_key = str(conflict["existing_source_key"] or "")
            existing = self.catalog.get_artifact(existing_source_key)
            if (
                not existing_source_key
                or existing is None
                or str(existing["local_path"] or "") != str(conflict["local_path"])
            ):
                continue
            self._verify_preserved_catalog_artifact(existing)
            identity_digest = hashlib.sha256(
                json.dumps(inventory.workspace_identity, sort_keys=True).encode()
            ).hexdigest()
            self.catalog.resolve_source_path_collision(
                conflict_id=int(conflict["id"]),
                source_key=artifact.source_key,
                existing_source_key=existing_source_key,
                resolution={
                    "contract": "explicit-workspace-epoch-v1",
                    "workspace": inventory.workspace,
                    "workspace_epoch": workspace_epoch,
                    "workspace_identity_sha256": identity_digest,
                    "routed_local_path": str(destination),
                    "preserved_local_path": str(existing["local_path"]),
                    "preserved_local_sha256": str(existing["local_sha256"]),
                },
            )

    @staticmethod
    def _verify_preserved_catalog_artifact(row: Any) -> None:
        path = Path(row["local_path"] or "")
        if not path.is_file():
            raise RuntimeError(f"preserved collision evidence is missing: {path}")
        if str(row["kind"]).startswith("database"):
            integrity = quick_check(path)
            if integrity != ["ok"]:
                raise RuntimeError(f"preserved database quick_check failed: {integrity}")
            digest = sha256(path)
        elif path.suffix == ".gz":
            digest = gzip_content_sha256(path)
        else:
            digest = sha256(path)
        if not row["local_sha256"] or digest != str(row["local_sha256"]):
            raise RuntimeError(f"preserved collision evidence checksum mismatch: {path}")

    def _ensure_artifact_provenance(
        self,
        plan: SyncPlan,
        artifact: RemoteArtifact,
    ) -> None:
        destination = self.local_path(
            artifact,
            workspace_epoch=plan.workspace_epoch,
        )
        collision = self.catalog.destination_conflict(
            artifact=artifact,
            local_path=destination,
        )
        if collision is not None:
            self.catalog.record_conflict(
                conflict_type="SOURCE_PATH_COLLISION",
                source=plan.source,
                artifact=artifact,
                local_path=destination,
                existing=collision,
                details={"phase": "execute"},
            )
            raise RuntimeError(
                "remote source path changed but maps to an existing local destination: "
                f"{artifact.remote_path} -> {destination}"
            )
        immutable = self.catalog.immutable_conflict(artifact)
        if immutable is not None:
            self.catalog.record_conflict(
                conflict_type="IMMUTABLE_REMOTE_CHANGED",
                source=plan.source,
                artifact=artifact,
                local_path=destination,
                existing=immutable,
                details={"phase": "execute"},
            )
            raise RuntimeError(
                f"immutable research archive fingerprint changed: {artifact.remote_path}"
            )

    def _sync_regular(
        self,
        plan: SyncPlan,
        artifact: RemoteArtifact,
        run_id: str,
    ) -> tuple[Path, str, int]:
        destination = self.local_path(
            artifact,
            workspace_epoch=plan.workspace_epoch,
        )
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
        return self._store_regular(plan, artifact, incoming)

    def _store_regular(
        self,
        plan: SyncPlan,
        artifact: RemoteArtifact,
        incoming: Path,
    ) -> tuple[Path, str, int]:
        destination = self.local_path(
            artifact,
            workspace_epoch=plan.workspace_epoch,
        )
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

    def local_path(
        self,
        artifact: RemoteArtifact,
        *,
        workspace_epoch: str | None = None,
    ) -> Path:
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
        if workspace_epoch:
            source_root = source_root / "workspace-epochs" / _safe_component(workspace_epoch)
        strategy = _safe_component(artifact.strategy or "unknown")
        runtime = _safe_component(artifact.runtime_job or "default")
        root = source_root / "strategies" / strategy / "runtime" / runtime
        remote_name = Path(artifact.remote_path).name
        if artifact.kind in {"database_live", "database_sim"}:
            return root / "databases" / "latest" / remote_name
        if artifact.kind == "database_research_archive":
            archive_date = research_archive_date(remote_name)
            if archive_date is None:
                raise ValueError(f"invalid research archive filename: {remote_name}")
            return (
                root
                / "databases"
                / "research"
                / f"{archive_date.year:04d}"
                / f"{archive_date.month:02d}"
                / f"{archive_date.day:02d}"
                / remote_name
            )
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

    def verify(
        self,
        *,
        job: str | None = None,
        strategy: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> dict[str, object]:
        if (from_date is None) != (to_date is None):
            raise ValueError("from_date and to_date must be passed together")
        if from_date and to_date and from_date > to_date:
            raise ValueError("from_date must not be after to_date")
        rows = self.catalog.list_artifacts(
            source=self.config.ssh_host,
            job=job,
            strategy=strategy,
        )
        open_conflicts = self.catalog.list_open_conflicts(
            source=self.config.ssh_host,
            job=job,
            strategy=strategy,
        )
        archive_coverage: dict[str, object] | None = None
        if from_date and to_date:
            rows = [
                row
                for row in rows
                if (row["kind"] == "database_research_archive" or self._is_research_active(row))
                and self._artifact_in_range(row, from_date=from_date, to_date=to_date)
            ]
            archive_coverage = self._archive_coverage(
                rows,
                open_conflicts=open_conflicts,
                from_date=from_date,
                to_date=to_date,
            )
            runtime_names = {str(row["runtime_job"] or "default") for row in rows} | {
                str(row["runtime_job"] or "default") for row in open_conflicts
            }
            runtime_coverages = {
                runtime_job: self._archive_coverage(
                    [row for row in rows if str(row["runtime_job"] or "default") == runtime_job],
                    open_conflicts=[
                        row for row in open_conflicts if row["runtime_job"] in {None, runtime_job}
                    ],
                    from_date=from_date,
                    to_date=to_date,
                )
                for runtime_job in sorted(runtime_names)
            }
            complete_runtime_jobs = [
                runtime_job
                for runtime_job, coverage in runtime_coverages.items()
                if coverage["complete"]
            ]
            archive_coverage["runtime_jobs"] = runtime_coverages
            archive_coverage["complete_runtime_jobs"] = complete_runtime_jobs
            archive_coverage["complete"] = bool(
                archive_coverage["complete"] and complete_runtime_jobs
            )
        if not rows and not open_conflicts and archive_coverage is None:
            return {
                "checked": 0,
                "skipped_retention_deleted": 0,
                "failed": 0,
                "errors": ["no synchronized artifacts match the requested identity"],
                "status": "NOT_FOUND",
            }
        checked = 0
        skipped_retention_deleted = 0
        failed = [
            "open catalog provenance conflict "
            f"#{row['id']} ({row['conflict_type']}): {row['remote_path']}"
            for row in open_conflicts
        ]
        if archive_coverage is not None:
            missing_dates = list(archive_coverage["missing_dates"])
            unavailable_dates = list(archive_coverage["unavailable_dates"])
            conflicted_dates = list(archive_coverage["conflicted_dates"])
            source_missing_unproven_dates = list(archive_coverage["source_missing_unproven_dates"])
            partial_active_dates = list(archive_coverage["partial_active_dates"])
            if missing_dates:
                failed.append("missing research archive date(s): " + ", ".join(missing_dates))
            if unavailable_dates:
                failed.append(
                    "unavailable research archive date(s): " + ", ".join(unavailable_dates)
                )
            if conflicted_dates:
                failed.append("conflicted research archive date(s): " + ", ".join(conflicted_dates))
            if source_missing_unproven_dates:
                failed.append(
                    "SOURCE_MISSING archive lacks full UTC-day cutoff evidence: "
                    + ", ".join(source_missing_unproven_dates)
                )
            if partial_active_dates:
                failed.append(
                    "current mutable research shard is partial, not a completed UTC-day archive: "
                    + ", ".join(partial_active_dates)
                )
            if (
                archive_coverage["covered_date_count"] == archive_coverage["requested_date_count"]
                and not archive_coverage["complete_runtime_jobs"]
                and not open_conflicts
            ):
                failed.append(
                    "requested dates are split across runtime jobs; "
                    "no single runtime has complete archive coverage"
                )
        for row in rows:
            if row["status"] == "RETENTION_DELETED":
                skipped_retention_deleted += 1
                continue
            if row["status"] in {"IMMUTABLE_CONFLICT", "PROVENANCE_CONFLICT"}:
                failed.append(
                    f"catalog provenance conflict ({row['status']}): {row['remote_path']}"
                )
                continue
            path = Path(row["local_path"] or "")
            if not path.is_file():
                failed.append(f"missing: {path}")
                continue
            try:
                if row["kind"].startswith("database"):
                    integrity = quick_check(path)
                    if integrity != ["ok"]:
                        raise RuntimeError(f"quick_check={integrity}")
                    self._validate_local_research_row(row, path)
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
            "skipped_retention_deleted": skipped_retention_deleted,
            "failed": len(failed),
            "errors": failed,
            "status": "SUCCESS" if not failed else "FAILED",
            "archive_coverage": archive_coverage,
            "open_artifact_conflicts": [self._conflict_location(row) for row in open_conflicts],
        }

    @classmethod
    def _validate_local_research_row(cls, row: Any, path: Path) -> None:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        if (
            row["kind"] == "database_research_archive"
            and metadata.get("data_contract") != "research-full-v1"
        ):
            raise RuntimeError(
                "local research archive is missing the research-full-v1 catalog contract"
            )
        if metadata.get("data_contract") != "research-full-v1":
            return
        contract = read_research_database_contract(path)
        if contract is None or contract.contract_name != "research-full-v1":
            raise RuntimeError("local research database contract is missing or mismatched")
        declared_raw = metadata.get("database_utc_date") or metadata.get("archive_date")
        try:
            declared_day = date.fromisoformat(str(declared_raw))
        except (TypeError, ValueError):
            raise RuntimeError("catalog research database_utc_date is invalid") from None
        if contract.database_utc_date != declared_day:
            raise RuntimeError(
                "catalog date does not match local database_utc_date: "
                f"catalog={declared_day} database={contract.database_utc_date}"
            )
        if row["kind"] == "database_research_archive":
            filename_day = research_archive_date(str(row["remote_path"]))
            if filename_day != contract.database_utc_date:
                raise RuntimeError(
                    "archive filename date does not match local database_utc_date"
                )

    def locate_evidence(
        self,
        *,
        job: str | None = None,
        strategy: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> dict[str, object]:
        """Return compact, machine-readable local evidence locations.

        Jenkins job, strategy and runtime job are separate identities. A strategy-only
        query can therefore return several deployments, while one deployment can contain
        several runtime databases.
        """
        if not job and not strategy:
            raise ValueError("pass at least one of job or strategy")
        if (from_date is None) != (to_date is None):
            raise ValueError("from_date and to_date must be passed together")
        if from_date and to_date and from_date > to_date:
            raise ValueError("from_date must not be after to_date")

        rows = self.catalog.list_artifacts(
            source=self.config.ssh_host,
            job=job,
            strategy=strategy,
        )
        conflicts = self.catalog.list_open_conflicts(
            source=self.config.ssh_host,
            job=job,
            strategy=strategy,
        )
        deployments: dict[tuple[str, str, str], list[Any]] = {}
        conflicts_by_deployment: dict[tuple[str, str, str], list[Any]] = {}
        for row in rows:
            deployment_strategy = str(row["strategy"] or "unknown")
            key = (str(row["source"]), str(row["jenkins_job"]), deployment_strategy)
            deployments.setdefault(key, []).append(row)
        for conflict in conflicts:
            source_name = str(conflict["source"])
            job_name = str(conflict["jenkins_job"])
            if conflict["strategy"] is None:
                keys = [key for key in deployments if key[0] == source_name and key[1] == job_name]
            else:
                keys = [(source_name, job_name, str(conflict["strategy"]))]
            if not keys:
                keys = [(source_name, job_name, str(strategy or "unknown"))]
            for key in keys:
                deployments.setdefault(key, [])
                conflicts_by_deployment.setdefault(key, []).append(conflict)

        matches: list[dict[str, object]] = []
        for (source, jenkins_job, deployment_strategy), deployment_rows in sorted(
            deployments.items()
        ):
            deployment_key = (source, jenkins_job, deployment_strategy)
            deployment_conflicts = [
                conflict
                for conflict in conflicts
                if str(conflict["source"]) == source
                and str(conflict["jenkins_job"]) == jenkins_job
                and (
                    conflict["strategy"] is None or str(conflict["strategy"]) == deployment_strategy
                )
            ]
            if not deployment_conflicts:
                deployment_conflicts = conflicts_by_deployment.get(deployment_key, [])
            console_rows = [row for row in deployment_rows if row["kind"] == "jenkins_console"]
            runtime_rows: dict[str, list[Any]] = {}
            for row in deployment_rows:
                if row["kind"] == "jenkins_console":
                    continue
                runtime_job = str(row["runtime_job"] or "default")
                runtime_rows.setdefault(runtime_job, []).append(row)
            for conflict in deployment_conflicts:
                runtime_rows.setdefault(str(conflict["runtime_job"] or "default"), [])

            latest_attempt = None
            latest_run = None
            if deployment_strategy != "unknown":
                latest_attempt = self._sync_run_location(
                    self.catalog.latest_sync_run(
                        source=source,
                        job=jenkins_job,
                        strategy=deployment_strategy,
                    )
                )
                latest_run = self._sync_run_location(
                    self.catalog.latest_sync_run(
                        source=source,
                        job=jenkins_job,
                        strategy=deployment_strategy,
                        successful_only=True,
                    )
                )

            latest_attempt_succeeded = (
                latest_attempt is not None and latest_attempt["status"] == "SUCCESS"
            )

            runtimes: list[dict[str, object]] = []
            database_available = False
            runtime_coverages: dict[str, dict[str, object]] = {}
            for runtime_job, scoped_rows in sorted(runtime_rows.items()):
                runtime_conflicts = [
                    conflict
                    for conflict in deployment_conflicts
                    if conflict["runtime_job"] in {None, runtime_job}
                ]
                canonical_rows = [
                    row
                    for row in scoped_rows
                    if row["kind"] in {"database_live", "database_sim"}
                    and self._artifact_in_range(row, from_date=from_date, to_date=to_date)
                ]
                research_rows = [
                    row
                    for row in scoped_rows
                    if row["kind"] == "database_research_archive"
                    and self._archive_in_range(row, from_date=from_date, to_date=to_date)
                ]
                safety_rows = [row for row in scoped_rows if row["kind"] == "database_safety"]
                canonical_databases = [self._database_location(row) for row in canonical_rows]
                research_archives = [self._database_location(row) for row in research_rows]
                safety_databases = [self._database_location(row) for row in safety_rows]
                databases = canonical_databases + research_archives + safety_databases
                runtime_coverage = None
                if from_date and to_date:
                    runtime_coverage = self._archive_coverage(
                        [
                            *research_rows,
                            *[row for row in canonical_rows if self._is_research_active(row)],
                        ],
                        open_conflicts=runtime_conflicts,
                        from_date=from_date,
                        to_date=to_date,
                    )
                    runtime_coverages[runtime_job] = runtime_coverage
                    runtime_database_available = bool(runtime_coverage["complete"])
                else:
                    runtime_database_available = any(
                        bool(item["available"]) and item["status"] in {"SYNCED", "SOURCE_MISSING"}
                        for item in databases
                    )
                database_available = database_available or runtime_database_available
                runtimes.append(
                    {
                        "runtime_job": runtime_job,
                        "databases": databases,
                        "current_databases": canonical_databases,
                        "research_archives": research_archives,
                        "safety_databases": safety_databases,
                        "archive_coverage": runtime_coverage,
                        "open_artifact_conflicts": [
                            self._conflict_location(row) for row in runtime_conflicts
                        ],
                        "bot_logs": self._log_location(
                            [row for row in scoped_rows if row["kind"] == "bot_log"]
                        ),
                        "other_artifacts": self._artifact_count(
                            [
                                row
                                for row in scoped_rows
                                if row["kind"] != "bot_log"
                                and not str(row["kind"]).startswith("database")
                            ]
                        ),
                    }
                )

            deployment_coverage = None
            if from_date and to_date:
                deployment_coverage = self._archive_coverage(
                    [
                        row
                        for row in deployment_rows
                        if (
                            row["kind"] == "database_research_archive"
                            or self._is_research_active(row)
                        )
                        and self._artifact_in_range(row, from_date=from_date, to_date=to_date)
                    ],
                    open_conflicts=deployment_conflicts,
                    from_date=from_date,
                    to_date=to_date,
                )
                complete_runtime_jobs = [
                    runtime_job
                    for runtime_job, coverage in runtime_coverages.items()
                    if coverage["complete"]
                ]
                deployment_coverage["runtime_jobs"] = runtime_coverages
                deployment_coverage["complete_runtime_jobs"] = complete_runtime_jobs
                deployment_coverage["complete"] = bool(
                    deployment_coverage["complete"] and complete_runtime_jobs
                )

            verify_parts = [
                "uv",
                "run",
                "daily-rsync",
                "verify",
                "--job",
                jenkins_job,
            ]
            if deployment_strategy != "unknown":
                verify_parts.extend(["--strategy", deployment_strategy])
            if from_date and to_date:
                verify_parts.extend(
                    [
                        "--from-date",
                        from_date.isoformat(),
                        "--to-date",
                        to_date.isoformat(),
                    ]
                )
            matches.append(
                {
                    "source": source,
                    "jenkins_job": jenkins_job,
                    "strategy": deployment_strategy,
                    "analysis_ready": (
                        database_available
                        and latest_run is not None
                        and latest_attempt_succeeded
                        and not deployment_conflicts
                    ),
                    "latest_sync_attempt": latest_attempt,
                    "latest_successful_sync": latest_run,
                    "jenkins_console_logs": self._log_location(console_rows),
                    "archive_coverage": deployment_coverage,
                    "open_artifact_conflicts": [
                        self._conflict_location(row) for row in deployment_conflicts
                    ],
                    "runtimes": runtimes,
                    "verification_command": shlex.join(verify_parts),
                }
            )

        query_coverage = None
        if from_date and to_date and not matches:
            query_coverage = self._archive_coverage(
                [],
                open_conflicts=conflicts,
                from_date=from_date,
                to_date=to_date,
            )
        return {
            "schema_version": 1,
            "catalog_path": str(self.config.catalog_path),
            "query": {
                "job": job,
                "strategy": strategy,
                "from_date": from_date.isoformat() if from_date else None,
                "to_date": to_date.isoformat() if to_date else None,
            },
            "match_count": len(matches),
            "matches": matches,
            "archive_coverage": query_coverage,
            "status": "FOUND" if matches else "NOT_FOUND",
        }

    @staticmethod
    def _sync_run_location(row: Any | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            key: row[key]
            for key in (
                "run_id",
                "plan_id",
                "status",
                "started_at",
                "finished_at",
                "transferred",
                "skipped",
                "failed",
                "bytes_written",
            )
        }

    @staticmethod
    def _database_location(row: Any) -> dict[str, object]:
        path = Path(row["local_path"] or "")
        metadata = json.loads(row["metadata_json"] or "{}")
        remote_mtime = datetime.fromtimestamp(
            int(row["remote_mtime_ns"]) / 1_000_000_000,
            UTC,
        ).isoformat()
        kind = str(row["kind"])
        archive_date = metadata.get("archive_date")
        if kind == "database_research_archive" and not archive_date:
            parsed_archive_date = research_archive_date(str(row["remote_path"]))
            archive_date = parsed_archive_date.isoformat() if parsed_archive_date else None
        return {
            "source_key": row["source_key"],
            "kind": kind,
            "canonical": bool(metadata.get("canonical", kind in {"database_live", "database_sim"})),
            "archive_date": archive_date,
            "mode": metadata.get(
                "mode",
                (
                    "sim"
                    if kind in {"database_sim", "database_research_archive"}
                    else "live"
                    if kind == "database_live"
                    else None
                ),
            ),
            "data_contract": metadata.get("data_contract"),
            "workspace": metadata.get("workspace"),
            "workspace_epoch": metadata.get("workspace_epoch"),
            "status": row["status"],
            "historical_source_missing": row["status"] == "SOURCE_MISSING",
            "remote_path": row["remote_path"],
            "local_path": str(path),
            "available": path.is_file(),
            "remote_size_bytes": int(row["remote_size_bytes"]),
            "remote_fingerprint": row["remote_fingerprint"],
            "source_completed_at": metadata.get("completed_at"),
            "source_mtime_at": remote_mtime,
            "local_sha256": row["local_sha256"],
            "remote_sha256": row["remote_sha256"],
            "synced_at": row["synced_at"],
        }

    @staticmethod
    def _archive_in_range(
        row: Any,
        *,
        from_date: date | None,
        to_date: date | None,
    ) -> bool:
        if from_date is None or to_date is None:
            return True
        archive_date = research_archive_date(str(row["remote_path"]))
        return archive_date is not None and from_date <= archive_date <= to_date

    @classmethod
    def _artifact_in_range(
        cls,
        row: Any,
        *,
        from_date: date | None,
        to_date: date | None,
    ) -> bool:
        if from_date is None or to_date is None:
            return True
        if row["kind"] == "database_research_archive":
            return cls._archive_in_range(row, from_date=from_date, to_date=to_date)
        if row["kind"] != "database_sim":
            return True
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        # A dated research archive is evidence only when both the catalog and
        # the SQLite payload attest the research-full contract.  Falling back
        # to filename/kind alone would let a legacy or malformed database
        # satisfy an otherwise strict UTC-day coverage request.
        if metadata.get("data_contract") != "research-full-v1":
            return False
        active_day = cls._research_row_date(row)
        current_day = datetime.now(UTC).date()
        return (
            active_day == current_day
            and from_date <= current_day <= to_date
        )

    @staticmethod
    def _is_research_active(row: Any) -> bool:
        if row["kind"] != "database_sim":
            return False
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return False
        return metadata.get("data_contract") == "research-full-v1"

    @classmethod
    def _research_row_date(cls, row: Any) -> date | None:
        archive_day = research_archive_date(str(row["remote_path"]))
        if archive_day is not None:
            return archive_day
        if not cls._is_research_active(row):
            return None
        path = Path(row["local_path"] or "")
        if path.is_file():
            try:
                contract = read_research_database_contract(path)
            except (OSError, RuntimeError, sqlite3.Error):
                return None
            if contract is not None:
                return contract.database_utc_date
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
            raw = metadata.get("database_utc_date") or metadata.get("archive_date")
            return date.fromisoformat(str(raw)) if raw else None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _requested_archive_dates(from_date: date, to_date: date) -> list[date]:
        return [
            from_date + timedelta(days=offset) for offset in range((to_date - from_date).days + 1)
        ]

    @staticmethod
    def _parse_utc_timestamp(value: object) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @classmethod
    def _source_missing_archive_proven(cls, row: Any, archive_day: date) -> bool:
        """Allow vanished immutable shards only with a full-day source cutoff."""
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        cutoff = cls._parse_utc_timestamp(
            metadata.get("source_completed_at") or metadata.get("completed_at")
        )
        if cutoff is None:
            try:
                cutoff = datetime.fromtimestamp(
                    int(row["remote_mtime_ns"]) / 1_000_000_000,
                    UTC,
                )
            except (KeyError, TypeError, ValueError, OSError, OverflowError):
                return False
        next_day = archive_day + timedelta(days=1)
        required_cutoff = datetime(
            next_day.year,
            next_day.month,
            next_day.day,
            tzinfo=UTC,
        )
        return cutoff >= required_cutoff

    @classmethod
    def _archive_coverage(
        cls,
        rows: list[Any],
        *,
        open_conflicts: list[Any],
        from_date: date,
        to_date: date,
    ) -> dict[str, object]:
        requested = cls._requested_archive_dates(from_date, to_date)
        by_date: dict[date, list[Any]] = {}
        for row in rows:
            archive_day = cls._research_row_date(row)
            if archive_day is not None and from_date <= archive_day <= to_date:
                by_date.setdefault(archive_day, []).append(row)

        conflict_dates: set[date] = set()
        blocking_conflict_ids: list[int] = []
        for conflict in open_conflicts:
            blocking_conflict_ids.append(int(conflict["id"]))
            raw_date = conflict["archive_date"]
            archive_day = None
            if raw_date:
                try:
                    archive_day = date.fromisoformat(str(raw_date))
                except ValueError:
                    archive_day = None
            if archive_day is None:
                archive_day = research_archive_date(str(conflict["remote_path"]))
            if archive_day is not None and from_date <= archive_day <= to_date:
                conflict_dates.add(archive_day)

        covered: list[str] = []
        missing: list[str] = []
        unavailable: list[str] = []
        conflicted: list[str] = []
        source_missing_unproven: list[str] = []
        partial_active: list[str] = []
        for archive_day in requested:
            label = archive_day.isoformat()
            candidates = by_date.get(archive_day, [])
            if archive_day in conflict_dates or any(
                row["status"] in {"IMMUTABLE_CONFLICT", "PROVENANCE_CONFLICT"} for row in candidates
            ):
                conflicted.append(label)
                continue
            if not candidates:
                missing.append(label)
                continue

            available = False
            unproven_source_missing = False
            has_valid_active = False
            for row in candidates:
                path = Path(row["local_path"] or "")
                if row["kind"] == "database_sim" and cls._is_research_active(row):
                    if (
                        archive_day == datetime.now(UTC).date()
                        and row["status"] == "SYNCED"
                        and path.is_file()
                        and cls._local_research_contract_matches(row, path, archive_day)
                    ):
                        has_valid_active = True
                    continue
                if row["status"] == "SYNCED" and path.is_file():
                    if cls._local_research_contract_matches(row, path, archive_day):
                        available = True
                        break
                if (
                    row["kind"] == "database_research_archive"
                    and row["status"] == "SOURCE_MISSING"
                    and path.is_file()
                ):
                    if (
                        cls._local_research_contract_matches(row, path, archive_day)
                        and cls._source_missing_archive_proven(row, archive_day)
                    ):
                        available = True
                        break
                    unproven_source_missing = True
            if available:
                covered.append(label)
            elif has_valid_active:
                partial_active.append(label)
            elif unproven_source_missing:
                source_missing_unproven.append(label)
            else:
                unavailable.append(label)

        return {
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "requested_dates": [value.isoformat() for value in requested],
            "covered_dates": covered,
            "missing_dates": missing,
            "unavailable_dates": unavailable,
            "conflicted_dates": conflicted,
            "source_missing_unproven_dates": source_missing_unproven,
            "partial_active_dates": partial_active,
            "blocking_conflict_ids": blocking_conflict_ids,
            "requested_date_count": len(requested),
            "covered_date_count": len(covered),
            "complete": (len(covered) == len(requested) and not blocking_conflict_ids),
        }

    @classmethod
    def _local_research_contract_matches(
        cls,
        row: Any,
        path: Path,
        expected_day: date,
    ) -> bool:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        if metadata.get("data_contract") != "research-full-v1":
            return True
        try:
            contract = read_research_database_contract(path)
        except (OSError, RuntimeError, sqlite3.Error):
            return False
        if (
            contract is None
            or contract.contract_name != "research-full-v1"
            or contract.database_utc_date != expected_day
        ):
            return False
        if row["kind"] == "database_research_archive":
            return research_archive_date(str(row["remote_path"])) == expected_day
        return row["kind"] == "database_sim" and expected_day == datetime.now(UTC).date()

    @staticmethod
    def _conflict_location(row: Any) -> dict[str, object]:
        return {
            key: row[key]
            for key in (
                "id",
                "detected_at",
                "conflict_type",
                "source_key",
                "source",
                "jenkins_job",
                "strategy",
                "runtime_job",
                "kind",
                "remote_path",
                "archive_date",
                "local_path",
                "status",
            )
        }

    @staticmethod
    def _artifact_count(rows: list[Any]) -> dict[str, int]:
        return {
            "cataloged": len(rows),
            "available": sum(Path(row["local_path"] or "").is_file() for row in rows),
        }

    @staticmethod
    def _log_location(rows: list[Any]) -> dict[str, object]:
        paths = [Path(row["local_path"] or "") for row in rows]
        available_paths = [path for path in paths if path.is_file()]
        local_root = None
        if available_paths:
            common = Path(os.path.commonpath([str(path) for path in available_paths]))
            local_root = str(common.parent if len(available_paths) == 1 else common)

        build_numbers = [
            int(row["build_number"]) for row in rows if row["build_number"] is not None
        ]
        completed_at: list[str] = []
        workspace_epochs: set[str] = set()
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            timestamp = metadata.get("completed_at")
            if timestamp:
                completed_at.append(str(timestamp))
            workspace_epoch = metadata.get("workspace_epoch")
            if workspace_epoch:
                workspace_epochs.add(str(workspace_epoch))
        return {
            "cataloged": len(rows),
            "available": len(available_paths),
            "remote_size_bytes": sum(int(row["remote_size_bytes"]) for row in rows),
            "local_size_bytes": sum(path.stat().st_size for path in available_paths),
            "local_root": local_root,
            "first_build": min(build_numbers) if build_numbers else None,
            "last_build": max(build_numbers) if build_numbers else None,
            "first_completed_at": min(completed_at) if completed_at else None,
            "last_completed_at": max(completed_at) if completed_at else None,
            "workspace_epochs": sorted(workspace_epochs),
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
        for row in self.catalog.list_artifacts(source=self.config.ssh_host):
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
            try:
                callback(payload)
            except BaseException:
                # Progress is an observer, never part of the evidence transaction.
                # UI disconnects/serialization bugs must not strand RUNNING rows.
                pass
