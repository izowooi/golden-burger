"""Actual RAW public store transfer and original-to-derivative pin lineage."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from polybot_observability.market_data_raw_links import (
    INDEX_SQL,
    RAW_TABLES,
    immutable_sql,
    initialize_raw_links,
    insert_raw_rows,
    iter_raw_logical_rows,
)
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore, StoreError
from test_projection_payloads import ProjectionRemote

from daily_rsync import remote_agent
from daily_rsync.models import JobInventory, RemoteArtifact
from daily_rsync.public_payloads import closure_sidecar
from daily_rsync.sync import SyncService, sha256


def seed(remote, namespace, *, native=False, with_levels=False):
    remote.path.parent.mkdir(parents=True)
    with PayloadStore(remote.public_db) as store:
        refs = PayloadReferences(store, store)
        with connect(remote.path, references=refs) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("CREATE TABLE market_sweeps(sweep_id TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE private_ledger(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO private_ledger VALUES(3, 'PRIVATE_RETAINED')")
            for table, profile in RAW_TABLES.items():
                db.execute(profile.source_sql)
                for op in ("UPDATE", "DELETE"):
                    db.execute(immutable_sql(table, op))
            for sql in INDEX_SQL.values():
                db.execute(sql)
            if with_levels:
                db.execute("""CREATE TABLE orderbook_levels (
                    level_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),
                    side TEXT NOT NULL CHECK (side IN ('BID','ASK')),
                    level_index INTEGER NOT NULL, price REAL NOT NULL, size REAL NOT NULL,
                    UNIQUE(snapshot_id,side,level_index))""")
                for operation in ("UPDATE", "DELETE"):
                    db.execute(immutable_sql("orderbook_levels", operation))
            db.execute("INSERT INTO market_sweeps VALUES('sweep')")
            if native:
                initialize_raw_links(
                    db, namespace, store.scalar_authority_identity(), references=refs
                )
            for rowid, (table, profile) in zip((-7, 0, 99), RAW_TABLES.items(), strict=True):
                row = {
                    name: (
                        0
                        if affinity == "INTEGER"
                        else 0.8
                        if affinity == "REAL"
                        else "{}"
                        if name.endswith("_json")
                        else "source"
                    )
                    for _, name, affinity, *_ in profile.info
                }
                row.update(__rowid__=rowid, **{profile.primary_key: table})
                for name, value in {
                    "sweep_id": "sweep",
                    "market_observation_id": "market_observations",
                    "condition_id": "condition",
                    "event_id": "event",
                    "token_id": "token",
                    "observed_at": "2026-09-29T23:00:00Z",
                    "source_timestamp": "1790722800000",
                    "normalized_json": '{"condition_id":"condition","fee_rate":0.02}',
                }.items():
                    if name in row:
                        row[name] = value
                if native:
                    insert_raw_rows(db, remote.strategy, table, [row], references=refs)
                else:
                    db.execute(
                        "INSERT INTO main."
                        + table
                        + "(rowid,"
                        + ",".join(profile.columns)
                        + ") VALUES("
                        + ",".join("?" for _ in row)
                        + ")",
                        (rowid, *(row[name] for name in profile.columns)),
                    )
            if with_levels:
                db.execute("INSERT INTO orderbook_levels(rowid,level_id,snapshot_id,side,level_index,price,size) "
                           "VALUES(-2,'level','orderbook_snapshots','BID',7,0.8,2.5)")


class RawRemote(ProjectionRemote):
    def scan(self, **_):
        strategy, runtime, mode, contract, day = remote_agent.database_identity(self.path)
        record = remote_agent.stat_record(
            self.path,
            "database_sim",
            self.job,
            strategy=strategy,
            runtime_job=runtime,
            canonical=True,
            mode=mode,
            data_contract=contract,
            database_utc_date=day,
        )
        return [
            JobInventory(
                name=self.job,
                workspace=str(self.root),
                workspace_identity={"fixture": True},
                build_count=0,
                min_build=None,
                max_build=None,
                current_strategy=strategy,
                strategies=(strategy,),
                artifacts=(RemoteArtifact.from_dict(record),),
                remote_free_bytes=10**12,
            )
        ]


def remote_fixture(tmp_path):
    remote = RawRemote(tmp_path / "remote", strategy="golden-black")
    remote.job, remote.runtime = "polybot-black", "black-raw"
    remote.path = (
        remote.root / remote.job / remote.strategy / "data" / remote.runtime / "trades_sim.db"
    )
    return remote


def test_native_raw_scan_sync_verify_pin_transfers_groups_and_mixed_bodies(app_config, tmp_path):
    remote = remote_fixture(tmp_path)
    namespace = scalar_namespace(app_config.ssh_host, remote.job, remote.strategy, remote.runtime)
    seed(remote, namespace, native=True)
    service = SyncService(app_config)
    service.remote = remote
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    result = service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert service.verify(job=remote.job)["status"] == "SUCCESS"
    pin = service.pin_database(plan.artifacts[0].source_key)
    closure = json.loads(closure_sidecar(pin).read_text())
    assert closure["payload_count"] > 0
    assert closure["public_projection_records"]["contract"] == "public-projection-closure-v3"
    assert closure["public_projection_records"]["receipt_count"] == 3
    with PayloadReader(app_config.public_store_path) as reader:
        with connect(pin, references=PayloadReferences(reader=reader)) as db:
            assert [r["__rowid__"] for r in iter_raw_logical_rows(db, "market_observations")] == [
                -7
            ]
            assert db.execute("SELECT value FROM private_ledger").fetchone() == (
                "PRIVATE_RETAINED",
            )
            assert db.execute("SELECT best_bid FROM orderbook_snapshots").fetchone() == (0.8,)
            assert not db.execute("PRAGMA foreign_key_check").fetchall()


@pytest.fixture
def raw_transition(app_config, tmp_path, request):
    remote = remote_fixture(tmp_path)
    namespace = scalar_namespace(app_config.ssh_host, remote.job, remote.strategy, remote.runtime)
    seed(remote, namespace, with_levels=getattr(request, "param", None) == "with-levels")
    service = SyncService(app_config)
    service.remote = remote
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    result = service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    artifact = plan.artifacts[0]
    old_row = service.catalog.get_artifact(artifact.source_key)
    old_pin = service.pin_database(artifact.source_key)
    state = remote_agent.sqlite_source_state(remote.path)
    original, target = remote.root / "original.db", remote.root / "derivative.db"
    shutil.copyfile(remote.path, original)
    with PayloadStore(remote.public_db) as store:
        from polybot_observability.market_data_raw_profiles import BLACK_PROFILE_ID

        explicit = getattr(request, "param", None) == "explicit-black-profile"
        manifest = migrate_raw_database(
            original,
            target,
            source_sha256=sha256(original),
            namespace=namespace,
            references=PayloadReferences(store, store),
            **({"profile_id": BLACK_PROFILE_ID} if explicit else {}),
        )
    manifest.update(
        source_snapshot_sha256=old_row["local_sha256"],
        source_file_sha256=sha256(original),
        original_source_path=str(remote.path),
        original_source_fingerprint=state["fingerprint"],
        original_source_members=state["members"],
    )
    shutil.copyfile(target, remote.path)
    sidecar = Path(str(remote.path) + ".raw-migration.json")
    sidecar.write_text(json.dumps(manifest))
    return SimpleNamespace(
        service=service,
        remote=remote,
        artifact=artifact,
        original=original,
        manifest=manifest,
        sidecar=sidecar,
        old_row=old_row,
        old_pin=old_pin,
        destination=service.local_path(artifact),
    )


@pytest.mark.parametrize(
    "raw_transition", ["legacy-default", "explicit-black-profile"], indirect=True
)
def test_raw_derivative_preserves_verified_original_and_source_generation(raw_transition):
    m = raw_transition
    original_pin = m.old_pin.read_bytes()
    plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    proposed = plan.artifacts[0].storage_migration["manifest"]
    assert proposed["contract"] == "black-raw-parent-derivative-v1"
    assert proposed["public_projection_records"]["contract"] == "public-projection-closure-v3"
    assert "raw_profile_id" not in proposed
    result = m.service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert m.service.verify(job=m.remote.job)["status"] == "SUCCESS"
    pin = m.service.pin_database(m.artifact.source_key)
    assert pin != m.old_pin and m.old_pin.read_bytes() == original_pin
    lineage = json.loads((pin.parent / "manifest.json").read_text())["storage_lineage"]
    assert lineage["public_projection_records"]["contract"] == "public-projection-closure-v3"
    assert lineage["source_snapshot_link"]["physical_byte_equality_claimed"] is False
    assert lineage["private_storage"]["private_ledger"]["rows"] == 1


@pytest.mark.parametrize("raw_transition", ["with-levels"], indirect=True)
def test_legacy_raw_present_numeric_levels_require_original_row_identity(raw_transition):
    m = raw_transition
    plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    claim = plan.artifacts[0].storage_migration["manifest"]["tables"]["orderbook_levels"]
    assert claim["externalized_level_rows"] == claim["rows"] == 1
    assert claim["key_basis"] == "declared-primary-key+rowid-v1"
    assert claim["implicit_rowid_preserved"] is True
    result = m.service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert m.service.verify(job=m.remote.job)["status"] == "SUCCESS"


@pytest.mark.parametrize("raw_transition", ["with-levels"], indirect=True)
@pytest.mark.parametrize("damage", ["missing_rowid", "false_rowid", "wrong_count", "wrong_key_basis"])
def test_legacy_raw_present_level_claim_cannot_skip_rowid_validation(raw_transition, damage):
    m = raw_transition
    before = m.destination.read_bytes(), m.old_pin.read_bytes()
    claim = m.manifest["tables"]["orderbook_levels"]
    if damage == "missing_rowid":
        del claim["implicit_rowid_preserved"]
    elif damage == "false_rowid":
        claim["implicit_rowid_preserved"] = False
    elif damage == "wrong_count":
        claim["externalized_level_rows"] += 1
    else:
        claim["key_basis"] = "declared primary key"
    m.sidecar.write_text(json.dumps(m.manifest))
    with pytest.raises(RuntimeError, match="RAW level|table key basis"):
        m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    assert (m.destination.read_bytes(), m.old_pin.read_bytes()) == before


@pytest.mark.parametrize("raw_transition", ["with-levels"], indirect=True)
def test_legacy_raw_omitted_existing_level_table_fails_full_local_proof(raw_transition):
    m = raw_transition
    before = m.destination.read_bytes(), m.old_pin.read_bytes()
    del m.manifest["tables"]["orderbook_levels"]
    del m.manifest["private_storage"]["orderbook_levels"]
    m.sidecar.write_text(json.dumps(m.manifest))
    plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    result = m.service.execute(plan)
    assert result.status == "FAILED", result.errors
    assert (m.destination.read_bytes(), m.old_pin.read_bytes()) == before


@pytest.mark.parametrize(
    "damage", ["private", "clock", "rowid", "receipt_count", "schema", "source_link"]
)
def test_raw_derivative_rejects_forged_evidence_and_preserves_latest(raw_transition, damage):
    m = raw_transition
    before, pin = m.destination.read_bytes(), m.old_pin.read_bytes()
    if damage == "receipt_count":
        m.manifest["public_projection_records"]["layout_receipt_counts"]["market_observations"] += 1
        m.manifest["public_projection_records"]["receipt_count"] += 1
    elif damage == "schema":
        m.manifest["target_schema_sha256"] = "f" * 64
    elif damage == "source_link":
        m.manifest["source_snapshot_sha256"] = "f" * 64
    else:
        with sqlite3.connect(m.remote.path) as db:
            if damage == "private":
                db.execute("UPDATE private_ledger SET value='FORGED'")
            else:
                db.execute("DROP TRIGGER market_observations_forbid_update")
                if damage == "clock":
                    db.execute("UPDATE market_observations SET observed_at='2099-01-01'")
                else:
                    db.execute("UPDATE market_observations SET rowid=107")
                db.execute(immutable_sql("market_observations", "UPDATE"))
        m.manifest.update(
            destination_sha256=sha256(m.remote.path), destination_bytes=m.remote.path.stat().st_size
        )
        m.manifest.update(
            target_sha256=m.manifest["destination_sha256"],
            target_bytes=m.manifest["destination_bytes"],
        )
    m.sidecar.write_text(json.dumps(m.manifest))
    plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    result = m.service.execute(plan)
    assert result.status == "FAILED", result.errors
    assert m.destination.read_bytes() == before and m.old_pin.read_bytes() == pin
    assert (
        m.service.catalog.get_artifact(m.artifact.source_key)["local_sha256"]
        == m.old_row["local_sha256"]
    )


def test_remote_raw_contract_mirror_matches_common():
    from polybot_observability.market_data_raw_links import CONTRACT

    assert remote_agent.RAW_LAYOUT_CONTRACT == CONTRACT
    assert remote_agent.RAW_KINDS == {
        "golden-black": {name: (profile.kind,) for name, profile in RAW_TABLES.items()}
    }


def test_raw_ambiguous_sidecars_are_rejected_before_transfer(raw_transition):
    m = raw_transition
    Path(str(m.remote.path) + ".storage-migration.json").write_text("{}")
    with pytest.raises(RuntimeError, match="ambiguous"):
        m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    assert not m.remote.projection_requests


@pytest.mark.parametrize("damage", ["missing_receipt", "missing_mixed_body"])
def test_raw_missing_dependency_blocks_verify_and_pin(app_config, tmp_path, damage):
    remote = remote_fixture(tmp_path)
    namespace = scalar_namespace(app_config.ssh_host, remote.job, remote.strategy, remote.runtime)
    seed(remote, namespace, native=True)
    service = SyncService(app_config)
    service.remote = remote
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    assert service.execute(plan).status == "SUCCESS"
    with sqlite3.connect(app_config.public_store_path) as db:
        if damage == "missing_receipt":
            from polybot_observability.market_data_projections import PROJECTION_TRIGGER_SQL

            db.execute("DROP TRIGGER projection_receipts_no_delete")
            db.execute("DELETE FROM projection_receipts WHERE original_id=-7")
            db.execute(PROJECTION_TRIGGER_SQL["projection_receipts_no_delete"])
        else:
            # Payloads have no immutable delete trigger; simulate actual storage loss.
            db.execute("DELETE FROM payloads")
    assert service.verify(job=remote.job)["status"] == "FAILED"
    with pytest.raises((RuntimeError, ValueError, StoreError)):
        service.pin_database(plan.artifacts[0].source_key)
