"""Stream public groups and source receipts required by a private snapshot DB."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import heapq
import json
from pathlib import Path
import sqlite3

from .market_data_projection_links import (
    TABLE, iter_projection_link_rows, iter_projection_record_ids,
    projection_layout_metadata, validate_projection_layout,
)
from .market_data_catalog_links import (
    catalog_layout_metadata, validate_catalog_layout,
    iter_catalog_record_ids, iter_catalog_link_rows,
)
from .market_data_raw_links import (
    raw_layout_metadata, validate_raw_layout, iter_raw_record_ids,
    iter_raw_receipt_keys, verify_raw_dependencies,
)
from .market_data_raw_profiles import profile_for_connection
from .market_data_scalar_closure import batches
from .market_data_store import StoreLimitError

CONTRACT = 'public-projection-closure-v1'
CATALOG_CONTRACT = 'public-projection-closure-v2'
RAW_CONTRACT = 'public-projection-closure-v3'
RAW_PROFILE_CONTRACT = 'public-projection-closure-v4'
RAW_PROFILE_FIELDS = ('raw_profile_id', 'raw_profile_version', 'raw_logical_schema_sha256')


def _owners(connection, strategy):
    validate_projection_layout(connection)
    validate_catalog_layout(connection)
    owners = {table: value for table, value in (
        (TABLE, projection_layout_metadata(connection)),
        ('market_catalog', catalog_layout_metadata(connection)),
    ) if value is not None}
    validate_raw_layout(connection)
    raw = raw_layout_metadata(connection)
    if raw is not None:
        schema = profile_for_connection(connection)
        for table, profile in schema.tables.items():
            owners[table] = {**raw, 'groups': (profile.kind,)}
    if any(value['strategy'] != strategy for value in owners.values()):
        raise ValueError('projection context strategy differs from the source identity')
    if len({(value['namespace'], value['authority_uuid']) for value in owners.values()}) > 1:
        raise ValueError('public projection layouts disagree on their private owner')
    return owners


@contextmanager
def _source(database, strategy, *, immutable=False, guard=None):
    from .market_data_bundle import _guarded_sqlite
    path = Path(database).resolve(strict=True)
    suffix = '&immutable=1' if immutable else ''
    connection = sqlite3.connect(path.as_uri() + '?mode=ro' + suffix, uri=True)
    try:
        with _guarded_sqlite(connection, guard):
            owners = _owners(connection, strategy)
            metadata = next(iter(owners.values()), None)
            identity = None if metadata is None else {
                'namespace': metadata['namespace'], 'authority_uuid': metadata['authority_uuid'],
                'kinds': sorted({kind for value in owners.values() for kind in value['groups']}),
            }
            raw = raw_layout_metadata(connection)
            if 'market_catalog' in owners or raw is not None:
                identity['layouts'] = {table: sorted(value['groups']) for table, value in sorted(owners.items())}
            if raw is not None:
                identity['raw_layout_contract'] = raw['contract']
                identity['raw_source_schema_sha'] = raw['source_schema_sha']
                if raw['contract'] == 'shared-raw-parent-skeleton-v2':
                    identity.update({
                        'raw_profile_id': raw['profile_id'],
                        'raw_profile_version': raw['profile_version'],
                        'raw_logical_schema_sha256': raw['logical_schema_sha256'],
                    })
            yield connection, identity
    finally:
        connection.close()


def projection_identity(database, strategy, *, immutable=False, guard=None):
    with _source(database, strategy, immutable=immutable, guard=guard) as (_, identity):
        return identity


def iter_projection_ids(database, strategy, *, immutable=False, guard=None):
    with _source(database, strategy, immutable=immutable, guard=guard) as (connection, identity):
        if identity is not None:
            iterators = []
            owners = _owners(connection, strategy)
            if TABLE in owners:
                iterators.append(iter_projection_record_ids(connection))
            if 'market_catalog' in owners:
                iterators.append(iter_catalog_record_ids(connection))
            if raw_layout_metadata(connection) is not None:
                iterators.append(iter_raw_record_ids(connection))
            previous = None
            for record_id in heapq.merge(*iterators):
                if guard: guard()
                if record_id != previous:
                    yield record_id
                    previous = record_id


def iter_projection_receipt_keys(database, strategy, *, immutable=False, guard=None):
    with _source(database, strategy, immutable=immutable, guard=guard) as (connection, identity):
        if identity is not None:
            owners = _owners(connection, strategy)
            if TABLE in owners:
                for original_id, kind, record_id in iter_projection_link_rows(connection):
                    if guard: guard()
                    yield identity['namespace'], TABLE, original_id, kind, record_id
            if 'market_catalog' in owners:
                for original_id, condition, kind, record_id in iter_catalog_link_rows(connection):
                    if guard: guard()
                    yield identity['namespace'], 'market_catalog', original_id, kind, record_id
            if raw_layout_metadata(connection) is not None:
                for key in iter_raw_receipt_keys(connection):
                    if guard: guard()
                    yield key


def read_projection_records(reader, ids, authority_uuid, *, guard=None):
    """Respect the record-byte cap even when a catalog group is unusually large."""
    try:
        if guard: guard()
        values = reader.get_projection_records(ids, authority_uuid)
        if guard: guard()
    except StoreLimitError:
        if len(ids) < 2:
            raise
        middle = len(ids) // 2
        yield from read_projection_records(reader, ids[:middle], authority_uuid, guard=guard)
        yield from read_projection_records(reader, ids[middle:], authority_uuid, guard=guard)
        return
    if [row.reference.record_id for row in values] != ids:
        raise ValueError('projection dependency record IDs differ')
    yield from values


def verify_projection_closure(reader, database, strategy, *, immutable=False, guard=None):
    identity = projection_identity(database, strategy, immutable=immutable, guard=guard)
    if identity is not None and (reader is None or reader.scalar_authority_identity() != identity['authority_uuid']):
        raise ValueError('projection context authority is missing or changed')
    if identity is not None:
        # Dependency IDs alone do not prove the retained local condition/token
        # belongs to that public group. Exercise the view's independent identity
        # proof without interpreting private data as public payloads.
        from .market_data_refs import PayloadReferences
        from .market_data_sqlite import connect
        path = Path(database).resolve(strict=True)
        suffix = '&immutable=1' if immutable else ''
        connection = connect(path.as_uri() + '?mode=ro' + suffix, references=PayloadReferences(reader=reader))
        try:
            from .market_data_bundle import _guarded_sqlite
            with _guarded_sqlite(connection, guard):
                owners = _owners(connection, strategy)
                if TABLE in owners:
                    previous = None
                    for (row_id,) in connection.execute('SELECT id FROM market_snapshots ORDER BY id'):
                        if type(row_id) is not int or (previous is not None and row_id <= previous):
                            raise ValueError('projection context has duplicated or invalid original IDs')
                        previous = row_id
                if 'market_catalog' in owners:
                    # Select only identity. The view's WHERE still proves every
                    # projected group; old inline catalog body markers are handled
                    # by the separate body closure and need not be downloaded yet.
                    previous = None
                    for row_id, condition in connection.execute(
                        'SELECT catalog_rowid(condition_id), condition_id FROM market_catalog '
                        'ORDER BY catalog_rowid(condition_id)'
                    ):
                        if type(row_id) is not int or (previous is not None and row_id <= previous):
                            raise ValueError('catalog context has duplicated or invalid original rowids')
                        previous = row_id
                if raw_layout_metadata(connection) is not None:
                    # Public groups alone do not prove which original RAW row
                    # observed them. Verify its source receipt and the retained
                    # condition/event/token identity without decoding unrelated
                    # body references before the separate body transfer.
                    verify_raw_dependencies(connection, references=PayloadReferences(reader=reader))
        except sqlite3.Error as error:
            raise ValueError('projection context/public identity verification failed') from error
        finally:
            connection.close()
    digest = hashlib.sha256()
    count = raw_bytes = receipt_count = 0
    layout_counts = {table: 0 for table in identity.get('layouts', {})} if identity else {}
    for ids in batches(iter_projection_ids(database, strategy, immutable=immutable, guard=guard)):
        for record in read_projection_records(reader, ids, identity['authority_uuid'], guard=guard):
            if guard: guard()
            ref = record.reference
            if ref.authority_uuid != identity['authority_uuid'] or ref.kind not in identity['kinds']:
                raise ValueError('projection dependency authority/kind differs from its private profile')
            if ref.sha256 != record.projection.sha256 or ref.kind != record.projection.kind:
                raise ValueError('projection dependency does not prove its exact values')
            digest.update(json.dumps([ref.record_id, ref.kind, ref.sha256], separators=(',', ':')).encode() + b'\n')
            raw_bytes += len(json.dumps(record.to_wire(), sort_keys=True, ensure_ascii=False,
                                        separators=(',', ':'), allow_nan=False).encode())
            count += 1
    for keys in batches(iter_projection_receipt_keys(database, strategy, immutable=immutable, guard=guard)):
        if guard: guard()
        values = reader.get_projection_receipts(keys, authority_uuid=identity['authority_uuid'])
        if guard: guard()
        if len(values) != len(keys) or any(value is None or value.row() != key for value, key in zip(values, keys, strict=True)):
            raise ValueError('projection context source receipt is missing')
        receipt_count += len(keys)
        for key in keys:
            if key[1] in layout_counts:
                layout_counts[key[1]] += 1
    if guard: guard()
    return {
        'contract': (RAW_PROFILE_CONTRACT if identity and 'raw_profile_id' in identity
                     else RAW_CONTRACT if identity and 'raw_layout_contract' in identity
                     else CATALOG_CONTRACT if identity and 'layouts' in identity else CONTRACT),
        'record_count': count, 'receipt_count': receipt_count,
        'authority_uuid': identity['authority_uuid'] if identity else None,
        'namespace': identity['namespace'] if identity else None,
        'kinds': identity['kinds'] if identity else [],
        'closure_sha256': digest.hexdigest(), 'raw_bytes': raw_bytes,
        **({'layouts': identity['layouts'], 'layout_receipt_counts': layout_counts} if identity and 'layouts' in identity else {}),
        **({key: identity[key] for key in ('raw_layout_contract', 'raw_source_schema_sha')}
           if identity and 'raw_layout_contract' in identity else {}),
        **({key: identity[key] for key in RAW_PROFILE_FIELDS}
           if identity and 'raw_profile_id' in identity else {}),
    }
