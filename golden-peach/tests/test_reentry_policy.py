from datetime import datetime

from polybot.db.models import TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot_observability import ExecutionLedger


NOW = datetime(2026, 8, 30, 4, 0)


def _repository(tmp_path, name: str):
    db_path = tmp_path / name
    ExecutionLedger(db_path, strategy_name="golden-peach")
    Session = init_database(str(db_path))
    session = Session()
    return session, TradeRepository(session)


def _trade(repo, *, status=TradeStatus.COMPLETED, token="token-a"):
    return repo.create_trade(
        condition_id="condition-a",
        event_id="event-1",
        outcome="No",
        outcome_side="NO",
        result_kind="AWAY",
        token_id=token,
        buy_price=0.80,
        buy_shares=6.25,
        buy_timestamp=NOW,
        status=status,
        mode="live",
    )


def test_completed_tp_or_stop_permanently_closes_event(tmp_path) -> None:
    session, repo = _repository(tmp_path, "closed-event.db")
    _trade(repo)
    assert repo.can_reenter(
        "condition-b", 720, NOW, event_id="event-1", token_id="token-b"
    ) == (False, "event_already_traded")
    session.close()


def test_open_or_uncertain_exposure_closes_event(tmp_path) -> None:
    for status in (
        TradeStatus.PENDING_BUY,
        TradeStatus.HOLDING,
        TradeStatus.PENDING_SELL,
        TradeStatus.QUARANTINED,
    ):
        session, repo = _repository(tmp_path, f"{status.value}.db")
        _trade(repo, status=status)
        assert repo.can_reenter(
            "condition-b", 720, NOW, event_id="event-1", token_id="token-b"
        ) == (False, "event_already_traded")
        session.close()


def test_exact_zero_fill_does_not_consume_event_entry(tmp_path) -> None:
    session, repo = _repository(tmp_path, "zero-fill.db")
    _trade(repo, status=TradeStatus.UNFILLED)
    assert repo.can_reenter(
        "condition-a", 720, NOW, event_id="event-1", token_id="token-a"
    ) == (True, "ok")
    session.close()


def test_event_identity_is_required(tmp_path) -> None:
    session, repo = _repository(tmp_path, "missing-event.db")
    assert repo.can_reenter("condition-a", 720, NOW, token_id="token-a") == (
        False,
        "event_id_missing",
    )
    session.close()
