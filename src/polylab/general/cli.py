"""`polylab general <discover|poll|backfill|status>` — near-resolution general-market collector (polylab.general)."""

from __future__ import annotations

import json
import sys

SUB = {
    "discover": "polylab.general.discover:main",
    "poll": "polylab.general.poll:main",
    "backfill": "polylab.general.backfill:main",
}


def status(argv: list[str]) -> int:
    from polylab import db as dbmod
    from polylab.general import store
    from polylab.settings import paths
    p = paths()
    reg = store.registry(p)
    try:
        q = lambda sql, *a: reg.execute(sql, a).fetchone()[0]  # noqa: E731
        out = {
            "markets": q("SELECT COUNT(*) FROM gen_markets"),
            "open": q("SELECT COUNT(*) FROM gen_markets WHERE closed=0"),
            "open_polled": q("SELECT COUNT(*) FROM gen_markets WHERE closed=0 AND in_core=0"),
            "resolved": q("SELECT COUNT(*) FROM gen_markets WHERE resolved_index IS NOT NULL"),
            "by_category": {r[0]: r[1] for r in reg.execute(
                "SELECT category, COUNT(*) FROM gen_markets GROUP BY 1 ORDER BY 2 DESC")},
            "hist_status": {r[0] or "todo": r[1] for r in reg.execute(
                "SELECT hist_status, COUNT(*) FROM gen_markets GROUP BY 1")},
            "enum": json.loads(dbmod.get_checkpoint(reg, "general.hist.enum") or "{}").get("day"),
            "last_poll": dbmod.get_checkpoint(reg, "general.poll.last_run"),
            "shards_mb": {sp.name: round(sp.stat().st_size / 1e6, 1) for sp in store.shard_paths(p)},
            "recent_runs": [dict(r) for r in reg.execute(
                "SELECT job, started_at, finished_at, ok, substr(summary, 1, 300) AS summary FROM job_runs "
                "ORDER BY id DESC LIMIT 5")],
        }
    finally:
        reg.close()
    print(json.dumps(out, indent=1, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in (*SUB, "status"):
        print("usage: polylab general <discover|poll|backfill|status> [args]")
        return 0 if not argv or argv[0] in ("-h", "--help") else 2
    if argv[0] == "status":
        return status(argv[1:])
    import importlib
    mod, func = SUB[argv[0]].split(":")
    return int(getattr(importlib.import_module(mod), func)(argv[1:]) or 0)
