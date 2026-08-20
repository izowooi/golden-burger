from __future__ import annotations

import sqlite3

import pytest

from polybot.db.repository import ResearchRepository


def test_schema_quick_check_and_append_only(tmp_path) -> None:
    repository = ResearchRepository(tmp_path / "trades_sim.db", busy_timeout_ms=1000, data_contract="sports-resolution-paired-v1")
    assert repository.quick_check() == "ok"
    repository.record_config({
        "config_hash": "c", "strategy_source_digest": "s", "preregistration_sha256": "p",
        "job_name": "black-shadow-paired", "mode": "sim", "config_json": "{}",
        "first_seen_at": "2026-08-20T00:00:00Z",
    })
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with repository.connect() as connection:
            connection.execute("UPDATE research_config_versions SET mode='live'")


def test_payload_is_compressed_deterministically(tmp_path) -> None:
    repository = ResearchRepository(tmp_path / "trades_sim.db", busy_timeout_ms=1000, data_contract="sports-resolution-paired-v1")
    row = repository.payload_row(run_id="r", kind="X", request_id="q", observed_at="2026-08-20T00:00:00Z", raw=b"x" * 1000)
    assert row["gzip_bytes"] < row["raw_bytes"]
    assert len(row["sha256"]) == 64


def test_stop_policy_tables_exist_for_append_only_contract(tmp_path) -> None:
    repository = ResearchRepository(
        tmp_path / "trades_sim.db", busy_timeout_ms=1000,
        data_contract="sports-resolution-paired-v1",
    )
    with repository.connect() as connection:
        names = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "counterfactual_exit_policies",
        "stop_execution_attempts",
        "counterfactual_stop_exits",
    } <= names


def test_existing_preflight_database_adds_normalized_neg_risk_stratum(tmp_path) -> None:
    database = tmp_path / "trades_sim.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE market_observations ("
            "observation_id TEXT PRIMARY KEY, condition_id TEXT, observed_at TEXT)"
        )
    repository = ResearchRepository(
        database,
        busy_timeout_ms=1000,
        data_contract="sports-resolution-paired-v1",
    )
    with repository.connect() as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(market_observations)")
        }
    assert "neg_risk" in columns
