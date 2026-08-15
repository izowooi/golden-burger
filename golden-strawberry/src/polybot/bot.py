"""Fail-closed orchestration for one Last Mile collection cycle."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import logging
import os
from pathlib import Path
import shutil
from typing import Iterator
from uuid import uuid4

from .collector import ResearchCollector
from .config import BotConfig, assert_no_credentials
from .db.repository import GIB, ResearchRepository
from .run_audit import ResearchRunAudit


logger = logging.getLogger(__name__)
_EXPECTED_JENKINS_WORKSPACE = Path("/Volumes/t7/jenkins/polybot-shadow-one")
_WORKSPACE_MARKER = ".daily-rsync-workspace.json"


@contextmanager
def exclusive_job_run_lock(path: str | Path) -> Iterator[None]:
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                f"Golden Strawberry single-writer lock is already held: {lock_path}"
            ) from error
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _existing_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


class PolymarketResearchBot:
    def __init__(
        self,
        config: BotConfig,
        *,
        repository: ResearchRepository | None = None,
        collector: ResearchCollector | None = None,
    ) -> None:
        if not config.simulation_mode:
            raise ValueError("Golden Strawberry can never run live")
        if config.trading.lifecycle_mode != "archive_only":
            raise ValueError("Golden Strawberry lifecycle must remain archive_only")
        self.config = config
        self.repository = repository or ResearchRepository(
            config.db_path,
            busy_timeout_ms=config.trading.storage.busy_timeout_ms,
        )
        self.collector = collector

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
            raise RuntimeError("Strawberry DB is not rooted in the pinned workspace")
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
        if project_root.stat().st_dev != workspace.stat().st_dev:
            raise RuntimeError("Strawberry project is not on the workspace device")

    def run(self) -> dict:
        # Repeat immediately before the first filesystem write. This catches a
        # credential injected after configuration resolution and precedes the
        # lock, database, logger, and public HTTP session.
        assert_no_credentials()
        self._assert_runtime_workspace()
        self._precreation_disk_guard()
        lock_path = self.config.db_path.parent / ".strawberry.lock"
        with exclusive_job_run_lock(lock_path):
            self.repository.initialize(self.config)
            preflight = self.repository.record_storage_metric(
                phase="preflight",
                storage=self.config.trading.storage,
                metric_id=uuid4().hex,
            )
            if preflight["guard_state"] == "STOP":
                raise RuntimeError("disk guard STOP before public source collection")
            if preflight["guard_state"] == "WARN":
                logger.warning(
                    "storage warning used_ratio=%.3f free_bytes=%s",
                    preflight["filesystem_used_ratio"],
                    preflight["filesystem_free_bytes"],
                )
            audit = ResearchRunAudit.start(self.config, repository=self.repository)
            try:
                collector = self.collector or ResearchCollector(
                    self.config,
                    repository=self.repository,
                )
                summary = collector.run_cycle(audit.run_id)
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
                    raise RuntimeError(
                        "disk guard reached STOP after atomic publication"
                    )
            except BaseException as error:
                audit.fail(error)
                raise
            audit.succeed(summary)
            logger.info(
                "Last Mile cycle complete run=%s cycle=%s pages=%s membership=%s "
                "crossings=%s executable_episodes=%s paths=%s resolutions=%s runtime_s=%.3f",
                audit.run_id,
                summary["cycle_number"],
                summary["market_pages"],
                summary["membership_markets"],
                summary["new_crossings"],
                summary["new_executable_episodes"],
                summary["path_observations"],
                summary["resolution_observations"],
                summary["runtime_seconds"],
            )
            return summary

    def status(self) -> dict:
        return self.repository.status()

    def health(self) -> dict:
        return self.repository.health(
            cadence_minutes=self.config.trading.cadence_minutes
        )


__all__ = ["PolymarketResearchBot", "exclusive_job_run_lock"]
