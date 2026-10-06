"""`polylab backtest --variant ID --from YYYY-MM-DD --to YYYY-MM-DD [--params JSON] ...`

Replays a variant's own entry/exit logic over stored core.db data with the live engine code
path (run_variant in paper mode) against a historical MarketView:
- every query is as-of the simulated minute; games are "live" by start/ended_at, not status;
- stored books are used when present, otherwise a synthetic book (price ± spread/2, deep);
- fills use the paper broker (full-depth FOK walk); fees from the stored schedule, unknown
  fees are counted in `fee_unknown_trades` rather than assumed zero;
- positions still open at the end are settled from the exact resolution if one exists.
Deterministic: same DB + args -> same JSON (ids are not part of the output).

Output JSON: {"variant", "range": {from, to}, "params", "step_s", "spread", "books": bool,
  "summary": {n, wins, losses, pnl, cost, roi, win_rate, max_dd, avg_entry, fee_unknown_trades,
              open_at_end, unfilled, by_exit_reason, by_sport, avg_hold_min, p05_return},
  "trades": [{opened_at, closed_at, sport, game_key, token_id, outcome, entry_price, shares, cost,
              exit_reason, exit_price, pnl, hold_min}]}
Maker variants (`order_style: maker`): stored pre-game books are sparse (10-min polls, mostly synthetic further out)
and the paper maker simulation never fills on synthetic books, so a faithful maker replay is nearly always n=0.
`--fill-model auto` (default) therefore replays maker variants with TAKER fills as a proxy (output
`fill_model: "taker_proxy"`; fees are charged, so it is pessimistic on cost and optimistic on fill rate) — enough to
compare current vs proposed entry window / TP params; `--fill-model maker` runs the conservative maker simulation.
goal_over pre-game entry windows (`entry_minutes_before_max`) are simulated at a coarse >= 10-minute step (book
cadence); in-game windows keep `--step`.
`--fee-rate R` replaces every market's stored fee schedule with the taker schedule rate R (exponent 1, taker-only):
2024–25 markets were mostly fee-free, so gate evidence (promotion, AI live, retune) is replayed at the current sports
schedule (`CURRENT_SPORTS_FEE_RATE`, sports_fees_v3) instead of the historical one.
Holding time of a resolution exit ends at the market's resolved_at (the final settlement pass
stamps closed_at far in the future); p05_return is the 5% quantile of per-trade pnl/cost.
"""

from __future__ import annotations

import argparse
import calendar
import copy
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from polylab import registry, settings
from polylab.marketview import DEFAULT_MAX_IN_PLAY_HOURS, MarketView, _ro, book_shards_for_range

DAY = 86400


def _parse_day(s: str) -> int:
    return calendar.timegm(time.strptime(s, "%Y-%m-%d"))


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


PRE_GAME_STEP_S = 600
CURRENT_SPORTS_FEE_RATE = 0.05   # sports_fees_v3 taker rate (exponent 1, taker-only), used for gate evidence replays


def active_steps(core: sqlite3.Connection, variant, start: int, end: int, step: int) -> list[int]:
    """Minutes worth simulating: around games of the variant's sports (skips dead time)."""
    params = variant.params
    pre_h = float(params.get("entry_hours_max", 0)) if variant.family == "cherry" else 0.0
    if variant.family == "goal_over":       # pre-game window at book cadence, the game itself at `step`
        pre_h = float(params.get("entry_minutes_before_max", 0) or 0) / 60.0
        coarse = _windows(core, variant, start, end, pre_h, 0.0, pre_only=True)
        fine = set(_steps(_windows(core, variant, start, end, 0.0, pre_h), start, end, step))
        return sorted(fine | set(_steps(coarse, start, end, max(step, PRE_GAME_STEP_S))))
    return _steps(_windows(core, variant, start, end, pre_h, pre_h), start, end, step)


def _windows(core, variant, start: int, end: int, pre_h: float, lookahead_h: float,
             pre_only: bool = False) -> list[list[int]]:
    params = variant.params
    sports = variant.sports or list(DEFAULT_MAX_IN_PLAY_HOURS)
    if getattr(variant, "per_sport", False):
        sports = [s for s in sports if variant.sport_mode(s) != "off"] or sports
    rows = core.execute(
        f"SELECT sport, start_time FROM games WHERE sport IN ({','.join('?' * len(sports))}) "
        "AND start_time BETWEEN ? AND ?",
        (*sports, start - 2 * DAY, end + int(max(pre_h, lookahead_h) * 3600) + DAY)).fetchall()
    windows = []
    for r in rows:
        age_h = float((params.get("sport_overrides") or {}).get(r["sport"], {}).get(
            "hours_max", params.get("hours_max", DEFAULT_MAX_IN_PLAY_HOURS.get(r["sport"], 6.0))))
        if pre_only:
            windows.append((r["start_time"] - int(pre_h * 3600), r["start_time"]))
        else:
            windows.append((r["start_time"] - int(pre_h * 3600), r["start_time"] + int((age_h + 1) * 3600)))
    windows.sort()
    merged: list[list[int]] = []
    for a, b in windows:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def _steps(merged: list[list[int]], start: int, end: int, step: int) -> list[int]:
    steps = []
    for a, b in merged:
        t = max(a, start)
        t -= t % step
        while t < min(b, end):
            if t >= start:
                steps.append(t)
            t += step
    return steps


def max_drawdown(pnls: list[float]) -> float:
    peak = cum = dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def hold_minutes(row: dict, resolved_at: int | None) -> float | None:
    if row.get("closed_at") is None or row.get("opened_at") is None:
        return None
    end = row["closed_at"]
    if (row.get("exit_reason") or "").startswith("resolution") and resolved_at is not None:
        end = min(end, resolved_at)
    return round(max(0, end - row["opened_at"]) / 60.0, 2)


def quantile(xs: list[float], q: float) -> float | None:
    """Linear-interpolated quantile (numpy 'linear'), stdlib only."""
    if not xs:
        return None
    v = sorted(xs)
    pos = (len(v) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def summarize(rows: list[dict]) -> dict[str, Any]:
    settled = [r for r in rows if r["status"] in ("closed", "resolved") and r["realized_pnl"] is not None]
    settled.sort(key=lambda r: (r["closed_at"], r["opened_at"], r["token_id"]))
    pnls = [r["realized_pnl"] for r in settled]
    cost = sum(r["cost_usdc"] or 0 for r in settled)
    by_reason: dict[str, int] = {}
    by_sport: dict[str, dict] = {}
    for r in settled:
        by_reason[r["exit_reason"]] = by_reason.get(r["exit_reason"], 0) + 1
        s = by_sport.setdefault(r["sport"] or "?", {"n": 0, "pnl": 0.0})
        s["n"] += 1
        s["pnl"] = round(s["pnl"] + r["realized_pnl"], 6)
    n = len(settled)
    return {
        "n": n,
        "wins": sum(1 for p in pnls if p > 0),
        "losses": sum(1 for p in pnls if p <= 0),
        "pnl": round(sum(pnls), 6),
        "cost": round(cost, 6),
        "roi": round(sum(pnls) / cost, 6) if cost else None,
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 6) if n else None,
        "max_dd": round(max_drawdown(pnls), 6),
        "avg_entry": round(sum(r["entry_price"] for r in settled) / n, 6) if n else None,
        "fee_unknown_trades": sum(1 for r in settled if (r.get("notes") or "") == "fee_unknown"),
        "open_at_end": sum(1 for r in rows if r["status"] in ("open", "pending", "closing", "quarantined")),
        "unfilled": sum(1 for r in rows if r["status"] == "unfilled"),
        "by_exit_reason": dict(sorted(by_reason.items())),
        "by_sport": dict(sorted(by_sport.items())),
        "avg_hold_min": (round(sum(h) / len(h), 2) if (h := [r["hold_min"] for r in settled
                                                          if r.get("hold_min") is not None]) else None),
        "p05_return": (round(q, 6) if (q := quantile([r["realized_pnl"] / r["cost_usdc"] for r in settled
                                                       if r["cost_usdc"]], 0.05)) is not None else None),
    }


def _as_taker(variant) -> bool:
    """Switch a maker variant's simulation copy to taker fills (in place). True if anything was maker."""
    params = copy.deepcopy(variant.params)
    hit = str(params.get("order_style") or "").lower() == "maker"
    if hit:
        params["order_style"] = "taker"
    for over in (params.get("sport_overrides") or {}).values():
        if isinstance(over, dict) and str(over.get("order_style") or "").lower() == "maker":
            over["order_style"] = "taker"
            hit = True
    variant.params = params
    return hit


def backtest(paths, variant, start: int, end: int, *, step: int = 60, spread: float = 0.01,
             fill_model: str = "auto", fee_rate: float | None = None) -> dict[str, Any]:
    from polylab.engine.tick import run_variant           # engine imports stay local: engine is the heavier module
    from polylab.execution.ledger import open_ledger
    from polylab.execution.reconcile import settle_resolutions

    core = _ro(paths.core_db)
    if core is None:
        raise FileNotFoundError(f"core db missing: {paths.core_db}")
    shards = book_shards_for_range(paths, start, end)
    view = MarketView(core, shards, historical=True, synthetic_spread=spread)
    if fee_rate is not None:
        from polylab.execution.fees import FeeSchedule  # noqa: PLC0415
        view.fee_override = FeeSchedule(float(fee_rate), 1.0, True, f"override_{fee_rate:g}")
    sim_variant = copy.copy(variant)
    sim_variant.mode = "paper"
    used_fill = "as_configured"
    if fill_model in ("auto", "taker") and _as_taker(sim_variant):
        used_fill = "taker_proxy"
    # per-sport variants: simulate every sport that is not off, whatever its live/paper mode
    sports = ([s for s in variant.sports if sim_variant.sport_mode(s) != "off"]
              if getattr(variant, "per_sport", False) else None)
    with tempfile.TemporaryDirectory(prefix="polylab-bt-") as tmp:
        sim_paths = settings.Paths(Path(tmp)).ensure()
        errors: dict[str, int] = {}
        for t in active_steps(core, sim_variant, start, end, step):
            res = run_variant(sim_paths, sim_variant, view, t, mode="paper", kill=False, sports=sports)
            if not res.ok:
                errors[res.error or "?"] = errors.get(res.error or "?", 0) + 1
        ledger = open_ledger(sim_paths, variant.id)
        settle_resolutions(ledger, MarketView(core, shards, historical=False), end + 365 * DAY, "paper")
        rows = [dict(r) for r in ledger.positions("paper")]
        ledger.conn.close()
    for r in rows:
        m = view.market(r["condition_id"])
        r["hold_min"] = hold_minutes(r, m.resolved_at if m else None)
    view.close()
    trades = [{
        "opened_at": r["opened_at"], "closed_at": r["closed_at"], "sport": r["sport"], "game_key": r["game_key"],
        "token_id": r["token_id"], "outcome": r["outcome_label"], "entry_price": r["entry_price"],
        "shares": r["shares"] if r["status"] in ("open", "pending") else None, "cost": r["cost_usdc"],
        "status": r["status"], "exit_reason": r["exit_reason"], "exit_price": r["exit_price"],
        "pnl": r["realized_pnl"], "hold_min": r["hold_min"],
    } for r in sorted(rows, key=lambda r: (r["opened_at"], r["token_id"])) if r["status"] != "unfilled"]
    return {
        "variant": variant.id, "family": variant.family,
        "range": {"from": start, "to": end}, "params": variant.params, "stake_usdc": variant.stake_usdc,
        "step_s": step, "spread": spread, "books": bool(shards), "fill_model": used_fill, "fee_rate": fee_rate,
        "summary": summarize(rows), "errors": errors, "trades": trades,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab backtest")
    ap.add_argument("--variant", help="variant id in strategies/ (or use --variant-file)")
    ap.add_argument("--variant-file", help="YAML of a proposed variant (overrides --variant lookup)")
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD (UTC, inclusive)")
    ap.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD (UTC, exclusive)")
    ap.add_argument("--params", help="JSON object merged over the variant params (autopilot proposals)")
    ap.add_argument("--step", type=int, default=None, help="seconds between simulated cycles (default 60; cherry 300)")
    ap.add_argument("--spread", type=float, default=0.01, help="synthetic spread when no stored book exists")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    ap.add_argument("--fill-model", choices=("auto", "maker", "taker"), default="auto",
                    help="auto/taker: maker variants replay with taker fills (proxy); maker: conservative maker sim")
    ap.add_argument("--fee-rate", type=float, default=None,
                    help="force this taker fee rate on every market (default: stored schedules)")
    args = ap.parse_args(argv)
    if not args.variant and not args.variant_file:   # the retro passes only --variant-file (staged proposal yaml)
        ap.error("one of --variant / --variant-file is required")

    if args.variant_file:
        variant = registry.load_variant(Path(args.variant_file))
    else:
        found = [v for v in registry.load_all(include_off=True) if v.id == args.variant]
        if not found:
            print(f"unknown variant {args.variant}", file=sys.stderr)
            return 2
        variant = found[0]
    if args.params:
        variant.params = _merge(variant.params, json.loads(args.params))
    step = args.step or (300 if variant.family == "cherry" else 60)
    result = backtest(settings.paths(), variant, _parse_day(args.start), _parse_day(args.end),
                      step=step, spread=args.spread, fill_model=args.fill_model, fee_rate=args.fee_rate)
    text = json.dumps(result, indent=1, sort_keys=True, default=str)
    if args.out:
        Path(args.out).write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
