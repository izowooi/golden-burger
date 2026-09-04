import sqlite3

from polybot.db.models import Trade, TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot_observability import ExecutionLedger

from tests.test_exact_history_analyzer import _add_confirmed_order


EXACT_PNL_BASIS = "exact_reconciled_buy_sell_confirmed_fills_net_known_fees"


def _repository(tmp_path):
    db_path = tmp_path / "trades.db"
    Session = init_database(str(db_path))
    ExecutionLedger(db_path, strategy_name="golden-cherry")
    return db_path, Session


def test_exact_economic_drawdown_combines_only_proven_channels(tmp_path):
    db_path, Session = _repository(tmp_path)
    _add_confirmed_order(db_path, "BUY-RESOLUTION", "BUY", "token-resolution")
    with Session() as session:
        session.add_all(
            [
                Trade(
                    condition_id="exact-sell",
                    token_id="token-sell",
                    status=TradeStatus.COMPLETED,
                    realized_pnl=-27.0,
                    pnl_basis=EXACT_PNL_BASIS,
                ),
                Trade(
                    condition_id="legacy-sell",
                    token_id="token-legacy",
                    status=TradeStatus.COMPLETED,
                    realized_pnl=-999.0,
                    pnl_basis=None,
                ),
                Trade(
                    condition_id="exact-resolution",
                    token_id="token-resolution",
                    buy_order_id="BUY-RESOLUTION",
                    status=TradeStatus.RESOLVED,
                    resolution_outcome="No",
                    resolution_value=0.0,
                    resolution_status="resolved",
                    resolution_confirmed_buy_size=5.0,
                    resolution_confirmed_buy_vwap=0.8,
                    resolution_confirmed_buy_fee_usdc=0.0,
                    resolution_position_size=5.0,
                    settlement_pnl_assumption=-4.0,
                    settlement_assumption_basis=(
                        "exact_confirmed_buy_remaining_position_net_known_buy_fee"
                    ),
                ),
            ]
        )
        session.commit()
        guard = TradeRepository(session).get_entry_guard(-30.0)

    assert guard["exact_confirmed_sell_pnl_usdc"] == -27.0
    assert guard["exact_proven_resolution_settlement_usdc"] == -4.0
    assert guard["exact_economic_pnl_usdc"] == -31.0
    assert guard["legacy_realized_pnl_included"] is False
    assert guard["entry_allowed"] is False
    assert "exact_economic_drawdown_floor_breached" in guard["blockers"]


def test_unknown_buy_reservation_blocks_entry_independently_of_drawdown(tmp_path):
    db_path, Session = _repository(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO order_submissions (
                submission_id, strategy_name, order_id, token_id, side,
                requested_price, requested_size, submitted_at, simulation,
                success, response_status, associated_trade_ids_json,
                needs_reconciliation
            ) VALUES (
                'unknown-buy', 'golden-cherry', NULL, 'unknown-token', 'BUY',
                0.8, 6.25, '2026-09-05T00:00:00Z', 0, 0,
                'SUBMIT_OUTCOME_UNKNOWN', '[]', 0
            )
            """
        )
    with Session() as session:
        guard = TradeRepository(session).get_entry_guard(-30.0)

    assert guard["exact_economic_pnl_usdc"] == 0.0
    assert guard["unknown_buy_evidence_count"] == 1
    assert guard["entry_allowed"] is False
    assert "unknown_buy_evidence" in guard["blockers"]


def test_fee_unproven_resolution_blocks_and_is_not_summed(tmp_path):
    _, Session = _repository(tmp_path)
    with Session() as session:
        session.add(
            Trade(
                condition_id="fee-gap-resolution",
                token_id="fee-gap-token",
                status=TradeStatus.RESOLVED,
                settlement_pnl_assumption=10.0,
                settlement_assumption_basis=(
                    "exact_confirmed_buy_remaining_position_gross_fee_unproven"
                ),
            )
        )
        session.commit()
        guard = TradeRepository(session).get_entry_guard(-30.0)

    assert guard["exact_proven_resolution_settlement_usdc"] == 0.0
    assert guard["incomplete_fee_evidence_count"] == 1
    assert guard["entry_allowed"] is False
    assert "incomplete_fee_evidence" in guard["blockers"]


def test_simulation_mode_does_not_apply_live_entry_guard(tmp_path):
    _, Session = _repository(tmp_path)
    with Session() as session:
        guard = TradeRepository(session).get_entry_guard(
            -30.0, simulation_mode=True
        )

    assert guard["entry_allowed"] is True
    assert guard["simulation_guard_not_applicable"] is True
