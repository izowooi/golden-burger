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
    monkeypatch.setattr(migration, 'externalize_row', classified)
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
