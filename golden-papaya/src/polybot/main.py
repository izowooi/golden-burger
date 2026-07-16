"""CLI entry point for Golden Papaya / Final Five."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .bot import PolymarketBot
from .config import load_config
from .utils.logger import setup_logger


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Golden Papaya - Final Five Polymarket strategy"
    )
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("run", help="Run one archive/trading cycle")
    run.add_argument("--config", "-c", default="config.yaml")
    run.add_argument("--job", "-j", default="default")
    run.add_argument("--simulate", "-s", action="store_true")
    run.add_argument("--verbose", "-v", action="store_true")
    status = commands.add_parser("status", help="Show DB status")
    status.add_argument("--config", "-c", default="config.yaml")
    status.add_argument("--job", "-j", default="default")
    config = commands.add_parser("config", help="Show resolved configuration")
    config.add_argument("--config", "-c", default="config.yaml")
    config.add_argument("--job", "-j", default="default")
    return parser


def _load(args, simulation_override=None):
    try:
        return load_config(
            args.config,
            args.job,
            simulation_mode=simulation_override,
        )
    except ValueError as error:
        print(f"Configuration error: {error}")
        sys.exit(1)


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        config = _load(
            args,
            simulation_override=True if args.simulate else None,
        )
        setup_logger(config.job_name, verbose=args.verbose)
        try:
            PolymarketBot(config).run()
        except KeyboardInterrupt:
            print("\n사용자에 의해 중단됨")
            sys.exit(0)
        except Exception as error:
            logging.exception("Bot 실패: %s", error)
            sys.exit(1)
        return

    config = _load(args)
    if args.command == "status":
        setup_logger(config.job_name, level=logging.WARNING)
        print(json.dumps(PolymarketBot(config).get_status(), indent=2, default=str))
        return

    trading = config.trading
    print("=== Golden Papaya / Final Five ===")
    print(f"Job: {config.job_name}")
    print(f"Simulation: {config.simulation_mode}")
    print(f"Lifecycle Mode: {trading.lifecycle_mode}")
    print(f"DB: {config.db_path}")
    print(f"YES-only (inherent): {trading.yes_only_mode}")
    print(
        "Entry crossing: prior YES < "
        f"{trading.entry.prob_min:.2f}, current YES "
        f"[{trading.entry.prob_min:.2f}, {trading.entry.prob_max:.2f}]"
    )
    lower_bracket = "(" if trading.entry.hours_min == 0 else "["
    print(
        f"Entry hours: {lower_bracket}{trading.entry.hours_min:.1f}, "
        f"{trading.entry.hours_max:.1f}]"
    )
    print(f"Absolute stop: current YES <= {trading.entry.stop_price:.2f}")
    print("Take profit / trailing / pre-resolution time exit: disabled")
    print(
        f"Order: ${trading.buy_amount_usdc:.2f}, min shares "
        f"{trading.min_order_size:.2f} + {trading.min_order_buffer_shares:.2f} buffer"
    )
    print(
        f"Limits: {trading.max_positions} total, "
        f"{trading.max_event_positions} per event, "
        f"{trading.reentry_cooldown_hours:.0f}h cooldown"
    )
    print(
        "Snapshot lineage: current run required, prior gap <= "
        f"{trading.max_snapshot_gap_minutes:.1f} minutes"
    )
    print(
        f"Archive: YES >= {trading.archive.prob_min:.2f}, "
        f"<= {trading.archive.hours_max:.0f}h, "
        f"{trading.archive.retention_days}d retention"
    )


if __name__ == "__main__":
    main()
