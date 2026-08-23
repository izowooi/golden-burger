from __future__ import annotations

import json

from polybot.api.clob_client import ClobBookClient
from polybot.utils.retry import CooperativeDeadline, JsonResponse


class Transport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        raw = json.dumps(self.payload).encode()
        return JsonResponse(
            payload=self.payload,
            raw=raw,
            request_id="book-request",
            request_hash="request-hash",
            started_at="2026-08-15T02:10:00Z",
            received_at="2026-08-15T02:10:01Z",
            response_sha256="response-sha",
        )


def test_clob_records_observed_missing_and_full_request(config):
    payload = [
        {
            "asset_id": "token-a",
            "bids": [{"price": "0.94", "size": "10"}],
            "asks": [{"price": "0.96", "size": "10"}],
        }
    ]
    transport = Transport(payload)
    deadline = CooperativeDeadline.after(450)
    result = ClobBookClient(config.trading.orderbook, transport).fetch_books(
        "run", ["token-a", "token-b"], deadline=deadline
    )
    assert result.attempts["token-a"].status == "OBSERVED"
    assert result.attempts["token-b"].status == "MISSING"
    assert len(result.raw_payloads) == 1
    assert transport.calls[0][2]["json_body"] == [
        {"token_id": "token-a"},
        {"token_id": "token-b"},
    ]
    assert transport.calls[0][2]["deadline"] is deadline


def test_clob_malformed_book_is_explicit(config):
    payload = [{"asset_id": "token-a", "bids": {}, "asks": []}]
    result = ClobBookClient(config.trading.orderbook, Transport(payload)).fetch_books(
        "run", ["token-a"]
    )
    assert result.attempts["token-a"].status == "MALFORMED"
    assert "token-a" not in result.books


def test_clob_wrong_top_level_marks_each_token_malformed(config):
    result = ClobBookClient(
        config.trading.orderbook, Transport({"books": []})
    ).fetch_books("run", ["token-a", "token-b"])
    assert {row.status for row in result.attempts.values()} == {"MALFORMED"}


def test_clob_empty_book_is_not_zero_depth_observation(config):
    payload = [{"asset_id": "token-a", "bids": [], "asks": []}]
    result = ClobBookClient(config.trading.orderbook, Transport(payload)).fetch_books(
        "run", ["token-a"]
    )
    assert result.attempts["token-a"].status == "EMPTY_BOOK"
    assert "token-a" not in result.books
