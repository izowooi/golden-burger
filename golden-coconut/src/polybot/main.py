"""Golden Coconut command-line interface."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys

from .analyzer import analyze_databases
from .bot import ResearchBot
from .config import CANONICAL_JOB, assert_safe_environment, load_config
from .db.repository import ResearchRepository


_FORBIDDEN_MODE_TOKENS = {
    "--live", "--active", "--close-only", "--close_only", "active", "close_only"
}


def _contains_forbidden_mode(arguments: list[str]) -> bool:
    for argument in arguments:
        normalized = argument.strip().casefold()
        if normalized in _FORBIDDEN_MODE_TOKENS:
            return True
        if normalized.startswith("--lifecycle-mode=") and normalized.split("=", 1)[1] in {"active", "close_only"}:
            return True
        if normalized.startswith("--mode=") and normalized.split("=", 1)[1] == "live":
            return True
    return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="polybot")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("config", "run", "status", "health", "analyze"):
        command = commands.add_parser(name)
        modes = command.add_mutually_exclusive_group(required=True)
        modes.add_argument("--simulate", action="store_true")
        modes.add_argument("--shadow", action="store_true")
        command.add_argument("--job", default=CANONICAL_JOB)
        command.add_argument("--config", default="config.yaml")
        if name == "analyze":
            command.add_argument("--db", action="append", type=Path)
            command.add_argument("--output", type=Path)
    return parser


def _configure_logging(config) -> None:
    log_dir = config.db_path.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    filename = datetime.now(timezone.utc).strftime("%Y%m%d.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / filename, encoding="utf-8"),
        ],
        force=True,
    )


def _render(value, output: Path | None = None) -> int:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if output is not None:
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        # The environment gate is deliberately before argparse, config, logs,
        # database construction, and every public client.
        assert_safe_environment()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if _contains_forbidden_mode(arguments):
        print(
            "Golden Coconut is accountless archive-only; live/active/close_only are forbidden",
            file=sys.stderr,
        )
        return 2
    try:
        args = _parser().parse_args(arguments)
    except SystemExit as error:
        return int(error.code)
    mode = "shadow" if args.shadow else "sim"
    try:
        if args.command == "analyze" and args.db:
            return _render(analyze_databases(args.db), args.output)
        config = load_config(args.config, args.job, mode=mode)
        if args.command == "config":
            return _render(config.redacted_dict())
        if args.command == "run":
            _configure_logging(config)
            result = ResearchBot(config).run()
            logging.info("Golden Coconut cycle result: %s", json.dumps(result, sort_keys=True))
            return _render(result)
        if args.command == "analyze":
            return _render(analyze_databases([config.db_path]), args.output)
        if not config.db_path.is_file():
            return _render(
                {
                    "healthy": False,
                    "database_exists": False,
                    "database": str(config.db_path),
                }
            )
        repository = ResearchRepository(
            config,
            database_utc_date=ResearchRepository._peek_database_date(config.db_path),
            create=False,
        )
        if args.command == "status":
            return _render(repository.summary())
        if args.command == "health":
            return _render(repository.health())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Golden Coconut {getattr(args, 'command', 'command')} failed: {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
