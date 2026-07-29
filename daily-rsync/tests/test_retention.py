from __future__ import annotations

from datetime import UTC, datetime, timedelta

from daily_rsync.models import RemoteArtifact
from daily_rsync.sync import SyncService


def test_retention_is_dry_run_by_default_and_apply_keeps_catalog(app_config) -> None:
    service = SyncService(app_config)
    local = app_config.data_root / "old.log.gz"
    local.write_bytes(b"old")
    old = datetime.now(UTC) - timedelta(days=400)
    item = RemoteArtifact(
        kind="bot_log",
        remote_path="/remote/old.log",
        size_bytes=3,
        mtime_ns=int(old.timestamp() * 1_000_000_000),
        jenkins_job="polybot-king",
        strategy="golden-queen",
        runtime_job="queen-live-12h",
        completed_at=old.isoformat(),
    )
    service.catalog.upsert_artifact(
        item,
        source="macmini",
        local_path=local,
        local_sha256="digest",
        metadata={"completed_at": old.isoformat()},
    )

    preview = service.prune_retention()
    assert preview["candidates"] == 1
    assert preview["applied"] is False
    assert local.is_file()

    applied = service.prune_retention(apply=True)
    assert applied["candidates"] == 1
    assert not local.exists()
    row = service.catalog.get_artifact(item.source_key)
    assert row["status"] == "RETENTION_DELETED"
    assert row["local_path"] is None
