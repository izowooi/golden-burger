from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import gzip
import json

import pytest

from polybot.api.clob_client import (
    MalformedBookError,
    canonical_book_gzip,
    classify_resolution,
    parse_book,
    walk_asks,
    walk_bids,
)
from polybot.crossings import PriorThresholdState, evaluate_threshold_vector


def source_book():
    return {
        "asset_id": "token",
        "bids": [{"price": "0.79", "size": "5"}, {"price": "0.78", "size": "20"}],
        "asks": [{"price": "0.80", "size": "5"}, {"price": "0.81", "size": "20"}],
        "timestamp": "123",
        "tick_size": "0.01",
        "min_order_size": "5",
    }


def test_full_and_partial_book_walks():
    book = parse_book(source_book(), "token")
    full = walk_asks(book.asks, 5)
    partial = walk_asks(book.asks, 100)
    assert full.status == "FULL" and full.vwap is not None
    assert partial.status == "PARTIAL" and partial.remaining > 0
    bid = walk_bids(book.bids, full.shares)
    assert bid.status == "FULL"
    assert bid.vwap < full.vwap


@pytest.mark.parametrize(
    "mutation",
    [
        lambda book: book.update(asset_id="wrong"),
        lambda book: book.update(asks="bad"),
        lambda book: book["asks"].append({"price": "nan", "size": "1"}),
        lambda book: book["bids"].append({"price": "0.5", "size": "-1"}),
    ],
)
def test_malformed_full_books_fail_closed(mutation):
    book = source_book()
    mutation(book)
    with pytest.raises(MalformedBookError):
        parse_book(book, "token")


def test_canonical_gzip_is_deterministic_once_per_payload():
    first, digest_one, raw_bytes = canonical_book_gzip(source_book(), "token")
    second, digest_two, _ = canonical_book_gzip(source_book(), "token")
    assert first == second
    assert digest_one == digest_two
    assert len(gzip.decompress(first)) == raw_bytes
    assert json.loads(gzip.decompress(first)) == source_book()


def test_first_observation_above_is_left_censored():
    vector = evaluate_threshold_vector(
        current_vwap=0.80,
        current_observed_at="2026-08-27T00:00:00Z",
        prior=None,
        thresholds=(Decimal("0.79"), Decimal("0.81")),
        max_gap_seconds=450,
    )
    assert vector.states["0.79"] == "LEFT_CENSORED"
    assert vector.upward_crossings == ()


def test_genuine_crossing_and_gap_censoring():
    prior = PriorThresholdState("2026-08-27T00:00:00Z", 0.80, "FULL")
    crossed = evaluate_threshold_vector(
        current_vwap=0.82,
        current_observed_at="2026-08-27T00:05:00Z",
        prior=prior,
        thresholds=(Decimal("0.81"),),
        max_gap_seconds=450,
    )
    assert crossed.upward_crossings == (Decimal("0.81"),)
    gap = evaluate_threshold_vector(
        current_vwap=0.82,
        current_observed_at="2026-08-27T00:10:00Z",
        prior=prior,
        thresholds=(Decimal("0.81"),),
        max_gap_seconds=450,
    )
    assert gap.gap_censored == (Decimal("0.81"),)
    assert gap.upward_crossings == ()


def test_missing_prior_full_depth_is_gap_censored():
    prior = PriorThresholdState("2026-08-27T00:00:00Z", None, "PARTIAL")
    vector = evaluate_threshold_vector(
        current_vwap=0.90,
        current_observed_at="2026-08-27T00:05:00Z",
        prior=prior,
        thresholds=(Decimal("0.85"),),
        max_gap_seconds=450,
    )
    assert vector.states["0.85"] == "GAP_CENSORED"


def test_unique_void_tie_and_open_resolution_classes():
    assert classify_resolution(
        {"closed": True, "tokens": [{"winner": True}, {"winner": False}]}
    ) == ("RESOLVED", (0,))
    assert classify_resolution(
        {"closed": True, "tokens": [{"price": 0.5}, {"price": 0.5}]}
    ) == ("VOID", ())
    assert classify_resolution(
        {"closed": True, "tokens": [{"winner": True}, {"winner": True}]}
    ) == ("TIE", (0, 1))
    assert classify_resolution(
        {"closed": False, "tokens": [{}, {}]}
    ) == ("OPEN", ())
