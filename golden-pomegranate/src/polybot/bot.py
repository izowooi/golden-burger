"""Fail-closed runtime wrapper for one accountless research cycle."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import logging
from pathlib import Path
import shutil
import signal
import time
from typing import Iterator

from .collector import ResearchCollector
from .config import BotConfig, assert_no_credentials
from .db.repository import GIB, ResearchRepository
from .run_audit import ResearchRunAudit


logger = logging.getLogger(__name__)


class ResearchTermination(BaseException):
    """Process termination that secondary-source fallbacks must never swallow."""


@contextmanager
def exclusive_job_run_lock(lock_path: str | Path) -> Iterator[None]:
    """Acquire an application-level nonblocking exclusive lock."""
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                f"research job already holds exclusive lock: {path}"
            ) from error
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def graceful_termination() -> Iterator[None]:
    """Convert Jenkins TERM/INT into an exception the run audit can finalize."""
    previous: dict[int, object] = {}

    def terminate(signum, _frame):
        name = signal.Signals(signum).name
        raise ResearchTermination(f"research process received {name}")

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, terminate)
    except ValueError:
        # Python only permits signal registration on the main thread. Unit
        # callers in a worker thread still retain ordinary exception auditing.
        previous.clear()
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _existing_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


class PolymarketResearchBot:
    """Coordinates rotation, disk safety, RunAudit, and atomic collection."""

    def __init__(
        self,
        config: BotConfig,
        *,
        repository: ResearchRepository | None = None,
        collector: ResearchCollector | None = None,
    ) -> None:
        if not config.simulation_mode:
            raise ValueError("Golden Pomegranate can never run live")
        if config.trading.lifecycle_mode != "archive_only":
            raise ValueError("Golden Pomegranate lifecycle must be archive_only")
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
            raise RuntimeError(
                f"disk guard STOP: free bytes below {storage.min_free_gib:.0f} GiB floor"
            )
        if used_ratio >= storage.stop_used_ratio:
            raise RuntimeError(
                f"disk guard STOP: filesystem used ratio {used_ratio:.3f}"
            )

    def run(self) -> dict:
        assert_no_credentials()
        self._precreation_disk_guard()
        lock_path = self.config.db_path.parent / ".pomegranate.lock"
        with exclusive_job_run_lock(lock_path):
            contract_metadata = {
                "strategy_name": "golden-pomegranate",
                "job_name": self.config.job_name,
                "mode": "sim",
                "lifecycle_mode": self.config.trading.lifecycle_mode,
                "cadence_minutes": self.config.trading.cadence_minutes,
                "strategy_source_digest": self.config.trading.strategy_source_digest,
                "parser_version": "research-full-v1-parser-1",
                "gamma_endpoint": f"{self.config.trading.gamma.base_url}/markets/keyset",
                "data_trade_endpoint": f"{self.config.trading.data_api.base_url}/trades",
                "data_trade_query_contract": {
                    "takerOnly": True,
                    "limit": self.config.trading.data_api.trade_limit,
                    "safety_lag_seconds": self.config.trading.data_api.safety_lag_seconds,
                    "overlap_seconds": self.config.trading.data_api.overlap_seconds,
                },
                "orderbook_endpoint": f"{self.config.trading.orderbook.base_url}/books",
                "orderbook_sampler": "sha256-market-bucket-v1",
            }
            archived = self.repository.rotate_if_utc_day_changed(
                contract_metadata=contract_metadata
            )
            self.repository.initialize(contract_metadata=contract_metadata)
            cycle_number = self.repository.next_cycle_number()
            pre = self.repository.record_storage_metric(
                phase="preflight",
                storage=self.config.trading.storage,
                cadence_minutes=self.config.trading.cadence_minutes,
                cycle_number=cycle_number,
            )
            if pre["guard_state"] == "STOP":
                raise RuntimeError("disk guard STOP before network collection")
            if pre["guard_state"] == "WARN":
                logger.warning(
                    "storage warning used_ratio=%.3f forecast_next_day=%d",
                    pre["filesystem_used_ratio"],
                    pre["forecast_next_day_bytes"],
                )

            audit = ResearchRunAudit.start(self.config, repository=self.repository)
            cycle_started_clock = time.monotonic()
            termination = graceful_termination()
            termination.__enter__()
            try:
                collector = self.collector or ResearchCollector(
                    self.config, repository=self.repository
                )
                stats = collector.run_cycle(audit.run_id)
                post = self.repository.record_storage_metric(
                    phase="post_publish",
                    storage=self.config.trading.storage,
                    cadence_minutes=self.config.trading.cadence_minutes,
                    run_id=audit.run_id,
                    cycle_number=stats["cycle_number"],
                )
                stats["storage"] = {
                    "db_bytes": post["db_bytes"],
                    "wal_bytes": post["wal_bytes"],
                    "logical_bytes": post["logical_bytes"],
                    "forecast_next_day_bytes": post["forecast_next_day_bytes"],
                    "guard_state": post["guard_state"],
                }
                stats["storage_filesystem"] = {
                    "used_ratio": post.get("filesystem_used_ratio"),
                    "free_bytes": post.get("filesystem_free_bytes"),
                    "forecast_days_to_stop": post.get("forecast_days_to_stop"),
                }
                if stats.get("market_sweeps", 1) != 1:
                    raise RuntimeError(
                        "exactly one complete market_sweeps census is required"
                    )
                stats["storage_metrics"] = 2
                stats["runtime_seconds"] = round(
                    time.monotonic() - cycle_started_clock, 3
                )
                stats["rotated_archive"] = str(archived) if archived else None
                if post["guard_state"] == "STOP":
                    raise RuntimeError("disk guard reached STOP after atomic publish")
            except BaseException as error:
                try:
                    audit.fail(error)
                finally:
                    termination.__exit__(type(error), error, error.__traceback__)
                raise
            else:
                try:
                    audit.succeed(stats)
                finally:
                    termination.__exit__(None, None, None)
                logger.info(
                    "research cycle complete run=%s cycle=%s runtime_s=%.3f source=%s "
                    "pages=%s markets=%s outcomes=%s books=%s books_status=%s "
                    "trades=%s tape_status=%s possible_gap=%s watermark=%s "
                    "resolution=%s resolution_status=%s logical_bytes=%s "
                    "used_ratio=%.3f free_bytes=%s days_to_stop=%s issues=%s",
                    audit.run_id,
                    stats["cycle_number"],
                    stats["runtime_seconds"],
                    self.config.trading.strategy_source_digest[:12],
                    stats.get("gamma_page_count"),
                    stats["markets_observed"],
                    stats.get("outcomes_observed"),
                    stats["orderbooks_observed"],
                    stats.get("orderbook_component_status"),
                    stats["trades_observed"],
                    stats.get("trade_tape_component_status"),
                    stats.get("trade_tape_possible_gap"),
                    stats.get("trade_watermark_advanced_to"),
                    stats["resolution_observed"],
                    stats.get("resolution_component_status"),
                    stats["storage"]["logical_bytes"],
                    float(stats["storage_filesystem"]["used_ratio"] or 0.0),
                    stats["storage_filesystem"]["free_bytes"],
                    stats["storage_filesystem"].get("forecast_days_to_stop"),
                    stats["data_quality_issue_count"],
                )
                return stats

    def get_status(self) -> dict:
        return self.repository.status(
            self.config.trading.storage,
            cadence_minutes=self.config.trading.cadence_minutes,
        )

    def get_health(self) -> dict:
        return self.repository.health(
            self.config.trading.storage,
            cadence_minutes=self.config.trading.cadence_minutes,
        )


PolymarketBot = PolymarketResearchBot

__all__ = [
    "PolymarketBot",
    "PolymarketResearchBot",
    "exclusive_job_run_lock",
]
