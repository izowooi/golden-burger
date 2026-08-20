"""CLI entry point for Golden Tangerine / Sports Resolution Hold Live."""

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
        description="Golden Tangerine - Sports Resolution Hold Live Polymarket strategy"
    )
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("run", help="Run one archive/trading cycle")
    run.add_argument("--config", "-c", default="config.yaml")
    run.add_argument("--job", "-j", default="default")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--simulate", "-s", action="store_true")
    mode.add_argument(
        "--live",
        action="store_true",
        help="Explicitly enable real CLOB orders (default is simulation)",
    )
    run.add_argument("--verbose", "-v", action="store_true")
    status = commands.add_parser("status", help="Show DB status")
    status.add_argument("--config", "-c", default="config.yaml")
    status.add_argument("--job", "-j", default="default")
    _add_mode_flags(status)
    config = commands.add_parser("config", help="Show resolved configuration")
    config.add_argument("--config", "-c", default="config.yaml")
    config.add_argument("--job", "-j", default="default")
    _add_mode_flags(config)
    return parser


def _add_mode_flags(command: argparse.ArgumentParser) -> None:
    mode = command.add_mutually_exclusive_group()
    mode.add_argument("--simulate", "-s", action="store_true")
    mode.add_argument("--live", action="store_true")


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


def _run_simulation_override(args: argparse.Namespace) -> bool:
    """실주문은 매번 명시적인 ``--live``를 요구한다 (queen과 같은 안전 기본값).

    config.yaml의 ``simulation_mode: true``는 그대로 두고, 실거래는 Jenkins에서
    ``--live``를 붙여야만 켜진다. 플래그 없이 실행하면 시뮬레이션이다.
    """
    return not bool(args.live)


def _inspection_simulation_override(args: argparse.Namespace):
    if args.live:
        return False
    if args.simulate:
        return True
    return None


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        config = _load(
            args,
            simulation_override=_run_simulation_override(args),
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

    config = _load(args, simulation_override=_inspection_simulation_override(args))
    if args.command == "status":
        setup_logger(config.job_name, level=logging.WARNING)
        print(json.dumps(PolymarketBot(config).get_status(), indent=2, default=str))
        return

    trading = config.trading
    print("=== Golden Tangerine / Sports Resolution Hold Live ===")
    print(f"Job: {config.job_name}")
    print(f"Simulation: {config.simulation_mode}")
    print(f"Lifecycle Mode: {trading.lifecycle_mode}")
    print(f"DB: {config.db_path}")
    print(f"Strict binary outcomes: both (yes_only={trading.yes_only_mode})")
    print(
        "Exact $5 ask VWAP band: "
        f"[{trading.entry.prob_min:.2f}, {trading.entry.prob_max:.2f}]"
    )
    lower_bracket = "(" if trading.entry.hours_min == 0 else "["
    print(
        f"Entry hours: {lower_bracket}{trading.entry.hours_min:.1f}, "
        f"{trading.entry.hours_max:.1f}]"
    )
    print("Exit: hold to proven resolution; TP/stop/trailing/time-exit disabled")
    print(
        f"Order: ${trading.buy_amount_usdc:.2f}, min shares "
        f"{trading.min_order_size:.2f} + {trading.min_order_buffer_shares:.2f} buffer"
    )
    print(
        f"Server universe: sports, liquidity >= ${trading.min_liquidity:,.0f}, "
        f"cumulative volume >= ${trading.min_cumulative_volume:,.0f}"
    )
    print(
        f"Limits: {trading.max_positions} total, "
        f"{trading.max_event_positions} per event, "
        f"{trading.max_new_positions_per_cycle} new/cycle, "
        f"{trading.reentry_cooldown_hours:.0f}h cooldown"
    )
    print(
        "Entry period: "
        f"[{trading.experiment_start_utc}, "
        f"{trading.experiment_entry_end_utc})"
    )
    print(
        f"Archive: both outcomes, <= {trading.archive.hours_max:.0f}h, "
        f"{trading.archive.retention_days}d retention"
    )


if __name__ == "__main__":
    main()
