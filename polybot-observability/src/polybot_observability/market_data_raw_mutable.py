"""Native latest-cache UPSERT with immutable exact public receipt tuples.

The original cache rowid/token and current private context remain local. Public
source values use the existing immutable (namespace, table, rowid, kind, record)
receipt contract. No private history table or synthetic observation is created;
closure follows only the current cache pointer. Repeated public states reuse
both their projection and receipt, including after private-clock-only updates.
"""
from __future__ import annotations

from contextlib import closing
import os
import sqlite3

from .market_data_projections import PublicProjection, ProjectionReceipt
from .market_data_raw_profiles import profile_for_connection


CONTRACT = 'public-raw-mutable-current-tuples-v1'
_REVIEWED_KEYS = {
    ('strawberry-last-mile-v1', 'latest_outcome_state'): 'token_id',
    ('coconut-recorder-v1', 'tracked_events'): 'event_id',
}


def _q(name):
    return '"' + name.replace('"', '""') + '"'


def _normalize(connection, table, source, profile, key):
    """Use the original SQLite constraints and current token conflict semantics."""
    names = profile.columns
    if set(source) - set(names) - {'__rowid__'}:
        raise ValueError('mutable RAW insertion contains unknown source columns')
    if set(names) - set(source):
        raise ValueError('mutable RAW upsert requires the complete native current row')
    old = connection.execute(f'SELECT rowid FROM main.{_q(table)} WHERE {_q(key)}=?', (source[key],)).fetchone()
    if old is not None:
        rowid = old[0]
        if '__rowid__' in source and source['__rowid__'] != rowid:
            raise ValueError('mutable RAW update changed the original rowid')
    elif '__rowid__' in source:
        rowid = source['__rowid__']
        if connection.execute(f'SELECT 1 FROM main.{_q(table)} WHERE rowid=?', (rowid,)).fetchone():
            raise sqlite3.IntegrityError('mutable RAW duplicate original rowid')
    else:
        maximum = connection.execute(f'SELECT MAX(rowid) FROM main.{_q(table)}').fetchone()[0]
        if maximum == (1 << 63) - 1:
            raise ValueError('mutable RAW random rowid allocation requires an explicit source rowid')
        rowid = 1 if maximum is None else maximum + 1
    with closing(sqlite3.connect(':memory:')) as validator:
        validator.execute('PRAGMA foreign_keys=ON')
        fks = connection.execute(f'PRAGMA main.foreign_key_list({_q(table)})').fetchall()
        for fk in fks:
            parent, child, key = fk[2:5]
            validator.execute(f'CREATE TABLE IF NOT EXISTS {_q(parent)}({_q(key)} TEXT UNIQUE)')
            found = connection.execute(f'SELECT {_q(key)} FROM main.{_q(parent)} WHERE {_q(key)}=?', (source[child],)).fetchone()
            if found is not None:
                validator.execute(f'INSERT OR IGNORE INTO {_q(parent)} VALUES(?)', (found[0],))
        validator.execute(profile.source_sql)
        validator.execute(f'INSERT INTO {_q(table)}(rowid,' + ','.join(map(_q, names)) + ') VALUES(' + ','.join('?' for _ in range(len(names)+1)) + ')',
                          (rowid, *(source[name] for name in names)))
        values = validator.execute(f'SELECT rowid,' + ','.join(map(_q, names)) + f' FROM {_q(table)}').fetchone()
    return dict(zip(('__rowid__', *names), values, strict=True))


def upsert_mutable_raw_rows(connection, table, rows, *, references=None, namespace=None):
    from .market_data_raw_links import _access, require_raw_capabilities, _read_bound_records

    schema = profile_for_connection(connection)
    if table not in schema.mutable_tables:
        return False
    key = _REVIEWED_KEYS.get((schema.profile_id, table))
    if key is None:
        raise ValueError('unreviewed mutable RAW source profile')
    if not connection.in_transaction or not connection.execute('PRAGMA foreign_keys').fetchone()[0]:
        raise ValueError('mutable RAW publication requires caller transaction and foreign keys ON')
    metadata, refs = _access(connection, references, write=True)
    if namespace is not None and metadata['namespace'] != namespace:
        raise ValueError('mutable RAW namespace differs from bound owner')
    if namespace is None and (os.environ.get('PUBLIC_MARKET_DATA_SOURCE') or os.environ.get('JOB_NAME')):
        from .market_data_scalar_links import _configured_namespace
        if _configured_namespace(connection,schema.strategy) != metadata['namespace']:
            raise ValueError('mutable RAW source/job/runtime differs from bound owner')
    require_raw_capabilities(refs.writer, profile_id=schema.profile_id)
    profile = schema.tables[table]
    connection.execute('SAVEPOINT publish_mutable_raw')
    try:
        for incoming in rows:
            from .market_data_migrate import _validate_reference_cell
            for name, value in incoming.items():
                _validate_reference_cell(schema.strategy, table, name, value, references=refs, connection=connection)
            from .market_data_sqlite import expand_private_values
            row = dict(zip(incoming, refs.decode_many(expand_private_values(connection, list(incoming.values()))), strict=True))
            row = _normalize(connection, table, row, profile, key)
            projection = PublicProjection(profile.kind, tuple(row[name] for name in profile.public_columns))
            acknowledgements = refs.writer.put_public_projections([projection])
            if len(acknowledgements) != 1:
                raise ValueError('mutable RAW public ACK differs')
            ref = acknowledgements[0]
            if (ref.authority_uuid, ref.kind, ref.sha256) != (metadata['authority_uuid'], profile.kind, projection.sha256):
                raise ValueError('mutable RAW public ACK differs')
            receipt = ProjectionReceipt(metadata['namespace'], table, row['__rowid__'], profile.kind, ref.record_id)
            # Existing public storage appends immutable five-tuples with INSERT
            # OR IGNORE; another record never overwrites the original receipt.
            refs.writer.append_projection_receipts([receipt], authority_uuid=metadata['authority_uuid'])
            record, = _read_bound_records(refs.reader, [receipt], metadata['authority_uuid'])
            if record.projection != projection:
                raise ValueError('mutable RAW public readback differs')
            from .market_data_refs import externalize_row
            from .market_data_private_packets import initialize_private_packets, intern_private_row
            private = externalize_row(schema.strategy, table,
                {name: row[name] for name in profile.private_columns}, references=refs)
            if schema.profile_id == 'coconut-recorder-v1':
                initialize_private_packets(connection, strategy=schema.strategy, namespace=metadata['namespace'])
                private = intern_private_row(connection, schema.strategy, table, private,
                    namespace=metadata['namespace'], references=refs)
            names = ('rowid', *profile.private_columns, '_public_record_id')
            values = (row['__rowid__'], *(private[name] for name in profile.private_columns), ref.record_id)
            updates = ','.join(_q(name) + '=excluded.' + _q(name) for name in names[1:] if name != key)
            connection.execute(f'INSERT INTO main.{_q(table)}(' + ','.join(map(_q, names)) + ') VALUES(' + ','.join('?' for _ in names) +
                               f') ON CONFLICT({_q(key)}) DO UPDATE SET ' + updates, values)
        connection.execute('RELEASE publish_mutable_raw')
    except BaseException:
        connection.execute('ROLLBACK TO publish_mutable_raw')
        connection.execute('RELEASE publish_mutable_raw')
        raise
    return True
