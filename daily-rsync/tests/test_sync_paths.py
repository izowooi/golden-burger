from __future__ import annotations

from daily_rsync.models import RemoteArtifact
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
