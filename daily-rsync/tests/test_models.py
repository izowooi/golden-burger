from __future__ import annotations

from daily_rsync.models import RemoteArtifact, SyncPlan


def test_plan_round_trip(tmp_path) -> None:
    item = RemoteArtifact(
        kind="database_live",
        remote_path="/remote/trades.db",
        size_bytes=123,
        mtime_ns=456,
        jenkins_job="polybot-king",
        strategy="golden-queen",
        runtime_job="queen-live-12h",
    )
    plan = SyncPlan.create(
        source="macmini-m5",
        jenkins_job="polybot-king",
        strategy="golden-queen",
        artifacts=[item],
        skipped_unchanged=2,
        include_safety_databases=False,
    )
    path = plan.write(tmp_path)
    restored = SyncPlan.read(path)

    assert restored.plan_id == plan.plan_id
    assert restored.estimated_bytes == 123
    assert restored.artifacts[0].source_key == item.source_key
