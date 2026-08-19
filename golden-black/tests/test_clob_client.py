from __future__ import annotations

import pytest

from polybot.api.clob_client import (
    normalized_levels,
    walk_asks,
    walk_bids,
    walk_bids_partial,
)


BOOK = {
    "asks": [{"price": "0.95", "size": "3"}, {"price": "0.94", "size": "3"}],
    "bids": [{"price": "0.92", "size": "4"}, {"price": "0.93", "size": "2"}],
}


def test_levels_sort_into_executable_order() -> None:
    assert normalized_levels(BOOK, "asks")[0][0] == 0.94
    assert normalized_levels(BOOK, "bids")[0][0] == 0.93


def test_full_five_dollar_ask_walk() -> None:
    walk = walk_asks(BOOK, 5)
    assert walk is not None
    assert walk.cost == 5
    assert walk.best_ask == 0.94
    assert walk.shares > 5
    assert 0.94 <= walk.vwap <= 0.95


def test_fixed_share_bid_walk() -> None:
    walk = walk_bids(BOOK, 5)
    assert walk is not None
    assert walk.shares == pytest.approx(5)
    assert walk.vwap == pytest.approx((2 * 0.93 + 3 * 0.92) / 5)


def test_insufficient_depth_is_not_imputed() -> None:
    assert walk_asks({"asks": [{"price": "0.94", "size": "1"}]}, 5) is None


def test_partial_bid_walk_preserves_shortfall_for_stop_retry() -> None:
    walk = walk_bids_partial({"bids": [{"price": "0.78", "size": "2"}]}, 5)
    assert walk is not None
    assert walk.complete is False
    assert walk.best_bid == 0.78
    assert walk.filled_shares == 2
    assert walk.remaining_shares == 3
    assert walk.proceeds == pytest.approx(1.56)
    assert walk_bids({"bids": [{"price": "0.78", "size": "2"}]}, 5) is None
