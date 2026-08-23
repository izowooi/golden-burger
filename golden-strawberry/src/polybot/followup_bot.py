"""Fail-closed orchestration for the compact Last Mile follow-up v2."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any
from uuid import uuid4

from .bot import _existing_ancestor, exclusive_job_run_lock
from .config import assert_no_credentials
from .db.followup_repository import GIB, FollowupRepository
from .followup_collector import FollowupCollector, PhaseRecord
from .followup_config import FollowupConfig
from .followup_run_audit import FollowupRunAudit
from .utils.retry import iso_utc
from .v1_source import V1SourceReader


_EXPECTED_JENKINS_WORKSPACE = Path("/Volumes/t7/jenkins/polybot-shadow-one")
_WORKSPACE_MARKER = ".daily-rsync-workspace.json"


class FollowupBot:
    def __init__(
        self,
        config: FollowupConfig,
        *,
        repository: FollowupRepository | None = None,
        collector: FollowupCollector | None = None,
        source_reader: V1SourceReader | None = None,
        monotonic: Any = time.monotonic,
    ) -> None:
        if not config.simulation_mode:
            raise ValueError("Golden Strawberry follow-up can never run live")
        if config.trading.lifecycle_mode != "archive_only":
            raise ValueError("follow-up lifecycle must remain archive_only")
        self.config = config
        self.repository = repository or FollowupRepository(
            config.db_path,
            busy_timeout_ms=config.trading.storage.busy_timeout_ms,
        )
        self.collector = collector
        self.source_reader = source_reader or V1SourceReader(config.trading.v1_source)
        self.monotonic = monotonic

    def _precreation_disk_guard(self) -> None:
        storage = self.config.trading.storage
        usage = shutil.disk_usage(_existing_ancestor(self.config.db_path.parent))
        used_ratio = usage.used / usage.total if usage.total else 1.0
        if usage.free < storage.min_free_gib * GIB:
            raise RuntimeError("disk guard STOP: free space is below 100 GiB")
        if used_ratio >= storage.stop_used_ratio:
            raise RuntimeError("disk guard STOP: filesystem used ratio reached 90%")

    def _assert_runtime_workspace(self) -> None:
        if "JENKINS_URL" not in os.environ:
            return
        workspace_text = os.environ.get("WORKSPACE", "")
        if workspace_text != str(_EXPECTED_JENKINS_WORKSPACE):
            raise RuntimeError("Jenkins WORKSPACE is not the pinned T7 path")
        workspace = Path(workspace_text)
        if workspace.is_symlink() or not workspace.is_dir():
            raise RuntimeError("pinned Jenkins workspace is absent or unsafe")
        if workspace.resolve(strict=True) != _EXPECTED_JENKINS_WORKSPACE:
            raise RuntimeError("pinned Jenkins workspace canonical path changed")
        project_root = self.config.db_path.parents[2]
        if project_root.parent != workspace or project_root.name != "golden-strawberry":
            raise RuntimeError("follow-up DB is not rooted in the pinned workspace")
        expected_source = project_root / "data/strawberry-shadow-one/trades_sim.db"
        if self.config.trading.v1_source.db_path != expected_source:
            raise RuntimeError("v1 source path is not pinned under the trusted workspace")
        marker = workspace / _WORKSPACE_MARKER
        if marker.is_symlink() or not marker.is_file():
            raise RuntimeError("trusted daily-rsync workspace marker is missing")
        try:
            marker_payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError("trusted workspace marker is malformed") from error
        expected = {
            "schema_version": 1,
            "job": "polybot-shadow-one",
            "workspace": str(workspace),
        }
        if marker_payload != expected:
            raise RuntimeError("trusted workspace marker does not match this job")
        device = workspace.stat().st_dev
        if project_root.stat().st_dev != device:
            raise RuntimeError("follow-up project is not on the workspace device")
        if self.config.trading.v1_source.db_path.stat().st_dev != device:
            raise RuntimeError("v1 source DB is not on the trusted workspace device")

    def run(self) -> dict[str, Any]:
        assert_no_credentials()
        self._assert_runtime_workspace()
        self._precreation_disk_guard()
        if datetime.now(timezone.utc) >= self.config.trading.experiment.followup_end_utc:
            raise RuntimeError("follow-up window has ended; public source access is disabled")
        lock_path = self.config.db_path.parent / ".strawberry-followup-v2.lock"
        with exclusive_job_run_lock(lock_path):
            anchor_started_at = iso_utc()
            anchor_started = self.monotonic()
            snapshot = self.source_reader.capture()
            anchor_phase = PhaseRecord(
                name="v1_anchor_validation",
                started_at=anchor_started_at,
                completed_at=iso_utc(),
                elapsed_seconds=max(0.0, self.monotonic() - anchor_started),
                details={
                    "source_cycle_number": snapshot.anchor["source_cycle_number"],
                    "source_sweep_id": snapshot.anchor["source_sweep_id"],
                },
            )
            self.repository.initialize(self.config)
            anchor = self.repository.ensure_seed(snapshot)
            preflight = self.repository.record_storage_metric(
                phase="preflight",
                storage=self.config.trading.storage,
                metric_id=uuid4().hex,
            )
            if preflight["guard_state"] == "STOP":
                raise RuntimeError("disk guard STOP before public source collection")
            audit = FollowupRunAudit.start(
                self.config,
                repository=self.repository,
                anchor_sha256=str(anchor["anchor_sha256"]),
            )
            try:
                collector = self.collector or FollowupCollector(
                    self.config,
                    repository=self.repository,
                    monotonic=self.monotonic,
                )
                summary = collector.run_cycle(
                    audit.run_id,
                    anchor=anchor,
                    initial_phases=(anchor_phase,),
                )
                post = self.repository.record_storage_metric(
                    phase="post_publish",
                    storage=self.config.trading.storage,
                    metric_id=uuid4().hex,
                    run_id=audit.run_id,
                )
                summary["storage"] = {
                    "db_bytes": post["db_bytes"],
                    "journal_bytes": post["journal_bytes"],
                    "filesystem_free_bytes": post["filesystem_free_bytes"],
                    "filesystem_used_ratio": post["filesystem_used_ratio"],
                    "guard_state": post["guard_state"],
                }
                if post["guard_state"] == "STOP":
                    raise RuntimeError("disk guard reached STOP after v2 publication")
            except BaseException as error:
                audit.fail(error)
                raise
            audit.succeed(summary)
            return summary

    def status(self) -> dict[str, Any]:
        return self.repository.lightweight_status()

    def health(self) -> dict[str, Any]:
        status = self.repository.lightweight_status()
        if not status.get("database_exists"):
            return status
        latest = status.get("latest_cycle") or {}
        latest_at = latest.get("completed_at")
        age_minutes = None
        if latest_at:
            observed = datetime.fromisoformat(str(latest_at).replace("Z", "+00:00"))
            age_minutes = (
                datetime.now(timezone.utc) - observed.astimezone(timezone.utc)
            ).total_seconds() / 60
        latest_total = None
        with self.repository.read_connect() as connection:
            row = connection.execute(
                """
                SELECT elapsed_seconds FROM phase_timings
                WHERE phase_name='total' ORDER BY completed_at DESC LIMIT 1
                """
            ).fetchone()
            if row is not None:
                latest_total = float(row[0])
        healthy = bool(
            status.get("healthy")
            and age_minutes is not None
            and age_minutes <= self.config.trading.cadence_minutes * 2.5
            and latest_total is not None
            and latest_total < self.config.trading.runtime.sla_seconds
        )
        return {
            **status,
            "healthy": healthy,
            "latest_success_age_minutes": age_minutes,
            "latest_total_seconds": latest_total,
            "runtime_sla_seconds": self.config.trading.runtime.sla_seconds,
            "deep_check_performed": False,
        }


__all__ = ["FollowupBot"]
