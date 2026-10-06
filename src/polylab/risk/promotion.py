"""Deterministic paper→live promotion gate (owner decision 2026-10-06 `sports3:auto-promotion`) and the evidence
check for the AI's own live proposals (2026-10-06 `promotion:ai-direct`).

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
  backtest     a replay the retro itself ran (polylab.analysis.backtest, the sport only, current params, current
               fee schedule, the sport's replay window): n >= the sport's minimum and ROI >= 0. A missing, skipped
               or failed replay fails.
Per-sport sample rule (2026-10-06 `manual:nfl-promotion-window`, `sample_rule`): low-frequency sports (NFL by
config, and any sport whose trailing-365-day schedule averages < 60 games per 120 days) use paper >= 15, replay
n >= 20 (halves >= 5) over the previous full season (365 days); a sport whose trailing 120 days hold < 60 games
(off-season) keeps the normal minimums but replays 365 days, so the window still contains a season.
AI-direct path (`evaluate_direct`): the AI may propose paper→live for one (variant, sport); the retro then runs the
replay itself and the validator accepts only when the same eligibility holds, either this gate passed or the replay
passes the owner's per-sport rule (n >= minimum, ROI and both halves (split at the median entry) >= 0, each half
>= the half minimum), and the observed sample does not contradict it (paper trades since the sport's last stake/mode
event plus live trades on the current params — a sport the ladder demoted for live losses keeps that record; 80%
two-sided bootstrap upper bound of the mean ROI/trade >= 0; fewer than 5 trades never contradict). Numbers the AI writes are never used.
Demotion (live→paper) stays with the stake ladder. Pure: no IO except `count_games` (read-only core.db query).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Sequence

from polylab.risk.ladder import LOWER_Q, bootstrap_lower

MIN_PAPER_TRADES = 30
BACKTEST_LOOKBACK_DAYS = 120
BACKTEST_MIN_N = 40
BACKTEST_MIN_HALF_N = 10
# Low-frequency sports (owner 2026-10-06 `manual:nfl-promotion-window`): smaller minimums, previous full season.
LOW_FREQUENCY_SPORTS = frozenset({"nfl"})
LOW_FREQUENCY_GAMES_PER_120D = 60
LOW_FREQUENCY_LOOKBACK_DAYS = 365
LOW_FREQUENCY_MIN_PAPER = 15
LOW_FREQUENCY_BACKTEST_MIN_N = 20
LOW_FREQUENCY_BACKTEST_MIN_HALF_N = 5
DIRECT_PAPER_MIN_CONTRADICT = 5       # AI-direct: fewer paper trades than this never contradict a passing replay
UPPER_Q = 1 - LOWER_Q                 # 80% two-sided upper bound
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
class SampleRule:
    sport: str
    lookback_days: int = BACKTEST_LOOKBACK_DAYS
    backtest_min_n: int = BACKTEST_MIN_N
    backtest_min_half_n: int = BACKTEST_MIN_HALF_N
    paper_min_trades: int = MIN_PAPER_TRADES
    params_min_trades: int = 20         # validator min_trades_params for plain params changes of this sport
    low_frequency: bool = False
    reason: str = "default"

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def sample_rule(sport: str | None, games_120d: int | None = None, games_365d: int | None = None) -> SampleRule:
    """Per-sport sample sizes and replay window. `games_*`: games of the sport in the trailing windows
    (preseason excluded, variant league scope), None when unknown (then only the static config applies)."""
    if not sport:
        return SampleRule("all")
    per_120 = None if games_365d is None else games_365d * 120 / 365
    if sport in LOW_FREQUENCY_SPORTS or (per_120 is not None and 0 < per_120 < LOW_FREQUENCY_GAMES_PER_120D):
        why = "config" if sport in LOW_FREQUENCY_SPORTS else f"{per_120:.0f} games/120d over the last 365 days"
        return SampleRule(sport, LOW_FREQUENCY_LOOKBACK_DAYS, LOW_FREQUENCY_BACKTEST_MIN_N,
                          LOW_FREQUENCY_BACKTEST_MIN_HALF_N, LOW_FREQUENCY_MIN_PAPER, LOW_FREQUENCY_MIN_PAPER,
                          True, f"low-frequency ({why})")
    if games_120d is not None and games_120d < LOW_FREQUENCY_GAMES_PER_120D and (games_365d or 0) > games_120d:
        return SampleRule(sport, LOW_FREQUENCY_LOOKBACK_DAYS,
                          reason=f"off-season: {games_120d} games in the last 120 days -> 365-day window")
    return SampleRule(sport)


def count_games(core, sport: str, start: int, end: int, leagues: Sequence[str] | None = None) -> int:
    """Games of `sport` that started in [start, end), preseason windows excluded (read-only core.db)."""
    sql = "SELECT start_time FROM games WHERE sport=? AND start_time >= ? AND start_time < ?"
    args: list = [sport, start, end]
    if leagues and sport == "soccer":
        sql += f" AND LOWER(league) IN ({','.join('?' * len(leagues))})"
        args += [str(x).lower() for x in leagues]
    return sum(1 for (ts,) in core.execute(sql, args) if ts is not None and not in_preseason(sport, int(ts)))


def rule_for(core, sport: str, end: int, leagues: Sequence[str] | None = None) -> SampleRule:
    if core is None:
        return sample_rule(sport)
    return sample_rule(sport, count_games(core, sport, end - 120 * 86400, end, leagues),
                       count_games(core, sport, end - 365 * 86400, end, leagues))


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
    out["roi_ci_hi"] = bootstrap_lower([t.roi for t in ts], q=UPPER_Q)
    out["ci"] = f"bootstrap mean ROI/trade, {int(LOWER_Q * 100)}th percentile (80% two-sided lower bound)"
    out["split_ts"] = ts[k].opened_at if len(ts) >= 2 else None
    out["halves"] = [_half(ts[:k]), _half(ts[k:])]
    return out


def evaluate(v, sport: str, trades: Sequence[PaperTrade], now: int, last_change_ts: int | None,
             backtest: dict | None = None, rule: SampleRule | None = None) -> Gate:
    """`backtest`: {"n", "roi", "range": [start, end]} of the retro-run replay, {"error": ...}, or None (not run)."""
    vid = v.id
    rule = rule or sample_rule(sport)
    why = eligibility(v, sport, now, last_change_ts)
    if why:
        return Gate(vid, sport, False, "eligibility", why, {"last_change_ts": last_change_ts, "rule": rule.as_dict()})
    st = paper_stats(trades)
    need = rule.paper_min_trades
    ev: dict[str, Any] = {"paper": st, "last_change_ts": last_change_ts, "needed_paper_trades": need,
                          "rule": rule.as_dict()}
    if st["n"] < need:
        return Gate(vid, sport, False, "paper", f"{st['n']}/{need} settled paper trades at current params", ev)
    if st["roi_ci_lo"] is None or st["roi_ci_lo"] <= 0:
        return Gate(vid, sport, False, "paper", f"paper ROI 80% lower bound {st['roi_ci_lo']:+.4f} <= 0", ev)
    bad = [i + 1 for i, h in enumerate(st["halves"]) if h["pnl"] < 0]
    if bad:
        return Gate(vid, sport, False, "paper", f"paper half {bad} net negative", ev)
    ev["backtest"] = backtest
    if backtest is None:
        return Gate(vid, sport, False, "backtest", f"paper gates passed; {rule.lookback_days}-day replay not run yet", ev)
    if backtest.get("error"):
        return Gate(vid, sport, False, "backtest", f"replay failed: {str(backtest['error'])[:200]}", ev)
    n, roi = int(backtest.get("n") or 0), backtest.get("roi")
    if n < rule.backtest_min_n:
        return Gate(vid, sport, False, "backtest", f"replay n={n} < {rule.backtest_min_n}", ev)
    if roi is None or roi < 0:
        return Gate(vid, sport, False, "backtest", f"replay ROI {roi} < 0", ev)
    return Gate(vid, sport, True, "passed",
                f"paper n={st['n']} ROI {st['roi']:+.2%} (80% lo {st['roi_ci_lo']:+.2%}), halves "
                f"{st['halves'][0]['pnl']:+.2f}/{st['halves'][1]['pnl']:+.2f} USDC; replay n={n} ROI {roi:+.2%}", ev)


def evaluate_direct(v, sport: str, trades: Sequence[PaperTrade], now: int, last_change_ts: int | None,
                    backtest: dict | None, rule: SampleRule | None = None) -> Gate:
    """AI-proposed paper→live (owner 2026-10-06 `promotion:ai-direct`): objective checks on the retro's own replay.
    `backtest`: {"n", "roi", "halves": [{"n", "roi"}, {"n", "roi"}], "range"} or {"error"} or None (not run)."""
    vid = v.id
    rule = rule or sample_rule(sport)
    why = eligibility(v, sport, now, last_change_ts)
    if why:
        return Gate(vid, sport, False, "eligibility", why, {"last_change_ts": last_change_ts, "rule": rule.as_dict(),
                                                           "path": "ai-direct"})
    st = paper_stats(trades)
    ev: dict[str, Any] = {"path": "ai-direct", "paper": st, "last_change_ts": last_change_ts, "rule": rule.as_dict(),
                          "backtest": backtest}
    if st["n"] >= DIRECT_PAPER_MIN_CONTRADICT and st["roi_ci_hi"] is not None and st["roi_ci_hi"] < 0:
        return Gate(vid, sport, False, "paper",
                    f"paper sample contradicts: n={st['n']} ROI {st['roi']:+.2%}, 80% upper bound {st['roi_ci_hi']:+.2%} < 0", ev)
    if backtest is None:
        return Gate(vid, sport, False, "backtest", f"{rule.lookback_days}-day replay not run", ev)
    if backtest.get("error"):
        return Gate(vid, sport, False, "backtest", f"replay failed: {str(backtest['error'])[:200]}", ev)
    n, roi, halves = int(backtest.get("n") or 0), backtest.get("roi"), backtest.get("halves") or []
    if n < rule.backtest_min_n:
        return Gate(vid, sport, False, "backtest", f"replay n={n} < {rule.backtest_min_n}", ev)
    if roi is None or roi < 0:
        return Gate(vid, sport, False, "backtest", f"replay ROI {roi} < 0", ev)
    if len(halves) != 2:
        return Gate(vid, sport, False, "backtest", "replay evidence must have two halves", ev)
    for i, h in enumerate(halves, 1):
        if int(h.get("n") or 0) < rule.backtest_min_half_n or h.get("roi") is None or h["roi"] < 0:
            return Gate(vid, sport, False, "backtest",
                        f"replay H{i} n={h.get('n') or 0} ROI {h.get('roi')} (need n >= {rule.backtest_min_half_n}, ROI >= 0)", ev)
    paper = f"paper n={st['n']}" + (f" ROI {st['roi']:+.2%}" if st["roi"] is not None else "")
    return Gate(vid, sport, True, "passed",
                f"{paper}; replay {rule.lookback_days}d n={n} ROI {roi:+.2%}, H1 {halves[0]['roi']:+.2%} / "
                f"H2 {halves[1]['roi']:+.2%} ({rule.reason})", ev)
