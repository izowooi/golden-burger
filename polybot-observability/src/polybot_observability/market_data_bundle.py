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

from .market_data_migrate import file_sha256, quote_identifier
from .market_data_policy import KNOWN_STRATEGIES, public_columns
from .market_data_refs import parse_reference


class BundleLimitError(ValueError):
    pass


def reference_closure(database: Path, strategy: str) -> list[str]:
    database = Path(database).resolve(strict=True)
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    try:
        hashes = set()
        for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            if strategy not in KNOWN_STRATEGIES:
                # An unclassified DB is not evidence of an empty dependency set.
                # Detect markers but never fetch possibly private data by guessing
                # another strategy's public-column policy.
                for info in connection.execute(f'PRAGMA table_info({quote_identifier(table)})'):
                    column=quote_identifier(info[1])
                    query=(f'SELECT {column} FROM {quote_identifier(table)} '
                           f'WHERE typeof({column}) IN (\'text\',\'blob\') AND length({column})=75')
                    if any(parse_reference(row[0]) for row in connection.execute(query)):
                        raise ValueError('shared references require a classified strategy identity')
            allowed = public_columns(strategy, table)
            if table == '_market_data_level_groups':
                from .market_data_levels import validate_level_layout
                validate_level_layout(connection)
                allowed = frozenset({'body_ref'})
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info({quote_identifier(table)})') if row[1] in allowed]
            if not columns:
                continue
            query = 'SELECT ' + ','.join(map(quote_identifier, columns)) + ' FROM ' + quote_identifier(table)
            for row in connection.execute(query):
                for value in row:
                    reference = parse_reference(value)
                    if reference:
                        hashes.add(reference[1])
        return sorted(hashes)
    finally:
        connection.close()


def closure_digest(hashes: list[str]) -> str:
    from .market_data_store import validate_hashes
    canonical = sorted(set(hashes))
    for start in range(0, len(canonical), 1024):
        validate_hashes(canonical[start:start+1024])
    return hashlib.sha256(('\n'.join(canonical) + ('\n' if canonical else '')).encode()).hexdigest()


def verify_closure(reader, hashes: list[str]) -> dict:
    digest = closure_digest(hashes)
    total = 0
    for sha in sorted(set(hashes)):
        values = reader.get_many([sha])
        if len(values) != 1 or hashlib.sha256(values[0]).hexdigest() != sha:
            raise ValueError('shared pin payload digest mismatch')
        total += len(values[0])
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


def export_bundle(reader, hashes: list[str], output: Path) -> dict:
    from .market_data_store import PayloadStore
    output = Path(output).absolute()
    closure = closure_digest(hashes)
    if output.exists() or output.is_symlink():
        raise ValueError('public bundle output already exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with PayloadStore(output) as bundle:
            for sha in sorted(set(hashes)):
                raw = reader.get_many([sha])
                if len(raw) != 1 or bundle.put_many(raw) != [sha]:
                    raise ValueError('public bundle payload mismatch')
            stats = bundle.stats()
        # Closing the only writer checkpoints the standalone file. Export is not
        # complete if data is still in a sidecar WAL.
        if Path(str(output) + '-wal').exists():
            raise ValueError('public bundle has an uncheckpointed WAL')
        manifest = {'contract': 'public-payload-bundle-v1', 'status': 'VERIFIED',
                    'file_sha256': file_sha256(output), 'closure_sha256': closure,
                    'payload_hashes': sorted(set(hashes)), **stats}
        output.with_suffix(output.suffix+'.manifest.json').write_text(json.dumps(manifest, indent=2))
        return manifest
    except BaseException:
        # Failed output remains visibly unverified for diagnosis. Never replace
        # an existing successful bundle or claim an incomplete export is usable.
        raise


def import_bundle(path: Path, manifest: dict, writer) -> dict:
    from .market_data_store import PayloadReader
    path = Path(path).resolve(strict=True)
    if manifest.get('contract') != 'public-payload-bundle-v1' or manifest.get('status') != 'VERIFIED':
        raise ValueError('unsupported or unverified public payload bundle')
    if file_sha256(path) != manifest['file_sha256']:
        raise ValueError('public payload bundle checksum mismatch')
    hashes = manifest['payload_hashes']
    if closure_digest(hashes) != manifest['closure_sha256']:
        raise ValueError('public payload bundle closure mismatch')
    connection = sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != {'payloads','observations'}:
            raise ValueError('public bundle contains unexpected tables')
    finally:
        connection.close()
    with PayloadReader(path) as reader:
        expected = len(set(hashes))
        if reader.stats()['observation_count'] != 0:
            raise ValueError('public payload bundle must not import observer state')
        if reader.stats()['payload_count'] != expected or manifest['payload_count'] != expected:
            raise ValueError('public bundle contains an unexpected payload set')
        for sha in sorted(set(hashes)):
            if writer.put_many(reader.get_many([sha])) != [sha]:
                raise ValueError('import writer acknowledgement mismatch')
    return {'imported_payload_count': len(set(hashes)), 'closure_sha256': manifest['closure_sha256']}


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
    from .market_data_store import PayloadReader, StoreError, validate_hashes
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export')
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
        data = sys.stdin.buffer.read(131073)
        if len(data) > 131072:
            raise ValueError('shared bundle request exceeds byte limit')
        request = json.loads(data)
        if not isinstance(request, dict) or set(request) != {'hashes'}:
            raise ValueError('shared bundle request must contain only hashes')
        hashes = request['hashes']
        validate_hashes(hashes)
        with PayloadReader(database) as reader:
            # Bound staging before materializing any bodies. Only the public
            # store's schema is accessed; there is no user-supplied SQL.
            sizes = []
            c = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)
            try:
                for sha in set(hashes):
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
            manifest = export_bundle(reader, hashes, path)
        identity = database.stat()
        response = {'bundle_path': str(path), 'manifest': manifest,
                    'source_identity': {'db_path': str(database), 'storage_root': str(root),
                                        'device': identity.st_dev, 'inode': identity.st_ino}}
        print(json.dumps(response, separators=(',', ':')))
        return 0
    except (OSError, ValueError, sqlite3.Error, StoreError) as exc:
        code = 'BUNDLE_LIMIT' if isinstance(exc, BundleLimitError) else type(exc).__name__
        print(json.dumps({'status': 'FAILED', 'error': code, 'message': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
