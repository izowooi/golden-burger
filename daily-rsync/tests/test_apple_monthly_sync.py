import json
import sqlite3
from datetime import date

import pytest
from test_apple_monthly_remote import make_apple_database
from test_public_payloads import LocalRemote

from daily_rsync import remote_agent
from daily_rsync.models import RemoteArtifact, SyncPlan
from daily_rsync.sync import SyncService


@pytest.fixture
def apple_sync(app_config, tmp_path):
    remote = LocalRemote(tmp_path / "remote-apple", [])
    path = remote.root / "polybot-do/data/collection-v2/2026-09.sqlite"
    make_apple_database(path)
    initial = remote_agent.apple_collection_record(path)
    artifact = RemoteArtifact(
        kind="database_sim",
        remote_path=str(path),
        size_bytes=path.stat().st_size,
        mtime_ns=path.stat().st_mtime_ns,
        source=app_config.ssh_host,
        jenkins_job="polybot-do",
        strategy="golden-apple",
        runtime_job="polybot-do",
        canonical=True,
        mode="sim",
        data_contract="apple-filtered-frames-v1",
        database_month="2026-09",
        observation_window=initial["observation_window"],
    )
    service = SyncService(app_config)
    service.remote = remote
    remote.validate_workspace = lambda **_: {"validated": True}
    base_snapshot = remote.snapshot_database

    def snapshot(source, **kwargs):
        identity = remote_agent.apple_collection_record(path)
        return {
            **base_snapshot(source, **kwargs),
            "apple_collection": identity,
            "data_contract": identity["contract"],
            "database_month": identity["month"],
            "observation_window": identity["observation_window"],
        }

    remote.snapshot_database = snapshot
    plan = SyncPlan.create(
        source=app_config.ssh_host,
        jenkins_job="polybot-do",
        strategy="golden-apple",
        workspace=str(path.parents[2]),
        workspace_identity={"fixture": True},
        artifacts=[artifact],
        skipped_unchanged=0,
        include_safety_databases=False,
    )
    return service, remote, path, artifact, plan


def test_monthly_sync_uses_snapshot_window_and_preserves_pin_identity(apple_sync):
    service, remote, path, artifact, plan = apple_sync
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE runs SET status='PARTIAL',finished=started+2 WHERE slot=1")
    result = service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    local = service.local_path(artifact)
    assert local.as_posix().endswith("/databases/monthly/2026-09/2026-09.sqlite")
    metadata = json.loads(service.catalog.get_artifact(artifact.source_key)["metadata_json"])
    assert metadata["observation_window"]["partial_run_count"] == 1
    assert metadata["observation_window"]["all_runs_finished"] is True
    verified = service.verify(
        job="polybot-do",
        strategy="golden-apple",
        from_date=date(2026, 9, 1),
        to_date=date(2026, 9, 30),
    )
    assert verified["status"] == "SUCCESS", verified
    assert verified["archive_coverage"] is None
    assert verified["monthly_coverage"]["calendar_month_complete"] is False
    assert verified["monthly_coverage"]["snapshots"][0]["month"] == "2026-09"
    pinned = service.pin_database(artifact.source_key)
    manifest = json.loads((pinned.parent / "manifest.json").read_text())
    assert manifest["database_month"] == "2026-09"
    assert manifest["observation_window"]["run_count"] == 2
    located = service.locate_evidence(
        job="polybot-do",
        strategy="golden-apple",
        from_date=date(2026, 9, 1),
        to_date=date(2026, 9, 30),
    )
    runtime = located["matches"][0]["runtimes"][0]
    assert runtime["monthly_coverage"]["scope"] == "observed_runs_only"
    assert len(runtime["current_databases"]) == 1
    outside = service.verify(
        job="polybot-do",
        strategy="golden-apple",
        from_date=date(2026, 8, 1),
        to_date=date(2026, 8, 31),
    )
    assert outside["status"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "field,value", [("job", "polybot-re"), ("month", "2026-08"), ("format", "unsupported")]
)
def test_monthly_snapshot_revalidates_local_database_identity(apple_sync, field, value):
    service, remote, path, artifact, plan = apple_sync
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE meta SET value=? WHERE key=?", (value, field))
    result = service.execute(plan)
    assert result.status == "FAILED"
    assert service.catalog.get_artifact(artifact.source_key) is None
    assert not service.local_path(artifact).exists()


def test_monthly_catalog_window_corruption_fails_verify(apple_sync):
    service, remote, path, artifact, plan = apple_sync
    assert service.execute(plan).status == "SUCCESS"
    with service.catalog.connect() as connection:
        metadata = json.loads(
            connection.execute("SELECT metadata_json FROM artifacts").fetchone()[0]
        )
        metadata["observation_window"]["run_count"] = 999
        connection.execute("UPDATE artifacts SET metadata_json=?", (json.dumps(metadata),))
    assert service.verify(job="polybot-do", strategy="golden-apple")["status"] == "FAILED"
