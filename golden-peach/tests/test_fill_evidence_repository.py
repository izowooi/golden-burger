"""Regression tests for exact live BUY/SELL fill evidence.

These tests deliberately use the real observability ledger schema in the same
SQLite database as the Papaya repository.  Accepted order intent alone must
never become a position or a settlement basis.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import sqlite3
from types import SimpleNamespace

from sqlalchemy import text

import pytest

from polybot.config import TradingConfig
from polybot.db.models import (
    EntryEpisode,
    MarketCatalog,
    MarketSnapshot,
    STOP_SELL_QUARANTINE_REASON,
    TradeStatus,
    init_database,
)
from polybot.db.repository import TradeRepository
from polybot.strategy.trader import Trader
from polybot_observability import ExecutionLedger, SubmissionEvidenceError


def _record_accepted_order(
    ledger: ExecutionLedger,
    order_id: str,
    *,
    side: str = "BUY",
    token_id: str | None = None,
    requested_size: float = 5.0,
) -> str:
    return ledger.record_submission(
        token_id=token_id or f"token-{order_id}",
        side=side,
        requested_price=0.96,
        requested_size=requested_size,
        result={"success": True, "orderID": order_id, "status": "live"},
        simulation=False,
    )


def test_exact_fill_evidence_reads_real_ledger_states(tmp_path):
    db_path = tmp_path / "papaya-ledger.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-peach")
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
    assert partial_sell.has_reconciled_executed_fill is True
    assert partial_sell.detail == "confirmed_reconciled_terminal_partial_fill"
    session.close()


def test_pending_sell_completes_from_real_buy_and_sell_ledger_rows(tmp_path):
    """Exercise the strategy transition against the real shared ledger schema."""
    db_path = tmp_path / "papaya-pending-sell.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-peach")
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


def test_pending_sell_records_unavoidable_two_decimal_sdk_dust(tmp_path):
    db_path = tmp_path / "watermelon-pending-sell-dust.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-peach")
    buy_submission = _record_accepted_order(
        ledger,
        "OID-buy-dust",
        side="BUY",
        requested_size=5.102,
    )
    sell_submission = _record_accepted_order(
        ledger,
        "OID-sell-dust",
        side="SELL",
        requested_size=5.10,
    )

    session = Session()
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='MATCHED', "
            "needs_reconciliation=0, latest_size_matched=CASE "
            "WHEN order_id='OID-buy-dust' THEN 5.102 ELSE 5.10 END "
            "WHERE order_id IN ('OID-buy-dust', 'OID-sell-dust')"
        )
    )
    session.execute(
        text(
            "INSERT INTO order_fills "
            "(submission_id, order_id, trade_id, bucket_index, status, side, "
            "size, price, fee_amount_usdc, matched_at, domain_error) VALUES "
            "(:buy_submission, 'OID-buy-dust', 'buy-dust-fill', 0, "
            "'CONFIRMED', 'BUY', 5.102, 0.98, 0.005, "
            "'2026-08-25T00:00:00Z', NULL), "
            "(:sell_submission, 'OID-sell-dust', 'sell-dust-fill', 0, "
            "'CONFIRMED', 'SELL', 5.10, 0.70, 0.05355, "
            "'2026-08-25T00:01:00Z', NULL)"
        ),
        {
            "buy_submission": buy_submission,
            "sell_submission": sell_submission,
        },
    )
    session.commit()

    repo = TradeRepository(session)
    trade = repo.create_trade(
        condition_id="condition-dust",
        outcome="Yes",
        token_id="token-dust",
        buy_price=0.98,
        buy_shares=5.102,
        buy_order_id="OID-buy-dust",
        buy_timestamp=datetime.utcnow(),
        sell_price=0.70,
        sell_shares=5.10,
        sell_order_id="OID-sell-dust",
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
    assert completed.status == TradeStatus.COMPLETED
    assert completed.sell_confirmed_size == pytest.approx(5.10)
    assert completed.sell_residual_shares == pytest.approx(0.002)
    assert completed.exit_reason.endswith("recorded_sdk_dust")
    allocated_buy_fee = 0.005 * 5.10 / 5.102
    assert completed.realized_pnl == pytest.approx(
        (0.70 - 0.98) * 5.10 - allocated_buy_fee - 0.05355
    )
    assert "excluding_recorded_unsellable_dust" in completed.pnl_basis
    session.close()


def test_stale_delayed_fok_sell_zero_fill_returns_to_holding_same_cycle(tmp_path):
    """Regression: Elversberg-style DELAYED SELL must not block every entry."""
    db_path = tmp_path / "watermelon-stale-sell.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-peach")
    sell_submission = ledger.record_submission(
        token_id="token-stale-sell",
        side="SELL",
        requested_price=0.89,
        requested_size=5.26,
        result={
            "success": True,
            "orderID": "OID-stale-sell",
            "status": "DELAYED",
            "makingAmount": "5260000",
            "takingAmount": "4681400",
        },
        simulation=False,
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE order_submissions SET submitted_at=?, "
            "reconciliation_error=? WHERE submission_id=?",
            (
                "2026-08-20T00:00:00+00:00",
                "phase=match_authoritative_order_catalogs "
                "error=ClobResponseUnavailableError "
                "response_shape=sequence(len=1,item_type=dict)",
                sell_submission,
            ),
        )

    session = Session()
    repo = TradeRepository(session)
    submitted_at = datetime(2026, 8, 20, 0, 0)
    trade = repo.create_trade(
        condition_id="condition-stale-sell",
        event_id="event-stale-sell",
        outcome="Away",
        token_id="token-stale-sell",
        buy_price=0.95,
        buy_shares=5.263,
        buy_order_id="OID-buy-stale-sell",
        buy_timestamp=submitted_at - timedelta(minutes=5),
        sell_price=0.89,
        sell_shares=5.26,
        sell_order_id="OID-stale-sell",
        sell_timestamp=submitted_at,
        status=TradeStatus.PENDING_SELL,
        mode="live",
    )

    class _TerminalizingClob:
        simulation_mode = False

        def __init__(self):
            self.calls = []

        def cancel_order_for_reconciliation(
            self, order_id, *, minimum_age_minutes
        ):
            self.calls.append((order_id, minimum_age_minutes))
            proof = ledger.record_delayed_fok_zero_fill(
                order_id=order_id,
                token_id="token-stale-sell",
                cancellation={
                    "canceled": [],
                    "not_canceled": {
                        order_id: "Order not found or already canceled"
                    },
                },
                authenticated_trades=[],
                minimum_age_minutes=minimum_age_minutes,
            )
            return {
                "verified_order_status": "CANCELED",
                "verified_size_matched": 0.0,
                "reconciliation_proof": proof,
            }

    clob = _TerminalizingClob()
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)

    assert trader.reconcile_pending_sell(
        trade, now=submitted_at + timedelta(minutes=3)
    ) is False
    assert clob.calls == [("OID-stale-sell", 2.0)]
    refreshed = repo.get_by_id(trade.id)
    assert refreshed.status == TradeStatus.HOLDING
    assert refreshed.exit_reason == "exit_sell_terminal_zero_fill"
    assert refreshed.sell_order_id is None
    assert refreshed.sell_timestamp is None
    assert repo.get_exact_sell_fill_evidence(
        "OID-stale-sell"
    ).state == "terminal_zero_fill"
    session.close()


def test_recent_delayed_fok_sell_stays_pending_without_early_cancel(tmp_path):
    db_path = tmp_path / "watermelon-recent-sell.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-peach")
    ledger.record_submission(
        token_id="token-recent-sell",
        side="SELL",
        requested_price=0.90,
        requested_size=5.0,
        result={
            "success": True,
            "orderID": "OID-recent-sell",
            "status": "DELAYED",
        },
        simulation=False,
    )
    session = Session()
    repo = TradeRepository(session)
    submitted_at = datetime(2026, 8, 20, 0, 0)
    trade = repo.create_trade(
        condition_id="condition-recent-sell",
        event_id="event-recent-sell",
        outcome="Home",
        token_id="token-recent-sell",
        buy_price=0.96,
        buy_shares=5.2,
        buy_order_id="OID-buy-recent-sell",
        buy_timestamp=submitted_at - timedelta(minutes=5),
        sell_price=0.90,
        sell_shares=5.0,
        sell_order_id="OID-recent-sell",
        sell_timestamp=submitted_at,
        status=TradeStatus.PENDING_SELL,
        mode="live",
    )
    clob = SimpleNamespace(
        simulation_mode=False,
        cancel_order_for_reconciliation=lambda *_args, **_kwargs: pytest.fail(
            "recent FOK SELL must not be canceled"
        ),
    )
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)

    assert trader.reconcile_pending_sell(
        trade, now=submitted_at + timedelta(minutes=1)
    ) is False
    assert repo.get_by_id(trade.id).status == TradeStatus.PENDING_SELL
    session.close()


def test_unresolved_delayed_sell_is_quarantined_after_three_hours(tmp_path):
    db_path = tmp_path / "watermelon-stale-sell-quarantine.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-peach")
    ledger.record_submission(
        token_id="token-stale-quarantine",
        side="SELL",
        requested_price=0.90,
        requested_size=5.0,
        result={
            "success": True,
            "orderID": "OID-stale-quarantine",
            "status": "DELAYED",
        },
        simulation=False,
    )
    session = Session()
    repo = TradeRepository(session)
    submitted_at = datetime(2026, 8, 20, 0, 0)
    trade = repo.create_trade(
        condition_id="condition-stale-quarantine",
        event_id="event-stale-quarantine",
        outcome="Home",
        token_id="token-stale-quarantine",
        buy_price=0.96,
        buy_shares=5.2,
        buy_order_id="OID-buy-stale-quarantine",
        buy_timestamp=submitted_at - timedelta(minutes=5),
        buy_confirmed_size=5.2,
        buy_confirmed_vwap=0.96,
        buy_confirmed_fee_usdc=0.0,
        sell_price=0.90,
        sell_shares=5.0,
        sell_order_id="OID-stale-quarantine",
        sell_timestamp=submitted_at,
        status=TradeStatus.PENDING_SELL,
        mode="live",
    )

    def unresolved_cancel(*_args, **_kwargs):
        raise SubmissionEvidenceError("catalog evidence remains unavailable")

    clob = SimpleNamespace(
        simulation_mode=False,
        cancel_order_for_reconciliation=unresolved_cancel,
    )
    trader = Trader(repo, clob, TradingConfig(), simulation_mode=False)

    assert trader.reconcile_pending_sell(
        trade, now=submitted_at + timedelta(minutes=181)
    ) is False
    refreshed = repo.get_by_id(trade.id)
    assert refreshed.status == TradeStatus.QUARANTINED
    assert refreshed.exit_reason == STOP_SELL_QUARANTINE_REASON
    assert repo.get_position_count() == 1
    assert repo.get_quarantine_state() == {
        "total": 1,
        "isolated_stop_sell": 1,
        "blocking": 0,
    }
    assert repo.get_isolated_stop_sell_trades() == [refreshed]
    assert repo.get_open_buy_evidence_gap_count() == 0
    session.close()


def test_entry_capacity_reserves_untracked_live_buy_intents_without_double_count(
    tmp_path,
):
    db_path = tmp_path / "watermelon-capacity.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-peach")
    _record_accepted_order(ledger, "OID-tracked", side="BUY")
    _record_accepted_order(ledger, "OID-orphan", side="BUY")
    with pytest.raises(SubmissionEvidenceError, match="representation"):
        ledger.submit_and_record(
            token_id="token-uncertain",
            side="BUY",
            requested_price=0.98,
            requested_size=5.1,
            submit=lambda: object(),
        )

    session = Session()
    repo = TradeRepository(session)
    repo.create_trade(
        condition_id="tracked-condition",
        outcome="Yes",
        token_id="token-OID-tracked",
        buy_order_id="OID-tracked",
        buy_timestamp=datetime.utcnow(),
        status=TradeStatus.PENDING_BUY,
        mode="live",
    )

    assert repo.get_entry_capacity_state() == {
        "open_positions": 1,
        "untracked_buy_reservations": 2,
        "total_reserved": 3,
    }

    repo.update_trade(1, status=TradeStatus.UNFILLED)
    assert repo.get_entry_capacity_state() == {
        "open_positions": 0,
        # The terminal Trade still represents OID-tracked; it is not an orphan.
        "untracked_buy_reservations": 2,
        "total_reserved": 2,
    }
    session.close()


def test_entry_capacity_releases_proven_synchronous_buy_rejection(tmp_path):
    db_path = tmp_path / "watermelon-rejected-buy.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-peach")

    result = ledger.submit_and_record(
        token_id="token-rejected",
        side="BUY",
        requested_price=0.98,
        requested_size=5.102,
        submit=lambda: {
            "success": False,
            "status": "FAILED",
            "error": "FOK order rejected",
        },
    )
    assert result["success"] is False

    session = Session()
    row = session.execute(
        text(
            "SELECT order_id, success, response_status, needs_reconciliation "
            "FROM order_submissions WHERE token_id='token-rejected'"
        )
    ).mappings().one()
    assert dict(row) == {
        "order_id": None,
        "success": 0,
        "response_status": "FAILED",
        "needs_reconciliation": 0,
    }

    repo = TradeRepository(session)
    assert repo.get_entry_capacity_state() == {
        "open_positions": 0,
        "untracked_buy_reservations": 0,
        "total_reserved": 0,
    }
    assert repo.get_untracked_buy_submissions() == []
    session.close()


def test_capacity_keeps_reconciled_orphan_and_quarantined_exposure(tmp_path):
    db_path = tmp_path / "watermelon-conservative-capacity.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-peach")
    positive_submission = _record_accepted_order(
        ledger,
        "OID-positive-orphan",
        token_id="token-positive",
        requested_size=5.102,
    )
    _record_accepted_order(
        ledger,
        "OID-zero-orphan",
        token_id="token-zero",
        requested_size=5.102,
    )

    session = Session()
    session.add_all(
        [
            EntryEpisode(
                token_id="token-positive",
                condition_id="condition-positive",
                event_id="event-positive",
                outcome="Yes",
                entry_snapshot_id=1,
                exact_vwap=0.98,
                arm_prob_min=0.98,
                arm_prob_max=0.999,
                observed_at=datetime.utcnow(),
            ),
            EntryEpisode(
                token_id="token-zero",
                condition_id="condition-zero",
                event_id="event-zero",
                outcome="Yes",
                entry_snapshot_id=2,
                exact_vwap=0.98,
                arm_prob_min=0.98,
                arm_prob_max=0.999,
                observed_at=datetime.utcnow(),
            ),
        ]
    )
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='MATCHED', "
            "latest_size_matched=5.102, needs_reconciliation=0 "
            "WHERE order_id='OID-positive-orphan'"
        )
    )
    session.execute(
        text(
            "INSERT INTO order_fills "
            "(submission_id, order_id, trade_id, bucket_index, status, side, "
            "size, price, fee_amount_usdc, matched_at, domain_error) VALUES "
            "(:submission_id, 'OID-positive-orphan', 'orphan-fill', 0, "
            "'CONFIRMED', 'BUY', 5.102, 0.98, 0.005, "
            "'2026-08-25T00:00:00Z', NULL)"
        ),
        {"submission_id": positive_submission},
    )
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='CANCELED', "
            "latest_size_matched=0, needs_reconciliation=0 "
            "WHERE order_id='OID-zero-orphan'"
        )
    )
    session.commit()

    repo = TradeRepository(session)
    repo.create_trade(
        condition_id="condition-quarantined",
        event_id="event-quarantined",
        outcome="Yes",
        token_id="token-quarantined",
        buy_order_id="OID-quarantined",
        buy_timestamp=datetime.utcnow(),
        status=TradeStatus.QUARANTINED,
        mode="live",
    )

    assert repo.get_entry_capacity_state() == {
        "open_positions": 1,
        "untracked_buy_reservations": 1,
        "total_reserved": 2,
    }
    assert repo.get_event_position_count("event-positive") == 1
    assert repo.get_event_position_count("event-zero") == 0
    assert repo.get_event_position_count("event-quarantined") == 1
    session.close()


def test_reconciled_positive_orphan_buy_is_atomically_recovered(tmp_path):
    db_path = tmp_path / "watermelon-orphan-recovery.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-peach")
    submission_id = _record_accepted_order(
        ledger,
        "OID-recover",
        token_id="token-recover",
        requested_size=5.102,
    )
    session = Session()
    snapshot = MarketSnapshot(
        condition_id="condition-recover",
        event_id="event-recover",
        token_id="token-recover",
        outcome="Yes",
        outcome_side="YES",
        result_kind="HOME",
        probability=0.98,
        liquidity=1000,
        volume_24h=2000,
        best_bid=0.97,
        best_ask=0.98,
        spread=0.01,
    )
    session.add(snapshot)
    session.flush()
    session.add(
        MarketCatalog(
            condition_id="condition-recover",
            market_slug="recover-market",
            question="Will the home team win?",
            event_id="event-recover",
            event_slug="recover-event",
            outcomes_json='["Yes","No"]',
            outcome_prices_json='["0.98","0.02"]',
            token_ids_json='["token-recover","token-no"]',
            tags_json="[]",
            neg_risk=1,
        )
    )
    session.add(
        EntryEpisode(
            token_id="token-recover",
            condition_id="condition-recover",
            event_id="event-recover",
            outcome="Yes",
            entry_snapshot_id=snapshot.id,
            exact_vwap=0.98,
            arm_prob_min=0.60,
            arm_prob_max=0.94,
            observed_at=datetime.utcnow(),
            game_start_time=datetime.utcnow(),
            in_play_hours=1.0,
        )
    )
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='MATCHED', "
            "latest_size_matched=5.102, making_amount=5.0, "
            "needs_reconciliation=0 "
            "WHERE order_id='OID-recover'"
        )
    )
    session.execute(
        text(
            "INSERT INTO order_fills "
            "(submission_id, order_id, trade_id, bucket_index, status, side, "
            "size, price, fee_amount_usdc, matched_at, domain_error) VALUES "
            "(:submission_id, 'OID-recover', 'recover-fill', 0, "
            "'CONFIRMED', 'BUY', 5.102, 0.98, 0.005, "
            "'2026-08-25T00:00:00Z', NULL)"
        ),
        {"submission_id": submission_id},
    )
    session.commit()

    repo = TradeRepository(session)
    trader = Trader(
        repo,
        SimpleNamespace(simulation_mode=False),
        TradingConfig(),
        simulation_mode=False,
    )
    stats = trader.recover_orphan_buys()

    assert stats == {
        "checked": 1,
        "recovered": 1,
        "evidence_gaps": 0,
        "identity_gaps": 0,
        "duplicate_token_submissions": 0,
    }
    trade = repo.get_all_trades()[0]
    assert trade.status == TradeStatus.HOLDING
    assert trade.buy_order_id == "OID-recover"
    assert trade.buy_shares == pytest.approx(5.102)
    assert trade.buy_confirmed_fee_usdc == pytest.approx(0.005)
    assert trade.outcome_side == "YES"
    assert trade.result_kind == "HOME"
    assert trade.take_profit_delta_at_buy == pytest.approx(0.03)
    assert trade.stop_loss_delta_at_buy == pytest.approx(0.10)
    episode = repo.get_entry_episode_by_token("token-recover")
    assert episode.trade_id == trade.id
    assert episode.execution_state == "ORPHAN_RECOVERED"
    assert repo.get_entry_capacity_state() == {
        "open_positions": 1,
        "untracked_buy_reservations": 0,
        "total_reserved": 1,
    }
    session.close()


def test_normal_trade_and_entry_episode_link_commit_atomically(tmp_path):
    db_path = tmp_path / "watermelon-entry-link.db"
    Session = init_database(str(db_path))
    session = Session()
    session.add(
        EntryEpisode(
            token_id="token-linked",
            condition_id="condition-linked",
            event_id="event-linked",
            outcome="Yes",
            entry_snapshot_id=1,
            exact_vwap=0.98,
            arm_prob_min=0.98,
            arm_prob_max=0.999,
            observed_at=datetime.utcnow(),
        )
    )
    session.commit()
    episode = session.query(EntryEpisode).one()
    repo = TradeRepository(session)

    trade = repo.create_trade(
        entry_episode_id=episode.id,
        condition_id="condition-linked",
        event_id="event-linked",
        outcome="Yes",
        token_id="token-linked",
        buy_order_id="OID-linked",
        buy_timestamp=datetime.utcnow(),
        status=TradeStatus.PENDING_BUY,
        mode="live",
    )

    session.expire_all()
    linked = repo.get_entry_episode_by_token("token-linked")
    assert linked.trade_id == trade.id
    assert linked.execution_state == "TRADE_CREATED"
    assert linked.execution_reason == "exact_order_submission_linked"
    session.close()


def test_market_catalog_canonicalizes_gamma_json_string_arrays(tmp_path):
    Session = init_database(str(tmp_path / "watermelon-catalog.db"))
    session = Session()
    repo = TradeRepository(session)

    repo.save_market_catalog(
        "condition-canonical",
        {
            "id": "market-canonical",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.98", "0.02"]',
            "clobTokenIds": '["yes-token", "no-token"]',
            "negRisk": True,
            "feesEnabled": True,
            "feeSchedule": {"rate": 0.05, "exponent": 1, "takerOnly": True},
        },
        commit=True,
    )

    catalog = session.get(MarketCatalog, "condition-canonical")
    assert catalog.outcomes_json == '["Yes","No"]'
    assert catalog.outcome_prices_json == '["0.98","0.02"]'
    assert catalog.token_ids_json == '["yes-token","no-token"]'
    session.close()


@pytest.mark.parametrize(
    "retryable_state",
    [
        "BLOCKED_GUARD",
        "PRE_SUBMISSION_CONTRACT_ERROR",
        "QUEUED_NO_POST",
        "NO_POST_RETRYABLE",
    ],
)
def test_entry_episode_retries_only_states_proven_to_precede_post(
    tmp_path,
    retryable_state,
):
    Session = init_database(str(tmp_path / f"watermelon-{retryable_state}.db"))
    session = Session()
    repo = TradeRepository(session)
    first = repo.claim_entry_episode(
        token_id="retry-token",
        condition_id="condition-retry",
        event_id="event-retry",
        outcome="Yes",
        entry_snapshot_id=1,
        exact_vwap=0.98,
        arm_prob_min=0.96,
        arm_prob_max=0.999,
        observed_at=datetime.utcnow(),
    )
    repo.commit()
    repo.mark_entry_episode_execution(first.id, state=retryable_state)

    retried = repo.claim_entry_episode(
        token_id="retry-token",
        condition_id="condition-retry",
        event_id="event-retry",
        outcome="Yes",
        entry_snapshot_id=2,
        exact_vwap=0.97,
        arm_prob_min=0.96,
        arm_prob_max=0.999,
        observed_at=datetime.utcnow(),
    )

    assert retried.id == first.id
    assert retried.entry_snapshot_id == 2
    assert retried.exact_vwap == pytest.approx(0.97)
    assert retried.execution_state == "RETRY_OBSERVED"
    repo.mark_entry_episode_execution(retried.id, state="NOT_EXECUTED")
    assert (
        repo.claim_entry_episode(
            token_id="retry-token",
            condition_id="condition-retry",
            event_id="event-retry",
            outcome="Yes",
            entry_snapshot_id=3,
            exact_vwap=0.98,
            arm_prob_min=0.96,
            arm_prob_max=0.999,
            observed_at=datetime.utcnow(),
        )
        is None
    )
    session.close()


def test_entry_episode_queue_is_durable_and_not_an_attempt(tmp_path):
    Session = init_database(str(tmp_path / "watermelon-queued-no-post.db"))
    session = Session()
    repo = TradeRepository(session)
    first = repo.claim_entry_episode(
        token_id="queued-token-1",
        condition_id="condition-1",
        event_id="event-1",
        outcome="Yes",
        entry_snapshot_id=1,
        exact_vwap=0.98,
        arm_prob_min=0.96,
        arm_prob_max=0.999,
        observed_at=datetime.utcnow(),
    )
    second = repo.claim_entry_episode(
        token_id="queued-token-2",
        condition_id="condition-2",
        event_id="event-2",
        outcome="Yes",
        entry_snapshot_id=2,
        exact_vwap=0.97,
        arm_prob_min=0.96,
        arm_prob_max=0.999,
        observed_at=datetime.utcnow(),
    )
    repo.commit()

    repo.mark_entry_episodes_queued_no_post(
        [first.id, second.id],
        reason="selected_for_cycle_before_any_submission",
    )

    session.expire_all()
    queued = [
        repo.get_entry_episode_by_token("queued-token-1"),
        repo.get_entry_episode_by_token("queued-token-2"),
    ]
    assert {episode.execution_state for episode in queued} == {"QUEUED_NO_POST"}
    assert all(episode.last_attempted_at is None for episode in queued)
    retried = repo.claim_entry_episode(
        token_id="queued-token-2",
        condition_id="condition-2",
        event_id="event-2",
        outcome="Yes",
        entry_snapshot_id=3,
        exact_vwap=0.975,
        arm_prob_min=0.96,
        arm_prob_max=0.999,
        observed_at=datetime.utcnow(),
    )
    assert retried.id == second.id
    assert retried.execution_state == "RETRY_OBSERVED"
    session.close()


def test_failed_entry_link_rolls_back_trade_before_failure_annotation(tmp_path):
    db_path = tmp_path / "watermelon-entry-link-rollback.db"
    Session = init_database(str(db_path))
    session = Session()
    repo = TradeRepository(session)
    existing = repo.create_trade(
        condition_id="condition-existing",
        event_id="event-existing",
        outcome="Yes",
        token_id="token-existing",
        buy_order_id="OID-existing",
        buy_timestamp=datetime.utcnow(),
        status=TradeStatus.PENDING_BUY,
        mode="live",
    )
    episode = EntryEpisode(
        token_id="token-claimed",
        condition_id="condition-claimed",
        event_id="event-claimed",
        outcome="Yes",
        entry_snapshot_id=1,
        exact_vwap=0.98,
        arm_prob_min=0.98,
        arm_prob_max=0.999,
        observed_at=datetime.utcnow(),
        trade_id=existing.id,
    )
    session.add(episode)
    session.commit()

    with pytest.raises(ValueError, match="already linked"):
        repo.create_trade(
            entry_episode_id=episode.id,
            condition_id="condition-ghost",
            event_id="event-ghost",
            outcome="Yes",
            token_id="token-ghost",
            buy_order_id="OID-ghost",
            buy_timestamp=datetime.utcnow(),
            status=TradeStatus.PENDING_BUY,
            mode="live",
        )

    # This mirrors Bot.run_cycle's exception annotation. It must not commit a
    # Trade left pending by the failed atomic link.
    repo.mark_entry_episode_execution(
        episode.id,
        state="EXECUTION_EXCEPTION",
        reason="ValueError",
    )
    assert [trade.token_id for trade in repo.get_all_trades()] == [
        "token-existing"
    ]
    session.close()


def test_open_buy_evidence_gap_counts_only_incomplete_owned_exposure(tmp_path):
    db_path = tmp_path / "watermelon-open-buy-evidence.db"
    Session = init_database(str(db_path))
    session = Session()
    repo = TradeRepository(session)
    repo.create_trade(
        condition_id="condition-complete",
        outcome="Yes",
        token_id="token-complete",
        buy_order_id="OID-complete",
        buy_timestamp=datetime.utcnow(),
        buy_confirmed_size=5.1,
        buy_confirmed_vwap=0.98,
        buy_confirmed_fee_usdc=0.0,
        status=TradeStatus.HOLDING,
        mode="live",
    )
    repo.create_trade(
        condition_id="condition-gap",
        outcome="Yes",
        token_id="token-gap",
        buy_order_id="OID-gap",
        buy_timestamp=datetime.utcnow(),
        buy_confirmed_size=5.1,
        buy_confirmed_vwap=0.98,
        buy_confirmed_fee_usdc=None,
        status=TradeStatus.HOLDING,
        mode="live",
    )
    repo.create_trade(
        condition_id="condition-resolved",
        outcome="Yes",
        token_id="token-resolved",
        buy_order_id="OID-resolved",
        buy_timestamp=datetime.utcnow(),
        status=TradeStatus.RESOLVED,
        mode="live",
    )

    assert repo.get_open_buy_evidence_gap_count() == 1
    session.close()


def test_economic_guard_replaces_legacy_resolution_with_confirmed_sell_ledger(
    tmp_path,
):
    """A confirmed SELL must not disappear behind a later RESOLVED Trade row."""
    db_path = tmp_path / "watermelon-economic-ledger-override.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-peach")
    sell_submission = _record_accepted_order(
        ledger,
        "OID-legacy-stop",
        side="SELL",
        token_id="token-legacy-stop",
        requested_size=5.09,
    )
    session = Session()
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='MATCHED', "
            "latest_size_matched=5.09, needs_reconciliation=0 "
            "WHERE order_id='OID-legacy-stop'"
        )
    )
    session.execute(
        text(
            "INSERT INTO order_fills "
            "(submission_id, order_id, trade_id, bucket_index, status, side, "
            "size, price, fee_amount_usdc, matched_at, domain_error) VALUES "
            "(:submission_id, 'OID-legacy-stop', 'legacy-stop-fill', 0, "
            "'CONFIRMED', 'SELL', 5.09, 0.001, 0.00025, "
            "'2026-08-28T00:00:00Z', NULL)"
        ),
        {"submission_id": sell_submission},
    )
    session.commit()

    repo = TradeRepository(session)
    buy_size = 5.102
    buy_vwap = 0.98
    buy_fee = 0.005
    settlement = (1.0 - buy_vwap) * buy_size - buy_fee
    trade = repo.create_trade(
        condition_id="condition-legacy-stop",
        outcome="Yes",
        token_id="token-legacy-stop",
        buy_price=buy_vwap,
        buy_shares=buy_size,
        buy_order_id="OID-legacy-buy",
        buy_timestamp=datetime.utcnow(),
        buy_confirmed_size=buy_size,
        buy_confirmed_vwap=buy_vwap,
        buy_confirmed_fee_usdc=buy_fee,
        status=TradeStatus.RESOLVED,
        mode="live",
        resolution_value=1.0,
        settlement_pnl_assumption=settlement,
        settlement_assumption_basis=(
            "confirmed_buy_fill_net_known_buy_fee"
        ),
    )

    guard = repo.get_economic_pnl_guard()

    sold_size = 5.09
    allocated_buy_fee = buy_fee * sold_size / buy_size
    ledger_sell_pnl = (
        sold_size * 0.001
        - 0.00025
        - buy_vwap * sold_size
        - allocated_buy_fee
    )
    residual_resolution_pnl = settlement * (buy_size - sold_size) / buy_size
    assert guard["recorded_realized_pnl"] == 0.0
    assert guard["recorded_settlement_pnl"] == pytest.approx(settlement)
    assert guard["confirmed_sell_pnl"] == pytest.approx(ledger_sell_pnl)
    assert guard["proven_resolution_pnl"] == pytest.approx(
        residual_resolution_pnl
    )
    assert guard["economic_pnl"] == pytest.approx(
        ledger_sell_pnl + residual_resolution_pnl
    )
    assert guard["execution_override_count"] == 1
    assert guard["evidence_gaps"] == 0

    # This is a read-only safety overlay; historical evidence stays immutable.
    session.refresh(trade)
    assert trade.status == TradeStatus.RESOLVED
    assert trade.sell_order_id is None
    assert trade.realized_pnl is None
    assert trade.settlement_pnl_assumption == pytest.approx(settlement)
    session.close()


def test_economic_guard_blocks_on_ambiguous_token_only_sell_mapping(tmp_path):
    db_path = tmp_path / "watermelon-economic-ledger-ambiguous.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-peach")
    sell_submission = _record_accepted_order(
        ledger,
        "OID-ambiguous-stop",
        side="SELL",
        token_id="token-ambiguous-stop",
        requested_size=5.0,
    )
    session = Session()
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='MATCHED', "
            "latest_size_matched=5.0, needs_reconciliation=0 "
            "WHERE order_id='OID-ambiguous-stop'"
        )
    )
    session.execute(
        text(
            "INSERT INTO order_fills "
            "(submission_id, order_id, trade_id, bucket_index, status, side, "
            "size, price, fee_amount_usdc, matched_at, domain_error) VALUES "
            "(:submission_id, 'OID-ambiguous-stop', 'ambiguous-stop-fill', 0, "
            "'CONFIRMED', 'SELL', 5.0, 0.70, 0.01, "
            "'2026-08-28T00:00:00Z', NULL)"
        ),
        {"submission_id": sell_submission},
    )
    session.commit()
    repo = TradeRepository(session)
    for suffix in ("a", "b"):
        repo.create_trade(
            condition_id=f"condition-{suffix}",
            outcome="Yes",
            token_id="token-ambiguous-stop",
            buy_order_id=f"OID-buy-{suffix}",
            buy_timestamp=datetime.utcnow(),
            buy_confirmed_size=5.0,
            buy_confirmed_vwap=0.96,
            buy_confirmed_fee_usdc=0.01,
            status=TradeStatus.RESOLVED,
            mode="live",
            settlement_pnl_assumption=0.19,
        )

    guard = repo.get_economic_pnl_guard()

    assert guard["execution_override_count"] == 0
    assert guard["execution_adjustment_pnl"] == 0.0
    assert guard["evidence_gaps"] == 1
    session.close()


def test_economic_guard_blocks_matched_sell_without_confirmed_fill(tmp_path):
    db_path = tmp_path / "watermelon-economic-ledger-missing-fill.db"
    Session = init_database(str(db_path))
    ledger = ExecutionLedger(db_path, strategy_name="golden-peach")
    _record_accepted_order(
        ledger,
        "OID-matched-without-fill",
        side="SELL",
        token_id="token-matched-without-fill",
        requested_size=5.0,
    )
    session = Session()
    session.execute(
        text(
            "UPDATE order_submissions SET latest_order_status='MATCHED', "
            "latest_size_matched=5.0, needs_reconciliation=0 "
            "WHERE order_id='OID-matched-without-fill'"
        )
    )
    session.commit()

    guard = TradeRepository(session).get_economic_pnl_guard()

    assert guard["economic_pnl"] == 0.0
    assert guard["execution_override_count"] == 0
    assert guard["evidence_gaps"] == 1
    session.close()
