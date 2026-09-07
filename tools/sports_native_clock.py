"""Pure NFL clock-format decoding for research display and data validation.

Q1..Q4 use a remaining game clock and 15-minute regulation quarters. The
900-second rule is NFL 2026 Rule 4, Section 1, Article 1:
https://static.www.nfl.com/image/upload/fl_attachment/league/tqivdkzt9mu6wdgsh1ku.pdf

This module performs no I/O and supplies no trading or lifecycle decisions.
It does not infer season, finality, overtime rules, source freshness, or a
soccer minute. Caller-owned event/source/receipt provenance stays separate.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any, Mapping

VERSION = "nfl-regulation-countdown-format-v1"
QUARTER_SECONDS = 900
RULE_SOURCE = "https://static.www.nfl.com/image/upload/fl_attachment/league/tqivdkzt9mu6wdgsh1ku.pdf"
_QUARTER = re.compile(r"Q([1-4])\Z")
_CLOCK = re.compile(r"([0-9]{1,2}):([0-9]{2})\Z")
_QUARTER_END = re.compile(r"END Q([1-4])\Z")
_STATE_MARKERS = {
    "HT": "HALFTIME_REPORTED",
    "FT": "FULL_TIME_REPORTED",
    "VFT": "VFT_UNINTERPRETED",
    "NS": "NOT_STARTED_REPORTED",
}


def _json_copy(value: Any) -> Any:
    # Preserve JSON source values; reject non-JSON Python objects/NaN instead
    # of stringifying an object or fabricating a raw source value.
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise TypeError("clock inputs must be finite JSON values") from error
    return deepcopy(value)


def decode_nfl_clock(period_raw: Any, clock_raw: Any) -> dict[str, Any]:
    """Decode one source NFL clock without changing its raw input values.

    Only Q1..Q4 with a valid remaining M:SS/MM:SS clock return numeric elapsed
    regulation seconds. Non-quarter state markers retain distinct states with
    no invented elapsed number. Their presence never proves game settlement.
    """
    result: dict[str, Any] = {
        "version": VERSION,
        "sport_family": "nfl",
        "raw_period": _json_copy(period_raw),
        "raw_clock": _json_copy(clock_raw),
        "status": "INVALID",
        "state": None,
        "reason": None,
        "quarter": None,
        "quarter_remaining_seconds": None,
        "regulation_elapsed_seconds": None,
        "clock_direction": None,
        "quarter_seconds_assumption": QUARTER_SECONDS,
        "assumption_basis": "NFL_2026_REGULATION_RULE_4_1_1",
        "format_only": True,
        "live_entry_ready": False,
        "terminal_proven": False,
        "season_inferred": False,
    }
    if period_raw is None or (isinstance(period_raw, str) and not period_raw.strip()):
        result.update(reason="period_missing")
        return result
    if not isinstance(period_raw, str):
        result.update(reason="period_must_be_text")
        return result
    period = period_raw.strip().upper()
    state = _STATE_MARKERS.get(period)
    end = _QUARTER_END.fullmatch(period)
    if state is not None or end is not None:
        result.update(
            status="STATE_ONLY",
            state=state or "QUARTER_END_REPORTED",
            reason="source_state_marker_without_numeric_clock_inference",
            quarter=int(end.group(1)) if end else None,
        )
        return result
    match = _QUARTER.fullmatch(period)
    if match is None:
        overtime = bool(re.fullmatch(r"(?:OT[0-9]*|[0-9]+OT|OVERTIME|Q[5-9])", period))
        result.update(
            status="UNSUPPORTED",
            state="OVERTIME_UNSUPPORTED" if overtime else "UNKNOWN_PERIOD",
            reason="overtime_rules_not_applied" if overtime else "period_format_not_supported",
        )
        return result
    quarter = int(match.group(1))
    result.update(state="REGULATION_QUARTER", quarter=quarter)
    if not isinstance(clock_raw, str):
        result.update(reason="remaining_clock_must_be_text")
        return result
    clock = _CLOCK.fullmatch(clock_raw.strip())
    if clock is None:
        result.update(reason="remaining_clock_must_be_m_ss_or_mm_ss")
        return result
    minutes, seconds = map(int, clock.groups())
    remaining = minutes * 60 + seconds
    if seconds >= 60 or remaining > QUARTER_SECONDS:
        result.update(reason="remaining_clock_outside_15_minute_quarter")
        return result
    result.update(
        status="DECODED",
        reason="regulation_quarter_remaining_clock",
        quarter_remaining_seconds=remaining,
        regulation_elapsed_seconds=quarter * QUARTER_SECONDS - remaining,
        clock_direction="REMAINING_COUNTDOWN",
    )
    return result


def _numeric_reading(value: Mapping[str, Any]) -> bool:
    if value.get("version") != VERSION or value.get("status") != "DECODED":
        return False
    quarter = value.get("quarter")
    remaining = value.get("quarter_remaining_seconds")
    elapsed = value.get("regulation_elapsed_seconds")
    return (
        type(quarter) is int and 1 <= quarter <= 4
        and type(remaining) is int and 0 <= remaining <= QUARTER_SECONDS
        and type(elapsed) is int and elapsed == quarter * QUARTER_SECONDS - remaining
    )


def compare_nfl_clocks(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """Describe two numeric clocks; caller must ensure same game/source/order.

    REGRESSED may reflect a clock correction or late source update. No value is
    clamped, extrapolated, reordered, or treated as a successful lifecycle step.
    """
    result = {
        "comparison": "UNAVAILABLE",
        "delta_regulation_elapsed_seconds": None,
        "clock_regression": None,
        "event_source_and_receipt_order_verified": False,
        "format_only": True,
    }
    if not _numeric_reading(previous) or not _numeric_reading(current):
        return result
    delta = current["regulation_elapsed_seconds"] - previous["regulation_elapsed_seconds"]
    result.update(
        comparison="REGRESSED" if delta < 0 else "UNCHANGED" if delta == 0 else "PROGRESSED",
        delta_regulation_elapsed_seconds=delta,
        clock_regression=delta < 0,
    )
    return result
