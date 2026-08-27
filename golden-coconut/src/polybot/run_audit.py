"""Append-only run lifecycle evidence rows."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from .api.transport import canonical_json, iso_utc
from .config import BotConfig


class ResearchRunAudit:
    def __init__(self, config: BotConfig, run_id: str) -> None:
        self.config = config
        self.run_id = run_id

    def event_row(
        self, event_type: str, detail: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        if event_type not in {"STARTED", "SUCCEEDED", "FAILED"}:
            raise ValueError("unsupported research run event")
        return {
            "run_event_id": uuid4().hex,
            "run_id": self.run_id,
            "event_type": event_type,
            "observed_at": iso_utc(),
            "config_hash": self.config.config_hash,
            "strategy_source_digest": self.config.trading.strategy_source_digest,
            "detail_json": canonical_json(detail or {}),
        }
