import sqlite3

import pytest

from polybot.config import TradingConfig
from polybot.db.models import Trade, TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.trader import Trader

from tests.test_time_and_order_safety import FakeClob, make_candidate


def _submission(
    connection,
    submission_id,
    *,
    order_id=None,
    response_status="SUBMIT_OUTCOME_UNKNOWN",
    needs_reconciliation=0,
    latest_order_status=None,
    latest_size_matched=None,
    outcome_resolution=None,
    outcome_resolved_at=None,
    outcome_resolution_reason=None,
):
    connection.execute(
        """
        INSERT INTO order_submissions (
            submission_id, strategy_name, order_id, token_id, side,
            requested_price, requested_size, submitted_at, simulation,
            success, response_status, associated_trade_ids_json,
            latest_order_status, latest_size_matched, needs_reconciliation,
            outcome_resolution, outcome_resolved_at, outcome_resolution_reason
        ) VALUES (?, 'golden-cherry', ?, ?, 'BUY', 0.8, 6.25,
                  '2026-09-04T00:00:00Z', 0, ?, ?, '[]', ?, ?, ?, ?, ?, ?)
        """,
        (
            submission_id,
            order_id,
            f"token-{submission_id}",
            int(order_id is not None),
            response_status,
            latest_order_status,
            latest_size_matched,
            needs_reconciliation,
            outcome_resolution,
            outcome_resolved_at,
            outcome_resolution_reason,
        ),
    )


def test_restart_reserves_unknown_and_untracked_accepted_buys(tmp_path):
    db_path = tmp_path / "trades.db"
    Session = init_database(str(db_path))
    # Initializing the live ledger schema models a first process that persisted
    # evidence and then died before it could create a managed Trade.
    from polybot_observability import ExecutionLedger

    ExecutionLedger(db_path, strategy_name="golden-cherry")
    with sqlite3.connect(db_path) as connection:
        _submission(connection, "unknown-post")
        _submission(
            connection,
            "accepted-before-crash",
            order_id="ORDER-ORPHAN",
            response_status="ACCEPTED",
            needs_reconciliation=1,
        )
        _submission(
            connection,
            "unknown-proved-no-order",
            outcome_resolution="NO_ORDER_CREATED",
            outcome_resolved_at="2026-09-04T00:10:00Z",
            outcome_resolution_reason="operator verified no venue order",
        )
        _submission(
            connection,
            "proved-zero",
            order_id="ORDER-ZERO",
            response_status="ACCEPTED",
            needs_reconciliation=0,
            latest_order_status="CANCELED",
            latest_size_matched=0,
        )
        _submission(
            connection,
            "managed",
            order_id="ORDER-MANAGED",
            response_status="ACCEPTED",
            needs_reconciliation=1,
        )

    with Session() as session:
        session.add(
            Trade(
                condition_id="managed-condition",
                token_id="token-managed",
                buy_order_id="ORDER-MANAGED",
                buy_amount=5.0,
                status=TradeStatus.PENDING_BUY,
            )
        )
        session.commit()

    # A new repository/session proves that reservations survive restart and do
    # not rely on in-memory cycle state.
    with Session() as restarted_session:
        repo = TradeRepository(restarted_session)
        exposure = repo.get_exposure_summary()

        assert exposure["managed_open_position_count"] == 1
        assert exposure["untracked_buy_reservation_count"] == 2
        assert exposure["untracked_buy_unknown_outcome_count"] == 1
        assert exposure["untracked_buy_reconciliation_count"] == 1
        assert exposure["untracked_buy_reservation_notional_usdc"] == 10.0
        assert exposure["reserved_position_count"] == 3
        assert exposure["reserved_open_notional_usdc"] == 15.0


def test_unknown_post_reservation_can_fail_closed_without_touching_clob(tmp_path):
    db_path = tmp_path / "trades.db"
    Session = init_database(str(db_path))
    from polybot_observability import ExecutionLedger

    ExecutionLedger(db_path, strategy_name="golden-cherry")
    with sqlite3.connect(db_path) as connection:
        _submission(connection, "unknown-post")

    with Session() as session:
        repo = TradeRepository(session)
        clob = FakeClob(midpoint=0.80)
        trader = Trader(repo, clob, TradingConfig(max_positions=1))

        assert trader.execute_buy(make_candidate()) is None
        assert clob.orders == []


def test_unknown_post_quarantine_remains_exact_token_and_side_local(tmp_path):
    from polybot_observability import (
        ExecutionLedger,
        UnresolvedTokenSubmissionError,
    )

    ledger = ExecutionLedger(tmp_path / "trades.db", strategy_name="golden-cherry")
    submission_id = ledger.record_intent(
        token_id="token-a",
        side="BUY",
        requested_price=0.8,
        requested_size=6.25,
        simulation=False,
    )
    ReadTimeout = type("ReadTimeout", (Exception,), {})
    assert (
        ledger.record_submission_error(submission_id, ReadTimeout("timeout"))
        == "SUBMIT_OUTCOME_UNKNOWN"
    )

    with pytest.raises(UnresolvedTokenSubmissionError):
        ledger.assert_submission_allowed(token_id="token-a", side="BUY")
    ledger.assert_submission_allowed(token_id="token-a", side="SELL")
    ledger.assert_submission_allowed(token_id="token-b", side="BUY")


def test_open_order_absence_never_autoresolves_unknown_sell_intent():
    class Ledger:
        def pending_submissions(self):
            return []

        def unresolved_submission_count(self, *, side):
            assert side == "SELL"
            return 1

        def autoresolve_stale_sell_intents(self, **kwargs):
            raise AssertionError("open-order absence must not resolve SELL intent")

    class Client:
        def get_open_orders(self, *args, **kwargs):
            raise AssertionError("global open-order absence must not be consulted")

    from polybot.api.clob_client import ClobClientWrapper

    wrapper = object.__new__(ClobClientWrapper)
    wrapper.simulation_mode = False
    wrapper.execution_ledger = Ledger()
    wrapper._initialized = True
    wrapper._client = Client()

    stats = wrapper.reconcile_order_ledger()

    assert stats["unknown_sell_intents_preserved"] == 1
    assert stats["checked"] == 0
