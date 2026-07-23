"""Regression tests for exact live BUY/SELL fill evidence.

These tests deliberately use the real observability ledger schema in the same
SQLite database as the Queen repository.  Accepted order intent alone must
never become a position or a settlement basis.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import text

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
    db_path = tmp_path / "queen-ledger.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-queen")
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
    assert partial_sell.detail == "confirmed_partial_or_unreconciled"
    session.close()


def test_pending_sell_completes_from_real_buy_and_sell_ledger_rows(tmp_path):
    """Exercise the strategy transition against the real shared ledger schema."""
    db_path = tmp_path / "queen-pending-sell.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-queen")
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
