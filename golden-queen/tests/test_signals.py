"""Pure Crown Momentum entry crossing and immutable exit contracts."""

from __future__ import annotations

import math

import pytest

from polybot.config import CrownEntryConfig
from polybot.strategy.signals import evaluate_entry, evaluate_exit


PARAMS = CrownEntryConfig(
    prob_min=0.90,
    prob_max=0.94,
    stop_price=0.85,
    take_profit_price=0.98,
    hours_min=0.0,
    hours_max=24.0,
)


def entry(previous, current, hours):
    return evaluate_entry(
        previous_price=previous,
        current_price=current,
        hours_left=hours,
        params=PARAMS,
    )


def test_first_upward_crossing_enters_at_lower_boundary():
    decision = entry(0.899, 0.90, 24)
    assert decision.entry is True
    assert decision.reason.startswith("crown_cross_")
    assert decision.previous_price == pytest.approx(0.899)
    assert decision.current_price == pytest.approx(0.90)
    assert decision.hours_left == pytest.approx(24)


@pytest.mark.parametrize(
    ("previous", "current", "reason"),
    [
        (None, 0.90, "no_prior_snapshot"),
        (0.89, 0.899, "price_out_of_band"),
        (0.90, 0.92, "no_upward_crossing"),
        (0.93, 0.90, "no_upward_crossing"),
        (0.89, 0.941, "price_out_of_band"),
    ],
)
def test_entry_requires_first_crossing_and_band(previous, current, reason):
    decision = entry(previous, current, 24)
    assert decision.entry is False
    assert decision.reason.startswith(reason)


def test_entry_price_boundaries_are_inclusive():
    assert entry(0.899, 0.90, 24).entry is True
    assert entry(0.899, 0.94, 24).entry is True


def test_scheduled_window_accepts_all_positive_time_through_twenty_four_hours():
    assert entry(0.89, 0.90, 0.001).entry is True
    assert entry(0.89, 0.90, 1.999).entry is True
    assert entry(0.89, 0.90, 2.0).entry is True
    assert entry(0.89, 0.90, 24.0).entry is True
    assert entry(0.89, 0.90, 0).reason.startswith("too_late")
    assert entry(0.89, 0.90, 24.001).reason.startswith("too_early")
    assert entry(0.89, 0.90, None).reason == "no_entry_deadline"
    assert entry(0.89, 0.90, -1).reason == "entry_deadline_passed"


def test_in_play_entry_uses_phase_instead_of_positive_time_remaining():
    decision = evaluate_entry(0.89, 0.91, -1.5, PARAMS, phase="in_play")
    assert decision.entry is True
    assert decision.reason.endswith("_in_play")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_signal_inputs_fail_closed(value):
    assert entry(value, 0.90, 24).entry is False
    assert entry(0.89, value, 24).entry is False
    assert entry(0.89, 0.90, value).entry is False


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
    assert math.isclose(PARAMS.stop_price, 0.85)
