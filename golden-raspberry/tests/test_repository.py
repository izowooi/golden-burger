from __future__ import annotations

import sqlite3

import pytest

from polybot.config import PROJECT_ROOT, load_config
from polybot.db.repository import ResearchRepository


def test_schema_and_contract_are_immutable(monkeypatch, tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-shard-0")
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
        for job in ("raspberry-do-shard-0", "raspberry-re-shard-1", "raspberry-mi-shard-2")
    ]
    assert len({config.db_path for config in configs}) == 3


def test_first_followup_attempt_is_terminal_for_case_selection(monkeypatch, tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-shard-0")
    repository = ResearchRepository(tmp_path / "trades_sim.db")
    repository.initialize(config)
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
                "2026-08-13T01:00:00Z",
                5.0,
                10.0,
                0.5,
                "2026-08-13T02:00:00Z",
                "2026-08-13T02:15:00Z",
                None,
            ),
        )
    due, expired = repository.pending_cases(now="2026-08-13T02:01:00Z")
    assert [row["case_id"] for row in due] == ["case"]
    assert expired == []

    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(
            """
            INSERT INTO followup_attempts(
                followup_id, case_id, observing_run_id, attempted_at, status,
                source_snapshot_id, observed_at, exit_bid, exit_vwap,
                exit_proceeds_usdc, executable_return_bps,
                base_stressed_return_bps, severe_stressed_return_bps, details_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "followup",
                "case",
                "run",
                "2026-08-13T02:01:00Z",
                "SOURCE_MISSING",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "{}",
            ),
        )
    due, expired = repository.pending_cases(now="2026-08-13T02:05:00Z")
    assert due == []
    assert expired == []
