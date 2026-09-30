from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest

from polybot_observability.market_data_scalars import (
    BLOCK_ROWS, CorruptScalarSnapshotError, MissingScalarRecordError, ScalarAuthorityError,
    ScalarRecordConflictError, ScalarReference, ScalarRecord, ScalarReceipt, ScalarSnapshot,
    SCALAR_TABLE_SQL, SCALAR_TRIGGER_SQL, snapshot_sha256,
)
from polybot_observability.market_data_store import PayloadReader, PayloadStore, StoreLimitError


def value(timestamp=1, **changes):
    return replace(ScalarSnapshot('condition-서울', .71, 230.0, None, timestamp), **changes)


def values(count=BLOCK_ROWS, offset=0):
    return [value(i+offset) for i in range(count)]


def read(store, refs):
    return store.get_scalar_records([ref.record_id for ref in refs], refs[0].authority_uuid)


def test_authority_unclaimed_hot_full_ack_reopen_and_typed_exactness(tmp_path):
    path=tmp_path/'public.db'
    timestamps=[None,'','1',1,1.0,0.0,-0.0,float('inf'),float('-inf')]
    snapshots=[value(t) for t in timestamps]
    assert len(set(snapshots))==len(snapshots)
    golden=ScalarSnapshot('test',-0.0,float('inf'),None,-0.0)
    assert golden.sha256=='9e0cc9aed9db2bbdc93ec490ba3cb3ee89621390a2c17cc0eaa1ed39c80486a4'
    assert snapshot_sha256(*golden.row())==golden.sha256
    with PayloadStore(path) as writer,PayloadReader(path) as independent:
        authority=writer.scalar_authority_identity()
        assert writer.scalar_authority_role()=='UNCLAIMED'
        assert writer.put_scalar_snapshots([])==[]
        assert writer.scalar_authority_role()=='UNCLAIMED'
        refs=writer.put_scalar_snapshots(snapshots+[golden])
        assert all(ref.authority_uuid==authority for ref in refs)
        assert writer.scalar_authority_role()=='ORIGIN'
        assert [r.snapshot for r in read(independent,refs)]==snapshots+[golden]
        assert writer.scalar_stats()['hot_count']==10
        assert writer.scalar_stats()['block_count']==0
        assert writer.stats()['payload_count']==0
        assert writer._connection.execute('PRAGMA synchronous').fetchone()[0]==2
        assert writer._connection.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
    with PayloadReader(path) as reader:
        assert reader.scalar_authority_identity()==authority
        assert [r.snapshot for r in read(reader,refs[::-1])]==(snapshots+[golden])[::-1]


def test_live_single_row_commits_pack_at_threshold_and_retry_reuses_cold_id(tmp_path):
    path=tmp_path/'public.db'
    with PayloadStore(path) as writer,PayloadReader(path) as reader:
        refs=writer.put_scalar_snapshots(values(BLOCK_ROWS-1))
        assert reader.scalar_stats()['hot_count']==BLOCK_ROWS-1
        last=writer.put_scalar_snapshots([value(BLOCK_ROWS-1)])[0]
        assert reader.scalar_stats()=={'condition_count':1,'snapshot_count':BLOCK_ROWS,'source_count':0,
                                      'receipt_count':0,'hot_count':0,'block_count':1}
        assert writer.put_scalar_snapshots([value(BLOCK_ROWS-1)])[0]==last
        assert writer.put_scalar_snapshots([value(0),value(0)])==[refs[0],refs[0]]
        assert [r.snapshot for r in read(reader,[*refs,last])]==values()
        extra=writer.put_scalar_snapshots([value(BLOCK_ROWS)])[0]
        assert extra.record_id>last.record_id
        assert writer.scalar_stats()['hot_count']==1
        assert writer.scalar_stats()['block_count']==1
        assert writer._scalar_state._mode==''


def test_namespace_receipts_preserve_original_id_reuse_and_query_numeric_affinity(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_scalar_snapshots([value(1),value(2)])
        authority=store.scalar_authority_identity()
        namespace=json.dumps({'source':'m5','jenkins_job':'job','strategy':'golden-date','runtime':'r'*400})
        first=ScalarReceipt(namespace,1,refs[0].record_id)
        reused=ScalarReceipt(namespace,1,refs[1].record_id)
        borrowed=ScalarReceipt('other',2,refs[0].record_id)
        store.append_scalar_receipts([first,reused,borrowed,first],authority_uuid=authority)
        assert store.get_scalar_receipts([first.row(),reused.row(),('absent',1,refs[0].record_id),first.row()],authority_uuid=authority)==[first,reused,None,first]
        assert [r.snapshot for r in store.query_scalar_snapshots('condition-서울',authority_uuid=authority,start='1',end='2',namespace=namespace)]==[value(1)]
        assert [r.snapshot for r in store.query_scalar_snapshots('condition-서울',authority_uuid=authority,namespace='other')]==[value(1)]
        assert store.scalar_stats()['receipt_count']==3
        plan=store._connection.execute('EXPLAIN QUERY PLAN SELECT id FROM scalar_snapshots WHERE condition_key=1 AND timestamp_numeric>=? AND timestamp_numeric<?',('1','2')).fetchall()
        assert any('scalar_snapshots_condition_time' in row[3] for row in plan)


def test_replica_adopts_authority_with_existing_body_cas_preserves_ids_and_packs_independently(tmp_path):
    with PayloadStore(tmp_path/'origin.db') as origin,PayloadStore(tmp_path/'replica.db') as replica:
        payload=replica.put_many([b'public raw receipt'])[0]
        refs=origin.put_scalar_snapshots(values())
        authority=origin.scalar_authority_identity()
        records=read(origin,refs)
        # Different import batches and order must not remap authoritative record IDs.
        replica.import_scalar_records(authority,records[512:])
        assert replica.scalar_stats()['hot_count']==512
        replica.import_scalar_records(authority,records[:512])
        assert replica.scalar_authority_identity()==authority
        assert replica.scalar_authority_role()=='REPLICA'
        assert read(replica,refs)==records
        assert replica.scalar_stats()['block_count']==1
        replica.import_scalar_records(authority,records[:10])
        assert replica.scalar_stats()['snapshot_count']==BLOCK_ROWS
        assert replica.get_many([payload])==[b'public raw receipt']
        with pytest.raises(ScalarAuthorityError,match='allocate'):
            replica.put_scalar_snapshots([value(9000)])
        with pytest.raises(ScalarAuthorityError):
            origin.import_scalar_records(authority,records[:1])
        wrong=ScalarRecord(ScalarReference(authority,records[0].reference.record_id,value(9999).sha256),value(9999))
        with pytest.raises(ScalarRecordConflictError):
            replica.import_scalar_records(authority,[wrong])
        assert read(replica,refs[:1])==records[:1]


def test_empty_replica_import_binds_zero_link_files_and_cannot_re_adopt(tmp_path):
    with PayloadStore(tmp_path/'replica.db') as replica:
        authority=str(uuid.uuid4())
        replica.import_scalar_records(authority,[])
        assert replica.scalar_authority_identity()==authority
        assert replica.scalar_authority_role()=='REPLICA'
        replica.import_scalar_records(authority,[])
        with pytest.raises(ScalarAuthorityError):
            replica.import_scalar_records(str(uuid.uuid4()),[])
        with pytest.raises(ScalarAuthorityError):
            replica.get_scalar_records([],str(uuid.uuid4()))
        assert replica.put_scalar_snapshots([])==[]


def test_failed_import_rolls_back_uuid_role_and_partial_record_rows(tmp_path):
    with PayloadStore(tmp_path/'replica.db') as replica:
        before=replica.scalar_authority_identity()
        incoming=str(uuid.uuid4())
        a=ScalarRecord(ScalarReference(incoming,10,value(1).sha256),value(1))
        b=ScalarRecord(ScalarReference(incoming,10,value(2).sha256),value(2))
        with pytest.raises(ScalarRecordConflictError):
            replica.import_scalar_records(incoming,[a,b])
        assert replica.scalar_authority_identity()==before
        assert replica.scalar_authority_role()=='UNCLAIMED'
        assert replica.scalar_stats()['snapshot_count']==0
        assert replica._scalar_state._mode==''
        assert not replica._connection.in_transaction


def test_pack_failure_rolls_back_new_row_pointers_hot_deletes_and_capability(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_scalar_snapshots(values(BLOCK_ROWS-1))
        store._connection.execute("CREATE TRIGGER fail_pack BEFORE DELETE ON scalar_hot WHEN OLD.record_id=2 BEGIN SELECT RAISE(ABORT,'synthetic packing failure'); END")
        with pytest.raises(sqlite3.IntegrityError,match='packing failure'):
            store.put_scalar_snapshots([value(BLOCK_ROWS-1)])
        assert store.scalar_stats()['snapshot_count']==store.scalar_stats()['hot_count']==BLOCK_ROWS-1
        assert store.scalar_stats()['block_count']==0
        assert store._scalar_state._mode==''
        assert not store._connection.in_transaction
        assert store._connection.execute('SELECT count(*) FROM scalar_snapshots WHERE block_id IS NOT NULL').fetchone()[0]==0
        assert [r.snapshot for r in read(store,refs)]==values(BLOCK_ROWS-1)
        store._connection.execute('DROP TRIGGER fail_pack')
        store.put_scalar_snapshots([value(BLOCK_ROWS-1)])
        assert store.scalar_stats()['block_count']==1


@pytest.mark.parametrize('when',['during_pack','after_commit'])
def test_process_death_during_pack_or_after_commit_never_loses_acknowledged_hot_rows(tmp_path,when):
    path=tmp_path/'public.db'
    with PayloadStore(path) as store:
        refs=store.put_scalar_snapshots(values(BLOCK_ROWS-1))
        authority=store.scalar_authority_identity()
    program='''import os,sys
from pathlib import Path
from polybot_observability.market_data_store import PayloadStore
from polybot_observability.market_data_scalars import ScalarSnapshot
s=PayloadStore(Path(sys.argv[1]))
if sys.argv[2]=='during_pack':
 s._connection.create_function('die',0,lambda: os._exit(37))
 s._connection.execute("CREATE TRIGGER die_pack AFTER UPDATE OF block_id ON scalar_snapshots BEGIN SELECT die(); END")
s.put_scalar_snapshots([ScalarSnapshot('condition-서울',.71,230.0,None,1023)])
os._exit(38)
'''
    result=subprocess.run([sys.executable,'-c',program,str(path),when],capture_output=True,text=True)
    assert result.returncode==(37 if when=='during_pack' else 38),result.stderr
    with PayloadStore(path) as recovered:
        assert recovered.scalar_authority_identity()==authority
        assert [r.snapshot for r in read(recovered,refs)]==values(BLOCK_ROWS-1)
        if when=='during_pack':
            assert recovered.scalar_stats()['block_count']==0
            assert recovered.scalar_stats()['hot_count']==BLOCK_ROWS-1
            recovered._connection.execute('DROP TRIGGER die_pack')
        else:
            assert recovered.scalar_stats()['block_count']==1
            assert recovered.scalar_stats()['hot_count']==0
        last=recovered.put_scalar_snapshots([value(BLOCK_ROWS-1)])[0]
        assert last.record_id==BLOCK_ROWS
        assert recovered.scalar_stats()['snapshot_count']==BLOCK_ROWS
        assert recovered.scalar_stats()['block_count']==1


@pytest.mark.parametrize('mutation,trigger',[
    ("UPDATE scalar_snapshots SET timestamp='tampered' WHERE id=2",'scalar_snapshots_pack'),
    ("UPDATE scalar_conditions SET condition_id='tampered'",'scalar_conditions_no_update'),
    ("UPDATE scalar_blocks SET body=x'00'",'scalar_blocks_no_update'),
    ("UPDATE scalar_snapshots SET ordinal=1023 WHERE id=2",'scalar_snapshots_pack'),
])
def test_cached_numeric_block_never_hides_current_metadata_or_body_tampering(tmp_path,mutation,trigger):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_scalar_snapshots(values())
        store.get_scalar_records([refs[0].record_id],refs[0].authority_uuid)
        assert store._scalar_state.cache_bytes>0
        store._connection.execute('DROP TRIGGER '+trigger)
        if 'ordinal=' in mutation:
            store._connection.execute('DROP INDEX scalar_snapshots_block')
        store._connection.execute(mutation)
        with pytest.raises(CorruptScalarSnapshotError):
            store.get_scalar_records([refs[0].record_id],refs[0].authority_uuid)


def test_hot_integrity_and_missing_dependencies_fail_closed(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        ref=store.put_scalar_snapshots([value()])[0]
        with pytest.raises(MissingScalarRecordError) as missing:
            store.get_scalar_records([ref.record_id,999],ref.authority_uuid)
        assert missing.value.record_ids==[999]
        with pytest.raises(MissingScalarRecordError):
            store.append_scalar_receipts([ScalarReceipt('ns',1,ref.record_id),ScalarReceipt('ns',2,999)],authority_uuid=ref.authority_uuid)
        assert store.scalar_stats()['receipt_count']==0
        store._connection.execute('DROP TRIGGER scalar_hot_no_update')
        store._connection.execute('UPDATE scalar_hot SET probability=0.9')
        with pytest.raises(CorruptScalarSnapshotError):
            store.get_scalar_records([ref.record_id],ref.authority_uuid)


def test_swapping_complete_hot_bodies_and_checksums_cannot_rebind_record_ids(tmp_path):
    path=tmp_path/'public.db'
    with PayloadStore(path) as store:
        refs=store.put_scalar_snapshots([value(1,probability=.71),value(1,probability=.72)])
        rows=store._connection.execute('SELECT record_id,probability,liquidity,volume_24h,row_sha FROM scalar_hot ORDER BY record_id').fetchall()
        store._connection.execute('DROP TRIGGER scalar_hot_no_update')
        for target,source in ((rows[0],rows[1]),(rows[1],rows[0])):
            store._connection.execute('UPDATE scalar_hot SET probability=?,liquidity=?,volume_24h=?,row_sha=? WHERE record_id=?',(*source[1:],target[0]))
        store._connection.execute(SCALAR_TRIGGER_SQL['scalar_hot_no_update'])
    with PayloadReader(path) as reader:
        for reference in refs:
            with pytest.raises(CorruptScalarSnapshotError,match='hot row/index checksum'):
                reader.get_scalar_records([reference.record_id],reference.authority_uuid)


def test_block_cache_is_bounded(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        store._scalar_state.cache_limit=180000
        refs=[]
        for offset in (0,BLOCK_ROWS,2*BLOCK_ROWS):
            refs.extend(store.put_scalar_snapshots(values(offset=offset)))
        for ref in refs[::BLOCK_ROWS]:
            store.get_scalar_records([ref.record_id],ref.authority_uuid)
            assert store._scalar_state.cache_bytes<=180000
        assert len(store._scalar_state.cache)<=1


def test_sparse_multi_block_read_does_not_retain_unrequested_block_records(tmp_path,monkeypatch):
    import weakref
    with PayloadStore(tmp_path/'public.db') as store:
        refs=[]
        for offset in (0,BLOCK_ROWS,2*BLOCK_ROWS):
            refs.extend(store.put_scalar_snapshots(values(offset=offset)))
        live=weakref.WeakSet();alive_before=[]
        original=store._scalar_state._block
        def observed(authority,block_id):
            alive_before.append(len(live))
            records=original(authority,block_id)
            for record in records.values():live.add(record)
            return records
        monkeypatch.setattr(store._scalar_state,'_block',observed)
        selected=refs[::BLOCK_ROWS]
        result=store.get_scalar_records([ref.record_id for ref in selected],selected[0].authority_uuid)
        assert [record.reference for record in result]==selected
        assert alive_before==[0,1,2]


@pytest.mark.parametrize('statement',[
    'DELETE FROM scalar_snapshots','UPDATE scalar_snapshots SET timestamp=timestamp',
    'DELETE FROM scalar_hot','UPDATE scalar_hot SET probability=probability',
    "UPDATE scalar_authority SET role='REPLICA'",'DELETE FROM scalar_authority',
    'DELETE FROM scalar_conditions',"UPDATE scalar_conditions SET condition_id='new'",
])
def test_outside_owner_mutation_path_cannot_change_logical_data(tmp_path,statement):
    with PayloadStore(tmp_path/'public.db') as store:
        store.put_scalar_snapshots([value()])
        with pytest.raises(sqlite3.IntegrityError):
            store._connection.execute(statement)
        assert store._scalar_state._mode==''


def test_authority_getter_does_not_count_or_scan_records(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        store.put_scalar_snapshots(values())
        statements=[]
        store._connection.set_trace_callback(statements.append)
        assert store.scalar_authority_identity()
        assert len(statements)==1 and 'scalar_authority WHERE singleton=1' in statements[0]
        assert 'count(' not in statements[0].lower()


def test_authority_replace_and_pointer_changes_cannot_bypass_owner_guards(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        ref=store.put_scalar_snapshots([value()])[0]
        with pytest.raises(sqlite3.IntegrityError,match='initialization'):
            store._connection.execute("INSERT OR REPLACE INTO scalar_authority SELECT singleton,contract,?,role FROM scalar_authority",(str(uuid.uuid4()),))
        with pytest.raises(sqlite3.IntegrityError,match='owner write path'):
            store._connection.execute('INSERT OR REPLACE INTO scalar_snapshots(id,condition_key,timestamp) VALUES(1,1,99)')
        assert store.scalar_authority_identity()==ref.authority_uuid
        assert read(store,[ref])[0].snapshot==value()


def test_transport_receipts_explicitly_require_replica_fk_off_transaction(tmp_path):
    from polybot_observability.market_data_scalar_store import insert_transport_scalar_receipt
    authority=str(uuid.uuid4())
    receipt=ScalarReceipt('transport-source',1,999)
    with PayloadStore(tmp_path/'bundle.db') as bundle:
        bundle.import_scalar_records(authority,[])
        db=bundle._connection
        with pytest.raises(ValueError,match='explicit isolated'):
            insert_transport_scalar_receipt(db,receipt,authority_uuid=authority,allow_missing_records=True)
        db.execute('BEGIN')
        with pytest.raises(ValueError,match='foreign-key'):
            insert_transport_scalar_receipt(db,receipt,authority_uuid=authority,allow_missing_records=True)
        db.execute('ROLLBACK');db.execute('PRAGMA foreign_keys=OFF');db.execute('BEGIN')
        insert_transport_scalar_receipt(db,receipt,authority_uuid=authority,allow_missing_records=True)
        db.execute('COMMIT')
        assert bundle.scalar_stats()['receipt_count']==1
        with pytest.raises(MissingScalarRecordError):
            bundle.get_scalar_receipts([receipt.row()],authority_uuid=authority)


def test_receipt_paging_namespace_filter_and_storage_independence(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_scalar_snapshots(values(10));authority=refs[0].authority_uuid
        receipts=[ScalarReceipt(ns,i//2,ref.record_id) for ns in ('a','b') for i,ref in enumerate(refs)]
        store.append_scalar_receipts(receipts,authority_uuid=authority)
        found=[];cursor=None
        while page:=list(store.iter_scalar_receipts(authority_uuid=authority,after=cursor,limit=3)):
            found.extend(page);cursor=page[-1].row()
        assert found==sorted(receipts,key=lambda r:r.row())
        assert list(store.iter_scalar_receipts(authority_uuid=authority,namespace='a',record_ids=[refs[0].record_id]))==[receipts[0]]
        assert list(store.iter_scalar_receipts(authority_uuid=authority,record_ids=[]))==[]
        with pytest.raises(ValueError,match='crosses'):
            list(store.iter_scalar_receipts(authority_uuid=authority,namespace='a',after=('b',1,refs[0].record_id)))


@pytest.mark.parametrize('field,bad', [('probability',float('nan')),('liquidity',float('nan')),('timestamp',float('nan')),
    ('timestamp',True),('timestamp',b'bytes'),('timestamp',1<<63),('condition_id',''),('probability',1)])
def test_unrepresentable_input_rejected(field,bad):
    with pytest.raises(ValueError): value(**{field:bad})


def test_wire_exactness_no_economic_clamps_and_private_fields_rejected():
    for snapshot in (value(-0.0),value(1.0),value(None,probability=-3.0,liquidity=float('-inf'),volume_24h=float('inf'))):
        assert ScalarSnapshot.from_wire(json.loads(json.dumps(snapshot.to_wire(),allow_nan=False)))==snapshot
    snapshot=value();wire=snapshot.to_wire();wire['private_order']='forbidden'
    with pytest.raises(ValueError): ScalarSnapshot.from_wire(wire)
    for bad in ('nan','0x1p+999999','1.0',1.0):
        wire=snapshot.to_wire();wire['probability']=bad
        with pytest.raises(ValueError): ScalarSnapshot.from_wire(wire)


def test_record_batch_limits_and_caller_transaction_not_committed(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        authority=store.scalar_authority_identity()
        with pytest.raises(StoreLimitError): store.put_scalar_snapshots([value()]*1025)
        with pytest.raises(StoreLimitError): store.get_scalar_records([1]*1025,authority)
        store._connection.execute('BEGIN')
        with pytest.raises(ValueError,match='transaction-free'): store.put_scalar_snapshots([value()])
        assert store._connection.in_transaction
        store._connection.execute('ROLLBACK')
        assert store.scalar_authority_role()=='UNCLAIMED'
