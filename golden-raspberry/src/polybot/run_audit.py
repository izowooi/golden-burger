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
        cls, config: BotConfig, *, repository: ResearchRepository
    ) -> "ResearchRunAudit":
        repository.register_config(config, git_commit=_git_commit())
        audit = cls(config=config, repository=repository, run_id=uuid4().hex)
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

    def succeed(self, summary: dict[str, Any]) -> None:
        if self.terminal:
            raise RuntimeError("research run already has a terminal event")
        self._event("SUCCEEDED", summary)
        self.terminal = True

    def fail(self, error: BaseException) -> None:
        if self.terminal:
            return
        self._event("FAILED", {}, error=error)
        self.terminal = True


__all__ = ["ResearchRunAudit"]
