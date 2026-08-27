"""Explicit sports lifecycle, schedule, and source-clock normalization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .api.transport import canonical_json, iso_utc


LIFECYCLE_STATES = (
    "DISCOVERED_OPEN",
    "PREGAME",
    "IN_PLAY",
    "ENDED",
    "POSTPONED",
    "CANCELLED",
    "RESOLVED",
    "VOID",
    "TIE",
)
TERMINAL_LIFECYCLE_STATES = frozenset({"CANCELLED", "RESOLVED", "VOID", "TIE"})
GAMMA_LIFECYCLE_FIELDS = (
    "active",
    "closed",
    "archived",
    "live",
    "ended",
    "gameStatus",
    "status",
    "resolution",
    "umaResolutionStatus",
    "startDate",
    "startTime",
    "eventDate",
    "eventStartTime",
    "endDate",
    "finishedTimestamp",
    "updatedAt",
    "score",
    "period",
    "elapsed",
    "clock",
    "gameId",
    "slug",
)
SCHEDULE_FIELDS = ("startTime", "eventDate", "eventStartTime", "startDate")


def parse_source_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def canonical_game_slug(event: Mapping[str, Any]) -> str | None:
    value = str(event.get("slug") or "").strip().casefold()
    return value or None


def schedule_evidence(event: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    for field in SCHEDULE_FIELDS:
        if field not in event or event.get(field) in (None, ""):
            continue
        raw = str(event[field])
        parsed = parse_source_utc(raw)
        return field, raw, iso_utc(parsed) if parsed is not None else None
    markets = event.get("markets")
    if isinstance(markets, list):
        candidates = {
            str(market.get("gameStartTime"))
            for market in markets
            if isinstance(market, Mapping) and market.get("gameStartTime") not in (None, "")
        }
        if len(candidates) == 1:
            raw = candidates.pop()
            parsed = parse_source_utc(raw)
            return "markets[].gameStartTime", raw, iso_utc(parsed) if parsed is not None else None
    return None, None, None


def raw_lifecycle_json(event: Mapping[str, Any]) -> str:
    return canonical_json({key: event[key] for key in GAMMA_LIFECYCLE_FIELDS if key in event})


def _status_text(event: Mapping[str, Any]) -> str:
    return " ".join(
        str(event.get(key) or "").strip().casefold()
        for key in ("gameStatus", "status", "resolution", "umaResolutionStatus")
    )


def classify_gamma_lifecycle(event: Mapping[str, Any]) -> tuple[str, str]:
    """Use explicit source fields only; wall time is intentionally not an input."""

    status = _status_text(event)
    if "cancel" in status or "abandon" in status:
        return "CANCELLED", "EXPLICIT_GAMMA_STATUS"
    if "postpon" in status or "suspend" in status or status.strip() == "delayed":
        return "POSTPONED", "EXPLICIT_GAMMA_STATUS"
    if "void" in status:
        return "VOID", "EXPLICIT_GAMMA_STATUS"
    if status.strip() in {"tie", "draw"}:
        return "TIE", "EXPLICIT_GAMMA_STATUS"
    if "resolved" in status and "unresolved" not in status:
        return "RESOLVED", "EXPLICIT_GAMMA_STATUS"
    if event.get("ended") is True:
        return "ENDED", "EXPLICIT_GAMMA_ENDED"
    if event.get("live") is True:
        return "IN_PLAY", "EXPLICIT_GAMMA_LIVE"
    if event.get("live") is False and event.get("ended") is False:
        return "PREGAME", "EXPLICIT_GAMMA_PREGAME"
    if event.get("closed") is True:
        return "ENDED", "EXPLICIT_GAMMA_CLOSED"
    return "DISCOVERED_OPEN", "OPEN_WITHOUT_EXPLICIT_PHASE"


def gamma_clock_fallback(event: Mapping[str, Any]) -> dict[str, Any] | None:
    fields = {
        key: event[key]
        for key in ("slug", "gameId", "period", "elapsed", "clock", "score", "live", "ended", "gameStatus")
        if key in event
    }
    if not any(key in fields for key in ("period", "elapsed", "clock", "score", "live", "ended")):
        return None
    return fields


def minutes_to_scheduled_start(observed_at: str, scheduled_start: str | None) -> float | None:
    observed = parse_source_utc(observed_at)
    scheduled = parse_source_utc(scheduled_start)
    if observed is None or scheduled is None:
        return None
    return (scheduled - observed).total_seconds() / 60.0


@dataclass(frozen=True)
class LifecycleIdentity:
    event_id: str
    canonical_slug: str
    game_id_alias: str | None
    event_cluster_id: str
    lifecycle_state: str
    lifecycle_reason: str
    scheduled_start_field: str | None
    scheduled_start_raw: str | None
    scheduled_start_utc: str | None


def lifecycle_identity(event: Mapping[str, Any], family: str) -> LifecycleIdentity:
    event_id = str(event.get("id") or "")
    slug = canonical_game_slug(event) or ""
    state, reason = classify_gamma_lifecycle(event)
    schedule_field, schedule_raw, schedule_utc = schedule_evidence(event)
    return LifecycleIdentity(
        event_id=event_id,
        canonical_slug=slug,
        game_id_alias=str(event.get("gameId") or event.get("game_id") or "") or None,
        event_cluster_id=f"{family}:{slug or event_id or 'MISSING'}",
        lifecycle_state=state,
        lifecycle_reason=reason,
        scheduled_start_field=schedule_field,
        scheduled_start_raw=schedule_raw,
        scheduled_start_utc=schedule_utc,
    )
