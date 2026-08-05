from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

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


def make_freestyle_config(home: Path, job: str, command: str) -> None:
    job_root = home / "jobs" / job
    job_root.mkdir(parents=True, exist_ok=True)
    (job_root / "config.xml").write_text(
        "<project><builders><hudson.tasks.Shell><command><![CDATA["
        + command
        + "]]></command></hudson.tasks.Shell></builders></project>",
        encoding="utf-8",
    )


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


def test_scan_prefers_current_config_after_clean_strategy_transition(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    build = home / "jobs" / "polybot-eagle" / "builds" / "100"
    build.mkdir(parents=True)
    (build / "log").write_text(
        "[RUN_AUDIT] 시작 strategy=golden-nectarine\nFinished: SUCCESS\n",
        encoding="utf-8",
    )
    (build / "build.xml").write_text(
        "<build><result>SUCCESS</result></build>", encoding="utf-8"
    )
    secret = "must-not-leak-private-key"
    make_freestyle_config(
        home,
        "polybot-eagle",
        f"export POLYMARKET_PRIVATE_KEY={secret}\ncd ./golden-blueberry\nuv run polybot run",
    )
    (home / "workspace" / "polybot-eagle").mkdir(parents=True)

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--job",
        "polybot-eagle",
        "--cutoff-epoch",
        "0",
    )
    job = payload["jobs"][0]

    assert job["current_strategy"] == "golden-blueberry"
    assert job["strategies"] == ["golden-blueberry", "golden-nectarine"]
    assert job["strategy_evidence"] == {
        "configured_candidates": ["golden-blueberry"],
        "configured_strategy": "golden-blueberry",
        "conflict": True,
        "current_source": "jenkins_config",
        "latest_build_number": 100,
        "latest_build_result": "SUCCESS",
        "latest_build_strategy": "golden-nectarine",
        "latest_database_strategy": None,
        "latest_successful_build": 100,
        "latest_successful_strategy": "golden-nectarine",
        "state": "PENDING_DEPLOYMENT",
    }
    assert secret not in json.dumps(payload)


@pytest.mark.parametrize(
    "command",
    (
        "cd golden-blueberry",
        "cd './golden-blueberry/'",
        'cd "$WORKSPACE/golden-blueberry"',
        "STRATEGY=golden-blueberry; cd \"$STRATEGY\"",
        "# cd golden-nectarine\ncd golden-blueberry # current strategy",
        "set -euo pipefail; builtin cd -- ./golden-blueberry && uv run polybot run",
    ),
)
def test_configured_strategy_parser_accepts_safe_static_cd_forms(command: str) -> None:
    assert remote_agent._shell_segments(command) == {"golden-blueberry"}


def test_unstructured_failed_build_cannot_override_structured_run_audit(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    job_root = home / "jobs" / "polybot-eagle"
    for number, result, log in (
        (100, "SUCCESS", "[RUN_AUDIT] 시작 strategy=golden-blueberry\nFinished: SUCCESS\n"),
        (101, "FAILURE", 'Commit message: "docs for golden-nectarine"\nFinished: FAILURE\n'),
    ):
        build = job_root / "builds" / str(number)
        build.mkdir(parents=True)
        (build / "log").write_text(log, encoding="utf-8")
        (build / "build.xml").write_text(
            f"<build><result>{result}</result></build>", encoding="utf-8"
        )
    (job_root / "nextBuildNumber").write_text("102\n", encoding="utf-8")
    (home / "workspace" / "polybot-eagle").mkdir(parents=True)

    payload = invoke("scan", "--jenkins-home", str(home), "--cutoff-epoch", "0")
    job = payload["jobs"][0]

    assert job["current_strategy"] == "golden-blueberry"
    assert job["strategy_evidence"]["latest_build_strategy"] == "golden-blueberry"
    assert job["strategy_evidence"]["latest_build_number"] == 100
    assert job["strategy_evidence"]["latest_successful_build"] == 100


def test_ambiguous_config_is_reported_without_guessing(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    make_freestyle_config(
        home,
        "polybot-eagle",
        "cd golden-blueberry\ncd golden-nectarine\n",
    )
    (home / "workspace" / "polybot-eagle").mkdir(parents=True)

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--job",
        "polybot-eagle",
        "--cutoff-epoch",
        "0",
    )
    evidence = payload["jobs"][0]["strategy_evidence"]

    assert evidence["configured_strategy"] is None
    assert evidence["configured_candidates"] == ["golden-blueberry", "golden-nectarine"]
    assert evidence["state"] == "AMBIGUOUS_CONFIG"
    assert evidence["conflict"] is True


def test_newer_safety_database_cannot_override_canonical_database_identity(
    tmp_path: Path,
) -> None:
    home = tmp_path / ".jenkins"
    job_root = home / "jobs" / "polybot-eagle"
    job_root.mkdir(parents=True)
    workspace = home / "workspace" / "polybot-eagle"
    canonical = workspace / "golden-blueberry" / "data" / "blueberry-live" / "trades.db"
    safety = workspace / "golden-nectarine" / "data" / "legacy" / "trades_backup.db"
    for database in (canonical, safety):
        database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database)
        connection.execute(
            "CREATE TABLE run_audits(strategy_name TEXT, job_name TEXT, mode TEXT, started_at TEXT)"
        )
        strategy = database.parents[2].name
        connection.execute(
            "INSERT INTO run_audits VALUES (?, ?, 'live', '2026-08-05T00:00:00Z')",
            (strategy, database.parent.name),
        )
        connection.commit()
        connection.close()
    os.utime(safety, (canonical.stat().st_mtime + 10, canonical.stat().st_mtime + 10))

    payload = invoke("scan", "--jenkins-home", str(home), "--cutoff-epoch", "0")
    evidence = payload["jobs"][0]["strategy_evidence"]

    assert evidence["latest_database_strategy"] == "golden-blueberry"
    assert payload["jobs"][0]["current_strategy"] == "golden-blueberry"


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
