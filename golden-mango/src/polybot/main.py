"""CLI entry point for the trading bot."""
import argparse
import json
import logging
import sys
from .config import load_config
from .bot import PolymarketBot
from .utils.logger import setup_logger


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Polymarket Automated Trading Bot (Golden Mango - Patience Premium)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with default settings
  polybot run

  # Run in simulation mode
  polybot run --simulate

  # Run with custom config and job name
  polybot run --config config_aggressive.yaml --job aggressive

  # Check bot status
  polybot status

  # Show current configuration
  polybot config
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run trading cycle")
    run_parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="Path to config file (default: config.yaml)"
    )
    run_parser.add_argument(
        "--job", "-j",
        default="default",
        help="Job name for DB separation (default: default)"
    )
    run_parser.add_argument(
        "--simulate", "-s",
        action="store_true",
        help="Run in simulation mode (no real orders)"
    )
    run_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    run_parser.add_argument(
        "--yes-only",
        action="store_true",
        help="YES-Only 모드: 1위 후보(Yes) 포지션만 매수, No 제외"
    )

    # Status command
    status_parser = subparsers.add_parser("status", help="Show bot status")
    status_parser.add_argument("--config", "-c", default="config.yaml")
    status_parser.add_argument("--job", "-j", default="default")

    # Config command
    config_parser = subparsers.add_parser("config", help="Show configuration")
    config_parser.add_argument("--config", "-c", default="config.yaml")
    config_parser.add_argument("--job", "-j", default="default")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        # Load config (pass simulation flag to use correct DB)
        try:
            config = load_config(
                args.config,
                args.job,
                simulation_mode=args.simulate if args.simulate else None,
                yes_only_mode=True if args.yes_only else None,
            )
        except ValueError as e:
            print(f"Configuration error: {e}")
            print("Make sure POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER_ADDRESS are set in .env")
            sys.exit(1)

        # Setup logging with job name (LOG_LEVEL env 반영, --verbose가 최우선)
        setup_logger(config.job_name, verbose=args.verbose)

        # Run bot
        bot = PolymarketBot(config)
        try:
            bot.run()
        except KeyboardInterrupt:
            print("\n사용자에 의해 중단됨")
            sys.exit(0)
        except Exception as e:
            logging.exception(f"Bot 실패: {e}")
            sys.exit(1)

    elif args.command == "status":
        try:
            config = load_config(args.config, args.job)
        except ValueError as e:
            print(f"Configuration error: {e}")
            sys.exit(1)

        setup_logger(config.job_name, level=logging.WARNING)

        bot = PolymarketBot(config)
        status = bot.get_status()

        print(json.dumps(status, indent=2, default=str))

    elif args.command == "config":
        try:
            config = load_config(args.config, args.job)
        except ValueError as e:
            print(f"Configuration error: {e}")
            sys.exit(1)

        trading = config.trading
        carry = trading.carry
        gate = trading.momentum_gate

        print("=== Bot Configuration ===")
        print(f"Job Name: {config.job_name}")
        print(f"Simulation Mode: {config.simulation_mode}")
        print(f"Lifecycle Mode: {trading.lifecycle_mode}")
        print(f"DB Path: {config.db_path}")
        print()
        print("=== Trading Config (Patience Premium) ===")
        print(f"Buy Amount: ${trading.buy_amount_usdc} USDC")
        print(f"Min Liquidity: ${trading.min_liquidity:,.0f}")
        print(f"Min Volume 24h: ${trading.min_volume_24h:,.0f} (0 = 필터 비활성)")
        print(f"Max Positions: {trading.max_positions if trading.max_positions > 0 else 'Unlimited'}")
        print(f"YES-Only Mode: {trading.yes_only_mode}")
        print()
        print("=== Carry (핵심 수식: y = ((1-p)/p) * (8760/h)) ===")
        print(f"Yield Min: {carry.yield_min:.2f} (연환산 {carry.yield_min:.0%})")
        print(f"Probability Band: [{carry.prob_min:.3f}, {carry.prob_max:.3f}]")
        print(f"Entry Hours: {carry.entry_hours_min}h < 잔여 <= {carry.entry_hours_max}h")
        print()
        print("=== Momentum Guard ===")
        print(f"Lookback: {gate.lookback_hours}h, Min Change: {gate.min_change:+.3f}")
        print()
        print("=== Exit ===")
        print(f"Stop Loss: {trading.stop_loss_percent:.0%}")
        print(f"Take Profit: 목표가 0.99 도달 (수렴 보유, take_profit_percent={trading.take_profit_percent})")
        print(f"Trailing Stop: 없음 (수렴 보유가 본질)")
        print(f"Time Exit: 해결 {trading.exit_hours}h 전")
        print()
        print("=== Reentry / Data ===")
        print(f"Reentry Cooldown: {trading.reentry_cooldown_hours:.0f}h")
        print(f"History Backfill: {trading.history_backfill}")
        print()
        print("=== Excluded Categories ===")
        if trading.excluded_categories:
            for cat in trading.excluded_categories:
                print(f"  - {cat}")
        else:
            print("  (없음 - 필터 비활성)")


if __name__ == "__main__":
    main()
