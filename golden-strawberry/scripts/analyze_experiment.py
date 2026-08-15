#!/usr/bin/env python3
"""Standalone JSON analyzer for one verified Golden Strawberry database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from polybot.analyzer import parse_utc, write_analysis


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True, help="Exclusive UTC boundary")
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = write_analysis(
        args.db,
        start=parse_utc(args.start),
        end=parse_utc(args.end),
        output=Path(args.output),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
