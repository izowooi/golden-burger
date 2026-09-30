"""RAW parent skeletons keep real SQLite FKs while public source cells leave main.

Only an explicit, validated Golden Black layout activates this adapter. The
original TEXT primary keys and rowids remain private receipt identities. Views
restore source columns; iter_raw_logical_rows exposes the otherwise hidden rowid.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
import hashlib
import json
import os
from itertools import islice
from functools import lru_cache
import sqlite3

from .market_data_raw_profiles import (RAW_TABLES, STRATEGY, BLACK_PROFILE_ID,
    raw_profile, BLACK_INDEX_SQL)
from .market_data_refs import configured_references, externalize_rows
from .market_data_projections import (PublicProjection, ProjectionReceipt, MAX_PROJECTION_BYTES,
    PROJECTION_NULLABLE_CAPABILITY)
from .market_data_store import StoreLimitError
from .market_data_scalar_links import _authority, _namespace, _configured_namespace

CONTRACT = "black-raw-parent-skeleton-v1"
LAYOUT_TABLE = "_public_raw_layout"
RECORD_COLUMN = "_public_record_id"
CACHE_BYTES = 8 << 20


def _q(name):
    return '"' + name.replace('"', '""') + '"'


def _sql(value):
    from .market_data_sql_schema import canonical_sql_key
    return canonical_sql_key(value)


@lru_cache(maxsize=32)
def _source_metadata(table, profile_id=BLACK_PROFILE_ID):
    profile = raw_profile(profile_id).tables[table]
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute(profile.source_sql)
        unique = []
        for row in db.execute('PRAGMA index_list(' + _q(table) + ')'):
            if row[2]: unique.append(tuple(c[2] for c in db.execute('PRAGMA index_info('+_q(row[1])+')')))
        fks = tuple(tuple(row) for row in db.execute('PRAGMA foreign_key_list('+_q(table)+')'))
    return tuple(unique), fks


def skeleton_sql(table, profile_id=BLACK_PROFILE_ID):
    profile = raw_profile(profile_id).tables[table]
    if profile.skeleton_source_sql is not None: return profile.skeleton_source_sql
    # Exact legacy Black v1 DDL is retained for already-created layout readers.
    unique, fks = _source_metadata(table, profile_id)
    clauses = []
    for _,name,declaration,required,default,primary in profile.info:
        if name not in profile.private_columns: continue
        clause = _q(name)+' '+declaration
        if primary: clause += ' PRIMARY KEY'
        if required: clause += ' NOT NULL'
        if default is not None: clause += ' DEFAULT '+default
        if name == 'eligible': clause += ' CHECK (eligible IN (0,1))'
        clauses.append(clause)
    clauses.append(RECORD_COLUMN+' INTEGER NOT NULL CHECK('+RECORD_COLUMN+'>0)')
    for columns in unique:
        if columns != profile.primary_key_columns:
            if not set(columns) <= set(profile.private_columns):
                raise ValueError('RAW unique identity is absent from skeleton')
            clauses.append('UNIQUE ('+','.join(map(_q,columns))+')')
    for _,_,parent,child,key,update,delete,_ in fks:
        clauses.append(f'FOREIGN KEY({_q(child)}) REFERENCES {_q(parent)}({_q(key)}) ON UPDATE {update} ON DELETE {delete}')
    return 'CREATE TABLE '+_q(table)+' ('+','.join(clauses)+')'


INDEX_SQL = dict(BLACK_INDEX_SQL)
LAYOUT_SQL = f'CREATE TABLE {LAYOUT_TABLE}(singleton INTEGER PRIMARY KEY CHECK(singleton=1),contract TEXT NOT NULL,strategy TEXT NOT NULL,namespace TEXT NOT NULL,authority_uuid TEXT NOT NULL,source_schema_sha TEXT NOT NULL)'
PROFILE_CONTRACT = 'shared-raw-parent-skeleton-v2'
PROFILE_LAYOUT_SQL = LAYOUT_SQL[:-1]+',profile_id TEXT NOT NULL,profile_version INTEGER NOT NULL,logical_schema_sha256 TEXT NOT NULL)'


def immutable_sql(table, operation):
    return f"CREATE TRIGGER {table}_forbid_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END"


@lru_cache(maxsize=16)
def source_schema_sha(profile_id=BLACK_PROFILE_ID):
    schema = raw_profile(profile_id)
    material = [(p.table,p.source_sql,p.public_columns,p.identity_columns) for p in schema.tables.values()]
    if profile_id != BLACK_PROFILE_ID:
        material += [('schema-profile',schema.strategy,schema.profile_id,schema.version,
                      schema.application_id,schema.user_version,schema.source_objects,
                      tuple((t,p.skeleton_source_sql) for t,p in schema.tables.items()))]
    if schema.authority_rows:
        material += [('source-authority',schema.authority_rows,schema.level_tables,
            tuple((t,p.condition_field,p.token_field,p.source_time_field) for t,p in schema.tables.items()))]
    if schema.mutable_tables:
        from .market_data_raw_mutable import CONTRACT as mutable_contract
        material += [('mutable-current-cache',mutable_contract,schema.mutable_tables)]
    return hashlib.sha256(json.dumps(material,separators=(',',':')).encode()).hexdigest()


def raw_layout_metadata(connection):
    if not connection.execute("SELECT 1 FROM main.sqlite_master WHERE name=? AND type='table'",(LAYOUT_TABLE,)).fetchone(): return None
    rows = connection.execute(f'SELECT * FROM main.{LAYOUT_TABLE}').fetchall()
    if len(rows) != 1: raise ValueError('unsupported RAW skeleton source contract')
    row = tuple(rows[0])
    if len(row) == 6 and row[:3] == (1,CONTRACT,STRATEGY):
        schema = raw_profile(BLACK_PROFILE_ID)
    elif len(row) == 9 and row[0] == 1 and row[1] == PROFILE_CONTRACT:
        schema = raw_profile(row[6])
        if schema.profile_id == BLACK_PROFILE_ID:
            raise ValueError('Black RAW uses its legacy v1 layout contract, not profiled v2')
        expected_logical = schema.logical_schema_sha256 or source_schema_sha(schema.profile_id)
        if (row[2],row[7],row[8]) != (schema.strategy,schema.version,expected_logical):
            raise ValueError('RAW profile identity/version/logical schema differs')
    else: raise ValueError('unsupported RAW skeleton source contract')
    if row[5] != source_schema_sha(schema.profile_id): raise ValueError('RAW reviewed source schema differs')
    schema.validate_authority(connection)
    _namespace(row[3],schema.strategy);_authority(row[4])
    schema.validate_namespace(connection,row[3])
    validate_raw_namespace_authority(connection,row[3],profile_id=schema.profile_id)
    return dict(contract=row[1],strategy=schema.strategy,namespace=row[3],authority_uuid=row[4],
        source_schema_sha=row[5],tables=tuple(schema.tables),profile_id=schema.profile_id,
        profile_version=schema.version,logical_schema_sha256=schema.logical_schema_sha256 or row[5])


def validate_raw_namespace_authority(connection, namespace, *, profile_id):
    """Bind Guava's distinct Jenkins job and research runtime to its contract."""
    if profile_id == 'black-research-full-v2':
        _namespace(namespace,'golden-black')
        schema=raw_profile(profile_id)
        schema.validate_authority(connection)
        schema.validate_namespace(connection,namespace)
        return
    if profile_id == 'watermelon-independent-raw-lifecycle-v1':
        _namespace(namespace,'golden-watermelon')
        schema=raw_profile(profile_id)
        schema.validate_authority(connection)
        schema.validate_namespace(connection,namespace)
        return
    if profile_id == 'cherry-shadow-resolution-v2':
        _namespace(namespace,'golden-cherry')
        schema=raw_profile(profile_id)
        schema.validate_authority(connection)
        schema.validate_namespace(connection,namespace)
        return
    if profile_id == 'coconut-recorder-v1':
        _namespace(namespace,'golden-coconut')
        schema = raw_profile(profile_id)
        schema.validate_authority(connection)
        schema.validate_namespace(connection,namespace)
        return
    if profile_id != 'guava-research-v1':
        return
    _namespace(namespace,'golden-guava')
    row = connection.execute('SELECT job_name FROM main.collection_contracts WHERE singleton=1').fetchone()
    if row is None:
        raise ValueError('Guava RAW owner has no collection authority')
    jobs = {f'guava-research-{letter}-v1':f'polybot-sim-guava-{letter}' for letter in 'abcd'}
    owner = json.loads(namespace)
    if (owner['runtime'],owner['jenkins_job']) != (row[0],jobs.get(row[0])):
        raise ValueError('Guava RAW runtime/Jenkins owner differs from collection authority')


def profile_for_connection(connection, *, profile_id=None):
    metadata = raw_layout_metadata(connection)
    if metadata is not None:
        if profile_id is not None and profile_id != metadata['profile_id']:
            raise ValueError('RAW explicit source profile differs from bound layout')
        return raw_profile(metadata['profile_id'])
    return raw_profile(profile_id or BLACK_PROFILE_ID)


def logical_raw_schema_rows(connection, *, profile_id=None):
    """Restore reviewed CREATE TABLE contracts only after physical validation."""
    schema = profile_for_connection(connection,profile_id=profile_id)
    raw_auxiliary = validate_raw_layout(connection,profile_id=schema.profile_id)
    auxiliary = raw_auxiliary
    from .market_data_levels import validate_level_layout
    auxiliary |= validate_level_layout(connection)
    from .market_data_private_packets import validate_private_packets
    auxiliary |= validate_private_packets(connection)
    rows = []
    for kind,name,table,sql in connection.execute("SELECT type,name,tbl_name,sql FROM main.sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name,tbl_name"):
        if name in auxiliary:continue
        if raw_auxiliary and kind == 'table' and name in schema.tables:
            sql = schema.tables[name].source_sql
            # SQLite stores CREATE without IF NOT EXISTS; profile stores the exact original for WM.
            if schema.profile_id == BLACK_PROFILE_ID: sql = sql.replace('IF NOT EXISTS ','')
        rows.append((kind,name,table,sql))
    if schema.source_objects and tuple(rows) != schema.source_objects:
        raise ValueError('RAW complete logical source schema differs from reviewed profile')
    return rows


def validate_raw_layout(connection, *, profile_id=None):
    metadata = raw_layout_metadata(connection)
    if metadata is None: return frozenset()
    schema = profile_for_connection(connection,profile_id=profile_id)
    expected = {LAYOUT_TABLE: LAYOUT_SQL if metadata['contract'] == CONTRACT else PROFILE_LAYOUT_SQL,
        **{t:skeleton_sql(t,schema.profile_id) for t in schema.tables},**schema.source_indexes}
    for table in schema.tables:
        expected.update(schema.triggers_for(table))
    for operation in ('UPDATE','DELETE'):
        expected[LAYOUT_TABLE+'_forbid_'+operation.lower()] = immutable_sql(LAYOUT_TABLE,operation)
    for name,sql in expected.items():
        row = connection.execute('SELECT sql FROM main.sqlite_master WHERE name=?',(name,)).fetchone()
        if row is None or _sql(row[0]) != _sql(sql):
            raise ValueError('RAW skeleton schema/immutable constraint differs: '+name)
    for table in schema.tables:
        triggers={row[0] for row in connection.execute("SELECT name FROM main.sqlite_master WHERE type='trigger' AND tbl_name=?",(table,))}
        if triggers != set(schema.triggers_for(table)): raise ValueError('unknown RAW skeleton trigger')
        indexes={row[1] for row in connection.execute('PRAGMA main.index_list('+_q(table)+')') if row[3]=='c'}
        if indexes != set(schema.indexes_for(table)):
            raise ValueError('RAW skeleton index set differs')
    return frozenset({LAYOUT_TABLE,LAYOUT_TABLE+'_forbid_update',LAYOUT_TABLE+'_forbid_delete'})


def validate_raw_source_schema(connection, *, profile_id=None):
    schema=profile_for_connection(connection,profile_id=profile_id)
    schema.validate_authority(connection)
    if schema.application_id is not None and (connection.execute('PRAGMA application_id').fetchone()[0],
            connection.execute('PRAGMA user_version').fetchone()[0]) != (schema.application_id,schema.user_version):
        raise ValueError('RAW source application/schema version differs')
    if raw_layout_metadata(connection):
        validate_raw_layout(connection,profile_id=schema.profile_id)
    else:
        for table,profile in schema.tables.items():
            row=connection.execute("SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
            if row is None or _sql(row[0]) != _sql(profile.source_sql): raise ValueError('RAW source table differs from reviewed schema: '+table)
            expected={name:_sql(sql) for name,sql in schema.triggers_for(table).items()}
            triggers=connection.execute("SELECT name,sql FROM main.sqlite_master WHERE type='trigger' AND tbl_name=?",(table,)).fetchall()
            if {row[0]:_sql(row[1]) for row in triggers} != expected: raise ValueError('RAW source immutable triggers differ: '+table)
            expected_indexes=schema.indexes_for(table)
            indexes={row[1] for row in connection.execute('PRAGMA main.index_list('+_q(table)+')') if row[3]=='c'}
            if indexes != set(expected_indexes):raise ValueError('RAW source has unreviewed indexes')
            for name,sql in expected_indexes.items():
                actual=connection.execute('SELECT sql FROM main.sqlite_master WHERE name=?',(name,)).fetchone()
                if actual is None or _sql(actual[0]) != _sql(sql):raise ValueError('RAW source index differs: '+name)
    if schema.source_objects: logical_raw_schema_rows(connection,profile_id=schema.profile_id)


def create_raw_layout(connection, namespace, authority_uuid, *, profile_id=None):
    if not connection.in_transaction: raise ValueError('RAW layout needs an explicit local transaction')
    schema=raw_profile(profile_id or BLACK_PROFILE_ID)
    _namespace(namespace,schema.strategy);_authority(authority_uuid)
    if schema.profile_id == BLACK_PROFILE_ID:
        connection.execute(LAYOUT_SQL)
        connection.execute(f'INSERT INTO {LAYOUT_TABLE} VALUES(1,?,?,?,?,?)',
            (CONTRACT,schema.strategy,namespace,authority_uuid,source_schema_sha(schema.profile_id)))
    else:
        connection.execute(PROFILE_LAYOUT_SQL)
        connection.execute(f'INSERT INTO {LAYOUT_TABLE} VALUES(1,?,?,?,?,?,?,?,?)',
            (PROFILE_CONTRACT,schema.strategy,namespace,authority_uuid,source_schema_sha(schema.profile_id),
             schema.profile_id,schema.version,schema.logical_schema_sha256 or source_schema_sha(schema.profile_id)))
    for operation in ('UPDATE','DELETE'): connection.execute(immutable_sql(LAYOUT_TABLE,operation))


def initialize_raw_links(connection,namespace,authority_uuid,*,references=None,profile_id=None):
    existing=raw_layout_metadata(connection)
    schema=profile_for_connection(connection,profile_id=profile_id)
    if existing:
        validate_raw_layout(connection,profile_id=schema.profile_id)
        if (existing['namespace'],existing['authority_uuid']) != (namespace,authority_uuid):raise ValueError('RAW runtime ownership changed')
        return
    validate_raw_source_schema(connection,profile_id=schema.profile_id)
    if any(connection.execute(f'SELECT 1 FROM main.{_q(table)} LIMIT 1').fetchone() for table in schema.tables):
        raise ValueError('populated RAW source requires explicit offline derivative migration')
    if not connection.in_transaction or not connection.execute('PRAGMA foreign_keys').fetchone()[0]:
        raise ValueError('RAW activation requires a transaction with foreign keys ON')
    connection.execute('SAVEPOINT initialize_raw')
    try:
        for table in reversed(tuple(schema.tables)): connection.execute('DROP TABLE main.'+_q(table))
        for table in schema.tables: connection.execute(skeleton_sql(table,schema.profile_id))
        for sql in schema.source_indexes.values():connection.execute(sql)
        for table in schema.tables:
            for sql in schema.triggers_for(table).values():connection.execute(sql)
        create_raw_layout(connection,namespace,authority_uuid,profile_id=profile_id)
        validate_raw_source_schema(connection,profile_id=schema.profile_id)
        connection.execute('RELEASE initialize_raw')
    except BaseException:
        connection.execute('ROLLBACK TO initialize_raw');connection.execute('RELEASE initialize_raw');raise
    install_raw_views(connection,references=references)


def _refs(connection, references=None):
    return references or getattr(connection, '_references', None) or configured_references()


def _access(connection, references=None, *, write=False):
    metadata = raw_layout_metadata(connection)
    if metadata is None: raise ValueError('RAW layout is absent')
    refs = _refs(connection, references)
    authority = metadata['authority_uuid']
    if refs.reader is None or refs.reader.scalar_authority_identity() != authority:
        raise ValueError('RAW public reader authority differs')
    if write and (refs.writer is None or refs.writer.scalar_authority_identity() != authority):
        raise ValueError('RAW write needs its authoritative public writer')
    return metadata, refs


def require_raw_capabilities(writer, *, profile_id=None):
    if writer is None:
        raise ValueError('RAW shared storage requires an authoritative writer')
    if hasattr(writer, 'require_capabilities'):
        required=['public-payload-cas-v1','public-projection-block-store-v1',PROJECTION_NULLABLE_CAPABILITY]
        if profile_id == 'watermelon-research-v401':required.append('public-raw-watermelon-v401-v1')
        if profile_id == 'coconut-historical-v6':required.append('public-raw-coconut-historical-v6-v1')
        if profile_id == 'pomegranate-research-full-v4':required.append('public-raw-pomegranate-research-full-v4-v1')
        if profile_id == 'raspberry-queue-echo-v3':required.append('public-raw-raspberry-queue-echo-v3-v1')
        if profile_id == 'strawberry-last-mile-v1':required.append('public-raw-strawberry-last-mile-v1-v1')
        if profile_id == 'strawberry-followup-v2a-v4':required.append('public-raw-strawberry-followup-v2a-v4-v1')
        if profile_id == 'guava-research-v1':required.append('public-raw-guava-research-v1-v1')
        if profile_id == 'coconut-recorder-v1':required.append('public-raw-coconut-recorder-v1-v1')
        if profile_id == 'cherry-shadow-resolution-v2':required.append('public-raw-cherry-shadow-resolution-v2-v1')
        if profile_id == 'watermelon-independent-raw-lifecycle-v1':required.append('public-raw-watermelon-independent-raw-lifecycle-v1-v1')
        if profile_id == 'black-research-full-v2':required.append('public-raw-black-research-full-v2-v1')
        writer.require_capabilities(required)


def _read_records(reader, ids, authority_uuid):
    try:
        records = reader.get_projection_records(ids, authority_uuid)
    except StoreLimitError:
        if len(ids) < 2: raise
        middle = len(ids) // 2
        yield from _read_records(reader, ids[:middle], authority_uuid)
        yield from _read_records(reader, ids[middle:], authority_uuid)
        return
    if [record.reference.record_id for record in records] != ids:
        raise ValueError('RAW public dependency record IDs differ')
    yield from records



def _read_bound_records(reader, receipts, authority_uuid):
    """Prefer one ownership+record read; the legacy pair remains fully checked."""
    bound=getattr(reader,'get_projection_bound_records',None)
    try:
        if callable(bound):
            records=bound([receipt.row() for receipt in receipts],authority_uuid=authority_uuid)
        else:
            actual=reader.get_projection_receipts([receipt.row() for receipt in receipts],authority_uuid=authority_uuid)
            if actual != receipts:raise ValueError('RAW public receipt ownership differs')
            records=list(_read_records(reader,[receipt.record_id for receipt in receipts],authority_uuid))
    except StoreLimitError:
        if len(receipts)<2:raise
        middle=len(receipts)//2
        yield from _read_bound_records(reader,receipts[:middle],authority_uuid)
        yield from _read_bound_records(reader,receipts[middle:],authority_uuid)
        return
    if len(records) != len(receipts):raise ValueError('RAW bound record count differs')
    for receipt,record in zip(receipts,records,strict=True):
        if record is None:raise ValueError('RAW public receipt ownership differs')
        if (record.reference.authority_uuid,record.reference.record_id,record.reference.kind) != (authority_uuid,receipt.record_id,receipt.kind):
            raise ValueError('RAW bound public record identity differs')
        yield record

def _write_batches(rows, projections):
    pending_rows, pending_groups, size = [], [], 0
    for row, group in zip(rows, projections, strict=True):
        if pending_groups and (len(pending_groups) == 500 or size + group.raw_bytes > MAX_PROJECTION_BYTES):
            yield pending_rows, pending_groups
            pending_rows, pending_groups, size = [], [], 0
        pending_rows.append(row); pending_groups.append(group); size += group.raw_bytes
    if pending_groups: yield pending_rows, pending_groups


def iter_raw_receipts(connection):
    metadata = raw_layout_metadata(connection)
    if metadata is None: return
    schema=profile_for_connection(connection)
    for table, profile in schema.tables.items():
        cursor = sqlite3.Connection.cursor(connection)
        cursor.row_factory = None
        try:
            cursor.execute(f'SELECT rowid,{RECORD_COLUMN} FROM main.{_q(table)} ORDER BY rowid')
            for rowid, record_id in cursor:
                yield ProjectionReceipt(metadata['namespace'], table, rowid, profile.kind, record_id)
        finally:
            cursor.close()


def iter_raw_receipt_keys(connection):
    for receipt in iter_raw_receipts(connection): yield receipt.row()


def iter_raw_record_ids(connection):
    if raw_layout_metadata(connection) is None: return
    query = ' UNION '.join(f'SELECT {RECORD_COLUMN} FROM main.{_q(table)}' for table in profile_for_connection(connection).tables)
    for row in connection.execute(query + ' ORDER BY 1'): yield row[0]


def raw_dependencies(connection):
    metadata = raw_layout_metadata(connection)
    if metadata is not None: validate_raw_layout(connection)
    return metadata, iter_raw_receipts(connection)


def verify_raw_dependencies(connection, *, references=None):
    metadata, refs = _access(connection, references)
    schema=profile_for_connection(connection)
    if schema.profile_id == 'watermelon-independent-raw-lifecycle-v1':
        from .market_data_raw_schema_watermelon_sidecar import validate_cycle_ownership
        validate_cycle_ownership(connection,metadata['namespace'])
    _, receipts = raw_dependencies(connection)
    while batch := list(islice(receipts, 500)):
        records = _read_bound_records(refs.reader,batch,metadata['authority_uuid'])
        for receipt, record in zip(batch, records, strict=True):
            profile = schema.tables[receipt.origin_table]
            if record.projection.kind != receipt.kind: raise ValueError('RAW public record kind differs')
            public = dict(zip(profile.public_columns,record.projection.values,strict=True))
            selected = ','.join(map(_q,profile.identity_columns)) or 'rowid'
            local = connection.execute('SELECT '+selected+f' FROM main.{_q(profile.table)} WHERE rowid=?',(receipt.original_id,)).fetchone()
            if not profile.identity_columns:
                if local is None:raise ValueError('RAW source receipt row is absent')
                continue
            if local is None or any(type(public[name]) is not type(value) or public[name] != value for name,value in zip(profile.identity_columns,local,strict=True)):
                raise ValueError('RAW public/local identity binding differs')
    return metadata, iter_raw_receipts(connection)


def install_raw_views(connection, *, references=None):
    if raw_layout_metadata(connection) is None: return
    validate_raw_layout(connection)
    metadata, refs = _access(connection, references)
    schema=profile_for_connection(connection)
    cache, sizes = OrderedDict(), [0]

    def clear():
        cache.clear(); sizes[0] = 0
    if hasattr(connection, '_raw_cache_clear'): connection._raw_cache_clear = clear

    def value(table, record_id, source_rowid, column, *identity):
        key = (table, record_id, source_rowid)
        if key not in cache:
            current = _refs(connection, references)
            profile = schema.tables[table]
            receipt = ProjectionReceipt(metadata['namespace'],table,source_rowid,profile.kind,record_id)
            record, = _read_bound_records(current.reader,[receipt],metadata['authority_uuid'])
            row = dict(zip(profile.public_columns, record.projection.values, strict=True))
            size = record.projection.raw_bytes + 512
            if size <= CACHE_BYTES:
                while cache and sizes[0] + size > CACHE_BYTES:
                    _, (_, old_size) = cache.popitem(last=False); sizes[0] -= old_size
                cache[key] = row, size; sizes[0] += size
        else:
            row = cache[key][0]
        profile = schema.tables[table]
        if any(row[name] != original or type(row[name]) is not type(original)
               for name, original in zip(profile.identity_columns, identity, strict=True)):
            raise ValueError('RAW public/local identity binding differs')
        return row[column]

    connection.create_function('raw_public_value', -1, value)
    for table, profile in schema.tables.items():
        expressions = []
        for info in profile.info:
            _, name, affinity, *_ = info
            if name in profile.private_columns:
                expression = 'c.' + _q(name)
            else:
                args = ''.join(',c.'+_q(key) for key in profile.identity_columns)
                expression = f"CAST(raw_public_value('{table}',c.{RECORD_COLUMN},c.rowid,'{name}'{args}) AS {affinity})"
            expressions.append(expression + ' AS ' + _q(name))
        connection.execute(f'CREATE TEMP VIEW IF NOT EXISTS {_q(table)} AS SELECT ' + ','.join(expressions) + f' FROM main.{_q(table)} c')
        for op in ('UPDATE', 'DELETE'):
            connection.execute(f"CREATE TEMP TRIGGER IF NOT EXISTS raw_{table}_{op.lower()} INSTEAD OF {op} ON {_q(table)} BEGIN SELECT RAISE(ABORT,'append-only evidence'); END")


def iter_raw_private_rows(connection, table, *, profile_id=None):
    profile = profile_for_connection(connection,profile_id=profile_id).tables[table]
    columns = ('__rowid__', *profile.private_columns, RECORD_COLUMN)
    query = f'SELECT rowid,{",".join(map(_q, columns[1:]))} FROM main.{_q(table)} ORDER BY rowid'
    cursor = sqlite3.Connection.cursor(connection)
    cursor.row_factory = None
    try:
        cursor.execute(query)
        for row in cursor: yield dict(zip(columns, row, strict=True))
    finally:
        cursor.close()


def iter_raw_logical_rows(connection, table, *, references=None, profile_id=None):
    profile = profile_for_connection(connection,profile_id=profile_id).tables[table]
    if raw_layout_metadata(connection) is None:
        for row in connection.execute(f'SELECT rowid,{",".join(map(_q,profile.columns))} FROM main.{_q(table)} ORDER BY rowid'):
            yield dict(zip(('__rowid__', *profile.columns), row, strict=True))
        return
    metadata, refs = _access(connection, references)
    with closing(iter_raw_private_rows(connection, table)) as rows:
        while batch := list(islice(rows, 500)):
            receipts = [ProjectionReceipt(metadata['namespace'],table,row['__rowid__'],profile.kind,row[RECORD_COLUMN]) for row in batch]
            records = _read_bound_records(refs.reader,receipts,metadata['authority_uuid'])
            for private, record in zip(batch, records, strict=True):
                if record.projection.kind != profile.kind: raise ValueError('RAW public kind differs')
                public = dict(zip(profile.public_columns, record.projection.values, strict=True))
                if any(public[key] != private[key] or type(public[key]) is not type(private[key]) for key in profile.identity_columns):
                    raise ValueError('RAW public/local identity differs')
                from .market_data_migrate import _validate_reference_cell
                for name in profile.private_columns:
                    _validate_reference_cell(metadata["strategy"], table, name, private[name], references=refs, connection=connection)
                from .market_data_sqlite import expand_private_values
                decoded = refs.decode_many(expand_private_values(connection,[private[key] for key in profile.private_columns]))
                yield {'__rowid__': private['__rowid__'], **dict(zip(profile.private_columns, decoded, strict=True)), **public}


def _validated_rows(connection, table, rows, *, or_ignore=False, references=None):
    schema=profile_for_connection(connection);profile=schema.tables[table]
    unique,fks=_source_metadata(table,schema.profile_id)
    foreign_groups={}
    for fk in fks:foreign_groups.setdefault(fk[0],[]).append(fk)
    foreign_groups=[sorted(group,key=lambda fk:fk[1]) for group in foreign_groups.values()]
    with closing(sqlite3.connect(':memory:')) as validator:
        validator.execute('PRAGMA foreign_keys=ON')
        parents={}
        for group in foreign_groups:
            parent=group[0][2]
            if parent == table:raise ValueError('RAW self-reference needs a reviewed batch validator')
            parents.setdefault(parent,[]).append(tuple(fk[4] for fk in group))
        for parent,key_sets in parents.items():
            columns=dict((row[1],row[2]) for row in connection.execute('PRAGMA main.table_info('+_q(parent)+')'))
            retained=tuple(dict.fromkeys(key for keys in key_sets for key in keys))
            ddl=[_q(key)+' '+columns[key] for key in retained]
            ddl+=['UNIQUE('+','.join(map(_q,keys))+')' for keys in key_sets]
            validator.execute('CREATE TABLE '+_q(parent)+'('+','.join(ddl)+')')
        validator.execute(profile.source_sql)
        normalized=[]
        seeded=set()
        last_rowid=connection.execute('SELECT MAX(rowid) FROM main.'+_q(table)).fetchone()[0]
        last_rowid=last_rowid if last_rowid is not None else 0
        def seed_parents(row):
            for group in foreign_groups:
                parent=group[0][2];keys=tuple(fk[4] for fk in group);values=tuple(row.get(fk[3]) for fk in group)
                found=connection.execute('SELECT '+','.join(map(_q,keys))+' FROM main.'+_q(parent)+' WHERE '+
                    ' AND '.join(_q(key)+'=?' for key in keys),values).fetchone()
                if found:
                    validator.execute('INSERT OR IGNORE INTO '+_q(parent)+'('+','.join(map(_q,keys))+') VALUES('+','.join('?' for _ in keys)+')',tuple(found))
        for row in rows:
            if set(row)-set(profile.columns)-{'__rowid__'}:
                raise ValueError('RAW insertion contains unknown source columns')
            seed_parents(row)
            if or_ignore:
                # Use the original SQLite DDL for conflict ordering: CHECK and
                # NOT NULL IGNORE, UNIQUE first receipt, and FK errors all retain
                # their native meaning. Seed only the existing conflicting rows.
                candidates=set()
                for keys in unique:
                    candidates.update(item[0] for item in connection.execute('SELECT rowid FROM main.'+_q(table)+' WHERE '+
                        ' AND '.join(_q(key)+'=?' for key in keys),tuple(row.get(key) for key in keys)))
                if '__rowid__' in row and connection.execute('SELECT 1 FROM main.'+_q(table)+' WHERE rowid=?',(row['__rowid__'],)).fetchone():
                    candidates.add(row['__rowid__'])
                for rowid in candidates-seeded:
                    source=connection.execute('SELECT '+','.join(map(_q,profile.private_columns))+','+RECORD_COLUMN+' FROM main.'+_q(table)+' WHERE rowid=?',(rowid,)).fetchone()
                    private=dict(zip((*profile.private_columns,RECORD_COLUMN),source,strict=True))
                    metadata,refs=_access(connection,references)
                    receipt=ProjectionReceipt(metadata['namespace'],table,rowid,profile.kind,private[RECORD_COLUMN])
                    record,=_read_bound_records(refs.reader,[receipt],metadata['authority_uuid'])
                    from .market_data_sqlite import expand_private_values
                    original=dict(zip(profile.private_columns,refs.decode_many(expand_private_values(connection,[private[k] for k in profile.private_columns])),strict=True))
                    original.update(zip(profile.public_columns,record.projection.values,strict=True))
                    seed_parents(original)
                    names=('rowid',*profile.columns)
                    validator.execute('INSERT OR IGNORE INTO '+_q(table)+'('+','.join(map(_q,names))+') VALUES('+','.join('?' for _ in names)+')',
                        (rowid,*(original[k] for k in profile.columns)))
                    seeded.add(rowid)
            fields=tuple(name for name in profile.columns if name in row)
            names=(('rowid',) if '__rowid__' in row else ())+fields
            values=((row['__rowid__'],) if '__rowid__' in row else ())+tuple(row[name] for name in fields)
            if or_ignore and '__rowid__' not in row:
                if last_rowid >= 9223372036854775807:
                    raise ValueError('RAW IGNORE random rowid allocation needs a reviewed path')
                names=('rowid',*names);values=(last_rowid+1,*values)
            cursor=validator.execute(('INSERT OR IGNORE' if or_ignore else 'INSERT')+' INTO '+_q(table)+'('+','.join(map(_q,names))+') VALUES('+','.join('?' for _ in names)+')',values)
            if cursor.rowcount == 0:continue
            last_rowid=max(last_rowid,cursor.lastrowid)
            normalized_row=dict(zip(profile.columns,validator.execute('SELECT '+','.join(map(_q,profile.columns))+' FROM '+_q(table)+' WHERE rowid=?',(cursor.lastrowid,)).fetchone(),strict=True))
            if '__rowid__' in row:
                normalized_row['__rowid__']=cursor.lastrowid
                if connection.execute('SELECT 1 FROM main.'+_q(table)+' WHERE rowid=?',(cursor.lastrowid,)).fetchone():
                    raise sqlite3.IntegrityError('RAW duplicate original rowid')
            for keys in (() if or_ignore else unique):
                values=tuple(normalized_row[key] for key in keys)
                if all(value is not None for value in values) and connection.execute('SELECT 1 FROM main.'+_q(table)+' WHERE '+' AND '.join(_q(key)+'=?' for key in keys),values).fetchone():
                    raise sqlite3.IntegrityError('RAW original UNIQUE constraint failed')
            normalized.append(normalized_row)
        return normalized


def insert_raw_rows(connection, strategy, table, rows, *, references=None, namespace=None,
                    or_ignore=False, receipt_context=None):
    metadata = raw_layout_metadata(connection)
    if metadata is None: return False
    schema = profile_for_connection(connection)
    if strategy != schema.strategy: raise ValueError('RAW strategy differs from bound source profile')
    if table not in schema.tables: return False
    if table in schema.mutable_tables:
        if or_ignore:raise ValueError('mutable RAW cache uses the reviewed native UPSERT, not IGNORE')
        from .market_data_raw_mutable import upsert_mutable_raw_rows
        return upsert_mutable_raw_rows(connection,table,rows,references=references,namespace=namespace)
    if not connection.in_transaction or not connection.execute('PRAGMA foreign_keys').fetchone()[0]:
        raise ValueError('RAW publication requires caller transaction and foreign keys ON')
    metadata, refs = _access(connection, references, write=True)
    if namespace is not None:
        _namespace(namespace, strategy)
        if namespace != metadata['namespace']:
            raise ValueError('RAW explicit source/job/runtime differs from the bound namespace')
    elif (os.environ.get('PUBLIC_MARKET_DATA_SOURCE') or os.environ.get('JOB_NAME')) and _configured_namespace(connection, strategy) != metadata['namespace']:
        raise ValueError('RAW source/job/runtime differs from the bound namespace')
    require_raw_capabilities(refs.writer,profile_id=schema.profile_id)
    rows = list(rows)
    if not rows: return True
    profile = schema.tables[table]
    if any(set(row)-set(profile.columns)-{"__rowid__"} for row in rows):
        raise ValueError("RAW insertion contains unknown source columns")
    # Validate the whole batch before the first public write or local row.
    from .market_data_migrate import _validate_reference_cell
    for row in rows:
        for name,value in row.items():
            _validate_reference_cell(strategy,table,name,value,references=refs,connection=connection)
    from .market_data_sqlite import expand_private_values
    decoded = [dict(zip(row,refs.decode_many(expand_private_values(connection,list(row.values()))),strict=True)) for row in rows]
    normalized = _validated_rows(connection, table, decoded, or_ignore=or_ignore,references=refs)
    profile = schema.tables[table]
    projections = [PublicProjection(profile.kind, tuple(row[name] for name in profile.public_columns)) for row in normalized]
    connection.execute('SAVEPOINT publish_raw')
    try:
        from .market_data_private_packets import initialize_private_packets
        initialize_private_packets(connection,strategy=strategy,namespace=metadata['namespace'])
        for batch, groups in _write_batches(normalized, projections):
            references_out = refs.writer.put_public_projections(groups)
            if len(references_out) != len(groups) or any(ref.authority_uuid != metadata['authority_uuid'] or ref.kind != group.kind or ref.sha256 != group.sha256 for ref, group in zip(references_out, groups, strict=True)):
                raise ValueError('RAW public ACK differs from exact group')
            # Publish the batch's public bodies before the first local parent.
            # Mixed fragments are prepared and durably acknowledged by the same
            # bounded encoder; no speculative ACK is used for private envelopes.
            private_rows = externalize_rows(strategy, table,
                [{name: row[name] for name in profile.private_columns} for row in batch],
                references=refs)
            if receipt_context is not None:
                from .market_data_index import index_public_row
                for row,private in zip(batch,private_rows,strict=True):
                    context=receipt_context(row) if callable(receipt_context) else receipt_context
                    if context is not None:
                        index_public_row(strategy,table,row,private,references=refs,context=context)
            receipts = []
            for row, ref, private in zip(batch, references_out, private_rows, strict=True):
                from .market_data_private_packets import intern_private_row
                private = intern_private_row(connection,strategy,table,private,namespace=metadata["namespace"],references=refs)
                names = (('rowid',) if '__rowid__' in row else ()) + profile.private_columns + (RECORD_COLUMN,)
                values = ((row['__rowid__'],) if '__rowid__' in row else ()) + tuple(private[name] for name in profile.private_columns) + (ref.record_id,)
                cursor = connection.execute(f'INSERT INTO main.{_q(table)}({",".join(map(_q,names))}) VALUES({",".join("?" for _ in names)})', values)
                receipts.append(ProjectionReceipt(metadata['namespace'], table, cursor.lastrowid, profile.kind, ref.record_id))
            refs.writer.append_projection_receipts(receipts, authority_uuid=metadata['authority_uuid'])
        connection.execute('RELEASE publish_raw')
    except BaseException:
        connection.execute('ROLLBACK TO publish_raw'); connection.execute('RELEASE publish_raw')
        raise
    return True
