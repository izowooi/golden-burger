from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import replace as replace_dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from daily_rsync.models import JobInventory, RemoteArtifact, SyncPlan
from daily_rsync.sync import SyncService


def make_research_db(path: Path, database_day: date) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE evidence(value TEXT);
            INSERT INTO evidence VALUES ('ok');
            CREATE TABLE collection_contracts(
                contract_name TEXT PRIMARY KEY,
                database_utc_date TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO collection_contracts VALUES (?, ?)",
            ("research-full-v1", database_day.isoformat()),
        )


def test_default_runtime_is_nested_below_real_jenkins_job(app_config) -> None:
    service = SyncService(app_config)
    artifact = RemoteArtifact(
        kind="database_live",
        remote_path="/jenkins/workspace/polybot-yellow/golden-cherry/data/default/trades.db",
        size_bytes=1,
        mtime_ns=2,
        jenkins_job="polybot-yellow",
        strategy="golden-cherry",
        runtime_job="default",
    )

    path = service.local_path(artifact)

    assert "jobs/polybot-yellow/strategies/golden-cherry/runtime/default" in str(path)
    assert path.name == "trades.db"


def test_shadow_database_uses_canonical_latest_path(app_config) -> None:
    service = SyncService(app_config)
    artifact = RemoteArtifact(
        kind="database_sim",
        remote_path=(
            "/jenkins/workspace/polybot-shadow/golden-blueberry/"
            "data/blueberry-shadow-research/shadow.db"
        ),
        size_bytes=1,
        mtime_ns=2,
        jenkins_job="polybot-shadow",
        strategy="golden-blueberry",
        runtime_job="blueberry-shadow-research",
        mode="sim",
    )

    path = service.local_path(artifact)

    assert path.as_posix().endswith(
        "/jobs/polybot-shadow/strategies/golden-blueberry/"
        "runtime/blueberry-shadow-research/databases/latest/shadow.db"
    )


def test_console_logs_are_sharded_by_build_number(app_config) -> None:
    service = SyncService(app_config)
    artifact = RemoteArtifact(
        kind="jenkins_console",
        remote_path="/jenkins/jobs/polybot-king/builds/49268/log",
        size_bytes=1,
        mtime_ns=2,
        jenkins_job="polybot-king",
        strategy="golden-queen",
        build_number=49268,
    )

    assert service.local_path(artifact).as_posix().endswith("/builds/049000/49268.log.gz")


def test_plan_requires_explicit_strategy_while_config_deployment_is_pending(
    app_config, monkeypatch
) -> None:
    service = SyncService(app_config)
    inventory = JobInventory(
        name="polybot-eagle",
        workspace="/jenkins/workspace/polybot-eagle",
        workspace_identity={
            "root_path": "/jenkins/workspace",
            "root_realpath": "/jenkins/workspace",
            "root_st_dev": 42,
            "workspace_st_dev": 42,
            "selection_contract": "allowlisted-root-job-v1",
        },
        build_count=101,
        min_build=1,
        max_build=101,
        current_strategy="golden-blueberry",
        strategies=("golden-blueberry", "golden-nectarine"),
        artifacts=(),
        remote_free_bytes=10**9,
        strategy_evidence={"state": "PENDING_DEPLOYMENT", "conflict": True},
    )
    monkeypatch.setattr(service, "scan", lambda **_kwargs: [inventory])

    with pytest.raises(ValueError, match="PENDING_DEPLOYMENT"):
        service.create_plan(job="polybot-eagle")

    plan = service.create_plan(job="polybot-eagle", strategy="golden-blueberry")
    assert plan.strategy == "golden-blueberry"


def test_plan_requires_scanned_workspace_mount_identity(app_config, monkeypatch) -> None:
    service = SyncService(app_config)
    inventory = JobInventory(
        name="polybot-queen",
        workspace="/jenkins/workspace/polybot-queen",
        build_count=0,
        min_build=None,
        max_build=None,
        current_strategy="golden-queen",
        strategies=("golden-queen",),
        artifacts=(),
        remote_free_bytes=10**9,
    )
    monkeypatch.setattr(service, "scan", lambda **_kwargs: [inventory])

    with pytest.raises(RuntimeError, match="requires a validated workspace mount identity"):
        service.create_plan(job="polybot-queen", strategy="golden-queen")


def test_changed_immutable_archive_fails_closed_and_preserves_local_evidence(
    app_config, monkeypatch
) -> None:
    service = SyncService(app_config)
    old = RemoteArtifact(
        kind="database_research_archive",
        remote_path=(
            "/external/workspace/polybot-pomegranate/golden-pomegranate/"
            "data/research/trades_sim_20260805.db"
        ),
        size_bytes=10,
        mtime_ns=20,
        fingerprint="immutable-v1",
        jenkins_job="polybot-pomegranate",
        source=app_config.ssh_host,
        strategy="golden-pomegranate",
        runtime_job="research",
        canonical=False,
        archive_date="2026-08-05",
        mode="sim",
        data_contract="research-full-v1",
        database_utc_date="2026-08-05",
    )
    local = service.local_path(old)
    local.parent.mkdir(parents=True)
    local.write_bytes(b"preserve-this-evidence")
    service.catalog.upsert_artifact(
        old,
        source=app_config.ssh_host,
        local_path=local,
        local_sha256=hashlib.sha256(local.read_bytes()).hexdigest(),
        metadata={
            "archive_date": "2026-08-05",
            "data_contract": "research-full-v1",
            "database_utc_date": "2026-08-05",
        },
    )
    changed = RemoteArtifact(**{**old.__dict__, "size_bytes": 11, "fingerprint": "v2"})
    inventory = JobInventory(
        name="polybot-pomegranate",
        workspace="/external/workspace/polybot-pomegranate",
        build_count=0,
        min_build=None,
        max_build=None,
        current_strategy="golden-pomegranate",
        strategies=("golden-pomegranate",),
        artifacts=(changed,),
        remote_free_bytes=10**12,
    )
    monkeypatch.setattr(service, "scan", lambda **_kwargs: [inventory])

    with pytest.raises(RuntimeError, match="immutable research archive changed"):
        service.create_plan(job="polybot-pomegranate", strategy="golden-pomegranate")

    assert local.read_bytes() == b"preserve-this-evidence"
    assert service.catalog.get_artifact(old.source_key)["status"] == "IMMUTABLE_CONFLICT"
    assert (
        service.verify(job="polybot-pomegranate", strategy="golden-pomegranate")["status"]
        == "FAILED"
    )
    conflicts = service.catalog.list_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0]["conflict_type"] == "IMMUTABLE_REMOTE_CHANGED"


def test_open_conflict_without_artifact_row_blocks_verify_and_future_plan(
    app_config, monkeypatch
) -> None:
    service = SyncService(app_config)
    conflicted = RemoteArtifact(
        kind="database_research_archive",
        remote_path=(
            "/external/workspace/polybot-pomegranate/golden-pomegranate/"
            "data/research/trades_sim_20260805.db"
        ),
        size_bytes=10,
        mtime_ns=20,
        fingerprint="conflicted-v1",
        jenkins_job="polybot-pomegranate",
        source=app_config.ssh_host,
        strategy="golden-pomegranate",
        runtime_job="research",
        canonical=False,
        archive_date="2026-08-05",
        mode="sim",
    )
    service.catalog.record_conflict(
        conflict_type="IMMUTABLE_LOCAL_EXISTS",
        source=app_config.ssh_host,
        artifact=conflicted,
        local_path=service.local_path(conflicted),
        existing=None,
        details={"test": True},
    )
    inventory = JobInventory(
        name="polybot-pomegranate",
        workspace="/external/workspace/polybot-pomegranate",
        workspace_identity={
            "root_path": "/external/workspace",
            "root_realpath": "/external/workspace",
            "root_st_dev": 42,
            "workspace_st_dev": 42,
            "selection_contract": "allowlisted-root-job-v1",
        },
        build_count=0,
        min_build=None,
        max_build=None,
        current_strategy="golden-pomegranate",
        strategies=("golden-pomegranate",),
        artifacts=(),
        remote_free_bytes=10**12,
    )
    monkeypatch.setattr(service, "scan", lambda **_kwargs: [inventory])

    verification = service.verify(
        job="polybot-pomegranate",
        strategy="golden-pomegranate",
    )
    located = service.locate_evidence(
        job="polybot-pomegranate",
        strategy="golden-pomegranate",
        from_date=date(2026, 8, 5),
        to_date=date(2026, 8, 5),
    )
    assert verification["status"] == "FAILED"
    assert verification["open_artifact_conflicts"][0]["source_key"] == (conflicted.source_key)
    assert located["matches"][0]["analysis_ready"] is False
    assert located["matches"][0]["archive_coverage"]["conflicted_dates"] == ["2026-08-05"]
    with pytest.raises(RuntimeError, match="unresolved artifact conflict"):
        service.create_plan(
            job="polybot-pomegranate",
            strategy="golden-pomegranate",
        )


def test_workspace_root_move_cannot_overwrite_same_local_destination(
    app_config, monkeypatch
) -> None:
    service = SyncService(app_config)
    base = {
        "kind": "database_live",
        "size_bytes": 10,
        "mtime_ns": 20,
        "fingerprint": "same-state",
        "jenkins_job": "polybot-king",
        "source": app_config.ssh_host,
        "strategy": "golden-queen",
        "runtime_job": "queen-live",
    }
    old = RemoteArtifact(
        remote_path=("/old/workspace/polybot-king/golden-queen/data/queen-live/trades.db"),
        **base,
    )
    local = service.local_path(old)
    local.parent.mkdir(parents=True)
    local.write_bytes(b"old-root-evidence")
    service.catalog.upsert_artifact(
        old,
        source=app_config.ssh_host,
        local_path=local,
        local_sha256=hashlib.sha256(local.read_bytes()).hexdigest(),
    )
    moved = RemoteArtifact(
        remote_path=("/new/workspace/polybot-king/golden-queen/data/queen-live/trades.db"),
        **base,
    )
    inventory = JobInventory(
        name="polybot-king",
        workspace="/new/workspace/polybot-king",
        build_count=0,
        min_build=None,
        max_build=None,
        current_strategy="golden-queen",
        strategies=("golden-queen",),
        artifacts=(moved,),
        remote_free_bytes=10**12,
    )
    monkeypatch.setattr(service, "scan", lambda **_kwargs: [inventory])

    with pytest.raises(RuntimeError, match="collides with"):
        service.create_plan(job="polybot-king", strategy="golden-queen")

    assert local.read_bytes() == b"old-root-evidence"
    assert service.catalog.get_artifact(old.source_key)["status"] == "PROVENANCE_CONFLICT"
    assert service.verify(job="polybot-king", strategy="golden-queen")["status"] == "FAILED"
    assert service.catalog.list_conflicts()[0]["conflict_type"] == "SOURCE_PATH_COLLISION"


def test_execute_revalidates_workspace_from_persisted_plan(app_config) -> None:
    service = SyncService(app_config)
    calls = []
    identity = {
        "root_path": "/new/workspace",
        "root_realpath": "/new/workspace",
        "root_st_dev": 42,
        "workspace_st_dev": 42,
        "selection_contract": "allowlisted-root-job-v1",
    }

    class FakeRemote:
        def validate_workspace(
            self,
            *,
            job: str,
            expected_workspace: str,
            expected_identity: dict,
        ):
            calls.append((job, expected_workspace, expected_identity))
            return {"validated": True}

    service.remote = FakeRemote()
    plan = SyncPlan.create(
        source=app_config.ssh_host,
        jenkins_job="polybot-king",
        strategy="golden-queen",
        workspace="/new/workspace/polybot-king",
        workspace_identity=identity,
        artifacts=[],
        skipped_unchanged=0,
        include_safety_databases=False,
    )

    result = service.execute(plan)

    assert result.status == "SUCCESS", result.errors
    assert calls == [("polybot-king", "/new/workspace/polybot-king", identity)]


def test_legacy_plan_without_workspace_identity_fails_and_records_attempt(
    app_config,
) -> None:
    service = SyncService(app_config)
    plan = SyncPlan.create(
        source=app_config.ssh_host,
        jenkins_job="polybot-king",
        strategy="golden-queen",
        workspace="/new/workspace/polybot-king",
        artifacts=[],
        skipped_unchanged=0,
        include_safety_databases=False,
    )

    with pytest.raises(RuntimeError, match="lacks workspace mount identity"):
        service.execute(plan)

    latest = service.catalog.latest_sync_run(
        source=app_config.ssh_host,
        job="polybot-king",
        strategy="golden-queen",
    )
    assert latest["status"] == "FAILED"
    assert latest["failed"] == 1
    assert "lacks workspace mount identity" in latest["error_json"]


def test_workspace_validation_failure_replaces_stale_success_as_latest_attempt(
    app_config,
) -> None:
    service = SyncService(app_config)
    service.catalog.begin_run(
        run_id="older-success",
        plan_id="older-plan",
        source=app_config.ssh_host,
        job="polybot-king",
        strategy="golden-queen",
    )
    service.catalog.finish_run(
        run_id="older-success",
        status="SUCCESS",
        transferred=0,
        skipped=0,
        failed=0,
        bytes_written=0,
        errors=[],
    )

    class RejectingRemote:
        def validate_workspace(self, **_kwargs):
            raise RuntimeError("workspace mount identity changed")

    service.remote = RejectingRemote()
    plan = SyncPlan.create(
        source=app_config.ssh_host,
        jenkins_job="polybot-king",
        strategy="golden-queen",
        workspace="/new/workspace/polybot-king",
        workspace_identity={
            "root_path": "/new/workspace",
            "root_realpath": "/new/workspace",
            "root_st_dev": 42,
            "workspace_st_dev": 42,
            "selection_contract": "allowlisted-root-job-v1",
        },
        artifacts=[],
        skipped_unchanged=0,
        include_safety_databases=False,
    )

    with pytest.raises(RuntimeError, match="mount identity changed"):
        service.execute(plan)

    latest = service.catalog.latest_sync_run(
        source=app_config.ssh_host,
        job="polybot-king",
        strategy="golden-queen",
    )
    successful = service.catalog.latest_sync_run(
        source=app_config.ssh_host,
        job="polybot-king",
        strategy="golden-queen",
        successful_only=True,
    )
    assert latest["status"] == "FAILED"
    assert latest["run_id"] != "older-success"
    assert successful["run_id"] == "older-success"


def test_research_archive_uses_online_snapshot_path_and_verifies(
    app_config, tmp_path: Path, monkeypatch
) -> None:
    service = SyncService(app_config)
    source = tmp_path / "remote" / "trades_sim_20260805.db"
    make_research_db(source, date(2026, 8, 5))
    source_stat = source.stat()
    artifact = RemoteArtifact(
        kind="database_research_archive",
        remote_path=str(source),
        size_bytes=source_stat.st_size,
        mtime_ns=source_stat.st_mtime_ns,
        jenkins_job="polybot-king",
        strategy="golden-queen",
        runtime_job="queen-research",
        canonical=False,
        archive_date="2026-08-05",
        mode="sim",
        data_contract="research-full-v1",
        database_utc_date="2026-08-05",
    )
    snapshot = tmp_path / "remote-staging" / "snapshot.db"
    identity = {
        "root_path": str(tmp_path / "remote"),
        "root_realpath": str(tmp_path / "remote"),
        "root_st_dev": 42,
        "workspace_st_dev": 42,
        "selection_contract": "allowlisted-root-job-v1",
    }

    class FakeRemote:
        def validate_workspace(
            self,
            *,
            job: str,
            expected_workspace: str,
            expected_identity: dict,
        ):
            assert job == "polybot-king"
            assert expected_workspace == str(tmp_path / "remote")
            assert expected_identity == identity
            return {"validated": True}

        def snapshot_database(
            self,
            remote_path: str,
            *,
            job: str,
            expected_workspace: str,
            expected_identity: dict,
            expected_data_contract: str | None = None,
            expected_database_utc_date: str | None = None,
        ):
            assert remote_path == str(source)
            assert job == "polybot-king"
            assert expected_workspace == str(tmp_path / "remote")
            assert expected_identity == identity
            assert expected_data_contract == "research-full-v1"
            assert expected_database_utc_date == "2026-08-05"
            snapshot.parent.mkdir(parents=True)
            shutil.copy2(source, snapshot)
            return {
                "schema_version": 1,
                "source": remote_path,
                "snapshot": str(snapshot),
                "source_size_bytes": source.stat().st_size,
                "snapshot_size_bytes": snapshot.stat().st_size,
                "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                "quick_check": ["ok"],
                "data_contract": "research-full-v1",
                "database_utc_date": "2026-08-05",
            }

        def rsync(self, *, remote_path: str, local_path: Path, compress: bool) -> None:
            assert remote_path == str(snapshot)
            assert compress is False
            local_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot, local_path)

        def cleanup_snapshot(self, snapshot_path: str) -> None:
            assert snapshot_path == str(snapshot)
            snapshot.unlink(missing_ok=True)

    service.remote = FakeRemote()
    plan = SyncPlan.create(
        source=app_config.ssh_host,
        jenkins_job="polybot-king",
        strategy="golden-queen",
        workspace=str(tmp_path / "remote"),
        workspace_identity=identity,
        artifacts=[artifact],
        skipped_unchanged=0,
        include_safety_databases=False,
    )

    result = service.execute(plan)

    assert result.status == "SUCCESS", result.errors
    local = service.local_path(artifact)
    assert local.as_posix().endswith("/databases/research/2026/08/05/trades_sim_20260805.db")
    assert service.verify(job="polybot-king", strategy="golden-queen")["status"] == "SUCCESS"
    assert (
        service.verify(
            job="polybot-king",
            strategy="golden-queen",
            from_date=date(2026, 8, 5),
            to_date=date(2026, 8, 5),
        )["status"]
        == "SUCCESS"
    )
    missing = service.verify(
        job="polybot-king",
        strategy="golden-queen",
        from_date=date(2026, 8, 4),
        to_date=date(2026, 8, 4),
    )
    assert missing["status"] == "FAILED"
    assert missing["archive_coverage"]["missing_dates"] == ["2026-08-04"]
    manifest = json.loads((local.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_kind"] == "database_research_archive"
    assert manifest["canonical"] is False
    assert manifest["mode"] == "sim"

    located = service.locate_evidence(
        job="polybot-king",
        strategy="golden-queen",
        from_date=date(2026, 8, 5),
        to_date=date(2026, 8, 5),
    )
    runtime = located["matches"][0]["runtimes"][0]
    assert located["matches"][0]["verification_command"].endswith(
        "--from-date 2026-08-05 --to-date 2026-08-05"
    )
    assert runtime["current_databases"] == []
    assert len(runtime["research_archives"]) == 1
    assert runtime["research_archives"][0]["canonical"] is False
    assert runtime["research_archives"][0]["archive_date"] == "2026-08-05"
    assert runtime["research_archives"][0]["mode"] == "sim"
    outside_range = service.locate_evidence(
        job="polybot-king",
        strategy="golden-queen",
        from_date=date(2026, 8, 4),
        to_date=date(2026, 8, 4),
    )
    assert outside_range["matches"][0]["runtimes"][0]["research_archives"] == []
    assert outside_range["matches"][0]["analysis_ready"] is False

    inventory = JobInventory(
        name="polybot-king",
        workspace="/external/workspace/polybot-king",
        workspace_identity={
            "root_path": "/external/workspace",
            "root_realpath": "/external/workspace",
            "root_st_dev": 42,
            "workspace_st_dev": 42,
            "selection_contract": "allowlisted-root-job-v1",
        },
        build_count=0,
        min_build=None,
        max_build=None,
        current_strategy="golden-queen",
        strategies=("golden-queen",),
        artifacts=(artifact,),
        remote_free_bytes=10**9,
    )
    monkeypatch.setattr(service, "scan", lambda **_kwargs: [inventory])
    incremental = service.create_plan(job="polybot-king", strategy="golden-queen")
    assert incremental.artifacts == []
    assert incremental.skipped_unchanged == 1


def test_online_snapshot_upserts_observed_remote_identity(
    app_config, tmp_path: Path
) -> None:
    service = SyncService(app_config)
    source = tmp_path / "remote" / "trades.db"
    source.parent.mkdir(parents=True)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('ok')")
    snapshot = tmp_path / "remote-staging" / "snapshot.db"
    planned_mtime = source.stat().st_mtime_ns
    observed_mtime = planned_mtime + 10_000
    observed_storage_size = source.stat().st_size + 321
    artifact = RemoteArtifact(
        kind="database_live",
        remote_path=str(source),
        size_bytes=source.stat().st_size,
        mtime_ns=planned_mtime,
        fingerprint="planned-fingerprint",
        jenkins_job="polybot-king",
        strategy="golden-queen",
        runtime_job="queen-live",
    )
    identity = {
        "root_path": str(tmp_path / "remote"),
        "root_realpath": str(tmp_path / "remote"),
        "root_st_dev": 42,
        "workspace_st_dev": 42,
        "selection_contract": "allowlisted-root-job-v1",
    }

    class FakeRemote:
        def validate_workspace(
            self,
            *,
            job: str,
            expected_workspace: str,
            expected_identity: dict,
        ):
            assert job == "polybot-king"
            assert expected_workspace == str(tmp_path / "remote")
            assert expected_identity == identity
            return {"validated": True}

        def snapshot_database(
            self,
            remote_path: str,
            *,
            job: str,
            expected_workspace: str,
            expected_identity: dict,
            expected_data_contract: str | None = None,
            expected_database_utc_date: str | None = None,
        ):
            assert remote_path == str(source)
            assert job == "polybot-king"
            assert expected_workspace == str(tmp_path / "remote")
            assert expected_identity == identity
            assert expected_data_contract is None
            assert expected_database_utc_date is None
            snapshot.parent.mkdir(parents=True)
            shutil.copy2(source, snapshot)
            snapshot_size = snapshot.stat().st_size
            return {
                "schema_version": 2,
                "source": remote_path,
                "snapshot": str(snapshot),
                "source_size_bytes": source.stat().st_size,
                "source_storage_bytes": observed_storage_size,
                "source_fingerprint_before": "planned-fingerprint",
                "source_fingerprint_after": "observed-fingerprint",
                "source_members_after": [
                    {
                        "suffix": "main",
                        "size_bytes": snapshot_size,
                        "mtime_ns": observed_mtime,
                    }
                ],
                "snapshot_size_bytes": snapshot_size,
                "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                "quick_check": ["ok"],
            }

        def rsync(self, *, remote_path: str, local_path: Path, compress: bool) -> None:
            assert remote_path == str(snapshot)
            assert compress is False
            local_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot, local_path)

        def cleanup_snapshot(self, snapshot_path: str) -> None:
            assert snapshot_path == str(snapshot)
            snapshot.unlink(missing_ok=True)

    service.remote = FakeRemote()
    plan = SyncPlan.create(
        source=app_config.ssh_host,
        jenkins_job="polybot-king",
        strategy="golden-queen",
        workspace=str(tmp_path / "remote"),
        workspace_identity=identity,
        artifacts=[artifact],
        skipped_unchanged=0,
        include_safety_databases=False,
    )

    result = service.execute(plan)
    row = service.catalog.get_artifact(plan.artifacts[0].source_key)

    assert result.status == "SUCCESS", result.errors
    assert row is not None
    assert row["remote_fingerprint"] == "observed-fingerprint"
    assert row["remote_size_bytes"] == observed_storage_size
    assert row["remote_mtime_ns"] == observed_mtime


def test_historical_range_excludes_mutable_research_full_active_shard(
    app_config,
) -> None:
    service = SyncService(app_config)
    runtime = "pomegranate-research"
    artifacts = []
    current_day = datetime.now(UTC).date()
    for name, kind, archive_date, canonical in (
        ("trades_sim.db", "database_sim", current_day.isoformat(), True),
        ("trades_sim_19990101.db", "database_research_archive", "1999-01-01", False),
    ):
        local = app_config.data_root / name
        database_day = date.fromisoformat(str(archive_date))
        make_research_db(local, database_day)
        payload = local.read_bytes()
        item = RemoteArtifact(
            kind=kind,
            remote_path=f"/remote/golden-pomegranate/data/{runtime}/{name}",
            size_bytes=len(payload),
            mtime_ns=local.stat().st_mtime_ns,
            jenkins_job="polybot-pomegranate",
            source=app_config.ssh_host,
            strategy="golden-pomegranate",
            runtime_job=runtime,
            canonical=canonical,
            archive_date=archive_date,
            mode="sim",
            data_contract="research-full-v1",
            database_utc_date=database_day.isoformat(),
        )
        service.catalog.upsert_artifact(
            item,
            source=app_config.ssh_host,
            local_path=local,
            local_sha256=hashlib.sha256(payload).hexdigest(),
            metadata={
                "canonical": canonical,
                "archive_date": archive_date,
                "mode": "sim",
                "data_contract": "research-full-v1",
                "database_utc_date": database_day.isoformat(),
            },
        )
        artifacts.append(item)

    verification = service.verify(
        job="polybot-pomegranate",
        strategy="golden-pomegranate",
        from_date=date(1999, 1, 1),
        to_date=date(1999, 1, 1),
    )
    located = service.locate_evidence(
        job="polybot-pomegranate",
        strategy="golden-pomegranate",
        from_date=date(1999, 1, 1),
        to_date=date(1999, 1, 1),
    )
    runtime_result = located["matches"][0]["runtimes"][0]

    assert verification["status"] == "SUCCESS"
    assert verification["checked"] == 1
    assert runtime_result["current_databases"] == []
    assert len(runtime_result["research_archives"]) == 1
    assert runtime_result["research_archives"][0]["data_contract"] == "research-full-v1"


def test_current_research_active_shard_is_partial_not_completed_day(app_config) -> None:
    service = SyncService(app_config)
    active_day = datetime.now(UTC).date()
    local = app_config.data_root / "active-pomegranate.db"
    make_research_db(local, active_day)
    payload = local.read_bytes()
    artifact = RemoteArtifact(
        kind="database_sim",
        remote_path="/remote/golden-pomegranate/data/research/trades_sim.db",
        size_bytes=len(payload),
        mtime_ns=local.stat().st_mtime_ns,
        jenkins_job="polybot-pomegranate",
        source=app_config.ssh_host,
        strategy="golden-pomegranate",
        runtime_job="research",
        canonical=True,
        archive_date=active_day.isoformat(),
        mode="sim",
        data_contract="research-full-v1",
        database_utc_date=active_day.isoformat(),
    )
    service.catalog.upsert_artifact(
        artifact,
        source=app_config.ssh_host,
        local_path=local,
        local_sha256=hashlib.sha256(payload).hexdigest(),
        metadata={
            "archive_date": active_day.isoformat(),
            "mode": "sim",
            "data_contract": "research-full-v1",
            "database_utc_date": active_day.isoformat(),
        },
    )

    verification = service.verify(
        job="polybot-pomegranate",
        strategy="golden-pomegranate",
        from_date=active_day,
        to_date=active_day,
    )

    assert verification["status"] == "FAILED"
    assert verification["archive_coverage"]["covered_dates"] == []
    assert verification["archive_coverage"]["partial_active_dates"] == [
        active_day.isoformat()
    ]


def test_requested_archive_range_is_not_satisfied_by_canonical_database_or_logs(
    app_config,
) -> None:
    service = SyncService(app_config)
    job = "polybot-pomegranate"
    strategy = "golden-pomegranate"
    runtime = "pomegranate-research"

    def save_sqlite(name: str, kind: str, archive_date: str | None = None) -> None:
        local = app_config.data_root / f"local-{name}"
        data_contract = "research-full-v1" if kind == "database_research_archive" else None
        if data_contract:
            make_research_db(local, date.fromisoformat(str(archive_date)))
        else:
            with sqlite3.connect(local) as connection:
                connection.execute("CREATE TABLE evidence(value TEXT)")
                connection.execute("INSERT INTO evidence VALUES ('ok')")
        payload = local.read_bytes()
        item = RemoteArtifact(
            kind=kind,
            remote_path=f"/remote/{strategy}/data/{runtime}/{name}",
            size_bytes=len(payload),
            mtime_ns=local.stat().st_mtime_ns,
            jenkins_job=job,
            source=app_config.ssh_host,
            strategy=strategy,
            runtime_job=runtime,
            canonical=kind == "database_sim",
            archive_date=archive_date,
            mode="sim",
            data_contract=data_contract,
            database_utc_date=archive_date if data_contract else None,
        )
        service.catalog.upsert_artifact(
            item,
            source=app_config.ssh_host,
            local_path=local,
            local_sha256=hashlib.sha256(payload).hexdigest(),
            metadata={
                "completed_at": "2026-08-06T00:00:00+00:00",
                "archive_date": archive_date,
                "data_contract": data_contract,
                "database_utc_date": archive_date if data_contract else None,
            },
        )

    save_sqlite("trades_sim.db", "database_sim")
    save_sqlite(
        "trades_sim_20260804.db",
        "database_research_archive",
        "2026-08-04",
    )
    service.catalog.begin_run(
        run_id="range-success",
        plan_id="range-plan",
        source=app_config.ssh_host,
        job=job,
        strategy=strategy,
    )
    service.catalog.finish_run(
        run_id="range-success",
        status="SUCCESS",
        transferred=2,
        skipped=0,
        failed=0,
        bytes_written=2,
        errors=[],
    )

    verification = service.verify(
        job=job,
        strategy=strategy,
        from_date=date(2026, 8, 4),
        to_date=date(2026, 8, 5),
    )
    located = service.locate_evidence(
        job=job,
        strategy=strategy,
        from_date=date(2026, 8, 4),
        to_date=date(2026, 8, 5),
    )

    assert verification["status"] == "FAILED"
    assert verification["archive_coverage"]["missing_dates"] == ["2026-08-05"]
    assert verification["archive_coverage"]["covered_dates"] == ["2026-08-04"]
    match = located["matches"][0]
    assert match["latest_sync_attempt"]["status"] == "SUCCESS"
    assert match["analysis_ready"] is False
    assert match["archive_coverage"]["missing_dates"] == ["2026-08-05"]
    assert match["runtimes"][0]["current_databases"] == []


def test_source_missing_archive_requires_full_utc_day_cutoff_metadata(
    app_config,
) -> None:
    service = SyncService(app_config)
    local = app_config.data_root / "trades_sim_20260804.db"
    archive_day = date(2026, 8, 4)
    make_research_db(local, archive_day)
    payload = local.read_bytes()
    item = RemoteArtifact(
        kind="database_research_archive",
        remote_path=("/remote/golden-pomegranate/data/research/trades_sim_20260804.db"),
        size_bytes=len(payload),
        mtime_ns=local.stat().st_mtime_ns,
        jenkins_job="polybot-pomegranate",
        source=app_config.ssh_host,
        strategy="golden-pomegranate",
        runtime_job="research",
        canonical=False,
        archive_date="2026-08-04",
        mode="sim",
        data_contract="research-full-v1",
        database_utc_date=archive_day.isoformat(),
    )
    service.catalog.upsert_artifact(
        item,
        source=app_config.ssh_host,
        local_path=local,
        local_sha256=hashlib.sha256(payload).hexdigest(),
        metadata={
            "completed_at": "2026-08-04T23:59:59+00:00",
            "archive_date": archive_day.isoformat(),
            "data_contract": "research-full-v1",
            "database_utc_date": archive_day.isoformat(),
        },
    )
    service.catalog.mark_source_missing(
        source=app_config.ssh_host,
        job="polybot-pomegranate",
        observed_paths=set(),
        log_cutoff_ns=0,
        archive_from_date=date(2026, 8, 4),
        archive_to_date=date(2026, 8, 4),
        include_canonical_databases=False,
    )

    unproven = service.verify(
        job="polybot-pomegranate",
        strategy="golden-pomegranate",
        from_date=date(2026, 8, 4),
        to_date=date(2026, 8, 4),
    )
    assert unproven["status"] == "FAILED"
    assert unproven["archive_coverage"]["source_missing_unproven_dates"] == ["2026-08-04"]

    with service.catalog.connect() as connection:
        connection.execute(
            "UPDATE artifacts SET metadata_json = ? WHERE source_key = ?",
            (
                json.dumps(
                    {
                        "completed_at": "2026-08-05T00:00:00+00:00",
                        "archive_date": archive_day.isoformat(),
                        "data_contract": "research-full-v1",
                        "database_utc_date": archive_day.isoformat(),
                    }
                ),
                item.source_key,
            ),
        )
    proven = service.verify(
        job="polybot-pomegranate",
        strategy="golden-pomegranate",
        from_date=date(2026, 8, 4),
        to_date=date(2026, 8, 4),
    )
    assert proven["status"] == "SUCCESS"
    assert proven["archive_coverage"]["covered_dates"] == ["2026-08-04"]


def test_each_regular_artifact_revalidates_workspace_before_transfer(app_config) -> None:
    service = SyncService(app_config)
    identity = {
        "root_path": "/remote/workspace",
        "root_realpath": "/remote/workspace",
        "root_st_dev": 42,
        "workspace_st_dev": 42,
        "selection_contract": "allowlisted-root-job-v1",
    }
    validations: list[str] = []

    class FakeRemote:
        def validate_workspace(self, **_kwargs):
            validations.append("validate")
            return {"validated": True}

        def rsync(self, *, remote_path: str, local_path: Path, compress: bool) -> None:
            assert compress is True
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text(remote_path, encoding="utf-8")

    artifacts = [
        RemoteArtifact(
            kind="bot_log",
            remote_path=f"/remote/workspace/polybot-queen/golden-queen/data/job/logs/{day}.log",
            size_bytes=10,
            mtime_ns=index,
            jenkins_job="polybot-queen",
            strategy="golden-queen",
            runtime_job="job",
        )
        for index, day in enumerate(("20260805", "20260806"), start=1)
    ]
    service.remote = FakeRemote()
    plan = SyncPlan.create(
        source=app_config.ssh_host,
        jenkins_job="polybot-queen",
        strategy="golden-queen",
        workspace="/remote/workspace/polybot-queen",
        workspace_identity=identity,
        artifacts=artifacts,
        skipped_unchanged=0,
        include_safety_databases=False,
    )

    result = service.execute(plan)

    assert result.status == "SUCCESS", result.errors
    # One preflight validation plus one immediately before each artifact.
    assert validations == ["validate", "validate", "validate"]


def test_each_console_batch_and_artifact_boundary_revalidates_workspace(
    app_config,
) -> None:
    config = replace_dataclass(app_config, batch_file_limit=1)
    service = SyncService(config)
    identity = {
        "root_path": "/remote/workspace",
        "root_realpath": "/remote/workspace",
        "root_st_dev": 42,
        "workspace_st_dev": 42,
        "selection_contract": "allowlisted-root-job-v1",
    }
    validations: list[str] = []

    class FakeRemote:
        def validate_workspace(self, **_kwargs):
            validations.append("validate")
            return {"validated": True}

        def rsync_files(self, *, remote_paths: list[str], local_root: Path) -> None:
            assert len(remote_paths) == 1
            for remote_path in remote_paths:
                relative = Path(remote_path).relative_to(Path(config.remote_jenkins_home))
                incoming = local_root / relative
                incoming.parent.mkdir(parents=True, exist_ok=True)
                incoming.write_text("console", encoding="utf-8")

    artifacts = [
        RemoteArtifact(
            kind="jenkins_console",
            remote_path=(
                f"{config.remote_jenkins_home}/jobs/polybot-queen/builds/{build}/log"
            ),
            size_bytes=10,
            mtime_ns=build,
            jenkins_job="polybot-queen",
            strategy="golden-queen",
            build_number=build,
        )
        for build in (1, 2)
    ]
    service.remote = FakeRemote()
    plan = SyncPlan.create(
        source=config.ssh_host,
        jenkins_job="polybot-queen",
        strategy="golden-queen",
        workspace="/remote/workspace/polybot-queen",
        workspace_identity=identity,
        artifacts=artifacts,
        skipped_unchanged=0,
        include_safety_databases=False,
    )

    result = service.execute(plan)

    assert result.status == "SUCCESS", result.errors
    # preflight + two batch boundaries + two artifact store boundaries
    assert validations == ["validate"] * 5


def test_sync_job_records_scan_plan_failure_over_stale_success(
    app_config, monkeypatch
) -> None:
    service = SyncService(app_config)
    service.catalog.save_inventory(
        source=app_config.ssh_host,
        job="polybot-pomegranate",
        current_strategy="golden-pomegranate",
        payload={"current_strategy": "golden-pomegranate"},
    )
    service.catalog.begin_run(
        run_id="older-success",
        plan_id="older-plan",
        source=app_config.ssh_host,
        job="polybot-pomegranate",
        strategy="golden-pomegranate",
    )
    service.catalog.finish_run(
        run_id="older-success",
        status="SUCCESS",
        transferred=0,
        skipped=0,
        failed=0,
        bytes_written=0,
        errors=[],
    )
    monkeypatch.setattr(
        service,
        "create_plan",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("archive date mismatch")),
    )

    for _attempt in range(2):
        with pytest.raises(RuntimeError, match="archive date mismatch"):
            service.sync_job(job="polybot-pomegranate")

    latest = service.catalog.latest_sync_run(
        source=app_config.ssh_host,
        job="polybot-pomegranate",
        strategy="golden-pomegranate",
    )
    successful = service.catalog.latest_sync_run(
        source=app_config.ssh_host,
        job="polybot-pomegranate",
        strategy="golden-pomegranate",
        successful_only=True,
    )
    failed = [
        row
        for row in service.catalog.list_sync_runs(source=app_config.ssh_host, limit=10)
        if row["status"] == "FAILED"
    ]
    assert latest["status"] == "FAILED"
    assert latest["finished_at"]
    assert successful["run_id"] == "older-success"
    assert len(failed) == 2
    assert failed[0]["plan_id"] == failed[1]["plan_id"]
    assert failed[0]["plan_id"].startswith("no-plan-")


def test_progress_callback_exception_cannot_leave_running_sync(app_config) -> None:
    service = SyncService(app_config)

    class FakeRemote:
        def validate_workspace(self, **_kwargs):
            return {"validated": True}

    service.remote = FakeRemote()
    plan = SyncPlan.create(
        source=app_config.ssh_host,
        jenkins_job="polybot-queen",
        strategy="golden-queen",
        workspace="/remote/workspace/polybot-queen",
        workspace_identity={
            "root_path": "/remote/workspace",
            "root_realpath": "/remote/workspace",
            "root_st_dev": 42,
            "workspace_st_dev": 42,
            "selection_contract": "allowlisted-root-job-v1",
        },
        artifacts=[],
        skipped_unchanged=0,
        include_safety_databases=False,
    )

    def broken_progress(**_payload: object) -> None:
        raise RuntimeError("UI disconnected")

    result = service.execute(plan, progress=broken_progress)
    latest = service.catalog.latest_sync_run(
        source=app_config.ssh_host,
        job="polybot-queen",
        strategy="golden-queen",
    )

    assert result.status == "SUCCESS"
    assert latest["status"] == "SUCCESS"
    assert latest["finished_at"]
    assert not [
        row
        for row in service.catalog.list_sync_runs(source=app_config.ssh_host)
        if row["status"] == "RUNNING"
    ]


def test_research_snapshot_rollover_is_rejected_without_false_success(
    app_config, tmp_path: Path
) -> None:
    service = SyncService(app_config)
    planned_day = date(2026, 8, 5)
    observed_day = date(2026, 8, 6)
    snapshot = tmp_path / "remote-staging" / "snapshot.db"
    make_research_db(snapshot, observed_day)
    identity = {
        "root_path": "/remote/workspace",
        "root_realpath": "/remote/workspace",
        "root_st_dev": 42,
        "workspace_st_dev": 42,
        "selection_contract": "allowlisted-root-job-v1",
    }
    artifact = RemoteArtifact(
        kind="database_research_archive",
        remote_path=(
            "/remote/workspace/polybot-pomegranate/golden-pomegranate/"
            "data/research/trades_sim_20260805.db"
        ),
        size_bytes=snapshot.stat().st_size,
        mtime_ns=snapshot.stat().st_mtime_ns,
        jenkins_job="polybot-pomegranate",
        strategy="golden-pomegranate",
        runtime_job="research",
        canonical=False,
        archive_date=planned_day.isoformat(),
        mode="sim",
        data_contract="research-full-v1",
        database_utc_date=planned_day.isoformat(),
    )

    class FakeRemote:
        def validate_workspace(self, **_kwargs):
            return {"validated": True}

        def snapshot_database(self, _remote_path: str, **_kwargs):
            return {
                "schema_version": 2,
                "snapshot": str(snapshot),
                "snapshot_size_bytes": snapshot.stat().st_size,
                "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                "quick_check": ["ok"],
                "data_contract": "research-full-v1",
                "database_utc_date": observed_day.isoformat(),
            }

        def rsync(self, *, remote_path: str, local_path: Path, compress: bool) -> None:
            assert remote_path == str(snapshot)
            assert compress is False
            local_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot, local_path)

        def cleanup_snapshot(self, _snapshot_path: str) -> None:
            return None

    service.remote = FakeRemote()
    plan = SyncPlan.create(
        source=app_config.ssh_host,
        jenkins_job="polybot-pomegranate",
        strategy="golden-pomegranate",
        workspace="/remote/workspace/polybot-pomegranate",
        workspace_identity=identity,
        artifacts=[artifact],
        skipped_unchanged=0,
        include_safety_databases=False,
    )

    result = service.execute(plan)

    assert result.status == "FAILED"
    assert result.transferred == 0
    assert not service.local_path(plan.artifacts[0]).exists()
    conflicts = service.catalog.list_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0]["conflict_type"] == "RESEARCH_SNAPSHOT_DATE_CHANGED"
    assert conflicts[0]["status"] == "OBSERVED"
    latest = service.catalog.latest_sync_run(
        source=app_config.ssh_host,
        job="polybot-pomegranate",
        strategy="golden-pomegranate",
    )
    assert latest["status"] == "FAILED"
    assert latest["finished_at"]
