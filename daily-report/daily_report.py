#!/usr/bin/env python3
"""Daily portfolio report script for multiple Polymarket accounts.

This script:
1. Loads configuration for multiple accounts from environment variables
2. Fetches portfolio data for each account using Polymarket Data API
3. Calculates P&L for 7-day and 30-day periods
4. Sends consolidated report to Slack
5. Upserts the same daily snapshot to Supabase

Usage:
    python daily_report.py
    python daily_report.py --monthly   # 날짜와 무관하게 월간 리포트 강제 실행

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

    # Account 4..6 (second apple + reusable eco/fox slots)
    ACCOUNT_4_NAME=golden-apple
    ACCOUNT_4_ADDRESS=0x...
    ACCOUNT_5_NAME=golden-eco
    ACCOUNT_5_ADDRESS=0x...
    ACCOUNT_6_NAME=golden-fox
    ACCOUNT_6_ADDRESS=0x...

    # Slack notification
    SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

    # Supabase server-side credentials
    SUPABASE_URL=https://your-project-ref.supabase.co
    SUPABASE_SECRET_KEY=sb_secret_...
    DAILY_EVIDENCE_DB=data/daily_evidence.sqlite3
"""

import argparse
import logging
import os
import stat
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from polybot_reporter.account_config import AccountConfig, load_account_configs
from polybot_reporter.api.data_api_client import DataAPIClient
from polybot_reporter.contracts import (
    PortfolioContractError,
    canonical_money_breakdown,
    safe_error_message,
    validate_account_display_names,
    validate_complete_reports,
    validate_report_valuation,
)
from polybot_reporter.notifications.slack_notifier import SlackNotifier
from polybot_reporter.storage.evidence_store import DailyEvidenceStore, EvidenceStoreError
from polybot_reporter.storage.supabase_writer import (
    SupabaseConfigurationError,
    SupabasePortfolioWriter,
)

load_dotenv(Path(__file__).parent / ".env")

def secure_private_file(path: Path, label: str) -> Path:
    """Create/tighten one Unix financial-data file to mode 0600."""
    expanded = path.expanduser()
    absolute = Path(os.path.abspath(os.fspath(expanded)))
    absolute.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        if absolute.is_symlink():
            raise OSError(f"{label} path가 symlink입니다")
        descriptor = os.open(absolute, flags, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(f"{label} path가 regular file이 아닙니다")
        os.fchmod(descriptor, 0o600)
        mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
        if mode != 0o600:
            raise OSError(f"{label} mode가 0600이 아닙니다: {mode:o}")
    except OSError as error:
        raise RuntimeError(f"{label}의 0600 권한을 강제할 수 없습니다") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return absolute


# Configure logging. Jenkins supplies a build-specific filename so one build
# never re-archives historical financial logs from a persistent workspace.
log_file = secure_private_file(
    Path(
        os.getenv("DAILY_REPORT_LOG_FILE")
        or f"daily_report_{datetime.now().strftime('%Y%m%d')}.log"
    ),
    "daily report log",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file),
    ],
)
logger = logging.getLogger(__name__)


def fetch_portfolio_report(client: DataAPIClient, account: AccountConfig) -> dict:
    """Fetch portfolio report for a single account.

    Args:
        client: Data API client
        account: Account configuration

    Returns:
        Portfolio summary dictionary
    """
    logger.info(f"포트폴리오 리포트 생성 중: {account.display_name}")

    try:
        summary = client.get_portfolio_summary(account.address)
        summary.pop("address", None)
        validate_report_valuation(account.display_name, summary)
        money = canonical_money_breakdown(account.display_name, summary)
        summary["total_value"] = float(money.total)
        summary["position_value"] = float(money.position)
        summary["cash_balance"] = float(money.cash)
        logger.info(
            f"{account.display_name} 리포트 완료 - "
            f"포지션: {summary['num_positions']}개, "
            f"가치: ${summary['total_value']:.2f}"
        )
        return summary
    except Exception as e:
        safe_error = safe_error_message(e)
        logger.error("%s 리포트 생성 실패: %s", account.display_name, safe_error)
        return {
            "error": safe_error,
        }


def mark_delivery_outcome(
    store: DailyEvidenceStore,
    run_id: str,
    channel: str,
    status: str,
    errors: list[str],
    *,
    error: BaseException | str | None = None,
) -> bool:
    """Update local delivery provenance and turn update failure into a hard error."""
    try:
        final_status = store.mark_delivery(run_id, channel, status, error=error)
        logger.info(
            "delivery evidence 갱신 - run_id=%s, channel=%s, status=%s, final=%s",
            run_id,
            channel,
            status,
            final_status,
        )
        return True
    except EvidenceStoreError as evidence_error:
        message = (
            f"Delivery evidence 갱신 실패({channel}={status}): "
            + safe_error_message(evidence_error)
        )
        logger.error(message)
        errors.append(message)
        return False


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Polymarket Daily Portfolio Reporter")
    parser.add_argument(
        "--monthly",
        action="store_true",
        help="날짜와 무관하게 월간 리포트(30일 P&L 포함)를 강제 실행합니다",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Slack 전송과 Supabase 적재 없이 로직만 실행합니다",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["run", "check-supabase"],
        help="실행 명령어: run 또는 check-supabase",
    )
    return parser.parse_args()


def main():
    """Main execution function."""
    args = parse_args()

    logger.info("=" * 60)
    logger.info("Polymarket Daily Portfolio Report")
    logger.info(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    accounts = load_account_configs()
    if not accounts:
        logger.error("계좌 설정이 없어 종료합니다")
        sys.exit(1)
    configured_names = [account.display_name for account in accounts]
    try:
        validate_account_display_names(configured_names)
    except PortfolioContractError as error:
        logger.error("Jenkins 6계정 설정 계약 실패: %s", safe_error_message(error))
        sys.exit(1)

    if args.command == "check-supabase":
        try:
            account_count = SupabasePortfolioWriter().check_connection(configured_names)
            logger.info(
                "✅ Supabase 연결/계정 계약 확인 성공 - 계정 카탈로그: %d개",
                account_count,
            )
            return
        except Exception as e:
            logger.error("Supabase 연결 점검 실패: %s", safe_error_message(e))
            sys.exit(1)

    supabase_writer = None
    if not args.simulate:
        try:
            # Validate the key type before fetching data or sending Slack messages.
            supabase_writer = SupabasePortfolioWriter()
            supabase_writer.check_connection(configured_names)
        except SupabaseConfigurationError as e:
            logger.error("Supabase 설정 오류: %s", safe_error_message(e))
            sys.exit(1)
        except Exception as e:
            logger.error("Supabase 계정 계약 확인 실패: %s", safe_error_message(e))
            sys.exit(1)

    logger.info(f"총 {len(accounts)}개 계좌 처리 시작")

    # Initialize clients
    data_client = DataAPIClient()
    slack = SlackNotifier()
    evidence_store = DailyEvidenceStore()

    # Fetch reports for all accounts
    reports = {}
    errors = []
    failed_account_names = []

    for account in accounts:
        try:
            summary = fetch_portfolio_report(data_client, account)
            reports[account.display_name] = summary

            if "error" in summary:
                errors.append(f"{account.display_name}: {summary['error']}")
                failed_account_names.append(account.display_name)
        except Exception as e:
            error_msg = (
                f"{account.display_name} 처리 중 예외 발생: {safe_error_message(e)}"
            )
            logger.error(error_msg)
            errors.append(error_msg)
            failed_account_names.append(account.display_name)

    if not errors:
        try:
            validate_complete_reports(reports)
        except PortfolioContractError as contract_error:
            errors.append(
                "완전한 6계정 valuation 계약 실패: "
                + safe_error_message(contract_error)
            )
            failed_account_names = configured_names.copy()

    # A collection error invalidates the whole snapshot. Never emit a normal
    # consolidated report containing synthetic zero balances: downstream Slack
    # collectors must only ever see COMPLETE portfolio reports.
    if errors:
        try:
            evidence = evidence_store.record_run(
                reports,
                expected_display_names=configured_names,
                failed_display_names=failed_account_names,
                delivery_enabled=not args.simulate,
            )
            logger.info(
                "daily evidence 실패 run 저장 - run_id=%s, 계정=%d, 포지션=%d",
                evidence.run_id,
                evidence.account_count,
                evidence.position_count,
            )
        except EvidenceStoreError as evidence_error:
            logger.error(
                "실패 run daily evidence 저장도 실패했습니다: %s",
                safe_error_message(evidence_error),
            )
        logger.error(
            "계정 수집 오류 %d건 - 정상 Slack 리포트와 Supabase 적재를 모두 생략합니다",
            len(errors),
        )
        if not args.simulate:
            for error in errors:
                try:
                    slack.send_error_notification("Daily Report", error)
                except Exception as notification_error:
                    logger.error(
                        "에러 알림 전송 실패: %s",
                        safe_error_message(notification_error),
                    )
        sys.exit(1)

    try:
        evidence = evidence_store.record_run(
            reports,
            expected_display_names=configured_names,
            delivery_enabled=not args.simulate,
        )
        if evidence.status != "COMPLETE":
            raise EvidenceStoreError(f"성공 수집인데 evidence status가 {evidence.status}입니다")
        logger.info(
            "daily evidence 저장 성공 - run_id=%s, 계정=%d, 포지션=%d, DB=%s",
            evidence.run_id,
            evidence.account_count,
            evidence.position_count,
            evidence.database_path,
        )
    except EvidenceStoreError as evidence_error:
        error_message = "Daily evidence 적재 실패: " + safe_error_message(evidence_error)
        # Do not attach the chained traceback: an underlying driver exception may
        # contain credentials even when the outer message is sanitized.
        logger.error(error_message)
        if not args.simulate:
            slack.send_error_notification("Daily Report", error_message)
        sys.exit(1)

    # Fill in 7d/30d P&L as the change in total_value over the window, read from
    # the stored daily snapshots. This matches the dashboard's definition so both
    # surfaces agree. Non-critical: a failure here must not block Slack/DB.
    if reports and supabase_writer is not None:
        try:
            period_pnl = supabase_writer.get_period_pnl(reports)
            for name, summary in reports.items():
                if "error" in summary:
                    continue
                windows = period_pnl.get(name, {})
                for days, key in ((7, "pnl_7d"), (30, "pnl_30d")):
                    summary.setdefault(key, {})["total_pnl"] = windows.get(days)
        except Exception as e:
            logger.warning(
                "기간 손익 계산 실패(과거 스냅샷 조회): %s", safe_error_message(e)
            )

    is_monthly = args.monthly or datetime.now().day == 1

    # Commit the complete DB snapshot before publishing a COMPLETE Slack marker.
    # A failed RPC must never leave a downstream-consumable success message.
    if reports:
        if args.simulate:
            logger.info("🔕 [SIMULATE] Supabase 일일 스냅샷 적재 생략")
        else:
            logger.info("Supabase 일일 스냅샷 atomic RPC 적재 중...")
            try:
                if supabase_writer is None:
                    raise RuntimeError("Supabase writer가 초기화되지 않았습니다")
                result = supabase_writer.write_daily_snapshot(reports)
                logger.info(
                    "✅ Supabase 적재 성공 - 날짜: %s, 계정: %d개, 총 자산: $%.2f",
                    result.report_date,
                    result.account_count,
                    result.total_value,
                )
            except Exception as e:
                error_msg = f"Supabase DB atomic 적재 실패: {safe_error_message(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
                mark_delivery_outcome(
                    evidence_store,
                    evidence.run_id,
                    "supabase",
                    "FAILED",
                    errors,
                    error=e,
                )
                mark_delivery_outcome(
                    evidence_store,
                    evidence.run_id,
                    "slack",
                    "SKIPPED",
                    errors,
                )
            else:
                mark_delivery_outcome(
                    evidence_store,
                    evidence.run_id,
                    "supabase",
                    "SUCCESS",
                    errors,
                )

    # Send consolidated Slack report only after durable DB completion. Monthly
    # mode changes formatting, not the six-account completion contract.
    if reports:
        if args.simulate:
            logger.info("🔕 [SIMULATE] Slack 리포트 전송 생략")
        elif errors:
            logger.warning("Supabase 적재 실패로 COMPLETE Slack 리포트를 생략합니다")
            mark_delivery_outcome(
                evidence_store,
                evidence.run_id,
                "slack",
                "SKIPPED",
                errors,
            )
        else:
            logger.info("Slack 리포트 전송 중...")
            try:
                if args.monthly:
                    logger.info("--monthly 플래그 감지: 월간 리포트 모드로 실행")
                success = slack.send_multi_account_report(reports, is_monthly=is_monthly)
                if success:
                    logger.info("✅ Slack 리포트 전송 성공")
                    mark_delivery_outcome(
                        evidence_store,
                        evidence.run_id,
                        "slack",
                        "SUCCESS",
                        errors,
                    )
                else:
                    logger.warning("⚠️ Slack 리포트 전송 실패 (웹훅 설정 확인 필요)")
                    slack_error = "Slack 정상 리포트 전송 실패"
                    errors.append(slack_error)
                    mark_delivery_outcome(
                        evidence_store,
                        evidence.run_id,
                        "slack",
                        "FAILED",
                        errors,
                        error=slack_error,
                    )
            except Exception as e:
                safe_error = safe_error_message(e)
                logger.error("Slack 전송 중 오류: %s", safe_error)
                errors.append(f"Slack 정상 리포트 전송 오류: {safe_error}")
                mark_delivery_outcome(
                    evidence_store,
                    evidence.run_id,
                    "slack",
                    "FAILED",
                    errors,
                    error=e,
                )

    # Send error notifications if any
    if errors:
        logger.warning(f"⚠️ 총 {len(errors)}개의 오류 발생")
        if not args.simulate:
            for error in errors:
                try:
                    slack.send_error_notification("Daily Report", error)
                except Exception as e:
                    logger.error(
                        "에러 알림 전송 실패: %s", safe_error_message(e)
                    )

    # Print summary
    logger.info("=" * 60)
    logger.info("리포트 요약")
    logger.info("-" * 60)

    total_position_value = sum(r.get("position_value", 0) for r in reports.values())
    total_cash = sum(r.get("cash_balance", 0) for r in reports.values())
    total_value = sum(r.get("total_value", 0) for r in reports.values())
    total_positions = sum(r.get("num_positions", 0) for r in reports.values())
    total_pnl_7d = sum((r.get("pnl_7d") or {}).get("total_pnl") or 0 for r in reports.values())
    total_pnl_30d = sum((r.get("pnl_30d") or {}).get("total_pnl") or 0 for r in reports.values())

    logger.info(
        f"총 포트폴리오 가치: ${total_value:.2f} (포지션: ${total_position_value:.2f} + Cash: ${total_cash:.2f})"
    )
    logger.info(f"총 포지션 수: {total_positions}개")
    logger.info(f"7일 P&L: ${total_pnl_7d:+.2f}")
    logger.info(f"30일 P&L: ${total_pnl_30d:+.2f}")

    for account_name, summary in reports.items():
        pnl_7d_value = (summary.get("pnl_7d") or {}).get("total_pnl")
        pnl_7d_text = "N/A" if pnl_7d_value is None else f"${pnl_7d_value:+.2f}"
        logger.info(
            f"  • {account_name}: ${summary.get('total_value', 0):.2f} "
            f"(포지션: ${summary.get('position_value', 0):.2f}, "
            f"Cash: ${summary.get('cash_balance', 0):.2f}, "
            f"7d: {pnl_7d_text})"
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
        logger.critical("예상치 못한 오류: %s", safe_error_message(e))
        sys.exit(1)
