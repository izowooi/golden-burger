"""CLI entry point for Golden Blueberry / Closing Surge."""

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
        description="Golden Blueberry - Closing Surge Polymarket strategy"
    )
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("run", help="Run one archive/trading cycle")
    run.add_argument("--config", "-c", default="config.yaml")
    run.add_argument("--job", "-j", default="default")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--simulate", "-s", action="store_true")
    mode.add_argument(
        "--shadow",
        action="store_true",
        help="Run accountless 2%p/5%p x 72h/168h research into shadow.db",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Explicitly enable real CLOB orders (default is simulation)",
    )
    run.add_argument("--verbose", "-v", action="store_true")
    status = commands.add_parser("status", help="Show DB status")
    status.add_argument("--config", "-c", default="config.yaml")
    status.add_argument("--job", "-j", default="default")
    status_mode = status.add_mutually_exclusive_group()
    status_mode.add_argument("--simulate", "-s", action="store_true")
    status_mode.add_argument("--shadow", action="store_true")
    status_mode.add_argument("--live", action="store_true")
    config = commands.add_parser("config", help="Show resolved configuration")
    config.add_argument("--config", "-c", default="config.yaml")
    config.add_argument("--job", "-j", default="default")
    config_mode = config.add_mutually_exclusive_group()
    config_mode.add_argument("--simulate", "-s", action="store_true")
    config_mode.add_argument("--shadow", action="store_true")
    config_mode.add_argument("--live", action="store_true")
    return parser


def _load(args, simulation_override=None):
    try:
        return load_config(
            args.config,
            args.job,
            simulation_mode=simulation_override,
            shadow_mode=bool(getattr(args, "shadow", False)),
        )
    except ValueError as error:
        print(f"Configuration error: {error}")
        sys.exit(1)


def _run_simulation_override(args: argparse.Namespace) -> bool:
    """Require an explicit ``--live`` decision for every real-order run."""
    return not bool(args.live)


def _inspection_simulation_override(args: argparse.Namespace) -> bool | None:
    """Keep config/status pointed at the operator-selected runtime database."""
    if bool(args.live):
        return False
    if bool(args.simulate) or bool(getattr(args, "shadow", False)):
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

    config = _load(
        args,
        simulation_override=_inspection_simulation_override(args),
    )
    if args.command == "status":
        setup_logger(config.job_name, level=logging.WARNING)
        print(json.dumps(PolymarketBot(config).get_status(), indent=2, default=str))
        return

    trading = config.trading
    shadow_only = trading.lifecycle_mode == "shadow_only"
    print("=== Golden Blueberry / Closing Surge ===")
    print(f"Job: {config.job_name}")
    print(f"Simulation: {config.simulation_mode}")
    print(f"Lifecycle Mode: {trading.lifecycle_mode}")
    if shadow_only:
        print("Shadow grid: surge [2%p, 5%p] x horizon [72h, 168h]")
        print("Shadow orders: disabled; hypothetical gross P&L, fees excluded")
    print(
        "Strategy source cohort: "
        f"{trading.strategy_source_digest[:12]} "
        "(Git commit is provenance only)"
    )
    if not shadow_only:
        print(
            f"A/B arm: {trading.ab_arm} "
            f"(minimum consecutive surge {trading.entry.min_surge * 100:.0f}%p)"
        )
    # 이 전략의 처치축과 안전장치는 반드시 프리플라이트에 보여야 한다.
    print(
        f"Execution Mode: {trading.execution_mode} (A/B 공통 고정)"
    )
    if not shadow_only:
        print(
            f"Drawdown kill switch: 경제손익(확정+해결추정) <= -$"
            f"{trading.experiment_capital_usdc * trading.max_drawdown_stop:.2f}"
            f"  (실험자금 ${trading.experiment_capital_usdc:.2f}"
            f" x {trading.max_drawdown_stop * 100:.0f}%) → 신규 진입 자동 차단"
        )
    print(f"Intent autoresolve: {trading.intent_autoresolve}")
    print(f"DB: {config.db_path}")
    print(f"YES-only (inherent): {trading.yes_only_mode}")
    print(
        "Entry crossing: prior YES < "
        f"{trading.entry.prob_min:.2f}, current YES "
        f"[{trading.entry.prob_min:.2f}, {trading.entry.prob_max:.2f}]"
    )
    if shadow_only:
        print(
            "Consecutive surge grid: current - prior >= 0.020 / 0.050 "
            f"within {trading.max_snapshot_gap_minutes:.0f} minutes"
        )
        print("Entry horizon grid: (0, 72h] / (0, 168h]; in-play is separate")
    else:
        print(
            "Consecutive surge: current - prior >= "
            f"{trading.entry.min_surge:.3f} within "
            f"{trading.max_snapshot_gap_minutes:.0f} minutes"
        )
        lower_bracket = "(" if trading.entry.hours_min == 0 else "["
        print(
            f"Entry hours: {lower_bracket}{trading.entry.hours_min:.1f}, "
            f"{trading.entry.hours_max:.1f}]"
        )
    print(f"Absolute stop: current YES <= {trading.entry.stop_price:.2f}")
    print(
        "Absolute take profit: current YES >= "
        f"{trading.entry.take_profit_price:.2f}"
    )
    print("Trailing / pre-resolution time exit: disabled")
    print(
        f"Order: ${trading.buy_amount_usdc:.2f}, min shares "
        f"{trading.min_order_size:.2f} + {trading.min_order_buffer_shares:.2f} buffer"
    )
    print(
        "Effective entry gates: liquidity >= "
        f"${trading.effective_min_liquidity:,.0f}, "
        f"24h volume >= ${trading.effective_min_volume_24h:,.0f}"
    )
    if shadow_only:
        print("Limits: recorded as comparability metadata; no capital/position mutation")
    else:
        print(
            f"Limits: {trading.max_positions} total, "
            f"{trading.max_event_positions} per event, "
            f"${trading.max_open_notional_usdc:,.0f} open notional, "
            f"{trading.max_new_positions_per_cycle}/cycle, "
            f"{trading.reentry_cooldown_hours:.0f}h cooldown"
        )
    print(
        "Snapshot lineage: current run required, prior gap <= "
        f"{trading.max_snapshot_gap_minutes:.1f} minutes"
    )
    print(
        f"Archive: YES >= {trading.archive.prob_min:.2f}, "
        f"<= {trading.archive.hours_max:.0f}h, "
        f"cumulative volume >= "
        f"${trading.archive.min_cumulative_volume:,.0f}, "
        f"{trading.archive.retention_days}d retention"
    )
    print(
        "Sports: included by default; gameStartTime clock, "
        f"in-play={trading.sports.allow_in_play}, "
        f"max {trading.sports.max_in_play_minutes:.0f}m"
    )


if __name__ == "__main__":
    main()
