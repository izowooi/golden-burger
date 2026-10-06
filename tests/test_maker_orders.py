"""Maker (GTC post-only) lifecycle against a fake CLOB: rest, partial fill, replace, TTL / window-close cancel,
resting TP, stop cancels TP, kickoff cancels TP, sweep, paper trade-through simulation, fee evidence."""

from __future__ import annotations

import json

import pytest
import yaml

from polylab import db, settings
from polylab.engine.tick import run
from polylab.execution import maker
from polylab.execution.clob import Clob
from polylab.execution.ledger import StrategyLedger
from polylab.marketview import Book
from polylab.strategies.base import EntryIntent
from test_execution_flows import FakeClient, FakeSigned
from test_goal_over import KICK, LEAGUES, world

FD = {"r": 0.05, "e": 1, "to": True}


class PostOnlyReject(Exception):
    status_code = 400


class MakerClient(FakeClient):
    """FakeClient + resting GTC orders, open-order listing, cancel by id and maker-side trades."""

    def __init__(self, bid=0.90, ask=0.93, **kw):
        super().__init__(fd=FD, **kw)
        self.set_book("ov1", bid, ask)
        self.cancelled = []
        self.n = 0

    def set_book(self, token, bid, ask, size=500, min_size="5"):
        self.books[token] = {"bids": [{"price": str(bid), "size": str(size)}],
                             "asks": [{"price": str(ask), "size": str(size)}], "min_order_size": min_size}

    def create_order(self, args, options=None):
        shares = int(round(args.size * 100)) * 10000
        usdc = int(shares * args.price)
        if args.side == "BUY":
            return FakeSigned(makerAmount=usdc, takerAmount=shares, args=args)
        return FakeSigned(makerAmount=shares, takerAmount=usdc, args=args)

    def post_order(self, signed, order_type, post_only=False, defer_exec=False):
        if order_type != "GTC":
            return super().post_order(signed, order_type)
        a = signed.args
        book = self.books[a.token_id]
        best_ask = min(float(x["price"]) for x in book["asks"])
        best_bid = max(float(x["price"]) for x in book["bids"])
        if post_only and ((a.side == "BUY" and a.price >= best_ask) or (a.side == "SELL" and a.price <= best_bid)):
            raise PostOnlyReject("post-only order would cross")
        self.posted.append(signed)
        self.n += 1
        oid = f"g{self.n}"
        size = (signed.takerAmount if a.side == "BUY" else signed.makerAmount) / 1e6
        self.orders[oid] = {"id": oid, "status": "LIVE", "size_matched": 0.0, "original_size": str(size),
                            "price": str(a.price), "side": a.side, "asset_id": a.token_id, "associate_trades": [],
                            "post_only": post_only}
        return {"success": True, "orderID": oid, "status": "live"}

    def get_open_orders(self, params=None, only_first_page=False, next_cursor=None):
        return [o for o in self.orders.values() if o["status"] == "LIVE"]

    def cancel_orders(self, ids):
        for i in ids:
            self.cancelled.append(i)
            if i in self.orders and self.orders[i]["status"] == "LIVE":
                self.orders[i]["status"] = "CANCELED"
        return {"canceled": ids}

    def maker_fill(self, oid, shares, status="CONFIRMED", ts=None, side_tag="MAKER"):
        o = self.orders[oid]
        tid = f"mt{len(self.trades) + 1}"
        self.trades[tid] = {"id": tid, "status": status, "taker_order_id": "someone", "size": shares,
                            "price": o["price"], "trader_side": side_tag, "match_time": str(ts or 0),
                            "maker_orders": [{"order_id": oid, "matched_amount": str(shares), "price": o["price"],
                                              "maker_address": "0x" + "9" * 40}]}
        o["associate_trades"].append(tid)
        o["size_matched"] = round(o["size_matched"] + shares, 6)
        if o["size_matched"] >= float(o["original_size"]) - 1e-9:
            o["status"] = "MATCHED"
        return tid


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.delenv("POLYLAB_KILL", raising=False)
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "accounts.env").write_text(
        "POLYBOT_CAT__POLYMARKET_PRIVATE_KEY=0xabc\nPOLYBOT_CAT__POLYMARKET_FUNDER_ADDRESS=0xdef\n")
    env, now = world(tmp_path)
    now -= 3600                          # one hour before kickoff: the entry window is open
    env.book("ov1", now - 30, [(0.90, 500)], [(0.93, 500)])
    reg = tmp_path / "reg"
    reg.mkdir()
    real = yaml.safe_load(open("strategies/goal-over-all.yaml"))
    # tunable values (AI retro / owner) are fixed here so a yaml retune cannot break this test
    real["params"].update(entry_minutes_before_max=4320, entry_minutes_before_min=5, price_min=0.5, price_max=0.985,
                          take_profit_price=None, stop_loss_price=None, take_profit_delta=0.02, take_profit_pct=None,
                          maker_ttl_minutes=60, maker_reprice_ticks=2)
    real["params"].update(leagues=LEAGUES, order_style="maker", book_max_age_s=900)
    real.update(mode="live", account="cat", stake_usdc=5.0)
    (reg / "goal-over-all.yaml").write_text(yaml.safe_dump(real, allow_unicode=True))
    client = MakerClient()
    return env, now, reg, client


def tick(env, reg, client, now, **kw):
    out = run(env.paths, registry_dir=reg, poll=False, now=now, clob_factory=lambda c: Clob(client), **kw)
    assert out["ok"], out
    return out


def rows(env, sql):
    return [dict(r) for r in db.strategy(env.paths, "goal-over-all").execute(sql)]


def pos(env):
    return rows(env, "SELECT * FROM positions ORDER BY opened_at")


def orders(env):
    return rows(env, "SELECT * FROM orders ORDER BY created_at, rowid")


def test_entry_rests_partial_then_full_fill_then_tp_rests(live):
    env, now, reg, client = live
    tick(env, reg, client, now)
    [p] = pos(env)
    [o] = orders(env)
    assert p["status"] == "pending" and o["order_type"] == "GTC" and o["status"] == "resting"
    assert o["side"] == "BUY" and o["limit_price"] == pytest.approx(0.91)       # min(bid + tick, ask - tick)
    assert o["shares"] == pytest.approx(5.49) and o["post_only"] == 1 and o["purpose"] == "entry"
    assert client.orders["g1"]["post_only"] is True
    # partial CONFIRMED maker fill: booked fee-free, position still pending (rest keeps working)
    client.maker_fill("g1", 2.0, ts=now + 30)
    tick(env, reg, client, now + 60)
    [p] = pos(env)
    assert p["status"] == "pending" and p["shares"] == pytest.approx(2.0) and p["entry_fee_usdc"] == 0.0
    [f] = rows(env, "SELECT * FROM fills")
    assert f["liquidity"] == "maker" and f["fee_usdc"] == 0.0 and f["status"] == "CONFIRMED"
    assert f["taker_fee_est"] == pytest.approx(2.0 * 0.05 * 0.91 * 0.09, abs=1e-5) and f["match_ts"] == now + 30
    assert "maker_address" not in json.dumps(f) and "9999" not in json.dumps(f)
    # a not-yet-confirmed trade is not booked
    tid = client.maker_fill("g1", 3.49, status="MATCHED", ts=now + 90)
    tick(env, reg, client, now + 120)
    assert pos(env)[0]["shares"] == pytest.approx(2.0) and pos(env)[0]["status"] == "pending"
    client.trades[tid]["status"] = "CONFIRMED"
    tick(env, reg, client, now + 180)
    [p] = pos(env)
    assert p["status"] == "open" and p["shares"] == pytest.approx(5.49)
    assert p["entry_price"] == pytest.approx(0.91) and p["cost_usdc"] == pytest.approx(5.49 * 0.91)
    o = orders(env)[0]
    assert o["status"] == "confirmed" and o["filled_shares"] == pytest.approx(5.49)
    # the open position rests its take-profit SELL at entry + 0.02 (pre-kickoff, below the 0.99 hold zone)
    tp = [x for x in orders(env) if x["side"] == "SELL"]
    assert len(tp) == 1 and tp[0]["status"] == "resting" and tp[0]["limit_price"] == pytest.approx(0.93)
    assert tp[0]["purpose"] == "take_profit" and tp[0]["shares"] == pytest.approx(5.49)
    # the TP fills as maker -> position closed fee-free, profit = 5.49 x 0.02
    client.maker_fill(tp[0]["exchange_order_id"], 5.49, ts=now + 200)
    tick(env, reg, client, now + 240)
    [p] = pos(env)
    assert p["status"] == "closed" and p["exit_reason"] == "take_profit" and p["exit_fee_usdc"] == 0.0
    assert p["realized_pnl"] == pytest.approx(5.49 * 0.02, abs=1e-6)


def test_replace_on_drift_and_ttl(live):
    env, now, reg, client = live
    tick(env, reg, client, now)
    assert orders(env)[0]["limit_price"] == pytest.approx(0.91)
    # our own quote is removed before re-pricing: the live book showing it at the top must not ratchet the price
    client.books["ov1"]["bids"].append({"price": "0.91", "size": "5.49"})
    tick(env, reg, client, now + 60)
    assert len(orders(env)) == 1 and client.cancelled == []
    # the market moves up 3 ticks -> cancel + re-quote on the same position
    client.set_book("ov1", 0.93, 0.96)
    tick(env, reg, client, now + 120)
    os_ = orders(env)
    assert [o["status"] for o in os_] == ["cancelled", "resting"] and os_[0]["cancel_reason"] == "reprice"
    assert os_[1]["limit_price"] == pytest.approx(0.94) and os_[1]["position_id"] == os_[0]["position_id"]
    assert client.cancelled == ["g1"] and pos(env)[0]["status"] == "pending"
    # TTL (60 min) -> cancel + re-quote even without drift
    tick(env, reg, client, now + 120 + 3601)
    os_ = orders(env)
    assert [o["status"] for o in os_] == ["cancelled", "cancelled", "resting"] and os_[1]["cancel_reason"] == "ttl"
    assert len(pos(env)) == 1


def test_window_close_cancels_and_keeps_partial(live):
    env, now, reg, client = live
    tick(env, reg, client, now)
    client.maker_fill("g1", 1.5, ts=now + 10)
    t = KICK - 4 * 60                       # inside the last 5 minutes: entry window closed
    tick(env, reg, client, t)
    [o] = [x for x in orders(env) if x["side"] == "BUY"]
    assert o["status"] == "cancelled" and o["cancel_reason"] == "window_closed" and client.cancelled[0] == "g1"
    [p] = pos(env)
    assert p["status"] == "open" and p["shares"] == pytest.approx(1.5)
    # 1.5 shares are below the 5-share minimum: no resting TP is attempted
    assert not [x for x in orders(env) if x["side"] == "SELL"]


def test_window_close_without_fill_marks_unfilled(live):
    env, now, reg, client = live
    tick(env, reg, client, now)
    tick(env, reg, client, KICK - 60)
    [p] = pos(env)
    assert p["status"] == "unfilled" and orders(env)[0]["status"] == "cancelled"


def _open_with_resting_tp(live):
    env, now, reg, client = live
    tick(env, reg, client, now)
    client.maker_fill("g1", 5.49, ts=now + 10)
    tick(env, reg, client, now + 60)
    tp = [x for x in orders(env) if x["side"] == "SELL"]
    assert pos(env)[0]["status"] == "open" and tp and tp[0]["status"] == "resting"
    return env, now, reg, client, tp[0]


def test_stop_loss_cancels_resting_tp_first(live):
    env, now, reg, client, tp = _open_with_resting_tp(live)
    # -10% stop (0.91 -> 0.819): live book collapses; the TP must be cancelled before the taker SELL
    client.set_book("ov1", 0.80, 0.82)
    tick(env, reg, client, now + 120)
    assert client.cancelled[-1] == tp["exchange_order_id"]
    sells = [x for x in orders(env) if x["side"] == "SELL"]
    assert sells[0]["status"] == "cancelled" and sells[0]["cancel_reason"] == "stop_loss"
    assert sells[1]["order_type"] == "FOK" and sells[1]["shares"] == pytest.approx(5.49)
    assert pos(env)[0]["exit_reason"] == "stop_loss"


def test_stop_waits_when_tp_cancel_not_confirmed(live):
    env, now, reg, client, tp = _open_with_resting_tp(live)
    client.cancel_orders = lambda ids: {"canceled": []}            # venue does not confirm the cancel
    client.set_book("ov1", 0.80, 0.82)
    out = tick(env, reg, client, now + 120)
    sells = [x for x in orders(env) if x["side"] == "SELL"]
    assert len(sells) == 1 and sells[0]["status"] == "cancel_requested"     # no taker SELL while the TP may rest
    assert out["variants"][0]["skipped"].get("tp_cancel_pending") == 1


def test_kickoff_cancels_resting_tp(live):
    env, now, reg, client, tp = _open_with_resting_tp(live)
    tick(env, reg, client, KICK + 60)
    sells = [x for x in orders(env) if x["side"] == "SELL"]
    assert sells[0]["status"] == "cancelled" and sells[0]["cancel_reason"] == "kickoff"
    assert pos(env)[0]["status"] == "open" and len(sells) == 1       # in play: no new resting TP


def test_kill_switch_cancels_resting_entries(live):
    env, now, reg, client = live
    tick(env, reg, client, now)
    (env.paths.state / "KILL").write_text("x")
    tick(env, reg, client, now + 60)
    [o] = orders(env)
    assert o["status"] == "cancelled" and o["cancel_reason"] == "kill_switch"
    assert pos(env)[0]["status"] == "unfilled"


def test_dry_run_posts_nothing(live):
    env, now, reg, client = live
    tick(env, reg, client, now, dry_run=True)
    assert client.posted == [] and pos(env) == []


def test_unknown_fee_schedule_keeps_maker_fill_unbooked(live):
    env, now, reg, client = live
    client.fd = None
    env.core.execute("UPDATE markets SET fee_schedule=NULL WHERE condition_id='ou1'")
    env.core.commit()
    tick(env, reg, client, now)
    client.maker_fill("g1", 5.49, ts=now + 10)
    tick(env, reg, client, now + 60)
    assert rows(env, "SELECT * FROM fills") == [] and pos(env)[0]["status"] == "pending"
    assert "fee_schedule_unknown" in orders(env)[0]["response"]


def test_role_conflict_is_not_booked_as_maker(live):
    env, now, reg, client = live
    tick(env, reg, client, now)
    client.maker_fill("g1", 5.49, ts=now + 10, side_tag="TAKER")
    tick(env, reg, client, now + 60)
    assert rows(env, "SELECT * FROM fills") == []
    assert "liquidity_role_conflict" in orders(env)[0]["response"]


def test_post_only_rejection_marks_unfilled(live):
    env, now, reg, client = live
    client.set_book("ov1", 0.92, 0.93)
    real = client.post_order

    def crossing(signed, order_type, post_only=False, defer_exec=False):
        raise PostOnlyReject("would cross")
    client.post_order = crossing
    tick(env, reg, client, now)
    [p] = pos(env)
    assert p["status"] == "unfilled" and orders(env)[0]["status"] == "failed"
    client.post_order = real


def test_sweep_adopts_cancels_orphans_and_ignores_foreign(tmp_path):
    env, _ = world(tmp_path)
    led = StrategyLedger(db.strategy(env.paths, "v"), "v")
    client = MakerClient()
    clob = Clob(client)
    venue = maker.LiveVenue(clob, clob.fee_schedule)
    intent = EntryIntent("ov1", "ou1", "g1", "soccer", "epl", "Over 0.5", 0.93, 0.5, 0.985, "t", {})
    pid = led.open_position(intent, "live", 1, 5.0, 100)
    # an id-less intent whose POST went through (crash before we stored the id) -> adopted
    signed = clob.sign_limit("ov1", "BUY", 0.91, 5.49)
    iid = led.record_intent(position_id=pid, mode="live", side="BUY", token_id="ov1", condition_id="ou1", now=100,
                            shares=5.49, limit_price=0.91, order_type="GTC", purpose="entry", post_only=True)
    client.post_order(signed.order, "GTC", post_only=True)
    # an order we already consider cancelled but which still rests -> cancelled by id
    led2 = led.record_intent(position_id=pid, mode="live", side="BUY", token_id="ov1", condition_id="ou1", now=100,
                             shares=5.0, limit_price=0.85, order_type="GTC")
    client.post_order(clob.sign_limit("ov1", "BUY", 0.85, 5.0).order, "GTC", post_only=True)
    led.update_order(led2, 100, "cancelled", "g2")
    # someone else's order on the account
    client.post_order(clob.sign_limit("ov1", "BUY", 0.80, 6.0).order, "GTC", post_only=True)
    out = maker.sweep(led, venue, 200)
    assert out == {"orphans_cancelled": 1, "adopted": 1, "foreign": 1}
    assert led.order(iid)["exchange_order_id"] == "g1" and led.order(iid)["status"] == "resting"
    assert client.cancelled == ["g2"]


# ---------------------------------------------------------------- paper simulation

def _paper_order(led, price, side="BUY", shares=5.0, created=1000):
    intent = EntryIntent("ov1", "ou1", "g1", "soccer", "epl", "Over 0.5", 0.93, 0.5, 0.985, "t", {})
    pid = led.open_position(intent, "paper", 1, 5.0, created)
    if side == "SELL":
        led.apply_buy(pid, shares, shares * 0.9, 0.0)
    v = maker.PaperVenue(None, lambda c: None)
    status, iid = v.post(led, position_id=pid, side=side, token_id="ov1", condition_id="ou1", price=price,
                         shares=shares, now=created, purpose="entry" if side == "BUY" else "take_profit")
    return pid, iid


class _View:
    def __init__(self, book):
        self.b = book

    def book(self, token_id, now, max_age_s=180):
        return self.b


def test_paper_maker_fills_only_on_trade_through(tmp_path):
    env, _ = world(tmp_path)
    led = StrategyLedger(db.strategy(env.paths, "p"), "p")
    pid, iid = _paper_order(led, 0.91)
    sched = lambda c: maker.FeeSchedule(0.05, 1, True, "t")       # noqa: E731
    touch = maker.PaperVenue(_View(Book("ov1", 1060, [(0.90, 100)], [(0.91, 100)], "poll")), sched)
    assert maker.sync(led, touch, led.order(iid), 1060) == "resting"            # touching is not a fill
    synth = maker.PaperVenue(_View(Book("ov1", 1120, [(0.80, 1e6)], [(0.85, 1e6)], "synthetic", True)), sched)
    assert maker.sync(led, synth, led.order(iid), 1120) == "resting"            # synthetic books never fill
    stale = maker.PaperVenue(_View(Book("ov1", 990, [(0.80, 100)], [(0.85, 100)], "poll")), sched)
    assert maker.sync(led, stale, led.order(iid), 1130) == "resting"            # book older than the order
    thin = maker.PaperVenue(_View(Book("ov1", 1180, [(0.88, 100)], [(0.90, 2.0), (0.95, 100)], "poll")), sched)
    assert maker.sync(led, thin, led.order(iid), 1180) == "resting"             # 2 shares traded through
    assert maker.sync(led, thin, led.order(iid), 1240) == "resting"             # same book: no double fill
    through = maker.PaperVenue(_View(Book("ov1", 1300, [(0.88, 100)], [(0.89, 100)], "poll")), sched)
    assert maker.sync(led, through, led.order(iid), 1300) == "confirmed"
    fills = [dict(r) for r in led.conn.execute("SELECT * FROM fills ORDER BY ts")]
    assert [f["shares"] for f in fills] == [2.0, 3.0] and all(f["price"] == 0.91 for f in fills)
    assert all(f["fee_usdc"] == 0.0 and f["liquidity"] == "maker" and f["taker_fee_est"] > 0 for f in fills)
    assert [f["match_ts"] - 1000 for f in fills] == [180, 300]                  # fill delay
    p = led.position(pid)
    assert p["status"] == "open" and p["shares"] == pytest.approx(5.0)


def test_paper_tp_sell_fills_when_bids_cross(tmp_path):
    env, _ = world(tmp_path)
    led = StrategyLedger(db.strategy(env.paths, "p"), "p")
    pid, iid = _paper_order(led, 0.93, side="SELL")
    v = maker.PaperVenue(_View(Book("ov1", 1100, [(0.93, 100)], [(0.94, 100)], "poll")), lambda c: None)
    assert maker.sync(led, v, led.order(iid), 1100) == "resting"
    v = maker.PaperVenue(_View(Book("ov1", 1200, [(0.95, 100)], [(0.96, 100)], "poll")), lambda c: None)
    assert maker.sync(led, v, led.order(iid), 1200) == "confirmed"
    p = led.position(pid)
    assert p["status"] == "closed" and p["notes"] == "fee_unknown"              # unknown schedule flagged, not 0


def test_paper_tick_end_to_end_maker(tmp_path, monkeypatch):
    monkeypatch.delenv("POLYLAB_KILL", raising=False)
    env, now = world(tmp_path)
    now -= 3600
    env.book("ov1", now - 30, [(0.90, 500)], [(0.93, 500)])
    reg = tmp_path / "reg"
    reg.mkdir()
    real = yaml.safe_load(open("strategies/goal-over-all.yaml"))
    # tunable values (AI retro / owner) are fixed here so a yaml retune cannot break this test
    real["params"].update(entry_minutes_before_max=4320, entry_minutes_before_min=5, price_min=0.5, price_max=0.985,
                          take_profit_price=None, stop_loss_price=None, take_profit_delta=0.02, take_profit_pct=None,
                          maker_ttl_minutes=60, maker_reprice_ticks=2)
    real["params"].update(leagues=LEAGUES, order_style="maker")
    real.update(mode="paper", account=None, stake_usdc=5.0)
    (reg / "goal-over-all.yaml").write_text(yaml.safe_dump(real, allow_unicode=True))
    run(env.paths, registry_dir=reg, poll=False, now=now)
    [o] = orders(env)
    assert o["mode"] == "paper" and o["status"] == "resting" and o["limit_price"] == pytest.approx(0.91)
    env.book("ov1", now + 30, [(0.89, 500)], [(0.90, 500)])                     # asks trade through 0.91
    run(env.paths, registry_dir=reg, poll=False, now=now + 60)
    [p] = pos(env)
    assert p["status"] == "open" and p["entry_price"] == pytest.approx(0.91) and p["entry_fee_usdc"] == 0.0


def test_entry_price_rules():
    b = Book("t", 0, [(0.90, 10)], [(0.93, 10)])
    assert maker.entry_price(b, 0.01, 0.5, 0.985) == pytest.approx(0.91)
    assert maker.entry_price(b, 0.01, 0.5, 0.985, "join") == pytest.approx(0.90)
    assert maker.entry_price(Book("t", 0, [(0.92, 10)], [(0.93, 10)]), 0.01, 0.5, 0.985) == pytest.approx(0.92)
    assert maker.entry_price(b, 0.01, 0.95, 0.985) is None                      # below the band
    assert maker.entry_price(Book("t", 0, [], [(0.93, 10)]), 0.01, 0.5, 0.985) is None
    mine = maker.own_removed(Book("t", 0, [(0.91, 5.49), (0.90, 10)], [(0.93, 10)]), "BUY", 0.91, 5.49)
    assert mine.bids == [(0.90, 10)]


def test_off_variant_still_finishes_its_resting_orders(live):
    env, now, reg, client = live
    tick(env, reg, client, now)
    y = yaml.safe_load((reg / "goal-over-all.yaml").read_text())
    y["mode"] = "off"
    (reg / "goal-over-all.yaml").write_text(yaml.safe_dump(y, allow_unicode=True))
    out = tick(env, reg, client, KICK - 60)                      # window closed -> cancel even though off
    assert [v["sports"] for v in out["variants"]] == [[]]        # no-entry leg
    assert orders(env)[0]["status"] == "cancelled" and pos(env)[0]["status"] == "unfilled"
    out = tick(env, reg, client, KICK)
    assert out["variants"] == []                                 # nothing resting any more: the leg stops


def test_execution_stats_and_report_section(live):
    from polylab.reports import build, render

    env, now, reg, client = live
    tick(env, reg, client, now)
    client.maker_fill("g1", 5.49, ts=now + 120)
    tick(env, reg, client, now + 180)
    e = build.execution_stats(env.paths, "goal-over-all")["live"]
    assert e["entries"] == 1 and e["entries_filled"] == 1 and e["fill_rate"] == 1.0 and e["avg_wait_min"] == 2.0
    assert e["maker_fills"] == 1 and e["maker_fee_usdc"] == 0.0
    assert e["fees_saved_usdc"] == pytest.approx(5.49 * 0.05 * 0.91 * 0.09, abs=1e-5)
    assert e["tp_orders"] == 1 and e["tp_active"] == 1
    lines = render.section_execution({"variants": [{"id": "goal-over-all", "execution": {"live": e}}]})
    assert "### 지정가(maker) 주문 체결" in lines and any("100.0%" in x or "100%" in x for x in lines)


def test_backtest_replays_maker_as_taker_proxy_and_covers_pre_game(tmp_path):
    from types import SimpleNamespace

    from polylab.analysis import backtest as bt

    v = SimpleNamespace(family="goal_over", sports=["soccer"], per_sport=False,
                        params={"order_style": "maker", "entry_minutes_before_max": 120,
                                "sport_overrides": {"soccer": {"order_style": "maker"}}})
    assert bt._as_taker(v) and v.params["order_style"] == "taker"
    assert v.params["sport_overrides"]["soccer"]["order_style"] == "taker"
    env, _ = world(tmp_path)                                      # g1 kicks off at KICK, g2 at KICK + 600
    steps = bt.active_steps(env.core, v, KICK - 3 * 3600, KICK + 3600, 60)
    pre = [t for t in steps if t < KICK]
    assert pre and min(pre) >= KICK - 2 * 3600 and all(t % 600 == 0 for t in pre)   # coarse pre-game window
    assert KICK + 60 in steps                                     # the game itself at --step


def test_reprice_tolerance_floor_on_fine_ticks(live):
    env, now, reg, client = live
    client.get_tick_size = lambda token_id: "0.001"
    client.set_book("ov1", 0.900, 0.930)
    tick(env, reg, client, now)
    assert orders(env)[0]["limit_price"] == pytest.approx(0.901)
    client.set_book("ov1", 0.905, 0.930)            # 4 ticks of 0.001 but under 1 cent: keep resting
    tick(env, reg, client, now + 60)
    assert len(orders(env)) == 1 and client.cancelled == []
    client.set_book("ov1", 0.915, 0.930)            # 1.5 cents: re-quote
    tick(env, reg, client, now + 120)
    assert [o["status"] for o in orders(env)] == ["cancelled", "resting"]
    assert orders(env)[1]["limit_price"] == pytest.approx(0.916)
