"""Build and verify a derivative DB with public body/level references, without editing source.

The source must be an offline/checksummed pin. This module does not cut over active
jobs or move/delete originals. Scalar-level migration is explicit in the manifest.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from itertools import groupby
import os
from pathlib import Path
import sqlite3

from .market_data_refs import PayloadReferences, externalize_row, parse_reference
from .market_data_sqlite import connect as resolving_connect


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _cell_bytes(value) -> bytes:
    if value is None:
        return b'N'
    if isinstance(value, bytes):
        return b'B' + value
    if isinstance(value, str):
        return b'T' + value.encode('utf-8')
    if isinstance(value, int):
        return b'I' + str(value).encode('ascii')
    if isinstance(value, float):
        return b'F' + value.hex().encode('ascii')
    raise TypeError(type(value).__name__)


def _update_digest(digest, row):
    for cell in row:
        data = _cell_bytes(cell)
        digest.update(len(data).to_bytes(8, 'big'))
        digest.update(data)


def _select(connection, table: str, *, include_rowid: bool = True):
    info = connection.execute(f'PRAGMA table_xinfo({quote_identifier(table)})').fetchall()
    columns = [r[1] for r in info if r[6] == 0]
    primary = sorted((r[5], r[1]) for r in info if r[5])
    ddl = connection.execute('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone()[0]
    hidden_rowid = next((name for name in ('_rowid_', 'rowid', 'oid') if name not in columns), None)
    if include_rowid and 'WITHOUT ROWID' not in ddl.upper() and hidden_rowid:
        columns.insert(0, hidden_rowid)
    # Explicit PK ordering is stable across rebuilds (including WITHOUT ROWID).
    order = ','.join(quote_identifier(name) for _, name in primary) if primary else 'rowid'
    sql = ('SELECT ' + ','.join(map(quote_identifier, columns)) +
           f' FROM {quote_identifier(table)} ORDER BY {order}')
    return columns, sql


def migrate_public_bodies(source: Path, destination: Path, *, strategy: str,
                          source_sha256: str, references: PayloadReferences,
                          batch_rows: int = 128, progress=None, include_levels: bool = False) -> dict:
    from .market_data_levels import insert_shared_levels, validate_level_layout
    from .market_data_policy import public_level_projections
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    if source == destination or destination.exists() or destination.is_symlink():
        raise ValueError('migration destination must be a new distinct file')
    if not 1 <= batch_rows <= 4096:
        raise ValueError('batch_rows out of range')
    if references.reader is None or references.writer is None:
        raise ValueError('migration requires shared writer and reader')
    if any(Path(str(source) + suffix).exists() for suffix in ('-wal', '-journal')):
        raise ValueError('migration requires offline source without active journal/WAL')
    if file_sha256(source) != source_sha256:
        raise ValueError('source checksum mismatch')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation ensures an unrelated caller cannot be overwritten.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    source_db = sqlite3.connect(source.as_uri() + '?mode=ro&immutable=1', uri=True)
    target = sqlite3.connect(destination)
    refs: set[str] = set()
    level_rules = {rule.table:rule for rule in public_level_projections(strategy)} if include_levels else {}
    manifest = {
        'contract': 'shared-public-bodies-migration-v1',
        'started_at': datetime.now(timezone.utc).isoformat(),
        'source_path': str(source), 'source_sha256': source_sha256,
        'destination_path': str(destination), 'strategy': strategy,
        'status': 'BUILDING', 'tables': {},
        'scope': 'Explicit public body columns only; scalar levels and private state preserved.',
    }
    if include_levels:
        manifest['scope']='Explicit public bodies and raw levels; private identities/flags preserved. Level primary keys are preserved; implicit SQLite rowid is not part of the level-view interface.'
    sidecar = destination.with_suffix(destination.suffix + '.migration.json')
    sidecar.write_text(json.dumps(manifest, indent=2))
    try:
        source_db.execute('PRAGMA query_only=ON')
        # Read-only source validation and final row-level verification complement
        # the file checksum; a matching hash alone proves no logical integrity.
        if source_db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('source SQLite quick_check failed')
        schema = source_db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        if any(row[1].startswith('_market_data_') for row in schema):
            raise ValueError('source already contains level links; use verified closure copying rather than legacy migration')
        if any('VIRTUAL TABLE' in row[3].upper() for row in schema):
            raise ValueError('virtual-table migration requires an explicit adapter')
        for pragma in ('page_size', 'application_id', 'user_version'):
            value = source_db.execute(f'PRAGMA {pragma}').fetchone()[0]
            target.execute(f'PRAGMA {pragma}={int(value)}')
        target.execute('PRAGMA synchronous=FULL')
        target.execute('PRAGMA journal_mode=DELETE')
        target.execute('PRAGMA foreign_keys=OFF')
        for kind, name, _, sql in schema:
            if kind == 'table':
                target.execute(sql)
        target.commit()
        for kind, table, _, _ in schema:
            if kind != 'table':
                continue
            if table in level_rules:
                continue
            columns, query = _select(source_db, table)
            insert = (f'INSERT INTO {quote_identifier(table)} (' + ','.join(map(quote_identifier, columns)) +
                      ') VALUES (' + ','.join('?' for _ in columns) + ')')
            cursor = source_db.execute(query)
            count, replaced, body_bytes, reference_bytes = 0, 0, 0, 0
            digest = hashlib.sha256()
            while batch := cursor.fetchmany(batch_rows):
                encoded_rows = []
                for row in batch:
                    _update_digest(digest, row)
                    transformed = externalize_row(strategy, table, dict(zip(columns, row)), references=references)
                    encoded = tuple(transformed[column] for column in columns)
                    for before, after in zip(row, encoded):
                        ref = parse_reference(after)
                        if ref:
                            refs.add(ref[1])
                        if before != after:
                            replaced += 1
                            body_bytes += len(before.encode('utf-8') if isinstance(before, str) else before)
                            reference_bytes += len(after)
                    encoded_rows.append(encoded)
                target.executemany(insert, encoded_rows)
                target.commit()
                count += len(batch)
            manifest['tables'][table] = {
                'rows': count, 'logical_sha256': digest.hexdigest(),
                'externalized_cells': replaced, 'inline_original_bytes': body_bytes,
                'reference_bytes': reference_bytes,
            }
            if progress:
                progress(table, manifest['tables'][table])
        if include_levels:
            target.commit()
            target.execute('PRAGMA foreign_keys=ON')
            for table,rule in level_rules.items():
                if not any(row[0]=='table' and row[1]==table for row in schema):
                    continue
                columns,ordered_query=_select(source_db,table,include_rowid=False)
                digest=hashlib.sha256();count=0
                for row in source_db.execute(ordered_query):
                    _update_digest(digest,row);count+=1
                query=('SELECT '+','.join(map(quote_identifier,columns))+' FROM '+quote_identifier(table)+' ORDER BY snapshot_id,side,level_index')
                snapshot_index=columns.index('snapshot_id')
                for snapshot,group in groupby(source_db.execute(query),key=lambda row:row[snapshot_index]):
                    rows=[dict(zip(columns,row)) for row in group]
                    target.execute('BEGIN')
                    try:
                        if not insert_shared_levels(target,strategy,table,rows,references=references):
                            raise ValueError('public level migration did not handle classified table')
                        target.commit()
                    except BaseException:
                        target.rollback();raise
                manifest['tables'][table]={'rows':count,'logical_sha256':digest.hexdigest(),
                                           'externalized_level_rows':count,'key_basis':'declared primary key',
                                           'implicit_rowid_preserved':False}
                if progress:
                    progress(table,manifest['tables'][table])
            if validate_level_layout(target):
                for (reference,) in target.execute('SELECT body_ref FROM _market_data_level_groups'):
                    refs.add(parse_reference(reference)[1])
            # Restore original main-schema triggers after removing temporary
            # read aliases; otherwise SQLite resolves their target to the view.
            for table in level_rules:
                target.execute('DROP VIEW IF EXISTS temp.'+quote_identifier(table))
        sequence_exists = source_db.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone()
        if sequence_exists:
            target.execute('DELETE FROM sqlite_sequence')
            target.executemany('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)', source_db.execute('SELECT name,seq FROM sqlite_sequence'))
        for kind, _, _, sql in schema:
            if kind != 'table':
                target.execute(sql)
        target.commit()
        actual_schema = target.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        auxiliary=validate_level_layout(target)
        if schema != [row for row in actual_schema if row[1] not in auxiliary]:
            raise ValueError('derivative database schema changed')
        if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('derivative SQLite integrity check failed')
        source_fk = sorted(source_db.execute('PRAGMA foreign_key_check').fetchall(), key=repr)
        target_fk = sorted(target.execute('PRAGMA foreign_key_check').fetchall(), key=repr)
        if source_fk != target_fk:
            raise ValueError('derivative foreign-key evidence changed')
        target.close()
        target = None
        # A fresh resolver avoids proving a roundtrip only through the writer's cache.
        uncached = PayloadReferences(reader=references.reader, cache_bytes=0)
        decoded = resolving_connect(destination.as_uri() + '?mode=ro&immutable=1', uri=True, references=uncached)
        try:
            for table, expected in manifest['tables'].items():
                _, query = _select(source_db, table,include_rowid=table not in level_rules)
                digest = hashlib.sha256()
                count = 0
                for row in decoded.execute(query):
                    _update_digest(digest, row)
                    count += 1
                if count != expected['rows'] or digest.hexdigest() != expected['logical_sha256']:
                    raise ValueError(f'logical roundtrip mismatch: {table}')
        finally:
            decoded.close()
        if file_sha256(source) != source_sha256:
            raise ValueError('source changed during migration')
        manifest.update(status='VERIFIED', completed_at=datetime.now(timezone.utc).isoformat(),
                        destination_sha256=file_sha256(destination),
                        source_bytes=source.stat().st_size, destination_bytes=destination.stat().st_size,
                        shared_payload_count=len(refs), payload_hashes=sorted(refs),
                        source_foreign_key_issues=len(source_fk))
        sidecar.write_text(json.dumps(manifest, indent=2))
        return manifest
    except BaseException as exc:
        manifest.update(status='FAILED', error=type(exc).__name__ + ': ' + str(exc))
        sidecar.write_text(json.dumps(manifest, indent=2))
        raise
    finally:
        source_db.close()
        if target is not None:
            target.close()


def main(argv=None):
    import argparse
    from .market_data_client import StoreClient
    from .market_data_store import PayloadReader
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--source-sha256',required=True)
    parser.add_argument('--strategy',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--storage-root',type=Path,required=True)
    parser.add_argument('--socket',type=Path,required=True)
    parser.add_argument('--public-db',type=Path,required=True)
    parser.add_argument('--body-only',action='store_true')
    args=parser.parse_args(argv)
    root=args.storage_root.expanduser().absolute()
    if root.resolve(strict=True)!=root or not root.is_dir():
        raise ValueError('migration storage root must exist without symlinks')
    if len(root.parts)>2 and root.parts[1]=='Volumes':
        volume=Path(*root.parts[:3])
        if not volume.is_mount() or root.stat().st_dev!=volume.stat().st_dev:
            raise ValueError('migration external volume is absent')
    output=args.output.expanduser().absolute()
    if not output.is_relative_to(root) or output.resolve()!=output:
        raise ValueError('migration destination escapes storage root or crosses symlink')
    with StoreClient(args.socket) as writer,PayloadReader(args.public_db) as reader:
        result=migrate_public_bodies(args.source,output,strategy=args.strategy,
                                     source_sha256=args.source_sha256,
                                     references=PayloadReferences(reader=reader,writer=writer),
                                     include_levels=not args.body_only)
    print(json.dumps({key:value for key,value in result.items() if key!='payload_hashes'},indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
