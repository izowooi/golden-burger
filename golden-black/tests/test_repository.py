from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

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
        "database_checks",
    } <= names


def test_scheduled_database_check_runs_full_once_then_uses_probe(
    tmp_path, monkeypatch
) -> None:
    repository = ResearchRepository(
        tmp_path / "trades_sim.db",
        busy_timeout_ms=1000,
        data_contract="sports-resolution-paired-v1",
    )
    calls = 0

    def quick_check() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    monkeypatch.setattr(repository, "quick_check", quick_check)
    start = datetime(2026, 8, 21, tzinfo=timezone.utc)
    first = repository.scheduled_database_check("run-1", now=start)
    second = repository.scheduled_database_check(
        "run-2", now=start + timedelta(minutes=5)
    )
    assert calls == 1
    assert first["mode"] == "FULL_QUICK_CHECK"
    assert first["full_check_performed"] is True
    assert second["mode"] == "LIGHTWEIGHT_PROBE"
    assert second["full_check_performed"] is False
    with repository.connect() as connection:
        checks = connection.execute(
            "SELECT run_id,result FROM database_checks"
        ).fetchall()
    assert [tuple(row) for row in checks] == [("run-1", "ok")]


def test_scheduled_database_check_repeats_after_interval(
    tmp_path, monkeypatch
) -> None:
    repository = ResearchRepository(
        tmp_path / "trades_sim.db",
        busy_timeout_ms=1000,
        data_contract="sports-resolution-paired-v1",
    )
    calls = 0

    def quick_check() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    monkeypatch.setattr(repository, "quick_check", quick_check)
    start = datetime(2026, 8, 21, tzinfo=timezone.utc)
    repository.scheduled_database_check(
        "run-1", now=start, interval=timedelta(hours=1)
    )
    result = repository.scheduled_database_check(
        "run-2",
        now=start + timedelta(hours=1, seconds=1),
        interval=timedelta(hours=1),
    )
    assert calls == 2
    assert result["full_check_performed"] is True


def test_scheduled_database_check_fails_closed_and_records_result(
    tmp_path, monkeypatch
) -> None:
    repository = ResearchRepository(
        tmp_path / "trades_sim.db",
        busy_timeout_ms=1000,
        data_contract="sports-resolution-paired-v1",
    )
    monkeypatch.setattr(repository, "quick_check", lambda: "corrupt page")
    with pytest.raises(RuntimeError, match="SQLite quick_check failed"):
        repository.scheduled_database_check(
            "run-1", now=datetime(2026, 8, 21, tzinfo=timezone.utc)
        )
    with repository.connect() as connection:
        result = connection.execute(
            "SELECT result FROM database_checks"
        ).fetchone()[0]
    assert result == "corrupt page"


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
