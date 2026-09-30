"""Entry caps and the global kill switch. Exits and reconciliation are never blocked here."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MAX_ORDER_USDC = 100.0
KILL_FILE_NAME = "KILL"


def kill_switch_active(state_dir: Path | None) -> bool:
    if os.environ.get("POLYLAB_KILL", "").strip() in ("1", "true", "yes"):
        return True
    return bool(state_dir) and (Path(state_dir) / KILL_FILE_NAME).exists()


def utc_day_start(now: int) -> int:
    return now - now % 86400


@dataclass
class CapState:
    open_positions: int
    open_usdc: float
    realized_today: float
    new_this_cycle: int = 0


@dataclass(frozen=True)
class CapCheck:
    ok: bool
    reason: str


def check_entry(limits: dict, state: CapState, stake_usdc: float) -> CapCheck:
    """All caps for one more entry of `stake_usdc`."""
    if stake_usdc <= 0 or stake_usdc > MAX_ORDER_USDC:
        return CapCheck(False, f"stake {stake_usdc} outside (0, {MAX_ORDER_USDC}]")
    max_pos = limits.get("max_positions")
    if max_pos is not None and state.open_positions >= int(max_pos):
        return CapCheck(False, "max_positions")
    max_open = limits.get("max_open_usdc")
    if max_open is not None and state.open_usdc + stake_usdc > float(max_open) + 1e-9:
        return CapCheck(False, "max_open_usdc")
    stop = limits.get("daily_loss_stop_usdc")
    if stop is not None and state.realized_today <= -abs(float(stop)):
        return CapCheck(False, "daily_loss_stop")
    per_cycle = limits.get("max_new_per_cycle")
    if per_cycle is not None and state.new_this_cycle >= int(per_cycle):
        return CapCheck(False, "max_new_per_cycle")
    return CapCheck(True, "ok")


def clamp_stake(stake_usdc: float) -> float:
    return min(float(stake_usdc), MAX_ORDER_USDC)

