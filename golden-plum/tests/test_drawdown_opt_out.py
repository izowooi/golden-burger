import pytest

from polybot.config import load_config
from tests.test_config import _credentials
from tests.test_lifecycle_mode import _build_bot


def test_only_explicit_queen_mlb_loss_guard_opt_out_is_allowed(monkeypatch):
    _credentials(monkeypatch)
    queen = "plum-live-queen-mlb-95-1m-v1"
    assert load_config("config.yaml", queen, simulation_mode=False).trading.drawdown_guard_enabled
    monkeypatch.setenv("POLYBOT_DRAWDOWN_GUARD_ENABLED", "false")
    cfg = load_config("config.yaml", queen, simulation_mode=False)
    assert cfg.trading.drawdown_guard_enabled is False
    assert cfg.trading.max_drawdown_stop == 0.20
    assert cfg.trading.max_positions == 10
    assert cfg.trading.buy_amount_usdc == 5
    with pytest.raises(ValueError, match="Queen MLB"):
        load_config("config.yaml", "plum-live-king-mlb-90-1m-v1", simulation_mode=False)
    with pytest.raises(ValueError, match="Queen MLB"):
        load_config("config.yaml", "plum-live-queen-95-1m-v1", simulation_mode=False)


@pytest.mark.parametrize("enabled", [True, False])
def test_loss_guard_opt_out_retains_loss_evidence_and_other_guards(
    monkeypatch, tmp_path, enabled,
):
    bot, scanner, trader, repo, *_ = _build_bot(monkeypatch, tmp_path, "active", [])
    bot.config.trading.drawdown_guard_enabled = enabled
    repo.get_economic_pnl_guard.return_value.update(
        economic_pnl=-12.73, confirmed_sell_pnl=-12.73, recorded_realized_pnl=-12.73,
    )
    candidate = {"condition_id": "new-condition", "event_id": "new-event"}
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    trader.execute_buy.side_effect = None
    trader.execute_buy.return_value = 1
    stats = bot.run_cycle()
    assert stats["drawdown_guard"]["enabled"] is enabled
    assert stats["drawdown_guard"]["threshold_breached"] is True
    assert stats["drawdown_guard"]["economic_pnl"] == -12.73
    assert stats["drawdown_guard"]["triggered"] is enabled
    assert stats["entry_guard"]["blocked"] is enabled
    assert trader.execute_buy.call_count == (0 if enabled else 1)


def test_disabling_loss_guard_does_not_disable_fill_evidence_guard(monkeypatch, tmp_path):
    bot, scanner, trader, repo, *_ = _build_bot(monkeypatch, tmp_path, "active", [])
    bot.config.trading.drawdown_guard_enabled = False
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = []
    repo.get_economic_pnl_guard.return_value.update(economic_pnl=-12.73, evidence_gaps=1)
    stats = bot.run_cycle()
    assert stats["entry_guard"]["blocked"]
    assert "economic_pnl_execution_evidence_gap" in stats["entry_guard"]["blocking_reasons"]
    trader.execute_buy.assert_not_called()
