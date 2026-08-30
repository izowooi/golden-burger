"""Event/result cooldown regression tests for one-time stop reversal."""

from datetime import datetime, timedelta

from polybot.db.models import TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot_observability import ExecutionLedger


NOW = datetime(2026, 8, 30, 4, 0)


def _trade(repo: TradeRepository, **overrides):
    values = {
        "condition_id": "condition-a",
        "event_id": "event-1",
        "outcome": "Team A",
        "token_id": "token-a",
        "buy_price": 0.97,
        "buy_shares": 5.15,
        "buy_timestamp": NOW - timedelta(minutes=10),
        "sell_timestamp": NOW - timedelta(minutes=2),
        "sell_confirmed_size": 5.15,
        "status": TradeStatus.COMPLETED,
        "exit_reason": "absolute_stop_confirmed_fill",
        "mode": "live",
    }
    values.update(overrides)
    return repo.create_trade(**values)


def _repository(tmp_path, name: str):
    db_path = tmp_path / name
    ExecutionLedger(db_path, strategy_name="golden-watermelon-live")
    Session = init_database(str(db_path))
    session = Session()
    return session, TradeRepository(session)


def test_opposite_token_is_allowed_once_after_confirmed_stop(tmp_path):
    session, repo = _repository(tmp_path, "reversal.db")
    _trade(repo)

    assert repo.can_reenter(
        "condition-a",
        720,
        NOW,
        event_id="event-1",
        token_id="token-b",
    ) == (True, "opposite_result_after_confirmed_stop")
    assert repo.can_reenter(
        "condition-a",
        720,
        NOW,
        event_id="event-1",
        token_id="token-a",
    ) == (False, "close_cooldown")
    session.close()


def test_second_stopped_result_closes_event_to_a_third_entry(tmp_path):
    session, repo = _repository(tmp_path, "reversal-limit.db")
    _trade(repo)
    _trade(
        repo,
        condition_id="condition-b",
        outcome="Draw",
        token_id="token-b",
        sell_timestamp=NOW - timedelta(minutes=1),
    )

    assert repo.can_reenter(
        "condition-c",
        720,
        NOW,
        event_id="event-1",
        token_id="token-c",
    ) == (False, "event_reversal_limit")
    session.close()


def test_non_stop_close_does_not_authorize_opposite_result(tmp_path):
    session, repo = _repository(tmp_path, "non-stop-close.db")
    _trade(
        repo,
        status=TradeStatus.RESOLVED,
        exit_reason="resolved_with_payout_evidence",
        sell_timestamp=None,
        sell_confirmed_size=None,
        resolution_observed_at=NOW - timedelta(minutes=1),
    )

    assert repo.can_reenter(
        "condition-b",
        720,
        NOW,
        event_id="event-1",
        token_id="token-b",
    ) == (False, "event_close_not_reversible")
    session.close()


def test_open_event_position_blocks_opposite_result(tmp_path):
    session, repo = _repository(tmp_path, "open-event.db")
    _trade(
        repo,
        status=TradeStatus.HOLDING,
        exit_reason=None,
        sell_timestamp=None,
        sell_confirmed_size=None,
    )

    assert repo.can_reenter(
        "condition-b",
        720,
        NOW,
        event_id="event-1",
        token_id="token-b",
    ) == (False, "event_holding")
    session.close()
