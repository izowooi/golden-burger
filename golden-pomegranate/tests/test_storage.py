"""Daily shard rotation, integrity, manifest and disk-guard contracts."""

from __future__ import annotations

from collections import namedtuple
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from polybot.config import StorageConfig
from polybot.db.repository import GIB, ResearchRepository


DiskUsage = namedtuple("DiskUsage", "total used free")


def _repository(tmp_path: Path, date: datetime) -> ResearchRepository:
    repository = ResearchRepository(
        tmp_path / "data" / "job" / "trades_sim.db",
        clock=lambda: date,
    )
    repository.initialize()
    return repository


def _trade_sweep(watermark: int) -> dict:
    digest = hashlib.sha256(b"[]").hexdigest()
    return {
        "run_id": "run-1",
        "cycle_number": 1,
        "trade_sweep": {
            "trade_sweep_id": "trade-sweep-1",
            "run_id": "run-1",
            "cycle_number": 1,
            "started_at": "2026-08-06T23:59:00+00:00",
            "completed_at": "2026-08-07T00:00:05+00:00",
            "target_start_epoch": watermark - 900,
            "source_target_end_epoch": watermark,
            "bounded_target_end_epoch": watermark,
            "watermark_before_epoch": watermark - 900,
            "watermark_advance_to_epoch": watermark,
            "status": "SUCCESS",
            "possible_gap": 0,
            "window_count": 0,
            "membership_count": 0,
            "unique_trade_count": 0,
            "head_timestamp_raw": None,
            "tail_timestamp_raw": None,
            "membership_digest_sha256": digest,
            "error_message": None,
        },
    }


def _insert_trade_sweep_fixture(repository: ResearchRepository, watermark: int) -> None:
    """Seed rotation state directly; publish_cycle correctly requires a Gamma census."""
    row = _trade_sweep(watermark)["trade_sweep"]
    columns = tuple(row)
    connection = sqlite3.connect(repository.db_path)
    try:
        connection.execute(
            f"INSERT INTO trade_tape_sweeps ({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            tuple(row[column] for column in columns),
        )
        connection.commit()
    finally:
        connection.close()


def test_utc_rotation_checkpoints_then_handoffs_whole_shard_and_carries_watermark(
    tmp_path,
):
    repository = _repository(
        tmp_path, datetime(2026, 8, 6, 23, 59, tzinfo=timezone.utc)
    )
    _insert_trade_sweep_fixture(repository, 1_786_000_000)

    archive = repository.rotate_if_utc_day_changed(
        datetime(2026, 8, 7, 0, 1, tzinfo=timezone.utc)
    )

    assert archive == repository.db_path.with_name("trades_sim_20260806.db")
    assert archive.is_file()
    assert repository.db_path.is_file()
    with sqlite3.connect(archive) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert (
            connection.execute("SELECT COUNT(*) FROM trade_tape_sweeps").fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT database_utc_date FROM collection_contracts"
            ).fetchone()[0]
            == "2026-08-06"
        )
    with sqlite3.connect(repository.db_path) as connection:
        contract = connection.execute(
            "SELECT database_utc_date, prior_trade_watermark_epoch "
            "FROM collection_contracts"
        ).fetchone()
        assert contract == ("2026-08-07", 1_786_000_000)
        assert (
            connection.execute("SELECT COUNT(*) FROM trade_tape_sweeps").fetchone()[0]
            == 0
        )
    assert repository.latest_trade_watermark() == 1_786_000_000


def test_rotation_never_overwrites_an_existing_dated_shard(tmp_path):
    repository = _repository(tmp_path, datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc))
    archive = repository.db_path.with_name("trades_sim_20260806.db")
    archive.write_bytes(b"existing immutable shard")

    with pytest.raises(FileExistsError, match="already exists"):
        repository.rotate_if_utc_day_changed(datetime(2026, 8, 7, tzinfo=timezone.utc))

    assert repository.db_path.is_file()
    assert archive.read_bytes() == b"existing immutable shard"


def test_rotation_refuses_busy_wal_then_preserves_committed_rows_on_retry(tmp_path):
    repository = _repository(tmp_path, datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc))
    reader = sqlite3.connect(repository.db_path)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM trade_tape_sweeps").fetchone()
    _insert_trade_sweep_fixture(repository, 1_786_000_001)

    with pytest.raises(RuntimeError, match="WAL checkpoint is incomplete"):
        repository.rotate_if_utc_day_changed(datetime(2026, 8, 7, tzinfo=timezone.utc))

    archive = repository.db_path.with_name("trades_sim_20260806.db")
    assert repository.db_path.is_file()
    assert not archive.exists()
    reader.rollback()
    reader.close()

    assert (
        repository.rotate_if_utc_day_changed(datetime(2026, 8, 7, tzinfo=timezone.utc))
        == archive
    )
    with sqlite3.connect(archive) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM trade_tape_sweeps").fetchone()[0]
            == 1
        )


def test_rotation_refuses_even_idle_reader_before_reusing_wal_namespace(tmp_path):
    repository = _repository(tmp_path, datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc))
    reader = sqlite3.connect(
        f"file:{repository.db_path}?mode=ro", uri=True, isolation_level=None
    )
    assert (
        reader.execute("SELECT database_utc_date FROM collection_contracts").fetchone()[
            0
        ]
        == "2026-08-06"
    )

    with pytest.raises(RuntimeError, match="WAL namespace"):
        repository.rotate_if_utc_day_changed(datetime(2026, 8, 7, tzinfo=timezone.utc))

    archive = repository.db_path.with_name("trades_sim_20260806.db")
    assert repository.db_path.exists()
    assert not archive.exists()
    reader.close()

    assert (
        repository.rotate_if_utc_day_changed(datetime(2026, 8, 7, tzinfo=timezone.utc))
        == archive
    )
    with sqlite3.connect(archive) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert (
            connection.execute(
                "SELECT database_utc_date FROM collection_contracts"
            ).fetchone()[0]
            == "2026-08-06"
        )


def test_rotation_recovers_same_inode_handoff_after_replace_failure(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path, datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc))
    _insert_trade_sweep_fixture(repository, 1_786_000_002)
    archive = repository.db_path.with_name("trades_sim_20260806.db")
    real_replace = os.replace
    failed = False

    def fail_active_install_once(source, destination):
        nonlocal failed
        if Path(destination) == repository.db_path and not failed:
            failed = True
            raise OSError("simulated crash before active install")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_active_install_once)
    with pytest.raises(OSError, match="simulated crash"):
        repository.rotate_if_utc_day_changed(datetime(2026, 8, 7, tzinfo=timezone.utc))

    assert archive.exists()
    assert os.path.samefile(repository.db_path, archive)

    assert (
        repository.rotate_if_utc_day_changed(datetime(2026, 8, 7, tzinfo=timezone.utc))
        == archive
    )
    assert not os.path.samefile(repository.db_path, archive)
    assert repository.latest_trade_watermark() == 1_786_000_002
    with sqlite3.connect(archive) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM trade_tape_sweeps").fetchone()[0]
            == 1
        )


def test_rotation_completed_before_directory_fsync_is_idempotent_on_retry(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path, datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc))
    _insert_trade_sweep_fixture(repository, 1_786_000_003)
    archive = repository.db_path.with_name("trades_sim_20260806.db")
    import polybot.db.repository as repository_module

    real_fsync_directory = repository_module._fsync_directory
    calls = 0

    def fail_second_directory_fsync(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated crash after active install")
        return real_fsync_directory(path)

    monkeypatch.setattr(
        repository_module, "_fsync_directory", fail_second_directory_fsync
    )
    with pytest.raises(OSError, match="after active install"):
        repository.rotate_if_utc_day_changed(datetime(2026, 8, 7, tzinfo=timezone.utc))

    assert archive.exists()
    assert repository.db_path.exists()
    assert not os.path.samefile(repository.db_path, archive)
    monkeypatch.setattr(repository_module, "_fsync_directory", real_fsync_directory)
    assert (
        repository.rotate_if_utc_day_changed(datetime(2026, 8, 7, tzinfo=timezone.utc))
        is None
    )
    assert repository.latest_trade_watermark() == 1_786_000_003


def test_missing_active_db_with_existing_archive_fails_closed(tmp_path):
    repository = _repository(tmp_path, datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc))
    archive = repository.db_path.with_name("trades_sim_20260805.db")
    archive.write_bytes(repository.db_path.read_bytes())
    repository.db_path.unlink()

    with pytest.raises(RuntimeError, match="active research DB is missing"):
        repository.rotate_if_utc_day_changed(datetime(2026, 8, 7, tzinfo=timezone.utc))


def test_rotation_carries_unfinished_trade_bootstrap_baseline(tmp_path):
    repository = ResearchRepository(
        tmp_path / "data" / "job" / "trades_sim.db",
        clock=lambda: datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
    )
    repository.initialize(prior_trade_bootstrap_start_epoch=1_785_900_000)

    repository.rotate_if_utc_day_changed(datetime(2026, 8, 7, tzinfo=timezone.utc))

    assert repository.latest_trade_watermark() is None
    assert repository.latest_trade_bootstrap_start() == 1_785_900_000


def test_same_utc_day_does_not_rotate(tmp_path):
    repository = _repository(tmp_path, datetime(2026, 8, 6, 1, 0, tzinfo=timezone.utc))

    assert (
        repository.rotate_if_utc_day_changed(
            datetime(2026, 8, 6, 23, 59, tzinfo=timezone.utc)
        )
        is None
    )
    assert list(repository.db_path.parent.glob("trades_sim_*.db")) == []


def test_health_requires_quick_check_wal_full_sync_profile_and_all_append_guards(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "polybot.db.repository.shutil.disk_usage",
        lambda _path: DiskUsage(1_000 * GIB, 100 * GIB, 900 * GIB),
    )
    repository = _repository(tmp_path, datetime(2026, 8, 6, tzinfo=timezone.utc))

    healthy = repository.health()
    assert healthy["healthy"] is True
    assert healthy["quick_check"] == "ok"
    assert healthy["journal_mode"].lower() == "wal"
    assert healthy["contract"] == "research-full-v1"

    with sqlite3.connect(repository.db_path) as connection:
        connection.execute("DROP TRIGGER market_sweeps_append_only_delete")
    degraded = repository.health()
    assert degraded["healthy"] is False


def test_same_utc_shard_rejects_cadence_or_contract_metadata_change(tmp_path):
    repository = ResearchRepository(
        tmp_path / "data" / "job" / "trades_sim.db",
        clock=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc),
    )
    repository.initialize(
        contract_metadata={"cadence_minutes": 15, "job_name": "research-job"}
    )

    with pytest.raises(RuntimeError, match="metadata changed within one UTC day"):
        repository.initialize(
            contract_metadata={"cadence_minutes": 10, "job_name": "research-job"}
        )


def test_same_utc_shard_allows_source_digest_cohort_change(tmp_path):
    repository = ResearchRepository(
        tmp_path / "data" / "job" / "trades_sim.db",
        clock=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc),
    )
    repository.initialize(
        contract_metadata={
            "cadence_minutes": 60,
            "job_name": "research-job",
            "strategy_source_digest": "a" * 64,
        }
    )

    repository.initialize(
        contract_metadata={
            "cadence_minutes": 60,
            "job_name": "research-job",
            "strategy_source_digest": "b" * 64,
        }
    )


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (DiskUsage(1_000 * GIB, 699 * GIB, 301 * GIB), "OK"),
        (DiskUsage(1_000 * GIB, 700 * GIB, 300 * GIB), "WARN"),
        (DiskUsage(1_000 * GIB, 800 * GIB, 200 * GIB), "STOP"),
        (DiskUsage(1_000 * GIB, 100 * GIB, 149 * GIB), "STOP"),
    ],
)
def test_disk_guard_warns_at_70_and_stops_at_80_or_below_150_gib(
    tmp_path, monkeypatch, usage, expected
):
    repository = _repository(tmp_path, datetime(2026, 8, 6, tzinfo=timezone.utc))
    monkeypatch.setattr("polybot.db.repository.shutil.disk_usage", lambda _path: usage)

    metric = repository.record_storage_metric(
        phase="preflight",
        storage=StorageConfig(
            min_free_gib=150,
            warn_used_ratio=0.70,
            stop_used_ratio=0.80,
        ),
        cadence_minutes=15,
        run_id="run",
        cycle_number=1,
    )

    assert metric["guard_state"] == expected
    assert metric["filesystem_free_bytes"] == usage.free
    assert metric["filesystem_used_ratio"] == usage.used / usage.total


def test_first_cycle_forecast_uses_current_logical_size_without_history(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "polybot.db.repository.shutil.disk_usage",
        lambda _path: DiskUsage(1_000 * GIB, 100 * GIB, 900 * GIB),
    )
    repository = _repository(tmp_path, datetime(2026, 8, 6, tzinfo=timezone.utc))

    metric = repository.inspect_storage(storage=StorageConfig(), cadence_minutes=15)

    assert metric["logical_bytes"] > 0
    assert metric["recent_growth_bytes_per_cycle"] == metric["logical_bytes"]
    assert metric["forecast_next_day_bytes"] == pytest.approx(
        metric["logical_bytes"] * 96
    )


def test_growth_forecast_uses_one_post_publish_sample_per_cycle(tmp_path, monkeypatch):
    repository = _repository(tmp_path, datetime(2026, 8, 6, tzinfo=timezone.utc))
    monkeypatch.setattr(
        "polybot.db.repository.shutil.disk_usage",
        lambda _path: DiskUsage(1_000 * GIB, 100 * GIB, 900 * GIB),
    )
    rows = [
        ("post-1", "post_publish", "2026-08-06T00:00:00+00:00", 1_000),
        ("pre-2", "preflight", "2026-08-06T00:01:00+00:00", 1_000),
        ("post-2", "post_publish", "2026-08-06T00:02:00+00:00", 2_000),
        ("pre-3", "preflight", "2026-08-06T00:03:00+00:00", 2_000),
    ]
    with sqlite3.connect(repository.db_path) as connection:
        connection.executemany(
            """
            INSERT INTO storage_metrics (
                storage_metric_id, phase, observed_at, db_bytes, wal_bytes,
                shm_bytes, logical_bytes, filesystem_total_bytes,
                filesystem_used_bytes, filesystem_free_bytes,
                filesystem_used_ratio, recent_growth_bytes_per_cycle,
                forecast_next_day_bytes, guard_state
            ) VALUES (?, ?, ?, 0, 0, 0, ?, ?, ?, ?, ?, 0, 0, 'OK')
            """,
            [
                (
                    metric_id,
                    phase,
                    observed_at,
                    logical_bytes,
                    1_000 * GIB,
                    100 * GIB,
                    900 * GIB,
                    0.1,
                )
                for metric_id, phase, observed_at, logical_bytes in rows
            ],
        )

    metric = repository.inspect_storage(
        storage=StorageConfig(
            min_free_gib=150,
            warn_used_ratio=0.70,
            stop_used_ratio=0.80,
        ),
        cadence_minutes=15,
    )

    expected_growth = (1_000 + max(0, metric["logical_bytes"] - 2_000)) / 2
    assert metric["recent_growth_bytes_per_cycle"] == pytest.approx(expected_growth)
    assert metric["forecast_next_day_bytes"] == pytest.approx(expected_growth * 96)


def test_export_manifest_checksums_active_and_closed_whole_shards(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "polybot.db.repository.shutil.disk_usage",
        lambda _path: DiskUsage(1_000 * GIB, 100 * GIB, 900 * GIB),
    )
    repository = _repository(tmp_path, datetime(2026, 8, 6, tzinfo=timezone.utc))
    repository.rotate_if_utc_day_changed(datetime(2026, 8, 7, tzinfo=timezone.utc))
    destination = tmp_path / "manifest.json"

    manifest = repository.export_manifest(destination)

    assert destination.is_file()
    assert manifest["schema"] == "golden-pomegranate-manifest-v2"
    assert manifest["data_contract"] == "research-full-v1"
    assert manifest["healthy"] is True
    assert manifest["health"]["healthy"] is True
    paths = {Path(item["path"]).name for item in manifest["files"]}
    assert {"trades_sim.db", "trades_sim_20260806.db"} <= paths
    for item in manifest["files"]:
        path = Path(item["path"])
        assert item["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert item["bytes"] == path.stat().st_size
        assert item["quick_check"] == "ok"
        assert item["schema_version"] >= 1
        assert item["data_contract"] == "research-full-v1"
        assert item["utc_contract"]["database_utc_date"]
        assert isinstance(item["table_row_counts"], dict)
        assert (
            item["append_only_trigger_count"]
            == item["expected_append_only_trigger_count"]
        )
        assert item["cadence_coverage"]["coverage_semantics"].startswith(
            "observed_start_bucket"
        )
        assert item["consistency"]["files_stable_during_export"] is True
    assert (
        json.loads(destination.read_text(encoding="utf-8"))["files"]
        == manifest["files"]
    )


def test_status_reports_run_terminal_component_runtime_and_observed_slot_gaps(
    tmp_path,
):
    repository = ResearchRepository(
        tmp_path / "data" / "job" / "trades_sim.db",
        clock=lambda: datetime(2026, 8, 6, 0, 30, tzinfo=timezone.utc),
    )
    repository.initialize(
        contract_metadata={"cadence_minutes": 15, "job_name": "research-job"}
    )
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(
            "INSERT INTO research_config_versions VALUES "
            "('cfg', 1, 'golden-pomegranate', 'sim', '{}', 'digest', 'commit', "
            "'2026-08-06T00:00:00+00:00')"
        )
        common = (
            "golden-pomegranate",
            "research-job",
            "sim",
            "archive_only",
            "cfg",
            "digest",
            "commit",
        )
        connection.executemany(
            "INSERT INTO research_run_events "
            "(event_id, run_id, event_type, event_at, strategy_name, job_name, "
            "mode, lifecycle_mode, config_hash, strategy_source_digest, git_commit, "
            "cycle_stats_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "event-1-start",
                    "run-1",
                    "STARTED",
                    "2026-08-06T00:01:00+00:00",
                    *common,
                    None,
                ),
                (
                    "event-1-end",
                    "run-1",
                    "SUCCEEDED",
                    "2026-08-06T00:02:00+00:00",
                    *common,
                    json.dumps({"runtime_seconds": 60.0, "storage": {"guard": "OK"}}),
                ),
                (
                    "event-2-start",
                    "run-2",
                    "STARTED",
                    "2026-08-06T00:30:00+00:00",
                    *common,
                    None,
                ),
            ],
        )
        connection.execute(
            "INSERT INTO source_component_runs "
            "(component_run_id, run_id, cycle_number, component, status, "
            "started_at, completed_at, requested_count, observed_count, error_count, "
            "possible_gap, details_json) VALUES "
            "('component-1', 'run-1', 1, 'gamma_census', 'SUCCESS', "
            "'2026-08-06T00:01:00+00:00', '2026-08-06T00:02:00+00:00', "
            "1, 1, 0, 0, '{}')"
        )

    status = repository.status(cadence_minutes=99)

    assert status["cadence_minutes"] == 15
    assert status["requested_config_cadence_minutes"] == 99
    assert status["research_runs"]["started"] == 2
    assert status["research_runs"]["terminal"] == 1
    assert status["research_runs"]["orphan"] == 1
    assert status["research_runs"]["latest"]["run_id"] == "run-2"
    assert status["runtime"]["runtime_seconds"] == 60.0
    assert status["recent_components"][0]["status"] == "SUCCESS"
    assert status["cadence_coverage"]["expected_slots"] == 3
    assert status["cadence_coverage"]["observed_slots"] == 2
    assert status["cadence_coverage"]["gap_slots"] == 1
