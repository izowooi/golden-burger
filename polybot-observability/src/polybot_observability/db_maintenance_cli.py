"""Standalone SQLite compaction command that never starts a trading cycle."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from .sqlite_maintenance import ENV_BACKUP_DIR, migrate_database, policy_for


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polybot-db-maintenance",
        description=(
            "Back up, compact, verify, and atomically replace one strategy "
            "SQLite DB without initializing API clients or running trades."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser(
        "migrate",
        help="Activate or safely tighten compact-v1 for an existing DB",
    )
    migrate.add_argument("--db", required=True, help="Exact existing SQLite path")
    migrate.add_argument(
        "--strategy",
        required=True,
        help="Strategy identity, for example golden-queen",
    )
    migrate.add_argument(
        "--backup-dir",
        help=(
            "Durable backup root outside the Jenkins workspace "
            "(default: $HOME/.polybot/db-backups)"
        ),
    )
    migrate.add_argument(
        "--confirm",
        action="store_true",
        help="Required acknowledgement that every writer/timer is stopped",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if not args.confirm:
        print(
            "Migration refused: stop every writer/timer, then pass --confirm.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if args.backup_dir:
        os.environ[ENV_BACKUP_DIR] = str(Path(args.backup_dir).expanduser())
    try:
        report = migrate_database(args.db, args.strategy)
        policy = policy_for(args.strategy)
    except Exception as error:
        print(f"Migration failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    payload = {
        "status": "already_active" if report is None else "migrated",
        "database": str(Path(args.db).expanduser().resolve()),
        "strategy_name": str(args.strategy).strip().lower(),
        "policy": asdict(policy),
        "report": asdict(report) if report is not None else None,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
