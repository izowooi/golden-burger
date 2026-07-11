"""Command line entrypoint for retrospective audits and consistent backups."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .retro_audit import (
    audit_many,
    backup_databases,
    discover_databases,
    parse_as_of,
    write_audit_bundle,
)


def _database_paths(
    values: list[Path], root: Path | None, *, include_sim: bool = False
) -> list[Path]:
    paths = {value.expanduser().resolve() for value in values}
    if root is not None:
        paths.update(
            discover_databases(root.expanduser().resolve(), include_sim=include_sim)
        )
    return sorted(path for path in paths if path.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and export Polymarket bot retrospective data"
    )
    parser.add_argument("--version", action="version", version="polybot-observability 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="DB/log 회고 준비 상태 검사")
    audit_parser.add_argument("--db", action="append", default=[], type=Path)
    audit_parser.add_argument(
        "--root", type=Path, help="trades.db 재귀 탐색 루트 (simulation은 --include-sim)"
    )
    audit_parser.add_argument("--days", type=int, default=30)
    audit_parser.add_argument("--as-of", help="재현용 종료일(UTC, YYYY-MM-DD)")
    audit_parser.add_argument("--output-dir", required=True, type=Path)
    audit_parser.add_argument(
        "--include-sim",
        action="store_true",
        help="trades_sim.db를 별도 simulation_assumption cohort로 포함",
    )
    audit_parser.add_argument(
        "--strict", action="store_true", help="CRITICAL/HIGH 이슈가 있으면 exit 1"
    )

    backup_parser = subparsers.add_parser("backup", help="SQLite online backup + checksum")
    backup_parser.add_argument("--db", action="append", default=[], type=Path)
    backup_parser.add_argument("--root", type=Path)
    backup_parser.add_argument("--output-dir", required=True, type=Path)
    backup_parser.add_argument(
        "--include-sim", action="store_true", help="trades_sim.db도 backup"
    )

    args = parser.parse_args()
    databases = _database_paths(args.db, args.root, include_sim=args.include_sim)
    if not databases:
        parser.error("--db 또는 --root에서 trades.db를 한 개 이상 찾을 수 없습니다")

    if args.command == "backup":
        destination = backup_databases(databases, args.output_dir)
        print(destination)
        return

    if args.days < 1:
        parser.error("--days는 1 이상이어야 합니다")
    try:
        as_of = parse_as_of(args.as_of)
    except ValueError as error:
        parser.error(str(error))
    bundle = audit_many(databases, days=args.days, as_of=as_of)
    json_path, markdown_path = write_audit_bundle(bundle, args.output_dir)
    print(json_path)
    print(markdown_path)
    if args.strict:
        # Simulation assumptions are visible in the bundle but never make a
        # live readiness gate fail. Default discovery excludes them entirely.
        severe = bundle["live_issue_counts"].get("CRITICAL", 0) + bundle[
            "live_issue_counts"
        ].get("HIGH", 0)
        if severe:
            sys.exit(1)


if __name__ == "__main__":
    main()
