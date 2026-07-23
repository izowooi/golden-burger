"""Deterministic entry-clock selection for sports and non-sports markets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..config import SportsConfig


_SPORTS_TAGS = frozenset(
    {
        "sports",
        "nba",
        "nfl",
        "mlb",
        "nhl",
        "mls",
        "soccer",
        "football",
        "basketball",
        "baseball",
        "hockey",
        "tennis",
        "golf",
        "mma",
        "ufc",
        "cricket",
        "rugby",
        "esports",
    }
)


def parse_market_datetime(value: Any) -> Optional[datetime]:
    """Parse a Gamma timestamp into an aware UTC datetime."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _tag_values(market: Dict[str, Any]) -> set[str]:
    values: set[str] = set()
    tags = market.get("tags") or []
    if not isinstance(tags, list):
        return values
    for tag in tags:
        if isinstance(tag, dict):
            for key in ("slug", "label"):
                raw = tag.get(key)
                if raw not in (None, ""):
                    values.add(str(raw).strip().lower())
        elif tag not in (None, ""):
            values.add(str(tag).strip().lower())
    return values


def is_sports_market(market: Dict[str, Any]) -> bool:
    """Identify sports clock semantics without question-text heuristics."""
    if market.get("gameStartTime") not in (None, ""):
        return True
    if market.get("sportsMarketType") not in (None, ""):
        return True
    return bool(_SPORTS_TAGS.intersection(_tag_values(market)))


@dataclass(frozen=True)
class EntryClock:
    """Normalized entry deadline and sports phase evidence."""

    valid: bool
    reason: str
    phase: str
    reference: str
    deadline: Optional[datetime]
    hours_left: Optional[float]
    is_sports: bool
    game_start_time: Optional[datetime] = None
    minutes_until_game_start: Optional[float] = None
    sports_market_type: Optional[str] = None


def evaluate_entry_clock(
    market: Dict[str, Any],
    config: SportsConfig,
    now: Optional[datetime] = None,
) -> EntryClock:
    """Choose ``gameStartTime`` for sports and ``endDate`` otherwise.

    Sports remain part of the universe.  Before kickoff their entry horizon is
    measured to ``gameStartTime``.  In-play markets remain eligible only while
    Gamma still exposes them as tradeable upstream and the elapsed game time is
    within ``max_in_play_minutes``.
    """
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    reference = reference.astimezone(timezone.utc)

    sports = is_sports_market(market)
    raw_sports_type = market.get("sportsMarketType")
    sports_type = (
        str(raw_sports_type).strip()
        if raw_sports_type not in (None, "")
        else None
    )

    if sports and config.use_game_start_time:
        raw_game_start = market.get("gameStartTime")
        if raw_game_start in (None, ""):
            if config.reject_without_game_start:
                return EntryClock(
                    False,
                    "sports_missing_game_start",
                    "unknown",
                    "game_start_time",
                    None,
                    None,
                    True,
                    sports_market_type=sports_type,
                )
        else:
            game_start = parse_market_datetime(raw_game_start)
            if game_start is None:
                return EntryClock(
                    False,
                    "invalid_game_start_time",
                    "unknown",
                    "game_start_time",
                    None,
                    None,
                    True,
                    sports_market_type=sports_type,
                )
            minutes_left = (game_start - reference).total_seconds() / 60.0
            if minutes_left > 0:
                return EntryClock(
                    True,
                    "sports_pregame",
                    "pregame",
                    "game_start_time",
                    game_start,
                    minutes_left / 60.0,
                    True,
                    game_start_time=game_start,
                    minutes_until_game_start=minutes_left,
                    sports_market_type=sports_type,
                )
            elapsed = abs(minutes_left)
            if not config.allow_in_play:
                return EntryClock(
                    False,
                    "game_in_play_disabled",
                    "in_play",
                    "game_start_time",
                    game_start,
                    minutes_left / 60.0,
                    True,
                    game_start_time=game_start,
                    minutes_until_game_start=minutes_left,
                    sports_market_type=sports_type,
                )
            if elapsed > config.max_in_play_minutes:
                return EntryClock(
                    False,
                    "game_in_play_too_old",
                    "in_play",
                    "game_start_time",
                    game_start,
                    minutes_left / 60.0,
                    True,
                    game_start_time=game_start,
                    minutes_until_game_start=minutes_left,
                    sports_market_type=sports_type,
                )
            return EntryClock(
                True,
                "game_in_play",
                "in_play",
                "game_start_time",
                game_start,
                minutes_left / 60.0,
                True,
                game_start_time=game_start,
                minutes_until_game_start=minutes_left,
                sports_market_type=sports_type,
            )

    end_date = parse_market_datetime(market.get("endDate"))
    if end_date is None:
        return EntryClock(
            False,
            "no_end_date",
            "scheduled",
            "end_date",
            None,
            None,
            sports,
            sports_market_type=sports_type,
        )
    hours_left = (end_date - reference).total_seconds() / 3600.0
    if hours_left <= 0:
        return EntryClock(
            False,
            "already_resolved",
            "scheduled",
            "end_date",
            end_date,
            hours_left,
            sports,
            sports_market_type=sports_type,
        )
    return EntryClock(
        True,
        "scheduled",
        "scheduled",
        "end_date",
        end_date,
        hours_left,
        sports,
        sports_market_type=sports_type,
    )
