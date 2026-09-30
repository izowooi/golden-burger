"""Scoring of the LLM 0-0 study (pre-registered in docs/research/llm-forecast-study.md).

Unit = one game, using the canonical forecast (latest successful run created before kickoff). Target y = 1 when
the game did NOT end 0-0. Forecasters of P(y=1):
- ai:       the LLM's p_not_0_0
- market:   Polymarket Total 0.5 Over mid at forecast time (only games that had a two-sided Over book)
- poisson:  1 - exp(-λ), λ = mean total goals of that league's finished games before the forecast
            (>= MIN_LEAGUE_N games, else the documented constant DEFAULT_LAMBDA)
Metrics: Brier, log-loss (probabilities clipped to [1e-4, 1-1e-4]), calibration buckets, and a paired bootstrap of the
Brier difference on the common subset. Paper P&L: $5 of Over 0.5 at the forecast-time ask for each AI top-3 game
(ex-fee, hypothetical) plus the llm-nil-draw paper ledger (engine paper fills, still paper — never real money).
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import time
from pathlib import Path

from polylab.research.llm_forecast import canonical_forecasts, connect_ro, db_path

DEFAULT_LAMBDA = 2.75            # top-league mean goals/game when a league has < MIN_LEAGUE_N finished games
MIN_LEAGUE_N = 30
CLIP = 1e-4
BUCKETS = (0.0, 0.80, 0.85, 0.90, 0.93, 0.96, 1.0000001)
PAPER_STAKE = 5.0
STRATEGY_ID = "llm-nil-draw"
BOOT_N = 2000
BOOT_SEED = 11


def brier(ps: list[float], ys: list[int]) -> float | None:
    return round(sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys), 5) if ys else None


def log_loss(ps: list[float], ys: list[int]) -> float | None:
    if not ys:
        return None
    tot = 0.0
    for p, y in zip(ps, ys):
        p = min(max(p, CLIP), 1 - CLIP)
        tot -= math.log(p) if y else math.log(1 - p)
    return round(tot / len(ys), 5)


def calibration(ps: list[float], ys: list[int]) -> list[dict]:
    out = []
    for lo, hi in zip(BUCKETS, BUCKETS[1:]):
        sel = [(p, y) for p, y in zip(ps, ys) if lo <= p < hi]
        if sel:
            out.append({"p_lo": lo, "p_hi": min(hi, 1.0), "n": len(sel),
                        "mean_p": round(sum(p for p, _ in sel) / len(sel), 4),
                        "rate": round(sum(y for _, y in sel) / len(sel), 4)})
    return out


def paired_brier_diff(a: list[float], b: list[float], ys: list[int]) -> dict | None:
    """mean[(a-y)^2 - (b-y)^2] with a game-level bootstrap 95% CI. Negative = a is better."""
    n = len(ys)
    if n < 2:
        return None
    d = [(pa - y) ** 2 - (pb - y) ** 2 for pa, pb, y in zip(a, b, ys)]
    rng = random.Random(BOOT_SEED)
    boots = sorted(sum(d[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOT_N))
    return {"n": n, "mean": round(sum(d) / n, 5), "ci95": [round(boots[int(0.025 * BOOT_N)], 5),
                                                          round(boots[int(0.975 * BOOT_N) - 1], 5)]}


def league_lambdas(core: sqlite3.Connection | None) -> list[tuple[str, int, int]]:
    """(league, start_time, total goals) of finished soccer games, for as-of λ estimates."""
    if core is None:
        return []
    return [(r[0], r[1], r[2]) for r in core.execute(
        "SELECT LOWER(league), start_time, home_score + away_score FROM games WHERE sport='soccer' "
        "AND home_score IS NOT NULL AND away_score IS NOT NULL AND ended_at IS NOT NULL")]


def poisson_p_not_0_0(history: list[tuple[str, int, int]], league: str | None, before: int) -> tuple[float, float, int]:
    goals = [t for lg, st, t in history if lg == (league or "").lower() and st is not None and st < before]
    lam = sum(goals) / len(goals) if len(goals) >= MIN_LEAGUE_N else DEFAULT_LAMBDA
    return 1 - math.exp(-lam), round(lam, 3), len(goals)


def _scores(ps, ys) -> dict:
    return {"n": len(ys), "brier": brier(ps, ys), "log_loss": log_loss(ps, ys)}


def _strategy_paper(paths) -> dict:
    p = Path(paths.strategy_db(STRATEGY_ID))
    if not p.exists():
        return {"available": False}
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        r = conn.execute("SELECT COUNT(*), SUM(realized_pnl), SUM(cost_usdc), "
                         "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) FROM positions "
                         "WHERE mode='paper' AND realized_pnl IS NOT NULL").fetchone()
        n_open = conn.execute("SELECT COUNT(*) FROM positions WHERE mode='paper' AND status IN "
                              "('pending','open','closing')").fetchone()[0]
    finally:
        conn.close()
    n, pnl, cost, wins = r[0] or 0, r[1] or 0.0, r[2] or 0.0, r[3] or 0
    return {"available": True, "settled": n, "open": n_open, "pnl": round(pnl, 4),
            "roi": round(pnl / cost, 4) if cost else None, "wins": wins,
            "note": "llm-nil-draw paper ledger (engine paper fills on stored books) — not real money"}


def evaluate(paths, since: int | None = None, until: int | None = None, now: int | None = None) -> dict:
    now = int(now if now is not None else time.time())
    conn = connect_ro(db_path(paths))
    if conn is None:
        return {"available": False, "reason": "no forecasts yet (research/llm_forecasts.db missing)"}
    core = connect_ro(Path(paths.core_db))
    try:
        fc = canonical_forecasts(conn, now)
        outcomes = {r["game_key"]: dict(r) for r in conn.execute("SELECT * FROM outcomes")}
        runs = conn.execute("SELECT COUNT(*), SUM(status='ok'), SUM(forced) FROM runs").fetchone()
        history = league_lambdas(core)
    finally:
        conn.close()
        if core is not None:
            core.close()
    rows = [f for f in fc.values() if (since is None or f["kickoff"] >= since) and (until is None or f["kickoff"] < until)]
    scored = []
    for f in rows:
        o = outcomes.get(f["game_key"])
        if o is None:
            continue
        pz, lam, n_hist = poisson_p_not_0_0(history, f["league"], f["created_at"])
        scored.append({**f, "y": int(o["not_0_0"]), "poisson": pz, "lambda": lam, "lambda_n": n_hist})
    ys = [r["y"] for r in scored]
    ai = [r["ai_prob"] for r in scored]
    po = [r["poisson"] for r in scored]
    mk = [r for r in scored if r["market_price_at_forecast"] is not None]
    mys = [r["y"] for r in mk]
    picks = [r for r in scored if r["rank"] and r["market_ask_at_forecast"]]
    pnl = [PAPER_STAKE / r["market_ask_at_forecast"] * r["y"] - PAPER_STAKE for r in picks]
    return {
        "available": True, "generated_at": now, "window": {"since": since, "until": until},
        "runs": {"total": runs[0] or 0, "ok": runs[1] or 0, "forced_reruns": runs[2] or 0},
        "forecast_games": len(rows), "resolved_games": len(scored),
        "with_market_price": len(mk), "base_rate_not_0_0": round(sum(ys) / len(ys), 4) if ys else None,
        "all_games": {"ai": _scores(ai, ys), "poisson": _scores(po, ys),
                      "ai_minus_poisson_brier": paired_brier_diff(ai, po, ys)},
        "market_subset": {"ai": _scores([r["ai_prob"] for r in mk], mys),
                          "market": _scores([r["market_price_at_forecast"] for r in mk], mys),
                          "poisson": _scores([r["poisson"] for r in mk], mys),
                          "ai_minus_market_brier": paired_brier_diff([r["ai_prob"] for r in mk],
                                                                     [r["market_price_at_forecast"] for r in mk], mys)},
        "calibration": {"ai": calibration(ai, ys), "market": calibration([r["market_price_at_forecast"] for r in mk], mys),
                        "poisson": calibration(po, ys)},
        "top3_paper": {"n": len(picks), "pnl": round(sum(pnl), 4), "wins": sum(r["y"] for r in picks),
                       "roi": round(sum(pnl) / (PAPER_STAKE * len(picks)), 4) if picks else None,
                       "note": "hypothetical $5 at forecast-time Over 0.5 ask, ex-fee, paper only"},
        "strategy_paper": _strategy_paper(paths),
        "notes": ["market = Over 0.5 mid at forecast time; games without a Total 0.5 market are excluded from "
                  "market comparisons (n shown)", f"poisson λ: league mean goals if >= {MIN_LEAGUE_N} games else "
                  f"{DEFAULT_LAMBDA}", "paper only: no real-money result is implied"],
    }


def report_section(paths, since: int, until: int) -> dict | None:
    """Weekly/monthly report hook: window + cumulative summaries; None when the study has no data."""
    try:
        cum = evaluate(paths)
        if not cum.get("available"):
            return None
        return {"window": evaluate(paths, since=since, until=until), "cumulative": cum}
    except Exception as exc:  # the report must never fail because of the side study
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _f(v, nd=4) -> str:
    return "–" if v is None else f"{v:.{nd}f}"


def render_lines(sec: dict | None) -> list[str]:
    if not sec:
        return []
    lines = ["## LLM vs 시장: 0-0 예측 (연구·paper)", ""]
    if sec.get("error"):
        return lines + [f"평가 실패: {sec['error']}", ""]
    for label, s in (("기간", sec["window"]), ("누적", sec["cumulative"])):
        a, m = s["all_games"], s["market_subset"]
        tp = s["top3_paper"]
        lines += [f"- {label}: 예측 {s['forecast_games']}경기, 결과 확인 {s['resolved_games']}, 시장가 있음 "
                  f"{s['with_market_price']} · Brier AI {_f(a['ai']['brier'])} / Poisson {_f(a['poisson']['brier'])}"
                  f" · 시장 부분집합(n={m['ai']['n']}) AI {_f(m['ai']['brier'])} / 시장 {_f(m['market']['brier'])}"
                  f" · top-3 가상 손익 {tp['pnl']:+.2f} USDC (n={tp['n']})"]
    lines += ["", "AI 확률은 `polylab forecast` 가 킥오프 전에 저장한 값만 쓴다. 손익은 모두 paper(가상)이며 실손익이 아니다.", ""]
    return lines


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str)
