"""Pure Final Five entry crossing and absolute-stop contracts."""

from __future__ import annotations

import math

import pytest

from polybot.config import FinalFiveConfig
from polybot.strategy.signals import evaluate_entry, evaluate_exit


PARAMS = FinalFiveConfig(
    prob_min=0.95,
    prob_max=0.97,
    stop_price=0.90,
    hours_min=2.0,
    hours_max=72.0,
)


def entry(previous, current, hours):
    return evaluate_entry(
        previous_price=previous,
        current_price=current,
        hours_left=hours,
        params=PARAMS,
    )


def test_first_upward_crossing_enters_at_lower_boundary():
    decision = entry(0.949, 0.95, 24)
    assert decision.entry is True
    assert decision.reason.startswith("final_five_cross_")
    assert decision.previous_price == pytest.approx(0.949)
    assert decision.current_price == pytest.approx(0.95)
    assert decision.hours_left == pytest.approx(24)


@pytest.mark.parametrize(
    ("previous", "current", "reason"),
    [
        (None, 0.95, "no_prior_snapshot"),
        (0.94, 0.949, "price_out_of_band"),
        (0.95, 0.96, "no_upward_crossing"),
        (0.96, 0.95, "no_upward_crossing"),
        (0.94, 0.971, "price_out_of_band"),
    ],
)
def test_entry_requires_first_crossing_and_band(previous, current, reason):
    decision = entry(previous, current, 24)
    assert decision.entry is False
    assert decision.reason.startswith(reason)


def test_entry_price_boundaries_are_inclusive():
    assert entry(0.949, 0.95, 24).entry is True
    assert entry(0.949, 0.97, 24).entry is True


def test_time_boundaries_two_to_seventy_two_hours_are_inclusive():
    assert entry(0.94, 0.95, 2.0).entry is True
    assert entry(0.94, 0.95, 72.0).entry is True
    assert entry(0.94, 0.95, 1.999).reason.startswith("too_late")
    assert entry(0.94, 0.95, 72.001).reason.startswith("too_early")
    assert entry(0.94, 0.95, None).reason == "no_end_date"
    assert entry(0.94, 0.95, -1).reason == "already_resolved"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_signal_inputs_fail_closed(value):
    assert entry(value, 0.95, 24).entry is False
    assert entry(0.94, value, 24).entry is False
    assert entry(0.94, 0.95, value).entry is False


def test_absolute_stop_boundary_and_no_relative_stop():
    assert evaluate_exit(current_price=0.90, stop_price=0.90) == "absolute_stop"
    assert evaluate_exit(current_price=0.899, stop_price=0.90) == "absolute_stop"
    assert evaluate_exit(current_price=0.901, stop_price=0.90) is None

    # An entry at 0.97 falling to 0.91 is a material relative loss, but the
    # contract is the absolute 0.90 stop; it must continue to hold.
    assert evaluate_exit(current_price=0.91, stop_price=0.90) is None


def test_there_is_no_take_profit_or_time_exit():
    # The pure exit API accepts only current/stop price.  Prices converging to
    # 1.00 are held for settlement and cannot trigger a synthetic TP.
    assert evaluate_exit(current_price=0.99, stop_price=0.90) is None
    assert evaluate_exit(current_price=1.00, stop_price=0.90) is None


def test_exit_rejects_nonfinite_or_out_of_domain_price():
    for value in (float("nan"), float("inf"), -0.01, 1.01):
        assert evaluate_exit(current_price=value, stop_price=0.90) is None


def test_signal_module_does_not_expose_mango_take_profit_contract():
    # A regression guard against retaining the inherited Mango API.  Papaya's
    # strategy source may contain helpers only if they are not used by its exit.
    import inspect
    from polybot.strategy import signals

    signature = inspect.signature(signals.evaluate_exit)
    assert list(signature.parameters) == ["current_price", "stop_price"]
    assert math.isclose(PARAMS.stop_price, 0.90)
