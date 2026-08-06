"""Append-only run provenance for the research-full-v1 collector."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
from typing import Any, Mapping
from uuid import uuid4

from .config import BotConfig
from .db.repository import SCHEMA_VERSION, ResearchRepository
from .utils.retry import utc_now


_COMMIT = re.compile(r"^[0-9a-fA-F]{7,64}$")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(private|secret|password|credential|authorization|api[_ -]?key|token)"
    r"\s*[:=]\s*[^\s,;]+"
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=lambda item: str(item),
    )


def _git_commit() -> str:
    configured = (os.getenv("GIT_COMMIT") or "").strip()
    if configured:
        return configured if _COMMIT.fullmatch(configured) else "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = result.stdout.strip()
    return value if _COMMIT.fullmatch(value) else "unknown"


def _safe_error(error: BaseException) -> str:
    flattened = " ".join(str(error).splitlines())[:4_000]
    return _SENSITIVE_ASSIGNMENT.sub(r"\1=<redacted>", flattened)[:2_000]


class ResearchRunAudit:
    """Record STARTED and terminal events as separate immutable rows."""

    def __init__(
        self,
        *,
        repository: ResearchRepository,
        run_id: str,
        config_hash: str,
        source_digest: str,
        git_commit: str,
        job_name: str,
    ) -> None:
        self.repository = repository
        self.run_id = run_id
        self.config_hash = config_hash
        self.source_digest = source_digest
        self.git_commit = git_commit
        self.job_name = job_name
        self._finished = False

    @classmethod
    def start(
        cls,
        config: BotConfig,
        *,
        repository: ResearchRepository,
    ) -> "ResearchRunAudit":
        payload = {
            "schema_version": SCHEMA_VERSION,
            "strategy_name": "golden-pomegranate",
            "mode": "sim",
            "trading": dataclasses.asdict(config.trading),
        }
        config_json = _canonical_json(payload)
        config_hash = hashlib.sha256(config_json.encode()).hexdigest()
        source_digest = config.trading.strategy_source_digest
        git_commit = _git_commit()
        run_id = str(uuid4())
        timestamp = utc_now()
        recorder = cls(
            repository=repository,
            run_id=run_id,
            config_hash=config_hash,
            source_digest=source_digest,
            git_commit=git_commit,
            job_name=config.job_name,
        )
        repository.record_research_run_start(
            config_row={
                "config_hash": config_hash,
                "schema_version": SCHEMA_VERSION,
                "strategy_name": "golden-pomegranate",
                "mode": "sim",
                "config_json": config_json,
                "strategy_source_digest": source_digest,
                "git_commit": git_commit,
                "first_seen_at": timestamp,
            },
            event_row=recorder._event("STARTED", timestamp=timestamp),
        )
        return recorder

    def _event(
        self,
        event_type: str,
        *,
        timestamp: str | None = None,
        stats: Mapping[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> dict[str, Any]:
        return {
            "event_id": str(uuid4()),
            "run_id": self.run_id,
            "event_type": event_type,
            "event_at": timestamp or utc_now(),
            "strategy_name": "golden-pomegranate",
            "job_name": self.job_name,
            "mode": "sim",
            "lifecycle_mode": "archive_only",
            "config_hash": self.config_hash,
            "strategy_source_digest": self.source_digest,
            "git_commit": self.git_commit,
            "cycle_stats_json": _canonical_json(dict(stats or {}))
            if stats is not None
            else None,
            "error_type": type(error).__name__ if error is not None else None,
            "error_message": _safe_error(error) if error is not None else None,
        }

    def succeed(self, stats: Mapping[str, Any] | None = None) -> None:
        if self._finished:
            return
        self.repository.record_research_run_event(
            self._event("SUCCEEDED", stats=stats or {})
        )
        self._finished = True

    def fail(self, error: BaseException) -> None:
        if self._finished:
            return
        self.repository.record_research_run_event(self._event("FAILED", error=error))
        self._finished = True


__all__ = ["ResearchRunAudit"]
