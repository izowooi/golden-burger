import json

import pytest

from polylab import db, settings
from polylab.analysis import explore
from polylab.publish import snapshot

NOW = 1_790_000_000 // 60 * 60
START = NOW - 2 * 86400  # older games: resolved, inside the 7-day browser window
WALL = 140  # NBA wall minutes (fraction_played fallback)


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYLAB_ROOT", str(tmp_path))
    return settings.paths()


def add_game(conn, key, start, home_won, home_path, sport="nba", league="nba", minutes=WALL + 10, ended=True):
    """home_path(minute) -> home price; away = 1 - home. Bars from 30 min before start to the end."""
    conn.execute("INSERT INTO games(game_key, sport, league, title, home_team, away_team, start_time, status,"
                 " ended_at, first_seen, updated_at, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 (key, sport, league, f"A{key} vs H{key}", f"H{key}", f"A{key}", start, "ended" if ended else "live",
                  start + minutes * 60 if ended else None, start, start, "history_backfill"))
    cond = f"c{key}"
    conn.execute("INSERT INTO markets(condition_id, game_key, market_type, resolved_outcome_index, resolved_at,"
                 " closed, volume, updated_at) VALUES(?,?,?,?,?,1,1000,?)",
                 (cond, key, "moneyline", (0 if home_won else 1) if ended else None, start + 5 * 3600, start))
    conn.execute("INSERT INTO tokens VALUES(?,?,?,?,?)", (f"{key}h", cond, 0, f"H{key}", "home"))
    conn.execute("INSERT INTO tokens VALUES(?,?,?,?,?)", (f"{key}a", cond, 1, f"A{key}", "away"))
    for minute in range(-30, minutes):
        ts = start + minute * 60
        p = home_path(minute)
        conn.execute("INSERT INTO price_bars VALUES(?,?,?,?,?)", (f"{key}h", ts, "history", p, ts))
        conn.execute("INSERT INTO price_bars VALUES(?,?,?,?,?)", (f"{key}a", ts, "history", round(1 - p, 4), ts))
    # post-game bar far after the end must be ignored
    conn.execute("INSERT INTO price_bars VALUES(?,?,?,?,?)", (f"{key}h", start + max(minutes + 60, 300) * 60, "history", 0.999, 0))


@pytest.fixture()
def world(paths):
    conn = db.core(paths)
    # 40 games: home priced 0.60 flat pre-game and in play, home wins 30 of 40 (under-priced by 0.15)
    for i in range(40):
        won = i % 4 != 0

        def path(m, won=won):
            if m < 100:
                return 0.60
            return 0.95 if won else 0.05  # late resolution drift
        add_game(conn, f"g{i}", START - i * 3600, won, path)
    # a swing game: +0.2 jump at minute 120 (late), recent enough for the browser
    add_game(conn, "swing", START + 3600, True, lambda m: 0.5 if m < 120 else 0.7)
    # implausible end (5 h after start for NBA) -> excluded from aggregates
    add_game(conn, "long", START, True, lambda m: 0.5, minutes=400)
    # soccer outside the major set -> excluded from soccer aggregates
    add_game(conn, "minor", START, True, lambda m: 0.5, sport="soccer", league="zz1", minutes=120)
    conn.execute("INSERT INTO game_states(game_key, ts, received_at, source, game_minute, home_score, away_score)"
                 " VALUES('swing', ?, ?, 'ws_sports', 10, 0, 0)", (START + 3600 + 600, START + 3600 + 600))
    conn.execute("INSERT INTO game_states(game_key, ts, received_at, source, game_minute, home_score, away_score)"
                 " VALUES('swing', ?, ?, 'ws_sports', 30, 3, 0)", (START + 3600 + 7200, START + 3600 + 7200))
    conn.commit()
    s = db.strategy(paths, "watermelon-cat")
    s.execute("INSERT INTO positions(position_id, mode, param_version, stake_usdc, sport, game_key, condition_id,"
              " token_id, outcome_label, opened_at, entry_price, shares, cost_usdc, status, closed_at, exit_reason,"
              " exit_price, realized_pnl) VALUES('p1','live',1,5,'nba','swing','cswing','swingh','Hswing',?,0.7,7,5,"
              "'resolved',?, 'resolution_win', 1.0, 2.0)", (START + 3600 + 125 * 60, START + 3600 + 150 * 60))
    s.commit()
    s.close()
    return conn


def test_heatmap_reliability_gap_and_exclusions(world):
    agg = explore.aggregates(world, NOW)
    nba = agg["nba"]
    assert nba["scope"]["games"] == 41 and nba["scope"]["excluded_games"]["implausible_end"] == 1
    cells = {(c["col"], c["b"]): c for c in nba["heatmap"]}
    home_pre = cells[("pre", 12)]  # 0.60 bucket, closing line
    assert home_pre["n"] == 40 and home_pre["games"] == 40
    assert home_pre["mean_price"] == pytest.approx(0.60) and home_pre["gap"] == pytest.approx(0.15)
    assert cells[("pre", 8)]["gap"] == pytest.approx(-0.15)  # mirrored away side
    assert cells[("t0", 12)]["n"] == 40
    lo, hi = cells[("t0", 12)]["ci_lo"], cells[("t0", 12)]["ci_hi"]
    assert lo < 0.75 < hi
    rel = {r["phase"]: r["points"] for r in nba["reliability"]}
    assert [p["p_lo"] for p in rel["pre"]] == [0.4, 0.5, 0.6]
    assert agg["soccer"]["scope"]["games"] == 0  # zz1 is not a major league
    assert nba["min_n"] == explore.MIN_N and len(nba["time_cols"]) == 12


def test_trajectory_winner_loser_and_favourite(world):
    traj = explore.aggregates(world, NOW)["nba"]["trajectory"]
    series = {s["key"]: s for s in traj["series"]}
    assert series["winner"]["tokens"] == 41 and series["loser"]["tokens"] == 41
    pre = {p["x"]: p for p in series["fav_won"]["points"]}[-0.1]
    assert pre["mean"] == pytest.approx(0.60) and pre["n"] == 30
    assert series["fav_lost"]["tokens"] == 10 and series["dog_won"]["tokens"] == 10
    end_w = {p["x"]: p for p in series["winner"]["points"]}[1.0]
    assert end_w["p50"] == pytest.approx(0.95)
    assert max(p["x"] for p in series["winner"]["points"]) <= 1.1  # nothing after ended_at


def test_swings_bucket_the_late_jump(world):
    rows = {(r["col"], r["window"]): r for r in explore.aggregates(world, NOW)["nba"]["swings"]}
    t0 = rows[("t0", "1m")]
    assert t0["p99"] == 0 and sum(t0["counts"]) == t0["n"]
    # other games jump 0.35 at wall minute 100 (f = 100/140 -> t7); the swing game's 0.2 jump lands where the
    # feed clock says (30 of 48 min -> t6), not at its wall-clock minute 120 (t8)
    assert rows[("t7", "1m")]["share_ge_10"] > 0 and rows[("t6", "1m")]["share_ge_10"] > 0
    assert rows[("t8", "1m")]["share_ge_10"] == 0
    assert rows[("t7", "10m")]["share_ge_10"] > rows[("t7", "1m")]["share_ge_10"]


def test_browser_paths_scores_positions(world, paths):
    index, games = explore.browser(world, paths, NOW)
    keys = [g["game_key"] for g in index["games"]]
    assert "swing" in keys and keys[0] == "swing"  # newest first
    g = games["swing"]
    assert [o["side"] for o in g["outcomes"]] == ["home", "away"]
    assert all(len(o["points"]) <= explore.MAX_POINTS for o in g["outcomes"])
    prices = [p for _, p in g["outcomes"][0]["points"]]
    assert min(prices) == 0.5 and max(prices) == 0.7  # min/max downsampling keeps the jump
    assert g["score_events"][0]["home_score"] == 3 and g["score_events"][0]["scorer"] == "home"
    assert g["positions"][0]["variant_id"] == "watermelon-cat" and g["positions"][0]["side"] == "home"
    assert "generated_at" not in g  # stable bytes -> unchanged games are not re-uploaded


def test_run_writes_cache_and_contract_shape(world, paths):
    res = explore.run(paths, now=NOW, conn=world)
    files = explore.cached_files(paths)
    assert {"latest/explore/nba.json", "latest/explore/games_index.json",
            "latest/explore/games/swing.json"} <= set(files)
    assert res["sports"]["nba"] == 41
    from test_publish_snapshot import assert_shape, contract_examples
    ex = contract_examples()
    assert_shape(ex["latest/explore/<sport>.json"], json.loads(files["latest/explore/nba.json"].read_text()))
    assert_shape(ex["latest/explore/games_index.json"], json.loads(files["latest/explore/games_index.json"].read_text()))
    assert_shape(ex["latest/explore/games/<game_key>.json"],
                 json.loads(files["latest/explore/games/swing.json"].read_text()))


class FakeStorage:
    def __init__(self):
        self.paths = []

    def upload(self, path, body, content_type="application/json"):
        self.paths.append(path)


def test_publish_explore_hourly_and_changed_only(world, paths, monkeypatch):
    real_run = explore.run
    calls = []

    def fake_run(p, now=None):
        calls.append(now)
        return real_run(p, now=NOW, conn=world)  # fixed stamp -> identical bytes on the second run
    monkeypatch.setattr(explore, "run", fake_run)
    st = FakeStorage()
    first = snapshot.publish_explore(paths, st, now=NOW)
    assert "latest/explore/games/swing.json" in first and "latest/explore/nba.json" in first
    assert st.paths == first and len(calls) == 1
    assert snapshot.publish_explore(paths, st, now=NOW + 600) == []  # within the hour: no recompute
    assert len(calls) == 1
    assert snapshot.publish_explore(paths, st, now=NOW + 3600) == []  # recomputed, nothing changed
    assert len(calls) == 2
    assert snapshot.publish_explore(paths, st, now=NOW + 3700, force=True) == [] and len(calls) == 3
