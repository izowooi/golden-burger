import sqlite3

import pytest
from polybot_observability import market_data_scalar_bundle as bundle
from polybot_observability.market_data_bundle import export_bundle, import_bundle
from polybot_observability.market_data_migrate import file_sha256
from polybot_observability.market_data_scalars import ScalarReceipt, ScalarSnapshot
from polybot_observability.market_data_store import PayloadStore, StoreError


def value(i=1):
    return ScalarSnapshot(
        "condition-1", 0.7, None, 0.0, f"2026-09-01 00:00:{i:02}.000000"
    )


def test_scalar_bundle_preserves_values_sources_and_reused_local_ids(tmp_path):
    with (
        PayloadStore(tmp_path / "remote.db") as source,
        PayloadStore(tmp_path / "local.db") as target,
    ):
        snapshots = [value(1), value(2)]
        refs = source.put_scalar_snapshots(snapshots)
        ids = [ref.record_id for ref in refs]
        authority = source.scalar_authority_identity()
        receipts = [
            ScalarReceipt("A", 1, ids[0]),
            ScalarReceipt("A", 1, ids[1]),
            ScalarReceipt("B", 1, ids[0]),
        ]
        source.append_scalar_receipts(receipts, authority_uuid=authority)
        # Unrelated body data must not enter a scalar-only transfer.
        source.put_many([b"not requested"])
        path = tmp_path / "bundle.db"
        manifest = bundle.export_scalar_bundle(
            source, ids, path, authority_uuid=authority
        )
        result = bundle.import_scalar_bundle(path, manifest, target)
        assert result["imported_record_count"] == 2
        assert [
            record.snapshot
            for record in target.get_scalar_records(ids, authority_uuid=authority)
        ] == snapshots
        assert (
            target.get_scalar_receipts(
                [row.row() for row in receipts], authority_uuid=authority
            )
            == receipts
        )
        assert target.stats()["payload_count"] == 0
        assert [
            record.snapshot
            for record in target.query_scalar_snapshots(
                "condition-1", namespace="B", authority_uuid=authority
            )
        ] == snapshots[:1]


def test_missing_values_only_transfer_once_across_receipt_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "PAGE_SIZE", 1)
    with (
        PayloadStore(tmp_path / "remote.db") as source,
        PayloadStore(tmp_path / "local.db") as target,
    ):
        ids = [ref.record_id for ref in source.put_scalar_snapshots([value()])]
        authority = source.scalar_authority_identity()
        source.append_scalar_receipts(
            [ScalarReceipt("source", i, ids[0]) for i in range(3)],
            authority_uuid=authority,
        )
        after = None
        transferred = []
        for i in range(3):
            path = tmp_path / f"page-{i}.db"
            manifest = bundle.export_scalar_bundle(
                source,
                ids,
                path,
                authority_uuid=authority,
                transferred_ids=ids if i == 0 else [],
                receipt_after=after,
            )
            result = bundle.import_scalar_bundle(path, manifest, target)
            transferred.append(result["imported_record_count"])
            after = manifest["next_receipt_after"]
        assert transferred == [1, 0, 0] and after is None
        assert target.scalar_stats()["receipt_count"] == 3
        assert [
            record.snapshot
            for record in target.get_scalar_records(ids, authority_uuid=authority)
        ] == [value()]


def test_new_receipt_for_existing_local_value_is_imported_without_body(tmp_path):
    with (
        PayloadStore(tmp_path / "remote.db") as source,
        PayloadStore(tmp_path / "local.db") as target,
    ):
        ids = [ref.record_id for ref in source.put_scalar_snapshots([value()])]
        authority = source.scalar_authority_identity()
        target.import_scalar_records(
            authority, source.get_scalar_records(ids, authority_uuid=authority)
        )
        source.append_scalar_receipts(
            [ScalarReceipt("late", 17, ids[0])], authority_uuid=authority
        )
        path = tmp_path / "receipt-only.db"
        manifest = bundle.export_scalar_bundle(
            source, ids, path, authority_uuid=authority, transferred_ids=[]
        )
        bundle.import_scalar_bundle(path, manifest, target)
        assert target.get_scalar_receipts(
            [("late", 17, ids[0])], authority_uuid=authority
        ) == [ScalarReceipt("late", 17, ids[0])]


@pytest.mark.parametrize(
    "damage", ["private_table", "value", "receipt", "omitted_value"]
)
def test_scalar_bundle_rejects_forged_proof_before_target_publication(tmp_path, damage):
    with (
        PayloadStore(tmp_path / "remote.db") as source,
        PayloadStore(tmp_path / "local.db") as target,
    ):
        ids = [ref.record_id for ref in source.put_scalar_snapshots([value()])]
        authority = source.scalar_authority_identity()
        source.append_scalar_receipts(
            [ScalarReceipt("source", 1, ids[0])], authority_uuid=authority
        )
        path = tmp_path / "bad.db"
        manifest = bundle.export_scalar_bundle(
            source,
            ids,
            path,
            authority_uuid=authority,
            transferred_ids=[] if damage == "omitted_value" else None,
        )
        if damage == "receipt":
            manifest["receipt_sha256"] = "f" * 64
        elif damage != "omitted_value":
            with sqlite3.connect(path) as connection:
                if damage == "private_table":
                    connection.execute("CREATE TABLE private_account(secret TEXT)")
                else:
                    for (name,) in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='scalar_hot'"
                    ).fetchall():
                        connection.execute('DROP TRIGGER "' + name + '"')
                    connection.execute("UPDATE scalar_hot SET probability=.4")
            manifest["file_sha256"] = file_sha256(path)
        with pytest.raises((ValueError, StoreError)):
            bundle.import_scalar_bundle(path, manifest, target)
        assert target.scalar_stats()["snapshot_count"] == 0
        assert target.scalar_stats()["receipt_count"] == 0


def test_body_bundle_keeps_unrelated_scalar_values_out(tmp_path):
    with (
        PayloadStore(tmp_path / "remote.db") as source,
        PayloadStore(tmp_path / "local.db") as target,
    ):
        hashes = source.put_many([b"public-body"])
        source.put_scalar_snapshots([value()])
        path = tmp_path / "bodies.db"
        manifest = export_bundle(source, hashes, path)
        import_bundle(path, manifest, target)
        assert target.get_many(hashes) == [b"public-body"]
        assert target.scalar_stats()["snapshot_count"] == 0


@pytest.mark.parametrize("orphan", ["hot", "block", "subjects"])
def test_scalar_bundle_rejects_unrequested_orphan_storage(tmp_path, orphan):
    with (
        PayloadStore(tmp_path / "remote.db") as source,
        PayloadStore(tmp_path / "local.db") as target,
    ):
        refs = source.put_scalar_snapshots([value()])
        authority = source.scalar_authority_identity()
        path = tmp_path / "orphan.db"
        manifest = bundle.export_scalar_bundle(
            source, [refs[0].record_id], path, authority_uuid=authority
        )
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.create_function(
                "scalar_write_mode",
                0,
                lambda: "pack" if orphan == "block" else "import",
            )
            if orphan == "hot":
                connection.execute(
                    "INSERT INTO scalar_hot VALUES(999,.5,NULL,NULL,?)", (bytes(32),)
                )
            elif orphan == "block":
                connection.execute(
                    "INSERT INTO scalar_blocks VALUES(999,?,1,27,?)",
                    (bytes(32), b"UNREQUESTED_PRIVATE_SENTINEL"),
                )
            else:
                connection.execute(
                    "INSERT INTO public_subject_sets VALUES(?)", ("f" * 64,)
                )
                connection.execute(
                    "INSERT INTO public_subject_set_members VALUES(?,?,?)",
                    ("f" * 64, "token", "unrequested"),
                )
        manifest["file_sha256"] = file_sha256(path)
        with pytest.raises(ValueError, match="undeclared|unreferenced"):
            bundle.import_scalar_bundle(path, manifest, target)
        assert target.scalar_stats()["snapshot_count"] == 0
