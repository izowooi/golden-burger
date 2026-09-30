"""Deterministic stake ladder (docs/ARCHITECTURE.md §7).

Promote one step (5→10→25→50→100) when, at the current tier since the last change:
  settled trades >= 20, net P&L > 0, bootstrap 80% lower bound of per-trade ROI > 0,
  and max drawdown < tier × 6.
Demote one step when the last 20 settled trades have net P&L < 0 and their ROI lower
bound < 0. At the floor (5) with >= 40 trades and cumulative loss at that tier -> paper.
Any change needs >= 3 days since the last stake event. Pure: returns decision + evidence.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any, Sequence

LADDER = (5.0, 10.0, 25.0, 50.0, 100.0)
MIN_TRADES_PROMOTE = 20
RECENT_WINDOW = 20
PAPER_DEMOTE_TRADES = 40
DD_MULTIPLE = 6.0
COOLDOWN_S = 3 * 86400
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20261001
LOWER_Q = 0.10          # "80% lower bound" = lower end of the two-sided 80% bootstrap interval


@dataclass(frozen=True)
class SettledTrade:
    closed_at: int
    pnl: float
    cost: float
    stake_usdc: float

    @property
    def roi(self) -> float:
        return self.pnl / self.cost if self.cost else 0.0


@dataclass
class LadderDecision:
    action: str                    # hold | promote | demote | paper
    from_usdc: float
    to_usdc: float
    from_mode: str
    to_mode: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.action != "hold"


def bootstrap_lower(rois: Sequence[float], q: float = LOWER_Q, n: int = BOOTSTRAP_N,
                    seed: int = BOOTSTRAP_SEED) -> float | None:
    if not rois:
        return None
    rng = random.Random(seed)
    k = len(rois)
    means = sorted(sum(rois[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return means[int(q * (n - 1))]


def max_drawdown(pnls: Sequence[float]) -> float:
    peak = cum = dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def _step(stake: float, direction: int) -> float:
    i = min(range(len(LADDER)), key=lambda j: abs(LADDER[j] - stake))
    return LADDER[max(0, min(len(LADDER) - 1, i + direction))]


def evaluate(stake_usdc: float, mode: str, trades: Sequence[SettledTrade], now: int,
             last_change_at: int | None) -> LadderDecision:
    trades = sorted(trades, key=lambda t: t.closed_at)
    since = last_change_at or 0
    tier = [t for t in trades if t.stake_usdc == stake_usdc and t.closed_at >= since]
    tier_pnls = [t.pnl for t in tier]
    recent = trades[-RECENT_WINDOW:]
    ev: dict[str, Any] = {
        "tier_usdc": stake_usdc, "trades_at_tier": len(tier), "needed": MIN_TRADES_PROMOTE,
        "tier_pnl": round(sum(tier_pnls), 6),
        "tier_roi_ci_lo": bootstrap_lower([t.roi for t in tier]),
        "tier_max_drawdown": round(max_drawdown(tier_pnls), 6),
        "dd_limit": stake_usdc * DD_MULTIPLE,
        "recent_n": len(recent), "recent_pnl": round(sum(t.pnl for t in recent), 6),
        "recent_roi_ci_lo": bootstrap_lower([t.roi for t in recent]),
        "ci": f"bootstrap mean ROI/trade, {int(LOWER_Q * 100)}th percentile, n={BOOTSTRAP_N}, seed={BOOTSTRAP_SEED}",
        "last_change_at": last_change_at,
    }
    hold = LadderDecision("hold", stake_usdc, stake_usdc, mode, mode, "no gate met", ev)
    if mode != "live":
        hold.reason = "not live"
        return hold
    if last_change_at is not None and now - last_change_at < COOLDOWN_S:
        hold.reason = "cooldown"
        ev["cooldown_until"] = last_change_at + COOLDOWN_S
        return hold

    if stake_usdc == LADDER[0] and len(tier) >= PAPER_DEMOTE_TRADES and sum(tier_pnls) < 0:
        return LadderDecision("paper", stake_usdc, stake_usdc, mode, "paper",
                              f"{len(tier)} trades at floor with cumulative loss", ev)
    if len(recent) >= RECENT_WINDOW and ev["recent_pnl"] < 0 and (ev["recent_roi_ci_lo"] or 0) < 0 \
            and stake_usdc > LADDER[0]:
        return LadderDecision("demote", stake_usdc, _step(stake_usdc, -1), mode, mode,
                              "last 20 net negative and ROI lower bound < 0", ev)
    if (len(tier) >= MIN_TRADES_PROMOTE and ev["tier_pnl"] > 0 and (ev["tier_roi_ci_lo"] or 0) > 0
            and ev["tier_max_drawdown"] < ev["dd_limit"] and stake_usdc < LADDER[-1]):
        return LadderDecision("promote", stake_usdc, _step(stake_usdc, +1), mode, mode,
                              "tier gates passed", ev)
    return hold


def status_label(d: LadderDecision) -> str:
    """Dashboard `ladder.status`: hold | promote_ready | demote_warning."""
    if d.action == "promote" or (d.reason == "cooldown" and d.evidence.get("trades_at_tier", 0) >= MIN_TRADES_PROMOTE
                                 and (d.evidence.get("tier_roi_ci_lo") or 0) > 0):
        return "promote_ready"
    if d.action in ("demote", "paper") or (d.evidence.get("recent_pnl", 0) < 0 and d.evidence.get("recent_n", 0) >= 10):
        return "demote_warning"
    return "hold"


def evaluate_ledger(conn, stake_usdc: float, mode: str, now: int) -> LadderDecision:
    """Convenience: read settled trades + last stake event from a strategy DB connection."""
    rows = conn.execute(
        "SELECT closed_at, realized_pnl, cost_usdc, stake_usdc FROM positions WHERE mode=? "
        "AND status IN ('closed','resolved') AND realized_pnl IS NOT NULL", (mode,)).fetchall()
    trades = [SettledTrade(int(r[0]), float(r[1]), float(r[2] or 0), float(r[3])) for r in rows]
    last = conn.execute("SELECT MAX(ts) FROM stake_events").fetchone()[0]
    return evaluate(stake_usdc, mode, trades, now, last)


def record_decision(conn, d: LadderDecision, now: int) -> None:
    if not d.changed:
        return
    conn.execute("INSERT INTO stake_events(ts, from_usdc, to_usdc, from_mode, to_mode, reason, evidence) "
                 "VALUES(?,?,?,?,?,?,?)", (now, d.from_usdc, d.to_usdc, d.from_mode, d.to_mode,
                                           f"{d.action}: {d.reason}", json.dumps(d.evidence, default=str)))
    conn.commit()
