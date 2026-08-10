"""Shared Gamma/CLOB evidence contracts exercised by Golden Kiwi.

These tests intentionally exercise the reusable transport and execution-ledger
surface in the Kiwi package rather than strategy decisions.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from polybot_observability import (
    ClobResponseContractError,
    ClobResponseUnavailableError,
)
from polybot.api.clob_client import ClobClientWrapper
from polybot.api.gamma_client import (
    GammaClient,
    GammaConditionMismatchError,
    GammaSweepBudgetExceeded,
)


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
                        "volume": "15000",
                        "outcomes": '["Yes", "No"]',
                    },
                    {
                        "conditionId": "closed",
                        "active": True,
                        "closed": True,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "50000",
                        "volume": "15000",
                    },
                    {
                        "conditionId": "server-filter-leak",
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "1",
                        "volume": "15000",
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
                        "volume": "15000",
                    },
                    {
                        "conditionId": "two",
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "30000",
                        "volume": "20000",
                    },
                    {
                        "conditionId": "missing-tradability",
                        "active": True,
                        "liquidity": "30000",
                        "volume": "20000",
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

    markets = client.get_all_tradable_markets(
        min_liquidity=20_000,
        min_volume=10_000,
        max_pages=53,
        max_markets=5_330,
        max_elapsed_seconds=120,
    )

    assert [market["conditionId"] for market in markets] == ["one", "two"]
    assert client.session.calls[0][0].endswith("/markets/keyset")
    assert client.session.calls[1][1]["after_cursor"] == "cursor-1"
    assert all(
        call[1]["liquidity_num_min"] == 20_000
        and call[1]["volume_num_min"] == 10_000
        for call in client.session.calls
    )
    assert all(call[2] == (3.05, 20.0) for call in client.session.calls)
    assert sleeps == [client.KEYSET_PAGE_INTERVAL_SECONDS]
    attestation = client.last_sweep_attestation
    assert attestation["schema_version"] == 2
    assert attestation["cursor_complete"] is True
    assert attestation["pages"] == 2
    assert attestation["raw_market_count"] == 6
    assert attestation["unique_condition_count"] == 5
    assert attestation["qualified_market_count"] == 2
    assert attestation["duplicate_raw_count"] == 1
    assert attestation["max_pages"] == 53
    assert attestation["max_markets"] == 5_330
    assert attestation["max_elapsed_seconds"] == 120
    assert 0 <= attestation["elapsed_seconds"] <= 120
    assert len(attestation["membership_digest_sha256"]) == 64
    memberships = {item["condition_id"]: item for item in attestation["memberships"]}
    assert memberships["one"]["raw_seen_count"] == 2
    assert memberships["one"]["qualified"] is True
    assert memberships["closed"]["qualification_reason"] == "closed_or_missing"
    assert memberships["server-filter-leak"]["qualification_reason"] == "below_min_liquidity"
    assert memberships["missing-tradability"]["qualified"] is False


def test_gamma_page_budget_fails_without_partial_attestation(monkeypatch):
    monkeypatch.setattr("polybot.api.gamma_client.time.sleep", lambda _value: None)
    client = GammaClient()
    client.session = KeysetSession()

    with pytest.raises(GammaSweepBudgetExceeded, match="page budget"):
        client.get_all_tradable_markets(max_pages=1)

    assert client.last_sweep_attestation is None


def test_gamma_market_budget_fails_without_partial_attestation():
    client = GammaClient()
    client.session = KeysetSession()

    with pytest.raises(GammaSweepBudgetExceeded, match="raw-market budget"):
        client.get_all_tradable_markets(max_markets=2)

    assert client.last_sweep_attestation is None


def test_gamma_elapsed_budget_fails_without_partial_attestation(monkeypatch):
    clocks = iter([0.0, 0.0, 121.0])
    monkeypatch.setattr("polybot.api.gamma_client.time.monotonic", lambda: next(clocks))
    client = GammaClient()
    client.session = KeysetSession()

    with pytest.raises(GammaSweepBudgetExceeded, match="elapsed-time budget"):
        client.get_all_tradable_markets(max_elapsed_seconds=120)

    assert client.last_sweep_attestation is None


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


def test_gamma_condition_lookup_distinguishes_genuine_absence():
    client = GammaClient()
    client.session = SimpleNamespace(
        get=lambda *_args, **_kwargs: Response([])
    )

    assert client.get_market_by_condition_id("requested") is None


def test_gamma_condition_lookup_accepts_only_exact_condition_id():
    client = GammaClient()
    client.session = SimpleNamespace(
        get=lambda *_args, **_kwargs: Response(
            [
                {
                    "conditionId": "requested",
                    "outcomes": '["Yes", "No"]',
                }
            ]
        )
    )

    market = client.get_market_by_condition_id("requested")

    assert market["conditionId"] == "requested"
    assert market["outcomes"] == ["Yes", "No"]
    assert market["_gammaObservedAt"]


def test_gamma_condition_lookup_rejects_nonempty_mismatched_response():
    client = GammaClient()
    client.session = SimpleNamespace(
        get=lambda *_args, **_kwargs: Response(
            [{"conditionId": "different"}]
        )
    )

    with pytest.raises(GammaConditionMismatchError, match="no exact match"):
        client.get_market_by_condition_id("requested")


def test_gamma_condition_lookup_retries_then_reraises_request_error(
    monkeypatch,
):
    sleeps = []
    monkeypatch.setattr("polybot.utils.retry.time.sleep", sleeps.append)
    client = GammaClient()
    client.session = TimeoutSession()

    with pytest.raises(requests.exceptions.Timeout):
        client.get_market_by_condition_id("requested")

    assert len(client.session.calls) == 3
    assert sleeps == [2.0, 4.0]


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


@pytest.mark.parametrize(
    "budgets",
    [
        {"max_pages": 0},
        {"max_pages": True},
        {"max_markets": 0},
        {"max_markets": True},
        {"max_elapsed_seconds": 0},
        {"max_elapsed_seconds": float("nan")},
    ],
)
def test_gamma_rejects_invalid_budgets_before_network(budgets):
    client = GammaClient()
    client.session = SimpleNamespace(
        get=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network must not be called")
        )
    )
    with pytest.raises(ValueError, match="max_|positive integer"):
        client.get_all_tradable_markets(**budgets)


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


def test_authenticated_cancel_path_is_hard_blocked_after_mode_mutation():
    wrapper = ClobClientWrapper(SimpleNamespace())
    wrapper._client = CancelClient(
        {"canceled": [], "not_canceled": {"cancel-me": "already matched"}},
        detail={"id": "cancel-me", "status": "MATCHED", "size_matched": "1"},
    )
    wrapper._initialized = True
    wrapper.simulation_mode = False
    with pytest.raises(RuntimeError, match="research/simulation-only"):
        wrapper.cancel_order("cancel-me")


def test_live_wrapper_construction_is_hard_blocked_before_client_access():
    with pytest.raises(RuntimeError, match="research/simulation-only"):
        ClobClientWrapper(SimpleNamespace(), simulation_mode=False)


def test_simulated_limit_order_needs_no_initialized_or_authenticated_client():
    wrapper = ClobClientWrapper(SimpleNamespace(), simulation_mode=True)
    result = wrapper.place_limit_order("token", 0.4137, 10, "BUY")
    assert result["success"] is True
    assert result["simulated"] is True
    assert result["price"] == pytest.approx(0.4137)
    assert wrapper._initialized is False


def test_simulation_initializes_public_l0_client_without_wallet_or_api_credentials(
    monkeypatch,
):
    calls = []

    class PublicClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def create_or_derive_api_creds(self):
            pytest.fail("simulation must never derive authenticated credentials")

        def set_api_creds(self, _credentials):
            pytest.fail("simulation must never install authenticated credentials")

    monkeypatch.setattr("py_clob_client_v2.ClobClient", PublicClient)
    wrapper = ClobClientWrapper(
        SimpleNamespace(
            chain_id=137,
            private_key="",
            funder_address="",
            signature_type=1,
        ),
        simulation_mode=True,
    )

    wrapper._ensure_initialized()

    assert calls == [{"host": wrapper.HOST, "chain_id": 137}]
    assert wrapper._initialized is True
