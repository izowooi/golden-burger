"""goal_over family (goal-over-all baseline): entry window/band/scope, in-play gate, TP/SL exits, paper tick."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from polylab.strategies import build
from polylab.strategies.base import Ledger, PositionView
from polylab.strategies.goal_over import GoalOver
from test_strategy_fixtures import T0, Env

KICK = T0 + 6 * 3600
LEAGUES = ["epl", "mls"]


def strat(**params):
    v = SimpleNamespace(id="goal-over-t", family="goal_over", sports=["soccer"], stake_usdc=5.0, limits={},
                        mode="paper", params={"leagues": LEAGUES, **params})
    return build(v)


def world(tmp_path, ask=0.93, league="epl"):
    env = Env(tmp_path / "rt")
    env.game("g1", "soccer", KICK, "Arsenal", "Chelsea", status="scheduled", league=league)
    env.market("ml1", "g1", "moneyline", [("h1", "Yes", "home"), ("hn", "No", "no")], volume=90000)
    env.market("ou1", "g1", "total", [("ov1", "Over", "over"), ("un1", "Under", "under")], volume=15000,
               fee_schedule={"feesEnabled": False})
    env.market("ou2", "g1", "total", [("ov2", "Over", "over"), ("un2", "Under", "under")], volume=15000)
    env.core.execute("UPDATE markets SET line=0.5 WHERE condition_id='ou1'")
    env.core.execute("UPDATE markets SET line=1.5 WHERE condition_id='ou2'")
    env.game("g2", "soccer", KICK + 600, "X", "Y", status="scheduled", league="epl")      # no Total 0.5 market
    env.market("ml2", "g2", "moneyline", [("h2", "Yes", "home"), ("h2n", "No", "no")])
    env.core.commit()
    now = KICK - 10 * 60
    env.book("ov1", now - 30, [(ask - 0.01, 500)], [(ask, 500)])
    return env, now


def test_enters_over_0_5_in_window_and_band(tmp_path):
    env, now = world(tmp_path)
    s = strat()
    intents = s.entry_signals(env.view(now), now, Ledger())
    assert [i.token_id for i in intents] == ["ov1"]                     # Over 0.5 only, not the 1.5 line
    i = intents[0]
    assert i.outcome_label == "Over 0.5" and i.min_price == 0.5 and i.max_price == 0.985
    assert i.features["implied_p00"] == pytest.approx(0.07) and i.exit_rules["hold_to_resolution"]
    assert ("g2", "no_over_0_5_market") in s.skips
    assert s.confirm_entry(i, {"ov1": env.view(now).book("ov1", now)}, 5.0).ok
    traded = Ledger([PositionView("p", "open", "ov1", "ou1", "g1", "soccer", now - 60, 0.93, 5.3, 5.0)])
    assert strat().entry_signals(env.view(now), now, traded) == []       # one entry per game


@pytest.mark.parametrize("minutes_before,expected", [(90, 0), (61, 0), (59, 1), (5, 1), (4, 0)])
def test_entry_window(tmp_path, minutes_before, expected):
    env, _ = world(tmp_path)
    now = KICK - minutes_before * 60
    env.book("ov1", now - 30, [(0.92, 500)], [(0.93, 500)])
    assert len(strat().entry_signals(env.view(now), now, Ledger())) == expected


def test_price_band_scope_and_fresh_book(tmp_path):
    env, now = world(tmp_path, ask=0.99)
    s = strat()
    assert s.entry_signals(env.view(now), now, Ledger()) == [] and ("g1", "price_out_of_band") in s.skips
    env2, now2 = world(tmp_path / "b", league="fif")
    assert strat().entry_signals(env2.view(now2), now2, Ledger()) == []  # league outside params.leagues
    env3, now3 = world(tmp_path / "c")
    s3 = strat()
    later = now3 + 200                                                    # book older than book_max_age_s 120
    assert s3.entry_signals(env3.view(later), later, Ledger()) == [] and ("g1", "no_fresh_over_book") in s3.skips


def test_in_play_entry_is_off_by_default_and_gated_on_fresh_0_0(tmp_path):
    env, _ = world(tmp_path)
    env.core.execute("UPDATE games SET status='live' WHERE game_key='g1'")
    env.core.commit()
    now = KICK + 10 * 60
    env.book("ov1", now - 30, [(0.86, 500)], [(0.87, 500)])
    assert strat().entry_signals(env.view(now), now, Ledger()) == []
    s = strat(allow_in_play=True)
    assert s.entry_signals(env.view(now), now, Ledger()) == []            # no game state -> refused
    assert ("g1", "in_play_state_not_fresh_0_0") in s.skips
    env.state("g1", now - 60, "1H", "9:00", minute=9)
    env.core.execute("UPDATE game_states SET home_score=0, away_score=0 WHERE game_key='g1'")
    env.core.commit()
    intents = strat(allow_in_play=True).entry_signals(env.view(now), now, Ledger())
    assert len(intents) == 1 and intents[0].features["in_play"] is True
    env.core.execute("UPDATE game_states SET home_score=1 WHERE game_key='g1'")
    env.core.commit()
    assert strat(allow_in_play=True).entry_signals(env.view(now), now, Ledger()) == []


def pos(rules, entry=0.93, shares=5.37, cost=5.0):
    return PositionView("p", "open", "ov1", "ou1", "g1", "soccer", KICK - 600, entry, shares, cost,
                        exit_rules=rules)


def test_threshold_exits(tmp_path):
    env, _ = world(tmp_path)
    now = KICK + 3600
    env.book("ov1", now - 20, [(0.995, 500)], [(0.997, 500)])
    s = strat()
    view = env.view(now)
    assert s.exit_signals(view, now, pos({"take_profit_price": None, "stop_loss_price": None})) is None
    tp = pos({"take_profit_price": 0.99, "stop_loss_price": None})
    ex = s.exit_signals(view, now, tp)
    assert ex.kind == "take_profit" and s.confirm_exit(tp, ex, view.book("ov1", now)).ok
    # not net-positive: bought at 0.999 -> TP never fires even above the threshold
    assert s.exit_signals(view, now, pos({"take_profit_price": 0.99}, entry=0.999, shares=5.0, cost=5.0)) is None
    env.book("ov1", now + 40, [(0.30, 500)], [(0.33, 500)])              # no goal, late: Over decays
    later = now + 60
    sl = pos({"take_profit_price": None, "stop_loss_price": 0.4})
    ex = s.exit_signals(env.view(later), later, sl)
    assert ex.kind == "stop_loss" and s.confirm_exit(sl, ex, env.view(later).book("ov1", later)).ok
    env.book("ov1", later + 30, [(0.30, 500)], [(0.60, 500)])
    assert s.confirm_exit(sl, ex, env.view(later + 40).book("ov1", later + 40)).reason == "stop_spread_too_wide"


def test_unknown_fee_never_takes_profit(tmp_path):
    env, _ = world(tmp_path)
    env.core.execute("UPDATE markets SET fee_schedule=NULL WHERE condition_id='ou1'")
    env.core.commit()
    now = KICK + 3600
    env.book("ov1", now - 20, [(0.995, 500)], [(0.997, 500)])
    assert strat().exit_signals(env.view(now), now, pos({"take_profit_price": 0.99})) is None


def test_registry_yaml_has_tunable_thresholds():
    from pathlib import Path

    from polylab.registry import load_variant

    v = load_variant(Path("strategies/goal-over-all.yaml"))
    assert v.mode in ("paper", "live") and v.family == "goal_over" and v.stake_usdc in (5.0, 10.0, 25.0, 50.0, 100.0)
    # autopilot may switch thresholds on inside bounds; only structure is pinned here
    for k in ("take_profit_price", "stop_loss_price"):
        assert v.params[k] is None or v.bounds[k][0] <= v.params[k] <= v.bounds[k][1]
    assert {"take_profit_price", "stop_loss_price", "price_min", "price_max", "entry_minutes_before_max",
            "entry_minutes_before_min"} <= set(v.bounds)
    assert v.limits["max_positions"] > 0 and v.limits["max_sells_per_cycle"] > 0
    assert isinstance(build(v), GoalOver)
    # the one-line live switch builds (accounts are resolved by the engine, not the family)
    assert isinstance(build(SimpleNamespace(**{**v.__dict__, "mode": "live", "account": "x"})), GoalOver)


def test_tick_paper_end_to_end(tmp_path):
    from polylab import db
    from polylab.engine.tick import run as tick

    env, now = world(tmp_path)
    reg = tmp_path / "reg"
    reg.mkdir()
    real = yaml.safe_load(open("strategies/goal-over-all.yaml"))
    # tunable values (AI retro / owner) are fixed here so a yaml retune cannot break this test
    real["params"].update(entry_minutes_before_max=4320, entry_minutes_before_min=5, price_min=0.5, price_max=0.985,
                          take_profit_price=None, stop_loss_price=None, take_profit_delta=0.02, take_profit_pct=None,
                          maker_ttl_minutes=60, maker_reprice_ticks=2)
    real["params"]["leagues"] = LEAGUES
    real["params"]["order_style"] = "taker"          # FOK path; maker lifecycle: tests/test_maker_orders.py
    real.update(mode="paper", account=None, stake_usdc=5.0)
    (reg / "goal-over-all.yaml").write_text(yaml.safe_dump(real, allow_unicode=True))
    out = tick(env.paths, registry_dir=reg, poll=False, now=now)
    assert out["ok"], out
    rows = [dict(r) for r in db.strategy(env.paths, "goal-over-all").execute("SELECT * FROM positions")]
    assert len(rows) == 1 and rows[0]["mode"] == "paper" and rows[0]["entry_price"] == pytest.approx(0.93)
    env.resolve("ou1", 0, KICK + 3600)
    tick(env.paths, registry_dir=reg, poll=False, now=KICK + 2 * 3600)
    row = dict(db.strategy(env.paths, "goal-over-all").execute("SELECT * FROM positions").fetchone())
    assert row["status"] == "resolved" and row["realized_pnl"] == pytest.approx(5 / 0.93 - 5, abs=1e-3)


def test_pct_take_profit_stop_loss_and_hold_above(tmp_path):
    env, _ = world(tmp_path)
    s = strat()
    rules = {"take_profit_pct": 0.02, "stop_loss_pct": 0.10, "hold_above_price": 0.99}
    now = KICK + 600
    # +2% over a 0.93 entry (target 0.9486) and still below 0.99 -> take profit
    env.book("ov1", now - 20, [(0.96, 500)], [(0.965, 500)])
    p = pos(rules)
    ex = s.exit_signals(env.view(now), now, p)
    assert ex.kind == "take_profit" and s.confirm_exit(p, ex, env.view(now).book("ov1", now)).ok
    # a goal pushes the bid to 0.995 -> hold to resolution, neither TP nor SL
    t2 = now + 60
    env.book("ov1", t2 - 10, [(0.995, 500)], [(0.997, 500)])
    assert s.exit_signals(env.view(t2), t2, p) is None
    assert s.confirm_exit(p, ex, env.view(t2).book("ov1", t2)).reason == "hold_above_price"
    # +1% only -> nothing
    t3 = t2 + 60
    env.book("ov1", t3 - 10, [(0.94, 500)], [(0.945, 500)])
    assert s.exit_signals(env.view(t3), t3, p) is None
    # -10% (0.93 -> 0.837) -> stop loss
    t4 = t3 + 60
    env.book("ov1", t4 - 10, [(0.83, 500)], [(0.86, 500)])
    ex = s.exit_signals(env.view(t4), t4, p)
    assert ex.kind == "stop_loss" and s.confirm_exit(p, ex, env.view(t4).book("ov1", t4)).ok
    # entry 0.975: +2% target is above 0.99, so TP can never fire before the hold zone
    hi = pos(rules, entry=0.975, shares=5.13, cost=5.0)
    t5 = t4 + 60
    env.book("ov1", t5 - 10, [(0.989, 500)], [(0.99, 500)])
    assert s.exit_signals(env.view(t5), t5, hi) is None


def test_absolute_take_profit_delta(tmp_path):
    env, _ = world(tmp_path)
    s = strat()
    rules = {"take_profit_delta": 0.02, "stop_loss_pct": 0.10, "hold_above_price": 0.99}
    now = KICK - 3600
    env.book("ov1", now - 20, [(0.965, 500)], [(0.97, 500)])
    assert s.exit_signals(env.view(now), now, pos(rules, entry=0.95, shares=5.26, cost=5.0)) is None  # 0.965 < 0.97
    t2 = now + 60
    env.book("ov1", t2 - 10, [(0.97, 500)], [(0.975, 500)])
    ex = s.exit_signals(env.view(t2), t2, pos(rules, entry=0.95, shares=5.26, cost=5.0))
    assert ex is not None and ex.kind == "take_profit"                                                  # 0.95 -> 0.97
    # 0.97 entry: target 0.99 sits in the hold zone -> held to resolution; 0.98 likewise
    for entry in (0.97, 0.98):
        assert s.exit_signals(env.view(t2), t2, pos(rules, entry=entry, shares=5.1, cost=5.0)) is None
