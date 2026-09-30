import hashlib
import json
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from polybot_observability.market_data_migrate import migrate_public_bodies
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadStore
from test_public_payloads import LocalRemote

from daily_rsync import remote_agent
from daily_rsync.models import JobInventory, RemoteArtifact, SyncPlan
from daily_rsync.public_payloads import (
    closure_sidecar,
    synchronize_database_closure,
    verify_database_closure,
    write_closure_descriptor,
)
from daily_rsync.sync import SyncService, sha256


@pytest.fixture
def migration(app_config, tmp_path, request):
    use_levels = getattr(request, "param", None) in {"levels", "existing_levels"}
    existing_levels = getattr(request, "param", None) == "existing_levels"
    strategy = "golden-black" if use_levels else "golden-coconut"
    remote = LocalRemote(tmp_path / "remote-storage", [])
    original = remote.root / "original.db"
    with sqlite3.connect(original) as connection:
        connection.executescript("""
            PRAGMA user_version=7;
            CREATE TABLE requests(id INTEGER PRIMARY KEY AUTOINCREMENT,
                                  raw_gzip BLOB, private_order TEXT);
            CREATE TABLE config(id INTEGER PRIMARY KEY, config_json TEXT);
            INSERT INTO requests VALUES(4, X'7075626c696320626f6479', 'private ledger');
            INSERT INTO config VALUES(2, '{"amount":17}');
            CREATE TABLE collection_contracts(contract_name TEXT PRIMARY KEY,
                                             database_utc_date TEXT NOT NULL);
            INSERT INTO collection_contracts VALUES('research-full-v1', '2026-08-05');
        """)
        if use_levels:
            connection.executescript("""
                CREATE TABLE orderbook_snapshots(snapshot_id TEXT PRIMARY KEY, token_id TEXT);
                CREATE TABLE orderbook_levels(level_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),
                    side TEXT CHECK(side IN ('BID','ASK')), level_index INTEGER,
                    price REAL, size REAL, UNIQUE(snapshot_id,side,level_index));
                INSERT INTO orderbook_snapshots VALUES('s', 'token');
                INSERT INTO orderbook_levels VALUES('l1', 's', 'ASK', 0, .71, 20.0);
                INSERT INTO orderbook_levels VALUES('l2', 's', 'BID', 0, .70, 30.0);
            """)
    target = remote.root / "trades_sim_20260805.db"
    if existing_levels:
        from polybot_observability.market_data_levels import insert_shared_levels

        with PayloadStore(remote.public_db) as store, sqlite3.connect(original) as connection:
            connection.execute("INSERT INTO orderbook_snapshots VALUES('s2','token')")
            insert_shared_levels(connection, strategy, "orderbook_levels", [{
                "level_id": "already-shared", "snapshot_id": "s2", "side": "ASK",
                "level_index": 0, "price": .72, "size": 25.0,
            }], references=PayloadReferences(store, store))
    with PayloadStore(remote.public_db) as store:
        manifest = migrate_public_bodies(
            original,
            target,
            strategy=strategy,
            source_sha256=sha256(original),
            references=PayloadReferences(store, store),
            include_levels=use_levels,
        )
    sidecar = Path(str(target) + ".storage-migration.json")
    sidecar.write_text(json.dumps(manifest))
    proposal = remote_agent.storage_migration_record(target)
    service = SyncService(app_config)
    service.remote = remote
    old = RemoteArtifact(
        kind="database_research_archive",
        remote_path=str(target),
        size_bytes=original.stat().st_size,
        mtime_ns=1,
        fingerprint="original",
        jenkins_job="polybot-white",
        source=app_config.ssh_host,
        strategy=strategy,
        runtime_job="recorder",
        archive_date="2026-08-05",
        data_contract="research-full-v1",
        database_utc_date="2026-08-05",
        completed_at="2026-08-06T00:01:00+00:00",
        canonical=False,
        mode="sim",
    )
    destination = service.local_path(old)
    destination.parent.mkdir(parents=True)
    shutil.copyfile(original, destination)
    if existing_levels:
        descriptor = synchronize_database_closure(
            app_config, remote, destination, strategy=strategy,
            source_key=old.source_key, database_sha256=sha256(original),
            ensure_capacity=lambda *_: None,
        )
        write_closure_descriptor(app_config, destination, descriptor)
    old_manifest = destination.parent / "manifest.json"
    old_manifest.write_text('{"original_evidence":true}')
    metadata = {
        "completed_at": old.completed_at,
        "archive_date": old.archive_date,
        "canonical": False,
    }
    service.catalog.upsert_artifact(
        old,
        source=app_config.ssh_host,
        local_path=destination,
        local_sha256=sha256(original),
        metadata=metadata,
    )
    changed = replace(
        old,
        size_bytes=target.stat().st_size,
        mtime_ns=2,
        fingerprint="derivative",
        completed_at="2026-09-30T00:00:00+00:00",
        storage_migration=proposal,
    )
    snapshot_original = remote.snapshot_database

    def snapshot(path, **kwargs):
        return {
            **snapshot_original(path, **kwargs),
            "storage_migration": changed.storage_migration,
            "data_contract": "research-full-v1",
            "database_utc_date": "2026-08-05",
            "source_fingerprint_after": changed.fingerprint,
        }

    remote.snapshot_database = snapshot
    identity = {"fixture": True}
    remote.validate_workspace = lambda **_: {"validated": True}
    plan = SyncPlan.create(
        source=app_config.ssh_host,
        jenkins_job=old.jenkins_job,
        strategy=old.strategy,
        workspace=str(remote.root),
        workspace_identity=identity,
        artifacts=[changed],
        skipped_unchanged=0,
        include_safety_databases=False,
    )
    # Tamper tests forge a consistent scan AND snapshot proposal, so rejection
    # must come from local provenance/decoded equality, not incidental TOCTOU.
    plan.artifacts[0] = changed
    inventory = JobInventory(
        name=old.jenkins_job,
        workspace=str(remote.root),
        workspace_identity=identity,
        build_count=0,
        min_build=None,
        max_build=None,
        current_strategy=old.strategy,
        strategies=(old.strategy,),
        artifacts=(changed,),
        remote_free_bytes=10**12,
    )
    return SimpleNamespace(
        service=service,
        remote=remote,
        original=original,
        target=target,
        destination=destination,
        old=old,
        artifact=changed,
        plan=plan,
        inventory=inventory,
        manifest=manifest,
        sidecar=sidecar,
    )


def test_verified_migration_preserves_original_pins_cutoff_and_one_artifact(migration, monkeypatch):
    m = migration
    old_pin = m.service.pin_database(m.old.source_key)
    old_sha = sha256(old_pin)
    monkeypatch.setattr(m.service, "scan", lambda **_: [m.inventory])
    plan = m.service.create_plan(job=m.old.jenkins_job, strategy=m.old.strategy)
    assert len(plan.artifacts) == 1
    result = m.service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert sha256(m.destination) == m.manifest["destination_sha256"]
    assert sha256(old_pin) == old_sha == m.manifest["source_sha256"]
    row = m.service.catalog.get_artifact(m.old.source_key)
    generation = m.service.catalog.storage_generation(m.old.source_key, row["local_sha256"])
    assert sha256(Path(generation["original_path"])) == old_sha
    assert json.loads(row["metadata_json"])["completed_at"] == m.old.completed_at
    assert len(m.service.catalog.list_artifacts()) == 1
    assert verify_database_closure(
        m.service.config,
        m.destination,
        strategy=m.old.strategy,
        source_key=m.old.source_key,
        database_sha256=row["local_sha256"],
    )
    pin = m.service.pin_database(m.old.source_key)
    pin_manifest = json.loads((pin.parent / "manifest.json").read_text())
    assert pin_manifest["storage_generation"] == row["local_sha256"]
    assert pin_manifest["storage_lineage"]["source_completed_at"] == m.old.completed_at
    again = m.service.create_plan(job=m.old.jenkins_job, strategy=m.old.strategy)
    assert not again.artifacts and again.skipped_unchanged == 1
    located = m.service.locate_evidence(job=m.old.jenkins_job, strategy=m.old.strategy)
    assert len(located["matches"][0]["runtimes"][0]["research_archives"]) == 1


@pytest.mark.parametrize(
    "tamper",
    [
        "private",
        "rowid",
        "schema",
        "public",
        "sequence",
        "pragma",
        "manifest_count",
        "source_sha",
        "missing_original",
        "sidecar_changed",
    ],
)
def test_forged_verified_label_cannot_admit_changed_evidence(migration, tamper):
    m = migration
    old_bytes = m.destination.read_bytes()
    if tamper in {"private", "rowid", "schema", "public", "sequence", "pragma"}:
        sql = {
            "private": "UPDATE requests SET private_order='changed'",
            "rowid": "UPDATE requests SET id=8",
            "schema": "CREATE TABLE injected (body TEXT)",
            "public": "UPDATE requests SET raw_gzip=X'77726f6e67'",
            "sequence": "UPDATE sqlite_sequence SET seq=100",
            "pragma": "PRAGMA user_version=8",
        }[tamper]
        with sqlite3.connect(m.target) as connection:
            connection.execute(sql)
        m.artifact.storage_migration["manifest"]["destination_sha256"] = sha256(m.target)
    elif tamper == "manifest_count":
        m.artifact.storage_migration["manifest"]["tables"]["requests"]["rows"] += 1
    elif tamper == "source_sha":
        m.artifact.storage_migration["manifest"]["source_sha256"] = "f" * 64
    elif tamper == "missing_original":
        m.destination.unlink()
    else:
        original_snapshot = m.remote.snapshot_database
        m.remote.snapshot_database = lambda *a, **k: {
            **original_snapshot(*a, **k),
            "storage_migration": None,
        }
    try:
        result = m.service.execute(m.plan)
        assert result.status == "FAILED", result.errors
    except RuntimeError:
        pass  # The unlinked original is rejected in plan preflight.
    if tamper != "missing_original":
        assert m.destination.read_bytes() == old_bytes
    assert (
        m.service.catalog.get_artifact(m.old.source_key)["local_sha256"]
        == hashlib.sha256(old_bytes).hexdigest()
    )
    assert m.service.catalog.storage_generation(m.old.source_key, sha256(m.target)) is None


@pytest.mark.parametrize("failure", ["closure", "manifest", "catalog"])
def test_publication_failure_restores_old_canonical_and_catalog(migration, monkeypatch, failure):
    from daily_rsync import sync

    m = migration
    old_bytes = m.destination.read_bytes()
    old_manifest = (m.destination.parent / "manifest.json").read_bytes()
    if failure == "catalog":
        monkeypatch.setattr(
            m.service.catalog,
            "upsert_artifact",
            lambda *a, **k: (_ for _ in ()).throw(OSError("catalog fault")),
        )
    else:
        original = sync.os.replace
        fired = []
        target = (
            closure_sidecar(m.destination)
            if failure == "closure"
            else sync.snapshot_manifest_path(m.destination,for_write=True)
        )

        def replace_file(source, destination):
            if Path(destination) == target and not fired:
                fired.append(True)
                raise OSError("publication fault")
            return original(source, destination)

        monkeypatch.setattr(sync.os, "replace", replace_file)
    result = m.service.execute(m.plan)
    assert result.status == "FAILED"
    assert m.destination.read_bytes() == old_bytes
    assert (m.destination.parent / "manifest.json").read_bytes() == old_manifest
    assert (
        m.service.catalog.get_artifact(m.old.source_key)["local_sha256"]
        == hashlib.sha256(old_bytes).hexdigest()
    )


def test_migration_without_prior_catalog_original_fails_closed(migration):
    m = migration
    with m.service.catalog.connect() as connection:
        connection.execute("DELETE FROM artifacts")
    result = m.service.execute(m.plan)
    assert result.status == "FAILED"
    assert m.destination.read_bytes() == m.original.read_bytes()


def test_only_exact_immutable_conflict_is_resolved_after_real_verification(migration, monkeypatch):
    m = migration
    existing = m.service.catalog.get_artifact(m.old.source_key)
    m.service.catalog.record_conflict(
        conflict_type="IMMUTABLE_REMOTE_CHANGED",
        source=m.old.source,
        artifact=m.artifact,
        local_path=m.destination,
        existing=existing,
    )
    monkeypatch.setattr(m.service, "scan", lambda **_: [m.inventory])
    plan = m.service.create_plan(job=m.old.jenkins_job, strategy=m.old.strategy)
    assert m.service.catalog.list_open_conflicts()
    assert m.service.execute(plan).status == "SUCCESS"
    assert not m.service.catalog.list_open_conflicts()
    assert m.service.catalog.list_conflicts()[0]["status"] == "RESOLVED"


def test_remote_sidecar_is_bounded_fixed_regular_file(migration, monkeypatch):
    m = migration
    assert remote_agent.storage_migration_record(m.target)["sidecar_sha256"] == sha256(m.sidecar)
    monkeypatch.setattr(remote_agent, "STORAGE_MIGRATION_MAX_BYTES", 5)
    with pytest.raises(RuntimeError, match="size/type"):
        remote_agent.storage_migration_record(m.target)
    m.sidecar.unlink()
    m.sidecar.symlink_to(m.target)
    with pytest.raises(OSError):
        remote_agent.storage_migration_record(m.target)


def test_remote_migration_snapshot_preserves_exact_target_sha(migration, tmp_path, monkeypatch):
    m = migration
    args = SimpleNamespace(
        staging_root=str(tmp_path / "staging"),
        expected_data_contract=None,
        expected_database_utc_date=None,
    )
    emitted = []
    monkeypatch.setattr(remote_agent, "emit", emitted.append)
    monkeypatch.setattr(remote_agent.shutil, "disk_usage", lambda _: SimpleNamespace(free=10**12))
    remote_agent._snapshot_database_source(args, m.target)
    assert emitted[0]["sha256"] == m.manifest["destination_sha256"]
    assert emitted[0]["storage_migration"] == m.artifact.storage_migration
    assert emitted[0]["snapshot_source_open_mode"] == "verified_storage_migration_copy"
    with sqlite3.connect(m.target) as connection:
        connection.execute("UPDATE requests SET private_order='changed'")
    with pytest.raises(RuntimeError, match="target checksum"):
        remote_agent._snapshot_database_source(args, m.target)


@pytest.mark.parametrize("migration", ["levels"], indirect=True)
def test_scalar_level_derivative_keeps_explicit_ids_and_complete_decoded_rows(migration):
    m = migration
    result = m.service.execute(m.plan)
    assert result.status == "SUCCESS", result.errors
    generation = m.service.catalog.storage_generation(m.old.source_key, sha256(m.target))
    expected = m.manifest["tables"]["orderbook_levels"]
    assert generation["tables"]["orderbook_levels"] == {
        "rows": 2,
        "logical_sha256": expected["logical_sha256"],
    }
    with sqlite3.connect(m.destination) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orderbook_levels").fetchone()[0] == 0


@pytest.mark.parametrize("migration", ["existing_levels"], indirect=True)
def test_second_generation_level_compaction_preserves_inline_and_shared_rows(migration):
    from polybot_observability.market_data_sqlite import connect
    from polybot_observability.market_data_store import PayloadReader

    m=migration
    old_pin=m.service.pin_database(m.old.source_key)
    result=m.service.execute(m.plan)
    assert result.status=="SUCCESS",result.errors
    with PayloadReader(m.service.config.public_store_path) as reader:
        codec=PayloadReferences(reader=reader)
        with connect(old_pin,references=codec) as before, connect(m.destination,references=codec) as after:
            assert before.execute("SELECT * FROM orderbook_levels ORDER BY level_id").fetchall()==after.execute(
                "SELECT * FROM orderbook_levels ORDER BY level_id"
            ).fetchall()
            assert after.execute("SELECT COUNT(*) FROM main.orderbook_levels").fetchone()==(0,)
            assert before.execute("SELECT COUNT(*) FROM main.orderbook_levels").fetchone()==(2,)
    generation=m.service.catalog.storage_generation(m.old.source_key,sha256(m.target))
    assert generation["tables"]["orderbook_levels"]["rows"]==3


def test_private_cell_cannot_be_externalized_even_if_decoded_value_is_equal(migration):
    m = migration
    with PayloadStore(m.remote.public_db) as store:
        reference = PayloadReferences(store, store).encode_many(["private ledger"])[0]
    with sqlite3.connect(m.target) as connection:
        connection.execute("UPDATE requests SET private_order=?", (reference,))
    m.artifact.storage_migration["manifest"]["destination_sha256"] = sha256(m.target)
    result = m.service.execute(m.plan)
    assert result.status == "FAILED"
    assert m.destination.read_bytes() == m.original.read_bytes()


def normalized_snapshot_link(m):
    """Use SQLite's real backup API to produce a distinct, equivalent old pin."""
    normalized = m.destination.with_name("normalized.db")
    with sqlite3.connect(m.destination) as source, sqlite3.connect(normalized) as target:
        source.backup(target)
    normalized.replace(m.destination)
    snapshot_sha = sha256(m.destination)
    assert snapshot_sha != m.manifest["source_sha256"]
    state = remote_agent.sqlite_source_state(m.original)
    prior_artifact = replace(
        m.old,
        fingerprint=state["fingerprint"],
        size_bytes=state["size_bytes"],
        mtime_ns=state["mtime_ns"],
    )
    prior = {
        "schema_version": 2,
        "source": m.artifact.remote_path,
        "local_path": str(m.destination),
        "sha256": snapshot_sha,
        "source_size_bytes": state["size_bytes"],
        "source_storage_bytes": state["size_bytes"],
        "snapshot_size_bytes": m.destination.stat().st_size,
        "source_members_after": state["members"],
        "source_fingerprint_before": state["fingerprint"],
        "source_fingerprint_after": state["fingerprint"],
        "remote_source_fingerprint": state["fingerprint"],
        "quick_check": ["ok"],
        "snapshot_journal_mode": "delete",
        "snapshot_source_open_mode": "read_only_locked",
        "artifact_kind": "database_research_archive",
        "data_contract": "research-full-v1",
        "database_utc_date": "2026-08-05",
    }
    parent = m.destination.parent / "manifest.json"
    parent.write_text(json.dumps(prior))
    metadata = json.loads(m.service.catalog.get_artifact(m.old.source_key)["metadata_json"])
    m.service.catalog.upsert_artifact(
        prior_artifact,
        source=m.old.source,
        local_path=m.destination,
        local_sha256=snapshot_sha,
        remote_sha256=snapshot_sha,
        metadata=metadata,
    )
    link = {
        "source_snapshot_sha256": snapshot_sha,
        "source_file_sha256": m.manifest["source_sha256"],
        "original_source_path": m.artifact.remote_path,
        "original_source_fingerprint": state["fingerprint"],
        "original_source_members": state["members"],
    }
    m.sidecar.write_text(json.dumps({**m.manifest, **link}))
    proposed = remote_agent.storage_migration_record(m.target)
    m.artifact.storage_migration.clear()
    m.artifact.storage_migration.update(proposed)
    return snapshot_sha, prior, parent


def test_actual_backup_normalization_is_accepted_only_with_verified_snapshot_link(migration):
    m = migration
    local_sha, prior, parent = normalized_snapshot_link(m)
    parent_sha = sha256(parent)
    result = m.service.execute(m.plan)
    assert result.status == "SUCCESS", result.errors
    generation = m.service.catalog.storage_generation(m.old.source_key, sha256(m.target))
    assert generation["original_sha256"] == local_sha
    assert generation["source_file_sha256"] == m.manifest["source_sha256"]
    assert generation["source_snapshot_link"]["parent_manifest_sha256"] == parent_sha
    assert generation["source_snapshot_link"]["physical_byte_equality_claimed"] is False
    assert sha256(Path(generation["original_path"])) == local_sha


@pytest.mark.parametrize(
    "tamper",
    [
        "no_link",
        "missing_parent",
        "parent_symlink",
        "parent_sha",
        "parent_source",
        "parent_local_path",
        "parent_before_fingerprint",
        "parent_after_fingerprint",
        "parent_remote_fingerprint",
        "parent_members",
        "parent_quick_check",
        "parent_mode",
        "parent_size",
        "catalog_fingerprint",
        "catalog_mtime",
        "catalog_size",
        "private_rows",
        "rowids",
        "schema",
    ],
)
def test_snapshot_link_never_bypasses_provenance_or_decoded_equality(migration, tamper):
    m = migration
    local_sha, prior, parent = normalized_snapshot_link(m)
    old_bytes = m.destination.read_bytes()
    if tamper == "no_link":
        for key in list(m.artifact.storage_migration["manifest"]):
            if key.startswith("original_source_") or key in {
                "source_snapshot_sha256",
                "source_file_sha256",
            }:
                del m.artifact.storage_migration["manifest"][key]
    elif tamper == "missing_parent":
        parent.unlink()
    elif tamper == "parent_symlink":
        other = parent.with_name("other-manifest.json")
        parent.rename(other)
        parent.symlink_to(other)
    elif tamper.startswith("parent_"):
        key = {
            "parent_sha": "sha256",
            "parent_source": "source",
            "parent_local_path": "local_path",
            "parent_before_fingerprint": "source_fingerprint_before",
            "parent_after_fingerprint": "source_fingerprint_after",
            "parent_remote_fingerprint": "remote_source_fingerprint",
            "parent_members": "source_members_after",
            "parent_quick_check": "quick_check",
            "parent_mode": "snapshot_source_open_mode",
            "parent_size": "snapshot_size_bytes",
        }[tamper]
        prior[key] = "tampered"
        parent.write_text(json.dumps(prior))
    elif tamper.startswith("catalog_"):
        column = {
            "catalog_fingerprint": "remote_fingerprint",
            "catalog_mtime": "remote_mtime_ns",
            "catalog_size": "remote_size_bytes",
        }[tamper]
        with m.service.catalog.connect() as connection:
            connection.execute("UPDATE artifacts SET " + column + "=?", (0,))
    else:
        sql = {
            "private_rows": "UPDATE config SET config_json='changed'",
            "rowids": "UPDATE requests SET id=100",
            "schema": "ALTER TABLE config ADD COLUMN unexpected TEXT",
        }[tamper]
        with sqlite3.connect(m.target) as connection:
            connection.execute(sql)
        m.artifact.storage_migration["manifest"]["destination_sha256"] = sha256(m.target)
    try:
        result = m.service.execute(m.plan)
        assert result.status == "FAILED", result.errors
    except RuntimeError:
        pass
    assert m.destination.read_bytes() == old_bytes
    assert m.service.catalog.get_artifact(m.old.source_key)["local_sha256"] == local_sha
    assert m.service.catalog.storage_generation(m.old.source_key, sha256(m.target)) is None
