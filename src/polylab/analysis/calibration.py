"""Calibration of Polymarket prices vs realised outcomes, per sport x game phase.

Observation unit: one (token, phase) pair — the closing pre-game price, and the first canonical
1-minute price of each resolved whole-game token inside each in-play phase. Using every token-minute would count the
same game hundreds of times and make the Wilson intervals meaninglessly tight.
"""

from __future__ import annotations

import math
import sqlite3

import pandas as pd

from polylab.analysis import _common as C

BUCKET_EDGES = (0.0, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.98, 1.0)
WHOLE_GAME_TYPES = ("moneyline", "draw")
SAMPLING_NOTE = ("calibration unit = one canonical 1-minute price per resolved whole-game token per phase: "
                 "pre = last bar before start (closing line), in-play phases = first bar in the phase "
                 "(phase 'all' pools those rows); win = tokens.outcome_index == markets.resolved_outcome_index; "
                 "95% Wilson CI; gap = win_rate - mean_price (positive = underpriced). "
                 "All outcome tokens of a market enter (complementary Yes/No or home/away prices are dependent), "
                 "so n overstates independent observations and the CI is optimistic")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _bucket_index(price: float) -> int:
    for i in range(len(BUCKET_EDGES) - 1):
        if price < BUCKET_EDGES[i + 1]:
            return i
    return len(BUCKET_EDGES) - 2  # price == 1.0 goes in the top bucket


def calibrate(obs: pd.DataFrame, min_n: int = 1) -> tuple[list[dict], list[dict]]:
    """obs columns: sport, phase, price (0..1), won (0/1). Returns (calibration, brier) per contract."""
    calibration: list[dict] = []
    brier: list[dict] = []
    if obs.empty:
        return calibration, brier
    obs = obs.dropna(subset=["price", "won", "phase"]).copy()
    obs["won"] = obs["won"].astype(int)
    obs["bucket"] = obs["price"].map(_bucket_index)
    pooled = obs.assign(phase="all")
    full = pd.concat([pooled, obs], ignore_index=True)
    order = {p: i for i, p in enumerate(("all",) + C.PHASES)}
    groups = sorted(full.groupby(["sport", "phase"]), key=lambda kv: (kv[0][0], order.get(kv[0][1], 99)))
    for (sport, phase), g in groups:
        buckets = []
        for b, gb in g.groupby("bucket"):
            n = int(len(gb))
            if n < min_n:
                continue
            k = int(gb["won"].sum())
            lo, hi = wilson(k, n)
            mean_price = float(gb["price"].mean())
            win_rate = k / n
            buckets.append({"p_lo": BUCKET_EDGES[b], "p_hi": BUCKET_EDGES[b + 1], "n": n,
                            "mean_price": round(mean_price, 4), "win_rate": round(win_rate, 4),
                            "ci_lo": round(lo, 4), "ci_hi": round(hi, 4),
                            "gap": round(win_rate - mean_price, 4)})
        calibration.append({"sport": sport, "phase": phase, "buckets": buckets})
        brier.append({"sport": sport, "phase": phase, "n": int(len(g)),
                      "brier": round(float(((g["price"] - g["won"]) ** 2).mean()), 5)})
    return calibration, brier


def load_observations(conn: sqlite3.Connection, since: int | None = None,
                      until: int | None = None, all_leagues: bool = False) -> pd.DataFrame:
    """Resolved whole-game tokens with their first canonical price in each phase (soccer: major leagues
    unless all_leagues)."""
    lg_sql, lg_params = C.league_filter_sql("g", all_leagues)
    tok = C.read_sql(conn, f"""
        SELECT t.token_id, t.outcome_index, m.resolved_outcome_index, m.resolved_at, m.condition_id,
               m.market_type, g.game_key, g.sport, g.start_time, g.ended_at
        FROM tokens t JOIN markets m ON m.condition_id = t.condition_id
        JOIN games g ON g.game_key = m.game_key
        WHERE m.resolved_outcome_index IS NOT NULL
          AND m.market_type IN ({','.join('?' * len(WHOLE_GAME_TYPES))})
          {"AND g.start_time >= ?" if since is not None else ""}
          {"AND g.start_time < ?" if until is not None else ""}
          {lg_sql}
    """, list(WHOLE_GAME_TYPES) + [x for x in (since, until) if x is not None] + lg_params)
    cols = ["token_id", "game_key", "sport", "phase", "ts", "price", "won"]
    if tok.empty:
        return pd.DataFrame(columns=cols)
    prices = C.canonical_prices(conn, tok["token_id"].tolist())
    if prices.empty:
        return pd.DataFrame(columns=cols)
    df = prices.merge(tok, on="token_id")
    end = df["ended_at"].where(df["ended_at"].notna(), df["resolved_at"])
    df = df[end.isna() | (df["ts"] < end)]
    states = C.game_states(conn, df["game_key"].unique().tolist())
    df = C.attach_state(df, states)
    df["phase"] = C.phase_series(df["sport"], df["ts"], df["start_time"], df["game_minute"], df["ended_at"])
    df = df[df["phase"].notna()]
    df = df.sort_values(["token_id", "ts"])
    pre = df[df["phase"] == "pre"].drop_duplicates(["token_id"], keep="last")        # closing line
    live = df[df["phase"] != "pre"].drop_duplicates(["token_id", "phase"], keep="first")
    df = pd.concat([pre, live], ignore_index=True)
    df["won"] = (df["outcome_index"] == df["resolved_outcome_index"]).astype(int)
    return df[cols].reset_index(drop=True)


def run(conn: sqlite3.Connection, since: int | None = None, until: int | None = None,
        all_leagues: bool = False) -> dict:
    obs = load_observations(conn, since, until, all_leagues)
    calibration, brier = calibrate(obs)
    return {"calibration": calibration, "brier": brier, "observations": int(len(obs)),
            "games": int(obs["game_key"].nunique()) if not obs.empty else 0,
            "notes": [SAMPLING_NOTE, C.PHASE_NOTE] + ([] if all_leagues else [C.LEAGUE_NOTE])}


def top_gaps(calibration: list[dict], min_n: int = 30, limit: int = 5) -> list[dict]:
    """Largest |gap| buckets whose Wilson CI excludes the mean price (report highlights)."""
    rows = []
    for c in calibration:
        for b in c["buckets"]:
            if b["n"] < min_n:
                continue
            significant = b["ci_lo"] > b["mean_price"] or b["ci_hi"] < b["mean_price"]
            rows.append({"sport": c["sport"], "phase": c["phase"], **b, "significant": significant})
    rows.sort(key=lambda r: (not r["significant"], -abs(r["gap"])))
    return rows[:limit]


__all__ = ["wilson", "calibrate", "load_observations", "run", "top_gaps", "BUCKET_EDGES"]
