"""End-to-end orchestration guards around incomplete account collection."""

import importlib.util
import logging
import os
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from polybot_reporter.account_config import AccountConfig
from polybot_reporter.contracts import safe_error_message
from polybot_reporter.storage.evidence_store import DailyEvidenceStore, EvidenceStoreError

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "daily_report.py"
SPEC = importlib.util.spec_from_file_location("daily_report_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
daily_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(daily_report)


ACCOUNT_ENV = {
    "ACCOUNT_1_NAME": "golden-apple",
    "ACCOUNT_1_ADDRESS": "0x1",
    "ACCOUNT_2_NAME": "golden-banana",
    "ACCOUNT_2_ADDRESS": "0x2",
    "ACCOUNT_3_NAME": "golden-cherry",
    "ACCOUNT_3_ADDRESS": "0x3",
    "ACCOUNT_4_NAME": "golden-apple",
    "ACCOUNT_4_ADDRESS": "0x4",
    "ACCOUNT_5_NAME": "golden-eco",
    "ACCOUNT_5_ADDRESS": "0x5",
    "ACCOUNT_6_NAME": "golden-fox",
    "ACCOUNT_6_ADDRESS": "0x6",
}


class FakeWriter:
    def check_connection(self, configured_names):
        assert len(configured_names) == 6
        return 6


class FailingDataClient:
    def get_portfolio_summary(self, address):
        if address == "0x6":
            raise RuntimeError("upstream timeout")
        return {
            "positions": [],
            "position_value": 0.0,
            "cash_balance": 10.0,
            "total_value": 10.0,
            "num_positions": 0,
            "pnl_7d": {"total_pnl": None},
            "pnl_30d": {"total_pnl": None},
        }


class SuccessfulDataClient:
    def get_portfolio_summary(self, _address):
        return {
            "positions": [],
            "position_value": 0.0,
            "cash_balance": 10.0,
            "total_value": 10.0,
            "num_positions": 0,
            "pnl_7d": {"total_pnl": None},
            "pnl_30d": {"total_pnl": None},
        }


class CompleteWriter(FakeWriter):
    writes = 0

    def get_period_pnl(self, _reports):
        return {}

    def write_daily_snapshot(self, _reports):
        type(self).writes += 1
        return SimpleNamespace(report_date="2026-07-11", account_count=6, total_value=60.0)


class CapturingSlack:
    def __init__(self):
        self.normal_reports = []
        self.errors = []

    def send_multi_account_report(self, reports, is_monthly=False):
        self.normal_reports.append((reports, is_monthly))
        return True

    def send_error_notification(self, account_name, error):
        self.errors.append((account_name, error))
        return True


def test_collection_failure_emits_only_error_and_failed_evidence(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("ACCOUNT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in ACCOUNT_ENV.items():
        monkeypatch.setenv(key, value)

    slack = CapturingSlack()
    evidence_path = tmp_path / "daily-evidence.sqlite3"
    monkeypatch.setattr(
        daily_report,
        "parse_args",
        lambda: SimpleNamespace(command="run", simulate=False, monthly=False),
    )
    monkeypatch.setattr(daily_report, "SupabasePortfolioWriter", FakeWriter)
    monkeypatch.setattr(daily_report, "DataAPIClient", FailingDataClient)
    monkeypatch.setattr(daily_report, "SlackNotifier", lambda: slack)
    monkeypatch.setattr(
        daily_report,
        "DailyEvidenceStore",
        lambda: DailyEvidenceStore(evidence_path),
    )

    with pytest.raises(SystemExit) as exit_info:
        daily_report.main()

    assert exit_info.value.code == 1
    assert slack.normal_reports == []
    assert len(slack.errors) == 1
    assert "golden-fox" in slack.errors[0][1]
    with sqlite3.connect(evidence_path) as connection:
        run = connection.execute(
            "SELECT status, expected_account_count, observed_account_count "
            "FROM evidence_report_runs"
        ).fetchone()
        delivery = connection.execute(
            "SELECT supabase_status, slack_status, delivery_status "
            "FROM evidence_delivery_status"
        ).fetchone()
    assert run == ("FAILED", 6, 5)
    assert delivery == ("SKIPPED", "SKIPPED", "NOT_ATTEMPTED")


def test_fetch_failure_redacts_wallet_from_payload_and_logs(caplog):
    wallet = "0x" + "a" * 40

    class LeakyClient:
        def get_portfolio_summary(self, _address):
            raise RuntimeError(f"upstream URL https://example.invalid/?user={wallet}")

    with caplog.at_level(logging.ERROR):
        report = daily_report.fetch_portfolio_report(
            LeakyClient(), AccountConfig("golden-fox", wallet)
        )

    assert set(report) == {"error"}
    assert wallet not in report["error"]
    assert wallet not in caplog.text
    assert "[REDACTED_CHAIN_ID]" in report["error"]


@pytest.mark.parametrize("identifier_length", [40, 64])
def test_safe_error_redacts_common_chain_identifier_lengths(identifier_length):
    identifier = "0x" + "b" * identifier_length

    message = safe_error_message(f"request failed for {identifier}")

    assert identifier not in message
    assert message.endswith("[REDACTED_CHAIN_ID]")


def test_safe_error_redacts_credentials_and_caps_length():
    secret = "sb_" + "secret_" + "this_must_not_escape"
    message = safe_error_message(
        f"api_key={secret} Authorization: Bearer abc.def " + "x" * 1_000
    )

    assert secret not in message
    assert "abc.def" not in message
    assert "[REDACTED]" in message
    assert message.endswith("…[TRUNCATED]")
    assert len(message) <= 520


@pytest.mark.parametrize(
    ("message", "forbidden"),
    [
        ("password='fixture-password-value'", "fixture-password-value"),
        (
            "postgresql+psycopg"
            + "://fixture_user:fixture_dsn_password@example.invalid/db",
            "fixture_dsn_password",
        ),
        ("gh" + "p_" + "A" * 32, "A" * 32),
        ("AK" + "IA" + "B" * 16, "B" * 16),
        ("sk-" + "proj-" + "C" * 32, "C" * 32),
        ('"Authorization": "Basic ' + "RklYVFVSRQ==" + '"', "RklYVFVSRQ=="),
        (
            "eyJ" + "fixtureheader" + "." + "fixturepayload" + "." + "fixturesignature",
            "fixturesignature",
        ),
        ("aws_secret_access_key='fixture-aws-secret'", "fixture-aws-secret"),
    ],
)
def test_safe_error_redacts_common_bare_and_labeled_secret_shapes(message, forbidden):
    sanitized = safe_error_message(message)

    assert forbidden not in sanitized
    assert "REDACTED" in sanitized


def test_slack_transport_failure_still_writes_db_but_exits_one(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("ACCOUNT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in ACCOUNT_ENV.items():
        monkeypatch.setenv(key, value)

    class FailingSlack(CapturingSlack):
        def send_multi_account_report(self, reports, is_monthly=False):
            self.normal_reports.append((reports, is_monthly))
            return False

    slack = FailingSlack()
    CompleteWriter.writes = 0
    evidence_path = tmp_path / "daily-evidence.sqlite3"
    monkeypatch.setattr(
        daily_report,
        "parse_args",
        lambda: SimpleNamespace(command="run", simulate=False, monthly=False),
    )
    monkeypatch.setattr(daily_report, "SupabasePortfolioWriter", CompleteWriter)
    monkeypatch.setattr(daily_report, "DataAPIClient", SuccessfulDataClient)
    monkeypatch.setattr(daily_report, "SlackNotifier", lambda: slack)
    monkeypatch.setattr(
        daily_report,
        "DailyEvidenceStore",
        lambda: DailyEvidenceStore(evidence_path),
    )

    with pytest.raises(SystemExit) as exit_info:
        daily_report.main()

    assert exit_info.value.code == 1
    assert CompleteWriter.writes == 1
    assert len(slack.normal_reports) == 1
    assert any("Slack 정상 리포트 전송 실패" in error for _, error in slack.errors)
    with sqlite3.connect(evidence_path) as connection:
        delivery = connection.execute(
            "SELECT supabase_status, slack_status, delivery_status "
            "FROM evidence_delivery_status"
        ).fetchone()
    assert delivery == ("SUCCESS", "FAILED", "FAILED")


def test_atomic_db_failure_never_emits_complete_slack(monkeypatch, tmp_path, caplog):
    for key in list(os.environ):
        if key.startswith("ACCOUNT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in ACCOUNT_ENV.items():
        monkeypatch.setenv(key, value)

    secret = "sb_" + "secret_" + "atomic_failure_must_be_redacted"

    class FailingSnapshotWriter(FakeWriter):
        def get_period_pnl(self, _reports):
            return {}

        def write_daily_snapshot(self, _reports):
            raise RuntimeError(f"RPC Authorization={secret}")

    slack = CapturingSlack()
    evidence_path = tmp_path / "daily-evidence.sqlite3"
    monkeypatch.setattr(
        daily_report,
        "parse_args",
        lambda: SimpleNamespace(command="run", simulate=False, monthly=False),
    )
    monkeypatch.setattr(daily_report, "SupabasePortfolioWriter", FailingSnapshotWriter)
    monkeypatch.setattr(daily_report, "DataAPIClient", SuccessfulDataClient)
    monkeypatch.setattr(daily_report, "SlackNotifier", lambda: slack)
    monkeypatch.setattr(
        daily_report,
        "DailyEvidenceStore",
        lambda: DailyEvidenceStore(evidence_path),
    )

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exit_info:
        daily_report.main()

    assert exit_info.value.code == 1
    assert slack.normal_reports == []
    assert len(slack.errors) == 1
    assert secret not in caplog.text
    assert secret not in slack.errors[0][1]
    with sqlite3.connect(evidence_path) as connection:
        delivery = connection.execute(
            "SELECT supabase_status, slack_status, delivery_status "
            "FROM evidence_delivery_status"
        ).fetchone()
    assert delivery == ("FAILED", "SKIPPED", "FAILED")


def test_success_requires_both_db_and_slack_delivery_evidence(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("ACCOUNT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in ACCOUNT_ENV.items():
        monkeypatch.setenv(key, value)

    slack = CapturingSlack()
    evidence_path = tmp_path / "daily-evidence.sqlite3"
    monkeypatch.setattr(
        daily_report,
        "parse_args",
        lambda: SimpleNamespace(command="run", simulate=False, monthly=False),
    )
    monkeypatch.setattr(daily_report, "SupabasePortfolioWriter", CompleteWriter)
    monkeypatch.setattr(daily_report, "DataAPIClient", SuccessfulDataClient)
    monkeypatch.setattr(daily_report, "SlackNotifier", lambda: slack)
    monkeypatch.setattr(
        daily_report,
        "DailyEvidenceStore",
        lambda: DailyEvidenceStore(evidence_path),
    )

    daily_report.main()

    assert len(slack.normal_reports) == 1
    with sqlite3.connect(evidence_path) as connection:
        collection = connection.execute(
            "SELECT status FROM evidence_report_runs"
        ).fetchone()
        delivery = connection.execute(
            "SELECT supabase_status, slack_status, delivery_status "
            "FROM evidence_delivery_status"
        ).fetchone()
    assert collection == ("COMPLETE",)
    assert delivery == ("SUCCESS", "SUCCESS", "COMPLETE")


@pytest.mark.parametrize("command", ["check-supabase", "run"])
def test_supabase_preflight_exception_is_redacted_on_every_outer_path(
    monkeypatch, caplog, command
):
    for key in list(os.environ):
        if key.startswith("ACCOUNT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in ACCOUNT_ENV.items():
        monkeypatch.setenv(key, value)

    secret = "sb_" + "secret_" + "preflight_must_be_redacted"

    class LeakyPreflightWriter:
        def __init__(self):
            raise RuntimeError(f"transport leaked {secret}")

    monkeypatch.setattr(
        daily_report,
        "parse_args",
        lambda: SimpleNamespace(command=command, simulate=False, monthly=False),
    )
    monkeypatch.setattr(daily_report, "SupabasePortfolioWriter", LeakyPreflightWriter)

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exit_info:
        daily_report.main()

    assert exit_info.value.code == 1
    assert secret not in caplog.text
    assert "[REDACTED_SUPABASE_SECRET]" in caplog.text


def test_chained_evidence_exception_traceback_cannot_leak_secret(monkeypatch, caplog):
    for key in list(os.environ):
        if key.startswith("ACCOUNT_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in ACCOUNT_ENV.items():
        monkeypatch.setenv(key, value)

    secret = "fixture-evidence-password-must-not-log"

    class LeakyEvidenceStore:
        def record_run(self, *_args, **_kwargs):
            try:
                raise RuntimeError(f"password={secret}")
            except RuntimeError as error:
                raise EvidenceStoreError("sanitized outer failure") from error

    slack = CapturingSlack()
    monkeypatch.setattr(
        daily_report,
        "parse_args",
        lambda: SimpleNamespace(command="run", simulate=False, monthly=False),
    )
    monkeypatch.setattr(daily_report, "SupabasePortfolioWriter", CompleteWriter)
    monkeypatch.setattr(daily_report, "DataAPIClient", SuccessfulDataClient)
    monkeypatch.setattr(daily_report, "SlackNotifier", lambda: slack)
    monkeypatch.setattr(daily_report, "DailyEvidenceStore", LeakyEvidenceStore)

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit):
        daily_report.main()

    assert secret not in caplog.text
    assert secret not in str(slack.errors)


def test_financial_log_file_is_mode_0600():
    assert stat.S_IMODE(daily_report.log_file.stat().st_mode) == 0o600


def test_log_setup_fails_if_mode_cannot_be_enforced(monkeypatch, tmp_path):
    def fail_chmod(*_args):
        raise OSError("fixture chmod failure")

    monkeypatch.setattr(daily_report.os, "fchmod", fail_chmod)

    with pytest.raises(RuntimeError, match="0600"):
        daily_report.secure_private_file(tmp_path / "report.log", "test log")


def test_log_setup_rejects_symlink_without_chmodding_target(tmp_path):
    target = tmp_path / "target.log"
    target.write_text("fixture", encoding="utf-8")
    target.chmod(0o644)
    link = tmp_path / "report.log"
    link.symlink_to(target)

    with pytest.raises(RuntimeError, match="0600"):
        daily_report.secure_private_file(link, "test log")

    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_fetch_normalizes_money_before_any_log_or_sink(caplog):
    class SlightlyMismatchedClient:
        def get_portfolio_summary(self, _address):
            return {
                "positions": [],
                "position_value": 5.0,
                "cash_balance": 5.0,
                "total_value": 10.02,
                "num_positions": 0,
            }

    with caplog.at_level(logging.INFO):
        report = daily_report.fetch_portfolio_report(
            SlightlyMismatchedClient(), AccountConfig("golden-fox", "0xfixture")
        )

    assert (report["total_value"], report["position_value"], report["cash_balance"]) == (
        10.02,
        5.0,
        5.02,
    )
    assert "가치: $10.02" in caplog.text
