"""Redeem resolved winning positions (gasless, via the official `polymarket-client` SDK).

Facts (docs/research/api-sources.md "Redemption"):
- Winners = Data API v2 `/v2/positions?status=REDEEMABLE` (v1 `redeemable=true` includes losers).
- `SecureClient.redeem_positions(condition_id=...)` sends CTF redeemPositions through the
  relayer; proxy (sig 1) and deposit (sig 3) wallets need a Builder or Relayer API key.
- `SecureClient.create()` WITHOUT `wallet=` deploys a new deposit wallet. We always pass the
  existing funder and refuse if the SDK does not classify it as the expected wallet type.
Builder creds are cached in ~/.polylab/builder/<alias>.json (0600), never in the repo.

Dry-run (default for the CLI) lists what would be redeemed. `polylab tick` runs the execute
path at most hourly when POLYLAB_AUTO_REDEEM is not "0" (default on).
Ledger: strategy positions with settlement=resolution and redeemed=0 flip to redeemed=1 after
a confirmed relayer transaction; each attempt is a `redemptions` row.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from polylab import registry, settings
from polylab.execution.accounts import fetch_positions

EXPECTED_WALLET = {1: "POLY_PROXY", 2: "GNOSIS_SAFE", 3: "DEPOSIT_WALLET"}
REDEEM_INTERVAL_S = 3600


def auto_redeem_enabled() -> bool:
    return os.environ.get("POLYLAB_AUTO_REDEEM", "1").strip() not in ("0", "false", "no")


def builder_key_path(alias: str) -> Path:
    return settings.SECRETS_DIR / "builder" / f"{alias}.json"


def load_builder_key(alias: str):
    path = builder_key_path(alias)
    if not path.exists():
        return None
    if path.stat().st_mode & 0o077:
        raise PermissionError(f"{path} must be chmod 600")
    from polymarket import BuilderApiKey
    d = json.loads(path.read_text())
    return BuilderApiKey(key=d["key"], secret=d["secret"], passphrase=d["passphrase"])


def save_builder_key(alias: str, key) -> None:
    path = builder_key_path(alias)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"key": key.key, "secret": key.secret, "passphrase": key.passphrase}, f)


def check_wallet_type(creds) -> str:
    """The funder must derive from the signer as the wallet type its signature type implies."""
    from eth_account import Account
    from polymarket._internal.environment import get_environment_config
    from polymarket._internal.wallet import try_classify_wallet_type
    from polymarket.environments import PRODUCTION

    signer = Account.from_key(creds.private_key).address
    cfg = get_environment_config(PRODUCTION)
    wtype = try_classify_wallet_type(signer=signer, wallet=creds.funder_address, config=cfg.wallet_derivation)
    expected = EXPECTED_WALLET.get(creds.signature_type)
    if wtype != expected:
        raise RuntimeError(f"wallet classification {wtype!r} != expected {expected!r}; refusing to redeem")
    return wtype


def secure_client(creds, alias: str):
    """SecureClient for the EXISTING funder wallet, with a cached/created builder key."""
    from polymarket import SecureClient
    check_wallet_type(creds)
    key = load_builder_key(alias)
    if key is None:
        bootstrap = SecureClient.create(private_key=creds.private_key, wallet=creds.funder_address)
        key = bootstrap.create_builder_api_key()
        save_builder_key(alias, key)
    return SecureClient.create(private_key=creds.private_key, wallet=creds.funder_address, api_key=key)


def redeemable_conditions(rows: list[dict]) -> list[str]:
    return sorted({r["condition_id"] for r in rows
                   if r.get("status") == "REDEEMABLE" and r.get("redeemable") and r.get("condition_id")
                   and float(r.get("current_size") or 0) > 0})


def redeem_account(alias: str, *, execute: bool, now: int | None = None,
                   positions_fetcher: Callable[[str], list[dict]] | None = None,
                   client_factory: Callable[[Any, str], Any] | None = None,
                   paths=None, variant_id: str | None = None) -> dict[str, Any]:
    from polylab.engine.tick import sanitize
    now = int(now or time.time())
    out: dict[str, Any] = {"alias": alias, "execute": execute, "conditions": [], "done": [], "failed": {}}
    creds = settings.account_credentials(alias)
    rows = (positions_fetcher or (lambda u: fetch_positions(u, status="REDEEMABLE")))(creds.funder_address)
    conds = redeemable_conditions(rows)
    out["conditions"] = conds
    ledger = None
    if paths is not None and variant_id:
        from polylab.execution.ledger import open_ledger
        ledger = open_ledger(paths, variant_id)
    try:
        if not conds or not execute:
            for c in conds:
                if ledger:
                    ledger.conn.execute("INSERT INTO redemptions(ts, condition_id, status, detail) VALUES(?,?,?,?)",
                                        (now, c, "dry_run" if not execute else "redeemable", None))
            return out
        client = (client_factory or secure_client)(creds, alias)
        for c in conds:
            try:
                outcome = client.redeem_positions(condition_id=c).wait()
                tx = getattr(outcome, "transaction_hash", None)
                out["done"].append(c)
                if ledger:
                    ledger.conn.execute("INSERT INTO redemptions(ts, condition_id, status, detail) VALUES(?,?,?,?)",
                                        (now, c, "done", json.dumps({"tx": str(tx) if tx else None})))
                    ledger.conn.execute("UPDATE positions SET redeemed=1 WHERE mode='live' AND condition_id=? "
                                        "AND settlement='resolution'", (c,))
            except Exception as e:
                msg = sanitize(f"{type(e).__name__}: {e}", 200)
                out["failed"][c] = msg
                if ledger:
                    ledger.conn.execute("INSERT INTO redemptions(ts, condition_id, status, detail) VALUES(?,?,?,?)",
                                        (now, c, "failed", msg))
        return out
    finally:
        if ledger:
            ledger.conn.commit()
            ledger.conn.close()


def run_due(paths, now: int | None = None, force: bool = False) -> dict[str, Any]:
    """Hourly auto-redeem for every live variant's account (called by tick)."""
    from polylab.engine.tick import sanitize
    now = int(now or time.time())
    stamp = paths.state / "redeem.last"
    if not force and stamp.exists() and now - int(stamp.read_text() or 0) < REDEEM_INTERVAL_S:
        return {"skipped": "not_due"}
    stamp.write_text(str(now))
    results = {}
    for v in registry.load_all():
        if not v.is_live or not v.account:
            continue
        try:
            results[v.account] = redeem_account(v.account, execute=True, now=now, paths=paths, variant_id=v.id)
        except Exception as e:
            results[v.account] = {"error": sanitize(f"{type(e).__name__}: {e}", 200)}
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab accounts redeem")
    ap.add_argument("--alias", action="append", help="account alias (repeatable); default all live variants")
    ap.add_argument("--execute", action="store_true", help="actually redeem (default: dry-run listing)")
    args = ap.parse_args(argv)
    variants = {v.account: v.id for v in registry.load_all(include_off=True) if v.account}
    aliases = args.alias or sorted(variants)
    paths = settings.paths()
    results = []
    for a in aliases:
        try:
            results.append(redeem_account(a, execute=args.execute, paths=paths, variant_id=variants.get(a)))
        except Exception as e:
            from polylab.engine.tick import sanitize
            results.append({"alias": a, "error": sanitize(f"{type(e).__name__}: {e}", 200)})
    print(json.dumps(results, indent=1, default=str))
    return 0 if all("error" not in r and not r.get("failed") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
