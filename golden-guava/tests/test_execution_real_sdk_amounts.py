"""Offline real-SDK matrix, separate from the fake-only execution suite.

Install: uv sync --frozen --extra dev --extra live
Run: uv run --no-sync pytest tests/test_execution_real_sdk_amounts.py
Without --extra live this module is explicitly skipped, not SDK coverage.
Sockets/DNS are blocked. Keys are ephemeral in-memory test keys; SQLite is
disposable. No venue acceptance, real fill, wallet, fee, or live gate is tested.
The stock bug fingerprints are version-specific to py-clob-client-v2 1.1.0.
Adapter and signing tests run against any installed version and fail on drift.
"""
import base64
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import importlib.metadata
import json
import secrets
import socket
import sqlite3
import time
from urllib.parse import urlsplit

import pytest

pytest.importorskip("py_clob_client_v2", reason="real-SDK matrix requires --extra dev --extra live")

from eth_account import Account
from eth_account.messages import encode_typed_data
from py_clob_client_v2.config import get_contract_config
from py_clob_client_v2.order_builder.builder import OrderBuilder, ROUNDING_CONFIG
from py_clob_client_v2.order_utils import ExchangeOrderBuilderV2
from polybot.budget import Budget
from polybot import execution
from polybot_observability import ExecutionLedger

D = Decimal
CONTEXT = {"event_id": "offline-event", "condition_id": "offline-condition", "decision_id": "offline-decision"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden in real-SDK tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@pytest.mark.parametrize("price,tick,taker", [
    ("0.29", "0.01", 17857100), ("0.58", "0.01", 8771900),
    ("0.97", "0.001", 5154630), ("0.97", "0.01", 5154600),
    ("0.999", "0.0001", 5005005), ("0.999", "0.001", 5005000),
    ("0.3", "0.1", 16666000),
])
def test_sdk_110_stock_amount_fingerprint(price, tick, taker):
    if importlib.metadata.version("py-clob-client-v2") != "1.1.0":
        pytest.skip("stock bug fingerprint specifically describes SDK 1.1.0")
    _, maker, actual = OrderBuilder(None).get_market_order_amounts("BUY", 5.0, float(price), ROUNDING_CONFIG[tick])
    assert maker == 5000000 and actual == taker


@pytest.fixture
def offline_broker(tmp_path, monkeypatch):
    brokers = []
    real_factory = execution._live_client

    def create(tick, ask, signature_type=0, neg_risk=False, notional_cap=5):
        account = Account.create()
        funder = account.address if signature_type == 0 else "0x" + "12" * 20
        credentials = execution.Credentials(account.key.hex(), funder, signature_type,
            secrets.token_hex(16), base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(), secrets.token_hex(16))
        settings = execution.BrokerSettings(max_buy_notional=D(notional_cap))
        client = real_factory(credentials, settings)
        path = tmp_path / f"offline-{len(brokers)}.db"
        ledger = ExecutionLedger(path, strategy_name="golden-guava")
        seen = {"gets": [], "posts": [], "signs": [], "signed": []}

        def get(endpoint, headers=None, data=None, params=None):
            assert urlsplit(endpoint).netloc == "clob.polymarket.com"
            route = urlsplit(endpoint).path
            seen["gets"].append(route)
            if route == "/book":
                return {"asset_id": "123", "market": "offline-condition", "timestamp": str(int(time.time() * 1000)),
                        "tick_size": tick, "neg_risk": neg_risk, "min_order_size": "1",
                        "asks": [{"price": ask, "size": "1000000"}],
                        "bids": [{"price": ask, "size": "1000000"}]}
            if route == "/clob-markets/offline-condition":
                return {"c": "offline-condition", "mts": tick, "nr": neg_risk,
                        "t": [{"t": "123"}], "fd": {"r": "0.03", "e": 1, "to": True}}
            if route == "/balance-allowance":
                return {"balance": "1000000000", "allowances": {}}
            if route == "/version":
                return {"version": 2}
            raise AssertionError("unexpected SDK route")

        def post(endpoint, headers=None, data=None, params=None):
            assert endpoint == "https://clob.polymarket.com/order"
            payload = json.loads(data)
            assert payload["orderType"] == "FOK"
            with sqlite3.connect(path) as con:
                assert con.execute("SELECT count(*) FROM order_submissions").fetchone()[0] == 1
                env = json.loads(con.execute("SELECT envelope_json FROM guava_execution_envelopes").fetchone()[0])
                assert env["maker_amount"] == payload["order"]["makerAmount"]
                assert env["taker_amount"] == payload["order"]["takerAmount"]
                assert con.execute("SELECT phase FROM guava_execution_events ORDER BY sequence DESC LIMIT 1").fetchone()[0] == "POST_STARTED"
            seen["posts"].append(payload)
            return {"success": True, "orderID": "offline-order", "status": "DELAYED", "tradeIDs": []}

        real_sign = client.create_market_order
        def sign(args, options=None):
            seen["signs"].append((args, options))
            result = real_sign(args, options=options)
            seen["signed"].append(result)
            return result

        real_limit_sign = client.create_order
        def sign_limit(args, options=None):
            seen["signs"].append((args, options))
            result = real_limit_sign(args, options=options)
            seen["signed"].append(result)
            return result

        monkeypatch.setattr(client, "_get", get)
        monkeypatch.setattr(client, "_post", post)
        monkeypatch.setattr(client, "create_market_order", sign)
        monkeypatch.setattr(client, "create_order", sign_limit)
        def no_auto_shrink(*args, **kwargs):
            raise AssertionError("principal must never be fee/balance auto-shrunk")
        monkeypatch.setattr(client, "_adjust_buy_amount_for_balance", no_auto_shrink)
        monkeypatch.setattr(execution, "_live_client", lambda *a, **kw: client)
        broker = execution.Broker(ledger, path, Budget(), credentials=credentials, settings=settings)
        brokers.append(broker)
        return broker, client, seen, path

    yield create
    for broker in brokers:
        broker.close()


@pytest.mark.parametrize("price,tick,expected", [
    ("0.29", "0.01", "17241300"), ("0.58", "0.01", "8620600"),
    ("0.97", "0.001", "5154600"), ("0.999", "0.0001", "5005000"),
    ("0.999", "0.001", "5005000"), ("0.965", "0.001", "5181300"),
    ("0.965", "0.005", "5181300"), ("0.9675", "0.0025", "5167900"),
    ("0.3", "0.1", "16666000"), ("0.7", "0.1", "7142000"),
])
def test_actual_client_signs_one_exact_principal_envelope(offline_broker, price, tick, expected):
    broker, client, seen, path = offline_broker(tick, price)
    result = broker.buy_fok("123", 5, price, CONTEXT)
    assert result.get("order_id") == "offline-order", result.get("error_type")
    env = result["envelope"]
    assert result["signed_maker_amount"] == "5000000"
    assert result["signed_taker_amount"] == expected
    assert D(env["limit_price"]) == D(price) <= D(env["requested_limit_price"])
    assert env["native_tick"] == env["signer_tick"] == tick
    assert env["buy_quantity_precision"] == (3 if tick == "0.1" else 4)
    assert int(expected) >= int(env["minimum_taker_amount"])
    assert int(expected) % 10 ** (6 - env["buy_quantity_precision"]) == 0
    assert D(result["signed_maker_amount"]) < (D(price) + D(tick)) * D(expected)
    assert len(seen["signs"]) == len(seen["posts"]) == 1
    assert getattr(seen["signs"][0][0], "user_usdc_balance", None) in (None, 0)
    assert result["fee_status"] == "UNKNOWN" and result["fee_usdc"] is None
    assert broker.execution_inventory()[0]["envelope"] == env
    assert broker.buy_fok("123", 5, price, CONTEXT)["duplicate"]
    assert len(seen["posts"]) == len(seen["signs"]) == 1


@pytest.mark.parametrize("signature_type", [0, 1, 2, 3])
@pytest.mark.parametrize("neg_risk", [False, True])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_actual_v2_signature_and_identity_compatibility(offline_broker, signature_type, neg_risk, side):
    broker, client, seen, path = offline_broker("0.01", "0.29", signature_type, neg_risk)
    result = (broker.buy_fok("123", 5, "0.29", CONTEXT) if side == "BUY"
              else broker.sell_fok("123", "5.02", "0.29", CONTEXT))
    assert result.get("order_id") == "offline-order"
    signed = seen["signed"][0]
    contracts = get_contract_config(137)
    exchange = ExchangeOrderBuilderV2(contracts.neg_risk_exchange_v2 if neg_risk else contracts.exchange_v2,
                                      137, client.signer)
    typed = exchange.build_order_typed_data(signed)
    assert signed.signature == exchange.build_order_signature(typed)
    if signature_type != 3:
        assert Account.recover_message(encode_typed_data(full_message=typed), signature=signed.signature) == client.signer.address()
    assert signed.maker.lower() == client.builder.funder.lower()
    assert int(signed.signatureType) == signature_type
    with sqlite3.connect(path) as con:
        dump = " ".join(con.iterdump())
    assert signed.signature not in dump and client.signer.private_key not in dump


@pytest.mark.parametrize("field,value", [("tokenId", "999"), ("side", 1), ("makerAmount", "4999999"),
    ("takerAmount", "NaN"), ("takerAmount", "17241301"), ("maker", "0x" + "34" * 20),
    ("signatureType", 3), ("signatureType", False), ("timestamp", None), ("signature", ""),
    ("signature", "garbled"), ("signer", "0x" + "56" * 20), ("builder", "0x" + "ff" * 32)])
def test_malformed_real_signed_result_never_falls_back(offline_broker, monkeypatch, field, value):
    broker, client, seen, path = offline_broker("0.01", "0.29")
    sign = client.create_market_order
    monkeypatch.setattr(client, "create_market_order", lambda *a, **kw: replace(sign(*a, **kw), **{field: value}))
    result = broker.buy_fok("123", 5, "0.29", CONTEXT)
    assert result["no_post"] and not seen["posts"] and len(seen["signs"]) == 1
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT count(*) FROM order_submissions").fetchone()[0] == 0


def test_stock_sdk_and_global_rounding_untouched(offline_broker):
    original = OrderBuilder.get_market_order_amounts
    original_limit = OrderBuilder.get_order_amounts
    config = deepcopy(ROUNDING_CONFIG)
    broker, client, seen, path = offline_broker("0.001", "0.965")
    assert broker.buy_fok("123", 5, "0.965", CONTEXT).get("order_id")
    assert OrderBuilder.get_market_order_amounts is original and ROUNDING_CONFIG == config
    assert OrderBuilder.get_order_amounts is original_limit
    assert type(client.builder) is not OrderBuilder


def test_native_point_one_refuses_finer_sdk_signer_option(offline_broker):
    from py_clob_client_v2.clob_types import MarketOrderArgs, PartialCreateOrderOptions
    broker, client, seen, path = offline_broker("0.1", "0.3")
    client.get_clob_market_info("offline-condition")
    with pytest.raises(Exception, match="invalid tick size"):
        client.create_market_order(MarketOrderArgs(token_id="123", amount=5, side="BUY", price=.3, order_type="FOK"),
                                   options=PartialCreateOrderOptions(tick_size="0.01", neg_risk=False))
    assert not seen["posts"]


@pytest.mark.parametrize("price,tick,amount", [("0.29", "0.01", "5.29"), ("0.58", "0.01", "100"),
                                            ("0.3", "0.1", "5.01")])
def test_cent_principal_never_float_floors_or_auto_shrinks(offline_broker, price, tick, amount):
    broker, client, seen, path = offline_broker(tick, price, notional_cap=100)
    result = broker.buy_fok("123", amount, price, CONTEXT)
    assert result.get("order_id")
    assert result["signed_maker_amount"] == str(int(D(amount) * 1000000))
    precision = result["envelope"]["buy_quantity_precision"]
    expected = (D(amount) / D(price)).quantize(D(1).scaleb(-precision), rounding=ROUND_FLOOR)
    assert D(result["signed_shares"]) == expected


def test_no_wider_price_when_original_limit_has_insufficient_full_depth(offline_broker):
    broker, client, seen, path = offline_broker("0.001", "0.965")
    assert broker.buy_fok("123", 5, "0.9649", CONTEXT)["no_post"]
    assert not seen["signs"] and not seen["posts"]


@pytest.mark.parametrize("tick", sorted(execution.TICKS))
@pytest.mark.parametrize("amount", ["5", "5.01", "99.99", "100"])
def test_all_native_grid_boundaries_obey_next_tick_and_quantum_proof(tick, amount):
    builder = execution._decimal_order_builder(None, None, None)
    t, q = D(tick), D(amount)
    precision = execution._buy_precision(t, t)
    quantum = D(1).scaleb(-precision)
    for price in {t, 2*t, (D("0.5")/t).to_integral_value(rounding=ROUND_FLOOR)*t, 1-2*t, 1-t}:
        _, maker, taker = builder.get_market_order_amounts("BUY", float(q), float(price), ROUNDING_CONFIG[tick])
        expected = (q/price).quantize(quantum, rounding=ROUND_FLOOR)
        assert maker == int(q*1000000) and maker % 10000 == 0
        assert D(taker)/1000000 == expected and taker % int(quantum*1000000) == 0
        assert 0 <= q/price - expected < quantum
        assert D(maker) < (price+t)*taker
        execution._check_buy_rounding_bound(q, price, t, maker, taker, precision)


def test_ratio_above_nominal_is_disclosed_not_claimed_exact_ceiling(offline_broker):
    broker, client, seen, path = offline_broker("0.01", "0.29")
    result = broker.buy_fok("123", 5, ".29", CONTEXT)
    ratio = D(result["signed_maker_amount"])/D(result["signed_taker_amount"])
    assert D("0.29") < ratio < D("0.30")


def test_real_sdk_exception_is_one_sign_attempt_no_fallback(offline_broker, monkeypatch):
    broker, client, seen, path = offline_broker("0.001", "0.965")
    calls = []
    def invalid_sign(*args, **kwargs):
        calls.append(1)
        raise ValueError("malformed signer; never retry with another tick")
    monkeypatch.setattr(client, "create_market_order", invalid_sign)
    assert broker.buy_fok("123", 5, ".965", CONTEXT)["no_post"]
    assert calls == [1] and not seen["posts"]


def test_sdk_110_stop_sell_float_floor_grid_reproduced():
    if importlib.metadata.version("py-clob-client-v2") != "1.1.0":
        pytest.skip("stock bug fingerprint specifically describes SDK 1.1.0")
    builder = OrderBuilder(None)
    assert builder.get_order_amounts("SELL", 5.02, .29, ROUNDING_CONFIG["0.01"])[1:] == (5010000, 1452900)
    assert builder.get_order_amounts("SELL", 5.06, .29, ROUNDING_CONFIG["0.01"])[1:] == (5050000, 1464500)
    wrong = sum(builder.get_order_amounts("SELL", float(D(cents)/100), float(price), ROUNDING_CONFIG["0.01"])[1] != cents*10000
                for cents in range(500, 1001) for price in (".29", ".58", ".73", ".97"))
    assert wrong == 140


def test_instance_decimal_stop_sell_corrects_all_2004_cases():
    builder = execution._decimal_order_builder(None, None, None)
    for cents in range(500, 1001):
        for text in (".29", ".58", ".73", ".97"):
            price, size = D(text), D(cents)/100
            _, maker, taker = builder.get_order_amounts("SELL", float(size), float(price), ROUNDING_CONFIG["0.01"])
            assert maker == cents*10000
            assert D(taker)/1000000 == size*price


@pytest.mark.parametrize("tick", sorted(execution.TICKS))
@pytest.mark.parametrize("size", ["5.02", "5.06", "5.029", "10.009"])
def test_real_stop_sell_all_grids_exact_quantity_and_dust(offline_broker, tick, size):
    price = str((D("0.29")/D(tick)).to_integral_value(rounding=ROUND_FLOOR)*D(tick))
    broker, client, seen, path = offline_broker(tick, price)
    result = broker.sell_fok("123", size, price, CONTEXT)
    assert result.get("order_id") == "offline-order", result.get("error_type")
    shares = D(size).quantize(D("0.01"), rounding=ROUND_FLOOR)
    assert D(result["signed_shares"]) == shares
    assert D(result["sell_residual_shares"]) == D(size)-shares
    assert result["signed_maker_amount"] == str(int(shares*1000000))
    proceeds = D(result["signed_taker_amount"])/1000000
    assert 0 <= proceeds-shares*D(price) < D("0.000001")
    assert D(result["envelope"]["limit_price"]) >= D(price)
    assert not (execution.BUY_AMOUNT_FIELDS & result["envelope"].keys())
    assert broker.execution_inventory()[0]["signed_shares"] == result["signed_shares"]
    assert len(seen["signs"]) == len(seen["posts"]) == 1


@pytest.mark.parametrize("tick", sorted(execution.TICKS))
def test_sell_boundary_amounts_and_prices_do_not_lose_micros(tick):
    builder = execution._decimal_order_builder(None, None, None)
    for price in (D(tick), 1-D(tick)):
        for size in (D("5.02"), D("5.06"), D("9.99"), D("100.009")):
            _, maker, taker = builder.get_order_amounts("SELL", float(size), float(price), ROUNDING_CONFIG[tick])
            shares = size.quantize(D("0.01"), rounding=ROUND_FLOOR)
            assert maker == int(shares*1000000)
            assert taker == int((shares*price*1000000).to_integral_value(rounding=ROUND_CEILING))
            assert 0 <= D(taker)/1000000-shares*price < D("0.000001")


@pytest.mark.parametrize("field,value", [("makerAmount", "5010000"), ("takerAmount", "1455799"),
                                         ("takerAmount", "1455801"), ("tokenId", "999")])
def test_bad_real_stop_sell_never_fallback_or_oversell(offline_broker, monkeypatch, field, value):
    broker, client, seen, path = offline_broker("0.01", "0.29")
    sign = client.create_order
    monkeypatch.setattr(client, "create_order", lambda *a, **kw: replace(sign(*a, **kw), **{field: value}))
    assert broker.sell_fok("123", "5.02", ".29", CONTEXT)["no_post"]
    assert len(seen["signs"]) == 1 and not seen["posts"]


@pytest.mark.parametrize("value", [{}, {"version": 1}, {"version": "2"}, {"version": True}, "transport_error"])
def test_version_failure_cannot_silently_default_to_v2_and_post(offline_broker, monkeypatch, value):
    broker, client, seen, path = offline_broker("0.01", "0.29")
    get = client._get
    def with_bad_version(endpoint, **kwargs):
        if endpoint.endswith("/version"):
            if value == "transport_error":
                raise execution.BrokerCallError("unavailable version")
            return value
        return get(endpoint, **kwargs)
    monkeypatch.setattr(client, "_get", with_bad_version)
    assert broker.buy_fok("123", 5, ".29", CONTEXT)["no_post"]
    assert not seen["signed"] and not seen["posts"]


@pytest.mark.parametrize("price,tick", [("0.3", "0.1"), ("0.29", "0.01"), ("0.965", "0.001")])
def test_full_real_sdk_inventory_accepts_maxwell_reducer_contract(offline_broker, price, tick):
    from polybot.position_state import Limits, reduce_positions
    broker, client, seen, path = offline_broker(tick, price)
    result = broker.buy_fok("123", 5, price, CONTEXT)
    before = (len(seen["gets"]), len(seen["posts"]), len(seen["signs"]))
    rows = broker.execution_inventory()
    limits = Limits(max_positions=20, max_notional_usdc=D(100), max_event_positions=1,
                    max_event_notional_usdc=D(5), max_cycle_positions=1,
                    max_cycle_notional_usdc=D(5), max_order_notional_usdc=D(5))
    state = reduce_positions(rows, sell_to_buy={}, expected_submission_ids={result["submission_id"]},
                             inventory_complete=True, limits=limits)
    assert state.notional_usdc == 5 and state.active_position_count == 1
    assert state.lots[0].owned_shares is None  # signed/DELAYED is NOT a fill
    assert before == (len(seen["gets"]), len(seen["posts"]), len(seen["signs"]))
