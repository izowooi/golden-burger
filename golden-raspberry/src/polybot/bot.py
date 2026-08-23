"""Fail-closed orchestration for one Queue Echo research cycle."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
import fcntl
import logging
from pathlib import Path
import shutil
import time
from typing import Callable, Iterator
from uuid import uuid4

from .collector import ResearchCollector
from .config import BotConfig, assert_no_credentials
from .db.repository import GIB, ResearchRepository
from .run_audit import ResearchRunAudit
from .utils.retry import CycleBudget, CycleBudgetExceeded, utc_now


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
        utcnow: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
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
        self.utcnow = utcnow
        self.monotonic = monotonic

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
        runtime = self.config.trading.runtime
        budget = CycleBudget(
            cooperative_seconds=runtime.cooperative_cycle_budget_seconds,
            hard_limit_seconds=runtime.hard_cycle_limit_seconds,
            network_stop_margin_seconds=runtime.network_stop_margin_seconds,
            clock=self.monotonic,
        )
        self._precreation_disk_guard()
        self.repository.initialize(self.config)
        invocation_id = uuid4().hex
        run_id = uuid4().hex
        slot_claim = self.repository.claim_cycle_slot(
            self.config,
            claimed_at=self.utcnow(),
            invocation_id=invocation_id,
            run_id=run_id,
        )
        if not slot_claim.accepted:
            result = {
                "status": slot_claim.event_type,
                "http_requests_allowed": False,
                "job_name": self.config.job_name,
                "slot_id": slot_claim.slot_id,
                "slot_at": slot_claim.slot_at,
                "claimed_at": slot_claim.claimed_at,
                "lateness_seconds": slot_claim.lateness_seconds,
            }
            logger.info(
                "Queue Echo invocation skipped job=%s slot=%s reason=%s lateness_s=%.3f",
                self.config.job_name,
                slot_claim.slot_id,
                slot_claim.event_type,
                slot_claim.lateness_seconds,
            )
            return result
        audit = ResearchRunAudit.start(
            self.config,
            repository=self.repository,
            run_id=run_id,
            slot_claim=asdict(slot_claim),
        )
        try:
            lock_path = self.config.db_path.parent / ".raspberry.lock"
            with exclusive_job_run_lock(lock_path):
                pre = self.repository.record_storage_metric(
                    phase="preflight",
                    storage=self.config.trading.storage,
                    metric_id=uuid4().hex,
                    run_id=audit.run_id,
                )
                if pre["guard_state"] == "STOP":
                    raise RuntimeError("disk guard STOP before public source collection")
                if pre["guard_state"] == "WARN":
                    logger.warning(
                        "storage warning used_ratio=%.3f free_bytes=%s",
                        pre["filesystem_used_ratio"],
                        pre["filesystem_free_bytes"],
                    )
                budget.checkpoint("before_collector")
                collector = self.collector or ResearchCollector(
                    self.config,
                    repository=self.repository,
                    budget=budget,
                )
                stats = collector.run_cycle(audit.run_id, budget=budget)
                budget.checkpoint("before_post_publish_metric", reserve_seconds=20)
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
                budget.checkpoint("before_terminal_success", reserve_seconds=10)
        except BaseException as error:
            phase = (
                error.phase
                if isinstance(error, CycleBudgetExceeded)
                else "cycle_exception"
            )
            audit.fail(
                error,
                terminal_evidence=budget.terminal_evidence(
                    status="FAILED", phase=phase
                ),
            )
            raise
        else:
            stats["slot"] = {
                "slot_id": slot_claim.slot_id,
                "slot_at": slot_claim.slot_at,
                "claimed_at": slot_claim.claimed_at,
                "lateness_seconds": slot_claim.lateness_seconds,
            }
            audit.succeed(
                stats,
                terminal_evidence=budget.terminal_evidence(
                    status="SUCCEEDED", phase="cycle_complete"
                ),
            )
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
