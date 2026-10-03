"""AI 0-0 study: game selection, dual fake-engine run, append-only research DB, outcomes, eval, strategy (no network).
Consensus-specific tests live in test_llm_consensus.py."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from polylab.autopilot.runner import RunResult
from polylab.research import llm_eval
from polylab.research import llm_forecast as lf
from polylab.strategies import build
from polylab.strategies.base import Ledger, PositionView
from polylab.strategies.llm_nil import LlmNil
from test_strategy_fixtures import T0, Env

NOW = T0


class FakeEngine:
    def __init__(self, name="claude", payload=None, ok=True, available=True):
        self.name, self.payload, self.ok, self._avail = name, payload, ok, available
        self.model = "fake-model"
        self.prompts = []

    def available(self):
        return (True, "") if self._avail else (False, "토큰 없음")

    def run(self, prompt, cwd, timeout):
        self.prompts.append(prompt)
        games = json.loads((cwd / "games.json").read_text())["games"]
        assert (cwd / "base_rates.json").exists() and (cwd / "MANIFEST.md").exists()
        payload = self.payload(games) if callable(self.payload) else self.payload
        if payload is not None:
            (cwd / "forecasts.json").write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return RunResult(self.ok, "" if self.ok else "boom")


def good_payload(games):
    return {"schema": lf.SCHEMA_ID,
            "games": [{"game_key": g["game_key"], "p_not_0_0": 0.95 - 0.01 * i, "p_draw": 0.25, "confidence": "Medium",
                       "factors": ["form  ok", "0xabcdef0123456789abcdef0123456789abcdef01 leak"],
                       "sources": ["https://example.com/a", "not a url", "https://example.com/a"]}
                      for i, g in enumerate(games)] + [{"game_key": "bogus", "p_not_0_0": 0.9}],
            "top3": [g["game_key"] for g in games][:2] + ["bogus"]}


def pair(claude_payload=good_payload, codex_payload=good_payload, **kw):
    return [FakeEngine("claude", payload=claude_payload, **kw), FakeEngine("codex", payload=codex_payload, **kw)]


def world(tmp_path):
    env = Env(tmp_path / "rt")
    k1 = NOW + 5 * 3600
    env.game("g1", "soccer", k1, "Arsenal", "Chelsea", status="scheduled", league="epl")
    env.market("ml1", "g1", "moneyline", [("h1", "Yes", "home"), ("hn", "No", "no")], volume=90000)
    env.market("d1", "g1", "draw", [("d1y", "Yes", "draw"), ("d1n", "No", "no")], volume=30000)
    env.market("ou1", "g1", "total", [("ov1", "Over", "over"), ("un1", "Under", "under")], volume=15000)
    env.core.execute("UPDATE markets SET line=0.5 WHERE condition_id='ou1'")
    env.core.commit()
    env.game("g2", "soccer", NOW + 20 * 3600, "LA Galaxy", "LAFC", status="scheduled", league="mls")
    env.market("d2", "g2", "draw", [("d2y", "Yes", "draw"), ("d2n", "No", "no")], volume=5000)
    env.book("d2y", NOW - 300, [(0.27, 100)], [(0.29, 100)])
    env.game("g3", "soccer", NOW + 5 * 3600, "A", "B", status="scheduled", league="fif")      # not major
    env.market("d3", "g3", "draw", [("d3y", "Yes", "draw"), ("d3n", "No", "no")])
    env.game("g4", "soccer", NOW + 40 * 3600, "C", "D", status="scheduled", league="epl")     # beyond 30h
    env.market("d4", "g4", "draw", [("d4y", "Yes", "draw"), ("d4n", "No", "no")])
    env.game("g5", "soccer", NOW + 600, "E", "F", status="scheduled", league="epl")           # too close
    env.market("d5", "g5", "draw", [("d5y", "Yes", "draw"), ("d5n", "No", "no")])
    # finished history for the base rate / poisson baseline
    for i in range(3):
        env.core.execute("INSERT INTO games(game_key, sport, league, start_time, status, home_score, away_score, "
                         "ended_at, first_seen, updated_at, source) VALUES(?, 'soccer', 'epl', ?, 'ended', ?, 0, ?, 0, 0,"
                         " 'discover')", (f"h{i}", NOW - 86400 * (i + 1), i, NOW - 86400 * (i + 1) + 7200))
    env.core.commit()
    return env


LIVE = {"ov1": {"bids": [{"price": "0.92", "size": "100"}], "asks": [{"price": "0.94", "size": "100"}]},
        "h1": {"bids": [{"price": "0.55", "size": "10"}], "asks": [{"price": "0.57", "size": "10"}]}}


def run(env, engines, **kw):
    kw.setdefault("clock", lambda: NOW + 60)
    return lf.run_forecast(env.paths, now=NOW, engines=engines, live_books=lambda toks: LIVE, **kw)


def test_selection_and_run_stores_validated_forecasts(tmp_path):
    env = world(tmp_path)
    engines = pair()
    res = run(env, engines)
    assert res["ok"] and res["games"] == 2 and res["over_markets"] == 1 and res["forecasts"] == 4
    assert res["context_shas_equal"] and engines[0].prompts == engines[1].prompts       # same prompt, same context
    for n in ("claude", "codex"):
        e = res["engines"][n]
        assert e["ok"] and e["run_id"] == f"{res['batch_id']}-{n}" and e["model"] == "fake-model" and e["n"] == 2
    base = tmp_path / "rt/state/forecast"
    dirs = sorted(p.parent.name for p in base.glob("*/*/games.json"))
    assert dirs == ["claude", "codex"]                                                 # independent cwd per engine
    games = json.loads(next(base.glob("*/claude/games.json")).read_text())["games"]
    assert [g["game_key"] for g in games] == ["g1", "g2"]            # Over-market game first; fif/far/near excluded
    assert games[1]["polymarket"]["over_0_5_goals"].startswith("no Total 0.5")
    conn = sqlite3.connect(lf.db_path(env.paths))
    conn.row_factory = sqlite3.Row
    rows = {r["game_key"]: dict(r) for r in conn.execute("SELECT * FROM forecasts WHERE run_id LIKE '%-claude'")}
    g1, g2 = rows["g1"], rows["g2"]
    assert g1["token_id"] == "ov1" and g1["market_price_at_forecast"] == pytest.approx(0.93)
    assert g1["market_ask_at_forecast"] == 0.94 and g1["market_source"] == "clob_live" and g1["rank"] == 1
    assert g1["home_price"] == pytest.approx(0.56) and g1["confidence"] == "medium"
    assert json.loads(g1["sources_json"]) == ["https://example.com/a"]
    assert "0x" not in g1["factors_json"] and "[REDACTED]" in g1["factors_json"]
    # no Over market -> nullable market columns, draw price only as context from the stored book
    assert g2["token_id"] is None and g2["market_price_at_forecast"] is None and g2["draw_price"] == pytest.approx(0.28)
    assert g2["rank"] == 2 and all(r["created_at"] < r["kickoff"] for r in rows.values())
    runs = {r["engine"]: dict(r) for r in conn.execute("SELECT * FROM runs")}
    assert set(runs) == {"claude", "codex"} and all(r["status"] == "ok" and r["n_forecasts"] == 2 for r in runs.values())
    assert runs["claude"]["context_sha"] == runs["codex"]["context_sha"] and runs["claude"]["prompt_sha"]
    b = dict(conn.execute("SELECT * FROM consensus_batches").fetchone())
    assert b["status"] == "ok" and b["n_games"] == 2 and b["claude_run_id"].endswith("-claude")
    assert json.loads(b["rule_json"]) == json.loads(json.dumps(lf.CONSENSUS_RULE, sort_keys=True))


def test_research_db_is_append_only_and_same_day_rerun_refused(tmp_path):
    env = world(tmp_path)
    run(env, pair())
    again = run(env, pair())
    assert again["skipped"].startswith("already forecast")
    conn = sqlite3.connect(lf.db_path(env.paths))
    for sql in ("UPDATE forecasts SET ai_prob=0.5", "DELETE FROM forecasts", "UPDATE runs SET status='x'",
                "DELETE FROM runs", "UPDATE consensus SET p00_consensus=0", "DELETE FROM consensus",
                "UPDATE consensus_batches SET status='ok'", "DELETE FROM consensus_batches"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(sql)


def test_forced_rerun_is_logged_and_becomes_canonical(tmp_path):
    env = world(tmp_path)
    run(env, pair())
    one = lambda gs: {"games": [{"game_key": "g1", "p_not_0_0": 0.9}], "top3": ["g1"]}  # noqa: E731
    res = lf.run_forecast(env.paths, now=NOW + 3600, force=True, live_books=lambda t: {}, engines=pair(one, one),
                          clock=lambda: NOW + 3700)
    assert res["ok"] and res["batch_id"].endswith("forced")
    conn = sqlite3.connect(lf.db_path(env.paths))
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT SUM(forced) FROM runs").fetchone()[0] == 2
    canon = lf.canonical_forecasts(conn, NOW + 4000, engine="claude")
    assert canon["g1"]["ai_prob"] == 0.9 and canon["g2"]["ai_prob"] == pytest.approx(0.94)
    assert lf.canonical_forecasts(conn, NOW + 200, engine="claude")["g1"]["ai_prob"] == 0.95   # later run unseen


def test_canonical_is_per_engine(tmp_path):
    env = world(tmp_path)
    import time as _t
    t0 = _t.monotonic()

    class Slow(FakeEngine):     # codex finishes later: without the engine filter it would win "latest run"
        def run(self, prompt, cwd, timeout):
            _t.sleep(0.3)
            return super().run(prompt, cwd, timeout)

    lo = lambda gs: {"games": [{"game_key": g["game_key"], "p_not_0_0": 0.80} for g in gs]}  # noqa: E731
    res = lf.run_forecast(env.paths, now=NOW, live_books=lambda t: LIVE,
                          clock=lambda: NOW + 60 + int((_t.monotonic() - t0) * 100),
                          engines=[FakeEngine("claude", payload=good_payload), Slow("codex", payload=lo)])
    assert res["engines"]["codex"]["created_at"] > res["engines"]["claude"]["created_at"]
    conn = sqlite3.connect(lf.db_path(env.paths))
    conn.row_factory = sqlite3.Row
    assert lf.canonical_forecasts(conn, NOW + 999, engine="claude")["g1"]["ai_prob"] == 0.95
    assert lf.canonical_forecasts(conn, NOW + 999, engine="codex")["g1"]["ai_prob"] == 0.80


def test_engine_failure_is_recorded_without_fallback(tmp_path):
    env = world(tmp_path)
    res = run(env, pair(claude_payload=good_payload, codex_payload="not json"))
    assert res["ok"] and res["engines"]["claude"]["ok"] and not res["engines"]["codex"]["ok"]
    assert res["consensus"]["status"] == "empty" and "ChatGPT 실패" in res["consensus"]["reason"]
    conn = sqlite3.connect(lf.db_path(env.paths))
    assert dict(conn.execute("SELECT engine, status FROM runs").fetchall()) == {"claude": "ok", "codex": "failed"}
    assert conn.execute("SELECT status FROM consensus_batches").fetchone()[0] == "empty"
    assert conn.execute("SELECT COUNT(*) FROM consensus").fetchone()[0] == 0
    env2 = world(tmp_path / "b")
    res2 = run(env2, [FakeEngine(payload=None, ok=False), FakeEngine("codex", available=False)])
    assert not res2["ok"] and res2["forecasts"] == 0
    conn = sqlite3.connect(lf.db_path(env2.paths))
    assert {r[0] for r in conn.execute("SELECT status FROM runs")} == {"failed"}
    assert not lf.already_ran_today(conn, NOW)                           # a failed run does not block the day


def test_forecast_after_kickoff_is_never_stored(tmp_path):
    env = world(tmp_path)
    res = run(env, pair(), clock=lambda: NOW + 6 * 3600)   # AI finished after g1 kickoff
    assert res["engines"]["claude"]["dropped_after_kickoff"] == ["g1"] and res["forecasts"] == 2
    assert [r["game_key"] for r in res["consensus"]["rows"]] == ["g2"]


def test_parse_forecasts_rejects_bad_values():
    rows, top, _ = lf.parse_forecasts({"games": [{"game_key": k, "p_not_0_0": 0.9} for k in "abcdefg"],
                                       "top5": list("gfedcba")}, set("abcdefg"))
    assert top == list("gfedc")                                           # top-5 list kept, truncated at 5
    rows, top, problems = lf.parse_forecasts({"games": [{"game_key": "a", "p_not_0_0": 1.4},
                                                        {"game_key": "b", "p_not_0_0": "0.9"}], "top3": ["a", "b"]},
                                             {"a", "b"})
    assert [r["game_key"] for r in rows] == ["b"] and top == ["b"] and problems
    assert lf.parse_forecasts([], {"a"})[0] == []


def test_forecast_engine_commands_enable_web_but_no_shell(tmp_path):
    cmd = lf.ForecastClaudeEngine().command()
    allow, deny = cmd[cmd.index("--allowedTools") + 1], cmd[cmd.index("--disallowedTools") + 1]
    assert "WebSearch" in allow and "WebFetch" in allow and "WebFetch" not in deny and "WebSearch" not in deny
    assert "Bash" in deny and "Read(~/**)" in deny and "WebSearch" in cmd[cmd.index("--tools") + 1]
    tools = cmd[cmd.index("--tools") + 1].split(",")
    assert "Grep" not in tools and "Glob" not in tools and "Grep" in deny.split(",")
    ccmd = lf.ForecastCodexEngine(binary="codex", web=True).command(tmp_path)
    assert 'web_search="live"' in ccmd and 'web_search="disabled"' not in ccmd
    assert "sandbox_workspace_write.network_access=false" in ccmd and "workspace-write" in ccmd


def test_codex_web_search_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv(lf.CODEX_WEB_ENV, raising=False)
    assert 'web_search="disabled"' in lf.ForecastCodexEngine(binary="codex").command(tmp_path)
    monkeypatch.setenv(lf.CODEX_WEB_ENV, "1")
    assert 'web_search="live"' in lf.ForecastCodexEngine(binary="codex").command(tmp_path)


def test_crash_after_ai_is_recorded_as_failed_run(tmp_path, monkeypatch):
    env = world(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(lf, "parse_forecasts", boom)
    with pytest.raises(RuntimeError):
        run(env, pair())
    conn = sqlite3.connect(lf.db_path(env.paths))
    rows = conn.execute("SELECT status, detail FROM runs").fetchall()
    assert len(rows) == 2 and all(st == "failed" and "parser exploded" in d for st, d in rows)
    assert conn.execute("SELECT status FROM consensus_batches").fetchone()[0] == "empty"


def test_resolve_outcomes_and_eval(tmp_path):
    env = world(tmp_path)
    cx = lambda gs: {"games": [{"game_key": "g1", "p_not_0_0": 0.90}, {"game_key": "g2", "p_not_0_0": 0.92}],  # noqa
                     "top5": ["g2", "g1"]}
    run(env, pair(good_payload, cx))
    later = NOW + 30 * 3600
    env.resolve("ou1", 1, NOW + 8 * 3600)                     # Under won -> 0-0
    env.core.execute("UPDATE games SET status='ended', home_score=2, away_score=1, ended_at=? WHERE game_key='g2'",
                     (NOW + 22 * 3600,))
    env.core.commit()
    out = lf.resolve_outcomes(env.paths, later)
    assert out["added"] == {"total_0_5_resolution": 1, "final_score": 1}
    assert lf.resolve_outcomes(env.paths, later)["pending"] == 0
    conn = sqlite3.connect(lf.db_path(env.paths))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE outcomes SET not_0_0=1")
    ev = llm_eval.evaluate(env.paths, now=later)
    assert ev["resolved_games"] == 2 and ev["forecast_games"] == 2
    cl, cxe, j = ev["engines"]["claude"], ev["engines"]["codex"], ev["joint"]
    # g1: claude .95 / codex .90, y=0 ; g2: claude .94 / codex .92, y=1
    assert cl["scores"]["brier"] == pytest.approx(round((0.95 ** 2 + 0.06 ** 2) / 2, 5))
    assert cxe["scores"]["brier"] == pytest.approx(round((0.90 ** 2 + 0.08 ** 2) / 2, 5))
    assert j["n"] == 2 and j["consensus"]["brier"] == pytest.approx(round((0.925 ** 2 + 0.07 ** 2) / 2, 5))
    assert cl["market_subset"]["market"]["brier"] == pytest.approx(0.93 ** 2, abs=1e-4)
    assert j["market_subset"]["n"] == 1 and j["claude_minus_codex_brier"]["n"] == 2
    # poisson falls back to the documented constant (only 3 finished epl games < MIN_LEAGUE_N)
    import math
    assert cl["poisson"]["brier"] == pytest.approx(
        round(((1 - math.exp(-2.75)) ** 2 + math.exp(-2.75) ** 2) / 2, 5))
    assert cl["top3"]["n"] == 2 and cl["top3"]["priced"] == 1 and cl["top3"]["pnl"] == -5.0   # g1 at .94, lost
    assert cl["top3"]["rate_0_0"] == 0.5
    assert j["consensus_top3"]["n"] == 2 and j["consensus_top3"]["pnl"] == -5.0
    assert ev["consensus_batches"] == {"total": 1, "ok": 1, "empty": 0}
    assert ev["strategies"]["llm-nil-draw"] == {"available": False}
    sec = llm_eval.report_section(env.paths, NOW, later)
    lines = llm_eval.render_lines(sec)
    assert lines[0] == "## AI 교차검증 0:0 연구" and any("ChatGPT" in ln and "합의" in ln for ln in lines)
    assert any("CONFIRMED" in ln for ln in lines)


def test_calibration_and_scores_helpers():
    assert llm_eval.brier([1.0, 0.0], [1, 0]) == 0
    assert llm_eval.log_loss([1.0], [0]) == pytest.approx(-__import__("math").log(1e-4), rel=1e-3)
    cal = llm_eval.calibration([0.95, 0.955, 0.5], [1, 0, 1])
    assert [c["n"] for c in cal] == [1, 2]
    assert llm_eval.paired_brier_diff([0.9], [0.9], [1]) is None
    assert llm_eval.evaluate(SimpleNamespace(research_dir="/nonexistent", core_db="/nonexistent"))["available"] is False


def test_slack_text_consensus_top3_and_status(tmp_path):
    pick = {"consensus_rank": 1, "home_team": "Arsenal", "away_team": "Chelsea", "league": "epl", "kickoff": NOW,
            "p00_claude": 0.05, "p00_codex": 0.07, "p00_consensus": 0.06, "market_ask_at_forecast": 0.92,
            "market_implied_p00": 0.08}
    res = {"ok": True, "over_markets": 1, "engines": {"claude": {"ok": True, "n": 2}, "codex": {"ok": True, "n": 2}},
           "consensus": {"status": "ok", "picks": [pick, {**pick, "consensus_rank": 2, "market_ask_at_forecast": None,
                                                          "market_implied_p00": None}]}}
    text = lf.slack_text(res, status="paper", now=NOW)
    assert "Claude 2경기 · ChatGPT 2경기" in text and "P(0:0) Claude 5.0% · ChatGPT 7.0% · 합의 6.0%" in text
    assert "시장 내재 8.0% (1−Over ask 0.920)" in text and "Over 0.5 시장가 없음" in text
    assert "llm-nil-consensus: paper(가상)" in text and text.endswith(lf.DISCLAIMER)
    assert "실거래(live)" in lf.slack_text(res, status="live", now=NOW)
    empty = {**res, "engines": {"claude": {"ok": True, "n": 2}, "codex": {"ok": False}},
             "consensus": {"status": "empty", "reason": "ChatGPT 실패: boom", "picks": []}}
    t2 = lf.slack_text(empty, status="paper", now=NOW)
    assert "ChatGPT 실패" in t2 and "합의 없음" in t2
    assert lf.consensus_variant_status() in ("paper", "live")             # strategies/llm-nil-consensus.yaml
    assert lf.consensus_variant_status(tmp_path) == "absent"


# ---------------------------------------------------------------- strategy

def nil(**params):
    v = SimpleNamespace(id="llm-nil-t", family="llm_nil", sports=["soccer"], params=params, limits={},
                        stake_usdc=5.0, mode="paper")
    return build(v)


def strat_world(tmp_path, ask=0.93):
    env = world(tmp_path)
    kick = NOW + 5 * 3600
    now = kick - 600
    env.book("ov1", now - 30, [(ask - 0.01, 500)], [(ask, 500)])
    fc = {"g1": {"game_key": "g1", "rank": 1, "ai_prob": 0.96, "run_id": "r1", "market_price_at_forecast": 0.93},
          "g2": {"game_key": "g2", "rank": 2, "ai_prob": 0.99, "run_id": "r1"}}
    return env, now, fc


def test_llm_nil_enters_over_with_edge_before_kickoff(tmp_path):
    env, now, fc = strat_world(tmp_path)
    s = nil(edge=0.02, max_price=0.96, top_k=3, leagues=["epl", "mls"])
    s.forecast_source = lambda t: fc
    view = env.view(now)
    intents = s.entry_signals(view, now, Ledger())
    assert [i.token_id for i in intents] == ["ov1"]
    i = intents[0]
    assert i.max_price == pytest.approx(0.94) and i.features["edge"] == pytest.approx(0.03)
    assert not any(k == "g2" for k, _ in s.skips)                         # g2 kicks off in 20h: not yet
    held = PositionView("p", "open", "ov1", "ou1", "g1", "soccer", now - 60, 0.93, 5.3, 5.0,
                        exit_rules=i.exit_rules)
    assert i.exit_rules["hold_to_resolution"] and s.exit_signals(view, now, held) is None
    books = {"ov1": view.book("ov1", now)}
    assert s.confirm_entry(i, books, 5.0).ok
    # already traded -> no re-entry
    traded = Ledger([PositionView("p", "open", "ov1", "ou1", "g1", "soccer", now - 60, 0.93, 5.3, 5.0)])
    assert s.entry_signals(view, now, traded) == []


def test_llm_nil_gates(tmp_path):
    env, now, fc = strat_world(tmp_path, ask=0.95)
    s = nil(edge=0.02)
    s.forecast_source = lambda t: fc
    assert s.entry_signals(env.view(now), now, Ledger()) == []              # 0.96 - 0.95 < edge
    assert ("g1", "no_edge_or_price_out_of_band") in s.skips
    env2, now2, fc2 = strat_world(tmp_path / "x")
    s2 = nil(top_k=1)
    s2.forecast_source = lambda t: {**fc2, "g1": {**fc2["g1"], "rank": 2}}
    assert s2.entry_signals(env2.view(now2), now2, Ledger()) == []         # outside top-k
    s3 = nil()
    s3.forecast_source = lambda t: fc2
    early = now2 - 3600
    assert s3.entry_signals(env2.view(early), early, Ledger()) == []        # outside the pre-kickoff window
    s4 = nil()
    s4.forecast_source = lambda t: {}
    assert s4.entry_signals(env2.view(now2), now2, Ledger()) == []


def test_llm_nil_refuses_live_and_default_source_is_safe(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="paper-only"):
        build(SimpleNamespace(id="x", family="llm_nil", sports=["soccer"], params={}, mode="live"))
    with pytest.raises(ValueError, match="paper-only"):
        build(SimpleNamespace(id="llm-nil-draw", family="llm_nil", sports=["soccer"], params={}, mode="live"))
    with pytest.raises(ValueError, match="paper-only"):          # an autopilot clone with source=consensus
        build(SimpleNamespace(id="llm-nil-consensus-v2", family="llm_nil", sports=["soccer"],
                              params={"source": "consensus"}, mode="live"))
    with pytest.raises(ValueError, match="paper-only"):          # the allowed id, but not on the consensus source
        build(SimpleNamespace(id="llm-nil-consensus", family="llm_nil", sports=["soccer"], params={}, mode="live"))
    ok = build(SimpleNamespace(id="llm-nil-consensus", family="llm_nil", sports=["soccer"],
                               params={"source": "consensus"}, mode="live"))
    assert isinstance(ok, LlmNil)
    with pytest.raises(ValueError, match="unknown source"):
        build(SimpleNamespace(id="x", family="llm_nil", sports=["soccer"], params={"source": "gpt"}, mode="paper"))
    monkeypatch.setenv("POLYLAB_ROOT", str(tmp_path / "empty"))
    assert LlmNil.forecast_source(NOW) == {} and LlmNil.forecast_source(NOW, "consensus") == {}


def test_llm_nil_skips_game_without_over_market(tmp_path):
    env, now, fc = strat_world(tmp_path)
    s = nil(entry_window_minutes=24 * 60)
    s.forecast_source = lambda t: fc
    s.entry_signals(env.view(now), now, Ledger())
    assert ("g2", "no_over_0_5_market") in s.skips


def test_tick_paper_end_to_end_then_resolution(tmp_path, monkeypatch):
    """forecast run -> research DB -> tick (paper, default forecast source via POLYLAB_ROOT) -> resolved paper P&L."""
    import yaml

    from polylab import db
    from polylab.engine.tick import run as tick

    env = world(tmp_path)
    monkeypatch.setenv("POLYLAB_ROOT", str(env.paths.root))
    env.core.execute("UPDATE markets SET fee_schedule='{\"feesEnabled\": false}'")
    env.core.commit()
    only_g1 = lambda gs: {"games": [{"game_key": "g1", "p_not_0_0": 0.97}], "top3": ["g1"]}  # noqa: E731
    run(env, pair(only_g1, lambda gs: {"games": [{"game_key": "g1", "p_not_0_0": 0.50}], "top3": ["g1"]}))
    reg = tmp_path / "reg"
    reg.mkdir()
    real = yaml.safe_load(open("strategies/llm-nil-draw.yaml"))
    (reg / "llm-nil-draw.yaml").write_text(yaml.safe_dump(real))
    now = NOW + 5 * 3600 - 300
    env.book("ov1", now - 20, [(0.92, 500)], [(0.93, 500)])
    out = tick(env.paths, registry_dir=reg, poll=False, now=now)
    assert out["ok"], out
    conn = db.strategy(env.paths, "llm-nil-draw")
    pos = [dict(r) for r in conn.execute("SELECT * FROM positions")]
    assert len(pos) == 1 and pos[0]["mode"] == "paper" and pos[0]["token_id"] == "ov1"
    assert pos[0]["status"] == "open" and pos[0]["entry_price"] == pytest.approx(0.93)
    env.resolve("ou1", 0, NOW + 7 * 3600)                     # Over won
    tick(env.paths, registry_dir=reg, poll=False, now=NOW + 8 * 3600)
    pos = dict(db.strategy(env.paths, "llm-nil-draw").execute("SELECT * FROM positions").fetchone())
    assert pos["status"] == "resolved" and pos["realized_pnl"] == pytest.approx(5 / 0.93 - 5, abs=1e-3)
    led = llm_eval.evaluate(env.paths, now=NOW + 9 * 3600)["strategies"]["llm-nil-draw"]
    assert led["paper"]["settled"] == 1 and led["live"]["settled"] == 0   # codex said .50: Claude-only source
