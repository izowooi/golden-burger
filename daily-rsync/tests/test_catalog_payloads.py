from __future__ import annotations

import json
import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest
from polybot_observability.market_data_catalog_links import (
    CONTEXT_TABLE,
    LAYOUT_TABLE,
    initialize_catalog_links,
    iter_catalog_rows,
    upsert_shared_catalog,
)
from polybot_observability.market_data_catalog_profiles import catalog_profile
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_projection_payloads import ProjectionRemote, create_schema, execute, populate_native

from daily_rsync.bundle import create_bundle
from daily_rsync.public_payloads import closure_sidecar
from daily_rsync.sync import SyncService


def create_catalog_schema(remote):
    columns = []
    for column in catalog_profile(remote.strategy).columns:
        declaration = column.name + " " + column.affinity
        if column.name == "condition_id":
            declaration += " PRIMARY KEY"
        if not column.nullable:
            declaration += " NOT NULL"
        columns.append(declaration)
    with sqlite3.connect(remote.path) as connection:
        connection.execute("CREATE TABLE market_catalog(" + ",".join(columns) + ")")


def catalog_row(strategy, condition):
    row = dict.fromkeys(catalog_profile(strategy).column_names)
    row.update(
        condition_id=condition,
        market_id=condition,
        question="Public source question",
        outcomes_json='[ "Yes", "No" ]',
        token_ids_json='[ "yes-token", "no-token" ]',
        tags_json="[]",
        fees_enabled=1,
        fee_rate=0.05,
        first_seen_at="2026-09-01 00:00:00.000000",
        last_seen_at="2026-09-01 00:05:00.000000",
    )
    for key, value in (("outcome_prices_json", "[0.7,0.3]"), ("followup_attempt_count", 0)):
        if key in row:
            row[key] = value
    if "resolution_evidence_json" in row:
        row["resolution_evidence_json"] = "PRIVATE_RESOLUTION_PROOF"
    return row


def populate_catalog(remote, source, *, count=2):
    namespace = scalar_namespace(source, remote.job, remote.strategy, remote.runtime)
    with PayloadStore(remote.public_db) as store:
        refs = PayloadReferences(reader=store, writer=store)
        with connect(remote.path, references=refs) as connection:
            connection.execute("BEGIN")
            initialize_catalog_links(
                connection, remote.strategy, namespace, store.scalar_authority_identity()
            )
            for index in range(count):
                upsert_shared_catalog(
                    connection,
                    remote.strategy,
                    catalog_row(remote.strategy, ("z-condition", "a-condition")[index]),
                    references=refs,
                    namespace=namespace,
                    original_rowid=(-7, 103)[index],
                    insert_only=True,
                )
            connection.commit()


def prepare_catalog(app_config, tmp_path, *, count=2, combined=False):
    remote = ProjectionRemote(tmp_path / "remote-catalog")
    create_schema(remote)
    create_catalog_schema(remote)
    if combined:
        populate_native(remote, app_config.ssh_host, count=3)
    populate_catalog(remote, app_config.ssh_host, count=count)
    service = SyncService(app_config)
    service.remote = remote
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    artifact = plan.artifacts[0]
    return SimpleNamespace(
        service=service,
        remote=remote,
        plan=plan,
        artifact=artifact,
        destination=service.local_path(artifact),
        count=count,
    )


@pytest.mark.parametrize("combined", [False, True])
@pytest.mark.parametrize("count", [0, 2])
def test_catalog_only_and_combined_native_scan_sync_verify_pin_bundle(
    app_config, tmp_path, count, combined
):
    m = prepare_catalog(app_config, tmp_path, count=count, combined=combined)
    execute(m)
    descriptor = json.loads(closure_sidecar(m.destination).read_text())
    closure = descriptor["public_projection_records"]
    assert (
        descriptor["payload_count"] == 0
        and descriptor["shared_store"] == "shared-market-data/public.db"
    )
    assert closure["contract"] == "public-projection-closure-v2"
    assert closure["layout_receipt_counts"] == {
        "market_catalog": count * 2,
        **({"market_snapshots": 3} if combined else {}),
    }
    assert closure["receipt_count"] == count * 2 + (3 if combined else 0)
    assert closure["route_identity_verified"] is True
    assert m.remote.projection_requests[0] == ([], [], None)
    assert m.service.verify(job=m.remote.job, strategy=m.remote.strategy)["status"] == "SUCCESS"
    pin = m.service.pin_database(m.artifact.source_key)
    with PayloadReader(app_config.public_store_path) as reader:
        with connect(pin, references=PayloadReferences(reader=reader)) as connection:
            rows = list(iter_catalog_rows(connection, m.remote.strategy, include_rowid=True))
            assert [row[0] for row in rows] == list((-7, 103)[:count])
            assert [row[1] for row in rows] == list(("z-condition", "a-condition")[:count])
            assert connection.execute("SELECT * FROM trades").fetchall() == [
                (7, "PRIVATE_ACCOUNT_SENTINEL", 12.5)
            ]
            assert connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == (
                3 if combined else 0
            )
    bundle = create_bundle(
        app_config,
        job=m.remote.job,
        strategy=m.remote.strategy,
        from_date=date(2026, 9, 1),
        to_date=date(2026, 9, 1),
    )
    bundle_manifest = json.loads((bundle / "manifest.json").read_text())
    assert bundle_manifest["public_payload_store"] == str(app_config.public_store_path)
    assert (
        bundle_manifest["artifacts"][0]["public_payloads"]["public_projection_records"]["layouts"]
        == closure["layouts"]
    )


def test_catalog_receipt_refresh_uses_origin_table_and_does_not_resend_records(
    app_config, tmp_path
):
    m = prepare_catalog(app_config, tmp_path, combined=True)
    execute(m)
    with sqlite3.connect(app_config.public_store_path) as db:
        from polybot_observability.market_data_projections import PROJECTION_TRIGGER_SQL
        db.execute("DROP TRIGGER projection_receipts_no_delete")
        db.execute("DELETE FROM projection_receipts WHERE original_id=-7")
        db.execute(PROJECTION_TRIGGER_SQL['projection_receipts_no_delete'])
    assert m.service.verify(job=m.remote.job, strategy=m.remote.strategy)["status"] == "FAILED"
    with pytest.raises((RuntimeError, ValueError)):
        m.service.pin_database(m.artifact.source_key)
    start = len(m.remote.projection_requests)
    m.plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    execute(m)
    assert all(not transferred for _, transferred, _ in m.remote.projection_requests[start:])
    assert m.service.verify(job=m.remote.job, strategy=m.remote.strategy)["status"] == "SUCCESS"


@pytest.mark.parametrize(
    "damage", ["wrong_namespace", "disagreeing_owners", "private_reference", "missing_group"]
)
def test_catalog_native_bad_ownership_or_private_reference_never_publishes(
    app_config, tmp_path, damage
):
    m = prepare_catalog(app_config, tmp_path, combined=(damage == "disagreeing_owners"))
    with sqlite3.connect(m.remote.path) as db:
        if damage in {"wrong_namespace", "disagreeing_owners"}:
            namespace = scalar_namespace(
                app_config.ssh_host, m.remote.job, m.remote.strategy, "wrong-runtime"
            )
            db.execute(f"UPDATE {LAYOUT_TABLE} SET namespace=?", (namespace,))
        elif damage == "private_reference":
            with PayloadStore(m.remote.public_db) as store:
                marker = PayloadReferences(reader=store, writer=store).encode_many(
                    ["2026-09-01 00:00:00.000000"]
                )[0]
            db.execute(f"UPDATE {CONTEXT_TABLE} SET first_seen_at=?", (marker,))
        else:
            db.execute(f"UPDATE {CONTEXT_TABLE} SET projection_0_id=999999")
    m.plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    result = m.service.execute(m.plan)
    assert result.status == "FAILED", result.errors
    assert not m.destination.exists()
    if damage != "missing_group":
        assert not m.remote.projection_requests


def test_native_catalog_update_preserves_old_pin_and_refreshes_current_groups(app_config, tmp_path):
    from polybot_observability.market_data_catalog_links import update_shared_catalog

    m = prepare_catalog(app_config, tmp_path)
    execute(m)
    old_pin = m.service.pin_database(m.artifact.source_key)
    old_bytes = old_pin.read_bytes()
    with PayloadStore(m.remote.public_db) as store:
        refs = PayloadReferences(reader=store, writer=store)
        with connect(m.remote.path, references=refs) as connection:
            update_shared_catalog(
                connection,
                m.remote.strategy,
                "z-condition",
                {
                    "question": "Updated public source question",
                    "last_seen_at": "2026-09-01 00:10:00.000000",
                },
                references=refs,
            )
            connection.commit()
    m.plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    execute(m)
    assert m.service.verify(job=m.remote.job, strategy=m.remote.strategy)["status"] == "SUCCESS"
    new_pin = m.service.pin_database(m.artifact.source_key)
    assert old_pin != new_pin and old_pin.read_bytes() == old_bytes
    with PayloadReader(app_config.public_store_path) as reader:
        for path, expected in (
            (old_pin, "Public source question"),
            (new_pin, "Updated public source question"),
        ):
            with connect(path, references=PayloadReferences(reader=reader)) as connection:
                assert connection.execute(
                    "SELECT question FROM market_catalog WHERE condition_id='z-condition'"
                ).fetchone() == (expected,)
