"""Stream dependencies of private scalar memberships without loading all IDs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .market_data_scalar_links import LAYOUT_TABLE, LINK_TABLE, validate_scalar_layout

CONTRACT = "public-scalar-closure-v1"


@contextmanager
def _source(database, strategy):
    path = Path(database).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        auxiliary = validate_scalar_layout(connection)
        metadata = None
        if auxiliary:
            actual, namespace, authority = connection.execute(
                f"SELECT strategy,namespace,authority_uuid FROM {LAYOUT_TABLE}"
            ).fetchone()
            if actual != strategy:
                raise ValueError(
                    "scalar membership strategy differs from source identity"
                )
            metadata = {"namespace": namespace, "authority_uuid": authority}
        yield connection, metadata
    finally:
        connection.close()


def scalar_identity(database, strategy):
    with _source(database, strategy) as (_, metadata):
        return metadata


def iter_scalar_ids(database, strategy):
    with _source(database, strategy) as (connection, metadata):
        if metadata is None:
            return
        for (record_id,) in connection.execute(
            f"SELECT DISTINCT record_id FROM {LINK_TABLE} ORDER BY record_id"
        ):
            if type(record_id) is not int or not 0 < record_id < 2**63:
                raise ValueError("invalid scalar membership record ID")
            yield record_id


def iter_scalar_receipt_keys(database, strategy):
    with _source(database, strategy) as (connection, metadata):
        if metadata is None:
            return
        for row_id, record_id in connection.execute(
            f"SELECT id,record_id FROM {LINK_TABLE} ORDER BY id"
        ):
            if (
                type(row_id) is not int
                or type(record_id) is not int
                or not 0 < record_id < 2**63
            ):
                raise ValueError("invalid scalar membership identity")
            yield metadata["namespace"], row_id, record_id


def batches(values, size=1024):
    batch = []
    for value in values:
        batch.append(value)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def verify_scalar_closure(reader, database, strategy):
    identity = scalar_identity(database, strategy)
    if identity is not None and (
        reader is None
        or reader.scalar_authority_identity() != identity["authority_uuid"]
    ):
        raise ValueError("scalar membership authority is missing or changed")
    digest = hashlib.sha256()
    count = raw_bytes = receipts = 0
    for batch in batches(iter_scalar_ids(database, strategy)):
        if reader is None:
            raise ValueError("scalar membership has no public reader")
        values = reader.get_scalar_records(
            batch, authority_uuid=identity["authority_uuid"]
        )
        if [value.reference.record_id for value in values] != batch:
            raise ValueError("scalar membership record identity mismatch")
        for record_id, value in zip(batch, values, strict=True):
            digest.update(
                json.dumps(
                    [record_id, value.reference.sha256], separators=(",", ":")
                ).encode()
                + b"\n"
            )
            count += 1
            raw_bytes += len(
                json.dumps(
                    value.to_wire(),
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            )
    for batch in batches(iter_scalar_receipt_keys(database, strategy)):
        if reader is None:
            raise ValueError("scalar membership has no public reader")
        values = reader.get_scalar_receipts(
            batch, authority_uuid=identity["authority_uuid"]
        )
        if len(values) != len(batch) or any(
            value is None or value.row() != key
            for value, key in zip(values, batch, strict=True)
        ):
            raise ValueError("scalar membership source receipt is missing")
        receipts += len(batch)
    return {
        "contract": CONTRACT,
        "record_count": count,
        "receipt_count": receipts,
        "namespace": identity["namespace"] if identity else None,
        "authority_uuid": identity["authority_uuid"] if identity else None,
        "closure_sha256": digest.hexdigest(),
        "raw_bytes": raw_bytes,
    }
