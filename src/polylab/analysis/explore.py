"""Visual exploration aggregates for the dashboard `/explore` page (docs/contracts/dashboard-json.md).

Per sport (soccer = MAJOR_SOCCER_LEAGUES only):
  heatmap     calibration gap by price bucket (0.05) x normalised game time column (pre, 10 deciles, 100%+)
  reliability price (0.1 buckets) vs realised win rate per phase (pre/early/mid/late/final), Wilson 95% CI
  trajectory  mean/IQR price path over normalised game time of the eventual winner/loser and of the
              pre-game favourite/underdog split by result
  swings      |Δp| histograms of 1-minute and 10-minute changes per game-time column
Plus a game browser for games that started in the last 7 days (price path per outcome, score changes,
strategy positions).

Normalised game time f follows analysis._common (game clock / regulation, else wall-clock elapsed /
WALL_MINUTES). Rows are cut at games.ended_at; games without a plausible ended_at are excluded because
post-whistle 0.99/0.01 bars would pollute the late columns.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from polylab import db
from polylab.analysis import _common as C
from polylab.analysis import events as ev
from polylab.collector.common import MAJOR_SOCCER_LEAGUES

SPORTS = C.SPORTS
MIN_N = 30
HEAT_STEP = 0.05
REL_STEP = 0.10
PRE_WINDOW_S = 6 * 3600
DURATION_BAND = (0.6, 2.5)              # plausible ended_at - start_time, x WALL_MINUTES
TIME_COLS = ([{"key": "pre", "label": "경기 전", "lo": None, "hi": 0.0}]
             + [{"key": f"t{i}", "label": f"{i * 10}–{i * 10 + 10}%", "lo": i / 10, "hi": (i + 1) / 10}
                for i in range(10)]
             + [{"key": "ot", "label": "100%+", "lo": 1.0, "hi": None}])
PHASE_KEYS = ("pre", "early", "mid", "late", "final")
GRID_LO, GRID_HI, GRID_STEP = -0.2, 1.2, 0.02
GRID = np.round(np.arange(GRID_LO, GRID_HI + GRID_STEP / 2, GRID_STEP), 2)
FFILL_BINS = 2
SWING_EDGES = (0.0, 0.002, 0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, 0.20, 0.30, 1.01)
BROWSER_DAYS = 7
MAX_POINTS = 300
GAME_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # file name and dashboard route segment
SOURCE_RANK_SQL = "CASE pb.source WHEN 'poll_mid' THEN 0 WHEN 'ws_last' THEN 1 ELSE 2 END"

TRAJ_SERIES = (("winner", "최종 승자"), ("loser", "최종 패자"),
               ("fav_won", "경기 전 우세 → 승"), ("fav_lost", "경기 전 우세 → 패"),
               ("dog_won", "경기 전 열세 → 승"), ("dog_lost", "경기 전 열세 → 패"))

NOTES = [
    "time = normalised fraction f of regulation (game clock / soccer 90, NBA 48, NFL/NHL 60, MLB 9 innings; "
    "without a clock wall-clock elapsed / soccer 112, NBA 140, NFL 195, NHL 155, MLB 180 min); "
    "columns pre, 10 deciles, 100%+ (overtime/stoppage until games.ended_at)",
    "calibration unit = one canonical 1-minute price per resolved outcome token per column "
    "(pre = last bar before start, others = first bar in the column); outcome tokens = home/away/draw; "
    "gap = win rate - mean price (positive = under-priced); 2-way sports enter both sides, so the map is "
    "mirror-symmetric and n counts each game twice — `games` is the independent count",
    "trajectory: decisive games only (soccer draws excluded), home/away tokens, last price per 0.02 f bin, "
    "forward-filled at most 2 bins; favourite = higher closing pre-game price; later bins hold fewer "
    "(longer) games — see n",
    "swings: |p(t)-p(t-1m)| and |p(t)-p(t-10m)| on each token's dominant price source with exact gaps, "
    "all outcome tokens, bucketed by the column of the later bar",
]


# ------------------------------------------------------------------ loading

def _games(conn: sqlite3.Connection) -> pd.DataFrame:
    return C.read_sql(conn, "SELECT game_key, sport, league, title, home_team, away_team, start_time, ended_at,"
                            " status, home_score, away_score FROM games WHERE start_time IS NOT NULL")


def _tokens(conn: sqlite3.Connection, game_keys: list[str] | None = None) -> pd.DataFrame:
    """Outcome tokens (home/away/draw), the highest-volume market per (game, side)."""
    sql = ("SELECT t.token_id, t.side, t.outcome_label, t.outcome_index, m.resolved_outcome_index, m.volume,"
           " m.game_key FROM tokens t JOIN markets m ON m.condition_id = t.condition_id"
           " WHERE m.market_type IN ('moneyline','draw') AND t.side IN ('home','away','draw')")
    if game_keys is None:
        tok = C.read_sql(conn, sql)
    else:
        frames = [C.read_sql(conn, sql + f" AND m.game_key IN ({','.join('?' * len(g))})", g)
                  for g in C._chunks(sorted(set(game_keys))) if g]
        tok = pd.concat(frames, ignore_index=True) if frames else C.read_sql(conn, sql + " AND 0")
    tok["volume"] = pd.to_numeric(tok["volume"], errors="coerce").fillna(0)
    tok = tok.sort_values("volume", ascending=False).drop_duplicates(["game_key", "side"])
    res = pd.to_numeric(tok["resolved_outcome_index"], errors="coerce")
    tok["won"] = np.where(res.notna(), (tok["outcome_index"] == res).astype(float), np.nan)
    return tok.drop(columns=["volume"]).reset_index(drop=True)


def _bars(conn: sqlite3.Connection, windows: pd.DataFrame) -> pd.DataFrame:
    """windows: tid, token_id, lo, hi -> numeric bars (tid, ts, rank, price) inside [lo, hi)."""
    cols = ["tid", "ts", "rank", "price"]
    if windows.empty:
        return pd.DataFrame(columns=cols)
    conn.execute("DROP TABLE IF EXISTS temp.explore_tk")
    conn.execute("CREATE TEMP TABLE explore_tk (token_id TEXT PRIMARY KEY, tid INTEGER, lo INTEGER, hi INTEGER)"
                 " WITHOUT ROWID")
    conn.executemany("INSERT OR IGNORE INTO temp.explore_tk VALUES (?,?,?,?)",
                     zip(windows["token_id"], windows["tid"].astype(int).tolist(),
                         windows["lo"].astype(int).tolist(), windows["hi"].astype(int).tolist()))
    rows = conn.execute(f"SELECT tk.tid, pb.ts, {SOURCE_RANK_SQL}, pb.price FROM temp.explore_tk tk"
                        f" JOIN price_bars pb ON pb.token_id = tk.token_id AND pb.ts >= tk.lo AND pb.ts < tk.hi"
                        ).fetchall()
    conn.execute("DROP TABLE IF EXISTS temp.explore_tk")
    if not rows:
        return pd.DataFrame(columns=cols)
    a = np.asarray(rows, dtype=np.float64)
    return pd.DataFrame({"tid": a[:, 0].astype(np.int64), "ts": a[:, 1].astype(np.int64),
                         "rank": a[:, 2].astype(np.int8), "price": a[:, 3]})


def _canonical(bars: pd.DataFrame) -> pd.DataFrame:
    """One bar per (tid, ts) by source priority poll_mid > ws_last > history."""
    b = bars.sort_values(["tid", "ts", "rank"], kind="mergesort")
    return b.drop_duplicates(["tid", "ts"], keep="first").reset_index(drop=True)


def _valid_games(games: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    wall = games["sport"].map(C.WALL_MINUTES)
    dur = (pd.to_numeric(games["ended_at"], errors="coerce") - games["start_time"]) / 60.0
    ok = dur.between(wall * DURATION_BAND[0], wall * DURATION_BAND[1])
    stats = {"no_end": int(games["ended_at"].isna().sum()), "implausible_end": int((games["ended_at"].notna() & ~ok).sum())}
    return games[ok].copy(), stats


def _frac(df: pd.DataFrame, conn: sqlite3.Connection | None, states: pd.DataFrame | None = None) -> pd.Series:
    """Normalised game time for rows with game_key, sport, ts, start_time (game clock where known)."""
    gm = pd.Series(np.nan, index=df.index)
    if states is None and conn is not None:
        states = C.game_states(conn, df["game_key"].unique().tolist())
    if states is not None and not states.empty:
        has = df["game_key"].isin(set(states["game_key"]))
        if has.any():
            left = df.loc[has, ["game_key", "ts", "sport"]].reset_index()
            left["game_key"] = left["game_key"].astype(str)
            sub = C.attach_state(left, states.assign(game_key=states["game_key"].astype(str)))
            gm.loc[sub["index"].to_numpy()] = sub["game_minute"].to_numpy()
    frac = C.fraction_played(df["sport"], df["ts"], df["start_time"], gm)
    pre = (df["ts"] < df["start_time"]) & gm.isna() | (gm.notna() & (gm <= 0))
    return frac.where(~pre, np.minimum(frac.fillna(-0.01), -1e-9))


def _time_col(frac: pd.Series) -> np.ndarray:
    f = frac.to_numpy()
    col = np.where(f < 0, 0, np.where(f >= 1, 11, 1 + np.floor(np.clip(f, 0, 0.999999) * 10)))
    return np.where(np.isnan(f), -1, col).astype(np.int8)


def _phase(frac: pd.Series) -> np.ndarray:
    f = frac.to_numpy()
    out = np.full(len(f), -1, dtype=np.int8)
    out[f < 0] = 0
    lo = 0.0
    for i, (cut, _) in enumerate(C.PHASE_CUTS, start=1):
        out[(f >= lo) & (f < cut)] = i
        lo = cut
    out[f >= lo] = len(C.PHASE_CUTS) + 1
    return out


# ------------------------------------------------------------------ aggregates

def _wilson(k: np.ndarray, n: np.ndarray, z: float = 1.96) -> tuple[np.ndarray, np.ndarray]:
    n = n.astype(float)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return np.clip(centre - half, 0, 1), np.clip(centre + half, 0, 1)


def _first_per(obs: pd.DataFrame, key: str) -> pd.DataFrame:
    """pre (key==0) -> last bar per token, other values -> first bar per (token, key)."""
    obs = obs[obs[key] >= 0]
    pre = obs[obs[key] == 0].drop_duplicates(["tid"], keep="last")
    live = obs[obs[key] > 0].drop_duplicates(["tid", key], keep="first")
    return pd.concat([pre, live], ignore_index=True)


def _calib_table(obs: pd.DataFrame, key: str, step: float) -> list[dict]:
    if obs.empty:
        return []
    nb = int(round(1 / step))
    o = obs.assign(b=np.minimum(np.floor(obs["price"] / step + 1e-9).astype(int), nb - 1))
    g = o.groupby([key, "b"]).agg(n=("won", "size"), k=("won", "sum"), games=("game_key", "nunique"),
                                  mean_price=("price", "mean")).reset_index()
    lo, hi = _wilson(g["k"].to_numpy(), g["n"].to_numpy())
    g["win_rate"] = g["k"] / g["n"]
    g["gap"] = g["win_rate"] - g["mean_price"]
    g["ci_lo"], g["ci_hi"] = lo, hi
    return [{"col": int(getattr(r, key)), "b": int(r.b), "p_lo": round(r.b * step, 2), "p_hi": round((r.b + 1) * step, 2),
             "n": int(r.n), "games": int(r.games), "mean_price": round(float(r.mean_price), 4),
             "win_rate": round(float(r.win_rate), 4), "gap": round(float(r.gap), 4),
             "ci_lo": round(float(r.ci_lo), 4), "ci_hi": round(float(r.ci_hi), 4)} for r in g.itertuples()]


def heatmap(obs: pd.DataFrame) -> list[dict]:
    rows = _calib_table(_first_per(obs, "col"), "col", HEAT_STEP)
    for r in rows:
        r["col"] = TIME_COLS[r.pop("col")]["key"]
    return rows


def reliability(obs: pd.DataFrame) -> list[dict]:
    rows = _calib_table(_first_per(obs, "phase"), "phase", REL_STEP)
    out = []
    for i, phase in enumerate(PHASE_KEYS):
        pts = [{k: v for k, v in r.items() if k not in ("col", "b")} for r in rows if r["col"] == i]
        out.append({"phase": phase, "points": pts})
    return out


def trajectory(obs: pd.DataFrame, tok: pd.DataFrame) -> dict:
    """obs: canonical bars with tid, frac, price; tok: tid, game_key, side, won."""
    empty = {"grid": GRID.tolist(), "min_n": MIN_N, "series": []}
    team = tok[tok["side"].isin(("home", "away")) & tok["won"].notna()]
    decisive = team.groupby("game_key")["won"].transform("sum") == 1
    team = team[decisive & (team.groupby("game_key")["tid"].transform("size") == 2)]
    o = obs[obs["tid"].isin(set(team["tid"])) & obs["frac"].notna()]
    o = o[(o["frac"] >= GRID_LO - GRID_STEP / 2) & (o["frac"] < GRID_HI + GRID_STEP / 2)]
    if o.empty:
        return empty
    gi = np.rint((o["frac"].to_numpy() - GRID_LO) / GRID_STEP).astype(int)
    last = o.assign(g=gi).drop_duplicates(["tid", "g"], keep="last")
    tids = np.sort(team["tid"].unique())
    row = np.searchsorted(tids, last["tid"].to_numpy())
    mat = np.full((len(tids), len(GRID)), np.nan)
    mat[row, last["g"].to_numpy()] = last["price"].to_numpy()
    mat = pd.DataFrame(mat).ffill(axis=1, limit=FFILL_BINS).to_numpy()
    # closing pre-game price picks the favourite
    pre = obs[obs["tid"].isin(set(tids)) & (obs["frac"] < 0)].drop_duplicates(["tid"], keep="last")
    t = team.set_index("tid").loc[tids].reset_index()
    t["pre_price"] = t["tid"].map(pre.set_index("tid")["price"])
    other = t.groupby("game_key")["pre_price"].transform("sum") - t["pre_price"]
    both = t.groupby("game_key")["pre_price"].transform("count") == 2
    t["fav"] = np.where(~both | (t["pre_price"] == other), np.nan,
                        (t["pre_price"] > other).astype(float))
    won = t["won"].to_numpy() == 1
    fav = t["fav"].to_numpy()
    masks = {"winner": won, "loser": ~won, "fav_won": (fav == 1) & won, "fav_lost": (fav == 1) & ~won,
             "dog_won": (fav == 0) & won, "dog_lost": (fav == 0) & ~won}
    series = []
    for key, label in TRAJ_SERIES:
        m = mat[masks[key]]
        pts = []
        with warnings.catch_warnings():  # all-NaN grid columns are expected (n == 0 there)
            warnings.simplefilter("ignore", RuntimeWarning)
            n = np.sum(~np.isnan(m), axis=0)
            if len(m):
                mean = np.nanmean(m, axis=0)
                q = np.nanpercentile(m, [25, 50, 75], axis=0)
        for j, x in enumerate(GRID):
            if n[j] == 0:
                continue
            pts.append({"x": float(x), "n": int(n[j]), "mean": round(float(mean[j]), 4),
                        "p25": round(float(q[0, j]), 4), "p50": round(float(q[1, j]), 4),
                        "p75": round(float(q[2, j]), 4)})
        series.append({"key": key, "label": label, "tokens": int(masks[key].sum()), "points": pts})
    return {"grid": GRID.tolist(), "min_n": MIN_N, "series": series}


def swings(bars: pd.DataFrame, colmap: pd.DataFrame) -> list[dict]:
    """bars: raw (tid, ts, rank, price); colmap: canonical (tid, ts, col) for column lookup."""
    if bars.empty:
        return []
    dom = bars.groupby(["tid", "rank"]).size().reset_index(name="c").sort_values(["tid", "c"], ascending=[True, False])
    dom = dom.drop_duplicates("tid").set_index("tid")["rank"]
    b = bars[bars["rank"].to_numpy() == bars["tid"].map(dom).to_numpy()].sort_values(["tid", "ts"])
    key = b["tid"].to_numpy() * 4_000_000_000 + b["ts"].to_numpy()
    price = b["price"].to_numpy()
    cm = colmap.sort_values(["tid", "ts"])
    ckey = cm["tid"].to_numpy() * 4_000_000_000 + cm["ts"].to_numpy()
    idx = np.searchsorted(ckey, key)
    ok = idx < len(ckey)
    col = np.full(len(key), -1, dtype=np.int16)
    col[ok] = np.where(ckey[idx[ok]] == key[ok], cm["col"].to_numpy()[idx[ok]], -1)
    out = []
    for window, lag in (("1m", 60), ("10m", 600)):
        j = np.searchsorted(key, key - lag)
        hit = (j < len(key)) & (key[np.minimum(j, len(key) - 1)] == key - lag)
        d = np.abs(price[hit] - price[j[hit]])
        c = col[hit]
        for ci, tc in enumerate(TIME_COLS):
            dv = d[c == ci]
            if not len(dv):
                continue
            counts, _ = np.histogram(dv, bins=SWING_EDGES)
            q = np.percentile(dv, [50, 90, 99])
            out.append({"col": tc["key"], "window": window, "n": int(len(dv)), "counts": counts.astype(int).tolist(),
                        "p50": round(float(q[0]), 4), "p90": round(float(q[1]), 4), "p99": round(float(q[2]), 4),
                        "share_ge_5": round(float((dv >= 0.05).mean()), 5),
                        "share_ge_10": round(float((dv >= 0.10).mean()), 5)})
    return out


def aggregates(conn: sqlite3.Connection, now: int | None = None) -> dict[str, dict]:
    """{sport: explore aggregate object} over every valid resolved game in core.db."""
    now = now or int(time.time())
    games = _games(conn)
    games = games[(games["sport"] != "soccer") | games["league"].str.lower().isin(MAJOR_SOCCER_LEAGUES)]
    valid, excluded = _valid_games(games)
    tok = _tokens(conn).merge(valid[["game_key", "sport", "start_time", "ended_at"]], on="game_key")
    tok = tok.reset_index(drop=True)
    tok["tid"] = np.arange(len(tok))
    bars = _bars(conn, tok.assign(lo=tok["start_time"] - PRE_WINDOW_S, hi=tok["ended_at"]))
    canon = _canonical(bars)
    obs = canon.merge(tok[["tid", "game_key", "sport", "start_time", "won"]], on="tid", how="left")
    states = C.game_states(conn, valid["game_key"].tolist())
    obs["frac"] = _frac(obs, None, states).to_numpy()
    obs["col"] = _time_col(obs["frac"])
    obs["phase"] = _phase(obs["frac"])
    out = {}
    for sport in SPORTS:
        o = obs[obs["sport"] == sport]
        t = tok[tok["sport"] == sport]
        resolved = o[o["won"].notna()]
        g = valid[valid["sport"] == sport]
        out[sport] = {
            "sport": sport, "generated_at": C.iso(now),
            "scope": {"games": int(t.loc[t["won"].notna(), "game_key"].nunique()),
                      "tokens": int(t["won"].notna().sum()), "bars": int(len(o)),
                      "from": C.iso(g["start_time"].min()) if len(g) else None,
                      "to": C.iso(g["start_time"].max()) if len(g) else None,
                      "leagues": sorted(MAJOR_SOCCER_LEAGUES) if sport == "soccer" else None,
                      "excluded_games": {k: int(v) for k, v in _valid_games(games[games["sport"] == sport])[1].items()}},
            "min_n": MIN_N, "heat_step": HEAT_STEP, "rel_step": REL_STEP,
            "time_cols": TIME_COLS, "phases": list(PHASE_KEYS), "swing_edges": list(SWING_EDGES),
            "heatmap": heatmap(resolved),
            "reliability": reliability(resolved),
            "trajectory": trajectory(o, t),
            "swings": swings(bars[bars["tid"].isin(set(t["tid"]))], o[["tid", "ts", "col"]]),
            "notes": NOTES,
        }
    out["_excluded"] = excluded  # type: ignore[assignment]
    return out


# ------------------------------------------------------------------ game browser

def _downsample(ts: np.ndarray, price: np.ndarray, max_points: int = MAX_POINTS) -> list[list]:
    """Min/max per bucket keeps swings visible; <= max_points points."""
    if len(ts) > max_points:
        buckets = np.array_split(np.arange(len(ts)), max_points // 2)
        keep = sorted({int(i[np.argmin(price[i])]) for i in buckets if len(i)}
                      | {int(i[np.argmax(price[i])]) for i in buckets if len(i)})
        ts, price = ts[keep], price[keep]
    return [[C.iso(t), round(float(p), 4)] for t, p in zip(ts, price)]


def _positions(paths, game_keys: set[str]) -> pd.DataFrame:
    cols = ["variant_id", "mode", "game_key", "token_id", "outcome_label", "status", "opened_at", "entry_price",
            "shares", "stake_usdc", "closed_at", "exit_price", "exit_reason", "realized_pnl"]
    frames = []
    for f in sorted(Path(paths.strategies_dir).glob("*.db")):
        conn = C.open_ro(f)
        if conn is None:
            continue
        try:
            df = C.read_sql(conn, "SELECT mode, game_key, token_id, outcome_label, status, opened_at, entry_price,"
                                  " shares, stake_usdc, closed_at, exit_price, exit_reason, realized_pnl"
                                  " FROM positions WHERE entry_price IS NOT NULL")
        except sqlite3.Error:
            continue
        finally:
            conn.close()
        frames.append(df.assign(variant_id=f.stem))
    if not frames:
        return pd.DataFrame(columns=cols)
    df = pd.concat(frames, ignore_index=True)
    return df[df["game_key"].isin(game_keys)][cols]


def browser(conn: sqlite3.Connection, paths, now: int | None = None) -> tuple[dict, dict[str, dict]]:
    """(games_index, {game_key: game object}) for games started in the last BROWSER_DAYS days."""
    now = now or int(time.time())
    games = _games(conn)
    games = games[(games["start_time"] >= now - BROWSER_DAYS * 86400) & (games["start_time"] <= now)]
    index = {"generated_at": C.iso(now), "window_days": BROWSER_DAYS, "max_points": MAX_POINTS, "games": []}
    if games.empty:
        return index, {}
    tok = _tokens(conn, games["game_key"].tolist()).merge(
        games[["game_key", "sport", "start_time", "ended_at"]], on="game_key").reset_index(drop=True)
    tok["tid"] = np.arange(len(tok))
    cap = tok["sport"].map(lambda s: C.WALL_MINUTES.get(s, 180) * 60 * DURATION_BAND[1])
    hi = pd.to_numeric(tok["ended_at"], errors="coerce").fillna(np.minimum(tok["start_time"] + cap, now)) + 60
    canon = _canonical(_bars(conn, tok.assign(lo=tok["start_time"] - 2 * 3600, hi=hi)))
    states = C.game_states(conn, games["game_key"].tolist())
    scores = ev.detect_score_changes(states, games)
    positions = _positions(paths, set(games["game_key"]))
    by_tid = {t: g for t, g in canon.groupby("tid")}
    objs = {}
    order = {"home": 0, "away": 1, "draw": 2}
    for g in games.sort_values("start_time", ascending=False).itertuples(index=False):
        if not GAME_KEY.match(str(g.game_key)):
            continue
        gt = tok[tok["game_key"] == g.game_key].sort_values("side", key=lambda s: s.map(order))
        outcomes = []
        for t in gt.itertuples(index=False):
            b = by_tid.get(t.tid)
            if b is None or b.empty:
                continue
            label = t.outcome_label if t.side != "draw" else "무승부"
            if t.side in ("home", "away"):
                label = (g.home_team if t.side == "home" else g.away_team) or t.outcome_label
            outcomes.append({"side": t.side, "label": label, "token_id": t.token_id,
                             "won": None if pd.isna(t.won) else bool(t.won),
                             "points": _downsample(b["ts"].to_numpy(), b["price"].to_numpy())})
        if not outcomes:
            continue
        sc = scores[scores["game_key"] == g.game_key] if not scores.empty else scores
        score_events = [{"at": C.iso(r.ts), "scorer": r.scorer, "points": int(r.points),
                         "home_score": int(r.home_score), "away_score": int(r.away_score),
                         "game_minute": None if pd.isna(r.game_minute) else round(float(r.game_minute), 1)}
                        for r in sc.itertuples(index=False)]
        side_of = dict(zip(gt["token_id"], gt["side"]))
        pos = [{"variant_id": p.variant_id, "mode": p.mode, "side": side_of.get(p.token_id),
                "outcome": p.outcome_label, "status": p.status, "opened_at": C.iso(p.opened_at),
                "entry_price": _num(p.entry_price), "shares": _num(p.shares), "stake_usdc": _num(p.stake_usdc),
                "closed_at": C.iso(p.closed_at) if pd.notna(p.closed_at) else None,
                "exit_price": _num(p.exit_price), "exit_reason": p.exit_reason, "realized_pnl": _num(p.realized_pnl)}
               for p in positions[positions["game_key"] == g.game_key].itertuples(index=False)]
        obj = {"game_key": g.game_key, "sport": g.sport, "league": g.league, "title": g.title,
               "home_team": g.home_team, "away_team": g.away_team, "start_time": C.iso(g.start_time),
               "ended_at": C.iso(g.ended_at) if pd.notna(g.ended_at) else None, "status": g.status,
               "home_score": _int(g.home_score), "away_score": _int(g.away_score),
               "outcomes": outcomes, "score_events": score_events, "positions": pos}
        objs[str(g.game_key)] = obj
        index["games"].append({k: obj[k] for k in ("game_key", "sport", "league", "title", "start_time", "ended_at",
                                                   "status", "home_score", "away_score")}
                              | {"outcomes": len(outcomes), "score_events": len(score_events),
                                 "positions": len(pos)})
    return index, objs


def _num(v) -> float | None:
    return None if v is None or pd.isna(v) else round(float(v), 4)


def _int(v) -> int | None:
    return None if v is None or pd.isna(v) else int(v)


# ------------------------------------------------------------------ cache

def cache_dir(paths) -> Path:
    return Path(paths.research_dir) / "explore"


def dump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":")).encode()


def run(paths, now: int | None = None, conn: sqlite3.Connection | None = None) -> dict:
    """Compute everything and rewrite <research_dir>/explore/ (storage-relative layout under latest/explore/)."""
    now = now or int(time.time())
    t0 = time.time()
    own = conn is None
    conn = conn or db.core(paths, readonly=True)
    try:
        agg = aggregates(conn, now)
        index, games = browser(conn, paths, now)
    finally:
        if own:
            conn.close()
    excluded = agg.pop("_excluded", {})
    root = cache_dir(paths)
    tmp = root.with_name("explore.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "games").mkdir(parents=True)
    for sport, obj in agg.items():
        (tmp / f"{sport}.json").write_bytes(dump(obj))
    (tmp / "games_index.json").write_bytes(dump(index))
    for key, obj in games.items():
        (tmp / "games" / f"{key}.json").write_bytes(dump(obj))
    shutil.rmtree(root, ignore_errors=True)
    tmp.replace(root)
    return {"generated_at": C.iso(now), "seconds": round(time.time() - t0, 1), "dir": str(root),
            "sports": {s: a["scope"]["games"] for s, a in agg.items()}, "excluded_games": excluded,
            "browser_games": len(games)}


def cached_files(paths) -> dict[str, Path]:
    """{storage path under latest/: local file} of the current explore cache."""
    root = cache_dir(paths)
    if not root.exists():
        return {}
    return {f"latest/explore/{p.relative_to(root).as_posix()}": p for p in sorted(root.rglob("*.json"))}


__all__ = ["aggregates", "browser", "run", "cached_files", "cache_dir", "TIME_COLS", "PHASE_KEYS"]
