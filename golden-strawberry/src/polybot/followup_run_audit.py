"""Durable run lifecycle for Golden Strawberry follow-up v2a."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .db.followup_repository import FollowupRepository
from .followup_config import FollowupConfig
from .run_audit import _git_commit
from .utils.retry import canonical_json, iso_utc


@dataclass
class FollowupRunAudit:
    config: FollowupConfig
    repository: FollowupRepository
    run_id: str
    terminal: bool = False

    @classmethod
    def start(
        cls,
        config: FollowupConfig,
        *,
        repository: FollowupRepository,
        anchor_sha256: str,
        validation_mode: str,
    ) -> "FollowupRunAudit":
        if validation_mode not in {"FULL_SEED", "PINNED_FAST"}:
            raise ValueError("follow-up validation mode is invalid")
        repository.register_config(config, git_commit=_git_commit())
        audit = cls(config=config, repository=repository, run_id=uuid4().hex)
        audit._event(
            "STARTED",
            {
                "data_contract": config.trading.data_contract,
                "lifecycle_mode": config.trading.lifecycle_mode,
                "strategy_source_digest": config.trading.strategy_source_digest,
                "v1_anchor_sha256": anchor_sha256,
                "validation_mode": validation_mode,
                "followup_end": iso_utc(
                    config.trading.experiment.followup_end_utc
                ),
            },
        )
        return audit

    def _event_row(
        self,
        event_type: str,
        details: Mapping[str, Any] | dict[str, Any],
        *,
        error: BaseException | None = None,
    ) -> dict[str, Any]:
        return {
            "event_id": uuid4().hex,
            "run_id": self.run_id,
            "config_hash": self.config.config_hash,
            "strategy_name": "golden-strawberry",
            "job_name": self.config.job_name,
            "mode": "sim",
            "event_type": event_type,
            "event_at": iso_utc(),
            "details_json": canonical_json(dict(details)),
            "error_type": type(error).__name__ if error else None,
            "error_message": str(error)[:1000] if error else None,
        }

    def _event(
        self,
        event_type: str,
        details: Mapping[str, Any] | dict[str, Any],
        *,
        error: BaseException | None = None,
    ) -> None:
        self.repository.record_research_run_event(
            self._event_row(event_type, details, error=error)
        )

    def success_event_row(self, summary: Mapping[str, Any]) -> dict[str, Any]:
        if self.terminal:
            raise RuntimeError("follow-up run already has a terminal event")
        return self._event_row("SUCCEEDED", summary)

    def mark_succeeded(self) -> None:
        self.terminal = True

    def fail(self, error: BaseException) -> None:
        if self.terminal:
            return
        self._event("FAILED", {}, error=error)
        self.terminal = True

__all__ = ["FollowupRunAudit"]
