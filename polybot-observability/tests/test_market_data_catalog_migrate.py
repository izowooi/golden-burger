"""Offline catalog migration preserves exact rowids, private cells and lineage."""

import hashlib
import json
import sqlite3

import pytest

from polybot_observability.market_data_catalog_links import (
    CONTEXT_TABLE,
    iter_catalog_private_rows,
    iter_catalog_rows,
)
from polybot_observability.market_data_catalog_profiles import (
    CATALOG_PROFILES,
    catalog_profile,
)
from polybot_observability.market_data_index import ReceiptContext
from polybot_observability.market_data_migrate import (
    _update_digest,
    file_sha256,
    migrate_public_bodies,
    validate_private_reference_ownership,
)
from polybot_observability.market_data_refs import PayloadReferences, PREFIX
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_market_data_catalog_links import make_source, row
from test_market_data_projection_migrate import source_database as snapshots_source


CONTEXT = ReceiptContext("source", "runtime", "job")


def catalog_source(path, strategy, *, combined=False):
    if combined:
        snapshots_source(path, strategy)
    with sqlite3.connect(path) as database:
        if combined:
            # Reuse source catalog DDL builder without creating a second ledger.
            database.execute(
                "ALTER TABLE private_ledger RENAME TO prior_private_ledger"
            )
        make_source(database, strategy)
        columns = catalog_profile(strategy).column_names
        values = [row(strategy, "z-condition"), row(strategy, "a-condition")]
        for rowid, record in zip((-7, 83), values):
            database.execute(
                "INSERT INTO market_catalog(rowid,"
                + ",".join(columns)
                + ") VALUES("
                + ",".join("?" for _ in range(len(columns) + 1))
                + ")",
                (rowid, *(record[name] for name in columns)),
            )
    with connect(path) as original:
        logical = list(iter_catalog_rows(original, strategy, include_rowid=True))
    return logical


def migrate(source, destination, strategy, refs, **kwargs):
    return migrate_public_bodies(
        source,
        destination,
        strategy=strategy,
        source_sha256=file_sha256(source),
        references=refs,
        include_catalog=True,
        receipt_context=CONTEXT,
        **kwargs,
    )


@pytest.mark.parametrize("strategy", CATALOG_PROFILES)
def test_catalog_only_full_schema_real_rowid_and_typed_private_proof(
    tmp_path, strategy
):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    expected = catalog_source(source, strategy)
    original_sha = file_sha256(source)
    with sqlite3.connect(source) as original:
        original_schema = original.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
        ).fetchall()
        original_ledger = original.execute("SELECT * FROM private_ledger").fetchall()
    with (
        PayloadStore(tmp_path / "public.db") as writer,
        PayloadReader(tmp_path / "public.db") as reader,
    ):
        refs = PayloadReferences(reader=reader, writer=writer)
        manifest = migrate(source, target, strategy, refs, batch_rows=1)
        assert manifest["status"] == "VERIFIED" and file_sha256(source) == original_sha
        proof = manifest["public_projection_records"]
        assert proof["contract"] == "public-projection-closure-v2"
        assert proof["layouts"] == {
            "market_catalog": sorted(catalog_profile(strategy).groups)
        }
        assert proof["layout_receipt_counts"] == {"market_catalog": 4}
        assert manifest["tables"]["market_catalog"]["implicit_rowid_preserved"] is True
        assert (
            manifest["tables"]["market_catalog"]["key_basis"]
            == "declared-primary-key+rowid-v1"
        )
        digest = hashlib.sha256()
        for record in expected:
            _update_digest(digest, record)
        assert (
            manifest["tables"]["market_catalog"]["logical_sha256"] == digest.hexdigest()
        )
        with connect(target, references=PayloadReferences(reader=reader)) as restored:
            assert (
                list(iter_catalog_rows(restored, strategy, include_rowid=True))
                == expected
            )
            assert (
                restored.execute("SELECT * FROM private_ledger").fetchall()
                == original_ledger
            )
            assert restored.execute(
                "SELECT COUNT(*) FROM main.market_catalog"
            ).fetchone() == (0,)
            schema = restored.execute(
                "SELECT type,name,tbl_name,sql FROM main.sqlite_master WHERE sql IS NOT NULL AND name NOT GLOB '_public_catalog_*' ORDER BY type,name"
            ).fetchall()
            assert schema == original_schema
            columns = (
                "__rowid__",
                "condition_id",
                *catalog_profile(strategy).private_columns,
            )
            with sqlite3.connect(source) as original:
                assert list(
                    iter_catalog_private_rows(restored, strategy, columns)
                ) == list(iter_catalog_private_rows(original, strategy, columns))


def test_combined_catalog_snapshot_migration_and_second_generation_preserve_unified_owner(
    tmp_path,
):
    strategy = "golden-plum"
    source, first, second = (
        tmp_path / "source.db",
        tmp_path / "first.db",
        tmp_path / "second.db",
    )
    expected = catalog_source(source, strategy, combined=True)
    with (
        PayloadStore(tmp_path / "public.db") as writer,
        PayloadReader(tmp_path / "public.db") as reader,
    ):
        refs = PayloadReferences(reader=reader, writer=writer)
        initial = migrate(source, first, strategy, refs, include_projections=True)
        assert initial["tables"]["market_catalog"]["externalized_catalog_rows"] == 2
        assert (
            initial["tables"]["market_snapshots"]["externalized_projection_rows"] == 2
        )
        proof = initial["public_projection_records"]
        assert proof["layout_receipt_counts"] == {
            "market_catalog": 4,
            "market_snapshots": 2,
        }
        assert set(proof["layouts"]) == {"market_catalog", "market_snapshots"}
        frozen = file_sha256(first)
        final = migrate_public_bodies(
            first,
            second,
            strategy=strategy,
            source_sha256=frozen,
            references=refs,
            include_projections=True,
            include_catalog=True,
        )
        assert file_sha256(first) == frozen
        assert final["public_projection_records"] == proof
        assert final["tables"] == initial["tables"]
        with connect(second, references=refs) as restored:
            assert (
                list(iter_catalog_rows(restored, strategy, include_rowid=True))
                == expected
            )
        with pytest.raises(ValueError, match="explicit catalog migration"):
            migrate_public_bodies(
                first,
                tmp_path / "unapproved.db",
                strategy=strategy,
                source_sha256=frozen,
                references=refs,
                include_projections=True,
            )
        with pytest.raises(ValueError, match="existing source owner"):
            migrate_public_bodies(
                first,
                tmp_path / "wrong-owner.db",
                strategy=strategy,
                source_sha256=frozen,
                references=refs,
                include_projections=True,
                include_catalog=True,
                receipt_context=ReceiptContext("other", "runtime", "job"),
            )


def test_body_only_discloses_retained_public_catalog_rows(tmp_path):
    source = tmp_path / "source.db"
    catalog_source(source, "golden-blueberry")
    with (
        PayloadStore(tmp_path / "public.db") as writer,
        PayloadReader(tmp_path / "public.db") as reader,
    ):
        manifest = migrate_public_bodies(
            source,
            tmp_path / "body.db",
            strategy="golden-blueberry",
            source_sha256=file_sha256(source),
            references=PayloadReferences(reader, writer),
        )
        assert manifest["remaining_public_catalog_rows"] == 2
        assert "public_projection_records" not in manifest
        assert "externalized_catalog_rows" not in manifest["tables"]["market_catalog"]


@pytest.mark.parametrize(
    "damage", ["condition", "rowid", "receipt", "private-reference", "private-type"]
)
def test_existing_catalog_source_tamper_is_not_silently_authorized(
    tmp_path, damage, monkeypatch
):
    strategy = "golden-plum"
    source = tmp_path / "source.db"
    first = tmp_path / "first.db"
    catalog_source(source, strategy)
    with (
        PayloadStore(tmp_path / "public.db") as writer,
        PayloadReader(tmp_path / "public.db") as reader,
    ):
        refs = PayloadReferences(reader, writer)
        migrate(source, first, strategy, refs)
        with sqlite3.connect(first) as database:
            if damage == "condition":
                database.execute(
                    f"UPDATE {CONTEXT_TABLE} SET condition_id='wrong' WHERE _rowid=-7"
                )
            elif damage == "rowid":
                database.execute(
                    f"UPDATE {CONTEXT_TABLE} SET _rowid=987 WHERE _rowid=-7"
                )
            elif damage == "private-reference":
                digest = writer.put_many([b"PRIVATE_CONTEXT"])[0]
                database.execute(
                    f"UPDATE {CONTEXT_TABLE} SET resolution_status=? WHERE _rowid=-7",
                    (PREFIX + "T:" + digest,),
                )
            elif damage == "private-type":
                # A private INTEGER affinity field can physically contain text.
                # A migration must preserve the value, never coerce it to zero.
                database.execute(
                    f"UPDATE {CONTEXT_TABLE} SET last_event_set_complete='unknown' WHERE _rowid=-7"
                )
        if damage == "receipt":
            monkeypatch.setattr(
                reader, "get_projection_receipts", lambda *args, **kwargs: []
            )
        frozen = file_sha256(first)
        target = tmp_path / "target.db"
        if damage == "private-type":
            final = migrate(first, target, strategy, refs)
            assert final["status"] == "VERIFIED"
            with connect(target, references=refs) as restored:
                assert restored.execute(
                    "SELECT last_event_set_complete,typeof(last_event_set_complete) FROM market_catalog WHERE condition_id='z-condition'"
                ).fetchone() == ("unknown", "text")
        else:
            with pytest.raises((ValueError, sqlite3.Error)):
                migrate(first, target, strategy, refs)
            sidecar = json.loads(target.with_suffix(".db.migration.json").read_text())
            assert sidecar["status"] == "FAILED"
        assert file_sha256(first) == frozen


def test_private_target_rewrite_is_detected_before_verified(tmp_path, monkeypatch):
    import polybot_observability.market_data_catalog_links as links

    strategy = "golden-plum"
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    catalog_source(source, strategy)
    real = links.upsert_shared_catalog

    def tampered(connection, *args, **kwargs):
        result = real(connection, *args, **kwargs)
        connection.execute(
            f"UPDATE {CONTEXT_TABLE} SET resolution_status='ALTERED_PRIVATE'"
        )
        return result

    monkeypatch.setattr(links, "upsert_shared_catalog", tampered)
    with (
        PayloadStore(tmp_path / "public.db") as writer,
        PayloadReader(tmp_path / "public.db") as reader,
    ):
        with pytest.raises(ValueError, match="private physical cell changed"):
            migrate(source, target, strategy, PayloadReferences(reader, writer))
    assert (
        json.loads(target.with_suffix(".db.migration.json").read_text())["status"]
        == "FAILED"
    )


def test_catalog_private_reference_preflight_has_no_public_io(tmp_path):
    strategy = "golden-blueberry"
    source = tmp_path / "source.db"
    catalog_source(source, strategy)
    with sqlite3.connect(source) as database:
        database.execute(
            "UPDATE market_catalog SET resolution_status=?", (PREFIX + "T:" + "a" * 64,)
        )
    with sqlite3.connect(source) as database:
        with pytest.raises(ValueError, match="unapproved reference in private cell"):
            validate_private_reference_ownership(database, strategy)


def test_frozen_source_hash_required_and_unknown_catalog_shape_rejected(tmp_path):
    source = tmp_path / "source.db"
    catalog_source(source, "golden-blueberry")
    with (
        PayloadStore(tmp_path / "public.db") as writer,
        PayloadReader(tmp_path / "public.db") as reader,
    ):
        refs = PayloadReferences(reader, writer)
        with pytest.raises(ValueError, match="source checksum mismatch"):
            migrate_public_bodies(
                source,
                tmp_path / "wrong-hash.db",
                strategy="golden-blueberry",
                source_sha256="0" * 64,
                references=refs,
                include_catalog=True,
                receipt_context=CONTEXT,
            )
        with sqlite3.connect(source) as db:
            db.execute("ALTER TABLE market_catalog ADD COLUMN unreviewed TEXT")
        with pytest.raises(ValueError, match="schema differs"):
            migrate(source, tmp_path / "unknown.db", "golden-blueberry", refs)
        assert writer.projection_stats()["record_count"] == 0


def test_source_identity_is_explicit_and_source_hash_is_rechecked_after_copy(
    tmp_path, monkeypatch
):
    strategy = "golden-blueberry"
    source = tmp_path / "source.db"
    catalog_source(source, strategy)
    monkeypatch.setenv("PUBLIC_MARKET_DATA_SOURCE", "not-proven-source")
    monkeypatch.setenv("JOB_NAME", "not-proven-job")
    with (
        PayloadStore(tmp_path / "public.db") as writer,
        PayloadReader(tmp_path / "public.db") as reader,
    ):
        refs = PayloadReferences(reader, writer)
        with pytest.raises(ValueError, match="explicit source/Jenkins job/runtime"):
            migrate_public_bodies(
                source,
                tmp_path / "no-owner.db",
                strategy=strategy,
                source_sha256=file_sha256(source),
                references=refs,
                include_catalog=True,
            )
        assert writer.projection_stats()["record_count"] == 0
        original = file_sha256(source)

        def changed(table, result):
            if table == "market_catalog":
                with sqlite3.connect(source) as db:
                    db.execute(
                        "UPDATE market_catalog SET question='changed after copied'"
                    )

        target = tmp_path / "source-changed.db"
        with pytest.raises(ValueError, match="source changed during migration"):
            migrate_public_bodies(
                source,
                target,
                strategy=strategy,
                source_sha256=original,
                references=refs,
                include_catalog=True,
                receipt_context=CONTEXT,
                progress=changed,
            )
        assert (
            json.loads(target.with_suffix(".db.migration.json").read_text())["status"]
            == "FAILED"
        )


def test_missing_catalog_ack_fails_manifest_and_fresh_retry_reuses_records(
    tmp_path, monkeypatch
):
    strategy = "golden-blueberry"
    source = tmp_path / "source.db"
    target = tmp_path / "lost-ack.db"
    catalog_source(source, strategy)
    with (
        PayloadStore(tmp_path / "public.db") as writer,
        PayloadReader(tmp_path / "public.db") as reader,
    ):
        refs = PayloadReferences(reader, writer)
        original = writer.append_projection_receipts

        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("receipt ACK lost")

        monkeypatch.setattr(writer, "append_projection_receipts", lost)
        with pytest.raises(RuntimeError, match="ACK lost"):
            migrate(source, target, strategy, refs, batch_rows=1)
        assert (
            json.loads(target.with_suffix(".db.migration.json").read_text())["status"]
            == "FAILED"
        )
        initial = writer.projection_stats()["record_count"]
        monkeypatch.setattr(writer, "append_projection_receipts", original)
        recovered = migrate(
            source, tmp_path / "recovered.db", strategy, refs, batch_rows=1
        )
        assert recovered["status"] == "VERIFIED"
        assert initial == 2 and writer.projection_stats()["record_count"] == 4
        with pytest.raises(ValueError, match="new distinct file"):
            migrate(source, target, strategy, refs)


def test_catalog_flag_combines_with_scalar_scope_without_inventing_absent_catalog(
    tmp_path,
):
    source = tmp_path / "scalar.db"
    with sqlite3.connect(source) as database:
        database.executescript("""CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY, condition_id TEXT NOT NULL,
            probability REAL NOT NULL, liquidity REAL, volume_24h REAL, timestamp DATETIME);
            INSERT INTO market_snapshots VALUES(7,'condition',0.75,1000.0,2000.0,'2026-09-30 00:00:00');""")
    with (
        PayloadStore(tmp_path / "public.db") as writer,
        PayloadReader(tmp_path / "public.db") as reader,
    ):
        result = migrate_public_bodies(
            source,
            tmp_path / "target.db",
            strategy="golden-date",
            source_sha256=file_sha256(source),
            references=PayloadReferences(reader, writer),
            include_scalars=True,
            include_catalog=True,
            receipt_context=CONTEXT,
        )
        assert result["status"] == "VERIFIED"
        assert result["public_scalar_records"]["record_count"] == 1
        assert (
            "market_catalog" not in result["tables"]
            and "public_projection_records" not in result
        )


def test_cli_requires_catalog_capability_and_passes_explicit_scope(
    tmp_path, monkeypatch, capsys
):
    import polybot_observability.market_data_migrate as module

    captured = {}

    class Writer:
        def __init__(self, path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def require_capabilities(self, values):
            captured["capabilities"] = values

    class Reader:
        def __init__(self, path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def migrated(*args, **kwargs):
        captured["options"] = kwargs
        return {"status": "VERIFIED"}

    monkeypatch.setattr("polybot_observability.market_data_client.StoreClient", Writer)
    monkeypatch.setattr("polybot_observability.market_data_store.PayloadReader", Reader)
    monkeypatch.setattr(module, "migrate_public_bodies", migrated)
    monkeypatch.setattr(module, "migration_storage_guard", lambda *a, **k: lambda: None)
    assert (
        module.main(
            [
                "--source",
                str(tmp_path / "source.db"),
                "--source-sha256",
                "a" * 64,
                "--strategy",
                "golden-blueberry",
                "--output",
                str(tmp_path / "target.db"),
                "--storage-root",
                str(tmp_path),
                "--socket",
                str(tmp_path / "service.sock"),
                "--public-db",
                str(tmp_path / "public.db"),
                "--include-catalog",
                "--include-projections",
                "--source-identity",
                "source",
                "--runtime",
                "runtime",
                "--jenkins-job",
                "job",
            ]
        )
        == 0
    )
    assert {"public-catalog-groups-v1", "public-projection-block-store-v1"} <= captured[
        "capabilities"
    ]
    assert captured["options"]["include_catalog"] is True
    assert captured["options"]["include_projections"] is True
    assert "VERIFIED" in capsys.readouterr().out
