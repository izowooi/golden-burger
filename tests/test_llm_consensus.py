"""Dual-AI consensus: pre-registered rule, as-of canonical consensus, llm-nil-consensus strategy (no network)."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from polylab.research import llm_forecast as lf
from polylab.strategies import build
from polylab.strategies.base import Ledger, PositionView
from test_llm_forecast import LIVE, NOW, FakeEngine, pair, world


def frow(gk, p_not, rank, kickoff=NOW + 5 * 3600, ask=None):
    return {"game_key": gk, "league": "epl", "home_team": gk + "H", "away_team": gk + "A", "kickoff": kickoff,
            "condition_id": None, "token_id": None, "ai_prob": p_not, "rank": rank,
            "market_price_at_forecast": None, "market_ask_at_forecast": ask}


def test_compute_consensus_rule():
    claude = [frow("a", 0.95, 1), frow("b", 0.94, 2), frow("c", 0.93, 3), frow("d", 0.92, 6),
              frow("e", 0.96, None), frow("f", 0.91, 5, ask=0.9), frow("late", 0.99, 4, kickoff=NOW)]
    codex = [frow("a", 0.91, 4), frow("b", 0.97, 1), frow("c", 0.93, 5), frow("d", 0.99, 1),
             frow("e", 0.99, 2), frow("f", 0.91, 3), frow("late", 0.99, 2, kickoff=NOW)]
    rows = lf.compute_consensus(claude, codex, NOW)
    by = {r["game_key"]: r for r in rows}
    assert "late" not in by                                              # kickoff not after consensus time
    assert by["a"]["p00_consensus"] == pytest.approx(0.07) and by["b"]["p00_consensus"] == pytest.approx(0.045)
    assert not by["d"]["qualifies"] and not by["e"]["qualifies"]          # outside one engine's top-5
    # qualified ordered by mean P(0-0): b .045, c .07 (worse rank 5) vs a .07 (worse rank 4) -> a before c
    assert [r["game_key"] for r in rows if r["qualifies"]] == ["b", "a", "c", "f"]
    assert [by[k]["consensus_rank"] for k in "bacf"] == [1, 2, 3, 4] and by["d"]["consensus_rank"] is None
    assert by["f"]["market_implied_p00"] == pytest.approx(0.1)


def _cx(p):
    return lambda gs: {"games": [{"game_key": g["game_key"], "p_not_0_0": p} for g in gs],
                       "top5": [g["game_key"] for g in gs]}


def test_consensus_stored_and_canonical_as_of(tmp_path):
    env = world(tmp_path)
    res = lf.run_forecast(env.paths, now=NOW, engines=pair(codex_payload=_cx(0.90)), live_books=lambda t: LIVE,
                          clock=lambda: NOW + 60)
    c = res["consensus"]
    assert c["status"] == "ok" and c["n_qualified"] == 2 and [p["game_key"] for p in c["picks"]] == ["g1", "g2"]
    g1 = c["picks"][0]
    assert g1["p00_claude"] == pytest.approx(0.05) and g1["p00_codex"] == pytest.approx(0.10)
    assert g1["p00_consensus"] == pytest.approx(0.075) and g1["market_implied_p00"] == pytest.approx(0.06)
    # next day: codex fails -> empty batch, earlier consensus rows still count for games not yet kicked off
    lf.run_forecast(env.paths, now=NOW + 86400 - 7 * 3600, force=True, live_books=lambda t: {},
                    engines=pair(codex_payload="nope"), clock=lambda: NOW + 86400 - 7 * 3600 + 60)
    conn = sqlite3.connect(lf.db_path(env.paths))
    conn.row_factory = sqlite3.Row
    assert [r[0] for r in conn.execute("SELECT status FROM consensus_batches ORDER BY ts")] == ["ok", "empty"]
    cons = lf.canonical_consensus(conn, NOW + 3600)
    assert set(cons) == {"g1", "g2"} and lf.canonical_consensus(conn, NOW + 30) == {}       # as-of
    shaped = lf.load_consensus_forecasts(lf.db_path(env.paths), NOW + 3600)
    assert shaped["g1"]["rank"] == 1 and shaped["g1"]["ai_prob"] == pytest.approx(0.925)
    assert shaped["g1"]["run_id"] == res["batch_id"]


def test_old_research_db_without_consensus_tables_reads_empty(tmp_path):
    p = tmp_path / "old.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE runs(run_id TEXT)")
    conn.commit()
    assert lf.load_consensus_forecasts(p, NOW) == {}


# ---------------------------------------------------------------- llm-nil-consensus strategy

def cons_strategy(**params):
    v = SimpleNamespace(id="llm-nil-consensus", family="llm_nil", sports=["soccer"], stake_usdc=5.0, limits={},
                        params={"source": "consensus", "edge": 0.0, "max_price": 0.985, **params}, mode="paper")
    return build(v)


def test_consensus_entry_uses_implied_minus_consensus_edge(tmp_path):
    env = world(tmp_path)
    now = NOW + 5 * 3600 - 600
    env.book("ov1", now - 30, [(0.91, 500)], [(0.92, 500)])              # implied P(0-0) = 0.08
    s = cons_strategy(edge=0.0)
    s.forecast_source = lambda t: {"g1": {"rank": 1, "ai_prob": 0.925, "run_id": "b1", "p00_claude": 0.05,
                                          "p00_codex": 0.10}}
    intents = s.entry_signals(env.view(now), now, Ledger())
    assert len(intents) == 1 and intents[0].features["implied_p00"] == pytest.approx(0.08)
    assert intents[0].features["source"] == "consensus" and intents[0].max_price == pytest.approx(0.925)
    s2 = cons_strategy(edge=0.01)                                        # 0.08 - 0.075 = 0.005 < 0.01
    s2.forecast_source = s.forecast_source
    assert s2.entry_signals(env.view(now), now, Ledger()) == []
    s3 = cons_strategy()
    s3.forecast_source = lambda t: {"g1": {"rank": None, "ai_prob": 0.99, "run_id": "b1"}}   # did not qualify
    assert s3.entry_signals(env.view(now), now, Ledger()) == []


def test_llm_nil_threshold_exits_are_opt_in(tmp_path):
    env = world(tmp_path)
    now = NOW + 6 * 3600
    env.core.execute("UPDATE markets SET fee_schedule='{\"feesEnabled\": false}'")
    env.core.commit()
    env.book("ov1", now - 20, [(0.995, 500)], [(0.996, 500)])
    s = cons_strategy(take_profit_price=0.99)
    pos = PositionView("p", "open", "ov1", "ou1", "g1", "soccer", now - 3600, 0.92, 5.43, 5.0,
                       exit_rules={"take_profit_price": 0.99, "stop_loss_price": None})
    ex = s.exit_signals(env.view(now), now, pos)
    assert ex is not None and ex.kind == "take_profit"
    assert s.confirm_exit(pos, ex, env.view(now).book("ov1", now)).ok
    held = PositionView("p", "open", "ov1", "ou1", "g1", "soccer", now - 3600, 0.92, 5.43, 5.0,
                        exit_rules={"hold_to_resolution": True, "ai_prob": 0.95})       # pre-TP positions
    assert s.exit_signals(env.view(now), now, held) is None


def test_tick_consensus_paper_end_to_end(tmp_path, monkeypatch):
    import yaml

    from polylab import db
    from polylab.engine.tick import run as tick

    env = world(tmp_path)
    monkeypatch.setenv("POLYLAB_ROOT", str(env.paths.root))
    env.core.execute("UPDATE markets SET fee_schedule='{\"feesEnabled\": false}'")
    env.core.commit()
    lf.run_forecast(env.paths, now=NOW, engines=[FakeEngine("claude", payload=_cx(0.96)),
                                                 FakeEngine("codex", payload=_cx(0.94))],
                    live_books=lambda t: LIVE, clock=lambda: NOW + 60)
    reg = tmp_path / "reg"
    reg.mkdir()
    real = yaml.safe_load(open("strategies/llm-nil-consensus.yaml"))
    assert real["params"]["source"] == "consensus"
    real.update(mode="paper", account=None)          # the real yaml may be switched live by the owner
    (reg / "llm-nil-consensus.yaml").write_text(yaml.safe_dump(real))
    now = NOW + 5 * 3600 - 300
    env.book("ov1", now - 20, [(0.92, 500)], [(0.93, 500)])              # implied .07 - consensus .05 >= 0
    out = tick(env.paths, registry_dir=reg, poll=False, now=now)
    assert out["ok"], out
    pos = [dict(r) for r in db.strategy(env.paths, "llm-nil-consensus").execute("SELECT * FROM positions")]
    assert len(pos) == 1 and pos[0]["mode"] == "paper" and pos[0]["token_id"] == "ov1"
