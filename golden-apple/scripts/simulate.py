#!/usr/bin/env python3
"""Run bot in simulation mode.

This script runs the trading bot without executing real orders.
Useful for testing the strategy and verifying configuration.

Usage:
    python scripts/simulate.py
    python scripts/simulate.py --job test_job
"""
import argparse
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description="Run bot in simulation mode")
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="Config file path"
    )
    parser.add_argument(
        "--job", "-j",
        default="simulation",
        help="Job name (default: simulation)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose logging"
    )
    args = parser.parse_args()

    # Load environment
    load_dotenv()

    # Import after path setup
    import logging
    from polybot.config import load_config
    from polybot.bot import PolymarketBot
    from polybot.utils.logger import setup_logger

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    setup_logger(args.job, log_level)

    logger = logging.getLogger(__name__)

    # Load config
    try:
        config = load_config(args.config, args.job)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        print("\nMake sure .env file exists with:")
        print("  POLYMARKET_PRIVATE_KEY=your_key")
        print("  POLYMARKET_FUNDER_ADDRESS=your_address")
        sys.exit(1)

    # Force simulation mode
    config.simulation_mode = True

    print()
    print("=" * 50)
    print("SIMULATION MODE")
    print("=" * 50)
    print(f"Job Name: {config.job_name}")
    print(f"Config: {args.config}")
    print(f"DB Path: {config.db_path}")
    print()
    print("Trading Settings:")
    print(f"  Buy Threshold: {config.trading.buy_threshold:.0%}")
    print(f"  Sell Threshold: {config.trading.sell_threshold:.0%}")
    print(f"  Buy Amount: ${config.trading.buy_amount_usdc} USDC")
    print(f"  Min Liquidity: ${config.trading.min_liquidity:,.0f}")
    print("=" * 50)
    print()

    # Run bot
    bot = PolymarketBot(config)

    try:
        bot.run()
    except KeyboardInterrupt:
        print("\nSimulation interrupted by user")
    except Exception as e:
        logger.exception(f"Simulation failed: {e}")
        sys.exit(1)

    # Show final status
    print()
    print("=" * 50)
    print("Simulation Complete")
    print("=" * 50)

    status = bot.get_status()
    stats = status.get("statistics", {})

    print(f"Total Trades: {stats.get('total_trades', 0)}")
    print(f"Holding: {stats.get('holding', 0)}")
    print(f"Completed: {stats.get('completed', 0)}")
    print(f"Skipped: {stats.get('skipped', 0)}")
    print(f"Total P&L: ${stats.get('total_pnl', 0):.4f}")

    if status.get("holdings"):
        print()
        print("Current Holdings:")
        for h in status["holdings"]:
            print(f"  - {h['outcome']} @ {h['buy_price']:.2%}: {h['question']}")


if __name__ == "__main__":
    main()
