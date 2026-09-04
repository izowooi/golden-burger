"""CLI entry point for Golden Tangerine / Sports Resolution Hold Live."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .bot import PolymarketBot
from .analyzer import analyze_ab, parse_utc
from .config import load_config
from .utils.logger import setup_logger
from .utils.run_lock import RunLockUnavailable, db_run_lock


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
    analyze = commands.add_parser("analyze", help="Read-only exact-range A/B evidence analysis")
    analyze.add_argument(
        "--db",
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help="Arm label and SQLite path; repeat for A and B",
    )
    analyze.add_argument("--start", required=True, help="Inclusive ISO-8601 timestamp")
    analyze.add_argument(
        "--end-exclusive", required=True, help="Exclusive ISO-8601 timestamp"
    )
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

    if args.command == "analyze":
        specs = []
        for value in args.db:
            if "=" not in value:
                parser.error("--db must use LABEL=PATH")
            label, path = value.split("=", 1)
            if not label.strip() or not path.strip():
                parser.error("--db label and path must be non-empty")
            specs.append((label.strip(), path.strip()))
        try:
            report = analyze_ab(
                specs,
                start=parse_utc(args.start),
                end_exclusive=parse_utc(args.end_exclusive),
            )
        except (ValueError, RuntimeError, OSError) as error:
            print(f"Analysis error: {error}", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        if not report["strict_evidence_complete"]:
            sys.exit(3)
        return

    if args.command == "run":
        config = _load(
            args,
            simulation_override=_run_simulation_override(args),
        )
        setup_logger(config.job_name, verbose=args.verbose)
        try:
            with db_run_lock(config.db_path):
                PolymarketBot(config).run()
        except RunLockUnavailable:
            logging.warning(
                "previous %s cycle still owns the DB-scoped lock; skipping overlap",
                config.job_name,
            )
            return
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
    print(
        "Aligned two-outcome sports markets: both tokens "
        f"(yes_only={trading.yes_only_mode})"
    )
    print(
        "Cohort source/preregistration: "
        f"{trading.strategy_source_digest[:12]}/"
        f"{trading.preregistration_sha256[:12]}"
    )
    print(
        f"Configured ${trading.buy_amount_usdc:.2f} ask VWAP band: "
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
        f"Guards: open notional <= ${trading.max_open_notional_usdc:.2f}, "
        f"cumulative exact net loss < ${trading.max_cumulative_exact_loss_usdc:.2f}, "
        f"exclude_esports={trading.exclude_esports}"
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
