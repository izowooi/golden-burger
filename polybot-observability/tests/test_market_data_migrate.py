import hashlib
import json
import sqlite3

import pytest

from polybot_observability import market_data_migrate as migration
from polybot_observability.market_data_refs import PayloadReferences, parse_reference
from polybot_observability.market_data_sqlite import connect


class MemoryStore:
    def __init__(self):
        self.values = {}

    def put_many(self, values):
        hashes = [hashlib.sha256(value).hexdigest() for value in values]
        self.values.update(zip(hashes, values))
        return hashes

    def get_many(self, hashes):
        return [self.values[digest] for digest in hashes]


@pytest.fixture
def example(tmp_path, monkeypatch):
    from polybot_observability import market_data_policy
    source = tmp_path / 'source.db'
    con = sqlite3.connect(source)
    con.executescript('''
      CREATE TABLE observations(id INTEGER PRIMARY KEY AUTOINCREMENT,raw TEXT,body BLOB);
      CREATE TABLE trades(id INTEGER PRIMARY KEY,cash REAL,secret TEXT);
      CREATE TABLE rowids(payload TEXT);
      CREATE TABLE compound(a TEXT,b TEXT,PRIMARY KEY(a,b)) WITHOUT ROWID;
      INSERT INTO observations VALUES(7,'event body',X'1F8BFF');
      INSERT INTO trades VALUES(4,5.25,'private attribution');
      INSERT INTO rowids(rowid,payload) VALUES(19,'rowid matters');
      INSERT INTO compound VALUES('a','b');
      CREATE INDEX observations_raw_idx ON observations(id);
      CREATE TRIGGER keep_raw BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT,'immutable'); END;
      PRAGMA application_id=1234;
      PRAGMA user_version=7;
    ''')
    con.close()
    store = MemoryStore()
    codec = PayloadReferences(store, store)
    def classified(strategy, table, row, *, references):
        assert strategy == 'example'
        if table == 'observations':
            values = references.encode_many([row['raw'], row['body']])
            return {**row, 'raw': values[0], 'body': values[1]}
        return row
    monkeypatch.setattr(migration, 'externalize_rows', lambda strategy,table,rows,*,references:
                        [classified(strategy,table,row,references=references) for row in rows])
    original_public_columns = market_data_policy.public_columns
    monkeypatch.setattr(market_data_policy, 'public_columns',
                        lambda strategy, table: frozenset({'raw', 'body'})
                        if (strategy, table) == ('example', 'observations')
                        else original_public_columns(strategy, table))
    return source, codec, store


def test_derivative_preserves_ledger_schema_rowids_triggers_and_exact_bytes(tmp_path, example):
    source, codec, store = example
    sha = migration.file_sha256(source)
    destination = tmp_path / 'derivative.db'
    result = migration.migrate_public_bodies(source, destination, strategy='example', source_sha256=sha, references=codec)
    assert result['status'] == 'VERIFIED'
    assert migration.file_sha256(source) == sha
    assert result['tables']['observations']['externalized_cells'] == 2
    assert result['tables']['trades']['externalized_cells'] == 0
    raw = sqlite3.connect(destination)
    assert parse_reference(raw.execute('SELECT raw FROM observations').fetchone()[0])
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        raw.execute("UPDATE observations SET raw='tamper'")
    raw.close()
    decoded = connect(destination, references=PayloadReferences(reader=store))
    assert decoded.execute('SELECT * FROM observations').fetchall() == [(7, 'event body', b'\x1f\x8b\xff')]
    assert decoded.execute('SELECT * FROM trades').fetchall() == [(4, 5.25, 'private attribution')]
    assert decoded.execute('SELECT rowid,* FROM rowids').fetchall() == [(19, 'rowid matters')]
    assert decoded.execute('PRAGMA application_id').fetchone()[0] == 1234
    assert decoded.execute('PRAGMA user_version').fetchone()[0] == 7
    decoded.close()


def test_bad_source_checksum_does_not_create_derivative(tmp_path, example):
    source, codec, _ = example
    destination = tmp_path / 'not_created.db'
    with pytest.raises(ValueError, match='checksum'):
        migration.migrate_public_bodies(source, destination, strategy='example', source_sha256='0'*64, references=codec)
    assert not destination.exists()


def test_failed_verification_preserves_source_and_marks_derivative_failed(tmp_path, example):
    source, codec, store = example
    sha = migration.file_sha256(source)
    destination = tmp_path / 'failed.db'
    store.get_many = lambda hashes: [b'wrong' for _ in hashes]
    with pytest.raises(ValueError, match='digest'):
        migration.migrate_public_bodies(source, destination, strategy='example', source_sha256=sha, references=codec)
    assert migration.file_sha256(source) == sha
    status = json.loads(destination.with_suffix('.db.migration.json').read_text())
    assert status['status'] == 'FAILED'


def test_existing_destination_never_overwritten(tmp_path, example):
    source, codec, _ = example
    destination = tmp_path / 'existing.db'
    destination.write_bytes(b'previous evidence')
    with pytest.raises(ValueError, match='new distinct'):
        migration.migrate_public_bodies(source, destination, strategy='example', source_sha256=migration.file_sha256(source), references=codec)
    assert destination.read_bytes() == b'previous evidence'


def test_migrates_a_mixed_inline_and_shared_body_snapshot(tmp_path,example):
    source,codec,store=example
    first=tmp_path/'first.db'
    migration.migrate_public_bodies(source,first,strategy='example',source_sha256=migration.file_sha256(source),references=codec)
    # A running collector can append references after deployment while older
    # rows remain inline. A later compaction must hash decoded logical values.
    c=sqlite3.connect(first)
    c.execute("INSERT INTO observations(raw,body) VALUES('new inline',X'0A0B')")
    c.commit();c.close()
    second=tmp_path/'second.db'
    result=migration.migrate_public_bodies(first,second,strategy='example',source_sha256=migration.file_sha256(first),references=codec)
    assert result['status']=='VERIFIED'
    c=connect(second,references=PayloadReferences(reader=store))
    assert c.execute('SELECT raw,body FROM observations ORDER BY id').fetchall()==[('event body',b'\x1f\x8b\xff'),('new inline',b'\x0a\x0b')]
    c.close()


def test_level_migration_removes_duplicate_numeric_rows_and_preserves_primary_keys(tmp_path):
    from polybot_observability.market_data_store import PayloadStore
    source=tmp_path/'original-levels.db'
    c=sqlite3.connect(source)
    c.executescript('''
      CREATE TABLE orderbook_snapshots(snapshot_id TEXT PRIMARY KEY,token_id TEXT);
      CREATE TABLE orderbook_levels(level_id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),side TEXT CHECK(side IN ('BID','ASK')),level_index INTEGER,price REAL,size REAL,UNIQUE(snapshot_id,side,level_index));
      INSERT INTO orderbook_snapshots VALUES('s1','token'),('s2','token');
      INSERT INTO orderbook_levels VALUES('a1','s1','ASK',0,.7,20),('b1','s1','BID',0,.69,30),('a2','s2','ASK',0,.7,20),('b2','s2','BID',0,.69,30);
      CREATE TRIGGER no_delete BEFORE DELETE ON orderbook_levels BEGIN SELECT RAISE(ABORT,'append-only');END;
    ''')
    expected=c.execute('SELECT * FROM orderbook_levels ORDER BY level_id').fetchall()
    c.close()
    original_sha=migration.file_sha256(source)
    destination=tmp_path/'level-refs.db'
    with PayloadStore(tmp_path/'public.db') as shared:
        codec=PayloadReferences(reader=shared,writer=shared)
        result=migration.migrate_public_bodies(source,destination,strategy='golden-black',source_sha256=original_sha,references=codec,include_levels=True)
        assert result['status']=='VERIFIED'
        assert result['tables']['orderbook_levels']['externalized_level_rows']==4
        assert shared.stats()['payload_count']==1
        decoded=connect(destination,references=PayloadReferences(reader=shared))
        assert decoded.execute('SELECT COUNT(*) FROM main.orderbook_levels').fetchone()[0]==0
        assert decoded.execute('SELECT * FROM orderbook_levels ORDER BY level_id').fetchall()==expected
        decoded.close()
    assert migration.file_sha256(source)==original_sha


def test_compacts_existing_shared_levels_and_legacy_inline_rows(tmp_path):
    from polybot_observability.market_data_levels import insert_shared_levels
    source = tmp_path / 'mixed-levels.db'
    store = MemoryStore()
    codec = PayloadReferences(reader=store, writer=store)
    c = sqlite3.connect(source)
    c.executescript('''
      CREATE TABLE orderbook_snapshots(snapshot_id TEXT PRIMARY KEY,token_id TEXT);
      CREATE TABLE orderbook_levels(level_id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),side TEXT CHECK(side IN ('BID','ASK')),level_index INTEGER,price REAL,size REAL,UNIQUE(snapshot_id,side,level_index));
      INSERT INTO orderbook_snapshots VALUES('s1','token'),('s2','token');
      INSERT INTO orderbook_levels VALUES('old','s1','ASK',0,.7,20);
    ''')
    c.execute('BEGIN')
    insert_shared_levels(c, 'golden-black', 'orderbook_levels', [{
        'level_id': 'new', 'snapshot_id': 's2', 'side': 'ASK',
        'level_index': 0, 'price': .71, 'size': 30.0,
    }], references=codec)
    c.commit()
    c.close()
    source_sha = migration.file_sha256(source)
    before = connect(source, references=PayloadReferences(reader=store))
    expected = before.execute('SELECT * FROM orderbook_levels ORDER BY level_id').fetchall()
    before.close()
    target = tmp_path / 'compacted.db'
    result = migration.migrate_public_bodies(
        source, target, strategy='golden-black', source_sha256=source_sha,
        references=codec, include_levels=True,
    )
    assert result['status'] == 'VERIFIED'
    assert result['tables']['orderbook_levels']['rows'] == 2
    after = connect(target, references=PayloadReferences(reader=store))
    assert after.execute('SELECT * FROM orderbook_levels ORDER BY level_id').fetchall() == expected
    assert after.execute('SELECT COUNT(*) FROM main.orderbook_levels').fetchone() == (0,)
    after.close()
    assert migration.file_sha256(source) == source_sha
    with pytest.raises(ValueError, match='matching strategy'):
        migration.migrate_public_bodies(
            source, tmp_path/'body-only.db', strategy='golden-black',
            source_sha256=source_sha, references=codec,
        )


def test_explicit_source_receipts_backfill_without_using_migration_clock(tmp_path, monkeypatch):
    import gzip
    from polybot_observability.market_data_index import ReceiptContext
    from polybot_observability.market_data_store import PayloadStore
    source = tmp_path / 'historical.db'
    original_receipt = '2026-09-01T01:02:03Z'
    raw = gzip.compress(b'{"asset_id":"token-1","bids":[{"price":".71","size":"20"}]}',mtime=0)
    c=sqlite3.connect(source)
    c.execute('CREATE TABLE raw_payloads(payload_id TEXT PRIMARY KEY,observed_at TEXT,payload_gzip BLOB)')
    c.executemany('INSERT INTO raw_payloads VALUES(?,?,?)', [('p1',original_receipt,raw),('p2',None,raw)])
    c.commit();c.close()
    monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE', 'wrong-live-source')
    monkeypatch.setenv('JOB_NAME', 'wrong-live-job')
    with PayloadStore(tmp_path/'public.db') as shared:
        result=migration.migrate_public_bodies(
            source,tmp_path/'historical-refs.db',strategy='golden-black',
            source_sha256=migration.file_sha256(source),
            references=PayloadReferences(reader=shared,writer=shared),
            receipt_context=ReceiptContext('archived-source','black-epoch','archived-job'),
        )
        receipts=list(shared.iter_observations(token_id='token-1'))
        assert len(receipts)==1
        assert receipts[0].observed_at==original_receipt
        assert receipts[0].observer=='archived-source/golden-black/black-epoch'
        assert json.loads(receipts[0].metadata_json)['job_name']=='archived-job'
        assert result['public_receipt_index']['gap_counts']=={'raw_payloads:missing_original_receipt_time':1}
        assert shared.get_many([receipts[0].payload_sha])==[raw]


def test_apple_legacy_public_pool_is_split_by_actual_reference_roles(tmp_path):
    import zlib
    from polybot_observability.market_data_store import PayloadStore
    source=tmp_path/'apple-legacy.db'
    c=sqlite3.connect(source)
    c.executescript('''
      CREATE TABLE payloads(hash TEXT PRIMARY KEY,encoding TEXT,bytes INTEGER,compressed BLOB);
      CREATE TABLE events(event_id TEXT PRIMARY KEY,payload_hash TEXT REFERENCES payloads(hash));
      CREATE TABLE book_observations(depth_hash TEXT REFERENCES payloads(hash),metrics_hash TEXT REFERENCES payloads(hash));
    ''')
    raws=[b'{"id":"public-event"}',b'{"private_capacity":5}',b'{"unknown":1}']
    hashes=[hashlib.sha256(raw).hexdigest() for raw in raws]
    blobs=[zlib.compress(raw) for raw in raws]
    c.executemany('INSERT INTO payloads VALUES(?,?,?,?)',[(h,'zlib',len(raw),blob) for h,raw,blob in zip(hashes,raws,blobs)])
    c.execute('INSERT INTO events VALUES(?,?)',('event',hashes[0]))
    c.execute('INSERT INTO book_observations VALUES(?,?)',(None,hashes[1]))
    c.commit();c.close()
    with PayloadStore(tmp_path/'shared.db') as store:
        result=migration.migrate_public_bodies(source,tmp_path/'split.db',strategy='golden-apple',
                   source_sha256=migration.file_sha256(source),references=PayloadReferences(reader=store,writer=store))
        assert result['tables']['payloads']['externalized_cells']==1
        c=sqlite3.connect(tmp_path/'split.db')
        cells=dict(c.execute('SELECT hash,compressed FROM payloads'));c.close()
        assert parse_reference(cells[hashes[0]])
        assert cells[hashes[1]]==blobs[1] and cells[hashes[2]]==blobs[2]
        assert store.stats()['payload_count']==1
        restored=connect(tmp_path/'split.db',references=PayloadReferences(reader=store))
        assert dict(restored.execute('SELECT hash,compressed FROM payloads'))==dict(zip(hashes,blobs))
        restored.close()


@pytest.mark.parametrize('damage', ['unknown_table', 'table_mismatch', 'missing_ordinal', 'extra_ordinal'])
def test_existing_level_links_cannot_disappear_from_verified_migration(tmp_path, damage):
    from polybot_observability.market_data_levels import insert_shared_levels
    source=tmp_path/'broken-links.db';store=MemoryStore();codec=PayloadReferences(store,store)
    c=sqlite3.connect(source)
    c.executescript('''
      CREATE TABLE orderbook_snapshots(snapshot_id TEXT PRIMARY KEY,token_id TEXT);
      INSERT INTO orderbook_snapshots VALUES('s','token');
      CREATE TABLE orderbook_levels(level_id TEXT PRIMARY KEY,snapshot_id TEXT,side TEXT,level_index INTEGER,price REAL,size REAL);
    ''')
    c.execute('BEGIN')
    insert_shared_levels(c,'golden-black','orderbook_levels',[
        dict(level_id='a',snapshot_id='s',side='ASK',level_index=0,price=.7,size=20),
        dict(level_id='b',snapshot_id='s',side='BID',level_index=0,price=.6,size=30),
    ],references=codec)
    c.commit()
    if damage=='unknown_table':
        c.execute("UPDATE _market_data_level_groups SET table_name='unsupported_levels'")
        c.execute("UPDATE _market_data_level_bindings SET table_name='unsupported_levels'")
    elif damage=='table_mismatch':
        c.execute("UPDATE _market_data_level_bindings SET table_name='unsupported_levels'")
    elif damage=='missing_ordinal':
        c.execute('DELETE FROM _market_data_level_bindings WHERE ordinal=0')
    else:
        c.execute('UPDATE _market_data_level_bindings SET ordinal=9 WHERE ordinal=0')
    c.commit();c.close();sha=migration.file_sha256(source)
    with pytest.raises(ValueError,match='source public level'):
        migration.migrate_public_bodies(source,tmp_path/'failed.db',strategy='golden-black',
            source_sha256=sha,references=codec,include_levels=True)
    assert migration.file_sha256(source)==sha
    manifest=json.loads((tmp_path/'failed.db.migration.json').read_text())
    assert manifest['status']=='FAILED'


def test_capacity_change_stops_migration_and_preserves_source(tmp_path, example):
    source,codec,_=example
    original_sha=migration.file_sha256(source)
    calls=0
    def guard():
        nonlocal calls
        calls+=1
        if calls==3:
            raise RuntimeError('capacity depleted during migration')
    destination=tmp_path/'partial.db'
    with pytest.raises(RuntimeError,match='capacity depleted'):
        migration.migrate_public_bodies(source,destination,strategy='example',
            source_sha256=original_sha,references=codec,batch_rows=1,storage_guard=guard)
    assert migration.file_sha256(source)==original_sha
    assert json.loads(destination.with_suffix('.db.migration.json').read_text())['status']=='FAILED'


def test_migration_guard_checks_current_space_and_root_identity(tmp_path,monkeypatch):
    from types import SimpleNamespace
    root=tmp_path/'volume';root.mkdir()
    space=SimpleNamespace(f_blocks=1000,f_frsize=1,f_bfree=200,f_bavail=200)
    monkeypatch.setattr(migration.os,'statvfs',lambda path:space)
    guard=migration.migration_storage_guard(root,minimum_free_bytes=50,maximum_used_ratio=.90)
    space.f_bfree=99
    with pytest.raises(RuntimeError,match='capacity gate'):
        guard()
    space.f_bfree=200;space.f_bavail=49
    with pytest.raises(RuntimeError,match='capacity gate'):
        guard()
    space.f_bavail=200;guard()
    root.rename(tmp_path/'old-volume');root.mkdir()
    with pytest.raises(RuntimeError,match='identity changed'):
        guard()
