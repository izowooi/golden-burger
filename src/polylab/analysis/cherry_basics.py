"""Cherry back-to-basics grid backtest over data/general history (docs/research/backtests/2026-10-05-cherry-basics.md).

`uv run python -m polylab.analysis.cherry_basics [--out research/cherry_basics.json] [--split 2026-06-15]`

Episode model (one entry per market, stake 1 USDC per trade in the grid; caps only in the portfolio replay):
- Universe: closed markets with an exact resolution, end_ref − listed >= MIN_LISTED_H (cherry `min_listed_hours`),
  final volume >= the floor (look-ahead: only the final Gamma volume is known historically), category from the
  stored tags (sports split into core = 5-sport game markets core.db tracks, other = props/tennis/esports-like/minor).
- Prices: YES prices-history `p` (5-minute, change-only rows, forward-filled). Leading side = YES if p >= 0.5 else
  NO at 1 − p (binary mirror). No bid/ask history: half-spread `hs` by final-volume tier, measured from a live
  POST /books snapshot of 0.85–0.97 leading outcomes (SPREAD_BASE); stress = 2×.
- Entry: first moment inside [end_ref − h_max, end_ref − h_min] (state at the window start included) where the
  lead mid q >= lo and the ask q + hs <= hi; fill at q + hs, taker fee rate·(1−f) per USDC (current schedule of the
  market's category, CATEGORY_FEE; unknown → the highest rate).
- Exits in time order: take-profit at the first row where bid = q − hs >= T (sold at T; with hold 0.99 only while
  bid < 0.99), else time exit at end_ref − X at the forward-filled bid (skipped while bid >= hold), else the exact
  resolution (1 or 0, no fee) at closedTime.
- Split: H1 = entries before --split, H2 = after. Selection on H1 only (score = mean − SE, event-clustered), H2 is
  the holdout; the share of cells positive in both halves is reported as the multiple-testing baseline.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from polylab.general import store

MIN_LISTED_H = 72.0
DAY = 86400
# full spread (ask − bid) of leading outcomes priced 0.85–0.97, mean of a POST /books snapshot (2026-10-05, 745
# markets with volume >= 10k, docs: backtest §data); by final-volume tier
SPREAD_BASE = ((25_000.0, 0.0225), (100_000.0, 0.0165), (float("inf"), 0.013))
CATEGORY_FEE = {"sports_core": 0.05, "sports_other": 0.05, "esports": 0.05, "crypto": 0.07, "weather": 0.05,
                "economy": 0.05, "politics": 0.04, "world": 0.04, "culture": 0.05, "tech": 0.04, "other": 0.07}
MAX_FEE = max(CATEGORY_FEE.values())

WINDOWS = ((0, 24), (12, 36), (24, 48), (36, 60), (48, 72), (60, 84), (72, 96), (0, 48), (24, 72), (48, 96), (0, 72))
LOS = (0.85, 0.87, 0.88, 0.90, 0.92, 0.94)
HIS = (0.90, 0.92, 0.94, 0.95, 0.96, 0.97)
TARGETS = (("abs", 0.95), ("abs", 0.96), ("abs", 0.97), ("abs", 0.98), ("abs", 0.99),
           ("delta", 0.02), ("delta", 0.03), ("delta", 0.04), ("delta", 0.05), ("none", None))
HOLDS = (None, 0.99)
TIME_EXITS = (None, 0.0, 12.0, 24.0)
VOLUME_FLOORS = (10_000.0, 25_000.0, 50_000.0, 100_000.0)
THRESH_STEP = 0.005


def bands() -> list[tuple[float, float]]:
    return [(lo, hi) for lo in LOS for hi in HIS if hi - lo >= 0.02 - 1e-9]


# ------------------------------------------------------------------ data

class Data:
    """Markets (per-market arrays) and YES price rows sorted by (market, ts)."""

    def __init__(self, mk: dict[str, np.ndarray], row_m: np.ndarray, row_ts: np.ndarray, row_p: np.ndarray):
        self.mk = mk
        self.row_m, self.row_ts, self.row_p = row_m, row_ts, row_p
        n = len(mk["gid"])
        self.start = np.searchsorted(row_m, np.arange(n), "left")
        self.stop = np.searchsorted(row_m, np.arange(n), "right")

    @property
    def n_markets(self) -> int:
        return len(self.mk["gid"])


def load(paths, min_volume: float = VOLUME_FLOORS[0]) -> Data:
    reg = sqlite3.connect(f"file:{store.registry_path(paths)}?mode=ro", uri=True)
    reg.row_factory = sqlite3.Row
    rows = reg.execute(
        "SELECT id, category, in_core, event_id, condition_id, neg_risk, end_ref, COALESCE(start_date, created_at) AS "
        "listed, volume, resolved_index, closed_at FROM gen_markets WHERE closed=1 AND resolved_index IN (0,1) "
        "AND end_ref IS NOT NULL AND closed_at IS NOT NULL AND volume >= ? "
        "AND hist_status='done' AND end_ref - COALESCE(start_date, created_at, 0) >= ? ORDER BY id",
        (min_volume, MIN_LISTED_H * 3600)).fetchall()
    reg.close()
    cats = [("sports_core" if r["in_core"] else "sports_other") if r["category"] == "sports" else r["category"]
            for r in rows]
    events = {}
    mk = {
        "gid": np.array([r["id"] for r in rows], dtype=np.int64),
        "cat": np.array(cats, dtype=object),
        "event": np.array([events.setdefault(r["event_id"] or r["condition_id"], len(events)) for r in rows],
                          dtype=np.int64),
        "end_ref": np.array([r["end_ref"] for r in rows], dtype=np.int64),
        "volume": np.array([r["volume"] or 0 for r in rows], dtype=np.float64),
        "winner": np.array([r["resolved_index"] for r in rows], dtype=np.int8),
        "closed_at": np.array([r["closed_at"] for r in rows], dtype=np.int64),
        "neg_risk": np.array([r["neg_risk"] or 0 for r in rows], dtype=np.int8),
    }
    mk["fee"] = np.array([CATEGORY_FEE.get(c, MAX_FEE) for c in cats])
    gids = mk["gid"]
    ms, ts, ps = [], [], []
    for sp in store.shard_paths(paths):
        c = sqlite3.connect(f"file:{sp}?mode=ro", uri=True)
        try:
            cur = c.execute("SELECT id, ts, p FROM gen_quotes WHERE p IS NOT NULL")
            while True:
                chunk = cur.fetchmany(1_000_000)
                if not chunk:
                    break
                arr = np.array(chunk, dtype=np.float64)
                g = arr[:, 0].astype(np.int64)
                idx = np.searchsorted(gids, g)
                idx = np.minimum(idx, len(gids) - 1)
                keep = (len(gids) > 0) & (gids[idx] == g)
                ms.append(idx[keep].astype(np.int32))
                ts.append(arr[keep, 1].astype(np.int64))
                ps.append(arr[keep, 2])
        finally:
            c.close()
    m = np.concatenate(ms) if ms else np.zeros(0, np.int32)
    t = np.concatenate(ts) if ts else np.zeros(0, np.int64)
    p = np.concatenate(ps) if ps else np.zeros(0)
    keep = t <= mk["closed_at"][m]                       # nothing after the market closed
    m, t, p = m[keep], t[keep], p[keep]
    order = np.lexsort((t, m))
    return Data(mk, m[order], t[order], p[order])


def half_spread(volume: np.ndarray, mult: float = 1.0) -> np.ndarray:
    out = np.empty(len(volume))
    lo = -np.inf
    for hi, spread in SPREAD_BASE:
        sel = (volume >= lo) & (volume < hi)
        out[sel] = spread / 2 * mult
        lo = hi
    return out


# ------------------------------------------------------------------ vectorised helpers

def state_at(d: Data, t: np.ndarray) -> np.ndarray:
    """Row index of the last row of each market at or before t[market] (-1 when none)."""
    key_rows = d.row_m.astype(np.float64) * 1e10 + d.row_ts
    key = np.arange(d.n_markets, dtype=np.float64) * 1e10 + t
    idx = np.searchsorted(key_rows, key, "right") - 1
    ok = (idx >= 0) & (idx >= d.start) & (idx < d.stop)
    return np.where(ok, idx, -1)


def first_true(d: Data, mask: np.ndarray) -> np.ndarray:
    """First row index per market where mask holds (-1 when never)."""
    out = np.full(d.n_markets, -1, dtype=np.int64)
    idx = np.flatnonzero(mask)
    if len(idx):
        mm, first = np.unique(d.row_m[idx], return_index=True)
        out[mm] = idx[first]
    return out


def next_true(d: Data, cond: np.ndarray) -> np.ndarray:
    """For every row: the first row index >= it in the SAME market where cond holds, else -1."""
    n = len(cond)
    big = np.iinfo(np.int64).max
    cand = np.where(cond, np.arange(n, dtype=np.int64), big)
    nxt = np.minimum.accumulate(cand[::-1])[::-1]
    ok = nxt != big
    same = np.zeros(n, dtype=bool)
    same[ok] = d.row_m[nxt[ok]] == d.row_m[ok]
    return np.where(same, nxt, -1)


# ------------------------------------------------------------------ episodes

def window_state(d: Data, window: tuple[float, float]):
    """(rows strictly inside the entry window and before close, state row at the window start, window start)."""
    h_min, h_max = window
    w_start = d.mk["end_ref"] - int(h_max * 3600)
    w_end = d.mk["end_ref"] - int(h_min * 3600)
    inside = ((d.row_ts > w_start[d.row_m]) & (d.row_ts <= w_end[d.row_m])
              & (d.row_ts < d.mk["closed_at"][d.row_m]))
    s0 = state_at(d, w_start)
    s0 = np.where(w_start < d.mk["closed_at"], s0, -1)
    return inside, s0, w_start


def entries(d: Data, q: np.ndarray, hs_row: np.ndarray, hs_mk: np.ndarray, wstate, lo: float, hi: float):
    """Per market: (entry ts, side 0=YES/1=NO, fill, entry row) or ts=-1 when no entry."""
    inside, s0, w_start = wstate
    in_band = (q >= lo - 1e-9) & (q + hs_row <= hi + 1e-9)
    first = first_true(d, inside & in_band)
    start_ok = s0 >= 0
    start_ok[start_ok] = in_band[s0[start_ok]]
    row = np.where(start_ok, s0, first)
    ts = np.where(start_ok, w_start, np.where(first >= 0, d.row_ts[np.maximum(first, 0)], -1))
    ok = row >= 0
    p = np.where(ok, d.row_p[np.maximum(row, 0)], np.nan)
    side = np.where(p >= 0.5, 0, 1).astype(np.int8)
    fill = np.where(side == 0, p, 1 - p) + hs_mk
    ts = np.where(ok, ts, -1)
    return ts, side, fill, row


def net_positive_bid(fill: np.ndarray, fee: np.ndarray) -> np.ndarray:
    """Lowest bid at which selling the whole holding beats its cost after both fees (live TP gate):
    b·(1 − r(1 − b)) = f·(1 + r(1 − f))  ->  r b² + (1 − r) b − c = 0."""
    c = fill * (1 + fee * (1 - fill))
    r = np.maximum(fee, 1e-12)
    return np.where(fee > 0, (-(1 - r) + np.sqrt((1 - r) ** 2 + 4 * r * c)) / (2 * r), c)


def simulate(d: Data, mult: float = 1.0, volume_floors=VOLUME_FLOORS, windows=WINDOWS, band_list=None,
             targets=TARGETS, holds=HOLDS, time_exits=TIME_EXITS, fee_mult: float = 1.0):
    """Yield one cell per grid combination: dict of params + per-entry arrays (`_idx` market indices, `_pnl`,
    `_cost`, `_ts`, `_exit_ts`, `_reason`) for the entries passing that cell's volume floor."""
    band_list = band_list or bands()
    hs_mk = half_spread(d.mk["volume"], mult)
    hs_row = hs_mk[d.row_m]
    big = np.iinfo(np.int64).max
    tx_state = {X: state_at(d, d.mk["end_ref"] - int(X * 3600)) for X in time_exits if X is not None}
    q = np.maximum(d.row_p, 1 - d.row_p)
    for w in windows:
        wstate = window_state(d, w)
        for lo, hi in band_list:
            ts_all, side_all, fill_all, row_all = entries(d, q, hs_row, hs_mk, wstate, lo, hi)
            E = np.flatnonzero(ts_all >= 0)
            if not len(E):
                continue
            ts, side, fill, row = ts_all[E], side_all[E], fill_all[E], row_all[E]
            fee, hs = d.mk["fee"][E] * fee_mult, hs_mk[E]
            b_min = net_positive_bid(fill, fee) + 1e-6
            closed = d.mk["closed_at"][E]
            shares = 1.0 / fill
            cost = 1.0 + fee * (1 - fill)
            won = (d.mk["winner"][E] == side)
            # rows after each entry (segments)
            first = row + 1
            lens = np.maximum(d.stop[E] - first, 0)
            seg = np.repeat(np.arange(len(E)), lens)
            offs = np.repeat(first - np.concatenate([[0], np.cumsum(lens)[:-1]]), lens)
            ridx = np.arange(lens.sum()) + offs
            p_r = d.row_p[ridx]
            bid_r = np.where(side[seg] == 0, p_r, 1 - p_r) - hs[seg]
            ts_r = d.row_ts[ridx]
            vol = d.mk["volume"][E]
            for kind, val in targets:
                if kind == "abs":
                    T = np.maximum(float(val), b_min)        # TP only when net-positive (live gate)
                elif kind == "delta":
                    T = np.maximum(fill + float(val), b_min)
                else:
                    T = None
                for hold in holds:
                    if kind == "none" and hold is not None:
                        continue
                    tp_ts = np.full(len(E), big)
                    if T is not None:
                        cond = bid_r >= T[seg] - 1e-7
                        if hold is not None:
                            cond &= bid_r < hold - 1e-7
                        hit = np.flatnonzero(cond)
                        if len(hit):
                            sg, fi = np.unique(seg[hit], return_index=True)
                            tp_ts[sg] = ts_r[hit[fi]]
                        if hold is not None:
                            tp_ts[T >= hold - 1e-9] = big
                    for X in time_exits:
                        if X is not None and X > w[0]:
                            continue                     # the time exit would fall inside the entry window
                        tx_ts = np.full(len(E), big)
                        tx_bid = np.zeros(len(E))
                        if X is not None:
                            st = tx_state[X][E]
                            t_x = d.mk["end_ref"][E] - int(X * 3600)
                            p_x = np.where(st >= 0, d.row_p[np.maximum(st, 0)], np.nan)
                            tx_bid = np.where(side == 0, p_x, 1 - p_x) - hs
                            valid = (st >= 0) & (t_x < closed) & (t_x > ts)
                            if hold is not None:
                                valid &= tx_bid < hold
                            tx_ts = np.where(valid, t_x, big)
                        use_tp = (tp_ts != big) & (tp_ts <= tx_ts) & (tp_ts <= closed)
                        use_tx = ~use_tp & (tx_ts < closed)
                        proceeds = shares * won
                        exit_ts = closed.copy()
                        reason = np.full(len(E), 2, dtype=np.int8)
                        if T is not None:
                            proceeds = np.where(use_tp, shares * T * (1 - fee * (1 - T)), proceeds)
                            exit_ts = np.where(use_tp, tp_ts, exit_ts)
                            reason = np.where(use_tp, 0, reason)
                        if X is not None:
                            proceeds = np.where(use_tx, shares * tx_bid * (1 - fee * (1 - tx_bid)), proceeds)
                            exit_ts = np.where(use_tx, tx_ts, exit_ts)
                            reason = np.where(use_tx, 1, reason)
                        pnl = proceeds - cost
                        for floor in volume_floors:
                            sel = vol >= floor
                            yield {"window": list(w), "lo": lo, "hi": hi, "target": [kind, val], "hold": hold,
                                   "time_exit_h": X, "min_volume": floor, "spread_mult": mult, "fee_mult": fee_mult,
                                   "_idx": E[sel], "_pnl": pnl[sel], "_cost": cost[sel], "_ts": ts[sel],
                                   "_exit_ts": exit_ts[sel], "_reason": reason[sel]}


def cell_key(c: dict) -> tuple:
    return (tuple(c["window"]), c["lo"], c["hi"], tuple(c["target"]), c["hold"], c["time_exit_h"], c["min_volume"])


def one_cell(d: Data, key: tuple, mult: float, fee_mult: float = 1.0) -> dict:
    w, lo, hi, target, hold, X, floor = key
    return next(simulate(d, mult, volume_floors=(floor,), windows=(w,), band_list=[(lo, hi)],
                         targets=(target,), holds=(hold,), time_exits=(X,), fee_mult=fee_mult))


def summarize(d: Data, cell: dict, split_ts: int, categories: bool = False) -> dict:
    pnl, cost, ts, idx = cell["_pnl"], cell["_cost"], cell["_ts"], cell["_idx"]
    sel = np.ones(len(idx), dtype=bool) if "_mask" not in cell else cell["_mask"]
    out = {k: v for k, v in cell.items() if not k.startswith("_")}
    cat_e = d.mk["cat"][idx]
    ev_e = d.mk["event"][idx]

    def agg(mask) -> dict:
        n = int(mask.sum())
        if n == 0:
            return {"n": 0, "events": 0, "roi": None, "pnl": 0.0, "win": None, "se": None}
        r = pnl[mask] / cost[mask]
        ev = ev_e[mask]
        # event-clustered SE of the mean ROI
        uniq, inv = np.unique(ev, return_inverse=True)
        sums = np.bincount(inv, weights=r)
        cnt = np.bincount(inv)
        k = len(uniq)
        mean = r.mean()
        se = float(np.sqrt(np.sum((sums - cnt * mean) ** 2)) / n) if k > 1 else None
        return {"n": n, "events": int(k), "roi": round(float(mean), 5), "pnl": round(float(pnl[mask].sum()), 4),
                "win": round(float((pnl[mask] > 0).mean()), 4), "se": None if se is None else round(se, 5)}

    out["all"] = agg(sel)
    out["h1"] = agg(sel & (ts < split_ts))
    out["h2"] = agg(sel & (ts >= split_ts))
    reason = cell["_reason"]
    out["exits"] = {name: int((sel & (reason == i)).sum()) for i, name in enumerate(("tp", "time", "resolution"))}
    hold_h = (cell["_exit_ts"] - ts) / 3600.0
    out["hold_h_median"] = round(float(np.median(hold_h[sel])), 1) if sel.any() else None
    if categories:
        out["by_category"] = {}
        for c in sorted(set(cat_e[sel].tolist())):
            m = sel & (cat_e == c)
            out["by_category"][c] = {"all": agg(m), "h1": agg(m & (ts < split_ts)), "h2": agg(m & (ts >= split_ts))}
    return out


def bootstrap_ci(d: Data, cell: dict, n_boot: int = 1000, seed: int = 7) -> list[float] | None:
    sel = cell.get("_mask", np.ones(len(cell["_idx"]), dtype=bool))
    if sel.sum() < 2:
        return None
    r = cell["_pnl"][sel] / cell["_cost"][sel]
    ev = d.mk["event"][cell["_idx"][sel]]
    uniq, inv = np.unique(ev, return_inverse=True)
    sums, cnt = np.bincount(inv, weights=r), np.bincount(inv)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
    means = sums[pick].sum(1) / cnt[pick].sum(1)
    return [round(float(np.quantile(means, 0.025)), 5), round(float(np.quantile(means, 0.975)), 5)]


def portfolio(d: Data, cell: dict, stake: float = 5.0, max_positions: int = 10, max_open: float = 50.0) -> dict:
    """Chronological replay with the live caps (10 positions, 50 USDC open, one new entry per minute)."""
    sel = np.flatnonzero(cell.get("_mask", np.ones(len(cell["_idx"]), dtype=bool)))
    order = sel[np.argsort(cell["_ts"][sel], kind="stable")]
    open_until: list[int] = []
    last_minute = None
    taken = []
    for i in order:
        t = int(cell["_ts"][i])
        open_until = [x for x in open_until if x > t]
        if len(open_until) >= max_positions or (len(open_until) + 1) * stake > max_open + 1e-9:
            continue
        if last_minute == t // 60:
            continue
        last_minute = t // 60
        open_until.append(int(cell["_exit_ts"][i]))
        taken.append(i)
    if not taken:
        return {"n": 0}
    idx = np.array(taken)
    pnl = cell["_pnl"][idx] * stake
    cost = cell["_cost"][idx] * stake
    ex = cell["_exit_ts"][idx]
    cum = np.cumsum(pnl[np.argsort(ex)])
    mdd = float(np.max(np.maximum.accumulate(np.concatenate([[0], cum])) - np.concatenate([[0], cum])))
    hold = (ex - cell["_ts"][idx]) / 3600.0
    return {"n": len(idx), "pnl_usdc": round(float(pnl.sum()), 2), "roi": round(float(pnl.sum() / cost.sum()), 5),
            "max_dd_usdc": round(mdd, 2), "hold_h_median": round(float(np.median(hold)), 1),
            "hold_h_p90": round(float(np.quantile(hold, 0.9)), 1),
            "per_day": round(len(idx) / max(1.0, (cell["_ts"][idx].max() - cell["_ts"][idx].min()) / DAY), 2)}


def plateau(summ: list[dict], i: int) -> float | None:
    """Median H1 ROI of the cell's band neighbours (lo/hi one step away, same window/exits/floor)."""
    c = summ[i]
    li, hi_ = LOS.index(c["lo"]), HIS.index(c["hi"])
    vals = []
    for s in summ:
        if (s["window"], s["target"], s["hold"], s["time_exit_h"], s["min_volume"]) != (
                c["window"], c["target"], c["hold"], c["time_exit_h"], c["min_volume"]):
            continue
        if abs(LOS.index(s["lo"]) - li) <= 1 and abs(HIS.index(s["hi"]) - hi_) <= 1 and s["h1"]["roi"] is not None:
            vals.append(s["h1"]["roi"])
    return round(float(np.median(vals)), 5) if vals else None


def score(s: dict) -> float:
    h1 = s["h1"]
    if not h1["n"] or h1["roi"] is None:
        return -9.0
    return h1["roi"] - (h1["se"] or 0.05)


SCENARIOS = {"base": (1.0, 1.0), "stress_2x_spread": (2.0, 1.0), "mid_no_spread": (0.0, 1.0),
             "maker_mid_no_fee": (0.0, 0.0)}


def run(paths, split_ts: int, *, scenarios=tuple(SCENARIOS), log=print) -> dict:
    t0 = time.time()
    d = load(paths)
    log(f"loaded {d.n_markets} markets, {len(d.row_p)} rows in {time.time() - t0:.0f}s")
    result: dict = {"generated_at": int(time.time()), "split_ts": split_ts, "markets": d.n_markets,
                    "rows": int(len(d.row_p)), "spread_base": SPREAD_BASE, "category_fee": CATEGORY_FEE,
                    "by_scenario": {}}
    cats = {c: int((d.mk["cat"] == c).sum()) for c in sorted(set(d.mk["cat"].tolist()))}
    result["universe_by_category"] = cats
    for name in scenarios:
        mult, fee_mult = SCENARIOS[name]
        summ = []
        for c in simulate(d, mult, fee_mult=fee_mult):
            summ.append(summarize(d, c, split_ts))
        log(f"{name}: {len(summ)} cells in {time.time() - t0:.0f}s")
        eligible = [i for i, s in enumerate(summ) if s["h1"]["events"] >= 30 and s["all"]["events"] >= 60]
        both = [i for i in eligible if (summ[i]["h1"]["roi"] or -1) >= 0 and (summ[i]["h2"]["roi"] or -1) >= 0]
        ranked = sorted(eligible, key=lambda i: score(summ[i]), reverse=True)

        def detail(i):
            cell = one_cell(d, cell_key(summ[i]), mult, fee_mult)
            s = summarize(d, cell, split_ts, categories=True)
            s["ci95"] = bootstrap_ci(d, cell)
            s["portfolio"] = portfolio(d, cell)
            s["plateau_h1"] = plateau(summ, i)
            return s
        top = [detail(i) for i in ranked[:15]]
        ref = [detail(i) for i, s in enumerate(summ)
               if s["window"] == [48, 72] and s["lo"] == 0.90 and s["hi"] == 0.95 and s["hold"] == 0.99
               and s["time_exit_h"] is None and s["min_volume"] == 10_000.0
               and s["target"] in (["abs", 0.95], ["delta", 0.03], ["abs", 0.98])]
        ref += [detail(i) for i, s in enumerate(summ)
                if s["window"] == [48, 72] and s["lo"] == 0.90 and s["hi"] == 0.95 and s["hold"] is None
                and s["time_exit_h"] is None and s["min_volume"] == 10_000.0 and s["target"] == ["none", None]]
        # best cell per category, selected on that category's H1 (per-category summaries need the arrays:
        # evaluate the 300 best overall-H1 cells per category plus the reference cells)
        per_cat: dict[str, dict] = {}
        pool = ranked[:300]
        for i in pool:
            cell = one_cell(d, cell_key(summ[i]), mult, fee_mult)
            cat_e = d.mk["cat"][cell["_idx"]]
            for c in cats:
                m = cat_e == c
                if m.sum() < 60:
                    continue
                s = summarize(d, {**cell, "_mask": m}, split_ts)
                if s["h1"]["events"] < 30:
                    continue
                if c not in per_cat or score(s) > score(per_cat[c]):
                    s["ci95"] = bootstrap_ci(d, {**cell, "_mask": m})
                    per_cat[c] = s
        result["by_scenario"][name] = {
            "cells": len(summ), "eligible": len(eligible), "both_halves_nonneg": len(both),
            "share_both_nonneg": round(len(both) / max(1, len(eligible)), 4),
            "top": top, "owner_reference": ref, "best_per_category": per_cat,
            "roi_quantiles": [round(float(q), 5) for q in np.quantile(
                [summ[i]["all"]["roi"] for i in eligible], [0.05, 0.25, 0.5, 0.75, 0.95])] if eligible else None,
            "all_cells": [{k: s[k] for k in ("window", "lo", "hi", "target", "hold", "time_exit_h", "min_volume")}
                          | {"n": s["all"]["n"], "ev": s["all"]["events"], "roi": s["all"]["roi"],
                             "h1": s["h1"]["roi"], "h2": s["h2"]["roi"]} for s in summ],
        }
    result["secs"] = round(time.time() - t0, 1)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m polylab.analysis.cherry_basics")
    ap.add_argument("--split", default="2026-06-15", help="H1 = entries before this UTC date")
    ap.add_argument("--out", default=None)
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    args = ap.parse_args(argv)
    from polylab.settings import paths
    p = paths()
    split = int(datetime.strptime(args.split, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    res = run(p, split, scenarios=tuple(args.scenarios.split(",")),
              log=lambda m: print(m, file=sys.stderr, flush=True))
    out = Path(args.out) if args.out else Path(p.research_dir) / "cherry_basics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=float))
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
