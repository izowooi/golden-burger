from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from polybot.api.clob_client import BookAttempt, BookCollection, RawPayload
from polybot.api.gamma_client import EventPage, EventSweep
from polybot.collector import Collector
from polybot.config import load_config
from polybot.db.repository import ResearchRepository


ROOT = Path(__file__).resolve().parents[1]


class FakeGamma:
    def fetch_sports_events(self, run_id, *, observed_at):
        event = {
            "id": "event-1", "title": "A vs B", "category": "Sports",
            "tags": [{"slug": "sports"}],
            "markets": [{
                "id": "market-1", "conditionId": "condition-1", "question": "Will A win?",
                "outcomes": '["Yes","No"]', "clobTokenIds": '["yes","no"]',
                "outcomePrices": '["0.94","0.06"]', "endDate": "2026-08-21T02:00:00Z",
                "gameStartTime": "2026-08-21T02:00:00Z", "liquidityNum": 20000,
                "volumeNum": 10000, "active": True, "closed": False,
                "acceptingOrders": True, "enableOrderBook": True, "feesEnabled": True,
                "feeSchedule": {"rate": 0.05, "takerOnly": True},
            }],
        }
        page = EventPage(1, "gamma-request", "2026-08-21T00:00:01Z", "a" * 64, b"{}", (event,), None, None)
        return EventSweep((page,), True)


class FakeClob:
    def fetch_books(self, run_id, token_ids):
        books = {
            "yes": {"asset_id": "yes", "bids": [{"price": "0.93", "size": "20"}], "asks": [{"price": "0.94", "size": "20"}]},
            "no": {"asset_id": "no", "bids": [{"price": "0.05", "size": "20"}], "asks": [{"price": "0.06", "size": "20"}]},
        }
        attempts = {token: BookAttempt(token, "OBSERVED", "book-request", "2026-08-21T00:00:02Z") for token in books}
        payload = RawPayload("book-request", "2026-08-21T00:00:02Z", "b" * 64, b"[]")
        return BookCollection(books, attempts, (payload,))

    def fetch_resolution(self, run_id, condition_id):
        raise AssertionError("resolution is not due before endDate")


class FallingClob(FakeClob):
    def fetch_books(self, run_id, token_ids):
        collection = super().fetch_books(run_id, token_ids)
        collection.books["yes"]["bids"] = [
            {"price": "0.79", "size": "2"},
            {"price": "0.78", "size": "20"},
        ]
        return collection


class PartialFallingClob(FakeClob):
    def fetch_books(self, run_id, token_ids):
        collection = super().fetch_books(run_id, token_ids)
        collection.books["yes"]["bids"] = [{"price": "0.79", "size": "2"}]
        return collection


class RetryFallingClob(FakeClob):
    def fetch_books(self, run_id, token_ids):
        collection = super().fetch_books(run_id, token_ids)
        collection.books["yes"]["bids"] = [{"price": "0.77", "size": "20"}]
        return collection


def test_only_matching_arm_opens_with_exact_depth(tmp_path) -> None:
    config = load_config(ROOT / "config.yaml")
    config = replace(config, db_path=tmp_path / "trades_sim.db")
    repository = ResearchRepository(config.db_path, busy_timeout_ms=1000, data_contract=config.trading.data_contract)
    result = Collector(config, repository, FakeGamma(), FakeClob()).collect("run-1", now=datetime(2026, 8, 21, tzinfo=timezone.utc))
    assert result["pages"] == 1
    assert result["episodes_opened"] == 1
    with repository.connect() as connection:
        episode = connection.execute("SELECT threshold,entry_vwap,entry_cost FROM hypothetical_episodes").fetchone()
        statuses = dict(connection.execute("SELECT threshold,decision_status FROM signal_decisions WHERE token_id='yes'").fetchall())
        policies = connection.execute(
            "SELECT policy_key,stop_price FROM counterfactual_exit_policies ORDER BY policy_key"
        ).fetchall()
    assert episode[0] == 0.94
    assert episode[1] == pytest.approx(0.94)
    assert episode[2] == 5.0
    assert statuses == {0.92: "ABOVE_ENTRY_BAND", 0.94: "OPENED"}
    assert len(policies) == 4
    assert {row[0] for row in policies} == {
        "HOLD_TO_RESOLUTION", "STOP_0.60", "STOP_0.70", "STOP_0.80",
    }


def test_stop_trigger_records_gap_and_exact_full_depth_vwap(tmp_path) -> None:
    config = load_config(ROOT / "config.yaml")
    config = replace(config, db_path=tmp_path / "trades_sim.db")
    repository = ResearchRepository(
        config.db_path, busy_timeout_ms=1000,
        data_contract=config.trading.data_contract,
    )
    Collector(config, repository, FakeGamma(), FakeClob()).collect(
        "run-1", now=datetime(2026, 8, 21, tzinfo=timezone.utc)
    )
    result = Collector(config, repository, FakeGamma(), FallingClob()).collect(
        "run-2", now=datetime(2026, 8, 21, 0, 5, tzinfo=timezone.utc)
    )
    assert result["stop_attempts"] == 1
    assert result["stop_exits"] == 1
    with repository.connect() as connection:
        attempt = connection.execute(
            "SELECT stop_price,trigger_best_bid,exit_vwap,status,drop_from_prior "
            "FROM stop_execution_attempts"
        ).fetchone()
        completed = connection.execute(
            "SELECT stop_price,exit_vwap,gap_from_stop,attempt_count "
            "FROM counterfactual_stop_exits"
        ).fetchone()
    assert tuple(attempt[:2]) == pytest.approx((0.80, 0.79))
    assert attempt[2] < 0.79
    assert attempt[3] == "FULL_EXIT"
    assert attempt[4] == pytest.approx(0.14)
    assert completed[0] == 0.80
    assert completed[1] < 0.79
    assert completed[2] == pytest.approx(0.80 - completed[1])
    assert completed[3] == 1


def test_partial_stop_fill_retries_only_remaining_shares(tmp_path) -> None:
    config = replace(
        load_config(ROOT / "config.yaml"), db_path=tmp_path / "trades_sim.db"
    )
    repository = ResearchRepository(
        config.db_path, busy_timeout_ms=1000,
        data_contract=config.trading.data_contract,
    )
    Collector(config, repository, FakeGamma(), FakeClob()).collect(
        "run-1", now=datetime(2026, 8, 21, tzinfo=timezone.utc)
    )
    partial = Collector(
        config, repository, FakeGamma(), PartialFallingClob()
    ).collect("run-2", now=datetime(2026, 8, 21, 0, 5, tzinfo=timezone.utc))
    assert partial["stop_attempts"] == 1
    assert partial["stop_exits"] == 0
    completed = Collector(
        config, repository, FakeGamma(), RetryFallingClob()
    ).collect("run-3", now=datetime(2026, 8, 21, 0, 10, tzinfo=timezone.utc))
    assert completed["stop_attempts"] == 1
    assert completed["stop_exits"] == 1
    with repository.connect() as connection:
        attempts = connection.execute(
            "SELECT requested_shares,filled_shares,remaining_shares,status "
            "FROM stop_execution_attempts ORDER BY observed_at"
        ).fetchall()
        exit_row = connection.execute(
            "SELECT requested_shares,filled_shares,attempt_count,exit_vwap "
            "FROM counterfactual_stop_exits"
        ).fetchone()
    assert attempts[0][1] == 2.0
    assert attempts[0][2] > 0
    assert attempts[0][3] == "PARTIAL_FILL"
    assert attempts[1][0] == pytest.approx(attempts[0][2])
    assert attempts[1][2] == pytest.approx(0)
    assert attempts[1][3] == "FULL_EXIT"
    assert exit_row[0] == pytest.approx(exit_row[1])
    assert exit_row[2] == 2
    assert 0.77 < exit_row[3] < 0.79
