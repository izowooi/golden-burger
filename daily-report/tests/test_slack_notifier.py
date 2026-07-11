"""Slack 통합 리포트 포맷과 complete-snapshot guard 검증."""

import logging

import pytest
import requests

from polybot_reporter.notifications.slack_notifier import (
    PORTFOLIO_REPORT_SCHEMA_VERSION,
    SlackNotifier,
)


def make_summary(total=34588.65, position=23140.98, cash=11447.67, pnl_7d=668.20, pnl_30d=None):
    return {
        "total_value": total,
        "position_value": position,
        "cash_balance": cash,
        "num_positions": 3,
        "positions": [{}, {}, {}],
        "pnl_7d": {"total_pnl": pnl_7d},
        "pnl_30d": {"total_pnl": pnl_30d},
    }


def capture_payload(monkeypatch, notifier):
    captured = {}

    def fake_send(text, attachments=None, blocks=None):
        captured["text"] = text
        captured["attachments"] = attachments
        return True

    monkeypatch.setattr(notifier, "send_message", fake_send)
    return captured


def make_reports(**overrides):
    reports = {
        "golden-apple (1)": make_summary(),
        "golden-banana": make_summary(),
        "golden-cherry": make_summary(),
        "golden-apple (2)": make_summary(),
        "golden-eco": make_summary(),
        "golden-fox": make_summary(),
    }
    reports.update(overrides)
    return reports


def account_attachment(captured, author_name):
    return next(
        attachment
        for attachment in captured["attachments"][1:]
        if attachment["author_name"] == author_name
    )


def test_account_block_is_three_lines_without_asset_label(monkeypatch):
    """계좌 블록 = author_name(1줄) + text(금액/손익 2줄). '자산 가치' 문구와 fields 없음."""
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
    captured = capture_payload(monkeypatch, notifier)

    notifier.send_multi_account_report(make_reports())

    account = account_attachment(captured, "GOLDEN-ECO")
    assert account["author_name"] == "GOLDEN-ECO"
    assert "fields" not in account
    assert account["text"] == ("$34588.65 (Position: $23140.98, Cash: $11447.67)\n7d 손익 $+668.20")
    assert "자산 가치" not in str(captured["attachments"])
    assert PORTFOLIO_REPORT_SCHEMA_VERSION in captured["text"]
    assert "COMPLETE" in captured["attachments"][0]["footer"]
    assert "tz=Asia/Seoul" in captured["attachments"][0]["footer"]


def test_new_account_without_history_shows_na(monkeypatch):
    """이력 없는 신규 계좌(7d None)는 N/A로 표기되고 색상은 초록."""
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
    captured = capture_payload(monkeypatch, notifier)

    notifier.send_multi_account_report(make_reports(**{"golden-fox": make_summary(pnl_7d=None)}))

    account = account_attachment(captured, "GOLDEN-FOX")
    assert "7d 손익 N/A" in account["text"]
    assert account["color"] == "#36a64f"


def test_monthly_appends_30d_on_pnl_line(monkeypatch):
    """월간 리포트는 30d 손익을 손익 줄에 덧붙인다 (여전히 3줄)."""
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
    captured = capture_payload(monkeypatch, notifier)

    notifier.send_multi_account_report(
        make_reports(**{"golden-banana": make_summary(pnl_30d=-12.5)}),
        is_monthly=True,
    )

    account = account_attachment(captured, "GOLDEN-BANANA")
    assert account["text"].endswith("· 30d 손익 $-12.50")
    assert account["text"].count("\n") == 1  # 금액 줄 + 손익 줄


def test_rejects_partial_or_failed_report_before_webhook(monkeypatch):
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
    captured = capture_payload(monkeypatch, notifier)
    partial = make_reports()
    del partial["golden-fox"]

    with pytest.raises(ValueError, match="exact set"):
        notifier.send_multi_account_report(partial)

    failed = make_reports()
    failed["golden-fox"]["error"] = "upstream timeout"
    with pytest.raises(ValueError, match="collection error"):
        notifier.send_multi_account_report(failed)

    assert captured == {}


def test_rejects_missing_valuation_field_before_webhook(monkeypatch):
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
    captured = capture_payload(monkeypatch, notifier)
    reports = make_reports()
    del reports["golden-fox"]["position_value"]

    with pytest.raises(ValueError, match="필수 valuation field"):
        notifier.send_multi_account_report(reports)

    assert captured == {}


@pytest.mark.parametrize(
    ("field", "value", "error_pattern"),
    [
        ("total_value", True, "boolean"),
        ("num_positions", 1.5, "실제 integer"),
        ("positions", [{}, {}, 42], "position entry"),
    ],
)
def test_rejects_ambiguous_valuation_types_before_webhook(
    monkeypatch, field, value, error_pattern
):
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
    captured = capture_payload(monkeypatch, notifier)
    reports = make_reports()
    reports["golden-fox"][field] = value

    with pytest.raises(ValueError, match=error_pattern):
        notifier.send_multi_account_report(reports)

    assert captured == {}


def test_transport_error_redacts_webhook_and_tokens(monkeypatch, caplog):
    webhook = "https://hooks.slack.com/services/T000/B000/secret-value"
    notifier = SlackNotifier(webhook_url=webhook)

    def fail_post(*_args, **_kwargs):
        raise requests.ConnectionError(
            f"connection failed: {webhook} Authorization=Bearer top-secret"
        )

    monkeypatch.setattr("polybot_reporter.notifications.slack_notifier.requests.post", fail_post)

    with caplog.at_level(logging.ERROR):
        assert notifier.send_message("test") is False

    assert webhook not in caplog.text
    assert "top-secret" not in caplog.text
    assert "[REDACTED_SLACK_WEBHOOK]" in caplog.text


def test_error_notification_sanitizes_untrusted_error_text(monkeypatch):
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
    captured = capture_payload(monkeypatch, notifier)
    secret = "sb_" + "secret_" + "notification_must_not_escape"

    notifier.send_error_notification("Daily Report", f"transport included {secret}")

    assert secret not in captured["text"]
    assert secret not in str(captured["attachments"])
    assert "[REDACTED_SUPABASE_SECRET]" in captured["text"]
