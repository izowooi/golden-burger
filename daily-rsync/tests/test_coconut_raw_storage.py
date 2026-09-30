"""Historical Coconut is admitted by its explicit v6 schema, never its strategy name."""

from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from polybot_observability.market_data_raw_links import initialize_raw_links, insert_raw_rows
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_raw_profiles import raw_profile
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_raw_storage_migrations import RawRemote

from daily_rsync import remote_agent
from daily_rsync.models import JobInventory, RemoteArtifact
from daily_rsync.public_payloads import closure_sidecar
from daily_rsync.sync import SyncService, sha256

PROFILE_ID = "coconut-historical-v6"
DAY = date(2026, 8, 27)


class CoconutRemote(RawRemote):
    def scan(self, **_):
        strategy, runtime, mode, contract, day = remote_agent.database_identity(self.path)
        record = remote_agent.stat_record(
            self.path,
            "database_research_archive",
            self.job,
            strategy=strategy,
            runtime_job=runtime,
            canonical=False,
            mode=mode,
            archive_date=DAY,
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
    remote = CoconutRemote(tmp_path / "remote", strategy="golden-coconut")
    remote.job, remote.runtime = "polybot-gold", "coconut-major-sports-lifecycle-5m-v7"
    remote.path = (
        remote.root
        / remote.job
        / remote.strategy
        / "data"
        / remote.runtime
        / "trades_sim_20260827.db"
    )
    return remote


def defaults(db, table):
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


def insert(db, table, row, rowid=None):
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
    profile = raw_profile(PROFILE_ID)
    remote.path.parent.mkdir(parents=True)
    with PayloadStore(remote.public_db) as store:
        refs = PayloadReferences(store, store)
        with connect(remote.path, references=refs) as db:
            db.execute("PRAGMA foreign_keys=ON")
            for kind in ("table", "index", "trigger", "view"):
                for obj_kind, _, _, sql in profile.source_objects:
                    if obj_kind == kind:
                        db.execute(sql)
            db.execute("PRAGMA application_id=" + str(profile.application_id))
            db.execute("PRAGMA user_version=" + str(profile.user_version))
            db.execute(
                "INSERT INTO collection_contracts VALUES(1,?,?)",
                ("research-full-v1", DAY.isoformat()),
            )
            cycle = defaults(db, "collection_cycles")
            cycle.update(cycle_id="cycle", run_id="run", mode="sim", job_name=remote.runtime)
            insert(db, "collection_cycles", cycle)
            payload = defaults(db, "raw_payloads")
            payload.update(
                raw_payload_id="payload",
                cycle_id="cycle",
                payload_gzip=gzip.compress(b"{}", mtime=0),
            )
            insert(db, "raw_payloads", payload)
            if native:
                initialize_raw_links(
                    db,
                    namespace,
                    store.scalar_authority_identity(),
                    references=refs,
                    profile_id=PROFILE_ID,
                )
            for index, (table, t) in enumerate(profile.tables.items()):
                if table == "threshold_episodes":
                    vector = defaults(db, "threshold_vectors")
                    vector.update(
                        threshold_vector_id="vector",
                        cycle_id="cycle",
                        book_snapshot_id="book_snapshots-row",
                    )
                    insert(db, "threshold_vectors", vector)
                row = {
                    name: (
                        0
                        if affinity == "INTEGER"
                        else 0.8
                        if affinity == "REAL"
                        else b"{}"
                        if affinity == "BLOB"
                        else "{}"
                        if name.endswith("_json")
                        else "source"
                    )
                    for _, name, affinity, *_ in t.info
                }
                updates = {
                    "cycle_id": "cycle",
                    "run_id": "run",
                    "sweep_id": None,
                    "raw_payload_id": "payload",
                    "event_observation_id": "event-row",
                    "threshold_vector_id": "vector",
                    "entry_book_snapshot_id": "book_snapshots-row",
                    "book_snapshot_id": "book_snapshots-row",
                    "anchor_role": "PRESTART_CANDIDATE",
                    "path_status": "FULL",
                    "market_observation_id": "market-row",
                    "event_id": "MISSING:fixture-generated",
                    "condition_id": "condition",
                    "token_id": "token",
                    "source_kind": (
                        "GAMMA_FALLBACK"
                        if table == "sports_clock_observations"
                        else "GAMMA_DISCOVERY"
                        if table == "game_lifecycle_observations"
                        else "DISCOVERY"
                    ),
                    "matched_by": "SAME_CYCLE_GAMMA",
                    "season_phase": "REGULAR",
                    "lifecycle_state": "IN_PLAY",
                    "classification_status": "ACCEPTED",
                    "sport_family": "mlb",
                    "observed_at": "2026-08-27T20:00:00Z",
                    "source_timestamp": "1790722800000",
                    "normalized_json": '{"volume_num":42.0,"private_decision":"KEEP_LOCAL"}',
                    "book_gzip": gzip.compress(
                        b'{"bids":[{"price":"0.8","size":"20"}],"asks":[{"price":"0.9","size":"20"}]}',
                        mtime=0,
                    ),
                }
                row.update({k: v for k, v in updates.items() if k in row})
                # Parent IDs remain stable; every other original PK gets its own ID.
                for key in t.primary_key_columns:
                    if key not in {"event_observation_id", "market_observation_id"}:
                        row[key] = table + "-row"
                rowid = -7 if index == 0 else 0 if index == 1 else index * 10 + 1
                if native:
                    insert_raw_rows(
                        db,
                        profile.strategy,
                        table,
                        [{**row, "__rowid__": rowid}],
                        references=refs,
                        namespace=namespace,
                    )
                else:
                    insert(db, table, row, rowid)


def test_historical_coconut_profile_mirror():
    profile = raw_profile(PROFILE_ID)
    mirror = remote_agent.RAW_PROFILE_SCHEMAS[PROFILE_ID]
    assert mirror["strategy"] == profile.strategy
    assert mirror["version"] == profile.version
    assert mirror["logical_schema_sha256"] == profile.logical_schema_sha256
    assert mirror["kinds"] == {table: (p.kind,) for table, p in profile.tables.items()}


def test_coconut_native_archive_sync_verify_pin_and_full_book(app_config, tmp_path):
    remote = remote_fixture(tmp_path)
    namespace = scalar_namespace(app_config.ssh_host, remote.job, remote.strategy, remote.runtime)
    seed(remote, namespace, native=True)
    service = SyncService(app_config)
    service.remote = remote
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    assert plan.artifacts[0].kind == "database_research_archive"
    result = service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert service.verify(job=remote.job)["status"] == "SUCCESS"
    pin = service.pin_database(plan.artifacts[0].source_key)
    closure = json.loads(closure_sidecar(pin).read_text())["public_projection_records"]
    assert closure["raw_profile_id"] == PROFILE_ID and closure["receipt_count"] == 15
    # Shared source records deduplicate across the two lifecycle/episode tables,
    # while each original table/row still has its separate receipt.
    assert closure["record_count"] == 13
    with PayloadReader(app_config.public_store_path) as reader:
        with sqlite3.connect(pin) as physical:
            record_id = physical.execute(
                "SELECT _public_record_id FROM event_observations"
            ).fetchone()[0]
        (event_record,) = reader.get_projection_records([record_id], closure["authority_uuid"])
        assert "MISSING:fixture-generated" not in event_record.projection.values
        with connect(pin, references=PayloadReferences(reader=reader)) as db:
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
            book = db.execute("SELECT book_gzip FROM book_snapshots").fetchone()[0]
            assert json.loads(gzip.decompress(book))["bids"][0]["price"] == "0.8"
            assert db.execute(
                "SELECT period_raw,elapsed_raw,score_raw FROM sports_clock_observations"
            ).fetchone() == ("source", "source", "source")
            assert (
                json.loads(
                    db.execute("SELECT normalized_json FROM event_observations").fetchone()[0]
                )["private_decision"]
                == "KEEP_LOCAL"
            )


@pytest.fixture(params=[False, True], ids=["inline", "private-dictionary"])
def transition(app_config, tmp_path, request):
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
            profile_id=PROFILE_ID,
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


def test_coconut_full_profile_archive_derivative_lineage(transition):
    m = transition
    before = m.old_pin.read_bytes()
    plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    assert plan.artifacts[0].storage_migration["manifest"]["raw_profile_id"] == PROFILE_ID
    result = m.service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert m.service.verify(job=m.remote.job)["status"] == "SUCCESS"
    pin = m.service.pin_database(m.artifact.source_key)
    assert pin != m.old_pin and m.old_pin.read_bytes() == before
    lineage = json.loads((pin.parent / "manifest.json").read_text())["storage_lineage"]
    assert lineage["public_projection_records"]["raw_profile_id"] == PROFILE_ID
    assert lineage["source_snapshot_link"]["physical_byte_equality_claimed"] is False


def test_current_coconut_recorder_not_admitted_as_historical(tmp_path):
    from polybot_observability.market_data_raw_links import validate_raw_source_schema

    path = tmp_path / "white-recorder.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA application_id=" + str(0x43535231))
        db.execute("PRAGMA user_version=1")
        db.execute("CREATE TABLE event_observations(run_id,event_id,event_json)")
        with pytest.raises(ValueError, match="application/schema version"):
            validate_raw_source_schema(db, profile_id=PROFILE_ID)


def test_coconut_native_unused_check_literal_change_is_not_normalized_away(app_config, tmp_path):
    remote = remote_fixture(tmp_path)
    namespace = scalar_namespace(app_config.ssh_host, remote.job, remote.strategy, remote.runtime)
    seed(remote, namespace, native=True)
    with sqlite3.connect(remote.path) as db:
        sql = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='event_observations'"
        ).fetchone()[0]
        assert "'FOLLOWUP'" in sql
        # DISCOVERY rows remain valid; quick_check alone cannot detect the changed
        # future-write contract. A SQL normalizer must preserve string literals.
        db.execute("PRAGMA writable_schema=ON")
        db.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='event_observations'",
            (sql.replace("'FOLLOWUP'", "'followup'"),),
        )
        version = db.execute("PRAGMA schema_version").fetchone()[0]
        db.execute("PRAGMA schema_version=" + str(version + 1))
        db.execute("PRAGMA writable_schema=OFF")
    with sqlite3.connect(remote.path) as db:
        assert db.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    service = SyncService(app_config)
    service.remote = remote
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    result = service.execute(plan)
    assert result.status == "FAILED", result.errors
    assert not service.local_path(plan.artifacts[0]).exists()
