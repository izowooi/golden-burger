"""Profiled recorder schema and PRIVATE local packet dictionaries travel in pins."""

from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from polybot_observability.market_data_raw_links import initialize_raw_links, insert_raw_rows
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_raw_profiles import WATERMELON_PROFILE_ID, raw_profile
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_raw_storage_migrations import RawRemote

from daily_rsync import remote_agent
from daily_rsync.public_payloads import closure_sidecar
from daily_rsync.sync import SyncService, sha256

PROFILE = raw_profile(WATERMELON_PROFILE_ID)


def remote_fixture(tmp_path):
    remote = RawRemote(tmp_path / "remote", strategy=PROFILE.strategy)
    remote.job, remote.runtime = "polybot-grey", "watermelon-historical"
    remote.path = (
        remote.root / remote.job / remote.strategy / "data" / remote.runtime / "trades_sim.db"
    )
    return remote


def _defaults(db, table):
    return {
        name: (
            0
            if "INT" in affinity
            else 0.8
            if affinity == "REAL"
            else b"{}"
            if affinity == "BLOB"
            else "{}"
            if name.endswith("_json")
            else "source"
        )
        for _, name, affinity, *_ in db.execute('PRAGMA main.table_info("' + table + '")')
    }


def _insert(db, table, row, rowid=None):
    columns, values = tuple(row), tuple(row.values())
    if rowid is not None:
        columns, values = ("rowid", *columns), (rowid, *values)
    db.execute(
        "INSERT INTO main."
        + table
        + "("
        + ",".join(columns)
        + ") VALUES("
        + ",".join("?" for _ in columns)
        + ")",
        values,
    )


def seed(remote, namespace, *, native=False):
    remote.path.parent.mkdir(parents=True)
    with PayloadStore(remote.public_db) as store:
        refs = PayloadReferences(store, store)
        with connect(remote.path, references=refs) as db:
            db.execute("PRAGMA foreign_keys=ON")
            for kind in ("table", "index", "trigger", "view"):
                for obj_kind, _, _, sql in PROFILE.source_objects:
                    if obj_kind == kind:
                        db.execute(sql)
            db.execute("PRAGMA application_id=" + str(PROFILE.application_id))
            db.execute("PRAGMA user_version=" + str(PROFILE.user_version))
            sweep = _defaults(db, "market_sweeps")
            sweep.update(sweep_id="sweep", run_id="run", cursor_complete=1)
            _insert(db, "market_sweeps", sweep)
            payload = _defaults(db, "raw_payloads")
            payload.update(payload_id="payload", payload_gzip=gzip.compress(b"{}", mtime=0))
            _insert(db, "raw_payloads", payload)
            if native:
                initialize_raw_links(
                    db,
                    namespace,
                    store.scalar_authority_identity(),
                    references=refs,
                    profile_id=PROFILE.profile_id,
                )
            for rowid, (table, profile) in zip(
                (-7, 0, 99, 101), PROFILE.tables.items(), strict=True
            ):
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
                changes = {
                    "sweep_id": "sweep",
                    "run_id": "run",
                    "source_payload_id": "payload",
                    "event_observation_id": "event-row",
                    "observation_id": "market-row",
                    "market_observation_id": "market-row",
                    "outcome_observation_id": "outcome-row",
                    "snapshot_id": "book-row",
                    "condition_id": "condition",
                    "token_id": "token",
                    "event_id": "event",
                    "classification_status": "ACCEPTED",
                    "observed_at": "2026-09-29T23:00:00Z",
                    "source_timestamp": "1790722800000",
                    "normalized_json": '{"condition_id":"condition","fee_rate":0.02}',
                }
                row.update({key: value for key, value in changes.items() if key in row})
                if native:
                    insert_raw_rows(
                        db,
                        PROFILE.strategy,
                        table,
                        [{**row, "__rowid__": rowid}],
                        references=refs,
                        namespace=namespace,
                    )
                else:
                    _insert(db, table, row, rowid)


def test_profiled_raw_remote_contract_mirror():
    expected = remote_agent.RAW_PROFILE_SCHEMAS[PROFILE.profile_id]
    assert expected["version"] == PROFILE.version
    assert expected["logical_schema_sha256"] == PROFILE.logical_schema_sha256
    assert expected["strategy"] == PROFILE.strategy
    assert expected["kinds"] == {table: (p.kind,) for table, p in PROFILE.tables.items()}


def test_native_profiled_raw_sync_verify_pin(app_config, tmp_path):
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
    closure = json.loads(closure_sidecar(pin).read_text())["public_projection_records"]
    assert closure["contract"] == "public-projection-closure-v4"
    assert closure["raw_profile_id"] == PROFILE.profile_id
    assert closure["receipt_count"] == 4
    with PayloadReader(app_config.public_store_path) as reader:
        with connect(pin, references=PayloadReferences(reader=reader)) as db:
            assert db.execute("SELECT best_bid FROM orderbook_snapshots").fetchone() == (0.8,)
            assert db.execute("SELECT fee_rate FROM market_observations").fetchone() == (0.8,)
            assert not db.execute("PRAGMA foreign_key_check").fetchall()


@pytest.fixture(params=[False, True], ids=["inline-source", "packet-dictionary-source"])
def profile_transition(app_config, tmp_path, request):
    remote = remote_fixture(tmp_path)
    namespace = scalar_namespace(app_config.ssh_host, remote.job, remote.strategy, remote.runtime)
    seed(remote, namespace, native=request.param)
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
        manifest = migrate_raw_database(
            original,
            target,
            source_sha256=sha256(original),
            namespace=namespace,
            references=PayloadReferences(store, store),
            profile_id=PROFILE.profile_id,
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
        manifest=manifest,
        sidecar=sidecar,
        old_pin=old_pin,
        old_row=old_row,
        destination=service.local_path(artifact),
    )


def test_profiled_raw_derivative_proves_original_generation(profile_transition):
    m = profile_transition
    old = m.old_pin.read_bytes()
    plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    assert plan.artifacts[0].storage_migration["manifest"]["raw_profile_id"] == PROFILE.profile_id
    result = m.service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert m.service.verify(job=m.remote.job)["status"] == "SUCCESS"
    pin = m.service.pin_database(m.artifact.source_key)
    assert pin != m.old_pin and m.old_pin.read_bytes() == old
    lineage = json.loads((pin.parent / "manifest.json").read_text())["storage_lineage"]
    assert lineage["public_projection_records"]["contract"] == "public-projection-closure-v4"
    assert lineage["source_snapshot_link"]["physical_byte_equality_claimed"] is False


def test_private_packet_dictionary_remains_in_private_pin(app_config, tmp_path):
    from polybot_observability.market_data_private_packets import (
        PACKET_TABLE,
        is_private_packet,
        iter_private_packet_records,
    )

    remote = remote_fixture(tmp_path)
    namespace = scalar_namespace(app_config.ssh_host, remote.job, remote.strategy, remote.runtime)
    seed(remote, namespace, native=True)
    with sqlite3.connect(remote.path) as source:
        marker = source.execute("SELECT normalized_json FROM market_observations").fetchone()[0]
        assert is_private_packet(marker)
        original_rows = source.execute("SELECT * FROM " + PACKET_TABLE + " ORDER BY id").fetchall()
        packets = list(
            iter_private_packet_records(source, strategy=remote.strategy, namespace=namespace)
        )
    assert packets
    service = SyncService(app_config)
    service.remote = remote
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    result = service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    pin = service.pin_database(plan.artifacts[0].source_key)
    with sqlite3.connect(pin) as private:
        assert (
            private.execute("SELECT * FROM " + PACKET_TABLE + " ORDER BY id").fetchall()
            == original_rows
        )
    with sqlite3.connect(app_config.public_store_path) as public:
        assert not public.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '_private_packet%'"
        ).fetchall()
        for packet in packets:
            assert not public.execute(
                "SELECT 1 FROM payloads WHERE sha256=?", (packet.sha256,)
            ).fetchone()
    with PayloadReader(app_config.public_store_path) as reader:
        with connect(pin, references=PayloadReferences(reader=reader)) as restored:
            value = restored.execute("SELECT normalized_json FROM market_observations").fetchone()[
                0
            ]
            assert json.loads(value)["fee_rate"] == 0.02


@pytest.mark.parametrize(
    "damage", ["missing_record", "foreign_owner", "corrupt_packet", "wrong_column"]
)
def test_profiled_raw_private_packet_damage_blocks_publication(app_config, tmp_path, damage):
    from polybot_observability.market_data_private_packets import LAYOUT_TABLE, PACKET_TABLE

    remote = remote_fixture(tmp_path)
    namespace = scalar_namespace(app_config.ssh_host, remote.job, remote.strategy, remote.runtime)
    seed(remote, namespace, native=True)
    with sqlite3.connect(remote.path) as db:
        marker = db.execute("SELECT normalized_json FROM market_observations").fetchone()[0]
        table = (
            LAYOUT_TABLE
            if damage == "foreign_owner"
            else "market_observations"
            if damage == "wrong_column"
            else PACKET_TABLE
        )
        operation = "DELETE" if damage == "missing_record" else "UPDATE"
        trigger = (
            table + ("_forbid_" if table == "market_observations" else "_no_") + operation.lower()
        )
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (trigger,)).fetchone()[0]
        db.execute("DROP TRIGGER " + trigger)
        if damage == "missing_record":
            db.execute("DELETE FROM " + PACKET_TABLE)
        elif damage == "foreign_owner":
            other = scalar_namespace(
                app_config.ssh_host, "another-job", remote.strategy, remote.runtime
            )
            db.execute("UPDATE " + LAYOUT_TABLE + " SET namespace=?", (other,))
        elif damage == "corrupt_packet":
            db.execute("UPDATE " + PACKET_TABLE + " SET body=x'00'")
        else:
            db.execute("UPDATE market_observations SET exclusion_reason=?", (marker,))
        db.execute(sql)
    service = SyncService(app_config)
    service.remote = remote
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    result = service.execute(plan)
    assert result.status == "FAILED", result.errors
    assert not service.local_path(plan.artifacts[0]).exists()
    with service.catalog.connect() as catalog:
        assert catalog.execute("SELECT COUNT(*) FROM pins").fetchone()[0] == 0


@pytest.mark.parametrize(
    "damage", [None, "profile", "version", "logical_schema", "strategy", "kind"]
)
def test_profiled_raw_remote_closure_rejects_unreviewed_profile(app_config, damage):
    namespace = scalar_namespace(app_config.ssh_host, "polybot-grey", PROFILE.strategy, "runtime")
    import uuid

    authority = str(uuid.uuid4())
    layouts = {table: [p.kind] for table, p in PROFILE.tables.items()}
    closure = {
        "contract": "public-projection-closure-v4",
        "namespace": namespace,
        "authority_uuid": authority,
        "record_count": 4,
        "receipt_count": 4,
        "raw_bytes": 100,
        "closure_sha256": "a" * 64,
        "layouts": layouts,
        "layout_receipt_counts": dict.fromkeys(layouts, 1),
        "kinds": sorted({p.kind for p in PROFILE.tables.values()}),
        "raw_layout_contract": "shared-raw-parent-skeleton-v2",
        "raw_source_schema_sha": "b" * 64,
        "raw_profile_id": PROFILE.profile_id,
        "raw_profile_version": PROFILE.version,
        "raw_logical_schema_sha256": PROFILE.logical_schema_sha256,
    }
    manifest = {
        "contract": "shared-raw-parent-derivative-v2",
        "strategy": PROFILE.strategy,
        "raw_profile_id": PROFILE.profile_id,
        "raw_profile_version": PROFILE.version,
        "raw_logical_schema_sha256": PROFILE.logical_schema_sha256,
        "projection_namespace": namespace,
        "projection_authority_uuid": authority,
        "public_projection_records": closure,
    }
    if damage == "profile":
        manifest["raw_profile_id"] = "unknown-profile"
    elif damage == "version":
        closure["raw_profile_version"] = True
    elif damage == "logical_schema":
        closure["raw_logical_schema_sha256"] = "f" * 64
    elif damage == "strategy":
        manifest["strategy"] = "golden-black"
    elif damage == "kind":
        closure["layouts"]["market_observations"] = ["black-market-source-v1"]
    if damage:
        with pytest.raises(RuntimeError):
            remote_agent._projection_migration_projection(manifest)
    else:
        assert (
            remote_agent._projection_migration_projection(manifest)["public_projection_records"]
            == closure
        )
