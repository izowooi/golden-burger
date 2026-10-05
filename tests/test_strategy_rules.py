from __future__ import annotations

from types import SimpleNamespace

import pytest

from polylab.marketview import Book, make_book, walk_asks, walk_bids
from polylab.strategies import build
from polylab.strategies.apricot import Apricot
from polylab.strategies.base import Ledger, PositionView
from polylab.strategies.cherry import Cherry
from polylab.strategies.plum import Plum, soccer_regulation_minute, source_minute, tp_slice
from polylab.strategies.watermelon import Watermelon
from test_strategy_fixtures import T0, Env, level_book


def variant(family, sports, **params):
    return SimpleNamespace(id=f"{family}-t", family=family, sports=sports, params=params, limits={},
                           stake_usdc=5.0, mode="paper")


def pos(**kw):
    base = dict(position_id="p1", status="open", token_id="tok", condition_id="c", game_key="g", sport="soccer",
                opened_at=T0, entry_price=0.95, shares=5.26, cost_usdc=5.0, exit_rules={})
    base.update(kw)
    return PositionView(**base)


# ---------------------------------------------------------------- walks

def test_walk_asks_exact_usd_and_limit():
    w = walk_asks([(0.90, 2.0), (0.95, 100.0)], 5.0)
    assert w.ok and w.limit_price == 0.95
    assert w.shares == pytest.approx(2 + (5 - 1.8) / 0.95)
    assert w.vwap == pytest.approx(5 / w.shares)
    assert not walk_asks([(0.9, 1.0)], 5.0).ok


def test_walk_bids_depth():
    w = walk_bids([(0.5, 3.0), (0.4, 10.0)], 5.0)
    assert w.ok and w.limit_price == 0.4 and w.usd == pytest.approx(1.5 + 0.8)
    assert not walk_bids([(0.5, 3.0)], 5.0).ok


# ---------------------------------------------------------------- watermelon

@pytest.fixture
def env(tmp_path):
    return Env(tmp_path / "root")


def test_watermelon_band_edges(env):
    now = T0 + 3600
    for key, ask in (("g1", 0.92), ("g2", 0.9199), ("g3", 0.999), ("g4", 1.0)):
        env.us_game(key, "nfl", T0)
        level_book(env, f"{key}-h", now - 30, ask - 0.01, ask)
        level_book(env, f"{key}-a", now - 30, 0.01, 0.05)
    s = Watermelon({"prob_min": 0.92, "prob_max": 0.999, "hours_max": 6}, variant("watermelon", ["nfl"]))
    got = {i.game_key for i in s.entry_signals(env.view(now), now, Ledger())}
    assert got == {"g1", "g3"}


def test_watermelon_in_play_age_and_stale_book(env):
    now = T0 + 5 * 3600
    env.us_game("old", "nfl", T0)                   # 5h in play > hours_max 4
    level_book(env, "old-h", now - 10, 0.94, 0.95)
    env.us_game("stale", "nfl", T0 + 3600)
    level_book(env, "stale-h", now - 1000, 0.94, 0.95)   # book older than 120 s
    s = Watermelon({"hours_max": 4}, variant("watermelon", ["nfl"]))
    assert s.entry_signals(env.view(now), now, Ledger()) == []


def test_watermelon_two_results_in_band_blocks_event(env):
    now = T0 + 3600
    env.soccer_game("s", T0)
    level_book(env, "s-H-Y", now - 5, 0.94, 0.95)
    level_book(env, "s-D-Y", now - 5, 0.94, 0.95)
    s = Watermelon({}, variant("watermelon", ["soccer"]))
    assert s.entry_signals(env.view(now), now, Ledger()) == []
    assert ("s", "multiple_results_in_band") in s.skips


def test_watermelon_reentry_rules(env):
    now = T0 + 3600
    env.soccer_game("s", T0)
    level_book(env, "s-A-Y", now - 5, 0.94, 0.95)
    s = Watermelon({}, variant("watermelon", ["soccer"]))
    stopped_home = pos(token_id="s-H-Y", game_key="s", status="closed", closed_at=now - 60, exit_reason="stop_loss")
    assert len(s.entry_signals(env.view(now), now, Ledger([stopped_home]))) == 1
    resolved = pos(token_id="s-H-Y", game_key="s", status="closed", closed_at=now - 60, exit_reason="take_profit")
    assert s.entry_signals(env.view(now), now, Ledger([resolved])) == []
    same_token = pos(token_id="s-A-Y", game_key="s", status="closed", closed_at=now - 60, exit_reason="stop_loss")
    assert s.entry_signals(env.view(now), now, Ledger([same_token])) == []
    unfilled = pos(token_id="s-A-Y", game_key="s", status="unfilled", closed_at=now - 60)
    assert len(s.entry_signals(env.view(now), now, Ledger([unfilled]))) == 1


def test_watermelon_stop_levels():
    s = Watermelon({}, None)
    cat = pos(entry_price=0.97, exit_rules={"stop_price": 0.65, "max_entry_drawdown": 0.30})
    assert s.stop_level(cat) == pytest.approx(0.67)          # relative leg binds above .95
    dog = pos(entry_price=0.97, exit_rules={"stop_price": 0.60, "max_entry_drawdown": 0.30,
                                            "use_stored_stop": True, "stop_price_at_entry": 0.65})
    assert s.stop_level(dog) == 0.65                          # dog keeps the stored stop
    nfl = pos(entry_price=0.93, exit_rules={"stop_price": 0.70, "max_entry_drawdown": 0.30})
    assert s.stop_level(nfl) == 0.70


def test_watermelon_stop_trigger_and_confirm(env):
    now = T0 + 3600
    env.us_game("g", "nfl", T0)
    level_book(env, "g-h", now - 5, 0.70, 0.72)
    s = Watermelon({}, None)
    p = pos(token_id="g-h", sport="nfl", entry_price=0.93, shares=5.37,
            exit_rules={"stop_price": 0.70, "max_entry_drawdown": 0.30})
    it = s.exit_signals(env.view(now), now, p)
    assert it and it.kind == "stop_loss" and it.shares == 5.37
    wide = make_book("g-h", now, [(0.5, 100)], [(0.7, 100)])
    assert not s.confirm_exit(p, it, wide).ok                    # spread .20 > .10
    gap = make_book("g-h", now, [(0.30, 100)], [(0.35, 100)])
    assert s.confirm_exit(p, it, gap).ok                          # gap stop allowed on a tight book
    level_book(env, "g-h", now + 60, 0.71, 0.72)
    assert s.exit_signals(env.view(now + 60), now + 60, p) is None


def _wm_tp_env(env, fee=None):
    env.us_game("g", "nfl", T0)
    if fee is not None:
        env.core.execute("UPDATE markets SET fee_schedule=? WHERE condition_id='c-g'", (fee,))
        env.core.commit()
    return Watermelon({}, None)


FEE_V3 = '{"feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": true}, "feesEnabled": true}'
TP_RULES = {"stop_price": 0.65, "max_entry_drawdown": 0.30, "take_profit_delta": 0.02, "take_profit_cap": 0.99}


def test_watermelon_entry_freezes_tp_rules(env):
    now = T0 + 3600
    env.us_game("g", "nfl", T0)
    level_book(env, "g-h", now - 5, 0.94, 0.95)
    s = Watermelon({"hours_max": 6, "take_profit_delta": 0.03, "take_profit_cap": 0.985}, variant("watermelon", ["nfl"]))
    [it] = s.entry_signals(env.view(now), now, Ledger())
    assert it.exit_rules["take_profit_delta"] == 0.03 and it.exit_rules["take_profit_cap"] == 0.985
    legacy = Watermelon({"hours_max": 6}, variant("watermelon", ["nfl"]))
    [it] = legacy.entry_signals(env.view(now), now, Ledger())
    assert it.exit_rules["take_profit_delta"] is None                  # default: hold to resolution


def test_watermelon_tp_threshold_boundary_and_priority(env):
    s = _wm_tp_env(env, FEE_V3)
    now = T0 + 3600
    p = pos(token_id="g-h", condition_id="c-g", sport="nfl", entry_price=0.92, shares=5.41, cost_usdc=5.0,
            exit_rules=dict(TP_RULES))
    assert s.tp_threshold(p) == pytest.approx(0.94)
    level_book(env, "g-h", now, 0.939, 0.95)
    assert s.exit_signals(env.view(now), now, p) is None                # 0.939 < 0.94: hold
    level_book(env, "g-h", now + 60, 0.94, 0.95)
    it = s.exit_signals(env.view(now + 60), now + 60, p)
    assert it and it.kind == "take_profit" and it.shares == 5.41 and it.min_price == pytest.approx(0.94)
    fresh = make_book("g-h", now + 60, [(0.94, 1000)], [(0.95, 1000)])
    assert s.confirm_exit(p, it, fresh).ok
    dropped = make_book("g-h", now + 60, [(0.93, 1000)], [(0.95, 1000)])
    assert s.confirm_exit(p, it, dropped).reason == "below_tp"
    # TP has priority but a crash still reaches the unchanged stop
    level_book(env, "g-h", now + 120, 0.60, 0.62)
    it = s.exit_signals(env.view(now + 120), now + 120, p)
    assert it and it.kind == "stop_loss"


def test_watermelon_tp_cap_binds(env):
    s = _wm_tp_env(env, FEE_V3)
    now = T0 + 3600
    p = pos(token_id="g-h", condition_id="c-g", sport="nfl", entry_price=0.98, shares=5.1, cost_usdc=5.0,
            exit_rules={**TP_RULES, "take_profit_delta": 0.03})
    assert s.tp_threshold(p) == pytest.approx(0.99)                     # min(1.01, cap .99)
    level_book(env, "g-h", now, 0.985, 0.995)
    assert s.exit_signals(env.view(now), now, p) is None
    level_book(env, "g-h", now + 60, 0.99, 0.995)
    assert s.exit_signals(env.view(now + 60), now + 60, p).kind == "take_profit"


def test_watermelon_tp_rejected_when_fee_negative_or_unknown(env):
    s = _wm_tp_env(env, FEE_V3)
    now = T0 + 3600
    tiny = pos(token_id="g-h", condition_id="c-g", sport="nfl", entry_price=0.92, shares=5.41, cost_usdc=5.0,
               exit_rules={**TP_RULES, "take_profit_delta": 0.001})
    level_book(env, "g-h", now, 0.921, 0.93)
    # 5.41 * .921 = 4.983 minus the .05 sell fee is below the 5.0 cost (buy fee included): no TP
    assert s.exit_signals(env.view(now), now, tiny) is None
    env.core.execute("UPDATE markets SET fee_schedule=NULL WHERE condition_id='c-g'")
    env.core.commit()
    p = pos(token_id="g-h", condition_id="c-g", sport="nfl", entry_price=0.92, shares=5.41, cost_usdc=5.0,
            exit_rules=dict(TP_RULES))
    level_book(env, "g-h", now + 60, 0.97, 0.98)
    assert s.exit_signals(env.view(now + 60), now + 60, p) is None      # fee unknown: never TP
    level_book(env, "g-h", now + 120, 0.60, 0.62)
    assert s.exit_signals(env.view(now + 120), now + 120, p).kind == "stop_loss"


def test_watermelon_tp_needs_full_holding_depth(env):
    s = _wm_tp_env(env, FEE_V3)
    now = T0 + 3600
    p = pos(token_id="g-h", condition_id="c-g", sport="nfl", entry_price=0.92, shares=5.41, cost_usdc=5.0,
            exit_rules=dict(TP_RULES))
    env.book("g-h", now, [(0.97, 2.0), (0.80, 100.0)], [(0.98, 100.0)])
    assert s.exit_signals(env.view(now), now, p) is None                # vwap of full 5.41 < .94; above stop
    env.book("g-h", now + 60, [(0.97, 2.0)], [(0.98, 100.0)])
    assert s.exit_signals(env.view(now + 60), now + 60, p) is None      # cannot sell the whole holding
    env.book("g-h", now + 120, [(0.97, 6.0)], [(0.98, 100.0)])
    assert s.exit_signals(env.view(now + 120), now + 120, p).kind == "take_profit"


def test_watermelon_legacy_rules_never_take_profit(env):
    s = _wm_tp_env(env, FEE_V3)
    now = T0 + 3600
    p = pos(token_id="g-h", condition_id="c-g", sport="nfl", entry_price=0.92, shares=5.41, cost_usdc=5.0,
            exit_rules={"stop_price": 0.65, "max_entry_drawdown": 0.30})
    level_book(env, "g-h", now, 0.99, 0.995)
    assert s.tp_threshold(p) is None and s.exit_signals(env.view(now), now, p) is None


# ---------------------------------------------------------------- apricot

def test_apricot_tick_window(env):
    start = T0
    env.us_game("m", "mlb", start)
    level_book(env, "m-h", start + 300, 0.5, 0.52)             # partial tick ignored
    tick0 = start + 600
    for tok, (b, a) in (("m-h", (0.5, 0.52)), ("m-a", (0.47, 0.49))):
        level_book(env, tok, tick0, b, a)
    s = Apricot({"entry_tick_minute": 90}, variant("apricot", ["mlb"]))
    for minute, expect in ((89.9, 0), (90, 1), (92, 1), (92.1, 0)):
        now = tick0 + int(minute * 60)
        level_book(env, "m-h", now - 10, 0.93, 0.935)
        level_book(env, "m-a", now - 10, 0.06, 0.07)
        out = s.entry_signals(env.view(now), now, Ledger())
        assert len(out) == expect, minute
    assert out == [] or out[0].token_id == "m-h"


def test_apricot_leader_margin_spread_and_band(env):
    start = T0
    env.us_game("m", "mlb", start)
    tick0 = start + 60
    level_book(env, "m-h", tick0, 0.5, 0.51)
    level_book(env, "m-a", tick0, 0.48, 0.49)
    now = tick0 + 90 * 60
    s = Apricot({}, variant("apricot", ["mlb"]))
    level_book(env, "m-h", now, 0.88, 0.95)                     # spread .07 > .05
    level_book(env, "m-a", now, 0.05, 0.06)
    assert s.entry_signals(env.view(now), now, Ledger()) == []
    level_book(env, "m-h", now, 0.88, 0.89)                     # leader ask below .90 band
    assert s.entry_signals(env.view(now), now, Ledger()) == []
    level_book(env, "m-h", now, 0.92, 0.93)
    [it] = s.entry_signals(env.view(now), now, Ledger())
    assert it.token_id == "m-h" and it.min_price == 0.90
    assert s.entry_signals(env.view(now), now, Ledger([pos(game_key="m", status="closed")])) == []
    fresh = {"m-h": make_book("m-h", now, [(0.05, 1000)], [(0.06, 1000)]),
             "m-a": make_book("m-a", now, [(0.92, 1000)], [(0.93, 1000)])}
    assert s.confirm_entry(it, fresh, 5.0).reason == "leader_changed"


def test_apricot_tp_requires_net_positive_and_known_fee(env):
    env.us_game("m", "mlb", T0)
    now = T0 + 7200
    s = Apricot({}, None)
    p = pos(token_id="m-h", condition_id="c-m", entry_price=0.92, shares=5.43, cost_usdc=5.0,
            exit_rules={"take_profit_price": 0.90})
    level_book(env, "m-h", now, 0.95, 0.96)
    assert s.exit_signals(env.view(now), now, p) is None         # fee schedule unknown -> no TP
    env.core.execute("UPDATE markets SET fee_schedule=? WHERE condition_id='c-m'", ('{"feesEnabled": false}',))
    env.core.commit()
    it = s.exit_signals(env.view(now), now, p)
    assert it and it.kind == "take_profit"                       # 5.43*.95=5.158 > 5.0
    level_book(env, "m-h", now + 60, 0.91, 0.92)
    assert s.exit_signals(env.view(now + 60), now + 60, p) is None   # .91*5.43 = 4.94 < cost


# ---------------------------------------------------------------- plum

def test_plum_regulation_minute_decoding():
    assert soccer_regulation_minute("30", "1H") == 30
    assert soccer_regulation_minute("10:30", "2H") == pytest.approx(55.5)
    assert soccer_regulation_minute("50", "2H") == 50
    assert soccer_regulation_minute("90+3", "2H") == 93
    assert soccer_regulation_minute(None, "HT") == 45
    assert soccer_regulation_minute("12", "ET1") is None
    from polylab.marketview import GameState
    st = GameState("g", 1000, "live", True, False, "1H", "58", None, 0, 0)
    assert source_minute(st, 1000 + 120, 1200) == pytest.approx(60)
    assert source_minute(st, 1000 + 1300, 1200) is None


def _plum_soccer(env, now, leader_tok="s-H-Y", leader=(0.71, 0.715)):
    env.soccer_game("s", T0)
    toks = ["s-H-Y", "s-H-N", "s-D-Y", "s-D-N", "s-A-Y", "s-A-N"]
    mids = {"s-H-Y": 0.40, "s-H-N": 0.59, "s-D-Y": 0.30, "s-D-N": 0.69, "s-A-Y": 0.30, "s-A-N": 0.69}
    for t in toks:
        b, a = (leader if t == leader_tok else (mids[t] - 0.005, mids[t] + 0.005))
        level_book(env, t, now - 5, b, a)


def test_plum_soccer_minute_60_inclusive(env):
    now = T0 + 3600
    _plum_soccer(env, now)
    s = Plum({"sport_overrides": {"soccer": {"max_source_minute": 60, "force_exit_minute": 65}}},
             variant("plum", ["soccer"]))
    env.state("s", now, "2H", "60")
    [it] = s.entry_signals(env.view(now), now, Ledger())
    assert it.token_id == "s-H-Y" and it.exit_rules["stop_price_at_entry"] == pytest.approx(0.715 - 0.12, abs=1e-3)
    env.state("s", now + 1, "2H", "60.5")
    assert s.entry_signals(env.view(now + 1), now + 1, Ledger()) == []


def test_plum_requires_source_clock_for_soccer(env):
    now = T0 + 3600
    _plum_soccer(env, now)
    s = Plum({"sport_overrides": {"soccer": {"max_source_minute": 60}}}, variant("plum", ["soccer"]))
    assert s.entry_signals(env.view(now), now, Ledger()) == []
    assert ("s", "source_clock_required") in s.skips


def test_plum_leader_can_be_no_token_and_band(env):
    now = T0 + 3600
    _plum_soccer(env, now, leader_tok="s-D-N", leader=(0.72, 0.725))
    env.state("s", now, "1H", "20")
    s = Plum({"sport_overrides": {"soccer": {"max_source_minute": 60}}}, variant("plum", ["soccer"]))
    [it] = s.entry_signals(env.view(now), now, Ledger())
    assert it.token_id == "s-D-N"


def test_plum_tp_slice_and_exit_priority():
    assert tp_slice([(0.91, 3.0)], 5.5, 0.90) == 0.0               # < 5 profitable shares
    assert tp_slice([(0.91, 100.0)], 5.5, 0.90) == 5.5             # whole position
    assert tp_slice([(0.91, 7.0), (0.5, 100)], 12.0, 0.90) == 7.0  # remainder 5 stays sellable
    assert tp_slice([(0.91, 9.0)], 12.0, 0.90) == 7.0              # capped at position - 5


def test_plum_exit_priority_and_time_exit():
    s = Plum({}, None)
    rules = {"take_profit_price": 0.90, "stop_loss_delta": 0.12, "force_exit_minute": 65}
    p = pos(entry_price=0.72, shares=6.94, exit_rules=rules)
    tp_book = make_book("tok", 0, [(0.91, 100)], [(0.92, 100)])
    assert s._plan(p, tp_book, 30).kind == "take_profit"
    assert s._plan(p, tp_book, 65).kind == "time_exit"             # TP skipped once minute 65 is due
    low = make_book("tok", 0, [(0.60, 100)], [(0.61, 100)])
    assert s._plan(p, low, 30).kind == "stop_loss"                 # .60 <= .72-.12
    assert s._plan(p, low, 70).kind == "stop_loss"
    mid = make_book("tok", 0, [(0.70, 100)], [(0.71, 100)])
    assert s._plan(p, mid, 30) is None
    nfl = pos(entry_price=0.72, shares=6.94, exit_rules={**rules, "force_exit_minute": None})
    assert s._plan(nfl, mid, None) is None


# ---------------------------------------------------------------- cherry

def test_cherry_exit_chain():
    s = Cherry({}, None)
    rules = {"take_profit_percent": 0.20, "stop_loss_percent": -0.08, "trailing_enabled": True,
             "trailing_percent": 0.15}
    p = pos(entry_price=0.80, exit_rules=rules)
    assert s.decide(p, 0.736, 0.80)[0] == "stop_loss"               # -8% exactly
    assert s.decide(p, 0.96, 0.96)[0] == "take_profit"
    assert s.decide(p, 0.80, 0.95)[0] == "trailing_stop"           # .80 < .95*.85=.8075
    assert s.decide(p, 0.85, 0.90) is None


# ---------------------------------------------------------------- 2026-10-06 per-sport timing / apricot stop

def test_watermelon_wall_minute_window_us_only(env):
    env.us_game("w", "nba", T0)
    s = Watermelon({"prob_min": 0.90, "hours_max": 5, "min_wall_minute": 60, "max_wall_minute": 120},
                   variant("watermelon", ["nba"]))
    for minute, expect in ((59, 0), (60, 1), (120, 1), (121, 0)):
        now = T0 + minute * 60
        level_book(env, "w-h", now - 10, 0.93, 0.94)
        level_book(env, "w-a", now - 10, 0.05, 0.06)
        assert len(s.entry_signals(env.view(now), now, Ledger())) == expect, minute


def test_plum_min_wall_minute(env):
    env.us_game("p", "nhl", T0)
    s = Plum({"min_wall_minute": 30, "max_wall_minute": 120}, variant("plum", ["nhl"]))
    for minute, expect in ((29, 0), (30, 1), (121, 0)):
        now = T0 + minute * 60
        level_book(env, "p-h", now - 10, 0.70, 0.71)
        level_book(env, "p-a", now - 10, 0.28, 0.29)
        assert len(s.entry_signals(env.view(now), now, Ledger())) == expect, minute


def test_apricot_optional_stop_loss(env):
    env.us_game("m", "nba", T0)
    env.core.execute("UPDATE markets SET fee_schedule=? WHERE condition_id='c-m'", ('{"feesEnabled": false}',))
    env.core.commit()
    now = T0 + 7200
    s = Apricot({}, None)
    legacy = pos(token_id="m-h", condition_id="c-m", entry_price=0.92, shares=5.43, cost_usdc=5.0,
                 exit_rules={"take_profit_price": 0.96})
    level_book(env, "m-h", now, 0.70, 0.71)
    assert s.exit_signals(env.view(now), now, legacy) is None       # no stop configured: hold
    p = pos(token_id="m-h", condition_id="c-m", entry_price=0.92, shares=5.43, cost_usdc=5.0,
            exit_rules={"take_profit_price": 0.96, "max_exit_spread": 0.10, "stop_loss_delta": 0.20})
    level_book(env, "m-h", now, 0.73, 0.74)
    assert s.exit_signals(env.view(now), now, p) is None            # 0.73 > trigger 0.72
    level_book(env, "m-h", now + 60, 0.72, 0.73)
    it = s.exit_signals(env.view(now + 60), now + 60, p)
    assert it and it.kind == "stop_loss" and it.shares == 5.43
    book = make_book("m-h", now + 60, [(0.72, 100)], [(0.73, 100)])
    assert s.confirm_exit(p, it, book).ok
    assert s.confirm_exit(p, it, make_book("m-h", now + 60, [(0.80, 100)], [(0.81, 100)])).reason == "recovered_above_stop"
    assert s.confirm_exit(p, it, make_book("m-h", now + 60, [(0.60, 100)], [(0.75, 100)])).reason == "exit_spread_too_wide"
