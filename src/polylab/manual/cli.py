"""`polylab manual sync [--alias A] [--since YYYY-MM-DD] [--full]` — see polylab.manual.sync."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    if not argv or argv[0] != "sync":
        print("usage: polylab manual sync [--alias A] [--since YYYY-MM-DD|Nd] [--full]", file=sys.stderr)
        return 2
    from polylab.manual.sync import main as sync_main
    return sync_main(argv[1:])
