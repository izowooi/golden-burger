from __future__ import annotations

import sqlite3
from uuid import uuid4

import pytest

from polybot.db.followup_repository import APPEND_ONLY_TABLES, FollowupRepository
from polybot.followup_collector import decode_compact_book, encode_compact_book
from polybot.utils.retry import canonical_json, iso_utc
from polybot.v1_source import V1SourceReader
from tests.followup_support import build_followup_evidence, build_v1_handoff


def _snapshot(config, followup_config):
    build_v1_handoff(config)
    return V1SourceReader(followup_config.trading.v1_source).capture()


def test_compact_book_roundtrip_is_deterministic_and_canonical():
    book = {
        "asset_id": "token",
        "asks": [
            {"price": "0.91", "size": "2.00"},
            {"price": "0.90", "size": "1"},
        ],
        "bids": [
            {"price": "0.88", "size": "3"},
            {"price": "0.89", "size": "1.50"},
        ],
        "tick_size": "0.01",
    }
    first = encode_compact_book("token", book)
    second = encode_compact_book("token", book)
    assert first["blob"] == second["blob"]
    assert first["sha256"] == second["sha256"]
    decoded = decode_compact_book(
        first["blob"], expected_sha256=first["sha256"]
    )
    assert decoded["bids"] == [["0.89", "1.5"], ["0.88", "3"]]
    assert decoded["asks"] == [["0.9", "1"], ["0.91", "2"]]


def test_one_shared_token_book_serves_multiple_threshold_episodes(
    config, followup_config
):
    snapshot = _snapshot(config, followup_config)
    evidence = build_followup_evidence(followup_config, snapshot)
    with evidence.repository.read_connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM imported_episodes").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM compact_books").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM episode_path_observations").fetchone()[0] == 4
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "clob_levels" not in tables
    assert evidence.books.calls == [("token-yes",)]


def test_resolved_condition_is_excluded_from_all_later_requests(
    config, followup_config
):
    snapshot = _snapshot(config, followup_config)
    evidence = build_followup_evidence(followup_config, snapshot, cycles=3)
    assert evidence.books.calls == [("token-yes",), ("token-yes",), ()]
    assert evidence.gamma.calls == [("condition",), ("condition",), ()]
    with evidence.repository.read_connect() as connection:
        third = connection.execute(
            "SELECT * FROM followup_cycles WHERE cycle_number=3"
        ).fetchone()
        assert third["unresolved_episode_count"] == 0
        assert third["distinct_token_count"] == 0
        assert third["distinct_condition_count"] == 0


def test_schema_is_append_only_and_cycle_failure_rolls_back(
    config, followup_config
):
    snapshot = _snapshot(config, followup_config)
    repository = FollowupRepository(followup_config.db_path)
    repository.initialize(followup_config)
    repository.ensure_seed(snapshot)
    repository.register_config(followup_config, git_commit=None)
    now = iso_utc()
    cycle_id = uuid4().hex
    bundle = {
        "expected_tokens": [],
        "expected_conditions": [],
        "expected_episode_ids": [],
        "cycle": {
            "cycle_id": cycle_id,
            "run_id": "rollback-run",
            "cycle_number": 1,
            "config_hash": followup_config.config_hash,
            "strategy_source_digest": followup_config.trading.strategy_source_digest,
            "anchor_id": "v1-seed",
            "anchor_sha256": snapshot.anchor_sha256,
            "validation_mode": "PINNED_FAST",
            "started_at": now,
            "completed_at": now,
            "published_at": now,
            "unresolved_episode_count": 0,
            "distinct_token_count": 0,
            "distinct_condition_count": 0,
            "book_observed_count": 0,
            "path_observation_count": 0,
            "resolution_observation_count": 0,
            "newly_resolved_condition_count": 0,
            "prepublication_seconds": 0.1,
            "summary_json": "{}",
        },
        "quality_issues": [
            {
                "issue_id": uuid4().hex,
                "cycle_id": cycle_id,
                "run_id": "rollback-run",
                "severity": "INVALID",
                "issue_code": "INJECTED",
                "recorded_at": now,
                "details_json": "{}",
            }
        ],
    }
    with pytest.raises(sqlite3.IntegrityError):
        repository.publish_successful_cycle(
            bundle,
            storage=followup_config.trading.storage,
            finalize=lambda _seconds, _storage: pytest.fail(
                "invalid cycle evidence reached successful finalization"
            ),
        )
    with repository.read_connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM followup_cycles").fetchone()[0] == 0
        trigger_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'"
        ).fetchone()[0]
    assert trigger_count == len(APPEND_ONLY_TABLES) * 2
    with repository._connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE imported_episodes SET outcome_label='changed'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM source_anchors")
