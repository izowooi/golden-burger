"""Build and verify a derivative DB with public body/level references, without editing source.

The source must be an offline/checksummed pin. This module does not cut over active
jobs or move/delete originals. Scalar-level migration is explicit in the manifest.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from itertools import groupby, zip_longest
import os
from pathlib import Path
import sqlite3

from .market_data_refs import PayloadReferences, externalize_rows, parse_reference, value_reference_hashes
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


def _select(connection, table: str, *, include_rowid: bool = True, include_generated: bool = False):
    info = connection.execute(f'PRAGMA main.table_xinfo({quote_identifier(table)})').fetchall()
    columns = [r[1] for r in info if r[6] == 0 or (include_generated and r[6] in (2, 3))]
    primary = sorted((r[5], r[1]) for r in info if r[5])
    ddl = connection.execute('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone()[0]
    # SQLite identifiers are case-insensitive, including generated columns.
    # A declared _ROWID_ must not be mistaken for the hidden row identifier.
    declared_names = {row[1].casefold() for row in info}
    hidden_rowid = next((name for name in ('_rowid_', 'rowid', 'oid')
                         if name not in declared_names), None)
    if include_rowid and 'WITHOUT ROWID' not in ddl.upper() and hidden_rowid:
        columns.insert(0, hidden_rowid)
    # Explicit PK ordering is stable across rebuilds (including WITHOUT ROWID).
    order = ','.join(quote_identifier(name) for _, name in primary) if primary else 'rowid'
    sql = ('SELECT ' + ','.join(map(quote_identifier, columns)) +
           f' FROM {quote_identifier(table)} ORDER BY {order}')
    return columns, sql


def _raw_rows(connection, query, parameters=()):
    """Bypass a resolving cursor; ownership concerns physical SQLite cells."""
    cursor = sqlite3.Connection.cursor(connection)
    cursor.row_factory = None
    try:
        yield from cursor.execute(query, parameters)
    finally:
        cursor.close()


def _reference_family(value):
    if isinstance(value, str):
        prefixes = ("\x1ePMDATA", "\x1ePMMIX", "\x1ePMAPPLEFRAME", "\x1ePMPKT")
    elif isinstance(value, bytes):
        prefixes = (b"\x1ePMDATA", b"\x1ePMMIX", b"\x1ePMAPPLEFRAME", b"\x1ePMPKT")
    else:
        return None
    return next((kind for kind, prefix in zip(("body", "mixed", "apple-frame", "private-packet"), prefixes)
                 if value.startswith(prefix)), None)


def _legacy_public_roles(connection, strategy, table, row):
    if strategy != 'golden-apple' or table != 'payloads':
        return False
    from .market_data_apple import PUBLIC_LEGACY_ROLES, legacy_payload_roles
    roles = set(legacy_payload_roles(connection, [row.get('hash')])[row.get('hash')])
    return bool(roles) and roles <= PUBLIC_LEGACY_ROLES


def _validate_reference_cell(strategy, table, column, value, *, references=None,
                             public_legacy=False, level_body=False, connection=None, private_reader=None):
    family = _reference_family(value)
    if family == 'private-packet':
        if connection is None:
            raise ValueError('private packet ownership requires its original database')
        if private_reader is not None:
            if private_reader.connection is not connection:
                raise ValueError('private packet reader belongs to another database')
            value = private_reader.resolve(value, table=table, column=column)
        else:
            from .market_data_private_packets import resolve_private_packet
            value = resolve_private_packet(connection, value, strategy=strategy, table=table, column=column)
        family = _reference_family(value)
    if family is None:
        return
    from .market_data_policy import public_columns
    from .market_data_mixed import supported_profile_ids, _packet, verify_mixed_ownership
    allowed_body = column in public_columns(strategy, table) or level_body or (
        public_legacy and column == 'compressed'
    )
    if family == 'body' and allowed_body:
        parse_reference(value)  # Check version, checksum syntax and SQLite type, without I/O.
        if references is not None:
            references.decode_many([value])
        return
    mixed = supported_profile_ids(strategy, table, column)
    if family == 'mixed' and mixed:
        if _packet(value)['profile'] not in mixed:
            raise ValueError('mixed reference belongs to another ownership profile')
        if references is not None and not verify_mixed_ownership(
            strategy, table, column, value, value, references
        ):
            raise ValueError('mixed reference lacks approved ownership evidence')
        return
    if family == 'apple-frame' and (strategy, table, column) == ('golden-apple', 'runs', 'frame'):
        from .market_data_apple import frame_reference_hashes, resolve_frame
        frame_reference_hashes(value)  # Header/format proof needs no public-store access.
        if references is not None:
            resolve_frame(value, references)
        return
    raise ValueError('unapproved reference in private cell: ' + table + '.' + column)


def validate_private_reference_ownership(connection, strategy: str, *, references=None):
    """Reject unregistered physical references before public transfer or decode.

    With ``references=None`` this is a policy/header-only preflight and performs
    no public-store I/O. Passing a reader additionally proves mixed/frame content
    ownership. Registered public bodies, approved mixed envelopes and Apple's
    role-proven public legacy pool are the only exceptions to private locality.
    """
    from .market_data_levels import GROUP_TABLE, LAYOUT_TABLE as LEVEL_LAYOUT, validate_level_layout
    from .market_data_projection_links import (
        CONTEXT_TABLE, iter_projection_private_rows, projection_layout_metadata,
        validate_projection_layout,
    )
    from .market_data_projection_profiles import snapshot_profile
    from .market_data_catalog_links import (
        CONTEXT_TABLE as CATALOG_CONTEXT, catalog_layout_metadata,
        validate_catalog_layout, iter_catalog_private_rows,
    )
    from .market_data_catalog_profiles import catalog_profile
    from .market_data_private_packets import validate_private_packets, PrivatePacketReader
    packet_aux = validate_private_packets(connection, strategy=strategy)
    packet_reader = PrivatePacketReader(connection, strategy=strategy) if packet_aux else None
    catalog_aux = validate_catalog_layout(connection)
    if catalog_aux and catalog_layout_metadata(connection)['strategy'] != strategy:
        raise ValueError('private catalog ownership strategy differs')
    level_aux = validate_level_layout(connection)
    if level_aux and connection.execute(f'SELECT strategy FROM main.{LEVEL_LAYOUT}').fetchone()[0] != strategy:
        raise ValueError('public level ownership strategy differs')
    projection_aux = validate_projection_layout(connection)
    if projection_aux and projection_layout_metadata(connection)['strategy'] != strategy:
        raise ValueError('private projection ownership strategy differs')
    tables = [row[0] for row in _raw_rows(connection,
        "SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        if table in packet_aux:
            continue  # Validated local dictionary, never a public-payload table.
        columns = [row[1] for row in connection.execute(
            f'PRAGMA main.table_xinfo({quote_identifier(table)})') if row[6] != 1]
        ownership_table = ('market_snapshots' if projection_aux and table == CONTEXT_TABLE
                           else 'market_catalog' if catalog_aux and table == CATALOG_CONTEXT else table)
        # Query only candidate marker rows, rather than copying every large
        # inline gzip/frame into Python a second time. Chunk the predicate to
        # stay below SQLite expression-depth limits for unusually wide schemas.
        for offset in range(0, len(columns), 64):
            selected = columns[offset:offset + 64]
            where = ' OR '.join(f'substr(CAST({quote_identifier(name)} AS BLOB),1,3)=?' for name in selected)
            query = ('SELECT ' + ','.join(map(quote_identifier, columns))
                     + ' FROM main.' + quote_identifier(table) + ' WHERE ' + where)
            with closing(_raw_rows(connection, query, (b'\x1ePM',) * len(selected))) as rows:
                for values in rows:
                    row = dict(zip(columns, values, strict=True))
                    public_legacy = _legacy_public_roles(connection, strategy, table, row)
                    for column, value in row.items():
                        _validate_reference_cell(strategy, ownership_table, column, value,
                            references=references, public_legacy=public_legacy,
                            level_body=bool(level_aux) and table == GROUP_TABLE and column == 'body_ref',
                            connection=connection, private_reader=packet_reader)
    if projection_aux:
        profile = snapshot_profile(strategy)
        retained = tuple(name for name in profile.column_names if name in (
            set(profile.private_columns) | set(profile.body_columns)
            | {'condition_id', 'token_id', 'event_id', 'outcome'}
        ))
        with closing(iter_projection_private_rows(connection, strategy, retained)) as rows:
            for values in rows:
                for name, value in zip(retained, values, strict=True):
                    _validate_reference_cell(strategy, 'market_snapshots', name, value, references=references)

    if catalog_aux:
        profile = catalog_profile(strategy)
        retained = ('condition_id', *profile.private_columns)
        with closing(iter_catalog_private_rows(connection, strategy, retained)) as rows:
            for values in rows:
                for name, value in zip(retained, values, strict=True):
                    _validate_reference_cell(strategy, 'market_catalog', name, value, references=references)


def _verify_private_storage(source, target, strategy, schema, *, projection_tables,
                            scalar_tables, level_rules, references, catalog_tables=()):
    """Logical equality cannot authorize moving a private cell into public CAS."""
    from .market_data_policy import public_columns
    from .market_data_mixed import mixed_columns, is_mixed_payload, verify_mixed_ownership
    from .market_data_apple import resolve_frame, is_shared_frame
    from .market_data_projection_links import iter_projection_private_rows
    from .market_data_projection_profiles import snapshot_profile
    from .market_data_catalog_links import iter_catalog_private_rows, _main_info as catalog_main_info, _retained as catalog_retained

    validate_private_reference_ownership(target, strategy, references=references)
    missing = object()
    for kind, table, _, _ in schema:
        if kind != 'table' or table in scalar_tables or table in level_rules:
            continue
        if table in catalog_tables:
            catalog, info, indexes = catalog_main_info(source, strategy)
            columns = ('__rowid__', *catalog_retained(catalog, info, indexes))
            left = iter_catalog_private_rows(source, strategy, columns)
            right = iter_catalog_private_rows(target, strategy, columns)
        elif table in projection_tables:
            profile = snapshot_profile(strategy)
            retained = set(profile.private_columns) | set(profile.body_columns) | {
                'condition_id', 'token_id', 'event_id', 'outcome'}
            columns = tuple(name for name in profile.column_names if name in retained)
            left = iter_projection_private_rows(source, strategy, columns)
            right = iter_projection_private_rows(target, strategy, columns)
        else:
            columns, query = _select(source, table, include_generated=True)
            left, right = _raw_rows(source, query), _raw_rows(target, query)
        public = public_columns(strategy, table)
        mixed = mixed_columns(strategy, table)
        with closing(left), closing(right):
            for before, after in zip_longest(left, right, fillvalue=missing):
                if before is missing or after is missing:
                    raise ValueError('private physical row coverage changed: ' + table)
                original = dict(zip(columns, before, strict=True))
                public_legacy = _legacy_public_roles(source, strategy, table, original)
                for column, old, new in zip(columns, before, after, strict=True):
                    if column in public or (public_legacy and column == 'compressed'):
                        continue  # Final logical digest separately proves public byte equality.
                    if column in mixed and is_mixed_payload(new):
                        if verify_mixed_ownership(strategy, table, column, old, new, references):
                            continue
                    if (strategy, table, column) == ('golden-apple', 'runs', 'frame') and is_shared_frame(new):
                        if resolve_frame(old, references) != resolve_frame(new, references):
                            raise ValueError('Apple private frame bytes changed')
                        continue
                    if _cell_bytes(old) != _cell_bytes(new):
                        raise ValueError('private physical cell changed: ' + table + '.' + column)


def _validate_source_level_links(connection, rules) -> None:
    """Every auxiliary link must participate in a known restored source row."""
    from .market_data_levels import GROUP_TABLE, BINDING_TABLE
    tables = {row[0] for row in connection.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
    if connection.execute(
        f'SELECT 1 FROM main.{BINDING_TABLE} b LEFT JOIN main.{GROUP_TABLE} g ON g.id=b.group_id '
        'WHERE g.id IS NULL OR b.table_name!=g.table_name LIMIT 1'
    ).fetchone():
        raise ValueError('source public level binding has no matching group/table')
    for group_id, table, snapshot, decoded_body in connection.execute(
        f'SELECT id,table_name,snapshot_id,body_ref FROM main.{GROUP_TABLE}'
    ):
        if table not in rules or table not in tables:
            raise ValueError('source public level group has an unsupported table')
        levels = json.loads(decoded_body)
        if not isinstance(levels, list) or not levels:
            raise ValueError('source public level group has no represented numeric rows')
        ordinals = [row[0] for row in connection.execute(
            f'SELECT ordinal FROM main.{BINDING_TABLE} WHERE group_id=? ORDER BY ordinal', (group_id,)
        )]
        if ordinals != list(range(len(levels))):
            raise ValueError('source public level group has unrepresented or invalid ordinals')


def migrate_public_bodies(source: Path, destination: Path, *, strategy: str,
                          source_sha256: str, references: PayloadReferences,
                          batch_rows: int = 128, progress=None, include_levels: bool = False,
                          receipt_context=None, storage_guard=None, include_scalars: bool = False,
                          include_projections: bool = False, include_catalog: bool = False) -> dict:
    from .market_data_levels import insert_shared_levels, validate_level_layout
    from .market_data_policy import public_level_projections
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    if source == destination or destination.exists() or destination.is_symlink():
        raise ValueError('migration destination must be a new distinct file')
    if not 1 <= batch_rows <= 4096:
        raise ValueError('batch_rows out of range')
    if include_scalars and include_projections:
        raise ValueError('choose the source scalar or extended projection layout, not both')
    if references.reader is None or references.writer is None:
        raise ValueError('migration requires shared writer and reader')
    if any(Path(str(source) + suffix).exists() for suffix in ('-wal', '-journal')):
        raise ValueError('migration requires offline source without active journal/WAL')
    if file_sha256(source) != source_sha256:
        raise ValueError('source checksum mismatch')
    if storage_guard is not None:
        storage_guard()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation ensures an unrelated caller cannot be overwritten.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    source_db = source_raw = target = None
    refs: set[str] = set()
    level_rules = {rule.table:rule for rule in public_level_projections(strategy)} if include_levels else {}
    scalar_tables = set()
    scalar_namespace = None
    projection_tables = set()
    catalog_tables = set()
    catalog_namespace = None
    projection_namespace = None
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
        source_db = resolving_connect(source.as_uri() + '?mode=ro&immutable=1', uri=True,
                                      references=PayloadReferences(reader=references.reader))
        source_raw = sqlite3.connect(source.as_uri() + '?mode=ro&immutable=1', uri=True)
        if receipt_context is not None:
            from dataclasses import replace
            # Join metadata only from this exact immutable source. The migration
            # process's environment is not provenance for historical receipts.
            receipt_context = replace(receipt_context, connection=source_db, gap_counts={}, indexed_count=0)
        target = sqlite3.connect(destination, uri=True)
        source_db.execute('PRAGMA query_only=ON')
        # Read-only source validation and final row-level verification complement
        # the file checksum; a matching hash alone proves no logical integrity.
        if source_db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('source SQLite quick_check failed')
        validate_private_reference_ownership(source_raw, strategy, references=references)
        schema = source_db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        source_auxiliary = validate_level_layout(source_db)
        if source_auxiliary:
            from .market_data_levels import LAYOUT_TABLE
            source_strategy = source_db.execute(
                f'SELECT strategy FROM main.{LAYOUT_TABLE}'
            ).fetchone()[0]
            if source_strategy != strategy or not include_levels:
                raise ValueError('existing public levels require matching strategy and level migration')
            _validate_source_level_links(source_db, level_rules)
            # Resolve both old inline rows and current shared rows through the
            # source view, then rebuild one set of links. Copying auxiliary IDs
            # as ordinary tables would duplicate the numeric price ladders.
            schema = [row for row in schema if row[1] not in source_auxiliary]
            manifest['source_level_layout'] = 'public-level-links-v1'
        from .market_data_scalar_links import (
            STRATEGIES as SCALAR_STRATEGIES, validate_scalar_layout,
            scalar_layout_metadata, scalar_namespace as make_scalar_namespace,
            initialize_scalar_links, LINK_TABLE,
        )
        source_scalar_aux = validate_scalar_layout(source_db)
        if source_scalar_aux and not include_scalars:
            raise ValueError('existing scalar links require explicit scalar migration')
        if include_scalars:
            if strategy not in SCALAR_STRATEGIES:
                raise ValueError('scalar migration requires a reviewed source strategy')
            if any(kind=='table' and table=='market_snapshots' for kind,table,_,_ in schema):
                from .market_data_scalar_links import _main_schema
                _main_schema(source_db)
                scalar_tables.add('market_snapshots')
                if source_scalar_aux:
                    from .market_data_scalar_closure import verify_scalar_closure
                    verify_scalar_closure(references.reader,source,strategy)
                    scalar_namespace=scalar_layout_metadata(source_db)['namespace']
                    if receipt_context is not None and scalar_namespace != make_scalar_namespace(
                        receipt_context.source, receipt_context.job_name, strategy, receipt_context.runtime
                    ):
                        raise ValueError('scalar migration receipt context differs from the existing source owner')
                else:
                    if receipt_context is None or not receipt_context.job_name:
                        raise ValueError('scalar migration requires explicit source/Jenkins job/runtime')
                    scalar_namespace=make_scalar_namespace(receipt_context.source,receipt_context.job_name,
                                                           strategy,receipt_context.runtime)
                schema=[row for row in schema if row[1] not in source_scalar_aux]
        elif strategy in SCALAR_STRATEGIES and any(kind=='table' and table=='market_snapshots' for kind,table,_,_ in schema):
            manifest['remaining_public_scalar_rows']=source_db.execute('SELECT COUNT(*) FROM market_snapshots').fetchone()[0]
        from .market_data_projection_links import (
            validate_projection_layout, projection_layout_metadata, initialize_projection_links,
            insert_shared_projection_snapshot,
        )
        from .market_data_projection_profiles import snapshot_profile, SNAPSHOT_PROFILES
        source_projection_aux = validate_projection_layout(source_db)
        if source_projection_aux and not include_projections:
            raise ValueError('existing public projection links require explicit projection migration')
        has_snapshots = any(kind=='table' and table=='market_snapshots' for kind,table,_,_ in schema)
        if include_projections:
            profile = snapshot_profile(strategy)
            if has_snapshots:
                from .market_data_projection_links import _main_info
                _main_info(source_db, strategy)
                projection_tables.add('market_snapshots')
                if source_projection_aux:
                    from .market_data_projection_closure import verify_projection_closure
                    verify_projection_closure(references.reader, source, strategy)
                    projection_namespace = projection_layout_metadata(source_db)['namespace']
                    if receipt_context is not None and projection_namespace != make_scalar_namespace(
                        receipt_context.source, receipt_context.job_name, strategy, receipt_context.runtime
                    ):
                        raise ValueError('projection migration receipt context differs from the existing source owner')
                else:
                    if receipt_context is None or not receipt_context.job_name:
                        raise ValueError('projection migration requires explicit source/Jenkins job/runtime')
                    projection_namespace = make_scalar_namespace(receipt_context.source, receipt_context.job_name,
                                                                 strategy, receipt_context.runtime)
                schema = [row for row in schema if row[1] not in source_projection_aux]
                manifest['scope'] += ' Reviewed immutable public groups are separated from private snapshot context.'
        elif strategy in SNAPSHOT_PROFILES and has_snapshots:
            manifest['remaining_public_projection_rows'] = source_db.execute('SELECT COUNT(*) FROM market_snapshots').fetchone()[0]
        from .market_data_catalog_links import (
            validate_catalog_layout, catalog_layout_metadata, initialize_catalog_links,
            upsert_shared_catalog, iter_catalog_rows,
        )
        from .market_data_catalog_profiles import CATALOG_PROFILES, catalog_profile
        source_catalog_aux = validate_catalog_layout(source_db)
        if source_catalog_aux and not include_catalog:
            raise ValueError('existing public catalog links require explicit catalog migration')
        has_catalog = any(kind == 'table' and table == 'market_catalog' for kind, table, _, _ in schema)
        if include_catalog and has_catalog:
            from .market_data_catalog_links import _main_info as catalog_main_info
            catalog_profile(strategy)  # Unknown families never inherit a generic schema.
            catalog_main_info(source_db, strategy)
            catalog_tables.add('market_catalog')
            if source_catalog_aux:
                from .market_data_projection_closure import verify_projection_closure
                verify_projection_closure(references.reader, source, strategy)
                catalog_namespace = catalog_layout_metadata(source_db)['namespace']
                if receipt_context is not None and catalog_namespace != make_scalar_namespace(
                    receipt_context.source, receipt_context.job_name, strategy, receipt_context.runtime
                ):
                    raise ValueError('catalog migration receipt context differs from the existing source owner')
            else:
                if receipt_context is None or not receipt_context.job_name:
                    raise ValueError('catalog migration requires explicit source/Jenkins job/runtime')
                catalog_namespace = make_scalar_namespace(receipt_context.source, receipt_context.job_name,
                                                          strategy, receipt_context.runtime)
            if projection_namespace is not None and projection_namespace != catalog_namespace:
                raise ValueError('catalog and snapshot projection source owners differ')
            projection_namespace = catalog_namespace
            schema = [row for row in schema if row[1] not in source_catalog_aux]
            manifest['scope'] += (' Reviewed catalog identity/source-state groups are separated from private context; '
                                  'real SQLite rowids are preserved through the explicit catalog iterator, not view.rowid.')
        elif strategy in CATALOG_PROFILES and has_catalog:
            manifest['remaining_public_catalog_rows'] = source_db.execute('SELECT COUNT(*) FROM market_catalog').fetchone()[0]
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
        # Catalog context reproduces the original indexed lookup columns. Build
        # those source indexes before deriving its auxiliary schema, and avoid
        # creating them again after all data have been restored.
        early_indexes = set()
        for kind, name, table, sql in schema:
            if kind == 'index' and table in catalog_tables:
                target.execute(sql)
                early_indexes.add(name)
        target.commit()
        for kind, table, _, _ in schema:
            if kind != 'table':
                continue
            if table in level_rules or table in scalar_tables or table in projection_tables or table in catalog_tables:
                continue
            columns, query = _select(source_db, table)
            insert = (f'INSERT INTO {quote_identifier(table)} (' + ','.join(map(quote_identifier, columns)) +
                      ') VALUES (' + ','.join('?' for _ in columns) + ')')
            cursor = source_db.execute(query)
            count, replaced, body_bytes, reference_bytes = 0, 0, 0, 0
            digest = hashlib.sha256()
            while batch := cursor.fetchmany(batch_rows):
                if storage_guard is not None:
                    storage_guard()
                encoded_rows = []
                original_rows = [dict(zip(columns,row)) for row in batch]
                options = {'references': references}
                if receipt_context is not None:
                    options['receipt_context'] = receipt_context
                if strategy == 'golden-apple' and table == 'payloads':
                    from .market_data_apple import legacy_payload_roles, externalize_legacy_payload
                    if not {'hash','encoding','bytes','compressed'} <= set(columns):
                        raise ValueError('unsupported Apple legacy public/private payload pool schema')
                    roles = legacy_payload_roles(source_db, [row['hash'] for row in original_rows])
                    transformed_batch = [externalize_legacy_payload(row, roles[row['hash']], references)
                                         for row in original_rows]
                else:
                    transformed_batch=externalize_rows(strategy,table,original_rows,**options)
                for row,transformed in zip(batch,transformed_batch):
                    _update_digest(digest, row)
                    encoded = tuple(transformed[column] for column in columns)
                    for before, after in zip(row, encoded):
                        refs.update(value_reference_hashes(after))
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
        if scalar_tables:
            from .market_data_scalars import ScalarSnapshot, ScalarReceipt
            authority = references.reader.scalar_authority_identity()
            target.execute('BEGIN')
            initialize_scalar_links(target,strategy,scalar_namespace,authority)
            target.commit()
            columns,query = _select(source_db,'market_snapshots',include_rowid=False)
            digest=hashlib.sha256()
            count=0
            cursor=source_db.execute(query)
            while batch:=cursor.fetchmany(min(batch_rows,1024)):
                if storage_guard is not None:
                    storage_guard()
                rows=[dict(zip(columns,row)) for row in batch]
                snapshots=[ScalarSnapshot(row['condition_id'],row['probability'],row['liquidity'],
                                          row['volume_24h'],row['timestamp']) for row in rows]
                acknowledgements=references.writer.put_scalar_snapshots(snapshots)
                if (len(acknowledgements)!=len(rows) or any(
                        ref.authority_uuid!=authority or ref.sha256!=snapshot.sha256
                        for ref,snapshot in zip(acknowledgements,snapshots,strict=True))):
                    raise ValueError('scalar migration acknowledgement differs from source values')
                record_ids=[ref.record_id for ref in acknowledgements]
                restored=references.reader.get_scalar_records(record_ids,authority_uuid=authority)
                if [record.snapshot for record in restored]!=snapshots:
                    raise ValueError('scalar migration public readback mismatch')
                receipts=[ScalarReceipt(scalar_namespace,row['id'],ref.record_id)
                          for row,ref in zip(rows,acknowledgements,strict=True)]
                references.writer.append_scalar_receipts(receipts,authority_uuid=authority)
                target.executemany(f'INSERT INTO {LINK_TABLE}(id,record_id) VALUES(?,?)',
                    [(row['id'],ref.record_id) for row,ref in zip(rows,acknowledgements,strict=True)])
                target.commit()
                for row in batch:
                    _update_digest(digest,row)
                count+=len(batch)
            manifest['tables']['market_snapshots']={'rows':count,'logical_sha256':digest.hexdigest(),
                'externalized_scalar_rows':count,'key_basis':'declared INTEGER primary key',
                'implicit_rowid_preserved':False}
            manifest['scalar_namespace']=scalar_namespace
            manifest['scalar_authority_uuid']=authority
            if progress:
                progress('market_snapshots',manifest['tables']['market_snapshots'])
        if projection_tables:
            authority = references.reader.scalar_authority_identity()
            target.execute('BEGIN')
            initialize_projection_links(target, strategy, projection_namespace, authority)
            target.commit()
            columns, query = _select(source_db, 'market_snapshots', include_rowid=False)
            digest = hashlib.sha256()
            count = 0
            cursor = source_db.execute(query)
            while batch := cursor.fetchmany(min(batch_rows, 1024)):
                if storage_guard is not None:
                    storage_guard()
                rows = [dict(zip(columns, row)) for row in batch]
                # Bodies retained in private context keep their exact CAS refs.
                # Catalog JSON now in a public group is not also copied to a
                # redundant standalone body merely because an old policy did so.
                bodies = [{name: row[name] for name in profile.body_columns} for row in rows]
                encoded = externalize_rows(strategy, 'market_snapshots', bodies, references=references)
                target.execute('BEGIN')
                try:
                    for original, row, public_bodies in zip(batch, rows, encoded, strict=True):
                        for value in public_bodies.values():
                            reference = parse_reference(value)
                            if reference is not None:
                                refs.add(reference[1])
                        insert_shared_projection_snapshot(target, strategy, {**row, **public_bodies},
                                                          namespace=projection_namespace, references=references)
                        _update_digest(digest, original)
                        count += 1
                    target.commit()
                except BaseException:
                    target.rollback()
                    raise
            manifest['tables']['market_snapshots'] = {
                'rows': count, 'logical_sha256': digest.hexdigest(), 'externalized_projection_rows': count,
                'key_basis': 'declared INTEGER primary key', 'implicit_rowid_preserved': False,
            }
            manifest['projection_namespace'] = projection_namespace
            manifest['projection_authority_uuid'] = authority
            target.execute('DROP VIEW IF EXISTS temp.market_snapshots')
            if progress:
                progress('market_snapshots', manifest['tables']['market_snapshots'])
        if catalog_tables:
            authority = references.reader.scalar_authority_identity()
            target.execute('BEGIN')
            initialize_catalog_links(target, strategy, catalog_namespace, authority)
            target.commit()
            columns = [row[1] for row in source_db.execute('PRAGMA main.table_xinfo(market_catalog)')]
            digest = hashlib.sha256()
            count = 0
            from itertools import islice
            with closing(iter_catalog_rows(source_db, strategy, include_rowid=True)) as rows:
                while batch := list(islice(rows, min(batch_rows, 1024))):
                    if storage_guard is not None:
                        storage_guard()
                    target.execute('BEGIN')
                    try:
                        for original in batch:
                            rowid, *cells = original
                            upsert_shared_catalog(target, strategy, dict(zip(columns, cells, strict=True)),
                                namespace=catalog_namespace, references=references,
                                original_rowid=rowid, insert_only=True)
                            _update_digest(digest, original)
                            count += 1
                        target.commit()
                    except BaseException:
                        target.rollback()
                        raise
            manifest['tables']['market_catalog'] = {
                'rows': count, 'logical_sha256': digest.hexdigest(), 'externalized_catalog_rows': count,
                'key_basis': 'declared-primary-key+rowid-v1', 'implicit_rowid_preserved': True,
            }
            manifest['projection_namespace'] = catalog_namespace
            manifest['projection_authority_uuid'] = authority
            target.execute('DROP VIEW IF EXISTS temp.market_catalog')
            if progress:
                progress('market_catalog', manifest['tables']['market_catalog'])
        if include_levels:
            target.commit()
            target.execute('PRAGMA foreign_keys=ON')
            for table,rule in level_rules.items():
                if not any(row[0]=='table' and row[1]==table for row in schema):
                    continue
                columns,ordered_query=_select(source_db,table,include_rowid=False)
                digest=hashlib.sha256()
                count=0
                for row in source_db.execute(ordered_query):
                    _update_digest(digest,row)
                    count+=1
                query=('SELECT '+','.join(map(quote_identifier,columns))+' FROM '+quote_identifier(table)+' ORDER BY snapshot_id,side,level_index')
                snapshot_index=columns.index('snapshot_id')
                for snapshot,group in groupby(source_db.execute(query),key=lambda row:row[snapshot_index]):
                    if storage_guard is not None:
                        storage_guard()
                    rows=[dict(zip(columns,row)) for row in group]
                    target.execute('BEGIN')
                    try:
                        if not insert_shared_levels(target,strategy,table,rows,references=references):
                            raise ValueError('public level migration did not handle classified table')
                        target.commit()
                    except BaseException:
                        target.rollback()
                        raise
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
        for kind, name, _, sql in schema:
            if kind != 'table' and name not in early_indexes:
                target.execute(sql)
        target.commit()
        actual_schema = target.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        auxiliary=validate_level_layout(target)|validate_scalar_layout(target)|validate_projection_layout(target)|validate_catalog_layout(target)
        if schema != [row for row in actual_schema if row[1] not in auxiliary]:
            raise ValueError('derivative database schema changed')
        if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('derivative SQLite integrity check failed')
        source_fk = sorted(source_db.execute('PRAGMA foreign_key_check').fetchall(), key=repr)
        target_fk = sorted(target.execute('PRAGMA foreign_key_check').fetchall(), key=repr)
        if source_fk != target_fk:
            raise ValueError('derivative foreign-key evidence changed')
        _verify_private_storage(source_raw, target, strategy, schema,
                                projection_tables=projection_tables, scalar_tables=scalar_tables,
                                level_rules=level_rules, references=references, catalog_tables=catalog_tables)
        target.close()
        target = None
        # A fresh resolver avoids proving a roundtrip only through the writer's cache.
        uncached = PayloadReferences(reader=references.reader, cache_bytes=0)
        decoded = resolving_connect(destination.as_uri() + '?mode=ro&immutable=1', uri=True, references=uncached)
        try:
            for table, expected in manifest['tables'].items():
                _, query = _select(source_db, table,include_rowid=table not in level_rules and table not in scalar_tables and table not in projection_tables)
                digest = hashlib.sha256()
                count = 0
                logical_rows = iter_catalog_rows(decoded, strategy, include_rowid=True) if table in catalog_tables else decoded.execute(query)
                for row in logical_rows:
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
        if receipt_context is not None:
            manifest['public_receipt_index'] = {
                'source': receipt_context.source, 'runtime': receipt_context.runtime,
                'jenkins_job': receipt_context.job_name,
                'gap_counts': dict(receipt_context.gap_counts),
                'indexed_count': receipt_context.indexed_count,
                'scope': 'Explicit source receipt rules; not cycle publication or trade confirmation.',
            }
        if scalar_tables:
            from .market_data_scalar_closure import verify_scalar_closure
            manifest['public_scalar_records']=verify_scalar_closure(references.reader,destination,strategy)
        if projection_tables or catalog_tables:
            from .market_data_projection_closure import verify_projection_closure
            manifest['public_projection_records'] = verify_projection_closure(references.reader, destination, strategy)
        sidecar.write_text(json.dumps(manifest, indent=2))
        return manifest
    except BaseException as exc:
        manifest.update(status='FAILED', error=type(exc).__name__ + ': ' + str(exc))
        sidecar.write_text(json.dumps(manifest, indent=2))
        raise
    finally:
        if source_raw is not None:
            source_raw.close()
        if source_db is not None:
            source_db.close()
        if target is not None:
            target.close()


def migration_storage_guard(root: Path, *, minimum_free_bytes: int,
                            maximum_used_ratio: float):
    """Check the same mounted root throughout a potentially long migration."""
    if type(minimum_free_bytes) is not int or minimum_free_bytes < 0:
        raise ValueError('migration free-space floor must be nonnegative')
    if not 0 < maximum_used_ratio <= 1:
        raise ValueError('migration maximum used ratio must be in (0,1]')
    root=Path(root).absolute()
    if root.resolve(strict=True)!=root or not root.is_dir():
        raise ValueError('migration storage root must exist without symlinks')
    identity=(root.stat().st_dev,root.stat().st_ino)
    volume=Path(*root.parts[:3]) if len(root.parts)>2 and root.parts[1]=='Volumes' else None
    def check():
        if (root.resolve(strict=True)!=root or (root.stat().st_dev,root.stat().st_ino)!=identity
                or (volume is not None and (not volume.is_mount() or root.stat().st_dev!=volume.stat().st_dev))):
            raise RuntimeError('migration storage root identity changed or volume is absent')
        space=os.statvfs(root)
        total=space.f_blocks*space.f_frsize
        free=space.f_bavail*space.f_frsize
        used=(space.f_blocks-space.f_bfree)*space.f_frsize
        if total<=0 or free<minimum_free_bytes or used/total>maximum_used_ratio:
            raise RuntimeError('migration storage capacity gate blocked further writes')
    check()
    return check


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
    parser.add_argument('--include-scalars',action='store_true',help='Migrate reviewed numeric snapshots with explicit source identity')
    parser.add_argument('--include-projections',action='store_true',help='Migrate reviewed extended snapshots, preserving private context')
    parser.add_argument('--include-catalog',action='store_true',help='Migrate reviewed public catalog groups with real rowid and private context preserved')
    parser.add_argument('--source-identity', help='Recorded physical source; never inferred from environment')
    parser.add_argument('--runtime', help='Exact source database runtime for public receipt indexing')
    parser.add_argument('--jenkins-job', help='Source Jenkins job provenance (optional)')
    parser.add_argument('--min-free-gib',type=float,default=50)
    parser.add_argument('--max-used-ratio',type=float,default=.90)
    args=parser.parse_args(argv)
    if bool(args.source_identity) != bool(args.runtime) or (args.jenkins_job and not args.runtime):
        parser.error('--source-identity and --runtime must be supplied together for receipt indexing')
    receipt_context = None
    if args.source_identity:
        from .market_data_index import ReceiptContext
        receipt_context = ReceiptContext(args.source_identity, args.runtime, args.jenkins_job)
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
    guard=migration_storage_guard(root,minimum_free_bytes=int(args.min_free_gib*(1<<30)),
                                  maximum_used_ratio=args.max_used_ratio)
    with StoreClient(args.socket) as writer,PayloadReader(args.public_db) as reader:
        required = {"public-payload-cas-v1"}
        if receipt_context:
            required.add("public-observation-subjects-v2")
        if args.include_scalars:
            required.add("public-scalar-block-store-v1")
        if args.include_projections or args.include_catalog:
            required.add("public-projection-block-store-v1")
        if args.include_catalog:
            required.add("public-catalog-groups-v1")
        writer.require_capabilities(required)
        result=migrate_public_bodies(args.source,output,strategy=args.strategy,
                                     source_sha256=args.source_sha256,
                                     references=PayloadReferences(reader=reader,writer=writer),
                                     include_levels=not args.body_only, receipt_context=receipt_context,
                                     storage_guard=guard, include_scalars=args.include_scalars,
                                     include_projections=args.include_projections, include_catalog=args.include_catalog)
    print(json.dumps({key:value for key,value in result.items() if key!='payload_hashes'},indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
