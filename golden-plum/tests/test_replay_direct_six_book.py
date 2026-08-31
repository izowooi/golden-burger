from datetime import datetime, timedelta

import pytest

from scripts.replay_direct_six_book import (
    Snapshot,
    trend_confirmed,
    walk_buy,
    walk_sell,
)


def _snapshot(snapshot_id: int, probability: float, minute: int) -> Snapshot:
    return Snapshot(
        snapshot_id=snapshot_id,
        event_id="event-1",
        condition_id="condition-1",
        token_id="token-1",
        run_id=f"run-{snapshot_id}",
        result_kind="HOME",
        outcome_side="YES",
        source_minute=float(minute),
        observed_at=datetime(2026, 8, 31) + timedelta(minutes=snapshot_id),
        probability=probability,
        midpoint=probability - 0.005,
        spread=0.01,
        bids=((probability - 0.01, 20.0),),
        asks=((probability, 20.0),),
    )


def test_full_depth_walks_use_all_levels_and_fail_if_shallow() -> None:
    buy = walk_buy(((0.60, 5.0), (0.61, 10.0)), notional=5.0)
    assert buy is not None
    vwap, shares = buy
    assert vwap > 0.60
    assert shares == pytest.approx(5.0 / vwap)
    assert walk_buy(((0.60, 1.0),), notional=5.0) is None
    assert walk_sell(((0.59, 2.0), (0.58, 10.0)), shares=5.0) == pytest.approx(
        (0.59 * 2 + 0.58 * 3) / 5
    )


def test_trend_requires_same_token_first_cross_and_bounded_pullback() -> None:
    clean = [_snapshot(1, 0.72, 18), _snapshot(2, 0.74, 19), _snapshot(3, 0.75, 20)]
    assert trend_confirmed(clean, threshold=0.75, current_snapshot_id=3)

    already_above = [
        _snapshot(1, 0.75, 18),
        _snapshot(2, 0.76, 19),
        _snapshot(3, 0.77, 20),
    ]
    assert not trend_confirmed(
        already_above, threshold=0.75, current_snapshot_id=3
    )

    pullback = [
        _snapshot(1, 0.72, 18),
        _snapshot(2, 0.77, 19),
        _snapshot(3, 0.75, 20),
    ]
    assert not trend_confirmed(pullback, threshold=0.75, current_snapshot_id=3)
