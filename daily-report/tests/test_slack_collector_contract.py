"""True producer-payload to collector-parser contract tests."""

import sys
from pathlib import Path

import pytest

from polybot_reporter.notifications.slack_notifier import SlackNotifier

COLLECTOR_SRC = Path(__file__).resolve().parents[2] / "slack-data-collector" / "src"
sys.path.insert(0, str(COLLECTOR_SRC))

from slack_data_collector.portfolio import (  # noqa: E402
    CURRENT_REPORT_SCHEMA_VERSION,
    PortfolioParseError,
    parse_portfolio_message,
)


def summary(total):
    return {
        "positions": [],
        "position_value": total - 4.0,
        "cash_balance": 4.0,
        "total_value": total,
        "num_positions": 0,
        "pnl_7d": {"total_pnl": None},
        "pnl_30d": {"total_pnl": None},
    }


def capture(notifier):
    payload = {}

    def fake_send(text, attachments=None, blocks=None):
        payload.update(text=text, attachments=attachments, blocks=blocks)
        return True

    notifier.send_message = fake_send
    return payload


def slack_message(payload):
    return {
        "type": "message",
        "subtype": "bot_message",
        "ts": "1782211560.242069",
        "text": payload["text"],
        "attachments": payload["attachments"],
    }


def test_current_reporter_payload_round_trips_through_collector():
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook", timezone_name="Asia/Seoul")
    payload = capture(notifier)
    notifier.send_multi_account_report(
        {
            "golden-apple (1)": summary(11.0),
            "golden-banana": summary(12.0),
            "golden-cherry": summary(13.0),
            "golden-apple (2)": summary(14.0),
            "golden-eco": summary(15.0),
            "golden-fox": summary(16.0),
        }
    )

    report = parse_portfolio_message(slack_message(payload))

    assert report is not None
    assert report.schema_version == CURRENT_REPORT_SCHEMA_VERSION
    assert len(report.algorithms) == 6
    assert report.algorithms[-1].account_id == "golden-fox"
    assert report.algorithms[-1].balance.total_value == 16
    assert report.reported_at.endswith("+09:00")


def test_raw_boundary_rounding_still_round_trips_with_exact_display_reconciliation():
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
    payload = capture(notifier)
    reports = {
        "golden-apple (1)": summary(11.0),
        "golden-banana": summary(12.0),
        "golden-cherry": summary(13.0),
        "golden-apple (2)": summary(14.0),
        "golden-eco": summary(15.0),
        "golden-fox": summary(16.0),
    }
    # Raw total differs from position+cash by exactly the allowed $0.02.
    # Independent cent rounding would produce $10.03 != $5.00 + $5.00.
    reports["golden-apple (1)"].update(
        total_value=10.025,
        position_value=5.002,
        cash_balance=5.003,
    )

    notifier.send_multi_account_report(reports)
    report = parse_portfolio_message(slack_message(payload))

    assert report is not None
    first = report.algorithms[0].balance
    assert first.total_value == first.position_value + first.cash_value
    assert report.total.total_value == sum(
        (algorithm.balance.total_value for algorithm in report.algorithms),
        start=0,
    )


def test_reporter_error_payload_is_rejected_by_collector():
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
    payload = capture(notifier)
    notifier.send_error_notification("Daily Report", "golden-fox: upstream timeout")

    with pytest.raises(PortfolioParseError, match="오류 상태"):
        parse_portfolio_message(slack_message(payload))
