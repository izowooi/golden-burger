"""Physical ownership checks supplement migration's decoded row equality."""

import hashlib
import json
import sqlite3
import zlib

import pytest

from polybot_observability import market_data_migrate as migration
from polybot_observability.market_data_index import ReceiptContext
from polybot_observability.market_data_mixed import is_mixed_payload
from polybot_observability.market_data_projection_links import RUN_TABLE
from polybot_observability.market_data_refs import PayloadReferences, externalize_row, parse_reference
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_market_data_migrate import MemoryStore
from test_market_data_projection_migrate import source_database


def run(source, target, refs, strategy="golden-black", **options):
    return migration.migrate_public_bodies(
        source, target, strategy=strategy, source_sha256=migration.file_sha256(source),
        references=refs, **options,
    )


def assert_failed(path):
    assert json.loads(path.with_suffix(path.suffix + ".migration.json").read_text())["status"] == "FAILED"


def mixed_cell(refs):
    raw = ' { "condition_id" : "public-c", "fee_metadata" : {"PRIVATE_FEE": 7} } '
    encoded = externalize_row("golden-pomegranate", "market_metadata_versions",
                               {"metadata_json": raw}, references=refs)["metadata_json"]
    assert is_mixed_payload(encoded)
    return raw, encoded


@pytest.mark.parametrize("family", ["PMDATA", "PMMIX", "malformed-PMDATA"])
def test_unregistered_source_private_reference_fails_without_changing_source(tmp_path, family):
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    value = (refs.encode_many(["PRIVATE_SENTINEL"])[0] if family == "PMDATA"
             else mixed_cell(refs)[1] if family == "PMMIX" else "\x1ePMDATA9:unsupported")
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE private_ledger(id INTEGER PRIMARY KEY,owner TEXT)")
        connection.execute("INSERT INTO private_ledger VALUES(1,?)", (value,))
    digest = migration.file_sha256(source)
    with pytest.raises(ValueError, match="unapproved reference in private cell"):
        run(source, target, refs)
    assert migration.file_sha256(source) == digest
    assert_failed(target)


def test_policy_only_preflight_needs_no_public_store_and_checks_mixed_profile(tmp_path):
    # This isolated store is needed only to prepare a valid envelope.
    store = MemoryStore(); refs = PayloadReferences(store, store)
    _, envelope = mixed_cell(refs)
    marker = refs.encode_many([b"public-response"])[0]
    with sqlite3.connect(tmp_path / "source.db") as connection:
        connection.execute("CREATE TABLE raw_payloads(payload_gzip BLOB)")
        connection.execute("INSERT INTO raw_payloads VALUES(?)", (marker,))
        migration.validate_private_reference_ownership(connection, "golden-black")
        connection.execute("DROP TABLE raw_payloads")
        connection.execute("CREATE TABLE market_metadata_versions(metadata_json TEXT)")
        connection.execute("INSERT INTO market_metadata_versions VALUES(?)", (envelope,))
        migration.validate_private_reference_ownership(connection, "golden-pomegranate")
        connection.execute("ALTER TABLE market_metadata_versions RENAME TO events")
        connection.execute("ALTER TABLE events RENAME COLUMN metadata_json TO event_json")
        with pytest.raises(ValueError, match="another ownership profile"):
            migration.validate_private_reference_ownership(connection, "golden-guava")


@pytest.mark.parametrize("second_generation", [False, True])
def test_projection_run_reference_is_rejected_before_it_becomes_private_plaintext(tmp_path, second_generation):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    source_database(source, "golden-blueberry")
    with PayloadStore(tmp_path / "public.db") as writer, PayloadReader(tmp_path / "public.db") as reader:
        refs = PayloadReferences(reader, writer)
        marker = refs.encode_many(["PRIVATE_RUN_SENTINEL"])[0]
        if second_generation:
            first = tmp_path / "first.db"
            run(source, first, refs, "golden-blueberry", include_projections=True,
                receipt_context=ReceiptContext("source", "runtime", "job"))
            source = first
            with sqlite3.connect(source) as connection:
                connection.execute(f"UPDATE {RUN_TABLE} SET run_id=? WHERE id=(SELECT MIN(id) FROM {RUN_TABLE})", (marker,))
        else:
            with sqlite3.connect(source) as connection:
                connection.execute("UPDATE market_snapshots SET run_id=? WHERE id=7", (marker,))
        digest = migration.file_sha256(source)
        with pytest.raises(ValueError, match="unapproved reference in private cell"):
            run(source, target, refs, "golden-blueberry", include_projections=True,
                receipt_context=ReceiptContext("source", "runtime", "job"))
        assert migration.file_sha256(source) == digest
        assert_failed(target)


def test_final_verification_rejects_whole_private_externalization_despite_logical_equality(tmp_path, monkeypatch):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE private_ledger(id INTEGER PRIMARY KEY,owner TEXT)")
        connection.execute("INSERT INTO private_ledger VALUES(1,'PRIVATE_SENTINEL')")
    store = MemoryStore(); refs = PayloadReferences(store, store)
    def unapproved(strategy, table, rows, *, references, **options):
        return [{**row, "owner": references.encode_many([row["owner"]])[0]} for row in rows]
    monkeypatch.setattr(migration, "externalize_rows", unapproved)
    with pytest.raises(ValueError, match="unapproved reference in private cell"):
        run(source, target, refs)
    assert_failed(target)


def test_final_verification_preserves_generated_private_values(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE raw_payloads(payload_id INTEGER PRIMARY KEY,payload_gzip BLOB,"
                           "private_derived_length INTEGER GENERATED ALWAYS AS(length(payload_gzip)) VIRTUAL)")
        connection.execute("INSERT INTO raw_payloads(payload_id,payload_gzip) VALUES(1,?)", (b"public",))
    store = MemoryStore(); refs = PayloadReferences(store, store)
    with pytest.raises(ValueError, match="private physical cell changed.*private_derived_length"):
        run(source, target, refs)
    assert_failed(target)


def test_registered_public_and_mixed_references_remain_valid_across_generations(tmp_path):
    store = MemoryStore(); refs = PayloadReferences(store, store)
    original, envelope = mixed_cell(refs)
    source = tmp_path / "mixed.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE market_metadata_versions(id INTEGER PRIMARY KEY,metadata_json TEXT)")
        connection.executemany("INSERT INTO market_metadata_versions VALUES(?,?)", [(1, original), (2, envelope)])
    for number in (1, 2):
        target = tmp_path / f"mixed-{number}.db"
        result = run(source, target, refs, "golden-pomegranate")
        assert result["status"] == "VERIFIED"
        with sqlite3.connect(target) as connection:
            cells = [row[0] for row in connection.execute("SELECT metadata_json FROM market_metadata_versions ORDER BY id")]
            migration.validate_private_reference_ownership(connection, "golden-pomegranate", references=refs)
        assert all(is_mixed_payload(value) for value in cells)
        assert refs.decode_many(cells) == [original, original]
        source = target


def test_apple_legacy_roles_allow_only_public_references_and_preserve_private_blobs(tmp_path):
    store = MemoryStore(); refs = PayloadReferences(store, store)
    source = tmp_path / "legacy.db"
    public_raw, private_raw = b'{"id":"public-event"}', b'{"PRIVATE_METRIC":7}'
    public_hash, private_hash = [hashlib.sha256(value).hexdigest() for value in (public_raw, private_raw)]
    public_blob, private_blob = zlib.compress(public_raw), zlib.compress(private_raw)
    marker = refs.encode_many([public_blob])[0]
    with sqlite3.connect(source) as connection:
        connection.executescript("CREATE TABLE payloads(hash TEXT PRIMARY KEY,encoding TEXT,bytes INTEGER,compressed BLOB);"
                                 "CREATE TABLE events(payload_hash TEXT REFERENCES payloads(hash));"
                                 "CREATE TABLE book_observations(metrics_hash TEXT REFERENCES payloads(hash),depth_hash TEXT REFERENCES payloads(hash));")
        connection.executemany("INSERT INTO payloads VALUES(?,'zlib',?,?)", [
            (public_hash, len(public_raw), marker), (private_hash, len(private_raw), private_blob)])
        connection.execute("INSERT INTO events VALUES(?)", (public_hash,))
        connection.execute("INSERT INTO book_observations VALUES(?,NULL)", (private_hash,))
    result = run(source, tmp_path / "approved.db", refs, "golden-apple")
    assert result["status"] == "VERIFIED"
    with sqlite3.connect(tmp_path / "approved.db") as connection:
        actual = dict(connection.execute("SELECT hash,compressed FROM payloads"))
    assert parse_reference(actual[public_hash]) and actual[private_hash] == private_blob
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE payloads SET compressed=? WHERE hash=?",
                           (refs.encode_many([private_blob])[0], private_hash))
    with pytest.raises(ValueError, match="unapproved reference in private cell"):
        run(source, tmp_path / "rejected.db", refs, "golden-apple")
    assert_failed(tmp_path / "rejected.db")


def test_registered_mixed_column_rejects_a_whole_wrapper_body_reference(tmp_path):
    store = MemoryStore(); refs = PayloadReferences(store, store)
    original, _ = mixed_cell(refs)
    source = tmp_path / "whole-wrapper.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE market_metadata_versions(metadata_json TEXT)")
        connection.execute("INSERT INTO market_metadata_versions VALUES(?)", (refs.encode_many([original])[0],))
    with pytest.raises(ValueError, match="unapproved reference in private cell"):
        run(source, tmp_path / "rejected.db", refs, "golden-pomegranate")
