import json
import sqlite3
import subprocess
import sys
import uuid

import pytest

from polybot_observability.market_data_projection_profiles import projection_profile
from polybot_observability.market_data_projections import (
    PublicProjection, ProjectionReference, ProjectionRecord, ProjectionReceipt,
    MissingProjectionRecordError, CorruptProjectionError, ProjectionRecordConflictError,
    PROJECTION_TRIGGER_SQL, PROJECTION_TABLE_SQL, projection_hot_sha,
)
from polybot_observability.market_data_scalars import ScalarSnapshot, ScalarAuthorityError, SCALAR_TRIGGER_SQL
from polybot_observability.market_data_store import PayloadStore, PayloadReader, StoreLimitError


def quote(i=0, *, clock=None, condition='condition', probability=.71):
    return PublicProjection('gamma-quote-v1',(condition,probability,120.0,None,.70,.72,.02,str(i) if clock is None else clock))


def token(i=0, *, token_id='123', source_time=None):
    return PublicProjection('token-quote-v1',('condition',token_id,'Yes',120.0,None,.70,.72,.02,source_time))


def catalog(i=0, *, text=' [ "Yes" , "No" ]\n'):
    profile=projection_profile('kiwi-catalog-v1')
    values=[]
    for field in profile.fields:
        if field.name=='condition_id': values.append('condition')
        elif field.name=='catalog_event_market_count': values.append(i)
        elif field.storage=='INTEGER': values.append(None if i%2 else 2)
        elif field.name.endswith('_json'): values.append(text)
        elif field.name=='catalog_source_updated_at': values.append(None)
        else: values.append('')
    return PublicProjection(profile.kind,tuple(values))


def get(store,refs):
    return store.get_projection_records([ref.record_id for ref in refs],refs[0].authority_uuid)


def test_exact_registered_fields_wire_lexical_json_null_sourceclock_and_types():
    for projection in (quote(),token(token_id=None),catalog(),quote(probability=float('inf'))):
        encoded=json.loads(json.dumps(projection.to_wire(),allow_nan=False))
        assert PublicProjection.from_wire(encoded)==projection
    assert catalog(text='[]').sha256!=catalog(text=' [] ').sha256
    assert quote(probability=-0.0)==quote(probability=0.0)
    with pytest.raises(ValueError): PublicProjection('unknown-kind',())
    with pytest.raises(ValueError): PublicProjection('gamma-quote-v1',quote().values+('private-run-id',))
    with pytest.raises(ValueError): PublicProjection.from_wire({**quote().to_wire(),'run_id':'private'})
    with pytest.raises(ValueError): quote(probability=float('nan'))
    with pytest.raises(ValueError): quote(probability=1)
    fields=list(catalog().values);fields[3]=True
    with pytest.raises(ValueError): PublicProjection('kiwi-catalog-v1',tuple(fields))


def test_direct_full_ack_holds_private_timestamp_outside_public_data(tmp_path):
    path=tmp_path/'public.db'
    with PayloadStore(path) as writer,PayloadReader(path) as reader:
        authority=writer.scalar_authority_identity()
        assert writer.scalar_authority_role()=='UNCLAIMED'
        assert writer.put_public_projections([])==[]
        assert writer.scalar_authority_role()=='UNCLAIMED'
        refs=writer.put_public_projections([quote(),quote(),token(token_id=None),catalog()])
        assert refs[0]==refs[1]
        assert [record.projection for record in get(reader,refs)]==[quote(),quote(),token(token_id=None),catalog()]
        assert writer.scalar_authority_role()=='ORIGIN'
        assert all(ref.authority_uuid==authority for ref in refs)
        assert writer._connection.execute('SELECT source_time FROM projection_records WHERE id=?',(refs[2].record_id,)).fetchone()==(None,)
        assert writer._connection.execute('PRAGMA synchronous').fetchone()[0]==2
        assert writer._connection.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
        assert writer.stats()['payload_count']==0
    with PayloadReader(path) as reader:
        assert get(reader,refs)[0].reference==refs[0]


def test_per_kind_hot_to_cold_and_reopen_idempotence(tmp_path):
    path=tmp_path/'public.db'
    values=[quote(i) for i in range(1024)]
    with PayloadStore(path) as store:
        refs=store.put_public_projections(values[:1023])
        other=store.put_public_projections([catalog()])[0]
        assert store.projection_stats()['hot_count']==1024
        last=store.put_public_projections(values[1023:])[0]
        assert store.projection_stats()['block_count']==1
        assert store.projection_stats()['hot_count']==1
        assert [row.projection for row in get(store,[*refs,last])]==values
        assert store.put_public_projections([values[0]])==[refs[0]]
        assert get(store,[other])[0].projection==catalog()
    with PayloadStore(path) as reopened:
        assert reopened.put_public_projections([values[-1]])==[last]
        assert reopened._projection_state._mode==''


def test_quote_and_catalog_dedup_independently(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        c=store.put_public_projections([catalog()])[0]
        refs=store.put_public_projections([quote(1),quote(2),catalog()])
        assert refs[2]==c
        assert store.projection_stats()['record_count']==3
        assert store.projection_stats()['kind_count']==2


def test_source_clock_query_is_exact_text_range_and_token_indexed(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        values=[token(source_time=None),token(source_time='2026-01-01T00:00:00+09:00'),token(source_time='2025-12-31T23:00:00Z'),token(token_id='different',source_time='x')]
        refs=store.put_public_projections(values);authority=store.scalar_authority_identity()
        actual=list(store.query_projection_records(authority_uuid=authority,token_id='123',start='2026',end='2027'))
        assert [row.projection for row in actual]==[values[1]]
        all_rows=list(store.query_projection_records(authority_uuid=authority,condition_id='condition',kind='token-quote-v1'))
        assert [row.projection for row in all_rows]==[values[0],values[2],values[1],values[3]]
        plan=store._connection.execute('EXPLAIN QUERY PLAN SELECT id FROM projection_records WHERE token_key=1 AND source_time_text>=?',('2026',)).fetchall()
        assert any('projection_records_token_time' in row[3] for row in plan)
        with pytest.raises(ValueError,match='indexed condition'):list(store.query_projection_records(authority_uuid=authority))
        with pytest.raises(ValueError,match='raw bounded TEXT'):list(store.query_projection_records(authority_uuid=authority,token_id='123',start=1))


def test_receipts_bind_table_original_id_kind_and_record_id(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_public_projections([quote(1),quote(2),catalog()]);authority=refs[0].authority_uuid
        rows=[ProjectionReceipt('namespace','market_snapshots',1,ref.kind,ref.record_id) for ref in refs]
        catalog_receipt=ProjectionReceipt('namespace','market_catalog',1,refs[2].kind,refs[2].record_id)
        store.append_projection_receipts([*rows,catalog_receipt,rows[0]],authority_uuid=authority)
        assert store.projection_stats()['receipt_count']==4
        assert store.get_projection_receipts([row.row() for row in rows],authority_uuid=authority)==rows
        pages=[];after=None
        while page:=list(store.iter_projection_receipts(authority_uuid=authority,after=after,limit=2)):
            pages.extend(page);after=page[-1].row()
        assert pages==sorted([*rows,catalog_receipt],key=lambda row:row.row())
        wrong=ProjectionReceipt('namespace','market_snapshots',99,'gamma-quote-v1',refs[2].record_id)
        with pytest.raises(ProjectionRecordConflictError):store.append_projection_receipts([wrong],authority_uuid=authority)
        with pytest.raises(MissingProjectionRecordError):store.append_projection_receipts([ProjectionReceipt('namespace','market_snapshots',1,'gamma-quote-v1',999)],authority_uuid=authority)


def test_shared_authority_scalar_projection_origin_and_replica(tmp_path):
    with PayloadStore(tmp_path/'origin.db') as origin,PayloadStore(tmp_path/'replica.db') as replica:
        scalar=origin.put_scalar_snapshots([ScalarSnapshot('s',.5,None,None,'runtime')])[0]
        refs=origin.put_public_projections([quote(),catalog()]);authority=scalar.authority_uuid
        assert all(ref.authority_uuid==authority for ref in refs)
        assert scalar.record_id==refs[0].record_id==1  # Separate typed identity spaces.
        replica.put_many([b'body CAS can preexist'])
        replica.import_projection_records(authority,get(origin,refs))
        replica.import_scalar_records(authority,origin.get_scalar_records([scalar.record_id],authority))
        assert get(replica,refs)==get(origin,refs)
        assert replica.scalar_authority_role()=='REPLICA'
        with pytest.raises(ScalarAuthorityError):replica.put_public_projections([quote(999)])
        with pytest.raises(ScalarAuthorityError):replica.put_scalar_snapshots([ScalarSnapshot('s',.6,None,None,'runtime')])
        with pytest.raises(ScalarAuthorityError):origin.import_projection_records(authority,[])
        with pytest.raises(ScalarAuthorityError):replica.import_projection_records(str(uuid.uuid4()),[])


def test_projection_data_trigger_blocks_scalar_empty_import_rebinding_corrupted_unclaimed_role(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        ref=store.put_public_projections([quote()])[0]
        db=store._connection
        db.execute('DROP TRIGGER scalar_authority_claim');db.execute('DROP TRIGGER projection_authority_no_rebind')
        db.execute("UPDATE scalar_authority SET role='UNCLAIMED'")
        db.execute(SCALAR_TRIGGER_SQL['scalar_authority_claim']);db.execute(PROJECTION_TRIGGER_SQL['projection_authority_no_rebind'])
        with pytest.raises(sqlite3.IntegrityError,match='projection data already bind'):
            store.import_scalar_records(str(uuid.uuid4()),[])
        assert store.scalar_authority_identity()==ref.authority_uuid
        assert store._scalar_state._mode==store._projection_state._mode==''


def test_empty_projection_import_binds_replica_forever(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        authority=str(uuid.uuid4());store.import_projection_records(authority,[])
        store.import_scalar_records(authority,[])
        assert store.scalar_authority_role()=='REPLICA'
        with pytest.raises(ScalarAuthorityError):store.import_projection_records(str(uuid.uuid4()),[])


def test_replica_conflicting_id_or_kind_rolls_back_all_new_records(tmp_path):
    authority=str(uuid.uuid4())
    a=ProjectionRecord(ProjectionReference(authority,'gamma-quote-v1',20,quote().sha256),quote())
    b=ProjectionRecord(ProjectionReference(authority,'kiwi-catalog-v1',20,catalog().sha256),catalog())
    with PayloadStore(tmp_path/'public.db') as store:
        original=store.scalar_authority_identity()
        with pytest.raises(ProjectionRecordConflictError):store.import_projection_records(authority,[a,b])
        assert store.scalar_authority_identity()==original
        assert store.scalar_authority_role()=='UNCLAIMED'
        assert store.projection_stats()['record_count']==0
        assert store._projection_state._mode==''


def test_kind_and_receipt_stream_dictionary_ids_are_replica_local(tmp_path):
    with PayloadStore(tmp_path/'origin.db') as origin,PayloadStore(tmp_path/'replica.db') as replica:
        refs=origin.put_public_projections([quote(),catalog()]);authority=refs[0].authority_uuid
        source_records=get(origin,refs)
        replica.import_projection_records(authority,source_records[::-1])
        assert get(replica,refs)==source_records
        source_keys=origin._connection.execute('SELECT id,kind_key FROM projection_records ORDER BY id').fetchall()
        replica_keys=replica._connection.execute('SELECT id,kind_key FROM projection_records ORDER BY id').fetchall()
        assert source_keys==[(1,1),(2,2)] and replica_keys==[(1,2),(2,1)]
        rows=[ProjectionReceipt('source','market_snapshots',10,ref.kind,ref.record_id) for ref in refs]
        origin.append_projection_receipts(rows,authority_uuid=authority)
        replica.append_projection_receipts(rows[::-1],authority_uuid=authority)
        assert replica.get_projection_receipts([row.row() for row in rows],authority_uuid=authority)==rows
        assert replica.projection_stats()['stream_count']==2


def test_namespace_query_does_not_accept_corrupt_wrong_kind_receipt_stream(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_public_projections([quote(),catalog()]);authority=refs[0].authority_uuid
        receipt=ProjectionReceipt('source','market_snapshots',1,refs[0].kind,refs[0].record_id)
        store.append_projection_receipts([receipt],authority_uuid=authority)
        store._connection.execute('DROP TRIGGER projection_receipt_streams_no_update')
        store._connection.execute("UPDATE projection_receipt_streams SET kind_key=(SELECT id FROM projection_kinds WHERE kind='kiwi-catalog-v1')")
        assert list(store.query_projection_records(authority_uuid=authority,condition_id='condition',kind='gamma-quote-v1',namespace='source'))==[]
        with pytest.raises(CorruptProjectionError):list(store.iter_projection_receipts(authority_uuid=authority,namespace='source'))


def test_hot_id_kind_content_binding_survives_restored_trigger_schema(tmp_path):
    path=tmp_path/'public.db'
    with PayloadStore(path) as store:
        refs=store.put_public_projections([quote(probability=.71),quote(probability=.72)])
        rows=store._connection.execute('SELECT record_id,cells,row_sha FROM projection_hot ORDER BY record_id').fetchall()
        store._connection.execute('DROP TRIGGER projection_hot_no_update')
        for target,source in ((rows[0],rows[1]),(rows[1],rows[0])):
            store._connection.execute('UPDATE projection_hot SET cells=?,row_sha=? WHERE record_id=?',(*source[1:],target[0]))
        store._connection.execute(PROJECTION_TRIGGER_SQL['projection_hot_no_update'])
    with PayloadReader(path) as reader:
        with pytest.raises(CorruptProjectionError):get(reader,refs)


@pytest.mark.parametrize('mutation,trigger',[
    ("UPDATE projection_records SET source_time='tampered' WHERE id=2",'projection_records_pack'),
    ("UPDATE projection_records SET token_key=NULL WHERE id=2",'projection_records_pack'),
    ("UPDATE scalar_conditions SET condition_id='tampered'",'scalar_conditions_no_update'),
    ("UPDATE projection_tokens SET token_id='tampered'",'projection_tokens_no_update'),
    ("UPDATE projection_blocks SET body=x'00'",'projection_blocks_no_update'),
])
def test_cold_cache_rechecks_all_index_metadata_and_body(tmp_path,mutation,trigger):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_public_projections([token(source_time=str(i)) for i in range(1024)])
        get(store,refs[:1]);assert store._projection_state.cache_bytes>0
        store._connection.execute('DROP TRIGGER '+trigger);store._connection.execute(mutation)
        with pytest.raises(CorruptProjectionError):get(store,refs[:1])


def test_large_catalog_values_pack_on_bytes_and_preserve_lexical_bytes(tmp_path,monkeypatch):
    from polybot_observability import market_data_projection_store as backend
    monkeypatch.setattr(backend,'PROJECTION_PACK_TARGET_BYTES',8000)
    values=[catalog(i,text='["'+('data '*500)+'"]\n') for i in range(5)]
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_public_projections(values)
        assert store.projection_stats()['block_count']==1
        assert store.projection_stats()['hot_count']==0
        assert [row.projection for row in get(store,refs)]==values


def test_pack_failure_rolls_back_every_new_pointer_hot_row_and_claim_state(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_public_projections([quote(i) for i in range(1023)])
        store._connection.execute("CREATE TRIGGER fail_projection_pack BEFORE DELETE ON projection_hot WHEN OLD.record_id=2 BEGIN SELECT RAISE(ABORT,'pack failure'); END")
        with pytest.raises(sqlite3.IntegrityError,match='pack failure'):store.put_public_projections([quote(1023)])
        assert store.projection_stats()['record_count']==store.projection_stats()['hot_count']==1023
        assert store.projection_stats()['block_count']==0
        assert store._projection_state._mode=='' and not store._connection.in_transaction
        store._connection.execute('DROP TRIGGER fail_projection_pack')
        store.put_public_projections([quote(1023)])
        assert get(store,refs[:1])[0].projection==quote(0)


@pytest.mark.parametrize('when',['during_pack','after_commit'])
def test_process_death_preserves_all_acknowledged_hot_projection_rows(tmp_path,when):
    path=tmp_path/'public.db'
    with PayloadStore(path) as store:refs=store.put_public_projections([quote(i) for i in range(1023)])
    program='''import os,sys
from polybot_observability.market_data_store import PayloadStore
from polybot_observability.market_data_projections import PublicProjection
s=PayloadStore(sys.argv[1])
if sys.argv[2]=='during_pack':
 s._connection.create_function('die',0,lambda:os._exit(31))
 s._connection.execute("CREATE TRIGGER projection_die AFTER UPDATE OF block_id ON projection_records BEGIN SELECT die(); END")
s.put_public_projections([PublicProjection('gamma-quote-v1',('condition',.71,120.0,None,.70,.72,.02,'1023'))])
os._exit(32)
'''
    result=subprocess.run([sys.executable,'-c',program,str(path),when],capture_output=True,text=True)
    assert result.returncode==(31 if when=='during_pack' else 32),result.stderr
    with PayloadStore(path) as store:
        assert [row.projection for row in get(store,refs)]==[quote(i) for i in range(1023)]
        if when=='during_pack':store._connection.execute('DROP TRIGGER projection_die')
        last=store.put_public_projections([quote(1023)])[0]
        assert last.record_id==1024 and store.projection_stats()['record_count']==1024


def test_transport_receipt_allows_only_explicit_omitted_body_replica_transaction(tmp_path):
    from polybot_observability.market_data_projection_store import insert_transport_projection_receipt
    authority=str(uuid.uuid4());receipt=ProjectionReceipt('source','market_snapshots',1,'gamma-quote-v1',999)
    with PayloadStore(tmp_path/'bundle.db') as store:
        store.import_projection_records(authority,[]);db=store._connection
        with pytest.raises(ValueError):insert_transport_projection_receipt(db,receipt,authority_uuid=authority,allow_missing_records=True)
        db.execute('PRAGMA foreign_keys=OFF');db.execute('BEGIN')
        insert_transport_projection_receipt(db,receipt,authority_uuid=authority,allow_missing_records=True)
        db.execute('COMMIT')
        assert store.projection_stats()['receipt_count']==store.projection_stats()['kind_count']==1
        with pytest.raises(MissingProjectionRecordError):store.get_projection_receipts([receipt.row()],authority_uuid=authority)


def test_bounds_and_external_sql_mutations_fail_closed(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        with pytest.raises(StoreLimitError):store.put_public_projections([quote()]*1025)
        ref=store.put_public_projections([quote()])[0]
        for sql in ('DELETE FROM projection_records','UPDATE projection_records SET source_time=source_time','DELETE FROM projection_hot','UPDATE projection_hot SET cells=cells'):
            with pytest.raises(sqlite3.IntegrityError):store._connection.execute(sql)
        assert get(store,[ref])[0].projection==quote()
        store._connection.execute('BEGIN')
        with pytest.raises(ValueError,match='transaction-free'):store.put_public_projections([quote(2)])
        assert store._connection.in_transaction;store._connection.execute('ROLLBACK')
