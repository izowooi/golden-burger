"""Command-line interface for Golden Pomegranate."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Sequence

from .bot import PolymarketResearchBot
from .config import BotConfig, load_config
from .db.repository import ResearchRepository


class _UtcFormatter(logging.Formatter):
    converter = staticmethod(
        lambda timestamp: datetime.fromtimestamp(timestamp, timezone.utc).timetuple()
    )


def setup_logging(config: BotConfig, *, verbose: bool = False) -> Path:
    """Install one console/file summary logger in the job-isolated rsync layout."""
    log_dir = config.db_path.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{datetime.now(timezone.utc):%Y%m%d}.log"
    root = logging.getLogger()
    level = logging.DEBUG if verbose else logging.INFO
    root.setLevel(level)
    formatter = _UtcFormatter(
        "%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    if not any(
        getattr(handler, "_pomegranate_console", False) for handler in root.handlers
    ):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console._pomegranate_console = True  # type: ignore[attr-defined]
        root.addHandler(console)
    if not any(
        getattr(handler, "_pomegranate_path", None) == str(log_path)
        for handler in root.handlers
    ):
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler._pomegranate_path = str(log_path)  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    return log_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Golden Pomegranate accountless research-full-v1 collector"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--config", "-c", default="config.yaml")
        command.add_argument("--job", "-j", default="pomegranate-research")
        modes = command.add_mutually_exclusive_group(required=True)
        modes.add_argument("--simulate", action="store_true", help="Explicit safe mode")
        modes.add_argument(
            "--live", action="store_true", help="Rejected: live is impossible"
        )

    run = commands.add_parser("run", help="Run one atomic research census")
    common(run)
    run.add_argument("--verbose", "-v", action="store_true")

    for name, help_text in (
        ("config", "Print the resolved secret-free contract"),
        ("status", "Inspect collection counts without evidence-row mutation"),
        ("health", "Read-only DB/disk readiness check"),
    ):
        command = commands.add_parser(name, help=help_text)
        common(command)
        if name in {"status", "health"}:
            command.add_argument(
                "--db",
                help=(
                    "Operator-supplied absolute canonical SQLite path; Daily Rsync "
                    "catalog verification and SHA comparison remain separate"
                ),
            )

    manifest = commands.add_parser(
        "export-manifest", help="Export checksums and read-only evidence status"
    )
    common(manifest)
    manifest.add_argument("--output", "-o")
    manifest.add_argument(
        "--db",
        help=(
            "Operator-supplied absolute canonical SQLite path; Daily Rsync catalog "
            "verification and SHA comparison remain separate"
        ),
    )
    return parser


def _json_default(value):
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def _validated_read_only_db_path(value: str) -> Path:
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        raise ValueError("--db must be an absolute path")
    canonical = Path(os.path.abspath(requested))
    try:
        resolved = requested.resolve(strict=True)
    except OSError as error:
        raise ValueError("--db does not exist or cannot be resolved") from error
    if requested != canonical or resolved != canonical:
        raise ValueError("--db must be canonical and must not traverse a symlink")
    if not resolved.is_file():
        raise ValueError("--db must reference a regular file")
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if getattr(args, "live", False):
        print(
            "Golden Pomegranate is accountless research-only; --live is forbidden",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_config(
            args.config,
            args.job,
            # ``--simulate`` is a required operator acknowledgement, not
            # permission to hide a contradictory YAML/environment setting.
            # Let the resolved config reject any explicit false value.
            simulation_mode=None,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Configuration error ({type(error).__name__}): {error}", file=sys.stderr)
        return 2

    if args.command == "config":
        print(
            json.dumps(asdict(config), indent=2, sort_keys=True, default=_json_default)
        )
        return 0

    db_override = getattr(args, "db", None)
    if db_override:
        try:
            repository_path = _validated_read_only_db_path(db_override)
        except ValueError as error:
            print(f"Read-only DB error: {error}", file=sys.stderr)
            return 2
    else:
        repository_path = config.db_path
    repository_kwargs = {
        "busy_timeout_ms": config.trading.storage.busy_timeout_ms,
    }
    if db_override:
        repository_kwargs["immutable_reads"] = True
    repository = ResearchRepository(repository_path, **repository_kwargs)
    if args.command == "status":
        validation = None
        if db_override:
            validation = repository.validate_read_only_database(
                cadence_minutes=config.trading.cadence_minutes
            )
            if not validation.get("healthy"):
                print(
                    json.dumps(
                        {"read_only_input_validation": validation},
                        indent=2,
                        sort_keys=True,
                    )
                )
                return 1
        result = repository.status(
            config.trading.storage,
            cadence_minutes=config.trading.cadence_minutes,
        )
        if db_override:
            result["read_only_input_validation"] = validation
        print(
            json.dumps(
                result,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "health":
        # Inspection must not create a parent directory, lock file, DB, or WAL.
        result = (
            repository.validate_read_only_database(
                cadence_minutes=config.trading.cadence_minutes
            )
            if db_override
            else repository.health(
                config.trading.storage,
                cadence_minutes=config.trading.cadence_minutes,
            )
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("healthy") else 1
    if args.command == "export-manifest":
        result = repository.export_manifest(
            args.output,
            storage=config.trading.storage,
            cadence_minutes=config.trading.cadence_minutes,
            include_sibling_shards=not bool(db_override),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("healthy") else 1

    storage_preflight = repository.inspect_storage(
        storage=config.trading.storage,
        cadence_minutes=config.trading.cadence_minutes,
    )
    if storage_preflight["guard_state"] == "STOP":
        print("Storage guard STOP before log/DB/network writes", file=sys.stderr)
        return 1
    log_path = setup_logging(config, verbose=args.verbose)
    logger = logging.getLogger(__name__)
    logger.info(
        "starting research collector job=%s lifecycle=%s contract=%s db=%s log=%s",
        config.job_name,
        config.trading.lifecycle_mode,
        config.trading.data_contract,
        config.db_path,
        log_path,
    )
    try:
        PolymarketResearchBot(config, repository=repository).run()
    except KeyboardInterrupt:
        logger.warning("research collector interrupted")
        return 130
    except BaseException as error:
        logger.exception(
            "research collector failed error_type=%s error=%s",
            type(error).__name__,
            " ".join(str(error).splitlines()),
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "setup_logging"]
