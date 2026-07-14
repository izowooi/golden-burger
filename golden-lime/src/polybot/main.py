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
        description="Polymarket Automated Trading Bot (Golden Lime - Shock Follow)",
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
        help="Enable verbose logging (LOG_LEVEL env보다 우선)"
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

    # Setup logging (--verbose는 DEBUG로 최우선, None이면 LOG_LEVEL env 사용)
    log_level = logging.DEBUG if getattr(args, "verbose", False) else None

    if args.command == "run":
        # Load config (pass simulation flag to use correct DB)
        try:
            config = load_config(
                args.config,
                args.job,
                simulation_mode=args.simulate if args.simulate else None,
            )
        except ValueError as e:
            print(f"Configuration error: {e}")
            print("Make sure POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER_ADDRESS are set in .env")
            sys.exit(1)

        # Setup logging with job name
        setup_logger(config.job_name, log_level)

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

        setup_logger(config.job_name, logging.WARNING)

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
        shock = trading.shock
        print("=== Bot Configuration (Golden Lime - Shock Follow) ===")
        print(f"Job Name: {config.job_name}")
        print(f"Simulation Mode: {config.simulation_mode}")
        print(f"Lifecycle Mode: {trading.lifecycle_mode}")
        print(f"DB Path: {config.db_path}")
        print()
        print("=== Trading Config ===")
        print(f"Buy Amount: ${trading.buy_amount_usdc} USDC")
        print(f"Min Liquidity: ${trading.min_liquidity:,.0f}")
        print(f"Min Volume 24h: ${trading.min_volume_24h:,.0f}")
        print(f"Max Positions: {trading.max_positions if trading.max_positions > 0 else 'Unlimited'}")
        print(f"Take Profit: {trading.take_profit_percent:.0%} (목표가 0.99 캡)")
        print(f"Stop Loss: {trading.stop_loss_percent:.0%}")
        print(f"Trailing Stop: {trading.trailing_stop.percent:.0%} (enabled: {trading.trailing_stop.enabled})")
        print(f"Entry Hours Min: {trading.time_based.entry_hours_min}h")
        print(f"Exit Hours: {trading.time_based.exit_hours}h")
        print(f"Reentry Cooldown: {trading.reentry_cooldown_hours}h")
        print(f"History Backfill: {trading.history_backfill}")
        print()
        print("=== Shock Follow Config ===")
        print(f"Jump Window: {shock.jump_window_hours}h")
        print(f"Jump Min: +{shock.jump_min:.2f} (윈도우 내 최저가 대비)")
        print(f"Base Range: [{shock.base_min:.2f}, {shock.base_max:.2f}]")
        print(f"Current Max: {shock.current_max:.2f}")
        print(f"Hold Window: {shock.hold_window_minutes:.0f}min, Max Pullback: {shock.max_pullback:.2f}")
        print(f"Volume Multiplier Min: x{shock.vol_mult_min:.1f}")
        print(f"Momentum Death Window: {shock.death_window_hours}h")
        print()
        print("=== Excluded Categories ===")
        if trading.excluded_categories:
            for cat in trading.excluded_categories:
                print(f"  - {cat}")
        else:
            print("  (없음 - 필터 비활성)")


if __name__ == "__main__":
    main()
