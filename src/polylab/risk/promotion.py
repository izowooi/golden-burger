"""Deterministic paper→live promotion gate (owner decision 2026-10-06 `sports3:auto-promotion`).

A (variant, sport) of a per-sport variant that is in paper goes live at 5 USDC only when ALL hold:
  eligibility  per-sport variant with an account, the sport's own mode is paper (master not off), no
               future `live_from`, today outside the sport's preseason window, >= 3 days since the sport's
               last param change or stake/mode event, and (master paper) no other sport already set live
               (flipping the master to live must not take another sport live with it);
  paper        >= 30 settled paper trades of that sport on its current param version (only trades opened
               after the sport's last stake/mode event: a sport demoted by the ladder cannot be re-promoted
               on its pre-promotion paper record; preseason-window trades excluded), paper ROI/trade 80% bootstrap lower bound > 0
               (risk.ladder.bootstrap_lower), and both halves of that sample (split at the median entry)
               with P&L >= 0;
  backtest     a replay the retro itself ran (polylab.analysis.backtest, the sport only, current params,
               last 120 complete UTC days): n >= 40 and ROI >= 0. A missing, skipped or failed replay fails.
The AI never reaches this path: the validator accepts paper→live only from the retro's own "promotion"
source with this module's passing evidence. Demotion (live→paper) stays with the stake ladder.
Pure: no IO; the retro supplies trades, clocks and the replay summary.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Sequence

from polylab.risk.ladder import LOWER_Q, bootstrap_lower

MIN_PAPER_TRADES = 30
BACKTEST_LOOKBACK_DAYS = 120
BACKTEST_MIN_N = 40
COOLDOWN_S = 3 * 86400
PROMOTE_STAKE_USDC = 5.0
# Preseason / exhibition windows, (month, day) inclusive in UTC. Fixed calendar approximations of the leagues'
# schedules (NFL preseason early Aug - Labor Day, NBA preseason early-mid Oct, NHL mid Sep - early Oct, MLB spring
# training); soccer has none in scope (friendlies are outside the traded leagues). No promotion inside a window.
PRESEASON: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "nfl": ((8, 1), (9, 3)),
    "nba": ((10, 1), (10, 20)),
    "nhl": ((9, 15), (10, 6)),
    "mlb": ((2, 15), (3, 25)),
}


@dataclass(frozen=True)
class PaperTrade:
    opened_at: int
    closed_at: int
    pnl: float
    cost: float

    @property
    def roi(self) -> float:
        return self.pnl / self.cost if self.cost else 0.0


@dataclass
class Gate:
    variant_id: str
    sport: str
    ok: bool
    stage: str                      # eligibility | paper | backtest | passed
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_backtest(self) -> bool:
        """Eligibility and paper checks passed; only the retro-run replay is missing."""
        return self.stage == "backtest" and self.evidence.get("backtest") is None

    def as_dict(self) -> dict[str, Any]:
        return {"variant_id": self.variant_id, "sport": self.sport, "ok": self.ok, "stage": self.stage,
                "reason": self.reason, "evidence": self.evidence}


def _day(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()


def in_preseason(sport: str, ts: int) -> bool:
    window = PRESEASON.get(sport)
    if window is None:
        return False
    d = _day(ts)
    return window[0] <= (d.month, d.day) <= window[1]


def eligibility(v, sport: str, now: int, last_change_ts: int | None) -> str | None:
    """Refusal reason, or None when the (variant, sport) may be considered."""
    if not getattr(v, "per_sport", False):
        return "not a per-sport variant"
    if sport not in v.sports:
        return f"{sport} not covered"
    if v.mode == "off":
        return "variant is off"
    own = (v.sport_settings.get(sport) or {}).get("mode", v.mode)
    if own != "paper":
        return f"{sport} own mode is {own}, not paper"
    if not v.account:
        return "no account"
    start = (v.sport_settings.get(sport) or {}).get("live_from")
    if start and _day(now) < dt.date.fromisoformat(str(start)):
        return f"live_from {start} is in the future"
    if in_preseason(sport, now):
        (m0, d0), (m1, d1) = PRESEASON[sport]
        return f"{sport} preseason window {m0:02d}-{d0:02d}..{m1:02d}-{d1:02d}"
    if last_change_ts is not None and now - last_change_ts < COOLDOWN_S:
        return f"cooldown: last param/stake change {(now - last_change_ts) / 3600:.0f}h ago (< 72h)"
    if v.mode == "paper":
        others = [s for s in v.sports if s != sport and (v.sport_settings.get(s) or {}).get("mode") == "live"]
        if others:
            return f"master mode paper with {others} set live: promotion would take them live too"
    return None


def _half(trades: Sequence[PaperTrade]) -> dict[str, Any]:
    cost = sum(t.cost for t in trades)
    pnl = sum(t.pnl for t in trades)
    return {"n": len(trades), "pnl": round(pnl, 6), "roi": round(pnl / cost, 6) if cost else None}


def paper_stats(trades: Sequence[PaperTrade]) -> dict[str, Any]:
    ts = sorted(trades, key=lambda t: (t.opened_at, t.closed_at))
    k = len(ts) // 2
    out = _half(ts)
    out["roi_ci_lo"] = bootstrap_lower([t.roi for t in ts])
    out["ci"] = f"bootstrap mean ROI/trade, {int(LOWER_Q * 100)}th percentile (80% two-sided lower bound)"
    out["split_ts"] = ts[k].opened_at if len(ts) >= 2 else None
    out["halves"] = [_half(ts[:k]), _half(ts[k:])]
    return out


def evaluate(v, sport: str, trades: Sequence[PaperTrade], now: int, last_change_ts: int | None,
             backtest: dict | None = None) -> Gate:
    """`backtest`: {"n", "roi", "range": [start, end]} of the retro-run replay, {"error": ...}, or None (not run)."""
    vid = v.id
    why = eligibility(v, sport, now, last_change_ts)
    if why:
        return Gate(vid, sport, False, "eligibility", why, {"last_change_ts": last_change_ts})
    st = paper_stats(trades)
    ev: dict[str, Any] = {"paper": st, "last_change_ts": last_change_ts, "needed_paper_trades": MIN_PAPER_TRADES}
    if st["n"] < MIN_PAPER_TRADES:
        return Gate(vid, sport, False, "paper", f"{st['n']}/{MIN_PAPER_TRADES} settled paper trades at current params", ev)
    if st["roi_ci_lo"] is None or st["roi_ci_lo"] <= 0:
        return Gate(vid, sport, False, "paper", f"paper ROI 80% lower bound {st['roi_ci_lo']:+.4f} <= 0", ev)
    bad = [i + 1 for i, h in enumerate(st["halves"]) if h["pnl"] < 0]
    if bad:
        return Gate(vid, sport, False, "paper", f"paper half {bad} net negative", ev)
    ev["backtest"] = backtest
    if backtest is None:
        return Gate(vid, sport, False, "backtest", "paper gates passed; 120-day replay not run yet", ev)
    if backtest.get("error"):
        return Gate(vid, sport, False, "backtest", f"replay failed: {str(backtest['error'])[:200]}", ev)
    n, roi = int(backtest.get("n") or 0), backtest.get("roi")
    if n < BACKTEST_MIN_N:
        return Gate(vid, sport, False, "backtest", f"replay n={n} < {BACKTEST_MIN_N}", ev)
    if roi is None or roi < 0:
        return Gate(vid, sport, False, "backtest", f"replay ROI {roi} < 0", ev)
    return Gate(vid, sport, True, "passed",
                f"paper n={st['n']} ROI {st['roi']:+.2%} (80% lo {st['roi_ci_lo']:+.2%}), halves "
                f"{st['halves'][0]['pnl']:+.2f}/{st['halves'][1]['pnl']:+.2f} USDC; replay n={n} ROI {roi:+.2%}", ev)
