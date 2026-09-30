"""Immutable incremental public-payload bundles for verified strategy DB pins.

An individual strategy snapshot is no longer self-contained after body migration.
Its reference closure must be present and hash-verified in the shared local store.
Bundles contain only public payloads, never private strategy tables or credentials.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid
from contextlib import contextmanager

from .market_data_migrate import file_sha256, quote_identifier
from .market_data_policy import KNOWN_STRATEGIES, public_columns
from .market_data_refs import is_external_value,value_reference_hashes


class BundleLimitError(ValueError):
    pass


@contextmanager
def _guarded_sqlite(connection, guard=None):
    """Restore the caller's guard exception after SQLite aborts its VM work."""
    if guard is None:
        yield
        return
    guard()
    errors = []
    def progress():
        try:
            guard()
        except BaseException as error:
            errors.append(error)
            return 1
        return 0
    connection.set_progress_handler(progress, 10000)
    try:
        yield
        guard()
    except sqlite3.Error:
        if errors:
            raise errors[0]
        guard()
        raise
    finally:
        connection.set_progress_handler(None, 0)


def reference_closure(database: Path, strategy: str, *, immutable: bool = False, guard=None) -> list[str]:
    database = Path(database).resolve(strict=True)
    suffix = '&immutable=1' if immutable else ''
    connection = sqlite3.connect(database.as_uri() + '?mode=ro' + suffix, uri=True)
    try:
        with _guarded_sqlite(connection, guard):
            return _reference_closure(connection, strategy, guard)
    finally:
        connection.close()


def _reference_closure(connection, strategy, guard):
    from .market_data_mixed import mixed_columns
    from .market_data_private_packets import validate_private_packets, PrivatePacketReader
    from .market_data_refs import _is_local_packet
    packet_aux = validate_private_packets(connection, strategy=strategy)
    packet_reader = PrivatePacketReader(connection, strategy=strategy) if packet_aux else None
    hashes = set()
    for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        if guard: guard()
        if table in packet_aux:
            continue  # Trace only reachable packets under their original field policy.
        if strategy not in KNOWN_STRATEGIES:
            # An unclassified DB is not evidence of an empty dependency set.
            # Detect markers but never fetch possibly private data by guessing
            # another strategy's public-column policy.
            for info in connection.execute(f'PRAGMA table_info({quote_identifier(table)})'):
                column=quote_identifier(info[1])
                query=(f'SELECT {column} FROM {quote_identifier(table)} '
                       f'WHERE typeof({column}) IN (\'text\',\'blob\') '
                       f'AND substr(CAST({column} AS BLOB),1,3)=?')
                if any(is_external_value(row[0]) for row in connection.execute(query,(b'\x1ePM',))):
                    raise ValueError('shared references require a classified strategy identity')
        if strategy == 'golden-apple' and table == 'payloads':
            from .market_data_apple import PUBLIC_LEGACY_ROLES, legacy_payload_roles
            columns = {row[1] for row in connection.execute('PRAGMA table_info(payloads)')}
            if columns != {'hash', 'encoding', 'bytes', 'compressed'}:
                raise ValueError('unsupported Apple legacy payload pool schema')
            cursor = connection.execute(
                "SELECT hash,compressed FROM payloads "
                "WHERE substr(CAST(compressed AS BLOB),1,3)=?", (b'\x1ePM',)
            )
            while rows := cursor.fetchmany(1024):
                if guard: guard()
                roles = legacy_payload_roles(connection, [row[0] for row in rows])
                for source_sha, value in rows:
                    if not set(roles[source_sha]) <= PUBLIC_LEGACY_ROLES:
                        raise ValueError('Apple legacy shared payload lacks an exclusively public role')
                    hashes.update(value_reference_hashes(value))
            continue
        logical_table = table
        if table == '_public_projection_context':
            from .market_data_projection_links import validate_projection_layout, projection_layout_metadata
            validate_projection_layout(connection)
            owner = projection_layout_metadata(connection)
            if owner is None or owner['strategy'] != strategy:
                raise ValueError('projection body context strategy differs from source')
            logical_table = 'market_snapshots'
        elif table == '_public_catalog_context':
            from .market_data_catalog_links import validate_catalog_layout, catalog_layout_metadata
            validate_catalog_layout(connection)
            owner = catalog_layout_metadata(connection)
            if owner is None or owner['strategy'] != strategy:
                raise ValueError('catalog body context strategy differs from source')
            logical_table = 'market_catalog'
        allowed = public_columns(strategy, logical_table)
        allowed=allowed|mixed_columns(strategy,logical_table)
        if strategy=='golden-apple' and table=='runs':
            names={row[1] for row in connection.execute('PRAGMA table_info(runs)')}
            if 'frame' in names:
                from .market_data_apple import APPLICATION_ID
                if connection.execute('PRAGMA application_id').fetchone()[0]!=APPLICATION_ID:
                    raise ValueError('Apple shared-frame source has unexpected application identity')
                allowed=allowed|{'frame'}
        if table == '_market_data_level_groups':
            from .market_data_levels import validate_level_layout
            validate_level_layout(connection)
            allowed = frozenset({'body_ref'})
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info({quote_identifier(table)})') if row[1] in allowed]
        if not columns:
            continue
        query = 'SELECT ' + ','.join(map(quote_identifier, columns)) + ' FROM ' + quote_identifier(table)
        for row in connection.execute(query):
            if guard: guard()
            for column, value in zip(columns, row, strict=True):
                if _is_local_packet(value):
                    if packet_reader is None:
                        raise ValueError('private packet dependency has no owning dictionary')
                    value = packet_reader.resolve(value, table=logical_table, column=column)
                hashes.update(value_reference_hashes(value))
    return sorted(hashes)


def closure_digest(hashes: list[str]) -> str:
    from .market_data_store import validate_hashes
    canonical = sorted(set(hashes))
    for start in range(0, len(canonical), 1024):
        validate_hashes(canonical[start:start+1024])
    return hashlib.sha256(('\n'.join(canonical) + ('\n' if canonical else '')).encode()).hexdigest()


def verify_closure(reader, hashes: list[str], *, guard=None) -> dict:
    if guard: guard()
    digest = closure_digest(hashes)
    total = 0
    for sha in sorted(set(hashes)):
        if guard: guard()
        values = reader.get_many([sha])
        if len(values) != 1 or hashlib.sha256(values[0]).hexdigest() != sha:
            raise ValueError('shared pin payload digest mismatch')
        total += len(values[0])
    if guard: guard()
    return {'closure_sha256': digest, 'payload_count': len(set(hashes)), 'raw_bytes': total}


def missing_payloads(reader, hashes: list[str]) -> list[str]:
    from .market_data_store import MissingPayloadError
    closure_digest(hashes)
    missing = []
    for sha in sorted(set(hashes)):
        try:
            values = reader.get_many([sha])
        except MissingPayloadError:
            missing.append(sha)
        else:
            # Corruption is not a missing artifact to silently overwrite.
            if len(values) != 1 or hashlib.sha256(values[0]).hexdigest() != sha:
                raise ValueError('existing shared pin payload is corrupt')
    return missing


# Bundle readers never execute incoming DDL. Exact known layouts also reject
# extra columns, views, changed indexes and triggers before any local write.
_TABLE_SQL = {
    "payloads": "CREATE TABLE payloads (sha256 TEXT PRIMARY KEY, codec TEXT NOT NULL CHECK(codec IN ('identity','zlib')), raw_size INTEGER NOT NULL CHECK(raw_size >= 0), body BLOB NOT NULL) WITHOUT ROWID",
    "observations": "CREATE TABLE observations (observer TEXT NOT NULL, observation_id TEXT NOT NULL, observed_at TEXT NOT NULL, kind TEXT NOT NULL, event_id TEXT, token_id TEXT, payload_sha TEXT NOT NULL REFERENCES payloads(sha256), metadata_json TEXT NOT NULL, PRIMARY KEY(observer, observation_id)) WITHOUT ROWID",
    "public_subject_sets": "CREATE TABLE public_subject_sets (set_sha TEXT PRIMARY KEY) WITHOUT ROWID",
    "public_subject_set_members": "CREATE TABLE public_subject_set_members (set_sha TEXT NOT NULL REFERENCES public_subject_sets(set_sha),subject_kind TEXT NOT NULL CHECK(subject_kind IN ('token','event')),subject_id TEXT NOT NULL,PRIMARY KEY(set_sha,subject_kind,subject_id)) WITHOUT ROWID",
    "observation_subject_sets": "CREATE TABLE observation_subject_sets (observer TEXT NOT NULL,observation_id TEXT NOT NULL,set_sha TEXT NOT NULL REFERENCES public_subject_sets(set_sha),PRIMARY KEY(observer,observation_id),FOREIGN KEY(observer,observation_id) REFERENCES observations(observer,observation_id)) WITHOUT ROWID",
}
_INDEX_SQL = {
    "observations_token_time": "CREATE INDEX observations_token_time ON observations(token_id, observed_at)",
    "observations_event_time": "CREATE INDEX observations_event_time ON observations(event_id, observed_at)",
    "observations_owner_kind_time": "CREATE INDEX observations_owner_kind_time ON observations(observer, kind, observed_at)",
    "observations_payload_sha": "CREATE INDEX observations_payload_sha ON observations(payload_sha, observer, observation_id)",
    "public_subject_lookup": "CREATE INDEX public_subject_lookup ON public_subject_set_members(subject_kind,subject_id,set_sha)",
}
_TRIGGER_SQL = {
    "observations_no_" + action.lower():
        "CREATE TRIGGER observations_no_" + action.lower() + " BEFORE " + action +
        " ON observations BEGIN SELECT RAISE(ABORT, 'observations are append only'); END"
    for action in ("UPDATE", "DELETE")
}
MAX_INDEX_BYTES = 64 << 20
MAX_BUNDLE_OBSERVATIONS = 100_000


def _sql_key(value):
    from .market_data_sql_schema import canonical_sql_key
    return canonical_sql_key(value)


def _validate_bundle_schema(connection):
    from .market_data_store import APPLICATION_ID, SCHEMA_VERSION
    from .market_data_scalars import SCALAR_TABLE_SQL, SCALAR_INDEX_SQL, SCALAR_TRIGGER_SQL
    from .market_data_projections import (PROJECTION_TABLE_SQL, PROJECTION_INDEX_SQL,
                                         PROJECTION_TRIGGER_SQL, validate_projection_storage_schema)
    if (connection.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
            or connection.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION):
        raise ValueError('unsupported public bundle schema identity')
    objects = connection.execute(
        "SELECT type,name,sql FROM sqlite_master"
    ).fetchall()
    tables = {name for kind, name, _ in objects if kind == 'table'}
    base_tables = tables - set(SCALAR_TABLE_SQL) - set(PROJECTION_TABLE_SQL)
    scalar_tables = tables & set(SCALAR_TABLE_SQL)
    projection_tables = tables & set(PROJECTION_TABLE_SQL)
    if (base_tables not in ({'payloads', 'observations'}, set(_TABLE_SQL))
            or scalar_tables not in (set(), set(SCALAR_TABLE_SQL))
            or projection_tables not in (set(), set(PROJECTION_TABLE_SQL))
            or (projection_tables and scalar_tables != set(SCALAR_TABLE_SQL))):
        raise ValueError('public bundle contains unexpected tables')
    allowed = {'table': {**_TABLE_SQL, **SCALAR_TABLE_SQL, **PROJECTION_TABLE_SQL},
               'index': {**_INDEX_SQL, **SCALAR_INDEX_SQL, **PROJECTION_INDEX_SQL},
               'trigger': {**_TRIGGER_SQL, **SCALAR_TRIGGER_SQL, **PROJECTION_TRIGGER_SQL}}
    for kind, name, sql in objects:
        if kind == 'index' and sql is None and name.startswith('sqlite_autoindex_'):
            continue
        expected = allowed.get(kind, {}).get(name)
        if expected is None or sql is None or _sql_key(sql) != _sql_key(expected):
            raise ValueError('public bundle contains an unexpected schema object: ' + name)
    if projection_tables:
        validate_projection_storage_schema(connection)
    return 'public_subject_sets' in tables


def _assert_no_projection_rows(connection):
    """Body/scalar transfer contracts cannot smuggle other public field groups."""
    from .market_data_projections import PROJECTION_TABLE_SQL
    present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in PROJECTION_TABLE_SQL:
        if table == 'projection_storage':
            continue  # The exact validated format singleton is metadata, not market data.
        if table in present and connection.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
            raise ValueError('bundle contains undeclared projection data: ' + table)


def validate_observation_cursor(cursor, hashes):
    from .market_data_store import validate_hashes
    if cursor is None:
        return
    if (not isinstance(cursor, list) or len(cursor) != 3
            or any(not isinstance(value, str) or not value or len(value) > 1024 for value in cursor)):
        raise ValueError('invalid public observation continuation cursor')
    validate_hashes(cursor[:1])
    if cursor[0] not in hashes:
        raise ValueError('public observation cursor is outside requested closure')


def _observations(connection, hashes, *, subjects, after=None):
    from .market_data_store import Observation
    for sha in sorted(set(hashes)):
        if after is not None and sha < after[0]:
            continue
        continuation = ' AND (observer,observation_id)>(?,?)' if after is not None and sha == after[0] else ''
        parameters = (sha, *after[1:]) if continuation else (sha,)
        cursor = connection.execute(
            'SELECT observer,observation_id,observed_at,kind,event_id,token_id,payload_sha,metadata_json '
            'FROM observations WHERE payload_sha=?' + continuation + ' ORDER BY observer,observation_id', parameters
        )
        for row in cursor:
            pairs = ()
            if subjects:
                link = connection.execute(
                    'SELECT set_sha FROM observation_subject_sets WHERE observer=? AND observation_id=?', row[:2]
                ).fetchone()
                if link is not None:
                    pairs = tuple(connection.execute(
                        'SELECT subject_kind,subject_id FROM public_subject_set_members WHERE set_sha=? '
                        'ORDER BY subject_kind,subject_id LIMIT 32769', link
                    ))
                    actual = hashlib.sha256(json.dumps(pairs, separators=(',', ':')).encode()).hexdigest()
                    if not pairs or actual != link[0]:
                        raise ValueError('public bundle subject index checksum mismatch')
            observation = Observation(observer=row[0], observation_id=row[1], observed_at=row[2],
                                      kind=row[3], event_id=row[4], token_id=row[5], payload_sha=row[6],
                                      metadata_json=row[7],
                                      token_ids=tuple(value for kind, value in pairs if kind == 'token'),
                                      event_ids=tuple(value for kind, value in pairs if kind == 'event'))
            observation.row()
            if pairs != observation.subjects():
                raise ValueError('public bundle has invalid subject identities')
            yield observation


def _index_record(observation):
    return (json.dumps([observation.row(), observation.subjects()], separators=(',', ':')) + '\n').encode()


def _insert_bundle_observation(connection, observation):
    connection.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)', observation.row())
    pairs = observation.subjects()
    if pairs:
        sha = hashlib.sha256(json.dumps(pairs, separators=(',', ':')).encode()).hexdigest()
        connection.execute('INSERT OR IGNORE INTO public_subject_sets VALUES(?)', (sha,))
        connection.executemany('INSERT OR IGNORE INTO public_subject_set_members VALUES(?,?,?)',
                               ((sha, *pair) for pair in pairs))
        connection.execute('INSERT INTO observation_subject_sets VALUES(?,?,?)',
                           (observation.observer, observation.observation_id, sha))


def export_bundle(reader, hashes: list[str], output: Path, *, payload_hashes=None, observation_after=None) -> dict:
    """Export reachable public receipts, with optional already-local body omission.

    V2 transport permits omitted payload foreign keys only for its explicitly
    declared reference closure. Import verifies those bodies in the local store.
    """
    from .market_data_store import PayloadStore
    output = Path(output).absolute()
    closure = closure_digest(hashes)
    hashes = sorted(set(hashes))
    validate_observation_cursor(observation_after, hashes)
    bodies = hashes if payload_hashes is None else sorted(set(payload_hashes))
    closure_digest(bodies)
    if not set(bodies) <= set(hashes):
        raise ValueError('public bundle bodies must be within the requested closure')
    if output.exists() or output.is_symlink():
        raise ValueError('public bundle output already exists')
    source = reader._connection
    subjects = _validate_bundle_schema(source)
    own_snapshot = not source.in_transaction
    if own_snapshot:
        source.execute('BEGIN')
    try:
        # Even omitted bodies must exist and pass their exact-byte hash check.
        verify_closure(reader, hashes)
        output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with PayloadStore(output) as bundle:
            for sha in bodies:
                if bundle.put_many(reader.get_many([sha])) != [sha]:
                    raise ValueError('public bundle payload mismatch')
            connection = bundle._connection
            connection.execute('PRAGMA foreign_keys=OFF')
            connection.execute('BEGIN IMMEDIATE')
            digest, count, index_bytes = hashlib.sha256(), 0, 0
            last_key = next_key = None
            try:
                for observation in _observations(source, hashes, subjects=subjects, after=observation_after):
                    record = _index_record(observation)
                    if len(record) > MAX_INDEX_BYTES:
                        raise BundleLimitError('one public observation exceeds index byte limit')
                    if count == MAX_BUNDLE_OBSERVATIONS or index_bytes + len(record) > MAX_INDEX_BYTES:
                        next_key = last_key
                        break
                    count += 1
                    index_bytes += len(record)
                    digest.update(record)
                    _insert_bundle_observation(connection, observation)
                    last_key = [observation.payload_sha, observation.observer, observation.observation_id]
                connection.execute('COMMIT')
            except BaseException:
                connection.execute('ROLLBACK')
                raise
            stats = bundle.stats()
        if Path(str(output) + '-wal').exists():
            raise ValueError('public bundle has an uncheckpointed WAL')
        manifest = {'contract': 'public-payload-bundle-v2', 'status': 'VERIFIED',
                    'file_sha256': file_sha256(output), 'closure_sha256': closure,
                    'reference_hashes': hashes, 'payload_hashes': bodies,
                    'observation_sha256': digest.hexdigest(), 'index_bytes': index_bytes,
                    'observation_after': observation_after, 'next_observation_after': next_key,
                    'observation_snapshot': 'per-page-read-snapshot-v1', **stats}
        output.with_suffix(output.suffix + '.manifest.json').write_text(json.dumps(manifest, indent=2))
        return manifest
    finally:
        if own_snapshot:
            source.execute('ROLLBACK')


def import_bundle(path: Path, manifest: dict, writer) -> dict:
    from .market_data_store import PayloadReader
    path = Path(path).resolve(strict=True)
    version = manifest.get('contract')
    if version not in {'public-payload-bundle-v1', 'public-payload-bundle-v2'} or manifest.get('status') != 'VERIFIED':
        raise ValueError('unsupported or unverified public payload bundle')
    if file_sha256(path) != manifest['file_sha256']:
        raise ValueError('public payload bundle checksum mismatch')
    bodies = manifest['payload_hashes']
    hashes = manifest['reference_hashes'] if version.endswith('v2') else bodies
    if closure_digest(hashes) != manifest['closure_sha256']:
        raise ValueError('public payload bundle closure mismatch')
    closure_digest(bodies)
    after = manifest.get('observation_after')
    next_key = manifest.get('next_observation_after')
    validate_observation_cursor(after, hashes)
    validate_observation_cursor(next_key, hashes)
    if version.endswith('v2') and manifest.get('observation_snapshot') != 'per-page-read-snapshot-v1':
        raise ValueError('unsupported public observation snapshot contract')
    if hashes != sorted(set(hashes)) or bodies != sorted(set(bodies)) or not set(bodies) <= set(hashes):
        raise ValueError('public bundle has an invalid payload set')
    with PayloadReader(path) as reader:
        connection = reader._connection
        subjects = _validate_bundle_schema(connection)
        _assert_no_projection_rows(connection)
        stats = reader.stats()
        if any(reader.scalar_stats().values()):
            raise ValueError('body bundle contains undeclared scalar records')
        actual_bodies = [row[0] for row in connection.execute('SELECT sha256 FROM payloads ORDER BY sha256')]
        if actual_bodies != bodies or any(manifest.get(key) != stats[key] for key in ('payload_count', 'raw_bytes')):
            raise ValueError('public bundle contains an unexpected payload set')
        if version.endswith('v1') and stats['observation_count'] != 0:
            raise ValueError('legacy public bundle must not import observer state')
        verify_closure(reader, bodies)
        verify_closure(writer, sorted(set(hashes) - set(bodies)))
        # Only explicit omitted-body references may be unresolved in transport.
        if any(row[0] != 'observations' or row[2] != 'payloads'
               for row in connection.execute('PRAGMA foreign_key_check')):
            raise ValueError('public bundle contains broken subject references')
        if subjects and connection.execute(
            'SELECT 1 FROM public_subject_sets s WHERE NOT EXISTS '
            '(SELECT 1 FROM observation_subject_sets l WHERE l.set_sha=s.set_sha) LIMIT 1'
        ).fetchone():
            raise ValueError('public bundle contains unreachable subject sets')
        digest, count, index_bytes = hashlib.sha256(), 0, 0
        last_key = None
        for observation in _observations(connection, hashes, subjects=subjects):
            key = [observation.payload_sha, observation.observer, observation.observation_id]
            if after is not None and key <= after:
                raise ValueError('public observation page precedes continuation cursor')
            last_key = key
            record = _index_record(observation)
            digest.update(record)
            count += 1
            index_bytes += len(record)
            if count > MAX_BUNDLE_OBSERVATIONS or index_bytes > MAX_INDEX_BYTES:
                raise BundleLimitError('public observation index exceeds limit')
        if next_key is not None and (not count or next_key != last_key):
            raise ValueError('public observation continuation does not match page')
        if count != stats['observation_count']:
            raise ValueError('public bundle contains observations outside requested closure')
        if version.endswith('v2') and (manifest.get('observation_count') != count
                or manifest.get('observation_sha256') != digest.hexdigest()
                or manifest.get('index_bytes') != index_bytes):
            raise ValueError('public bundle observation index checksum mismatch')
        for sha in bodies:
            if writer.put_many(reader.get_many([sha])) != [sha]:
                raise ValueError('import writer acknowledgement mismatch')
        batch = []
        for observation in _observations(connection, hashes, subjects=subjects):
            batch.append(observation)
            if len(batch) == 1024:
                writer.append_observations(batch)
                batch = []
        if batch:
            writer.append_observations(batch)
    result = {'imported_payload_count': len(bodies), 'closure_sha256': manifest['closure_sha256']}
    if version.endswith('v2'):
        result.update(imported_observation_count=count, observation_sha256=digest.hexdigest(), index_bytes=index_bytes)
    return result


def _canonical_existing(path: Path, *, directory: bool) -> Path:
    path = path.expanduser().absolute()
    resolved = path.resolve(strict=True)
    if path != resolved or path.is_symlink():
        raise ValueError('shared bundle paths must be canonical without symlinks')
    if (directory and not path.is_dir()) or (not directory and not path.is_file()):
        raise ValueError('shared bundle path has the wrong type')
    return path


def _staging_root(path: Path) -> Path:
    root = _canonical_existing(path, directory=True)
    stat = root.stat()
    if stat.st_uid != os.getuid() or stat.st_mode & 0o022:
        raise ValueError('shared staging root must be owner controlled')
    return root


def main(argv=None) -> int:
    import argparse
    from .market_data_store import PayloadReader, StoreError, StoreLimitError, validate_hashes
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for operation in ('export','export-scalars','export-projections'):
        export = commands.add_parser(operation)
        export.add_argument('--db', type=Path, required=True)
        export.add_argument('--storage-root', type=Path, required=True)
        export.add_argument('--staging-root', type=Path, required=True)
    cleanup = commands.add_parser('cleanup')
    cleanup.add_argument('--bundle', type=Path, required=True)
    cleanup.add_argument('--staging-root', type=Path, required=True)
    cleanup.add_argument('--expected-sha', required=True)
    args = parser.parse_args(argv)
    try:
        staging = _staging_root(args.staging_root)
        if args.command == 'cleanup':
            path = _canonical_existing(args.bundle, directory=False)
            directory = path.parent
            if directory.parent != staging or not directory.name.startswith('shared-payload-') or path.name != 'payloads.db':
                raise ValueError('cleanup target is outside a shared bundle directory')
            manifest_path = path.with_suffix('.db.manifest.json')
            if manifest_path.is_symlink() or set(directory.iterdir()) != {path, manifest_path}:
                raise ValueError('cleanup directory contains unexpected files')
            manifest = json.loads(manifest_path.read_text())
            if args.expected_sha != manifest.get('file_sha256') or file_sha256(path) != args.expected_sha:
                raise ValueError('cleanup checksum mismatch')
            path.unlink()
            manifest_path.unlink()
            directory.rmdir()
            print(json.dumps({'status': 'REMOVED'}))
            return 0
        root = _canonical_existing(args.storage_root, directory=True)
        database = _canonical_existing(args.db, directory=False)
        if not database.is_relative_to(root) or database.stat().st_dev != root.stat().st_dev:
            raise ValueError('shared store is not on its configured storage root')
        # A missing /Volumes mount must not be replaced by an ordinary directory
        # on the startup disk. Subfolders inside a valid volume are accepted.
        if len(root.parts) > 2 and root.parts[1] == 'Volumes':
            volume = Path(*root.parts[:3])
            if not volume.is_mount() or root.stat().st_dev != volume.stat().st_dev:
                raise ValueError('shared external volume is not mounted')
        data = sys.stdin.buffer.read(262145)
        if len(data) > 262144:
            raise ValueError('shared bundle request exceeds byte limit')
        request = json.loads(data)
        scalar_mode = args.command == 'export-scalars'
        projection_mode = args.command == 'export-projections'
        record_mode = scalar_mode or projection_mode
        body_key = 'transferred_ids' if record_mode else 'payload_hashes'
        cursor_key = 'receipt_after' if record_mode else 'observation_after'
        key = 'record_ids' if record_mode else 'hashes'
        allowed = {key,body_key,cursor_key}|({'authority_uuid'} if record_mode else set())
        if not isinstance(request, dict) or key not in request or set(request) - allowed:
            raise ValueError('shared bundle request contains unexpected fields')
        hashes = request[key]
        bodies = request.get(body_key, hashes)
        if record_mode:
            from .market_data_scalar_bundle import validate_ids
            validate_ids(hashes);validate_ids(bodies)
            if not isinstance(request.get('authority_uuid'),str):
                raise ValueError('public record bundle requires an authority identity')
        else:
            validate_hashes(hashes);validate_hashes(bodies)
        if not set(bodies) <= set(hashes):
            raise ValueError('bundle bodies are outside requested closure')
        with PayloadReader(database) as reader:
            # Bound staging before materializing any bodies. Only the public
            # store's schema is accessed; there is no user-supplied SQL.
            sizes = []
            if record_mode:
                get_records = reader.get_projection_records if projection_mode else reader.get_scalar_records
                sizes = [len(json.dumps(value.to_wire(),ensure_ascii=False).encode())
                         for value in get_records(bodies,authority_uuid=request['authority_uuid'])]
            else:
                c = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)
                try:
                    for sha in set(bodies):
                        found = c.execute('SELECT raw_size FROM payloads WHERE sha256=?', (sha,)).fetchone()
                        if found is None:
                            raise ValueError('requested shared payload is missing')
                        sizes.append(found[0])
                finally:
                    c.close()
            if sum(sizes) > 256 << 20:
                raise BundleLimitError('shared bundle batch exceeds 256MiB; split hashes')
            free = os.statvfs(staging).f_bavail * os.statvfs(staging).f_frsize
            if free < (sum(sizes) * 2 + (1 << 30)):
                raise ValueError('insufficient staging free space')
            output_dir = staging / ('shared-payload-' + uuid.uuid4().hex)
            output_dir.mkdir(mode=0o700)
            path = output_dir / 'payloads.db'
            if projection_mode:
                from .market_data_projection_bundle import export_projection_bundle
                manifest = export_projection_bundle(reader, hashes, path, transferred_ids=bodies,
                                                     authority_uuid=request['authority_uuid'],
                                                     receipt_after=request.get(cursor_key))
            elif scalar_mode:
                from .market_data_scalar_bundle import export_scalar_bundle
                manifest = export_scalar_bundle(reader, hashes, path, transferred_ids=bodies,
                                                 authority_uuid=request['authority_uuid'],
                                                 receipt_after=request.get(cursor_key))
            else:
                manifest = export_bundle(reader, hashes, path, payload_hashes=bodies,
                                         observation_after=request.get(cursor_key))
        identity = database.stat()
        response = {'bundle_path': str(path), 'manifest': manifest,
                    'source_identity': {'db_path': str(database), 'storage_root': str(root),
                                        'device': identity.st_dev, 'inode': identity.st_ino}}
        print(json.dumps(response, separators=(',', ':')))
        return 0
    except (OSError, ValueError, sqlite3.Error, StoreError) as exc:
        # Typed projection reads have a tighter aggregate byte cap than body
        # staging. The caller can safely split the selected IDs and retry.
        split_projection = args.command == 'export-projections' and isinstance(exc, StoreLimitError)
        code = 'BUNDLE_LIMIT' if isinstance(exc, BundleLimitError) or split_projection else type(exc).__name__
        print(json.dumps({'status': 'FAILED', 'error': code, 'message': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
