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
                value = 'b.level_id'
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
                          rows, *, references=None) -> bool:
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
        if set(row) != set(columns):
            raise ValueError('shared levels require complete source columns')
        groups[row['snapshot_id']].append(row)
    insert = (f'INSERT INTO main.{quote_identifier(table)} (' + ','.join(map(quote_identifier, columns)) +
              ') VALUES (' + ','.join('?' for _ in columns) + ')')
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
            connection.executemany(insert, [tuple(row[col] for col in columns) for row in group])
            normalized = connection.execute(
                'SELECT ' + ','.join(map(quote_identifier, columns)) +
                f' FROM main.{quote_identifier(table)} WHERE snapshot_id=? ORDER BY side,level_index', (snapshot,)).fetchall()
            normalized = [dict(zip(columns, row)) for row in normalized]
        finally:
            connection.execute('ROLLBACK TO public_levels_validate')
            connection.execute('RELEASE public_levels_validate')
        public_columns = [column for column in columns if column not in {'level_id','snapshot_id'} and column not in rule.retained_columns]
        public = [{column: row[column] for column in public_columns} for row in normalized]
        serialized = json.dumps(public, sort_keys=True, separators=(',', ':'), allow_nan=False)
        reference = codec.encode_many([serialized])[0]
        cursor = connection.execute(f'INSERT INTO {GROUP_TABLE}(table_name,snapshot_id,body_ref) VALUES(?,?,?)', (table,snapshot,reference))
        group_id = cursor.lastrowid
        connection.executemany(f'INSERT INTO {BINDING_TABLE} VALUES(?,?,?,?,?)', [
            (table, row['level_id'], group_id, ordinal,
             json.dumps({key: row[key] for key in rule.retained_columns}, sort_keys=True, separators=(',', ':')))
            for ordinal, row in enumerate(normalized)
        ])
    install_level_views(connection, references=codec)
    return True


def insert_shared_levels(connection: sqlite3.Connection, strategy: str, table: str,
                         rows, *, references=None) -> bool:
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
        handled=_insert_shared_levels(connection,strategy,table,rows,references=codec)
        connection.execute('RELEASE publish_public_levels')
        return handled
    except BaseException:
        connection.execute('ROLLBACK TO publish_public_levels')
        connection.execute('RELEASE publish_public_levels')
        raise
