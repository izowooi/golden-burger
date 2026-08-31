"""Pure signals for Golden Plum in-play result trading."""

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


def _optional_param(params: Any, name: str) -> Optional[float]:
    value = params.get(name) if isinstance(params, Mapping) else getattr(params, name)
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite or None")
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
) -> EntryDecision:
    """Evaluate one Golden Plum entry.

    Entry is the first executable observation in the configured exact-VWAP
    band.  ``previous_price`` is retained only for replay compatibility and is
    not a crossing requirement.
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
    hours_max = _optional_param(params, "hours_max")

    if current is None:
        return EntryDecision(False, "invalid_current_price", previous, None, hours)
    if hours is None or not math.isfinite(hours):
        return EntryDecision(False, "no_end_date", previous, current, None)
    if hours < 0:
        return EntryDecision(False, "not_in_play_yet", previous, current, hours)
    if hours < hours_min - EPSILON:
        return EntryDecision(False, f"not_in_play_yet_{hours:.1f}h", previous, current, hours)
    if hours_max is not None and hours > hours_max + EPSILON:
        return EntryDecision(False, f"stale_in_play_{hours:.1f}h", previous, current, hours)
    if current < prob_min - EPSILON or current > prob_max + EPSILON:
        return EntryDecision(
            False,
            f"price_out_of_band_{current:.3f}",
            previous,
            current,
            hours,
        )
    return EntryDecision(
        True,
        f"exact_book_band_{current:.3f}_{hours:.1f}h",
        previous,
        current,
        hours,
    )


def evaluate_exit(
    current_price: Optional[float],
    stop_price: float,
    take_profit_price: Optional[float] = None,
) -> Optional[str]:
    """Return the basic TP/SL signal used by deterministic replay helpers."""
    current = _finite_price(current_price)
    try:
        stop = float(stop_price)
    except (TypeError, ValueError):
        return None
    try:
        take_profit = (
            float(take_profit_price) if take_profit_price is not None else None
        )
    except (TypeError, ValueError):
        return None
    if current is None or not math.isfinite(stop) or stop <= 0:
        return None
    if take_profit is not None:
        if not math.isfinite(take_profit) or not 0 < take_profit < 1:
            return None
        if current + EPSILON >= take_profit:
            return "take_profit"
    if current <= stop + EPSILON:
        return "absolute_stop"
    return None
