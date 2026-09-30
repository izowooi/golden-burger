"""Bounded public projection replication; private runtime context never travels.

Logical IDs/values are preserved under the source authority. Physical blocks may
be repacked by the replica. Each page uses one read snapshot, and omitted record
dependencies must already exist in the receiver before a receipt can be imported.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .market_data_bundle import _validate_bundle_schema
from .market_data_migrate import file_sha256
from .market_data_projections import (
    ProjectionReceipt, projection_schema_sha, validate_projection_ids,
)
from .market_data_scalars import validate_authority
from .market_data_store import PayloadReader, PayloadStore

CONTRACT = 'public-projection-record-bundle-v1'
PAGE_SIZE = 5000
MAX_INDEX_BYTES = 64 << 20


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode()


def record_digest(references):
    digest = hashlib.sha256()
    for ref in references:
        digest.update(_canonical([ref.record_id, ref.kind, ref.sha256]) + b'\n')
    return digest.hexdigest()


def validate_cursor(value, ids):
    if value is None:
        return
    if not isinstance(value, list) or len(value) != 5:
        raise ValueError('invalid projection receipt cursor')
    receipt = ProjectionReceipt(*value)
    if receipt.record_id not in ids:
        raise ValueError('projection receipt cursor is outside requested records')


def _receipts(connection, ids, *, after=None, limit=None):
    validate_projection_ids(ids)
    validate_cursor(after, ids)
    if not ids:
        return
    query = ('SELECT n.name,s.origin_table,r.original_id,k.kind,r.record_id '
             'FROM projection_receipts r JOIN projection_receipt_streams s ON s.id=r.stream_id '
             'JOIN scalar_sources n ON n.id=s.source_id JOIN projection_kinds k ON k.id=s.kind_key '
             'WHERE r.record_id IN (' + ','.join('?' for _ in ids) + ')')
    parameters = list(ids)
    if after is not None:
        query += ' AND (n.name,s.origin_table,r.original_id,k.kind,r.record_id)>(?,?,?,?,?)'
        parameters += after
    query += ' ORDER BY n.name,s.origin_table,r.original_id,k.kind,r.record_id'
    if limit is not None:
        query += ' LIMIT ?'
        parameters.append(limit)
    cursor = connection.execute(query, parameters)
    try:
        for row in cursor:
            yield ProjectionReceipt(*row)
    finally:
        cursor.close()


def export_projection_bundle(reader, record_ids, output, *, authority_uuid,
                             transferred_ids=None, receipt_after=None):
    validate_projection_ids(record_ids)
    validate_authority(authority_uuid)
    if transferred_ids is not None:
        validate_projection_ids(transferred_ids)
    ids = sorted(set(record_ids))
    bodies = ids if transferred_ids is None else sorted(set(transferred_ids))
    validate_cursor(receipt_after, ids)
    if not set(bodies) <= set(ids) or reader.scalar_authority_identity() != authority_uuid:
        raise ValueError('projection bundle authority or selection differs')
    path = Path(output).absolute()
    if path.exists() or path.is_symlink():
        raise ValueError('projection bundle output already exists')
    source = reader._connection
    _validate_bundle_schema(source)
    own_snapshot = not source.in_transaction
    if own_snapshot:
        source.execute('BEGIN')
    try:
        values = reader.get_projection_records(ids, authority_uuid)
        if [row.reference.record_id for row in values] != ids:
            raise ValueError('projection export record identity differs')
        records = {row.reference.record_id: row for row in values}
        page = list(_receipts(source, ids, after=receipt_after, limit=PAGE_SIZE + 1))
        more = len(page) > PAGE_SIZE
        page = page[:PAGE_SIZE]
        index = b''.join(_canonical(row.to_wire()) + b'\n' for row in page)
        if len(index) > MAX_INDEX_BYTES:
            raise ValueError('projection receipt index exceeds bound')
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with PayloadStore(path) as target:
            from .market_data_projection_store import insert_transport_projection_receipt
            target.import_projection_records(authority_uuid, [records[key] for key in bodies])
            connection = target._connection
            connection.execute('PRAGMA foreign_keys=OFF')
            connection.execute('BEGIN IMMEDIATE')
            try:
                for receipt in page:
                    insert_transport_projection_receipt(connection, receipt, authority_uuid=authority_uuid,
                                                        allow_missing_records=True)
                connection.execute('COMMIT')
            except BaseException:
                connection.execute('ROLLBACK')
                raise
        manifest = {
            'contract': CONTRACT, 'status': 'VERIFIED', 'authority_uuid': authority_uuid,
            'record_ids': ids, 'transferred_ids': bodies,
            'record_hashes': [[row.reference.record_id, row.reference.kind, row.reference.sha256] for row in values],
            'record_count': len(bodies), 'closure_sha256': record_digest([row.reference for row in values]),
            'raw_bytes': sum(len(_canonical(records[key].to_wire())) for key in bodies),
            'receipt_count': len(page), 'receipt_sha256': hashlib.sha256(index).hexdigest(),
            'index_bytes': len(index), 'receipt_after': receipt_after,
            'next_receipt_after': list(page[-1].row()) if more else None,
            'receipt_snapshot': 'per-page-read-snapshot-v1', 'file_sha256': file_sha256(path),
        }
        path.with_suffix(path.suffix + '.manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        return manifest
    finally:
        if own_snapshot:
            source.execute('ROLLBACK')


def _validate_transfer_rows(connection, bodies, ids):
    for table in ('payloads', 'observations', 'public_subject_sets', 'public_subject_set_members',
                  'observation_subject_sets', 'scalar_snapshots', 'scalar_hot', 'scalar_blocks', 'scalar_receipts'):
        if connection.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
            raise ValueError('projection bundle contains undeclared data: ' + table)
    if [row[0] for row in connection.execute('SELECT id FROM projection_records ORDER BY id')] != bodies:
        raise ValueError('projection bundle contains unrequested records')
    if connection.execute('SELECT 1 FROM projection_hot h LEFT JOIN projection_records r ON r.id=h.record_id '
                          'WHERE r.id IS NULL OR r.block_id IS NOT NULL LIMIT 1').fetchone():
        raise ValueError('projection bundle contains an unreferenced hot row')
    if connection.execute('SELECT 1 FROM projection_blocks b WHERE NOT EXISTS '
                          '(SELECT 1 FROM projection_records r WHERE r.block_id=b.id) LIMIT 1').fetchone():
        raise ValueError('projection bundle contains an unreferenced block')
    if connection.execute('SELECT 1 FROM projection_unkeyed u LEFT JOIN projection_records r ON r.id=u.record_id '
                          'WHERE r.id IS NULL OR r.condition_key IS NOT NULL OR r.token_key IS NOT NULL '
                          'OR r.source_time IS NOT NULL LIMIT 1').fetchone():
        raise ValueError('projection bundle contains an unrequested unkeyed lookup row')
    for table, foreign, column in (
        ('scalar_conditions', 'projection_records', 'condition_key'),
        ('scalar_sources', 'projection_receipt_streams', 'source_id'),
        ('projection_tokens', 'projection_records', 'token_key'),
        ('projection_receipt_streams', 'projection_receipts', 'stream_id'),
    ):
        if connection.execute(f'SELECT 1 FROM {table} p WHERE NOT EXISTS '
                              f'(SELECT 1 FROM {foreign} r WHERE r.{column}=p.id) LIMIT 1').fetchone():
            raise ValueError('projection bundle contains unused dictionary data: ' + table)
    for kind_id, kind, schema_sha in connection.execute('SELECT id,kind,schema_sha FROM projection_kinds'):
        if schema_sha != projection_schema_sha(kind) or not connection.execute(
            'SELECT 1 FROM projection_records WHERE kind_key=? UNION ALL SELECT 1 FROM projection_receipt_streams WHERE kind_key=? LIMIT 1',
            (kind_id, kind_id),
        ).fetchone():
            raise ValueError('projection bundle kind schema or membership differs')
    # The exporter may omit only record bodies; no other dangling FK is allowed.
    for table, rowid, parent, fk in connection.execute('PRAGMA foreign_key_check'):
        if table != 'projection_receipts' or parent != 'projection_records':
            raise ValueError('projection bundle contains broken dictionary references')


def import_projection_bundle(path, manifest, writer):
    path = Path(path)
    if (manifest.get('contract') != CONTRACT or manifest.get('status') != 'VERIFIED'
            or file_sha256(path) != manifest.get('file_sha256')):
        raise ValueError('projection bundle file/contract mismatch')
    ids, bodies = manifest.get('record_ids'), manifest.get('transferred_ids')
    validate_projection_ids(ids)
    validate_projection_ids(bodies)
    authority = validate_authority(manifest.get('authority_uuid'))
    if (ids != sorted(set(ids)) or bodies != sorted(set(bodies)) or not set(bodies) <= set(ids)
            or manifest.get('receipt_snapshot') != 'per-page-read-snapshot-v1'):
        raise ValueError('projection bundle requested records differ')
    for field in ('record_count', 'receipt_count', 'raw_bytes', 'index_bytes'):
        if type(manifest.get(field)) is not int or manifest[field] < 0:
            raise ValueError('projection bundle has invalid counts')
    if manifest['record_count'] != len(bodies):
        raise ValueError('projection bundle record count differs')
    after, next_after = manifest.get('receipt_after'), manifest.get('next_receipt_after')
    validate_cursor(after, ids)
    validate_cursor(next_after, ids)
    with PayloadReader(path) as reader:
        connection = reader._connection
        _validate_bundle_schema(connection)
        if reader.scalar_authority_identity() != authority or reader.scalar_authority_role() != 'REPLICA':
            raise ValueError('projection bundle must be an isolated source-authority replica')
        _validate_transfer_rows(connection, bodies, ids)
        values = reader.get_projection_records(bodies, authority)
        present = sorted(set(ids) - set(bodies))
        existing = writer.get_projection_records(present, authority) if present else []
        records = {row.reference.record_id: row for row in [*values, *existing]}
        refs = [records[key].reference for key in ids]
        if ([[ref.record_id, ref.kind, ref.sha256] for ref in refs] != manifest.get('record_hashes')
                or record_digest(refs) != manifest.get('closure_sha256')
                or sum(len(_canonical(row.to_wire())) for row in values) != manifest['raw_bytes']):
            raise ValueError('projection bundle logical values differ')
        rows = list(_receipts(connection, ids, after=after, limit=PAGE_SIZE + 1))
        if (len(rows) > PAGE_SIZE or len(rows) != connection.execute('SELECT COUNT(*) FROM projection_receipts').fetchone()[0]
                or any(row.kind != records[row.record_id].reference.kind for row in rows)):
            raise ValueError('projection bundle receipt membership differs')
        index = b''.join(_canonical(row.to_wire()) + b'\n' for row in rows)
        if (len(index) > MAX_INDEX_BYTES or len(index) != manifest['index_bytes']
                or len(rows) != manifest['receipt_count']
                or hashlib.sha256(index).hexdigest() != manifest.get('receipt_sha256')
                or (next_after is not None and (not rows or next_after != list(rows[-1].row())))):
            raise ValueError('projection bundle receipt attestation differs')
        writer.import_projection_records(authority, values)
        for offset in range(0, len(rows), 1024):
            writer.append_projection_receipts(rows[offset:offset + 1024], authority_uuid=authority)
    return {'imported_record_count': len(bodies), 'imported_receipt_count': len(rows),
            'closure_sha256': manifest['closure_sha256'], 'receipt_sha256': manifest['receipt_sha256'],
            'index_bytes': len(index)}
