from __future__ import annotations

import pytest

from polylab.risk.caps import CapState, check_entry, kill_switch_active
from polylab.risk.ladder import COOLDOWN_S, SettledTrade, bootstrap_lower, evaluate, max_drawdown, status_label

NOW = 1_800_000_000


def trades(pnls, stake=5.0, start=NOW - 10 * 86400):
    return [SettledTrade(start + i * 600, p, stake, stake) for i, p in enumerate(pnls)]


@pytest.mark.usefixtures("unfrozen_ladder")
def test_promote_needs_20_trades_positive_ci_and_small_dd():
    assert evaluate(5, "live", trades([0.3] * 19), NOW, None).action == "hold"
    d = evaluate(5, "live", trades([0.3] * 20), NOW, None)
    assert d.action == "promote" and d.to_usdc == 10 and d.evidence["tier_roi_ci_lo"] > 0
    noisy = [1.0, -0.95] * 10                                  # positive sum but CI lower bound < 0
    assert evaluate(5, "live", trades(noisy), NOW, None).action == "hold"


@pytest.mark.usefixtures("unfrozen_ladder")
def test_drawdown_gate_blocks_promotion():
    pnls = [2.0] * 30 + [-31.0] + [2.0] * 10                  # dd 31 >= 5*6
    d = evaluate(5, "live", trades(pnls), NOW, None)
    assert d.action == "hold" and d.evidence["tier_max_drawdown"] >= 30


@pytest.mark.usefixtures("unfrozen_ladder")
def test_cooldown_and_tier_counting():
    last = NOW - COOLDOWN_S + 60
    assert evaluate(5, "live", trades([0.3] * 30), NOW, last).reason == "cooldown"
    # trades at a previous tier do not count for the new tier
    d = evaluate(10, "live", trades([0.3] * 30, stake=5.0), NOW, NOW - 4 * 86400)
    assert d.action == "hold" and d.evidence["trades_at_tier"] == 0


def test_demote_and_paper():
    d = evaluate(25, "live", trades([-0.5] * 20, stake=25.0), NOW, None)
    assert d.action == "demote" and d.to_usdc == 10
    p = evaluate(5, "live", trades([-0.1] * 40), NOW, None)
    assert p.action == "paper" and p.to_mode == "paper"
    assert evaluate(5, "live", trades([-0.1] * 39), NOW, None).action == "hold"   # floor: no further demotion
    assert status_label(evaluate(5, "live", trades([-0.1] * 39), NOW, None)) == "demote_warning"


def test_cap_at_100_and_determinism():
    assert evaluate(100, "live", trades([1.0] * 25, stake=100.0), NOW, None).action == "hold"
    assert bootstrap_lower([0.1, -0.2, 0.3]) == bootstrap_lower([0.1, -0.2, 0.3])
    assert max_drawdown([1, -2, 1, -3]) == 4


def test_caps_and_kill_switch(tmp_path, monkeypatch):
    limits = {"max_positions": 2, "max_open_usdc": 10, "daily_loss_stop_usdc": 5, "max_new_per_cycle": 1}
    assert check_entry(limits, CapState(0, 0, 0), 5).ok
    assert check_entry(limits, CapState(2, 0, 0), 5).reason == "max_positions"
    assert check_entry(limits, CapState(1, 6, 0), 5).reason == "max_open_usdc"
    assert check_entry(limits, CapState(0, 0, -5), 5).reason == "daily_loss_stop"
    assert check_entry(limits, CapState(0, 0, 0, 1), 5).reason == "max_new_per_cycle"
    assert not check_entry({}, CapState(0, 0, 0), 150).ok     # per-order <= 100
    monkeypatch.delenv("POLYLAB_KILL", raising=False)
    assert not kill_switch_active(tmp_path)
    (tmp_path / "KILL").touch()
    assert kill_switch_active(tmp_path)


def test_stake_freeze_blocks_promotion_but_keeps_demotion():
    """Owner 2026-10-06 `stake:freeze-5`: no tier above 5 USDC, demotion and floor->paper unchanged."""
    d = evaluate(5, "live", trades([0.3] * 30), NOW, None)
    assert d.action == "hold" and "frozen" in d.reason and d.evidence["stake_freeze_usdc"] == 5.0
    assert status_label(d) == "hold"
    cooling = evaluate(5, "live", trades([0.3] * 30), NOW, NOW - COOLDOWN_S + 60)
    assert cooling.reason == "cooldown" and status_label(cooling) == "hold"   # no promote_ready while frozen
    assert evaluate(25, "live", trades([-0.5] * 20, stake=25.0), NOW, None).action == "demote"
    assert evaluate(5, "live", trades([-0.1] * 40), NOW, None).action == "paper"
