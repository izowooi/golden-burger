#!/usr/bin/env python3
"""Daily portfolio report script for multiple Polymarket accounts.

This script:
1. Loads configuration for multiple accounts from environment variables
2. Fetches portfolio data for each account using Polymarket Data API
3. Calculates P&L for 7-day and 30-day periods
4. Sends consolidated report to Slack

Usage:
    python daily_report.py

Environment Variables:
    # Account 1 (golden-apple)
    ACCOUNT_1_NAME=golden-apple
    ACCOUNT_1_ADDRESS=0x...

    # Account 2 (golden-banana)
    ACCOUNT_2_NAME=golden-banana
    ACCOUNT_2_ADDRESS=0x...

    # Account 3 (golden-cherry)
    ACCOUNT_3_NAME=golden-cherry
    ACCOUNT_3_ADDRESS=0x...

    # Slack notification
    SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
"""
import sys
import os
import logging
from pathlib import Path
from typing import Dict, List
from datetime import datetime

# Add src directory to Python path to import polybot modules
project_root = Path(__file__).parent / "golden-apple"
sys.path.insert(0, str(project_root / "src"))

from polybot.api.data_api_client import DataAPIClient
from polybot.notifications.slack_notifier import SlackNotifier

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f'daily_report_{datetime.now().strftime("%Y%m%d")}.log')
    ]
)
logger = logging.getLogger(__name__)


class AccountConfig:
    """Configuration for a single account."""

    def __init__(self, name: str, address: str):
        """Initialize account config.

        Args:
            name: Account name (e.g., "golden-apple")
            address: Wallet address (funder address)
        """
        self.name = name
        self.address = address

    def __repr__(self):
        return f"AccountConfig(name={self.name}, address={self.address[:10]}...)"


def load_account_configs() -> List[AccountConfig]:
    """Load account configurations from environment variables.

    Expects environment variables in format:
    - ACCOUNT_1_NAME, ACCOUNT_1_ADDRESS
    - ACCOUNT_2_NAME, ACCOUNT_2_ADDRESS
    - ACCOUNT_3_NAME, ACCOUNT_3_ADDRESS

    Returns:
        List of AccountConfig objects
    """
    accounts = []

    for i in range(1, 10):  # Support up to 9 accounts
        name_key = f"ACCOUNT_{i}_NAME"
        address_key = f"ACCOUNT_{i}_ADDRESS"

        name = os.getenv(name_key)
        address = os.getenv(address_key)

        if name and address:
            accounts.append(AccountConfig(name=name, address=address))
            logger.info(f"계좌 {i} 로드 완료: {name} ({address[:10]}...)")
        elif name or address:
            logger.warning(
                f"계좌 {i} 설정 불완전 - NAME: {bool(name)}, ADDRESS: {bool(address)}"
            )

    if not accounts:
        logger.error("환경변수에서 계좌 설정을 찾을 수 없습니다")
        logger.error("ACCOUNT_1_NAME, ACCOUNT_1_ADDRESS 등을 설정하세요")

    return accounts


def fetch_portfolio_report(
    client: DataAPIClient,
    account: AccountConfig
) -> Dict:
    """Fetch portfolio report for a single account.

    Args:
        client: Data API client
        account: Account configuration

    Returns:
        Portfolio summary dictionary
    """
    logger.info(f"포트폴리오 리포트 생성 중: {account.name}")

    try:
        summary = client.get_portfolio_summary(account.address)
        logger.info(
            f"{account.name} 리포트 완료 - "
            f"포지션: {summary['num_positions']}개, "
            f"가치: ${summary['total_value']:.2f}, "
            f"7d P&L: ${summary['pnl_7d']['total_pnl']:+.2f}"
        )
        return summary
    except Exception as e:
        logger.error(f"{account.name} 리포트 생성 실패: {e}", exc_info=True)
        return {
            "address": account.address,
            "positions": [],
            "total_value": 0,
            "num_positions": 0,
            "pnl_7d": {"total_pnl": 0, "num_trades": 0},
            "pnl_30d": {"total_pnl": 0, "num_trades": 0},
            "error": str(e)
        }


def main():
    """Main execution function."""
    logger.info("=" * 60)
    logger.info("Polymarket Daily Portfolio Report")
    logger.info(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # Load account configurations
    accounts = load_account_configs()

    if not accounts:
        logger.error("계좌 설정이 없어 종료합니다")
        sys.exit(1)

    logger.info(f"총 {len(accounts)}개 계좌 처리 시작")

    # Initialize clients
    data_client = DataAPIClient()
    slack = SlackNotifier()

    # Fetch reports for all accounts
    reports = {}
    errors = []

    for account in accounts:
        try:
            summary = fetch_portfolio_report(data_client, account)
            reports[account.name] = summary

            if "error" in summary:
                errors.append(f"{account.name}: {summary['error']}")
        except Exception as e:
            error_msg = f"{account.name} 처리 중 예외 발생: {e}"
            logger.error(error_msg, exc_info=True)
            errors.append(error_msg)

    # Send consolidated Slack report
    if reports:
        logger.info("Slack 리포트 전송 중...")
        try:
            success = slack.send_multi_account_report(reports)
            if success:
                logger.info("✅ Slack 리포트 전송 성공")
            else:
                logger.warning("⚠️ Slack 리포트 전송 실패 (웹훅 설정 확인 필요)")
        except Exception as e:
            logger.error(f"Slack 전송 중 오류: {e}", exc_info=True)

    # Send error notifications if any
    if errors:
        logger.warning(f"⚠️ 총 {len(errors)}개의 오류 발생")
        for error in errors:
            try:
                slack.send_error_notification("Daily Report", error)
            except Exception as e:
                logger.error(f"에러 알림 전송 실패: {e}")

    # Print summary
    logger.info("=" * 60)
    logger.info("리포트 요약")
    logger.info("-" * 60)

    total_value = sum(r.get("total_value", 0) for r in reports.values())
    total_positions = sum(r.get("num_positions", 0) for r in reports.values())
    total_pnl_7d = sum(
        r.get("pnl_7d", {}).get("total_pnl", 0) for r in reports.values()
    )
    total_pnl_30d = sum(
        r.get("pnl_30d", {}).get("total_pnl", 0) for r in reports.values()
    )

    logger.info(f"총 포트폴리오 가치: ${total_value:.2f}")
    logger.info(f"총 포지션 수: {total_positions}개")
    logger.info(f"7일 P&L: ${total_pnl_7d:+.2f}")
    logger.info(f"30일 P&L: ${total_pnl_30d:+.2f}")

    for account_name, summary in reports.items():
        logger.info(
            f"  • {account_name}: ${summary.get('total_value', 0):.2f} "
            f"(7d: ${summary.get('pnl_7d', {}).get('total_pnl', 0):+.2f})"
        )

    logger.info("=" * 60)
    logger.info("Daily report 완료")

    # Exit with error code if there were errors
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("사용자에 의해 중단됨")
        sys.exit(130)
    except Exception as e:
        logger.critical(f"예상치 못한 오류: {e}", exc_info=True)
        sys.exit(1)
