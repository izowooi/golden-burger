from __future__ import annotations

import pytest

from daily_rsync.models import JobInventory, RemoteArtifact
from daily_rsync.sync import SyncService


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
