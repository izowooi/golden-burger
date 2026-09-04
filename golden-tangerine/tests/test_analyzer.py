from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from sqlalchemy import text

from polybot.analyzer import analyze_ab, analyze_database
from polybot.db.models import TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot_observability import ExecutionLedger


START = datetime(2026, 8, 20, tzinfo=timezone.utc)
END = datetime(2026, 9, 20, tzinfo=timezone.utc)


def _analyzable_db(tmp_path, name: str):
    path = tmp_path / f"{name}.db"
    Session = init_database(str(path))
    ledger = ExecutionLedger(path, strategy_name="golden-tangerine")
    submission_id = ledger.record_submission(
        token_id=f"token-{name}",
        side="BUY",
        requested_price=0.95,
        requested_size=5.2631,
        result={"success": True, "orderID": f"order-{name}"},
        simulation=False,
    )
    session = Session()
    session.execute(
        text(
            """
            UPDATE order_submissions SET latest_order_status='MATCHED',
                latest_size_matched=5.2631, needs_reconciliation=0
            WHERE submission_id=:submission
            """
        ),
        {"submission": submission_id},
    )
    session.execute(
        text(
            """
            INSERT INTO order_fills (
                submission_id, order_id, trade_id, bucket_index, status, side,
                size, price, liquidity_role, fee_rate_bps, fee_amount_usdc,
                domain_error
            ) VALUES (:submission, :order, :fill, 0, 'CONFIRMED', 'BUY',
                      5.2631, 0.95, 'TAKER', 0, NULL, NULL)
            """
        ),
        {"submission": submission_id, "order": f"order-{name}", "fill": f"fill-{name}"},
    )
    session.commit()
    repo = TradeRepository(session)
    episode = repo.claim_entry_episode(
        token_id=f"token-{name}",
        condition_id=f"condition-{name}",
        event_id=f"event-{name}",
        outcome="Winner",
        entry_snapshot_id=1,
        exact_vwap=0.95,
        arm_prob_min=0.94,
        arm_prob_max=0.95,
        observed_at=datetime(2026, 8, 21),
    )
    repo.commit()
    trade = repo.create_trade(
        condition_id=f"condition-{name}",
        event_id=f"event-{name}",
        outcome="Winner",
        token_id=f"token-{name}",
        buy_order_id=f"order-{name}",
        buy_amount=5,
        buy_timestamp=datetime(2026, 8, 21),
        status=TradeStatus.RESOLVED,
        mode="live",
        resolution_value=1.0,
        resolution_status="resolved",
        resolution_evidence="exact-proof",
        resolution_confirmed_buy_size=5.2631,
        resolution_confirmed_buy_vwap=0.95,
        resolution_confirmed_buy_fee_usdc=0.0,
        settlement_pnl_assumption=0.263155,
        settlement_assumption_basis="confirmed_buy_fill_net_known_buy_fee",
    )
    repo.mark_entry_episode_execution(
        episode.id,
        state="TRADE_CREATED",
        reason="accepted_order_linked_to_trade",
        proven_no_post=False,
        post_may_have_occurred=True,
        trade_id=trade.id,
        order_id=f"order-{name}",
    )
    evidence = {
        "closed": True,
        "tokens": [
            {"outcome": "Winner", "price": 1.0, "token_id": f"token-{name}", "winner": True},
            {"outcome": "Loser", "price": 0.0, "token_id": f"other-{name}", "winner": False},
        ],
    }
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    repo.stage_clob_resolution_observation(
        trade_id=trade.id,
        condition_id=trade.condition_id,
        observed_at=datetime(2026, 8, 22),
        winner_index=0,
        winner_token_id=f"token-{name}",
        winner_outcome="Winner",
        selected_token_id=f"token-{name}",
        selected_outcome="Winner",
        selected_payout=1.0,
        evidence_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        evidence_json=payload,
    )
    repo.commit()
    session.close()
    return path


def test_exact_range_analyzer_is_read_only_and_reports_evidence(tmp_path):
    path = _analyzable_db(tmp_path, "a")
    report = analyze_database(path, start=START, end_exclusive=END, label="A")
    assert report["entry_cohort"]["trades"] == 1
    assert report["entry_cohort"]["proven_resolved"] == 1
    assert report["entry_cohort"]["fee_complete_fill_rows"] == 1
    assert report["event_clustering"]["unique_events"] == 1
    assert report["episode_funnel"]["first_band_episodes"] == 1
    assert report["unresolved_exposure"]["trade_count"] == 0
    assert report["database_stable_during_read"] is True
    assert report["strict_evidence_complete"] is True


def test_ab_analyzer_reports_async_cadence_and_checksum(tmp_path):
    left = _analyzable_db(tmp_path, "left")
    right = _analyzable_db(tmp_path, "right")
    report = analyze_ab(
        [("A", left), ("B", right)], start=START, end_exclusive=END
    )
    assert report["async_cadence"]["comparable"] is True
    assert len(report["report_sha256"]) == 64
    assert report["strict_evidence_complete"] is True
