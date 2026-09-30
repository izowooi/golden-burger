from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
import uuid

import pytest
from polybot_observability.market_data_projection_bundle import export_projection_bundle
from polybot_observability.market_data_projection_links import (
    CONTEXT_TABLE, LAYOUT_TABLE, initialize_projection_links, insert_shared_projection_snapshot,
)
from polybot_observability.market_data_projection_profiles import snapshot_profile
from polybot_observability.market_data_projections import MissingProjectionRecordError, ProjectionReceipt
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore, StoreError, StoreLimitError
from test_public_payloads import LocalRemote

from daily_rsync import remote_agent
from daily_rsync.models import JobInventory, RemoteArtifact
from daily_rsync.projection_payloads import _missing_records
from daily_rsync.public_payloads import closure_sidecar, synchronize_database_closure, verify_database_closure
from daily_rsync.remote import PublicPayloadBundleLimitError
from daily_rsync.sync import SyncService, sha256


class ProjectionRemote(LocalRemote):
    def __init__(self, root, strategy="golden-blueberry"):
        super().__init__(root, [])
        self.strategy = strategy
        self.job = "polybot-eagle"
        self.runtime = "blueberry-live-a-2pp"
        self.path = root / self.job / strategy / "data" / self.runtime / "trades.db"
        self.projection_requests = []
        self.projection_limit = None
        self.projection_byte_limit = None
        self.changed_identity = False

    def export_public_projections(self, ids, *, authority_uuid, transferred_ids=None, receipt_after=None):
        self.projection_requests.append((list(ids), list(transferred_ids), receipt_after))
        if self.projection_limit is not None and len(ids) > self.projection_limit:
            raise PublicPayloadBundleLimitError("fixture bundle byte cap")
        output = self.root / uuid.uuid4().hex / "payloads.db"
        with PayloadReader(self.public_db) as reader:
            if self.projection_byte_limit is not None:
                values = reader.get_projection_records(ids, authority_uuid)
                if sum(row.projection.raw_bytes for row in values) > self.projection_byte_limit:
                    raise PublicPayloadBundleLimitError("fixture catalog byte cap")
            manifest = export_projection_bundle(reader, ids, output, authority_uuid=authority_uuid,
                                                transferred_ids=transferred_ids, receipt_after=receipt_after)
        return {"bundle_path": str(output), "manifest": manifest, "source_identity": {
            "db_path": str(self.public_db), "storage_root": str(self.root), "device": 1,
            "inode": 3 if self.changed_identity and len(self.projection_requests) > 1 else 2,
        }}

    def scan(self, **_):
        record = remote_agent.stat_record(self.path, "database_live", self.job,
                                           strategy=self.strategy, runtime_job=self.runtime,
                                           canonical=True, mode="live")
        return [JobInventory(name=self.job, workspace=str(self.root), workspace_identity={"fixture": True},
                             build_count=0, min_build=None, max_build=None, current_strategy=self.strategy,
                             strategies=(self.strategy,), artifacts=(RemoteArtifact.from_dict(record),),
                             remote_free_bytes=10**12)]

    def validate_workspace(self, **_):
        return {"validated": True}

    def snapshot_database(self, source, **_):
        results = []
        old_emit = remote_agent.emit
        try:
            remote_agent.emit = results.append
            remote_agent._snapshot_database_source(SimpleNamespace(
                staging_root=str(self.root / "staging"), expected_data_contract=None,
                expected_database_utc_date=None,
            ), Path(source))
        finally:
            remote_agent.emit = old_emit
        return results[0]


def create_schema(remote):
    remote.path.parent.mkdir(parents=True)
    columns = []
    profile = snapshot_profile(remote.strategy)
    for column in profile.columns:
        declaration = column.name + " " + column.affinity
        declaration += " PRIMARY KEY" if column.name == "id" else " NOT NULL" if not column.nullable else ""
        columns.append(declaration)
    with sqlite3.connect(remote.path) as connection:
        connection.execute("CREATE TABLE market_snapshots(" + ",".join(columns) + ")")
        connection.executescript("CREATE TABLE trades(id INTEGER PRIMARY KEY,private_account TEXT,private_pnl REAL);"
                                 "INSERT INTO trades VALUES(7,'PRIVATE_ACCOUNT_SENTINEL',12.5);")


def populate_native(remote, source, *, count=3, distinct=False, catalog_bytes=0):
    namespace = scalar_namespace(source, remote.job, remote.strategy, remote.runtime)
    with PayloadStore(remote.public_db) as store:
        references = PayloadReferences(reader=store, writer=store)
        connection = connect(remote.path, references=references)
        try:
            connection.execute("BEGIN")
            initialize_projection_links(connection, remote.strategy, namespace, store.scalar_authority_identity())
            for index in range(count):
                values = {name: None for name in snapshot_profile(remote.strategy).column_names}
                values.update(id=17 + index, condition_id="condition-a", probability=.75 + index * .01 if distinct else .75,
                              liquidity=None, volume_24h=12.0, best_bid=.74, best_ask=.76, spread=.02,
                              source_updated_at="2026-09-01T00:00:00Z", run_id="PRIVATE_RUN_SENTINEL",
                              timestamp=f"2026-09-01 00:00:0{index}.000000")
                if catalog_bytes:
                    values["catalog_tags_json"] = '[ "' + str(index) + "x" * catalog_bytes + '" ]\n'
                insert_shared_projection_snapshot(connection, remote.strategy, values,
                                                   namespace=namespace, references=references)
            connection.commit()
        finally:
            connection.close()


def prepare(app_config, tmp_path, *, count=3, distinct=False, native=True,
            strategy="golden-blueberry", catalog_bytes=0):
    remote = ProjectionRemote(tmp_path / "remote-projection", strategy=strategy)
    create_schema(remote)
    if native:
        populate_native(remote, app_config.ssh_host, count=count, distinct=distinct, catalog_bytes=catalog_bytes)
    service = SyncService(app_config)
    service.remote = remote
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    artifact = plan.artifacts[0]
    return SimpleNamespace(service=service, remote=remote, plan=plan, artifact=artifact,
                           destination=service.local_path(artifact), count=count)


def execute(m):
    result = m.service.execute(m.plan)
    assert result.status == "SUCCESS", result.errors
    return result


@pytest.mark.parametrize("count", [0, 3])
def test_fresh_native_blueberry_sync_verify_pin_requires_store_with_zero_body_scalar_rows(app_config, tmp_path, count):
    m = prepare(app_config, tmp_path, count=count)
    execute(m)
    descriptor = json.loads(closure_sidecar(m.destination).read_text())
    assert descriptor["payload_count"] == 0
    assert "public_scalar_records" not in descriptor
    assert descriptor["shared_store"] == "shared-market-data/public.db"
    projection = descriptor["public_projection_records"]
    assert projection["record_count"] == bool(count) and projection["receipt_count"] == count
    assert projection["route_identity_verified"] is True
    assert projection["route_identity"] == {
        "source": app_config.ssh_host, "jenkins_job": m.remote.job,
        "strategy": m.remote.strategy, "runtime_job": m.remote.runtime,
    }
    assert m.remote.projection_requests[0] == ([], [], None)
    assert m.service.verify(job=m.remote.job, strategy=m.remote.strategy)["status"] == "SUCCESS"
    pin = m.service.pin_database(m.artifact.source_key)
    manifest = json.loads((pin.parent / "manifest.json").read_text())
    assert manifest["public_payloads"]["public_projection_records"]["receipt_count"] == count
    with PayloadReader(app_config.public_store_path) as reader:
        assert reader.stats()["payload_count"] == reader.scalar_stats()["snapshot_count"] == 0
        with connect(pin, references=PayloadReferences(reader=reader)) as restored:
            assert restored.execute("SELECT id,probability FROM market_snapshots ORDER BY id").fetchall() == [
                (17 + index, .75) for index in range(count)]
            assert restored.execute("SELECT * FROM trades").fetchall() == [(7, "PRIVATE_ACCOUNT_SENTINEL", 12.5)]
            assert restored.execute("SELECT DISTINCT run_id FROM market_snapshots").fetchall() == (
                [("PRIVATE_RUN_SENTINEL",)] if count else [])
    with sqlite3.connect(pin) as raw:
        assert raw.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 0
        assert raw.execute(f"SELECT COUNT(*) FROM {CONTEXT_TABLE}").fetchone()[0] == count
    assert m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy).skipped_unchanged == 1


def delete_receipt(path, original_id):
    from polybot_observability.market_data_projections import PROJECTION_TRIGGER_SQL
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER projection_receipts_no_delete")
        connection.execute("DELETE FROM projection_receipts WHERE original_id=?", (original_id,))
        connection.execute(PROJECTION_TRIGGER_SQL['projection_receipts_no_delete'])


def test_existing_projection_refreshes_receipts_without_resending_values_and_pages(app_config, tmp_path, monkeypatch):
    from polybot_observability import market_data_projection_bundle
    monkeypatch.setattr(market_data_projection_bundle, "PAGE_SIZE", 2)
    m = prepare(app_config, tmp_path)
    execute(m)
    assert len(m.remote.projection_requests) == 3
    assert m.remote.projection_requests[-1][1] == []
    delete_receipt(app_config.public_store_path, 18)
    assert m.service.verify(job=m.remote.job, strategy=m.remote.strategy)["status"] == "FAILED"
    with pytest.raises((RuntimeError, ValueError)):
        m.service.pin_database(m.artifact.source_key)
    with PayloadStore(m.remote.public_db) as source:
        record_id = source._connection.execute("SELECT id FROM projection_records").fetchone()[0]
        source.append_projection_receipts([ProjectionReceipt("another-source", "market_snapshots", 99,
                                                             "gamma-quote-v1", record_id)],
                                           authority_uuid=source.scalar_authority_identity())
    offset = len(m.remote.projection_requests)
    m.plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    execute(m)
    assert all(not bodies for _, bodies, _ in m.remote.projection_requests[offset:])
    assert m.service.verify(job=m.remote.job, strategy=m.remote.strategy)["status"] == "SUCCESS"


@pytest.mark.parametrize("key,value", [("source", "wrong-source"), ("jenkins_job", "wrong-job"),
                                       ("runtime", "wrong-runtime")])
def test_wrong_artifact_route_is_rejected_before_public_transfer(app_config, tmp_path, key, value):
    m = prepare(app_config, tmp_path)
    namespace = {"source": app_config.ssh_host, "jenkins_job": m.remote.job,
                 "strategy": m.remote.strategy, "runtime": m.remote.runtime, key: value}
    with sqlite3.connect(m.remote.path) as connection:
        connection.execute(f"UPDATE {LAYOUT_TABLE} SET namespace=?", (scalar_namespace(**namespace),))
    m.plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    result = m.service.execute(m.plan)
    assert result.status == "FAILED" and any("namespace differs" in error for error in result.errors)
    assert not m.remote.projection_requests and not m.destination.exists()


@pytest.mark.parametrize("damage", ["authority", "record", "receipt"])
def test_local_projection_damage_blocks_verify_and_pin(app_config, tmp_path, damage):
    m = prepare(app_config, tmp_path)
    execute(m)
    with sqlite3.connect(app_config.public_store_path) as connection:
        if damage == "authority":
            connection.execute("DROP TRIGGER scalar_authority_claim")
            connection.execute("DROP TRIGGER projection_authority_no_rebind")
            connection.execute("UPDATE scalar_authority SET authority_uuid=?", (str(uuid.uuid4()),))
        elif damage == "record":
            connection.execute("DROP TRIGGER projection_hot_no_update")
            connection.execute("UPDATE projection_hot SET cells=x'00'")
        else:
            connection.execute("DROP TRIGGER projection_receipts_no_delete")
            connection.execute("DELETE FROM projection_receipts WHERE original_id=18")
    assert m.service.verify(job=m.remote.job, strategy=m.remote.strategy)["status"] == "FAILED"
    with pytest.raises((RuntimeError, ValueError, StoreError)):
        m.service.pin_database(m.artifact.source_key)


@pytest.mark.parametrize("failure", ["missing_remote_receipt", "corrupt_bundle", "changed_store_identity", "wrong_authority"])
def test_failed_dependencies_never_publish_native_snapshot(app_config, tmp_path, failure):
    m = prepare(app_config, tmp_path)
    if failure == "missing_remote_receipt":
        delete_receipt(m.remote.public_db, 18)
    elif failure == "corrupt_bundle":
        m.remote.corrupt_transfer = True
    elif failure == "changed_store_identity":
        m.remote.changed_identity = True
    else:
        with sqlite3.connect(m.remote.path) as connection:
            connection.execute(f"UPDATE {LAYOUT_TABLE} SET authority_uuid=?", (str(uuid.uuid4()),))
        m.plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    result = m.service.execute(m.plan)
    assert result.status == "FAILED", result.errors
    assert not m.destination.exists() and not closure_sidecar(m.destination).exists()


def test_projection_only_descriptor_cannot_disappear_and_dependency_helper_does_not_claim_route(app_config, tmp_path):
    m = prepare(app_config, tmp_path)
    execute(m)
    descriptor = verify_database_closure(app_config, m.destination, strategy=m.remote.strategy,
                                         source_key=m.artifact.source_key, database_sha256=sha256(m.destination))
    assert descriptor["public_projection_records"]["route_identity_verified"] is False
    assert descriptor["public_projection_records"]["route_identity"] is None
    closure_sidecar(m.destination).unlink()
    with pytest.raises(RuntimeError, match="attestation"):
        verify_database_closure(app_config, m.destination, strategy=m.remote.strategy,
                                source_key=m.artifact.source_key, database_sha256=sha256(m.destination))


def test_bundle_limit_splits_ids_but_never_bypasses_single_record_limit(app_config, tmp_path):
    m = prepare(app_config, tmp_path, distinct=True)
    m.remote.projection_limit = 2
    execute(m)
    assert any(len(ids) == 3 for ids, _, _ in m.remote.projection_requests)
    assert [len(ids) for ids, _, _ in m.remote.projection_requests] == [0, 3, 1, 2]
    m.remote.projection_limit = 0
    # Force a receipt refresh of an already-known one-record selection.
    closure_sidecar(m.destination).unlink()
    m.plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    result = m.service.execute(m.plan)
    assert result.status == "FAILED" and any("fixture bundle byte cap" in error for error in result.errors)


def test_large_catalog_values_split_only_record_selection_and_preserve_exact_pin_cells(app_config, tmp_path):
    m = prepare(app_config, tmp_path, count=2, distinct=True, strategy="golden-kiwi", catalog_bytes=65536)
    m.remote.projection_byte_limit = 96 << 10
    execute(m)
    assert [len(ids) for ids, _, _ in m.remote.projection_requests] == [0, 4, 2, 2]
    pin = m.service.pin_database(m.artifact.source_key)
    with PayloadReader(app_config.public_store_path) as reader:
        with connect(pin, references=PayloadReferences(reader=reader)) as restored:
            assert restored.execute("SELECT catalog_tags_json FROM market_snapshots ORDER BY id").fetchall() == [
                ('[ "' + str(index) + "x" * 65536 + '" ]\n',) for index in range(2)]
    descriptor = json.loads(closure_sidecar(m.destination).read_text())
    assert descriptor["public_projection_records"]["record_count"] == 4
    assert descriptor["public_projection_records"]["receipt_count"] == 4


def test_dependency_only_sync_does_not_claim_route_and_zero_layout_cannot_lose_store(app_config, tmp_path):
    m = prepare(app_config, tmp_path, count=0)
    dependency = app_config.incoming_root / "dependency.db"
    shutil.copyfile(m.remote.path, dependency)
    descriptor = synchronize_database_closure(
        app_config, m.remote, dependency, strategy=m.remote.strategy, source_key="dependency",
        database_sha256=sha256(dependency), ensure_capacity=lambda _: None,
    )
    assert descriptor["public_projection_records"]["route_identity_verified"] is False
    assert descriptor["public_projection_records"]["route_identity"] is None
    execute(m)
    app_config.public_store_path.unlink()
    assert m.service.verify(job=m.remote.job, strategy=m.remote.strategy)["status"] == "FAILED"
    with pytest.raises((RuntimeError, ValueError, FileNotFoundError)):
        m.service.pin_database(m.artifact.source_key)


def test_projection_transition_without_migration_proof_keeps_prior_inline_snapshot(app_config, tmp_path):
    m = prepare(app_config, tmp_path, native=False)
    execute(m)
    original = m.destination.read_bytes()
    populate_native(m.remote, app_config.ssh_host)
    m.plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    result = m.service.execute(m.plan)
    assert result.status == "FAILED" and any("original-to-derivative attestation" in error for error in result.errors)
    assert m.destination.read_bytes() == original and not m.remote.projection_requests


def test_availability_checks_split_catalog_reads_and_find_all_missing_records():
    seen = []
    class Reader:
        def get_projection_records(self, ids, authority):
            seen.append(list(ids))
            if len(ids) > 2:
                raise StoreLimitError("fixture catalog byte bound")
            missing = [value for value in ids if value in {1, 4}]
            if missing:
                raise MissingProjectionRecordError(missing)
            return [SimpleNamespace(reference=SimpleNamespace(record_id=value)) for value in ids]
    assert _missing_records(Reader(), [1, 2, 3, 4], "authority") == {1, 4}
    assert [1, 2, 3, 4] in seen and [3, 4] in seen
