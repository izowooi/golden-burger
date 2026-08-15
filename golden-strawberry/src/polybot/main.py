"""Command-line interface for Golden Strawberry."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Sequence

from .analyzer import parse_utc, write_analysis
from .bot import PolymarketResearchBot
from .config import CANONICAL_JOB, BotConfig, assert_no_credentials, load_config


class _UtcFormatter(logging.Formatter):
    converter = staticmethod(
        lambda timestamp: datetime.fromtimestamp(timestamp, timezone.utc).timetuple()
    )


def _prune_logs(log_dir: Path, retention_days: int) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for path in log_dir.glob("*.log"):
        if not path.is_file() or path.is_symlink():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            path.unlink()


def setup_logging(config: BotConfig, *, verbose: bool = False) -> Path:
    log_dir = config.db_path.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _prune_logs(log_dir, config.trading.storage.bot_log_retention_days)
    log_path = log_dir / f"{datetime.now(timezone.utc):%Y%m%d}.log"
    root = logging.getLogger()
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = logging.DEBUG if verbose else getattr(logging, level_name, logging.INFO)
    root.setLevel(level)
    formatter = _UtcFormatter(
        "%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    if not any(
        getattr(handler, "_strawberry_console", False) for handler in root.handlers
    ):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console._strawberry_console = True  # type: ignore[attr-defined]
        root.addHandler(console)
    if not any(
        getattr(handler, "_strawberry_path", None) == str(log_path)
        for handler in root.handlers
    ):
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler._strawberry_path = str(log_path)  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    return log_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Golden Strawberry accountless Last Mile research collector"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--config", "-c", default="config.yaml")
        command.add_argument("--job", "-j", default=CANONICAL_JOB)
        modes = command.add_mutually_exclusive_group(required=True)
        modes.add_argument("--simulate", action="store_true")
        modes.add_argument("--live", action="store_true", help="Rejected by design")

    run = commands.add_parser("run", help="Run one public-data collection cycle")
    common(run)
    run.add_argument("--verbose", action="store_true")
    for name in ("config", "status", "health"):
        command = commands.add_parser(name)
        common(command)
    analyze = commands.add_parser("analyze", help="Analyze an immutable verified DB")
    common(analyze)
    analyze.add_argument("--db", required=True)
    analyze.add_argument("--start", required=True)
    analyze.add_argument("--end", required=True, help="Exclusive UTC boundary")
    analyze.add_argument("--output", required=True)
    return parser


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Mode rejection is deliberately earlier than config, DB, log, or client work.
    if args.live:
        print(
            "Golden Strawberry is accountless research-only; --live is forbidden",
            file=sys.stderr,
        )
        return 2
    try:
        assert_no_credentials()
        config = load_config(args.config, args.job, simulation_mode=True)
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
            result = write_analysis(
                args.db,
                start=parse_utc(args.start),
                end=parse_utc(args.end),
                output=args.output,
            )
        except (OSError, ValueError, sqlite3.Error) as error:
            print(f"Analysis error ({type(error).__name__}): {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    bot = PolymarketResearchBot(config)
    if args.command == "status":
        result = bot.status()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("healthy") else 1
    if args.command == "health":
        result = bot.health()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("healthy") else 1

    log_path = setup_logging(config, verbose=args.verbose)
    logging.getLogger(__name__).info(
        "starting Last Mile job=%s source=%s db=%s log=%s",
        config.job_name,
        config.trading.strategy_source_digest[:12],
        config.db_path,
        log_path,
    )
    try:
        result = bot.run()
    except BaseException as error:
        logging.getLogger(__name__).exception("Last Mile cycle failed: %s", error)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
