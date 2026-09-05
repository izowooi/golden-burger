"""Lifecycle modes must gate every Papaya order path."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import polybot.bot as bot_module
import pytest
from polybot.api.clob_client import PreSubmissionContractError
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
    trader.reconcile_pending_buy.return_value = False
    trader.reconcile_pending_sell.return_value = False
    trader.recover_orphan_buys.return_value = {
        "checked": 0,
        "recovered": 0,
        "evidence_gaps": 0,
        "identity_gaps": 0,
        "duplicate_token_submissions": 0,
    }
    trader.execute_buy.side_effect = AssertionError(
        "inactive entry path must never buy"
    )
    trader.last_entry_may_have_reached_venue = True
    repo = MagicMock()
    repo.get_pending_buy_trades.return_value = []
    repo.get_isolated_buy_trades.return_value = []
    repo.get_pending_sell_trades.return_value = []
    repo.get_isolated_stop_sell_trades.return_value = []
    repo.get_holding_trades.return_value = holdings
    repo.get_stats.return_value = {
        "holding": len(holdings),
        "pending_buy": 0,
        "pending_sell": 0,
        "resolved": 0,
        "expired": 0,
        "unfilled": 0,
        "quarantined": 0,
        "total_pnl": 0.0,
    }
    repo.get_economic_pnl_guard.return_value = {
        "economic_pnl": 0.0,
        "recorded_realized_pnl": 0.0,
        "recorded_settlement_pnl": 0.0,
        "confirmed_sell_pnl": 0.0,
        "proven_resolution_pnl": 0.0,
        "execution_adjustment_pnl": 0.0,
        "invalidated_settlement_pnl": 0.0,
        "execution_override_count": 0,
        "evidence_gaps": 0,
    }
    repo.get_entry_capacity_state.return_value = {
        "open_positions": len(holdings),
        "untracked_buy_reservations": 0,
        "total_reserved": len(holdings),
    }
    repo.get_open_buy_evidence_gap_count.return_value = 0
    repo.get_quarantine_state.return_value = {
        "total": 0,
        "isolated_buy": 0,
        "isolated_stop_sell": 0,
        "blocking": 0,
    }
    session = MagicMock()

    monkeypatch.setattr(bot_module, "MarketScanner", lambda *args, **kwargs: scanner)
    monkeypatch.setattr(bot_module, "Trader", lambda *args, **kwargs: trader)
    monkeypatch.setattr(bot_module, "TradeRepository", lambda _session: repo)

    gamma = MagicMock()
    gamma.get_all_tradable_markets.return_value = [{"conditionId": "market-1"}]
    gamma.last_sweep_attestation = None
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
    repo.get_isolated_buy_trades.assert_called_once_with()
    repo.get_isolated_stop_sell_trades.assert_called_once_with()
    repo.get_pending_buy_trades.assert_called_once_with()
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
    repo.get_isolated_buy_trades.assert_not_called()
    repo.get_isolated_stop_sell_trades.assert_not_called()
    repo.get_pending_buy_trades.assert_not_called()
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


def test_active_caps_one_cycle_at_five_new_positions(monkeypatch, tmp_path):
    bot, scanner, trader, _repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidates = [
        {"condition_id": f"market-{index}", "event_id": f"event-{index}"}
        for index in range(7)
    ]
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = candidates
    trader.execute_buy.side_effect = [1, 2, 3, 4, 5]

    stats = bot.run_cycle()

    assert stats["buy_candidates"] == 7
    assert stats["bought"] == 5
    assert stats["entry_guard"]["new_positions_per_cycle_limit"] == 5
    assert stats["entry_guard"]["new_notional_per_cycle_limit_usdc"] == 25
    assert trader.execute_buy.call_count == 5
    assert [
        call.args[0] for call in trader.execute_buy.call_args_list
    ] == candidates[:5]
    session.close.assert_called_once()


def test_active_never_exceeds_remaining_account_capacity(monkeypatch, tmp_path):
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidates = [
        {"condition_id": f"market-{index}", "event_id": f"event-{index}"}
        for index in range(5)
    ]
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = candidates
    repo.get_entry_capacity_state.return_value = {
        "open_positions": 18,
        "untracked_buy_reservations": 0,
        "total_reserved": 18,
    }
    trader.execute_buy.side_effect = [1, 2]

    stats = bot.run_cycle()

    assert stats["entry_guard"]["capacity_remaining"] == 0
    assert stats["bought"] == 0
    assert trader.execute_buy.call_count == 0
    session.close.assert_called_once()


def test_active_marks_pre_submission_contract_error_retryable_and_continues_cycle(
    monkeypatch,
    tmp_path,
):
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {
        "condition_id": "market-1",
        "event_id": "event-1",
        "entry_episode_id": 21,
    }
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    trader.execute_buy.side_effect = PreSubmissionContractError(
        "fee catalog contract failed"
    )

    stats = bot.run_cycle()

    repo.mark_entry_episodes_queued_no_post.assert_called_once_with(
        [21],
        reason="selected_for_cycle_before_any_submission",
    )
    assert repo.mark_entry_episode_execution.call_args_list == [
        call(
            21,
            state="PRE_SUBMISSION_CONTRACT_ERROR",
            reason="PreSubmissionContractError",
        ),
    ]
    assert stats["entry_pre_submission_failures"] == 1
    assert stats["bought"] == 0
    session.close.assert_called_once()


def test_pre_submission_failure_does_not_skip_later_candidate(
    monkeypatch,
    tmp_path,
):
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    first = {
        "condition_id": "market-1",
        "event_id": "event-1",
        "entry_episode_id": 21,
    }
    later = {
        "condition_id": "market-2",
        "event_id": "event-2",
        "entry_episode_id": 22,
    }
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [first, later]
    trader.execute_buy.side_effect = [
        PreSubmissionContractError("signed amount precision"),
        42,
    ]

    stats = bot.run_cycle()

    repo.mark_entry_episodes_queued_no_post.assert_called_once_with(
        [21, 22],
        reason="selected_for_cycle_before_any_submission",
    )
    assert repo.mark_entry_episode_execution.call_args_list == [
        call(
            21,
            state="PRE_SUBMISSION_CONTRACT_ERROR",
            reason="PreSubmissionContractError",
        ),
    ]
    assert trader.execute_buy.call_args_list == [call(first), call(later)]
    assert stats["entry_pre_submission_failures"] == 1
    assert stats["bought"] == 1
    session.close.assert_called_once()


def test_uncertain_buy_reserves_one_slot_and_later_event_still_executes(
    monkeypatch,
    tmp_path,
):
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    first = {
        "condition_id": "market-1",
        "event_id": "event-1",
        "entry_episode_id": 41,
    }
    later = {
        "condition_id": "market-2",
        "event_id": "event-2",
        "entry_episode_id": 42,
    }
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [first, later]

    calls = 0

    def execute(candidate):
        nonlocal calls
        calls += 1
        if calls == 1:
            trader.last_entry_may_have_reached_venue = True
            trader.last_entry_outcome_reason = "buy_submission_outcome_unknown"
            return None
        return 42

    trader.execute_buy.side_effect = execute

    stats = bot.run_cycle()

    assert trader.execute_buy.call_args_list == [call(first), call(later)]
    assert stats["bought"] == 1
    assert repo.mark_entry_episode_execution.call_args_list == [
        call(
            41,
            state="NOT_EXECUTED",
            reason="buy_submission_outcome_unknown",
        )
    ]
    session.close.assert_called_once()


def test_event_capacity_no_post_is_retryable_for_opposite_result(
    monkeypatch,
    tmp_path,
):
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {
        "condition_id": "draw-condition",
        "event_id": "event-1",
        "entry_episode_id": 31,
    }
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    trader.execute_buy.side_effect = None
    trader.execute_buy.return_value = None
    trader.last_entry_outcome_reason = "event_capacity_reserved"
    trader.last_entry_may_have_reached_venue = False

    stats = bot.run_cycle()

    assert stats["bought"] == 0
    assert repo.mark_entry_episode_execution.call_args_list == [
        call(
            31,
            state="NO_POST_RETRYABLE",
            reason="event_capacity_reserved",
        ),
    ]
    session.close.assert_called_once()


def test_active_pending_buy_reserves_capacity_but_does_not_block_other_event(
    monkeypatch, tmp_path
):
    pending = SimpleNamespace(id=8, token_id="yes-token")
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {"condition_id": "market-1", "event_id": "event-1"}
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    repo.get_pending_buy_trades.return_value = [pending]
    trader.reconcile_pending_buy.return_value = False
    repo.get_stats.return_value = {
        "holding": 0,
        "pending_buy": 1,
        "pending_sell": 0,
        "resolved": 0,
        "expired": 0,
        "unfilled": 0,
        "quarantined": 0,
        "total_pnl": 0.0,
    }
    repo.get_entry_capacity_state.return_value = {
        "open_positions": 1,
        "untracked_buy_reservations": 0,
        "total_reserved": 1,
    }
    trader.execute_buy.side_effect = None
    trader.execute_buy.return_value = 2

    stats = bot.run_cycle()

    assert stats["buy_candidates"] == 1
    assert stats["entry_blocked_candidates"] == 0
    assert stats["entry_guard"]["blocking_reasons"] == []
    assert stats["entry_guard"]["degraded_reasons"] == [
        "pending_buy_event_isolated"
    ]
    scanner.scan_buy_candidates.assert_called_once()
    trader.execute_buy.assert_called_once_with(candidate)
    session.close.assert_called_once()


def test_active_isolates_sell_intent_without_blocking_unrelated_buy(
    monkeypatch, tmp_path
):
    bot, scanner, trader, _repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {"condition_id": "market-1", "event_id": "event-1"}
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    trader.execute_buy.side_effect = None
    trader.execute_buy.return_value = 1

    stats = bot.run_cycle(
        order_reconciliation={
            "unresolved_sell_outcomes": 1,
            "errors": 1,
            "buy_errors": 0,
            "sell_errors": 1,
            "unknown_side_errors": 0,
        }
    )

    assert stats["entry_blocked_candidates"] == 0
    assert stats["entry_guard"]["blocking_reasons"] == []
    assert stats["entry_guard"]["degraded_reasons"] == [
        "unresolved_sell_outcome_isolated",
        "sell_reconciliation_error_isolated",
    ]
    trader.execute_buy.assert_called_once_with(candidate)
    session.close.assert_called_once()


def test_active_isolates_known_buy_reconciliation_error_by_capacity(
    monkeypatch, tmp_path
):
    bot, scanner, trader, _repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {"condition_id": "market-1", "event_id": "event-1"}
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    trader.execute_buy.side_effect = None
    trader.execute_buy.return_value = 1

    stats = bot.run_cycle(
        order_reconciliation={
            "errors": 1,
            "buy_errors": 1,
            "sell_errors": 0,
            "unknown_side_errors": 0,
        }
    )

    assert stats["entry_guard"]["blocking_reasons"] == []
    assert stats["entry_guard"]["degraded_reasons"] == [
        "buy_reconciliation_error_isolated"
    ]
    trader.execute_buy.assert_called_once_with(candidate)
    session.close.assert_called_once()


def test_active_still_blocks_unknown_side_reconciliation_error(
    monkeypatch, tmp_path
):
    bot, scanner, trader, _repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {"condition_id": "market-1", "event_id": "event-1"}
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]

    stats = bot.run_cycle(
        order_reconciliation={
            "errors": 1,
            "buy_errors": 0,
            "sell_errors": 0,
            "unknown_side_errors": 1,
        }
    )

    assert stats["entry_guard"]["blocking_reasons"] == [
        "order_reconciliation_error"
    ]
    trader.execute_buy.assert_not_called()
    session.close.assert_called_once()


def test_active_isolated_stop_quarantine_reserves_capacity_but_not_global_gate(
    monkeypatch, tmp_path
):
    isolated = SimpleNamespace(id=41, token_id="isolated-token")
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {"condition_id": "market-1", "event_id": "event-1"}
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    trader.execute_buy.side_effect = None
    trader.execute_buy.return_value = 1
    repo.get_isolated_stop_sell_trades.return_value = [isolated]
    repo.get_stats.return_value = {
        "holding": 0,
        "pending_buy": 0,
        "pending_sell": 0,
        "resolved": 0,
        "expired": 0,
        "unfilled": 0,
        "quarantined": 1,
        "isolated_stop_sell": 1,
        "total_pnl": 0.0,
    }
    repo.get_quarantine_state.return_value = {
        "total": 1,
        "isolated_buy": 0,
        "isolated_stop_sell": 1,
        "blocking": 0,
    }
    repo.get_entry_capacity_state.return_value = {
        "open_positions": 1,
        "untracked_buy_reservations": 0,
        "total_reserved": 1,
    }

    stats = bot.run_cycle()

    assert stats["isolated_stop_sells_checked"] == 1
    assert stats["entry_guard"]["capacity_remaining"] == 9
    assert stats["entry_guard"]["blocking_reasons"] == []
    assert stats["entry_guard"]["degraded_reasons"] == [
        "stop_sell_unknown_exposure_isolated"
    ]
    trader.reconcile_pending_sell.assert_called_once_with(isolated)
    trader.execute_buy.assert_called_once_with(candidate)
    session.close.assert_called_once()


def test_active_blocks_new_buy_after_economic_drawdown_limit(
    monkeypatch, tmp_path
):
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {"condition_id": "market-1", "event_id": "event-1"}
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    repo.get_economic_pnl_guard.return_value = {
        "economic_pnl": -10.0,
        "recorded_realized_pnl": -4.0,
        "recorded_settlement_pnl": -6.0,
        "confirmed_sell_pnl": -4.0,
        "proven_resolution_pnl": -6.0,
        "execution_adjustment_pnl": 0.0,
        "invalidated_settlement_pnl": 0.0,
        "execution_override_count": 0,
        "evidence_gaps": 0,
    }

    stats = bot.run_cycle()

    assert stats["drawdown_guard"] == {
        "triggered": True,
        "economic_pnl": -10.0,
        "confirmed_sell_pnl": -4.0,
        "proven_resolution_pnl": -6.0,
        "recorded_realized_pnl": -4.0,
        "recorded_settlement_pnl": -6.0,
        "execution_adjustment_pnl": 0.0,
        "invalidated_settlement_pnl": 0.0,
        "execution_override_count": 0,
        "evidence_gaps": 0,
        "loss_limit_usdc": 10.0,
    }
    assert "economic_drawdown_limit_reached" in stats["entry_guard"][
        "blocking_reasons"
    ]
    trader.execute_buy.assert_not_called()
    session.close.assert_called_once()


def test_active_blocks_new_buy_when_confirmed_sell_cannot_map_to_trade(
    monkeypatch, tmp_path
):
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {"condition_id": "market-1", "event_id": "event-1"}
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    repo.get_economic_pnl_guard.return_value = {
        "economic_pnl": 0.0,
        "recorded_realized_pnl": 0.0,
        "recorded_settlement_pnl": 0.0,
        "confirmed_sell_pnl": 0.0,
        "proven_resolution_pnl": 0.0,
        "execution_adjustment_pnl": 0.0,
        "invalidated_settlement_pnl": 0.0,
        "execution_override_count": 0,
        "evidence_gaps": 1,
    }

    stats = bot.run_cycle()

    assert stats["entry_guard"]["blocking_reasons"] == [
        "economic_pnl_execution_evidence_gap"
    ]
    assert stats["drawdown_guard"]["evidence_gaps"] == 1
    trader.execute_buy.assert_not_called()
    session.close.assert_called_once()


def test_active_untracked_buy_reserves_capacity_without_global_block(
    monkeypatch, tmp_path
):
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {
        "condition_id": "market-1",
        "event_id": "event-1",
        "entry_episode_id": 17,
    }
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    repo.get_entry_capacity_state.return_value = {
        "open_positions": 0,
        "untracked_buy_reservations": 1,
        "total_reserved": 1,
    }
    trader.execute_buy.side_effect = None
    trader.execute_buy.return_value = 1

    stats = bot.run_cycle()

    assert stats["entry_guard"]["blocking_reasons"] == []
    assert stats["entry_guard"]["degraded_reasons"] == [
        "untracked_buy_exposure_isolated"
    ]
    trader.execute_buy.assert_called_once_with(candidate)
    session.close.assert_called_once()


def test_active_blocks_new_buy_when_owned_buy_fee_evidence_is_incomplete(
    monkeypatch, tmp_path
):
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {
        "condition_id": "market-1",
        "event_id": "event-1",
        "entry_episode_id": 19,
    }
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    repo.get_open_buy_evidence_gap_count.return_value = 1

    stats = bot.run_cycle()

    assert stats["entry_guard"]["open_buy_evidence_gaps"] == 1
    assert stats["entry_guard"]["blocking_reasons"] == [
        "open_buy_fill_or_fee_evidence_gap"
    ]
    repo.mark_entry_episode_execution.assert_called_once_with(
        19,
        state="BLOCKED_GUARD",
        reason="open_buy_fill_or_fee_evidence_gap",
    )
    trader.execute_buy.assert_not_called()
    session.close.assert_called_once()


def test_simulation_does_not_treat_missing_live_fill_ledger_as_entry_gap(
    monkeypatch, tmp_path
):
    bot, scanner, trader, repo, session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    bot.config.simulation_mode = True
    candidate = {
        "condition_id": "market-shadow",
        "event_id": "event-shadow",
        "entry_episode_id": 20,
    }
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    repo.get_open_buy_evidence_gap_count.return_value = 1
    trader.execute_buy.side_effect = None
    trader.execute_buy.return_value = 20

    stats = bot.run_cycle()

    assert stats["entry_guard"]["open_buy_evidence_gaps"] == 0
    assert stats["entry_guard"]["blocking_reasons"] == []
    repo.get_open_buy_evidence_gap_count.assert_not_called()
    trader.execute_buy.assert_called_once_with(candidate)
    session.close.assert_called_once()


@pytest.mark.parametrize("simulation", [False, True])
def test_virtual_capital_and_capacity_do_not_censor_research_events(
    monkeypatch, tmp_path, simulation
):
    bot, scanner, trader, repo, _session, _gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    bot.config.simulation_mode = simulation
    candidates = [
        {"condition_id": f"c-{i}", "event_id": f"e-{i}", "entry_episode_id": i+1}
        for i in range(8)
    ]
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = candidates
    trader.execute_buy.side_effect = list(range(1, 9))
    repo.get_economic_pnl_guard.return_value["economic_pnl"] = -15.0
    repo.get_economic_pnl_guard.return_value["recorded_settlement_pnl"] = -15.0
    repo.get_entry_capacity_state.return_value = {
        "open_positions": 10, "untracked_buy_reservations": 0, "total_reserved": 10,
    }

    stats = bot.run_cycle()

    assert stats["entry_guard"]["portfolio_guards_enforced"] is not simulation
    if simulation:
        assert stats["entry_guard"]["blocking_reasons"] == []
        assert stats["drawdown_guard"]["triggered"] is False
        assert stats["bought"] == 8  # Not truncated by the live cycle cap of 5.
        assert trader.execute_buy.call_args_list == [call(c) for c in candidates]
    else:
        assert "max_capacity_reserved" in stats["entry_guard"]["blocking_reasons"]
        assert "economic_drawdown_limit_reached" in stats["entry_guard"]["blocking_reasons"]
        trader.execute_buy.assert_not_called()


def test_active_blocks_entry_when_allowed_league_metadata_drifts(
    monkeypatch, tmp_path
):
    bot, scanner, trader, repo, session, gamma = _build_bot(
        monkeypatch, tmp_path, "active", []
    )
    candidate = {
        "condition_id": "market-1",
        "event_id": "event-1",
        "entry_episode_id": 18,
    }
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = [candidate]
    gamma.last_sweep_attestation = {
        "raw_market_count": 9,
        "qualified_market_count": 0,
        "exclusion_counts": {
            "sport_name_mismatch:sport=epl:status=drift": 9
        },
    }

    stats = bot.run_cycle()

    assert stats["universe_health"] == {
        "raw_market_count": 9,
        "qualified_market_count": 0,
        "drift_excluded_count": 9,
        "metadata_drift_suspected": True,
    }
    assert stats["entry_guard"]["blocking_reasons"] == [
        "league_identity_metadata_drift"
    ]
    repo.mark_entry_episode_execution.assert_called_once_with(
        18,
        state="BLOCKED_GUARD",
        reason="league_identity_metadata_drift",
    )
    trader.execute_buy.assert_not_called()
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


def test_bot_close_releases_network_pools_and_database_engine() -> None:
    bot = object.__new__(PolymarketBot)
    bot.gamma = MagicMock()
    bot.clob = MagicMock()
    engine = MagicMock()
    bot.Session = SimpleNamespace(kw={"bind": engine})

    failures = bot.close()

    assert failures == ()
    bot.gamma.close.assert_called_once_with()
    bot.clob.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_bot_close_continues_after_individual_cleanup_failure() -> None:
    bot = object.__new__(PolymarketBot)
    bot.gamma = MagicMock()
    bot.gamma.close.side_effect = RuntimeError("broken gamma pool")
    bot.clob = MagicMock()
    engine = MagicMock()
    bot.Session = SimpleNamespace(kw={"bind": engine})

    failures = bot.close()

    assert failures == ("gamma_session",)
    bot.gamma.close.assert_called_once_with()
    bot.clob.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()
