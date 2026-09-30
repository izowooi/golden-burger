"""Optional hooks into modules owned by other components (risk ladder, accounts, backtest).

Each hook returns None when the module is not importable or does not expose a known entry
point, so reports degrade to null fields instead of duplicating that logic here.
"""

from __future__ import annotations

import dataclasses
import importlib
from typing import Any, Callable

ACCOUNT_ENTRY_POINTS = (("polylab.execution.accounts", "snapshot"),)
BACKTEST_ENTRY_POINTS = (("polylab.analysis.backtest", "backtest"),)
ACCOUNT_FIELDS = ("alias", "variant_id", "cash_usdc", "positions_value_usdc", "equity_usdc", "redeemable_usdc",
                  "updated_at")


def _find(entry_points) -> Callable | None:
    for module, name in entry_points:
        try:
            mod = importlib.import_module(module)
        except Exception:  # ImportError or a broken sibling module must not break reports
            continue
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None


def _as_dict(obj: Any) -> dict | None:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return None


NULL_LADDER = {"status": None, "action": None, "trades_at_tier": None, "needed": None, "roi_ci_lo": None,
               "next_stake_usdc": None, "promote_ok": None, "demote": None, "reason": None, "evidence": None,
               "available": False}


def ladder_status(variant, paths, now: int | None = None) -> dict:
    """Deterministic ladder view from polylab.risk.ladder (evaluate_ledger + status_label).

    `promote_ok` is True only when the gate says promote now (cooldown included). Any error or a
    missing module yields nulls, which the validator treats as "no promotion" (fail closed).
    """
    try:
        from polylab.risk import ladder  # noqa: PLC0415
    except Exception:
        return dict(NULL_LADDER)
    db_path = paths.strategy_db(variant.id)
    if not db_path.exists():
        return dict(NULL_LADDER)
    import sqlite3  # noqa: PLC0415
    import time  # noqa: PLC0415
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        try:
            d = ladder.evaluate_ledger(conn, float(variant.stake_usdc), variant.mode, now or int(time.time()))
        finally:
            conn.close()
    except Exception as exc:
        return {**NULL_LADDER, "error": type(exc).__name__}
    ev = d.evidence or {}
    return {"status": ladder.status_label(d), "action": d.action, "trades_at_tier": ev.get("trades_at_tier"),
            "needed": ev.get("needed"), "roi_ci_lo": ev.get("tier_roi_ci_lo"),
            "next_stake_usdc": d.to_usdc if d.action in ("promote", "demote") else None,
            "promote_ok": d.action == "promote", "demote": d.action in ("demote", "paper"),
            "to_mode": d.to_mode, "reason": d.reason, "evidence": ev, "decision": d, "available": True}


def account_balances(variants) -> list[dict] | None:
    """Dashboard account rows via polylab.execution.accounts.snapshot (alias only, never addresses)."""
    fn = _find(ACCOUNT_ENTRY_POINTS)
    if fn is None:
        return None
    rows = []
    seen = set()
    for v in variants:
        if not v.account or v.account in seen or v.mode == "off":
            continue
        seen.add(v.account)
        try:
            raw = _as_dict(fn(v.account, v.id)) or {}
        except Exception:
            raw = {"alias": v.account, "variant_id": v.id}
        rows.append({k: raw.get(k) for k in ACCOUNT_FIELDS} | {"alias": v.account, "variant_id": v.id})
    return rows


def backtest_available() -> bool:
    return _find(BACKTEST_ENTRY_POINTS) is not None


def backtest(variant, params: dict, paths, since: int, until: int | None = None) -> dict | None:
    """Summary of polylab.analysis.backtest.backtest for `variant` with `params` (trades dropped)."""
    fn = _find(BACKTEST_ENTRY_POINTS)
    if fn is None:
        return None
    import copy  # noqa: PLC0415
    import time  # noqa: PLC0415
    from polylab.autopilot.validator import set_path  # noqa: PLC0415
    v = copy.copy(variant)
    v.params = copy.deepcopy(variant.params)
    for k, val in params.items():  # dotted keys address nested overrides
        set_path(v.params, k, val)
    try:
        res = fn(paths, v, since, until or int(time.time()))
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:300]}
    return {k: res.get(k) for k in ("range", "params", "summary", "errors", "books", "step_s")}
