import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import zlib

import pytest

from polybot_observability import market_data_apple as apple
from polybot_observability.market_data_apple_finalize import (
    finalize_monthly_migration, retire_monthly_sidecar, verify_monthly_finalization,
)
from polybot_observability.market_data_migrate import file_sha256, migrate_public_bodies
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import Observation, PayloadReader, PayloadStore


# Native Apple compact_store.SCHEMA, with an extra private ledger to guard scope.
SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE configs (hash TEXT PRIMARY KEY, document TEXT NOT NULL);
CREATE TABLE runs (
 slot INTEGER PRIMARY KEY, run_key TEXT NOT NULL UNIQUE,
 started REAL NOT NULL, finished REAL, status TEXT NOT NULL,
 config_hash TEXT NOT NULL REFERENCES configs(hash), source_hash TEXT NOT NULL,
 git_commit TEXT, summary_json TEXT, frame BLOB, frame_sha256 TEXT, frame_raw_bytes INTEGER
);
CREATE INDEX running_runs ON runs(slot) WHERE status='RUNNING';
CREATE TABLE watch (event_id TEXT PRIMARY KEY, market_ids TEXT NOT NULL, next_due REAL NOT NULL);
CREATE INDEX watch_due ON watch(next_due);
CREATE TABLE account_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, fill BLOB, fee REAL);
"""


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.fixture
def cutover(tmp_path):
    source = tmp_path / "2026-09.sqlite"
    stage = tmp_path / "2026-09.shared.sqlite"
    document = canonical({"storage": {"max_month_db_bytes_per_job": 1_000_000},
                          "jobs": ["polybot-do"]})
    config_hash = hashlib.sha256(document).hexdigest()
    with sqlite3.connect(source) as db:
        db.executescript(SCHEMA)
        db.execute("PRAGMA application_id=" + str(apple.APPLICATION_ID))
        db.execute("PRAGMA user_version=1")
        db.executemany("INSERT INTO meta VALUES (?,?)", [("job", "polybot-do"),
            ("month", "2026-09"), ("format", apple.ORIGINAL_FORMAT), ("watch_seed", "none")])
        db.execute("INSERT INTO configs VALUES (?,?)", (config_hash, document.decode()))
        db.execute("INSERT INTO watch VALUES ('e-private','[\"private-selection\"]',1790640100.0)")
        db.execute("INSERT INTO account_ledger(id,fill,fee) VALUES (7,?,0.007)", (b"private-fill",))
        for slot, status in ((29844160, "COMPLETE"), (29844161, "PARTIAL"), (29844162, "FAILED")):
            frame = {"format": apple.ORIGINAL_FORMAT, "markets": [{"market": {"id": "m-public"},
                "tokens": ["private-selection"]}], "events": {}, "books": {}, "followups": [],
                "audit": {"slot": slot, "private-fee": "0.003"}}
            raw = canonical(frame)
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (slot, f"jenkins:{slot}",
                1790640000.0, 1790640060.125, status, config_hash, "a" * 64, "source-commit",
                '{"private-summary":true}', zlib.compress(raw, 6), hashlib.sha256(raw).hexdigest(), len(raw)))
        db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (29844163, "jenkins:9",
            1790640100.0, None, "RUNNING", config_hash, "a" * 64, None, None, None, None, None))
    public = tmp_path / "public.sqlite"
    with PayloadStore(public) as store:
        refs = PayloadReferences(reader=store, writer=store, cache_bytes=0)
        intermediate = migrate_public_bodies(source, stage, strategy="golden-apple",
            source_sha256=file_sha256(source), references=refs)
    return {"source": source, "stage": stage, "public": public, "intermediate": intermediate,
            "kwargs": {"intermediate_manifest_path": Path(str(stage) + ".migration.json"),
                "final_manifest_path": Path(str(stage) + ".final.migration.json"),
                "collector_lock": tmp_path / ".collector.lock", "observer": "m5:golden-apple:polybot-do:collection-v2",
                "job": "polybot-do", "month": "2026-09", "maximum_bytes": 1_000_000,
                "effective_config_hash": config_hash}}


def finalize(cutover, writer=None, **overrides):
    with PayloadStore(cutover["public"]) as store:
        return finalize_monthly_migration(cutover["source"], cutover["stage"],
            references=PayloadReferences(reader=store, writer=writer or store, cache_bytes=0),
            **{**cutover["kwargs"], **overrides})


def verify(cutover, manifest, **kwargs):
    with PayloadReader(cutover["public"]) as reader:
        return verify_monthly_finalization(cutover["source"], cutover["stage"],
            PayloadReferences(reader=reader, cache_bytes=0), manifest, **kwargs)


def rewrite_final_sha(cutover, manifest):
    manifest["destination_sha256"] = file_sha256(cutover["stage"])
    manifest["destination_bytes"] = cutover["stage"].stat().st_size
    manifest["apple_monthly_finalization"]["final_sha256"] = manifest["destination_sha256"]


def test_finalization_exact_source_private_rows_frames_budget_and_shared_clock(cutover):
    source_before = cutover["source"].read_bytes()
    intermediate_before = cutover["kwargs"]["intermediate_manifest_path"].read_bytes()
    manifest = finalize(cutover)
    detail = manifest["apple_monthly_finalization"]
    assert manifest["contract"] == "shared-public-bodies-migration-v1"
    assert detail["source_sha256"] == cutover["intermediate"]["source_sha256"]
    assert detail["intermediate_sha256"] == cutover["intermediate"]["destination_sha256"]
    assert detail["final_sha256"] != detail["intermediate_sha256"]
    assert detail["frame_count"] == detail["observation_count"] == 3
    assert detail["allowed_meta_additions_count"] == 5
    assert detail["reserved_bytes"] == 3 * 20480
    assert detail["page_cap"] * 4096 + detail["reserved_bytes"] <= 1_000_000
    assert cutover["source"].read_bytes() == source_before
    assert cutover["kwargs"]["intermediate_manifest_path"].read_bytes() == intermediate_before
    assert verify(cutover, manifest)["status"] == "VERIFIED"
    assert finalize(cutover) == manifest
    with PayloadReader(cutover["public"]) as reader:
        observations = list(reader.iter_observations())
        assert len(observations) == 3
        assert {item.observed_at for item in observations} == {"2026-09-29T00:01:00.125000+00:00"}
        assert {item.observer for item in observations} == {cutover["kwargs"]["observer"]}


@pytest.mark.parametrize("mutation,match", [
    ("UPDATE account_ledger SET fee=900", "private row"),
    ("UPDATE runs SET status='COMPLETE' WHERE status='FAILED'", "private row"),
    ("UPDATE runs SET slot=99 WHERE slot=29844160", "private row"),
    ("UPDATE meta SET value='changed' WHERE key='watch_seed'", "metadata"),
    ("UPDATE meta SET rowid=90 WHERE key='watch_seed'", "rowid"),
    ("INSERT INTO meta VALUES('frame_storage_format','unapproved')", "metadata"),
    ("UPDATE meta SET value='1' WHERE key='shared_frame_reserved_bytes'", "metadata"),
    ("INSERT INTO meta VALUES('shared_frame_reservation:999','{}')", "metadata"),
    ("DROP INDEX watch_due", "schema"),
    ("UPDATE sqlite_sequence SET seq=100 WHERE name='account_ledger'", "internal state"),
])
def test_verifier_recomputes_evidence_despite_refreshed_claimed_hash(cutover, mutation, match):
    manifest = finalize(cutover)
    with sqlite3.connect(cutover["stage"]) as db:
        db.execute(mutation)
    rewrite_final_sha(cutover, manifest)
    with pytest.raises(ValueError, match=match):
        verify(cutover, manifest)


def test_unknown_outcome_ack_records_failed_then_retry_keeps_reservations(cutover):
    class UnknownAck:
        def append_observations(self, values):
            with PayloadStore(cutover["public"]) as durable:
                durable.append_observations(values)
            raise TimeoutError("ACK lost after commit")

    with pytest.raises(TimeoutError):
        finalize(cutover, writer=UnknownAck())
    failed = json.loads(cutover["kwargs"]["final_manifest_path"].read_text())
    assert failed["status"] == "FAILED"
    with sqlite3.connect(cutover["stage"]) as db:
        before = dict(db.execute("SELECT key,value FROM meta"))
    with pytest.raises(ValueError, match="retry identity"):
        finalize(cutover, observer="different-runtime")
    result = finalize(cutover)
    with sqlite3.connect(cutover["stage"]) as db:
        assert dict(db.execute("SELECT key,value FROM meta")) == before
    assert verify(cutover, result)["observation_count"] == 3


def test_false_ack_without_persisted_observation_never_verifies(cutover):
    class DroppedWrite:
        def append_observations(self, values):
            pass

    with pytest.raises(ValueError, match="readback is incomplete"):
        finalize(cutover, writer=DroppedWrite())
    assert json.loads(cutover["kwargs"]["final_manifest_path"].read_text())["status"] == "FAILED"
    assert finalize(cutover)["status"] == "VERIFIED"


def test_storage_gate_after_seed_blocks_publication_and_remains_retryable(cutover):
    calls = 0

    def storage_guard():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("external volume storage gate blocked")

    with pytest.raises(RuntimeError, match="storage gate"):
        finalize(cutover, storage_guard=storage_guard)
    with PayloadReader(cutover["public"]) as reader:
        assert reader.stats()["observation_count"] == 0
    assert json.loads(cutover["kwargs"]["final_manifest_path"].read_text())["status"] == "FAILED"
    assert finalize(cutover)["status"] == "VERIFIED"


def test_original_frame_header_counter_cannot_be_invented(cutover):
    manifest = finalize(cutover)
    with sqlite3.connect(cutover["stage"]) as db:
        db.execute("UPDATE runs SET frame_raw_bytes=1 WHERE frame IS NOT NULL")
    rewrite_final_sha(cutover, manifest)
    with pytest.raises(ValueError, match="private row"):
        verify(cutover, manifest)


def test_readonly_verifier_detects_deleted_observation_without_private_sha_change(cutover):
    manifest = finalize(cutover)
    before = file_sha256(cutover["stage"])
    with sqlite3.connect(cutover["public"]) as db:
        db.execute("DROP TRIGGER observations_no_delete")
        db.execute("DELETE FROM observations")
    with pytest.raises(ValueError, match="readback is incomplete"):
        verify(cutover, manifest)
    assert file_sha256(cutover["stage"]) == before


def test_conflicting_original_clock_and_missing_payload_fail_closed(cutover):
    with PayloadStore(cutover["public"]) as store:
        store.append_observations([Observation(cutover["kwargs"]["observer"],
            "2026-09/29844160/jenkins:29844160", "2026-09-29T00:00:00Z", apple.PUBLIC_FRAME_KIND,
            cutover["intermediate"]["payload_hashes"][0])])
    with pytest.raises(Exception, match="observation"):
        finalize(cutover)
    assert json.loads(cutover["kwargs"]["final_manifest_path"].read_text())["status"] == "FAILED"


@pytest.mark.parametrize("change,match", [
    ({"maximum_bytes": 2_000_000}, "limit"),
    ({"job": "polybot-re"}, "job/month"),
    ({"month": "2026-10"}, "job/month"),
    ({"effective_config_hash": "f" * 64}, "effective config"),
])
def test_identity_and_limit_cannot_be_invented(cutover, change, match):
    before = cutover["stage"].read_bytes()
    with pytest.raises(ValueError, match=match):
        finalize(cutover, **change)
    assert cutover["stage"].read_bytes() == before
    assert not cutover["kwargs"]["final_manifest_path"].exists()


def test_native_collector_lock_and_wal_guard(cutover):
    with cutover["kwargs"]["collector_lock"].open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            finalize(cutover)
    wal = Path(str(cutover["source"]) + "-wal")
    wal.touch()
    with pytest.raises(ValueError, match="offline"):
        finalize(cutover)


def test_finalization_requires_explicit_verified_intermediate_stage(cutover):
    with sqlite3.connect(cutover["stage"]) as db:
        db.execute("UPDATE watch SET next_due=0")
    with pytest.raises(ValueError, match="intermediate stage checksum"):
        finalize(cutover)


@pytest.mark.parametrize("reserved", ["collector", "stage_lock", "source_wal", "stage_journal"])
def test_final_receipt_cannot_replace_flock_or_create_sqlite_auxiliary(cutover, reserved):
    paths = {"collector": cutover["kwargs"]["collector_lock"],
             "stage_lock": Path(str(cutover["stage"]) + ".finalize.lock"),
             "source_wal": Path(str(cutover["source"]) + "-wal"),
             "stage_journal": Path(str(cutover["stage"]) + "-journal")}
    path = paths[reserved]
    with pytest.raises(ValueError, match="distinct explicit"):
        finalize(cutover, final_manifest_path=path)
    assert not path.exists()


def test_retry_manifest_hardlink_cannot_replace_the_native_lock(cutover, tmp_path):
    lock = cutover["kwargs"]["collector_lock"]
    lock.write_text("{}")
    receipt = tmp_path / "receipt.json"
    receipt.hardlink_to(lock)
    with pytest.raises(ValueError, match="aliases"):
        finalize(cutover, final_manifest_path=receipt)
    assert receipt.samefile(lock)
    assert lock.read_text() == "{}"


def test_read_only_verify_snapshot_lineage_substitution_is_explicit(cutover, tmp_path):
    manifest = finalize(cutover)
    backup_path = tmp_path / "source.backup.sqlite"
    with sqlite3.connect(cutover["source"]) as source, sqlite3.connect(backup_path) as backup:
        source.backup(backup)
    cutover["source"] = backup_path
    verify(cutover, manifest, source_snapshot_sha256=file_sha256(backup_path))
    with pytest.raises(ValueError, match="checksum chain"):
        verify(cutover, manifest, source_snapshot_sha256="0" * 64)


@pytest.mark.parametrize("key,value", [
    ("reserved_bytes", 0), ("page_cap", 999999), ("frame_count", 0),
    ("allowed_meta_additions_sha256", "0" * 64), ("observations_sha256", "0" * 64),
])
def test_claimed_finalization_counts_and_digests_not_trusted(cutover, key, value):
    manifest = finalize(cutover)
    manifest["apple_monthly_finalization"][key] = value
    with pytest.raises(ValueError, match="computed evidence"):
        verify(cutover, manifest)


@pytest.fixture
def retirement(cutover, tmp_path):
    from polybot_observability.market_data_bundle import verify_closure

    manifest = finalize(cutover)
    canonical = tmp_path / "live.sqlite"
    pinned = tmp_path / "pinned.sqlite"
    shutil.copyfile(cutover["stage"], canonical)
    shutil.copyfile(cutover["stage"], pinned)
    sidecar = Path(str(canonical) + ".storage-migration.json")
    shutil.copyfile(cutover["kwargs"]["final_manifest_path"], sidecar)
    review = tmp_path / "review"
    review.mkdir()
    source_key = "fixture-source-key"
    identity = {"source": "m5", "jenkins_job": "polybot-do", "strategy": "golden-apple",
                "runtime_job": "polybot-do", "remote_path": str(canonical)}
    digest = manifest["destination_sha256"]
    with PayloadReader(cutover["public"]) as reader:
        closure = {"contract": "daily-rsync-public-closure-v1", "status": "VERIFIED",
            "source_key": source_key, "source": "m5", "strategy": "golden-apple",
            "database_sha256": digest, **verify_closure(reader, manifest["payload_hashes"])}
    lineage = {"contract": "daily-rsync-storage-migration-lineage-v1", "status": "VERIFIED",
        "source_key": source_key, "source": "m5", "strategy": "golden-apple", "remote_path": str(canonical),
        "source_file_sha256": manifest["source_sha256"], "generation_sha256": digest,
        "closure_sha256": closure["closure_sha256"], "remote_sidecar": {
            "sidecar_path": str(sidecar), "sidecar_sha256": file_sha256(sidecar), "manifest": manifest}}
    pin = {"schema_version": 1, "source_key": source_key, "pinned_path": str(pinned), "sha256": digest,
           "storage_generation": digest, "quick_check": ["ok"], "database_month": "2026-09",
           "source_identity": identity, "public_payloads": closure, "storage_lineage": lineage}
    pin_path = tmp_path / "pin-manifest.json"
    pin_path.write_bytes(canonical_json := json.dumps(pin, sort_keys=True).encode())
    catalog_path = tmp_path / "catalog.sqlite"
    with sqlite3.connect(catalog_path) as db:
        db.executescript("""
        CREATE TABLE pins(source_key TEXT,pinned_path TEXT,manifest_json TEXT);
        CREATE TABLE storage_generations(source_key TEXT,generation_sha256 TEXT,attestation_json TEXT);
        CREATE TABLE artifacts(source_key TEXT,source TEXT,jenkins_job TEXT,strategy TEXT,runtime_job TEXT,
                               remote_path TEXT,local_sha256 TEXT,status TEXT);
        CREATE TABLE artifact_conflicts(source_key TEXT,existing_source_key TEXT,status TEXT);
        """)
        db.execute("INSERT INTO pins VALUES (?,?,?)", (source_key, str(pinned), canonical_json.decode()))
        db.execute("INSERT INTO storage_generations VALUES (?,?,?)", (source_key, digest, json.dumps(lineage)))
        db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)", (source_key, *identity.values(), digest, "SYNCED"))
    return {"cutover": cutover, "canonical": canonical, "kwargs": {
        "sidecar_path": sidecar, "review_path": review / sidecar.name, "pinned_db": pinned,
        "pin_manifest_path": pin_path, "catalog_path": catalog_path, "source_key": source_key,
        "source_identity": identity, "collector_lock": tmp_path / ".collector.lock"}}


def retire(retirement):
    cutover = retirement["cutover"]
    with PayloadReader(cutover["public"]) as reader:
        return retire_monthly_sidecar(cutover["source"], retirement["canonical"], cutover["stage"],
            references=PayloadReferences(reader=reader, cache_bytes=0), **retirement["kwargs"])


def test_retirement_requires_actual_pin_then_moves_only_fixed_sidecar(retirement):
    kwargs = retirement["kwargs"]
    before = {path: file_sha256(path) for path in (retirement["canonical"],
        retirement["cutover"]["source"], retirement["cutover"]["stage"], kwargs["pinned_db"])}
    receipt = retire(retirement)
    assert receipt["status"] == "RETIRED"
    assert not kwargs["sidecar_path"].exists()
    assert file_sha256(kwargs["review_path"]) == receipt["sidecar_sha256"]
    assert {path: file_sha256(path) for path in before} == before
    evidence = Path(str(kwargs["review_path"]) + ".retirement.json")
    assert json.loads(evidence.read_text()) == receipt


@pytest.mark.parametrize("mutation", [
    "DELETE FROM pins", "DELETE FROM storage_generations", "UPDATE artifacts SET local_sha256='changed'",
    "INSERT INTO artifact_conflicts VALUES ('fixture-source-key',NULL,'OPEN')",
])
def test_retirement_rejects_invented_or_unverified_catalog_pin(retirement, mutation):
    with sqlite3.connect(retirement["kwargs"]["catalog_path"]) as db:
        db.execute(mutation)
    with pytest.raises(ValueError, match="official catalog"):
        retire(retirement)
    assert retirement["kwargs"]["sidecar_path"].is_file()
    assert not retirement["kwargs"]["review_path"].exists()


@pytest.mark.parametrize("target", ["canonical", "checkpoint", "pin"])
def test_retirement_rejects_resume_append_or_modified_checkpoint_pin(retirement, target):
    path = {"canonical": retirement["canonical"], "checkpoint": retirement["cutover"]["stage"],
            "pin": retirement["kwargs"]["pinned_db"]}[target]
    with sqlite3.connect(path) as db:
        db.execute("UPDATE watch SET next_due=next_due+1")
    with pytest.raises(ValueError, match="checksum chain|checkpoint/pin SHA"):
        retire(retirement)
    assert retirement["kwargs"]["sidecar_path"].is_file()


def test_retirement_does_not_accept_live_hardlink_as_checkpoint(retirement):
    retirement["canonical"].unlink()
    retirement["canonical"].hardlink_to(retirement["cutover"]["stage"])
    with pytest.raises(ValueError, match="distinct inodes"):
        retire(retirement)


def test_retirement_crash_after_move_preserves_authoritative_sidecar_location(retirement, monkeypatch):
    from polybot_observability import market_data_apple_finalize as module

    original_write = module._write_manifest

    def lose_completion(path, receipt):
        if receipt["status"] == "RETIRED":
            raise OSError("simulated crash after move")
        original_write(path, receipt)

    monkeypatch.setattr(module, "_write_manifest", lose_completion)
    with pytest.raises(OSError, match="simulated crash"):
        retire(retirement)
    assert not retirement["kwargs"]["sidecar_path"].exists()
    assert retirement["kwargs"]["review_path"].is_file()
    evidence = Path(str(retirement["kwargs"]["review_path"]) + ".retirement.json")
    assert json.loads(evidence.read_text())["status"] == "PREPARED"
