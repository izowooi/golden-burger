from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from polybot.api.transport import iso_utc
from polybot.db.repository import (
    APPEND_ONLY_TABLES,
    ResearchRepository,
    SlotAlreadyClaimed,
)
from polybot.registry import FAMILY_ORDER
from polybot.run_audit import ResearchRunAudit


def minimal_bundle(config, *, run_id="run-1", cycle_id="cycle-1", slot="2026-08-27T00:00:00Z"):
    cycle = {
        "cycle_id": cycle_id,
        "run_id": run_id,
        "slot_start_utc": slot,
        "job_name": config.job_name,
        "mode": config.mode,
        "started_at": slot,
        "cooperative_deadline_at": "2026-08-27T00:03:45Z",
        "request_stop_at": "2026-08-27T00:03:15Z",
        "hard_deadline_at": "2026-08-27T00:04:00Z",
        "completed_at": "2026-08-27T00:00:10Z",
        "elapsed_seconds": 10,
        "receipt_skew_seconds": 2,
        "all_families_cursor_complete": 1,
        "followup_complete": 1,
        "request_envelope_json": "{}",
        "summary_json": "{}",
    }
    sweeps = [
        {
            "sweep_id": f"sweep-{family}",
            "cycle_id": cycle_id,
            "run_id": run_id,
            "sport_family": family,
            "tag_id": config.registry.by_code[family].tag_id,
            "started_at": slot,
            "completed_at": "2026-08-27T00:00:01Z",
            "page_count": 1,
            "source_event_count": 0,
            "accepted_event_count": 0,
            "rejected_event_count": 0,
            "drift_event_count": 0,
            "cursor_complete": 1,
            "terminal_cursor": None,
            "start_time_min": "2026-08-26T00:00:00Z",
            "start_time_max": "2026-08-29T00:00:00Z",
            "request_envelope_json": "{}",
        }
        for family in FAMILY_ORDER
    ]
    return {
        "cycle": cycle,
        "sweeps": sweeps,
        "raw_payloads": [],
        "events": [],
        "game_lifecycle": [],
        "schedule_revisions": [],
        "tags": [],
        "series": [],
        "teams": [],
        "markets": [],
        "outcomes": [],
        "book_attempts": [],
        "book_snapshots": [],
        "book_ladder": [],
        "threshold_vectors": [],
        "episodes": [],
        "paths": [],
        "anchors": [],
        "resolution_attempts": [],
        "resolutions": [],
        "sports_clock": [],
        "quality_issues": [],
        "storage_metrics": [],
        "database_checks": [],
    }


def test_create_only_schema_has_required_domains_and_no_transaction_tables(config):
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    with repository.read_connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        sweep_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sport_sweeps)")
        }
    assert set(APPEND_ONLY_TABLES) == tables
    assert tables.isdisjoint({"orders", "fills", "positions", "wallets", "trades", "pnl"})
    assert {"start_time_min", "start_time_max"} <= sweep_columns
    assert {"start_date_min", "start_date_max"}.isdisjoint(sweep_columns)
    assert repository.path.name == "trades_sim.db"


def test_v5_repository_rejects_a_v2_database(config):
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    migration = (
        Path(__file__).resolve().parents[1]
        / "src/polybot/db/migrations/0002_major_sports_lifecycle_v2.sql"
    )
    with sqlite3.connect(config.db_path) as connection:
        connection.executescript(migration.read_text(encoding="utf-8"))
    with pytest.raises(RuntimeError, match="application/user version epoch mismatch"):
        ResearchRepository(config, database_utc_date="2026-08-27")


def test_every_evidence_table_rejects_update_and_delete(config):
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    with repository.write_connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE schema_metadata SET database_utc_date='2099-01-01'"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM sports_registry_versions")


def test_schema_drift_is_detected_before_writable_use(config):
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    with sqlite3.connect(repository.path) as connection:
        connection.execute("CREATE TABLE unexpected_table(value TEXT)")
    with pytest.raises(RuntimeError, match="schema fingerprint"):
        ResearchRepository(config, database_utc_date="2026-08-27")


def test_atomic_slot_claim_rejects_duplicate(config):
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    now = datetime(2026, 8, 27, 0, 2, tzinfo=timezone.utc)
    assert repository.claim_slot(run_id="run-a", now=now) == "2026-08-27T00:00:00Z"
    with pytest.raises(SlotAlreadyClaimed):
        repository.claim_slot(run_id="run-b", now=now)


def test_atomic_bundle_rolls_back_all_rows_on_precommit_failure(config, monkeypatch):
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    bundle = minimal_bundle(config)
    audit = ResearchRunAudit(config, "run-1")

    def fail(connection, supplied):
        raise RuntimeError("injected publication failure")

    monkeypatch.setattr(repository, "_before_publish_commit", fail)
    with pytest.raises(RuntimeError, match="injected"):
        repository.publish_cycle(bundle, terminal_event=audit.event_row("SUCCEEDED"))
    with repository.read_connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM collection_cycles").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM sport_sweeps").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM research_run_events").fetchone()[0] == 0


def test_success_bundle_commits_cycle_and_terminal_event(config):
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    bundle = minimal_bundle(config)
    audit = ResearchRunAudit(config, "run-1")
    repository.publish_cycle(bundle, terminal_event=audit.event_row("SUCCEEDED"))
    with repository.read_connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM collection_cycles").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM sport_sweeps").fetchone()[0] == 5
        assert connection.execute(
            "SELECT event_type FROM research_run_events"
        ).fetchone()[0] == "SUCCEEDED"


def test_stale_v2_sweep_payload_cannot_publish_into_v5(config):
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    bundle = minimal_bundle(config)
    for sweep in bundle["sweeps"]:
        sweep["start_date_min"] = sweep.pop("start_time_min")
        sweep["start_date_max"] = sweep.pop("start_time_max")
    audit = ResearchRunAudit(config, "run-1")
    with pytest.raises(sqlite3.OperationalError, match="start_date_min"):
        repository.publish_cycle(
            bundle, terminal_event=audit.event_row("SUCCEEDED")
        )
    with repository.read_connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM collection_cycles").fetchone()[0] == 0


def test_utc_rotation_archives_whole_canonical_file_and_creates_new_active(config):
    old = ResearchRepository(config, database_utc_date="2026-08-26")
    old.register_config()
    active_inode = old.path.stat().st_ino
    current = ResearchRepository.prepare(
        config, now=datetime(2026, 8, 27, 0, 1, tzinfo=timezone.utc)
    )
    archive = config.db_path.with_name("trades_sim_20260826.db")
    assert archive.is_file()
    assert archive.stat().st_ino == active_inode
    assert current.path.is_file()
    assert current.path.stat().st_ino != active_inode
    assert current.database_utc_date == "2026-08-27"
    assert current.quick_check() == "ok"
    with sqlite3.connect(archive) as connection:
        assert connection.execute(
            "SELECT contract_name,database_utc_date FROM collection_contracts"
        ).fetchall() == [("research-full-v1", "2026-08-26")]

    remote_agent = Path(__file__).resolve().parents[2] / "daily-rsync/src/daily_rsync/remote_agent.py"
    spec = importlib.util.spec_from_file_location("daily_rsync_remote_agent", remote_agent)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    identity = module.database_identity(archive)
    assert identity[3:] == ("research-full-v1", "2026-08-26")
