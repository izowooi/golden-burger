"""Account balances / positions / redeemable status.

Public Data API v2 (v1 retires 2026-10-24): /v2/positions?user=&status=, /v2/value?user=.
Cash (pUSD collateral) comes from the authenticated CLOB balance endpoint when credentials
work. Wallet numbers are account evidence, never strategy P&L. The funder address is used
only inside requests and never printed or stored.

CLI: `polylab accounts [--json]`
     `polylab accounts redeem [--alias A] [--execute]`   (dry-run by default; see redeem.py)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Callable

import requests

from polylab import registry, settings

DATA_API = "https://data-api.polymarket.com"
TIMEOUT = (5, 20)


def _get(session, path: str, params: dict) -> Any:
    r = session.get(DATA_API + path, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_positions(user: str, status: str | None = None, session=None, max_pages: int = 50) -> list[dict]:
    session = session or requests.Session()
    out, cursor = [], None
    for _ in range(max_pages):
        params = {"user": user, "limit": 500}
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        body = _get(session, "/v2/positions", params)
        out.extend(body.get("data") or [])
        cursor = (body.get("pagination") or {}).get("next_cursor")
        if not cursor:
            break
    return out


def fetch_value(user: str, session=None) -> float | None:
    body = _get(session or requests.Session(), "/v2/value", {"user": user})
    data = body.get("data") if isinstance(body, dict) else None
    return float(data["value"]) if data and data.get("value") is not None else None


def fetch_cash(creds, clob_factory: Callable | None = None) -> float | None:
    """Collateral balance via the authenticated CLOB (micro units -> USDC)."""
    from py_clob_client_v2 import AssetType, BalanceAllowanceParams

    from polylab.execution.clob import build_client
    client = (clob_factory or build_client)(creds)
    resp = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL,
                                                               signature_type=creds.signature_type))
    bal = (resp or {}).get("balance")
    return int(bal) / 1e6 if bal is not None else None


def summarize_positions(rows: list[dict]) -> dict[str, Any]:
    def val(r):
        return float(r.get("current_value") or 0)
    by = {}
    for r in rows:
        by.setdefault(r.get("status") or "UNKNOWN", []).append(r)
    redeemable = by.get("REDEEMABLE", [])
    return {
        "open_positions": len(by.get("OPEN", [])),
        "positions_value_usdc": round(sum(val(r) for r in by.get("OPEN", [])), 6),
        "redeemable_count": len(redeemable),
        "redeemable_usdc": round(sum(val(r) for r in redeemable), 6),
        "redeemable_lost_count": len(by.get("REDEEMABLE_LOST", [])),
        "redeemable_conditions": sorted({r["condition_id"] for r in redeemable if r.get("condition_id")}),
    }


def snapshot(alias: str, variant_id: str | None = None, session=None, clob_factory=None) -> dict[str, Any]:
    """Dashboard `portfolio.accounts[]` row (+ redeem details). Unknown values are None."""
    from polylab.engine.tick import sanitize
    row: dict[str, Any] = {"alias": alias, "variant_id": variant_id, "cash_usdc": None,
                           "positions_value_usdc": None, "equity_usdc": None, "redeemable_usdc": None,
                           "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "errors": []}
    try:
        creds = settings.account_credentials(alias)
    except KeyError:
        row["errors"].append("credentials missing")
        return row
    session = session or requests.Session()
    try:
        rows = fetch_positions(creds.funder_address, session=session)
        row.update(summarize_positions(rows))
    except Exception as e:
        row["errors"].append(sanitize(f"positions: {type(e).__name__}: {e}"))
    try:
        value = fetch_value(creds.funder_address, session=session)
        if value is not None:
            row["positions_value_usdc"] = value
    except Exception as e:
        row["errors"].append(sanitize(f"value: {type(e).__name__}: {e}"))
    try:
        row["cash_usdc"] = fetch_cash(creds, clob_factory)
    except Exception as e:
        row["errors"].append(sanitize(f"cash: {type(e).__name__}: {e}"))
    if row["cash_usdc"] is not None and row["positions_value_usdc"] is not None:
        row["equity_usdc"] = round(row["cash_usdc"] + row["positions_value_usdc"], 6)
    return row


def alias_variants() -> dict[str, str]:
    """account alias -> the variant that trades it. Several variants may share an account (one live at most, the
    rest paper): the live one names the account, else the last by id (the registry's file order, as before)."""
    try:
        variants = registry.load_all(include_off=True)
    except Exception:
        return {}
    out: dict[str, str] = {}
    owned = [v for v in variants if v.account]
    # later writes win: the other variants in id order, then the live ones
    for v in sorted((v for v in owned if v.mode != "live"), key=lambda v: v.id) + \
            [v for v in owned if v.mode == "live"]:
        out[v.account] = v.id
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    if argv and argv[0] == "redeem":
        from polylab.execution.redeem import main as redeem_main
        return redeem_main(argv[1:])
    ap = argparse.ArgumentParser(prog="polylab accounts")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    mapping = alias_variants()
    rows = [snapshot(a, mapping.get(a)) for a in settings.account_aliases()]
    if args.json:
        print(json.dumps(rows, indent=1, default=str))
        return 0
    print(f"{'alias':10s} {'variant':16s} {'cash':>10s} {'positions':>10s} {'equity':>10s} {'redeem':>8s} errors")
    for r in rows:
        f = lambda v: "-" if v is None else f"{v:.2f}"
        print(f"{r['alias']:10s} {str(r['variant_id'] or '-'):16s} {f(r['cash_usdc']):>10s} "
              f"{f(r['positions_value_usdc']):>10s} {f(r['equity_usdc']):>10s} {f(r['redeemable_usdc']):>8s} "
              f"{'; '.join(r['errors'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
