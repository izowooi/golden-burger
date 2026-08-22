"""Append-only run lifecycle evidence."""

from __future__ import annotations

import json
from typing import Any, Mapping
from uuid import uuid4

from .config import BotConfig
from .db.repository import ResearchRepository
from .utils.retry import iso_utc


class ResearchRunAudit:
    def __init__(self, repository: ResearchRepository, config: BotConfig, run_id: str) -> None:
        self.repository = repository
        self.config = config
        self.run_id = run_id

    def _record(self, event_type: str, detail: Mapping[str, Any] | None = None) -> None:
        self.repository.record_run_event({
            "event_id": uuid4().hex,
            "run_id": self.run_id,
            "event_type": event_type,
            "observed_at": iso_utc(),
            "config_hash": self.config.config_hash,
            "strategy_source_digest": self.config.trading.strategy_source_digest,
            "detail_json": json.dumps(dict(detail or {}), sort_keys=True, separators=(",", ":")),
        })

    def start(self) -> None:
        self._record("STARTED")

    def succeed(self, detail: Mapping[str, Any]) -> None:
        self._record("SUCCEEDED", detail)

    def fail(self, error: BaseException) -> None:
        self._record("FAILED", {"error_type": type(error).__name__, "error_message": str(error)[:1000]})
