from __future__ import annotations

from types import SimpleNamespace

import pytest

from polylab.marketview import make_book
from polylab.reports import reminders
from polylab.strategies import build
from polylab.strategies.base import Ledger, PositionView
from polylab.strategies.late_leader import LateLeader
from test_strategy_fixtures import T0, Env, level_book


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path / "root")


def strat(sport="nhl", **params):
    base = {"min_wall_minute": 100, "trigger_price": 0.80, "take_profit_price": 0.96, "pregame_max": 0.70}
    base.update(params)
    v = SimpleNamespace(id="late-leader-t", family="late_leader", sports=[sport], params=base, limits={},
                        stake_usdc=5.0, mode="paper")
    return build(v)


def game(env, sport="nhl", pre=(0.55, 0.45)):
    env.us_game("g", sport, T0)
    env.bar("g-h", T0 - 300, pre[0])
    env.bar("g-a", T0 - 300, pre[1])


def at(env, minute, home):
    ts = T0 + minute * 60
    env.bar("g-h", ts, home)
    env.bar("g-a", ts, round(1 - home, 4))
    level_book(env, "g-h", ts, round(home - 0.005, 4), round(home + 0.005, 4))
    level_book(env, "g-a", ts, round(1 - home - 0.005, 4), round(1 - home + 0.005, 4))
    return ts


def test_family_registered_and_soccer_ignored():
    s = strat()
    assert isinstance(s, LateLeader) and s.family == "late_leader"
    assert s.p("nhl")["trigger_price"] == 0.80 and s.p("nhl")["max_entry_premium"] == 0.03


def test_entry_needs_wall_minute_cross_and_band(env):
    game(env)
    s = strat()
    now = at(env, 90, 0.85)                                   # before T: nothing
    assert s.entry_signals(env.view(now), now, Ledger()) == []
    now = at(env, 101, 0.85)                                  # >= Y but never below Y since T: no crossing
    assert s.entry_signals(env.view(now), now, Ledger()) == []
    at(env, 102, 0.75)                                        # dips below Y after T
    now = at(env, 103, 0.86)                                  # ask .865 > Y + .03: no chasing
    assert s.entry_signals(env.view(now), now, Ledger()) == []
    now = at(env, 104, 0.81)
    [it] = s.entry_signals(env.view(now), now, Ledger())
    assert it.token_id == "g-h" and it.max_price == pytest.approx(0.83) and it.exit_rules["take_profit_price"] == 0.96
    assert it.features["pregame"] == [0.55, 0.45]
    closed = PositionView("p", "closed", "g-h", "c-g", "g", "nhl", now, 0.815, 6.1, 5.0, {})
    assert s.entry_signals(env.view(now), now, Ledger([closed])) == []        # one entry per game
    fresh = {"g-h": make_book("g-h", now, [(0.15, 1000)], [(0.16, 1000)]),
             "g-a": make_book("g-a", now, [(0.84, 1000)], [(0.85, 1000)])}
    assert not s.confirm_entry(it, fresh, 5.0).ok


def test_pregame_filter_game_and_token_scope(env):
    game(env, pre=(0.75, 0.25))                               # favourite 0.75 > X 0.70
    s = strat()
    at(env, 101, 0.70)
    now = at(env, 102, 0.81)
    assert s.entry_signals(env.view(now), now, Ledger()) == []
    assert ("g", "pregame_above_max") in s.skips
    # token scope: the bought team's own closing price (0.75) still too high; the underdog would pass
    assert strat(pregame_scope="token").entry_signals(env.view(now), now, Ledger()) == []
    assert len(strat(pregame_max=None).entry_signals(env.view(now), now, Ledger())) == 1


def test_missing_pregame_price_skips_only_when_filter_set(env):
    env.us_game("g", "nhl", T0)
    at(env, 101, 0.70)
    now = at(env, 102, 0.81)
    assert strat().entry_signals(env.view(now), now, Ledger()) == []
    assert len(strat(pregame_max=None).entry_signals(env.view(now), now, Ledger())) == 1


def test_exits_hold_tp_and_stop(env):
    game(env)
    env.core.execute("UPDATE markets SET fee_schedule=? WHERE condition_id='c-g'", ('{"feesEnabled": false}',))
    env.core.commit()
    s = strat()
    now = T0 + 150 * 60
    hold = PositionView("p", "open", "g-h", "c-g", "g", "nhl", now - 600, 0.815, 6.13, 5.0,
                        {"take_profit_price": None, "max_exit_spread": 0.10, "stop_loss_delta": None})
    level_book(env, "g-h", now, 0.97, 0.975)
    assert s.exit_signals(env.view(now), now, hold) is None                  # Z None: hold to resolution
    tp = PositionView("p", "open", "g-h", "c-g", "g", "nhl", now - 600, 0.815, 6.13, 5.0,
                      {"take_profit_price": 0.96, "max_exit_spread": 0.10, "stop_loss_delta": 0.20})
    it = s.exit_signals(env.view(now), now, tp)
    assert it and it.kind == "take_profit"
    level_book(env, "g-h", now + 60, 0.60, 0.61)
    it = s.exit_signals(env.view(now + 60), now + 60, tp)
    assert it and it.kind == "stop_loss"


def test_reminders_activate_by_date_and_account():
    on = 1_791_849_600                        # 2026-10-13 00:00 UTC
    before = on - 60
    storage = {"projected_30d_gb": 5.9, "total_gb_now": 18.0, "level": "ok", "one_time_gb": 1.7,
               "areas": {"core": {"gb_30d": 1.6, "steady_days": 7}, "raw": {"gb_30d": 0.8, "steady_days": 6}}}
    report = {"health": {"storage": storage},
              "variants": [{"id": "late-leader-paper", "account": None, "mode": "paper"}]}
    ids = {r["id"] for r in reminders.due(report, before)}
    assert "account:late-leader-paper" in ids and "reminder:storage-7d-steady-state" not in ids
    due = {r["id"]: r for r in reminders.due(report, on)}
    st = due["reminder:storage-7d-steady-state"]
    assert st["slack"] and "5.9GB/월" in st["detail"] and "최소 6일" in st["detail"]
    later = {r["id"]: r for r in reminders.due(report, on + 3 * 86400)}
    assert not later["reminder:storage-7d-steady-state"]["slack"]          # Slack line only for 3 days
    report["variants"][0]["account"] = "bear"
    assert "account:late-leader-paper" not in {r["id"] for r in reminders.due(report, on)}
