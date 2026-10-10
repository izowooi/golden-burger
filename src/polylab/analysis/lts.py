"""LTS — late threshold stability (owner request 2026-10-10 `hypothesis:lts`, docs/research/hypothesis-lts.md).

Question: once a game is past a progress point T and the leader's price is at a threshold Y, how often does it fall
back below Y (re-break), how deep does it dip (max drawdown) and how often does it finally lose (reversal)? And can a
fee-free maker bid one tick BELOW the best bid, held to resolution, make money there?

Pre-registered (doc section 3, frozen before the grid ran):
- clock = wall minutes since the scheduled start (no historical game clock / score; never `ended_at`), T = progress
  x the sport's median game length (SPORT_CFG); one signal per game: the first cycle minute >= T where the leader
  (higher of the two team tokens; soccer = team-win Yes tokens, a draw loses) is inside [Y, min(Y + DELTA, BAND_CAP)],
  both team prices fresh (<= FRESH_S) and the lead >= MIN_MARGIN;
- maker model: limit L = synthetic bid (bar - HALF_SPREAD) - tick (execution.maker.entry_price rule `below`, tick by
  execution.maker.synthetic_tick), frozen (no re-quote); it fills ONLY when a LATER bar is strictly below L, before the
  cancel cycle (fill is checked before cancel in the same minute, as engine.tick.maintain_maker does), at L, fee 0;
  cancel after WAIT minutes (5 / 30 / until the game ends or start + hours_max); floor Y - FLOOR_C (non-binding: a
  bar below a floor that is below L has already filled). Held to resolution;
- taker baseline: the same signals bought at bar + HALF_SPREAD with the current fee 0.05 x shares x p x (1 - p);
- stress: spread 0.02 (L one more cent lower), strict trade-through (bar < L - HALF_SPREAD);
- rules R1-R6 + multiple-comparison check + H1-only selection per arm (king: T >= 80%, queen: T <= 70%); R7 (engine
  replay of the real strategy) is run separately with `polylab backtest` and recorded in the doc.

Outputs (dashboard read model latest/lts/*, docs/contracts/dashboard-json.md):
  <research_dir>/lts/latest/summary.json   scope, constants, progress mapping, per-sport rule counts, MC check,
                                           arm selections with their full rule record
  <research_dir>/lts/latest/grid-<sport>.json   every (T, Y, wait) cell's metrics
"""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
import statistics
import time
from bisect import bisect_left, bisect_right
from functools import lru_cache
from pathlib import Path
from typing import Any

from polylab.analysis import _common as C
from polylab.analysis.calibration import wilson
from polylab.execution.maker import entry_price, synthetic_tick
from polylab.marketview import Book
from polylab.risk import promotion
from polylab.risk.ladder import bootstrap_lower
from polylab.strategies.base import floor2

SPORTS = ("soccer", "mlb", "nba", "nfl", "nhl")
SOCCER_LEAGUES = ("epl", "lal", "bun", "sea", "fl1", "mls", "ucl", "uel", "unl")
# median wall minutes scheduled start -> ended_at (2026-10-10 copy, games < 6 h) and the in-play horizon
SPORT_CFG: dict[str, dict[str, float]] = {
    "soccer": {"median_min": 119.0, "hours_max": 3.0},
    "mlb": {"median_min": 168.0, "hours_max": 6.0},
    "nba": {"median_min": 149.0, "hours_max": 5.0},
    "nfl": {"median_min": 189.0, "hours_max": 6.0},
    "nhl": {"median_min": 164.0, "hours_max": 5.0},
}
PROGRESS = (0.6, 0.7, 0.8, 0.9)
THRESHOLDS = (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.97, 0.99)
WAITS: tuple[int | None, ...] = (5, 30, None)          # minutes; None = until the game ends
DELTA = 0.03
BAND_CAP = 0.995
FLOOR_C = 0.05
HALF_SPREAD = 0.005
STRESS_HALF_SPREAD = 0.01
FRESH_S = 120
MIN_MARGIN = 0.005
MIN_VOLUME = 20_000.0
STAKE = 5.0
TAKER_FEE = 0.05
COLLAPSE_PRICE = 0.10
ARMS = {"king": (0.8, 0.9), "queen": (0.6, 0.7)}
# H1/H2 boundary frozen at the pre-registered run (2026-10-10 copy: median scheduled start of each sport's sample), so
# the weekly recompute keeps "H2" = after this date instead of moving the split as data grows.
SPLIT_AT = {"soccer": 1779044400, "mlb": 1782583710, "nba": 1747596600, "nfl": 1758114450, "nhl": 1761868800}
MC_TOP = 20
EPS = 1e-9


def wall_minute(sport: str, progress: float) -> int:
    return int(round(progress * SPORT_CFG[sport]["median_min"]))


def band(y: float) -> tuple[float, float]:
    return y, round(min(y + DELTA, BAND_CAP), 6)


def wait_key(w: int | None) -> str:
    return "end" if w is None else f"{w}m"


@lru_cache(maxsize=None)
def limit_price(p: float, hs: float = HALF_SPREAD) -> float | None:
    """The resting BUY the engine posts for a synthetic book at bar price p (maker rule `below`)."""
    bid, ask = max(0.001, round(p - hs, 6)), min(0.999, round(p + hs, 6))
    book = Book("x", 0, [(bid, 1e6)], [(ask, 1e6)], "synthetic", True)
    return entry_price(book, synthetic_tick(book.mid), 0.0, 1.0, "below")


def taker_trade(p: float, win: bool, hs: float = HALF_SPREAD) -> tuple[float, float]:
    """(pnl, cost) of buying STAKE at the synthetic ask with the current taker fee, held to resolution."""
    a = min(0.999, round(p + hs, 6))
    shares = STAKE / a
    fee = shares * TAKER_FEE * a * (1 - a)
    cost = STAKE + fee
    return shares * (1.0 if win else 0.0) - cost, cost


def maker_trade(limit: float, win: bool) -> tuple[float, float]:
    shares = floor2(STAKE / limit)
    cost = shares * limit
    return shares * (1.0 if win else 0.0) - cost, cost


def season_of(sport: str, ts: int) -> str:
    d = time.gmtime(ts)
    if sport == "mlb":
        return str(d.tm_year)
    y = d.tm_year if d.tm_mon >= 7 else d.tm_year - 1
    return f"{y}-{(y + 1) % 100:02d}" if sport != "nfl" else str(y)


# ------------------------------------------------------------------ loading

def load_games(conn: sqlite3.Connection, sport: str, until: int) -> list[dict]:
    """Sample games: ended & resolved before `until`, result-market volume >= MIN_VOLUME, no preseason, 2 team tokens."""
    sql = ("SELECT game_key, league, start_time, ended_at FROM games WHERE sport=? AND start_time IS NOT NULL "
           "AND start_time < ? AND (status IS NULL OR status != 'cancelled')")
    args: list[Any] = [sport, until - int(SPORT_CFG[sport]["hours_max"] * 3600)]
    if sport == "soccer":
        sql += f" AND LOWER(league) IN ({','.join('?' * len(SOCCER_LEAGUES))})"
        args += list(SOCCER_LEAGUES)
    games = []
    for gk, league, start, ended in conn.execute(sql + " ORDER BY start_time", args):
        if sport != "soccer" and promotion.in_preseason(sport, int(start)):
            continue
        rows = conn.execute(
            "SELECT m.condition_id, m.market_type, m.volume, m.resolved_outcome_index, t.token_id, t.side, "
            "t.outcome_index FROM markets m JOIN tokens t ON t.condition_id=m.condition_id "
            "WHERE m.game_key=? AND m.market_type IN ('moneyline','draw')", (gk,)).fetchall()
        vol = sum({r[0]: (r[2] or 0.0) for r in rows}.values())
        if vol < MIN_VOLUME:
            continue
        team = [r for r in rows if r[1] == "moneyline" and r[5] in ("home", "away")]
        if sport == "soccer":
            if len({r[0] for r in team}) != 2 or not any(r[1] == "draw" for r in rows):
                continue
        elif len(team) != 2 or len({r[0] for r in team}) != 1:
            continue
        if any(r[3] is None for r in team):
            continue
        games.append({"game_key": gk, "league": league, "start": int(start),
                      "ended": int(ended) if ended else None,
                      "tokens": [(r[4], int(r[3]) == int(r[6])) for r in sorted(team, key=lambda r: r[5])]})
    return games


def canonical_bars(conn: sqlite3.Connection, token: str, since: int, until: int) -> tuple[list[int], list[float]]:
    best: dict[int, tuple[int, float]] = {}
    for ts, src, price in conn.execute("SELECT ts, source, price FROM price_bars WHERE token_id=? AND ts>=? AND ts<=?",
                                       (token, since, until)):
        r = C.SOURCE_RANK.get(src, 9)
        if ts not in best or r < best[ts][0]:
            best[ts] = (r, float(price))
    keys = sorted(best)
    return keys, [best[k][1] for k in keys]


def _price_at(ts: list[int], px: list[float], t: int) -> float | None:
    i = bisect_right(ts, t) - 1
    if i < 0 or t - ts[i] > FRESH_S:
        return None
    return px[i]


# ------------------------------------------------------------------ per-game signals

def game_signals(conn: sqlite3.Connection, sport: str, g: dict) -> list[dict]:
    """Every (T, Y) signal of one game with its fill outcome per wait and stress model."""
    cfg = SPORT_CFG[sport]
    start = g["start"]
    limit = start + int(cfg["hours_max"] * 3600)
    end = min(limit, g["ended"]) if g["ended"] else limit
    if end <= start:
        return []
    bars = [canonical_bars(conn, tok, start - 300, limit) for tok, _ in g["tokens"]]
    cycles = list(range(start + (-start % 60), end, 60))       # engine cycles: minute-aligned, game not over
    lead: list[tuple[int, float, int]] = []                     # (cycle, leader price, token index)
    for t in cycles:
        a, b = _price_at(*bars[0], t), _price_at(*bars[1], t)
        if a is None or b is None or abs(a - b) + EPS < MIN_MARGIN:
            continue
        lead.append((t, a, 0) if a > b else (t, b, 1))
    if not lead:
        return []
    out = []
    lead_ts = [x[0] for x in lead]
    for prog in PROGRESS:
        tmin = wall_minute(sport, prog)
        i0 = bisect_left(lead_ts, start + tmin * 60)
        for y in THRESHOLDS:
            lo, hi = band(y)
            hit = next((x for x in lead[i0:] if lo - EPS <= x[1] <= hi + EPS), None)
            if hit is None:
                continue
            t_sig, p, k = hit
            tok_ts, tok_px = bars[k]
            win = g["tokens"][k][1]
            j = bisect_right(tok_ts, t_sig)
            after_ts, after_px = tok_ts[j:], tok_px[j:]
            stop = g["ended"] or limit
            path = [q for s, q in zip(after_ts, after_px) if s <= stop]
            low = min(path) if path else p
            sig = {"progress": prog, "t_min": tmin, "y": y, "ts": t_sig, "p": p, "win": win,
                   "wall": round((t_sig - start) / 60, 1), "rebreak": low < y - EPS, "mdd": round(max(0.0, p - low), 4),
                   "fills": {}}
            for model, hs, strict in (("main", HALF_SPREAD, False), ("spread02", STRESS_HALF_SPREAD, False),
                                      ("strict", HALF_SPREAD, True)):
                lim = limit_price(p, hs)
                if lim is None:
                    continue
                trig = lim - (hs if strict else 0.0)
                for w in WAITS:
                    cancel = min(t_sig + w * 60 if w is not None else limit, limit,
                                 _ceil60(g["ended"]) if g["ended"] else limit)
                    fill = next(((s, q) for s, q in zip(after_ts, after_px) if s <= cancel and q < trig - EPS), None)
                    if fill is not None and fill[0] > cancel:
                        fill = None
                    sig["fills"][(model, w)] = (lim, fill)
            out.append(sig)
    return out


def _ceil60(ts: int) -> int:
    return ts + (-ts % 60)


# ------------------------------------------------------------------ aggregation

def _roi(trades: list[tuple[float, float]]) -> float | None:
    cost = sum(c for _, c in trades)
    return round(sum(p for p, _ in trades) / cost, 6) if cost else None


def _stats(trades: list[tuple[float, float, int]], split: int) -> dict:
    """trades: (pnl, cost, ts). Totals, halves and bootstrap bounds of the per-trade ROI."""
    h1 = [(p, c) for p, c, t in trades if t < split]
    h2 = [(p, c) for p, c, t in trades if t >= split]
    rois = [p / c for p, c, _ in trades if c]
    return {"n": len(trades), "pnl": round(sum(p for p, _, _ in trades), 4),
            "cost": round(sum(c for _, c, _ in trades), 4), "roi": _roi([(p, c) for p, c, _ in trades]),
            "h1": {"n": len(h1), "pnl": round(sum(p for p, _ in h1), 4), "cost": round(sum(c for _, c in h1), 4),
                   "roi": _roi(h1)},
            "h2": {"n": len(h2), "pnl": round(sum(p for p, _ in h2), 4), "cost": round(sum(c for _, c in h2), 4),
                   "roi": _roi(h2)},
            "lo80": _r(bootstrap_lower(rois)) if len(rois) >= 2 else None,
            "lo95": _r(bootstrap_lower(rois, q=0.05)) if len(rois) >= 2 else None}


def _r(x: float | None, nd: int = 6) -> float | None:
    return None if x is None else round(x, nd)


def _q(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    v = sorted(xs)
    return round(v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))], 4)


def cell_metrics(sigs: list[dict], sport: str, wait: int | None, split: int) -> dict:
    n = len(sigs)
    wins = sum(1 for s in sigs if s["win"])
    mean_p = sum(s["p"] for s in sigs) / n if n else None
    lo, hi = wilson(wins, n) if n else (None, None)
    win_rate = wins / n if n else None
    out: dict[str, Any] = {
        "signals": n, "mean_price": _r(mean_p, 4), "win_rate": _r(win_rate, 4),
        "win_ci": [_r(lo, 4), _r(hi, 4)], "gap": _r(win_rate - mean_p, 4) if n else None,
        "gap_ci": [_r(lo - mean_p, 4), _r(hi - mean_p, 4)] if n else [None, None],
        "rebreak_rate": _r(sum(s["rebreak"] for s in sigs) / n, 4) if n else None,
        "reversal_rate": _r(1 - win_rate, 4) if n else None,
        "mdd_median": _q([s["mdd"] for s in sigs], 0.5), "mdd_p90": _q([s["mdd"] for s in sigs], 0.9),
        "mdd_win_median": _q([s["mdd"] for s in sigs if s["win"]], 0.5),
        "mdd_win_p90": _q([s["mdd"] for s in sigs if s["win"]], 0.9),
        "median_wall": _q([s["wall"] for s in sigs], 0.5),
    }
    taker = [(*taker_trade(s["p"], s["win"]), s["ts"]) for s in sigs]
    out["taker"] = _stats(taker, split)
    for model in ("main", "spread02", "strict"):
        trades, seasons, fw, fl, collapse, fill_wait = [], {}, 0, 0, 0, []
        for s in sigs:
            lim, fill = s["fills"].get((model, wait), (None, None))
            if fill is None:
                continue
            pnl, cost = maker_trade(lim, s["win"])
            trades.append((pnl, cost, s["ts"]))
            seasons.setdefault(season_of(sport, s["ts"]), []).append((pnl, cost))
            fw += s["win"]
            fl += not s["win"]
            collapse += fill[1] <= COLLAPSE_PRICE
            fill_wait.append((fill[0] - s["ts"]) / 60)
        st = _stats(trades, split)
        if model == "main":
            st.update({
                "fill_rate": _r(len(trades) / n, 4) if n else None,
                "fill_rate_win": _r(fw / wins, 4) if wins else None,
                "fill_rate_loss": _r(fl / (n - wins), 4) if n - wins else None,
                "fill_win_rate": _r(fw / len(trades), 4) if trades else None,
                "mean_limit": _r(sum(s["fills"][(model, wait)][0] for s in sigs
                                     if s["fills"].get((model, wait), (None, None))[1] is not None) / len(trades), 4)
                if trades else None,
                "collapse_fills": collapse, "fill_wait_median_min": _q(fill_wait, 0.5),
                "itt_pnl_per_signal": _r(st["pnl"] / n, 4) if n else None,
                "seasons": {k: {"n": len(v), "roi": _roi(v)} for k, v in sorted(seasons.items())}})
        out[model] = st
    return out


def build_grid(sigs_by_cell: dict[tuple[float, float], list[dict]], sport: str, split: int) -> list[dict]:
    cells = []
    for prog in PROGRESS:
        for y in THRESHOLDS:
            sigs = sigs_by_cell.get((prog, y), [])
            for w in WAITS:
                cells.append({"progress": prog, "t_min": wall_minute(sport, prog), "y": y, "band": list(band(y)),
                              "wait": wait_key(w), **cell_metrics(sigs, sport, w, split)})
    return cells


# ------------------------------------------------------------------ rules, multiple comparison, selection

def _neighbors(cells: dict, cell: dict) -> list[dict]:
    pi, yi = PROGRESS.index(cell["progress"]), THRESHOLDS.index(cell["y"])
    out = []
    for dp, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        if 0 <= pi + dp < len(PROGRESS) and 0 <= yi + dy < len(THRESHOLDS):
            out.append(cells[(PROGRESS[pi + dp], THRESHOLDS[yi + dy], cell["wait"])])
    return out


def _pooled(group: list[dict], half: str | None = None) -> float | None:
    """Pooled maker ROI of several cells (sum pnl / sum cost), optionally one half only."""
    pnl = cost = 0.0
    for c in group:
        st = c["main"] if half is None else c["main"][half]
        pnl += st["pnl"]
        cost += st["cost"]
    return round(pnl / cost, 6) if cost else None


def rule_record(cells: dict, cell: dict, rule: promotion.SampleRule) -> dict:
    m = cell["main"]
    nmin, hmin = rule.backtest_min_n, rule.backtest_min_half_n
    ok = lambda x: x is not None and x >= 0  # noqa: E731
    r1 = (m["n"] >= nmin and ok(m["roi"]) and ok(m["h1"]["roi"]) and ok(m["h2"]["roi"])
          and m["h1"]["n"] >= hmin and m["h2"]["n"] >= hmin)
    nb = [c for c in _neighbors(cells, cell) if c["main"]["n"] >= hmin]
    nb_ok = sum(1 for c in nb if ok(c["main"]["roi"]))
    pooled = _pooled([cell, *nb])
    r2 = bool(nb) and nb_ok * 2 >= len(nb) and ok(pooled)
    r3 = m["lo80"] is not None and m["lo80"] > 0
    r4 = m["h1"]["pnl"] >= STAKE and m["h2"]["pnl"] >= STAKE
    r5 = all(ok(s["roi"]) for s in m["seasons"].values() if s["n"] >= 20)
    r6 = ok(cell["spread02"]["roi"]) and ok(cell["strict"]["roi"])
    return {"R1": r1, "R2": r2, "R3": r3, "R4": r4, "R5": r5, "R6": r6, "sig95": m["lo95"] is not None and m["lo95"] > 0,
            "all_R1_R6": all((r1, r2, r3, r4, r5, r6)), "neighbors": len(nb), "neighbors_ok": nb_ok,
            "pooled_roi": pooled}


def mc_check(cells: list[dict], rule: promotion.SampleRule) -> dict:
    eligible = [c for c in cells if c["main"]["h1"]["n"] >= rule.backtest_min_half_n and c["main"]["h1"]["roi"] is not None]
    top = sorted(eligible, key=lambda c: (-c["main"]["h1"]["roi"], -c["main"]["h1"]["n"]))[:MC_TOP]
    h2 = [c["main"]["h2"]["roi"] for c in top if c["main"]["h2"]["roi"] is not None]
    return {"top": len(top), "h2_median": _r(statistics.median(h2)) if h2 else None,
            "h2_nonneg": sum(1 for x in h2 if x >= 0), "h2_n": len(h2)}


def select_arm(cells: dict, sport: str, arm: str, rule: promotion.SampleRule) -> dict | None:
    """H1-only selection inside the arm's progress stratum: max H1 neighbour-pooled ROI, tie -> larger H1 n."""
    best, key = None, None
    for c in cells.values():
        if c["progress"] not in ARMS[arm] or c["main"]["h1"]["n"] < rule.backtest_min_half_n:
            continue
        nb = [x for x in _neighbors(cells, c) if x["main"]["h1"]["n"] >= rule.backtest_min_half_n]
        score = _pooled([c, *nb], "h1")
        if score is None:
            continue
        k = (score, c["main"]["h1"]["n"])
        if key is None or k > key:
            best, key = c, k
    if best is None:
        return None
    return {"arm": arm, "progress": best["progress"], "t_min": best["t_min"], "y": best["y"], "wait": best["wait"],
            "h1_score": key[0], "cell": best}


# ------------------------------------------------------------------ run

def study(conn: sqlite3.Connection, until: int, sports: tuple[str, ...] = SPORTS) -> dict:
    res: dict[str, Any] = {"sports": {}}
    for sport in sports:
        games = load_games(conn, sport, until)
        if not games:
            res["sports"][sport] = {"games": 0}
            continue
        split = SPLIT_AT.get(sport) or int(statistics.median(g["start"] for g in games))
        by_cell: dict[tuple[float, float], list[dict]] = {}
        for g in games:
            for s in game_signals(conn, sport, g):
                by_cell.setdefault((s["progress"], s["y"]), []).append(s)
        grid = build_grid(by_cell, sport, split)
        cells = {(c["progress"], c["y"], c["wait"]): c for c in grid}
        rule = promotion.sample_rule(sport)
        for c in grid:
            c["rules"] = rule_record(cells, c, rule)
        arms = {}
        for arm in ARMS:
            sel = select_arm(cells, sport, arm, rule)
            if sel is not None:
                sel["rules"] = sel["cell"]["rules"]
                sel["h2_roi"] = sel["cell"]["main"]["h2"]["roi"]
                sel["cell"] = {k: v for k, v in sel["cell"].items() if k != "rules"}
            arms[arm] = sel
        lengths = [(g["ended"] - g["start"]) / 60 for g in games if g["ended"] and 0 < g["ended"] - g["start"] < 6 * 3600]
        res["sports"][sport] = {
            "games": len(games), "first_start": C.iso(games[0]["start"]), "last_start": C.iso(games[-1]["start"]),
            "split": C.iso(split), "rule": rule.as_dict(),
            "median_game_min_now": _r(statistics.median(lengths), 1) if lengths else None,
            "median_game_min_used": SPORT_CFG[sport]["median_min"],
            "progress_minutes": {f"{int(p * 100)}": wall_minute(sport, p) for p in PROGRESS},
            "passing_cells": sum(1 for c in grid if c["rules"]["all_R1_R6"]),
            "r1_cells": sum(1 for c in grid if c["rules"]["R1"]),
            "mc": mc_check(grid, rule), "arms": arms, "grid": grid}
    return res


def constants() -> dict:
    return {"progress": list(PROGRESS), "thresholds": list(THRESHOLDS), "waits": [wait_key(w) for w in WAITS],
            "delta": DELTA, "band_cap": BAND_CAP, "floor_c": FLOOR_C, "half_spread": HALF_SPREAD,
            "stress_half_spread": STRESS_HALF_SPREAD, "fresh_s": FRESH_S, "min_margin": MIN_MARGIN,
            "min_volume_usd": MIN_VOLUME, "stake_usdc": STAKE, "taker_fee_rate": TAKER_FEE,
            "soccer_leagues": list(SOCCER_LEAGUES), "arms": {k: list(v) for k, v in ARMS.items()},
            "sport_cfg": SPORT_CFG}


NOTES = [
    "신호 = 예정 시작 뒤 벽시계 분 ≥ T 인 첫 분 중 선두 가격이 [Y, Y+0.03] 안에 있는 분, 경기당 1회. T(%) = 진행률 × 종목 중앙 경기 길이",
    "maker 체결 = 그 뒤의 1분 가격이 지정가(합성 매수호가 − 1틱, 매수호가 = 가격 − 0.005)보다 엄격히 낮을 때만, 지정가로, 수수료 0. "
    "지정가는 고정(재호가 없음), 대기 시간(5분 / 30분 / 경기 끝) 뒤 취소. taker = 가격 + 0.005 에 즉시 매수, 수수료 0.05 × p(1−p)",
    "보정(실현 승률 vs 가격)은 신호 순간의 (종목, 진행 시점, 가격) 조건부 — 거래 판단에 쓰는 질문이다. /explore 의 ‘최종 승자 vs 패자’ "
    "경로는 결과로 먼저 나눈 그림(사후 정보)이라 거래에 쓸 수 없다",
    "R1–R6·다중 비교 점검(H1 선택 → H2 평가)은 여기서 계산한다. R7(실제 전략 코드의 엔진 재생)은 docs/research/hypothesis-lts.md 5절",
]


def cache_dir(paths) -> Path:
    return Path(paths.research_dir) / "lts" / "latest"


def _dump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":"), allow_nan=False).encode()


def _finite(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def run(paths, now: int | None = None) -> dict:
    now = now or int(time.time())
    t0 = time.time()
    conn = C.open_ro(paths.core_db)
    if conn is None:
        raise FileNotFoundError(f"core db missing: {paths.core_db}")
    try:
        res = study(conn, now)
    finally:
        conn.close()
    root = cache_dir(paths)
    tmp = root.with_name("latest.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    summary = {"generated_at": C.iso(now), "doc": "docs/research/hypothesis-lts.md", "constants": constants(),
               "notes": NOTES, "seconds": None, "sports": {}}
    for sport, s in res["sports"].items():
        grid = s.pop("grid", [])
        summary["sports"][sport] = s
        (tmp / f"grid-{sport}.json").write_bytes(_dump(_finite({"generated_at": C.iso(now), "sport": sport,
                                                                 "cells": grid})))
    summary["seconds"] = round(time.time() - t0, 1)
    (tmp / "summary.json").write_bytes(_dump(_finite(summary)))
    shutil.rmtree(root, ignore_errors=True)
    tmp.replace(root)
    return {"generated_at": C.iso(now), "seconds": summary["seconds"], "dir": str(root),
            "games": {k: v.get("games", 0) for k, v in summary["sports"].items()}}


def cached_files(paths) -> dict[str, Path]:
    root = cache_dir(paths)
    if not root.exists():
        return {}
    return {f"latest/lts/{p.relative_to(root).as_posix()}": p for p in sorted(root.glob("*.json"))}
