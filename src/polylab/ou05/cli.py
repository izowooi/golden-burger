"""`polylab ou05 <discover|poll|backfill|status>` — soccer O/U 0.5 market-life collector (polylab.ou05)."""

from __future__ import annotations

import json
import sys

SUB = {
    "discover": "polylab.ou05.discover:main",
    "poll": "polylab.ou05.poll:main",
    "backfill": "polylab.ou05.backfill:main",
}


def status(argv: list[str]) -> int:
    from polylab import db as dbmod
    from polylab.ou05 import store
    from polylab.settings import paths
    p = paths()
    reg = store.registry(p)
    try:
        q = lambda sql, *a: reg.execute(sql, a).fetchone()[0]  # noqa: E731
        out = {
            "markets": q("SELECT COUNT(*) FROM ou05_markets"),
            "open": q("SELECT COUNT(*) FROM ou05_markets WHERE closed=0"),
            "resolved": q("SELECT COUNT(*) FROM ou05_markets WHERE resolved_over IS NOT NULL"),
            "hist_todo": q("SELECT COUNT(*) FROM ou05_markets WHERE hist_status IS NULL"),
            "last_poll": dbmod.get_checkpoint(reg, "ou05.poll.last_run"),
            "shards": {sp.name: round(sp.stat().st_size / 1e6, 1) for sp in store.shard_paths(p)},
            "recent_runs": [dict(r) for r in reg.execute(
                "SELECT job, started_at, finished_at, ok, summary FROM job_runs ORDER BY id DESC LIMIT 5")],
        }
    finally:
        reg.close()
    print(json.dumps(out, indent=1, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in (*SUB, "status"):
        print("usage: polylab ou05 <discover|poll|backfill|status> [args]")
        return 0 if not argv or argv[0] in ("-h", "--help") else 2
    if argv[0] == "status":
        return status(argv[1:])
    import importlib
    mod, func = SUB[argv[0]].split(":")
    return int(getattr(importlib.import_module(mod), func)(argv[1:]) or 0)
