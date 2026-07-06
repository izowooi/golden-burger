"""Slack 통합 리포트 포맷 검증 - 계좌당 3줄 (이름 / 금액 / 손익)."""
from polybot_reporter.notifications.slack_notifier import SlackNotifier


def make_summary(total=34588.65, position=23140.98, cash=11447.67, pnl_7d=668.20, pnl_30d=None):
    return {
        "total_value": total,
        "position_value": position,
        "cash_balance": cash,
        "num_positions": 3,
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


def test_account_block_is_three_lines_without_asset_label(monkeypatch):
    """계좌 블록 = author_name(1줄) + text(금액/손익 2줄). '자산 가치' 문구와 fields 없음."""
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
    captured = capture_payload(monkeypatch, notifier)

    notifier.send_multi_account_report({"golden-eco": make_summary()})

    account = captured["attachments"][1]  # [0]은 전체 요약
    assert account["author_name"] == "GOLDEN-ECO"
    assert "fields" not in account
    assert account["text"] == (
        "$34588.65 (Position: $23140.98, Cash: $11447.67)\n7d 손익 $+668.20"
    )
    assert "자산 가치" not in str(captured["attachments"])


def test_new_account_without_history_shows_na(monkeypatch):
    """이력 없는 신규 계좌(7d None)는 N/A로 표기되고 색상은 초록."""
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
    captured = capture_payload(monkeypatch, notifier)

    notifier.send_multi_account_report({"golden-fox": make_summary(pnl_7d=None)})

    account = captured["attachments"][1]
    assert "7d 손익 N/A" in account["text"]
    assert account["color"] == "#36a64f"


def test_monthly_appends_30d_on_pnl_line(monkeypatch):
    """월간 리포트는 30d 손익을 손익 줄에 덧붙인다 (여전히 3줄)."""
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
    captured = capture_payload(monkeypatch, notifier)

    notifier.send_multi_account_report(
        {"golden-banana": make_summary(pnl_30d=-12.5)}, is_monthly=True
    )

    account = captured["attachments"][1]
    assert account["text"].endswith("· 30d 손익 $-12.50")
    assert account["text"].count("\n") == 1  # 금액 줄 + 손익 줄
