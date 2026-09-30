from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from polybot_observability.market_data_index import ReceiptContext
from polybot_observability.market_data_migrate import migrate_public_bodies
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_bundle import export_scalar_bundle
from polybot_observability.market_data_scalar_links import LINK_TABLE
from polybot_observability.market_data_scalars import MissingScalarRecordError, ScalarReceipt
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_public_payloads import LocalRemote

from daily_rsync import remote_agent
from daily_rsync.models import JobInventory, RemoteArtifact
from daily_rsync.public_payloads import (
    closure_sidecar,
    local_payload_writer,
    verify_database_closure,
)
from daily_rsync.sync import SyncService, sha256


class ScalarRemote(LocalRemote):
    def __init__(self, root, strategy="golden-date"):
        super().__init__(root, [])
        self.scalar_requests = []
        self.strategy = strategy
        self.path = root / f"polybot-red/{strategy}/data/default/trades_sim.db"
        self.job = "polybot-red"

    def export_public_scalars(
        self, ids, *, authority_uuid, transferred_ids=None, receipt_after=None
    ):
        self.scalar_requests.append((list(ids), list(transferred_ids), receipt_after))
        output = self.root / uuid.uuid4().hex / "payloads.db"
        with PayloadReader(self.public_db) as reader:
            manifest = export_scalar_bundle(
                reader,
                ids,
                output,
                authority_uuid=authority_uuid,
                transferred_ids=transferred_ids,
                receipt_after=receipt_after,
            )
        return {
            "bundle_path": str(output),
            "manifest": manifest,
            "source_identity": {
                "db_path": str(self.public_db),
                "storage_root": str(self.root),
                "device": 1,
                "inode": 2,
            },
        }

    def scan(self, **_):
        record = remote_agent.stat_record(
            self.path,
            "database_sim",
            self.job,
            strategy=self.strategy,
            runtime_job="default",
            canonical=True,
            mode="sim",
        )
        return [
            JobInventory(
                name=self.job,
                workspace=str(self.root),
                workspace_identity={"fixture": True},
                build_count=0,
                min_build=None,
                max_build=None,
                current_strategy=self.strategy,
                strategies=(self.strategy,),
                artifacts=(RemoteArtifact.from_dict(record),),
                remote_free_bytes=10**12,
            )
        ]

    def validate_workspace(self, **_):
        return {"validated": True}

    def snapshot_database(self, source, **_):
        result = []
        old_emit = remote_agent.emit
        try:
            remote_agent.emit = result.append
            remote_agent._snapshot_database_source(
                SimpleNamespace(
                    staging_root=str(self.root / "staging"),
                    expected_data_contract=None,
                    expected_database_utc_date=None,
                ),
                Path(source),
            )
        finally:
            remote_agent.emit = old_emit
        return result[0]


def source_database(path, count):
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,condition_id TEXT NOT NULL,
              probability REAL NOT NULL,liquidity REAL,volume_24h REAL,timestamp DATETIME);
            CREATE INDEX snapshot_condition ON market_snapshots(condition_id);
            CREATE TABLE trades(id INTEGER PRIMARY KEY,private_account TEXT,private_pnl REAL);
            INSERT INTO trades VALUES(7,'PRIVATE_ACCOUNT_SENTINEL',12.5);
        """)
        connection.executemany(
            "INSERT INTO market_snapshots VALUES(?,?,?,?,?,?)",
            [
                (17 + index, "condition-a", 0.75, None, 12.0, "2026-09-01 00:00:00.000000")
                for index in range(count)
            ],
        )


@pytest.fixture
def scalar_sync(app_config, tmp_path, request):
    return prepare_scalar_sync(app_config, tmp_path, count=getattr(request, "param", 3))


def prepare_scalar_sync(app_config, tmp_path, *, count=3, strategy="golden-date"):
    remote = ScalarRemote(tmp_path / "remote-scalar", strategy=strategy)
    source_database(remote.path, count)
    service = SyncService(app_config)
    service.remote = remote
    initial = service.create_plan(job=remote.job, strategy=strategy)
    result = service.execute(initial)
    assert result.status == "SUCCESS", result.errors
    artifact = initial.artifacts[0]
    destination = service.local_path(artifact)
    old_row = service.catalog.get_artifact(artifact.source_key)
    old_pin = service.pin_database(artifact.source_key)
    state = remote_agent.sqlite_source_state(remote.path)
    original = remote.root / "preserved-original.db"
    shutil.copyfile(remote.path, original)
    target = remote.root / "derivative.db"
    with PayloadStore(remote.public_db) as store:
        manifest = migrate_public_bodies(
            original,
            target,
            strategy=strategy,
            source_sha256=sha256(original),
            references=PayloadReferences(store, store),
            include_scalars=True,
            receipt_context=ReceiptContext(app_config.ssh_host, "default", remote.job),
        )
    manifest.update(
        source_snapshot_sha256=old_row["local_sha256"],
        source_file_sha256=sha256(original),
        original_source_path=str(remote.path),
        original_source_fingerprint=state["fingerprint"],
        original_source_members=state["members"],
    )
    shutil.copyfile(target, remote.path)
    sidecar = Path(str(remote.path) + ".storage-migration.json")
    sidecar.write_text(json.dumps(manifest))
    plan = service.create_plan(job=remote.job, strategy=strategy)
    return SimpleNamespace(
        service=service,
        remote=remote,
        original=original,
        target=target,
        artifact=artifact,
        destination=destination,
        manifest=manifest,
        plan=plan,
        old_pin=old_pin,
        old_row=old_row,
        sidecar=sidecar,
        count=count,
    )


def test_remote_scalar_strategy_mirror_matches_reviewed_adapters():
    from polybot_observability.market_data_scalar_links import STRATEGIES

    assert remote_agent.SCALAR_STRATEGIES == STRATEGIES


@pytest.mark.parametrize("strategy", sorted(remote_agent.SCALAR_STRATEGIES))
def test_each_reviewed_scalar_family_syncs_and_pins_default_runtime(app_config, tmp_path, strategy):
    from polybot_observability.market_data_sqlite import connect

    m = prepare_scalar_sync(app_config, tmp_path, strategy=strategy)
    result = m.service.execute(m.plan)
    assert result.status == "SUCCESS", result.errors
    assert m.service.verify(job=m.remote.job, strategy=strategy)["status"] == "SUCCESS"
    pin = m.service.pin_database(m.artifact.source_key)
    manifest = json.loads((pin.parent / "manifest.json").read_text())
    closure = manifest["public_payloads"]["public_scalar_records"]
    assert closure["route_identity_verified"] is True
    assert closure["route_identity"]["strategy"] == strategy
    assert closure["route_identity"]["runtime_job"] == "default"
    assert closure["receipt_count"] == 3
    with PayloadReader(m.service.config.public_store_path) as reader:
        connection = connect(pin, references=PayloadReferences(reader=reader))
        try:
            assert connection.execute("SELECT id,probability FROM market_snapshots ORDER BY id").fetchall() == [
                (17, 0.75), (18, 0.75), (19, 0.75),
            ]
            assert connection.execute("SELECT * FROM trades").fetchall() == [
                (7, "PRIVATE_ACCOUNT_SENTINEL", 12.5),
            ]
        finally:
            connection.close()
    with sqlite3.connect(pin) as raw:
        assert raw.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 0


@pytest.mark.parametrize("scalar_sync", [0, 3], indirect=True)
def test_scalar_only_snapshot_scan_sync_verify_pin_requires_shared_store(scalar_sync):
    m = scalar_sync
    old_pin = m.old_pin.read_bytes()
    result = m.service.execute(m.plan)
    assert result.status == "SUCCESS", result.errors
    descriptor = json.loads(closure_sidecar(m.destination).read_text())
    assert descriptor["payload_count"] == 0
    assert descriptor["shared_store"] == "shared-market-data/public.db"
    scalar = descriptor["public_scalar_records"]
    assert scalar["record_count"] == bool(m.count)
    assert scalar["receipt_count"] == m.count
    assert scalar["authority_uuid"] == m.manifest["scalar_authority_uuid"]
    assert scalar["route_identity_verified"] is True
    assert scalar["route_identity"]["runtime_job"] == "default"
    assert m.remote.scalar_requests[0] == ([], [], None)
    assert m.service.verify(job=m.remote.job, strategy="golden-date")["status"] == "SUCCESS"
    pin = m.service.pin_database(m.artifact.source_key)
    manifest = json.loads((pin.parent / "manifest.json").read_text())
    assert manifest["public_payloads"]["public_scalar_records"]["receipt_count"] == m.count
    assert manifest["storage_lineage"]["public_scalar_records"]["receipt_count"] == m.count
    assert pin != m.old_pin and m.old_pin.read_bytes() == old_pin
    assert (
        manifest["storage_lineage"]["source_snapshot_link"]["physical_byte_equality_claimed"]
        is False
    )
    assert m.service.config.public_store_path.is_file()
    assert m.service.create_plan(job=m.remote.job, strategy="golden-date").skipped_unchanged == 1


def remove_receipt(store, original_id):
    connection = store._connection
    connection.execute("DROP TRIGGER scalar_receipts_no_delete")
    connection.execute("DELETE FROM scalar_receipts WHERE original_id=?", (original_id,))
    connection.commit()


def test_existing_record_refreshes_missing_receipt_and_multiple_pages(scalar_sync, monkeypatch):
    from polybot_observability import market_data_scalar_bundle

    m = scalar_sync
    monkeypatch.setattr(market_data_scalar_bundle, "PAGE_SIZE", 2)
    assert m.service.execute(m.plan).status == "SUCCESS"
    assert len(m.remote.scalar_requests) == 3  # handshake + two pages for one repeated record.
    assert m.remote.scalar_requests[-1][1] == []
    with PayloadStore(m.service.config.public_store_path) as local:
        remove_receipt(local, 18)
    assert m.service.verify(job=m.remote.job, strategy="golden-date")["status"] == "FAILED"
    with PayloadStore(m.remote.public_db) as remote:
        record_id = remote._connection.execute("SELECT id FROM scalar_snapshots").fetchone()[0]
        remote.append_scalar_receipts(
            [ScalarReceipt("additional-public-source", 99, record_id)],
            authority_uuid=remote.scalar_authority_identity(),
        )
    before = len(m.remote.scalar_requests)
    plan = m.service.create_plan(job=m.remote.job, strategy="golden-date")
    assert len(plan.artifacts) == 1
    result = m.service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert all(not requested for _, requested, _ in m.remote.scalar_requests[before:])
    assert m.service.verify(job=m.remote.job, strategy="golden-date")["status"] == "SUCCESS"
    with PayloadReader(m.service.config.public_store_path) as reader:
        assert (
            reader.get_scalar_receipts(
                [("additional-public-source", 99, record_id)],
                authority_uuid=reader.scalar_authority_identity(),
            )[0]
            is not None
        )


@pytest.mark.parametrize("tamper", [
    "private", "private_ref", "record", "id", "namespace", "authority", "count",
])
def test_scalar_migration_rejects_forged_evidence_and_keeps_original(scalar_sync, tamper):
    m = scalar_sync
    original = m.destination.read_bytes()
    if tamper == "private_ref":
        # Synthetic preexisting public bytes ensure decoded equality succeeds;
        # the private physical-cell check must still reject the full-column ref.
        with local_payload_writer(m.service.config) as store:
            reference = PayloadReferences(store, store).encode_many(["PRIVATE_ACCOUNT_SENTINEL"])[0]
        with sqlite3.connect(m.remote.path) as connection:
            connection.execute("UPDATE trades SET private_account=?", (reference,))
        m.manifest["destination_sha256"] = sha256(m.remote.path)
    elif tamper in {"private", "record", "id", "namespace", "authority"}:
        with sqlite3.connect(m.remote.path) as connection:
            statement = {
                "private": "UPDATE trades SET private_account='changed'",
                "record": f"UPDATE {LINK_TABLE} SET record_id=999999",
                "id": f"UPDATE {LINK_TABLE} SET id=id+100",
                "namespace": "UPDATE _public_scalar_layout SET namespace='unknown'",
                "authority": "UPDATE _public_scalar_layout SET authority_uuid='unknown'",
            }[tamper]
            connection.execute(statement)
        m.manifest["destination_sha256"] = sha256(m.remote.path)
    else:
        m.manifest["public_scalar_records"]["receipt_count"] += 1
    m.sidecar.write_text(json.dumps(m.manifest))
    plan = m.service.create_plan(job=m.remote.job, strategy="golden-date")
    result = m.service.execute(plan)
    assert result.status == "FAILED", result.errors
    if tamper == "private_ref":
        assert any("unapproved reference in private cell" in error for error in result.errors)
        assert not m.remote.scalar_requests  # Reject before importing any public dependency.
    assert m.destination.read_bytes() == original
    assert (
        m.service.catalog.get_artifact(m.artifact.source_key)["local_sha256"]
        == m.old_row["local_sha256"]
    )


def test_scalar_closure_descriptor_cannot_disappear_when_body_count_is_zero(scalar_sync):
    m = scalar_sync
    assert m.service.execute(m.plan).status == "SUCCESS"
    closure_sidecar(m.destination).unlink()
    with pytest.raises(RuntimeError, match="attestation"):
        verify_database_closure(
            m.service.config,
            m.destination,
            strategy="golden-date",
            source_key=m.artifact.source_key,
            database_sha256=sha256(m.destination),
        )


def test_remote_scalar_projection_omits_unreviewed_fields(scalar_sync):
    m = scalar_sync
    m.manifest["public_scalar_records"]["private_rows"] = "PRIVATE_SENTINEL"
    m.manifest["private_config"] = "PRIVATE_SENTINEL"
    m.sidecar.write_text(json.dumps(m.manifest))
    projection = remote_agent.storage_migration_record(m.remote.path)
    assert "PRIVATE_SENTINEL" not in json.dumps(projection)
    assert (
        projection["manifest"]["tables"]["market_snapshots"]["key_basis"]
        == "declared INTEGER primary key"
    )


def test_ordinary_scalar_sync_rejects_wrong_runtime_before_public_transfer(scalar_sync):
    from polybot_observability.market_data_scalar_links import scalar_namespace

    m = scalar_sync
    original = m.destination.read_bytes()
    namespace = scalar_namespace(
        m.service.config.ssh_host, m.remote.job, "golden-date", "different-runtime"
    )
    with sqlite3.connect(m.remote.path) as connection:
        connection.execute("UPDATE _public_scalar_layout SET namespace=?", (namespace,))
        links = connection.execute(f"SELECT id,record_id FROM {LINK_TABLE}").fetchall()
    with PayloadStore(m.remote.public_db) as store:
        store.append_scalar_receipts(
            [ScalarReceipt(namespace, row_id, record_id) for row_id, record_id in links],
            authority_uuid=store.scalar_authority_identity(),
        )
    m.sidecar.unlink()
    plan = m.service.create_plan(job=m.remote.job, strategy="golden-date")
    assert plan.artifacts[0].storage_migration is None
    result = m.service.execute(plan)
    assert result.status == "FAILED"
    assert any("namespace differs" in error for error in result.errors)
    assert not m.remote.scalar_requests
    assert m.destination.read_bytes() == original


@pytest.mark.parametrize("damage", ["authority", "record"])
def test_local_scalar_store_damage_blocks_verify_and_pin(scalar_sync, damage):
    m = scalar_sync
    assert m.service.execute(m.plan).status == "SUCCESS"
    with PayloadStore(m.service.config.public_store_path) as store:
        connection = store._connection
        if damage == "authority":
            connection.execute("DROP TRIGGER scalar_authority_claim")
            connection.execute("UPDATE scalar_authority SET authority_uuid=?", (str(uuid.uuid4()),))
        else:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("DROP TRIGGER scalar_snapshots_no_delete")
            connection.execute("DELETE FROM scalar_snapshots")
        connection.commit()
    assert m.service.verify(job=m.remote.job, strategy="golden-date")["status"] == "FAILED"
    with pytest.raises((RuntimeError, ValueError, MissingScalarRecordError)):
        m.service.pin_database(m.artifact.source_key)


def test_dependency_only_verification_does_not_claim_artifact_route_verification(scalar_sync):
    m = scalar_sync
    assert m.service.execute(m.plan).status == "SUCCESS"
    result = verify_database_closure(
        m.service.config,
        m.destination,
        strategy="golden-date",
        source_key=m.artifact.source_key,
        database_sha256=sha256(m.destination),
    )
    assert result["public_scalar_records"]["route_identity_verified"] is False
    assert result["public_scalar_records"]["route_identity"] is None
