"""`polylab publish [--dry-run] [--out DIR] [--prefix P] [--no-reports] [--no-jenkins]`.

Builds the dashboard read model (docs/contracts/dashboard-json.md) and uploads it to the
private Supabase Storage bucket `polylab`. Reports (markdown + index.json) under the repo's
reports/ are uploaded when their content changed since the last publish.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import requests

from polylab import settings
from polylab.analysis import _common as C
from polylab.analysis import integrations
from polylab.analysis.cli import load_cached
from polylab.reports import build as report_build

BUCKET = "polylab"
REPORTS_DIR = settings.REPO_ROOT / "reports"
DATASET_STATS_TTL = 3600


class Storage:
    def __init__(self, url: str, key: str, bucket: str = BUCKET, timeout: float = 30.0):
        self.base = url.rstrip("/") + f"/storage/v1/object/{bucket}/"
        self._headers = {"apikey": key, "Authorization": f"Bearer {key}", "x-upsert": "true"}
        self.timeout = timeout
        from polylab.autopilot.gitops import secret_values
        self._secrets = [v.encode() for v in secret_values()]

    def upload(self, path: str, body: bytes, content_type: str = "application/json") -> None:
        lowered = body.lower()
        if any(v in lowered for v in self._secrets):  # the dashboard is public; never ship keys/wallets
            raise RuntimeError(f"storage upload {path} refused: account secret value in payload")
        r = requests.post(self.base + path.lstrip("/"), data=body, timeout=self.timeout,
                          headers={**self._headers, "Content-Type": content_type})
        if not r.ok:  # status only: response bodies can echo request details
            raise RuntimeError(f"storage upload {path} failed: http {r.status_code}")

    def __repr__(self) -> str:
        return "Storage(***)"


def storage_from_env(env: dict | None = None) -> Storage | None:
    env = env if env is not None else settings.service_env()
    if not env.get("SUPABASE_URL") or not env.get("SUPABASE_SECRET_KEY"):
        return None
    return Storage(env["SUPABASE_URL"], env["SUPABASE_SECRET_KEY"])


# ------------------------------------------------------------------ shaping

def _ai_status(paths, now: int) -> dict:
    retro_dir = Path(paths.state) / "retro"
    last = {}
    try:
        last = json.loads((retro_dir / "last.json").read_text())
    except (OSError, json.JSONDecodeError):
        pass
    applied = 0
    try:
        for line in (retro_dir / "history.jsonl").read_text().splitlines():
            rec = json.loads(line)
            if rec.get("ts", 0) >= now - 7 * 86400:
                applied += len(rec.get("applied", []))
    except (OSError, json.JSONDecodeError):
        applied = None if not last else 0
    return {"last_retro_at": last.get("at"), "last_retro_kind": last.get("kind"),
            "last_retro_ok": last.get("ok"), "proposals_applied_7d": applied}


def _portfolio(variants_states: list[dict], variants) -> dict:
    balances = integrations.account_balances(variants)
    if balances is None:
        return {"total_equity_usdc": None, "total_cash_usdc": None, "total_positions_value_usdc": None,
                "accounts": []}
    by_alias = {v["account"]: v["id"] for v in variants_states if v.get("account") and v["mode"] == "live"}
    accounts = []
    for b in balances:
        b = dict(b)
        if not b.get("variant_id") and b.get("alias") in by_alias:
            b["variant_id"] = by_alias[b["alias"]]
        accounts.append(b)

    def total(key):
        vals = [a.get(key) for a in accounts]
        return None if not vals or any(v is None for v in vals) else round(sum(vals), 4)
    return {"total_equity_usdc": total("equity_usdc"), "total_cash_usdc": total("cash_usdc"),
            "total_positions_value_usdc": total("positions_value_usdc"), "accounts": accounts}


def strategy_summary(v: dict) -> dict:
    s = v[v["primary_mode"]]
    lad = v["ladder"]
    return {"id": v["id"], "family": v["family"], "hypothesis": v["hypothesis"], "mode": v["mode"],
            "account": v["account"], "sports": v["sports"], "stake_usdc": v["stake_usdc"],
            "next_stake_usdc": lad.get("next_stake_usdc"),
            "ladder": {"status": lad.get("status"), "trades_at_tier": lad.get("trades_at_tier"),
                       "needed": lad.get("needed"), "roi_ci_lo": lad.get("roi_ci_lo")},
            "params": v["params"], "open_positions": len(v["open"]),
            "open_cost_usdc": round(sum(o["cost_usdc"] or 0 for o in v["open"]), 4),
            "pnl": s["pnl"], "pnl_mode": v["primary_mode"], "trades": s["trades"], "win_rate": s["win_rate"],
            "roi": s["roi"], "last_trade_at": s["last_trade_at"], "last_change": v["last_change"]}


def overview(report: dict, paths, variants) -> dict:
    h = report["health"]
    c = h.get("collector") or {}
    return {
        "generated_at": report["generated_at"], "git_commit": report["git_commit"],
        "system": {
            "jobs": [{k: j.get(k) for k in ("name", "last_run_at", "last_ok_at", "status", "detail")}
                     for j in h.get("jobs", [])],
            "collector": {"last_poll_at": C.iso(c.get("last_poll_at")), "live_games": c.get("live_games"),
                          "tracked_markets": c.get("tracked_markets"),
                          "ws_last_message_at": C.iso(c.get("ws_last_message_at")),
                          "core_db_mb": h.get("core_db_mb"), "books_db_mb": h.get("books_db_mb"),
                          "disk_free_gb": h.get("disk_free_gb"),
                          "backfill_progress": c.get("backfill_progress") or {"games_done": None, "games_total": None}},
            "ai": _ai_status(paths, report["now"]),
        },
        "portfolio": _portfolio(report["variants"], variants),
        "strategies": [strategy_summary(v) for v in report["variants"]],
        "alerts": report["alerts"],
    }


def strategy_detail(v: dict, generated_at: str) -> dict:
    return {"id": v["id"], "generated_at": generated_at, "variant": v["yaml"],
            "param_history": [{k: p[k] for k in ("version", "at", "params", "stake_usdc", "mode", "author", "rationale")}
                              for p in v["param_history"]],
            "stake_events": [{k: s[k] for k in ("at", "from_usdc", "to_usdc", "from_mode", "to_mode", "reason", "evidence")}
                             for s in v["stake_events"]],
            "equity_curve": v["equity_curve"], "equity_mode": v["primary_mode"],
            "open_positions": [{k: o[k] for k in ("opened_at", "sport", "league", "title", "outcome", "entry_price",
                                                  "shares", "cost_usdc", "mark_price", "unrealized_pnl", "game_minute",
                                                  "status", "mode")} for o in v["open"]],
            "recent_positions": v["recent"],
            "breakdown": {k: v["breakdown"][k] for k in ("by_sport", "by_entry_minute", "by_stake", "by_day")}}


def dataset_stats(paths, now: int) -> dict:
    cache = Path(paths.research_dir) / "dataset_stats.json"
    try:
        cached = json.loads(cache.read_text())
        if now - cached.get("_ts", 0) < DATASET_STATS_TTL:
            return {k: v for k, v in cached.items() if k != "_ts"}
    except (OSError, json.JSONDecodeError):
        pass
    conn = C.open_ro(paths.core_db)
    stats = {"games": None, "markets": None, "price_bars": None, "from": None, "to": None, "by_sport": []}
    if conn is not None:
        try:
            q = lambda sql: conn.execute(sql).fetchone()  # noqa: E731
            stats["games"] = q("SELECT COUNT(*) FROM games")[0]
            stats["markets"] = q("SELECT COUNT(*) FROM markets")[0]
            stats["price_bars"] = q("SELECT COUNT(*) FROM price_bars")[0]
            lo, hi = q("SELECT MIN(start_time), MAX(start_time) FROM games")
            stats["from"], stats["to"] = C.iso(lo), C.iso(hi)
            stats["by_sport"] = [{"sport": s, "games": n} for s, n in
                                 conn.execute("SELECT sport, COUNT(*) FROM games GROUP BY sport ORDER BY 2 DESC")]
        finally:
            conn.close()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({**stats, "_ts": now}))
    return stats


def research(report: dict, paths) -> dict:
    cal = load_cached(paths, "calibration") or {}
    ev = load_cached(paths, "events") or {}
    return {"generated_at": report["generated_at"], "dataset": dataset_stats(paths, report["now"]),
            "calibration": cal.get("calibration", []), "brier": cal.get("brier", []),
            "event_sensitivity": ev.get("event_sensitivity", []),
            "event_by_score_state": ev.get("by_score_state", []),
            "stake_tiers": report.get("stake_tiers", []),
            "analysis_generated_at": {"calibration": cal.get("generated_at"), "events": ev.get("generated_at")},
            "notes": (cal.get("notes") or []) + (ev.get("notes") or [])}


TX_FIELDS = ("at", "variant_id", "account", "mode", "sport", "league", "game_title", "outcome", "side", "price",
             "shares", "usdc", "fee_usdc", "status", "position_id", "position_status", "exit_reason", "realized_pnl")


def transactions_24h(report: dict) -> list[dict]:
    return [{k: t.get(k) for k in TX_FIELDS} for t in sorted(report["transactions"], key=lambda t: t["ts"] or 0,
                                                              reverse=True)]


def games_24h(report: dict) -> dict:
    """latest/games_24h.json: the daily report's games section as built by reports.games (same rows)."""
    return report.get("games") or {"generated_at": report["generated_at"], "window": None, "games": []}


def attention_snapshot(reports_dir: Path = REPORTS_DIR) -> dict:
    """latest/attention.json: the retro's attention inbox (reports/attention.json), open items first."""
    try:
        state = json.loads((reports_dir / "attention.json").read_text())
        items = [i for i in state["items"] if isinstance(i, dict)]
    except (OSError, ValueError, KeyError, TypeError):
        state, items = {}, []
    keys = ("id", "created_at", "updated_at", "severity", "category", "title", "detail", "evidence_ref", "source",
            "status", "resolved_at", "resolution")
    return {"generated_at": state.get("generated_at"), "url": state.get("url"),
            "open": [{k: i.get(k) for k in keys} for i in items if i.get("status") == "open"],
            "resolved": [{k: i.get(k) for k in keys} for i in items if i.get("status") == "resolved"]}


def build_objects(paths, now: int | None = None, use_jenkins: bool = True, variants=None) -> dict[str, dict | list]:
    """{storage path: json object} for the latest/ read model."""
    if variants is None:
        variants, _ = report_build.load_variants()
    report = report_build.build("daily", paths, now=now, slot="morning", use_jenkins=use_jenkins, variants=variants)
    objs: dict[str, dict | list] = {"latest/overview.json": overview(report, paths, variants),
                                    "latest/research.json": research(report, paths),
                                    "latest/transactions_24h.json": transactions_24h(report)}
    objs["latest/games_24h.json"] = games_24h(report)
    objs["latest/attention.json"] = attention_snapshot()
    for v in report["variants"]:
        objs[f"latest/strategies/{v['id']}.json"] = strategy_detail(v, report["generated_at"])
    return objs


def _dump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":")).encode()


def report_files(reports_dir: Path = REPORTS_DIR) -> list[Path]:
    if not reports_dir.exists():
        return []
    files = [p for p in reports_dir.rglob("*") if p.is_file() and p.suffix in (".md", ".json")
             and "context" not in p.relative_to(reports_dir).parts]
    return sorted(files)


def changed_reports(paths, files: list[Path], reports_dir: Path = REPORTS_DIR) -> tuple[list[Path], dict]:
    state_path = Path(paths.state) / "publish_reports.json"
    try:
        seen = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        seen = {}
    changed, new = [], dict(seen)
    for f in files:
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        rel = str(f.relative_to(reports_dir.parent))
        if seen.get(rel) != digest:
            changed.append(f)
        new[rel] = digest
    return changed, new


def publish(paths, storage: Storage | None, prefix: str = "", out: Path | None = None, reports: bool = True,
            use_jenkins: bool = True, now: int | None = None, reports_dir: Path = REPORTS_DIR) -> list[str]:
    objs = build_objects(paths, now=now, use_jenkins=use_jenkins)
    written = []
    for path, obj in objs.items():
        body = _dump(obj)
        if out is not None:
            dest = out / prefix / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
        if storage is not None:
            storage.upload(prefix + path, body)
        written.append(prefix + path)
    if reports:
        changed, new_state = changed_reports(paths, report_files(reports_dir), reports_dir)
        for f in changed:
            rel = str(f.relative_to(reports_dir.parent))
            ctype = "text/markdown; charset=utf-8" if f.suffix == ".md" else "application/json"
            if out is not None:
                dest = out / prefix / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(f.read_bytes())
            if storage is not None:
                storage.upload(prefix + rel, f.read_bytes(), ctype)
            written.append(prefix + rel)
        if storage is not None and not prefix:
            state_path = Path(paths.state) / "publish_reports.json"
            state_path.write_text(json.dumps(new_state, indent=0))
    return written


EXPLORE_INTERVAL = 3600


def publish_explore(paths, storage: Storage | None, prefix: str = "", out: Path | None = None,
                    now: int | None = None, force: bool = False) -> list[str]:
    """latest/explore/*: recompute the /explore aggregates + game browser at most once per EXPLORE_INTERVAL
    (gated on the last attempt, so a failing run does not retry every 5 minutes) and upload only files whose
    content changed since the last upload."""
    from polylab.analysis import explore  # noqa: PLC0415
    now = now or int(time.time())
    state_path = Path(paths.state) / "publish_explore.json"
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        state = {}
    if not force and now - state.get("last_attempt", 0) < EXPLORE_INTERVAL:
        return []
    track = storage is not None and not prefix
    if track:
        state["last_attempt"] = now
        state_path.write_text(json.dumps(state))
    explore.run(paths, now=now)
    seen = state.get("digests", {}) if track else {}
    digests, written = {}, []
    for path, f in explore.cached_files(paths).items():
        body = f.read_bytes()
        digests[path] = hashlib.sha256(body).hexdigest()
        if seen.get(path) == digests[path]:
            continue
        if out is not None:
            dest = out / prefix / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
        if storage is not None:
            storage.upload(prefix + path, body)
        written.append(prefix + path)
    if track:
        state["digests"] = digests
        state_path.write_text(json.dumps(state))
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab publish")
    ap.add_argument("--dry-run", action="store_true", help="build only; do not upload")
    ap.add_argument("--out", type=Path, help="also write the JSON objects under this directory")
    ap.add_argument("--prefix", default="", help="storage path prefix, e.g. dev/ for smoke tests")
    ap.add_argument("--no-reports", action="store_true")
    ap.add_argument("--no-jenkins", action="store_true")
    args = ap.parse_args(argv)
    prefix = args.prefix if not args.prefix or args.prefix.endswith("/") else args.prefix + "/"
    try:
        paths = settings.paths()
    except settings.StorageUnavailable as exc:
        print(f"publish: {exc}", file=sys.stderr)
        return 3
    storage = None
    if not args.dry_run:
        storage = storage_from_env()
        if storage is None:
            print("publish: SUPABASE_URL / SUPABASE_SECRET_KEY not configured", file=sys.stderr)
            return 3
    t0 = time.time()
    written = publish(paths, storage, prefix=prefix, out=args.out, reports=not args.no_reports,
                      use_jenkins=not args.no_jenkins)
    try:  # dashboard /explore must never fail the core read model or the health check that follows
        written += publish_explore(paths, storage, prefix=prefix, out=args.out)
    except Exception as exc:  # noqa: BLE001
        print(f"publish: explore skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
    verb = "built" if args.dry_run else "uploaded"
    print(f"publish: {verb} {len(written)} objects in {time.time() - t0:.1f}s")
    return 0
