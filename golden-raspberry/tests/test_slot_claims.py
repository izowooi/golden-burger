from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

import pytest

from polybot.bot import PolymarketResearchBot
from polybot.config import PROJECT_ROOT, load_config
from polybot.db.repository import ResearchRepository


class StaticClock:
    def __init__(self, value: float = 0.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeCollector:
    def __init__(self):
        self.calls = 0

    def run_cycle(self, run_id, *, budget):
        self.calls += 1
        return {
            "shard_index": 0,
            "cycle_number": 1,
            "gamma_pages": 1,
            "source_envelope_markets": 0,
            "eligible_markets": 0,
            "panel_markets": 0,
            "shard_markets": 0,
            "books_normalized": 0,
            "books_requested": 0,
            "qualified_by_arm": {"DO": 0, "RE": 0, "MI": 0},
            "new_cases": 0,
            "followup_attempts": 0,
            "runtime_seconds": budget.elapsed_seconds,
        }


class BudgetFailCollector:
    def __init__(self, clock: StaticClock):
        self.clock = clock

    def run_cycle(self, run_id, *, budget):
        self.clock.value = 195.0
        budget.request_timeout(3.05, 30, phase="collector_network")
        raise AssertionError("unreachable")


def test_slot_claim_is_atomic_unique_and_records_duplicate_and_late(tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    object.__setattr__(config, "db_path", tmp_path / "trades_sim.db")
    repository = ResearchRepository(config.db_path)
    repository.initialize(config)

    first = repository.claim_cycle_slot(
        config,
        claimed_at=datetime(2026, 8, 23, 20, 0, 30, tzinfo=timezone.utc),
        invocation_id="invocation-1",
        run_id="run-1",
    )
    duplicate = repository.claim_cycle_slot(
        config,
        claimed_at=datetime(2026, 8, 23, 20, 0, 40, tzinfo=timezone.utc),
        invocation_id="invocation-2",
        run_id="run-2",
    )
    late = repository.claim_cycle_slot(
        config,
        claimed_at=datetime(2026, 8, 23, 20, 6, 1, tzinfo=timezone.utc),
        invocation_id="invocation-3",
        run_id="run-3",
    )

    assert first.accepted is True
    assert first.slot_at == "2026-08-23T20:00:00Z"
    assert first.lateness_seconds == 30
    assert duplicate.accepted is False
    assert duplicate.event_type == "SKIPPED_DUPLICATE"
    assert duplicate.claim_id == first.claim_id
    assert late.accepted is False
    assert late.event_type == "SKIPPED_LATE"
    assert late.slot_at == "2026-08-23T20:05:00Z"
    assert late.lateness_seconds == 61
    with sqlite3.connect(repository.db_path) as connection:
        claims = connection.execute("SELECT COUNT(*) FROM cycle_slot_claims").fetchone()[0]
        events = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM cycle_slot_events ORDER BY rowid"
            )
        ]
    assert claims == 2
    assert events == ["CLAIMED", "SKIPPED_DUPLICATE", "SKIPPED_LATE"]


def test_duplicate_bot_invocation_returns_before_collector_or_http(tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    object.__setattr__(config, "db_path", tmp_path / "trades_sim.db")
    repository = ResearchRepository(config.db_path)
    collector = FakeCollector()
    now = datetime(2026, 8, 23, 20, 0, 10, tzinfo=timezone.utc)
    bot = PolymarketResearchBot(
        config,
        repository=repository,
        collector=collector,
        utcnow=lambda: now,
        monotonic=StaticClock(),
    )

    first = bot.run()
    second = bot.run()

    assert first["slot"]["slot_at"] == "2026-08-23T20:00:00Z"
    assert second["status"] == "SKIPPED_DUPLICATE"
    assert second["http_requests_allowed"] is False
    assert collector.calls == 1


def test_late_bot_invocation_returns_before_collector_or_http(tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    object.__setattr__(config, "db_path", tmp_path / "trades_sim.db")
    repository = ResearchRepository(config.db_path)
    collector = FakeCollector()
    bot = PolymarketResearchBot(
        config,
        repository=repository,
        collector=collector,
        utcnow=lambda: datetime(2026, 8, 23, 20, 1, 1, tzinfo=timezone.utc),
        monotonic=StaticClock(),
    )

    result = bot.run()

    assert result["status"] == "SKIPPED_LATE"
    assert result["http_requests_allowed"] is False
    assert result["lateness_seconds"] == 61
    assert collector.calls == 0
    with sqlite3.connect(repository.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM api_requests").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM research_run_events").fetchone()[0] == 0


def test_budget_failure_records_terminal_deadline_evidence(tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    object.__setattr__(config, "db_path", tmp_path / "trades_sim.db")
    repository = ResearchRepository(config.db_path)
    clock = StaticClock()
    bot = PolymarketResearchBot(
        config,
        repository=repository,
        collector=BudgetFailCollector(clock),
        utcnow=lambda: datetime(2026, 8, 23, 20, 0, 10, tzinfo=timezone.utc),
        monotonic=clock,
    )

    with pytest.raises(RuntimeError, match="network stop margin"):
        bot.run()

    with sqlite3.connect(repository.db_path) as connection:
        terminal = connection.execute(
            """
            SELECT event_type, details_json FROM research_run_events
            ORDER BY rowid DESC LIMIT 1
            """
        ).fetchone()
    details = json.loads(terminal[1])
    assert terminal[0] == "FAILED"
    assert details["duration_seconds"] == 195.0
    assert details["hard_cycle_limit_seconds"] == 240.0
    assert details["deadline_error"]["phase"] == "collector_network"
