"""주문별 대사 오류가 전략 전체를 멈추지 않는지 검증."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import polybot.bot as bot_module
from polybot.bot import PolymarketBot


def _build_bot(monkeypatch, reconciliation):
    audit = MagicMock()
    monkeypatch.setattr(
        bot_module.RunAudit,
        "start",
        MagicMock(return_value=audit),
    )
    bot = object.__new__(PolymarketBot)
    bot.config = SimpleNamespace(job_name="default")
    bot.gamma = SimpleNamespace(
        sweep_attestations=[],
        get_sweep_summaries=MagicMock(return_value=[]),
    )
    bot.clob = SimpleNamespace(
        reconcile_order_ledger=MagicMock(return_value=reconciliation)
    )
    bot.run_cycle = MagicMock(return_value={"bought": 0, "sold": 0})
    return bot, audit


def test_per_order_reconciliation_errors_are_locally_quarantined(
    monkeypatch, caplog
):
    reconciliation = {
        "checked": 43,
        "fills": 16,
        "completed": 0,
        "legacy_unavailable": 0,
        "errors": 8,
    }
    bot, audit = _build_bot(monkeypatch, reconciliation)

    bot.run()

    bot.run_cycle.assert_called_once_with()
    audit.fail.assert_not_called()
    audit.succeed.assert_called_once()
    stats = audit.succeed.call_args.args[0]
    assert stats["order_reconciliation"] == reconciliation
    assert "해당 token/side 신규 주문만 격리" in caplog.text


def test_reconciliation_queue_failure_still_fails_closed(monkeypatch):
    bot, audit = _build_bot(monkeypatch, {})
    bot.clob.reconcile_order_ledger.side_effect = RuntimeError("ledger unavailable")

    with pytest.raises(RuntimeError, match="ledger unavailable"):
        bot.run()

    bot.run_cycle.assert_not_called()
    audit.fail.assert_called_once()
    audit.succeed.assert_not_called()
