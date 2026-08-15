from __future__ import annotations

from datetime import datetime, timezone

import pytest

from polybot.collector import (
    evaluate_crossing,
    normalize_book,
    parse_gamma_market,
    walk_asks,
    walk_bids,
)


def _market(probabilities=(0.94, 0.04, 0.02), *, sports=True):
    return {
        "id": "market-1",
        "conditionId": "condition-1",
        "eventId": "event-1",
        "question": "Will the home side win?",
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "outcomes": ["Home", "Draw", "Away"],
        "clobTokenIds": ["token-home", "token-draw", "token-away"],
        "outcomePrices": list(probabilities),
        "liquidity": 0,
        "volume": 0,
        "volume24hr": 0,
        "negRisk": True,
        "category": "Sports" if sports else "Politics",
        "tags": [{"label": "soccer" if sports else "elections"}],
        "endDate": "2027-01-01T00:00:00Z",
        "_page_number": 1,
        "_item_number": 0,
        "_page_received_at": "2026-08-15T02:00:00Z",
        "_page_request_id": "gamma-request",
    }


def test_parse_preserves_multi_negrisk_sports_and_zero_volume():
    parsed = parse_gamma_market(
        _market(),
        sweep_id="sweep",
        run_id="run",
        sports_classifier_version="gamma-fields-tags-v1",
    )
    catalog = parsed.catalog_row
    assert catalog["tradable"] == 1
    assert catalog["outcome_type"] == "MULTI"
    assert catalog["neg_risk"] == 1
    assert catalog["sports_classification"] == "SPORTS"
    assert catalog["liquidity"] == 0
    assert catalog["volume_total"] == 0
    assert catalog["volume_24h"] == 0
    assert catalog["event_id"] == "event-1"
    assert catalog["event_cluster_id"] == "event-1"
    assert len(parsed.outcome_rows) == 3


def test_only_source_tradability_excludes_market():
    market = _market(sports=False)
    market["acceptingOrders"] = False
    parsed = parse_gamma_market(
        market,
        sweep_id="sweep",
        run_id="run",
        sports_classifier_version="gamma-fields-tags-v1",
    )
    assert parsed.catalog_row["tradable"] == 0
    assert "NOT_ACCEPTING_ORDERS" in parsed.catalog_row["exclusion_reason"]
    assert parsed.catalog_row["sports_classification"] == "NON_SPORTS"


def test_initial_above_is_left_censored_and_later_crossing_is_interval_censored():
    start = datetime(2026, 8, 15, 2, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 22, 2, 0, tzinfo=timezone.utc)
    initial = evaluate_crossing(
        current_probability=0.96,
        current_observed_at="2026-08-15T02:00:00Z",
        current_condition_id="condition",
        threshold=0.95,
        prior=None,
        episode_exists=False,
        entry_start=start,
        entry_end=end,
        max_gap_minutes=25,
    )
    assert initial["status"] == "LEFT_CENSORED"
    crossing = evaluate_crossing(
        current_probability=0.97,
        current_observed_at="2026-08-15T02:20:00Z",
        current_condition_id="condition",
        threshold=0.95,
        prior={
            "condition_id": "condition",
            "probability": 0.94,
            "observed_at": "2026-08-15T02:10:00Z",
        },
        episode_exists=False,
        entry_start=start,
        entry_end=end,
        max_gap_minutes=25,
    )
    assert crossing["status"] == "NEW_CROSSING"
    assert crossing["interval_censored"] == 1
    assert crossing["jump_size"] == pytest.approx(0.03)


def test_gap_over_25_minutes_is_censored():
    result = evaluate_crossing(
        current_probability=0.96,
        current_observed_at="2026-08-15T02:30:01Z",
        current_condition_id="condition",
        threshold=0.95,
        prior={
            "condition_id": "condition",
            "probability": 0.94,
            "observed_at": "2026-08-15T02:00:00Z",
        },
        episode_exists=False,
        entry_start=datetime(2026, 8, 15, 2, 0, tzinfo=timezone.utc),
        entry_end=datetime(2026, 8, 22, 2, 0, tzinfo=timezone.utc),
        max_gap_minutes=25,
    )
    assert result["status"] == "GAP_CENSORED"


def test_ask_and_bid_walk_enforce_displayed_depth_exactly():
    asks = ((0.95, 2.0), (0.96, 10.0))
    entry = walk_asks(asks, 5.0)
    assert entry["status"] == "EXECUTABLE"
    assert entry["covered_notional"] == 5.0
    assert entry["vwap"] == 5.0 / entry["shares"]
    exit_walk = walk_bids(((0.94, 10.0),), entry["shares"])
    assert exit_walk["status"] == "EXECUTABLE"
    assert exit_walk["proceeds"] == exit_walk["vwap"] * entry["shares"]
    assert walk_asks(((0.95, 1.0),), 5.0)["status"] == "INSUFFICIENT_ASK_DEPTH"
    assert walk_bids(((0.94, 1.0),), 5.0)["status"] == "INSUFFICIENT_BID_DEPTH"


def test_book_preserves_fee_tick_minimum_and_full_depth():
    book = normalize_book(
        "token",
        {
            "asset_id": "token",
            "bids": [
                {"price": "0.90", "size": "2"},
                {"price": "0.91", "size": "3"},
            ],
            "asks": [
                {"price": "0.94", "size": "3"},
                {"price": "0.93", "size": "2"},
            ],
            "tick_size": "0.01",
            "min_order_size": "5",
            "fee_rate_bps": "20",
        },
        request_id="request",
        observed_at="2026-08-15T02:10:00Z",
    )
    assert book.bids == ((0.91, 3.0), (0.90, 2.0))
    assert book.asks == ((0.93, 2.0), (0.94, 3.0))
    assert book.tick_size == 0.01
    assert book.min_order_size == 5
    assert book.fee_rate_bps == 20
