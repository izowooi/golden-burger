import math

import pandas as pd
import pytest

from polylab import db, settings
from polylab.analysis import _common as C
from polylab.analysis import calibration, dataset, events, performance

T0 = 1_790_000_000 // 60 * 60  # game start (UTC, minute aligned)


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYLAB_ROOT", str(tmp_path))
    return settings.paths()


def add_game(conn, key, sport="soccer", start=T0, home_won=True, goal_minute=80, pre_price=0.60, jump=0.25):
    conn.execute("INSERT INTO games(game_key, sport, league, title, home_team, away_team, start_time, status,"
                 " ended_at, first_seen, updated_at, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 (key, sport, "EPL", f"Home{key} vs Away{key}", f"Home{key}", f"Away{key}", start, "ended",
                  start + 115 * 60, start, start, "discover"))
    cond = f"c{key}"
    conn.execute("INSERT INTO markets(condition_id, game_key, market_type, resolved_outcome_index, resolved_at,"
                 " closed, updated_at) VALUES(?,?,?,?,?,1,?)", (cond, key, "moneyline", 0 if home_won else 1,
                                                              start + 116 * 60, start))
    conn.execute("INSERT INTO tokens VALUES(?,?,?,?,?)", (f"{key}h", cond, 0, f"Home{key}", "home"))
    conn.execute("INSERT INTO tokens VALUES(?,?,?,?,?)", (f"{key}a", cond, 1, f"Away{key}", "away"))
    # states: kickoff 0-0, goal by home at goal_minute (wall == game minute for simplicity)
    conn.execute("INSERT INTO game_states(game_key, ts, received_at, source, game_minute, home_score, away_score)"
                 " VALUES(?,?,?,?,?,?,?)", (key, start, start, "ws_sports", 0.5, 0, 0))
    g_ts = start + goal_minute * 60 + 20
    conn.execute("INSERT INTO game_states(game_key, ts, received_at, source, game_minute, home_score, away_score)"
                 " VALUES(?,?,?,?,?,?,?)", (key, g_ts, g_ts, "ws_sports", goal_minute + 0.3, 1, 0))
    m = g_ts // 60 * 60
    for minute in range(-10, 100):
        ts = start + minute * 60
        if ts < m:
            p = pre_price
        elif ts == m:
            p = pre_price + jump / 2
        elif ts <= m + 3 * 60:
            p = pre_price + jump + 0.02  # overshoot
        else:
            p = pre_price + jump
        conn.execute("INSERT INTO price_bars VALUES(?,?,?,?,?)", (f"{key}h", ts, "history", p - 0.05, ts))
        conn.execute("INSERT INTO price_bars VALUES(?,?,?,?,?)", (f"{key}h", ts, "poll_mid", p, ts))
        conn.execute("INSERT INTO price_bars VALUES(?,?,?,?,?)", (f"{key}a", ts, "poll_mid", 1 - p, ts))
    conn.commit()


def test_wilson_matches_reference():
    lo, hi = calibration.wilson(9, 10)
    assert math.isclose(lo, 0.5958, abs_tol=1e-3) and math.isclose(hi, 0.9821, abs_tol=1e-3)
    assert calibration.wilson(0, 0) == (None, None)


def test_calibrate_buckets_gap_and_brier():
    obs = pd.DataFrame({"sport": ["soccer"] * 4, "phase": ["late"] * 4,
                        "price": [0.92, 0.93, 0.94, 0.91], "won": [1, 1, 1, 0]})
    cal, brier = calibration.calibrate(obs)
    late = next(c for c in cal if c["phase"] == "late")
    (b,) = late["buckets"]
    assert (b["p_lo"], b["p_hi"], b["n"]) == (0.9, 0.95, 4)
    assert math.isclose(b["gap"], 0.75 - 0.925, abs_tol=1e-4)
    assert {c["phase"] for c in cal} == {"all", "late"}
    exp = ((0.92 - 1) ** 2 + (0.93 - 1) ** 2 + (0.94 - 1) ** 2 + 0.91 ** 2) / 4
    assert math.isclose(next(r for r in brier if r["phase"] == "late")["brier"], exp, abs_tol=1e-4)


def test_canonical_price_prefers_poll_mid(paths):
    conn = db.core(paths)
    add_game(conn, "1")
    df = C.canonical_prices(conn, ["1h"])
    assert set(df["source"]) == {"poll_mid"}
    assert df["ts"].is_unique


def test_phase_series_uses_clock_and_wall_fallback():
    sport = pd.Series(["soccer", "soccer", "soccer", "nba", "soccer"])
    ts = pd.Series([T0 - 60, T0 + 600, T0 + 3000, T0 + 130 * 60, T0 + 200 * 60])
    start = pd.Series([T0] * 5)
    gm = pd.Series([None, 10.0, 80.0, None, None], dtype=float)
    ended = pd.Series([None, None, None, None, T0 + 120 * 60], dtype=float)
    assert C.phase_series(sport, ts, start, gm, ended).tolist() == ["pre", "early", "final", "final", None]


def test_load_observations_one_row_per_token_phase(paths):
    conn = db.core(paths)
    add_game(conn, "1", home_won=True)
    add_game(conn, "2", home_won=False, start=T0 + 86400)
    obs = calibration.load_observations(conn)
    assert not obs.duplicated(["token_id", "phase"]).any()
    assert set(obs["phase"]) == {"pre", "early", "mid", "late", "final"}
    home1 = obs[(obs.token_id == "1h")]
    assert home1["won"].eq(1).all()
    res = calibration.run(conn)
    assert res["games"] == 2 and res["calibration"]


def test_event_detection_and_reaction(paths):
    conn = db.core(paths)
    add_game(conn, "1", goal_minute=80, pre_price=0.60, jump=0.25)
    add_game(conn, "2", goal_minute=10, pre_price=0.45, jump=0.10, start=T0 + 86400)
    res = events.run(conn)
    assert res["events_detected"] == 2 and res["events_measured"] == 2
    by = {r["minute_bucket"]: r for r in res["event_sensitivity"]}
    assert set(by) == {"0-15", "75-90"}
    assert math.isclose(by["75-90"]["median_jump"], 0.27, abs_tol=1e-6)   # 0.60 -> 0.87 overshoot bar
    assert math.isclose(by["75-90"]["reversion_5m"], -0.02, abs_tol=1e-6)  # overshoot given back
    assert by["75-90"]["mean_abs_jump"] > by["0-15"]["mean_abs_jump"]
    assert res["by_score_state"][0]["score_state"] == "level"


def test_detect_ignores_lagging_sources_and_double_changes():
    # ws 1-0, lagging gamma poll still 0-0, ws 1-0 again, then both change at once, then a real away goal
    st = pd.DataFrame({"game_key": ["g"] * 6, "ts": [0, 100, 150, 200, 300, 400], "game_minute": [1, 2, 2, 3, 4, 5],
                       "home_score": [0, 1, 0, 1, 2, 2], "away_score": [0, 0, 0, 0, 1, 2]})
    ev = events.detect_score_changes(st)
    assert ev["scorer"].tolist() == ["home", "away"]
    assert ev.iloc[1]["diff_before"] == -1


def test_scorer_token_for_binary_yes_markets(paths):
    conn = db.core(paths)
    conn.execute("INSERT INTO games(game_key, sport, home_team, away_team, start_time, first_seen, updated_at,"
                 " source) VALUES('g','soccer','Arsenal','Chelsea',0,0,0,'discover')")
    for cid, team in (("ca", "Arsenal"), ("cc", "Chelsea"), ("cx", "Arsenal vs Chelsea")):
        conn.execute("INSERT INTO markets(condition_id, game_key, market_type, group_item_title, updated_at)"
                     " VALUES(?,?,?,?,0)", (cid, "g", "moneyline", team))
        conn.execute("INSERT INTO tokens VALUES(?,?,0,'Yes','yes')", (cid + "y", cid))
        conn.execute("INSERT INTO tokens VALUES(?,?,1,'No','no')", (cid + "n", cid))
    toks = events.scorer_tokens(conn, ["g"])
    assert toks == {("g", "home"): "cay", ("g", "away"): "ccy"}


def _strategy_db(paths, vid, rows):
    conn = db.strategy(paths, vid)
    for i, r in enumerate(rows):
        pid = f"p{i}"
        conn.execute("INSERT INTO positions(position_id, mode, param_version, stake_usdc, sport, condition_id,"
                     " token_id, opened_at, game_minute_at_entry, cost_usdc, status, closed_at, realized_pnl,"
                     " settlement) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (pid, r.get("mode", "live"), 1, r.get("stake", 5.0), "soccer", "c", "t", r["closed"] - 3600,
                      r.get("minute", 70), r.get("cost", 5.0), r.get("status", "resolved"), r["closed"],
                      r.get("pnl"), r.get("settlement", "resolution")))
        conn.execute("INSERT INTO orders(intent_id, position_id, created_at, mode, side, token_id, order_type,"
                     " status, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (f"i{i}", pid, 0, "live", "BUY", "t", "FOK", "confirmed", 0))
        if r.get("confirmed", True):
            conn.execute("INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?)",
                         (f"f{i}", f"i{i}", 0, "BUY", 0.9, 5.5, 0.0, "CONFIRMED", None))
    conn.commit()


def test_performance_filters_and_tiers(paths):
    now = T0 + 10 * 86400
    _strategy_db(paths, "v1", [
        {"closed": now - 3600, "pnl": 0.5},
        {"closed": now - 2 * 86400, "pnl": -5.0},
        {"closed": now - 20 * 86400, "pnl": 0.4, "stake": 10.0, "cost": 10.0},
        {"closed": now - 100, "pnl": 9.0, "confirmed": False},          # no confirmed BUY -> excluded
        {"closed": now - 100, "pnl": None, "status": "quarantined"},     # excluded
        {"closed": now - 100, "pnl": 0.3, "mode": "paper", "settlement": "paper"},
    ])
    res = performance.run(paths, ["v1"], now)
    live = res["variants"]["v1"]["live"]
    assert live["trades"] == {"all": 3, "wins": 2, "losses": 1}
    assert live["pnl"]["d7"] == -4.5 and live["pnl"]["all"] == -4.1
    assert res["variants"]["v1"]["paper"]["trades"]["all"] == 1
    assert res["variants"]["v1"]["excluded"]["live_without_confirmed_buy"] == 1
    tiers = {t["tier_usdc"]: t for t in res["stake_tiers"]}
    assert tiers[5.0]["trades"] == 2 and tiers[5.0]["max_drawdown"] == 5.0
    assert tiers[10.0]["pnl_std"] is None


def test_max_drawdown():
    assert performance.max_drawdown([1, -2, 3, -4, 1]) == 4
    assert performance.max_drawdown([]) == 0.0


def test_dataset_export(paths):
    conn = db.core(paths)
    add_game(conn, "1")
    conn.execute("INSERT INTO public_trades(uid, condition_id, token_id, ts, side, price, size, usd)"
                 " VALUES('u1','c1','1h',?, 'BUY', 0.6, 10, 6.0)", (T0 + 5 * 60 + 7,))
    bconn = db.books(paths, C.dt.datetime.fromtimestamp(T0, C.dt.timezone.utc).strftime("%Y-%m"))
    bconn.execute("INSERT INTO book_snapshots(token_id, ts, source, best_bid, best_ask, mid, spread, imb_l5)"
                  " VALUES('1h', ?, 'poll', 0.59, 0.61, 0.6, 0.02, 0.1)", (T0 + 5 * 60 + 30,))
    bconn.commit()
    files = dataset.export(conn, paths, T0, T0 + 3600)
    assert len(files) == 1
    df = pd.read_parquet(files[0])
    assert set(dataset.FEATURES) <= set(df.columns)
    row = df[(df.token_id == "1h") & (df.ts == T0 + 300)].iloc[0]
    assert row["volume_1m"] == 6.0 and row["bid"] == 0.59 and row["final_result"] == 1.0
    assert row["phase"] == "early" and pd.isna(row["probability_change_10s"])
