from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from polybot.api.clob_client import (
    ClobClientWrapper,
    _normalize_clob_resolution,
    _walk_buy_book,
)
from polybot.api.gamma_client import GammaClient
from polybot.config import ApiConfig
from polybot_observability import (
    ClobResponseContractError,
    ClobResponseUnavailableError,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _market(
    condition_id: str,
    *,
    liquidity=20_000,
    volume=8_000,
    outcomes=("Team A", "Team B"),
    neg_risk=False,
):
    return {
        "id": f"market-{condition_id}",
        "conditionId": condition_id,
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "liquidity": str(liquidity),
        "liquidityNum": liquidity,
        "volume": str(volume),
        "volumeNum": volume,
        "volume24hr": "1000",
        "endDate": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
        "outcomes": list(outcomes),
        "outcomePrices": ["0.94", "0.06"],
        "clobTokenIds": [f"yes-{condition_id}", f"no-{condition_id}"],
        "negRisk": neg_risk,
    }


def _event(event_id: str, markets):
    return {
        "id": event_id,
        "slug": event_id,
        "title": event_id,
        "tags": [{"slug": "sports", "label": "Sports"}],
        "markets": markets,
    }


class _Session:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, dict(params), timeout))
        return _Response(self.pages.pop(0))


def test_gamma_uses_server_filters_and_terminal_keyset_cursor() -> None:
    first = _market("one")
    duplicate = _market("one")
    low = _market("low", liquidity=1)
    second = _market("two")
    session = _Session(
        [
            {"events": [_event("event-1", [first, low])], "next_cursor": "next"},
            {"events": [_event("event-2", [duplicate, second])]},
        ]
    )
    client = GammaClient()
    client.session = session

    markets = client.get_all_tradable_markets(
        min_liquidity=10_000, min_volume=5_000
    )

    assert {market["conditionId"] for market in markets} == {"one", "two"}
    assert session.calls[0][0].endswith("/events/keyset")
    params = session.calls[0][1]
    assert params["tag_slug"] == "sports"
    assert params["liquidity_min"] == 10_000
    assert params["volume_min"] == 5_000
    assert "end_date_min" in params and "end_date_max" in params
    assert session.calls[1][1]["after_cursor"] == "next"
    proof = client.last_sweep_attestation
    assert proof["cursor_complete"] is True
    assert proof["pages"] == 2
    assert proof["raw_market_count"] == 4
    assert proof["qualified_market_count"] == 2
    assert proof["duplicate_raw_count"] == 1
    assert proof["exclusion_counts"] == {"below_min_liquidity": 1}


def test_gamma_accepts_named_moneyline_and_yes_no_negrisk_strata() -> None:
    named = _market("named")
    proposition = _market(
        "prop", outcomes=("Yes", "No"), neg_risk=True
    )
    client = GammaClient()
    client.session = _Session(
        [{"events": [_event("event", [named, proposition])]}]
    )

    markets = client.get_all_tradable_markets(10_000, 5_000)

    assert {market["conditionId"] for market in markets} == {"named", "prop"}


def test_gamma_rejects_out_of_range_probability() -> None:
    malformed = _market("negative")
    malformed["outcomePrices"] = ["-0.01", "1.01"]
    client = GammaClient()
    client.session = _Session([{"events": [_event("event", [malformed])]}])

    assert client.get_all_tradable_markets(10_000, 5_000) == []
    assert client.last_sweep_attestation["exclusion_counts"] == {
        "not_aligned_two_outcome": 1
    }


def test_gamma_rejects_nonadvancing_or_unbounded_cursor() -> None:
    client = GammaClient()
    client.MAX_SWEEP_PAGES = 1
    client.session = _Session(
        [{"events": [_event("event", [_market("one")])], "next_cursor": "more"}]
    )
    with pytest.raises(RuntimeError, match="page cap"):
        client.get_all_tradable_markets(10_000, 5_000)
    assert client.last_sweep_attestation is None


def test_exact_five_dollar_walk_uses_all_required_ask_levels() -> None:
    book = {
        "asset_id": "token",
        "bids": [{"price": "0.91", "size": "20"}],
        "asks": [
            {"price": "0.92", "size": "2"},
            {"price": "0.93", "size": "10"},
        ],
    }
    walk = _walk_buy_book(book, "token", 5.0)
    assert walk.cost == 5.0
    assert walk.levels_used == 2
    assert walk.limit_price == 0.93
    assert walk.vwap == pytest.approx(5 / (2 + (5 - 1.84) / 0.93))
    assert walk.best_bid == 0.91
    assert walk.best_ask == 0.92


def test_shallow_book_is_censored_not_imputed() -> None:
    book = {
        "bids": [{"price": "0.91", "size": "20"}],
        "asks": [{"price": "0.92", "size": "1"}],
    }
    with pytest.raises(ClobResponseUnavailableError, match=r"full \$5"):
        _walk_buy_book(book, "token", 5.0)


def test_clob_resolution_requires_closed_unique_one_hot_winner() -> None:
    proof = _normalize_clob_resolution(
        "condition",
        {
            "condition_id": "condition",
            "closed": True,
            "tokens": [
                {
                    "outcome": "Team A",
                    "price": 0,
                    "token_id": "token-a",
                    "winner": False,
                },
                {
                    "outcome": "Team B",
                    "price": 1,
                    "token_id": "token-b",
                    "winner": True,
                },
            ],
        },
        observed_at="2026-08-21T11:00:00Z",
    )

    assert proof.status == "RESOLVED"
    assert proof.winner_index == 1
    assert proof.tokens[1].token_id == "token-b"
    assert proof.evidence_sha256 == _normalize_clob_resolution(
        "condition",
        {
            "closed": True,
            "tokens": [
                {
                    "winner": False,
                    "token_id": "token-a",
                    "price": 0,
                    "outcome": "Team A",
                },
                {
                    "winner": True,
                    "token_id": "token-b",
                    "price": 1,
                    "outcome": "Team B",
                },
            ],
        },
    ).evidence_sha256


def test_clob_resolution_rejects_winner_payout_mismatch() -> None:
    with pytest.raises(ClobResponseContractError):
        _normalize_clob_resolution(
            "condition",
            {
                "closed": True,
                "tokens": [
                    {
                        "outcome": "A",
                        "price": 1,
                        "token_id": "a",
                        "winner": True,
                    },
                    {
                        "outcome": "B",
                        "price": 1,
                        "token_id": "b",
                        "winner": False,
                    },
                ],
            },
        )


def test_clob_resolution_multiple_winners_remains_unresolved() -> None:
    proof = _normalize_clob_resolution(
        "condition",
        {
            "closed": True,
            "tokens": [
                {"outcome": "A", "price": 0, "token_id": "a", "winner": True},
                {"outcome": "B", "price": 1, "token_id": "b", "winner": True},
            ],
        },
    )
    assert proof.status == "CLOSED_UNRESOLVED"
    assert proof.winner_index is None


def test_live_fok_uses_venue_tick_and_fok_order_type() -> None:
    captured = {}

    class _Client:
        def get_tick_size(self, token_id):
            assert token_id == "token"
            return "0.001"

        def create_order(self, order):
            captured["order"] = order
            return "signed"

        def post_order(self, signed, order_type):
            captured["signed"] = signed
            captured["order_type"] = order_type
            return {"success": True, "orderID": "order-1"}

    wrapper = ClobClientWrapper(ApiConfig("key", "funder"), simulation_mode=False)
    wrapper._client = _Client()
    wrapper._initialized = True
    result = wrapper.place_limit_order(
        "token", price=0.945, size=5.3, side="BUY", order_type="FOK"
    )

    assert result["orderID"] == "order-1"
    assert float(captured["order"].price) == 0.945
    assert "FOK" in str(captured["order_type"])


def test_live_exact_usdc_fok_buy_uses_two_decimal_maker_envelope() -> None:
    captured = {}

    class _Client:
        def get_tick_size(self, token_id):
            assert token_id == "token"
            return "0.01"

        def create_market_order(self, order):
            captured["order"] = order
            return SimpleNamespace(makerAmount="5000000", takerAmount="5319100")

        def post_order(self, signed, order_type):
            captured["signed"] = signed
            captured["order_type"] = order_type
            return {"success": True, "orderID": "order-2"}

    wrapper = ClobClientWrapper(ApiConfig("key", "funder"), simulation_mode=False)
    wrapper._client = _Client()
    wrapper._initialized = True

    result = wrapper.place_fok_buy("token", amount_usdc=5, limit_price=0.94)

    assert result["orderID"] == "order-2"
    assert result["maker_amount_usdc"] == 5
    assert result["requested_size"] == 5.3191
    assert captured["order"].amount == 5
    assert captured["order"].price == 0.94
    assert "FOK" in str(captured["order_type"])


def test_live_exact_usdc_fok_buy_coarsens_cent_aligned_signing_grid() -> None:
    captured = {}

    class _Client:
        def get_tick_size(self, _token_id):
            return "0.001"

        def create_market_order(self, order, options=None):
            captured["order"] = order
            captured["options"] = options
            return SimpleNamespace(makerAmount="5000000", takerAmount="5376300")

        def post_order(self, _signed, _order_type):
            captured["posted"] = True
            return {"success": True, "orderID": "order-fine-cent"}

    wrapper = ClobClientWrapper(ApiConfig("key", "funder"), simulation_mode=False)
    wrapper._client = _Client()
    wrapper._initialized = True

    result = wrapper.place_fok_buy("token", amount_usdc=5, limit_price=0.93)

    assert result["orderID"] == "order-fine-cent"
    assert result["requested_size"] == 5.3763
    assert captured["order"].price == 0.93
    assert captured["options"].tick_size == "0.01"
    assert captured["posted"] is True


def test_live_exact_usdc_fok_buy_allows_valid_non_cent_taker_precision() -> None:
    captured = {}

    class _Client:
        def get_tick_size(self, _token_id):
            return "0.005"

        def create_market_order(self, order):
            captured["order"] = order
            return SimpleNamespace(makerAmount="5000000", takerAmount="5291000")

        def post_order(self, _signed, _order_type):
            captured["posted"] = True
            return {"success": True, "orderID": "order-fine-non-cent"}

    wrapper = ClobClientWrapper(ApiConfig("key", "funder"), simulation_mode=False)
    wrapper._client = _Client()
    wrapper._initialized = True

    result = wrapper.place_fok_buy("token", amount_usdc=5, limit_price=0.945)

    assert result["orderID"] == "order-fine-non-cent"
    assert result["requested_size"] == 5.291
    assert captured["order"].price == 0.945
    assert captured["posted"] is True


def test_live_exact_usdc_fok_buy_rejects_excess_taker_precision_before_post() -> None:
    class _Client:
        def get_tick_size(self, _token_id):
            return "0.005"

        def create_market_order(self, _order):
            return SimpleNamespace(makerAmount="5000000", takerAmount="5347590")

        def post_order(self, _signed, _order_type):
            raise AssertionError("invalid taker precision must never be posted")

    wrapper = ClobClientWrapper(ApiConfig("key", "funder"), simulation_mode=False)
    wrapper._client = _Client()
    wrapper._initialized = True

    result = wrapper.place_fok_buy("token", amount_usdc=5, limit_price=0.935)

    assert result["success"] is False
    assert "four decimal places" in result["error"]


def test_live_exact_usdc_fok_buy_rejects_signed_amount_drift() -> None:
    class _Client:
        def get_tick_size(self, _token_id):
            return "0.01"

        def create_market_order(self, _order):
            return SimpleNamespace(makerAmount="4991400", takerAmount="5310000")

        def post_order(self, _signed, _order_type):
            raise AssertionError("invalid signed amount must never be posted")

    wrapper = ClobClientWrapper(ApiConfig("key", "funder"), simulation_mode=False)
    wrapper._client = _Client()
    wrapper._initialized = True

    result = wrapper.place_fok_buy("token", amount_usdc=5, limit_price=0.94)

    assert result["success"] is False
    assert "exact maker USDC" in result["error"]


def test_stale_delayed_fok_uses_terminal_absence_ledger_proof() -> None:
    captured = {}

    class _Client:
        def cancel_orders(self, order_ids):
            assert order_ids == ["order-stale"]
            return {
                "canceled": [],
                "not_canceled": {
                    "order-stale": "Order not found or already canceled"
                },
            }

        def get_order(self, order_id):
            assert order_id == "order-stale"
            return []

        def get_trades(self, params, *, only_first_page):
            assert str(params.asset_id) == "token-stale"
            assert only_first_page is False
            return []

    class _Ledger:
        def pending_submissions(self):
            return [
                {
                    "order_id": "order-stale",
                    "token_id": "token-stale",
                }
            ]

        def record_delayed_fok_zero_fill(self, **evidence):
            captured.update(evidence)
            return "DELAYED_FOK_TERMINAL_ABSENCE_ZERO_FILL"

    wrapper = ClobClientWrapper(ApiConfig("key", "funder"), simulation_mode=False)
    wrapper._client = _Client()
    wrapper._initialized = True
    wrapper.execution_ledger = _Ledger()

    result = wrapper.cancel_order_for_reconciliation(
        "order-stale", minimum_age_minutes=30
    )

    assert result["verified_order_status"] == "CANCELED"
    assert result["verified_size_matched"] == 0
    assert captured["order_id"] == "order-stale"
    assert captured["token_id"] == "token-stale"
    assert captured["authenticated_trades"] == []
    assert captured["minimum_age_minutes"] == 30
