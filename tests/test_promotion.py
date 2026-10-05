"""Deterministic paper→live promotion gate (owner decision 2026-10-06 `sports3:auto-promotion`)."""

from __future__ import annotations

import json

import yaml
from test_autopilot_retro import FakeEngine, fake_trades, opts, setup
from test_per_sport import write
from test_reports_build import NOW

from polylab import db, registry
from polylab.autopilot import retro
from polylab.autopilot.validator import Context, Facts, Rules, apply_to_variant, promotion_key, validate
from polylab.risk import promotion
from polylab.risk.promotion import PaperTrade

DAY = 86400


def _variant(tmp_path, **over):
    data = {"mode": "live", "account": "cat", "sports": {"soccer": {"mode": "live"}, "nba": {"mode": "paper"},
                                                         "nfl": {"mode": "paper"}}}
    data.update(over)
    return registry.load_variant(write(tmp_path, "wm", data))


def _trades(n, pnl=lambda i: 0.3 if i % 5 else -0.4, start=NOW - 60 * DAY):
    return [PaperTrade(start + i * 3600, start + i * 3600 + 600, pnl(i), 5.0) for i in range(n)]


def test_eligibility_refusals(tmp_path):
    v = _variant(tmp_path)
    oct_day = 1_791_000_000                                 # 2026-10-03: NBA preseason window
    assert "preseason" in promotion.evaluate(v, "nba", _trades(40), oct_day, None).reason
    assert promotion.in_preseason("nfl", 1_786_000_000)     # 2026-08-06
    assert not promotion.in_preseason("soccer", oct_day)
    assert "own mode is live" in promotion.evaluate(v, "soccer", _trades(40), NOW, None).reason
    assert "cooldown" in promotion.evaluate(v, "nba", _trades(40), NOW, NOW - DAY).reason
    assert "no account" in promotion.evaluate(_variant(tmp_path, account=None, mode="paper"), "nba", _trades(40),
                                              NOW, None).reason
    later = _variant(tmp_path, sports={"nba": {"mode": "paper", "live_from": "2026-12-01"}})
    assert "live_from" in promotion.evaluate(later, "nba", _trades(40), NOW, None).reason
    # master paper + another sport already set live: flipping the master would take it live too
    flip = _variant(tmp_path, mode="paper", sports={"soccer": {"mode": "live"}, "nba": {"mode": "paper"}})
    assert "would take them live" in promotion.evaluate(flip, "nba", _trades(40), NOW, None).reason
    legacy = registry.load_variant(write(tmp_path, "lg", {"mode": "paper", "account": "cat", "sports": ["nba"]}))
    assert "per-sport" in promotion.evaluate(legacy, "nba", _trades(40), NOW, None).reason


def test_paper_and_backtest_stages(tmp_path):
    v = _variant(tmp_path)
    few = promotion.evaluate(v, "nba", _trades(29), NOW, None)
    assert few.stage == "paper" and "29/30" in few.reason
    losing = promotion.evaluate(v, "nba", _trades(40, lambda i: -0.1 if i % 2 else 0.05), NOW, None)
    assert losing.stage == "paper" and "lower bound" in losing.reason
    # positive overall but the second half loses
    tail = promotion.evaluate(v, "nba", _trades(40, lambda i: 0.6 if i < 20 else (-0.3 if i % 3 else 0.2)), NOW, None)
    assert tail.stage == "paper" and "half [2]" in tail.reason, tail.reason
    ready = promotion.evaluate(v, "nba", _trades(40), NOW, None)
    assert ready.needs_backtest and not ready.ok
    st = ready.evidence["paper"]
    assert st["n"] == 40 and st["roi_ci_lo"] > 0 and [h["n"] for h in st["halves"]] == [20, 20]
    for bt, why in (({"error": "timeout"}, "replay failed"), ({"n": 39, "roi": 0.02}, "n=39"),
                    ({"n": 60, "roi": -0.001}, "ROI")):
        g = promotion.evaluate(v, "nba", _trades(40), NOW, None, backtest=bt)
        assert not g.ok and g.stage == "backtest" and why in g.reason
    ok = promotion.evaluate(v, "nba", _trades(40), NOW, None, backtest={"n": 60, "roi": 0.01})
    assert ok.ok and ok.stage == "passed"


def _ctx(v):
    by_sport = {s: Facts(trades_at_version=40) for s in v.sports}
    return Context({"wm": v}, {"wm": Facts(trades_at_version=40, by_sport=by_sport)}, now=NOW)


def test_validator_live_only_from_promotion_source_with_evidence(tmp_path):
    v = _variant(tmp_path, mode="paper", sports={"soccer": {"mode": "paper"}, "nba": {"mode": "paper"},
                                                 "nfl": {"mode": "off"}})
    ctx = _ctx(v)
    ch = {"variant_id": "wm", "sport": "nba", "change": "mode", "values": {"mode": "live"}, "rationale": "gate"}
    # no evidence: even the promotion source is refused
    d = validate({"changes": [ch]}, ctx, Rules(), "promotion")[0]
    assert not d.accepted and "no passing promotion-gate evidence" in d.reason
    ctx.promotions[promotion_key("wm", "nba")] = {"ok": True}
    for source in ("ai:claude", "inbox:x.json", "ladder"):
        d = validate({"changes": [ch]}, ctx, Rules(), source)[0]
        assert not d.accepted and "only the deterministic promotion gate" in d.reason, source
    d = validate({"changes": [ch]}, ctx, Rules(), "promotion")[0]
    assert d.accepted, d.reason
    new = apply_to_variant(d, ctx)
    assert new.mode == "live" and new.sport_mode("nba") == "live" and new.sport_stake("nba") == 5.0
    assert new.sport_mode("soccer") == "paper" and new.sport_mode("nfl") == "off"   # others keep their own mode
    other = {**ch, "sport": "soccer"}
    assert not validate({"changes": [other]}, ctx, Rules(), "promotion")[0].accepted


def _per_sport_world(tmp_path, monkeypatch, n_paper=36, pnl=lambda i: 0.3 if i % 6 else -0.5, stake_event_ts=None):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    d = yaml.safe_load((reg / "watermelon-cat.yaml").read_text())
    d["sports"] = {"soccer": {"mode": "live", "stake_usdc": 5.0}, "nba": {"mode": "paper", "stake_usdc": 5.0}}
    (reg / "watermelon-cat.yaml").write_text(yaml.safe_dump(d, sort_keys=False))
    s = db.strategy(paths, "watermelon-cat")
    for i in range(n_paper):
        closed = NOW - 2 * DAY - i * 7200
        p = pnl(i)
        s.execute("INSERT INTO positions(position_id, mode, param_version, stake_usdc, sport, league, game_key,"
                  " condition_id, token_id, outcome_label, opened_at, entry_price, shares, cost_usdc, status,"
                  " closed_at, exit_reason, exit_price, proceeds_usdc, realized_pnl, settlement)"
                  " VALUES(?, 'paper', 2, 5, 'nba', 'nba', 'gn', 'cn', 'tn', 'Team', ?, 0.9, 5.5, 5.0, 'resolved',"
                  " ?, 'resolution_win', 1.0, ?, ?, 'resolution')", (f"pp{i}", closed - 3000, closed, 5 + p, p))
    if stake_event_ts:
        db_ok = s.execute("PRAGMA table_info(stake_events)").fetchall()
        if not any(r[1] == "sport" for r in db_ok):
            s.execute("ALTER TABLE stake_events ADD COLUMN sport TEXT")
        s.execute("INSERT INTO stake_events(ts, from_usdc, to_usdc, from_mode, to_mode, reason, sport) "
                  "VALUES(?, 5, 5, 'live', 'paper', 'paper: ladder', 'nba')", (stake_event_ts,))
    s.commit()
    env.engines = lambda: []
    return paths, reg, env, calls, s


def test_weekly_retro_promotes_paper_sport_and_records_everything(tmp_path, monkeypatch):
    paths, reg, env, calls, s = _per_sport_world(tmp_path, monkeypatch)
    runs = []

    def fake_backtest(jobs, start, end, timeout):
        runs.append([(v.id, v.sports, p) for v, p in jobs])
        assert end - start == 120 * DAY
        return [{"trades": fake_trades(50, lambda i: 0.2 if i % 8 else -0.5)}]

    env.backtest = fake_backtest
    res = retro.run_retro(opts("weekly"), env)
    assert runs == [[("watermelon-cat", ["nba"], {})]]
    promo = [a for a in res["applied"] if a["source"] == "promotion"]
    assert len(promo) == 1 and promo[0]["sport"] == "nba", res["rejected"]
    d = yaml.safe_load((reg / "watermelon-cat.yaml").read_text())
    assert d["sports"]["nba"] == {"mode": "live", "stake_usdc": 5.0} and d["mode"] == "live"
    ev = s.execute("SELECT from_mode, to_mode, to_usdc, sport, reason, evidence FROM stake_events "
                   "WHERE sport='nba'").fetchall()
    assert [(r[0], r[1], r[2]) for r in ev] == [("paper", "live", 5.0)] and ev[0][4].startswith("promotion")
    assert json.loads(ev[0][5])["gate"]["paper"]["n"] == 36
    assert "자동 실거래 전환" in (env.reports_dir / "changes.md").read_text()
    att = json.loads((env.reports_dir / "attention.json").read_text())
    items = [i for i in att["items"] if "자동 실거래 전환" in i["title"]]
    assert items and items[0]["severity"] == "info", att["items"]


def test_daily_retro_never_replays_and_flags_waiting(tmp_path, monkeypatch):
    paths, reg, env, calls, s = _per_sport_world(tmp_path, monkeypatch)
    env.backtest = lambda *a: (_ for _ in ()).throw(AssertionError("daily must not run the promotion replay"))
    res = retro.run_retro(opts("daily"), env)
    assert not [a for a in res["applied"] if a["source"] == "promotion"]
    assert yaml.safe_load((reg / "watermelon-cat.yaml").read_text())["sports"]["nba"]["mode"] == "paper"
    att = json.loads((env.reports_dir / "attention.json").read_text())
    assert any(i["id"] == "promotion:watermelon-cat:nba" for i in att["items"]), att["items"]


def test_demoted_sport_needs_fresh_paper_record(tmp_path, monkeypatch):
    # every paper trade predates the ladder's live→paper event: the sample restarts from zero
    paths, reg, env, calls, s = _per_sport_world(tmp_path, monkeypatch, stake_event_ts=NOW - 2 * DAY - 140000)
    v = registry.load_variant(reg / "watermelon-cat.yaml")
    trades, last = retro.promotion_sample(v, "nba", paths)
    assert last == NOW - 2 * DAY - 140000 and len(trades) == 20       # only trades opened after the event
    env.backtest = lambda jobs, start, end, timeout: [{"trades": fake_trades(50, lambda i: 0.2)}]
    res = retro.run_retro(opts("weekly"), env)
    assert not [a for a in res["applied"] if a["source"] == "promotion"]


def test_ai_cannot_promote_even_with_a_proposal(tmp_path, monkeypatch):
    paths, reg, env, calls, s = _per_sport_world(tmp_path, monkeypatch, n_paper=5)
    prop = {"schema": "polylab.proposal/v1", "summary": "go live",
            "changes": [{"variant_id": "watermelon-cat", "sport": "nba", "change": "mode", "values": {"mode": "live"},
                         "rationale": "looks good"}]}
    env.engines = lambda: [FakeEngine("claude", proposal=prop)]
    env.backtest = lambda jobs, start, end, timeout: [{"trades": fake_trades(50, lambda i: 0.2)}]
    res = retro.run_retro(opts("weekly"), env)
    assert not res["applied"]
    assert any("only the deterministic promotion gate" in r["reason"] for r in res["rejected"])
    assert yaml.safe_load((reg / "watermelon-cat.yaml").read_text())["sports"]["nba"]["mode"] == "paper"


def test_preseason_paper_trades_do_not_count(tmp_path, monkeypatch):
    paths, reg, env, calls, s = _per_sport_world(tmp_path, monkeypatch)
    v = registry.load_variant(reg / "watermelon-cat.yaml")
    assert len(retro.promotion_sample(v, "nba", paths)[0]) == 36
    # the same trades in October (NBA preseason window 10/01-10/20) are not evidence
    s.execute("UPDATE positions SET opened_at = 1_791_000_000, closed_at = 1_791_003_000 WHERE sport='nba'")
    s.commit()
    assert retro.promotion_sample(v, "nba", paths)[0] == []
