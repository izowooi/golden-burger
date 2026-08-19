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
                "outcomePrices": '["0.94","0.06"]', "endDate": "2026-08-20T02:00:00Z",
                "gameStartTime": "2026-08-20T02:00:00Z", "liquidityNum": 20000,
                "volumeNum": 10000, "active": True, "closed": False,
                "acceptingOrders": True, "enableOrderBook": True, "feesEnabled": True,
                "feeSchedule": {"rate": 0.05, "takerOnly": True},
            }],
        }
        page = EventPage(1, "gamma-request", "2026-08-20T00:00:01Z", "a" * 64, b"{}", (event,), None, None)
        return EventSweep((page,), True)


class FakeClob:
    def fetch_books(self, run_id, token_ids):
        books = {
            "yes": {"asset_id": "yes", "bids": [{"price": "0.93", "size": "20"}], "asks": [{"price": "0.94", "size": "20"}]},
            "no": {"asset_id": "no", "bids": [{"price": "0.05", "size": "20"}], "asks": [{"price": "0.06", "size": "20"}]},
        }
        attempts = {token: BookAttempt(token, "OBSERVED", "book-request", "2026-08-20T00:00:02Z") for token in books}
        payload = RawPayload("book-request", "2026-08-20T00:00:02Z", "b" * 64, b"[]")
        return BookCollection(books, attempts, (payload,))

    def fetch_resolution(self, run_id, condition_id):
        raise AssertionError("resolution is not due before endDate")


def test_only_matching_arm_opens_with_exact_depth(tmp_path) -> None:
    config = load_config(ROOT / "config.yaml")
    config = replace(config, db_path=tmp_path / "trades_sim.db")
    repository = ResearchRepository(config.db_path, busy_timeout_ms=1000, data_contract=config.trading.data_contract)
    result = Collector(config, repository, FakeGamma(), FakeClob()).collect("run-1", now=datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert result["pages"] == 1
    assert result["episodes_opened"] == 1
    with repository.connect() as connection:
        episode = connection.execute("SELECT threshold,entry_vwap,entry_cost FROM hypothetical_episodes").fetchone()
        statuses = dict(connection.execute("SELECT threshold,decision_status FROM signal_decisions WHERE token_id='yes'").fetchall())
    assert episode[0] == 0.94
    assert episode[1] == pytest.approx(0.94)
    assert episode[2] == 5.0
    assert statuses == {0.92: "ABOVE_ENTRY_BAND", 0.94: "OPENED"}
