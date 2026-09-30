from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from test_remote_agent import invoke, make_freestyle_config, snapshot_identity_arguments

from daily_rsync import remote_agent
from daily_rsync.models import JobInventory, SyncPlan

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE configs (hash TEXT PRIMARY KEY, document TEXT NOT NULL);
CREATE TABLE runs (
 slot INTEGER PRIMARY KEY, run_key TEXT NOT NULL UNIQUE,
 started REAL NOT NULL, finished REAL, status TEXT NOT NULL,
 config_hash TEXT NOT NULL REFERENCES configs(hash), source_hash TEXT NOT NULL,
 git_commit TEXT, summary_json TEXT, frame BLOB, frame_sha256 TEXT, frame_raw_bytes INTEGER
);
CREATE INDEX running_runs ON runs(slot) WHERE status='RUNNING';
CREATE TABLE watch (event_id TEXT PRIMARY KEY, market_ids TEXT NOT NULL, next_due REAL NOT NULL);
CREATE INDEX watch_due ON watch(next_due);
"""


def make_apple_database(path: Path, *, job: str = "polybot-do", running: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.fromisoformat(path.stem + "-15T00:00:00+00:00").timestamp()
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(f"PRAGMA application_id={remote_agent.APPLE_COLLECTION_APPLICATION_ID}")
        connection.execute("PRAGMA user_version=1")
        connection.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [("job", job), ("format", "apple-filtered-frames-v1"), ("month", path.stem)],
        )
        connection.execute("INSERT INTO configs VALUES ('config', 'PRIVATE_CONFIG_SENTINEL')")
        rows = [(0, "jenkins:1", started, started + 2, "SUCCESS")]
        if running:
            rows.append((1, "jenkins:2", started + 60, None, "RUNNING"))
        connection.executemany(
            "INSERT INTO runs (slot,run_key,started,finished,status,config_hash,source_hash,"
            "summary_json,frame) VALUES (?,?,?,?,?,'config','source','PRIVATE_SUMMARY_SENTINEL',?)",
            [(*row, b"PRIVATE_FRAME_SENTINEL") for row in rows],
        )


def apple_workspace(tmp_path: Path, *, job: str = "polybot-do") -> tuple[Path, Path, Path]:
    home = tmp_path / ".jenkins"
    root = tmp_path / "golden-apple"
    workspace = root / job
    workspace.mkdir(parents=True)
    make_freestyle_config(
        home, job, "python -m apple.compact_collection", custom_workspace=workspace
    )
    return home, root, workspace


@pytest.mark.parametrize("job", sorted(remote_agent.APPLE_COLLECTION_JOBS))
def test_scan_month_overlap_and_exact_identity(tmp_path: Path, job: str) -> None:
    home, root, workspace = apple_workspace(tmp_path, job=job)
    for month in ("2026-08", "2026-09", "2026-10"):
        make_apple_database(workspace / "data/collection-v2" / (month + ".sqlite"), job=job)
    (workspace / "data/collection-v2/arbitrary.sqlite").write_text("not a database")
    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--workspace-root",
        str(root),
        "--job",
        job,
        "--archive-from-date",
        "2026-08-31",
        "--archive-to-date",
        "2026-09-01",
    )
    inventory = JobInventory.from_dict(payload["jobs"][0])
    assert inventory.current_strategy == "golden-apple"
    assert len(inventory.artifacts) == 2
    assert {item.database_month for item in inventory.artifacts} == {"2026-08", "2026-09"}
    for item in inventory.artifacts:
        assert item.kind == "database_sim" and item.canonical is True
        assert item.strategy == "golden-apple" and item.runtime_job == job and item.mode == "sim"
        assert item.data_contract == "apple-filtered-frames-v1"
        assert item.database_utc_date is None and item.archive_date is None
        assert item.observation_window["run_count"] == 2
        assert item.observation_window["finished_run_count"] == 1
        assert item.observation_window["unfinished_run_count"] == 1
        assert item.observation_window["calendar_month_complete"] is False
    assert "PRIVATE_" not in json.dumps(payload)
    plan = SyncPlan.create(
        source="test-host",
        jenkins_job=job,
        strategy="golden-apple",
        artifacts=list(inventory.artifacts),
        skipped_unchanged=0,
        include_safety_databases=False,
    )
    plans = tmp_path / "plans"
    plans.mkdir()
    restored = SyncPlan.read(plan.write(plans))
    assert restored.artifacts[0].database_month == "2026-08"
    assert restored.artifacts[0].observation_window == inventory.artifacts[0].observation_window


@pytest.mark.parametrize(
    "statement",
    [
        "PRAGMA application_id=1",
        "PRAGMA user_version=2",
        "UPDATE meta SET value='polybot-re' WHERE key='job'",
        "UPDATE meta SET value='2026-08' WHERE key='month'",
        "UPDATE meta SET value='unrelated' WHERE key='format'",
        "DROP TABLE configs",
        "DELETE FROM meta WHERE key='job'",
        "UPDATE runs SET started=0 WHERE slot=0",
        "UPDATE runs SET finished=started-1 WHERE slot=0",
        "UPDATE runs SET status='FAILED' WHERE slot=1",
        "UPDATE runs SET finished=started+1 WHERE slot=1",
        "UPDATE runs SET status='PRIVATE_STATUS_SENTINEL' WHERE slot=0",
    ],
)
def test_apple_identity_rejects_incompatible_or_corrupt_database(
    tmp_path: Path, statement: str
) -> None:
    path = tmp_path / "polybot-do/data/collection-v2/2026-09.sqlite"
    make_apple_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(statement)
    with pytest.raises(RuntimeError, match="Apple collection") as failure:
        remote_agent.apple_collection_record(path)
    assert "PRIVATE_" not in str(failure.value)


@pytest.mark.parametrize("state", ["empty", "finished", "running"])
def test_observation_window_reports_runs_without_claiming_calendar_coverage(
    tmp_path: Path, state: str
) -> None:
    path = tmp_path / "polybot-do/data/collection-v2/2026-09.sqlite"
    make_apple_database(path, running=state == "running")
    if state == "empty":
        with sqlite3.connect(path) as connection:
            connection.execute("DELETE FROM runs")
    record = remote_agent.apple_collection_record(path)
    window = record["observation_window"]
    assert record["accountless"] is True
    assert window["all_runs_finished"] is (state == "finished")
    assert window["calendar_month_complete"] is False
    assert window["coverage"] == "observed_runs_only"
    if state == "empty":
        assert window["first_started_at"] is None and window["last_finished_at"] is None
    else:
        assert window["first_started_at"] == "2026-09-15T00:00:00+00:00"
        assert window["last_finished_at"] == "2026-09-15T00:00:02+00:00"
    assert remote_agent.database_identity(path) == (
        "golden-apple",
        "polybot-do",
        "sim",
        "apple-filtered-frames-v1",
        None,
    )


def test_monthly_path_is_exact_and_disallows_symlink(tmp_path: Path) -> None:
    path = tmp_path / "polybot-do/data/collection-v2/2026-09.sqlite"
    make_apple_database(path)
    outside = tmp_path / "snapshot.db"
    shutil.copy2(path, outside)
    with pytest.raises(RuntimeError, match="monthly database path"):
        remote_agent.apple_collection_record(outside)
    copied = remote_agent.apple_collection_record(
        outside,
        expected_job="polybot-do",
        expected_month="2026-09",
        require_path=False,
    )
    assert copied == remote_agent.apple_collection_record(path)
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(RuntimeError, match="direct regular"):
        remote_agent.apple_collection_record(path)
    assert remote_agent.supported_database_name("2026-09.sqlite") is False


def test_snapshot_preserves_monthly_identity_and_reads_copied_observation_window(
    tmp_path: Path,
) -> None:
    home, root, workspace = apple_workspace(tmp_path)
    path = workspace / "data/collection-v2/2026-09.sqlite"
    make_apple_database(path)
    staging = tmp_path / "staging"
    identity = snapshot_identity_arguments(home, "polybot-do", workspace_root=root)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE runs SET finished=started+2,status='PARTIAL' WHERE slot=1")
    payload = invoke(
        "snapshot",
        "--jenkins-home",
        str(home),
        "--workspace-root",
        str(root),
        *identity,
        "--source",
        str(path),
        "--staging-root",
        str(staging),
        "--expected-data-contract",
        "apple-filtered-frames-v1",
    )
    assert Path(payload["snapshot"]).name == "snapshot.db"
    assert payload["apple_collection"] == remote_agent.apple_collection_record(path)
    assert payload["database_month"] == "2026-09"
    assert payload["observation_window"]["all_runs_finished"] is True
    assert payload["observation_window"]["partial_run_count"] == 1
    assert payload["data_contract"] == "apple-filtered-frames-v1"
    assert payload["database_utc_date"] is None
    assert "PRIVATE_" not in json.dumps(payload)


@pytest.mark.parametrize(
    "relative",
    [
        "data/other/2026-09.sqlite",
        "data/collection-v2/arbitrary.sqlite",
        "golden-apple/data/collection-v2/2026-09.sqlite",
    ],
)
def test_snapshot_does_not_expand_general_sqlite_allowlist(tmp_path: Path, relative: str) -> None:
    home, root, workspace = apple_workspace(tmp_path)
    good = workspace / "data/collection-v2/2026-09.sqlite"
    make_apple_database(good)
    identity = snapshot_identity_arguments(home, "polybot-do", workspace_root=root)
    bad = workspace / relative
    bad.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(good, bad)
    with pytest.raises(subprocess.CalledProcessError) as failure:
        invoke(
            "snapshot",
            "--jenkins-home",
            str(home),
            "--workspace-root",
            str(root),
            *identity,
            "--source",
            str(bad),
            "--staging-root",
            str(tmp_path / "staging"),
        )
    assert "outside an exact allowlisted job workspace" in failure.value.stderr


def test_snapshot_revalidates_meta_after_persisted_scan(tmp_path: Path) -> None:
    home, root, workspace = apple_workspace(tmp_path)
    path = workspace / "data/collection-v2/2026-09.sqlite"
    make_apple_database(path)
    identity = snapshot_identity_arguments(home, "polybot-do", workspace_root=root)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE meta SET value='polybot-re' WHERE key='job'")
    with pytest.raises(subprocess.CalledProcessError) as failure:
        invoke(
            "snapshot",
            "--jenkins-home",
            str(home),
            "--workspace-root",
            str(root),
            *identity,
            "--source",
            str(path),
            "--staging-root",
            str(tmp_path / "staging"),
        )
    assert "metadata identity mismatch" in failure.value.stderr
    assert not (tmp_path / "staging").exists()
