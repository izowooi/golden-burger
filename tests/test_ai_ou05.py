"""ai-ou05-red: frozen O/U 0.5 picks (rule, storage, as-of), maker-only entries with the overround gate, owner-wallet
separation (shared conditions, Track 2 attribution) and the owner lock (no network)."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
import yaml

from polylab import db
from polylab.autopilot import retro
from polylab.autopilot.validator import OWNER_LOCKED, Context, Rules, validate
from polylab.manual import ledger as manual_ledger
from polylab.manual import sync as manual_sync
from polylab.registry import load_variant
from polylab.research import llm_forecast as lf
from polylab.strategies import build
from polylab.strategies.ai_ou05 import AiOu05
from polylab.strategies.base import Ledger, PositionView
from test_llm_forecast import LIVE, NOW, pair, world as forecast_world
from test_strategy_fixtures import T0, Env

KICK = T0 + 6 * 3600


# ---------------------------------------------------------------- pick rule

def g(key, league="epl", over=True, under=True, kickoff=NOW + 5 * 3600):
    return lf.GameCtx(key, league, key + "H", key + "A", kickoff, over_condition="c" + key if over else None,
                      over_token="o" + key if over else None, under_token="u" + key if under else None)


def eng(key, p00, kickoff=NOW + 5 * 3600):
    return {"game_key": key, "ai_prob": round(1 - p00, 6), "kickoff": kickoff}


def test_pick_rule_sides_overlap_pool_and_order():
    games = {k: g(k) for k in "abcdefgh"}
    games["x"] = g("x", league="fif")                     # outside the study pool (friendlies)
    games["y"] = g("y", under=False)                      # no Under token: cannot be bet both ways -> not pooled
    claude = [eng(k, p) for k, p in zip("abcdefgh", (.03, .04, .05, .06, .10, .11, .12, .13))] + \
        [eng("x", .01), eng("y", .02)]
    codex = [eng(k, p) for k, p in zip("abcdefgh", (.05, .03, .09, .04, .12, .15, .10, .14))] + \
        [eng("x", .01), eng("y", .02)]
    quotes = {"oa": {"bid": .95, "ask": .96}, "ua": {"bid": .04, "ask": .06}}
    rows = lf.compute_ou05_picks(claude, codex, games, quotes, NOW, {**lf.OU05_RULE, "qualify_top": 3})
    by = {r["game_key"]: r for r in rows}
    assert set(by) == set("abcdefgh")
    # lowest-3 Claude {a,b,c}, ChatGPT {b,d,a} -> Over a,b; highest-3 Claude {h,g,f}, ChatGPT {f,h,e} -> Under f,h
    assert [k for k in "abcdefgh" if by[k]["side"] == "over"] == ["a", "b"]
    assert [k for k in "abcdefgh" if by[k]["side"] == "under"] == ["f", "h"]
    assert (by["b"]["pick_rank"], by["a"]["pick_rank"]) == (1, 2)          # mean P(0-0) .035 before .04
    assert (by["h"]["pick_rank"], by["f"]["pick_rank"]) == (1, 2)          # highest mean P(0-0) first: .135, .13
    assert by["a"]["overround"] == pytest.approx(0.02) and by["c"]["overround"] is None
    assert by["c"]["side"] is None and by["c"]["low_rank_claude"] == 3


def test_under_order_is_highest_mean_first():
    games = {k: g(k) for k in "abcdef"}
    claude = [eng(k, p) for k, p in zip("abcdef", (.02, .03, .04, .20, .30, .25))]
    codex = [eng(k, p) for k, p in zip("abcdef", (.02, .03, .04, .22, .28, .26))]
    rows = lf.compute_ou05_picks(claude, codex, games, {}, NOW, {**lf.OU05_RULE, "qualify_top": 3})
    under = sorted((r for r in rows if r["side"] == "under"), key=lambda r: r["pick_rank"])
    assert [r["game_key"] for r in under] == ["e", "f", "d"]


def test_small_slate_overlap_is_dropped():
    games = {k: g(k) for k in "abc"}
    rows = lf.compute_ou05_picks([eng(k, .05) for k in "abc"], [eng(k, .06) for k in "abc"], games, {}, NOW)
    assert len(rows) == 3 and all(r["side"] is None for r in rows)       # top-8 of 3 games = both sides


def test_run_freezes_picks_and_later_batch_wins(tmp_path):
    env = forecast_world(tmp_path)
    res = lf.run_forecast(env.paths, now=NOW, engines=pair(), live_books=lambda t: LIVE, clock=lambda: NOW + 60)
    ou = res["consensus"]["ou05"]
    assert ou["status"] == "ok" and ou["n_pool"] == 1 and ou["over"] == [] and ou["under"] == []   # g1 only, both
    path = lf.db_path(env.paths)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM ou05_picks")                              # append-only
    row = dict(conn.execute("SELECT * FROM ou05_picks").fetchone())
    assert row["over_token"] == "ov1" and row["under_token"] == "un1" and row["over_ask"] == 0.94
    # two later batches: the first picks g1 Over, the next keeps g1 in the pool but unpicked
    base = {k: row[k] for k in row if k != "batch_id"}
    lf._insert_ou05(conn, "b2", NOW + 120, "ok", [{**base, "side": "over", "pick_rank": 1, "created_at": NOW + 120}],
                    {})
    assert lf.canonical_ou05_picks(conn, NOW + 200)["g1"]["side"] == "over"
    lf._insert_ou05(conn, "b3", NOW + 300, "ok", [{**base, "side": None, "pick_rank": None, "created_at": NOW + 300}],
                    {})
    lf._insert_ou05(conn, "b4", NOW + 400, "empty", [], {"reason": "codex failed"})
    assert lf.canonical_ou05_picks(conn, NOW + 500)["g1"]["side"] is None   # empty batch revokes nothing
    assert lf.canonical_ou05_picks(conn, NOW + 200)["g1"]["side"] == "over"  # as-of
    text = lf.slack_text(res, status="paper", now=NOW)
    assert "O/U 0.5 픽" in text and "ai-ou05-red" in text


def test_selection_puts_study_leagues_first(tmp_path):
    env = Env(tmp_path / "rt")
    env.game("u1", "soccer", NOW + 3 * 3600, "A", "B", status="scheduled", league="fifwc")
    env.market("mu", "u1", "moneyline", [("hu", "Yes", "home"), ("hun", "No", "no")], volume=900000)
    env.game("m1", "soccer", NOW + 4 * 3600, "C", "D", status="scheduled", league="mls")
    env.market("mm", "m1", "moneyline", [("hm", "Yes", "home"), ("hmn", "No", "no")], volume=10)
    view = env.view(NOW)
    assert [c.game_key for c in lf.select_games(view, NOW, max_games=1)] == ["m1"]


# ---------------------------------------------------------------- strategy

def market_world(tmp_path, over=(0.95, 0.96), under=(0.04, 0.06)):
    env = Env(tmp_path / "rt")
    env.game("g1", "soccer", KICK, "Arsenal", "Chelsea", status="scheduled", league="epl")
    env.market("ml1", "g1", "moneyline", [("h1", "Yes", "home"), ("hn", "No", "no")], volume=90000)
    env.market("ou1", "g1", "total", [("ov1", "Over", "over"), ("un1", "Under", "under")], volume=15000,
               fee_schedule={"feesEnabled": False})
    env.core.execute("UPDATE markets SET line=0.5 WHERE condition_id='ou1'")
    env.core.commit()
    now = KICK - 6 * 3600
    env.book("ov1", now - 60, [(over[0], 500)], [(over[1], 500)])
    env.book("un1", now - 60, [(under[0], 500)], [(under[1], 500)])
    return env, now


def pick(side="over", gk="g1", batch="b1"):
    return {"game_key": gk, "batch_id": batch, "side": side, "pick_rank": 1, "condition_id": "ou1",
            "over_token": "ov1", "under_token": "un1", "p00_claude": 0.03, "p00_codex": 0.04, "p00_mean": 0.035,
            "overround": 0.02}


def strat(picks, held=frozenset(), **params):
    v = SimpleNamespace(id="ai-ou05-t", family="ai_ou05", sports=["soccer"], stake_usdc=5.0, limits={},
                        mode="paper", account="red",
                        params={"leagues": ["epl", "mls"], "order_style": "maker", **params})
    s = build(v)
    s.picks_source = lambda now: picks
    s.owner_conditions = lambda alias, now, age: held if held is None else set(held)
    return s


@pytest.mark.parametrize("side,token,label", [("over", "ov1", "Over 0.5"), ("under", "un1", "Under 0.5")])
def test_entry_on_picked_side_hold_to_resolution(tmp_path, side, token, label):
    env, now = market_world(tmp_path)
    [i] = strat({"g1": pick(side)}).entry_signals(env.view(now), now, Ledger())
    assert i.token_id == token and i.outcome_label == label and i.features["overround"] == pytest.approx(0.02)
    assert i.exit_rules["hold_to_resolution"] and i.exit_rules["side"] == side
    assert i.exit_rules["take_profit_price"] is None and i.exit_rules["stop_loss_price"] is None
    lo, hi = (0.8, 0.99) if side == "over" else (0.01, 0.2)
    assert (i.min_price, i.max_price) == (lo, hi)


def test_tight_books_first_and_priced_inside_wide_books_wait_at_bid(tmp_path):
    env, now = market_world(tmp_path)                                     # g1: 0.96 + 0.06 -> overround .02
    env.game("g2", "soccer", KICK + 600, "Spurs", "Fulham", status="scheduled", league="epl")
    env.market("ou2", "g2", "total", [("ov2", "Over", "over"), ("un2", "Under", "under")], volume=15000)
    env.core.execute("UPDATE markets SET line=0.5 WHERE condition_id='ou2'")
    env.core.commit()
    env.book("ov2", now - 60, [(0.95, 500)], [(0.953, 500)])
    env.book("un2", now - 60, [(0.047, 500)], [(0.05, 500)])              # 0.953 + 0.05 -> overround .003
    p2 = {**pick(gk="g2"), "condition_id": "ou2", "over_token": "ov2", "under_token": "un2", "pick_rank": 2}
    intents = strat({"g1": pick(), "g2": p2}).entry_signals(env.view(now), now, Ledger())
    assert [i.game_key for i in intents] == ["g2", "g1"]                  # lower overround first, despite pick rank
    tight, wide = intents
    assert tight.features["overround_tier"] == "tight" and wide.features["overround_tier"] == "wide"
    assert tight.features["maker_price_rule"] == wide.features["maker_price_rule"] == "below"   # always behind bid


def test_overround_gate_and_bands(tmp_path):
    env, now = market_world(tmp_path, under=(0.04, 0.09))                 # 0.96 + 0.09 = 1.05
    s = strat({"g1": pick()})
    assert s.entry_signals(env.view(now), now, Ledger()) == [] and ("g1", "overround_too_high") in s.skips
    env, now = market_world(tmp_path / "b", over=(0.60, 0.61), under=(0.39, 0.40))
    s = strat({"g1": pick()})
    assert s.entry_signals(env.view(now), now, Ledger()) == [] and ("g1", "price_out_of_band") in s.skips


def test_maker_only_unpicked_traded_and_cutoff(tmp_path):
    env, now = market_world(tmp_path)
    s = strat({"g1": pick()}, order_style="taker")
    assert s.entry_signals(env.view(now), now, Ledger()) == [] and ("*", "maker_only_strategy") in s.skips
    assert strat({"g1": {**pick(), "side": None}}).entry_signals(env.view(now), now, Ledger()) == []
    assert strat({"g1": pick()}, sides=["under"]).entry_signals(env.view(now), now, Ledger()) == []
    traded = Ledger([PositionView("p", "open", "ov1", "ou1", "g1", "soccer", now - 60, 0.95, 5.2, 5.0)])
    assert strat({"g1": pick()}).entry_signals(env.view(now), now, traded) == []
    late = KICK - 4 * 60
    env.book("ov1", late - 30, [(0.95, 500)], [(0.96, 500)])
    env.book("un1", late - 30, [(0.04, 500)], [(0.06, 500)])
    assert strat({"g1": pick()}).entry_signals(env.view(late), late, Ledger()) == []


def test_owner_held_condition_and_unknown_holdings_fail_closed(tmp_path):
    env, now = market_world(tmp_path)
    s = strat({"g1": pick()}, held={"ou1"})
    assert s.entry_signals(env.view(now), now, Ledger()) == [] and ("g1", "owner_holds_condition") in s.skips
    s = strat({"g1": pick()}, held=None)
    assert s.entry_signals(env.view(now), now, Ledger()) == [] and ("g1", "owner_holdings_unknown") in s.skips


def test_resting_entry_withdrawn_when_pick_dropped_or_overround_widens(tmp_path):
    env, now = market_world(tmp_path)
    p = PositionView("p", "pending", "ov1", "ou1", "g1", "soccer", now - 60, None, None, None,
                     exit_rules={"side": "over"})
    assert strat({"g1": pick()}).maker_entry_open(env.view(now), now, p)
    assert not strat({"g1": {**pick(), "side": None}}).maker_entry_open(env.view(now), now, p)
    assert not strat({}).maker_entry_open(env.view(now), now, p)
    env.book("un1", now - 10, [(0.05, 500)], [(0.10, 500)])
    assert not strat({"g1": pick()}).maker_entry_open(env.view(now), now, p)


def test_paper_tick_end_to_end_maker(tmp_path, monkeypatch):
    from polylab.engine.tick import run as tick

    monkeypatch.delenv("POLYLAB_KILL", raising=False)
    env, now = market_world(tmp_path, over=(0.94, 0.96), under=(0.04, 0.05))      # overround .01 -> tight
    monkeypatch.setattr(AiOu05, "picks_source", staticmethod(lambda n: {"g1": pick()}))
    monkeypatch.setattr(AiOu05, "owner_conditions", staticmethod(lambda a, n, age: set()))
    reg = tmp_path / "reg"
    reg.mkdir()
    real = yaml.safe_load(open("strategies/ai-ou05-red.yaml"))
    real.update(mode="paper", account=None)
    (reg / "ai-ou05-red.yaml").write_text(yaml.safe_dump(real, allow_unicode=True))
    assert tick(env.paths, registry_dir=reg, poll=False, now=now)["ok"]
    conn = db.strategy(env.paths, "ai-ou05-red")
    [o] = [dict(r) for r in conn.execute("SELECT * FROM orders")]
    assert o["order_type"] == "GTC" and o["status"] == "resting" and o["limit_price"] == pytest.approx(0.93)
    env.book("ov1", now + 30, [(0.91, 500)], [(0.92, 500)])               # asks trade through our 0.93 bid
    env.book("un1", now + 30, [(0.05, 500)], [(0.06, 500)])
    tick(env.paths, registry_dir=reg, poll=False, now=now + 60)
    [p] = [dict(r) for r in db.strategy(env.paths, "ai-ou05-red").execute("SELECT * FROM positions")]
    assert p["status"] == "open" and p["entry_price"] == pytest.approx(0.93) and p["entry_fee_usdc"] == 0.0
    env.resolve("ou1", 0, KICK + 7200)
    tick(env.paths, registry_dir=reg, poll=False, now=KICK + 3 * 3600)
    p = dict(db.strategy(env.paths, "ai-ou05-red").execute("SELECT * FROM positions").fetchone())
    assert p["status"] == "resolved" and p["realized_pnl"] == pytest.approx(5 / 0.93 * 1 - 5, abs=0.06)


# ---------------------------------------------------------------- owner wallet separation & lock

def test_manual_ledger_excludes_bot_trades_by_identity(tmp_path):
    conn = manual_ledger.open_db(tmp_path / "red.db")

    def trade(c, tx, ts):
        return {"type": "TRADE", "side": "BUY", "condition_id": c, "token_id": c + "-over", "outcome_index": 0,
                "outcome": "Over", "size": 5.0, "price": 0.95, "usdc_size": 4.75, "timestamp": ts,
                "transaction_hash": tx, "title": "X vs Y: O/U 0.5"}
    rows = [trade("0xowner", "0xAAA", T0), trade("0xbot", "0xBBB", T0 + 1), trade("0xbot", "0xCCC", T0 + 2),
            {"type": "REDEEM", "condition_id": "0xbot", "token_id": "", "size": 5.0, "usdc_size": 5.0,
             "timestamp": T0 + 9000, "transaction_hash": "0xR"}]
    manual_ledger.ingest_activity(conn, rows, T0 + 10000)
    conn.commit()
    bot = ({("0xbbb", "0xbot-over")}, {"0xbot"})
    manual_ledger.rebuild(conn, "red", None, T0 + 10000, bot)
    held = {r[0]: r[1] for r in conn.execute("SELECT p.token_id, m.bought_shares FROM positions p "
                                             "JOIN position_meta m USING(position_id)")}
    # the owner's own fill on the bot's condition (tx 0xCCC) stays; the bot fill and its redeem do not
    assert held == {"0xowner-over": 5.0, "0xbot-over": 5.0}
    assert manual_ledger.get_meta(conn, "bot_rows_excluded") == "1"
    manual_ledger.rebuild(conn, "red", None, T0 + 10000, ({("0xbbb", "0xbot-over"), ("0xccc", "0xbot-over")},
                                                          {"0xbot"}))
    assert {r[0] for r in conn.execute("SELECT token_id FROM positions")} == {"0xowner-over"}
    assert manual_ledger.get_meta(conn, "bot_rows_excluded") == "3"


def test_bot_activity_reads_live_fills_of_the_wallets_variants(tmp_path):
    env = Env(tmp_path / "rt")
    conn = db.strategy(env.paths, "ai-ou05-red")
    conn.execute("INSERT INTO orders(intent_id, position_id, created_at, mode, side, token_id, condition_id, "
                 "order_type, status, updated_at) VALUES('i1','p1',1,'live','BUY','tokA','condA','GTC','confirmed',2)")
    conn.execute("INSERT INTO fills(fill_id, intent_id, ts, side, price, shares, fee_usdc, status, raw) "
                 "VALUES('f1','i1',2,'BUY',0.95,5,0,'CONFIRMED','{\"transaction_hash\": \"0xABC\"}')")
    conn.execute("INSERT INTO positions(position_id, mode, param_version, stake_usdc, condition_id, token_id, "
                 "opened_at, status) VALUES('p1','live',1,5,'condA','tokA',1,'open')")
    conn.commit()
    variants = [SimpleNamespace(id="ai-ou05-red", account="red"), SimpleNamespace(id="other", account="lion")]
    assert manual_sync.bot_activity(env.paths, "red", variants) == ({("0xabc", "tokA")}, {"condA"})
    assert manual_sync.bot_activity(env.paths, "wolf", variants) == (set(), set())


def test_registry_yaml_is_red_maker_no_stop():
    v = load_variant(__import__("pathlib").Path("strategies/ai-ou05-red.yaml"))
    assert (v.account, v.stake_usdc, v.family) == ("red", 5.0, "ai_ou05") and v.mode in ("paper", "live")
    assert v.params["entry_price_rule"] == "below"
    assert v.params["order_style"] == "maker" and v.params["leagues"] == list(lf.OU05_LEAGUES)
    assert v.params["max_overround"] <= 0.04 and "stop_loss_price" not in v.params
    assert "ai-ou05-red" in OWNER_LOCKED


def test_owner_lock_rejects_every_change_and_ladder_skips_it():
    v = load_variant(__import__("pathlib").Path("strategies/ai-ou05-red.yaml"))
    ctx = Context(variants={v.id: v}, facts={}, now=NOW)
    for ch in ({"change": "params", "values": {"max_overround": 0.03}}, {"change": "mode", "values": {"mode": "paper"}},
               {"change": "stake", "values": {"stake_usdc": 5.0}}, {"change": "retire", "values": {}}):
        [d] = validate({"changes": [{"variant_id": v.id, "rationale": "x", **ch}]}, ctx, Rules(), source="ai")
        assert not d.accepted and "owner-locked" in d.reason
    report = {"variants": [{"id": v.id, "mode": "live", "ladder": {"available": True, "action": "paper",
                                                                   "reason": "floor loss"}}]}
    assert retro.ladder_changes(report)["changes"] == []


def test_entry_rests_one_tick_below_best_bid_in_paper_tick(tmp_path, monkeypatch):
    from polylab.engine.tick import run as tick

    monkeypatch.delenv("POLYLAB_KILL", raising=False)
    env, now = market_world(tmp_path, over=(0.93, 0.96), under=(0.04, 0.07))       # overround .03 -> wide
    monkeypatch.setattr(AiOu05, "picks_source", staticmethod(lambda n: {"g1": pick()}))
    monkeypatch.setattr(AiOu05, "owner_conditions", staticmethod(lambda a, n, age: set()))
    reg = tmp_path / "reg"
    reg.mkdir()
    real = yaml.safe_load(open("strategies/ai-ou05-red.yaml"))
    real.update(mode="paper", account=None)
    (reg / "ai-ou05-red.yaml").write_text(yaml.safe_dump(real, allow_unicode=True))
    tick(env.paths, registry_dir=reg, poll=False, now=now)
    [o] = [dict(r) for r in db.strategy(env.paths, "ai-ou05-red").execute("SELECT * FROM orders")]
    assert o["limit_price"] == pytest.approx(0.92)                       # one tick below the 0.93 bid


def test_codex_web_runs_inside_secret_denying_sandbox(tmp_path):
    e = lf.ForecastCodexEngine(binary="codex", web=True)
    cmd = e.command(tmp_path)
    assert cmd[0] == "/usr/bin/sandbox-exec" and cmd[1] == "-p"
    profile = cmd[2]
    assert '.polylab")' in profile and '.ssh")' in profile and "deny file-read*" in profile
    assert "deny file-write* (require-not" in profile and str(tmp_path.resolve()) in profile
    assert 'web_search="live"' in cmd and "danger-full-access" in cmd and "workspace-write" not in cmd
    assert cmd[cmd.index("-m") + 1] == "gpt-6.1-sol" and 'model_reasoning_effort="high"' in cmd and cmd[-1] == "-"
    missing = lf.ForecastCodexEngine(binary="codex", web=True, sandbox=str(tmp_path / "nope"))
    assert missing.available()[0] is False or "codex" in missing.available()[1]
    off = lf.ForecastCodexEngine(binary="codex", web=False).command(tmp_path)
    assert off[0] == "codex" and 'web_search="disabled"' in off and "workspace-write" in off


def test_claude_forecast_engine_pins_opus_high():
    cmd = lf.ForecastClaudeEngine().command()
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5" and cmd[cmd.index("--effort") + 1] == "high"


def test_context_hides_market_prices_and_research_items_are_kept(tmp_path):
    g1 = g("a")
    g1.tokens = {"over": "oa", "under": "ua", "home": "ha"}
    lf.build_context(tmp_path, [g1], {"oa": {"bid": .9, "ask": .95, "mid": .925}}, {}, NOW)
    text = (tmp_path / "games.json").read_text()
    assert "0.95" not in text and "polymarket" not in text and '"a"' in text
    rows, _, problems = lf.parse_forecasts({"games": [{"game_key": "a", "p_not_0_0": 0.93, "research": {
        "form": "f", "strength_gap": "s", "managers": "m", "h2h": "h", "absences": "x", "context": "c"},
        "factors": ["k"]}, {"game_key": "b", "p_not_0_0": 0.9}]}, {"a", "b"})
    assert rows[0]["factors"][:2] == ["form: f", "strength_gap: s"] and rows[0]["factors"][-1] == "k"
    assert any("b: research 0/6" in p for p in problems)


def test_chunked_engine_merges_chunks_and_survives_one_failed_chunk(tmp_path):
    from test_llm_forecast import FakeEngine
    games = [g(k, kickoff=NOW + 3600 * (i + 1)) for i, k in enumerate("abcde")]

    def payload(gs):
        if any(x["game_key"] == "c" for x in gs):
            return "not json"                                      # the chunk holding c, d fails
        return {"games": [{"game_key": x["game_key"], "p_not_0_0": 0.9 + 0.01 * i} for i, x in enumerate(gs)]}
    e = FakeEngine("claude", payload=payload)
    r = lf.run_engine_chunked(e, "p", tmp_path, games, {}, NOW, 60, clock=lambda: NOW, chunk_size=2, parallel=2)
    assert r["ok"] and r["chunks"] == {"n": 3, "ok": 2} and len(e.prompts) == 3
    assert sorted(x["game_key"] for x in r["rows"]) == ["a", "b", "e"] and r["top_derived"]
    assert any("chunk 1 failed" in p for p in r["problems"])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c0", "c1", "c2"]
    lf.build_context(tmp_path / "s", games[:2], {}, {}, NOW)                 # run_forecast builds it per engine
    single = lf.run_engine_chunked(FakeEngine("codex", payload=payload), "p", tmp_path / "s", games[:2], {}, NOW, 60,
                                   clock=lambda: NOW, chunk_size=2)
    assert single["ok"] and not (tmp_path / "s" / "c0").exists()


def test_below_rule_prices_one_tick_under_bid():
    from polylab.execution import maker
    from polylab.marketview import Book
    b = Book("t", 0, [(0.95, 10)], [(0.96, 10)], "poll", False)
    assert maker.entry_price(b, 0.01, 0.8, 0.99, "below") == pytest.approx(0.94)
    assert maker.entry_price(Book("t", 0, [(0.01, 10)], [(0.02, 10)], "poll", False), 0.01, 0.0, 1, "below") is None


def test_paper_venue_infers_fine_tick_from_stored_book(tmp_path):
    from polylab.execution.maker import PaperVenue
    env, now = market_world(tmp_path, over=(0.971, 0.979), under=(0.021, 0.029))
    v = PaperVenue(env.view(now), None)
    assert v.tick("ov1") == 0.01                                          # unknown before a book was read
    v.book("ov1", now)
    assert v.tick("ov1") == 0.001
    env2, now2 = market_world(tmp_path / "b")
    v2 = PaperVenue(env2.view(now2), None)
    v2.book("ov1", now2)
    assert v2.tick("ov1") == 0.01
