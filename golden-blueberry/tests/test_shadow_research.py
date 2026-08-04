"""Accountless Shadow grid and counterfactual outcome contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from polybot.config import TradingConfig
from polybot.db.models import ShadowObservation, ShadowSignal, Trade, init_database
from polybot.db.repository import TradeRepository
from polybot.strategy.shadow import ShadowResearcher


NOW = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)


class PublicBook:
    def __init__(self, midpoint=0.90):
        self.midpoint = midpoint

    def get_midpoint(self, _token_id):
        return self.midpoint

    def get_buy_book_depth(self, _token_id, **_kwargs):
        return SimpleNamespace(
            best_bid=0.89,
            best_ask=0.90,
            spread=0.01,
            ask_depth_shares=100.0,
            ask_limit_price=0.91,
        )


def crossing(
    condition_id: str,
    *,
    prior=0.84,
    current=0.90,
    hours_left=100.0,
):
    return {
        "condition_id": condition_id,
        "question": "Will the market resolve Yes?",
        "event_id": "event-1",
        "token_id": "yes-token",
        "prior_snapshot_id": 1,
        "current_snapshot_id": 2,
        "prior_probability": prior,
        "current_probability": current,
        "surge": current - prior,
        "snapshot_gap_minutes": 5.0,
        "liquidity": 50_000.0,
        "volume_24h": 50_000.0,
        "market_end_date": NOW + timedelta(hours=hours_left),
        "hours_until_resolution": hours_left,
        "clock_reference": "end_date",
        "entry_deadline": NOW + timedelta(hours=hours_left),
        "hours_left": hours_left,
        "sports_phase": "not_sports",
        "is_sports": False,
        "source_updated_at": NOW.isoformat(),
    }


def researcher(tmp_path, *, midpoint=0.90):
    Session = init_database(str(tmp_path / "shadow.db"))
    session = Session()
    repo = TradeRepository(session)
    engine = ShadowResearcher(
        repo,
        scanner=SimpleNamespace(),
        gamma=SimpleNamespace(),
        clob=PublicBook(midpoint),
        config=TradingConfig(lifecycle_mode="shadow_only"),
    )
    return session, repo, engine


def test_first_crossing_expands_to_fixed_2x2_grid_without_trade(tmp_path):
    session, repo, engine = researcher(tmp_path)

    created, observation_created = engine._record_crossing(
        crossing("condition-grid"), NOW
    )
    repo.commit()

    rows = session.query(ShadowSignal).order_by(
        ShadowSignal.min_surge, ShadowSignal.horizon_hours
    ).all()
    assert created == 4
    assert observation_created is True
    assert [(row.min_surge, row.horizon_hours) for row in rows] == [
        (0.02, 72.0),
        (0.02, 168.0),
        (0.05, 72.0),
        (0.05, 168.0),
    ]
    assert [row.status for row in rows] == [
        "COUNTERFACTUAL_OPEN",
        "OPEN",
        "COUNTERFACTUAL_OPEN",
        "OPEN",
    ]
    assert all(row.entry_limit_price == pytest.approx(0.91) for row in rows)
    assert all(row.hypothetical_shares == pytest.approx(5 / 0.91) for row in rows)
    assert session.query(ShadowObservation).count() == 1
    assert session.query(Trade).count() == 0
    assert repo.get_stats()["shadow_signals"] == 4
    session.close()


def test_counterfactual_resolution_labels_missed_loss_avoidance(tmp_path):
    session, repo, engine = researcher(tmp_path, midpoint=0.88)
    # +3%p at 100h: only A-2pp/168h enters; the other three treatments reject.
    engine._record_crossing(
        crossing("condition-no", prior=0.849, current=0.88), NOW
    )
    repo.commit()

    engine._apply_observation(
        "condition-no",
        probability=0.0,
        best_bid=None,
        resolution={
            "outcome": "No",
            "yes_payout": 0.0,
            "evidence": "gamma_closed_final_outcome_prices",
        },
        observed_at=NOW + timedelta(days=5),
    )
    repo.commit()

    rows = session.query(ShadowSignal).all()
    assert {row.status for row in rows} == {"CLOSED"}
    assert sum(row.classification == "ENTERED_LOSS" for row in rows) == 1
    assert sum(row.classification == "AVOIDED_LOSS" for row in rows) == 3
    assert all(row.exit_reason == "PROVEN_RESOLUTION" for row in rows)
    assert all(row.resolution_outcome == "No" for row in rows)
    assert all("fees_excluded" in row.pnl_basis for row in rows)
    assert repo.get_stats()["shadow_avoided_loss"] == 3
    assert session.query(Trade).count() == 0
    session.close()


def test_rejected_treatment_profit_is_missed_opportunity(tmp_path):
    session, repo, engine = researcher(tmp_path)
    engine._record_crossing(crossing("condition-target"), NOW)
    repo.commit()

    engine._apply_observation(
        "condition-target",
        probability=0.98,
        best_bid=0.975,
        resolution=None,
        observed_at=NOW + timedelta(hours=12),
    )
    repo.commit()

    rows = session.query(ShadowSignal).all()
    assert sum(row.classification == "ENTERED_PROFIT" for row in rows) == 2
    assert sum(row.classification == "MISSED_PROFIT" for row in rows) == 2
    assert repo.get_stats()["shadow_missed_profit"] == 2
    session.close()
