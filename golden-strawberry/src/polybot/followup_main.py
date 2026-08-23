"""Command-line interface for Golden Strawberry follow-up v2a."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from typing import Sequence

from .config import assert_no_credentials
from .followup_analyzer import write_followup_analysis
from .followup_bot import FollowupBot
from .followup_config import (
    FOLLOWUP_CANONICAL_JOB,
    load_followup_config,
)
from .main import _json_default, setup_logging


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Golden Strawberry compact Last Mile follow-up v2a"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--config", "-c", default="config.followup-v2a.yaml")
        command.add_argument("--job", "-j", default=FOLLOWUP_CANONICAL_JOB)
        modes = command.add_mutually_exclusive_group(required=True)
        modes.add_argument("--simulate", action="store_true")
        modes.add_argument("--live", action="store_true", help="Rejected by design")

    run = commands.add_parser("run", help="Run one compact follow-up cycle")
    common(run)
    run.add_argument("--verbose", action="store_true")
    for name in ("config", "status", "health"):
        common(commands.add_parser(name))
    analyze = commands.add_parser("analyze", help="Combine immutable v1/v2a health")
    common(analyze)
    analyze.add_argument("--v1-db", required=True)
    analyze.add_argument("--v2a-db", required=True)
    analyze.add_argument("--start", required=True)
    analyze.add_argument("--end", required=True, help="Exclusive UTC boundary")
    analyze.add_argument("--output", required=True)
    analyze.add_argument("--deep-v1", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.live:
        print(
            "Golden Strawberry follow-up is accountless research-only; --live is forbidden",
            file=sys.stderr,
        )
        return 2
    try:
        assert_no_credentials()
        config = load_followup_config(args.config, args.job, simulation_mode=True)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Configuration error ({type(error).__name__}): {error}", file=sys.stderr)
        return 2
    if args.command == "config":
        print(
            json.dumps(
                config.redacted_dict(),
                indent=2,
                sort_keys=True,
                default=_json_default,
            )
        )
        return 0
    if args.command == "analyze":
        try:
            result = write_followup_analysis(
                args.v1_db,
                args.v2a_db,
                start=args.start,
                end=args.end,
                output=args.output,
                deep_v1=args.deep_v1,
            )
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            print(f"Analysis error ({type(error).__name__}): {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("healthy") else 1
    bot = FollowupBot(config)
    if args.command == "status":
        result = bot.status()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("healthy") else 1
    if args.command == "health":
        result = bot.health()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("healthy") else 1

    try:
        bot._assert_runtime_workspace()
    except RuntimeError as error:
        print(f"Workspace error ({type(error).__name__}): {error}", file=sys.stderr)
        return 2
    log_path = setup_logging(config, verbose=args.verbose)  # type: ignore[arg-type]
    logging.getLogger(__name__).info(
        "starting Last Mile follow-up v2a job=%s source=%s v1=%s db=%s log=%s",
        config.job_name,
        config.trading.strategy_source_digest[:12],
        config.trading.v1_source.db_path,
        config.db_path,
        log_path,
    )
    try:
        result = bot.run()
    except BaseException as error:
        logging.getLogger(__name__).exception(
            "Last Mile follow-up v2a cycle failed: %s", error
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
