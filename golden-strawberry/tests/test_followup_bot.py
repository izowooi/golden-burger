from __future__ import annotations

from collections import namedtuple
from datetime import datetime, timezone
import json
import sqlite3

import pytest

import polybot.db.followup_repository as repository_module
import polybot.followup_bot as bot_module
from polybot.db.followup_repository import FollowupRepository
from polybot.followup_bot import FollowupBot
from polybot.followup_collector import FollowupCollector
from tests.followup_support import FollowupBooks, FollowupGamma, build_v1_handoff


FIXED_NOW = datetime(2026, 8, 24, 12, 7, 5, tzinfo=timezone.utc)


def _healthy_disk(monkeypatch):
    DiskUsage = namedtuple("DiskUsage", "total used free")
    usage = DiskUsage(500 * 1024**3, 100 * 1024**3, 400 * 1024**3)
    monkeypatch.setattr(bot_module.shutil, "disk_usage", lambda path: usage)
    monkeypatch.setattr(repository_module.shutil, "disk_usage", lambda path: usage)


def _bot(config, followup_config, monkeypatch):
    build_v1_handoff(config)
    _healthy_disk(monkeypatch)
    repository = FollowupRepository(followup_config.db_path)
    books = FollowupBooks(repository)
    gamma = FollowupGamma(repository)
    collector = FollowupCollector(
        followup_config,
        repository=repository,
        clob_client=books,
        gamma_client=gamma,
    )
    bot = FollowupBot(
        followup_config,
        repository=repository,
        collector=collector,
        utcnow=lambda: FIXED_NOW,
    )
    return bot, repository, books, gamma


def test_bot_transitions_full_seed_to_verified_pinned_fast(
    config, followup_config, monkeypatch
):
    bot, repository, books, gamma = _bot(config, followup_config, monkeypatch)

    first = bot.run()
    second = bot.run()

    assert first["validation_mode"] == "FULL_SEED"
    assert first["runtime_sla_met"] is None
    assert second["validation_mode"] == "PINNED_FAST"
    assert second["runtime_sla_met"] is True
    assert second["seed_integrity"]["healthy"] is True
    assert second["seed_integrity"]["observed"] == second["seed_integrity"][
        "expected"
    ]
    assert books.calls == [("token-yes",), ("token-yes",)]
    assert gamma.calls == [("condition",), ("condition",)]

    with repository.read_connect() as connection:
        modes = [
            row[0]
            for row in connection.execute(
                "SELECT validation_mode FROM followup_cycles ORDER BY cycle_number"
            )
        ]
        phases = [
            json.loads(str(row[0]))["validation_mode"]
            for row in connection.execute(
                "SELECT details_json FROM phase_timings "
                "WHERE phase_name='v1_anchor_validation' ORDER BY completed_at"
            )
        ]
    assert modes == ["FULL_SEED", "PINNED_FAST"]
    assert phases == modes


def test_seed_corruption_fails_pinned_fast_before_network_or_cycle_publication(
    config, followup_config, monkeypatch
):
    bot, repository, books, gamma = _bot(config, followup_config, monkeypatch)
    bot.run()
    first_request_count = len(books.calls) + len(gamma.calls)

    with sqlite3.connect(repository.db_path) as connection:
        connection.execute("DROP TRIGGER imported_episodes_no_update")
        connection.execute(
            "UPDATE imported_episodes SET episode_json='{}' "
            "WHERE episode_id=(SELECT MIN(episode_id) FROM imported_episodes)"
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="imported seed integrity drift"):
        bot.run()

    assert len(books.calls) + len(gamma.calls) == first_request_count
    with repository.read_connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM followup_cycles").fetchone()[0] == 1
        terminal = connection.execute(
            "SELECT event_type FROM research_run_events "
            "ORDER BY event_at DESC,event_id DESC LIMIT 1"
        ).fetchone()[0]
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='trigger' AND name='imported_episodes_no_update'"
        ).fetchone()[0] == 1
    assert terminal == "FAILED"


def test_failure_after_cycle_rows_are_staged_rolls_back_success_state(
    config, followup_config, monkeypatch
):
    bot, repository, _, _ = _bot(config, followup_config, monkeypatch)

    def fail_after_evidence_insert(connection, bundle):
        assert connection.execute("SELECT COUNT(*) FROM followup_cycles").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM episode_path_observations"
        ).fetchone()[0] == 4
        raise RuntimeError("injected post-publication-stage failure")

    monkeypatch.setattr(repository, "_before_success_finalize", fail_after_evidence_insert)
    with pytest.raises(RuntimeError, match="post-publication-stage"):
        bot.run()

    with repository.read_connect() as connection:
        for table in (
            "followup_cycles",
            "book_token_attempts",
            "compact_books",
            "episode_path_observations",
            "episode_threshold_events",
            "resolution_observations",
            "data_quality_issues",
            "phase_timings",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM api_requests").fetchone()[0] > 0
        assert connection.execute("SELECT COUNT(*) FROM imported_episodes").fetchone()[0] == 4
        terminal_counts = dict(
            connection.execute(
                "SELECT event_type,COUNT(*) FROM research_run_events GROUP BY event_type"
            )
        )
    assert terminal_counts == {"FAILED": 1, "STARTED": 1}

    monkeypatch.setattr(
        repository,
        "_before_success_finalize",
        lambda _connection, _bundle: None,
    )
    deployment_retry = bot.run()
    recurring = bot.run()
    assert deployment_retry["validation_mode"] == "FULL_SEED"
    assert recurring["validation_mode"] == "PINNED_FAST"
