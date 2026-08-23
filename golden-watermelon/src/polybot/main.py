"""Golden Watermelon command line interface."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys

from .analyzer import analyze_database, analyze_databases
from .bot import ResearchBot
from .config import (
    CANONICAL_JOB,
    assert_no_credentials,
    league_registry_payload,
    load_config,
)
from .db.repository import ResearchRepository


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="polybot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("config", "run", "status", "health", "analyze"):
        command = subparsers.add_parser(name)
        modes = command.add_mutually_exclusive_group(required=True)
        modes.add_argument("--simulate", action="store_true")
        modes.add_argument("--live", action="store_true")
        command.add_argument("--job", default=CANONICAL_JOB)
        command.add_argument("--config", default="config.yaml")
        if name == "analyze":
            command.add_argument("--db", type=Path, action="append")
            command.add_argument("--output", type=Path)
    return parser


def _configure_log(config) -> None:
    log_dir = config.db_path.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    filename = datetime.now(timezone.utc).strftime("%Y%m%d.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_dir / filename, encoding="utf-8")],
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    assert_no_credentials()
    if "--live" in arguments:
        print("Golden Watermelon is accountless simulation-only; --live is forbidden", file=sys.stderr)
        return 2
    args = _parser().parse_args(arguments)
    config = load_config(args.config, args.job, simulation_mode=True)
    if args.command == "config":
        print(json.dumps(config.redacted_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "run":
        _configure_log(config)
        result = ResearchBot(config).run()
        logging.info("Golden Watermelon cycle completed: %s", json.dumps(result, sort_keys=True))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "analyze":
        databases = args.db or [config.db_path]
        result = (
            analyze_database(databases[0])
            if len(databases) == 1
            else analyze_databases(databases)
        )
        rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0
    repository = ResearchRepository(
        config.db_path,
        busy_timeout_ms=config.trading.storage.busy_timeout_ms,
        data_contract=config.trading.data_contract,
        schema_profile=config.trading.schema_profile,
        universe_profile=config.trading.universe_profile,
        classifier_version=config.trading.classifier_version,
        league_mapping_sha256=config.trading.league_mapping_sha256,
        league_mapping_json=json.dumps(
            league_registry_payload(config.trading.gamma.league_mapping),
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    if args.command == "health":
        print(json.dumps({"quick_check": repository.quick_check(), "data_contract": config.trading.data_contract, "db": str(config.db_path)}, sort_keys=True))
        return 0
    if args.command == "status":
        print(json.dumps(repository.summary(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
