"""Pure signal functions for the Crown Momentum strategy."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Optional


EPSILON = 1e-9


@dataclass(frozen=True)
class EntryDecision:
    """Deterministic threshold-crossing decision."""

    should_enter: bool
    reason: str
    previous_price: Optional[float] = None
    current_price: Optional[float] = None
    hours_left: Optional[float] = None

    @property
    def entry(self) -> bool:
        """Compatibility/readability alias used by scanners and tests."""
        return self.should_enter


def _param(params: Any, name: str) -> float:
    value = params.get(name) if isinstance(params, Mapping) else getattr(params, name)
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _finite_price(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0 <= number <= 1:
        return None
    return number


def evaluate_entry(
    previous_price: Optional[float],
    current_price: Optional[float],
    hours_left: Optional[float],
    params: Any,
    *,
    phase: str = "scheduled",
) -> EntryDecision:
    """Evaluate one Crown Momentum entry.

    Entry is valid only when an earlier archived observation was strictly below
    the lower threshold and the current YES price has reached the configured
    band.  A missing prior observation is fail-closed: startup inventory is not
    reinterpreted as a threshold crossing.
    """
    previous = _finite_price(previous_price)
    current = _finite_price(current_price)
    try:
        hours = float(hours_left) if hours_left is not None else None
    except (TypeError, ValueError):
        hours = None

    prob_min = _param(params, "prob_min")
    prob_max = _param(params, "prob_max")
    hours_min = _param(params, "hours_min")
    hours_max = _param(params, "hours_max")

    if previous is None:
        return EntryDecision(False, "no_prior_snapshot", None, current, hours)
    if current is None:
        return EntryDecision(False, "invalid_current_price", previous, None, hours)
    normalized_phase = str(phase or "").strip().lower()
    if normalized_phase not in {"scheduled", "pregame", "in_play"}:
        return EntryDecision(False, "invalid_entry_phase", previous, current, hours)
    if normalized_phase != "in_play":
        if hours is None or not math.isfinite(hours):
            return EntryDecision(False, "no_entry_deadline", previous, current, None)
        if hours < 0:
            return EntryDecision(False, "entry_deadline_passed", previous, current, hours)
        if hours <= 0:
            return EntryDecision(
                False, f"too_late_{hours:.1f}h", previous, current, hours
            )
        if hours < hours_min - EPSILON:
            return EntryDecision(
                False, f"too_late_{hours:.1f}h", previous, current, hours
            )
        if hours > hours_max + EPSILON:
            return EntryDecision(
                False, f"too_early_{hours:.1f}h", previous, current, hours
            )
    if current < prob_min - EPSILON or current > prob_max + EPSILON:
        return EntryDecision(
            False,
            f"price_out_of_band_{current:.3f}",
            previous,
            current,
            hours,
        )
    if previous >= prob_min - EPSILON:
        return EntryDecision(
            False,
            f"no_upward_crossing_{previous:.3f}",
            previous,
            current,
            hours,
        )
    return EntryDecision(
        True,
        (
            f"crown_cross_{previous:.3f}_to_{current:.3f}_in_play"
            if normalized_phase == "in_play"
            else f"crown_cross_{previous:.3f}_to_{current:.3f}_{hours:.1f}h"
        ),
        previous,
        current,
        hours,
    )


def evaluate_exit(
    current_price: Optional[float],
    stop_price: float,
    take_profit_price: Optional[float] = None,
) -> Optional[str]:
    """Return an immutable absolute-price exit reason, or ``None``.

    There is no trailing stop or time exit.  The take-profit is evaluated
    before the stop for an explicit and stable priority contract.
    """
    current = _finite_price(current_price)
    try:
        stop = float(stop_price)
        target = (
            float(take_profit_price)
            if take_profit_price is not None
            else None
        )
    except (TypeError, ValueError):
        return None
    if (
        current is None
        or not math.isfinite(stop)
        or (target is not None and not math.isfinite(target))
    ):
        return None
    if target is not None and current >= target - EPSILON:
        return "take_profit"
    if current <= stop + EPSILON:
        return "absolute_stop"
    return None
