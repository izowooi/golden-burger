"""Single-cycle accountless Cherry shadow runtime."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from uuid import uuid4

from .clients import ShadowClobClient, ShadowGammaClient
from .collector import ShadowCollector
from .config import ShadowConfig
from .db import ShadowRepository
from .safety import assert_shadow_boundary
from .transport import CollectionDeadline, PublicGetTransport
from ..utils.process_lock import DatabaseRunLock


logger = logging.getLogger(__name__)


class ShadowRuntime:
    def __init__(self, config: ShadowConfig) -> None:
        assert_shadow_boundary()
        self.config = config

    def run(self, *, now: datetime | None = None) -> dict[str, object]:
        assert_shadow_boundary()
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if current < self.config.experiment.start_utc:
            return {
                "skipped": True,
                "reason": "before_preregistered_start",
                "start_utc": self.config.experiment.start_utc.isoformat(),
            }
        if current >= self.config.experiment.followup_end_utc:
            return {
                "skipped": True,
                "reason": "after_preregistered_followup_end",
                "followup_end_utc": self.config.experiment.followup_end_utc.isoformat(),
            }
        with DatabaseRunLock(self.config.db_path) as run_lock:
            if not run_lock.acquired:
                return {
                    "skipped": True,
                    "reason": "shadow_db_process_lock_busy",
                    "owner_pid": run_lock.owner.get("pid"),
                    "owner_acquired_at": run_lock.owner.get("acquired_at"),
                }
            repository = ShadowRepository(self.config.db_path, self.config)
            repository.record_config()
            run_id = uuid4().hex
            repository.record_run_event(run_id, "STARTED")
            deadline = CollectionDeadline(self.config.collection_budget_seconds)
            try:
                transport = PublicGetTransport(
                    self.config.transport,
                    deadline,
                    repository.record_api_attempt,
                )
                result = ShadowCollector(
                    self.config,
                    repository,
                    ShadowGammaClient(self.config.gamma, transport),
                    ShadowClobClient(self.config.clob, transport),
                    deadline,
                ).collect(run_id, now=current)
                quick_check = repository.quick_check()
                if quick_check != "ok":
                    raise RuntimeError(f"shadow DB quick_check failed: {quick_check}")
                result["quick_check"] = quick_check
                result["config_hash"] = self.config.config_hash
                result["strategy_source_digest"] = self.config.strategy_source_digest
                result["preregistration_sha256"] = self.config.preregistration_sha256
                repository.record_run_event(run_id, "SUCCEEDED", result)
                return {"run_id": run_id, **result}
            except BaseException as error:
                repository.record_run_event(
                    run_id,
                    "FAILED",
                    {
                        "error_type": type(error).__name__,
                        "error_message": " ".join(str(error).splitlines())[:1000],
                        "elapsed_seconds": round(deadline.elapsed_seconds, 3),
                    },
                )
                logger.exception("Cherry shadow collection failed")
                raise
