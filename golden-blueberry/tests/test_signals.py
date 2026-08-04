"""Pure Closing Surge entry crossing and immutable exit contracts."""

from __future__ import annotations

import math

import pytest

from polybot.config import SurgeEntryConfig
from polybot.strategy.signals import evaluate_entry, evaluate_exit


PARAMS = SurgeEntryConfig(
    prob_min=0.85,
    prob_max=0.93,
    stop_price=0.78,
    take_profit_price=0.97,
    hours_min=0.0,
    hours_max=72.0,
    min_surge=0.02,
)


def entry(previous, current, hours):
    return evaluate_entry(
        previous_price=previous,
        current_price=current,
        hours_left=hours,
        params=PARAMS,
    )


def test_first_upward_crossing_enters_at_lower_boundary():
    decision = entry(0.82, 0.85, 72)
    assert decision.entry is True
    assert decision.reason.startswith("closing_surge_")
    assert decision.previous_price == pytest.approx(0.82)
    assert decision.current_price == pytest.approx(0.85)
    assert decision.surge == pytest.approx(0.03)
    assert decision.hours_left == pytest.approx(72)


@pytest.mark.parametrize(
    ("previous", "current", "reason"),
    [
        (None, 0.85, "no_prior_snapshot"),
        (0.82, 0.849, "price_out_of_band"),
        (0.85, 0.90, "no_upward_crossing"),
        (0.90, 0.86, "no_upward_crossing"),
        (0.82, 0.931, "price_out_of_band"),
        (0.849, 0.85, "surge_below_min"),
    ],
)
def test_entry_requires_first_crossing_and_band(previous, current, reason):
    decision = entry(previous, current, 24)
    assert decision.entry is False
    assert decision.reason.startswith(reason)


def test_entry_price_boundaries_are_inclusive():
    assert entry(0.82, 0.85, 72).entry is True
    assert entry(0.84, 0.93, 72).entry is True


def test_scheduled_window_accepts_all_positive_time_through_twenty_four_hours():
    assert entry(0.82, 0.85, 0.001).entry is True
    assert entry(0.82, 0.85, 1.999).entry is True
    assert entry(0.82, 0.85, 2.0).entry is True
    assert entry(0.82, 0.85, 72.0).entry is True
    assert entry(0.82, 0.85, 0).reason.startswith("too_late")
    assert entry(0.82, 0.85, 72.001).reason.startswith("too_early")
    assert entry(0.82, 0.85, None).reason == "no_entry_deadline"
    assert entry(0.82, 0.85, -1).reason == "entry_deadline_passed"


def test_in_play_entry_uses_phase_instead_of_positive_time_remaining():
    decision = evaluate_entry(0.82, 0.87, -1.5, PARAMS, phase="in_play")
    assert decision.entry is True
    assert decision.reason.endswith("_in_play")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_signal_inputs_fail_closed(value):
    assert entry(value, 0.85, 24).entry is False
    assert entry(0.82, value, 24).entry is False
    assert entry(0.82, 0.85, value).entry is False


def test_ab_treatment_differs_only_by_minimum_surge():
    arm_a = PARAMS
    arm_b = SurgeEntryConfig(**{**PARAMS.__dict__, "min_surge": 0.05})

    assert evaluate_entry(0.82, 0.85, 12, arm_a).entry is True
    assert evaluate_entry(0.82, 0.85, 12, arm_b).reason.startswith(
        "surge_below_min"
    )
    assert evaluate_entry(0.79, 0.85, 12, arm_b).entry is True


def test_absolute_stop_boundary_and_no_relative_stop():
    assert evaluate_exit(0.85, 0.85, 0.98) == "absolute_stop"
    assert evaluate_exit(0.849, 0.85, 0.98) == "absolute_stop"
    assert evaluate_exit(0.851, 0.85, 0.98) is None

    # The contract is absolute, not a relative percentage from entry.
    assert evaluate_exit(0.86, 0.85, 0.98) is None


def test_take_profit_is_absolute_and_has_explicit_priority():
    assert evaluate_exit(0.98, 0.85, 0.98) == "take_profit"
    assert evaluate_exit(0.99, 0.85, 0.98) == "take_profit"
    assert evaluate_exit(0.97, 0.85, 0.98) is None


def test_exit_rejects_nonfinite_or_out_of_domain_price():
    for value in (float("nan"), float("inf"), -0.01, 1.01):
        assert evaluate_exit(value, 0.85, 0.98) is None


def test_exit_api_exposes_only_absolute_prices_and_no_time_or_trailing_state():
    import inspect
    from polybot.strategy import signals

    signature = inspect.signature(signals.evaluate_exit)
    assert list(signature.parameters) == [
        "current_price",
        "stop_price",
        "take_profit_price",
    ]
    assert math.isclose(PARAMS.stop_price, 0.78)
