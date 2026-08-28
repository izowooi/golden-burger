"""One bounded, accountless Jenkins research cycle."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from pathlib import Path
import time
from typing import Any, Iterator
from uuid import uuid4

from .api.clob_client import ClobClient, ClobClientPool
from .api.gamma_client import GammaClient, GammaFamilyPool
from .api.sports_client import SportsClockClient
from .api.transport import CycleBudget, PublicJsonTransport
from .collector import Collector
from .config import BotConfig, assert_safe_environment
from .db.repository import (
    ResearchRepository,
    SlotAlreadyClaimed,
    inspect_storage,
)
from .run_audit import ResearchRunAudit


@contextmanager
def exclusive_cycle_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError("cycle lock path cannot be a symlink")
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another Golden Coconut cycle is running") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ResearchBot:
    def __init__(self, config: BotConfig) -> None:
        assert_safe_environment()
        self.config = config

    def run(self, *, now: datetime | None = None) -> dict[str, Any]:
        assert_safe_environment()
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        preflight = inspect_storage(self.config.db_path, self.config.trading.storage)
        if preflight["guard_state"] == "STOP":
            raise RuntimeError("storage safety preflight rejected the cycle")
        lock_path = self.config.db_path.parent / ".coconut-cycle.lock"
        with exclusive_cycle_lock(lock_path):
            repository = ResearchRepository.prepare(self.config, now=current)
            repository.register_config()
            run_id = uuid4().hex
            try:
                slot = repository.claim_slot(run_id=run_id, now=current)
            except SlotAlreadyClaimed:
                return {
                    "status": "ALREADY_CLAIMED",
                    "job_name": self.config.job_name,
                    "slot_start_utc": current.replace(
                        minute=current.minute - current.minute % 5,
                        second=0,
                        microsecond=0,
                    ).isoformat().replace("+00:00", "Z"),
                }
            audit = ResearchRunAudit(self.config, run_id)
            repository.record_run_event(audit.event_row("STARTED", {"slot_start_utc": slot}))
            budget = CycleBudget(
                started_monotonic=time.monotonic(),
                cooperative_seconds=self.config.trading.cooperative_budget_seconds,
                stop_margin_seconds=self.config.trading.stop_margin_seconds,
                hard_seconds=self.config.trading.hard_cycle_seconds,
            )
            gamma_config = self.config.trading.gamma
            def new_transport() -> PublicJsonTransport:
                return PublicJsonTransport(
                    connect_timeout_seconds=gamma_config.connect_timeout_seconds,
                    read_timeout_seconds=gamma_config.read_timeout_seconds,
                    attempt_wall_seconds=gamma_config.attempt_wall_seconds,
                    max_retries=gamma_config.max_retries,
                    retry_base_seconds=gamma_config.retry_base_seconds,
                    retry_max_seconds=gamma_config.retry_max_seconds,
                    receipt_sink=repository.record_api_request,
                )

            gamma_pool = GammaFamilyPool(
                {
                    family.code: GammaClient(gamma_config, new_transport())
                    for family in self.config.registry.families
                },
                max_workers=gamma_config.parallel_family_workers,
            )
            clob_config = self.config.trading.clob
            clob_pool = ClobClientPool(
                tuple(
                    ClobClient(clob_config, new_transport())
                    for _ in range(clob_config.parallel_read_workers)
                ),
                max_workers=clob_config.parallel_read_workers,
            )
            collector = Collector(
                self.config,
                repository,
                gamma_pool,
                clob_pool,
                SportsClockClient(
                    self.config.trading.sports_feed, repository.record_api_request
                ),
            )
            try:
                product = collector.collect(
                    run_id, slot_start=slot, budget=budget, now=current
                )
                if product.fatal_error:
                    terminal = audit.event_row(
                        "FAILED",
                        {"error_type": "CollectionHealthGate", "error_message": product.fatal_error},
                    )
                    repository.publish_cycle(product.bundle, terminal_event=terminal)
                    raise RuntimeError(product.fatal_error)
                terminal = audit.event_row("SUCCEEDED", product.summary)
                repository.publish_cycle(product.bundle, terminal_event=terminal)
                return {"run_id": run_id, **product.summary}
            except BaseException as error:
                # If the terminal event was not atomically published with a
                # collection bundle, preserve an independent FAILED receipt.
                with repository.read_connect() as connection:
                    terminal_exists = bool(
                        connection.execute(
                            "SELECT 1 FROM research_run_events "
                            "WHERE run_id=? AND event_type='FAILED'",
                            (run_id,),
                        ).fetchone()
                    )
                if not terminal_exists:
                    repository.record_run_event(
                        audit.event_row(
                            "FAILED",
                            {
                                "error_type": type(error).__name__,
                                "error_message": str(error).replace("\n", " ")[:1000],
                            },
                        )
                    )
                raise
