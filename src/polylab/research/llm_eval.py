"""Scoring of the AI 0-0 cross-check study (pre-registered in docs/research/llm-forecast-study.md).

Unit = one game. Forecasters of P(y=1), y = 1 when the game did NOT end 0-0:
- claude / codex (ChatGPT): each engine's canonical forecast (latest successful run of that engine before kickoff)
- consensus: 1 - consensus P(0-0) from the latest ok consensus batch containing the game (before kickoff)
- market:   Polymarket Total 0.5 Over mid at forecast time (only games that had a two-sided Over book)
- poisson:  1 - exp(-λ), λ = mean total goals of that league's finished games before the forecast
            (>= MIN_LEAGUE_N games, else the documented constant DEFAULT_LAMBDA)
Metrics: Brier, log-loss (probabilities clipped to [1e-4, 1-1e-4]), calibration buckets, paired bootstrap of Brier
differences on common subsets. Picks: each forecaster's top-3 → 0-0 rate and a hypothetical $5 of Over 0.5 at the
forecast-time ask (ex-fee). Strategy ledgers (llm-nil-draw, llm-nil-consensus, goal-over-all) are read with paper and
live kept apart; live P&L comes only from the engine's CONFIRMED-fill ledger (realized_pnl of settled positions).
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import time
from pathlib import Path

from polylab.research.llm_forecast import canonical_consensus, canonical_forecasts, connect_ro, db_path

DEFAULT_LAMBDA = 2.75            # top-league mean goals/game when a league has < MIN_LEAGUE_N finished games
MIN_LEAGUE_N = 30
CLIP = 1e-4
BUCKETS = (0.0, 0.80, 0.85, 0.90, 0.93, 0.96, 1.0000001)
PAPER_STAKE = 5.0
PICKS = 3
STRATEGY_IDS = ("llm-nil-draw", "llm-nil-consensus", "goal-over-all")
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


def _ledger(conn: sqlite3.Connection, mode: str) -> dict:
    r = conn.execute("SELECT COUNT(*), SUM(realized_pnl), SUM(cost_usdc), "
                     "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) FROM positions "
                     "WHERE mode=? AND realized_pnl IS NOT NULL", (mode,)).fetchone()
    n_open = conn.execute("SELECT COUNT(*) FROM positions WHERE mode=? AND status IN "
                          "('pending','open','closing','quarantined')", (mode,)).fetchone()[0]
    n, pnl, cost, wins = r[0] or 0, r[1] or 0.0, r[2] or 0.0, r[3] or 0
    return {"settled": n, "open": n_open, "pnl": round(pnl, 4), "roi": round(pnl / cost, 4) if cost else None,
            "wins": wins}


def _strategy_ledgers(paths) -> dict:
    """Per variant: paper and live settled P&L kept apart (live = engine ledger, CONFIRMED fills only)."""
    out = {}
    for sid in STRATEGY_IDS:
        p = Path(paths.strategy_db(sid))
        if not p.exists():
            out[sid] = {"available": False}
            continue
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            out[sid] = {"available": True, "paper": _ledger(conn, "paper"), "live": _ledger(conn, "live")}
        finally:
            conn.close()
    return out


def _picks(rows: list[dict], rank_key: str, ask_key: str) -> dict:
    sel = [r for r in rows if r.get(rank_key) and r[rank_key] <= PICKS]
    priced = [r for r in sel if r.get(ask_key)]
    pnl = [PAPER_STAKE / r[ask_key] * r["y"] - PAPER_STAKE for r in priced]
    return {"n": len(sel), "rate_0_0": round(1 - sum(r["y"] for r in sel) / len(sel), 4) if sel else None,
            "priced": len(priced), "pnl": round(sum(pnl), 4),
            "roi": round(sum(pnl) / (PAPER_STAKE * len(priced)), 4) if priced else None}


def _engine_block(scored: list[dict]) -> dict:
    ys = [r["y"] for r in scored]
    ai = [r["ai_prob"] for r in scored]
    mk = [r for r in scored if r["market_price_at_forecast"] is not None]
    mys = [r["y"] for r in mk]
    return {"resolved": len(scored), "scores": _scores(ai, ys), "poisson": _scores([r["poisson"] for r in scored], ys),
            "minus_poisson_brier": paired_brier_diff(ai, [r["poisson"] for r in scored], ys),
            "market_subset": {"n": len(mk), "ai": _scores([r["ai_prob"] for r in mk], mys),
                              "market": _scores([r["market_price_at_forecast"] for r in mk], mys),
                              "minus_market_brier": paired_brier_diff(
                                  [r["ai_prob"] for r in mk], [r["market_price_at_forecast"] for r in mk], mys)},
            "calibration": calibration(ai, ys),
            "top3": _picks(scored, "rank", "market_ask_at_forecast")}


def evaluate(paths, since: int | None = None, until: int | None = None, now: int | None = None) -> dict:
    now = int(now if now is not None else time.time())
    conn = connect_ro(db_path(paths))
    if conn is None:
        return {"available": False, "reason": "no forecasts yet (research/llm_forecasts.db missing)"}
    core = connect_ro(Path(paths.core_db))
    try:
        fcs = {e: canonical_forecasts(conn, now, engine=e) for e in ("claude", "codex")}
        cons = canonical_consensus(conn, now)
        outcomes = {r["game_key"]: dict(r) for r in conn.execute("SELECT * FROM outcomes")}
        runs = conn.execute("SELECT COUNT(*), SUM(status='ok'), SUM(forced) FROM runs").fetchone()
        try:
            batches = conn.execute("SELECT COUNT(*), SUM(status='ok'), SUM(status='empty') FROM consensus_batches"
                                   ).fetchone()
        except sqlite3.OperationalError:
            batches = (0, 0, 0)
        history = league_lambdas(core)
    finally:
        conn.close()
        if core is not None:
            core.close()

    def in_window(r):
        return (since is None or r["kickoff"] >= since) and (until is None or r["kickoff"] < until)

    def score_rows(rows):
        out = []
        for f in rows:
            o = outcomes.get(f["game_key"])
            if o is None or not in_window(f):
                continue
            pz, lam, n_hist = poisson_p_not_0_0(history, f["league"], f["created_at"])
            out.append({**f, "y": int(o["not_0_0"]), "poisson": pz, "lambda": lam, "lambda_n": n_hist})
        return out

    eng = {e: score_rows(fc.values()) for e, fc in fcs.items()}
    cons_scored = [{**r, "ai_prob": 1 - r["p00_consensus"]} for r in score_rows(cons.values())]
    forecast_games = {gk for fc in fcs.values() for gk, f in fc.items() if in_window(f)}
    resolved = {r["game_key"]: r["y"] for rows in eng.values() for r in rows}
    # joint set: games with a consensus row whose two engine probabilities are those of that batch
    js = cons_scored
    jys = [r["y"] for r in js]
    jm = [r for r in js if r["market_price_at_forecast"] is not None]
    jmys = [r["y"] for r in jm]
    p_cl, p_cx = [1 - r["p00_claude"] for r in js], [1 - r["p00_codex"] for r in js]
    p_cs = [r["ai_prob"] for r in js]
    return {
        "available": True, "generated_at": now, "window": {"since": since, "until": until},
        "runs": {"total": runs[0] or 0, "ok": runs[1] or 0, "forced_reruns": runs[2] or 0},
        "consensus_batches": {"total": batches[0] or 0, "ok": batches[1] or 0, "empty": batches[2] or 0},
        "forecast_games": len(forecast_games), "resolved_games": len(resolved),
        "base_rate_not_0_0": round(sum(resolved.values()) / len(resolved), 4) if resolved else None,
        "engines": {e: _engine_block(rows) for e, rows in eng.items()},
        "joint": {"n": len(js), "with_market_price": len(jm),
                  "claude": _scores(p_cl, jys), "codex": _scores(p_cx, jys), "consensus": _scores(p_cs, jys),
                  "poisson": _scores([r["poisson"] for r in js], jys),
                  "market_subset": {"n": len(jm), "consensus": _scores([r["ai_prob"] for r in jm], jmys),
                                    "market": _scores([r["market_price_at_forecast"] for r in jm], jmys)},
                  "consensus_minus_market_brier": paired_brier_diff(
                      [r["ai_prob"] for r in jm], [r["market_price_at_forecast"] for r in jm], jmys),
                  "claude_minus_codex_brier": paired_brier_diff(p_cl, p_cx, jys),
                  "consensus_top3": _picks(js, "consensus_rank", "market_ask_at_forecast"),
                  "calibration": {"consensus": calibration(p_cs, jys),
                                  "market": calibration([r["market_price_at_forecast"] for r in jm], jmys)}},
        "strategies": _strategy_ledgers(paths),
        "notes": ["market = Over 0.5 mid at forecast time; games without a Total 0.5 market are excluded from "
                  "market comparisons (n shown)", f"poisson λ: league mean goals if >= {MIN_LEAGUE_N} games else "
                  f"{DEFAULT_LAMBDA}", "top-3 P&L: hypothetical $5 at the forecast-time ask, ex-fee",
                  "strategy live P&L = CONFIRMED fills + resolution only; paper is never real money",
                  "ChatGPT (codex) runs without web search by default: an engine asymmetry, not a model comparison"],
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


def _pc(v) -> str:
    return "–" if v is None else f"{v:.0%}"


def _ledger_txt(sid: str, s: dict) -> str:
    if not s.get("available"):
        return f"{sid} 원장 없음"
    p, lv = s["paper"], s["live"]
    out = f"{sid} paper {p['pnl']:+.2f} (n={p['settled']})"
    if lv["settled"] or lv["open"]:
        out += f" / live {lv['pnl']:+.2f} USDC (n={lv['settled']}, 미정산 {lv['open']})"
    return out


def render_lines(sec: dict | None) -> list[str]:
    if not sec:
        return []
    lines = ["## AI 교차검증 0:0 연구", ""]
    if sec.get("error"):
        return lines + [f"평가 실패: {sec['error']}", ""]
    for label, s in (("기간", sec["window"]), ("누적", sec["cumulative"])):
        cl, cx, j = s["engines"]["claude"], s["engines"]["codex"], s["joint"]
        cb = s["consensus_batches"]
        lines += [f"- {label}: 예측 {s['forecast_games']}경기, 결과 확인 {s['resolved_games']} · 합의 batch "
                  f"{cb['ok']} ok / {cb['empty']} 비움",
                  f"  - Brier(결과 확인 경기): Claude {_f(cl['scores']['brier'])} (n={cl['resolved']}) · ChatGPT "
                  f"{_f(cx['scores']['brier'])} (n={cx['resolved']}) · 공동 n={j['n']}: Claude "
                  f"{_f(j['claude']['brier'])} / ChatGPT {_f(j['codex']['brier'])} / 합의 {_f(j['consensus']['brier'])}"
                  f" / Poisson {_f(j['poisson']['brier'])} · 시장 부분집합(n={j['market_subset']['n']}) 합의 "
                  f"{_f(j['market_subset']['consensus']['brier'])} / 시장 {_f(j['market_subset']['market']['brier'])}",
                  f"  - top-3 0:0 비율·가상 손익: Claude {_pc(cl['top3']['rate_0_0'])} {cl['top3']['pnl']:+.2f} "
                  f"(n={cl['top3']['n']}) · ChatGPT {_pc(cx['top3']['rate_0_0'])} {cx['top3']['pnl']:+.2f} "
                  f"(n={cx['top3']['n']}) · 합의 {_pc(j['consensus_top3']['rate_0_0'])} "
                  f"{j['consensus_top3']['pnl']:+.2f} (n={j['consensus_top3']['n']})",
                  "  - 원장: " + " · ".join(_ledger_txt(sid, st) for sid, st in s["strategies"].items())]
    lines += ["", "AI 확률은 `polylab forecast` 가 킥오프 전에 저장한 값만 쓴다. top-3 손익은 예측 시각 ask 기준 가상값(수수료 "
              "제외)이고, 원장 live 손익만 실손익(CONFIRMED)이다. ChatGPT 는 기본적으로 웹 검색 없이 예측한다.", ""]
    return lines


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str)
