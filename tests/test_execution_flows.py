from __future__ import annotations

from types import SimpleNamespace

import pytest

from polylab import db
from polylab.execution.clob import Clob, classify_post_error, classify_post_response
from polylab.execution.fees import ZERO, FeeSchedule, fee_usdc, parse_fee_schedule
from polylab.execution.ledger import StrategyLedger
from polylab.execution.paper import paper_buy, paper_sell
from polylab.execution.reconcile import QUARANTINE_AFTER_S, reconcile_order, settle_resolutions
from polylab.marketview import make_book
from polylab.strategies.base import EntryIntent
from test_strategy_fixtures import T0, Env


# ---------------------------------------------------------------- fees

def test_fee_formula_and_unknown():
    s = FeeSchedule(0.03, 1, True, "t")
    assert fee_usdc(s, 100, 0.5) == pytest.approx(100 * 0.03 * 0.25)
    assert fee_usdc(s, 100, 0.5, taker=False) == 0.0
    assert fee_usdc(None, 100, 0.5) is None
    assert parse_fee_schedule('{"feesEnabled": false}') is ZERO
    assert parse_fee_schedule('{"feesEnabled": true}') is None              # enabled without schedule = unknown
    assert parse_fee_schedule({"fd": {"r": 0.02, "e": 2, "to": True}}).exponent == 2
    assert parse_fee_schedule({"feesEnabled": True, "feeSchedule": {"rate": 0.01, "exponent": 1,
                                                                     "takerOnly": True}}).rate == 0.01


# ---------------------------------------------------------------- paper broker

def test_paper_fok_respects_limit_and_depth():
    book = make_book("t", 0, [(0.90, 3), (0.80, 100)], [(0.93, 2), (0.95, 100)])
    f = paper_buy(book, 5.0, 0.95, ZERO)
    assert f.filled and f.usd == pytest.approx(5.0) and f.fee_usdc == 0.0
    assert not paper_buy(book, 5.0, 0.94, ZERO).filled          # only 1.86 USD at <= .94
    assert paper_buy(book, 5.0, 0.95, None).fee_usdc is None     # unknown fee flagged, not zero
    s = paper_sell(book, 10, 0.80, ZERO)
    assert s.filled and s.usd == pytest.approx(3 * 0.9 + 7 * 0.8)
    assert not paper_sell(book, 10, 0.85, ZERO).filled


# ---------------------------------------------------------------- fake CLOB

class FakeSigned(SimpleNamespace):
    pass


class FakeClient:
    def __init__(self, book=None, fd=None, accepting=True):
        self.books = book or {}
        self.fd = fd
        self.accepting = accepting
        self.posted = []
        self.orders = {}
        self.trades = {}
        self.post_exc = None

    def get_order_book(self, token_id):
        return self.books.get(token_id)

    def get_market(self, cid):
        return {"accepting_orders": self.accepting, "closed": False, "active": True}

    def get_clob_market_info(self, cid):
        return {"t": [], "fd": self.fd} if self.fd is not None else {"t": []}

    def get_tick_size(self, token_id):
        return "0.01"

    def create_market_order(self, args, options=None):
        taker = int(args.amount / args.price * 1e4) * 100
        return FakeSigned(makerAmount=int(round(args.amount * 1e6)), takerAmount=taker, args=args)

    def create_order(self, args, options=None):
        maker = int(args.size * 100) * 10000
        return FakeSigned(makerAmount=maker, takerAmount=int(maker * args.price), args=args)

    def post_order(self, signed, order_type):
        if self.post_exc:
            raise self.post_exc
        self.posted.append(signed)
        oid = f"o{len(self.posted)}"
        a = signed.args
        is_buy = a.side == "BUY"
        shares = (signed.takerAmount if is_buy else signed.makerAmount) / 1e6
        price = a.price
        tid = f"t{len(self.posted)}"
        self.orders[oid] = {"id": oid, "status": "MATCHED", "size_matched": shares, "associate_trades": [tid]}
        self.trades[tid] = {"id": tid, "taker_order_id": oid, "size": shares, "price": price, "status": "MATCHED",
                            "maker_orders": []}
        return {"success": True, "orderID": oid, "status": "matched", "tradeIDs": [tid]}

    def get_order(self, oid):
        return self.orders.get(oid)

    def get_trades(self, params, only_first_page=False):
        if params.id:
            return [self.trades[params.id]] if params.id in self.trades else []
        return list(self.trades.values())

    def cancel_orders(self, ids):
        return {"canceled": ids}


def _ledger(tmp_path):
    env = Env(tmp_path / "root")
    return env, StrategyLedger(db.strategy(env.paths, "v"), "v")


def _intent(token="tok", cond="c1"):
    return EntryIntent(token_id=token, condition_id=cond, game_key="g", sport="nfl", league=None,
                       outcome_label="X", signal_price=0.93, min_price=0.92, max_price=0.999, reason="t",
                       exit_rules={"stop_price": 0.7})


def test_live_buy_reconcile_pending_to_confirmed(tmp_path):
    env, led = _ledger(tmp_path)
    client = FakeClient(fd={"r": 0.03, "e": 1, "to": True})
    clob = Clob(client)
    pid = led.open_position(_intent(), "live", 1, 5.0, T0)
    signed = clob.sign_fok_buy("tok", 5.0, 0.93, 0.999)
    assert signed.maker_amount == 5.0 and signed.price == 0.93
    iid = led.record_intent(position_id=pid, mode="live", side="BUY", token_id="tok", condition_id="c1", now=T0,
                            usdc_amount=5.0, limit_price=signed.price)
    assert led.order(iid)["status"] == "intent"                   # durable before POST
    st, oid = classify_post_response(clob.post(signed))
    led.update_order(iid, T0, st, oid)
    assert reconcile_order(led, clob, led.order(iid), T0, clob.fee_schedule) == "matched"   # trade still MATCHED
    assert led.position(pid)["status"] == "pending"
    client.trades["t1"]["status"] = "CONFIRMED"
    assert reconcile_order(led, clob, led.order(iid), T0 + 30, clob.fee_schedule) == "confirmed"
    p = led.position(pid)
    assert p["status"] == "open" and p["shares"] == pytest.approx(5.3763)
    assert p["entry_fee_usdc"] == pytest.approx(5.3763 * 0.03 * 0.93 * 0.07, abs=1e-5)
    assert p["cost_usdc"] == pytest.approx(5.3763 * 0.93 + p["entry_fee_usdc"], abs=1e-6)


def test_unknown_fee_keeps_pending_then_quarantines(tmp_path):
    env, led = _ledger(tmp_path)
    client = FakeClient(fd=None)
    clob = Clob(client)
    pid = led.open_position(_intent(), "live", 1, 5.0, T0)
    signed = clob.sign_fok_buy("tok", 5.0, 0.93, 0.999)
    iid = led.record_intent(position_id=pid, mode="live", side="BUY", token_id="tok", condition_id="c1", now=T0)
    st, oid = classify_post_response(clob.post(signed))
    led.update_order(iid, T0, st, oid)
    client.trades["t1"]["status"] = "CONFIRMED"
    no_fee = lambda cid: None
    assert reconcile_order(led, clob, led.order(iid), T0 + 60, no_fee) == "matched"
    assert led.position(pid)["status"] == "pending"
    reconcile_order(led, clob, led.order(iid), T0 + QUARANTINE_AFTER_S, no_fee)
    assert led.position(pid)["status"] == "quarantined"


def test_zero_fill_and_failed_trades(tmp_path):
    env, led = _ledger(tmp_path)
    client = FakeClient()
    clob = Clob(client)
    pid = led.open_position(_intent(), "live", 1, 5.0, T0)
    iid = led.record_intent(position_id=pid, mode="live", side="BUY", token_id="tok", condition_id="c1", now=T0)
    client.orders["oz"] = {"id": "oz", "status": "CANCELED", "size_matched": 0, "associate_trades": []}
    led.update_order(iid, T0, "posted", "oz")
    assert reconcile_order(led, clob, led.order(iid), T0 + 5, lambda c: ZERO) == "cancelled"
    assert led.position(pid)["status"] == "unfilled"


def test_post_error_classification():
    assert classify_post_error(SimpleNamespace(status_code=400)) == "failed"
    assert classify_post_error(TimeoutError()) == "unknown"
    assert classify_post_response({"success": True, "orderID": "x", "status": "matched"}) == ("matched", "x")
    assert classify_post_response({"success": False, "errorMsg": "no"}) == ("failed", None)
    assert classify_post_response({"success": False, "orderID": "x"})[0] == "unknown"


def test_live_sell_partial_then_resolution(tmp_path):
    env, led = _ledger(tmp_path)
    env.us_game("g", "nfl", T0)
    pid = led.open_position(_intent(token="g-h", cond="c-g"), "paper", 1, 5.0, T0)
    led.apply_buy(pid, 10.0, 7.2, 0.0)
    led.apply_sell(pid, 5.0, 4.5, 0.01, "take_profit", T0 + 60, "paper")
    p = led.position(pid)
    assert p["status"] == "open" and p["shares"] == 5.0 and p["exit_reason"] == "partial_take_profit"
    env.resolve("c-g", 0, T0 + 3600)                             # home token wins
    assert settle_resolutions(led, env.view(T0 + 7200), T0 + 7200, "paper") == 1
    p = led.position(pid)
    assert p["status"] == "resolved" and p["exit_reason"] == "resolution_win"
    assert p["realized_pnl"] == pytest.approx(4.49 + 5.0 - 7.2)
