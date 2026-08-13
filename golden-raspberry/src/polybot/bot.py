"""Fail-closed orchestration for one Queue Echo research cycle."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import logging
from pathlib import Path
import shutil
from typing import Iterator
from uuid import uuid4

from .collector import ResearchCollector
from .config import BotConfig, assert_no_credentials
from .db.repository import GIB, ResearchRepository
from .run_audit import ResearchRunAudit


logger = logging.getLogger(__name__)


@contextmanager
def exclusive_job_run_lock(path: str | Path) -> Iterator[None]:
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Queue Echo job already holds lock: {lock_path}") from error
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
            raise ValueError("Golden Raspberry can never run live")
        if config.trading.lifecycle_mode != "archive_only":
            raise ValueError("Golden Raspberry lifecycle must be archive_only")
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
            raise RuntimeError("disk guard STOP: free space is below 30 GiB")
        if used_ratio >= storage.stop_used_ratio:
            raise RuntimeError("disk guard STOP: filesystem used ratio exceeded limit")

    def run(self) -> dict:
        # This is repeated immediately before any DB or HTTP construction so a
        # credential added after config resolution cannot open an order-bearing path.
        assert_no_credentials()
        self._precreation_disk_guard()
        lock_path = self.config.db_path.parent / ".raspberry.lock"
        with exclusive_job_run_lock(lock_path):
            self.repository.initialize(self.config)
            pre = self.repository.record_storage_metric(
                phase="preflight",
                storage=self.config.trading.storage,
                metric_id=uuid4().hex,
            )
            if pre["guard_state"] == "STOP":
                raise RuntimeError("disk guard STOP before public source collection")
            if pre["guard_state"] == "WARN":
                logger.warning(
                    "storage warning used_ratio=%.3f free_bytes=%s",
                    pre["filesystem_used_ratio"],
                    pre["filesystem_free_bytes"],
                )
            audit = ResearchRunAudit.start(self.config, repository=self.repository)
            try:
                collector = self.collector or ResearchCollector(
                    self.config, repository=self.repository
                )
                stats = collector.run_cycle(audit.run_id)
                post = self.repository.record_storage_metric(
                    phase="post_publish",
                    storage=self.config.trading.storage,
                    metric_id=uuid4().hex,
                    run_id=audit.run_id,
                )
                stats["storage"] = {
                    "db_bytes": post["db_bytes"],
                    "wal_bytes": post["wal_bytes"],
                    "filesystem_free_bytes": post["filesystem_free_bytes"],
                    "filesystem_used_ratio": post["filesystem_used_ratio"],
                    "guard_state": post["guard_state"],
                }
                if post["guard_state"] == "STOP":
                    raise RuntimeError("disk guard reached STOP after atomic publish")
            except BaseException as error:
                audit.fail(error)
                raise
            audit.succeed(stats)
            logger.info(
                "Queue Echo cycle complete run=%s shard=%s cycle=%s pages=%s "
                "source=%s eligible=%s panel=%s shard_markets=%s books=%s/%s "
                "qualified=%s cases=%s followups=%s runtime_s=%s db_bytes=%s",
                audit.run_id,
                stats["shard_index"],
                stats["cycle_number"],
                stats["gamma_pages"],
                stats["source_envelope_markets"],
                stats["eligible_markets"],
                stats["panel_markets"],
                stats["shard_markets"],
                stats["books_normalized"],
                stats["books_requested"],
                stats["qualified_by_arm"],
                stats["new_cases"],
                stats["followup_attempts"],
                stats["runtime_seconds"],
                stats["storage"]["db_bytes"],
            )
            return stats

    def status(self) -> dict:
        return self.repository.status()

    def health(self) -> dict:
        return self.repository.health(
            cadence_minutes=self.config.trading.cadence_minutes
        )


PolymarketBot = PolymarketResearchBot

__all__ = ["PolymarketBot", "PolymarketResearchBot", "exclusive_job_run_lock"]
