import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from polybot_observability.market_data_catalog_links import CONTEXT_TABLE, iter_catalog_rows
from polybot_observability.market_data_index import ReceiptContext
from polybot_observability.market_data_migrate import migrate_public_bodies
from polybot_observability.market_data_projection_profiles import snapshot_profile
from polybot_observability.market_data_projections import ProjectionReceipt
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_catalog_payloads import catalog_row, create_catalog_schema
from test_projection_payloads import ProjectionRemote, create_schema

from daily_rsync import remote_agent
from daily_rsync.sync import SyncService, sha256


@pytest.fixture(params=[False, True], ids=["catalog-only", "combined"])
def catalog_transition(app_config, tmp_path, request):
    remote = ProjectionRemote(tmp_path / "remote")
    create_schema(remote)
    create_catalog_schema(remote)
    combined = request.param
    with sqlite3.connect(remote.path) as connection:
        for rowid, condition in ((-7, "z-condition"), (103, "a-condition")):
            row = catalog_row(remote.strategy, condition)
            connection.execute(
                "INSERT INTO market_catalog(rowid,"
                + ",".join(row)
                + ") VALUES("
                + ",".join("?" for _ in range(len(row) + 1))
                + ")",
                (rowid, *row.values()),
            )
        if combined:
            columns = snapshot_profile(remote.strategy).column_names
            row = dict.fromkeys(columns)
            row.update(
                id=17,
                condition_id="z-condition",
                probability=0.75,
                run_id="PRIVATE_RUN",
                timestamp="2026-09-01 00:00:00.000000",
            )
            connection.execute(
                "INSERT INTO market_snapshots VALUES(" + ",".join("?" for _ in columns) + ")",
                tuple(row.values()),
            )
    service = SyncService(app_config)
    service.remote = remote
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    result = service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    artifact = plan.artifacts[0]
    old_row = service.catalog.get_artifact(artifact.source_key)
    old_pin = service.pin_database(artifact.source_key)
    state = remote_agent.sqlite_source_state(remote.path)
    original = remote.root / "original.db"
    target = remote.root / "derivative.db"
    shutil.copyfile(remote.path, original)
    with PayloadStore(remote.public_db) as store:
        manifest = migrate_public_bodies(
            original,
            target,
            strategy=remote.strategy,
            source_sha256=sha256(original),
            references=PayloadReferences(reader=store, writer=store),
            include_catalog=True,
            include_projections=combined,
            receipt_context=ReceiptContext(app_config.ssh_host, remote.runtime, remote.job),
        )
    manifest.update(
        source_snapshot_sha256=old_row["local_sha256"],
        source_file_sha256=sha256(original),
        original_source_path=str(remote.path),
        original_source_fingerprint=state["fingerprint"],
        original_source_members=state["members"],
    )
    shutil.copyfile(target, remote.path)
    sidecar = Path(str(remote.path) + ".storage-migration.json")
    sidecar.write_text(json.dumps(manifest))
    return SimpleNamespace(
        service=service,
        remote=remote,
        artifact=artifact,
        original=original,
        target=target,
        manifest=manifest,
        sidecar=sidecar,
        old_row=old_row,
        old_pin=old_pin,
        combined=combined,
        destination=service.local_path(artifact),
    )


def test_catalog_migration_proves_real_rowids_and_distinct_backup_source_sha(
    catalog_transition, app_config
):
    m = catalog_transition
    assert sha256(m.original) != m.old_row["local_sha256"]
    old_bytes = m.old_pin.read_bytes()
    plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    proposed = plan.artifacts[0].storage_migration["manifest"]
    assert proposed["tables"]["market_catalog"]["key_basis"] == "declared-primary-key+rowid-v1"
    assert proposed["public_projection_records"]["layout_receipt_counts"] == {
        "market_catalog": 4,
        **({"market_snapshots": 1} if m.combined else {}),
    }
    result = m.service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert m.service.verify(job=m.remote.job, strategy=m.remote.strategy)["status"] == "SUCCESS"
    new_pin = m.service.pin_database(m.artifact.source_key)
    assert new_pin != m.old_pin and m.old_pin.read_bytes() == old_bytes
    manifest = json.loads((new_pin.parent / "manifest.json").read_text())
    lineage = manifest["storage_lineage"]
    assert (
        lineage["public_projection_records"]["layout_receipt_counts"]
        == proposed["public_projection_records"]["layout_receipt_counts"]
    )
    assert lineage["source_snapshot_link"]["physical_byte_equality_claimed"] is False
    with PayloadReader(app_config.public_store_path) as reader:
        with connect(new_pin, references=PayloadReferences(reader=reader)) as db:
            rows = list(iter_catalog_rows(db, m.remote.strategy, include_rowid=True))
            assert [(row[0], row[1]) for row in rows] == [(-7, "z-condition"), (103, "a-condition")]
            assert db.execute("SELECT * FROM trades").fetchall() == [
                (7, "PRIVATE_ACCOUNT_SENTINEL", 12.5)
            ]
    with sqlite3.connect(new_pin) as db:
        assert db.execute("SELECT COUNT(*) FROM market_catalog").fetchone() == (0,)


@pytest.mark.parametrize(
    "damage",
    [
        "private_ledger",
        "private_clock",
        "private_reference",
        "rowid_with_valid_receipts",
        "count",
        "snapshot_link",
    ],
)
def test_catalog_migration_rejects_forged_evidence_preserving_old_pin(catalog_transition, damage):
    m = catalog_transition
    original_bytes = m.destination.read_bytes()
    pin_bytes = m.old_pin.read_bytes()
    if damage == "count":
        m.manifest["public_projection_records"]["layout_receipt_counts"]["market_catalog"] += 1
        m.manifest["public_projection_records"]["receipt_count"] += 1
    elif damage == "snapshot_link":
        m.manifest["source_snapshot_sha256"] = "f" * 64
    else:
        with sqlite3.connect(m.remote.path) as connection:
            if damage == "private_ledger":
                connection.execute("UPDATE trades SET private_pnl=99")
            elif damage == "private_clock":
                connection.execute(f"UPDATE {CONTEXT_TABLE} SET first_seen_at='2099-01-01'")
            elif damage == "private_reference":
                with PayloadStore(m.remote.public_db) as store:
                    marker = PayloadReferences(reader=store, writer=store).encode_many(
                        ["2026-09-01 00:00:00.000000"]
                    )[0]
                connection.execute(f"UPDATE {CONTEXT_TABLE} SET first_seen_at=?", (marker,))
            else:
                groups = connection.execute(
                    f"SELECT projection_0_id,projection_1_id FROM {CONTEXT_TABLE} WHERE _rowid=-7"
                ).fetchone()
                connection.execute(f"UPDATE {CONTEXT_TABLE} SET _rowid=104 WHERE _rowid=-7")
                with PayloadStore(m.remote.public_db) as store:
                    records = store.get_projection_records(
                        list(groups), store.scalar_authority_identity()
                    )
                    receipts = [
                        ProjectionReceipt(
                            m.manifest["projection_namespace"],
                            "market_catalog",
                            104,
                            record.reference.kind,
                            record.reference.record_id,
                        )
                        for record in records
                    ]
                    store.append_projection_receipts(
                        receipts, authority_uuid=store.scalar_authority_identity()
                    )
        m.manifest["destination_sha256"] = sha256(m.remote.path)
        m.manifest["destination_bytes"] = m.remote.path.stat().st_size
    m.sidecar.write_text(json.dumps(m.manifest))
    plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    result = m.service.execute(plan)
    assert result.status == "FAILED", result.errors
    assert m.destination.read_bytes() == original_bytes and m.old_pin.read_bytes() == pin_bytes
    assert (
        m.service.catalog.get_artifact(m.artifact.source_key)["local_sha256"]
        == m.old_row["local_sha256"]
    )
