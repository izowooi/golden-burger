from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest

from daily_rsync import remote_agent


def make_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def make_research_db(path: Path) -> None:
    make_db(path)
    archive_day = remote_agent.research_archive_date(path)
    database_utc_date = archive_day.isoformat() if archive_day else "2026-08-06"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE collection_contracts("
            "contract_name TEXT PRIMARY KEY, database_utc_date TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO collection_contracts VALUES (?, ?)",
            ("research-full-v1", database_utc_date),
        )


def invoke(*arguments: str) -> dict:
    process = subprocess.run(
        [sys.executable, str(Path(remote_agent.__file__)), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(process.stdout)


def test_existing_files_is_limited_to_jenkins_console_paths(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    existing = home / "jobs" / "polybot-cat" / "builds" / "1" / "log"
    missing = home / "jobs" / "polybot-cat" / "builds" / "2" / "log"
    existing.parent.mkdir(parents=True)
    existing.write_text("console", encoding="utf-8")

    payload = invoke(
        "existing-files",
        "--jenkins-home",
        str(home),
        "--paths-json",
        json.dumps([str(existing), str(missing)]),
    )

    assert payload == {"existing": [str(existing)], "missing": [str(missing)]}


def test_existing_files_rejects_non_console_path(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    other = home / "secrets" / "master.key"
    other.parent.mkdir(parents=True)
    other.write_text("not-a-real-secret", encoding="utf-8")

    process = subprocess.run(
        [
            sys.executable,
            str(Path(remote_agent.__file__)),
            "existing-files",
            "--jenkins-home",
            str(home),
            "--paths-json",
            json.dumps([str(other)]),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode != 0
    assert "not a Jenkins build console log" in process.stderr


def snapshot_identity_arguments(
    home: Path,
    job: str,
    *,
    workspace_root: Path | None = None,
) -> list[str]:
    (home / "jobs" / job).mkdir(parents=True, exist_ok=True)
    arguments = [
        "scan",
        "--jenkins-home",
        str(home),
        "--job",
        job,
        "--cutoff-epoch",
        "0",
    ]
    if workspace_root is not None:
        arguments.extend(("--workspace-root", str(workspace_root)))
    inventory = invoke(*arguments)["jobs"][0]
    return [
        "--job",
        job,
        "--expected-workspace",
        inventory["workspace"],
        "--expected-identity",
        json.dumps(inventory["workspace_identity"], sort_keys=True),
    ]


def make_freestyle_config(
    home: Path,
    job: str,
    command: str,
    *,
    custom_workspace: Path | None = None,
) -> None:
    job_root = home / "jobs" / job
    job_root.mkdir(parents=True, exist_ok=True)
    custom = (
        f"<customWorkspace>{escape(str(custom_workspace))}</customWorkspace>"
        if custom_workspace
        else ""
    )
    (job_root / "config.xml").write_text(
        "<project>"
        + custom
        + "<builders><hudson.tasks.Shell><command><![CDATA["
        + command
        + "]]></command></hudson.tasks.Shell></builders></project>",
        encoding="utf-8",
    )


def write_workspace_marker(workspace: Path, job: str, **overrides: object) -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "job": job,
        "workspace": str(workspace),
    }
    payload.update(overrides)
    (workspace / ".daily-rsync-workspace.json").write_text(
        json.dumps(payload) + "\n",
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
    assert database_record["fingerprint"]


def test_scan_discovers_shadow_database_as_canonical_simulation(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    job = "polybot-shadow"
    (home / "jobs" / job).mkdir(parents=True)
    database = (
        home
        / "workspace"
        / job
        / "golden-blueberry"
        / "data"
        / "blueberry-shadow-research"
        / "shadow.db"
    )
    make_db(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE run_audits SET strategy_name = ?, job_name = ?, mode = ?",
            ("golden-blueberry", "blueberry-shadow-research", "sim"),
        )

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--job",
        job,
        "--cutoff-epoch",
        "0",
    )
    record = next(
        item
        for item in payload["jobs"][0]["artifacts"]
        if item["remote_path"].endswith("shadow.db")
    )

    assert record["kind"] == "database_sim"
    assert record["canonical"] is True
    assert record["mode"] == "sim"
    assert record["strategy"] == "golden-blueberry"
    assert record["runtime_job"] == "blueberry-shadow-research"


def test_scan_fingerprint_and_size_include_wal_only_changes(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    (home / "jobs" / "polybot-king").mkdir(parents=True)
    database = (
        home / "workspace" / "polybot-king" / "golden-queen" / "data" / "queen-live" / "trades.db"
    )
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE evidence(value TEXT)")
    connection.commit()
    first = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--job",
        "polybot-king",
        "--cutoff-epoch",
        "0",
    )["jobs"][0]["artifacts"][0]
    main_stat = database.stat()
    connection.execute("INSERT INTO evidence VALUES ('wal-only')")
    connection.commit()
    assert database.stat().st_size == main_stat.st_size
    second = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--job",
        "polybot-king",
        "--cutoff-epoch",
        "0",
    )["jobs"][0]["artifacts"][0]
    connection.close()

    assert first["fingerprint"] != second["fingerprint"]
    assert second["size_bytes"] > database.stat().st_size


def test_sqlite_fingerprint_excludes_volatile_shm_metadata(tmp_path: Path) -> None:
    database = tmp_path / "trades_sim.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE evidence(value TEXT)")
    connection.execute("INSERT INTO evidence VALUES ('wal')")
    connection.commit()
    first = remote_agent.sqlite_source_state(database)
    shm = Path(f"{database}-shm")
    assert shm.is_file()
    value = shm.stat()
    os.utime(shm, ns=(value.st_atime_ns, value.st_mtime_ns + 1_000_000))
    second = remote_agent.sqlite_source_state(database)
    connection.close()

    assert first["fingerprint"] == second["fingerprint"]
    assert all(member["suffix"] != "-shm" for member in second["members"])


def test_sqlite_source_state_retries_sidecar_toctou_until_two_reads_are_stable(
    monkeypatch,
) -> None:
    stable = {
        "fingerprint": "stable",
        "size_bytes": 10,
        "mtime_ns": 20,
        "members": [{"suffix": "main", "size_bytes": 10, "mtime_ns": 20, "inode": 1}],
    }
    responses = iter((FileNotFoundError("wal vanished"), stable, stable))

    def fake_state(_path):
        value = next(responses)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(remote_agent, "_sqlite_source_state_once", fake_state)

    assert remote_agent.sqlite_source_state("trades.db", attempts=3, delay_seconds=0) == stable


def test_sqlite_source_state_fails_when_composite_state_never_stabilizes(
    monkeypatch,
) -> None:
    counter = iter(range(3))

    def changing_state(_path):
        value = next(counter)
        return {
            "fingerprint": str(value),
            "size_bytes": value,
            "mtime_ns": value,
            "members": [
                {
                    "suffix": "main",
                    "size_bytes": value,
                    "mtime_ns": value,
                    "inode": 1,
                }
            ],
        }

    monkeypatch.setattr(remote_agent, "_sqlite_source_state_once", changing_state)

    with pytest.raises(RuntimeError, match="remained unstable"):
        remote_agent.sqlite_source_state("trades.db", attempts=3, delay_seconds=0)


def test_scan_uses_external_allowlisted_workspace_root(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    (home / "jobs" / "polybot-king").mkdir(parents=True)
    external_root = tmp_path / "external" / "jenkins" / "workspace"
    database = (
        external_root / "polybot-king" / "golden-queen" / "data" / "queen-live-12h" / "trades.db"
    )
    make_db(database)

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--workspace-root",
        str(external_root),
        "--job",
        "polybot-king",
        "--cutoff-epoch",
        "0",
    )

    assert payload["jobs"][0]["workspace"] == str(external_root / "polybot-king")
    assert payload["jobs"][0]["artifacts"][0]["remote_path"] == str(database)


def test_unique_workspace_marker_selects_external_root_when_default_also_exists(
    tmp_path: Path,
) -> None:
    home = tmp_path / ".jenkins"
    job = "polybot-pomegranate"
    make_freestyle_config(home, job, "cd golden-pomegranate")
    default_root = home / "workspace"
    external_root = tmp_path / "external" / "workspace"
    (default_root / job).mkdir(parents=True)
    selected = external_root / job
    database = selected / "golden-pomegranate" / "data" / "pomegranate-research" / "trades_sim.db"
    make_research_db(database)
    write_workspace_marker(selected, job)

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--workspace-root",
        str(default_root),
        "--workspace-root",
        str(external_root),
        "--job",
        job,
        "--cutoff-epoch",
        "0",
    )

    inventory = payload["jobs"][0]
    assert inventory["workspace"] == str(selected)
    assert inventory["workspace_identity"]["workspace_marker"]["name"] == (
        ".daily-rsync-workspace.json"
    )
    assert inventory["workspace_identity"]["root_st_dev"] == selected.parent.stat().st_dev


@pytest.mark.parametrize("case", ("none", "multiple", "invalid"))
def test_multiple_workspace_candidates_fail_closed_without_one_valid_marker(
    tmp_path: Path,
    case: str,
) -> None:
    home = tmp_path / ".jenkins"
    job = "polybot-pomegranate"
    make_freestyle_config(home, job, "cd golden-pomegranate")
    roots = (home / "workspace", tmp_path / "external" / "workspace")
    candidates = []
    for root in roots:
        candidate = root / job
        candidate.mkdir(parents=True)
        candidates.append(candidate)
    if case == "multiple":
        for candidate in candidates:
            write_workspace_marker(candidate, job)
    elif case == "invalid":
        write_workspace_marker(candidates[1], "wrong-job")

    process = subprocess.run(
        [
            sys.executable,
            str(Path(remote_agent.__file__)),
            "scan",
            "--jenkins-home",
            str(home),
            "--workspace-root",
            str(roots[0]),
            "--workspace-root",
            str(roots[1]),
            "--job",
            job,
            "--cutoff-epoch",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode != 0
    assert (
        "exactly one valid .daily-rsync-workspace.json" in process.stderr
        if case != "invalid"
        else "payload mismatch" in process.stderr
    )


def test_custom_workspace_selects_the_exact_allowlisted_job_path(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    default_workspace = home / "workspace" / "polybot-king"
    default_workspace.mkdir(parents=True)
    external_root = tmp_path / "external" / "workspace"
    custom_workspace = external_root / "polybot-king"
    database = custom_workspace / "golden-queen" / "data" / "default" / "trades.db"
    make_db(database)
    make_freestyle_config(
        home,
        "polybot-king",
        "cd golden-queen",
        custom_workspace=custom_workspace,
    )

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--workspace-root",
        str(home / "workspace"),
        "--workspace-root",
        str(external_root),
        "--job",
        "polybot-king",
        "--cutoff-epoch",
        "0",
    )

    assert payload["jobs"][0]["workspace"] == str(custom_workspace)
    assert any(item["remote_path"] == str(database) for item in payload["jobs"][0]["artifacts"])


@pytest.mark.parametrize("job_variable", ("${JOB_NAME}", "$JOB_NAME"))
def test_custom_workspace_expands_only_the_jenkins_job_name_variable(
    tmp_path: Path,
    job_variable: str,
) -> None:
    home = tmp_path / ".jenkins"
    job = "polybot-pomegranate"
    external_root = tmp_path / "external" / "workspace"
    database = (
        external_root
        / job
        / "golden-pomegranate"
        / "data"
        / "pomegranate-research"
        / "trades_sim.db"
    )
    make_db(database)
    make_freestyle_config(
        home,
        job,
        "cd golden-pomegranate",
        custom_workspace=Path(str(external_root / job_variable)),
    )

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--workspace-root",
        str(external_root),
        "--job",
        job,
        "--cutoff-epoch",
        "0",
    )

    assert payload["jobs"][0]["workspace"] == str(external_root / job)
    assert any(item["remote_path"] == str(database) for item in payload["jobs"][0]["artifacts"])


@pytest.mark.parametrize("case", ("unmounted", "outside", "symlink", "shared-root"))
def test_scan_rejects_unsafe_workspace_resolution(tmp_path: Path, case: str) -> None:
    home = tmp_path / ".jenkins"
    allowed_root = tmp_path / "external" / "workspace"
    job = "polybot-king"
    custom_workspace = None
    if case != "unmounted":
        allowed_root.mkdir(parents=True)
    if case == "outside":
        custom_workspace = tmp_path / "outside" / job
        custom_workspace.mkdir(parents=True)
    elif case == "symlink":
        outside = tmp_path / "outside" / job
        outside.mkdir(parents=True)
        (allowed_root / job).symlink_to(outside, target_is_directory=True)
    elif case == "shared-root":
        custom_workspace = allowed_root
    make_freestyle_config(
        home,
        job,
        "cd golden-queen",
        custom_workspace=custom_workspace,
    )

    process = subprocess.run(
        [
            sys.executable,
            str(Path(remote_agent.__file__)),
            "scan",
            "--jenkins-home",
            str(home),
            "--workspace-root",
            str(allowed_root),
            "--job",
            job,
            "--cutoff-epoch",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode != 0
    assert any(
        phrase in process.stderr
        for phrase in ("not mounted", "outside", "symlink", "exact allowlisted")
    )


def test_scan_rejects_symlinked_allowlisted_workspace_root(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    job = "polybot-pomegranate"
    make_freestyle_config(home, job, "cd golden-pomegranate")
    real_root = tmp_path / "external" / "real-workspace"
    (real_root / job).mkdir(parents=True)
    symlinked_root = tmp_path / "external" / "workspace"
    symlinked_root.symlink_to(real_root, target_is_directory=True)

    process = subprocess.run(
        [
            sys.executable,
            str(Path(remote_agent.__file__)),
            "scan",
            "--jenkins-home",
            str(home),
            "--workspace-root",
            str(symlinked_root),
            "--job",
            job,
            "--cutoff-epoch",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode != 0
    assert "workspace root must be a real directory, not a symlink" in process.stderr


def test_research_archive_has_sim_identity_and_is_not_canonical(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    (home / "jobs" / "polybot-king").mkdir(parents=True)
    runtime = home / "workspace" / "polybot-king" / "golden-queen" / "data" / "queen-research"
    active = runtime / "trades_sim.db"
    archive = runtime / "trades_sim_20260805.db"
    make_research_db(active)
    make_research_db(archive)

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--job",
        "polybot-king",
        "--cutoff-epoch",
        "0",
    )
    records = {Path(item["remote_path"]).name: item for item in payload["jobs"][0]["artifacts"]}

    assert records["trades_sim.db"]["kind"] == "database_sim"
    assert records["trades_sim.db"]["canonical"] is True
    assert records["trades_sim.db"]["mode"] == "sim"
    assert records["trades_sim.db"]["data_contract"] == "research-full-v1"
    assert records[archive.name]["kind"] == "database_research_archive"
    assert records[archive.name]["canonical"] is False
    assert records[archive.name]["archive_date"] == "2026-08-05"
    assert records[archive.name]["mode"] == "sim"
    assert records[archive.name]["data_contract"] == "research-full-v1"


def test_research_archive_scan_honors_utc_date_range(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    (home / "jobs" / "polybot-king").mkdir(parents=True)
    runtime = home / "workspace" / "polybot-king" / "golden-queen" / "data" / "queen-research"
    for day in ("20260804", "20260805", "20260806"):
        make_research_db(runtime / f"trades_sim_{day}.db")

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--job",
        "polybot-king",
        "--cutoff-epoch",
        "0",
        "--archive-from-date",
        "2026-08-05",
        "--archive-to-date",
        "2026-08-05",
    )
    archives = [
        item
        for item in payload["jobs"][0]["artifacts"]
        if item["kind"] == "database_research_archive"
    ]

    assert [Path(item["remote_path"]).name for item in archives] == ["trades_sim_20260805.db"]


def test_research_archive_scan_rejects_filename_contract_date_mismatch(
    tmp_path: Path,
) -> None:
    home = tmp_path / ".jenkins"
    job = "polybot-pomegranate"
    (home / "jobs" / job).mkdir(parents=True)
    archive = (
        home
        / "workspace"
        / job
        / "golden-pomegranate"
        / "data"
        / "research"
        / "trades_sim_20260805.db"
    )
    make_research_db(archive)
    with sqlite3.connect(archive) as connection:
        connection.execute(
            "UPDATE collection_contracts SET database_utc_date = ?",
            ("2026-08-04",),
        )

    process = subprocess.run(
        [
            sys.executable,
            str(Path(remote_agent.__file__)),
            "scan",
            "--jenkins-home",
            str(home),
            "--job",
            job,
            "--cutoff-epoch",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode != 0
    assert "filename date does not match" in process.stderr


def test_historical_archive_scan_does_not_include_mutable_active_shard(
    tmp_path: Path,
) -> None:
    home = tmp_path / ".jenkins"
    (home / "jobs" / "polybot-pomegranate").mkdir(parents=True)
    runtime = (
        home
        / "workspace"
        / "polybot-pomegranate"
        / "golden-pomegranate"
        / "data"
        / "pomegranate-research"
    )
    make_research_db(runtime / "trades_sim.db")
    make_research_db(runtime / "trades_sim_19990101.db")

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--job",
        "polybot-pomegranate",
        "--cutoff-epoch",
        "0",
        "--archive-from-date",
        "1999-01-01",
        "--archive-to-date",
        "1999-01-01",
    )
    database_names = {
        Path(item["remote_path"]).name
        for item in payload["jobs"][0]["artifacts"]
        if item["kind"].startswith("database")
    }

    assert database_names == {"trades_sim_19990101.db"}


def test_historical_archive_scan_excludes_pre_contract_pomegranate_database(
    tmp_path: Path,
) -> None:
    home = tmp_path / ".jenkins"
    (home / "jobs" / "golden-pomegranate").mkdir(parents=True)
    runtime = (
        home
        / "workspace"
        / "golden-pomegranate"
        / "golden-pomegranate"
        / "data"
        / "pomegranate-local"
    )
    database = runtime / "trades_sim.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('pre-contract')")

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--job",
        "golden-pomegranate",
        "--cutoff-epoch",
        "0",
        "--archive-from-date",
        "1999-01-01",
        "--archive-to-date",
        "1999-01-01",
    )
    database_names = {
        Path(item["remote_path"]).name
        for item in payload["jobs"][0]["artifacts"]
        if item["kind"].startswith("database")
    }

    assert database_names == set()


def test_historical_archive_scan_keeps_ordinary_cumulative_simulation_database(
    tmp_path: Path,
) -> None:
    home = tmp_path / ".jenkins"
    (home / "jobs" / "polybot-queen").mkdir(parents=True)
    runtime = home / "workspace" / "polybot-queen" / "golden-queen" / "data" / "queen-sim"
    make_db(runtime / "trades_sim.db")

    payload = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--job",
        "polybot-queen",
        "--cutoff-epoch",
        "0",
        "--archive-from-date",
        "1999-01-01",
        "--archive-to-date",
        "1999-01-01",
    )
    database_names = {
        Path(item["remote_path"]).name
        for item in payload["jobs"][0]["artifacts"]
        if item["kind"].startswith("database")
    }

    assert database_names == {"trades_sim.db"}


def test_scan_prefers_current_config_after_clean_strategy_transition(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    build = home / "jobs" / "polybot-eagle" / "builds" / "100"
    build.mkdir(parents=True)
    (build / "log").write_text(
        "[RUN_AUDIT] 시작 strategy=golden-nectarine\nFinished: SUCCESS\n",
        encoding="utf-8",
    )
    (build / "build.xml").write_text("<build><result>SUCCESS</result></build>", encoding="utf-8")
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
        'STRATEGY=golden-blueberry; cd "$STRATEGY"',
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
        *snapshot_identity_arguments(home, "polybot-king"),
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


def test_snapshot_normalizes_wal_to_one_self_contained_file(tmp_path: Path) -> None:
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
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("INSERT INTO evidence(value) VALUES ('wal evidence')")
        connection.commit()
        source_bytes = database.read_bytes()
        source_mtime_ns = database.stat().st_mtime_ns
        staging = tmp_path / ".cache" / "daily-rsync"

        payload = invoke(
            "snapshot",
            "--jenkins-home",
            str(home),
            *snapshot_identity_arguments(home, "polybot-king"),
            "--source",
            str(database),
            "--staging-root",
            str(staging),
        )
        assert database.read_bytes() == source_bytes
        assert database.stat().st_mtime_ns == source_mtime_ns
    finally:
        connection.close()
    snapshot = Path(payload["snapshot"])

    assert payload["snapshot_journal_mode"] == "delete"
    assert sorted(path.name for path in snapshot.parent.iterdir()) == [
        "manifest.json",
        "snapshot.db",
    ]
    with sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 2


def test_snapshot_accepts_canonical_shadow_database(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    job = "polybot-shadow"
    database = (
        home
        / "workspace"
        / job
        / "golden-blueberry"
        / "data"
        / "blueberry-shadow-research"
        / "shadow.db"
    )
    make_db(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE run_audits SET strategy_name = ?, job_name = ?, mode = ?",
            ("golden-blueberry", "blueberry-shadow-research", "sim"),
        )
    staging = tmp_path / ".cache" / "daily-rsync"

    payload = invoke(
        "snapshot",
        "--jenkins-home",
        str(home),
        *snapshot_identity_arguments(home, job),
        "--source",
        str(database),
        "--staging-root",
        str(staging),
    )

    assert Path(payload["snapshot"]).is_file()
    assert payload["quick_check"] == ["ok"]
    assert payload["data_contract"] is None


def test_research_snapshot_requires_collector_lock(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    job = "polybot-pomegranate"
    database = (
        home
        / "workspace"
        / job
        / "golden-pomegranate"
        / "data"
        / "pomegranate-research"
        / "trades_sim.db"
    )
    make_research_db(database)
    staging = tmp_path / ".cache" / "daily-rsync"
    identity_arguments = snapshot_identity_arguments(home, job)
    command = [
        sys.executable,
        str(Path(remote_agent.__file__)),
        "snapshot",
        "--jenkins-home",
        str(home),
        *identity_arguments,
        "--source",
        str(database),
        "--staging-root",
        str(staging),
        "--expected-data-contract",
        "research-full-v1",
        "--expected-database-utc-date",
        "2026-08-06",
    ]

    missing_lock = subprocess.run(command, capture_output=True, text=True, check=False)

    assert missing_lock.returncode != 0
    assert "collector snapshot lock is required" in missing_lock.stderr

    (database.parent / ".pomegranate.lock").touch()
    accepted = subprocess.run(command, capture_output=True, text=True, check=False)

    assert accepted.returncode == 0, accepted.stderr
    payload = json.loads(accepted.stdout)
    assert payload["data_contract"] == "research-full-v1"
    invoke(
        "cleanup",
        "--staging-root",
        str(staging),
        "--path",
        str(Path(payload["snapshot"]).parent),
    )


def test_research_archive_completed_at_uses_publication_ctime(tmp_path: Path) -> None:
    archive = tmp_path / "trades_sim_20260805.db"
    make_research_db(archive)
    old_timestamp_ns = 946_684_800 * 1_000_000_000
    os.utime(archive, ns=(old_timestamp_ns, old_timestamp_ns))

    record = remote_agent.stat_record(
        archive,
        "database_research_archive",
        "polybot-pomegranate",
        strategy="golden-pomegranate",
        runtime_job="pomegranate-research",
        canonical=False,
        archive_date=remote_agent.research_archive_date(archive),
        mode="sim",
        data_contract="research-full-v1",
        database_utc_date="2026-08-05",
    )

    assert record["mtime_ns"] == old_timestamp_ns
    assert record["completed_at"] > remote_agent.iso_from_ns(old_timestamp_ns)


@pytest.mark.parametrize(
    "runtime_job",
    (
        "coconut-major-sports-lifecycle-5m-v2",
        "coconut-major-sports-lifecycle-5m-v3",
        "coconut-major-sports-lifecycle-5m-v4",
    ),
)
def test_coconut_research_snapshot_uses_coconut_cycle_lock(
    tmp_path: Path, runtime_job: str
) -> None:
    home = tmp_path / ".jenkins"
    job = "polybot-gold"
    database = (
        home
        / "workspace"
        / job
        / "golden-coconut"
        / "data"
        / runtime_job
        / "trades_sim.db"
    )
    make_research_db(database)
    staging = tmp_path / ".cache" / "daily-rsync"
    command = [
        sys.executable,
        str(Path(remote_agent.__file__)),
        "snapshot",
        "--jenkins-home",
        str(home),
        *snapshot_identity_arguments(home, job),
        "--source",
        str(database),
        "--staging-root",
        str(staging),
        "--expected-data-contract",
        "research-full-v1",
        "--expected-database-utc-date",
        "2026-08-06",
    ]

    (database.parent / ".pomegranate.lock").touch()
    rejected = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rejected.returncode != 0
    assert ".coconut-cycle.lock" in rejected.stderr

    (database.parent / ".coconut-cycle.lock").touch()
    accepted = subprocess.run(command, capture_output=True, text=True, check=False)
    assert accepted.returncode == 0, accepted.stderr
    payload = json.loads(accepted.stdout)
    assert payload["data_contract"] == "research-full-v1"
    invoke(
        "cleanup",
        "--staging-root",
        str(staging),
        "--path",
        str(Path(payload["snapshot"]).parent),
    )


def test_snapshot_accepts_external_allowlisted_workspace_and_rejects_outside(
    tmp_path: Path,
) -> None:
    home = tmp_path / ".jenkins"
    home.mkdir()
    external_root = tmp_path / "external" / "workspace"
    database = (
        external_root
        / "polybot-king"
        / "golden-queen"
        / "data"
        / "queen-research"
        / "trades_sim_20260805.db"
    )
    make_research_db(database)
    staging = tmp_path / ".cache" / "daily-rsync"
    identity_arguments = snapshot_identity_arguments(
        home,
        "polybot-king",
        workspace_root=external_root,
    )

    payload = invoke(
        "snapshot",
        "--jenkins-home",
        str(home),
        "--workspace-root",
        str(external_root),
        *identity_arguments,
        "--source",
        str(database),
        "--staging-root",
        str(staging),
    )

    assert payload["quick_check"] == ["ok"]
    outside = tmp_path / "outside" / "trades.db"
    make_db(outside)
    process = subprocess.run(
        [
            sys.executable,
            str(Path(remote_agent.__file__)),
            "snapshot",
            "--jenkins-home",
            str(home),
            "--workspace-root",
            str(external_root),
            *identity_arguments,
            "--source",
            str(outside),
            "--staging-root",
            str(staging),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode != 0
    assert "outside an exact allowlisted job workspace" in process.stderr


def test_persisted_plan_workspace_validation_rejects_new_or_ambiguous_root(
    tmp_path: Path,
) -> None:
    home = tmp_path / ".jenkins"
    job = "polybot-king"
    root_a = tmp_path / "a" / "workspace"
    root_b = tmp_path / "b" / "workspace"
    make_freestyle_config(home, job, "cd golden-queen")
    (root_a / job).mkdir(parents=True)
    inventory = invoke(
        "scan",
        "--jenkins-home",
        str(home),
        "--workspace-root",
        str(root_a),
        "--job",
        job,
        "--cutoff-epoch",
        "0",
    )["jobs"][0]
    expected_identity = json.dumps(inventory["workspace_identity"], sort_keys=True)

    accepted = invoke(
        "validate-workspace",
        "--jenkins-home",
        str(home),
        "--workspace-root",
        str(root_a),
        "--job",
        job,
        "--expected-workspace",
        str(root_a / job),
        "--expected-identity",
        expected_identity,
    )
    assert accepted["validated"] is True

    changed_identity = dict(inventory["workspace_identity"])
    changed_identity["root_st_dev"] = int(changed_identity["root_st_dev"]) + 1
    changed_mount = subprocess.run(
        [
            sys.executable,
            str(Path(remote_agent.__file__)),
            "validate-workspace",
            "--jenkins-home",
            str(home),
            "--workspace-root",
            str(root_a),
            "--job",
            job,
            "--expected-workspace",
            str(root_a / job),
            "--expected-identity",
            json.dumps(changed_identity, sort_keys=True),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert changed_mount.returncode != 0
    assert "mount identity changed" in changed_mount.stderr

    (root_b / job).mkdir(parents=True)
    process = subprocess.run(
        [
            sys.executable,
            str(Path(remote_agent.__file__)),
            "validate-workspace",
            "--jenkins-home",
            str(home),
            "--workspace-root",
            str(root_a),
            "--workspace-root",
            str(root_b),
            "--job",
            job,
            "--expected-workspace",
            str(root_a / job),
            "--expected-identity",
            expected_identity,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode != 0
    assert "ambiguous" in process.stderr


def test_snapshot_failure_removes_remote_staging_directory(tmp_path: Path) -> None:
    home = tmp_path / ".jenkins"
    job = "polybot-king"
    (home / "jobs" / job).mkdir(parents=True)
    database = home / "workspace" / job / "golden-queen" / "data" / "queen-live" / "trades.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not-a-sqlite-database")
    staging = tmp_path / "staging"
    identity_arguments = snapshot_identity_arguments(home, job)
    process = subprocess.run(
        [
            sys.executable,
            str(Path(remote_agent.__file__)),
            "snapshot",
            "--jenkins-home",
            str(home),
            *identity_arguments,
            "--source",
            str(database),
            "--staging-root",
            str(staging),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode != 0
    assert staging.is_dir()
    assert list(staging.iterdir()) == []


def test_snapshot_retries_one_transient_cantopen_and_records_it(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    source = tmp_path / "external" / "trades_sim.db"
    make_db(source)
    staging = tmp_path / "staging"
    real_connect = remote_agent.sqlite3.connect
    source_uri = f"file:{source.resolve()}?mode=ro"
    calls = 0

    def flaky_connect(database, *args, **kwargs):
        nonlocal calls
        if str(database) == source_uri:
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("unable to open database file")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(remote_agent.sqlite3, "connect", flaky_connect)
    remote_agent._snapshot_database_source(
        SimpleNamespace(
            staging_root=str(staging),
            expected_data_contract=None,
            expected_database_utc_date=None,
        ),
        source.resolve(),
    )

    payload = json.loads(capsys.readouterr().out)
    assert calls == 2
    assert payload["quick_check"] == ["ok"]
    assert payload["snapshot_open_retry_count"] == 1
    assert payload["snapshot_source_open_mode"] == "read_only_locked"
    assert Path(payload["snapshot"]).is_file()


def test_snapshot_uses_stable_immutable_main_when_read_lock_is_unsupported(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    source = tmp_path / "external" / "trades_sim.db"
    make_db(source)
    staging = tmp_path / "staging"
    real_connect = remote_agent.sqlite3.connect
    source_uri = f"file:{source.resolve()}?mode=ro"
    locked_calls = 0

    def filesystem_without_sqlite_read_locks(database, *args, **kwargs):
        nonlocal locked_calls
        if str(database) == source_uri:
            locked_calls += 1
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(
        remote_agent.sqlite3,
        "connect",
        filesystem_without_sqlite_read_locks,
    )
    remote_agent._snapshot_database_source(
        SimpleNamespace(
            staging_root=str(staging),
            expected_data_contract=None,
            expected_database_utc_date=None,
        ),
        source.resolve(),
    )

    payload = json.loads(capsys.readouterr().out)
    assert locked_calls == 2
    assert payload["quick_check"] == ["ok"]
    assert payload["source_fingerprint_before"] == payload["source_fingerprint_after"]
    assert payload["snapshot_open_retry_count"] == 2
    assert payload["snapshot_source_open_mode"] == "immutable_stable_main"
