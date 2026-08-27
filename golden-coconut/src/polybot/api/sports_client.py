"""Bounded public sports-clock snapshot; source time is never inferred."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

from websockets.sync.client import connect

from ..config import SportsFeedConfig
from .transport import CycleBudget, canonical_json, iso_utc


def _candidates(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [candidate for item in payload for candidate in _candidates(item)]
    if not isinstance(payload, Mapping):
        return []
    nested = payload.get("payload")
    if isinstance(nested, (list, Mapping)):
        return _candidates(nested)
    state = payload.get("event_state", payload.get("eventState"))
    if isinstance(state, Mapping):
        merged = dict(state)
        merged.update({key: value for key, value in payload.items() if key not in {"event_state", "eventState"}})
        return [merged]
    return [dict(payload)]


@dataclass(frozen=True)
class ClockUpdate:
    game_id: str
    cluster_id: str
    received_at: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ClockBatch:
    request_id: str
    status: str
    started_at: str
    completed_at: str
    target_count: int
    matched_count: int
    message_count: int
    updates: Mapping[str, ClockUpdate]
    raw_messages: tuple[bytes, ...]
    error_type: str | None = None
    error_message: str | None = None


class SportsClockClient:
    def __init__(
        self,
        config: SportsFeedConfig,
        receipt_sink: Callable[[Mapping[str, Any]], None],
    ) -> None:
        self.config = config
        self.receipt_sink = receipt_sink

    def collect(
        self,
        run_id: str,
        targets: Mapping[str, str],
        *,
        budget: CycleBudget,
    ) -> ClockBatch:
        request_id = uuid4().hex
        started_at = iso_utc()
        started_clock = time.monotonic()
        normalized = {
            str(game_id): str(cluster_id)
            for game_id, cluster_id in targets.items()
            if str(game_id) and str(cluster_id)
        }
        if not normalized:
            batch = ClockBatch(request_id, "NO_TARGETS", started_at, iso_utc(), 0, 0, 0, {}, ())
            self._receipt(run_id, batch, started_clock)
            return batch
        updates: dict[str, ClockUpdate] = {}
        raws: list[bytes] = []
        messages = 0
        error_type: str | None = None
        error_message: str | None = None
        try:
            budget.ensure_can_start_request("sports_clock")
            window = min(
                self.config.receive_window_seconds,
                max(0.1, budget.request_stop_at - time.monotonic()),
            )
            with connect(
                self.config.websocket_url,
                open_timeout=min(self.config.connect_timeout_seconds, window),
                close_timeout=2,
                proxy=None,
            ) as websocket:
                deadline = time.monotonic() + window
                while time.monotonic() < deadline and messages < self.config.max_messages and len(updates) < len(normalized):
                    try:
                        message = websocket.recv(timeout=min(1.0, max(0.05, deadline - time.monotonic())))
                    except TimeoutError:
                        continue
                    raw = message if isinstance(message, bytes) else str(message).encode("utf-8")
                    text = raw.decode("utf-8")
                    if text.casefold() == "ping":
                        websocket.send("pong")
                        continue
                    messages += 1
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    matched = False
                    for candidate in _candidates(payload):
                        game_id = str(candidate.get("gameId") or candidate.get("game_id") or "")
                        cluster = normalized.get(game_id)
                        if cluster is None:
                            continue
                        if not any(key in candidate for key in ("period", "elapsed", "clock", "score", "live", "ended")):
                            continue
                        updates[game_id] = ClockUpdate(game_id, cluster, iso_utc(), candidate)
                        matched = True
                    if matched:
                        raws.append(raw)
        except Exception as error:  # public WSS failures are evidence, not synthesized time
            error_type = type(error).__name__
            error_message = str(error).replace("\n", " ")[:500]
        status = (
            "FAILED" if error_type else
            "OBSERVED" if len(updates) == len(normalized) else
            "PARTIAL" if updates else "NO_MATCH"
        )
        batch = ClockBatch(
            request_id, status, started_at, iso_utc(), len(normalized), len(updates),
            messages, updates, tuple(raws), error_type, error_message
        )
        self._receipt(run_id, batch, started_clock)
        return batch

    def _receipt(self, run_id: str, batch: ClockBatch, started_clock: float) -> None:
        joined = b"\n".join(batch.raw_messages)
        self.receipt_sink(
            {
                "api_attempt_id": uuid4().hex,
                "logical_request_id": batch.request_id,
                "run_id": run_id,
                "request_kind": "sports_clock_websocket",
                "sport_family": None,
                "page_number": None,
                "attempt_number": 1,
                "method": "WSS",
                "url": self.config.websocket_url,
                "params_json": canonical_json({"target_count": batch.target_count}),
                "body_sha256": None,
                "started_at": batch.started_at,
                "completed_at": batch.completed_at,
                "elapsed_ms": max(0.0, (time.monotonic() - started_clock) * 1000),
                "status": batch.status,
                "http_status": None,
                "response_sha256": hashlib.sha256(joined).hexdigest() if joined else None,
                "response_bytes": len(joined),
                "error_type": batch.error_type,
                "error_message": batch.error_message,
            }
        )
