"""Shared Gamma/CLOB evidence contracts adapted from Golden Date.

These tests intentionally exercise the reusable transport and execution-ledger
surface in the Queen package rather than strategy decisions.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
import requests
from py_clob_client_v2.exceptions import PolyApiException

from polybot_observability import (
    ClobResponseContractError,
    ClobResponseUnavailableError,
    SubmissionEvidenceError,
)
from polybot.api.clob_client import ClobClientWrapper
from polybot.api.gamma_client import GammaClient


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class KeysetSession:
    def __init__(self):
        self.calls = []
        self.pages = [
            {
                "markets": [
                    {
                        "conditionId": "one",
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "20000",
                        "volume": "1000",
                        "outcomes": '["Yes", "No"]',
                    },
                    {
                        "conditionId": "closed",
                        "active": True,
                        "closed": True,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "50000",
                        "volume": "1000",
                    },
                    {
                        "conditionId": "server-filter-leak",
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "1",
                        "volume": "1000",
                    },
                ],
                "next_cursor": "cursor-1",
            },
            {
                "markets": [
                    {
                        "conditionId": "one",
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "20000",
                        "volume": "1000",
                    },
                    {
                        "conditionId": "two",
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "30000",
                        "volume": "2000",
                    },
                    {
                        "conditionId": "missing-tradability",
                        "active": True,
                        "liquidity": "30000",
                        "volume": "2000",
                    },
                ]
            },
        ]

    def get(self, url, params, timeout):
        self.calls.append((url, dict(params), timeout))
        return Response(self.pages.pop(0))


def test_gamma_keyset_sweep_deduplicates_and_attests_membership(monkeypatch):
    sleeps = []
    monkeypatch.setattr("polybot.api.gamma_client.time.sleep", sleeps.append)
    client = GammaClient()
    client.session = KeysetSession()

    markets = client.get_all_tradable_markets(min_liquidity=10_000)

    assert [market["conditionId"] for market in markets] == ["one", "two"]
    assert client.session.calls[0][0].endswith("/markets/keyset")
    assert client.session.calls[1][1]["after_cursor"] == "cursor-1"
    assert all(call[2] == (3.05, 20.0) for call in client.session.calls)
    assert sleeps == [client.KEYSET_PAGE_INTERVAL_SECONDS]
    attestation = client.last_sweep_attestation
    assert attestation["cursor_complete"] is True
    assert attestation["pages"] == 2
    assert attestation["raw_market_count"] == 6
    assert attestation["unique_condition_count"] == 5
    assert attestation["qualified_market_count"] == 2
    assert attestation["duplicate_raw_count"] == 1
    assert len(attestation["membership_digest_sha256"]) == 64
    memberships = {item["condition_id"]: item for item in attestation["memberships"]}
    assert memberships["one"]["raw_seen_count"] == 2
    assert memberships["one"]["qualified"] is True
    assert memberships["closed"]["qualification_reason"] == "closed_or_missing"
    assert memberships["server-filter-leak"]["qualification_reason"] == "below_min_liquidity"
    assert memberships["missing-tradability"]["qualified"] is False


class TimeoutSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        raise requests.exceptions.Timeout("read timed out")


def test_gamma_timeout_retry_is_bounded_and_never_attests_partial(monkeypatch):
    sleeps = []
    monkeypatch.setattr("polybot.utils.retry.time.sleep", sleeps.append)
    client = GammaClient()
    client.session = TimeoutSession()

    with pytest.raises(requests.exceptions.Timeout):
        client.get_all_tradable_markets(min_liquidity=1_000)

    assert len(client.session.calls) == 6
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 32.0]
    assert client.last_sweep_attestation is None


@pytest.mark.parametrize(
    "filters",
    [
        {"min_liquidity": -1},
        {"min_volume": -1},
        {"min_liquidity": float("nan")},
        {"min_volume": float("inf")},
    ],
)
def test_gamma_rejects_invalid_filters_before_network(filters):
    client = GammaClient()
    client.session = SimpleNamespace(
        get=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network must not be called")
        )
    )
    with pytest.raises(ValueError, match="finite and non-negative"):
        client.get_all_tradable_markets(**filters)


@pytest.mark.parametrize("field", ["liquidity", "volume"])
@pytest.mark.parametrize("value", [None, "", "   ", True, False])
def test_gamma_qualification_rejects_missing_blank_and_boolean_numeric_evidence(
    field, value
):
    market = {
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "liquidity": 0,
        "volume": 0,
    }
    if value is None:
        market.pop(field)
    else:
        market[field] = value

    assert (
        GammaClient._qualification_reason(market, 0, 0)
        == "invalid_numeric_filter_field"
    )


@pytest.mark.parametrize("value", [0, 0.0, "0", "0.0"])
def test_gamma_qualification_accepts_literal_numeric_zero(value):
    market = {
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "liquidity": value,
        "volume": value,
    }
    assert GammaClient._qualification_reason(market, 0, 0) == "qualified"


@pytest.mark.parametrize("model", [{"price": "0.41"}, SimpleNamespace(price="0.41")])
def test_best_bid_supports_mapping_and_typed_response(model):
    calls = []
    wrapper = ClobClientWrapper(SimpleNamespace())
    wrapper._client = SimpleNamespace(
        get_price=lambda token, side: calls.append((token, side)) or model
    )
    wrapper._initialized = True
    assert wrapper.get_best_bid("token") == 0.41
    assert calls == [("token", "BUY")]


@pytest.mark.parametrize("model", [{"price": "0.43"}, SimpleNamespace(price="0.43")])
def test_best_ask_supports_mapping_and_typed_response(model):
    calls = []
    wrapper = ClobClientWrapper(SimpleNamespace())
    wrapper._client = SimpleNamespace(
        get_price=lambda token, side: calls.append((token, side)) or model
    )
    wrapper._initialized = True
    assert wrapper.get_best_ask("token") == 0.43
    assert calls == [("token", "SELL")]


@pytest.mark.parametrize(
    "book",
    [
        {
            "bids": [{"price": "0.89", "size": "20"}, {"price": "0.90", "size": "5"}],
            "asks": [
                {"price": "0.94", "size": "100"},
                {"price": "0.92", "size": "10"},
                {"price": "0.91", "size": "5"},
            ],
        },
        SimpleNamespace(
            bids=[
                SimpleNamespace(price="0.89", size="20"),
                SimpleNamespace(price="0.90", size="5"),
            ],
            asks=[
                SimpleNamespace(price="0.94", size="100"),
                SimpleNamespace(price="0.92", size="10"),
                SimpleNamespace(price="0.91", size="5"),
            ],
        ),
    ],
)
def test_buy_book_depth_uses_one_snapshot_and_caps_best_ask_window(book):
    calls = []
    wrapper = ClobClientWrapper(SimpleNamespace())
    wrapper._client = SimpleNamespace(
        get_order_book=lambda token: calls.append(token) or book
    )
    wrapper._initialized = True

    depth = wrapper.get_buy_book_depth(
        "token",
        ask_limit_price=0.94,
        max_price_window=0.01,
    )

    assert calls == ["token"]
    assert depth.best_bid == 0.90
    assert depth.best_ask == 0.91
    assert depth.spread == pytest.approx(0.01)
    assert depth.ask_limit_price == pytest.approx(0.92)
    assert depth.ask_depth_shares == pytest.approx(15.0)


@pytest.mark.parametrize(
    "book",
    [
        {"bids": [], "asks": [{"price": "0.91", "size": "5"}]},
        {
            "bids": [{"price": "0.92", "size": "5"}],
            "asks": [{"price": "0.91", "size": "5"}],
        },
        {
            "bids": [{"price": "0.90", "size": "5"}],
            "asks": [{"price": "bad", "size": "5"}],
        },
    ],
)
def test_buy_book_depth_fails_closed_on_empty_crossed_or_malformed_book(book):
    wrapper = ClobClientWrapper(SimpleNamespace())
    wrapper._client = SimpleNamespace(get_order_book=lambda _token: book)
    wrapper._initialized = True

    with pytest.raises(
        (ClobResponseContractError, ClobResponseUnavailableError)
    ):
        wrapper.get_buy_book_depth("token", ask_limit_price=0.94)


class BatchMidpointClient:
    def __init__(self, responses, live_midpoint=None):
        self.responses = iter(responses)
        self.live_midpoint = live_midpoint or {"mid": "0.61"}
        self.batch_calls = []
        self.live_calls = []

    def get_midpoints(self, params):
        self.batch_calls.append([param.token_id for param in params])
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(params)
        return response

    def get_midpoint(self, token_id):
        self.live_calls.append(token_id)
        return self.live_midpoint


def _batch_wrapper(client):
    wrapper = ClobClientWrapper(SimpleNamespace())
    wrapper._client = client
    wrapper._initialized = True
    return wrapper


def test_batch_midpoints_chunk_unique_tokens_and_normalize_values():
    client = BatchMidpointClient(
        [
            lambda params: {param.token_id: "0.41" for param in params},
            lambda params: {param.token_id: {"mid": "0.42"} for param in params},
        ]
    )
    wrapper = _batch_wrapper(client)
    tokens = [f"token-{index}" for index in range(501)]

    result = wrapper.get_midpoints(["", tokens[0], *tokens, None])

    assert [len(call) for call in client.batch_calls] == [500, 1]
    assert len(result) == 501
    assert result["token-0"] == 0.41
    assert result["token-500"] == 0.42


def test_midpoint_snapshot_fails_closed_for_requested_missing_and_restores_scope():
    client = BatchMidpointClient([{"cached": "0.44"}])
    wrapper = _batch_wrapper(client)

    with wrapper.midpoint_snapshot(["cached", "missing"]):
        assert wrapper.get_midpoint("cached") == 0.44
        with pytest.raises(ClobResponseUnavailableError):
            wrapper.get_midpoint("missing")
        assert wrapper.get_midpoint("not-requested") == 0.61

    assert wrapper.get_midpoint("cached") == 0.61
    assert client.live_calls == ["not-requested", "cached"]


class CancelClient:
    def __init__(self, response, detail=None):
        self.response = response
        self.detail = detail or {
            "id": "cancel-me",
            "status": "ORDER_STATUS_CANCELED",
            "size_matched": "0",
        }

    def cancel_orders(self, order_ids):
        assert order_ids == ["cancel-me"]
        return self.response

    def get_order(self, order_id):
        assert order_id == "cancel-me"
        return self.detail


def test_cancel_requires_authoritative_terminal_zero_fill():
    wrapper = ClobClientWrapper(SimpleNamespace())
    wrapper._client = CancelClient({"canceled": ["cancel-me"], "not_canceled": {}})
    wrapper._initialized = True

    result = wrapper.cancel_order("cancel-me")

    assert result["verified_order_status"] == "CANCELED"
    assert result["verified_size_matched"] == 0.0


def test_cancel_unproved_fill_state_fails_closed():
    wrapper = ClobClientWrapper(SimpleNamespace())
    wrapper._client = CancelClient(
        {"canceled": [], "not_canceled": {"cancel-me": "already matched"}},
        detail={"id": "cancel-me", "status": "MATCHED", "size_matched": "1"},
    )
    wrapper._initialized = True
    with pytest.raises(SubmissionEvidenceError):
        wrapper.cancel_order("cancel-me")


class PlacementClient:
    def __init__(self, db_path):
        self.db_path = db_path

    def create_order(self, order_args):
        with sqlite3.connect(self.db_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM order_submissions"
            ).fetchone()[0] == 0
        return {"signed": True, "args": order_args}

    def post_order(self, signed_order, order_type):
        assert signed_order["signed"] is True
        with sqlite3.connect(self.db_path) as connection:
            assert connection.execute(
                "SELECT response_status FROM order_submissions"
            ).fetchall() == [("INTENT",)]
        return {
            "success": True,
            "orderID": "placed-order",
            "status": "live",
            "makingAmount": "4000000",
            "takingAmount": "10000000",
        }

    def cancel_orders(self, order_ids):
        return {"canceled": order_ids, "not_canceled": {}}


def _placement_wrapper(db_path, client):
    wrapper = ClobClientWrapper(
        SimpleNamespace(), audit_db_path=db_path, strategy_name="golden-queen"
    )
    wrapper._client = client
    wrapper._initialized = True
    return wrapper


def test_order_intent_is_durable_before_post(tmp_path):
    db_path = tmp_path / "trades.db"
    wrapper = _placement_wrapper(db_path, PlacementClient(db_path))

    assert wrapper.place_limit_order("token", 0.4, 10, "BUY")["orderID"] == "placed-order"
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT order_id, response_status, making_amount, taking_amount, "
            "needs_reconciliation FROM order_submissions"
        ).fetchone()
    assert row == ("placed-order", "LIVE", 4.0, 10.0, 1)


def test_preflight_timeout_creates_no_uncertain_intent(tmp_path):
    class PreflightTimeout:
        def create_order(self, order_args):
            raise PolyApiException(error_msg="Request exception!")

        def post_order(self, *_args):
            raise AssertionError("POST must not run after preflight failure")

    db_path = tmp_path / "trades.db"
    wrapper = _placement_wrapper(db_path, PreflightTimeout())

    result = wrapper.place_limit_order("token", 0.4, 10, "SELL")

    assert result["success"] is False
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM order_submissions"
        ).fetchone()[0] == 0


def test_post_timeout_is_quarantined_without_failing_cycle(tmp_path):
    class PostTimeout(PlacementClient):
        def post_order(self, signed_order, order_type):
            with sqlite3.connect(self.db_path) as connection:
                assert connection.execute(
                    "SELECT response_status FROM order_submissions"
                ).fetchall() == [("INTENT",)]
            raise PolyApiException(error_msg="Request exception!")

    db_path = tmp_path / "trades.db"
    wrapper = _placement_wrapper(db_path, PostTimeout(db_path))

    result = wrapper.place_limit_order("token", 0.4, 10, "BUY")

    assert result == {
        "success": False,
        "error": "주문 POST 결과가 불확실하여 동일 token/side를 격리했습니다",
        "submission_outcome_unknown": True,
        "quarantined": True,
    }

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT response_status, order_id FROM order_submissions"
        ).fetchone() == ("SUBMIT_OUTCOME_UNKNOWN", None)


def test_unresolved_intent_quarantines_only_same_token_side(tmp_path):
    class Client:
        def __init__(self):
            self.created = []

        def create_order(self, order_args):
            self.created.append(order_args.token_id)
            return {"signed": True}

        def post_order(self, *_args):
            return {"success": True, "orderID": "safe-order", "status": "live"}

        def cancel_orders(self, order_ids):
            return {"canceled": order_ids, "not_canceled": {}}

    db_path = tmp_path / "trades.db"
    client = Client()
    wrapper = _placement_wrapper(db_path, client)
    wrapper.execution_ledger.record_intent(
        token_id="uncertain", side="BUY", requested_price=0.4,
        requested_size=10, simulation=False,
    )

    wrapper.reconcile_order_ledger()
    blocked = wrapper.place_limit_order("uncertain", 0.4, 10, "BUY")
    allowed = wrapper.place_limit_order("other", 0.4, 10, "BUY")

    assert blocked["success"] is False
    assert "동일 token/side" in blocked["error"]
    assert allowed["orderID"] == "safe-order"
    assert client.created == ["other"]
