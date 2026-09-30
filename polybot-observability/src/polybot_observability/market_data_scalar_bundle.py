"""Bounded numeric-record replication with stable source IDs and authority UUID.

Compression blocks are physical storage. Replicas preserve logical cells and
IDs, while packing their own blocks. Receipts refresh independently of bodies.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .market_data_bundle import _validate_bundle_schema, _assert_no_projection_rows
from .market_data_migrate import file_sha256
from .market_data_scalars import ScalarReceipt
from .market_data_store import PayloadReader, PayloadStore

CONTRACT = "public-scalar-record-bundle-v1"
PAGE_SIZE = 5000
MAX_INDEX_BYTES = 64 << 20


def validate_ids(values):
    if (
        not isinstance(values, list)
        or len(values) > 1024
        or any(type(value) is not int or not 0 < value < 2**63 for value in values)
    ):
        raise ValueError("scalar record IDs must be a bounded positive-integer list")


def validate_cursor(value, record_ids):
    if value is None:
        return
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("invalid scalar receipt cursor")
    row = ScalarReceipt(*value)
    if row.record_id not in record_ids:
        raise ValueError("scalar receipt cursor is outside requested records")


def _canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def record_digest(references):
    digest = hashlib.sha256()
    for ref in references:
        digest.update(_canonical([ref.record_id, ref.sha256]) + b"\n")
    return digest.hexdigest()


def _receipts(connection, record_ids, *, after=None, limit=None):
    validate_ids(record_ids)
    validate_cursor(after, record_ids)
    if not record_ids:
        return
    query = (
        "SELECT n.name,r.original_id,r.record_id FROM scalar_receipts r "
        "JOIN scalar_sources n ON n.id=r.source_id WHERE r.record_id IN ("
        + ",".join("?" for _ in record_ids)
        + ")"
    )
    parameters = list(record_ids)
    if after is not None:
        query += " AND (n.name,r.original_id,r.record_id)>(?,?,?)"
        parameters += after
    query += " ORDER BY n.name,r.original_id,r.record_id"
    if limit is not None:
        query += " LIMIT ?"
        parameters.append(limit)
    cursor = connection.execute(query, parameters)
    try:
        for namespace, original_id, record_id in cursor:
            yield ScalarReceipt(namespace, original_id, record_id)
    finally:
        cursor.close()


def _insert_receipt(connection, receipt, authority):
    from .market_data_scalar_store import insert_transport_scalar_receipt

    insert_transport_scalar_receipt(
        connection, receipt, authority_uuid=authority, allow_missing_records=True
    )


def export_scalar_bundle(
    reader,
    record_ids,
    output,
    *,
    authority_uuid,
    transferred_ids=None,
    receipt_after=None,
):
    validate_ids(record_ids)
    record_ids = sorted(set(record_ids))
    bodies = record_ids if transferred_ids is None else sorted(set(transferred_ids))
    validate_ids(bodies)
    validate_cursor(receipt_after, record_ids)
    if (
        not set(bodies) <= set(record_ids)
        or reader.scalar_authority_identity() != authority_uuid
    ):
        raise ValueError("scalar bundle request authority or selection differs")
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("scalar bundle output already exists")
    source = reader._connection
    _validate_bundle_schema(source)
    own_snapshot = not source.in_transaction
    if own_snapshot:
        source.execute("BEGIN")
    try:
        values = reader.get_scalar_records(record_ids, authority_uuid=authority_uuid)
        if [row.reference.record_id for row in values] != record_ids:
            raise ValueError("scalar export record identity mismatch")
        records = {row.reference.record_id: row for row in values}
        page = list(
            _receipts(source, record_ids, after=receipt_after, limit=PAGE_SIZE + 1)
        )
        more = len(page) > PAGE_SIZE
        page = page[:PAGE_SIZE]
        index = b"".join(_canonical(row.to_wire()) + b"\n" for row in page)
        if len(index) > MAX_INDEX_BYTES:
            raise ValueError("scalar receipt index exceeds bound")
        output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with PayloadStore(output) as target:
            target.import_scalar_records(
                authority_uuid, [records[key] for key in bodies]
            )
            connection = target._connection
            # Omitted record dependencies are checked in the receiving store.
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("BEGIN IMMEDIATE")
            try:
                for receipt in page:
                    _insert_receipt(connection, receipt, authority_uuid)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        manifest = {
            "contract": CONTRACT,
            "status": "VERIFIED",
            "authority_uuid": authority_uuid,
            "record_ids": record_ids,
            "transferred_ids": bodies,
            "record_hashes": [
                [row.reference.record_id, row.reference.sha256] for row in values
            ],
            "record_count": len(bodies),
            "closure_sha256": record_digest([row.reference for row in values]),
            "raw_bytes": sum(len(_canonical(records[key].to_wire())) for key in bodies),
            "receipt_count": len(page),
            "receipt_sha256": hashlib.sha256(index).hexdigest(),
            "index_bytes": len(index),
            "receipt_after": receipt_after,
            "next_receipt_after": list(page[-1].row()) if more else None,
            "receipt_snapshot": "per-page-read-snapshot-v1",
            "file_sha256": file_sha256(output),
        }
        output.with_suffix(output.suffix + ".manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n"
        )
        return manifest
    finally:
        if own_snapshot:
            source.execute("ROLLBACK")


def import_scalar_bundle(path, manifest, writer):
    path = Path(path)
    if (
        manifest.get("contract") != CONTRACT
        or manifest.get("status") != "VERIFIED"
        or file_sha256(path) != manifest.get("file_sha256")
    ):
        raise ValueError("scalar bundle file/contract mismatch")
    ids, bodies = manifest.get("record_ids"), manifest.get("transferred_ids")
    validate_ids(ids)
    validate_ids(bodies)
    if (
        ids != sorted(set(ids))
        or bodies != sorted(set(bodies))
        or not set(bodies) <= set(ids)
        or type(manifest.get("record_count")) is not int
        or manifest.get("record_count") != len(bodies)
        or manifest.get("receipt_snapshot") != "per-page-read-snapshot-v1"
    ):
        raise ValueError("scalar bundle requested records differ")
    authority = manifest.get("authority_uuid")
    after, next_after = (
        manifest.get("receipt_after"),
        manifest.get("next_receipt_after"),
    )
    validate_cursor(after, ids)
    validate_cursor(next_after, ids)
    with PayloadReader(path) as reader:
        connection = reader._connection
        _validate_bundle_schema(connection)
        _assert_no_projection_rows(connection)
        if any(reader.stats().values()):
            raise ValueError("scalar bundle contains body data")
        for table in (
            "public_subject_sets",
            "public_subject_set_members",
            "observation_subject_sets",
        ):
            if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise ValueError("scalar bundle contains undeclared body subjects")
        if reader.scalar_authority_identity() != authority:
            raise ValueError("scalar bundle authority differs")
        if reader.scalar_authority_role() != "REPLICA":
            raise ValueError("scalar bundle must be an isolated replica transfer")
        if connection.execute(
            "SELECT 1 FROM scalar_hot h LEFT JOIN scalar_snapshots s ON s.id=h.record_id "
            "WHERE s.id IS NULL OR s.block_id IS NOT NULL LIMIT 1"
        ).fetchone():
            raise ValueError("scalar bundle contains an unreferenced hot row")
        if connection.execute(
            "SELECT 1 FROM scalar_blocks b WHERE NOT EXISTS "
            "(SELECT 1 FROM scalar_snapshots s WHERE s.block_id=b.id) LIMIT 1"
        ).fetchone():
            raise ValueError("scalar bundle contains an unreferenced block")
        if [
            row[0]
            for row in connection.execute("SELECT id FROM scalar_snapshots ORDER BY id")
        ] != bodies:
            raise ValueError("scalar bundle contains unrequested records")
        values = reader.get_scalar_records(bodies, authority_uuid=authority)
        present = sorted(set(ids) - set(bodies))
        existing = (
            writer.get_scalar_records(present, authority_uuid=authority)
            if present
            else []
        )
        records = {row.reference.record_id: row for row in [*values, *existing]}
        all_refs = [records[key].reference for key in ids]
        if (
            [[ref.record_id, ref.sha256] for ref in all_refs]
            != manifest.get("record_hashes")
            or record_digest(all_refs) != manifest.get("closure_sha256")
            or sum(len(_canonical(row.to_wire())) for row in values)
            != manifest.get("raw_bytes")
        ):
            raise ValueError("scalar bundle logical values differ")
        rows = list(_receipts(connection, ids, after=after, limit=PAGE_SIZE + 1))
        if (
            len(rows) > PAGE_SIZE
            or len(rows)
            != connection.execute("SELECT COUNT(*) FROM scalar_receipts").fetchone()[0]
        ):
            raise ValueError("scalar bundle has out-of-page receipts")
        for table, foreign, column in [
            ("scalar_conditions", "scalar_snapshots", "condition_key"),
            ("scalar_sources", "scalar_receipts", "source_id"),
        ]:
            if connection.execute(
                f"SELECT 1 FROM {table} p WHERE NOT EXISTS "
                f"(SELECT 1 FROM {foreign} c WHERE c.{column}=p.id) LIMIT 1"
            ).fetchone():
                raise ValueError("scalar bundle contains unused dictionary data")
        index = b"".join(_canonical(row.to_wire()) + b"\n" for row in rows)
        if (
            len(index) > MAX_INDEX_BYTES
            or len(index) != manifest.get("index_bytes")
            or len(rows) != manifest.get("receipt_count")
            or hashlib.sha256(index).hexdigest() != manifest.get("receipt_sha256")
            or (
                next_after is not None
                and (not rows or next_after != list(rows[-1].row()))
            )
        ):
            raise ValueError("scalar bundle receipt attestation differs")
        writer.import_scalar_records(authority, values)
        for offset in range(0, len(rows), 1024):
            writer.append_scalar_receipts(
                rows[offset : offset + 1024], authority_uuid=authority
            )
    return {
        "imported_record_count": len(bodies),
        "imported_receipt_count": len(rows),
        "closure_sha256": manifest["closure_sha256"],
        "receipt_sha256": manifest["receipt_sha256"],
        "index_bytes": len(index),
    }
