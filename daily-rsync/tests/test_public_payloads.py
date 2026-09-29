from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from polybot_observability.market_data_bundle import export_bundle, reference_closure
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import (
    CorruptPayloadError,
    MissingPayloadError,
    PayloadReader,
    PayloadStore,
)

from daily_rsync.models import JobInventory, RemoteArtifact, SyncPlan
from daily_rsync.public_payloads import (
    closure_sidecar,
    local_payload_writer,
    synchronize_database_closure,
    verify_database_closure,
    write_closure_descriptor,
)
from daily_rsync.remote import PublicPayloadBundleLimitError
from daily_rsync.sync import SyncService, sha256


class LocalRemote:
    def __init__(self, root, payloads):
        self.root = root
        root.mkdir()
        self.public_db = root / "public.db"
        with PayloadStore(self.public_db) as store:
            self.hashes = store.put_many(payloads)
            self.references = PayloadReferences(reader=store, writer=store).encode_many(payloads)
        self.requests = []
        self.cleaned = []
        self.split_limit = None
        self.corrupt_transfer = False

    def export_public_payloads(self, hashes):
        self.requests.append(hashes)
        if self.split_limit and len(hashes) > self.split_limit:
            raise PublicPayloadBundleLimitError("split required")
        path = self.root / uuid.uuid4().hex / "payloads.db"
        with PayloadReader(self.public_db) as reader:
            manifest = export_bundle(reader, hashes, path)
        return {
            "bundle_path": str(path),
            "manifest": manifest,
            "source_identity": {
                "db_path": str(self.public_db),
                "storage_root": str(self.root),
                "device": 1,
                "inode": 2,
            },
        }

    def rsync(self, *, remote_path, local_path, compress):
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(remote_path, local_path)
        if self.corrupt_transfer and Path(remote_path).name == "payloads.db":
            with local_path.open("ab") as handle:
                handle.write(b"corruption")

    def cleanup_public_payloads(self, path, expected_sha):
        self.cleaned.append((path, expected_sha))

    def snapshot_database(self, remote_path, **kwargs):
        return {
            "snapshot": remote_path,
            "sha256": sha256(Path(remote_path)),
            "snapshot_size_bytes": Path(remote_path).stat().st_size,
        }

    def cleanup_snapshot(self, path):
        pass


def make_database(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE requests (raw_gzip BLOB, private_order TEXT)")
        connection.executemany(
            "INSERT INTO requests VALUES (?, ?)",
            [(value, "private-fixture-retained-locally") for value in values],
        )


def synchronize(config, remote, database, *, source_key="fixture-key"):
    return synchronize_database_closure(
        config,
        remote,
        database,
        strategy="golden-coconut",
        source_key=source_key,
        database_sha256=sha256(database),
        ensure_capacity=lambda _: None,
    )


def verify(config, database, descriptor=None, *, source_key="fixture-key"):
    return verify_database_closure(
        config,
        database,
        strategy="golden-coconut",
        source_key=source_key,
        database_sha256=sha256(database),
        descriptor=descriptor,
    )


def test_incremental_public_closure_keeps_private_columns_local(app_config, tmp_path):
    remote = LocalRemote(tmp_path / "remote-public", [b"public-one", b"public-two"])
    with local_payload_writer(app_config) as writer:
        writer.put_many([b"public-one"])
    database = app_config.incoming_root / "snapshot.db"
    make_database(database, remote.references)
    descriptor = synchronize(app_config, remote, database)
    assert remote.requests == [[remote.hashes[1]]]
    assert descriptor["payload_count"] == 2
    assert descriptor["source_identity"]["export_identity"]["db_path"] == str(remote.public_db)
    write_closure_descriptor(app_config, database, descriptor)
    assert verify(app_config, database) == descriptor
    with PayloadReader(app_config.public_store_path) as reader:
        assert reader.get_many(remote.hashes) == [b"public-one", b"public-two"]
        assert reader.stats()["payload_count"] == 2
    with sqlite3.connect(database) as connection:
        assert {row[0] for row in connection.execute("SELECT private_order FROM requests")} == {
            "private-fixture-retained-locally"
        }
    synchronize(app_config, remote, database)
    assert len(remote.requests) == 1
    assert len(remote.cleaned) == 1


def test_inline_snapshot_needs_no_remote_or_shared_store(app_config):
    database = app_config.incoming_root / "inline.db"
    make_database(database, [b"ordinary inline public receipt"])
    assert verify(app_config, database) is None
    descriptor = synchronize(app_config, object(), database)
    assert descriptor["payload_count"] == 0
    assert not app_config.public_store_path.exists()
    write_closure_descriptor(app_config, database, descriptor)
    assert verify(app_config, database)["raw_bytes"] == 0


def test_failed_remote_cleanup_is_reported_without_hiding_verified_import(app_config, tmp_path):
    remote = LocalRemote(tmp_path / "remote-cleanup", [b"book"])
    database = app_config.data_root / "cleanup-fixture.db"
    make_database(database, remote.references)

    def fail_cleanup(path, expected_sha):
        raise OSError("staging temporarily unavailable")

    remote.cleanup_public_payloads = fail_cleanup
    descriptor = synchronize(app_config, remote, database)
    assert descriptor["status"] == "VERIFIED"
    assert descriptor["remote_staging_cleanup_pending_count"] == 1
    assert descriptor["remote_staging_cleanup_pending_examples"][0]["error"] == "OSError"
    assert verify(app_config, database, descriptor)["payload_count"] == 1


def test_bundle_limit_splits_and_commits_complete_closure(app_config, tmp_path):
    remote = LocalRemote(tmp_path / "remote-public", [b"one", b"two", b"three"])
    remote.split_limit = 1
    database = app_config.incoming_root / "snapshot.db"
    make_database(database, remote.references)
    descriptor = synchronize(app_config, remote, database)
    assert any(len(batch) > 1 for batch in remote.requests)
    assert verify(app_config, database, descriptor)["payload_count"] == 3
    assert len(remote.cleaned) == 3


def test_corrupt_existing_payload_is_not_silently_refetched(app_config, tmp_path):
    remote = LocalRemote(tmp_path / "remote-public", [b"original"])
    with local_payload_writer(app_config) as writer:
        writer.put_many([b"original"])
    with sqlite3.connect(app_config.public_store_path) as connection:
        connection.execute("UPDATE payloads SET body=?", (b"corrupt",))
    database = app_config.incoming_root / "snapshot.db"
    make_database(database, remote.references)
    with pytest.raises(CorruptPayloadError):
        synchronize(app_config, remote, database)
    assert remote.requests == []


@pytest.mark.parametrize("change", ["source", "source_key", "database_sha256", "closure_sha256"])
def test_closure_attestation_is_bound_to_snapshot_and_source(app_config, tmp_path, change):
    remote = LocalRemote(tmp_path / "remote-public", [b"public"])
    database = app_config.incoming_root / "snapshot.db"
    make_database(database, remote.references)
    descriptor = synchronize(app_config, remote, database)
    descriptor[change] = "wrong"
    with pytest.raises(RuntimeError, match="attestation"):
        verify(app_config, database, descriptor)


def test_missing_payload_blocks_verify_and_pin_without_catalog_entry(app_config, tmp_path):
    remote = LocalRemote(tmp_path / "remote-public", [b"public"])
    database = app_config.incoming_root / "snapshot.db"
    make_database(database, remote.references)
    artifact = RemoteArtifact(
        "database_sim",
        "/remote/source.db",
        database.stat().st_size,
        1,
        "polybot-white",
        strategy="golden-coconut",
        source=app_config.ssh_host,
    )
    descriptor = synchronize(app_config, remote, database, source_key=artifact.source_key)
    write_closure_descriptor(app_config, database, descriptor)
    service = SyncService(app_config)
    service.catalog.upsert_artifact(
        artifact,
        source=app_config.ssh_host,
        local_path=database,
        local_sha256=sha256(database),
        remote_sha256=sha256(database),
    )
    with sqlite3.connect(app_config.public_store_path) as connection:
        connection.execute("DELETE FROM payloads")
    with pytest.raises(MissingPayloadError):
        service.pin_database(artifact.source_key)
    with service.catalog.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM pins").fetchone()[0] == 0
    assert service.verify(job="polybot-white", strategy="golden-coconut")["status"] == "FAILED"


def test_pin_records_closure_and_source_identity(app_config, tmp_path):
    remote = LocalRemote(tmp_path / "remote-public", [b"public"])
    database = app_config.incoming_root / "snapshot.db"
    make_database(database, remote.references)
    artifact = RemoteArtifact(
        "database_sim",
        "/remote/source.db",
        database.stat().st_size,
        1,
        "polybot-white",
        strategy="golden-coconut",
        source=app_config.ssh_host,
    )
    descriptor = synchronize(app_config, remote, database, source_key=artifact.source_key)
    write_closure_descriptor(app_config, database, descriptor)
    service = SyncService(app_config)
    service.catalog.upsert_artifact(
        artifact,
        source=app_config.ssh_host,
        local_path=database,
        local_sha256=sha256(database),
        remote_sha256=sha256(database),
    )
    pinned = service.pin_database(artifact.source_key)
    manifest = json.loads((pinned.parent / "manifest.json").read_text())
    assert manifest["public_payloads"] == descriptor
    assert manifest["source_identity"]["source"] == app_config.ssh_host
    assert verify(app_config, pinned, source_key=artifact.source_key)["payload_count"] == 1


def test_failed_bundle_transfer_preserves_existing_snapshot(app_config, tmp_path):
    remote = LocalRemote(tmp_path / "remote-public", [b"new public receipt"])
    remote.corrupt_transfer = True
    source = remote.root / "strategy.db"
    make_database(source, remote.references)
    artifact = RemoteArtifact(
        "database_sim",
        str(source),
        source.stat().st_size,
        1,
        "polybot-white",
        strategy="golden-coconut",
        source=app_config.ssh_host,
    )
    service = SyncService(app_config)
    service.remote = remote
    destination = service.local_path(artifact)
    make_database(destination, [b"old inline public receipt"])
    old_bytes = destination.read_bytes()
    plan = SyncPlan(
        "plan",
        datetime.now(UTC).isoformat(),
        app_config.ssh_host,
        "polybot-white",
        "golden-coconut",
        artifacts=[artifact],
    )
    with pytest.raises(ValueError, match="checksum"):
        service._sync_database(plan, artifact, "run")
    assert destination.read_bytes() == old_bytes
    assert not closure_sidecar(destination).exists()
    assert service.catalog.get_artifact(artifact.source_key) is None


def test_shared_symlink_escape_is_rejected_without_touching_target(app_config, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    app_config.public_store_path.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink"):
        with local_payload_writer(app_config):
            pass
    assert list(outside.iterdir()) == []


def test_missing_external_mount_is_not_recreated(app_config):
    config = replace(
        app_config,
        data_root=Path("/Volumes/absent-public-test/data"),
        require_external_data_root=True,
    )
    with pytest.raises(ValueError, match="not mounted"):
        with local_payload_writer(config):
            pass
    assert not config.data_root.exists()


def test_only_allowlisted_public_columns_enter_reference_closure(app_config):
    database = app_config.incoming_root / "private.db"
    digest = hashlib.sha256(b"private synthetic fixture").hexdigest()
    make_database(database, [b"inline"])
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE requests SET private_order=?", ("\x1ePMDATA1:T:" + digest,))
    assert reference_closure(database, "golden-coconut") == []


def test_writer_lock_excludes_an_independent_cli_process(app_config):
    program = """
from pathlib import Path
from unittest.mock import patch
import sys
from daily_rsync.config import AppConfig
from daily_rsync.public_payloads import local_payload_writer
config = AppConfig(project_root=Path(sys.argv[1]), data_root=Path(sys.argv[2]))
try:
    with patch('daily_rsync.public_payloads.time.monotonic', side_effect=[0.0, 10.0]):
        with local_payload_writer(config):
            raise AssertionError('second process acquired active writer lock')
except RuntimeError as error:
    assert 'busy' in str(error)
    print('BUSY')
"""
    with local_payload_writer(app_config) as writer:
        writer.put_many([b"existing"])
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                str(app_config.project_root),
                str(app_config.data_root),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert result.stdout.strip() == "BUSY"
        assert writer.stats()["payload_count"] == 1


def test_unchanged_snapshot_with_missing_closure_is_selected_for_repair(
    app_config,
    tmp_path,
    monkeypatch,
):
    remote = LocalRemote(tmp_path / "remote-public", [b"public"])
    service = SyncService(app_config)
    artifact = RemoteArtifact(
        "database_sim",
        "/remote/strategy/data/default/trades_sim.db",
        100,
        1,
        "polybot-white",
        strategy="golden-coconut",
        runtime_job="default",
        source=app_config.ssh_host,
        fingerprint="stable-source",
    )
    database = service.local_path(artifact)
    make_database(database, remote.references)
    descriptor = synchronize(app_config, remote, database, source_key=artifact.source_key)
    write_closure_descriptor(app_config, database, descriptor)
    service.catalog.upsert_artifact(
        artifact,
        source=app_config.ssh_host,
        local_path=database,
        local_sha256=sha256(database),
        remote_sha256=sha256(database),
    )
    inventory = JobInventory(
        artifact.jenkins_job,
        "/remote/workspace",
        0,
        None,
        None,
        "golden-coconut",
        ("golden-coconut",),
        (artifact,),
        1 << 40,
        workspace_identity={"root_path": "/remote/workspace"},
    )
    monkeypatch.setattr(service, "scan", lambda **_: [inventory])
    initial = service.create_plan(job="polybot-white", strategy="golden-coconut")
    assert initial.artifacts == []
    with sqlite3.connect(app_config.public_store_path) as connection:
        connection.execute("DELETE FROM payloads")
    repair = service.create_plan(job="polybot-white", strategy="golden-coconut")
    assert [item.source_key for item in repair.artifacts] == [artifact.source_key]


def test_sports_recorder_uses_scoped_reader_without_environment_fallback(
    app_config,
    tmp_path,
    monkeypatch,
):
    from daily_rsync.sports_recorder import export_group

    with local_payload_writer(app_config) as writer:
        digest = writer.put_many([b"public gzip body"])[0]
        encoded = PayloadReferences(reader=writer, writer=writer).encode_many([b"public gzip body"])
    monkeypatch.setenv("PUBLIC_MARKET_DATA_DB", str(tmp_path / "unrelated-missing.db"))
    captured = []

    def export_scoped(*args):
        references = args[-1]
        captured.append(references)
        assert references.reader.path == app_config.public_store_path
        return references.decode_many(encoded)

    monkeypatch.setattr("daily_rsync.sports_recorder._export_group", export_scoped)
    assert export_group(None, None, [], None, None, None, None, app_config.data_root) == [
        b"public gzip body"
    ]
    with pytest.raises(sqlite3.ProgrammingError):
        captured[0].reader.get_many([digest])
