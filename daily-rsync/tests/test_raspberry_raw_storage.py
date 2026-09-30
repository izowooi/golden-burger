"""Raspberry source-copy fields, private flags and durable follow-up lineage pin together."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from polybot_observability.market_data_levels import insert_shared_levels, iter_level_logical_rows
from polybot_observability.market_data_raw_links import initialize_raw_links, insert_raw_rows
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_raw_profiles import RASPBERRY_PROFILE_ID, raw_profile
from polybot_observability.market_data_refs import PayloadReferences, externalize_row
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_raw_storage_migrations import RawRemote
from test_pomegranate_raw_storage import insert

from daily_rsync import remote_agent
from daily_rsync.public_payloads import closure_sidecar
from daily_rsync.sync import SyncService, sha256


PROFILE = raw_profile(RASPBERRY_PROFILE_ID)
IDS = {
    "market_sweeps": ("sweep_id", "sweep"), "api_requests": ("request_id", "request"),
    "raw_payloads": ("payload_id", "payload"), "market_observations": ("observation_id", "observation"),
    "orderbook_token_attempts": ("attempt_id", "attempt"), "orderbook_snapshots": ("snapshot_id", "snapshot"),
    "signal_decisions": ("decision_id", "decision"), "research_cases": ("case_id", "case"),
    "followup_claims": ("claim_id", "claim"), "followup_claim_leases": ("lease_id", "lease"),
    "followup_request_starts": ("claim_id", "claim"), "followup_attempts": ("followup_id", "followup"),
}


def seed(tmp_path, namespace_source, *, native):
    remote = RawRemote(tmp_path / "remote", strategy=PROFILE.strategy)
    remote.job, remote.runtime = "polybot-do", "raspberry-do-v3-shard-0"
    remote.path = remote.root / remote.job / remote.strategy / "data" / remote.runtime / "trades_sim.db"
    remote.path.parent.mkdir(parents=True)
    namespace = scalar_namespace(namespace_source, remote.job, remote.strategy, remote.runtime)
    frozen = Path(__file__).resolve().parents[2] / "golden-raspberry/research/frozen-2026-08-23-v3"
    with PayloadStore(remote.public_db) as store:
        refs = PayloadReferences(store, store)
        with connect(remote.path, references=refs) as db:
            db.execute("PRAGMA foreign_keys=ON")
            for kind in ("table", "index", "trigger", "view"):
                for obj_kind, _, _, sql in PROFILE.source_objects:
                    if obj_kind == kind:
                        db.execute(sql)
            info = {name: db.execute("PRAGMA table_info(" + name + ")").fetchall()
                    for kind, name, _, _ in PROFILE.source_objects if kind == "table"}

            def row(table, **changes):
                values = {name: 0 if affinity == "INTEGER" else .8 if affinity == "REAL"
                          else b"{}" if affinity == "BLOB" else "{}" if name.endswith("_json")
                          else "source" for _, name, affinity, *_ in info[table]}
                for _, _, parent, local, target, *_ in db.execute("PRAGMA main.foreign_key_list(" + table + ")"):
                    assert target == IDS[parent][0]
                    values[local] = IDS[parent][1]
                common = {
                    "run_id": "PRIVATE_RUN", "observing_run_id": "PRIVATE_FOLLOWUP_RUN",
                    "condition_id": "condition", "token_id": "token", "event_id": "event",
                    "outcome_label": "Yes", "selected_token_id": "token", "selected_outcome_label": "Yes",
                    "observed_at": "2026-08-24T00:00:00Z", "source_timestamp": "1787529600000",
                    "question": "Original public question?", "tags_json": '[{"slug":"sports"}]',
                    "token_ids_json": '["token","opposite"]', "outcome_labels_json": '["Yes","No"]',
                    "outcome_prices_json": '["0.80","0.20"]', "attempt_role": "UNIVERSE",
                    "snapshot_role": "UNIVERSE", "prior_move_bin": "FLAT",
                    "matched_control_prior_move_bin": "FLAT", "case_kind": "SIGNAL",
                    "generation": 1, "logical_request_id": "PRIVATE_FIRST_REQUEST",
                    "request_started_at": "2026-08-24T01:00:00Z",
                    "details_json": '{"private_reason":"KEEP_LOCAL"}',
                }
                values.update({k: v for k, v in common.items() if k in values})
                if table in IDS:
                    values[IDS[table][0]] = IDS[table][1]
                values.update(changes)
                return values

            db.executemany("INSERT INTO schema_metadata VALUES(?,?)", [
                ("data_contract", "queue-echo-v3"), ("schema_profile", "queue-echo-v3-sqlite-v3"),
                ("schema_version", "3"),
            ])
            insert(db, "experiment_contracts", row(
                "experiment_contracts", job_name=remote.runtime, strategy_name=remote.strategy,
                data_contract="queue-echo-v3", schema_version=3, schema_profile="queue-echo-v3-sqlite-v3",
                shard_index=0, shard_count=3, cadence_minutes=5, cadence_offset_minute=0,
                window_start="2026-08-23T20:00:00Z", window_end="2026-09-22T20:00:00Z",
                preregistration_sha256=hashlib.sha256((frozen / "PREREGISTRATION.md").read_bytes()).hexdigest(),
                data_contract_sha256=hashlib.sha256((frozen / "DATA_CONTRACT.md").read_bytes()).hexdigest(),
            ))
            if native:
                initialize_raw_links(db, namespace, store.scalar_authority_identity(),
                                     references=refs, profile_id=PROFILE.profile_id)
            for index, table in enumerate(IDS):
                overrides = {
                    "api_requests": {"method": "GET", "status": "SUCCESS"},
                    "market_sweeps": {"cursor_complete": 1, "membership_encoding": "gzip-json-v1",
                                      "membership_blob": gzip.compress(b"[]", mtime=0),
                                      "membership_sha256": hashlib.sha256(b"[]").hexdigest(),
                                      "data_contract": "queue-echo-v3"},
                    "raw_payloads": {"payload_kind": "clob_universe_books", "content_encoding": "gzip",
                                     "payload_blob": gzip.compress(b'{"asset_id":"token"}', mtime=0)},
                    "orderbook_token_attempts": {"status": "OBSERVED"},
                    "followup_attempts": {"status": "QUOTE_COMPLETE", "exit_bid": .52, "exit_vwap": .51},
                }.get(table, {})
                values = row(table, **overrides)
                rowid = -7 if index == 0 else 0 if index == 1 else index * 17
                if native and table in PROFILE.tables:
                    insert_raw_rows(db, remote.strategy, table, [{**values, "__rowid__": rowid}],
                                    references=refs, namespace=namespace)
                else:
                    insert(db, table, externalize_row(remote.strategy, table, values, references=refs)
                           if native else values, rowid)
            levels = [row("orderbook_levels", level_id=None if index < 2 else "level",
                          snapshot_id="snapshot", side="ASK", level_index=index,
                          price=.51, size=30., in_near_touch_window=index % 2,
                          used_for_entry=int(index == 2), __rowid__=rowid)
                      for index, rowid in enumerate((-7, 0, 99))]
            if native:
                assert insert_shared_levels(db, remote.strategy, "orderbook_levels", levels,
                                            references=refs, preserve_rowid=True)
            else:
                for values in levels:
                    rowid = values.pop("__rowid__")
                    insert(db, "orderbook_levels", values, rowid)
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
    return remote, namespace


def assert_pin(config, pin):
    closure = json.loads(closure_sidecar(pin).read_text())
    assert closure["public_projection_records"]["raw_profile_id"] == PROFILE.profile_id
    assert closure["public_projection_records"]["receipt_count"] == 7
    with PayloadReader(config.public_store_path) as reader:
        refs = PayloadReferences(reader=reader)
        with connect(pin, references=refs) as db:
            assert db.execute("SELECT question,tags_json FROM market_observations").fetchone() == (
                "Original public question?", '[{"slug":"sports"}]',
            )
            assert db.execute("SELECT logical_request_id,token_id FROM followup_request_starts").fetchone() == (
                "PRIVATE_FIRST_REQUEST", "token",
            )
            assert db.execute("SELECT exit_bid,exit_vwap,details_json FROM followup_attempts").fetchone() == (
                .52, .51, '{"private_reason":"KEEP_LOCAL"}',
            )
            rows = list(iter_level_logical_rows(db, PROFILE.strategy,
                                                "orderbook_levels", references=refs, require_rowid=True))
            assert [(r["__rowid__"], r["level_id"], r["in_near_touch_window"], r["used_for_entry"])
                    for r in rows] == [(-7, None, 0, 0), (0, None, 1, 0), (99, "level", 0, 1)]
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
        with sqlite3.connect(pin) as physical:
            assert "exit_bid" not in {r[1] for r in physical.execute("PRAGMA table_info(followup_attempts)")}
            assert "exit_vwap" in {r[1] for r in physical.execute("PRAGMA table_info(followup_attempts)")}


def test_raspberry_schema_mirror_matches_source_copy_and_level_profile():
    mirror = remote_agent.RAW_PROFILE_SCHEMAS[PROFILE.profile_id]
    assert mirror["strategy"] == PROFILE.strategy
    assert mirror["logical_schema_sha256"] == PROFILE.logical_schema_sha256
    assert mirror["kinds"] == {name: (p.kind,) for name, p in PROFILE.tables.items()}
    assert mirror["level_tables"] == PROFILE.level_tables


def test_raspberry_native_scan_sync_verify_pin(app_config, tmp_path):
    remote, _ = seed(tmp_path, app_config.ssh_host, native=True)
    service = SyncService(app_config)
    service.remote = remote
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    result = service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert service.verify(job=remote.job)["status"] == "SUCCESS"
    assert_pin(app_config, service.pin_database(plan.artifacts[0].source_key))


@pytest.mark.parametrize("native", [False, True], ids=["inline-original", "shared-original"])
def test_raspberry_private_first_request_and_level_flags_survive_derivative_lineage(app_config, tmp_path, native):
    remote, namespace = seed(tmp_path, app_config.ssh_host, native=native)
    service = SyncService(app_config)
    service.remote = remote
    first = service.create_plan(job=remote.job, strategy=remote.strategy)
    assert service.execute(first).status == "SUCCESS"
    artifact = first.artifacts[0]
    old_row = service.catalog.get_artifact(artifact.source_key)
    old_pin = service.pin_database(artifact.source_key)
    old_bytes = old_pin.read_bytes()
    state = remote_agent.sqlite_source_state(remote.path)
    original, target = remote.root / "original.db", remote.root / "derivative.db"
    shutil.copyfile(remote.path, original)
    with PayloadStore(remote.public_db) as store:
        manifest = migrate_raw_database(original, target, source_sha256=sha256(original),
                                        namespace=namespace, references=PayloadReferences(store, store),
                                        profile_id=PROFILE.profile_id, batch_rows=2)
    manifest.update(source_snapshot_sha256=old_row["local_sha256"], source_file_sha256=sha256(original),
                    original_source_path=str(remote.path), original_source_fingerprint=state["fingerprint"],
                    original_source_members=state["members"])
    shutil.copyfile(target, remote.path)
    Path(str(remote.path) + ".raw-migration.json").write_text(json.dumps(manifest))
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    result = service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert service.verify(job=remote.job)["status"] == "SUCCESS"
    pin = service.pin_database(artifact.source_key)
    assert pin != old_pin and old_pin.read_bytes() == old_bytes
    assert_pin(app_config, pin)
