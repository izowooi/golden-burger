from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from scripts.replay_direct_six_book import (
    Snapshot,
    replay_cell,
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


def _six_run(run_index: int, leader_price: float, minute: int) -> list[Snapshot]:
    rows = []
    identities = [
        ("HOME", "YES", 0.45),
        ("HOME", "NO", 0.55),
        ("DRAW", "YES", 0.30),
        ("DRAW", "NO", 0.60),
        ("AWAY", "YES", 0.42),
        ("AWAY", "NO", leader_price),
    ]
    for offset, (result_kind, outcome_side, probability) in enumerate(identities):
        rows.append(
            Snapshot(
                snapshot_id=run_index * 10 + offset,
                event_id="event-1",
                condition_id=f"condition-{result_kind.lower()}",
                token_id=f"{result_kind}-{outcome_side}",
                run_id=f"run-{run_index}",
                result_kind=result_kind,
                outcome_side=outcome_side,
                source_minute=float(minute),
                observed_at=datetime(2026, 8, 31) + timedelta(minutes=run_index),
                probability=probability,
                midpoint=probability - 0.005,
                spread=0.01,
                bids=((probability - 0.01, 100.0),),
                asks=((probability, 100.0),),
            )
        )
    return rows


def test_replay_defaults_to_full_match_without_time_exit() -> None:
    snapshots = [
        *_six_run(1, 0.72, 73),
        *_six_run(2, 0.74, 74),
        *_six_run(3, 0.75, 75),
        *_six_run(4, 0.80, 80),
    ]

    current = replay_cell(
        snapshots,
        entry_threshold=0.75,
        target_price=0.90,
        stop_delta=0.15,
    )
    assert len(current) == 1
    assert current[0].exit_reason == "right_censored"
    assert current[0].right_censored is True
    assert current[0].pnl_usdc is None
    legacy = replay_cell(
        snapshots,
        entry_threshold=0.75,
        target_price=0.90,
        stop_delta=0.15,
        min_source_minute=5,
        max_source_minute=75,
        force_exit_minute=80,
    )
    assert len(legacy) == 1
    assert legacy[0].exit_reason == "time_exit"


def _direct_run(run_index: int, home_price: float) -> list[Snapshot]:
    rows = []
    for offset, (result_kind, probability) in enumerate(
        (("HOME", home_price), ("AWAY", 1 - home_price))
    ):
        rows.append(
            Snapshot(
                snapshot_id=run_index * 10 + offset,
                event_id="mlb-event-1",
                condition_id="mlb-condition-1",
                token_id=f"mlb-{result_kind.lower()}",
                run_id=f"run-{run_index}",
                result_kind=result_kind,
                outcome_side="DIRECT",
                source_minute=None,
                observed_at=datetime(2026, 9, 1) + timedelta(minutes=run_index),
                probability=probability,
                midpoint=probability - 0.005,
                spread=0.01,
                bids=((probability - 0.01, 100.0),),
                asks=((probability, 100.0),),
            )
        )
    return rows


def test_mlb_replay_uses_timestamp_cadence_without_inventing_source_minutes() -> None:
    snapshots = [
        *_direct_run(1, 0.72),
        *_direct_run(2, 0.74),
        *_direct_run(3, 0.75),
        *_direct_run(4, 0.91),
    ]

    trades = replay_cell(
        snapshots,
        sport_family="mlb",
        entry_threshold=0.75,
        target_price=0.90,
        stop_delta=0.15,
    )

    assert len(trades) == 1
    assert trades[0].result_kind == "HOME"
    assert trades[0].entry_source_minute is None
    assert trades[0].exit_source_minute is None
    assert trades[0].exit_reason == "take_profit"


def test_stop_triggers_on_best_bid_but_uses_full_depth_vwap_for_proceeds() -> None:
    snapshots = [
        *_direct_run(1, 0.72),
        *_direct_run(2, 0.74),
        *_direct_run(3, 0.75),
    ]
    final = _direct_run(4, 0.61)
    final[0] = replace(
        final[0],
        bids=((0.60, 1.0), (0.50, 100.0)),
        asks=((0.61, 100.0),),
    )
    snapshots.extend(final)

    trades = replay_cell(
        snapshots,
        sport_family="mlb",
        entry_threshold=0.75,
        target_price=0.90,
        stop_delta=0.15,
    )

    assert len(trades) == 1
    assert trades[0].exit_reason == "stop"
    assert trades[0].exit_price < 0.60
    assert trades[0].exit_residual_shares == 0


def test_partial_capacity_path_settles_residual_at_terminal_with_fee_sensitivity() -> None:
    snapshots = [
        *_direct_run(1, 0.72),
        *_direct_run(2, 0.74),
        *_direct_run(3, 0.75),
        *_direct_run(4, 0.80),
    ]
    entry_index = next(
        index
        for index, snapshot in enumerate(snapshots)
        if snapshot.run_id == "run-3" and snapshot.token_id == "mlb-home"
    )
    snapshots[entry_index] = replace(
        snapshots[entry_index],
        asks=((0.75, 5.0),),
    )

    trades = replay_cell(
        snapshots,
        sport_family="mlb",
        entry_threshold=0.75,
        target_price=0.90,
        stop_delta=0.15,
        notional_usdc=10.0,
        fee_bps=100.0,
        terminal_payouts={"mlb-home": 1.0, "mlb-away": 0.0},
    )

    assert len(trades) == 1
    trade = trades[0]
    assert trade.entry_full_fill is False
    assert trade.entry_filled_notional_usdc == pytest.approx(3.75)
    assert trade.entry_residual_usdc == pytest.approx(6.25)
    assert trade.exit_reason == "terminal_resolution"
    assert trade.terminal_payout == 1.0
    assert trade.fee_usdc == pytest.approx(0.0375)
    assert trade.pnl_usdc == pytest.approx(1.2125)
