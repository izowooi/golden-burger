from __future__ import annotations

from datetime import datetime
import hashlib
import json
import sqlite3

import pytest
from sqlalchemy import text

from polybot.api.clob_client import ClobClientWrapper, _normalize_clob_resolution
from polybot.bot import PolymarketBot
from polybot.config import ApiConfig
from polybot.db.models import EntryEpisode, TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.filters import is_exact_esports_market
from polybot.utils.run_lock import RunLockUnavailable, db_run_lock
from polybot_observability import ExecutionLedger


def _repo(tmp_path):
    path = tmp_path / "tangerine.db"
    Session = init_database(str(path))
    ExecutionLedger(path, strategy_name="golden-tangerine")
    session = Session()
    return path, session, TradeRepository(session)


def test_quarantine_unknown_submission_and_restart_reserve_capacity(tmp_path):
    path, session, repo = _repo(tmp_path)
    repo.create_trade(
        condition_id="held",
        event_id="event-held",
        outcome="A",
        token_id="held-token",
        buy_amount=5,
        status=TradeStatus.QUARANTINED,
        mode="live",
    )
    episode = EntryEpisode(
        token_id="unknown-token",
        condition_id="unknown",
        event_id="event-unknown",
        outcome="B",
        entry_snapshot_id=1,
        exact_vwap=0.95,
        arm_prob_min=0.94,
        arm_prob_max=0.95,
        observed_at=datetime.utcnow(),
        execution_state="SUBMISSION_IN_PROGRESS",
        execution_reason="restart-window",
        execution_updated_at=datetime.utcnow(),
    )
    session.add(episode)
    session.commit()
    ledger = ExecutionLedger(path, strategy_name="golden-tangerine")
    ledger.record_intent(
        token_id="unknown-token",
        side="BUY",
        requested_price=0.95,
        requested_size=5.2631,
        simulation=False,
    )

    capacity = repo.get_entry_capacity_state(base_notional_usdc=5)
    assert capacity["open_positions"] == 1
    assert capacity["untracked_buy_reservations"] == 1
    assert capacity["prepost_crash_reservations"] == 0
    assert capacity["total_reserved"] == 2
    assert capacity["total_notional_usdc"] == pytest.approx(10.0, abs=0.01)
    assert repo.get_event_position_count("event-unknown") == 1
    session.close()

    # Reservation survives a process/session restart.
    Session = init_database(str(path))
    restarted = Session()
    assert TradeRepository(restarted).get_entry_capacity_state(
        base_notional_usdc=5
    )["total_reserved"] == 2
    restarted.close()


def test_first_band_candidate_queue_and_post_boundary_are_append_only(tmp_path):
    _, session, repo = _repo(tmp_path)
    episode = repo.claim_entry_episode(
        token_id="candidate",
        condition_id="condition",
        event_id="event",
        outcome="A",
        entry_snapshot_id=1,
        exact_vwap=0.95,
        arm_prob_min=0.94,
        arm_prob_max=0.95,
        observed_at=datetime.utcnow(),
    )
    repo.commit()
    repo.mark_entry_episode_execution(
        episode.id,
        state="REJECTED_PROVEN_NO_POST",
        reason="fresh_book_unavailable",
        proven_no_post=True,
        post_may_have_occurred=False,
    )
    events = session.execute(
        text(
            "SELECT state, proven_no_post FROM entry_candidate_events "
            "WHERE episode_id=:episode ORDER BY id"
        ),
        {"episode": episode.id},
    ).all()
    assert events == [
        ("QUEUED_PROVEN_NO_POST", 1),
        ("REJECTED_PROVEN_NO_POST", 1),
    ]


def test_candidate_queue_continues_after_prepost_failure_then_preserves_cycle_cap():
    class Repo:
        def __init__(self):
            self.deferred = []

        def mark_entry_episode_execution(self, episode_id, **values):
            self.deferred.append((episode_id, values))

    class Trader:
        def __init__(self):
            self.calls = []
            self.last_entry_may_have_reached_venue = False

        def execute_buy(self, candidate):
            self.calls.append(candidate["entry_episode_id"])
            if len(self.calls) == 1:
                self.last_entry_may_have_reached_venue = False
                return None
            self.last_entry_may_have_reached_venue = True
            return 39

    repo, trader = Repo(), Trader()
    result = PolymarketBot._execute_candidate_queue(
        repo,
        trader,
        [{"entry_episode_id": 1}, {"entry_episode_id": 2}, {"entry_episode_id": 3}],
        1,
    )
    assert trader.calls == [1, 2]
    assert result == {"bought": 1, "post_reservations": 1}
    assert repo.deferred[0][0] == 3
    assert repo.deferred[0][1]["state"] == "CYCLE_POST_CAP_PROVEN_NO_POST"


def _seed_terminal_buy(
    tmp_path, *, order_id: str, confirmed_size: float, price: float = 0.95
):
    path = tmp_path / f"{order_id}.db"
    init_database(str(path))
    ledger = ExecutionLedger(path, strategy_name="golden-tangerine")
    submission_id = ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=price,
        requested_size=5.2631,
        result={
            "success": True,
            "orderID": order_id,
            "status": "MATCHED",
            "makingAmount": 5_000_000,
            "takingAmount": 5_263_100,
        },
        simulation=False,
    )
    connection = sqlite3.connect(path)
    connection.execute(
        """
        UPDATE order_submissions SET latest_order_status='MATCHED',
            latest_size_matched=?, associated_trade_ids_json='["fill-1"]',
            needs_reconciliation=1,
            reconciliation_error='confirmed BUY notional exceeds maker envelope'
        WHERE submission_id=?
        """,
        (confirmed_size, submission_id),
    )
    connection.execute(
        """
        INSERT INTO order_fills (
            submission_id, order_id, trade_id, bucket_index, status, side,
            size, price, liquidity_role, fee_rate_bps, fee_amount_usdc,
            matched_at, domain_error
        ) VALUES (?, ?, 'fill-1', 0, 'CONFIRMED', 'BUY', ?, ?, 'TAKER', 0,
                  NULL, '2026-08-26T00:00:00Z', NULL)
        """,
        (submission_id, order_id, confirmed_size, price),
    )
    connection.commit()
    connection.close()
    wrapper = ClobClientWrapper(
        ApiConfig("key", "funder"),
        simulation_mode=False,
        audit_db_path=path,
        strategy_name="golden-tangerine",
    )
    return path, submission_id, wrapper


def test_terminal_fok_buy_accepts_current_one_cent_economic_tolerance_case(tmp_path):
    path, submission_id, wrapper = _seed_terminal_buy(
        tmp_path, order_id="within", confirmed_size=5.26852
    )
    assert wrapper._finish_tangerine_fok_buy_with_economic_tolerance(submission_id)
    connection = sqlite3.connect(path)
    row = connection.execute(
        "SELECT needs_reconciliation, reconciliation_proof FROM order_submissions"
    ).fetchone()
    assert row == (0, "TANGERINE_FOK_TERMINAL_CONFIRMED_WITHIN_ONE_CENT")


def test_terminal_fok_buy_larger_economic_mismatch_fails_closed(tmp_path):
    path, submission_id, wrapper = _seed_terminal_buy(
        tmp_path, order_id="outside", confirmed_size=5.28
    )
    assert not wrapper._finish_tangerine_fok_buy_with_economic_tolerance(submission_id)
    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT needs_reconciliation FROM order_submissions"
    ).fetchone()[0] == 1


def test_closed_exact_half_half_is_void_not_one_hot():
    proof = _normalize_clob_resolution(
        "condition",
        {
            "condition_id": "condition",
            "closed": True,
            "tokens": [
                {"outcome": "A", "token_id": "a", "price": 0.5, "winner": False},
                {"outcome": "B", "token_id": "b", "price": 0.5, "winner": False},
            ],
        },
        observed_at="2026-08-26T00:00:00Z",
    )
    assert proof.status == "VOID"
    assert proof.winner_index is None
    assert [token.price for token in proof.tokens] == [0.5, 0.5]


def test_void_proof_is_token_aligned_and_append_only(tmp_path):
    _, session, repo = _repo(tmp_path)
    trade = repo.create_trade(
        condition_id="void-condition",
        outcome="B",
        token_id="b",
        status=TradeStatus.HOLDING,
        mode="live",
    )
    evidence = {
        "closed": True,
        "tokens": [
            {"outcome": "A", "price": 0.5, "token_id": "a", "winner": False},
            {"outcome": "B", "price": 0.5, "token_id": "b", "winner": False},
        ],
    }
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    observation = repo.stage_clob_resolution_observation(
        trade_id=trade.id,
        condition_id=trade.condition_id,
        observed_at=datetime.utcnow(),
        winner_index=-1,
        winner_token_id="",
        winner_outcome="VOID",
        selected_token_id="b",
        selected_outcome="B",
        selected_payout=0.5,
        evidence_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        evidence_json=payload,
        settlement_kind="VOID",
    )
    repo.commit()
    assert observation.settlement_kind == "VOID"
    assert observation.selected_token_id == "b"
    with pytest.raises(Exception, match="append-only evidence"):
        session.execute(
            text("UPDATE resolution_observations SET selected_payout=0 WHERE resolution_id=:id"),
            {"id": observation.resolution_id},
        )
        session.commit()
    session.rollback()


def test_exact_esports_exclusion_is_identity_only():
    assert is_exact_esports_market({"tags": [{"slug": "esports"}]})
    assert not is_exact_esports_market(
        {"question": "Will the esports company win?", "tags": [{"slug": "sports"}]}
    )


def test_exact_loss_state_fails_closed_on_fee_gap(tmp_path):
    _, session, repo = _repo(tmp_path)
    repo.create_trade(
        condition_id="resolved",
        outcome="A",
        token_id="a",
        status=TradeStatus.RESOLVED,
        mode="live",
        resolution_evidence="proof",
        resolution_confirmed_buy_size=5,
        resolution_confirmed_buy_vwap=0.95,
        resolution_confirmed_buy_fee_usdc=None,
        settlement_pnl_assumption=-4.75,
        settlement_assumption_basis="confirmed_buy_fill_gross_fee_unproven",
    )
    state = repo.get_exact_economic_loss_state()
    assert state["evidence_complete"] is False
    assert state["evidence_gap_trade_ids"]


def test_cycle_runtime_telemetry_is_append_only(tmp_path):
    _, session, repo = _repo(tmp_path)
    repo.append_cycle_runtime_event(
        cycle_id="cycle", phase="cycle", status="STARTED", elapsed_seconds=0
    )
    with pytest.raises(Exception, match="append-only evidence"):
        session.execute(text("UPDATE cycle_runtime_events SET status='X'"))
        session.commit()
    session.rollback()


def test_db_run_lock_is_nonblocking_and_db_scoped(tmp_path):
    db_path = tmp_path / "run.db"
    with db_run_lock(db_path):
        with pytest.raises(RunLockUnavailable):
            with db_run_lock(db_path):
                pass
    with db_run_lock(db_path):
        pass


def test_migration_type_error_is_not_swallowed(tmp_path):
    path = tmp_path / "bad-migration.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE entry_episodes (id INTEGER PRIMARY KEY, execution_state REAL)"
    )
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match=r"entry_episodes\.execution_state"):
        init_database(str(path))
