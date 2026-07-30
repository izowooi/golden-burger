"""Frozen Micro-Cascade signal and exit-clock contracts."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from polybot.config import MicroCascadeEntryConfig
from polybot.strategy.signals import evaluate_entry, evaluate_exit


PRIMARY = MicroCascadeEntryConfig()


@pytest.mark.parametrize(
    ("steps", "minimum", "prices"),
    [
        (3, 0.01, [0.40, 0.404, 0.407, 0.410]),
        (3, 0.02, [0.40, 0.407, 0.414, 0.420]),
        (5, 0.01, [0.40, 0.402, 0.404, 0.406, 0.408, 0.410]),
        (5, 0.02, [0.40, 0.404, 0.408, 0.412, 0.416, 0.420]),
    ],
)
def test_all_four_frozen_arms_accept_their_boundary(steps, minimum, prices):
    params = replace(
        PRIMARY,
        confirmation_steps=steps,
        min_cumulative_move=minimum,
    )
    decision = evaluate_entry(prices, [3.0] * steps, 6.0, params)
    assert decision.should_enter
    assert decision.confirmation_steps == steps
    assert decision.cumulative_move == pytest.approx(minimum)


def test_requires_exact_observation_and_gap_counts():
    assert not evaluate_entry([0.4, 0.41, 0.42], [5, 5], 8, PRIMARY).should_enter
    assert not evaluate_entry(
        [0.4, 0.407, 0.414, 0.42], [5, 5], 8, PRIMARY
    ).should_enter


@pytest.mark.parametrize("gaps", [[3, 3, 3], [10, 10, 10]])
def test_snapshot_gap_boundaries_are_inclusive(gaps):
    assert evaluate_entry(
        [0.4, 0.407, 0.414, 0.42], gaps, 8, PRIMARY
    ).should_enter


@pytest.mark.parametrize(
    ("prices", "gaps", "reason"),
    [
        ([0.4, 0.407, 0.414, 0.42], [2.99, 5, 5], "snapshot_gap_too_short"),
        ([0.4, 0.407, 0.414, 0.42], [5, 10.01, 5], "snapshot_gap_too_long"),
        ([0.4, 0.407, 0.407, 0.42], [5, 5, 5], "non_positive_step"),
        ([0.4, 0.421, 0.422, 0.423], [5, 5, 5], "step_above_cap"),
        ([0.4, 0.405, 0.41, 0.415], [5, 5, 5], "cumulative_below_floor"),
        ([0.4, 0.415, 0.43, 0.445], [5, 5, 5], "cumulative_above_cap"),
    ],
)
def test_staircase_fail_closed_reasons(prices, gaps, reason):
    decision = evaluate_entry(prices, gaps, 8, PRIMARY)
    assert not decision.should_enter
    assert reason in decision.reason


@pytest.mark.parametrize("current", [0.20, 0.80])
def test_current_probability_band_is_inclusive(current):
    prices = [current - 0.02, current - 0.013, current - 0.006, current]
    assert evaluate_entry(prices, [5, 5, 5], 6, PRIMARY).should_enter


def test_resolution_must_be_at_least_six_hours_away():
    prices = [0.4, 0.407, 0.414, 0.42]
    assert evaluate_entry(prices, [5, 5, 5], 6.0, PRIMARY).should_enter
    decision = evaluate_entry(prices, [5, 5, 5], 5.999, PRIMARY)
    assert not decision.should_enter
    assert "resolution_too_close" in decision.reason


def test_exit_is_time_only_at_first_observed_cycle_at_or_after_60_minutes():
    opened = datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc)
    before = evaluate_exit(opened, opened + timedelta(minutes=59.99), 60)
    boundary = evaluate_exit(opened, opened + timedelta(minutes=60), 60)
    delayed = evaluate_exit(opened, opened + timedelta(minutes=67.5), 60)
    assert not before.should_exit
    assert boundary.should_exit and boundary.delay_minutes == pytest.approx(0)
    assert delayed.should_exit
    assert delayed.elapsed_minutes == pytest.approx(67.5)
    assert delayed.delay_minutes == pytest.approx(7.5)


def test_exit_fails_closed_without_a_buy_timestamp():
    decision = evaluate_exit(None, datetime.now(timezone.utc), 60)
    assert not decision.should_exit
    assert decision.reason == "missing_buy_timestamp"
