import json

import pytest
from test_publish_snapshot import assert_shape, contract_examples
from test_reports_build import NOW, make_world, write_variant

from polylab import db, registry
from polylab.publish import snapshot
from polylab.reports import build, games, render, slack

KICK = (NOW - 6 * 3600) // 60 * 60          # finished EPL game
END = KICK + 115 * 60


def add_game(core, key, sport, league, start, status, ended_at=None, home="Home FC", away="Away FC", score=(None, None)):
    core.execute("INSERT INTO games(game_key, sport, league, title, home_team, away_team, start_time, status, home_score,"
                 " away_score, ended_at, first_seen, updated_at, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'discover')",
                 (key, sport, league, f"{home} vs. {away}", home, away, start, status, score[0], score[1], ended_at,
                  start - 86400, start))


def add_market(core, cond, key, mtype, side, label, resolved=None, resolved_at=None, volume=1000.0, index=0):
    core.execute("INSERT INTO markets(condition_id, game_key, market_type, question, group_item_title, volume,"
                 " resolved_outcome_index, resolved_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (cond, key, mtype, f"Will {label} win?", label, volume, resolved, resolved_at, NOW))
    core.execute("INSERT INTO tokens VALUES(?,?,?,?,?)", (f"{cond}-t", cond, index, "Yes", side))
    return f"{cond}-t"


def bars(core, token, series, source="history"):
    for ts, p in series:
        core.execute("INSERT INTO price_bars VALUES(?,?,?,?,?)", (token, ts, source, p, ts))


def build_world(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYLAB_SOCCER_LEAGUES", "epl")  # pin scope independent of MAJOR_SOCCER_LEAGUES
    paths, reg = make_world(tmp_path, monkeypatch)
    core = db.core(paths)
    # gA: EPL, home favourite (0.60) loses 1-2, big in-play swing at ~70', larger post-whistle settlement jump
    add_game(core, "gA", "soccer", "epl", KICK, "ended", ended_at=END, home="Arsenal", away="Chelsea", score=(1, 2))
    res_at = END + 3600
    th = add_market(core, "cA_h", "gA", "moneyline", "home", "Arsenal", resolved=1, resolved_at=res_at, volume=9e5)
    td = add_market(core, "cA_d", "gA", "draw", "draw", "Draw (Arsenal vs. Chelsea)", resolved=1, resolved_at=res_at)
    ta = add_market(core, "cA_a", "gA", "moneyline", "away", "Chelsea", resolved=0, resolved_at=res_at, volume=4e5)
    home = [(KICK - 3600, 0.58), (KICK - 60, 0.60), (KICK, 0.61)]
    home += [(KICK + m * 60, 0.62) for m in range(1, 80)]
    home += [(KICK + 80 * 60, 0.55), (KICK + 81 * 60, 0.40), (KICK + 84 * 60, 0.20)]   # -0.35 within 4 min
    home += [(KICK + m * 60, 0.20) for m in range(85, 115)]
    home += [(END + 60, 0.01), (res_at - 60, 0.001), (res_at + 60, 0.0)]
    bars(core, th, home)
    bars(core, td, [(KICK - 60, 0.25), (KICK + 30 * 60, 0.27), (KICK + 100 * 60, 0.30), (END + 60, 0.01)])
    away = [(KICK - 60, 0.15), (KICK + 30 * 60, 0.14), (KICK + 80 * 60, 0.14), (KICK + 84 * 60, 0.50), (KICK + 114 * 60, 0.55),
            (END + 60, 0.999)]  # +0.45 post whistle: must not be an in-play swing
    bars(core, ta, away)
    bars(core, th, [(KICK + 81 * 60, 0.41)], source="poll_mid")  # canonical source wins over history
    for ts, gm, h, a in ((KICK + 10 * 60, 10, 0, 0), (KICK + 25 * 60, 25, 1, 0), (KICK + 81 * 60, 71, 1, 1),
                         (KICK + 84 * 60, 74, 1, 2)):
        core.execute("INSERT INTO game_states(game_key, ts, received_at, source, status, game_minute, home_score,"
                     " away_score) VALUES('gA', ?, ?, 'ws_sports', 'live', ?, ?, ?)", (ts, ts, gm, h, a))
    # gB: MLB still running, unresolved
    add_game(core, "gB", "mlb", "mlb", NOW - 3 * 3600, "live", home="Yankees", away="Red Sox")
    tb = add_market(core, "cB", "gB", "moneyline", "home", "Yankees", volume=2e6, index=1)
    core.execute("INSERT INTO tokens VALUES('cB-away','cB',0,'Red Sox','away')")
    bars(core, tb, [(NOW - 4 * 3600, 0.52), (NOW - 3600, 0.60)], source="poll_mid")
    bars(core, "cB-away", [(NOW - 4 * 3600, 0.48), (NOW - 3600, 0.40)], source="poll_mid")
    # gC: out-of-scope friendly (not traded) -> excluded, counted
    add_game(core, "gC", "soccer", "fif", NOW - 5 * 3600, "ended", ended_at=NOW - 3 * 3600)
    # gD: bad row, ended_at before kickoff, no resolution
    add_game(core, "gD", "soccer", "epl", NOW - 8 * 3600, "ended", ended_at=NOW - 30 * 3600, score=(0, 0))
    # gE: ended two days ago -> outside the window
    add_game(core, "gE", "nhl", "nhl", NOW - 60 * 3600, "ended", ended_at=NOW - 57 * 3600)
    core.commit()
    # paper variant with one settled paper position on MLB and a NULL-fee fill
    write_variant(reg, "plum-king", mode="paper", account="king", sports=["mlb"])
    s = db.strategy(paths, "plum-king")
    s.execute("INSERT INTO positions(position_id, mode, param_version, stake_usdc, sport, league, game_key,"
              " condition_id, token_id, outcome_label, opened_at, entry_price, shares, cost_usdc, status, closed_at,"
              " exit_reason, exit_price, proceeds_usdc, realized_pnl, settlement) VALUES('pp','paper',1,5,'mlb','mlb',"
              "'gB','cB',?,'Yankees',?,0.5,10,5,'closed',?, 'take_profit', 0.9, 9, 4.0, 'paper')",
              (tb, NOW - 7200, NOW - 3600))
    s.execute("INSERT INTO orders(intent_id, position_id, created_at, mode, side, token_id, condition_id, order_type,"
              " usdc_amount, status, updated_at) VALUES('ip','pp',?, 'paper','BUY',?, 'cB','FOK',5,'confirmed',?)",
              (NOW - 7200, tb, NOW - 7200))
    s.execute("INSERT INTO fills VALUES('fp','ip',?, 'BUY', 0.5, 10, NULL, 'PAPER', NULL)", (NOW - 7200,))
    s.commit()
    return paths, reg


@pytest.fixture
def world(tmp_path, monkeypatch):
    return build_world(tmp_path, monkeypatch)


def _game(rep, key):
    return next(g for g in rep["games"]["games"] if g["game_key"] == key)


def test_games_selection_swings_and_upset(world):
    paths, _ = world
    rep = build.build("daily", paths, now=NOW, slot="evening", use_jenkins=False)
    gm = rep["games"]
    keys = [g["game_key"] for g in gm["games"]]
    assert "gA" in keys and "gB" in keys and "gD" in keys and "g1" in keys
    assert "gC" not in keys and "gE" not in keys
    assert gm["excluded_out_of_scope"] == {"fif": 1}
    a = _game(rep, "gA")
    assert a["resolved"] and a["result"] == "away" and a["favourite"] == "home" and a["upset"]
    assert (a["home_score"], a["away_score"]) == (1, 2)
    home = next(o for o in a["outcomes"] if o["side"] == "home")
    away = next(o for o in a["outcomes"] if o["side"] == "away")
    assert home["pre_price"] == 0.60 and home["won"] is False and away["won"] is True
    assert home["min_price"] == 0.20 and home["max_price"] == 0.62
    assert home["final_price"] == 0.001                       # last bar before resolution
    s10 = home["swing_10m"]
    assert s10["delta"] == pytest.approx(-0.42) and s10["from_price"] == 0.62
    assert s10["game_minute"] == pytest.approx(74.0) and s10["elapsed_min"] == pytest.approx(84.0)
    assert home["swing_1m"]["delta"] == pytest.approx(-0.14)   # 0.55 -> 0.41 (poll_mid beats history at 81')
    assert home["swing_1m"]["sources"] == "history>poll_mid"
    assert a["notable_swing"] and a["max_swing_10m"] == pytest.approx(-0.42)
    # the +0.449 post-whistle settlement jump is outside the play window
    assert away["swing_10m"]["delta"] == pytest.approx(0.36) and away["max_price"] == 0.55
    assert [e["scorer"] for e in a["events"]] == ["home", "away", "away"]
    assert all(len(o["path"]) <= games.PATH_POINTS for g in gm["games"] for o in g["outcomes"])
    b = _game(rep, "gB")
    assert b["result"] is None and not b["resolved"] and not b["upset"] and b["end_source"] == "running"
    assert b["favourite"] == "home" and b["outcomes"][0]["pre_price"] == 0.52
    d = _game(rep, "gD")
    assert d["end_source"] == "nominal" and d["result"] is None and d["outcomes"] == []
    soccer = next(s for s in gm["summary_by_sport"] if s["sport"] == "soccer")
    assert soccer["upsets"] == 1 and soccer["favourite_win_rate"] == 0.0 and soccer["favourite_avg_pre_price"] == 0.6


def test_downsample_caps_points():
    series = [(i * 60, 0.5 + (i % 7) / 100, "history") for i in range(1000)]
    path = games.downsample(series, 0, 1000 * 60)
    assert len(path) <= games.PATH_POINTS and path[-1][1] == round(series[-1][1], 4)
    assert games.max_swing([], 600) is None


def test_settlement_convergence_near_end_is_not_a_swing():
    play = [(0, 0.50, "history"), (60, 0.52, "history"), (3000, 0.50, "history"), (3060, 0.99, "history")]
    assert games.max_swing(play, 600)["delta"] == pytest.approx(0.49)
    s = games.max_swing(play, 600, settle_from=3060 - 300)
    assert s["delta"] == pytest.approx(0.02)          # the late 0.50 -> 0.99 convergence is skipped
    mid = [(0, 0.50, "history"), (60, 0.99, "history"), (4000, 0.99, "history")]
    assert games.max_swing(mid, 600, settle_from=3500)["delta"] == pytest.approx(0.49)   # early decisive move kept
    assert games.when({"game_minute": 0.5, "period": "Top 1st", "elapsed_min": 30.0}, "mlb") == "Top 1st"
    assert games.when({"game_minute": None, "period": None, "elapsed_min": 30.0}, "nhl") == "+30m"


def test_strategy_sport_money_rules(world):
    paths, _ = world
    rep = build.build("daily", paths, now=NOW, slot="evening", use_jenkins=False)
    rows = {(r["variant_id"], r["mode"], r["sport"]): r for r in rep["strategy_sport"]}
    live = rows[("watermelon-cat", "live", "soccer")]
    assert live["all"]["settled"] == 3 and live["all"]["wins"] == 2 and live["all"]["losses"] == 1
    assert live["all"]["realized_pnl"] == pytest.approx(0.35 * 2 - 1.2)
    assert live["all"]["fees_usdc"] == pytest.approx(0.03) and live["all"]["fees_unknown"] == 0
    assert live["open"] == 1 and live["unrealized_pnl"] == pytest.approx(5.37 * 0.95 - 5.0, abs=1e-3)
    paper = rows[("plum-king", "paper", "mlb")]
    assert paper["window"]["realized_pnl"] == 4.0 and paper["window"]["fees_unknown"] == 1
    assert paper["window"]["fees_usdc"] == 0.0
    # paper money never reaches live totals
    assert rep["totals"]["all"] == pytest.approx(0.35 * 2 - 1.2)
    best, worst = build.best_worst(rep["strategy_sport"])
    assert best["variant_id"] == "plum-king" and best["mode"] == "paper"
    assert worst["variant_id"] == "watermelon-cat"


def test_render_sections_and_slack(world):
    paths, _ = world
    rep = build.build("daily", paths, now=NOW, slot="evening", use_jenkins=False)
    md = render.render(rep)
    assert "## 지난 24시간 경기와 확률 움직임" in md and "## 전략 × 종목 손익" in md
    assert "#### 축구 · epl" in md and "업셋" in md
    assert "-0.42 (" in md and "74'" in md and "범위 밖 축구 제외 1경기(fif 1)" in md
    assert "+미확인 1" in md
    assert render.render(rep) == md
    text, blocks = slack.report_blocks(rep, "u")
    body = json.dumps(blocks, ensure_ascii=False)
    assert "*24h 경기*" in body and "*최대 스윙*" in body and "*업셋*" in body and "최고 `plum-king`(paper)" in body
    lines = slack.games_lines(rep)
    assert 3 <= len(lines) <= 5


def test_weekly_has_no_games_section(world):
    paths, _ = world
    rep = build.build("weekly", paths, now=NOW, use_jenkins=False)
    assert rep["games"] is None
    md = render.render(rep)
    assert "## 지난 24시간 경기와 확률 움직임" not in md and "## 전략 × 종목 손익 (주간)" in md


def test_games_24h_object_matches_contract(world):
    paths, reg = world
    objs = snapshot.build_objects(paths, now=NOW, use_jenkins=False, variants=registry.load_all(reg, include_off=True))
    obj = objs["latest/games_24h.json"]
    assert_shape(contract_examples()["latest/games_24h.json"], obj)
    assert any(g["game_key"] == "gA" for g in obj["games"])
    json.dumps(obj)


def test_markdown_caps_games(world):
    paths, _ = world
    rep = build.build("daily", paths, now=NOW, slot="evening", use_jenkins=False)
    base = _game(rep, "gB")
    rows = [{**base, "game_key": f"x{i:02d}", "title": f"Game {i}", "volume_usd": float(i), "upset": False,
             "notable_swing": False, "traded": False} for i in range(40)]
    rep["games"]["games"] = rows
    shown = games.top_games(rows)
    assert len(shown) == games.MAX_MD_GAMES and "x00" not in {g["game_key"] for g in shown}
    assert "외 10경기 생략" in render.render(rep)


def test_missing_core_db(tmp_path, monkeypatch):
    paths, _ = make_world(tmp_path, monkeypatch)
    paths.core_db.unlink()
    out = games.build(paths, NOW - 86400, NOW)
    assert out["games"] == [] and out["error"]
