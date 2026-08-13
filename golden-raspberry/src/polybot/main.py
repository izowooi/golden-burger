"""Command-line interface for Golden Raspberry."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Sequence

from .bot import PolymarketResearchBot
from .config import BotConfig, load_config


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
    if not any(getattr(handler, "_raspberry_console", False) for handler in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console._raspberry_console = True  # type: ignore[attr-defined]
        root.addHandler(console)
    if not any(
        getattr(handler, "_raspberry_path", None) == str(log_path)
        for handler in root.handlers
    ):
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler._raspberry_path = str(log_path)  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    return log_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Golden Raspberry accountless Queue Echo research collector"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--config", "-c", default="config.yaml")
        command.add_argument("--job", "-j", default="raspberry-re-shard-1")
        modes = command.add_mutually_exclusive_group(required=True)
        modes.add_argument("--simulate", action="store_true")
        modes.add_argument("--live", action="store_true", help="Rejected by design")

    run = commands.add_parser("run", help="Run one public-data collection cycle")
    common(run)
    run.add_argument("--verbose", action="store_true")
    for name in ("config", "status", "health"):
        command = commands.add_parser(name)
        common(command)
    return parser


def _default(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.live:
        print(
            "Golden Raspberry is accountless research-only; --live is forbidden",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_config(args.config, args.job, simulation_mode=True)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Configuration error ({type(error).__name__}): {error}", file=sys.stderr)
        return 2
    if args.command == "config":
        print(json.dumps(config.redacted_dict(), indent=2, sort_keys=True, default=_default))
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
        "starting Queue Echo job=%s shard=%s/3 source=%s db=%s log=%s",
        config.job_name,
        config.trading.experiment.shard_index,
        config.trading.strategy_source_digest[:12],
        config.db_path,
        log_path,
    )
    try:
        result = bot.run()
    except BaseException as error:
        logging.getLogger(__name__).exception("Queue Echo cycle failed: %s", error)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
