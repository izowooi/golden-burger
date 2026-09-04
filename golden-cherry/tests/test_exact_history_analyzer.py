import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest
from polybot_observability import ExecutionLedger

from polybot.db.models import Trade, TradeStatus, init_database
from polybot.db.repository import TradeRepository
from report import load_trades
from scripts.analyze_exact_history import analyze, parse_exact_utc


EXACT_BASIS = "exact_reconciled_buy_sell_confirmed_fills_net_known_fees"


def _add_confirmed_order(db_path, order_id, side, token_id, *, run_id="run-1"):
    ledger = ExecutionLedger(db_path, strategy_name="golden-cherry")
    submission_id = ledger.record_submission(
        token_id=token_id,
        side=side,
        requested_price=0.8 if side == "BUY" else 0.9,
        requested_size=5.0,
        result={"success": True, "orderID": order_id, "status": "ACCEPTED"},
        simulation=False,
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE order_submissions
            SET run_id = ?, latest_order_status = 'MATCHED',
                latest_size_matched = 5.0, needs_reconciliation = 0
            WHERE submission_id = ?
            """,
            (run_id, submission_id),
        )
        connection.execute(
            """
            INSERT INTO order_fills (
                submission_id, order_id, trade_id, bucket_index, status,
                side, size, price, liquidity_role, fee_rate_bps,
                fee_amount_usdc, matched_at, last_update, transaction_hash,
                domain_error
            ) VALUES (?, ?, ?, 0, 'CONFIRMED', ?, 5.0, ?, 'MAKER', 0,
                      0, '2026-08-15T00:00:00Z', '2026-08-15T00:00:00Z',
                      '0xtx', NULL)
            """,
            (
                submission_id,
                order_id,
                f"venue-{order_id}",
                side,
                0.8 if side == "BUY" else 0.9,
            ),
        )


def _fixture_db(tmp_path):
    db_path = tmp_path / "history.db"
    Session = init_database(str(db_path))
    ExecutionLedger(db_path, strategy_name="golden-cherry")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE run_audits (
                run_id TEXT PRIMARY KEY, config_hash TEXT, git_commit TEXT,
                mode TEXT, job_name TEXT, started_at TEXT, finished_at TEXT,
                status TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO order_submissions (
                submission_id, strategy_name, order_id, token_id, side,
                requested_price, requested_size, submitted_at, simulation,
                success, response_status, associated_trade_ids_json,
                needs_reconciliation
            ) VALUES (
                'unknown-active', 'golden-cherry', NULL, 'unknown-token-active',
                'BUY', 0.8, 6.25, '2026-08-20T00:00:00Z', 0, 0,
                'SUBMIT_OUTCOME_UNKNOWN', '[]', 0
            )
            """
        )
        connection.execute(
            """
            INSERT INTO order_submissions (
                submission_id, strategy_name, order_id, token_id, side,
                requested_price, requested_size, submitted_at, simulation,
                success, response_status, associated_trade_ids_json,
                needs_reconciliation, outcome_resolution, outcome_resolved_at,
                outcome_resolution_reason
            ) VALUES (
                'unknown-proved-no-order', 'golden-cherry', NULL,
                'unknown-token-resolved', 'BUY', 0.8, 6.25,
                '2026-08-20T00:01:00Z', 0, 0, 'SUBMIT_OUTCOME_UNKNOWN',
                '[]', 0, 'NO_ORDER_CREATED', '2026-08-21T00:00:00Z',
                'operator verified no venue order'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO run_audits VALUES (
                'run-1', 'config-a', 'commit-a', 'live', 'default',
                '2026-08-15T00:00:00Z', '2026-08-15T00:05:00Z', 'SUCCESS'
            )
            """
        )

    for order_id, side, token in (
        ("BUY-1", "BUY", "token-1"),
        ("SELL-1", "SELL", "token-1"),
        ("BUY-RES", "BUY", "token-res"),
    ):
        _add_confirmed_order(db_path, order_id, side, token)

    with Session() as session:
        session.add_all(
            [
                Trade(
                    condition_id="condition-exact",
                    market_slug="event-a-market",
                    question="Will A happen?",
                    outcome="Yes",
                    token_id="token-1",
                    buy_order_id="BUY-1",
                    sell_order_id="SELL-1",
                    buy_amount=4.0,
                    buy_price=0.8,
                    sell_price=0.9,
                    sell_timestamp=datetime(2026, 8, 15),
                    sell_fill_matched_at=str(
                        int(
                            datetime(
                                2026, 8, 15, 0, 1, tzinfo=timezone.utc
                            ).timestamp()
                        )
                    ),
                    realized_pnl=0.5,
                    pnl_basis=EXACT_BASIS,
                    status=TradeStatus.COMPLETED,
                ),
                Trade(
                    condition_id="condition-legacy",
                    market_slug="legacy",
                    question="Legacy estimate?",
                    outcome="Yes",
                    token_id="token-legacy",
                    buy_amount=100.0,
                    sell_timestamp=datetime(2026, 8, 16),
                    realized_pnl=999.0,
                    pnl_basis=None,
                    status=TradeStatus.COMPLETED,
                ),
                Trade(
                    condition_id="condition-resolution",
                    market_slug="event-a-resolution",
                    question="Will A happen?",
                    outcome="Yes",
                    token_id="token-res",
                    buy_order_id="BUY-RES",
                    buy_amount=4.0,
                    status=TradeStatus.RESOLVED,
                    resolution_observed_at=datetime(2026, 8, 15, 0, 2),
                    resolution_evidence=(
                        "gamma_closed_final_outcome_prices_exact_token+"
                        "execution_ledger_exact_confirmed_buy"
                    ),
                    resolution_outcome="Yes",
                    resolution_value=1.0,
                    resolution_status="resolved",
                    resolution_confirmed_buy_size=5.0,
                    resolution_confirmed_buy_vwap=0.8,
                    resolution_confirmed_buy_fee_usdc=0.0,
                    resolution_position_size=5.0,
                    settlement_pnl_assumption=1.0,
                    settlement_assumption_basis=(
                        "exact_confirmed_buy_remaining_position_net_known_buy_fee"
                    ),
                ),
                Trade(
                    condition_id="condition-open",
                    market_slug="event-open",
                    question="Still open?",
                    outcome="Yes",
                    token_id="token-open",
                    buy_amount=5.0,
                    status=TradeStatus.QUARANTINED,
                ),
            ]
        )
        session.commit()
    return db_path


def test_analyzer_uses_exact_evidence_and_keeps_settlement_separate(tmp_path):
    db_path = _fixture_db(tmp_path)
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    result = analyze(
        db_path,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert result["database"]["opened_read_only"] is True
    assert result["confirmed_sell"]["trade_count"] == 1
    assert result["confirmed_sell"]["pnl_usdc"] == 0.5
    assert result["confirmed_sell"][
        "legacy_or_unproven_realized_pnl_rows_excluded"
    ] == 1
    assert result["confirmed_sell"]["legacy_realized_pnl_summed_as_actual"] is False
    assert result["proven_resolution_settlement"]["position_count"] == 1
    assert result["proven_resolution_settlement"][
        "net_known_buy_fee_assumption_usdc"
    ] == 1.0
    assert result["proven_resolution_settlement"]["is_sell_cashflow"] is False
    assert result["cohorts"][0]["config_hash"] == "config-a"
    assert result["cohorts"][0]["git_commit"] == "commit-a"
    assert result["current_exposure_snapshot"]["managed_open_count"] == 1
    assert result["current_exposure_snapshot"][
        "untracked_buy_reservation_count"
    ] == 1
    reconciliation = result["current_exposure_snapshot"][
        "reservation_count_reconciliation"
    ]
    assert reconciliation["raw_order_id_null_submit_outcome_unknown_count"] == 2
    assert reconciliation["operator_proven_no_order_created_excluded_count"] == 1
    assert reconciliation["active_repository_semantics_count"] == 1
    question = next(
        row
        for row in result["clustering"]["exact_question"]
        if row["question"] == "Will A happen?"
    )
    assert question["condition_count"] == 2
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before


def test_sell_timestamp_falls_back_when_preferred_value_is_invalid(tmp_path):
    db_path = _fixture_db(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE trades SET sell_fill_matched_at = 'not-a-timestamp' "
            "WHERE condition_id = 'condition-exact'"
        )

    result = analyze(
        db_path,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert result["confirmed_sell"]["trade_count"] == 1
    assert result["confirmed_sell"]["pnl_usdc"] == 0.5


def test_analyzer_reservation_query_matches_runtime_repository(tmp_path):
    db_path = _fixture_db(tmp_path)
    Session = init_database(str(db_path))
    with Session() as session:
        runtime = TradeRepository(session).get_buy_exposure_reservations()

    result = analyze(
        db_path,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    snapshot = result["current_exposure_snapshot"]

    assert snapshot["untracked_buy_reservation_count"] == runtime[
        "untracked_buy_reservation_count"
    ]
    assert snapshot["untracked_buy_reservation_notional_usdc"] == runtime[
        "untracked_buy_reservation_notional_usdc"
    ]


def test_analyzer_requires_exact_utc_timestamp_inputs():
    assert parse_exact_utc("2026-08-01T00:00:00Z").tzinfo == timezone.utc
    with pytest.raises(ValueError, match="exact time"):
        parse_exact_utc("2026-08-01")
    with pytest.raises(ValueError, match="must use UTC"):
        parse_exact_utc("2026-08-01T09:00:00+09:00")


def test_html_report_excludes_legacy_realized_pnl(tmp_path):
    db_path = _fixture_db(tmp_path)

    rows = load_trades(db_path)

    assert len(rows) == 1
    assert rows[0]["realized_pnl"] == 0.5
