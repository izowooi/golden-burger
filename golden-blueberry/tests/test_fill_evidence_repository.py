"""Regression tests for exact live BUY/SELL fill evidence.

These tests deliberately use the real observability ledger schema in the same
SQLite database as the Blueberry repository.  Accepted order intent alone must
never become a position or a settlement basis.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import text
import requests

import pytest

from polybot.config import TradingConfig
from polybot.db.models import TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.trader import Trader
from polybot_observability import ExecutionLedger


def _record_accepted_order(
    ledger: ExecutionLedger, order_id: str, *, side: str = "BUY"
) -> str:
    return ledger.record_submission(
        token_id=f"token-{order_id}",
        side=side,
        requested_price=0.96,
        requested_size=5.0,
        result={"success": True, "orderID": order_id, "status": "live"},
        simulation=False,
    )


def test_exact_fill_evidence_reads_real_ledger_states(tmp_path):
    db_path = tmp_path / "quince-ledger.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-blueberry")
    confirmed_submission = _record_accepted_order(ledger, "OID-confirmed")
    _record_accepted_order(ledger, "OID-zero")
    _record_accepted_order(ledger, "OID-pending")
    confirmed_sell_submission = _record_accepted_order(
        ledger, "OID-sell-confirmed", side="SELL"
    )
    _record_accepted_order(ledger, "OID-sell-zero", side="SELL")
    partial_sell_submission = _record_accepted_order(
        ledger, "OID-sell-partial", side="SELL"
    )

    session = Session()
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='MATCHED', "
            "latest_size_matched=5.0, needs_reconciliation=0 "
            "WHERE order_id='OID-confirmed'"
        )
    )
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='MATCHED', "
            "latest_size_matched=5.0, needs_reconciliation=0 "
            "WHERE order_id='OID-sell-confirmed'"
        )
    )
    session.execute(
        text(
            "INSERT INTO order_fills "
            "(submission_id, order_id, trade_id, bucket_index, status, side, "
            "size, price, fee_amount_usdc, matched_at, domain_error) VALUES "
            "(:submission_id, 'OID-sell-confirmed', 'sell-fill', 0, "
            "'CONFIRMED', 'SELL', 5.0, 0.89, 0.02, "
            "'2026-07-14T00:02:00Z', NULL)"
        ),
        {"submission_id": confirmed_sell_submission},
    )
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='CANCELED', "
            "latest_size_matched=0.0, needs_reconciliation=0 "
            "WHERE order_id='OID-sell-zero'"
        )
    )
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='CANCELED', "
            "latest_size_matched=2.0, needs_reconciliation=0 "
            "WHERE order_id='OID-sell-partial'"
        )
    )
    session.execute(
        text(
            "INSERT INTO order_fills "
            "(submission_id, order_id, trade_id, bucket_index, status, side, "
            "size, price, fee_amount_usdc, matched_at, domain_error) VALUES "
            "(:submission_id, 'OID-sell-partial', 'partial-fill', 0, "
            "'CONFIRMED', 'SELL', 2.0, 0.89, 0.01, "
            "'2026-07-14T00:03:00Z', NULL)"
        ),
        {"submission_id": partial_sell_submission},
    )
    session.execute(
        text(
            "INSERT INTO order_fills "
            "(submission_id, order_id, trade_id, bucket_index, status, side, "
            "size, price, fee_amount_usdc, matched_at, domain_error) VALUES "
            "(:submission_id, 'OID-confirmed', 'fill-a', 0, 'CONFIRMED', "
            "'BUY', 2.0, 0.95, 0.01, '2026-07-14T00:00:00Z', NULL), "
            "(:submission_id, 'OID-confirmed', 'fill-b', 0, 'CONFIRMED', "
            "'BUY', 3.0, 0.97, 0.02, '2026-07-14T00:01:00Z', NULL)"
        ),
        {"submission_id": confirmed_submission},
    )
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='CANCELED', "
            "latest_size_matched=0.0, needs_reconciliation=0 "
            "WHERE order_id='OID-zero'"
        )
    )
    session.commit()

    repo = TradeRepository(session)
    confirmed = repo.get_exact_buy_fill_evidence("OID-confirmed")
    terminal_zero = repo.get_exact_buy_fill_evidence("OID-zero")
    pending = repo.get_exact_buy_fill_evidence("OID-pending")
    unavailable = repo.get_exact_buy_fill_evidence("OID-unknown")
    confirmed_sell = repo.get_exact_sell_fill_evidence("OID-sell-confirmed")
    side_mismatch = repo.get_exact_buy_fill_evidence("OID-sell-confirmed")
    terminal_zero_sell = repo.get_exact_sell_fill_evidence("OID-sell-zero")
    partial_sell = repo.get_exact_sell_fill_evidence("OID-sell-partial")

    assert confirmed.state == "confirmed"
    assert confirmed.has_confirmed_fill is True
    assert confirmed.has_reconciled_full_fill is True
    assert confirmed.confirmed_size == 5.0
    assert confirmed.confirmed_vwap == pytest.approx((2 * 0.95 + 3 * 0.97) / 5)
    assert confirmed.confirmed_fee_usdc == pytest.approx(0.03)
    assert confirmed.fee_complete is True
    assert confirmed.matched_at == "2026-07-14T00:01:00Z"

    assert terminal_zero.state == "terminal_zero_fill"
    assert terminal_zero.order_status == "CANCELED"
    assert terminal_zero.confirmed_size == 0.0
    assert terminal_zero.has_confirmed_fill is False

    assert pending.state == "pending"
    assert pending.detail == "reconciliation_pending"
    assert pending.has_confirmed_fill is False

    assert unavailable.state == "unavailable"
    assert unavailable.detail == "submission_missing"
    assert unavailable.has_confirmed_fill is False

    assert confirmed_sell.state == "confirmed"
    assert confirmed_sell.side == "SELL"
    assert confirmed_sell.has_reconciled_full_fill is True
    assert confirmed_sell.confirmed_size == 5.0
    assert confirmed_sell.confirmed_vwap == 0.89
    assert confirmed_sell.confirmed_fee_usdc == 0.02

    assert side_mismatch.state == "unavailable"
    assert side_mismatch.detail == "submission_side_mismatch"

    assert terminal_zero_sell.state == "terminal_zero_fill"
    assert terminal_zero_sell.side == "SELL"
    assert terminal_zero_sell.needs_reconciliation is False

    assert partial_sell.state == "confirmed"
    assert partial_sell.confirmed_size == 2.0
    assert partial_sell.has_reconciled_full_fill is False
    assert partial_sell.has_reconciled_terminal_fill is True
    assert partial_sell.detail == "confirmed_partial_or_unreconciled"
    session.close()


def test_matched_quantized_buy_promotes_pending_trade_to_holding(tmp_path):
    """The venue-rounded MATCHED size, not the raw intent, defines the position."""
    db_path = tmp_path / "blueberry-quantized-buy.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-blueberry")
    requested_size = 5.43478260869565
    matched_size = 5.43
    submission_id = ledger.record_submission(
        token_id="yes-token",
        side="BUY",
        requested_price=0.92,
        requested_size=requested_size,
        result={
            "success": True,
            "orderID": "OID-quantized-buy",
            "status": "MATCHED",
            "makingAmount": "4.9413",
            "takingAmount": "5.43",
        },
        simulation=False,
    )

    session = Session()
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='MATCHED', "
            "latest_size_matched=:matched_size, needs_reconciliation=0 "
            "WHERE order_id='OID-quantized-buy'"
        ),
        {"matched_size": matched_size},
    )
    session.execute(
        text(
            "INSERT INTO order_fills "
            "(submission_id, order_id, trade_id, bucket_index, status, side, "
            "size, price, fee_amount_usdc, matched_at, domain_error) VALUES "
            "(:submission_id, 'OID-quantized-buy', 'quantized-fill', 0, "
            "'CONFIRMED', 'BUY', :matched_size, 0.91, 0.0, "
            "'2026-08-10T00:00:00Z', NULL)"
        ),
        {"submission_id": submission_id, "matched_size": matched_size},
    )
    session.commit()

    repo = TradeRepository(session)
    evidence = repo.get_exact_buy_fill_evidence("OID-quantized-buy")
    assert evidence.has_reconciled_full_fill is True
    assert evidence.requested_size == pytest.approx(requested_size)
    assert evidence.confirmed_size == pytest.approx(matched_size)

    trade = repo.create_trade(
        condition_id="condition-quantized-buy",
        outcome="Yes",
        token_id="yes-token",
        buy_price=0.92,
        buy_shares=requested_size,
        buy_order_id="OID-quantized-buy",
        buy_timestamp=datetime.utcnow(),
        status=TradeStatus.PENDING_BUY,
        mode="live",
    )
    trader = Trader(
        repo,
        SimpleNamespace(simulation_mode=False),
        TradingConfig(),
        simulation_mode=False,
    )

    assert trader.reconcile_pending_buy(trade) is True
    holding = repo.get_by_id(trade.id)
    assert holding is not None
    assert holding.status == TradeStatus.HOLDING
    assert holding.buy_shares == pytest.approx(matched_size)
    assert holding.buy_price == pytest.approx(0.91)
    assert holding.buy_confirmed_size == pytest.approx(matched_size)
    session.close()


def test_canceled_terminal_partial_buy_promotes_only_confirmed_size(tmp_path):
    db_path = tmp_path / "blueberry-terminal-partial-buy.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-blueberry")
    submission_id = _record_accepted_order(ledger, "OID-partial-buy")
    session = Session()
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='CANCELED', "
            "latest_size_matched=2.0, needs_reconciliation=0 "
            "WHERE order_id='OID-partial-buy'"
        )
    )
    session.execute(
        text(
            "INSERT INTO order_fills "
            "(submission_id, order_id, trade_id, bucket_index, status, side, "
            "size, price, fee_amount_usdc, matched_at, domain_error) VALUES "
            "(:submission_id, 'OID-partial-buy', 'partial-buy-fill', 0, "
            "'CONFIRMED', 'BUY', 2.0, 0.95, 0.01, "
            "'2026-08-10T00:00:00Z', NULL)"
        ),
        {"submission_id": submission_id},
    )
    session.commit()
    repo = TradeRepository(session)
    trade = repo.create_trade(
        condition_id="condition-partial-buy",
        outcome="Yes",
        token_id="token-OID-partial-buy",
        buy_price=0.96,
        buy_amount=5.0,
        buy_shares=5.0,
        buy_order_id="OID-partial-buy",
        buy_timestamp=datetime.utcnow(),
        status=TradeStatus.PENDING_BUY,
        mode="live",
    )
    trader = Trader(
        repo,
        SimpleNamespace(simulation_mode=False),
        TradingConfig(),
        simulation_mode=False,
    )

    assert trader.reconcile_pending_buy(trade) is True
    holding = repo.get_by_id(trade.id)
    assert holding.status == TradeStatus.HOLDING
    assert holding.buy_shares == 2.0
    assert holding.buy_confirmed_size == 2.0
    assert holding.buy_price == 0.95
    session.close()


def test_unknown_post_and_quarantined_trade_reserve_capacity_after_restart(tmp_path):
    db_path = tmp_path / "blueberry-capacity.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-blueberry")
    intent = ledger.record_intent(
        token_id="unknown-token",
        side="BUY",
        requested_price=0.90,
        requested_size=5.5,
        simulation=False,
    )
    assert ledger.record_submission_error(
        intent, requests.exceptions.ReadTimeout("post response unavailable")
    ) == "SUBMIT_OUTCOME_UNKNOWN"

    session = Session()
    repo = TradeRepository(session)
    repo.create_trade(
        condition_id="quarantined-condition",
        outcome="Yes",
        token_id="quarantined-token",
        buy_amount=5.0,
        buy_timestamp=datetime.utcnow(),
        status=TradeStatus.QUARANTINED,
        mode="live",
    )
    session.close()

    restarted = Session()
    restarted_repo = TradeRepository(restarted)
    assert restarted_repo.get_untracked_buy_capacity_reservations() == pytest.approx(
        (1, 4.95)
    )
    assert restarted_repo.get_position_count() == 2
    assert restarted_repo.get_open_notional_usdc() == pytest.approx(9.95)
    restarted.close()


def test_terminal_zero_fill_orphan_releases_ledger_reservation(tmp_path):
    db_path = tmp_path / "blueberry-zero-reservation.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-blueberry")
    _record_accepted_order(ledger, "OID-orphan-zero")
    session = Session()
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='CANCELED', "
            "latest_size_matched=0, needs_reconciliation=0 "
            "WHERE order_id='OID-orphan-zero'"
        )
    )
    session.commit()
    repo = TradeRepository(session)
    assert repo.get_untracked_buy_capacity_reservations() == (0, 0.0)
    assert repo.get_position_count() == 0
    session.close()


def test_explicit_dust_residual_remains_in_capacity(tmp_path):
    Session = init_database(str(tmp_path / "blueberry-residual-capacity.db"))
    session = Session()
    repo = TradeRepository(session)
    repo.create_trade(
        condition_id="residual-condition",
        event_id="residual-event",
        outcome="Yes",
        token_id="residual-token",
        buy_amount=5.0,
        buy_timestamp=datetime.utcnow(),
        sell_residual_shares=0.005,
        status=TradeStatus.RESIDUAL,
        mode="live",
    )
    assert repo.get_position_count() == 1
    assert repo.get_open_notional_usdc() == 5.0
    assert repo.get_event_position_count("residual-event") == 1
    session.close()


def test_pending_sell_completes_from_real_buy_and_sell_ledger_rows(tmp_path):
    """Exercise the strategy transition against the real shared ledger schema."""
    db_path = tmp_path / "quince-pending-sell.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-blueberry")
    buy_submission = _record_accepted_order(ledger, "OID-buy-full", side="BUY")
    sell_submission = _record_accepted_order(ledger, "OID-sell-full", side="SELL")

    session = Session()
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='MATCHED', "
            "latest_size_matched=5.0, needs_reconciliation=0 "
            "WHERE order_id IN ('OID-buy-full', 'OID-sell-full')"
        )
    )
    session.execute(
        text(
            "INSERT INTO order_fills "
            "(submission_id, order_id, trade_id, bucket_index, status, side, "
            "size, price, fee_amount_usdc, matched_at, domain_error) VALUES "
            "(:buy_submission, 'OID-buy-full', 'buy-fill', 0, 'CONFIRMED', "
            "'BUY', 5.0, 0.96, 0.03, '2026-07-14T00:00:00Z', NULL), "
            "(:sell_submission, 'OID-sell-full', 'sell-fill', 0, 'CONFIRMED', "
            "'SELL', 5.0, 0.89, 0.02, '2026-07-14T00:01:00Z', NULL)"
        ),
        {
            "buy_submission": buy_submission,
            "sell_submission": sell_submission,
        },
    )
    session.commit()

    repo = TradeRepository(session)
    trade = repo.create_trade(
        condition_id="condition-full",
        outcome="Yes",
        token_id="yes-token",
        buy_price=0.96,
        buy_shares=5.0,
        buy_order_id="OID-buy-full",
        buy_timestamp=datetime.utcnow(),
        sell_price=0.89,
        sell_shares=5.0,
        sell_order_id="OID-sell-full",
        sell_timestamp=datetime.utcnow(),
        status=TradeStatus.PENDING_SELL,
        mode="live",
    )
    trader = Trader(
        repo,
        SimpleNamespace(simulation_mode=False),
        TradingConfig(),
        simulation_mode=False,
    )

    assert trader.reconcile_pending_sell(trade) is True

    completed = repo.get_by_id(trade.id)
    assert completed is not None
    assert completed.status == TradeStatus.COMPLETED
    assert completed.buy_confirmed_size == 5.0
    assert completed.sell_confirmed_size == 5.0
    assert completed.buy_confirmed_vwap == 0.96
    assert completed.sell_confirmed_vwap == 0.89
    assert completed.realized_pnl == pytest.approx(
        (0.89 - 0.96) * 5 - 0.03 - 0.02
    )
    assert completed.pnl_basis == (
        "exact_reconciled_buy_sell_confirmed_fills_net_known_fees"
    )
    session.close()
