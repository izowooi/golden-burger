"""cherry absolute take-profit / hold-above / null SL and plum early (delta) take-profit (2026-10-04)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from polylab.marketview import make_book
from polylab.strategies.base import Ledger, PositionView
from polylab.strategies.cherry import Cherry
from polylab.strategies.plum import Plum
from test_strategy_fixtures import T0, Env, level_book

FEE_V3 = '{"feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": true}, "feesEnabled": true}'
NEW_RULES = {"take_profit_percent": None, "stop_loss_percent": None, "trailing_enabled": False,
             "trailing_percent": 0.15, "take_profit_delta": 0.03, "take_profit_price": None,
             "hold_above_price": 0.99, "submit_mid": 0.92}


def variant(family, sports, **params):
    return SimpleNamespace(id=f"{family}-t", family=family, sports=sports, params=params, limits={},
                           stake_usdc=5.0, mode="paper")


def pos(**kw):
    base = dict(position_id="p1", status="open", token_id="g-h", condition_id="c-g", game_key="g", sport="mlb",
                opened_at=T0, entry_price=0.92, shares=5.43, cost_usdc=5.02, exit_rules=dict(NEW_RULES))
    base.update(kw)
    return PositionView(**base)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path / "root")
    return e


def _fee(env, cond="c-g", raw=FEE_V3):
    env.core.execute("UPDATE markets SET fee_schedule=? WHERE condition_id=?", (raw, cond))
    env.core.commit()


# ---------------------------------------------------------------- cherry

def test_cherry_entry_freezes_new_rules_and_in_play_window(env):
    now = T0
    env.us_game("ip", "mlb", now - 50 * 60)                       # 50 min in play
    env.bar("ip-h", now - 60, 0.92)
    level_book(env, "ip-h", now - 30, 0.915, 0.925)
    env.us_game("late", "mlb", now - 90 * 60)                     # 90 min in play: outside 60
    env.bar("late-h", now - 60, 0.92)
    level_book(env, "late-h", now - 30, 0.915, 0.925)
    env.us_game("pre", "mlb", now + 80 * 3600)                    # > 72 h pre-game
    env.bar("pre-h", now - 60, 0.92)
    level_book(env, "pre-h", now - 30, 0.915, 0.925)
    s = Cherry({"buy_threshold": 0.90, "sell_threshold": 0.95, "entry_hours_max": 72, "in_play_max_minutes": 60,
                "take_profit_delta": 0.03, "hold_above_price": 0.99, "stop_loss_percent": None,
                "take_profit_percent": None, "trailing_enabled": False}, variant("cherry", ["mlb"]))
    [it] = s.entry_signals(env.view(now), now, Ledger())
    assert it.game_key == "ip" and it.features["in_play"]
    r = it.exit_rules
    assert r["take_profit_delta"] == 0.03 and r["hold_above_price"] == 0.99
    assert r["stop_loss_percent"] is None and r["take_profit_percent"] is None and r["trailing_enabled"] is False


def test_cherry_absolute_tp_threshold_and_net_positive(env):
    env.us_game("g", "mlb", T0)
    _fee(env)
    s = Cherry({}, variant("cherry", ["mlb"]))
    now = T0 + 3600
    p = pos()
    assert s.tp_threshold(p) == pytest.approx(0.95)
    level_book(env, "g-h", now, 0.949, 0.955)
    assert s.exit_signals(env.view(now), now, p) is None            # below entry + 0.03, no SL: hold
    level_book(env, "g-h", now + 60, 0.95, 0.955)
    it = s.exit_signals(env.view(now + 60), now + 60, p)
    assert it and it.kind == "take_profit" and it.shares == 5.43 and it.min_price == pytest.approx(0.95)
    assert s.confirm_exit(p, it, make_book("g-h", now, [(0.95, 100)], [(0.96, 100)])).ok
    assert s.confirm_exit(p, it, make_book("g-h", now, [(0.94, 100)], [(0.96, 100)])).reason == "below_tp"
    # a fixed sell price takes the lower of the two thresholds
    both = pos(exit_rules={**NEW_RULES, "take_profit_delta": 0.06, "take_profit_price": 0.97})
    assert s.tp_threshold(both) == pytest.approx(0.97)
    only_price = pos(exit_rules={**NEW_RULES, "take_profit_delta": None, "take_profit_price": 0.98})
    assert s.tp_threshold(only_price) == pytest.approx(0.98)
    # fees can make a tiny delta unprofitable -> no TP
    tiny = pos(exit_rules={**NEW_RULES, "take_profit_delta": 0.001})
    level_book(env, "g-h", now + 120, 0.921, 0.93)
    assert s.exit_signals(env.view(now + 120), now + 120, tiny) is None


def test_cherry_unknown_fee_never_takes_profit(env):
    env.us_game("g", "mlb", T0)                                     # fee_schedule NULL
    s = Cherry({}, variant("cherry", ["mlb"]))
    now = T0 + 3600
    level_book(env, "g-h", now, 0.97, 0.975)
    assert s.exit_signals(env.view(now), now, pos()) is None


def test_cherry_hold_above_and_no_stop(env):
    env.us_game("g", "mlb", T0)
    _fee(env)
    s = Cherry({}, variant("cherry", ["mlb"]))
    now = T0 + 3600
    hi = pos(exit_rules={**NEW_RULES, "take_profit_delta": None, "take_profit_price": 0.98})
    level_book(env, "g-h", now, 0.99, 0.995)
    assert s.exit_signals(env.view(now), now, hi) is None           # bid >= 0.99: ride to resolution
    it = s.exit_signals(env.view(now), now, pos(exit_rules={**NEW_RULES, "hold_above_price": None,
                                                              "take_profit_delta": None, "take_profit_price": 0.98}))
    assert it and it.kind == "take_profit"
    assert s.confirm_exit(hi, it, make_book("g-h", now, [(0.99, 100)], [(0.995, 100)])).reason == "hold_above_price"
    level_book(env, "g-h", now + 60, 0.30, 0.31)                    # collapse: SL and trailing are off
    assert s.exit_signals(env.view(now + 60), now + 60, pos()) is None


def test_cherry_legacy_rules_unchanged(env):
    env.us_game("g", "mlb", T0)
    _fee(env)
    s = Cherry({}, variant("cherry", ["mlb"]))
    now = T0 + 3600
    legacy = {"take_profit_percent": 0.20, "stop_loss_percent": -0.08, "trailing_enabled": True,
              "trailing_percent": 0.15, "submit_mid": 0.775}
    p = pos(entry_price=0.78, shares=6.41, exit_rules=legacy)
    assert s.tp_threshold(p) is None
    level_book(env, "g-h", now, 0.70, 0.71)
    it = s.exit_signals(env.view(now), now, p)
    assert it and it.kind == "stop_loss"
    level_book(env, "g-h", now + 60, 0.94, 0.95)
    it = s.exit_signals(env.view(now + 60), now + 60, p)
    assert it and it.kind == "take_profit" and it.min_price == 0.78 and "absolute_tp" not in it.features


def test_cherry_depth_floor_on_entry():
    s = Cherry({"buy_threshold": 0.90, "sell_threshold": 0.95, "min_ask_depth_usd": 50}, variant("cherry", ["mlb"]))
    it = SimpleNamespace(token_id="t", sport="mlb", min_price=0.90, max_price=0.95)
    thin = make_book("t", T0, [(0.91, 100)], [(0.92, 10.0), (0.97, 1000)])
    assert s.confirm_entry(it, {"t": thin}, 5.0).reason.startswith("depth_floor")
    deep = make_book("t", T0, [(0.91, 100)], [(0.92, 100.0)])
    assert s.confirm_entry(it, {"t": deep}, 5.0).ok


# ---------------------------------------------------------------- plum

PLUM_RULES = {"take_profit_price": 0.90, "stop_loss_delta": 0.12, "force_exit_minute": 65,
              "take_profit_delta": 0.05, "hold_above_price": 0.99}


def _sched():
    from polylab.execution.fees import parse_fee_schedule
    return parse_fee_schedule(FEE_V3)


def test_plum_delta_tp_full_holding_net_positive():
    s = Plum({}, None)
    p = pos(entry_price=0.72, shares=6.94, cost_usdc=5.06, exit_rules=dict(PLUM_RULES))
    assert s.delta_tp(p) == pytest.approx(0.77)
    below = make_book("tok", 0, [(0.765, 100)], [(0.775, 100)])
    assert s._plan(p, below, 30, _sched()) is None
    hit = make_book("tok", 0, [(0.77, 100)], [(0.78, 100)])
    it = s._plan(p, hit, 30, _sched())
    assert it.kind == "take_profit" and it.shares == 6.94 and it.min_price == pytest.approx(0.77)
    assert s.confirm_exit(p, it, hit).ok
    assert s.confirm_exit(p, it, below).reason == "below_tp"
    # no partial slice under delta TP: the whole holding's bid VWAP must reach the threshold
    thin = make_book("tok", 0, [(0.80, 3.0), (0.70, 100)], [(0.81, 100)])
    assert s._plan(p, thin, 30, _sched()) is None
    assert s._plan(p, hit, 30, None) is None                       # unknown fee never TPs
    capped = pos(entry_price=0.72, shares=6.94, cost_usdc=5.06,
                 exit_rules={**PLUM_RULES, "take_profit_delta": 0.25})
    assert s.delta_tp(capped) == pytest.approx(0.90)               # min(entry + delta, take_profit_price)


def test_plum_delta_tp_keeps_stop_force_exit_and_hold():
    s = Plum({}, None)
    p = pos(entry_price=0.72, shares=6.94, cost_usdc=5.06, exit_rules=dict(PLUM_RULES))
    low = make_book("tok", 0, [(0.60, 100)], [(0.61, 100)])
    assert s._plan(p, low, 30, _sched()).kind == "stop_loss"
    hit = make_book("tok", 0, [(0.80, 100)], [(0.81, 100)])
    assert s._plan(p, hit, 65, _sched()).kind == "time_exit"       # TP skipped once minute 65 is due
    sure = make_book("tok", 0, [(0.99, 100)], [(0.995, 100)])
    assert s._plan(p, sure, 70, _sched()) is None                  # hold_above suppresses every exit
    it = s._plan(pos(entry_price=0.72, shares=6.94, cost_usdc=5.06,
                     exit_rules={**PLUM_RULES, "hold_above_price": None}), sure, 70, _sched())
    assert it.kind == "time_exit"
    assert s.confirm_exit(p, it, sure).reason == "hold_above_price"


def test_plum_legacy_rules_use_tp_slice():
    s = Plum({}, None)
    legacy = {"take_profit_price": 0.90, "stop_loss_delta": 0.12, "force_exit_minute": 65}
    p = pos(entry_price=0.72, shares=12.0, cost_usdc=8.7, exit_rules=legacy)
    assert s.delta_tp(p) is None
    it = s._plan(p, make_book("tok", 0, [(0.91, 7.0), (0.5, 100)], [(0.92, 100)]), 30)
    assert it.kind == "take_profit" and it.shares == 7.0           # partial slice as before


def test_plum_entry_freezes_delta_rules(env):
    now = T0 + 3600
    env.soccer_game("s", T0)
    mids = {"s-H-Y": 0.40, "s-H-N": 0.59, "s-D-Y": 0.30, "s-D-N": 0.69, "s-A-Y": 0.30, "s-A-N": 0.69}
    for t, m in mids.items():
        b, a = ((0.71, 0.715) if t == "s-H-Y" else (m - 0.005, m + 0.005))
        level_book(env, t, now - 5, b, a)
    env.state("s", now, "1H", "20")
    s = Plum({"take_profit_delta": 0.05, "hold_above_price": 0.99,
              "sport_overrides": {"soccer": {"max_source_minute": 60}}}, variant("plum", ["soccer"]))
    [it] = s.entry_signals(env.view(now), now, Ledger())
    assert it.exit_rules["take_profit_delta"] == 0.05 and it.exit_rules["hold_above_price"] == 0.99
    legacy = Plum({"sport_overrides": {"soccer": {"max_source_minute": 60}}}, variant("plum", ["soccer"]))
    [it] = legacy.entry_signals(env.view(now), now, Ledger())
    assert it.exit_rules["take_profit_delta"] is None
