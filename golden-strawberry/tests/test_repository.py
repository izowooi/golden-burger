from __future__ import annotations

import sqlite3

import pytest

from polybot.db.repository import APPEND_ONLY_TABLES, ResearchRepository
from tests.support import minimal_bundle


def _repository(config):
    repository = ResearchRepository(config.db_path)
    repository.initialize(config)
    repository.register_config(config, git_commit=None)
    return repository


def test_schema_has_required_evidence_and_only_documented_cache_mutable(config):
    repository = _repository(config)
    with repository._read_connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        metadata = dict(connection.execute("SELECT key,value FROM schema_metadata"))
    assert {
        "experiment_contracts",
        "research_config_versions",
        "research_run_events",
        "api_requests",
        "raw_payloads",
        "gamma_sweeps",
        "gamma_membership_blobs",
        "market_catalog_versions",
        "outcome_observations",
        "crossing_decisions",
        "clob_token_attempts",
        "clob_snapshots",
        "clob_levels",
        "hypothetical_episodes",
        "episode_path_observations",
        "resolution_observations",
        "data_quality_issues",
        "storage_metrics",
        "latest_outcome_state",
    } <= tables
    assert metadata["mutable_cache_table"] == "latest_outcome_state"
    assert "latest_outcome_state" not in APPEND_ONLY_TABLES


def test_complete_cycle_publishes_and_append_only_triggers_reject_mutation(config):
    repository = _repository(config)
    repository.publish_cycle(minimal_bundle(config, repository))
    assert repository.next_cycle_number() == 2
    with repository._connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only evidence"):
            connection.execute("UPDATE gamma_sweeps SET page_count=2")
        with pytest.raises(sqlite3.IntegrityError, match="append-only evidence"):
            connection.execute("DELETE FROM raw_payloads")


def test_partial_cycle_is_rejected_atomically(config):
    repository = _repository(config)
    bundle = minimal_bundle(config, repository)
    bundle["sweep"]["cursor_complete"] = 0
    with pytest.raises(ValueError, match="partial Gamma"):
        repository.publish_cycle(bundle)
    with repository._read_connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM gamma_sweeps").fetchone()[0] == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0] == 0
        )


def test_bad_raw_sha_is_rejected_before_publication(config):
    repository = _repository(config)
    bundle = minimal_bundle(config, repository)
    bundle["raw_payloads"][0]["payload_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        repository.publish_cycle(bundle)
    with repository._read_connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM gamma_sweeps").fetchone()[0] == 0
        )
