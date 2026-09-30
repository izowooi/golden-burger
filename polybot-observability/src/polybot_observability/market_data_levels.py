"""Store public numeric price ladders once, with transactional local identity links.

The original empty/legacy level table stays as a schema/constraint authority.
Connection-local read views join compact local identities to shared public arrays.
New level writes must go through ``insert_shared_levels``; direct SQL writes are
intentionally rejected by the read view. Private experimental flags stay local.
"""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
import json
import os
import sqlite3

from .market_data_policy import public_level_projections
from .market_data_refs import MissingMarketDataConfiguration, configured_references


def quote_identifier(value):
    return '"' + value.replace('"','""') + '"'


CONTRACT = 'public-level-links-v1'
LAYOUT_TABLE = '_market_data_layout'
GROUP_TABLE = '_market_data_level_groups'
BINDING_TABLE = '_market_data_level_bindings'
SCHEMA = (
    f'CREATE TABLE IF NOT EXISTS {LAYOUT_TABLE}(singleton INTEGER PRIMARY KEY CHECK(singleton=1),contract TEXT NOT NULL,strategy TEXT NOT NULL)',
    f'CREATE TABLE IF NOT EXISTS {GROUP_TABLE}(id INTEGER PRIMARY KEY,table_name TEXT NOT NULL,snapshot_id TEXT NOT NULL,body_ref TEXT NOT NULL,UNIQUE(table_name,snapshot_id))',
    f'CREATE TABLE IF NOT EXISTS {BINDING_TABLE}(table_name TEXT NOT NULL,level_id TEXT NOT NULL,group_id INTEGER NOT NULL REFERENCES {GROUP_TABLE}(id),ordinal INTEGER NOT NULL,retained_json TEXT NOT NULL,PRIMARY KEY(table_name,level_id),UNIQUE(group_id,ordinal)) WITHOUT ROWID',
)


@lru_cache(maxsize=1)
def _expected_layout():
    c=sqlite3.connect(':memory:')
    try:
        for statement in SCHEMA:
            c.execute(statement)
        return tuple(c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"))
    finally:
        c.close()


def validate_level_layout(connection):
    rows=tuple(tuple(row) for row in connection.execute("SELECT type,name,tbl_name,sql FROM main.sqlite_master WHERE name GLOB '_market_data_*' AND sql IS NOT NULL ORDER BY type,name"))
    if rows and rows != _expected_layout():
        raise ValueError('public level auxiliary schema mismatch')
    return frozenset(row[1] for row in rows)


def _rule(strategy, table):
    return next((rule for rule in public_level_projections(strategy) if rule.table == table), None)


def install_level_views(connection: sqlite3.Connection, *, references=None) -> None:
    exists = connection.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?", (LAYOUT_TABLE,)).fetchone()
    if not exists:
        return
    validate_level_layout(connection)
    metadata = connection.execute(f'SELECT contract,strategy FROM main.{LAYOUT_TABLE}').fetchall()
    if len(metadata) != 1 or metadata[0][0] != CONTRACT:
        raise ValueError('unsupported public level-link layout')
    strategy = metadata[0][1]
    rules = public_level_projections(strategy)
    if not rules:
        raise ValueError('public level-link strategy has no schema policy')

    def public_body(reference):
        codec = references or getattr(connection, '_references', None) or configured_references()
        value = codec.decode_many([reference])[0]
        if not isinstance(value, str):
            raise ValueError('public level array must decode to TEXT')
        return value
    connection.create_function('market_data_level_body', 1, public_body)
    tables = {r[0] for r in connection.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
    for rule in rules:
        if rule.table not in tables:
            continue
        table = quote_identifier(rule.table)
        columns = [r[1] for r in connection.execute(f'PRAGMA main.table_info({table})')]
        expected = set(rule.columns) | set(rule.retained_columns)
        if set(columns) != expected:
            raise ValueError(f'public level schema differs from policy: {rule.table}')
        expressions = []
        for column in columns:
            if column == 'level_id':
                value = "CASE WHEN json_type(b.retained_json,'$.__level_id__') IS NOT NULL THEN json_extract(b.retained_json,'$.__level_id__') ELSE b.level_id END"
            elif column == 'snapshot_id':
                value = 'g.snapshot_id'
            elif column in rule.retained_columns:
                value = f"json_extract(b.retained_json,'$.{column}')"
            else:
                value = f"json_extract(level.value,'$.{column}')"
            expressions.append(value + ' AS ' + quote_identifier(column))
        # Table names and column names are constants in the reviewed policy.
        sql = (f'CREATE TEMP VIEW IF NOT EXISTS {table} AS SELECT * FROM main.{table} UNION ALL SELECT ' +
               ','.join(expressions) + f' FROM main.{GROUP_TABLE} g CROSS JOIN '
               f'json_each(market_data_level_body(g.body_ref)) level JOIN main.{BINDING_TABLE} b '
               f'ON b.group_id=g.id AND b.ordinal=CAST(level.key AS INTEGER) '
               f"WHERE g.table_name='{rule.table}' AND b.table_name='{rule.table}'")
        connection.execute(sql)


def _insert_shared_levels(connection: sqlite3.Connection, strategy: str, table: str,
                          rows, *, references=None, preserve_rowid=False) -> bool:
    rule = _rule(strategy, table)
    if rule is None:
        return False
    codec = references or configured_references()
    if codec.writer is None:
        return False
    rows = list(rows)
    if not rows:
        return True
    if not connection.in_transaction:
        raise ValueError('shared level publication requires the collector transaction')
    for statement in SCHEMA:
        connection.execute(statement)
    metadata = connection.execute(f'SELECT contract,strategy FROM {LAYOUT_TABLE}').fetchall()
    if not metadata:
        connection.execute(f'INSERT INTO {LAYOUT_TABLE} VALUES(1,?,?)', (CONTRACT, strategy))
    elif len(metadata) != 1 or tuple(metadata[0]) != (CONTRACT, strategy):
        raise ValueError('public level layout identity mismatch')
    columns = [r[1] for r in connection.execute(f'PRAGMA main.table_info({quote_identifier(table)})')]
    if set(columns) != set(rule.columns) | set(rule.retained_columns):
        raise ValueError('public level schema differs from policy')
    groups = defaultdict(list)
    for row in rows:
        if set(row)-({'__rowid__'} if preserve_rowid else set()) != set(columns):
            raise ValueError('shared levels require complete source columns')
        groups[row['snapshot_id']].append(row)
    insert_columns=(['rowid'] if preserve_rowid else [])+columns
    insert = (f'INSERT INTO main.{quote_identifier(table)} (' + ','.join(map(quote_identifier, insert_columns)) +
              ') VALUES (' + ','.join('?' for _ in insert_columns) + ')')
    native_rowids = preserve_rowid and all('__rowid__' not in row for row in rows)
    native_ids = {}
    if native_rowids:
        # Native appends need only the next ID and incoming-key validation.
        # Copying every historical physical level into a TEMP B-tree on every
        # connection turns a small live batch into an unbounded archive scan.
        # An explicit-ID migration later on this same connection must rebuild
        # its complete validation set after these native appends.
        connection.execute('DROP TABLE IF EXISTS temp.'+quote_identifier('_public_level_rowids_'+table))
        rowid_table='_public_level_native_ids_'+table
        connection.execute('CREATE TEMP TABLE IF NOT EXISTS '+quote_identifier(rowid_table)+'(original_rowid INTEGER PRIMARY KEY,original_level_id TEXT UNIQUE)')
        invalid, shared_maximum = connection.execute(
            f"SELECT MAX(CASE WHEN COALESCE(json_type(retained_json,'$.__rowid__'),'null')!='integer' THEN 1 ELSE 0 END),MAX(json_extract(retained_json,'$.__rowid__')) FROM main.{BINDING_TABLE} WHERE table_name=?",(table,)).fetchone()
        if invalid:
            raise ValueError('original level rowid is unknown in existing bindings')
        maxima=[connection.execute('SELECT MAX(rowid) FROM main.'+quote_identifier(table)).fetchone()[0],
                shared_maximum,connection.execute('SELECT MAX(original_rowid) FROM temp.'+quote_identifier(rowid_table)).fetchone()[0]]
        maximum=max(value for value in maxima if value is not None) if any(value is not None for value in maxima) else 0
        for snapshot, group in groups.items():
            native_ids[snapshot]=[]
            for row in group:
                maximum+=1
                if maximum >= (1<<63):raise ValueError('unsupported original level rowid')
                connection.execute('INSERT INTO temp.'+quote_identifier(rowid_table)+' VALUES(?,?)',(maximum,row['level_id']))
                native_ids[snapshot].append(maximum)
        duplicate=connection.execute(
            f"SELECT 1 FROM main.{BINDING_TABLE} b JOIN temp.{quote_identifier(rowid_table)} n "
            "ON n.original_level_id=CASE WHEN json_type(b.retained_json,'$.__level_id__') IS NOT NULL THEN json_extract(b.retained_json,'$.__level_id__') ELSE b.level_id END "
            "WHERE b.table_name=? AND json_extract(b.retained_json,'$.__rowid__')!=n.original_rowid LIMIT 1",(table,)).fetchone()
        if duplicate:raise sqlite3.IntegrityError('shared level original ID already exists')
    elif preserve_rowid:
        # Connection-local B-tree keeps rowid validation bounded for full books;
        # it participates in the caller savepoint and never changes v1 storage.
        rowid_table='_public_level_rowids_'+table
        if not connection.execute("SELECT 1 FROM sqlite_temp_master WHERE type='table' AND name=?",(rowid_table,)).fetchone():
            if connection.execute(f"SELECT 1 FROM main.{BINDING_TABLE} WHERE table_name=? AND COALESCE(json_type(retained_json,'$.__rowid__'),'null')!='integer' LIMIT 1",(table,)).fetchone():
                raise ValueError('original level rowid is unknown in existing bindings')
            connection.execute('CREATE TEMP TABLE '+quote_identifier(rowid_table)+'(original_rowid INTEGER PRIMARY KEY,original_level_id TEXT UNIQUE)')
            connection.execute('INSERT INTO temp.'+quote_identifier(rowid_table)+' SELECT rowid,level_id FROM main.'+quote_identifier(table))
            connection.execute('INSERT INTO temp.'+quote_identifier(rowid_table)+f" SELECT json_extract(retained_json,'$.__rowid__'),CASE WHEN json_type(retained_json,'$.__level_id__') IS NOT NULL THEN json_extract(retained_json,'$.__level_id__') ELSE level_id END FROM main.{BINDING_TABLE} WHERE table_name=?",(table,))
        maximum=connection.execute('SELECT MAX(original_rowid) FROM temp.'+quote_identifier(rowid_table)).fetchone()[0]
        maximum=maximum if maximum is not None else 0
    for snapshot, group in groups.items():
        if not connection.execute(
                f'SELECT 1 FROM main.{quote_identifier(rule.parent_table)} WHERE snapshot_id=?',
                (snapshot,)).fetchone():
            raise ValueError('shared level group has no local parent observation')
        if connection.execute(f'SELECT 1 FROM main.{quote_identifier(table)} WHERE snapshot_id=? LIMIT 1', (snapshot,)).fetchone():
            raise ValueError('a snapshot level group must be published exactly once')
        # Validate all original CHECK/UNIQUE/FK constraints and SQLite affinities,
        # then roll back the temporary rows. Append-only DELETE triggers are not
        # bypassed or disabled; no deletion statement is executed.
        connection.execute('SAVEPOINT public_levels_validate')
        try:
            payload=[]
            for index,row in enumerate(group):
                if preserve_rowid:
                    rowid=native_ids[snapshot][index] if native_rowids else row.get('__rowid__',maximum+1)
                    if type(rowid) is not int or not -(1<<63)<=rowid<(1<<63):raise ValueError('unsupported original level rowid')
                    if not native_rowids:connection.execute('INSERT INTO temp.'+quote_identifier(rowid_table)+' VALUES(?,?)',(rowid,row['level_id']))
                    maximum=max(maximum,rowid)
                payload.append(((rowid,) if preserve_rowid else ())+tuple(row[col] for col in columns))
            connection.executemany(insert,payload)
            normalized = connection.execute(
                'SELECT ' + ','.join(map(quote_identifier, insert_columns)) +
                f' FROM main.{quote_identifier(table)} WHERE snapshot_id=? ORDER BY side,level_index', (snapshot,)).fetchall()
            normalized = [dict(zip((['__rowid__'] if preserve_rowid else [])+columns, row)) for row in normalized]
        finally:
            connection.execute('ROLLBACK TO public_levels_validate')
            connection.execute('RELEASE public_levels_validate')
        if preserve_rowid and not native_rowids:
            connection.executemany('INSERT INTO temp.'+quote_identifier(rowid_table)+' VALUES(?,?)',((row['__rowid__'],row['level_id']) for row in normalized))
        public_columns = [column for column in columns if column not in {'level_id','snapshot_id'} and column not in rule.retained_columns]
        public = [{column: row[column] for column in public_columns} for row in normalized]
        serialized = json.dumps(public, sort_keys=True, separators=(',', ':'), allow_nan=False)
        reference = codec.encode_many([serialized])[0]
        cursor = connection.execute(f'INSERT INTO {GROUP_TABLE}(table_name,snapshot_id,body_ref) VALUES(?,?,?)', (table,snapshot,reference))
        group_id = cursor.lastrowid
        for ordinal,row in enumerate(normalized):
            private={key:row[key] for key in (*rule.retained_columns, *(('__rowid__',) if preserve_rowid else ())) }
            binding_id=row['level_id']
            if preserve_rowid and (binding_id is None or connection.execute(
                    f'SELECT 1 FROM main.{BINDING_TABLE} WHERE table_name=? AND level_id=?',(table,binding_id)).fetchone()):
                # Original TEXT PRIMARY KEY permits multiple NULLs. The binding
                # key is internal; exact original identities remain private.
                private['__level_id__']=binding_id
                binding_id='__original_level_rowid__:'+str(row['__rowid__'])
                while connection.execute(f'SELECT 1 FROM main.{BINDING_TABLE} WHERE table_name=? AND level_id=?',(table,binding_id)).fetchone():
                    binding_id+=':'
            connection.execute(f'INSERT INTO {BINDING_TABLE} VALUES(?,?,?,?,?)',
                (table,binding_id,group_id,ordinal,json.dumps(private,sort_keys=True,separators=(',',':'))))
    install_level_views(connection, references=codec)
    return True


def insert_shared_levels(connection: sqlite3.Connection, strategy: str, table: str,
                         rows, *, references=None, preserve_rowid=False) -> bool:
    if _rule(strategy,table) is None:
        return False
    codec=references or configured_references()
    if codec.writer is None:
        if os.environ.get('PUBLIC_MARKET_DATA_DB'):
            raise MissingMarketDataConfiguration('read-only shared configuration cannot publish levels')
        return False
    if not connection.in_transaction:
        raise ValueError('shared level publication requires the collector transaction')
    connection.execute('SAVEPOINT publish_public_levels')
    try:
        handled=_insert_shared_levels(connection,strategy,table,rows,references=codec,preserve_rowid=preserve_rowid)
        connection.execute('RELEASE publish_public_levels')
        return handled
    except BaseException:
        connection.execute('ROLLBACK TO publish_public_levels')
        connection.execute('RELEASE publish_public_levels')
        raise


def iter_level_logical_rows(connection, strategy, table, *, references=None, require_rowid=False):
    """Restore ladder rows ordered by original rowid, without guessing old IDs."""
    rule=_rule(strategy,table)
    if rule is None:raise ValueError('unreviewed public level table')
    columns=[r[1] for r in connection.execute(f'PRAGMA main.table_info({quote_identifier(table)})')]
    query='SELECT rowid AS __rowid__,'+','.join(map(quote_identifier,columns))+' FROM main.'+quote_identifier(table)
    if validate_level_layout(connection):
        codec=references or getattr(connection,'_references',None) or configured_references()
        connection.create_function('raw_level_body',1,lambda value:codec.decode_many([value])[0])
        expressions=[]
        for column in columns:
            if column == 'level_id':value="CASE WHEN json_type(b.retained_json,'$.__level_id__') IS NOT NULL THEN json_extract(b.retained_json,'$.__level_id__') ELSE b.level_id END"
            elif column == 'snapshot_id':value='g.snapshot_id'
            elif column in rule.retained_columns:value=f"json_extract(b.retained_json,'$.{column}')"
            else:value=f"json_extract(level.value,'$.{column}')"
            expressions.append(value)
        query+=(" UNION ALL SELECT json_extract(b.retained_json,'$.__rowid__'),"+','.join(expressions)+
            f' FROM main.{GROUP_TABLE} g CROSS JOIN json_each(raw_level_body(g.body_ref)) level JOIN main.{BINDING_TABLE} b '+
            f"ON b.group_id=g.id AND b.ordinal=CAST(level.key AS INTEGER) WHERE g.table_name='{table}' AND b.table_name='{table}'")
    previous=None
    for row in connection.execute(query+' ORDER BY __rowid__'):
        if require_rowid and type(row[0]) is not int:raise ValueError('original level rowid is unknown in legacy bindings')
        if row[0] is not None and row[0] == previous:raise ValueError('duplicate original level rowid')
        previous=row[0]
        yield dict(zip(('__rowid__',*columns),row,strict=True))
