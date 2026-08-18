"""Exact execution-ledger evidence for the live Cherry lifecycle."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from polybot.db.models import init_database
from polybot.db.repository import TradeRepository


def make_repo(tmp_path):
    Session = init_database(str(tmp_path / "fills.db"))
    session = Session()
    return session, TradeRepository(session)


def create_ledger_tables(session):
    session.execute(
        text(
            """
            CREATE TABLE order_submissions (
                submission_id TEXT,
                order_id TEXT,
                side TEXT,
                requested_size REAL,
                latest_order_status TEXT,
                latest_size_matched REAL,
                latest_status_domain_error TEXT,
                needs_reconciliation INTEGER,
                reconciliation_error TEXT,
                simulation INTEGER,
                making_amount REAL,
                taking_amount REAL
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE order_status_events (
                submission_id TEXT,
                observed_at TEXT,
                status TEXT,
                original_size REAL,
                size_matched REAL,
                domain_error TEXT
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE order_fills (
                submission_id TEXT,
                order_id TEXT,
                status TEXT,
                side TEXT,
                size REAL,
                price REAL,
                liquidity_role TEXT,
                fee_rate_bps REAL,
                fee_amount_usdc REAL,
                matched_at TEXT,
                domain_error TEXT
            )
            """
        )
    )
    session.commit()


def insert_submission(
    session,
    *,
    order_id="order-1",
    side="BUY",
    status="LIVE",
    requested=5.0,
    matched=0.0,
    reconciliation=0,
    original=None,
    making_amount=None,
    taking_amount=None,
):
    session.execute(
        text(
            "INSERT INTO order_submissions "
            "(submission_id, order_id, side, requested_size, "
            "latest_order_status, latest_size_matched, "
            "latest_status_domain_error, needs_reconciliation, "
            "reconciliation_error, simulation, making_amount, taking_amount) VALUES "
            "(:submission, :order_id, :side, :requested, :status, :matched, "
            "NULL, :reconciliation, NULL, 0, :making_amount, :taking_amount)"
        ),
        {
            "submission": f"submission-{order_id}",
            "order_id": order_id,
            "side": side,
            "requested": requested,
            "status": status,
            "matched": matched,
            "reconciliation": reconciliation,
            "making_amount": making_amount,
            "taking_amount": taking_amount,
        },
    )
    session.execute(
        text(
            "INSERT INTO order_status_events "
            "(submission_id, observed_at, status, original_size, "
            "size_matched, domain_error) VALUES "
            "(:submission, '2026-08-14T00:00:00Z', :status, "
            ":original, :matched, NULL)"
        ),
        {
            "submission": f"submission-{order_id}",
            "status": status,
            "original": requested if original is None else original,
            "matched": matched,
        },
    )
    session.commit()


def insert_fill(
    session,
    *,
    order_id="order-1",
    side="BUY",
    size=5.0,
    price=0.95,
    liquidity_role=None,
    fee_rate=0,
    fee=None,
):
    session.execute(
        text(
            "INSERT INTO order_fills "
            "(submission_id, order_id, status, side, size, price, "
            "liquidity_role, fee_rate_bps, fee_amount_usdc, matched_at, "
            "domain_error) VALUES "
            "(:submission, :order_id, 'CONFIRMED', :side, :size, :price, "
            ":liquidity_role, :fee_rate, :fee, '2026-08-14T00:00:00Z', NULL)"
        ),
        {
            "submission": f"submission-{order_id}",
            "order_id": order_id,
            "side": side,
            "size": size,
            "price": price,
            "liquidity_role": liquidity_role,
            "fee_rate": fee_rate,
            "fee": fee,
        },
    )
    session.commit()


def test_missing_ledger_fails_closed(tmp_path):
    session, repo = make_repo(tmp_path)
    evidence = repo.get_exact_buy_fill_evidence("accepted-order")
    assert evidence.state == "unavailable"
    assert evidence.detail == "ledger_tables_missing"
    session.close()


def test_accepted_live_zero_fill_is_pending_not_holding(tmp_path):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(
        session,
        order_id="live-zero",
        status="LIVE",
        matched=0,
        reconciliation=1,
    )
    evidence = repo.get_exact_buy_fill_evidence("live-zero")
    assert evidence.state == "pending"
    assert evidence.order_status == "LIVE"
    assert evidence.has_confirmed_fill is False
    session.close()


@pytest.mark.parametrize("status", ["CANCELED", "CANCELLED", "INVALID"])
def test_terminal_exact_zero_proves_unfilled(tmp_path, status):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(session, order_id="zero", status=status, matched=0)
    evidence = repo.get_exact_buy_fill_evidence("zero")
    assert evidence.state == "terminal_zero_fill"
    assert evidence.confirmed_size == 0.0
    session.close()


def test_matched_quantized_fill_and_explicit_zero_fee_are_complete(tmp_path):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(
        session,
        order_id="quantized",
        status="MATCHED",
        requested=5.224660397,
        matched=5.22,
        original=5.22,
    )
    insert_fill(session, order_id="quantized", size=5.22, fee_rate=0, fee=None)
    evidence = repo.get_exact_buy_fill_evidence("quantized")
    assert evidence.state == "confirmed"
    assert evidence.confirmed_size == 5.22
    assert evidence.has_reconciled_full_fill is True
    assert evidence.has_reconciled_matched_fill is True
    assert evidence.fee_complete is True
    assert evidence.confirmed_fee_usdc == 0.0
    session.close()


def test_terminal_partial_fill_reconciles_actual_matched_size(tmp_path):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(
        session,
        order_id="terminal-partial",
        status="CANCELED_MARKET_RESOLVED",
        requested=5.780347,
        matched=4.72,
    )
    insert_fill(
        session,
        order_id="terminal-partial",
        size=4.72,
        fee_rate=0,
        fee=None,
    )
    evidence = repo.get_exact_buy_fill_evidence("terminal-partial")
    assert evidence.state == "confirmed"
    assert evidence.confirmed_size == pytest.approx(4.72)
    assert evidence.has_reconciled_matched_fill is True
    assert evidence.has_reconciled_full_fill is False
    assert evidence.detail == "confirmed_reconciled_terminal_partial_fill"
    session.close()


def test_matched_status_does_not_hide_large_original_size_shortfall(tmp_path):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(
        session,
        order_id="matched-partial",
        side="SELL",
        status="MATCHED",
        requested=5.78,
        original=5.78,
        matched=1.82,
    )
    insert_fill(
        session,
        order_id="matched-partial",
        side="SELL",
        size=1.82,
        fee_rate=0,
        fee=None,
    )
    evidence = repo.get_exact_sell_fill_evidence("matched-partial")
    assert evidence.has_reconciled_matched_fill is True
    assert evidence.has_reconciled_full_fill is False
    assert evidence.submitted_size == pytest.approx(5.78)
    assert evidence.submitted_size_source == "order_status_original_size"
    session.close()


def test_nonzero_rate_without_fee_amount_remains_incomplete(tmp_path):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(
        session, order_id="fee-gap", status="MATCHED", matched=5.0
    )
    insert_fill(
        session,
        order_id="fee-gap",
        liquidity_role="MAKER",
        fee_rate=30,
        fee=None,
    )
    evidence = repo.get_exact_buy_fill_evidence("fee-gap")
    assert evidence.has_reconciled_full_fill is True
    assert evidence.fee_complete is False
    assert evidence.confirmed_fee_usdc is None
    session.close()


def test_maker_fill_with_omitted_fee_metadata_is_known_zero(tmp_path):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(
        session, order_id="maker-zero", side="SELL", status="MATCHED", matched=5.0
    )
    insert_fill(
        session,
        order_id="maker-zero",
        side="SELL",
        liquidity_role="maker",
        fee_rate=None,
        fee=None,
    )
    evidence = repo.get_exact_sell_fill_evidence("maker-zero")
    assert evidence.has_reconciled_full_fill is True
    assert evidence.fee_complete is True
    assert evidence.confirmed_fee_usdc == 0.0
    session.close()


@pytest.mark.parametrize("liquidity_role", [None, "TAKER"])
def test_missing_fee_metadata_without_maker_role_remains_incomplete(
    tmp_path, liquidity_role
):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(
        session, order_id="unknown-fee", status="MATCHED", matched=5.0
    )
    insert_fill(
        session,
        order_id="unknown-fee",
        liquidity_role=liquidity_role,
        fee_rate=None,
        fee=None,
    )
    evidence = repo.get_exact_buy_fill_evidence("unknown-fee")
    assert evidence.has_reconciled_full_fill is True
    assert evidence.fee_complete is False
    assert evidence.confirmed_fee_usdc is None
    session.close()


def test_side_mismatch_never_authorizes_a_fill(tmp_path):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(
        session,
        order_id="sell-order",
        side="SELL",
        status="MATCHED",
        matched=5.0,
    )
    insert_fill(session, order_id="sell-order", side="SELL")
    evidence = repo.get_exact_buy_fill_evidence("sell-order")
    assert evidence.state == "unavailable"
    assert evidence.detail == "submission_side_mismatch"
    session.close()
