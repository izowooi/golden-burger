"""cherry back-to-basics (2026-10-05): general-market entry window/band/floors/category, v2 exits, and the
MarketView core-miss fallback onto data/general (NO side mirrored from the YES row)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from polylab.general import store
from polylab.marketview import make_book
from polylab.strategies.base import Ledger, PositionView
from polylab.strategies.cherry import Cherry, leading_side
from test_strategy_fixtures import T0, Env

FEE = json.dumps({"feesEnabled": True, "feeSchedule": {"exponent": 1, "rate": 0.04, "takerOnly": True}})
H = 3600


def variant(**params):
    return SimpleNamespace(id="cherry-t", family="cherry", sports=["soccer"], params=params, limits={},
                           stake_usdc=5.0, mode="paper")


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path / "root")


def gen_market(env, cid, *, end_in_h, category="politics", volume=50000.0, listed_h_before_end=200, in_core=0,
               closed=0, resolved=None, closed_at=None, fee=FEE):
    reg = store.registry(env.paths)
    end = T0 + int(end_in_h * H)
    reg.execute(
        "INSERT INTO gen_markets(condition_id, question, category, tags, outcomes, yes_token, no_token, neg_risk, "
        "created_at, start_date, end_date, end_ref, closed, closed_at, resolved_index, fee_json, volume, liquidity, "
        "in_core, first_seen, updated_at, source) VALUES(?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?, 'discover')",
        (cid, f"Q {cid}?", category, "[]", json.dumps(["Yes", "No"]), f"{cid}-y", f"{cid}-n",
         end - listed_h_before_end * H, end - listed_h_before_end * H, end, end, closed, closed_at, resolved, fee,
         volume, 1000.0, in_core, T0 - 86400, T0))
    reg.commit()
    gid = reg.execute("SELECT id FROM gen_markets WHERE condition_id=?", (cid,)).fetchone()[0]
    reg.close()
    return gid


def quote(env, gid, ts, bid, ask, size=500.0, src=store.SRC_POLL):
    p = round((bid + ask) / 2, 6) if bid is not None and ask is not None else None
    store.write_quotes(env.paths, [(gid, ts - ts % 60, bid, ask, size, size, None, p, src)])


def test_leading_side():
    assert leading_side(0.92, 0.08) == 0 and leading_side(0.07, 0.93) == 1
    assert leading_side(None, 0.9) == 1 and leading_side(0.3, None) == 1 and leading_side(None, None) is None


def test_fallback_market_price_book_mirror_and_core_untouched(env):
    gid = gen_market(env, "g1", end_in_h=60)
    quote(env, gid, T0 - 120, 0.06, 0.08)                       # YES 0.07 -> NO leads at 0.93 (bid .92 / ask .94)
    v = env.view(T0)
    m = v.market("g1")
    assert m.market_type == "general" and m.game_key is None and [t.side for t in m.tokens] == ["yes", "no"]
    assert v.price("g1-y", T0)[1] == pytest.approx(0.07) and v.price("g1-n", T0)[1] == pytest.approx(0.93)
    b = v.book("g1-n", T0)
    assert b.source == "general_l1" and b.best_bid == pytest.approx(0.92) and b.best_ask == pytest.approx(0.94)
    assert v.book("g1-n", T0 + 3600) is None                    # stale (max_age 180 s)
    assert v.token_market("g1-n").condition_id == "g1"
    assert v.market("nope") is None and v.price("nope", T0) is None
    env.us_game("core", "mlb", T0 + 5 * H)                      # a core market still answers from core only
    assert v.market("c-core").market_type == "moneyline"
    v.close()


def test_resolution_from_general_registry(env):
    gen_market(env, "r1", end_in_h=-1, closed=1, resolved=1, closed_at=T0 - 600)
    v = env.view(T0)
    assert v.resolution("r1", T0) == 1 and v.resolution("r1", T0 - 3600) is None
    assert not v.is_tradable(v.market("r1"), T0)
    v.close()


def test_entry_window_band_floors_category_and_once(env):
    ok = gen_market(env, "ok", end_in_h=60)
    early = gen_market(env, "early", end_in_h=90)                       # outside 48-72 h
    young = gen_market(env, "young", end_in_h=60, listed_h_before_end=50)  # listed < 72 h before end
    thin = gen_market(env, "thin", end_in_h=60, volume=2000.0)
    sport = gen_market(env, "sp", end_in_h=60, category="sports")
    wide = gen_market(env, "wide", end_in_h=60)
    low = gen_market(env, "low", end_in_h=60)
    for g in (ok, early, young, thin, sport):
        quote(env, g, T0 - 60, 0.91, 0.93)
    quote(env, wide, T0 - 60, 0.88, 0.96)                               # mid .92 but ask .96 > band
    quote(env, low, T0 - 60, 0.85, 0.87)
    s = Cherry({"hours_to_end_min": 48, "hours_to_end_max": 72, "entry_min": 0.90, "entry_max": 0.95,
                "min_volume": 10000, "categories": ["politics"], "take_profit_delta": 0.03,
                "take_profit_price": None, "time_exit_hours_before_end": 6}, variant())
    v = env.view(T0)
    [it] = s.entry_signals(v, T0, Ledger())
    assert it.condition_id == "ok" and it.token_id == "ok-y" and it.sport == "politics" and it.game_key is None
    r = it.exit_rules
    assert r["v"] == 2 and r["take_profit_delta"] == 0.03 and r["hold_above_price"] == 0.99
    assert r["time_exit_at"] == T0 + 60 * H - 6 * H and r["stop_loss_percent"] is None
    assert ("wide", "ask_above_band") in s.skips
    taken = PositionView("p", "unfilled", "ok-y", "ok", None, "politics", T0, None, None, None)
    assert s.entry_signals(v, T0, Ledger([taken])) == []
    s_all = Cherry({"hours_to_end_min": 48, "hours_to_end_max": 72, "min_volume": 10000}, variant())
    assert {i.condition_id for i in s_all.entry_signals(v, T0, Ledger())} == {"ok", "sp"}
    v.close()


def test_no_side_entry_and_fee_from_general(env):
    g = gen_market(env, "n", end_in_h=50)
    quote(env, g, T0 - 60, 0.07, 0.08)                               # NO bid .92 ask .93
    s = Cherry({}, variant())
    v = env.view(T0)
    [it] = s.entry_signals(v, T0, Ledger())
    assert it.token_id == "n-n" and it.signal_price == pytest.approx(0.925)
    chk = s.confirm_entry(it, {"n-n": v.book("n-n", T0)}, 5.0)
    assert chk.ok and chk.limit_price == pytest.approx(0.93)
    v.close()


def pos(rules, **kw):
    base = dict(position_id="p1", status="open", token_id="x-y", condition_id="x", game_key=None,
                sport="politics", opened_at=T0, entry_price=0.92, shares=5.43, cost_usdc=5.02, exit_rules=rules)
    base.update(kw)
    return PositionView(**base)


def test_v2_exits_tp_hold_time_and_no_stop(env):
    g = gen_market(env, "x", end_in_h=10)
    s = Cherry({}, variant())
    rules = {"v": 2, "take_profit_delta": None, "take_profit_price": 0.95, "hold_above_price": 0.99,
             "time_exit_at": T0 + 4 * H, "end_ref": T0 + 10 * H, "stop_loss_percent": None,
             "take_profit_percent": None, "trailing_enabled": False}
    quote(env, g, T0, 0.50, 0.52)                                    # collapse: no stop
    assert s.exit_signals(env.view(T0), T0, pos(rules)) is None
    quote(env, g, T0 + 60, 0.955, 0.96)
    it = s.exit_signals(env.view(T0 + 60), T0 + 60, pos(rules))
    assert it.kind == "take_profit" and it.min_price == pytest.approx(0.95)
    assert s.confirm_exit(pos(rules), it, make_book("x-y", T0, [(0.955, 100)], [(0.96, 100)])).ok
    quote(env, g, T0 + 120, 0.99, 0.995)
    assert s.exit_signals(env.view(T0 + 120), T0 + 120, pos(rules)) is None   # hold to resolution
    t = T0 + 4 * H
    quote(env, g, t, 0.93, 0.94)
    it = s.exit_signals(env.view(t), t, pos(rules))
    assert it.kind == "time_exit" and it.min_price == pytest.approx(0.90)
