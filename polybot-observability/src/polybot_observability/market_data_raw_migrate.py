"""Verified offline Golden Black derivative; no source mutation or automatic cutover."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
from itertools import groupby, islice, zip_longest
import json
import os
from pathlib import Path
import sqlite3
import time

from .market_data_raw_profiles import RAW_TABLES, STRATEGY, BLACK_PROFILE_ID, GUAVA_PROFILE_ID, COCONUT_RECORDER_PROFILE_ID, raw_profile, profile_for_connection
from .market_data_raw_links import (
    LAYOUT_TABLE, _q, _sql, create_raw_layout, skeleton_sql, validate_raw_source_schema,
    validate_raw_layout, raw_layout_metadata, iter_raw_logical_rows, insert_raw_rows,
    verify_raw_dependencies, INDEX_SQL, immutable_sql, require_raw_capabilities,
    validate_raw_namespace_authority,
)
from .market_data_refs import externalize_rows, PayloadReferences
from .market_data_sqlite import connect, expand_private_values
from .market_data_private_packets import (validate_private_packets, resolve_private_packet, is_private_packet)
from .market_data_migrate import (
    file_sha256, validate_private_reference_ownership, _raw_rows, _select,
    _cell_bytes, _update_digest,
)

CONTRACT = 'black-raw-parent-derivative-v1'
PROFILE_DERIVATIVE_CONTRACT = 'shared-raw-parent-derivative-v2'
DIGEST_CONTRACT = 'raw-typed-scalar-cell-length-prefix-v1'


def _objects(connection):
    return [tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM main.sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name")]


def validate_raw_source_objects(connection, *, profile_id=None):
    """Persistent SQL binds main parents, not their restored TEMP read views."""
    from .market_data_levels import validate_level_layout
    packet_auxiliary = (validate_private_packets(connection) | validate_level_layout(connection)
                        | validate_raw_layout(connection, profile_id=profile_id))
    schema=profile_for_connection(connection,profile_id=profile_id)
    reviewed={name:_sql(sql) for kind,name,_,sql in schema.source_objects if kind == 'trigger'}
    for table in schema.tables:reviewed.update({name:_sql(sql) for name,sql in schema.triggers_for(table).items()})
    for operation in ('UPDATE','DELETE'):
        reviewed[LAYOUT_TABLE+'_forbid_'+operation.lower()]=_sql(immutable_sql(LAYOUT_TABLE,operation))
    for kind,name,table,sql in _objects(connection):
        if name in packet_auxiliary:continue
        if kind == 'view':
            raise ValueError('RAW migration does not support persistent source views')
        if 'VIRTUAL TABLE' in sql.upper():
            raise ValueError('RAW migration does not support virtual source tables')
        if kind == 'trigger' and reviewed.get(name) != _sql(sql) and not (not schema.source_objects and any(name == table+'_forbid_'+operation.lower()
                and _sql(sql) == _sql(immutable_sql(table,operation))
                for operation in ('UPDATE','DELETE'))):
            raise ValueError('RAW migration has an unreviewed persistent trigger: '+name)


def raw_schema_evidence(connection):
    objects = _objects(connection)
    digest = hashlib.sha256()
    for row in objects:
        _update_digest(digest, row)
    return dict(sha256=digest.hexdigest(), **{key: connection.execute('PRAGMA '+key).fetchone()[0]
        for key in ('encoding', 'application_id', 'user_version')})


def verify_raw_schema_transition(source, target, *, profile_id=None):
    """Prove the three reviewed substitutions; all other user schema stays exact."""
    profile = profile_for_connection(target,profile_id=profile_id)
    validate_raw_source_schema(source,profile_id=profile.profile_id)
    validate_raw_source_objects(source,profile_id=profile.profile_id)
    from .market_data_levels import validate_level_layout
    auxiliary = validate_raw_layout(target,profile_id=profile.profile_id)
    if not auxiliary:
        raise ValueError('RAW target layout is absent')
    auxiliary |= validate_private_packets(target,strategy=profile.strategy) | validate_level_layout(target)
    source_auxiliary = validate_raw_layout(source,profile_id=profile.profile_id) | validate_private_packets(source,strategy=profile.strategy) | validate_level_layout(source)
    expected = []
    for kind, name, table, sql in _objects(source):
        if name in source_auxiliary:
            continue
        expected.append((kind, name, table, skeleton_sql(name,profile.profile_id) if kind == 'table' and name in profile.tables else sql))
    actual = [row for row in _objects(target) if row[1] not in auxiliary]
    if expected != actual:
        raise ValueError('RAW derivative schema changed outside reviewed parent transition')
    validate_raw_source_schema(target,profile_id=profile.profile_id)
    before, after = raw_schema_evidence(source), raw_schema_evidence(target)
    for key in ('encoding', 'application_id', 'user_version'):
        if before[key] != after[key]:
            raise ValueError('RAW derivative database pragma changed: '+key)
    for connection in (source, target):
        if connection.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('RAW derivative/source has foreign key violations')
    def sequence(connection):
        if connection.execute("SELECT 1 FROM main.sqlite_master WHERE name='sqlite_sequence'").fetchone():
            return tuple(connection.execute('SELECT name,seq FROM main.sqlite_sequence ORDER BY name'))
        return ()
    if sequence(source) != sequence(target):
        raise ValueError('RAW derivative autoincrement sequence changed')
    return {'source': before, 'target': after}


def _private_columns_query(connection, table, *, profile_id=None):
    from .market_data_policy import public_columns
    from .market_data_mixed import mixed_columns
    schema = profile_for_connection(connection,profile_id=profile_id)
    if table in schema.tables:
        profile = schema.tables[table]
        # The original rowid is receipt identity, not a public source value.
        retained = profile.private_columns
        columns = ('__rowid__', *retained)
        query = 'SELECT rowid,'+','.join(map(_q,retained))+' FROM main.'+_q(table)+' ORDER BY rowid'
    else:
        columns, query = _select(connection, table, include_generated=True)
        columns = tuple(columns)
        query = query.replace(' FROM '+_q(table), ' FROM main.'+_q(table), 1)
    public, mixed = set(public_columns(schema.strategy, table)), set(mixed_columns(schema.strategy, table))
    included = tuple(name for name in columns if name not in public | mixed)
    return columns, query, included, mixed


def raw_private_fingerprint(connection, table, *, profile_id=None, references=None):
    """Hash physical private cells; mixed templates are separately proven lexically."""
    schema=profile_for_connection(connection,profile_id=profile_id)
    if table in schema.level_tables:
        from .market_data_levels import iter_level_logical_rows, _rule
        retained=('__rowid__','level_id','snapshot_id',*_rule(schema.strategy,table).retained_columns)
        digest,count=hashlib.sha256(),0
        for row in iter_level_logical_rows(connection,schema.strategy,table,require_rowid=True,references=references):
            _update_digest(digest,tuple(row[key] for key in retained));count+=1
        return {'rows':count,'columns':list(retained),'sha256':digest.hexdigest()}
    columns, query, retained, _ = _private_columns_query(connection, table,profile_id=profile_id)
    indices = tuple(columns.index(name) for name in retained)
    digest, count = hashlib.sha256(), 0
    for row in _raw_rows(connection, query):
        _update_digest(digest, tuple(row[index] for index in indices))
        count += 1
    return {'rows': count, 'columns': list(retained), 'sha256': digest.hexdigest()}


def verify_raw_private_storage(source, target, references, *, profile_id=None):
    """Logical equality alone cannot authorize publishing a private cell."""
    from .market_data_mixed import is_mixed_payload, verify_mixed_ownership
    schema = profile_for_connection(target,profile_id=profile_id)
    for connection in (source, target):
        validate_private_reference_ownership(connection, schema.strategy, references=references)
    from .market_data_levels import validate_level_layout
    source_auxiliary = validate_raw_layout(source,profile_id=schema.profile_id) | validate_private_packets(source,strategy=schema.strategy) | validate_level_layout(source)
    tables = [name for kind,name,_,_ in _objects(source) if kind == 'table' and name not in source_auxiliary]
    result, missing = {}, object()
    for table in tables:
        before = raw_private_fingerprint(source, table,profile_id=schema.profile_id,references=references)
        after = raw_private_fingerprint(target, table,profile_id=schema.profile_id,references=references)
        if before != after:
            raise ValueError('RAW private physical cells changed: '+table)
        old_columns, old_query, _, mixed = _private_columns_query(source, table,profile_id=schema.profile_id)
        new_columns, new_query, _, _ = _private_columns_query(target, table,profile_id=schema.profile_id)
        mixed_count = 0
        if mixed:
            with closing(_raw_rows(source, old_query)) as left, closing(_raw_rows(target,new_query)) as right:
                for old, new in zip_longest(left,right,fillvalue=missing):
                    if old is missing or new is missing:
                        raise ValueError('RAW mixed private row coverage changed: '+table)
                    for column in mixed & set(old_columns):
                        old_value, new_value = old[old_columns.index(column)], new[new_columns.index(column)]
                        if is_private_packet(old_value):old_value=resolve_private_packet(source,old_value,strategy=schema.strategy,table=table,column=column)
                        if is_private_packet(new_value):new_value=resolve_private_packet(target,new_value,strategy=schema.strategy,table=table,column=column)
                        if is_mixed_payload(new_value):
                            if not verify_mixed_ownership(schema.strategy, table, column, old_value, new_value, references):
                                raise ValueError('RAW mixed private ownership changed: '+table+'.'+column)
                            mixed_count += 1
                        elif _cell_bytes(old_value) != _cell_bytes(new_value):
                            raise ValueError('RAW private mixed cell changed without a reviewed envelope')
        result[table] = {'rows': before['rows'], 'columns': before['columns'],
            'source_sha256': before['sha256'], 'target_sha256': after['sha256'],
            'mixed_cells_verified': mixed_count}
    return result


def _logical_digest(connection, table, references, *, profile_id=None):
    digest = hashlib.sha256()
    schema = profile_for_connection(connection,profile_id=profile_id)
    if table in schema.level_tables:
        from .market_data_levels import iter_level_logical_rows
        names=('__rowid__',*(row[1] for row in connection.execute('PRAGMA main.table_info('+_q(table)+')')))
        rows=(tuple(row[key] for key in names) for row in iter_level_logical_rows(connection,schema.strategy,table,references=references,require_rowid=True))
    elif table in schema.tables:
        names = ('__rowid__', *schema.tables[table].columns)
        rows = (tuple(row[name] for name in names) for row in iter_raw_logical_rows(connection, table, references=references,profile_id=schema.profile_id))
    else:
        _, query = _select(connection, table, include_generated=True)
        query = query.replace(' FROM '+_q(table), ' FROM main.'+_q(table), 1)
        rows = connection.execute(query)
    from .market_data_scalars import scalar_cell_bytes
    count = 0
    for row in rows:
        count += 1
        for value in references.decode_many(expand_private_values(connection,row)):
            body = b'B' + len(value).to_bytes(8,'big') + value if type(value) is bytes else scalar_cell_bytes(value)
            digest.update(len(body).to_bytes(8,'big')); digest.update(body)
    return count, digest.hexdigest()


def _dependency_order(connection, tables):
    remaining = {table: {row[2] for row in connection.execute('PRAGMA main.foreign_key_list('+_q(table)+')')}
                 for table in tables}
    result = []
    while remaining:
        ready = [table for table, parents in remaining.items() if parents <= set(result)]
        if not ready:
            raise ValueError('RAW migration needs an acyclic, local foreign key graph')
        for table in ready:
            result.append(table); del remaining[table]
    return result


def _private_copy_select(connection, table):
    """Copy ordinary private rowid tables in B-tree order, not canonical PK order.

    _select remains the unchanged digest/fingerprint contract. Be conservative
    about declared rowid aliases (including generated and differently cased
    columns); their existing canonical copy path is retained.
    """
    names, query = _select(connection, table)
    info = tuple(connection.execute('PRAGMA main.table_xinfo('+_q(table)+')'))
    declared = {row[1].casefold() for row in info}
    aliases = {'_rowid_', 'rowid', 'oid'}
    ddl = connection.execute(
        "SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    hidden_selected = (len(names) == sum(row[6] == 0 for row in info) + 1
        and names[0].casefold() in aliases and not (declared & aliases)
        and 'WITHOUT ROWID' not in ddl.upper())
    if hidden_selected:
        query = ('SELECT '+','.join(map(_q,names))+' FROM main.'+_q(table)
                 +' ORDER BY '+_q(names[0]))
    else:
        query = query.replace(' FROM '+_q(table),' FROM main.'+_q(table),1)
    return names, query


def _lifecycle_copy_plan(profile, tables):
    """Guava's real STARTED rows must precede children and terminal rows follow.

    Every source guard is active during this construction. Splitting one table
    does not change its original rowids, values, or the final whole-table proof.
    Other reviewed schemas retain their existing FK-only insertion schedule.
    """
    if profile.profile_id == 'black-research-full-v2':
        authority=('schema_metadata','research_config_versions')
        return tuple((table,None) for table in authority) + tuple((table,None) for table in tables if table not in authority)
    if profile.profile_id == 'watermelon-independent-raw-lifecycle-v1':
        authority=('raw_metadata','raw_cycles')
        return tuple((table,None) for table in authority) + tuple((table,None) for table in tables if table not in authority)
    if profile.profile_id == 'cherry-shadow-resolution-v2':
        authority=('shadow_schema_metadata','shadow_config_versions')
        return tuple((table,None) for table in authority) + tuple((table,None) for table in tables if table not in authority)
    if profile.profile_id == COCONUT_RECORDER_PROFILE_ID:
        # Layout readers verify the original contract before accepting a RAW
        # row. Copy that real authority row before any public parent, even if
        # an equivalent source created its tables in another physical order.
        return (('collection_contracts', None),) + tuple((table, None) for table in tables if table != 'collection_contracts')
    if profile.profile_id != GUAVA_PROFILE_ID:
        return tuple((table, None) for table in tables)
    order = ('collection_contracts','strategy_configs','run_audits','run_events',
             'source_requests','cycles','events','book_attempts','features','latest_event_state')
    if set(tables) != set(order):
        raise ValueError('Guava lifecycle copy plan differs from reviewed tables')
    return tuple((table, "status='STARTED'" if table == 'run_events' else None)
                 for table in order) + (('run_events', "status!='STARTED'"),)


def _offline_source(source, expected_sha):
    if any(Path(str(source)+suffix).exists() for suffix in ('-wal','-journal')):
        raise ValueError('RAW migration requires offline source without active journal/WAL')
    if file_sha256(source) != expected_sha:
        raise ValueError('RAW source SHA changed')


def verify_raw_transition(old, new, references, manifest, *, original, incoming, strategy):
    """Share the independent migration proof with readers and synchronization."""
    from .market_data_raw_transition import verify_raw_transition as verify

    return verify(old, new, references, manifest, original=original,
                  incoming=incoming, strategy=strategy)


def _assert_owned_path(path, descriptor):
    current, owned = path.stat(follow_symlinks=False), os.fstat(descriptor)
    if (current.st_dev,current.st_ino) != (owned.st_dev,owned.st_ino):
        raise ValueError('RAW derivative output path ownership changed: '+str(path))


def _write_manifest(sidecar, manifest, descriptor):
    # Write the exclusively-created inode, never reopen a replaceable path.
    _assert_owned_path(sidecar,descriptor)
    body=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode('utf-8')
    os.lseek(descriptor,0,os.SEEK_SET);os.ftruncate(descriptor,0)
    view=memoryview(body)
    while view:
        count=os.write(descriptor,view)
        if count <= 0: raise OSError('RAW manifest write did not advance')
        view=view[count:]
    os.fsync(descriptor)
    _assert_owned_path(sidecar,descriptor)


def _connect_owned_target(target, descriptor):
    _assert_owned_path(target,descriptor)
    connection=sqlite3.connect(target.as_uri()+'?mode=rw',uri=True)
    try:
        _assert_owned_path(target,descriptor)
    except BaseException:
        connection.close();raise
    return connection



def _page_evidence(connection):
    result={key:int(connection.execute('PRAGMA main.'+key).fetchone()[0])
            for key in ('auto_vacuum','page_size','page_count','freelist_count')}
    result['file_page_bytes']=result['page_size']*result['page_count']
    try:
        row=connection.execute('SELECT COALESCE(SUM(unused),0) FROM dbstat WHERE name NOT LIKE \'sqlite_stat%\'').fetchone()
        result['btree_unused_bytes']=int(row[0])
    except sqlite3.OperationalError:
        result['btree_unused_bytes']=None
    return result


def _pack_new_derivative(connection, guard, evidence):
    """Pack indexes/free pages only; never rebuild tables or renumber rowids.

    This connection is the raw, unattached writer of the exclusively-created
    derivative. REINDEX leaves table cells and rowids unchanged. Incremental
    vacuum moves whole pages, unlike the SQL VACUUM table-rebuild operation.
    """
    if type(connection) is not sqlite3.Connection or connection.in_transaction:
        raise ValueError('RAW packing requires an idle plain SQLite derivative connection')
    if any(row[1] not in ('main','temp') for row in connection.execute('PRAGMA database_list')):
        raise ValueError('RAW packing must not touch attached databases')
    if connection.execute('PRAGMA main.auto_vacuum').fetchone()[0] != 2:
        raise ValueError('RAW new derivative lacks incremental auto-vacuum')
    started=time.monotonic()
    evidence.update(contract='raw-new-derivative-index-pack-v1',status='RUNNING',
        scope='main indexes and free pages only; no table rebuild',before=_page_evidence(connection),
        reindexed=[],vacuum_steps=0,reclaimed_free_pages=0)
    try:
        indexes=[row[0] for row in connection.execute("SELECT name FROM main.sqlite_master WHERE type='index' ORDER BY name")]
        for index in indexes:
            guard();connection.execute('BEGIN IMMEDIATE')
            try:
                connection.execute('REINDEX main.'+_q(index))
                connection.commit()
            except BaseException:
                connection.rollback();raise
            evidence['reindexed'].append(index)
        evidence['after_reindex']=_page_evidence(connection)
        remaining=evidence['after_reindex']['freelist_count']
        while remaining:
            guard()
            # Fully step the PRAGMA cursor; some SQLite builds emit one row per page.
            connection.execute('PRAGMA main.incremental_vacuum(128)').fetchall()
            connection.commit()
            after=int(connection.execute('PRAGMA main.freelist_count').fetchone()[0])
            if after >= remaining:
                raise ValueError('RAW incremental vacuum made no progress')
            evidence['vacuum_steps']+=1
            evidence['reclaimed_free_pages']+=remaining-after
            remaining=after
        guard()
        evidence.update(status='PACKED',after=_page_evidence(connection),elapsed_seconds=time.monotonic()-started)
    except BaseException as error:
        evidence.update(status='FAILED',error=type(error).__name__+': '+str(error),
            elapsed_seconds=time.monotonic()-started)
        raise
    return evidence

def migrate_raw_database(source, target, *, source_sha256, namespace, references,
                         batch_rows=500, storage_guard=None, progress=None, profile_id=None):
    """Build an exclusive derivative in bounded commits, then prove it afresh.

    Failure leaves an explicitly FAILED derivative for review. It never removes
    or modifies source data and never publishes a partial derivative as VERIFIED.
    The writer owns shared storage admission; storage_guard checks local capacity.
    """
    source, target = Path(source).resolve(strict=True), Path(target).absolute()
    if source == target or target.exists() or target.is_symlink():
        raise ValueError('RAW derivative target must be absent')
    if type(batch_rows) is not int or not 1 <= batch_rows <= 4096:
        raise ValueError('RAW batch_rows must be in [1,4096]')
    sidecar = target.with_suffix(target.suffix+'.raw-migration.json')
    if sidecar.exists() or sidecar.is_symlink():
        raise ValueError('RAW derivative sidecar must be absent')
    _offline_source(source,source_sha256)
    if references.reader is None or references.writer is None:
        raise ValueError('RAW migration needs public reader and writer')
    require_raw_capabilities(references.writer)
    authority = references.writer.scalar_authority_identity()
    if authority != references.reader.scalar_authority_identity():
        raise ValueError('RAW public authorities differ')
    target_descriptor = sidecar_descriptor = None
    def guard():
        if storage_guard is not None: storage_guard()
        if target_descriptor is not None: _assert_owned_path(target,target_descriptor)
        if sidecar_descriptor is not None: _assert_owned_path(sidecar,sidecar_descriptor)
    guard()
    manifest = {'contract':CONTRACT,'strategy':STRATEGY,'digest_contract':DIGEST_CONTRACT,
        'source_sha256':source_sha256,'source':str(source),'source_path':str(source),
        'target':str(target),'destination_path':str(target),'namespace':namespace,
        'authority_uuid':authority,'projection_namespace':namespace,
        'projection_authority_uuid':authority,'status':'INCOMPLETE',
        'started_at':datetime.now(timezone.utc).isoformat(),'batch_rows':batch_rows,
        'source_identity':'offline-file-sha256','omitted_sqlite_metadata':'optimizer statistics only'}
    target.parent.mkdir(parents=True,exist_ok=True)
    # Preflight source completely before creating an output or publishing data.
    with closing(connect(source.as_uri()+'?mode=ro&immutable=1',uri=True,references=references)) as before:
        before.execute('PRAGMA query_only=ON')
        source_profile = profile_for_connection(before,profile_id=profile_id)
        strategy = source_profile.strategy
        require_raw_capabilities(references.writer,profile_id=source_profile.profile_id)
        layout_profile_id = None if source_profile.profile_id == BLACK_PROFILE_ID else source_profile.profile_id
        manifest['strategy'] = strategy
        if layout_profile_id is not None:
            manifest.update(contract=PROFILE_DERIVATIVE_CONTRACT,raw_profile_id=source_profile.profile_id,
                raw_profile_version=source_profile.version,raw_logical_schema_sha256=source_profile.logical_schema_sha256)
        validate_raw_source_schema(before,profile_id=source_profile.profile_id)
        validate_raw_namespace_authority(before,namespace,profile_id=source_profile.profile_id)
        if source_profile.profile_id == 'watermelon-independent-raw-lifecycle-v1':
            from .market_data_raw_schema_watermelon_sidecar import validate_cycle_ownership
            validate_cycle_ownership(before,namespace)
        validate_raw_source_objects(before,profile_id=source_profile.profile_id)
        validate_private_reference_ownership(before,strategy,references=references)
        if before.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('RAW source quick_check failed')
        if before.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('RAW source has foreign key violations')
        if raw_layout_metadata(before) is not None:
            metadata = raw_layout_metadata(before)
            if (metadata['namespace'],metadata['authority_uuid']) != (namespace,authority):
                raise ValueError('RAW source owner differs from migration owner')
            verify_raw_dependencies(before,references=references)
        from .market_data_levels import validate_level_layout, iter_level_logical_rows, insert_shared_levels
        from .market_data_policy import public_level_projections
        level_auxiliary=validate_level_layout(before)
        if level_auxiliary:
            from .market_data_migrate import _validate_source_level_links
            _validate_source_level_links(before,{rule.table:rule for rule in public_level_projections(strategy)
                if rule.table in source_profile.level_tables})
        source_auxiliary = validate_raw_layout(before) | validate_private_packets(before,strategy=strategy) | level_auxiliary
        objects = [tuple(row) for row in before.execute(
            "SELECT type,name,tbl_name,sql FROM main.sqlite_master WHERE sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' ORDER BY rowid") if row[1] not in source_auxiliary]
        tables = _dependency_order(before,[name for kind,name,_,_ in objects if kind == 'table'])
        expected = {table:_logical_digest(before,table,references,profile_id=source_profile.profile_id) for table in tables}
        _offline_source(source,source_sha256)
        # Exclusive creation: never truncate an unrelated racing output.
        target_descriptor = os.open(target,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
        try:
            sidecar_descriptor = os.open(sidecar,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
        except BaseException:
            # Target is ours but stays for review; no potentially foreign sidecar overwrite.
            os.close(target_descriptor)
            raise
        try:
            _write_manifest(sidecar,manifest,sidecar_descriptor)
            with closing(_connect_owned_target(target,target_descriptor)) as after:
                after.execute('PRAGMA foreign_keys=ON')
                if source_profile.profile_id == GUAVA_PROFILE_ID:
                    after.execute('PRAGMA recursive_triggers=ON')
                after.execute('PRAGMA journal_mode=DELETE')
                after.execute('PRAGMA synchronous=FULL')
                encoding = before.execute('PRAGMA encoding').fetchone()[0]
                if encoding not in ('UTF-8','UTF-16le','UTF-16be'):
                    raise ValueError('RAW source has unsupported encoding')
                after.execute("PRAGMA encoding='"+encoding+"'")
                # Set on the empty derivative; converting a populated file would require VACUUM.
                after.execute("PRAGMA auto_vacuum=INCREMENTAL")
                if after.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
                    raise ValueError("RAW new derivative auto-vacuum initialization failed")
                after.execute('BEGIN IMMEDIATE')
                for kind,name,_,sql in objects:
                    if kind == 'table': after.execute(skeleton_sql(name,source_profile.profile_id) if name in source_profile.tables else sql)
                create_raw_layout(after,namespace,authority,profile_id=layout_profile_id)
                # These exact reviewed indexes/triggers constrain every batch.
                early = set(source_profile.source_indexes)
                for sql in source_profile.source_indexes.values(): after.execute(sql)
                for table in source_profile.tables:
                    for name,sql in source_profile.triggers_for(table).items():
                        after.execute(sql)
                        early.add(name)
                if source_profile.profile_id == GUAVA_PROFILE_ID:
                    # Lifecycle and private append-only guards constrain the
                    # copy itself; never disable guards or fabricate run states.
                    for kind,name,_,sql in objects:
                        if kind in ('index','trigger') and name not in early:
                            after.execute(sql)
                            early.add(name)
                after.commit()
                copy_plan = _lifecycle_copy_plan(source_profile,tables)
                if source_profile.profile_id == GUAVA_PROFILE_ID:
                    manifest['lifecycle_copy_plan'] = 'guava-real-started-children-terminal-v1'
                for table, row_filter in copy_plan:
                    if table in source_profile.level_tables:
                        # Reuse the existing public ladder CAS and binding schema.
                        # Group by the original snapshot so a ladder is acknowledged once.
                        rows=iter_level_logical_rows(before,strategy,table,references=references,require_rowid=True)
                        # Snapshot IDs may be interleaved by original rowid; use a
                        # bounded SQLite ordering spool, never a whole-file list.
                        with closing(sqlite3.connect('')) as spool:
                            spool.execute('PRAGMA cache_size=-8192')
                            spool.execute('PRAGMA temp_store=FILE')
                            if source_profile.source_objects:
                                ddl=next(sql for kind,name,_,sql in source_profile.source_objects if kind=='table' and name==table)
                            else:
                                # Legacy Black v1 has no full-schema snapshot;
                                # retain its already validated original ladder DDL.
                                ddl=before.execute('SELECT sql FROM main.sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone()[0]
                            spool.execute(ddl)
                            columns=tuple(row[1] for row in spool.execute('PRAGMA table_info('+_q(table)+')'))
                            names=('rowid',*columns)
                            spool.executemany('INSERT INTO '+_q(table)+'('+','.join(map(_q,names))+') VALUES('+','.join('?' for _ in names)+')',
                                ((row['__rowid__'],*(row[key] for key in columns)) for row in rows))
                            query='SELECT rowid,'+','.join(map(_q,columns))+' FROM '+_q(table)+' ORDER BY snapshot_id,side,level_index'
                            offset=1+columns.index('snapshot_id')
                            for _,group in groupby(spool.execute(query),key=lambda row:row[offset]):
                                batch=[dict(zip(('__rowid__',*columns),row,strict=True)) for row in group]
                                guard();after.execute('BEGIN IMMEDIATE')
                                insert_shared_levels(after,strategy,table,batch,references=references,preserve_rowid=True)
                                after.commit()
                        after.execute('DROP VIEW IF EXISTS temp.'+_q(table))
                    elif table in source_profile.tables:
                        with closing(iter_raw_logical_rows(before,table,references=references,profile_id=source_profile.profile_id)) as rows:
                            while batch := list(islice(rows,batch_rows)):
                                guard(); after.execute('BEGIN IMMEDIATE')
                                insert_raw_rows(after,strategy,table,batch,references=references,namespace=namespace)
                                after.commit()
                    else:
                        names,query = _private_copy_select(before,table)
                        if row_filter is not None:
                            # Both predicates above are reviewed constants, not
                            # user-supplied SQL. Keep the original rowid ordering.
                            prefix, ordering = query.rsplit(' ORDER BY ',1)
                            query = prefix+' WHERE '+row_filter+' ORDER BY '+ordering
                        with closing(_raw_rows(before,query)) as cursor:
                            while chunk := list(islice(cursor,batch_rows)):
                                guard()
                                dictionaries = [dict(zip(names,expand_private_values(before,row),strict=True)) for row in chunk]
                                encoded = externalize_rows(strategy,table,dictionaries,references=references)
                                payload = [tuple(row[name] for name in names) for row in encoded]
                                after.execute('BEGIN IMMEDIATE')
                                after.executemany(f'INSERT INTO main.{_q(table)}({",".join(map(_q,names))}) VALUES({",".join("?" for _ in names)})',payload)
                                after.commit()
                    if progress: progress(table,{'rows':expected[table][0]})
                    guard()
                guard(); after.execute('BEGIN IMMEDIATE')
                if before.execute("SELECT 1 FROM main.sqlite_master WHERE name='sqlite_sequence'").fetchone():
                    after.execute('DELETE FROM sqlite_sequence')
                    after.executemany('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)',before.execute('SELECT name,seq FROM main.sqlite_sequence'))
                for kind,name,_,sql in objects:
                    if kind in ('index','trigger','view') and name not in early: after.execute(sql)
                for pragma in ('application_id','user_version'):
                    after.execute('PRAGMA '+pragma+'='+str(before.execute('PRAGMA '+pragma).fetchone()[0]))
                after.commit()
                manifest["physical_maintenance"]={}
                _pack_new_derivative(after,guard,manifest["physical_maintenance"])
                schema = verify_raw_schema_transition(before,after,profile_id=source_profile.profile_id)
                private = verify_raw_private_storage(before,after,references,profile_id=source_profile.profile_id)
                if after.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('RAW derivative integrity failed')
            # Fresh resolver and uncached body references prove the durable file.
            uncached = PayloadReferences(reader=references.reader,cache_bytes=0)
            with closing(connect(target.as_uri()+'?mode=ro&immutable=1',uri=True,references=uncached)) as after:
                verify_raw_dependencies(after,references=uncached)
                actual = {table:_logical_digest(after,table,uncached,profile_id=source_profile.profile_id) for table in tables}
                if actual != expected: raise ValueError('RAW derivative typed logical rows differ')
            _offline_source(source,source_sha256)
            from .market_data_projection_closure import verify_projection_closure
            closure = verify_projection_closure(references.reader,target,strategy)
            guard()
            digest, size = file_sha256(target),target.stat().st_size
            manifest.update(status='VERIFIED',completed_at=datetime.now(timezone.utc).isoformat(),
                tables={name:{'rows':value[0],'logical_sha256':value[1],
                    **({'externalized_raw_rows':value[0], 'key_basis':'declared-primary-key+rowid-v1',
                        'implicit_rowid_preserved':True} if name in source_profile.tables else
                       {'externalized_level_rows':value[0], 'key_basis':'declared-primary-key+rowid-v1',
                        'implicit_rowid_preserved':True} if name in source_profile.level_tables else {})}
                    for name,value in expected.items()},
                source_schema_sha256=schema['source']['sha256'],target_schema_sha256=schema['target']['sha256'],
                schema_evidence=schema,private_storage=private,public_projection_records=closure,
                target_sha256=digest,destination_sha256=digest,source_bytes=source.stat().st_size,
                target_bytes=size,destination_bytes=size)
        except BaseException as exc:
            manifest.update(status='FAILED',error=type(exc).__name__+': '+str(exc))
            try:
                _write_manifest(sidecar,manifest,sidecar_descriptor)
            except (OSError,ValueError):
                pass  # Preserve original failure; never overwrite a replacement path.
            raise
        else:
            _write_manifest(sidecar,manifest,sidecar_descriptor)
        finally:
            os.close(sidecar_descriptor);os.close(target_descriptor)
    return manifest
