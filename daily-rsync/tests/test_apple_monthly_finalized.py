from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import zlib
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from polybot_observability.market_data_apple import (
    prepare_frame,
    publish_frame_observation,
    reserve_frame_storage,
)
from polybot_observability.market_data_apple_finalize import (
    finalize_monthly_migration,
    retire_monthly_sidecar,
)
from polybot_observability.market_data_migrate import migrate_public_bodies
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_apple_monthly_remote import SCHEMA
from test_public_payloads import LocalRemote
from test_remote_agent import invoke, make_freestyle_config

from daily_rsync import remote_agent
from daily_rsync.models import JobInventory
from daily_rsync.public_payloads import storage_migration_candidate
from daily_rsync.sync import SyncService, sha256


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class MonthlyRemote(LocalRemote):
    """Exercise the actual stdlib scan/snapshot helper against local synthetic files."""

    def __init__(self, root):
        super().__init__(root, [])
        self.home = root / ".jenkins"
        self.workspaces = root / "workspaces"
        self.workspace = self.workspaces / "polybot-do"
        self.workspace.mkdir(parents=True)
        make_freestyle_config(
            self.home, "polybot-do", "python -m apple.compact_collection",
            custom_workspace=self.workspace,
        )

    def arguments(self):
        return ["--jenkins-home", str(self.home), "--workspace-root", str(self.workspaces)]

    def scan(self, *, job, cutoff_epoch, archive_from_date, archive_to_date):
        args = [*self.arguments(), "--job", job, "--cutoff-epoch", str(cutoff_epoch)]
        if archive_from_date:
            args += ["--archive-from-date", archive_from_date.isoformat(),
                     "--archive-to-date", archive_to_date.isoformat()]
        return [JobInventory.from_dict(row) for row in invoke("scan", *args)["jobs"]]

    def identity_arguments(self, job, expected_workspace, expected_identity):
        return [*self.arguments(), "--job", job, "--expected-workspace", expected_workspace,
                "--expected-identity", json.dumps(expected_identity)]

    def validate_workspace(self, *, job, expected_workspace, expected_identity):
        return invoke("validate-workspace", *self.identity_arguments(
            job, expected_workspace, expected_identity,
        ))

    def snapshot_database(self, remote_path, *, job, expected_workspace, expected_identity,
                          expected_data_contract, expected_database_utc_date):
        args = self.identity_arguments(job, expected_workspace, expected_identity)
        args += ["--source", remote_path, "--staging-root", str(self.root / "staging")]
        if expected_data_contract:
            args += ["--expected-data-contract", expected_data_contract]
        if expected_database_utc_date:
            args += ["--expected-database-utc-date", expected_database_utc_date]
        return invoke("snapshot", *args)


def make_source(path):
    # This is the collection-v2 compact_store SCHEMA, including its real run PK.
    path.parent.mkdir(parents=True)
    maximum = 16 * 1024 * 1024
    config = {"jobs": ["polybot-do"], "storage": {"max_month_db_bytes_per_job": maximum},
              "private_selection": "PRIVATE_CONFIG_SENTINEL"}
    document = canonical(config)
    config_hash = hashlib.sha256(document).hexdigest()
    started = datetime(2026, 9, 15, tzinfo=UTC).timestamp()
    slot = int(started) // 60
    frame = {
        "format": "apple-filtered-frames-v1",
        "markets": [{"market": {"id": "public-market", "question": "Public question"},
                     "tokens": ["PRIVATE_TOKEN_SELECTION"]}],
        "events": {"event": {"event": {"id": "public-event"},
                             "request": {"trace": "PRIVATE_EVENT_REQUEST"}}},
        "books": {"token": {"book": {"asset_id": "token", "bids": [], "asks": [
            {"price": "0.5", "size": "10"}]}, "capacity": "PRIVATE_CAPACITY"}},
        "followups": [], "audit": "PRIVATE_AUDIT", "fee": "PRIVATE_FEE",
    }
    raw = canonical(frame)
    packed = zlib.compress(raw, 6)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(f"PRAGMA application_id={remote_agent.APPLE_COLLECTION_APPLICATION_ID}")
        connection.execute("PRAGMA user_version=1")
        connection.executemany("INSERT INTO meta VALUES (?,?)", [
            ("job", "polybot-do"), ("month", "2026-09"),
            ("format", "apple-filtered-frames-v1"), ("watch_seed", "none"),
        ])
        connection.execute("INSERT INTO configs VALUES (?,?)", (config_hash, document.decode()))
        connection.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            slot, "jenkins:1", started, started + 2, "PARTIAL", config_hash, "a" * 64,
            "b" * 40, '{"private":"PRIVATE_SUMMARY"}', packed,
            hashlib.sha256(raw).hexdigest(), len(raw),
        ))
        connection.execute("INSERT INTO watch VALUES ('private-event','[\"PRIVATE_WATCH\"]',?)",
                           (started + 86400,))
        connection.execute("CREATE TABLE account_ledger(id INTEGER PRIMARY KEY, account TEXT)")
        connection.execute("INSERT INTO account_ledger VALUES (9, 'PRIVATE_ACCOUNT')")
    return config_hash, maximum, packed


@pytest.fixture
def finalized_month(app_config, tmp_path):
    remote = MonthlyRemote(tmp_path / "remote-apple-finalized")
    path = remote.workspace / "data/collection-v2/2026-09.sqlite"
    config_hash, maximum, packed = make_source(path)
    service = SyncService(app_config)
    service.remote = remote
    initial_plan = service.create_plan(job="polybot-do", strategy="golden-apple")
    assert service.execute(initial_plan).status == "SUCCESS"
    old_artifact = initial_plan.artifacts[0]
    destination = service.local_path(old_artifact)
    old_row = service.catalog.get_artifact(old_artifact.source_key)
    old_pin = service.pin_database(old_artifact.source_key)
    original_state = remote_agent.sqlite_source_state(path)
    original = remote.root / "original.sqlite"
    shutil.copyfile(path, original)
    target = remote.root / "finalized.sqlite"
    with PayloadStore(remote.public_db) as store:
        references = PayloadReferences(reader=store, writer=store)
        migrate_public_bodies(
            original, target, strategy="golden-apple", source_sha256=sha256(original),
            references=references,
        )
        manifest = finalize_monthly_migration(
            original, target, references=references, job="polybot-do", month="2026-09",
            intermediate_manifest_path=Path(str(target) + ".migration.json"),
            final_manifest_path=remote.root / "finalized-receipt.json",
            collector_lock=original.parent / ".collector.lock",
            observer="test-host:golden-apple:polybot-do:collection-v2",
            effective_config_hash=config_hash, maximum_bytes=maximum,
        )
    manifest.update(
        source_snapshot_sha256=old_row["local_sha256"],
        source_file_sha256=sha256(original), original_source_path=str(path),
        original_source_fingerprint=original_state["fingerprint"],
        original_source_members=original_state["members"],
    )
    shutil.copyfile(target, path)
    sidecar = Path(str(path) + ".storage-migration.json")
    sidecar.write_text(json.dumps(manifest))
    plan = service.create_plan(job="polybot-do", strategy="golden-apple")
    assert len(plan.artifacts) == 1
    return SimpleNamespace(
        service=service, remote=remote, path=path, original=original, target=target,
        destination=destination, manifest=manifest, plan=plan, old_row=old_row,
        old_pin=old_pin, sidecar=sidecar, packed=packed,
    )


def test_finalized_month_scan_plan_sync_verify_pin_preserves_source(finalized_month):
    m = finalized_month
    artifact = m.plan.artifacts[0]
    assert storage_migration_candidate(artifact, m.old_row)
    old_bytes, old_pin_bytes = m.destination.read_bytes(), m.old_pin.read_bytes()
    result = m.service.execute(m.plan)
    assert result.status == "SUCCESS", result.errors
    assert sha256(m.destination) == m.manifest["destination_sha256"]
    assert m.old_pin.read_bytes() == old_pin_bytes
    row = m.service.catalog.get_artifact(artifact.source_key)
    lineage = m.service.catalog.storage_generation(artifact.source_key, row["local_sha256"])
    assert Path(lineage["original_path"]).read_bytes() == old_bytes
    assert lineage["source_snapshot_link"]["source_snapshot_sha256"] == m.old_row["local_sha256"]
    assert lineage["source_file_sha256"] == sha256(m.original)
    assert lineage["complete_utc_day"] is False
    assert lineage["apple_monthly_finalization"]["month"] == "2026-09"
    metadata = json.loads(row["metadata_json"])
    assert metadata["completed_at"] == json.loads(m.old_row["metadata_json"])["completed_at"]
    verified = m.service.verify(job="polybot-do", strategy="golden-apple",
                                from_date=date(2026, 9, 1), to_date=date(2026, 9, 30))
    assert verified["status"] == "SUCCESS", verified
    assert verified["archive_coverage"] is None
    assert verified["monthly_coverage"]["calendar_month_complete"] is False
    pin = m.service.pin_database(artifact.source_key)
    assert pin != m.old_pin and sha256(pin) == row["local_sha256"]
    pinned = json.loads((pin.parent / "manifest.json").read_text())
    assert pinned["database_month"] == "2026-09"
    assert pinned["storage_lineage"]["complete_utc_day"] is False
    assert m.old_pin.read_bytes() == old_pin_bytes
    assert len(m.service.catalog.list_artifacts()) == 1
    again = m.service.create_plan(job="polybot-do", strategy="golden-apple")
    assert not again.artifacts and again.skipped_unchanged == 1
    with PayloadReader(m.service.config.public_store_path) as reader:
        observations = list(reader.iter_observations(kind="apple_public_frame_v2"))
        assert len(observations) == 1
        assert observations[0].observed_at == "2026-09-15T00:00:02+00:00"


@pytest.mark.parametrize("tamper", [
    "private", "watch", "account", "rowid", "original_meta", "budget", "config", "frame",
    "intermediate", "source_link", "cached_original", "observation_clock", "missing_observation",
])
def test_forged_monthly_finalization_never_changes_existing_evidence(finalized_month, tamper):
    m = finalized_month
    original_bytes = m.destination.read_bytes()
    if tamper == "cached_original":
        with sqlite3.connect(m.destination) as connection:
            connection.execute("UPDATE account_ledger SET account='changed'")
        original_bytes = m.destination.read_bytes()
    elif tamper in {"observation_clock", "missing_observation"}:
        with sqlite3.connect(m.remote.public_db) as connection:
            for (trigger,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='observations'"
            ).fetchall():
                connection.execute('DROP TRIGGER "' + trigger.replace('"', '""') + '"')
            connection.execute(
                "UPDATE observations SET observed_at='2026-09-15T00:01:00+00:00'"
                if tamper == "observation_clock" else "DELETE FROM observations"
            )
    elif tamper in {"intermediate", "source_link"}:
        if tamper == "intermediate":
            final = m.manifest["apple_monthly_finalization"]
            final["intermediate_sha256"] = "f" * 64
        else:
            m.manifest["source_snapshot_sha256"] = "f" * 64
    else:
        sql = {
            "private": "UPDATE runs SET summary_json='PRIVATE_TAMPERED'",
            "watch": "UPDATE watch SET market_ids='[\"changed\"]'",
            "account": "UPDATE account_ledger SET account='changed'",
            "rowid": "UPDATE runs SET slot=slot+1",
            "original_meta": "UPDATE meta SET value='changed' WHERE key='watch_seed'",
            "budget": "UPDATE meta SET value='0' WHERE key='shared_frame_reserved_bytes'",
            "config": "UPDATE configs SET document='{}'",
            "frame": "UPDATE runs SET frame=X'00'",
        }[tamper]
        with sqlite3.connect(m.path) as connection:
            connection.execute(sql)
        digest = sha256(m.path)
        m.manifest["destination_sha256"] = digest
        m.manifest["destination_bytes"] = m.path.stat().st_size
        m.manifest["apple_monthly_finalization"]["final_sha256"] = digest
    m.sidecar.write_text(json.dumps(m.manifest))
    try:
        plan = m.service.create_plan(job="polybot-do", strategy="golden-apple")
        result = m.service.execute(plan)
        assert result.status == "FAILED", result.errors
    except (RuntimeError, ValueError, subprocess.CalledProcessError):
        pass
    assert m.destination.read_bytes() == original_bytes
    assert m.service.catalog.get_artifact(m.plan.artifacts[0].source_key)["local_sha256"] == (
        m.old_row["local_sha256"]
    )
    assert m.service.catalog.storage_generation(
        m.plan.artifacts[0].source_key, sha256(m.path),
    ) is None


def test_monthly_migration_snapshot_copies_final_sha_without_sqlite_rebackup(finalized_month):
    m = finalized_month
    artifact = m.plan.artifacts[0]
    snapshot = m.remote.snapshot_database(
        str(m.path), job=artifact.jenkins_job, expected_workspace=m.plan.workspace,
        expected_identity=m.plan.workspace_identity,
        expected_data_contract=artifact.data_contract, expected_database_utc_date=None,
    )
    assert snapshot["snapshot_source_open_mode"] == "verified_storage_migration_copy"
    assert snapshot["sha256"] == m.manifest["destination_sha256"]
    assert snapshot["database_month"] == "2026-09"
    assert snapshot["database_utc_date"] is None
    assert snapshot["observation_window"]["calendar_month_complete"] is False


def test_plain_body_migration_is_insufficient_for_monthly_replacement(finalized_month):
    m = finalized_month
    del m.plan.artifacts[0].storage_migration["manifest"]["apple_monthly_finalization"]
    assert not storage_migration_candidate(m.plan.artifacts[0], m.old_row)


def test_monthly_projection_excludes_unreviewed_and_private_fields(finalized_month):
    m = finalized_month
    final = m.manifest["apple_monthly_finalization"]
    final["private_config"] = "PRIVATE_SENTINEL"
    m.manifest["private_account"] = "PRIVATE_SENTINEL"
    final["intermediate_manifest"]["private_config"] = "PRIVATE_SENTINEL"
    m.sidecar.write_text(json.dumps(m.manifest))
    projected = remote_agent.storage_migration_record(m.path)
    assert "PRIVATE_SENTINEL" not in json.dumps(projected)
    assert projected["sidecar_sha256"] == sha256(m.sidecar)
    assert projected["manifest"]["apple_monthly_finalization"]["frame_count"] == 1


@pytest.mark.parametrize(("field", "value"), [
    ("contract", "other"), ("job", "polybot-unapproved"), ("month", "2026-13"),
    ("observer", "PRIVATE_SENTINEL\n"), ("effective_config_hash", "A" * 64),
    ("intermediate_manifest_canonical_sha256", "f" * 64),
    ("maximum_bytes", True), ("allowed_meta_additions_count", -1),
    ("observation_count", "1"), ("reserved_bytes", 1.5),
])
def test_monthly_projection_rejects_invalid_bounded_fields(finalized_month, field, value):
    m = finalized_month
    m.manifest["apple_monthly_finalization"][field] = value
    m.sidecar.write_text(json.dumps(m.manifest))
    with pytest.raises(RuntimeError, match="Apple") as error:
        remote_agent.storage_migration_record(m.path)
    assert "PRIVATE_SENTINEL" not in str(error.value)


def test_stale_monthly_sidecar_is_rejected_instead_of_ignored(finalized_month):
    m = finalized_month
    original = m.destination.read_bytes()
    with sqlite3.connect(m.path) as connection:
        connection.execute("UPDATE watch SET next_due=next_due+60")
    plan = m.service.create_plan(job="polybot-do", strategy="golden-apple")
    result = m.service.execute(plan)
    assert result.status == "FAILED"
    assert m.destination.read_bytes() == original


def test_explicit_receipt_retirement_then_native_append_preserves_both_pins(finalized_month):
    m = finalized_month
    source_key = m.plan.artifacts[0].source_key
    assert m.service.execute(m.plan).status == "SUCCESS"
    assert m.service.verify(job="polybot-do", strategy="golden-apple")["status"] == "SUCCESS"
    finalized_pin = m.service.pin_database(source_key)
    finalized_sha = m.manifest["destination_sha256"]
    lineage = m.service.catalog.storage_generation(source_key, finalized_sha)
    original_pin_bytes = m.old_pin.read_bytes()

    # Explicit stopped-writer cutover: verified baseline and official pin first,
    # receipt retirement second. An active stale sidecar is never ignored.
    checkpoint = m.remote.root / "review/2026-09.sqlite"
    checkpoint.parent.mkdir()
    shutil.copyfile(m.path, checkpoint)
    assert sha256(checkpoint) == sha256(m.path) == sha256(finalized_pin) == finalized_sha
    assert lineage["generation_sha256"] == finalized_sha
    retired_receipt = checkpoint.with_suffix(".storage-migration.json")
    pin_manifest = finalized_pin.parent / "manifest.json"
    with PayloadReader(m.service.config.public_store_path) as reader:
        retirement = retire_monthly_sidecar(
            m.original, m.path, checkpoint,
            sidecar_path=m.sidecar, review_path=retired_receipt,
            pinned_db=finalized_pin, pin_manifest_path=pin_manifest,
            catalog_path=m.service.catalog.path, source_key=source_key,
            source_identity=json.loads(pin_manifest.read_text())["source_identity"],
            references=PayloadReferences(reader=reader),
            collector_lock=m.path.parent / ".collector.lock",
        )
    assert retirement["status"] == "RETIRED"
    assert retirement["final_sha256"] == finalized_sha
    assert not m.sidecar.exists()

    started = datetime(2026, 9, 15, 0, 1, tzinfo=UTC).timestamp()
    slot = int(started) // 60
    raw = zlib.decompress(m.packed)
    with PayloadStore(m.remote.public_db) as store, sqlite3.connect(m.path) as connection:
        refs = PayloadReferences(reader=store, writer=store)
        prepared = prepare_frame(m.packed, refs)
        reserve_frame_storage(connection, prepared, slot=slot,
                              maximum_bytes=m.manifest["apple_monthly_finalization"]["maximum_bytes"])
        blob = prepared.publish(refs)
        publish_frame_observation(
            blob, refs, observer=m.manifest["apple_monthly_finalization"]["observer"],
            frame_id=f"2026-09/{slot}/jenkins:2", observed_at="2026-09-15T00:01:02+00:00",
            job="polybot-do",
        )
        connection.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            slot, "jenkins:2", started, started + 2, "SUCCESS",
            m.manifest["apple_monthly_finalization"]["effective_config_hash"], "a" * 64,
            "b" * 40, '{"private":"PRIVATE_NEW_SUMMARY"}', blob,
            hashlib.sha256(raw).hexdigest(), len(raw),
        ))
        connection.execute("UPDATE watch SET next_due=next_due+60")
    mutable_plan = m.service.create_plan(job="polybot-do", strategy="golden-apple")
    assert len(mutable_plan.artifacts) == 1
    assert mutable_plan.artifacts[0].storage_migration is None
    result = m.service.execute(mutable_plan)
    assert result.status == "SUCCESS", result.errors
    verified = m.service.verify(job="polybot-do", strategy="golden-apple")
    assert verified["status"] == "SUCCESS", verified
    assert verified["monthly_coverage"]["calendar_month_complete"] is False
    assert m.old_pin.read_bytes() == original_pin_bytes
    assert sha256(finalized_pin) == sha256(checkpoint) == finalized_sha
    assert m.service.catalog.storage_generation(source_key, finalized_sha) == lineage
    assert json.loads(retired_receipt.read_text())["destination_sha256"] == finalized_sha
    with sqlite3.connect(m.destination) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
