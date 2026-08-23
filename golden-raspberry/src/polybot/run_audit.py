"""Immutable run lifecycle for accountless research collection."""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
from typing import Any
from uuid import uuid4

from .config import BotConfig, PROJECT_ROOT
from .db.repository import ResearchRepository
from .utils.retry import iso_utc


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if len(value) == 40 else None


@dataclass
class ResearchRunAudit:
    config: BotConfig
    repository: ResearchRepository
    run_id: str
    terminal: bool = False

    @classmethod
    def start(
        cls,
        config: BotConfig,
        *,
        repository: ResearchRepository,
        run_id: str | None = None,
        slot_claim: dict[str, Any] | None = None,
    ) -> "ResearchRunAudit":
        repository.register_config(config, git_commit=_git_commit())
        audit = cls(
            config=config,
            repository=repository,
            run_id=run_id or uuid4().hex,
        )
        runtime = config.trading.runtime
        audit._event(
            "STARTED",
            {
                "strategy_name": "golden-raspberry",
                "job_name": config.job_name,
                "mode": "sim",
                "lifecycle_mode": "archive_only",
                "data_contract": config.trading.data_contract,
                "shard_index": config.trading.experiment.shard_index,
                "shard_count": config.trading.experiment.shard_count,
                "strategy_source_digest": config.trading.strategy_source_digest,
                "slot_claim": slot_claim,
                "cooperative_cycle_budget_seconds": runtime.cooperative_cycle_budget_seconds,
                "hard_cycle_limit_seconds": runtime.hard_cycle_limit_seconds,
                "network_stop_margin_seconds": runtime.network_stop_margin_seconds,
            },
        )
        return audit

    def _event(
        self,
        event_type: str,
        details: dict[str, Any],
        *,
        error: BaseException | None = None,
    ) -> None:
        self.repository.record_research_run_event(
            {
                "event_id": uuid4().hex,
                "run_id": self.run_id,
                "config_hash": self.config.config_hash,
                "event_type": event_type,
                "event_at": iso_utc(),
                "details_json": json.dumps(details, sort_keys=True, separators=(",", ":")),
                "error_type": type(error).__name__ if error else None,
                "error_message": str(error)[:1000] if error else None,
            }
        )

    def succeed(
        self,
        summary: dict[str, Any],
        *,
        terminal_evidence: dict[str, Any],
    ) -> None:
        if self.terminal:
            raise RuntimeError("research run already has a terminal event")
        self._event("SUCCEEDED", {**summary, **terminal_evidence})
        self.terminal = True

    def fail(
        self,
        error: BaseException,
        *,
        terminal_evidence: dict[str, Any],
    ) -> None:
        if self.terminal:
            return
        details = dict(terminal_evidence)
        evidence = getattr(error, "evidence", None)
        if callable(evidence):
            details["deadline_error"] = evidence()
        self._event("FAILED", details, error=error)
        self.terminal = True


__all__ = ["ResearchRunAudit"]
