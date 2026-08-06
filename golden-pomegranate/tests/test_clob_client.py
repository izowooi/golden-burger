"""Deterministic public CLOB sampling and fail-visible book coverage."""

from __future__ import annotations

import json

import pytest
from requests.exceptions import ChunkedEncodingError

from polybot.api.clob_client import ClobPublicClient
from polybot.config import OrderBookConfig


class Response:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload
        self.content = json.dumps(payload, sort_keys=True).encode("utf-8")

    def json(self):
        return self.payload


class PostSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def post(self, url, *, json, timeout):
        self.calls.append((url, list(json), timeout))
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return Response(value)


def _market(condition_id, tokens):
    return {
        "id": f"market-{condition_id}",
        "conditionId": condition_id,
        "clobTokenIds": tokens,
    }


def _without_ids(rows):
    return [
        {key: value for key, value in row.items() if key != "selection_id"}
        for row in rows
    ]


def test_rotating_sample_is_deterministic_and_covers_each_market_bucket_once():
    markets = [
        _market("a", ["a-yes", "a-no"]),
        _market("b", ["b-yes", "b-no", "b-invalid"]),
        _market("c", ["c-yes", "c-no"]),
    ]
    client = ClobPublicClient(
        OrderBookConfig(bucket_count=4, max_markets_per_cycle=100)
    )

    first = client.select_rotating_sample(markets, cycle_number=1)
    repeat = client.select_rotating_sample(markets, cycle_number=1)
    all_cycles = [
        client.select_rotating_sample(markets, cycle_number=cycle)
        for cycle in range(1, 5)
    ]

    assert _without_ids(first) == _without_ids(repeat)
    covered = [row["condition_id"] for rows in all_cycles for row in rows]
    assert sorted(covered) == ["a", "b", "c"]
    assert len(covered) == len(set(covered))
    variable = next(
        row for rows in all_cycles for row in rows if row["condition_id"] == "b"
    )
    assert variable["token_ids"] == ["b-yes", "b-no", "b-invalid"]
    assert variable["expected_token_count"] == 3


def test_selection_carries_bias_and_truncation_metadata():
    markets = [
        _market(f"condition-{number}", [f"token-{number}"]) for number in range(30)
    ]
    client = ClobPublicClient(OrderBookConfig(bucket_count=1, max_markets_per_cycle=3))

    rows = client.select_rotating_sample(markets, cycle_number=1)

    assert len(rows) == 3
    for row in rows:
        assert row["selection_reason"] == "deterministic_rotating_bucket"
        assert row["sampler_version"]
        assert row["frame_market_count"] == 30
        assert row["bucket_candidate_count"] == 30
        assert row["sample_max"] == 3
        assert row["sampled_market_count"] == 3
        assert row["truncated_count"] == 27
        assert row["truncation_applied"] == 1
        assert "SHA-256" in row["inclusion_probability_basis"]
        assert row["bucket_count"] == 1
        assert row["bucket_number"] == 0
        assert row["selection_rank"]


def test_overfull_bucket_uses_wall_clock_visits_to_cover_cyclic_windows():
    markets = [
        _market(f"condition-{number}", [f"token-{number}"]) for number in range(8)
    ]
    client = ClobPublicClient(OrderBookConfig(bucket_count=1, max_markets_per_cycle=3))

    visits = [
        client.select_rotating_sample(
            markets,
            cycle_number=1,  # shard-local cycle may reset; slot must not.
            sampler_slot=slot,
        )
        for slot in (0, 1, 2)
    ]

    assert [rows[0]["rotation_offset"] for rows in visits] == [0, 3, 6]
    assert [rows[0]["bucket_visit_index"] for rows in visits] == [0, 1, 2]
    assert [rows[0]["wrap_around"] for rows in visits] == [0, 0, 1]
    covered = {row["condition_id"] for rows in visits for row in rows}
    assert covered == {f"condition-{number}" for number in range(8)}
    assert all(
        row["sampler_slot"] == visit
        for visit, rows in enumerate(visits)
        for row in rows
    )


def test_fetch_books_preserves_raw_response_and_accounts_for_every_token():
    payload = [
        {
            "asset_id": "yes",
            "bids": [
                {"price": "0.4", "size": "10"},
                {"price": "0.3", "size": "20"},
            ],
            "asks": [{"price": "0.5", "size": "11"}],
            "tick_size": "0.01",
            "min_order_size": "5",
            "hash": "book-yes",
        },
        {
            "asset_id": "no",
            "bids": [{"price": "0.5", "size": "9"}],
            "asks": [{"price": "0.6", "size": "8"}],
            "hash": "book-no",
        },
    ]
    session = PostSession(payload)
    raw_calls = []
    client = ClobPublicClient(
        OrderBookConfig(batch_token_limit=500, normalized_levels=2),
        session=session,
        raw_payload_sink=lambda **kwargs: raw_calls.append(kwargs) or "raw-books",
    )
    selection = {
        "selection_id": "selection",
        "condition_id": "condition",
        "token_ids": ["yes", "no"],
        "expected_token_count": 2,
    }

    result = client.fetch_books([selection], cycle_number=1, run_id="run")

    assert result.status == "SUCCESS"
    assert result.error_count == 0
    assert result.selections[0]["status"] == "COMPLETE"
    assert result.selections[0]["observed_token_count"] == 2
    assert result.selections[0]["coverage_ratio"] == 1.0
    assert {book["token_id"] for book in result.books} == {"yes", "no"}
    assert all(book["raw_payload_id"] == "raw-books" for book in result.books)
    yes = next(book for book in result.books if book["token_id"] == "yes")
    assert yes["raw_book"]["bids"][0] == {"price": "0.4", "size": "10"}
    assert yes["raw_book"]["asks"][0] == {"price": "0.5", "size": "11"}
    assert raw_calls[0]["kind"] == "clob_books_exact_batch"
    assert raw_calls[0]["content"] == Response(payload).content
    assert session.calls[0][0].endswith("/books")
    assert session.calls[0][1] == [{"token_id": "yes"}, {"token_id": "no"}]


def test_missing_book_is_partial_instead_of_silent_success():
    session = PostSession(
        [
            {
                "asset_id": "yes",
                "bids": [],
                "asks": [],
            }
        ]
    )
    client = ClobPublicClient(OrderBookConfig(), session=session)
    selection = {
        "selection_id": "selection",
        "condition_id": "condition",
        "token_ids": ["yes", "no"],
    }

    result = client.fetch_books([selection], cycle_number=1)

    # The only selected market lacks one outcome book, so the component has
    # zero complete market selections even though this selection is PARTIAL.
    assert result.status == "ERROR"
    assert result.error_count == 1
    assert result.selections[0]["status"] == "PARTIAL"
    assert result.selections[0]["observed_token_count"] == 1
    assert result.selections[0]["coverage_ratio"] == 0.5
    assert "book_missing_from_batch_response" in result.selections[0]["error_message"]


@pytest.mark.parametrize(
    "malformed",
    [
        {"asset_id": "bad", "bids": "not-json", "asks": []},
        {"asset_id": "bad", "bids": {}, "asks": []},
        {"asset_id": "bad", "bids": [{"price": "x", "size": "1"}], "asks": []},
        {"asset_id": "bad", "bids": [], "asks": [{"price": "0.5"}]},
        {"asset_id": "bad", "bids": [], "asks": [{"price": "1.1", "size": "1"}]},
    ],
)
def test_malformed_book_levels_are_token_errors_and_degrade_component(malformed):
    session = PostSession(
        [
            {
                "asset_id": "good",
                "bids": [{"price": "0.4", "size": "2"}],
                "asks": [{"price": "0.5", "size": "2"}],
            },
            malformed,
        ]
    )
    client = ClobPublicClient(OrderBookConfig(), session=session)

    result = client.fetch_books(
        [
            {"selection_id": "good-selection", "token_ids": ["good"]},
            {"selection_id": "bad-selection", "token_ids": ["bad"]},
        ],
        cycle_number=1,
    )

    assert result.status == "PARTIAL"
    assert result.error_count == 1
    assert [row["status"] for row in result.selections] == ["COMPLETE", "ERROR"]
    assert [book["token_id"] for book in result.books] == ["good"]
    bad_attempt = next(row for row in result.token_attempts if row["token_id"] == "bad")
    assert bad_attempt["status"] == "ERROR"
    assert bad_attempt["error_type"] == "MalformedBook"
    assert bad_attempt["error_message"]


def test_malformed_numeric_level_is_explicit_instead_of_successful_observation():
    session = PostSession(
        [
            {
                "asset_id": "yes",
                "bids": [{"price": "nan", "size": "10"}],
                "asks": [],
            }
        ]
    )
    client = ClobPublicClient(OrderBookConfig(), session=session)

    result = client.fetch_books(
        [
            {
                "selection_id": "selection",
                "condition_id": "condition",
                "token_ids": ["yes"],
            }
        ],
        cycle_number=1,
    )

    assert result.status == "ERROR"
    assert result.books == ()
    assert result.selections[0]["status"] == "ERROR"
    assert result.token_attempts[0]["status"] == "ERROR"
    assert result.token_attempts[0]["error_type"] == "MalformedBook"
    assert "not_finite" in result.token_attempts[0]["error_message"]


def test_batch_transport_error_is_committed_as_component_error_without_retry():
    evidence = []
    session = PostSession(ChunkedEncodingError("truncated public book batch"))
    client = ClobPublicClient(
        OrderBookConfig(), session=session, evidence_sink=evidence.append
    )

    result = client.fetch_books(
        [
            {
                "selection_id": "selection",
                "condition_id": "condition",
                "token_ids": ["yes", "no"],
            }
        ],
        cycle_number=1,
        run_id="run",
    )

    assert len(session.calls) == 1
    assert result.status == "ERROR"
    assert result.selections[0]["status"] == "ERROR"
    assert "ChunkedEncodingError" in result.selections[0]["error_message"]
    assert evidence[0]["status"] == "REQUEST_ERROR"


def test_empty_selection_is_explicit_and_performs_no_network():
    session = PostSession()
    client = ClobPublicClient(OrderBookConfig(), session=session)

    result = client.fetch_books([], cycle_number=1)

    assert result.status == "EMPTY"
    assert result.selections == ()
    assert result.books == ()
    assert session.calls == []


def test_clob_public_client_never_adds_authorization_headers():
    session = PostSession()
    ClobPublicClient(OrderBookConfig(), session=session)

    assert "Authorization" not in session.headers
    assert not any(name.startswith("POLY_") for name in session.headers)
    assert session.trust_env is False
