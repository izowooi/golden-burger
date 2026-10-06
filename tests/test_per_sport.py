"""Per-sport mode / stake / params / ladder (owner decision 2026-10-05 `sports3:per-sport`)."""

from __future__ import annotations

import json

import pytest
import yaml

from polylab import db, registry, settings
from polylab.autopilot import retro
from polylab.autopilot.validator import Context, Facts, Rules, apply_to_variant, validate
from polylab.engine.tick import plan_legs, run
from polylab.execution.clob import Clob
from polylab.risk import ladder
from test_execution_flows import FakeClient
from test_strategy_fixtures import T0, Env, level_book


def write(d, vid, data):
    d.mkdir(parents=True, exist_ok=True)
    base = {"id": vid, "family": "watermelon", "hypothesis": "t", "account": None, "mode": "paper",
            "sports": ["nfl"], "stake_usdc": 5, "params": {"hours_max": 6}, "bounds": {},
            "limits": {"max_positions": 5, "max_open_usdc": 50, "daily_loss_stop_usdc": 20, "max_new_per_cycle": 5}}
    base.update(data)
    (d / f"{vid}.yaml").write_text(yaml.safe_dump(base, sort_keys=False))
    return d / f"{vid}.yaml"


# ------------------------------------------------------------------ registry

def test_list_form_is_unchanged(tmp_path):
    p = write(tmp_path, "legacy", {"mode": "live", "account": "cat", "sports": ["soccer", "nfl"], "stake_usdc": 10})
    v = registry.load_variant(p)
    assert not v.per_sport and v.sports == ["soccer", "nfl"]
    assert v.sport_mode("nfl") == "live" and v.sport_stake("nfl") == 10.0 and v.effective_mode == "live"
    assert v.to_yaml()["sports"] == ["soccer", "nfl"]
    registry.save_variant(v, tmp_path)
    assert yaml.safe_load(p.read_text())["sports"] == ["soccer", "nfl"]


def test_mapping_form_modes_stakes_and_round_trip(tmp_path):
    p = write(tmp_path, "ps", {"mode": "live", "account": "cat", "stake_usdc": 5, "sports": {
        "soccer": {"mode": "live", "stake_usdc": 10},
        "nba": {"mode": "paper"},
        "nhl": {"mode": "off", "stake_usdc": 25, "limits": {"max_open_usdc": 30}}}})
    v = registry.load_variant(p)
    assert v.per_sport and v.sports == ["soccer", "nba", "nhl"]
    assert [v.sport_mode(s) for s in v.sports] == ["live", "paper", "off"]
    assert [v.sport_stake(s) for s in v.sports] == [10.0, 5.0, 25.0]
    assert v.sport_limits("nhl") == {"max_open_usdc": 30} and v.sport_limits("nba") == {}
    assert v.effective_mode == "live" and v.primary_sport() == "soccer"
    registry.save_variant(v, tmp_path)
    again = registry.load_variant(p)
    assert again.sport_settings == {"soccer": {"mode": "live", "stake_usdc": 10.0},
                                    "nba": {"mode": "paper", "stake_usdc": 5.0},
                                    "nhl": {"mode": "off", "stake_usdc": 25.0, "limits": {"max_open_usdc": 30}}}
    # the master switch caps every sport
    v.mode = "paper"
    assert v.sport_mode("soccer") == "paper" and v.effective_mode == "paper"
    v.mode = "off"
    assert v.sport_mode("soccer") == "off"


def test_mapping_form_rejects_bad_stake_and_keys(tmp_path):
    with pytest.raises(ValueError, match="not on ladder"):
        registry.load_variant(write(tmp_path, "bad", {"sports": {"nba": {"stake_usdc": 7}}}))
    with pytest.raises(ValueError, match="must be a mapping"):
        registry.load_variant(write(tmp_path, "bad2", {"sports": {"nba": {"prob_min": 0.9}}}))


def test_effective_params_merge_sport_overrides():
    params = {"prob_min": 0.93, "sport_overrides": {"nba": {"prob_min": 0.9, "take_profit_delta": None}}}
    assert registry.effective_params(params, "nba") == {"prob_min": 0.9, "take_profit_delta": None}
    assert registry.effective_params(params, "soccer") == {"prob_min": 0.93}


def test_repo_per_sport_variants_cover_us_sports():
    vs = {v.id: v for v in registry.load_all(include_off=True)}
    # Structure only: modes and stakes move over time (ladder, promotion gate, owner decisions such as
    # 2026-10-06 apricot:nfl-live), so they are not pinned here.
    for vid in ("watermelon-cat", "watermelon-dog", "apricot-eco", "apricot-fruit", "plum-king", "plum-queen"):
        v = vs[vid]
        assert v.per_sport and {"mlb", "nba", "nhl", "nfl"} <= set(v.sports), vid
        assert all(v.sport_stake(s) in registry.STAKE_LADDER for s in v.sports), vid
        assert all(v.sport_mode(s) in registry.MODES for s in v.sports), vid
    # NFL moved into the family variants: the NFL-only paper variants are off
    assert vs["watermelon-us-paper"].mode == "off" and vs["plum-us-paper"].mode == "off"


# ------------------------------------------------------------------ engine

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.delenv("POLYLAB_KILL", raising=False)
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "accounts.env").write_text(
        "POLYBOT_CAT__POLYMARKET_PRIVATE_KEY=0xabc\nPOLYBOT_CAT__POLYMARKET_FUNDER_ADDRESS=0xdef\n")
    e = Env(tmp_path / "root")
    e.us_game("g", "nfl", T0)
    e.us_game("n", "nba", T0)
    e.core.execute("UPDATE markets SET fee_schedule='{\"feesEnabled\": false}'")
    e.core.commit()
    return e


def rows(env, vid):
    conn = db.strategy(env.paths, vid)
    return [dict(r) for r in conn.execute("SELECT * FROM positions ORDER BY opened_at")]


def client():
    book = {"bids": [{"price": "0.94", "size": "500"}], "asks": [{"price": "0.95", "size": "500"}]}
    return FakeClient(book={"g-h": book, "n-h": book}, fd={"r": 0.0, "e": 1, "to": True})


def test_engine_runs_each_sport_at_its_own_mode_and_stake(env, tmp_path):
    reg = tmp_path / "reg"
    write(reg, "wm", {"mode": "live", "account": "cat", "sports": {
        "nfl": {"mode": "live", "stake_usdc": 5}, "nba": {"mode": "paper", "stake_usdc": 10}}})
    now = T0 + 3600
    level_book(env, "g-h", now - 10, 0.94, 0.95)
    level_book(env, "n-h", now - 10, 0.94, 0.95)
    c = client()
    s = run(env.paths, registry_dir=reg, poll=False, now=now, clob_factory=lambda _: Clob(c))
    assert s["ok"], s
    legs = {(r["mode"], tuple(r["sports"])) for r in s["variants"]}
    assert legs == {("live", ("nfl",)), ("paper", ("nba",))}
    by_sport = {p["sport"]: p for p in rows(env, "wm")}
    assert by_sport["nfl"]["mode"] == "live" and by_sport["nfl"]["stake_usdc"] == 5.0
    assert by_sport["nba"]["mode"] == "paper" and by_sport["nba"]["stake_usdc"] == 10.0
    assert len(c.posted) == 1                          # only the live sport reached the venue
    # two legs a minute must not churn param versions
    for k in range(1, 3):
        run(env.paths, registry_dir=reg, poll=False, now=now + 60 * k, clob_factory=lambda _: Clob(c))
    conn = db.strategy(env.paths, "wm")
    modes = [r[0] for r in conn.execute("SELECT mode FROM param_versions ORDER BY version")]
    assert sorted(modes) == ["live", "paper"]


def test_sport_moved_to_paper_keeps_managing_its_live_positions(env, tmp_path):
    reg = tmp_path / "reg"
    write(reg, "wm", {"mode": "live", "account": "cat", "sports": {"nfl": {"mode": "live"}, "nba": {"mode": "off"}}})
    now = T0 + 3600
    level_book(env, "g-h", now - 10, 0.94, 0.95)
    c = client()
    run(env.paths, registry_dir=reg, poll=False, now=now, clob_factory=lambda _: Clob(c))
    [p] = rows(env, "wm")
    assert p["mode"] == "live" and p["status"] == "pending"
    write(reg, "wm", {"mode": "live", "account": "cat", "sports": {"nfl": {"mode": "paper"}, "nba": {"mode": "off"}}})
    v = registry.load_variant(reg / "wm.yaml")
    assert plan_legs(env.paths, v, False) == [("live", []), ("paper", ["nfl"])]
    c.trades["t1"]["status"] = "CONFIRMED"
    s = run(env.paths, registry_dir=reg, poll=False, now=now + 60, clob_factory=lambda _: Clob(c))
    assert s["ok"], s
    live = [q for q in rows(env, "wm") if q["mode"] == "live"]
    assert len(live) == 1 and live[0]["status"] == "open"     # reconciled by the exposure-only live leg
    assert len(c.posted) == 1                                 # and no new live entry
    # no live exposure -> no live leg, so no credentials are needed
    write(reg, "other", {"mode": "live", "account": "nobody", "sports": {"nfl": {"mode": "paper"}}})
    assert plan_legs(env.paths, registry.load_variant(reg / "other.yaml"), False) == [("paper", ["nfl"])]


def test_per_sport_limits_skip_only_that_sport(env, tmp_path):
    reg = tmp_path / "reg"
    write(reg, "wm", {"mode": "paper", "sports": {
        "nfl": {"mode": "paper"}, "nba": {"mode": "paper", "limits": {"max_open_usdc": 1}}}})
    now = T0 + 3600
    level_book(env, "g-h", now - 10, 0.94, 0.95)
    level_book(env, "n-h", now - 10, 0.94, 0.95)
    s = run(env.paths, registry_dir=reg, poll=False, now=now)
    assert s["ok"], s
    assert [p["sport"] for p in rows(env, "wm")] == ["nfl"]
    assert s["variants"][0]["skipped"].get("nba:max_open_usdc") == 1


def test_backtest_simulates_every_non_off_sport(tmp_path):
    from polylab.analysis.backtest import active_steps  # noqa: PLC0415
    v = registry.load_variant(write(tmp_path, "ps", {"mode": "live", "account": "cat", "sports": {
        "nfl": {"mode": "live"}, "nba": {"mode": "paper"}, "nhl": {"mode": "off"}}}))
    import copy  # noqa: PLC0415
    sim = copy.copy(v)
    sim.mode = "paper"
    assert [s for s in sim.sports if sim.sport_mode(s) != "off"] == ["nfl", "nba"]
    assert callable(active_steps)


# ------------------------------------------------------------------ ladder

def _ledger_with_trades(paths, vid, sport_trades: dict, now: int):
    from polylab.execution.ledger import open_ledger  # noqa: PLC0415
    lg = open_ledger(paths, vid)
    i = 0
    for sport, (n, pnl) in sport_trades.items():
        for _ in range(n):
            i += 1
            lg.conn.execute(
                "INSERT INTO positions(position_id, mode, param_version, stake_usdc, sport, condition_id, token_id, "
                "opened_at, status, closed_at, cost_usdc, realized_pnl) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"p{i}", "live", 1, 5.0, sport, "c", "t", now - 10 * 86400, "closed", now - 5 * 86400 + i, 5.0,
                 pnl + (0.01 * (i % 3))))
    lg.conn.commit()
    return lg.conn


def test_ladder_is_evaluated_per_sport(tmp_path):
    paths = settings.Paths(tmp_path / "root").ensure()
    now = T0
    conn = _ledger_with_trades(paths, "wm", {"soccer": (25, 0.3), "nba": (25, -0.4)}, now)
    soccer = ladder.evaluate_ledger(conn, 5.0, "live", now, sport="soccer")
    nba = ladder.evaluate_ledger(conn, 5.0, "live", now, sport="nba")
    both = ladder.evaluate_ledger(conn, 5.0, "live", now)
    assert soccer.action == "promote" and soccer.to_usdc == 10.0 and soccer.evidence["trades_at_tier"] == 25
    assert nba.action == "hold" and nba.evidence["trades_at_tier"] == 25
    assert both.evidence["trades_at_tier"] == 50
    # a sport's own stake event starts its cooldown; another sport is unaffected; legacy NULL counts for all
    ladder.record_decision(conn, soccer, now, sport="soccer")
    assert ladder.evaluate_ledger(conn, 10.0, "live", now + 60, sport="soccer").reason == "cooldown"
    assert ladder.last_stake_change(conn, "nba") is None
    conn.execute("INSERT INTO stake_events(ts, to_usdc, reason) VALUES(?, 5, 'legacy')", (now + 120,))
    assert ladder.last_stake_change(conn, "nba") == now + 120


def test_old_ledger_gets_sport_column(tmp_path):
    import sqlite3  # noqa: PLC0415
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE stake_events (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, from_usdc REAL, "
                 "to_usdc REAL NOT NULL, from_mode TEXT, to_mode TEXT, reason TEXT NOT NULL, evidence TEXT)")
    conn.execute("INSERT INTO stake_events(ts, to_usdc, reason) VALUES(5, 5, 'x')")
    assert ladder.last_stake_change(conn, "nba") == 5        # read path works without the column
    ladder.ensure_sport_column(conn)
    ladder.ensure_sport_column(conn)                          # idempotent
    assert ladder.has_sport_column(conn)


def test_retro_ladder_changes_are_per_sport():
    report = {"variants": [
        {"id": "wm", "mode": "live", "per_sport": True, "ladder": {"available": True, "action": "promote"},
         "sports_detail": [
             {"sport": "soccer", "mode": "live", "ladder": {"available": True, "action": "promote",
                                                            "next_stake_usdc": 10.0, "reason": "tier gates passed"}},
             {"sport": "nba", "mode": "live", "ladder": {"available": True, "action": "paper",
                                                         "reason": "40 trades at floor with cumulative loss"}},
             {"sport": "nhl", "mode": "paper", "ladder": {"available": True, "action": "hold"}}]},
        {"id": "legacy", "mode": "live", "per_sport": False, "sports_detail": [],
         "ladder": {"available": True, "action": "demote", "next_stake_usdc": 5.0, "reason": "x"}}]}
    ch = retro.ladder_changes(report)["changes"]
    assert [(c["variant_id"], c.get("sport"), c["change"]) for c in ch] == [
        ("wm", "soccer", "stake"), ("wm", "nba", "mode"), ("legacy", None, "stake")]


# ------------------------------------------------------------------ validator

def _ctx(tmp_path, **facts):
    p = write(tmp_path, "wm", {"mode": "live", "account": "cat", "sports": {
        "soccer": {"mode": "live"}, "nba": {"mode": "live"}, "nhl": {"mode": "paper"}},
        "params": {"prob_min": 0.93, "hours_max": 4.0, "sport_overrides": {"nba": {"prob_min": 0.90}}},
        "bounds": {"prob_min": [0.85, 0.99, 0.01], "hours_max": [2.0, 6.0, 0.5],
                   "sport_overrides.nba.prob_min": [0.85, 0.97, 0.01]}})
    v = registry.load_variant(p)
    by_sport = {s: Facts(trades_at_version=30, promote_ok=facts.get(f"{s}_promote")) for s in v.sports}
    return Context({"wm": v}, {"wm": Facts(trades_at_version=30, by_sport=by_sport)}, now=T0)


def _one(ctx, change, source="ai", rules=None):
    return validate({"changes": [change]}, ctx, rules or Rules(), source)[0]


def test_validator_sport_params_map_to_overrides(tmp_path):
    ctx = _ctx(tmp_path)
    d = _one(ctx, {"variant_id": "wm", "sport": "nba", "change": "params", "values": {"prob_min": 0.91},
                   "rationale": "r"})
    assert d.accepted, d.reason
    v = apply_to_variant(d, ctx)
    assert v.params["sport_overrides"]["nba"]["prob_min"] == 0.91 and v.params["prob_min"] == 0.93
    # nhl has no override: current value is the base 0.93, bounds fall back to the base name
    d = _one(ctx, {"variant_id": "wm", "sport": "nhl", "change": "params", "values": {"prob_min": 0.94},
                   "rationale": "r"})
    assert d.accepted, d.reason
    assert apply_to_variant(d, ctx).params["sport_overrides"]["nhl"] == {"prob_min": 0.94}
    bad = _one(ctx, {"variant_id": "wm", "sport": "nhl", "change": "params", "values": {"prob_min": 0.96},
                     "rationale": "r"})
    assert not bad.accepted and "max_step" in bad.reason
    no_sport = _one(ctx, {"variant_id": "wm", "change": "params", "values": {"prob_min": 0.94}, "rationale": "r"})
    assert not no_sport.accepted and "name the sport" in no_sport.reason
    dotted = _one(ctx, {"variant_id": "wm", "change": "params", "values": {"sport_overrides.nba.prob_min": 0.89},
                        "rationale": "r"})
    assert dotted.accepted and dotted.change["sport"] == "nba", dotted.reason


def test_validator_sport_stake_and_mode(tmp_path):
    ctx = _ctx(tmp_path, soccer_promote=True)
    up = _one(ctx, {"variant_id": "wm", "sport": "soccer", "change": "stake", "values": {"stake_usdc": 10},
                    "rationale": "r"})
    assert up.accepted, up.reason
    v = apply_to_variant(up, ctx)
    assert v.sport_stake("soccer") == 10.0 and v.sport_stake("nba") == 5.0 and v.stake_usdc == 5.0
    assert v.limits["max_open_usdc"] == 100.0          # variant caps follow the largest sport tier
    nba_up = _one(ctx, {"variant_id": "wm", "sport": "nba", "change": "stake", "values": {"stake_usdc": 10},
                        "rationale": "r"})
    assert not nba_up.accepted and "ladder gate" in nba_up.reason
    nhl_up = _one(ctx, {"variant_id": "wm", "sport": "nhl", "change": "stake", "values": {"stake_usdc": 10},
                        "rationale": "r"})
    assert not nhl_up.accepted and "not live" in nhl_up.reason
    down = _one(ctx, {"variant_id": "wm", "sport": "nba", "change": "mode", "values": {"mode": "paper"},
                      "rationale": "r"})
    assert down.accepted, down.reason
    v = apply_to_variant(down, ctx)
    assert v.sport_mode("nba") == "paper" and v.sport_mode("soccer") == "live" and v.mode == "live"
    to_live = _one(ctx, {"variant_id": "wm", "sport": "nhl", "change": "mode", "values": {"mode": "live"},
                         "rationale": "r"})
    assert not to_live.accepted and "only the deterministic promotion gate" in to_live.reason
    no_sport = _one(ctx, {"variant_id": "wm", "change": "stake", "values": {"stake_usdc": 10}, "rationale": "r"})
    assert not no_sport.accepted and "name the sport" in no_sport.reason


def test_validator_one_change_per_variant_sport(tmp_path):
    ctx = _ctx(tmp_path, soccer_promote=True)
    ds = validate({"changes": [
        {"variant_id": "wm", "sport": "soccer", "change": "stake", "values": {"stake_usdc": 10}},
        {"variant_id": "wm", "sport": "nba", "change": "mode", "values": {"mode": "paper"}},
        {"variant_id": "wm", "sport": "nba", "change": "mode", "values": {"mode": "off"}},
        {"variant_id": "wm", "change": "mode", "values": {"mode": "paper"}}]}, ctx, Rules(), "ladder")
    assert [d.accepted for d in ds] == [True, True, False, False]


def test_validator_legacy_variant_rejects_sport_stake(tmp_path):
    v = registry.load_variant(write(tmp_path, "legacy", {"mode": "live", "account": "cat"}))
    ctx = Context({"legacy": v}, {"legacy": Facts(trades_at_version=30, promote_ok=True)}, now=T0)
    d = _one(ctx, {"variant_id": "legacy", "sport": "nfl", "change": "stake", "values": {"stake_usdc": 10},
                   "rationale": "r"})
    assert not d.accepted and "per-sport variant" in d.reason
    d = _one(ctx, {"variant_id": "legacy", "change": "stake", "values": {"stake_usdc": 10}, "rationale": "r"})
    assert d.accepted, d.reason


def test_sport_facts_epoch_ignores_other_sports_overrides():
    import pandas as pd  # noqa: PLC0415

    class V:
        sports = ["soccer", "nba"]
        per_sport = True

        def sport_mode(self, s):
            return "live"
    history = [{"version": 1, "ts": 100, "params": {"prob_min": 0.93}},
               {"version": 2, "ts": 200, "params": {"prob_min": 0.93, "sport_overrides": {"nba": {"prob_min": 0.9}}}}]
    df = pd.DataFrame([{"sport": "soccer", "param_version": 1, "mode": "live", "status": "closed",
                        "realized_pnl": 0.1, "closed_at": 150, "settlement": "confirmed_sell", "confirmed_buy": 1,
                        "cost_usdc": 5.0}])
    f = retro.sport_facts(V(), df, history, [{"ts": 300, "sport": "nba"}, {"ts": 50, "sport": None}], {})
    assert f["soccer"].trades_at_version == 1 and f["soccer"].last_param_change_ts is None
    assert f["soccer"].last_stake_change_ts == 50
    assert f["nba"].last_param_change_ts == 200 and f["nba"].last_stake_change_ts == 300


def test_strategy_param_overrides_reach_us_sports():
    from polylab.strategies import build  # noqa: PLC0415
    v = next(v for v in registry.load_all(include_off=True) if v.id == "plum-king")
    s = build(v)
    assert s.p("nba")["max_wall_minute"] == 120 and s.p("nba")["min_wall_minute"] == 30
    assert s.p("nba")["take_profit_delta"] is None and s.p("nba")["hold_above_price"] is None
    assert s.p("soccer")["take_profit_delta"] == 0.03 and s.p("soccer")["hold_above_price"] == 0.99
    a = build(next(v for v in registry.load_all(include_off=True) if v.id == "apricot-eco"))
    assert a.p("nfl")["entry_tick_minute"] == 190 and a.p("mlb")["entry_tick_minute"] == 60
    assert a.p("nfl")["stop_loss_delta"] is None and a.p("mlb")["min_game_volume_usd"] == 100000
    assert a.p("nba")["entry_game_minute"] is None          # wall clock: the only replayable clock
    assert json.dumps(a.p("nhl"))


def test_live_from_keeps_sport_paper_until_date(tmp_path):
    import datetime as dt
    from polylab import registry
    p = tmp_path / "v1.yaml"
    p.write_text("id: v1\nfamily: watermelon\naccount: cat\nmode: live\nstake_usdc: 5\n"
                 "sports:\n  soccer: {mode: live}\n  nba: {mode: live, live_from: '2026-10-21'}\nparams: {}\n")
    v = registry.load_variant(p)
    before = dt.datetime(2026, 10, 20, 23, tzinfo=dt.timezone.utc).timestamp()
    after = dt.datetime(2026, 10, 21, 0, 1, tzinfo=dt.timezone.utc).timestamp()
    assert v.sport_mode("nba", now=before) == "paper" and v.sport_mode("nba", now=after) == "live"
    assert v.sport_mode("soccer", now=before) == "live"
    assert v.to_yaml()["sports"]["nba"]["live_from"] == "2026-10-21"
