"""The complete SQLite -> public closure -> old reader path for mixed state."""
import gzip
import hashlib
import json
import sqlite3
import zlib

import pytest

from polybot_observability.market_data_apple import APPLICATION_ID,is_shared_frame
from polybot_observability.market_data_bundle import reference_closure,verify_closure
from polybot_observability.market_data_migrate import file_sha256,migrate_public_bodies
from polybot_observability.market_data_mixed import is_mixed_payload
from polybot_observability.market_data_refs import PayloadReferences,externalize_row
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadStore


def apple_frame():
    value={'format':'apple-filtered-frames-v1','markets':[{'market':{'id':'market','question':'public '*1000},'selection':'private-selection'}],
           'events':{},'books':{'token':{'book':{'asset_id':'token','bids':[{'price':'.9','size':'12'}],'asks':[]},'capacity':'private-capacity'}},
           'followups':[],'audit':'private-audit'}
    raw=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    return raw,zlib.compress(raw,6)


def test_apple_migration_pin_closure_and_native_bytes_remain_exact(tmp_path):
    raw,packed=apple_frame()
    source=tmp_path/'apple.db'
    c=sqlite3.connect(source)
    c.execute(f'PRAGMA application_id={APPLICATION_ID}')
    c.execute('PRAGMA user_version=1')
    c.execute('CREATE TABLE runs(run_id TEXT PRIMARY KEY,frame BLOB,frame_sha256 TEXT,frame_raw_bytes INTEGER)')
    c.execute('INSERT INTO runs VALUES(?,?,?,?)',('r1',packed,hashlib.sha256(raw).hexdigest(),len(raw)))
    c.commit();c.close()
    target=tmp_path/'apple-shared.db'
    with PayloadStore(tmp_path/'shared.db') as store:
        codec=PayloadReferences(store,store)
        manifest=migrate_public_bodies(source,target,strategy='golden-apple',source_sha256=file_sha256(source),references=codec)
        assert manifest['status']=='VERIFIED'
        raw_db=sqlite3.connect(target)
        assert is_shared_frame(raw_db.execute('SELECT frame FROM runs').fetchone()[0])
        raw_db.close()
        closure=reference_closure(target,'golden-apple')
        assert closure==manifest['payload_hashes']
        assert verify_closure(store,closure)['payload_count']==1
        decoded=connect(target,references=PayloadReferences(reader=store))
        row=decoded.execute('SELECT frame,frame_sha256 FROM runs').fetchone()
        assert row[0]==packed and hashlib.sha256(zlib.decompress(row[0])).hexdigest()==row[1]
        decoded.close()


def test_guava_mixed_envelope_reuses_raw_and_restores_original_lexemes(tmp_path):
    public={'id':'public','question':'public '*500}
    public_text=json.dumps(public,separators=(',',':'))
    text='{ "raw" : '+public_text+', "eligibility":false, "fee":{"private":3e+02} }'
    raw=gzip.compress(public_text.encode(),mtime=0)
    with PayloadStore(tmp_path/'shared.db') as store:
        codec=PayloadReferences(store,store)
        encoded=externalize_row('golden-guava','events',{'event_json':text,'raw_gzip':raw},references=codec)
        assert is_mixed_payload(encoded['event_json'])
        assert store.stats()['payload_count']==1
        path=tmp_path/'private.db'
        c=sqlite3.connect(path)
        c.execute('CREATE TABLE events(event_json TEXT,raw_gzip BLOB)')
        c.execute('INSERT INTO events VALUES(?,?)',(encoded['event_json'],encoded['raw_gzip']))
        c.commit();c.close()
        closure=reference_closure(path,'golden-guava')
        assert len(closure)==1
        decoded=connect(path,references=PayloadReferences(reader=store))
        assert decoded.execute('SELECT * FROM events').fetchone()==(text,raw)
        decoded.close()


@pytest.mark.parametrize('value',[b'\x1ePMDATA2:B:' + b'0'*64,b'\x1ePMAPPLEFRAME9:bad','\x1ePMMIX9:bad'])
def test_unknown_reference_format_cannot_be_pinned_as_inline(tmp_path,value):
    c=sqlite3.connect(tmp_path/'source.db')
    c.execute('CREATE TABLE requests(raw_gzip BLOB)')
    c.execute('INSERT INTO requests VALUES(?)',(value,))
    c.commit();c.close()
    with pytest.raises(ValueError):
        reference_closure(tmp_path/'source.db','golden-coconut')
