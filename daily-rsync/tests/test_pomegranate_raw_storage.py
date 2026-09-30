"""Pomegranate v4 scan/transfer/pin keeps raw identities and private evidence."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
import shutil
import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest

from polybot_observability.market_data_levels import insert_shared_levels, iter_level_logical_rows
from polybot_observability.market_data_raw_links import initialize_raw_links, insert_raw_rows
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_raw_profiles import POMEGRANATE_PROFILE_ID, raw_profile
from polybot_observability.market_data_refs import PayloadReferences, externalize_row
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_raw_storage_migrations import RawRemote

from daily_rsync import remote_agent
from daily_rsync.models import JobInventory, RemoteArtifact
from daily_rsync.public_payloads import closure_sidecar
from daily_rsync.sync import SyncService, sha256


PROFILE = raw_profile(POMEGRANATE_PROFILE_ID)
PARSE = '{"volume":{"raw_preview":"not a number","reason":"PRIVATE_PARSE","source_type":"str"}}'
PRIOR = '{"closed":1,"resolution_value_raw":"0.000","lookup_status":"PRIVATE_LOOKUP"}'


class PomegranateRemote(RawRemote):
    def scan(self, **_):
        strategy, runtime, mode, contract, day = remote_agent.database_identity(self.path)
        record = remote_agent.stat_record(
            self.path, "database_research_archive", self.job, strategy=strategy,
            runtime_job=runtime, canonical=False, mode=mode,
            archive_date=date(2026, 8, 6), data_contract=contract, database_utc_date=day,
        )
        return [JobInventory(
            name=self.job, workspace=str(self.root), workspace_identity={"fixture": True},
            build_count=0, min_build=None, max_build=None, current_strategy=strategy,
            strategies=(strategy,), artifacts=(RemoteArtifact.from_dict(record),),
            remote_free_bytes=10**12,
        )]


def remote_fixture(tmp_path):
    remote = PomegranateRemote(tmp_path / "remote", strategy=PROFILE.strategy)
    remote.job, remote.runtime = "polybot-pomegranate", "pomegranate-15m-v2"
    remote.path = remote.root / remote.job / remote.strategy / "data" / remote.runtime / "trades_sim_20260806.db"
    return remote


def insert(db, table, row, rowid=None):
    columns, values = tuple(row), tuple(row.values())
    if rowid is not None:
        columns, values = ("rowid", *columns), (rowid, *values)
    db.execute(
        "INSERT INTO main." + table + "(" + ",".join(columns) + ") VALUES("
        + ",".join("?" for _ in values) + ")", values,
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
            original_columns = {
                name: db.execute("PRAGMA main.table_info(" + name + ")").fetchall()
                for kind, name, _, _ in PROFILE.source_objects if kind == "table"
            }

            def row(table, **changes):
                values = {
                    name: 0 if affinity == "INTEGER" else .8 if affinity == "REAL"
                    else b"{}" if affinity == "BLOB" else "{}" if name.endswith("_json")
                    else "source"
                    for _, name, affinity, *_ in original_columns[table]
                }
                values.update({key: value for key, value in {
                    "sweep_id": "sweep", "run_id": "PRIVATE_RUN", "request_id": "request",
                    "observation_id": "observation", "selection_id": "selection",
                    "snapshot_id": "snapshot", "raw_payload_id": "payload",
                    "condition_id": "condition", "token_id": "token", "asset": "token",
                    "page_received_at": "2026-08-06T00:01:00Z",
                    "received_at": "2026-08-06T00:01:01Z", "observed_at": "2026-08-06T00:01:02Z",
                    "first_received_at": "2026-08-06T00:01:03Z",
                    "source_timestamp": "1790722800000", "timestamp_raw": "1790722800",
                    "question": "Exact public question?", "tags_json": '[{"slug":"sports"}]',
                    "price_raw": "0.8000", "size_raw": "3e1", "parse_quality_json": PARSE,
                    "prior_state_json": PRIOR, "status": "OBSERVED",
                    "first_observed_sweep_id": "sweep",
                    "sanitized_trade_json": '{"asset":"token","price":0.8,"size":30}',
                }.items() if key in values})
                values.update(changes)
                return values

            insert(db, "collection_contracts", row(
                "collection_contracts", contract_name="research-full-v1", schema_version=4,
                database_utc_date="2026-08-06", metadata_json='{"private":"KEEP_LOCAL"}',
            ))
            insert(db, "market_sweeps", row(
                "market_sweeps", cursor_complete=1, data_contract="research-full-v1",
            ))
            insert(db, "api_requests", row("api_requests", method="GET"))
            payload = row("raw_payloads", payload_id="payload", blob_stored=1,
                          content_encoding="gzip", payload_blob=gzip.compress(b'{"public":true}', mtime=0))
            insert(db, "raw_payloads", externalize_row(
                remote.strategy, "raw_payloads", payload, references=refs,
            ) if native else payload)
            if native:
                initialize_raw_links(db, namespace, store.scalar_authority_identity(),
                                     references=refs, profile_id=PROFILE.profile_id)
            for index, (table, spec) in enumerate(PROFILE.tables.items()):
                values = row(table)
                for key in spec.primary_key_columns:
                    if key not in {"observation_id", "selection_id", "snapshot_id", "condition_id"}:
                        values[key] = table + "-id"
                rowid = -7 if index == 0 else 0 if index == 1 else 19 * index
                if native:
                    insert_raw_rows(db, remote.strategy, table, [{**values, "__rowid__": rowid}],
                                    references=refs, namespace=namespace)
                else:
                    insert(db, table, values, rowid)
            levels = [row("orderbook_levels", level_id=None if index < 2 else "level",
                          side="ASK", level_index=index, price_raw="0.5100", price=.51,
                          size_raw="3e1", size=30., __rowid__=rowid)
                      for index, rowid in enumerate((-7, 0, 99))]
            if native:
                assert insert_shared_levels(db, remote.strategy, "orderbook_levels", levels,
                                            references=refs, preserve_rowid=True)
            else:
                for value in levels:
                    values = dict(value)
                    rowid = values.pop("__rowid__")
                    insert(db, "orderbook_levels", values, rowid)
            assert not db.execute("PRAGMA foreign_key_check").fetchall()


def service_for(config, remote):
    service = SyncService(config)
    service.remote = remote
    return service


def test_pomegranate_profile_mirror_includes_original_rowid_levels():
    mirror = remote_agent.RAW_PROFILE_SCHEMAS[PROFILE.profile_id]
    assert mirror["strategy"] == PROFILE.strategy
    assert mirror["version"] == PROFILE.version
    assert mirror["logical_schema_sha256"] == PROFILE.logical_schema_sha256
    assert mirror["kinds"] == {name: (t.kind,) for name, t in PROFILE.tables.items()}
    assert mirror["level_tables"] == PROFILE.level_tables


def assert_pin(config, pin):
    closure = json.loads(closure_sidecar(pin).read_text())
    assert closure["payload_count"] > 0
    assert closure["public_projection_records"]["raw_profile_id"] == PROFILE.profile_id
    assert closure["public_projection_records"]["receipt_count"] == 12
    with PayloadReader(config.public_store_path) as reader:
        refs = PayloadReferences(reader=reader)
        with connect(pin, references=refs) as db:
            assert db.execute("SELECT question,tags_json,parse_quality_json FROM market_observations").fetchone() == (
                "Exact public question?", '[{"slug":"sports"}]', PARSE,
            )
            assert db.execute("SELECT prior_state_json FROM resolution_watchlist").fetchone()[0] == PRIOR
            assert db.execute("SELECT metadata_json FROM collection_contracts").fetchone()[0] == '{"private":"KEEP_LOCAL"}'
            assert gzip.decompress(db.execute("SELECT payload_blob FROM raw_payloads").fetchone()[0]) == b'{"public":true}'
            levels = list(iter_level_logical_rows(db, PROFILE.strategy, "orderbook_levels",
                                                 references=refs, require_rowid=True))
            assert [(r["__rowid__"], r["level_id"], r["price_raw"], r["size_raw"])
                    for r in levels] == [(-7, None, "0.5100", "3e1"), (0, None, "0.5100", "3e1"), (99, "level", "0.5100", "3e1")]
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
        with sqlite3.connect(pin) as physical:
            assert "question" not in {r[1] for r in physical.execute("PRAGMA table_info(market_observations)")}
            assert physical.execute("SELECT COUNT(*) FROM orderbook_levels").fetchone()[0] == 0


def test_native_pomegranate_scan_sync_verify_pin(app_config, tmp_path):
    remote = remote_fixture(tmp_path)
    namespace = scalar_namespace(app_config.ssh_host, remote.job, remote.strategy, remote.runtime)
    seed(remote, namespace, native=True)
    service = service_for(app_config, remote)
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    result = service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert service.verify(job=remote.job)["status"] == "SUCCESS"
    assert_pin(app_config, service.pin_database(plan.artifacts[0].source_key))


@pytest.fixture(params=[False, True], ids=["inline-original", "shared-original"])
def transition(app_config, tmp_path, request):
    remote = remote_fixture(tmp_path)
    namespace = scalar_namespace(app_config.ssh_host, remote.job, remote.strategy, remote.runtime)
    seed(remote, namespace, native=request.param)
    service = service_for(app_config, remote)
    first = service.create_plan(job=remote.job, strategy=remote.strategy)
    result = service.execute(first)
    assert result.status == "SUCCESS", result.errors
    artifact = first.artifacts[0]
    old_row = service.catalog.get_artifact(artifact.source_key)
    old_pin = service.pin_database(artifact.source_key)
    original, target = remote.root / "original.db", remote.root / "derivative.db"
    state = remote_agent.sqlite_source_state(remote.path)
    shutil.copyfile(remote.path, original)
    with PayloadStore(remote.public_db) as store:
        manifest = migrate_raw_database(original, target, source_sha256=sha256(original),
                                        namespace=namespace, references=PayloadReferences(store, store),
                                        profile_id=PROFILE.profile_id, batch_rows=2)
    manifest.update(source_snapshot_sha256=old_row["local_sha256"], source_file_sha256=sha256(original),
                    original_source_path=str(remote.path), original_source_fingerprint=state["fingerprint"],
                    original_source_members=state["members"])
    shutil.copyfile(target, remote.path)
    sidecar = Path(str(remote.path) + ".raw-migration.json")
    sidecar.write_text(json.dumps(manifest))
    return SimpleNamespace(remote=remote, service=service, manifest=manifest, artifact=artifact,
                           old_pin=old_pin, old_row=old_row, sidecar=sidecar,
                           destination=service.local_path(artifact))


def test_pomegranate_derivative_pin_requires_all_logical_private_and_level_rows(app_config, transition):
    m = transition
    before = m.old_pin.read_bytes()
    plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    claim = plan.artifacts[0].storage_migration["manifest"]["tables"]["orderbook_levels"]
    assert claim["externalized_level_rows"] == 3 and claim["implicit_rowid_preserved"]
    result = m.service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert m.service.verify(job=m.remote.job)["status"] == "SUCCESS"
    pin = m.service.pin_database(m.artifact.source_key)
    assert pin != m.old_pin and m.old_pin.read_bytes() == before
    assert_pin(app_config, pin)


@pytest.mark.parametrize("missing", ["externalized_level_rows", "implicit_rowid_preserved"])
def test_missing_original_level_identity_claim_keeps_previous_pin(transition, missing):
    m = transition
    before = m.old_pin.read_bytes()
    m.manifest["tables"]["orderbook_levels"].pop(missing)
    m.sidecar.write_text(json.dumps(m.manifest))
    with pytest.raises(RuntimeError, match="storage migration|RAW level"):
        m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    assert m.old_pin.read_bytes() == before
    assert sha256(m.destination) == m.old_row["local_sha256"]


def test_full_profile_missing_entire_level_table_claim_is_rejected(transition):
    m = transition
    before = m.old_pin.read_bytes()
    del m.manifest["tables"]["orderbook_levels"]
    del m.manifest["private_storage"]["orderbook_levels"]
    m.sidecar.write_text(json.dumps(m.manifest))
    with pytest.raises(RuntimeError, match="RAW level original row identity"):
        m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    assert m.old_pin.read_bytes() == before
    assert sha256(m.destination) == m.old_row["local_sha256"]
