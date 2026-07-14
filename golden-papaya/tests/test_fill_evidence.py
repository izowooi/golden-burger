"""Exact execution-ledger BUY fill evidence for live settlement."""

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
                simulation INTEGER
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
    status="LIVE",
    matched=0.0,
    reconciliation=0,
    side="BUY",
    simulation=0,
    requested_size=None,
):
    if requested_size is None:
        requested_size = matched if matched and matched > 0 else 5.0
    session.execute(
        text(
            "INSERT INTO order_submissions "
            "(submission_id, order_id, side, requested_size, "
            "latest_order_status, latest_size_matched, "
            "latest_status_domain_error, needs_reconciliation, "
            "reconciliation_error, simulation) VALUES "
            "(:submission_id, :order_id, :side, :requested_size, :status, "
            ":matched, NULL, :reconciliation, NULL, :simulation)"
        ),
        {
            "submission_id": f"submission-{order_id}",
            "order_id": order_id,
            "side": side,
            "requested_size": requested_size,
            "status": status,
            "matched": matched,
            "reconciliation": reconciliation,
            "simulation": simulation,
        },
    )
    session.commit()


def insert_fill(
    session,
    *,
    order_id="order-1",
    status="CONFIRMED",
    side="BUY",
    size=1.0,
    price=0.95,
    fee=0.001,
    matched_at="2026-07-14T00:00:00Z",
    domain_error=None,
):
    session.execute(
        text(
            "INSERT INTO order_fills VALUES "
            "(:submission_id, :order_id, :status, :side, :size, :price, "
            ":fee, :matched_at, :domain_error)"
        ),
        {
            "submission_id": f"submission-{order_id}",
            "order_id": order_id,
            "status": status,
            "side": side,
            "size": size,
            "price": price,
            "fee": fee,
            "matched_at": matched_at,
            "domain_error": domain_error,
        },
    )
    session.commit()


def test_missing_ledger_or_order_is_unavailable_not_an_inferred_fill(tmp_path):
    session, repo = make_repo(tmp_path)
    evidence = repo.get_exact_buy_fill_evidence("accepted-local-order")
    assert evidence.state == "unavailable"
    assert evidence.detail == "ledger_tables_missing"
    session.close()


def test_accepted_live_gtc_without_confirmed_fill_remains_pending_intent(tmp_path):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(
        session,
        order_id="accepted-order",
        status="LIVE",
        matched=0,
        reconciliation=1,
    )

    evidence = repo.get_exact_buy_fill_evidence("accepted-order")

    assert evidence.state == "pending"
    assert evidence.has_confirmed_fill is False
    assert evidence.order_status == "LIVE"
    assert evidence.detail == "reconciliation_pending"
    assert evidence.confirmed_size is None
    session.close()


@pytest.mark.parametrize("status", ["CANCELED", "CANCELLED", "INVALID"])
def test_terminal_status_plus_exact_zero_size_proves_zero_fill(tmp_path, status):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(
        session,
        order_id="zero-order",
        status=status,
        matched=0,
    )

    evidence = repo.get_exact_buy_fill_evidence("zero-order")

    assert evidence.state == "terminal_zero_fill"
    assert evidence.confirmed_size == 0.0
    assert evidence.order_status == status
    session.close()


def test_exact_confirmed_buy_fills_aggregate_size_vwap_and_known_fees(tmp_path):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(
        session,
        order_id="filled-order",
        status="MATCHED",
        matched=3,
    )
    insert_fill(
        session,
        order_id="filled-order",
        size=1,
        price=0.95,
        fee=0.001,
        matched_at="2026-07-14T00:00:00Z",
    )
    insert_fill(
        session,
        order_id="filled-order",
        size=2,
        price=0.96,
        fee=0.002,
        matched_at="2026-07-14T00:01:00Z",
    )

    evidence = repo.get_exact_buy_fill_evidence("filled-order")

    assert evidence.state == "confirmed"
    assert evidence.has_confirmed_fill is True
    assert evidence.confirmed_size == 3.0
    assert evidence.confirmed_vwap == pytest.approx((0.95 + 2 * 0.96) / 3)
    assert evidence.confirmed_fee_usdc == pytest.approx(0.003)
    assert evidence.fee_complete is True
    assert evidence.matched_at == "2026-07-14T00:01:00Z"
    session.close()


def test_confirmed_fill_with_unknown_fee_preserves_gross_only_evidence(tmp_path):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(session, order_id="fee-gap", status="MATCHED", matched=1)
    insert_fill(session, order_id="fee-gap", fee=None)

    evidence = repo.get_exact_buy_fill_evidence("fee-gap")

    assert evidence.state == "confirmed"
    assert evidence.confirmed_size == 1.0
    assert evidence.confirmed_fee_usdc is None
    assert evidence.fee_complete is False
    session.close()


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"side": "SELL"}, "confirmed_fill_contract_invalid"),
        ({"size": 0}, "confirmed_fill_contract_invalid"),
        ({"price": 1.1}, "confirmed_fill_contract_invalid"),
        ({"domain_error": "bad exact identity"}, "confirmed_fill_contract_invalid"),
    ],
)
def test_malformed_confirmed_fill_fails_closed(tmp_path, overrides, detail):
    session, repo = make_repo(tmp_path)
    create_ledger_tables(session)
    insert_submission(session, order_id="malformed", status="MATCHED", matched=1)
    insert_fill(session, order_id="malformed", **overrides)

    evidence = repo.get_exact_buy_fill_evidence("malformed")

    assert evidence.state == "unavailable"
    assert evidence.detail == detail
    session.close()
