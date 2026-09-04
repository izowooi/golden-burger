"""CLI contract for the accountless Cherry shadow runtime."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

from . import RUNTIME_JOB
from .analyzer import analyze_shadow_database, parse_utc
from .config import PROJECT_ROOT, load_shadow_config
from .db import ShadowRepository
from .runtime import ShadowRuntime
from .safety import assert_shadow_boundary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="polybot shadow")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command_name in ("config", "run", "status", "analyze"):
        command = subparsers.add_parser(command_name)
        command.add_argument("--shadow", action="store_true", required=True)
        command.add_argument("--job", default=RUNTIME_JOB)
        command.add_argument("--config", type=Path, default=PROJECT_ROOT / "shadow_config.yaml")
        if command_name == "run":
            command.add_argument("--verbose", action="store_true")
        if command_name == "analyze":
            command.add_argument("--db", type=Path)
            command.add_argument("--start", required=True)
            command.add_argument("--end", required=True)
            command.add_argument("--output", type=Path)
    return parser


def shadow_main(arguments: list[str]) -> int:
    assert_shadow_boundary(arguments)
    args = _parser().parse_args(arguments)
    config = load_shadow_config(args.config, args.job)
    if args.command == "config":
        print(json.dumps(config.evidence_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "run":
        logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.INFO,
            format="%(asctime)sZ %(levelname)s %(message)s",
            force=True,
        )
        print(json.dumps(ShadowRuntime(config).run(), sort_keys=True))
        return 0
    if args.command == "status":
        if not config.db_path.exists():
            print(json.dumps({"runtime_job": RUNTIME_JOB, "db_path": str(config.db_path), "exists": False}, indent=2, sort_keys=True))
            return 0
        print(json.dumps(ShadowRepository(config.db_path, config).summary(), indent=2, sort_keys=True))
        return 0
    if args.command == "analyze":
        db_path = args.db or config.db_path
        result = analyze_shadow_database(
            db_path,
            start=parse_utc(args.start),
            end=parse_utc(args.end),
        )
        rendered = json.dumps(result, indent=2, sort_keys=True)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0
    return 2


def dispatch_from_process() -> int:
    return shadow_main(list(sys.argv[1:]))
