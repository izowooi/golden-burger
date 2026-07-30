from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from daily_rsync.models import RemoteArtifact
from daily_rsync.sync import SyncService


def _save(
    service: SyncService,
    *,
    tmp_path: Path,
    kind: str,
    remote_path: str,
    runtime_job: str | None,
    jenkins_job: str = "polybot-bear",
    strategy: str = "golden-honeydew",
    build_number: int | None = None,
) -> None:
    item = RemoteArtifact(
        kind=kind,
        remote_path=remote_path,
        size_bytes=5,
        mtime_ns=1,
        jenkins_job=jenkins_job,
        strategy=strategy,
        runtime_job=runtime_job,
        build_number=build_number,
        completed_at="2026-07-30T00:00:00+00:00",
    )
    path = tmp_path / f"{item.source_key}.artifact"
    path.write_bytes(b"hello")
    digest = hashlib.sha256(b"hello").hexdigest()
    service.catalog.upsert_artifact(
        item,
        source="test-host",
        local_path=path,
        local_sha256=digest,
        remote_sha256=digest if kind.startswith("database") else None,
        metadata={"completed_at": item.completed_at},
    )


def test_locate_evidence_preserves_deployment_and_runtime_identity(
    app_config, tmp_path: Path
) -> None:
    service = SyncService(app_config)
    _save(
        service,
        tmp_path=tmp_path,
        kind="database_live",
        remote_path="/workspace/golden-honeydew/data/default/trades.db",
        runtime_job="default",
    )
    _save(
        service,
        tmp_path=tmp_path,
        kind="bot_log",
        remote_path="/workspace/golden-honeydew/data/default/logs/20260730.log",
        runtime_job="default",
    )
    _save(
        service,
        tmp_path=tmp_path,
        kind="jenkins_console",
        remote_path="/jenkins/jobs/polybot-bear/builds/42/log",
        runtime_job=None,
        build_number=42,
    )
    service.catalog.begin_run(
        run_id="run-1",
        plan_id="plan-1",
        source="test-host",
        job="polybot-bear",
        strategy="golden-honeydew",
    )
    service.catalog.finish_run(
        run_id="run-1",
        status="SUCCESS",
        transferred=3,
        skipped=0,
        failed=0,
        bytes_written=15,
        errors=[],
    )

    result = service.locate_evidence(strategy="golden-honeydew")

    assert result["status"] == "FOUND"
    assert result["match_count"] == 1
    match = result["matches"][0]
    assert match["jenkins_job"] == "polybot-bear"
    assert match["strategy"] == "golden-honeydew"
    assert match["analysis_ready"] is True
    assert match["latest_sync_attempt"]["run_id"] == "run-1"
    assert match["latest_successful_sync"]["run_id"] == "run-1"
    assert match["jenkins_console_logs"]["first_build"] == 42
    assert match["runtimes"][0]["runtime_job"] == "default"
    database = match["runtimes"][0]["databases"][0]
    assert database["available"] is True
    assert database["source_completed_at"] == "2026-07-30T00:00:00+00:00"
    assert database["historical_source_missing"] is False
    assert database["remote_path"].endswith("/data/default/trades.db")
    assert match["runtimes"][0]["bot_logs"]["available"] == 1
    assert (
        match["verification_command"]
        == "uv run daily-rsync verify --job polybot-bear --strategy golden-honeydew"
    )


def test_locate_evidence_keeps_multiple_deployments_separate(
    app_config, tmp_path: Path
) -> None:
    service = SyncService(app_config)
    deployments = (
        ("polybot-bear", "golden-honeydew"),
        ("polybot-bear-ab", "golden-honeydew"),
    )
    for index, (job, strategy) in enumerate(deployments, start=1):
        _save(
            service,
            tmp_path=tmp_path,
            kind="database_live",
            remote_path=f"/workspace/{job}/{strategy}/data/default/trades.db",
            runtime_job="default",
            jenkins_job=job,
            strategy=strategy,
        )
        run_id = f"run-{index}"
        service.catalog.begin_run(
            run_id=run_id,
            plan_id=f"plan-{index}",
            source="test-host",
            job=job,
            strategy=strategy,
        )
        service.catalog.finish_run(
            run_id=run_id,
            status="SUCCESS",
            transferred=1,
            skipped=0,
            failed=0,
            bytes_written=5,
            errors=[],
        )

    result = service.locate_evidence(strategy="golden-honeydew")

    assert result["match_count"] == 2
    assert {
        (match["jenkins_job"], match["strategy"]) for match in result["matches"]
    } == set(deployments)
    assert all(match["analysis_ready"] is True for match in result["matches"])


def test_locate_evidence_keeps_strategy_epochs_separate(
    app_config, tmp_path: Path
) -> None:
    service = SyncService(app_config)
    strategies = ("golden-honeydew", "golden-nectarine")
    for index, strategy in enumerate(strategies, start=1):
        _save(
            service,
            tmp_path=tmp_path,
            kind="database_live",
            remote_path=f"/workspace/polybot-bear/{strategy}/data/default/trades.db",
            runtime_job="default",
            strategy=strategy,
        )
        service.catalog.begin_run(
            run_id=f"epoch-{index}",
            plan_id=f"epoch-plan-{index}",
            source="test-host",
            job="polybot-bear",
            strategy=strategy,
        )
        service.catalog.finish_run(
            run_id=f"epoch-{index}",
            status="SUCCESS",
            transferred=1,
            skipped=0,
            failed=0,
            bytes_written=5,
            errors=[],
        )

    result = service.locate_evidence(job="polybot-bear")

    assert result["match_count"] == 2
    assert {match["strategy"] for match in result["matches"]} == set(strategies)


def test_locate_evidence_fails_closed_when_latest_sync_attempt_failed(
    app_config, tmp_path: Path
) -> None:
    service = SyncService(app_config)
    _save(
        service,
        tmp_path=tmp_path,
        kind="database_live",
        remote_path="/workspace/golden-honeydew/data/default/trades.db",
        runtime_job="default",
    )
    service.catalog.begin_run(
        run_id="run-success",
        plan_id="plan-success",
        source="test-host",
        job="polybot-bear",
        strategy="golden-honeydew",
    )
    service.catalog.finish_run(
        run_id="run-success",
        status="SUCCESS",
        transferred=1,
        skipped=0,
        failed=0,
        bytes_written=5,
        errors=[],
    )
    service.catalog.begin_run(
        run_id="run-failed",
        plan_id="plan-failed",
        source="test-host",
        job="polybot-bear",
        strategy="golden-honeydew",
    )
    service.catalog.finish_run(
        run_id="run-failed",
        status="FAILED",
        transferred=0,
        skipped=0,
        failed=1,
        bytes_written=0,
        errors=["transfer failed"],
    )

    result = service.locate_evidence(
        job="polybot-bear", strategy="golden-honeydew"
    )

    match = result["matches"][0]
    assert match["analysis_ready"] is False
    assert match["latest_sync_attempt"]["run_id"] == "run-failed"
    assert match["latest_sync_attempt"]["status"] == "FAILED"
    assert match["latest_successful_sync"]["run_id"] == "run-success"


def test_verify_treats_retention_deleted_logs_as_an_explicit_skip(
    app_config, tmp_path: Path
) -> None:
    service = SyncService(app_config)
    _save(
        service,
        tmp_path=tmp_path,
        kind="bot_log",
        remote_path="/workspace/golden-honeydew/data/default/logs/old.log",
        runtime_job="default",
    )
    row = service.catalog.list_artifacts(
        job="polybot-bear", strategy="golden-honeydew"
    )[0]
    Path(row["local_path"]).unlink()
    service.catalog.mark_retention_deleted(str(row["source_key"]))

    result = service.verify(job="polybot-bear", strategy="golden-honeydew")

    assert result["status"] == "SUCCESS"
    assert result["checked"] == 0
    assert result["skipped_retention_deleted"] == 1
    assert result["failed"] == 0


def test_locate_evidence_requires_an_identity(app_config) -> None:
    service = SyncService(app_config)

    with pytest.raises(ValueError, match="at least one"):
        service.locate_evidence()
