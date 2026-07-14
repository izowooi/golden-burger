"""수명주기 모드가 신규 주문 경로를 확실히 차단하는지 검증."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import polybot.bot as bot_module
from polybot.bot import PolymarketBot
from polybot.config import TradingConfig


def _build_bot(monkeypatch, tmp_path, lifecycle_mode: str, holdings):
    scanner = MagicMock()
    scanner.save_market_snapshots.return_value = 1
    scanner.scan_buy_candidates.side_effect = AssertionError(
        "비활성 진입 경로에서 스캔하면 안 됩니다"
    )

    trader = MagicMock()
    trader.execute_sell.return_value = False
    trader.execute_buy.side_effect = AssertionError(
        "비활성 진입 경로에서 매수하면 안 됩니다"
    )

    repo = MagicMock()
    repo.get_holding_trades.return_value = holdings
    repo.get_stats.return_value = {
        "holding": len(holdings),
        "expired": 0,
        "total_pnl": 0.0,
    }

    session = MagicMock()
    monkeypatch.setattr(bot_module, "MarketScanner", lambda *args, **kwargs: scanner)
    monkeypatch.setattr(bot_module, "Trader", lambda *args, **kwargs: trader)
    monkeypatch.setattr(bot_module, "TradeRepository", lambda _: repo)

    gamma = MagicMock()
    gamma.get_all_tradable_markets.return_value = [{"condition_id": "market-1"}]

    bot = object.__new__(PolymarketBot)
    bot.config = SimpleNamespace(
        trading=TradingConfig(lifecycle_mode=lifecycle_mode),
        simulation_mode=False,
        db_path=tmp_path / "trades.db",
    )
    bot.Session = lambda: session
    bot.gamma = gamma
    bot.history = object()
    bot.clob = SimpleNamespace(
        midpoint_snapshot=MagicMock(return_value=nullcontext())
    )
    return bot, scanner, trader, repo, session


def test_close_only_checks_exits_but_never_scans_or_buys(monkeypatch, tmp_path):
    trade = SimpleNamespace(id=1, token_id="token-1")
    bot, scanner, trader, repo, session = _build_bot(
        monkeypatch, tmp_path, "close_only", [trade]
    )

    stats = bot.run_cycle()

    assert stats["lifecycle_mode"] == "close_only"
    assert stats["snapshots_saved"] == 1
    assert stats["checked_holdings"] == 1
    assert stats["buy_candidates"] == 0
    assert stats["bought"] == 0
    trader.execute_sell.assert_called_once_with(trade)
    scanner.scan_buy_candidates.assert_not_called()
    trader.execute_buy.assert_not_called()
    repo.cleanup_old_snapshots.assert_called_once()
    session.close.assert_called_once()


def test_archive_only_saves_snapshots_without_touching_orders(monkeypatch, tmp_path):
    bot, scanner, trader, repo, session = _build_bot(
        monkeypatch, tmp_path, "archive_only", []
    )
    repo.get_holding_trades.side_effect = AssertionError(
        "archive_only에서 보유 포지션 주문 경로를 읽으면 안 됩니다"
    )

    stats = bot.run_cycle()

    assert stats["lifecycle_mode"] == "archive_only"
    assert stats["snapshots_saved"] == 1
    assert stats["checked_holdings"] == 0
    repo.get_holding_trades.assert_not_called()
    scanner.scan_buy_candidates.assert_not_called()
    trader.execute_sell.assert_not_called()
    trader.execute_buy.assert_not_called()
    repo.cleanup_old_snapshots.assert_called_once()
    session.close.assert_called_once()


def test_active_keeps_existing_entry_path(monkeypatch, tmp_path):
    bot, scanner, trader, repo, session = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {"condition_id": "market-1"}
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    repo.can_enter.return_value = (True, "ok")
    trader.execute_buy.side_effect = None
    trader.execute_buy.return_value = True

    stats = bot.run_cycle()

    assert stats["lifecycle_mode"] == "active"
    assert stats["buy_candidates"] == 1
    assert stats["bought"] == 1
    scanner.scan_buy_candidates.assert_called_once()
    trader.execute_buy.assert_called_once_with(candidate)
    session.close.assert_called_once()
