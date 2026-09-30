"""`polylab report <daily|weekly|monthly> [--slot dawn|morning|evening] [--out DIR] [--json]`.

Deterministic only (no AI, no Slack). Without --out the markdown goes to stdout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from polylab import settings
from polylab.reports import build, render


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab report")
    ap.add_argument("kind", choices=["daily", "weekly", "monthly"])
    ap.add_argument("--slot", choices=sorted(build.SLOTS), help="daily slot (default: from current KST time)")
    ap.add_argument("--out", type=Path, help="write <name>.md and <name>.json into this directory")
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    ap.add_argument("--no-jenkins", action="store_true")
    args = ap.parse_args(argv)
    try:
        paths = settings.paths()
    except settings.StorageUnavailable as exc:
        print(f"report: {exc}", file=sys.stderr)
        return 3
    report = build.build(args.kind, paths, slot=args.slot, use_jenkins=not args.no_jenkins)
    md = render.render(report)
    if args.out:
        md_path, js_path = build.write(report, args.out, md)
        print(f"wrote {md_path} {js_path}")
    elif args.json:
        print(build.to_json(report))
    else:
        print(md)
    return 0
