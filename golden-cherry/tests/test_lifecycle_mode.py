"""수명주기 모드가 Cherry의 신규 주문 경로를 차단하는지 검증."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import polybot.bot as bot_module
from polybot.bot import PolymarketBot
from polybot.config import TradingConfig


def _build_bot(monkeypatch, tmp_path, lifecycle_mode: str, holdings):
    scanner = MagicMock()
    scanner.scan_buy_candidates.side_effect = AssertionError(
        "비활성 진입 경로에서 스캔하면 안 됩니다"
    )

    trader = MagicMock()
    trader.execute_sell.return_value = False
    trader.reclassify_unconfirmed_live_buy.return_value = False
    trader.reconcile_pending_buy.return_value = False
    trader.reconcile_pending_sell.return_value = False
    trader.execute_buy.side_effect = AssertionError(
        "비활성 진입 경로에서 매수하면 안 됩니다"
    )

    repo = MagicMock()
    repo.get_holding_trades.return_value = holdings
    repo.get_pending_buy_trades.return_value = []
    repo.get_pending_sell_trades.return_value = []
    repo.get_stats.return_value = {
        "pending_buy": 0,
        "holding": len(holdings),
        "pending_sell": 0,
        "quarantined": 0,
        "total_pnl": 0.0,
        "managed_open_position_count": len(holdings),
        "managed_open_notional_usdc": 0.0,
        "untracked_buy_reservation_count": 0,
        "untracked_buy_reservation_notional_usdc": 0.0,
        "untracked_buy_unknown_outcome_count": 0,
        "untracked_buy_reconciliation_count": 0,
        "reserved_position_count": len(holdings),
        "reserved_open_notional_usdc": 0.0,
    }

    session = MagicMock()
    monkeypatch.setattr(bot_module, "MarketScanner", lambda *args, **kwargs: scanner)
    monkeypatch.setattr(bot_module, "Trader", lambda *args, **kwargs: trader)
    monkeypatch.setattr(bot_module, "TradeRepository", lambda _: repo)

    bot = object.__new__(PolymarketBot)
    bot.config = SimpleNamespace(
        trading=TradingConfig(lifecycle_mode=lifecycle_mode),
        simulation_mode=False,
        db_path=tmp_path / "trades.db",
    )
    bot.Session = lambda: session
    bot.gamma = object()
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
    assert stats["checked_holdings"] == 1
    assert stats["buy_candidates"] == 0
    assert stats["bought"] == 0
    trader.execute_sell.assert_called_once_with(trade)
    scanner.scan_buy_candidates.assert_not_called()
    trader.execute_buy.assert_not_called()
    session.close.assert_called_once()


def test_archive_only_never_touches_order_paths(monkeypatch, tmp_path):
    bot, scanner, trader, repo, session = _build_bot(
        monkeypatch, tmp_path, "archive_only", []
    )
    repo.get_holding_trades.side_effect = AssertionError(
        "archive_only에서 보유 포지션 주문 경로를 읽으면 안 됩니다"
    )

    stats = bot.run_cycle()

    assert stats["lifecycle_mode"] == "archive_only"
    assert stats["checked_holdings"] == 0
    repo.get_holding_trades.assert_not_called()
    scanner.scan_buy_candidates.assert_not_called()
    trader.execute_sell.assert_not_called()
    trader.execute_buy.assert_not_called()
    session.close.assert_called_once()


def test_active_keeps_existing_entry_path(monkeypatch, tmp_path):
    bot, scanner, trader, repo, session = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    trader.get_entry_guard.return_value = {
        "entry_allowed": True,
        "exact_economic_pnl_usdc": 0.0,
        "exact_confirmed_sell_pnl_usdc": 0.0,
        "exact_proven_resolution_settlement_usdc": 0.0,
        "drawdown_floor_usdc": -30.0,
        "unknown_buy_evidence_count": 0,
        "incomplete_fee_evidence_count": 0,
        "resolution_evidence_gap_count": 0,
        "blockers": [],
    }
    candidate = {"condition_id": "market-1"}
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    repo.is_already_traded.return_value = False
    trader.execute_buy.side_effect = None
    trader.execute_buy.return_value = True

    stats = bot.run_cycle()

    assert stats["lifecycle_mode"] == "active"
    assert stats["buy_candidates"] == 1
    assert stats["bought"] == 1
    scanner.scan_buy_candidates.assert_called_once_with()
    trader.execute_buy.assert_called_once_with(candidate)
    session.close.assert_called_once()


def test_active_drawdown_guard_blocks_scan_after_holding_management(
    monkeypatch, tmp_path
):
    trade = SimpleNamespace(id=1, token_id="token-1")
    pending_buy = SimpleNamespace(id=2, token_id="token-buy")
    pending_sell = SimpleNamespace(id=3, token_id="token-sell")
    bot, scanner, trader, repo, session = _build_bot(
        monkeypatch, tmp_path, "active", [trade]
    )
    repo.get_pending_buy_trades.return_value = [pending_buy]
    repo.get_pending_sell_trades.return_value = [pending_sell]
    trader.get_entry_guard.return_value = {
        "entry_allowed": False,
        "exact_economic_pnl_usdc": -145.16,
        "exact_confirmed_sell_pnl_usdc": -44.85,
        "exact_proven_resolution_settlement_usdc": -100.31,
        "drawdown_floor_usdc": -30.0,
        "unknown_buy_evidence_count": 14,
        "incomplete_fee_evidence_count": 0,
        "resolution_evidence_gap_count": 0,
        "blockers": [
            "exact_economic_drawdown_floor_breached",
            "unknown_buy_evidence",
        ],
    }

    stats = bot.run_cycle()

    trader.execute_sell.assert_called_once_with(trade)
    trader.reconcile_pending_buy.assert_called_once_with(pending_buy)
    trader.reconcile_pending_sell.assert_called_once_with(pending_sell)
    scanner.scan_buy_candidates.assert_not_called()
    trader.execute_buy.assert_not_called()
    assert stats["entry_guard"]["entry_allowed"] is False
    assert stats["checked_holdings"] == 1
    session.close.assert_called_once()
