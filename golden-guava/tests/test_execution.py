"""All execution tests use fake SDK + disposable SQLite. No venue traffic."""
import builtins
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from decimal import Decimal, ROUND_FLOOR
import importlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from polybot.budget import Budget, BudgetExceeded
from polybot.execution import Broker, BrokerSettings, BrokerContractError, BrokerEvidenceError, Credentials
from polybot_observability import ExecutionLedger


CONTEXT = {"event_id": "event-1", "condition_id": "condition-1", "decision_id": "decision-1"}


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden in execution tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


class FakeSDK:
    retry_on_error = False
    builder_config = None

    def __init__(self):
        self.posts = []
        self.signs = []
        self.cancels = []
        self.orders = {}
        self.trades = []
        self.callback = None
        self.sign_callback = None
        self.auth_failure = False
        self.book_empty = False
        self.book_stale = False
        self.tick = "0.01"
        self.signed_override = {}
        self.fee_gap = False
        self.status = "MATCHED"
        self.balance = "1000000000"
        self.cursor = "LTE="
        self.trade_calls = 0
        self.token_conditions = {}

    def assert_level_2_auth(self):
        if self.auth_failure:
            raise ValueError("private key NEVER_PRINT secret")

    def get_order_book(self, token):
        return {"asset_id": token, "market": self.token_conditions.get(token, "condition-1"), "timestamp": str(int((time.time() - (60 if self.book_stale else 0)) * 1000)),
                "tick_size": self.tick, "neg_risk": False, "min_order_size": "1",
                "asks": [] if self.book_empty else [{"price": "0.50", "size": "1000"}],
                "bids": [{"price": "0.50", "size": "1000"}]}

    def get_market(self, condition):
        return {"condition_id": condition, "closed": False, "tokens": []}

    def get_clob_market_info(self, condition):
        return {"c": condition, "mts": self.tick, "nr": False, "t": [{"t": t} for t in ("token-1", "token-2", "token-3") if self.token_conditions.get(t, "condition-1") == condition],
                "fd": None if self.fee_gap else {"r": "0.03", "e": 1, "to": True}}

    def get_balance_allowance(self, params):
        return {"balance": self.balance, "allowances": {}}

    def create_market_order(self, args, options=None):
        self.signs.append(args)
        if self.sign_callback:
            self.sign_callback()
        amount, price = Decimal(str(args.amount)), Decimal(str(args.price))
        precision = Decimal("0.001") if options.tick_size == "0.1" else Decimal("0.0001")
        taker = (amount / price).quantize(precision, rounding=ROUND_FLOOR)
        values = dict(tokenId=args.token_id, side=0, makerAmount=str(int(amount * 1000000)),
                      takerAmount=str(int(taker * 1000000)), timestamp="1234567", signature="NEVER_PERSIST_SIGNATURE")
        values.update(self.signed_override)
        return SimpleNamespace(**values)

    def create_order(self, args, options=None):
        self.signs.append(args)
        qty, price = Decimal(str(args.size)), Decimal(str(args.price))
        values = dict(tokenId=args.token_id, side=1, makerAmount=str(int(qty * 1000000)),
                      takerAmount=str(int(qty * price * 1000000)), timestamp="1234567", signature="NEVER_PERSIST_SIGNATURE")
        values.update(self.signed_override)
        return SimpleNamespace(**values)

    def post_order(self, signed, order_type):
        assert order_type == "FOK"
        self.posts.append(signed)
        if self.callback:
            self.callback(signed)
        oid = f"order-{len(self.posts)}"
        size = Decimal(signed.takerAmount if signed.side == 0 else signed.makerAmount) / 1000000
        self.orders[oid] = {"id": oid, "asset_id": signed.tokenId, "market": self.token_conditions.get(signed.tokenId, "condition-1"),
                            "side": "BUY" if signed.side == 0 else "SELL", "status": self.status,
                            "original_size": str(size), "size_matched": str(size), "associate_trades": [f"trade-{oid}"]}
        return {"success": True, "orderID": oid, "status": self.status, "tradeIDs": [],
                "secret": "NEVER_PERSIST_RESPONSE", "makingAmount": "999999999"}

    def get_order(self, oid):
        return self.orders.get(oid)

    def get_trades_paginated(self, params, next_cursor=None):
        self.trade_calls += 1
        return {"trades": self.trades, "next_cursor": self.cursor}

    def get_open_orders(self):
        return [r for r in self.orders.values() if r and r["status"] == "LIVE"]

    def cancel_orders(self, ids):
        self.cancels.append(ids)
        for oid in ids:
            if self.orders.get(oid):
                self.orders[oid]["status"] = "CANCELED"
        return {"canceled": ids, "not_canceled": {}}


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "trades.db"
    ledger = ExecutionLedger(path, strategy_name="golden-guava")
    sdk = FakeSDK()
    broker = Broker(ledger, path, Budget(), sdk_client=sdk)
    yield broker, ledger, sdk, path
    broker.close()


def buy(broker, *, token="token-1", decision="decision-1", amount=5, price="0.5"):
    return broker.buy_fok(token, amount, price, dict(CONTEXT, decision_id=decision))


def confirmed(sdk, order="order-1", *, size="10", fee="150000", status="CONFIRMED"):
    detail = sdk.orders[order]
    row = {"id": f"trade-{order}", "bucket_index": 0, "taker_order_id": order,
           "trader_side": "TAKER", "status": status, "size": size, "price": "0.5",
           "asset_id": detail["asset_id"], "market": detail["market"], "side": detail["side"],
           "fee_rate_bps": "0", "transaction_hash": "0xconfirmedtx"}
    if fee is not None:
        row["fee_amount_usdc"] = fee
    sdk.trades = [row]
    return row


def reconcile(broker, result):
    sid = result["submission_id"]
    return broker.reconcile([sid])[sid]


def test_no_ledger_no_sdk_import_or_creation(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("no live initialization without ledger")
    monkeypatch.setattr("polybot.execution._live_client", forbidden)
    with pytest.raises(BrokerContractError):
        Broker(None, tmp_path / "no.db", Budget())
    assert not (tmp_path / "no.db").exists()


def test_simulation_import_does_not_load_sdk_or_signer():
    root = Path(__file__).resolve().parents[1]
    script = """
import builtins, sys
original = builtins.__import__
def guard(name, *args, **kwargs):
    if name.startswith(('py_clob', 'eth_account', 'eth_keys')):
        raise AssertionError('SDK/signing imported by research')
    return original(name, *args, **kwargs)
builtins.__import__ = guard
import polybot.execution
assert not any(k.startswith('py_clob') for k in sys.modules)
"""
    result = subprocess.run([sys.executable, "-B", "-c", script], env=dict(os.environ, PYTHONPATH=str(root / "src")), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_credentials_repr_and_environment_are_secret_safe():
    creds = Credentials("a" * 64, "0x" + "b" * 40, 3, "apikey", "apisecret", "passphrase")
    assert str(creds) == "Credentials([REDACTED])"
    for env in ({}, {"POLYMARKET_PRIVATE_KEY": "VERY_SECRET"}):
        with pytest.raises(BrokerContractError) as error:
            Credentials.from_environment(env)
        assert "VERY_SECRET" not in str(error.value)


def test_wrong_strategy_and_db_are_rejected(tmp_path):
    ledger = ExecutionLedger(tmp_path / "a.db", strategy_name="golden-plum")
    with pytest.raises(BrokerContractError):
        Broker(ledger, ledger.db_path, Budget(), sdk_client=FakeSDK())
    ledger = ExecutionLedger(tmp_path / "b.db", strategy_name="golden-guava")
    with pytest.raises(BrokerContractError):
        Broker(ledger, tmp_path / "c.db", Budget(), sdk_client=FakeSDK())


@pytest.mark.parametrize("attribute", ["auth_failure", "book_empty", "book_stale", "fee_gap"])
def test_prepost_failures_are_event_local(setup, attribute, capsys):
    broker, ledger, sdk, path = setup
    setattr(sdk, attribute, True)
    result = buy(broker)
    assert result["no_post"] and result["status"] == "NO_POST"
    assert not sdk.posts
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM order_submissions").fetchone()[0] == 0
    setattr(sdk, attribute, False)
    assert buy(broker, token="token-2", decision="other")["order_id"] == "order-1"
    assert "NEVER_PRINT" not in repr(result) + repr(capsys.readouterr())


@pytest.mark.parametrize("override", [{"makerAmount": "5000001"}, {"takerAmount": "9999999"},
                                      {"makerAmount": "nan"}, {"takerAmount": "-1"},
                                      {"tokenId": "wrong"}, {"side": 1}, {"side": True},
                                      {"timestamp": None}, {"builder": "nonzero"}])
def test_signed_precision_identity_and_cap(setup, override):
    broker, ledger, sdk, path = setup
    sdk.signed_override = override
    assert buy(broker)["no_post"]
    assert not sdk.posts
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM order_submissions").fetchone()[0] == 0
    sdk.signed_override = {}
    assert buy(broker, decision="next")["order_id"]


def test_envelope_committed_inside_fake_post_and_immutable(setup):
    broker, ledger, sdk, path = setup
    def callback(signed):
        with sqlite3.connect(path) as con:
            row = con.execute("SELECT e.envelope_json,s.response_status,e.envelope_sha256 FROM guava_execution_envelopes e JOIN order_submissions s USING(submission_id)").fetchone()
            env = json.loads(row[0])
            assert env["maker_amount"] == signed.makerAmount == "5000000"
            assert env["taker_amount"] == signed.takerAmount
            assert env["decision_id"] == "decision-1" and row[1] == "INTENT"
            assert len(row[2]) == 64
            assert con.execute("SELECT phase FROM guava_execution_events ORDER BY sequence DESC LIMIT 1").fetchone()[0] == "POST_STARTED"
    sdk.callback = callback
    result = buy(broker)
    assert result["order_id"] == "order-1"
    assert result["confirmed_shares"] is None and result["fee_usdc"] is None
    with sqlite3.connect(path) as con:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE guava_execution_envelopes SET envelope_json='{}'")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM guava_execution_events")
        dump = " ".join(con.iterdump())
        assert "NEVER_PERSIST" not in dump
        assert con.execute("SELECT making_amount,taking_amount FROM order_submissions").fetchone() == (5.0, 10.0)


@pytest.mark.parametrize("point", ["intent", "envelope", "boundary"])
def test_no_post_if_db_failure(setup, monkeypatch, point):
    broker, ledger, sdk, path = setup
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("fake DB failure")
    if point == "intent":
        monkeypatch.setattr(ledger, "record_intent", fail)
    elif point == "envelope":
        monkeypatch.setattr(broker, "_envelope", fail)
    else:
        original = broker._event
        def event(sid, phase, evidence):
            if phase == "POST_STARTED":
                fail()
            return original(sid, phase, evidence)
        monkeypatch.setattr(broker, "_event", event)
    with pytest.raises(BrokerEvidenceError, match="no POST"):
        buy(broker)
    assert not sdk.posts


def test_ambiguous_post_never_double_submits_after_restart(setup):
    broker, ledger, sdk, path = setup
    def timeout(signed):
        raise TimeoutError("NEVER_PRINT_SECRET")
    sdk.callback = timeout
    first = buy(broker)
    assert first["submission_outcome_unknown"] and first["exposure_unknown"]
    assert first["confirmed_shares"] is None and not first["zero_fill_proven"]
    again = buy(broker)
    assert again["duplicate"] and len(sdk.posts) == 1
    broker2 = Broker(ledger, path, Budget(), sdk_client=sdk)
    assert buy(broker2)["duplicate"]
    assert buy(broker2, token="token-2", decision="new")["no_post"]
    assert len(sdk.posts) == 1
    broker2.close()


def test_result_db_failure_keeps_exact_id_without_retry(setup, monkeypatch):
    broker, ledger, sdk, path = setup
    monkeypatch.setattr(ledger, "record_submission_result", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError()))
    with pytest.raises(BrokerEvidenceError, match="do not resubmit"):
        buy(broker)
    assert len(sdk.posts) == 1
    with sqlite3.connect(path) as con:
        returned = con.execute("SELECT evidence_json FROM guava_execution_events WHERE phase='POST_RETURNED'").fetchone()
        assert json.loads(returned[0])["orderID"] == "order-1"
    assert buy(broker)["duplicate"] and len(sdk.posts) == 1


def test_confirmed_fills_idempotent_and_fee_actual(setup):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    confirmed(sdk)
    first = reconcile(broker, result)
    second = reconcile(broker, result)
    assert first["execution_complete"] and second["new_risk_allowed"]
    assert Decimal(first["confirmed_shares"]) == 10
    assert Decimal(first["confirmed_notional_usdc"]) == 5
    assert Decimal(first["fee_usdc"]) == Decimal("0.15")
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM order_fills").fetchone()[0] == 1
        assert con.execute("SELECT fee_rate_bps FROM order_fills").fetchone()[0] is None
    assert buy(broker, token="token-2", decision="second")["order_id"] == "order-2"


def test_matched_and_mined_are_not_confirmed(setup):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    confirmed(sdk, status="MINED")
    pending = reconcile(broker, result)
    assert pending["confirmed_shares"] is None
    assert pending["exposure_unknown"] and not pending["execution_complete"]
    sdk.trades[0]["status"] = "CONFIRMED"
    assert reconcile(broker, result)["execution_complete"]


def test_partial_plus_cancel_preserves_actual_and_remainder(setup):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    sdk.orders["order-1"].update(size_matched="4", status="LIVE")
    confirmed(sdk, size="4", fee="60000")
    partial = reconcile(broker, result)
    assert Decimal(partial["confirmed_shares"]) == 4
    assert partial["exposure_unknown"] and not partial["execution_complete"]
    canceled = broker.cancel_exact("order-1")
    assert canceled["execution_complete"] and canceled["partial_terminal"]
    assert Decimal(canceled["remaining_order_shares"]) == 6
    assert Decimal(canceled["confirmed_notional_usdc"]) == 2
    assert not canceled["zero_fill_proven"]
    assert sdk.cancels == [["order-1"]]


def test_missing_fees_stay_unknown_despite_fresh_schedule_and_zero_bps(setup):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    confirmed(sdk, fee=None)
    summary = reconcile(broker, result)
    assert summary["execution_complete"] and summary["fee_status"] == "UNKNOWN"
    assert summary["fee_usdc"] is None and not summary["new_risk_allowed"]
    assert Decimal(summary["confirmed_shares"]) == 10
    assert buy(broker, token="token-2", decision="next")["no_post"]
    sdk.trades[0]["fee_amount_usdc"] = "0"  # explicit same-fill amount, not bps
    summary = reconcile(broker, result)
    assert summary["fee_status"] == "KNOWN" and Decimal(summary["fee_usdc"]) == 0


def test_missing_catalog_never_zero_or_quarantine_without_exposure(setup):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    sdk.orders.clear()
    summary = reconcile(broker, result)
    assert not summary["execution_complete"] and summary["exposure_unknown"]
    assert summary["confirmed_shares"] is None and not summary["zero_fill_proven"]
    canceled = broker.cancel_exact("order-1")
    assert canceled["exposure_unknown"] and not canceled["zero_fill_proven"]


def test_canceled_zero_detail_alone_is_insufficient(setup):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    sdk.orders["order-1"].update(status="CANCELED", size_matched="0", associate_trades=[])
    summary = reconcile(broker, result)
    assert not summary["zero_fill_proven"] and summary["confirmed_shares"] is None


def test_exact_order_correlation_rejects_unrelated_taker(setup):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    confirmed(sdk)["taker_order_id"] = "other-order"
    assert not reconcile(broker, result)["execution_complete"]
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM order_fills").fetchone()[0] == 0


def test_catalog_cursor_must_finish(setup):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    confirmed(sdk)
    sdk.cursor = "MA=="
    assert not reconcile(broker, result)["execution_complete"]
    assert sdk.trade_calls == 1


def test_safe_sell_rounding_retains_dust(setup):
    broker, ledger, sdk, path = setup
    result = broker.sell_fok("token-1", "10.009", "0.499", CONTEXT)
    assert result["order_id"]
    assert Decimal(result["signed_shares"]) == 10
    assert Decimal(result["sell_residual_shares"]) == Decimal("0.009")
    assert result["envelope"]["limit_price"] == "0.50"
    assert sdk.posts[0].makerAmount == "10000000"


def test_buy_limit_never_widened(setup):
    broker, ledger, sdk, path = setup
    result = buy(broker, price="0.509")
    assert result["envelope"]["limit_price"] == "0.50"
    assert buy(broker, decision="x", price="0.001")["no_post"]


@pytest.mark.parametrize("value", [True, False, "NaN", "Infinity", "-Infinity", "0", "-5", "5.001", "100"])
def test_finite_cent_precision_and_default_cap(setup, value):
    broker, ledger, sdk, path = setup
    assert buy(broker, amount=value)["no_post"]
    assert not sdk.posts


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "0", "1", "-1"])
def test_price_domain(setup, value):
    assert buy(setup[0], price=value)["no_post"]
    assert not setup[2].posts


def test_explicit_future_100_cap(setup):
    broker, ledger, sdk, path = setup
    broker2 = Broker(ledger, path, Budget(), sdk_client=sdk, settings=BrokerSettings(max_buy_notional=Decimal("100")))
    result = buy(broker2, amount=100)
    assert result["signed_maker_amount"] == "100000000"
    broker2.close()


def test_budget_exhaustion_before_intent_and_post(setup):
    broker, ledger, sdk, path = setup
    now = [0.0]
    broker.budget = Budget(45, margin=7, monotonic=lambda: now[0])
    sdk.sign_callback = lambda: now.__setitem__(0, 38)
    result = buy(broker)
    assert result["no_post"] and not sdk.posts
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM order_submissions").fetchone()[0] == 0


def test_blocking_post_bounded_and_late_completion_never_retried(setup):
    broker, ledger, sdk, path = setup
    entered, release = threading.Event(), threading.Event()
    broker.settings = replace(broker.settings, attempt_seconds=0.2, socket_seconds=0.1)
    def blocked(signed):
        entered.set()
        assert release.wait(2)
    sdk.callback = blocked
    start = time.monotonic()
    result = buy(broker)
    elapsed = time.monotonic() - start
    assert entered.is_set() and elapsed < 0.7
    assert result["exposure_unknown"] and result["submission_outcome_unknown"]
    assert buy(broker)["duplicate"] and len(sdk.posts) == 1
    release.set()
    broker._worker.join(1)
    assert buy(broker)["duplicate"] and len(sdk.posts) == 1


def test_blocking_signer_cannot_later_post(setup):
    broker, ledger, sdk, path = setup
    release = threading.Event()
    broker.settings = replace(broker.settings, attempt_seconds=0.15, socket_seconds=0.1)
    sdk.sign_callback = lambda: release.wait(2)
    result = buy(broker)
    assert result["no_post"]
    release.set()
    broker._worker.join(1)
    assert not sdk.posts


def test_retrying_sdk_rejected_before_intent(setup):
    broker, ledger, sdk, path = setup
    sdk.retry_on_error = True
    assert buy(broker)["no_post"] and not sdk.posts


def test_no_backfill_legacy_submission_and_no_foreign_cancel(setup):
    broker, ledger, sdk, path = setup
    sid = ledger.record_intent(token_id="token-3", side="BUY", requested_price=.5, requested_size=10, simulation=False)
    with pytest.raises(BrokerContractError, match="no backfill"):
        broker.reconcile([sid])
    with pytest.raises(BrokerContractError):
        broker.cancel_exact("manual-order")
    assert not sdk.cancels and not sdk.posts


def test_context_required_and_secret_extra_rejected(setup):
    broker, ledger, sdk, path = setup
    for context in ({}, None, {"decision_id": "a"}, dict(CONTEXT, private_key="NEVER_PRINT")):
        assert broker.buy_fok("token-1", 5, .5, context)["no_post"]
    assert not sdk.posts


def test_public_reads_and_close_do_not_submit(setup):
    broker, ledger, sdk, path = setup
    assert set(broker.fresh_book(["token-1", "token-2"])) == {"token-1", "token-2"}
    assert broker.market_state("condition-1")["closed"] is False
    assert broker.collateral_balance() == 1000
    assert broker.token_balance("token-1") == 1000
    broker.close()
    broker.close()
    assert not sdk.posts and not sdk.cancels
    with pytest.raises(BrokerContractError):
        broker.collateral_balance()


def test_explicit_cancel_detail_catalog_trades_prove_zero(setup):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    sdk.orders["order-1"].update(status="LIVE", size_matched="0", associate_trades=[])
    summary = broker.cancel_exact("order-1")
    assert summary["zero_fill_proven"] and summary["execution_complete"]
    assert summary["confirmed_shares"] == "0" and summary["fee_usdc"] == "0"
    assert summary["new_risk_allowed"] and not summary["exposure_unknown"]
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM order_fills").fetchone()[0] == 0
    assert buy(broker, decision="new", token="token-2")["order_id"]


def test_delayed_fok_zero_requires_ttl_and_all_catalog_proof(setup):
    broker, ledger, sdk, path = setup
    sdk.status = "DELAYED"
    result = buy(broker)
    sdk.orders.clear()
    with pytest.raises(Exception):
        broker.cancel_exact("order-1")  # not old enough
    assert broker._snapshot(result["submission_id"])["exposure_unknown"]
    with sqlite3.connect(path) as con:
        con.execute("UPDATE order_submissions SET submitted_at=?", ((datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),))
    summary = broker.cancel_exact("order-1")
    assert summary["zero_fill_proven"] and summary["execution_complete"]
    assert summary["confirmed_shares"] == "0"


def test_cancel_ack_with_trade_or_open_catalog_never_zero(setup, monkeypatch):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    confirmed(sdk)
    sdk.orders["order-1"].update(status="CANCELED", size_matched="0", associate_trades=[])
    with pytest.raises(BrokerContractError, match="forbids zero"):
        broker.cancel_exact("order-1")
    assert not broker._snapshot(result["submission_id"])["zero_fill_proven"]
    sdk.trades = []
    monkeypatch.setattr(sdk, "get_open_orders", lambda: [{"id": "order-1"}])
    with pytest.raises(BrokerContractError, match="still present"):
        broker.cancel_exact("order-1")


def test_recover_original_response_without_signing_or_backfill(setup, monkeypatch):
    broker, ledger, sdk, path = setup
    original = ledger.record_submission_result
    monkeypatch.setattr(ledger, "record_submission_result", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError()))
    with pytest.raises(BrokerEvidenceError):
        buy(broker)
    result = buy(broker)
    assert result["submission_outcome_unknown"]
    monkeypatch.setattr(ledger, "record_submission_result", original)
    confirmed(sdk)
    summary = reconcile(broker, result)
    assert summary["execution_complete"] and summary["order_id"] == "order-1"
    assert len(sdk.signs) == len(sdk.posts) == 1


def test_conflicting_success_false_keeps_exact_order_id(setup, monkeypatch):
    broker, ledger, sdk, path = setup
    original = sdk.post_order
    def anomaly(*args):
        response = original(*args)
        response["success"] = False
        return response
    monkeypatch.setattr(sdk, "post_order", anomaly)
    result = buy(broker)
    assert result["order_id"] == "order-1"
    assert result["submission_outcome_unknown"] and result["exposure_unknown"]
    assert buy(broker)["duplicate"]
    confirmed(sdk)
    assert reconcile(broker, result)["execution_complete"]
    assert len(sdk.posts) == 1


def test_transaction_and_confirmed_evidence_must_not_regress(setup):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    trade = confirmed(sdk)
    trade.pop("transaction_hash")
    assert not reconcile(broker, result)["execution_complete"]
    trade["transaction_hash"] = "0xconfirmedtx"
    assert reconcile(broker, result)["execution_complete"]
    trade["size"] = "8"
    summary = reconcile(broker, result)
    assert not summary["execution_complete"] and not summary["new_risk_allowed"]
    assert Decimal(summary["confirmed_shares"]) == 10


def test_whole_attempt_includes_blocking_ledger_write(setup, monkeypatch):
    broker, ledger, sdk, path = setup
    entered, release = threading.Event(), threading.Event()
    original = ledger.record_intent
    def blocked(**kwargs):
        entered.set()
        assert release.wait(2)
        return original(**kwargs)
    monkeypatch.setattr(ledger, "record_intent", blocked)
    broker.settings = replace(broker.settings, attempt_seconds=0.15, socket_seconds=0.1)
    start = time.monotonic()
    with pytest.raises(BrokerEvidenceError, match="no POST"):
        buy(broker)
    assert time.monotonic() - start < 0.6 and entered.is_set()
    release.set()
    broker._worker.join(1)
    assert not sdk.posts
    assert buy(broker, token="token-2", decision="different")["no_post"]


def test_changed_request_cannot_reuse_decision_key(setup):
    broker, ledger, sdk, path = setup
    assert buy(broker)["order_id"]
    assert buy(broker, price="0.6")["status"] == "NO_POST"
    assert len(sdk.posts) == 1


@pytest.mark.parametrize("mutate", [lambda book: book.update(min_order_size="100"),
                                   lambda book: book.update(bids=[{"price": "0.60", "size": "10"}]),
                                   lambda book: book.update(neg_risk=True),
                                   lambda book: book.update(asks=[{"price": "0.501", "size": "100"}])])
def test_minimum_size_crossed_book_and_tick_contract(setup, monkeypatch, mutate):
    broker, ledger, sdk, path = setup
    original = sdk.get_order_book
    def changed(token):
        book = original(token)
        mutate(book)
        return book
    monkeypatch.setattr(sdk, "get_order_book", changed)
    assert buy(broker)["no_post"] and not sdk.posts


def buy_event_b(broker, sdk):
    sdk.token_conditions["token-2"] = "condition-2"
    return broker.buy_fok("token-2", 5, "0.5", dict(event_id="event-2", condition_id="condition-2", decision_id="decision-2"))


def test_unknown_post_blocks_event_or_token_but_not_other_event_next_cycle(setup):
    broker, ledger, sdk, path = setup
    sdk.callback = lambda signed: (_ for _ in ()).throw(TimeoutError())
    first = buy(broker)
    sdk.callback = None
    broker.close()
    next_cycle = Broker(ledger, path, Budget(), sdk_client=sdk)
    try:
        assert buy(next_cycle)["duplicate"]
        assert buy(next_cycle, token="token-3", decision="same-event")["no_post"]
        assert next_cycle.buy_fok("token-1", 5, .5, dict(CONTEXT, event_id="different", decision_id="same-token"))["no_post"]
        second = buy_event_b(next_cycle, sdk)
        assert second["order_id"] == "order-2"
        reservations = next_cycle.signed_reservations()
        assert {r["submission_id"] for r in reservations} == {first["submission_id"], second["submission_id"]}
        assert sum(Decimal(r["buy_notional_cap_usdc"]) for r in reservations if r["reservation_required"]) == 10
        assert all(r["runner_must_account"] for r in reservations)
        assert first["reservation"]["buy_notional_cap_usdc"] == "5"
        assert len(sdk.posts) == 2
    finally:
        next_cycle.close()


def test_fee_gap_blocks_same_event_but_not_other_event_and_keeps_reservation(setup):
    broker, ledger, sdk, path = setup
    first = buy(broker)
    confirmed(sdk, fee=None)
    assert reconcile(broker, first)["fee_status"] == "UNKNOWN"
    assert buy(broker, token="token-3", decision="same-event")["no_post"]
    assert buy_event_b(broker, sdk)["order_id"] == "order-2"
    reservation = next(r for r in broker.signed_reservations() if r["submission_id"] == first["submission_id"])
    assert reservation["reservation_required"] and reservation["event_or_token_blocked"]
    assert reservation["execution_complete"] and reservation["fee_status"] == "UNKNOWN"
    assert reservation["buy_notional_cap_usdc"] == "5"


def test_orphan_still_blocks_all_events_and_reservation_export(setup):
    broker, ledger, sdk, path = setup
    ledger.record_intent(token_id="token-3", side="BUY", requested_price=.5, requested_size=10, simulation=False)
    assert buy(broker)["no_post"]
    assert buy_event_b(broker, sdk)["no_post"]
    with pytest.raises(BrokerContractError, match="orphan|ownership"):
        broker.signed_reservations()
    assert not sdk.posts


def test_durable_no_post_resolves_only_its_intent_and_never_replays_decision(setup, monkeypatch):
    broker, ledger, sdk, path = setup
    original = broker._event
    def boundary_failure(sid, phase, evidence):
        if phase == "POST_STARTED":
            raise sqlite3.OperationalError("boundary was never committed")
        return original(sid, phase, evidence)
    monkeypatch.setattr(broker, "_event", boundary_failure)
    with pytest.raises(BrokerEvidenceError):
        buy(broker)
    monkeypatch.setattr(broker, "_event", original)
    assert not sdk.posts
    assert buy(broker, decision="fresh-decision")["order_id"] == "order-1"
    with sqlite3.connect(path) as con:
        first = con.execute("SELECT s.submission_id,s.outcome_resolution FROM order_submissions s JOIN guava_execution_envelopes e USING(submission_id) WHERE e.decision_id='decision-1'").fetchone()
        assert first[1] == "NO_ORDER_CREATED"
    assert buy(broker)["duplicate"] and len(sdk.posts) == 1
    reservation = next(r for r in broker.signed_reservations() if r["submission_id"] == first[0])
    assert reservation["no_post_proven"] and not reservation["reservation_required"]


@pytest.mark.parametrize("changes", [{"maker_amount": "NaN"}, {"maker_amount": "0"},
                                      {"maker_amount": "5000001"}, {"taker_amount": "0"},
                                      {"event_id": None}, {"condition_id": ""},
                                      {"strategy_name": "other"}, {"side": "INVALID"}])
def test_invalid_or_unowned_envelope_stays_global_fail_closed(setup, changes):
    broker, ledger, sdk, path = setup
    original = buy(broker)["envelope"]
    sid = ledger.record_intent(token_id="token-3", side="BUY", requested_price=.5, requested_size=10, simulation=False)
    envelope = dict(original, decision_id="corrupt-envelope", token_id="token-3")
    envelope.update(changes)
    broker._envelope(sid, envelope)  # Deliberately malformed legacy DB fixture.
    assert buy_event_b(broker, sdk)["no_post"]
    with pytest.raises(BrokerContractError, match="reservation"):
        broker.signed_reservations()
    assert len(sdk.posts) == 1


def test_no_post_without_positive_hash_linked_proof_stays_reserved(setup):
    broker, ledger, sdk, path = setup
    original = buy(broker)["envelope"]
    sid = ledger.record_intent(token_id="token-3", side="BUY", requested_price=.5, requested_size=10, simulation=False)
    broker._envelope(sid, dict(original, decision_id="old-intent", token_id="token-3", event_id="event-3"))
    broker._event(sid, "NO_POST", {"reason": "PRE_POST_WRITE_OR_BUDGET_FAILURE"})
    assert buy_event_b(broker, sdk)["order_id"] == "order-2"
    reservation = next(r for r in broker.signed_reservations() if r["submission_id"] == sid)
    assert not reservation["no_post_proven"] and reservation["reservation_required"]
    assert reservation["event_or_token_blocked"]
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT outcome_resolution FROM order_submissions WHERE submission_id=?", (sid,)).fetchone()[0] is None


def test_post_boundary_conflict_cannot_be_resolved_as_no_post(setup, monkeypatch):
    broker, ledger, sdk, path = setup
    original = broker._event
    def uncertain_commit(sid, phase, evidence):
        original(sid, phase, evidence)
        if phase == "POST_STARTED":
            raise sqlite3.OperationalError("failure observed after boundary commit")
    monkeypatch.setattr(broker, "_event", uncertain_commit)
    with pytest.raises(BrokerEvidenceError):
        buy(broker)
    monkeypatch.setattr(broker, "_event", original)
    assert buy(broker, decision="retry")["no_post"]
    assert buy_event_b(broker, sdk)["order_id"] == "order-1"
    first = next(r for r in broker.signed_reservations() if r["decision_id"] == "decision-1")
    assert first["reservation_required"] and not first["no_post_proven"]
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT outcome_resolution FROM order_submissions WHERE submission_id=?", (first["submission_id"],)).fetchone()[0] is None


def test_inflight_worker_is_temporary_not_permanent_cross_event_block(setup):
    broker, ledger, sdk, path = setup
    release = threading.Event()
    broker.settings = replace(broker.settings, attempt_seconds=0.2, socket_seconds=0.1)
    sdk.callback = lambda signed: release.wait(2)
    first = buy(broker)
    assert first["submission_outcome_unknown"]
    assert buy_event_b(broker, sdk)["no_post"]  # same SDK still in flight
    release.set()
    broker._worker.join(1)
    sdk.callback = None
    broker.budget = Budget()  # subsequent cycle after outstanding call returned
    assert buy_event_b(broker, sdk)["order_id"] == "order-2"
    assert buy(broker)["duplicate"] and len(sdk.posts) == 2


def test_reservation_inventory_retains_original_cap_and_never_credits_unknown_sell(setup):
    broker, ledger, sdk, path = setup
    broker.settings = replace(broker.settings, max_buy_notional=Decimal("100"))
    first = buy(broker, amount=100)
    broker.settings = replace(broker.settings, max_buy_notional=Decimal("5"))
    sdk.callback = lambda signed: (_ for _ in ()).throw(TimeoutError())
    sell = broker.sell_fok("token-3", "10.009", .5, dict(CONTEXT, event_id="event-3", decision_id="sell-3"))
    assert sell["submission_outcome_unknown"]
    sdk.callback = None
    assert buy_event_b(broker, sdk)["order_id"] == "order-3"
    inventory = {r["submission_id"]: r for r in broker.signed_reservations()}
    assert inventory[first["submission_id"]]["buy_notional_cap_usdc"] == "100"
    assert inventory[sell["submission_id"]]["buy_notional_cap_usdc"] is None
    assert Decimal(inventory[sell["submission_id"]]["sell_shares_cap"]) == 10
    assert all(r["reservation_required"] and not r["portfolio_approval"] for r in inventory.values())


def test_execution_inventory_full_snapshot_read_only_no_auth_or_sdk(setup, monkeypatch):
    import hashlib
    broker, ledger, sdk, path = setup
    first = buy(broker)
    confirmed(sdk, fee=None)
    partial = reconcile(broker, first)
    second = buy_event_b(broker, sdk)
    expected = {s["submission_id"]: broker._snapshot(s["submission_id"]) for s in (partial, second)}
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    def forbidden(*args, **kwargs):
        raise AssertionError("inventory must not invoke SDK/auth or write events")
    monkeypatch.setattr(broker, "_invoke", forbidden)
    monkeypatch.setattr(broker, "_event", forbidden)
    monkeypatch.setattr(broker, "_authenticate", forbidden)
    rows = broker.execution_inventory()
    assert {r["submission_id"]: r for r in rows} == expected
    assert sum(Decimal(r["reservation"]["buy_notional_cap_usdc"]) for r in rows) == 10
    assert any(r["fills"] and r["fee_status"] == "UNKNOWN" for r in rows)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_execution_inventory_empty_is_read_only_and_no_sdk(setup, monkeypatch):
    broker, ledger, sdk, path = setup
    monkeypatch.setattr(broker, "_authenticate", lambda *a: pytest.fail("no auth"))
    assert broker.execution_inventory() == []
    assert not sdk.posts and not sdk.signs


def test_execution_inventory_orphan_invalid_and_budget_never_partial(setup, monkeypatch):
    broker, ledger, sdk, path = setup
    buy(broker)
    buy_event_b(broker, sdk)
    check = broker._check
    seen = []
    def limited(deadline, **kwargs):
        seen.append(1)
        if len(seen) == 2:
            raise BudgetExceeded("complete inventory unavailable")
        return check(deadline, **kwargs)
    monkeypatch.setattr(broker, "_check", limited)
    with pytest.raises(BudgetExceeded):
        broker.execution_inventory()
    monkeypatch.setattr(broker, "_check", check)
    ledger.record_intent(token_id="token-3", side="BUY", requested_price=.5, requested_size=10, simulation=False)
    with pytest.raises(BrokerContractError, match="orphan"):
        broker.execution_inventory()
    assert len(sdk.posts) == 2


def test_execution_inventory_db_failure_not_success_shaped_empty(setup, monkeypatch):
    broker, ledger, sdk, path = setup
    def broken():
        raise sqlite3.OperationalError("fixture")
    monkeypatch.setattr(broker, "_connect", broken)
    with pytest.raises(BrokerEvidenceError):
        broker.execution_inventory()


def test_native_point_one_metadata_declares_three_dp(setup, monkeypatch):
    broker, ledger, sdk, path = setup
    sdk.tick = "0.1"
    original = sdk.get_order_book
    def at_price(token):
        book = original(token)
        book.update(asks=[{"price": "0.3", "size": "1000"}], bids=[])
        return book
    monkeypatch.setattr(sdk, "get_order_book", at_price)
    result = buy(broker, price="0.3")
    assert result.get("order_id")
    env = result["envelope"]
    assert env["native_tick"] == env["signer_tick"] == "0.1"
    assert env["buy_quantity_precision"] == 3 and env["signed_shares"] == "16.666"
    assert broker.execution_inventory()[0]["envelope"] == env


@pytest.mark.parametrize("changes", [
    {"buy_amount_contract": "unknown"}, {"native_tick": "0.1"}, {"signer_tick": "0.001"},
    {"signer_tick": "0.03"}, {"buy_quantity_precision": 3}, {"buy_quantity_precision": True},
    {"buy_quantity_precision": "4"}, {"minimum_taker_amount": "9999900"},
])
def test_new_amount_contract_invalid_metadata_is_not_legacy_fallback(setup, changes):
    broker, ledger, sdk, path = setup
    original = buy(broker)["envelope"]
    sid = ledger.record_intent(token_id="token-3", side="BUY", requested_price=.5, requested_size=10, simulation=False)
    env = dict(original, decision_id="bad-contract", token_id="token-3", **changes)
    broker._envelope(sid, env)
    with pytest.raises(BrokerContractError):
        broker.execution_inventory()


def test_partial_new_metadata_cannot_downgrade_but_legacy_still_reads(setup):
    from polybot.execution import BUY_AMOUNT_FIELDS
    broker, ledger, sdk, path = setup
    original = buy(broker)["envelope"]
    legacy = {k: v for k, v in original.items() if k not in BUY_AMOUNT_FIELDS}
    sid = ledger.record_intent(token_id="token-2", side="BUY", requested_price=.5, requested_size=10, simulation=False)
    broker._envelope(sid, dict(legacy, decision_id="legacy", token_id="token-2"))
    assert len(broker.execution_inventory()) == 2
    sid = ledger.record_intent(token_id="token-3", side="BUY", requested_price=.5, requested_size=10, simulation=False)
    broker._envelope(sid, dict(legacy, decision_id="partial", token_id="token-3", native_tick="0.01"))
    with pytest.raises(BrokerContractError):
        broker.execution_inventory()


def test_next_tick_and_quantity_relaxation_bound_rejects_worse_price():
    from polybot.execution import _check_buy_rounding_bound, _buy_precision
    with pytest.raises(BrokerContractError):
        _check_buy_rounding_bound(Decimal(5), Decimal("0.29"), Decimal("0.01"), 5000000, 16666600, 4)
    with pytest.raises(BrokerContractError):
        _check_buy_rounding_bound(Decimal(5), Decimal("0.5"), Decimal("0.01"), 5000000, 9999900, 4)
    with pytest.raises(BrokerContractError):
        _buy_precision("0.1", "0.01")


@pytest.mark.parametrize("collision", ["submission_id", "decision_side"])
@pytest.mark.parametrize("verb", ["INSERT OR REPLACE", "REPLACE"])
def test_envelope_insert_replace_cannot_delete_history_recursive_off(setup, collision, verb):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    other = ledger.record_intent(token_id="token-2", side="BUY", requested_price=.5, requested_size=10, simulation=False)
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA recursive_triggers=OFF")
        before = con.execute("SELECT * FROM guava_execution_envelopes").fetchall()
        values = list(before[0])
        if collision == "decision_side":
            values[0] = other
        else:
            values[1] = "replacement-decision"
        values[3] = "{}"
        values[5] += 100
        with pytest.raises(sqlite3.IntegrityError, match="immutable execution envelope"):
            con.execute(f"{verb} INTO guava_execution_envelopes VALUES (?,?,?,?,?,?)", values)
        assert con.execute("SELECT * FROM guava_execution_envelopes").fetchall() == before


@pytest.mark.parametrize("change_body", [False, True])
def test_explicit_event_sequence_replace_aborts_even_identical_body(setup, change_body):
    broker, ledger, sdk, path = setup
    buy(broker)
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA recursive_triggers=OFF")
        before = con.execute("SELECT * FROM guava_execution_events ORDER BY sequence").fetchall()
        values = list(before[0])
        if change_body:
            values[3] = "{}"
        values[5] += 100
        with pytest.raises(sqlite3.IntegrityError, match="sequence collision"):
            con.execute("INSERT OR REPLACE INTO guava_execution_events VALUES (?,?,?,?,?,?)", values)
        assert con.execute("SELECT * FROM guava_execution_events ORDER BY sequence").fetchall() == before


def test_event_canonical_replay_ignores_replace_and_preserves_created_at(setup):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA recursive_triggers=OFF")
        before = con.execute("SELECT * FROM guava_execution_events ORDER BY sequence").fetchall()
        original = before[0]
        con.execute("""INSERT OR REPLACE INTO guava_execution_events
            (submission_id,phase,evidence_json,fingerprint,created_at) VALUES (?,?,?,?,?)""",
            (*original[1:5], original[5] + 100))
        assert con.execute("SELECT * FROM guava_execution_events ORDER BY sequence").fetchall() == before
    broker._event(result["submission_id"], original[2], json.loads(original[3]))
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT * FROM guava_execution_events ORDER BY sequence").fetchall() == before


@pytest.mark.parametrize("verb", ["INSERT OR REPLACE", "INSERT OR IGNORE"])
def test_event_same_fingerprint_conflicting_body_aborts(setup, verb):
    broker, ledger, sdk, path = setup
    buy(broker)
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA recursive_triggers=OFF")
        before = con.execute("SELECT * FROM guava_execution_events ORDER BY sequence").fetchall()
        original = before[0]
        with pytest.raises(sqlite3.IntegrityError, match="conflicting execution event replay"):
            con.execute(f"""{verb} INTO guava_execution_events
                (submission_id,phase,evidence_json,fingerprint,created_at) VALUES (?,?,?,?,?)""",
                (original[1], original[2], '{"forged":true}', original[4], original[5] + 100))
        assert con.execute("SELECT * FROM guava_execution_events ORDER BY sequence").fetchall() == before


@pytest.mark.parametrize("body", ["correct_proof_wrong_fingerprint", "{", "[]", '{"x":NaN}'])
def test_snapshot_checks_body_fingerprint_before_no_post_proof(setup, body):
    from polybot.execution import _hash
    import hashlib
    broker, ledger, sdk, path = setup
    env = dict(buy(broker)["envelope"], token_id="token-2", decision_id="unposted")
    sid = ledger.record_intent(token_id="token-2", side="BUY", requested_price=.5, requested_size=10, simulation=False)
    broker._envelope(sid, env)
    if body == "correct_proof_wrong_fingerprint":
        body = json.dumps({"reason": "PRE_POST_ABORT", "envelope_sha256": _hash(env)}, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(path) as con:
        con.execute("""INSERT INTO guava_execution_events
            (submission_id,phase,evidence_json,fingerprint,created_at) VALUES (?,?,?,?,?)""",
            (sid, "NO_POST", body, "0" * 64, time.time()))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(BrokerEvidenceError, match="body/fingerprint"):
        broker._snapshot(sid)
    with pytest.raises(BrokerEvidenceError, match="body/fingerprint"):
        broker.execution_inventory()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_new_insert_guards_install_on_existing_broker_db_without_row_changes(setup):
    broker, ledger, sdk, path = setup
    result = buy(broker)
    with sqlite3.connect(path) as con:
        con.execute("DROP TRIGGER guava_execution_envelopes_no_replace")
        con.execute("DROP TRIGGER guava_execution_events_no_replace")
        original = con.execute("SELECT * FROM guava_execution_events ORDER BY sequence").fetchall()
    reopened = Broker(ledger, path, Budget(), sdk_client=sdk)
    try:
        assert reopened.execution_inventory()[0]["submission_id"] == result["submission_id"]
        with sqlite3.connect(path) as con:
            assert con.execute("SELECT * FROM guava_execution_events ORDER BY sequence").fetchall() == original
            assert con.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name LIKE '%no_replace'").fetchone()[0] == 2
    finally:
        reopened.close()
