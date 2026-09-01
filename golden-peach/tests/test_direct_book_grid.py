import pytest

from scripts.analyze_direct_book_grid import (
    BookObservation,
    Entry,
    _evaluate,
    _execution_fee,
    _walk_sell,
)


def _entry() -> Entry:
    return Entry(
        event_id="event-1",
        condition_id="condition-1",
        token_id="token-1",
        outcome="No",
        outcome_side="NO",
        result_kind="AWAY",
        observed_at="2026-08-30 13:00:00",
        source_minute=3.0,
        entry_vwap=0.80,
        shares=6.25,
        fee_rate=0.05,
        execution_state="TRADE_CREATED",
        execution_reason="exact_order_submission_linked",
        trade_id=1,
    )


def _book(at: str, bid: float, minute: float) -> BookObservation:
    return BookObservation(
        observed_at=at,
        source_minute=minute,
        best_bid=bid,
        best_ask=bid + 0.01,
        spread=0.01,
        bids=((bid, 100.0),),
    )


def test_walk_sell_uses_complete_depth() -> None:
    walked = _walk_sell(((0.90, 2.0), (0.80, 4.0)), 5.0)
    assert walked is not None
    vwap, proceeds = walked
    assert proceeds == pytest.approx(4.2)
    assert vwap == pytest.approx(0.84)


def test_fee_matches_frozen_sports_taker_formula() -> None:
    assert _execution_fee(
        shares=6.25, price=0.80, fee_rate=0.05
    ) == pytest.approx(0.05)


def test_replay_uses_first_full_depth_take_profit_and_fees() -> None:
    result = _evaluate(
        _entry(),
        (
            _book("2026-08-30 13:01:00", 0.82, 4.0),
            _book("2026-08-30 13:02:00", 0.85, 5.0),
        ),
        take_profit_delta=0.05,
        stop_loss_delta=0.10,
    )
    assert result is not None
    assert result.reason == "TAKE_PROFIT"
    assert result.observed_at == "2026-08-30 13:02:00"
    assert result.gross_pnl_usdc == pytest.approx(0.3125)
    assert result.fee_net_pnl_usdc < result.gross_pnl_usdc


def test_replay_stop_requires_proven_pre_cutoff_clock() -> None:
    no_clock = BookObservation(
        observed_at="2026-08-30 13:01:00",
        source_minute=None,
        best_bid=0.60,
        best_ask=0.61,
        spread=0.01,
        bids=((0.60, 100.0),),
    )
    assert (
        _evaluate(
            _entry(),
            (no_clock,),
            take_profit_delta=0.05,
            stop_loss_delta=0.10,
        )
        is None
    )
    stopped = _evaluate(
        _entry(),
        (_book("2026-08-30 13:01:00", 0.60, 20.0),),
        take_profit_delta=0.05,
        stop_loss_delta=0.10,
    )
    assert stopped is not None
    assert stopped.reason == "STOP"
