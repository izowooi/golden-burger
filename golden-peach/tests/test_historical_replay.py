import pytest

from scripts.replay_watermelon_kickoff_leader import (
    Book,
    _parse_source_minute,
    _synthetic_no_book,
    _walk_buy,
    _walk_sell,
)


def test_legacy_no_book_is_an_explicit_complement_not_a_direct_book() -> None:
    yes = Book(
        bids=((0.60, 10.0), (0.59, 20.0)),
        asks=((0.62, 10.0), (0.63, 20.0)),
    )
    no = _synthetic_no_book(yes)

    assert tuple(value for level in no.bids for value in level) == pytest.approx(
        (0.38, 10.0, 0.37, 20.0)
    )
    assert tuple(value for level in no.asks for value in level) == pytest.approx(
        (0.40, 10.0, 0.41, 20.0)
    )
    assert no.midpoint == pytest.approx(0.39)


def test_full_depth_walks_do_not_assume_top_level_fill() -> None:
    buy_vwap, shares = _walk_buy(((0.50, 4.0), (0.60, 10.0)), 5.0)
    assert shares == 9.0
    assert buy_vwap == 5.0 / 9.0
    assert _walk_sell(((0.60, 4.0), (0.50, 10.0)), shares) == (2.4 + 2.5) / 9.0


def test_replay_source_clock_normalizes_second_half_and_rejects_unknown() -> None:
    assert _parse_source_minute(
        {"sports_clock": {"period": "2H", "elapsed_raw": "12:30"}}
    ) == 57.5
    assert _parse_source_minute(
        {"sports_clock": {"period": "2H", "elapsed_raw": "90+4"}}
    ) == 94.0
    assert _parse_source_minute(
        {"sports_clock": {"period": "Unknown", "elapsed_raw": "5"}}
    ) is None
