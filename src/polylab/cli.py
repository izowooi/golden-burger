"""`polylab <command> [args]` — thin dispatcher; each command lives in its own module."""

from __future__ import annotations

import importlib
import sys

COMMANDS = {
    "discover": "polylab.collector.discover:main",      # find sports games/markets (Gamma)
    "poll": "polylab.collector.poll:main",              # 1-minute price + book snapshot
    "stream": "polylab.collector.stream:main",          # WebSocket daemon (sports + market)
    "backfill": "polylab.collector.backfill:main",      # prices-history / trades / resolution
    "tick": "polylab.engine.tick:main",                 # poll + run every strategy variant
    "accounts": "polylab.execution.accounts:main",      # balances / positions / redeem status
    "backtest": "polylab.analysis.backtest:main",       # replay strategies on stored bars
    "analyze": "polylab.analysis.cli:main",             # research datasets + calibration
    "report": "polylab.reports.cli:main",               # deterministic daily/weekly/monthly report
    "retro": "polylab.autopilot.retro:main",            # report + AI retro + guarded apply + slack
    "publish": "polylab.publish.snapshot:main",         # dashboard JSON -> Supabase Storage
    "health": "polylab.ops.health:main",                # freshness / disk / job checks
    "forecast": "polylab.research.llm_forecast:main",   # LLM 0-0 forecast study (paper only)
    "manual": "polylab.manual.cli:main",                # watch-only ledger of manual Track 2 bets (sync)
    "ou05": "polylab.ou05.cli:main",                    # soccer O/U 0.5 market-life quotes (discover/poll/backfill)
    "general": "polylab.general.cli:main",              # near-resolution markets of every category (cherry)
    "storage": "polylab.ops.storage:main",              # data growth budget + closed-shard VACUUM
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help", "help"} or argv[0] not in COMMANDS:
        print("usage: polylab <command> [args]\n\ncommands:")
        for name, target in COMMANDS.items():
            print(f"  {name:10s} {target}")
        return 0 if not argv or argv[0] in {"-h", "--help", "help"} else 2
    module_name, func_name = COMMANDS[argv[0]].split(":")
    func = getattr(importlib.import_module(module_name), func_name)
    return int(func(argv[1:]) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
