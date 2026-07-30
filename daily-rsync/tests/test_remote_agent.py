from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from daily_rsync import remote_agent


def make_db(path: Path) -> None:
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE run_audits (
            strategy_name TEXT, job_name TEXT, mode TEXT, started_at TEXT
        );
        INSERT INTO run_audits VALUES (
            'golden-queen', 'queen-live-12h', 'live',
            '2026-07-29T00:00:00+00:00'
        );
        CREATE TABLE evidence(id INTEGER PRIMARY KEY, value TEXT);
        INSERT INTO evidence(value) VALUES ('ok');
        """
    )
    connection.commit()
    connection.close()


def invoke(*arguments: str) -> dict:
    process = subprocess.run(
        [sys.executable, str(Path(remote_agent.__file__)), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(process.stdout)


def test_scan_preserves_job_strategy_and_runtime_identity(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    build = home / "jobs" / "polybot-king" / "builds" / "725"
    build.mkdir(parents=True)
    (build / "log").write_text(
        "[RUN_AUDIT] 시작 strategy=golden-queen\nJob: queen-live-12h\nFinished: SUCCESS\n",
        encoding="utf-8",
    )
    (build / "build.xml").write_text("<build><result>SUCCESS</result></build>", encoding="utf-8")
    database = (
        home
        / "workspace"
        / "polybot-king"
        / "golden-queen"
        / "data"
        / "queen-live-12h"
        / "trades.db"
    )
    make_db(database)
    logs = database.parent / "logs"
    logs.mkdir()
    (logs / "20260729.log").write_text("cycle success\n", encoding="utf-8")

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--job",
        "polybot-king",
        "--cutoff-epoch",
        "0",
    )
    job = payload["jobs"][0]

    assert job["current_strategy"] == "golden-queen"
    assert job["strategies"] == ["golden-queen"]
    database_record = next(item for item in job["artifacts"] if item["kind"] == "database_live")
    assert database_record["runtime_job"] == "queen-live-12h"
    assert database_record["canonical"] is True


def test_job_list_uses_next_build_number_without_full_history(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    job_root = home / "jobs" / "polybot-king"
    latest = job_root / "builds" / "725"
    latest.mkdir(parents=True)
    (job_root / "nextBuildNumber").write_text("726\n", encoding="utf-8")
    (latest / "log").write_text(
        "[RUN_AUDIT] 시작 strategy=golden-queen\nFinished: SUCCESS\n",
        encoding="utf-8",
    )
    (latest / "build.xml").write_text("<build><result>SUCCESS</result></build>", encoding="utf-8")
    database = (
        home
        / "workspace"
        / "polybot-king"
        / "golden-queen"
        / "data"
        / "queen-live-12h"
        / "trades.db"
    )
    make_db(database)

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--cutoff-epoch",
        "0",
    )
    job = payload["jobs"][0]

    assert job["build_count"] == 725
    assert job["max_build"] == 725
    assert job["current_strategy"] == "golden-queen"
    assert job["artifacts"] == []


def test_snapshot_is_consistent_and_cleanup_is_scoped(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    database = (
        home
        / "workspace"
        / "polybot-king"
        / "golden-queen"
        / "data"
        / "queen-live-12h"
        / "trades.db"
    )
    make_db(database)
    staging = tmp_path / ".cache" / "daily-rsync"

    payload = invoke(
        "snapshot",
        "--jenkins-home",
        str(home),
        "--source",
        str(database),
        "--staging-root",
        str(staging),
    )
    snapshot = Path(payload["snapshot"])

    assert snapshot.is_file()
    assert payload["quick_check"] == ["ok"]
    assert payload["sha256"]
    invoke(
        "cleanup",
        "--staging-root",
        str(staging),
        "--path",
        str(snapshot.parent),
    )
    assert not snapshot.parent.exists()
