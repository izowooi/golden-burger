"""Bounded public Polymarket sports-clock snapshot collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

from websockets.sync.client import connect

from ..config import SportsFeedConfig


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_error(error: BaseException) -> str:
    return str(error).replace("\n", " ")[:500]


def _candidate_updates(payload: Any) -> list[dict[str, Any]]:
    """Normalize documented and observed production envelope variants."""
    if isinstance(payload, list):
        return [
            candidate
            for item in payload
            for candidate in _candidate_updates(item)
        ]
    if not isinstance(payload, Mapping):
        return []
    nested = payload.get("payload")
    if isinstance(nested, list):
        return [
            candidate
            for item in nested
            for candidate in _candidate_updates(item)
        ]
    if isinstance(nested, Mapping):
        return _candidate_updates(nested)
    event_state = payload.get("event_state") or payload.get("eventState")
    if isinstance(event_state, Mapping):
        merged = dict(event_state)
        for key, value in payload.items():
            if key not in {"event_state", "eventState"}:
                merged[key] = value
        return [merged]
    return [dict(payload)]


@dataclass(frozen=True)
class SportsClockUpdate:
    slug: str
    received_at: str
    payload: dict[str, Any]
    game_id: str | None = None


@dataclass(frozen=True)
class SportsClockBatch:
    request_id: str
    started_at: str
    completed_at: str
    status: str
    target_count: int
    matched_count: int
    message_count: int
    updates: dict[str, SportsClockUpdate]
    matched_raw_messages: tuple[bytes, ...]
    error_type: str | None = None
    error_message: str | None = None


class SportsClockClient:
    """Take a short, deterministic snapshot from the public sports stream.

    The Jenkins process remains a bounded periodic job rather than becoming a
    daemon. Source ``elapsed`` is preserved verbatim; this client deliberately
    does not infer match minutes from kickoff wall time.
    """

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
        target_games: Mapping[str, str],
    ) -> SportsClockBatch:
        request_id = uuid4().hex
        started_at = _iso_now()
        monotonic_start = time.monotonic()
        targets = {
            str(game_id).strip(): str(slug).strip()
            for game_id, slug in target_games.items()
            if str(game_id).strip() and str(slug).strip()
        }
        target_slugs = set(targets.values())
        updates: dict[str, SportsClockUpdate] = {}
        matched_raw: list[bytes] = []
        message_count = 0
        error_type: str | None = None
        error_message: str | None = None

        if not targets:
            completed_at = _iso_now()
            batch = SportsClockBatch(
                request_id, started_at, completed_at, "NO_TARGETS", 0, 0, 0,
                {}, (),
            )
            self._record_receipt(run_id, batch, monotonic_start)
            return batch

        try:
            with connect(
                self.config.websocket_url,
                open_timeout=self.config.connect_timeout_seconds,
                close_timeout=2,
                proxy=None,
            ) as websocket:
                deadline = monotonic_start + self.config.receive_window_seconds
                while (
                    time.monotonic() < deadline
                    and message_count < self.config.max_messages
                    and len(updates) < len(target_slugs)
                ):
                    remaining = max(0.05, deadline - time.monotonic())
                    try:
                        message = websocket.recv(timeout=min(1.0, remaining))
                    except TimeoutError:
                        continue
                    if isinstance(message, bytes):
                        raw = message
                        text = message.decode("utf-8")
                    else:
                        text = str(message)
                        raw = text.encode("utf-8")
                    if text.casefold() == "ping":
                        websocket.send("pong")
                        continue
                    message_count += 1
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    matched_message = False
                    received_at = _iso_now()
                    for candidate in _candidate_updates(payload):
                        game_id = str(
                            candidate.get("gameId")
                            or candidate.get("game_id")
                            or ""
                        ).strip()
                        source_slug = str(candidate.get("slug") or "").strip()
                        slug = targets.get(game_id)
                        if slug is None and source_slug in target_slugs:
                            slug = source_slug
                        if slug is None:
                            continue
                        if not any(
                            key in candidate
                            for key in (
                                "live", "ended", "score", "period", "elapsed",
                                "clock", "last_update", "updatedAt",
                            )
                        ):
                            continue
                        updates[slug] = SportsClockUpdate(
                            slug=slug,
                            received_at=received_at,
                            payload=candidate,
                            game_id=game_id or None,
                        )
                        matched_message = True
                    if matched_message:
                        matched_raw.append(raw)
        except Exception as error:
            error_type = type(error).__name__
            error_message = _bounded_error(error)

        completed_at = _iso_now()
        if error_type is not None:
            status = "FAILED"
        elif len(updates) == len(target_slugs):
            status = "OBSERVED"
        elif updates:
            status = "PARTIAL"
        else:
            status = "NO_MATCH"
        batch = SportsClockBatch(
            request_id=request_id,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            target_count=len(target_slugs),
            matched_count=len(updates),
            message_count=message_count,
            updates=updates,
            matched_raw_messages=tuple(matched_raw),
            error_type=error_type,
            error_message=error_message,
        )
        self._record_receipt(run_id, batch, monotonic_start)
        return batch

    def _record_receipt(
        self,
        run_id: str,
        batch: SportsClockBatch,
        monotonic_start: float,
    ) -> None:
        joined = b"\n".join(batch.matched_raw_messages)
        self.receipt_sink(
            {
                "request_id": batch.request_id,
                "run_id": run_id,
                "request_kind": "sports_clock_websocket_snapshot",
                "page_number": None,
                "attempt_number": 1,
                "method": "WSS",
                "url": self.config.websocket_url,
                "params_json": json.dumps(
                    {
                        "target_count": batch.target_count,
                        "receive_window_seconds": self.config.receive_window_seconds,
                        "max_messages": self.config.max_messages,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "body_sha256": None,
                "started_at": batch.started_at,
                "completed_at": batch.completed_at,
                "elapsed_ms": max(0.0, (time.monotonic() - monotonic_start) * 1000),
                "status": batch.status,
                "http_status": None,
                "response_sha256": hashlib.sha256(joined).hexdigest() if joined else None,
                "response_bytes": len(joined),
                "error_type": batch.error_type,
                "error_message": batch.error_message,
            }
        )
