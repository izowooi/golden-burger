"""Pure, deterministic Micro-Cascade signal functions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Mapping, Optional, Sequence


EPSILON = 1e-9


@dataclass(frozen=True)
class EntryDecision:
    should_enter: bool
    reason: str
    confirmation_steps: Optional[int] = None
    start_price: Optional[float] = None
    current_price: Optional[float] = None
    cumulative_move: Optional[float] = None
    min_gap_minutes: Optional[float] = None
    max_gap_minutes: Optional[float] = None
    min_step_move: Optional[float] = None
    max_step_move: Optional[float] = None
    hours_left: Optional[float] = None

    @property
    def entry(self) -> bool:
        return self.should_enter


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str
    elapsed_minutes: Optional[float] = None
    delay_minutes: Optional[float] = None


def _param(params: Any, name: str) -> float:
    value = params.get(name) if isinstance(params, Mapping) else getattr(params, name)
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _finite_price(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def evaluate_entry(
    prices: Sequence[float],
    gap_minutes: Sequence[float],
    hours_left: Optional[float],
    params: Any,
) -> EntryDecision:
    """Evaluate the frozen staircase contract from ``steps + 1`` observations."""
    steps = int(_param(params, "confirmation_steps"))
    try:
        hours = float(hours_left) if hours_left is not None else None
    except (TypeError, ValueError):
        hours = None
    if len(prices) != steps + 1:
        return EntryDecision(False, "wrong_observation_count", steps, hours_left=hours)
    if len(gap_minutes) != steps:
        return EntryDecision(False, "wrong_gap_count", steps, hours_left=hours)

    normalized_prices = [_finite_price(value) for value in prices]
    if any(value is None for value in normalized_prices):
        return EntryDecision(False, "invalid_price", steps, hours_left=hours)
    normalized = [float(value) for value in normalized_prices if value is not None]
    try:
        gaps = [float(value) for value in gap_minutes]
    except (TypeError, ValueError):
        return EntryDecision(False, "invalid_gap", steps, hours_left=hours)
    if any(not math.isfinite(value) for value in gaps):
        return EntryDecision(False, "invalid_gap", steps, hours_left=hours)

    current = normalized[-1]
    prob_min = _param(params, "prob_min")
    prob_max = _param(params, "prob_max")
    min_hours = _param(params, "min_hours_to_resolution")
    min_gap = _param(params, "min_snapshot_gap_minutes")
    max_gap = _param(params, "max_snapshot_gap_minutes")
    min_step = _param(params, "min_step_move")
    max_step = _param(params, "max_step_move")
    min_cumulative = _param(params, "min_cumulative_move")
    max_cumulative = _param(params, "max_cumulative_move")

    if hours is None or not math.isfinite(hours):
        return EntryDecision(False, "no_resolution_deadline", steps, hours_left=None)
    if hours < min_hours - EPSILON:
        return EntryDecision(
            False, f"resolution_too_close_{hours:.1f}h", steps, hours_left=hours
        )
    if current < prob_min - EPSILON or current > prob_max + EPSILON:
        return EntryDecision(
            False,
            f"current_price_out_of_band_{current:.3f}",
            steps,
            start_price=normalized[0],
            current_price=current,
            hours_left=hours,
        )

    observed_min_gap = min(gaps)
    observed_max_gap = max(gaps)
    if observed_min_gap < min_gap - EPSILON:
        return EntryDecision(
            False,
            f"snapshot_gap_too_short_{observed_min_gap:.1f}m",
            steps,
            start_price=normalized[0],
            current_price=current,
            min_gap_minutes=observed_min_gap,
            max_gap_minutes=observed_max_gap,
            hours_left=hours,
        )
    if observed_max_gap > max_gap + EPSILON:
        return EntryDecision(
            False,
            f"snapshot_gap_too_long_{observed_max_gap:.1f}m",
            steps,
            start_price=normalized[0],
            current_price=current,
            min_gap_minutes=observed_min_gap,
            max_gap_minutes=observed_max_gap,
            hours_left=hours,
        )

    moves = [
        normalized[index + 1] - normalized[index]
        for index in range(steps)
    ]
    observed_min_step = min(moves)
    observed_max_step = max(moves)
    cumulative = normalized[-1] - normalized[0]
    common = {
        "confirmation_steps": steps,
        "start_price": normalized[0],
        "current_price": current,
        "cumulative_move": cumulative,
        "min_gap_minutes": observed_min_gap,
        "max_gap_minutes": observed_max_gap,
        "min_step_move": observed_min_step,
        "max_step_move": observed_max_step,
        "hours_left": hours,
    }
    if observed_min_step <= EPSILON:
        return EntryDecision(False, "non_positive_step", **common)
    if observed_min_step < min_step - EPSILON:
        return EntryDecision(
            False,
            f"step_below_floor_{observed_min_step:.6f}",
            **common,
        )
    if observed_max_step > max_step + EPSILON:
        return EntryDecision(
            False, f"step_above_cap_{observed_max_step:.3f}", **common
        )
    if cumulative < min_cumulative - EPSILON:
        return EntryDecision(
            False, f"cumulative_below_floor_{cumulative:.3f}", **common
        )
    if cumulative > max_cumulative + EPSILON:
        return EntryDecision(
            False, f"cumulative_above_cap_{cumulative:.3f}", **common
        )
    return EntryDecision(
        True,
        f"micro_cascade_{steps}step_{cumulative:.3f}_{hours:.1f}h",
        **common,
    )


def _as_aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def evaluate_exit(
    buy_timestamp: Optional[datetime],
    now: Optional[datetime],
    hold_minutes: float,
) -> ExitDecision:
    """Return the time-only exit decision and observable scheduling delay."""
    opened = _as_aware_utc(buy_timestamp)
    current = _as_aware_utc(now) if now is not None else datetime.now(timezone.utc)
    try:
        target = float(hold_minutes)
    except (TypeError, ValueError):
        return ExitDecision(False, "invalid_hold_minutes")
    if opened is None:
        return ExitDecision(False, "missing_buy_timestamp")
    if current is None or not math.isfinite(target) or target <= 0:
        return ExitDecision(False, "invalid_exit_clock")
    elapsed = (current - opened).total_seconds() / 60.0
    if not math.isfinite(elapsed) or elapsed < 0:
        return ExitDecision(False, "invalid_elapsed_time")
    if elapsed < target - EPSILON:
        return ExitDecision(False, "hold_window_not_elapsed", elapsed, None)
    return ExitDecision(True, "time_exit", elapsed, max(0.0, elapsed - target))
