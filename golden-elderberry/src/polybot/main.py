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
        description="Polymarket Automated Trading Bot (Golden Elderberry - Panic Fade)",
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

    # Setup logging: --verbose > LOG_LEVEL env > INFO (setup_logger가 env를 읽음)
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

        s = config.trading.strategy
        print("=== Bot Configuration (Panic Fade) ===")
        print(f"Job Name: {config.job_name}")
        print(f"Simulation Mode: {config.simulation_mode}")
        print(f"Lifecycle Mode: {config.trading.lifecycle_mode}")
        print(f"DB Path: {config.db_path}")
        print()
        print("=== Trading Config ===")
        print(f"Buy Amount: ${config.trading.buy_amount_usdc} USDC")
        print(f"Min Liquidity: ${config.trading.min_liquidity:,.0f}")
        print(f"Min 24h Volume: ${config.trading.min_volume_24h:,.0f}")
        print(f"Max Positions: {config.trading.max_positions if config.trading.max_positions > 0 else 'Unlimited'}")
        print(f"Take Profit: {config.trading.take_profit_percent:+.0%} (0.99 캡)")
        print(f"Stop Loss: {config.trading.stop_loss_percent:+.0%}")
        print(f"Reentry Cooldown: {config.trading.reentry_cooldown_hours}h")
        print(f"History Backfill: {config.trading.history_backfill}")
        print()
        print("=== Panic Fade Strategy ===")
        print(f"Ref Window: {s.ref_window_hours}h (최근 {s.ref_exclude_recent_hours}h 제외)")
        print(f"Ref Min: {s.ref_min:.0%}")
        print(f"Drop Min: {s.drop_min:.0%}")
        print(f"Entry Band: [{s.current_min:.0%}, {s.current_max:.0%}]")
        print(f"Stabilization: {s.stab_window_minutes}분, std <= {s.stab_max_std}")
        print(f"Max Holding: {s.max_holding_hours}h")
        print()
        print("=== Time-based ===")
        print(f"Entry Hours Min: {config.trading.time_based.entry_hours_min}h")
        print(f"Exit Hours: {config.trading.time_based.exit_hours}h")
        print()
        print("=== Excluded Categories ===")
        if config.trading.excluded_categories:
            for cat in config.trading.excluded_categories:
                print(f"  - {cat}")
        else:
            print("  (비활성 - 모든 카테고리 스캔)")


if __name__ == "__main__":
    main()
