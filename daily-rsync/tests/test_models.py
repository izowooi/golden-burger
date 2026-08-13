from __future__ import annotations

from daily_rsync.models import RemoteArtifact, SyncPlan


def test_plan_round_trip(tmp_path) -> None:
    item = RemoteArtifact(
        kind="database_live",
        remote_path="/remote/trades.db",
        size_bytes=123,
        mtime_ns=456,
        jenkins_job="polybot-king",
        source="macmini-m5",
        strategy="golden-queen",
        runtime_job="queen-live-12h",
        fingerprint="composite-1",
    )
    plan = SyncPlan.create(
        source="macmini-m5",
        jenkins_job="polybot-king",
        strategy="golden-queen",
        workspace="/external/workspace/polybot-king",
        workspace_identity={
            "root_path": "/external/workspace",
            "root_realpath": "/external/workspace",
            "root_st_dev": 42,
            "workspace_st_dev": 42,
            "selection_contract": "allowlisted-root-job-v1",
        },
        workspace_epoch="external-v2",
        artifacts=[item],
        skipped_unchanged=2,
        include_safety_databases=False,
    )
    path = plan.write(tmp_path)
    restored = SyncPlan.read(path)

    assert restored.plan_id == plan.plan_id
    assert restored.estimated_bytes == 123
    assert restored.workspace == "/external/workspace/polybot-king"
    assert restored.workspace_identity == plan.workspace_identity
    assert restored.workspace_epoch == "external-v2"
    assert restored.artifacts[0].fingerprint == "composite-1"
    assert restored.artifacts[0].source_key == item.source_key
