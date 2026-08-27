from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from polybot.config import (
    CLASSIFIER_VERSION,
    DATA_CONTRACT,
    LEAGUE_MAPPING_SHA256,
    SCHEMA_PROFILE,
    UNIVERSE_PROFILE,
    league_registry_payload,
)
from polybot.db.repository import (
    APPLICATION_ID,
    EXPECTED_SCHEMA_SHA256,
    MIGRATION_PATH,
    SCHEMA_USER_VERSION,
    ResearchRepository,
)


def mapping_json(payload: dict[str, object] | None = None) -> str:
    return json.dumps(
        league_registry_payload() if payload is None else payload,
        sort_keys=True,
        separators=(",", ":"),
    )


def mapping_sha(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            {"classifier_version": CLASSIFIER_VERSION, **payload},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def repository(path: Path) -> ResearchRepository:
    return ResearchRepository(
        path,
        busy_timeout_ms=1000,
        data_contract=DATA_CONTRACT,
        schema_profile=SCHEMA_PROFILE,
        universe_profile=UNIVERSE_PROFILE,
        classifier_version=CLASSIFIER_VERSION,
        league_mapping_sha256=LEAGUE_MAPPING_SHA256,
        league_mapping_json=mapping_json(),
    )


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_create_only_schema_identity_registry_and_append_only(tmp_path) -> None:
    db = tmp_path / "trades_sim.db"
    repo = repository(db)
    assert repo.quick_check() == "ok"
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_USER_VERSION
        metadata = connection.execute(
            """
            SELECT data_contract,schema_profile,universe_profile,classifier_version,
                   league_mapping_sha256,migration_sha256,schema_sha256
            FROM schema_metadata
            """
        ).fetchone()
        registry = connection.execute(
            """
            SELECT league_mapping_sha256,classifier_version,universe_profile,mapping_json
            FROM league_registry_versions
            """
        ).fetchone()
    assert metadata[:5] == (
        DATA_CONTRACT,
        SCHEMA_PROFILE,
        UNIVERSE_PROFILE,
        CLASSIFIER_VERSION,
        LEAGUE_MAPPING_SHA256,
    )
    assert metadata[5] == hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest()
    assert metadata[6] == EXPECTED_SCHEMA_SHA256
    assert registry == (
        LEAGUE_MAPPING_SHA256,
        CLASSIFIER_VERSION,
        UNIVERSE_PROFILE,
        mapping_json(),
    )

    repo.record_config(
        {
            "config_hash": "c",
            "strategy_source_digest": "s",
            "preregistration_sha256": "p",
            "job_name": "watermelon-white-1m-v3d",
            "mode": "sim",
            "config_json": "{}",
            "first_seen_at": "2026-08-24T00:00:00Z",
        }
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with repo.connect() as connection:
            connection.execute("UPDATE research_config_versions SET mode='live'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with repo.connect() as connection:
            connection.execute("DELETE FROM schema_metadata")


def test_payload_is_compressed_deterministically(tmp_path) -> None:
    repo = repository(tmp_path / "trades_sim.db")
    row = repo.payload_row(
        run_id="r",
        kind="X",
        request_id="q",
        observed_at="2026-08-24T00:00:00Z",
        raw=b"x" * 1000,
    )
    assert row["gzip_bytes"] < row["raw_bytes"]
    assert len(row["sha256"]) == 64


def test_event_evidence_owns_authority_json_without_market_duplication(tmp_path) -> None:
    repo = repository(tmp_path / "trades_sim.db")
    with repo.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        event_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(event_observations)")
        }
        market_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(market_observations)")
        }
    assert {
        "event_observations",
        "league_registry_versions",
        "counterfactual_exit_policies",
        "stop_execution_attempts",
        "counterfactual_stop_exits",
        "database_checks",
    } <= names
    assert {
        "sport_json",
        "tags_json",
        "series_json",
        "teams_json",
        "classification_status",
        "rejection_reason",
        "league_mapping_sha256",
    } <= event_columns
    assert "event_observation_id" in market_columns
    assert {
        "sport_json",
        "tags_json",
        "series_json",
        "teams_json",
        "event_tag_slugs_json",
        "team_leagues_json",
    }.isdisjoint(market_columns)


def test_existing_wrong_epoch_preflight_leaves_database_byte_identical(tmp_path) -> None:
    db = tmp_path / "legacy-v3.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE schema_metadata(data_contract TEXT PRIMARY KEY,created_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_metadata VALUES(?,?)",
            ("soccer-inplay-major-league-match-winner-v1", "2026-08-23T00:00:00Z"),
        )
    before_hash = file_sha256(db)
    with sqlite3.connect(db) as connection:
        before_tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        before_schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]

    with pytest.raises(RuntimeError, match="epoch mismatch"):
        repository(db)

    assert file_sha256(db) == before_hash
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA schema_version").fetchone()[0] == before_schema_version
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall() == before_tables
    assert not Path(f"{db}-wal").exists()
    assert not Path(f"{db}-shm").exists()


def test_existing_mapping_mismatch_fails_before_writable_open(tmp_path) -> None:
    db = tmp_path / "v3a.db"
    repository(db)
    before_hash = file_sha256(db)
    alternate = {**league_registry_payload(), "soccer_tag_id": 999}
    with pytest.raises(RuntimeError, match="contract/schema/mapping mismatch"):
        ResearchRepository(
            db,
            busy_timeout_ms=1000,
            data_contract=DATA_CONTRACT,
            schema_profile=SCHEMA_PROFILE,
            universe_profile=UNIVERSE_PROFILE,
            classifier_version=CLASSIFIER_VERSION,
            league_mapping_sha256=mapping_sha(alternate),
            league_mapping_json=mapping_json(alternate),
        )
    assert file_sha256(db) == before_hash


def test_existing_schema_drift_ignores_self_claim_and_remains_unchanged(tmp_path) -> None:
    db = tmp_path / "v3a-schema-drift.db"
    repository(db)
    with sqlite3.connect(db) as connection:
        connection.execute("DROP INDEX event_status_time_idx")
    before_hash = file_sha256(db)
    with pytest.raises(RuntimeError, match="schema fingerprint mismatch"):
        repository(db)
    assert file_sha256(db) == before_hash
    assert not Path(f"{db}-wal").exists()
    assert not Path(f"{db}-shm").exists()


def test_reopening_exact_epoch_is_read_only_until_connect(tmp_path) -> None:
    db = tmp_path / "v3a.db"
    repository(db)
    before_hash = file_sha256(db)
    before_mtime = db.stat().st_mtime_ns
    repository(db)
    assert file_sha256(db) == before_hash
    assert db.stat().st_mtime_ns == before_mtime


def test_no_runtime_alter_and_migration_is_create_only() -> None:
    repository_source = Path(__file__).parents[1] / "src/polybot/db/repository.py"
    assert "ALTER TABLE" not in repository_source.read_text(encoding="utf-8")
    assert "ALTER TABLE" not in MIGRATION_PATH.read_text(encoding="utf-8")


def test_scheduled_database_check_runs_full_once_then_uses_probe(
    tmp_path, monkeypatch
) -> None:
    repo = repository(tmp_path / "trades_sim.db")
    calls = 0

    def quick_check() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    monkeypatch.setattr(repo, "quick_check", quick_check)
    start = datetime(2026, 8, 24, tzinfo=timezone.utc)
    first = repo.scheduled_database_check("run-1", now=start)
    second = repo.scheduled_database_check("run-2", now=start + timedelta(minutes=5))
    assert calls == 1
    assert first["mode"] == "FULL_QUICK_CHECK"
    assert first["full_check_performed"] is True
    assert second["mode"] == "LIGHTWEIGHT_PROBE"
    assert second["full_check_performed"] is False
    with repo.connect() as connection:
        checks = connection.execute("SELECT run_id,result FROM database_checks").fetchall()
    assert [tuple(row) for row in checks] == [("run-1", "ok")]


def test_scheduled_database_check_repeats_after_interval(tmp_path, monkeypatch) -> None:
    repo = repository(tmp_path / "trades_sim.db")
    calls = 0

    def quick_check() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    monkeypatch.setattr(repo, "quick_check", quick_check)
    start = datetime(2026, 8, 24, tzinfo=timezone.utc)
    repo.scheduled_database_check("run-1", now=start, interval=timedelta(hours=1))
    result = repo.scheduled_database_check(
        "run-2", now=start + timedelta(hours=1, seconds=1), interval=timedelta(hours=1)
    )
    assert calls == 2
    assert result["full_check_performed"] is True


def test_scheduled_database_check_fails_closed_and_records_result(
    tmp_path, monkeypatch
) -> None:
    repo = repository(tmp_path / "trades_sim.db")
    monkeypatch.setattr(repo, "quick_check", lambda: "corrupt page")
    with pytest.raises(RuntimeError, match="SQLite quick_check failed"):
        repo.scheduled_database_check(
            "run-1", now=datetime(2026, 8, 24, tzinfo=timezone.utc)
        )
    with repo.connect() as connection:
        result = connection.execute("SELECT result FROM database_checks").fetchone()[0]
    assert result == "corrupt page"
