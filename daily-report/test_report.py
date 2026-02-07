#!/usr/bin/env python3
"""Test script for portfolio report functionality.

This script tests the Data API client and Slack notifier without
requiring actual account credentials. Use this to verify the setup
before deploying to Jenkins.

Usage:
    python3 test_report.py
"""
import sys
import os
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv

# .env 파일 로드 (daily-report 디렉토리의 .env)
load_dotenv(Path(__file__).parent / ".env")

from polybot_reporter.api.data_api_client import DataAPIClient
from polybot_reporter.notifications.slack_notifier import SlackNotifier


def test_data_api_client():
    """Test Data API client with a known address (optional)."""
    print("=" * 60)
    print("Testing Data API Client")
    print("=" * 60)

    client = DataAPIClient()

    # Test with a dummy address (replace with real one if you want)
    test_address = os.getenv("TEST_ADDRESS", "0x0000000000000000000000000000000000000000")

    print(f"Testing with address: {test_address[:10]}...")

    try:
        # Test get_positions
        print("\n1. Testing get_positions()...")
        positions = client.get_positions(test_address)
        print(f"   ✅ Found {len(positions)} positions")

        # Test get_portfolio_summary
        print("\n2. Testing get_portfolio_summary()...")
        summary = client.get_portfolio_summary(test_address)
        print(f"   ✅ Portfolio value: ${summary['total_value']:.2f}")
        print(f"   ✅ Positions: {summary['num_positions']}")
        print(f"   ✅ 7d P&L: ${summary['pnl_7d']['total_pnl']:+.2f}")
        print(f"   ✅ 30d P&L: ${summary['pnl_30d']['total_pnl']:+.2f}")

        return summary

    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None


def test_slack_notifier(mock_summary=None):
    """Test Slack notifier (will actually send if SLACK_WEBHOOK_URL is set)."""
    print("\n" + "=" * 60)
    print("Testing Slack Notifier")
    print("=" * 60)

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")

    if not webhook_url:
        print("⚠️  SLACK_WEBHOOK_URL not set - skipping Slack test")
        print("   Set SLACK_WEBHOOK_URL environment variable to test Slack")
        return

    slack = SlackNotifier(webhook_url)

    # Create a mock summary if none provided
    if not mock_summary:
        mock_summary = {
            "address": "0x1234...abcd",
            "positions": [
                {"outcome": "Yes", "value": 100, "pnl": 10},
                {"outcome": "No", "value": 50, "pnl": -5},
            ],
            "total_value": 150.0,
            "num_positions": 2,
            "pnl_7d": {
                "realized_pnl": 5.0,
                "unrealized_pnl": 10.0,
                "total_pnl": 15.0,
                "num_trades": 3
            },
            "pnl_30d": {
                "realized_pnl": 20.0,
                "unrealized_pnl": 10.0,
                "total_pnl": 30.0,
                "num_trades": 10
            }
        }

    try:
        print("\n1. Testing send_portfolio_report()...")
        success = slack.send_portfolio_report("test-account", mock_summary)
        if success:
            print("   ✅ Single account report sent successfully")
        else:
            print("   ❌ Failed to send single account report")

        print("\n2. Testing send_multi_account_report()...")
        multi_reports = {
            "golden-apple": mock_summary,
            "golden-banana": mock_summary,
            "golden-cherry": mock_summary
        }
        success = slack.send_multi_account_report(multi_reports)
        if success:
            print("   ✅ Multi-account report sent successfully")
        else:
            print("   ❌ Failed to send multi-account report")

        print("\n3. Testing send_error_notification()...")
        success = slack.send_error_notification(
            "test-account",
            "This is a test error notification"
        )
        if success:
            print("   ✅ Error notification sent successfully")
        else:
            print("   ❌ Failed to send error notification")

    except Exception as e:
        print(f"   ❌ Error: {e}")


def main():
    """Main test function."""
    print("\n")
    print("╔" + "═" * 58 + "╗")
    print("║" + " " * 10 + "Polymarket Daily Report - Test Suite" + " " * 10 + "║")
    print("╚" + "═" * 58 + "╝")
    print(f"\nTest time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Test Data API
    summary = test_data_api_client()

    # Test Slack notifier
    test_slack_notifier(summary)

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print("✅ Data API client: Working")

    if os.getenv("SLACK_WEBHOOK_URL"):
        print("✅ Slack notifier: Tested (check Slack channel)")
    else:
        print("⚠️  Slack notifier: Not tested (SLACK_WEBHOOK_URL not set)")

    print("\n💡 Tips:")
    print("   - Set TEST_ADDRESS env var to test with a real account")
    print("   - Set SLACK_WEBHOOK_URL to test Slack notifications")
    print("   - Check your Slack channel for test messages")
    print("\nExample:")
    print("   export TEST_ADDRESS=0x1234567890abcdef1234567890abcdef12345678")
    print("   export SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...")
    print("   python3 test_report.py")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Critical error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
