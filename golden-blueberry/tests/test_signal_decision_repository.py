"""Durable first-crossing decision evidence for the Blueberry A/B test."""

from __future__ import annotations

from polybot.db.models import EntrySignalDecision, init_database
from polybot.db.repository import TradeRepository


def test_entry_signal_decision_is_unique_per_run_and_condition(tmp_path):
    Session = init_database(str(tmp_path / "blueberry.db"))
    session = Session()
    repository = TradeRepository(session)
    payload = {
        "run_id": "run-1",
        "condition_id": "condition-1",
        "event_id": "event-1",
        "prior_snapshot_id": 10,
        "current_snapshot_id": 11,
        "prior_price": 0.82,
        "current_price": 0.86,
        "surge": 0.04,
        "snapshot_gap_minutes": 5.0,
        "min_surge": 0.05,
        "hours_left": 12.0,
        "clock_reference": "end_date",
        "sports_phase": "not_sports",
        "is_sports": 0,
        "liquidity": 20_000.0,
        "volume_24h": 50_000.0,
        "effective_min_liquidity": 10_000.0,
        "effective_min_volume_24h": 10_000.0,
        "decision": "rejected",
        "reason": "surge_below_min",
    }

    first = repository.record_entry_signal_decision(**payload)
    second = repository.record_entry_signal_decision(**payload)

    assert first.id == second.id
    assert session.query(EntrySignalDecision).count() == 1
    assert first.surge == 0.04
    assert repository.get_stats()["entry_signal_decisions"] == 1
    session.close()
