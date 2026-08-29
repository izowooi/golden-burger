"""CLI entry point for Golden Watermelon Live."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .bot import PolymarketBot
from .config import load_config
from .utils.deadline import enforced_cycle_deadline
from .utils.logger import setup_logger
from .utils.run_lock import exclusive_job_run_lock


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Golden Watermelon Live - in-play major-sports winner strategy"
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
            lock_path = config.db_path.parent / ".cycle-run.lock"
            with exclusive_job_run_lock(lock_path) as acquired:
                if not acquired:
                    logging.warning(
                        "이전 %s cycle이 아직 실행 중이므로 중복 실행을 안전하게 건너뜁니다",
                        config.job_name,
                    )
                    return
                with enforced_cycle_deadline() as cycle_budget:
                    PolymarketBot(config, cycle_budget=cycle_budget).run()
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
    print("=== Golden Watermelon Live / In-Play Major-Sports Winner ===")
    print(f"Job: {config.job_name}")
    print(f"Simulation: {config.simulation_mode}")
    print(f"Lifecycle Mode: {trading.lifecycle_mode}")
    print(f"Sport Family: {trading.sport_family}")
    print(f"DB: {config.db_path}")
    print(f"Whole-match winner tokens only (yes_only={trading.yes_only_mode})")
    print(
        "Cohort source/preregistration: "
        f"{trading.strategy_source_digest[:12]}/"
        f"{trading.preregistration_sha256[:12]}"
    )
    print(
        "Exact $5 ask VWAP band: "
        f"[{trading.entry.prob_min:.3f}, {trading.entry.prob_max:.3f}]"
    )
    print(
        f"In-play age: [{trading.entry.hours_min:.1f}, "
        f"{trading.entry.hours_max:.1f}] hours"
    )
    print(
        f"Exit: hold to proven resolution; emergency FOK stop at displayed bid "
        f"<= {trading.entry.stop_price:.2f}, executable floor "
        f">= {trading.entry.stop_price - trading.entry.max_stop_slippage:.2f}, "
        f"spread <= {trading.entry.max_stop_spread:.2f}; no TP/time-exit"
    )
    print(
        f"Order: ${trading.buy_amount_usdc:.2f}, min shares "
        f"{trading.min_order_size:.2f} + {trading.min_order_buffer_shares:.2f} buffer"
    )
    print(
        f"Server universe: live {trading.sport_family}; cumulative volume >= "
        f"${trading.min_cumulative_volume:.0f}, liquidity >= "
        f"${trading.min_liquidity:.0f}; fresh exact-$5 CLOB depth is final gate"
    )
    print(
        f"Limits: {trading.max_positions} total, "
        f"{trading.max_event_positions} per event, "
        f"{trading.max_new_positions_per_cycle} new/cycle, "
        f"{trading.max_emergency_sells_per_cycle} emergency sell/cycle, "
        f"{trading.reentry_cooldown_hours:.0f}h cooldown"
    )
    print(
        "Economic drawdown entry guard: "
        f"-${trading.experiment_capital_usdc * trading.max_drawdown_stop:.2f} "
        "(confirmed SELL + proven resolution P&L)"
    )
    print(
        "Entry period: "
        f"[{trading.experiment_start_utc}, "
        f"{trading.experiment_entry_end_utc})"
    )
    print(
        f"Archive: result YES books, <= {trading.archive.hours_max:.0f}h in play, "
        f"{trading.archive.retention_days}d retention"
    )


if __name__ == "__main__":
    main()
