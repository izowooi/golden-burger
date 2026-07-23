"""Lifecycle modes must gate every Queen order path."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import polybot.bot as bot_module
from polybot.bot import PolymarketBot
from polybot.config import TradingConfig


def _build_bot(monkeypatch, tmp_path, mode: str, holdings):
    scanner = MagicMock()
    scanner.fetch_markets.return_value = [{"conditionId": "market-1"}]
    scanner.save_market_snapshots.return_value = 1
    scanner.scan_buy_candidates.side_effect = AssertionError(
        "inactive entry path must never scan"
    )
    trader = MagicMock()
    trader.execute_sell.return_value = False
    trader.execute_buy.side_effect = AssertionError(
        "inactive entry path must never buy"
    )
    repo = MagicMock()
    repo.get_pending_sell_trades.return_value = []
    repo.get_holding_trades.return_value = holdings
    repo.get_stats.return_value = {
        "holding": len(holdings),
        "resolved": 0,
        "expired": 0,
        "unfilled": 0,
        "quarantined": 0,
        "total_pnl": 0.0,
    }
    session = MagicMock()

    monkeypatch.setattr(bot_module, "MarketScanner", lambda *args, **kwargs: scanner)
    monkeypatch.setattr(bot_module, "Trader", lambda *args, **kwargs: trader)
    monkeypatch.setattr(bot_module, "TradeRepository", lambda _session: repo)

    gamma = MagicMock()
    gamma.get_all_tradable_markets.return_value = [{"conditionId": "market-1"}]
    bot = object.__new__(PolymarketBot)
    bot.config = SimpleNamespace(
        trading=TradingConfig(lifecycle_mode=mode),
        simulation_mode=False,
        db_path=tmp_path / "trades.db",
    )
    bot.Session = lambda: session
    bot.gamma = gamma
    bot.history = object()
    bot.clob = SimpleNamespace(midpoint_snapshot=MagicMock(return_value=nullcontext()))
    bot._log_strategy_config = lambda: None
    return bot, scanner, trader, repo, session, gamma


def test_close_only_archives_and_checks_existing_positions_without_entry(
    monkeypatch, tmp_path
):
    trade = SimpleNamespace(id=1, token_id="yes-token")
    bot, scanner, trader, repo, session, gamma = _build_bot(
        monkeypatch, tmp_path, "close_only", [trade]
    )

    stats = bot.run_cycle()

    assert stats["lifecycle_mode"] == "close_only"
    assert stats["snapshots_saved"] == 1
    assert stats["checked_holdings"] == 1
    assert stats["buy_candidates"] == 0
    assert stats["bought"] == 0
    scanner.fetch_markets.assert_called_once_with()
    gamma.get_all_tradable_markets.assert_not_called()
    repo.get_pending_sell_trades.assert_called_once_with()
    trader.execute_sell.assert_called_once_with(trade)
    scanner.scan_buy_candidates.assert_not_called()
    trader.execute_buy.assert_not_called()
    repo.cleanup_old_snapshots.assert_called_once_with(days=60)
    session.close.assert_called_once()


def test_archive_only_persists_research_without_reading_or_writing_orders(
    monkeypatch, tmp_path
):
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "archive_only", []
    )
    repo.get_holding_trades.side_effect = AssertionError(
        "archive_only must not enter an order lifecycle"
    )

    stats = bot.run_cycle()

    assert stats["snapshots_saved"] == 1
    assert stats["checked_holdings"] == 0
    assert stats["sold"] == 0
    assert stats["bought"] == 0
    scanner.save_market_snapshots.assert_called_once()
    repo.get_holding_trades.assert_not_called()
    repo.get_pending_sell_trades.assert_not_called()
    scanner.scan_buy_candidates.assert_not_called()
    trader.execute_sell.assert_not_called()
    trader.execute_buy.assert_not_called()
    repo.cleanup_old_snapshots.assert_called_once_with(days=60)
    session.close.assert_called_once()


def test_active_keeps_entry_path_and_event_guard(monkeypatch, tmp_path):
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {"condition_id": "market-1", "event_id": "event-1"}
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    repo.can_reenter.return_value = (True, "ok")
    trader.execute_buy.side_effect = None
    trader.execute_buy.return_value = 1

    stats = bot.run_cycle()

    assert stats["buy_candidates"] == 1
    assert stats["bought"] == 1
    scanner.scan_buy_candidates.assert_called_once()
    trader.execute_buy.assert_called_once_with(candidate)
    session.close.assert_called_once()


def test_close_only_reconciles_pending_sell_before_holding_checks(
    monkeypatch, tmp_path
):
    pending = SimpleNamespace(id=9, token_id="yes-token")
    completed = SimpleNamespace(id=9, token_id="yes-token")
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "close_only", []
    )
    repo.get_pending_sell_trades.return_value = [pending]
    trader.reconcile_pending_sell.return_value = True
    repo.get_by_id.return_value = completed

    stats = bot.run_cycle()

    assert stats["pending_sells_checked"] == 1
    assert stats["sold"] == 1
    trader.reconcile_pending_sell.assert_called_once_with(pending)
    repo.append_trade_to_csv.assert_called_once_with(completed, tmp_path)
    scanner.scan_buy_candidates.assert_not_called()
    session.close.assert_called_once()
