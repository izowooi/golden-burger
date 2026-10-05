"""`polylab analyze ou05` — O/U 0.5 market-life aggregates (docs/research/ou05-overround-study.md).

Inputs: data/ou05/registry.db + data/ou05/YYYY-MM.db shards (read-only); core.db game_states for goal markers.
Outputs under <research_dir>/ou05/ (storage layout = latest/ou05/ of the dashboard read model):
  summary.json         overround (sum_ask), sum_bid, Over spread by time-to-kickoff band x tier; sum_ask
                       distribution; league comparison; overround vs volume/liquidity; Over-price calibration
  markets_index.json   market browser list (recent/upcoming + top by volume)
  markets/<cid>.json   one market's whole-life series (downsampled)
  parquet/             raw minute series per shard + registry (`--export`, thesis use; never uploaded)

Statistics are TIME-WEIGHTED: a stored row stands for the time until the market's next stored row, capped at 10 min
(the poll heartbeat); if the next row is more than 13 min later (heartbeat + a few skipped runs) the row stands for
60 s only (a longer hole is missing data, not an unchanged quote).
Overround statistics use poll rows only (history has no bid/ask). Time to kickoff is recomputed from the
registry's latest game_start (postponements). In-play bands are WALL-CLOCK minutes since kickoff (half-time
inside ~45–60′). Tiers: league (MAJOR_SOCCER_LEAGUES vs other) and activity by the market's latest Gamma volume.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

from polylab import db
from polylab.analysis import _common as C
from polylab.collector.common import MAJOR_SOCCER_LEAGUES
from polylab.ou05 import store

BIN = 0.005
MAX_SPREAD_FOR_PRICE = 0.10
PLACEHOLDER_MID = 0.5        # history mid of an empty 0.01/0.99 book; O/U 0.5 never trades near 0.5, so not a price
HEARTBEAT_S = store.HEARTBEAT_S
MIN_MARKETS = 5
BROWSER_MAX = 120
MAX_POINTS = 400

# (key, label, kind, lo, hi): pre = minutes before kickoff in (lo, hi]; inplay = wall minutes since kickoff [lo, hi)
BANDS = (
    ("d30p", "30일+ 전", "pre", 43200, math.inf),
    ("d14", "14–30일 전", "pre", 20160, 43200),
    ("d7", "7–14일 전", "pre", 10080, 20160),
    ("d3", "3–7일 전", "pre", 4320, 10080),
    ("d1", "1–3일 전", "pre", 1440, 4320),
    ("h6", "6–24시간 전", "pre", 360, 1440),
    ("h1", "1–6시간 전", "pre", 60, 360),
    ("m15", "15–60분 전", "pre", 15, 60),
    ("m0", "0–15분 전", "pre", 0, 15),
    ("ip0", "킥오프 0–15′", "inplay", 0, 15),
    ("ip15", "15–30′", "inplay", 15, 30),
    ("ip30", "30–45′", "inplay", 30, 45),
    ("ip45", "45–60′ (HT 포함)", "inplay", 45, 60),
    ("ip60", "60–75′", "inplay", 60, 75),
    ("ip75", "75–90′", "inplay", 75, 90),
    ("ip90", "90–105′", "inplay", 90, 105),
    ("ip105", "105–120′", "inplay", 105, 120),
    ("ip120", "120–180′", "inplay", 120, 180),
    ("post", "킥오프 180′ 이후", "inplay", 180, math.inf),
)
BAND_KEYS = [b[0] for b in BANDS]
LAST24 = ("h6", "h1", "m15", "m0")
LAST1H = ("m15", "m0")
PRE_KEYS = tuple(b[0] for b in BANDS if b[2] == "pre")
INPLAY_KEYS = tuple(b[0] for b in BANDS if b[2] == "inplay" and b[0] != "post")
TIERS = (
    ("all", "전체"), ("major", "주요 리그"), ("other", "기타 리그"),
    ("vol_ge_10k", "거래량 ≥ 1만"), ("vol_1k_10k", "거래량 1천–1만"), ("vol_lt_1k", "거래량 < 1천"),
)
DIST_EDGES = [round(1.0 + i * 0.01, 2) for i in range(21)] + [1.3, 1.5, 2.0]   # plus <1.00 and >=2.00 buckets
VOLUME_EDGES = [0, 100, 1_000, 10_000, 100_000, math.inf]
LIQUIDITY_EDGES = [0, 1_000, 5_000, 20_000, 100_000, math.inf]


def _band_sql(m: str = "m") -> str:
    parts = []
    for key, _, kind, lo, hi in BANDS:
        if kind == "pre":
            cond = f"{m} > {lo}" + ("" if hi == math.inf else f" AND {m} <= {hi}")
        else:
            cond = f"{m} <= 0 AND -{m} >= {lo}" + ("" if hi == math.inf else f" AND -{m} < {hi}")
        parts.append(f"WHEN {cond} THEN '{key}'")
    return "CASE " + " ".join(parts) + " END"


def _weighted_sql(source: str) -> str:
    """Per-row weight: seconds until the market's next row of the same source, capped at the heartbeat; when the next
    row is later than heartbeat + 3 min (more than a skipped run or two) the row stands for 60 s only (missing data)."""
    return f"""
        WITH q AS (
            SELECT x.condition_id, x.ts, x.over_bid, x.over_ask, x.sum_ask, x.sum_bid, x.over_mid,
                   (r.game_start - x.ts) / 60.0 AS m,
                   LEAD(x.ts) OVER (PARTITION BY x.condition_id ORDER BY x.ts) - x.ts AS gap
            FROM ou05_quotes x JOIN reg.ou05_markets r ON r.condition_id = x.condition_id
            WHERE x.source = '{source}' AND r.game_start IS NOT NULL)
        SELECT *, CASE WHEN gap IS NULL OR gap > {HEARTBEAT_S + store.GAP_S} THEN 60
                       ELSE MIN(gap, {HEARTBEAT_S}) END AS w,
               {_band_sql('m')} AS band
        FROM q"""


def shard_aggregates(shard: Path, registry: Path) -> dict[str, pd.DataFrame]:
    """Per-shard partial aggregates (cacheable): overround histograms per (market, band) and Over-price sums."""
    conn = sqlite3.connect(f"file:{shard}?mode=ro", uri=True, timeout=60)
    conn.execute("ATTACH DATABASE ? AS reg", (f"file:{registry}?mode=ro",))
    try:
        hist = C.read_sql(conn, f"""
            SELECT condition_id, band,
                   CAST(ROUND(sum_ask / {BIN}) AS INTEGER) AS b_ask,
                   CAST(ROUND(sum_bid / {BIN}) AS INTEGER) AS b_bid,
                   CAST(ROUND((over_ask - over_bid) / {BIN}) AS INTEGER) AS b_spr,
                   SUM(w) AS w, COUNT(*) AS rows
            FROM ({_weighted_sql('poll')}) WHERE band IS NOT NULL
            GROUP BY condition_id, band, b_ask, b_bid, b_spr""")
        price_frames = []
        for source, px in (("poll", f"CASE WHEN over_bid IS NOT NULL AND over_ask IS NOT NULL AND "
                                    f"over_ask - over_bid <= {MAX_SPREAD_FOR_PRICE} + 1e-9 "
                                    f"THEN (over_bid + over_ask) / 2.0 END"),
                           ("history", f"CASE WHEN over_mid <> {PLACEHOLDER_MID} THEN over_mid END")):
            price_frames.append(C.read_sql(conn, f"""
                SELECT condition_id, band, '{source}' AS source, SUM(w * px) AS wpx, SUM(w) AS w, MAX(ts) AS last_ts
                FROM (SELECT *, {px} AS px FROM ({_weighted_sql(source)})) WHERE band IS NOT NULL AND px IS NOT NULL
                GROUP BY condition_id, band"""))
        counts = C.read_sql(conn, "SELECT source, COUNT(*) AS rows, MIN(ts) AS lo, MAX(ts) AS hi, "
                                  "COUNT(DISTINCT condition_id) AS markets FROM ou05_quotes GROUP BY source")
    finally:
        conn.close()
    return {"hist": hist, "price": pd.concat(price_frames, ignore_index=True), "counts": counts}


def _cache_key(shard: Path, registry: Path) -> str:
    st = shard.stat()
    return hashlib.sha1(f"{shard.name}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:16]


def load_aggregates(paths, now: int) -> dict[str, pd.DataFrame]:
    """Concatenate shard aggregates; closed months are cached by (size, mtime) under research_dir/ou05/cache."""
    reg = store.ou05_dir(paths) / "registry.db"
    cache = Path(paths.research_dir) / "ou05" / "cache"
    cur_month = db.year_month(now)
    parts: dict[str, list[pd.DataFrame]] = {"hist": [], "price": [], "counts": []}
    for sp in store.shard_paths(paths):
        key = _cache_key(sp, reg)
        cached = {k: cache / f"{sp.stem}.{key}.{k}.parquet" for k in parts}
        if sp.stem != cur_month and all(p.exists() for p in cached.values()):
            agg = {k: pd.read_parquet(p) for k, p in cached.items()}
        else:
            agg = shard_aggregates(sp, reg)
            if sp.stem != cur_month:
                cache.mkdir(parents=True, exist_ok=True)
                for old in cache.glob(f"{sp.stem}.*.parquet"):
                    old.unlink()
                for k, p in cached.items():
                    agg[k].to_parquet(p, index=False)
        for k in parts:
            parts[k].append(agg[k])
    return {k: (pd.concat(v, ignore_index=True) if v else pd.DataFrame()) for k, v in parts.items()}


# ------------------------------------------------------------------ statistics

def wquantiles(values: np.ndarray, weights: np.ndarray, qs=(0.1, 0.25, 0.5, 0.75, 0.9)) -> dict | None:
    ok = ~np.isnan(values) & (weights > 0)
    if not ok.any():
        return None
    v, w = values[ok], weights[ok]
    order = np.argsort(v, kind="stable")
    v, w = v[order], w[order]
    cw = np.cumsum(w) / w.sum()
    return {f"p{int(q * 100)}": round(float(v[min(np.searchsorted(cw, q), len(v) - 1)]), 4) for q in qs}


def _wilson(k: float, n: float, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(c - h, 4), round(c + h, 4)


def tier_masks(markets: pd.DataFrame) -> dict[str, pd.Series]:
    major = markets["league"].fillna("").str.lower().isin(MAJOR_SOCCER_LEAGUES)
    vol = markets["volume"].fillna(0)
    return {"all": pd.Series(True, index=markets.index), "major": major, "other": ~major,
            "vol_ge_10k": vol >= 10_000, "vol_1k_10k": (vol >= 1_000) & (vol < 10_000), "vol_lt_1k": vol < 1_000}


def _metric_stats(h: pd.DataFrame) -> dict:
    out = {}
    for col, name, off in (("b_ask", "sum_ask", 0.0), ("b_bid", "sum_bid", 0.0), ("b_spr", "spread", 0.0)):
        vals = h[col].to_numpy(dtype=float) * BIN + off
        out[name] = wquantiles(vals, h["w"].to_numpy(dtype=float))
    return out


def overround_table(hist: pd.DataFrame, markets: pd.DataFrame) -> list[dict]:
    rows = []
    if hist.empty:
        return rows
    masks = tier_masks(markets)
    for tier, _ in TIERS:
        cids = set(markets.loc[masks[tier], "condition_id"])
        sub = hist[hist["condition_id"].isin(cids)]
        for band in BAND_KEYS:
            h = sub[sub["band"] == band]
            if h.empty:
                continue
            total_w = float(h["w"].sum())
            two = h[h["b_ask"].notna()]
            rows.append({"band": band, "tier": tier, "markets": int(h["condition_id"].nunique()),
                         "minutes": round(total_w / 60), "rows": int(h["rows"].sum()),
                         "two_sided_share": round(float(two["w"].sum()) / total_w, 4) if total_w else None,
                         **_metric_stats(two)})
    return rows


def distribution(hist: pd.DataFrame, markets: pd.DataFrame) -> list[dict]:
    out = []
    if hist.empty:
        return out
    masks = tier_masks(markets)
    edges = np.array(DIST_EDGES)
    for tier, _ in TIERS:
        cids = set(markets.loc[masks[tier], "condition_id"])
        sub = hist[hist["condition_id"].isin(cids) & hist["b_ask"].notna()]
        for phase, keys in (("pre", PRE_KEYS), ("last24h", LAST24), ("inplay", INPLAY_KEYS)):
            h = sub[sub["band"].isin(keys)]
            if h.empty:
                continue
            v = h["b_ask"].to_numpy(dtype=float) * BIN
            w = h["w"].to_numpy(dtype=float)
            idx = np.searchsorted(edges, v + 1e-9, side="right")      # 0 = below 1.00, len(edges) = >= 2.00
            counts = np.bincount(idx, weights=w, minlength=len(edges) + 1)
            out.append({"tier": tier, "phase": phase, "markets": int(h["condition_id"].nunique()),
                        "minutes": round(float(w.sum()) / 60),
                        "shares": [round(float(c / w.sum()), 5) for c in counts]})
    return out


def per_market_median(hist: pd.DataFrame, bands: tuple[str, ...]) -> pd.Series:
    h = hist[hist["band"].isin(bands) & hist["b_ask"].notna()]
    out = {}
    for cid, g in h.groupby("condition_id"):
        q = wquantiles(g["b_ask"].to_numpy(dtype=float) * BIN, g["w"].to_numpy(dtype=float), qs=(0.5,))
        if q:
            out[cid] = q["p50"]
    return pd.Series(out, dtype=float)


def league_table(hist: pd.DataFrame, markets: pd.DataFrame) -> list[dict]:
    rows = []
    if hist.empty:
        return rows
    lg = markets.set_index("condition_id")["league"].fillna("?")
    h = hist.assign(league=hist["condition_id"].map(lg))
    vols = markets.groupby(markets["league"].fillna("?"))["volume"].median()
    for league, g in h.groupby("league"):
        n = g["condition_id"].nunique()
        if n < MIN_MARKETS:
            continue
        g2 = g[g["b_ask"].notna()]
        def med(keys):
            x = g2[g2["band"].isin(keys)]
            q = wquantiles(x["b_ask"].to_numpy(dtype=float) * BIN, x["w"].to_numpy(dtype=float), qs=(0.25, 0.5, 0.75))
            return q
        rows.append({"league": league, "tier": "major" if league in MAJOR_SOCCER_LEAGUES else "other",
                     "markets": int(n), "volume_median": _r(vols.get(league)),
                     "minutes": round(float(g["w"].sum()) / 60),
                     "pre": med(PRE_KEYS), "last1h": med(LAST1H), "inplay": med(INPLAY_KEYS)})
    rows.sort(key=lambda r: (r["tier"] != "major", -(r["volume_median"] or 0)))
    return rows


def volume_relation(hist: pd.DataFrame, markets: pd.DataFrame) -> dict:
    med = per_market_median(hist, LAST24)
    m = markets.set_index("condition_id")
    df = pd.DataFrame({"sum_ask": med}).join(m[["volume", "liquidity"]], how="left")
    out: dict = {"basis": "킥오프 전 24시간 시간가중 중앙값 sum_ask (시장별)", "markets": int(len(df)), "rows": []}
    for metric, edges in (("volume", VOLUME_EDGES), ("liquidity", LIQUIDITY_EDGES)):
        x = df.dropna(subset=[metric])
        for lo, hi in zip(edges, edges[1:]):
            g = x[(x[metric] >= lo) & (x[metric] < hi)]["sum_ask"]
            if g.empty:
                continue
            out["rows"].append({"metric": metric, "lo": lo, "hi": None if hi == math.inf else hi, "markets": int(len(g)),
                                "p25": _r(g.quantile(0.25)), "p50": _r(g.median()), "p75": _r(g.quantile(0.75))})
        out[f"spearman_{metric}"] = _r(x["sum_ask"].rank().corr(x[metric].rank())) if len(x) >= 10 else None
    return out


def calibration(price: pd.DataFrame, markets: pd.DataFrame) -> list[dict]:
    """Per resolved market and band: time-weighted Over price (poll mid with spread <= 0.10, else history mid)."""
    if price.empty:
        return []
    g = price.groupby(["condition_id", "band", "source"], as_index=False)[["wpx", "w"]].sum()
    g["px"] = g["wpx"] / g["w"]
    piv = g.pivot_table(index=["condition_id", "band"], columns="source", values="px")
    px = piv.get("poll", pd.Series(np.nan, index=piv.index))
    src = np.where(px.notna(), "poll", "history")
    if "history" in piv:
        px = px.fillna(piv["history"])
    obs = pd.DataFrame({"px": px, "src": src}).reset_index().dropna(subset=["px"])
    res = markets.dropna(subset=["resolved_over"]).set_index("condition_id")
    obs = obs[obs["condition_id"].isin(res.index)]
    obs["y"] = obs["condition_id"].map(res["resolved_over"]).astype(float)
    masks = tier_masks(markets)
    rows = []
    for tier in ("all", "major", "other"):
        cids = set(markets.loc[masks[tier], "condition_id"])
        sub = obs[obs["condition_id"].isin(cids)]
        for band in BAND_KEYS:
            b = sub[sub["band"] == band]
            n = len(b)
            if not n:
                continue
            k = float(b["y"].sum())
            lo, hi = _wilson(k, n)
            mean_px = float(b["px"].mean())
            rows.append({"band": band, "tier": tier, "n": n, "n_poll": int((b["src"] == "poll").sum()),
                         "mean_over": round(mean_px, 4), "over_rate": round(k / n, 4), "ci_lo": lo, "ci_hi": hi,
                         "gap": round(k / n - mean_px, 4), "nil_rate": round(1 - k / n, 4)})
    return rows


def _r(v, d: int = 4):
    return None if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else round(float(v), d)


# ------------------------------------------------------------------ market browser

def browser_pick(markets: pd.DataFrame, now: int) -> pd.DataFrame:
    """Open markets kicking off within -6 h..+7 d (live/upcoming, top 50 by volume) + markets that kicked off in the
    last 30 days (top by volume; resolved first)."""
    gs = markets["game_start"].fillna(0)
    upcoming = markets[(markets["closed"] == 0) & (gs >= now - 6 * 3600) & (gs <= now + 7 * 86400)]
    upcoming = upcoming.sort_values(["volume", "game_start"], ascending=[False, True]).head(50)
    past = markets[(gs >= now - 30 * 86400) & (gs < now)].assign(_res=markets["resolved_over"].notna())
    past = past.sort_values(["_res", "volume"], ascending=False).drop(columns="_res").head(BROWSER_MAX - len(upcoming))
    return pd.concat([upcoming, past]).drop_duplicates("condition_id").head(BROWSER_MAX)


def market_series(paths, cid: str) -> pd.DataFrame:
    frames = []
    for sp in store.shard_paths(paths):
        conn = db.connect(sp, readonly=True)
        try:
            frames.append(C.read_sql(conn, "SELECT ts, over_bid, over_ask, under_bid, under_ask, over_mid, sum_ask, "
                                           "source FROM ou05_quotes WHERE condition_id=? ORDER BY ts", (cid,)))
        finally:
            conn.close()
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True).sort_values("ts") if frames else pd.DataFrame()


def downsample(df: pd.DataFrame, max_points: int = MAX_POINTS) -> list[list]:
    """Last row per equal time bin (+ the bin's max sum_ask, so spikes survive)."""
    if df.empty:
        return []
    ts = df["ts"].to_numpy()
    lo, hi = int(ts.min()), int(ts.max())
    nb = min(max_points, len(df))
    bins = np.minimum(((ts - lo) / max(hi - lo, 1) * nb).astype(int), nb - 1)
    d = df.assign(_b=bins)
    last = d.groupby("_b").tail(1).set_index("_b")
    mx = d.groupby("_b")["sum_ask"].max()
    out = []
    for b, r in last.iterrows():
        out.append([C.iso(r["ts"]), _r(r["over_bid"], 3), _r(r["over_ask"], 3), _r(r["under_bid"], 3),
                    _r(r["under_ask"], 3), _r(mx.get(b), 3), _r(r["over_mid"], 4), "h" if r["source"] == "history" else "p"])
    return out


def _goals(paths, game_keys: list[str]) -> dict[str, list[dict]]:
    conn = C.open_ro(paths.core_db)
    if conn is None or not game_keys:
        return {}
    try:
        from polylab.analysis import events as ev
        states = C.game_states(conn, game_keys)
        if states.empty:
            return {}
        sc = ev.detect_score_changes(states)
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    out: dict[str, list[dict]] = {}
    for r in sc.itertuples(index=False):
        out.setdefault(str(r.game_key), []).append({"at": C.iso(r.ts), "scorer": r.scorer, "home_score": int(r.home_score),
                                                    "away_score": int(r.away_score)})
    return out


def market_objects(paths, markets: pd.DataFrame, now: int) -> tuple[dict, dict[str, dict]]:
    pick = browser_pick(markets, now)
    goals = _goals(paths, [str(k) for k in pick["game_key"].dropna().unique()])
    index, objs = [], {}
    for r in pick.itertuples(index=False):
        series = market_series(paths, r.condition_id)
        if series.empty:
            continue
        title = f"{r.home} vs {r.away}" if r.home and r.away else (r.question or r.condition_id)
        meta = {"condition_id": r.condition_id, "league": r.league, "title": title,
                "major": (r.league or "") in MAJOR_SOCCER_LEAGUES, "created_at": C.iso(r.created_at),
                "game_start": C.iso(r.game_start), "closed_at": C.iso(r.closed_at), "closed": bool(r.closed),
                "resolved_over": None if pd.isna(r.resolved_over) else bool(r.resolved_over),
                "final_score": r.final_score, "volume": _r(r.volume, 2), "liquidity": _r(r.liquidity, 2)}
        index.append({**meta, "rows": int(len(series)), "poll_rows": int((series["source"] == "poll").sum()),
                      "from": C.iso(series["ts"].min()), "to": C.iso(series["ts"].max())})
        objs[r.condition_id] = {**meta, "goals": goals.get(str(r.game_key), []),
                                "columns": ["at", "over_bid", "over_ask", "under_bid", "under_ask", "sum_ask_max",
                                            "over_mid", "src"],
                                "points": downsample(series)}
    index.sort(key=lambda x: x["game_start"] or "", reverse=True)
    return {"generated_at": C.iso(now), "max_points": MAX_POINTS, "markets": index}, objs


# ------------------------------------------------------------------ run

def load_markets(paths) -> pd.DataFrame:
    conn = C.open_ro(store.ou05_dir(paths) / "registry.db")
    if conn is None:
        return pd.DataFrame(columns=["condition_id", "league", "volume", "liquidity", "resolved_over", "game_start"])
    try:
        return C.read_sql(conn, "SELECT condition_id, game_key, league, home, away, question, created_at, game_start, "
                                "closed, closed_at, resolved_over, final_score, volume, liquidity FROM ou05_markets")
    finally:
        conn.close()


def quality_summary(paths, now: int) -> dict:
    conn = C.open_ro(store.ou05_dir(paths) / "registry.db")
    if conn is None:
        return {}
    try:
        rows = conn.execute("SELECT kind, COUNT(*), MAX(ts) FROM quality_events WHERE ts >= ? GROUP BY kind",
                            (now - 7 * 86400,)).fetchall()
        return {k: {"events_7d": n, "last_at": C.iso(t)} for k, n, t in rows}
    finally:
        conn.close()


def summary(paths, now: int) -> tuple[dict, pd.DataFrame]:
    markets = load_markets(paths)
    agg = load_aggregates(paths, now)
    hist, price, counts = agg["hist"], agg["price"], agg["counts"]
    scope = {"markets": int(len(markets)), "open": int((markets.get("closed", pd.Series(dtype=int)) == 0).sum()),
             "resolved": int(markets["resolved_over"].notna().sum()), "poll_rows": 0, "history_rows": 0,
             "from": None, "to": None}
    if not counts.empty:
        by = counts.groupby("source").agg(rows=("rows", "sum"), lo=("lo", "min"), hi=("hi", "max"))
        scope["poll_rows"] = int(by["rows"].get("poll", 0))
        scope["history_rows"] = int(by["rows"].get("history", 0))
        scope["from"], scope["to"] = C.iso(by["lo"].min()), C.iso(by["hi"].max())
    obj = {
        "generated_at": C.iso(now), "scope": scope, "bin": BIN, "heartbeat_s": HEARTBEAT_S,
        "bands": [{"key": k, "label": lbl, "kind": kind} for k, lbl, kind, _, _ in BANDS],
        "tiers": [{"key": k, "label": lbl} for k, lbl in TIERS],
        "overround": overround_table(hist, markets),
        "distribution": {"edges": DIST_EDGES, "series": distribution(hist, markets)},
        "leagues": league_table(hist, markets),
        "volume_relation": volume_relation(hist, markets) if not hist.empty else {"rows": [], "markets": 0},
        "calibration": calibration(price, markets),
        "quality": quality_summary(paths, now),
        "notes": NOTES,
    }
    return obj, markets


NOTES = [
    "overround = over_ask + under_ask − 1. Polymarket 은 Over·Under 호가창을 거울로 묶는다(under_ask = 1 − over_bid). "
    "따라서 sum_ask − 1 은 Over 토큰의 bid-ask 스프레드와 같고 sum_bid = 1 − 스프레드다. 한쪽만 비싼 것이 아니라 스프레드다.",
    "통계는 시간가중: 행은 다음 행까지 유효(최대 10분), 다음 행이 13분 넘게 없으면 60초만 인정(결측).",
    "sum_ask/sum_bid/스프레드는 1분 poll 행만 사용. 과거(prices-history) 행은 중간가뿐이며 Over+Under 합이 항상 1.000 이라 overround 를 복원할 수 없다.",
    "경기 중 구간은 킥오프 이후 벽시계 분(하프타임 약 15분 포함). 킥오프 시각은 레지스트리의 최신 gameStartTime(연기 반영).",
    "거래량 등급은 시장의 최신 Gamma volume(종료 시장은 최종값). 거래량 < 1천 시장 다수는 0.01/0.99 자리표시 호가라 sum_ask 가 1.5 이상으로 크다.",
    "보정(calibration): 정산된 시장만, 구간별 시간가중 Over 가격(스프레드 ≤ 0.10 인 poll 중간가, 없으면 과거 중간가) vs 실제 Over 비율. nil_rate = 0:0 비율.",
    "과거 중간가가 정확히 0.500 인 분은 빈 호가창(0.01/0.99)의 자리표시로 보고 가격에서 뺀다. 그래도 킥오프 수일 전 구간은 호가가 얇아 과거 중간가가 실제 확률과 거리가 멀 수 있다(스프레드를 알 수 없음).",
]


def cache_dir(paths) -> Path:
    return Path(paths.research_dir) / "ou05" / "latest"


def dump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":")).encode()


def export_parquet(paths) -> list[str]:
    """Raw minute series per shard (rewritten when the shard changed) + registry, under research_dir/ou05/parquet."""
    out_dir = Path(paths.research_dir) / "ou05" / "parquet"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for sp in store.shard_paths(paths):
        dest = out_dir / f"ou05_quotes_{sp.stem}.parquet"
        if dest.exists() and dest.stat().st_mtime >= sp.stat().st_mtime:
            continue
        conn = db.connect(sp, readonly=True)
        try:
            df = C.read_sql(conn, "SELECT * FROM ou05_quotes ORDER BY condition_id, ts")
        finally:
            conn.close()
        tmp = dest.with_suffix(".tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(dest)
        written.append(str(dest))
    load_markets(paths).to_parquet(out_dir / "ou05_markets.parquet", index=False)
    written.append(str(out_dir / "ou05_markets.parquet"))
    return written


def run(paths, now: int | None = None, export: bool = False) -> dict:
    now = now or int(time.time())
    t0 = time.time()
    obj, markets = summary(paths, now)
    index, objs = market_objects(paths, markets, now) if not markets.empty else ({"generated_at": C.iso(now),
                                                                                  "max_points": MAX_POINTS,
                                                                                  "markets": []}, {})
    root = cache_dir(paths)
    tmp = root.with_name("latest.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "markets").mkdir(parents=True)
    (tmp / "summary.json").write_bytes(dump(obj))
    (tmp / "markets_index.json").write_bytes(dump(index))
    for cid, o in objs.items():
        (tmp / "markets" / f"{cid}.json").write_bytes(dump(o))
    shutil.rmtree(root, ignore_errors=True)
    tmp.replace(root)
    res = {"generated_at": C.iso(now), "seconds": round(time.time() - t0, 1), "dir": str(root),
           "markets": obj["scope"]["markets"], "poll_rows": obj["scope"]["poll_rows"],
           "history_rows": obj["scope"]["history_rows"], "browser_markets": len(objs)}
    if export:
        res["parquet"] = export_parquet(paths)
    return res


def cached_files(paths) -> dict[str, Path]:
    """{storage path: local file} of the current ou05 cache (latest/ou05/...)."""
    root = cache_dir(paths)
    if not root.exists():
        return {}
    return {f"latest/ou05/{p.relative_to(root).as_posix()}": p for p in sorted(root.rglob("*.json"))}


__all__ = ["run", "cached_files", "summary", "BANDS", "TIERS", "history_weights"]


def history_weights(ts: np.ndarray) -> np.ndarray:
    """Python mirror of the SQL weight rule (tests)."""
    gap = np.diff(ts, append=np.nan)
    ok = ~np.isnan(gap) & (gap <= HEARTBEAT_S + store.GAP_S)
    return np.where(ok, np.minimum(np.nan_to_num(gap), HEARTBEAT_S), 60.0)
