from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import requests

from polybot.api.clob_client import (
    ClobClientWrapper,
    _normalize_clob_resolution,
    _walk_buy_book,
    _walk_sell_book,
)
from polybot.api.gamma_client import GammaClient
from polybot.config import ApiConfig
from polybot.db.models import MarketCatalog, init_database
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
    result="Home FC",
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
        "question": f"Will {result} win?",
        "groupItemTitle": result,
        "sportsMarketType": "moneyline",
        "description": (
            "This market refers only to the outcome within the first 90 minutes "
            "of regular play plus stoppage time."
        ),
        "gameStartTime": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.98", "0.02"],
        "clobTokenIds": [f"yes-{condition_id}", f"no-{condition_id}"],
        "negRisk": True,
    }


def _event(event_id: str, markets, *, parent_event_id=None, sport_code="epl"):
    event = {
        "id": event_id,
        "slug": event_id,
        "title": "Home FC vs. Away FC",
        "active": True,
        "closed": False,
        "live": True,
        "ended": False,
        "startTime": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "parentEventId": parent_event_id,
        "seriesSlug": "premier-league-2025",
        "sport": {
            "id": 2,
            "sport": "epl",
            "name": "Premier League",
            "primaryTagId": 306,
            "series": "10188",
            "tags": "1,100639,100350,82,306",
        },
        "tags": [
            {"id": "1", "slug": "sports"},
            {"id": "100639", "slug": "games"},
            {"id": "100350", "slug": "soccer"},
            {"id": "82", "slug": "premier-league"},
            {"id": "306", "slug": "epl"},
        ],
        "series": [{"id": "10188", "slug": "premier-league-2025"}],
        "teams": [
            {"name": "Home FC", "league": "epl"},
            {"name": "Away FC", "league": "epl"},
        ],
        "markets": markets,
    }
    if sport_code == "sea":
        event["seriesSlug"] = "serie-a-2025"
        event["sport"] = {
            "id": 12,
            "sport": "sea",
            "name": "Serie A",
            "primaryTagId": 100618,
            "series": "10203",
            "tags": "1,100639,100350,100618,101962",
        }
        event["tags"] = [
            {"id": "1", "slug": "sports"},
            {"id": "100639", "slug": "games"},
            {"id": "100350", "slug": "soccer"},
            {"id": "101962", "slug": "sea"},
        ]
        event["series"] = [{"id": "10203", "slug": "serie-a-2025"}]
        event["teams"] = [
            {"name": "Home FC", "league": "sea"},
            {"name": "Away FC", "league": "sea"},
        ]
    elif sport_code != "epl":
        event["sport"] = {
            **event["sport"],
            "id": 999,
            "sport": sport_code,
            "name": "Unlisted League",
            "primaryTagId": 999,
            "series": "999",
        }
        event["seriesSlug"] = "unlisted-league"
        event["series"] = [{"id": "999", "slug": "unlisted-league"}]
        event["teams"] = [
            {"name": "Home FC", "league": sport_code},
            {"name": "Away FC", "league": sport_code},
        ]
    return event


def _uefa_event(code: str, markets):
    identities = {
        "ucl": ("100977", "10204", "ucl-2025", "UEFA Champions League"),
        "uel": ("101787", "10209", "uel-2025", "UEFA Europa League"),
    }
    tag_id, series_id, series_slug, name = identities[code]
    event = _event(f"{code}-home-away-2026-08-27", markets)
    event["seriesSlug"] = series_slug
    event["resolutionSource"] = "https://www.uefa.com/example/match/"
    event["sport"] = {}
    event["tags"] = [
        {"id": "1", "slug": "sports"},
        {"id": "100639", "slug": "games"},
        {"id": "100350", "slug": "soccer"},
        {"id": tag_id, "slug": code},
    ]
    event["series"] = [{"id": series_id, "slug": series_slug}]
    event["teams"] = [
        {"name": "Home FC", "league": "epl"},
        {"name": "Away FC", "league": "lal"},
    ]
    event["title"] = f"{name}: Home FC vs. Away FC"
    return event


class _Session:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, dict(params), timeout))
        return _Response(self.pages.pop(0))


def test_gamma_uses_soccer_live_keyset_and_terminal_cursor() -> None:
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

    markets = client.get_all_tradable_markets(0, 0)

    assert {market["conditionId"] for market in markets} == {"one", "low", "two"}
    assert session.calls[0][0].endswith("/events/keyset")
    params = session.calls[0][1]
    assert params["tag_id"] == 100350
    assert params["live"] == "true"
    assert params["related_tags"] == "false"
    assert "liquidity_min" not in params and "volume_min" not in params
    assert session.calls[1][1]["after_cursor"] == "next"
    proof = client.last_sweep_attestation
    assert proof["cursor_complete"] is True
    assert proof["pages"] == 2
    assert proof["raw_market_count"] == 4
    assert proof["qualified_market_count"] == 3
    assert proof["duplicate_raw_count"] == 1
    assert proof["exclusion_counts"] == {}


def test_gamma_accepts_only_whole_match_home_draw_away_yes_markets() -> None:
    home = _market("home", result="Home FC")
    draw = _market("draw", result="Draw (Home FC vs. Away FC)")
    away = _market("away", result="Away FC")
    prop = _market("prop", result="Both Teams to Score")
    client = GammaClient()
    client.session = _Session(
        [{"events": [_event("event", [home, draw, away, prop])]}]
    )

    markets = client.get_all_tradable_markets(0, 0)

    assert {market["conditionId"] for market in markets} == {"home", "draw", "away"}
    assert client.last_sweep_attestation["exclusion_counts"] == {
        "result_proposition_not_identified": 1
    }


def test_gamma_accepts_exact_serie_a_identity() -> None:
    client = GammaClient()
    client.session = _Session(
        [{"events": [_event("serie-a", [_market("serie-a")], sport_code="sea")]}]
    )

    markets = client.get_all_tradable_markets(0, 0)

    assert len(markets) == 1
    assert markets[0]["leagueCode"] == "sea"
    assert markets[0]["leagueName"] == "Serie A"


@pytest.mark.parametrize(
    ("code", "name"),
    [
        ("ucl", "UEFA Champions League"),
        ("uel", "UEFA Europa League"),
    ],
)
def test_gamma_accepts_exact_cross_league_uefa_identity(code, name) -> None:
    client = GammaClient()
    client.session = _Session(
        [{"events": [_uefa_event(code, [_market(f"{code}-market")])]}]
    )

    markets = client.get_all_tradable_markets(0, 0)

    assert len(markets) == 1
    assert markets[0]["leagueCode"] == code
    assert markets[0]["leagueName"] == name


def test_gamma_rejects_uefa_advancement_scope_before_trading() -> None:
    advancement = _market("ucl-advance")
    advancement["description"] = (
        "This market resolves based on which team advances, including extra time "
        "and penalty shoot-outs."
    )
    client = GammaClient()
    client.session = _Session(
        [{"events": [_uefa_event("ucl", [advancement])]}]
    )

    assert client.get_all_tradable_markets(0, 0) == []
    assert client.last_sweep_attestation["exclusion_counts"] == {
        "settlement_scope_unproven": 1
    }


def test_gamma_rejects_out_of_range_probability() -> None:
    malformed = _market("negative")
    malformed["outcomePrices"] = ["-0.01", "1.01"]
    client = GammaClient()
    client.session = _Session([{"events": [_event("event", [malformed])]}])

    assert client.get_all_tradable_markets(0, 0) == []
    assert client.last_sweep_attestation["exclusion_counts"] == {
        "invalid_outcome_price": 1
    }


def test_gamma_exclusion_bucket_preserves_rejected_sport_identity() -> None:
    client = GammaClient()
    client.session = _Session(
        [
            {
                "events": [
                    _event("event", [_market("other")], sport_code="eredivisie")
                ]
            }
        ]
    )

    assert client.get_all_tradable_markets(0, 0) == []
    assert client.last_sweep_attestation["exclusion_counts"] == {
        "league_not_allowed:sport=eredivisie:status=rejected": 1
    }


def test_gamma_rejects_nonadvancing_or_unbounded_cursor() -> None:
    client = GammaClient()
    client.MAX_SWEEP_PAGES = 1
    client.session = _Session(
        [{"events": [_event("event", [_market("one")])], "next_cursor": "more"}]
    )
    with pytest.raises(RuntimeError, match="page cap"):
        client.get_all_tradable_markets(0, 0)
    assert client.last_sweep_attestation is None


def test_gamma_rate_limit_fails_fast_without_in_process_retry(monkeypatch) -> None:
    class RateLimitedSession:
        def __init__(self):
            self.calls = []

        def get(self, url, params, timeout):
            self.calls.append((url, dict(params), timeout))
            response = requests.Response()
            response.status_code = 429
            response.url = url
            response.headers["Retry-After"] = "60"
            response._content = b'{"error":"rate limited"}'
            return response

    sleeps = []
    monkeypatch.setattr("polybot.utils.retry.time.sleep", sleeps.append)
    client = GammaClient()
    client.session = RateLimitedSession()

    with pytest.raises(requests.HTTPError):
        client.get_all_tradable_markets(0, 0)

    assert len(client.session.calls) == 1
    assert client.session.calls[0][2] == (2.0, 5.0)
    assert sleeps == []
    assert client.last_sweep_attestation is None


def test_order_reconciliation_reports_health_without_unsafe_intent_autoresolve(
    monkeypatch,
) -> None:
    class _Ledger:
        def pending_submissions(self):
            return []

        def unresolved_submission_count(self, *, side):
            return {"BUY": 2, "SELL": 1}[side]

        def reconciliation_gap_count(self, *, side):
            return {"BUY": 3, "SELL": 4}[side]

        def autoresolve_stale_sell_intents(self, **_kwargs):
            raise AssertionError("open-order absence must never resolve an intent")

    wrapper = object.__new__(ClobClientWrapper)
    wrapper.simulation_mode = False
    wrapper.execution_ledger = _Ledger()
    monkeypatch.setenv("POLYBOT_INTENT_AUTORESOLVE", "true")

    stats = wrapper.reconcile_order_ledger()

    assert stats["unresolved_buy_outcomes"] == 2
    assert stats["unresolved_sell_outcomes"] == 1
    assert stats["reconciliation_buy_gaps"] == 3
    assert stats["reconciliation_sell_gaps"] == 4
    assert stats["intent_autoresolved"] == 0


def _fee_evidence_wrapper(tmp_path, *, clob_rate="0.05"):
    db_path = tmp_path / "fee-evidence.db"
    Session = init_database(str(db_path))
    with Session() as session:
        session.add(
            MarketCatalog(
                condition_id="condition-fee",
                token_ids_json='["token-fee", "token-no"]',
                outcomes_json='["Yes", "No"]',
                outcome_prices_json='["0.98", "0.02"]',
                tags_json="[]",
                fees_enabled=1,
                fee_rate=0.05,
                fee_exponent=1,
                fee_taker_only=1,
            )
        )
        session.commit()

    class _Client:
        def get_clob_market_info(self, condition_id):
            assert condition_id == "condition-fee"
            return {
                "c": "condition-fee",
                "t": [
                    {"t": "token-fee", "o": "Yes"},
                    {"t": "token-no", "o": "No"},
                ],
                "fd": {"r": clob_rate, "e": 1, "to": True},
            }

    wrapper = ClobClientWrapper(
        ApiConfig("key", "funder"),
        simulation_mode=False,
        audit_db_path=db_path,
        strategy_name="golden-watermelon-live",
    )
    wrapper._client = _Client()
    wrapper._initialized = True
    return wrapper


def test_clob_v2_dynamic_taker_fee_is_persisted_from_exact_fill(tmp_path) -> None:
    wrapper = _fee_evidence_wrapper(tmp_path)
    submission_id = wrapper.execution_ledger.record_submission(
        token_id="token-fee",
        side="BUY",
        requested_price=0.98,
        requested_size=5.102,
        result={"success": True, "orderID": "order-fee", "status": "matched"},
        simulation=False,
    )
    pending = wrapper.execution_ledger.pending_submissions()[0]
    trade = {
        "id": "trade-fee",
        "status": "CONFIRMED",
        "taker_order_id": "order-fee",
        "trader_side": "TAKER",
        "side": "BUY",
        "size": "5102000",
        "price": "0.98",
        # CLOB V2 can retain this legacy placeholder even though the protocol
        # applies an operator-set fee at match time.
        "fee_rate_bps": 0,
        "maker_orders": [],
    }

    enriched = wrapper._attach_clob_v2_fee_evidence(
        trade,
        pending=pending,
        order_id="order-fee",
    )
    wrapper.execution_ledger.record_fill(
        submission_id,
        "order-fee",
        enriched,
    )

    assert enriched["fee_rate_bps"] is None
    assert enriched["fee_amount_usdc"] == "5000"
    with wrapper._open_evidence_db_read_only() as connection:
        row = connection.execute(
            "SELECT size, price, liquidity_role, fee_rate_bps, fee_amount_usdc "
            "FROM order_fills WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()
    assert row["size"] == pytest.approx(5.102)
    assert row["price"] == pytest.approx(0.98)
    assert row["liquidity_role"] == "TAKER"
    assert row["fee_rate_bps"] is None
    assert row["fee_amount_usdc"] == pytest.approx(0.005)


def test_clob_v2_fee_schedule_mismatch_fails_closed(tmp_path) -> None:
    wrapper = _fee_evidence_wrapper(tmp_path, clob_rate="0.04")

    with pytest.raises(
        ClobResponseContractError,
        match="Gamma and CLOB dynamic fee parameters do not match",
    ):
        wrapper._clob_v2_fee_schedule("token-fee")


def test_clob_v2_fee_formula_matches_documented_sports_example(tmp_path) -> None:
    wrapper = _fee_evidence_wrapper(tmp_path)
    schedule = wrapper._clob_v2_fee_schedule("token-fee")
    shares = Decimal("5.102")
    price = Decimal("0.98")

    fee = shares * schedule.rate * (price * (1 - price)) ** schedule.exponent

    assert fee.quantize(Decimal("0.00001")) == Decimal("0.00500")


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


def test_buy_walk_accepts_executable_ask_only_book() -> None:
    walk = _walk_buy_book(
        {
            "bids": [],
            "asks": [{"price": "0.98", "size": "20"}],
        },
        "token",
        5.0,
    )
    assert walk.best_bid is None
    assert walk.best_ask == 0.98
    assert walk.spread is None
    assert walk.vwap == pytest.approx(0.98)


def test_shallow_book_is_censored_not_imputed() -> None:
    book = {
        "bids": [{"price": "0.91", "size": "20"}],
        "asks": [{"price": "0.92", "size": "1"}],
    }
    with pytest.raises(ClobResponseUnavailableError, match=r"full \$5"):
        _walk_buy_book(book, "token", 5.0)


def test_full_share_sell_walk_uses_deeper_bids_and_market_limit() -> None:
    book = {
        "bids": [
            {"price": "0.70", "size": "2"},
            {"price": "0.60", "size": "4"},
        ],
        "asks": [{"price": "0.72", "size": "20"}],
    }
    walk = _walk_sell_book(book, "token", 5.0)
    assert walk.best_bid == 0.70
    assert walk.best_ask == 0.72
    assert walk.limit_price == 0.60
    assert walk.levels_used == 2
    assert walk.proceeds == pytest.approx(2 * 0.70 + 3 * 0.60)
    assert walk.vwap == pytest.approx((2 * 0.70 + 3 * 0.60) / 5)


def test_sell_walk_accepts_executable_bid_only_book() -> None:
    walk = _walk_sell_book(
        {
            "bids": [{"price": "0.69", "size": "20"}],
            "asks": [],
        },
        "token",
        5.0,
    )
    assert walk.best_bid == 0.69
    assert walk.best_ask is None
    assert walk.spread is None
    assert walk.vwap == pytest.approx(0.69)


def test_shallow_stop_book_is_censored_not_partially_sold() -> None:
    book = {
        "bids": [{"price": "0.70", "size": "2"}],
        "asks": [{"price": "0.72", "size": "20"}],
    }
    with pytest.raises(ClobResponseUnavailableError, match="full displayed bid"):
        _walk_sell_book(book, "token", 5.0)


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


def test_live_sell_ledger_uses_signed_two_decimal_share_quantity(tmp_path) -> None:
    wrapper = _fee_evidence_wrapper(tmp_path)
    captured = {}

    class _Client:
        def get_clob_market_info(self, condition_id):
            assert condition_id == "condition-fee"
            return {
                "c": "condition-fee",
                "t": [
                    {"t": "token-fee", "o": "Yes"},
                    {"t": "token-no", "o": "No"},
                ],
                "fd": {"r": "0.05", "e": 1, "to": True},
            }

        def get_tick_size(self, token_id):
            assert token_id == "token-fee"
            return "0.01"

        def create_order(self, order):
            captured["order"] = order
            return SimpleNamespace(makerAmount="5100000", takerAmount="3570000")

        def post_order(self, _signed, order_type):
            captured["order_type"] = order_type
            return {"success": True, "orderID": "sell-signed", "status": "live"}

        def cancel_orders(self, _order_ids):
            return {"canceled": []}

    wrapper._client = _Client()
    result = wrapper.place_limit_order(
        "token-fee", price=0.70, size=5.102, side="SELL", order_type="FOK"
    )

    assert result["requested_size"] == pytest.approx(5.10)
    assert float(captured["order"].size) == pytest.approx(5.102)
    assert "FOK" in str(captured["order_type"])
    with wrapper._open_evidence_db_read_only() as connection:
        row = connection.execute(
            "SELECT requested_size, making_amount, taking_amount "
            "FROM order_submissions WHERE order_id='sell-signed'"
        ).fetchone()
    assert row["requested_size"] == pytest.approx(5.10)
    assert row["making_amount"] == pytest.approx(5.10)
    assert row["taking_amount"] == pytest.approx(3.57)


def test_live_client_derives_existing_api_key_without_create_attempt(
    monkeypatch,
) -> None:
    calls = []
    creds = object()

    class _Client:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def derive_api_key(self):
            calls.append(("derive", None))
            return creds

        def create_api_key(self):
            raise AssertionError("live cycle must not create a replacement API key")

        def create_or_derive_api_key(self):
            raise AssertionError("create-first credential flow must not be used")

        def set_api_creds(self, value):
            calls.append(("set", value))

    monkeypatch.setattr("py_clob_client_v2.ClobClient", _Client)
    wrapper = ClobClientWrapper(
        ApiConfig("private-key", "funder", signature_type=3),
        simulation_mode=False,
    )

    wrapper._ensure_initialized()

    assert wrapper._initialized is True
    assert [name for name, _value in calls] == ["init", "derive", "set"]
    assert calls[-1][1] is creds


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
