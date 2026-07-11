"""Fail-closed time coverage tests for the Banana momentum strategy."""
from datetime import datetime, timedelta

from polybot.config import MomentumConfig
from polybot.db.models import MarketSnapshot
from polybot.strategy.momentum import MomentumCalculator


NOW = datetime(2026, 7, 11, 12, 0, 0)


def snapshots(count, span_minutes, start=0.50, end=0.60, end_offset_minutes=0):
    step = span_minutes / (count - 1) if count > 1 else 0
    return [
        MarketSnapshot(
            timestamp=NOW - timedelta(
                minutes=end_offset_minutes + span_minutes - index * step
            ),
            probability=start + (end - start) * index / max(count - 1, 1),
        )
        for index in range(count)
    ]


def test_long_window_requires_all_configured_samples():
    calc = MomentumCalculator(MomentumConfig(short_window=3, long_window=72))
    points = snapshots(71, 350)

    assert calc.get_short_momentum(points) is not None
    assert calc.get_long_momentum(points) is None
    assert calc.get_entry_signal(points, 0.60) == (False, "insufficient_long_data")


def test_positive_short_momentum_never_bypasses_cold_start():
    calc = MomentumCalculator(MomentumConfig(short_window=3, long_window=72))
    points = snapshots(6, 25, start=0.50, end=0.60)

    assert calc.get_short_momentum(points) is not None
    assert calc.get_long_momentum(points) is None
    assert calc.get_entry_signal(points, 0.60) == (False, "insufficient_long_data")


def test_long_window_rejects_compressed_timestamps_even_with_72_points():
    calc = MomentumCalculator(MomentumConfig(short_window=3, long_window=72))
    points = snapshots(
        69, 50, start=0.50, end=0.55, end_offset_minutes=15
    )
    points.extend([
        MarketSnapshot(timestamp=NOW - timedelta(minutes=10), probability=0.56),
        MarketSnapshot(timestamp=NOW - timedelta(minutes=5), probability=0.58),
        MarketSnapshot(timestamp=NOW, probability=0.60),
    ])

    assert calc.get_short_momentum(points) is not None
    assert calc.get_long_momentum(points) is None
    assert calc.get_entry_signal(points, 0.60) == (False, "insufficient_long_data")


def test_fully_covered_short_and_long_windows_can_emit_golden_cross():
    config = MomentumConfig(
        short_window=3,
        long_window=72,
        golden_cross_threshold=0.01,
    )
    calc = MomentumCalculator(config)
    points = snapshots(72, 355, start=0.50, end=0.57)
    points[-3].probability = 0.57
    points[-2].probability = 0.62
    points[-1].probability = 0.68

    assert calc.get_short_momentum(points) is not None
    assert calc.get_long_momentum(points) is not None
    assert calc.get_entry_signal(points, 0.68) == (True, "golden_cross")
