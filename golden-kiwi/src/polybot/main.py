"""CLI entry point for Golden Kiwi / Micro-Cascade."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .bot import PolymarketBot
from .config import EXPERIMENT_SCHEMA_VERSION, load_config
from .utils.logger import setup_logger


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Golden Kiwi - Micro-Cascade research strategy"
    )
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("run", help="Run one archive/trading cycle")
    run.add_argument("--config", "-c", default="config.yaml")
    run.add_argument("--job", "-j", default="kiwi-sim-b-3x2")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--simulate", "-s", action="store_true")
    mode.add_argument(
        "--live",
        action="store_true",
        help="Rejected: Kiwi is research/simulation-only",
    )
    run.add_argument("--verbose", "-v", action="store_true")
    status = commands.add_parser("status", help="Show DB status")
    status.add_argument("--config", "-c", default="config.yaml")
    status.add_argument("--job", "-j", default="kiwi-sim-b-3x2")
    config = commands.add_parser("config", help="Show resolved configuration")
    config.add_argument("--config", "-c", default="config.yaml")
    config.add_argument("--job", "-j", default="kiwi-sim-b-3x2")
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


def _run_simulation_override(args: argparse.Namespace) -> bool:
    """Map ``--live`` to the fail-closed configuration path."""
    return not bool(args.live)


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

    config = _load(args)
    if args.command == "status":
        setup_logger(config.job_name, level=logging.WARNING)
        print(json.dumps(PolymarketBot(config).get_status(), indent=2, default=str))
        return

    trading = config.trading
    print("=== Golden Kiwi / Micro-Cascade ===")
    print(f"Job: {config.job_name}")
    print(f"Simulation only: {config.simulation_mode}")
    print("Live execution: HARD DISABLED (failed frozen OOS promotion gate)")
    print(f"Lifecycle Mode: {trading.lifecycle_mode}")
    print(f"DB: {config.db_path}")
    print(
        "Strategy source cohort: "
        f"{trading.strategy_source_digest[:12]} "
        "(Git commit is provenance only)"
    )
    print(f"YES-only (inherent): {trading.yes_only_mode}")
    print(
        f"Frozen arm: {trading.arm_name} "
        f"({trading.entry.confirmation_steps} steps, "
        f"cumulative >= {trading.entry.min_cumulative_move:.2f})"
    )
    print(
        "Entry staircase: each move > 0 and <= "
        f"{trading.entry.max_step_move:.2f}, cumulative <= "
        f"{trading.entry.max_cumulative_move:.2f}, gaps "
        f"[{trading.entry.min_snapshot_gap_minutes:.0f}, "
        f"{trading.entry.max_snapshot_gap_minutes:.0f}]m"
    )
    print(
        f"Universe: strict binary YES [{trading.entry.prob_min:.2f}, "
        f"{trading.entry.prob_max:.2f}], resolution >= "
        f"{trading.entry.min_hours_to_resolution:.0f}h"
    )
    print(
        f"Exit: first actual cycle >= {trading.entry.hold_minutes:.0f}m "
        f"(promotion window <= {trading.entry.max_exit_delay_minutes:.0f}m delay); "
        "no price stop/target"
    )
    print(
        f"Order: ${trading.buy_amount_usdc:.2f}, min shares "
        f"{trading.min_order_size:.2f} + {trading.min_order_buffer_shares:.2f} buffer"
    )
    print(
        "Effective entry gates: liquidity >= "
        f"${trading.effective_min_liquidity:,.0f}, "
        f"24h volume >= ${trading.effective_min_volume_24h:,.0f}"
    )
    print(
        f"Limits: {trading.max_positions} total, "
        f"{trading.max_event_positions} per event, "
        f"${trading.max_open_notional_usdc:,.0f} open notional, "
        f"{trading.max_new_positions_per_cycle}/cycle, "
        f"{trading.reentry_cooldown_hours:.0f}h cooldown"
    )
    print(
        "Snapshot lineage: current run required, exact persisted "
        f"{trading.entry.confirmation_steps + 1} observations"
    )
    print(
        f"Archive: YES [{trading.archive.prob_min:.2f}, "
        f"{trading.archive.prob_max:.2f}], "
        f"{trading.archive.retention_days}d retention"
    )
    print(
        "Gamma request: "
        f"liquidity >= ${trading.archive.fetch_min_liquidity:,.0f}, "
        f"cumulative volume >= ${trading.archive.fetch_min_total_volume:,.0f}; "
        f"budgets={trading.archive.max_fetch_pages} pages / "
        f"{trading.archive.max_fetch_markets:,} markets / "
        f"{trading.archive.max_sweep_seconds:.0f}s"
    )
    print(
        "Exact excluded tags: "
        + ", ".join(trading.excluded_categories)
    )
    print(
        "Drawdown kill switch: permanent DB latch when research economic "
        "P&L <= -$"
        f"{trading.experiment_capital_usdc * trading.max_drawdown_stop:.2f}; "
        "no automatic reset"
    )
    experiment = config.experiment
    if experiment.enabled:
        print(
            "Promotion collection: ENABLED "
            f"[{experiment.window_start.isoformat()}, "
            f"{experiment.window_end.isoformat()}) UTC, "
            f"{experiment.expected_cadence_minutes}m cadence, "
            f"offset={experiment.expected_offset_minute}"
        )
        print(
            f"Experiment evidence: schema={EXPERIMENT_SCHEMA_VERSION}, "
            f"analyzer={experiment.analyzer_version}, "
            f"prereg={experiment.preregistration_sha256}"
        )
    else:
        print(
            "Promotion collection: DISABLED (smoke/archive evidence only; "
            "cannot satisfy promotion gates)"
        )


if __name__ == "__main__":
    main()
