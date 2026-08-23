from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import sqlite3

import pytest

from polybot.config import PROJECT_ROOT, load_config
from polybot.db.repository import ResearchRepository


def test_schema_and_contract_are_immutable(monkeypatch, tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    repository = ResearchRepository(tmp_path / "trades_sim.db")
    repository.initialize(config)
    repository.register_config(config, git_commit=None)
    with sqlite3.connect(repository.db_path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        contract = connection.execute("SELECT shard_index, shard_count FROM experiment_contracts").fetchone()
        assert contract == (0, 3)
        with pytest.raises(sqlite3.IntegrityError, match="append-only evidence"):
            connection.execute("UPDATE experiment_contracts SET shard_index=1")


def test_each_job_gets_a_distinct_db_path(monkeypatch):
    configs = [
        load_config(PROJECT_ROOT / "config.yaml", job)
        for job in (
            "raspberry-do-v3-shard-0",
            "raspberry-re-v3-shard-1",
            "raspberry-mi-v3-shard-2",
        )
    ]
    assert len({config.db_path for config in configs}) == 3


def _insert_case(repository, *, target_at, window_end):
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """
            INSERT INTO research_cases(
                case_id, decision_id, case_kind, matched_pair_id, condition_id,
                event_id, token_id, outcome_label, entry_snapshot_id, entry_at,
                entry_cost_usdc, entry_shares, entry_vwap, target_at, window_end,
                control_match_distance
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "case",
                "decision",
                "SIGNAL",
                "pair",
                "condition",
                "event",
                "token",
                "Yes",
                "snapshot",
                "2026-08-23T20:00:00Z",
                5.0,
                10.0,
                0.5,
                target_at,
                window_end,
                None,
            ),
        )


def test_followup_request_start_is_durable_and_stale_recovery_never_retries(
    monkeypatch, tmp_path
):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    repository = ResearchRepository(tmp_path / "trades_sim.db")
    repository.initialize(config)
    _insert_case(
        repository,
        target_at="2026-08-23T21:00:00Z",
        window_end="2026-08-23T21:15:00Z",
    )
    first = repository.claim_due_followups(
        run_id="run-1",
        now=datetime(2026, 8, 23, 21, 1, tzinfo=timezone.utc),
        stale_after_seconds=120,
    )
    assert [row["case_id"] for row in first.due] == ["case"]
    assert repository.mark_followup_requests_started(
        first.due,
        token_ids=["token"],
        run_id="run-1",
        logical_request_id="logical-first",
        request_started_at="2026-08-23T21:01:00Z",
    ) == 1
    assert repository.mark_followup_requests_started(
        first.due,
        token_ids=["token"],
        run_id="run-1",
        logical_request_id="logical-duplicate",
        request_started_at="2026-08-23T21:01:01Z",
    ) == 0

    recovered = repository.claim_due_followups(
        run_id="run-2",
        now=datetime(2026, 8, 23, 21, 5, tzinfo=timezone.utc),
        stale_after_seconds=120,
    )
    assert recovered.due == []
    assert recovered.stale_terminalized == 1
    with sqlite3.connect(repository.db_path) as connection:
        request_start = connection.execute(
            "SELECT logical_request_id FROM followup_request_starts"
        ).fetchone()[0]
        terminal = connection.execute(
            "SELECT status FROM followup_attempts"
        ).fetchone()[0]
    assert request_start == "logical-first"
    assert terminal == "STALE_REQUEST_UNKNOWN"


def test_unstarted_stale_followup_claim_gets_new_append_only_lease(monkeypatch, tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    repository = ResearchRepository(tmp_path / "trades_sim.db")
    repository.initialize(config)
    _insert_case(
        repository,
        target_at="2026-08-23T21:00:00Z",
        window_end="2026-08-23T21:15:00Z",
    )
    first = repository.claim_due_followups(
        run_id="run-1",
        now=datetime(2026, 8, 23, 21, 1, tzinfo=timezone.utc),
        stale_after_seconds=120,
    )
    second = repository.claim_due_followups(
        run_id="run-2",
        now=datetime(2026, 8, 23, 21, 4, tzinfo=timezone.utc),
        stale_after_seconds=120,
    )
    assert len(first.due) == len(second.due) == 1
    assert second.recovered_claims == 1
    assert second.due[0]["lease_generation"] == 2
    with sqlite3.connect(repository.db_path) as connection:
        generations = [
            row[0]
            for row in connection.execute(
                "SELECT generation FROM followup_claim_leases ORDER BY generation"
            )
        ]
    assert generations == [1, 2]


def test_v3_initialize_refuses_to_mutate_v2_database(monkeypatch, tmp_path):
    path = tmp_path / "external-v2.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_metadata(key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO schema_metadata VALUES('schema_version', '1')")
        connection.execute("INSERT INTO schema_metadata VALUES('data_contract', 'queue-echo-v1')")
        connection.execute("CREATE TABLE legacy_evidence(value TEXT)")
        connection.execute("INSERT INTO legacy_evidence VALUES('preserve-me')")
    before_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    before_files = {item.name for item in tmp_path.iterdir()}
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    with pytest.raises(RuntimeError, match="refusing to migrate or mutate"):
        ResearchRepository(path).initialize(config)
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        value = connection.execute("SELECT value FROM legacy_evidence").fetchone()[0]
    assert tables == {"schema_metadata", "legacy_evidence"}
    assert value == "preserve-me"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_sha256
    assert {item.name for item in tmp_path.iterdir()} == before_files
