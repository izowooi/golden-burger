"""Full-record cache avoids reconstruction without weakening cold-block proof."""
from dataclasses import FrozenInstanceError
from types import MappingProxyType
import sqlite3
import uuid

import pytest

from polybot_observability import market_data_projection_store as backend
from polybot_observability import market_data_projection_profiles as profiles
from polybot_observability import market_data_projections as projection_contract
from polybot_observability.market_data_projections import (
    CorruptProjectionError, MissingProjectionRecordError, PublicProjection, ProjectionReceipt, projection_schema_sha,
)
from polybot_observability.market_data_projection_profiles import ProjectionField, ProjectionProfile
from polybot_observability.market_data_scalars import ScalarAuthorityError
from polybot_observability.market_data_store import PayloadStore, StoreLimitError


def quote(i):
    return PublicProjection('token-quote-v1', ('condition','token','Yes',120.0,None,.70,.72,.02,str(i)))


def get(store, refs):
    return store.get_projection_records([r.record_id for r in refs], refs[0].authority_uuid)


def clear(access):
    access.cache.clear(); access.cache_bytes = 0


def test_repeated_record_and_receipt_reads_reconstruct_only_once(tmp_path, monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        refs = store.put_public_projections([quote(i) for i in range(1024)])
        receipts = [ProjectionReceipt('test-source', 'market_snapshots', i, ref.kind, ref.record_id) for i,ref in enumerate(refs)]
        store.append_projection_receipts(receipts, authority_uuid=refs[0].authority_uuid)
        clear(store._projection_state)
        calls = []
        original = backend.reconstruct_projection
        def count(*args):
            calls.append(1)
            return original(*args)
        monkeypatch.setattr(backend, 'reconstruct_projection', count)
        for index in range(30):
            assert get(store, refs[index:index+1])[0].projection == quote(index)
            assert store.get_projection_receipts([receipts[index].row()], authority_uuid=refs[0].authority_uuid) == receipts[index:index+1]
        assert len(calls) == 1024
        assert len(store._projection_state.cache) == 1
        assert store._projection_state.cache_bytes > 1024*100
        record = get(store, refs[:1])[0]
        with pytest.raises(FrozenInstanceError):
            record.reference = None
        with pytest.raises(FrozenInstanceError):
            record.projection.values = ()
        block = store._projection_state._block(refs[0].authority_uuid, 1)
        with pytest.raises(TypeError):
            block[refs[0].record_id] = None


@pytest.mark.parametrize('damage', ['neighbor-clock','neighbor-condition','neighbor-token','body','digest','size','hot-leftover','kind-schema'])
def test_warm_records_never_hide_neighbor_index_or_block_damage(tmp_path, damage):
    with PayloadStore(tmp_path/'public.db') as store:
        refs = store.put_public_projections([quote(i) for i in range(1024)])
        get(store, refs[:1]); assert store._projection_state.cache_bytes > 0
        statements = {
            'neighbor-clock': ('projection_records_pack', "UPDATE projection_records SET source_time='tampered' WHERE id=2"),
            'neighbor-condition': ('projection_records_pack', "UPDATE projection_records SET condition_key=NULL WHERE id=2"),
            'neighbor-token': ('projection_records_pack', "UPDATE projection_records SET token_key=NULL WHERE id=2"),
            'body': ('projection_blocks_no_update', "UPDATE projection_blocks SET body=CAST(body||x'00' AS BLOB)"),
            'digest': ('projection_blocks_no_update', "UPDATE projection_blocks SET row_sha=zeroblob(32)"),
            'size': ('projection_blocks_no_update', "UPDATE projection_blocks SET raw_size=raw_size+1"),
            'hot-leftover': ('projection_hot_insert', "INSERT INTO projection_hot VALUES(2,x'00',zeroblob(32))"),
            'kind-schema': ('projection_kinds_no_update', "UPDATE projection_kinds SET schema_sha=zeroblob(32)"),
        }
        trigger,sql = statements[damage]
        definition = store._connection.execute('SELECT sql FROM sqlite_master WHERE name=?',(trigger,)).fetchone()[0]
        store._connection.execute('DROP TRIGGER '+trigger)
        store._connection.execute(sql)
        store._connection.execute(definition)
        with pytest.raises(CorruptProjectionError):
            get(store, refs[:1])


def test_warm_nullable_block_rechecks_unselected_sparse_index(tmp_path, monkeypatch):
    registry = dict(profiles.PROJECTION_PROFILES)
    kind = 'test-nullable-cache-v1'
    registry[kind] = ProjectionProfile(kind, (ProjectionField('condition_id','TEXT'), ProjectionField('price','REAL',False)))
    monkeypatch.setattr(profiles,'PROJECTION_PROFILES',MappingProxyType(registry))
    projection_schema_sha.cache_clear()
    try:
        with PayloadStore(tmp_path/'public.db') as store:
            refs = store.put_public_projections([PublicProjection(kind,(None,float(i))) for i in range(1024)])
            get(store,refs[:1]); assert store._projection_state.cache_bytes > 0
            definition=store._connection.execute("SELECT sql FROM sqlite_master WHERE name='projection_unkeyed_no_update'").fetchone()[0]
            store._connection.execute('DROP TRIGGER projection_unkeyed_no_update')
            store._connection.execute('UPDATE projection_unkeyed SET content_sha=zeroblob(32) WHERE record_id=2')
            store._connection.execute(definition)
            with pytest.raises(CorruptProjectionError):get(store,refs[:1])
    finally:
        projection_schema_sha.cache_clear()


def test_cache_budget_eviction_and_too_large_entry_fallback(tmp_path, monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_public_projections([quote(i) for i in range(1024)])
        refs+=store.put_public_projections([quote(i) for i in range(1024,2048)])
        access=store._projection_state;clear(access)
        get(store,refs[:1]);get(store,refs[1024:1025])
        assert len(access.cache)==2
        sizes=[item[1] for item in access.cache.values()]
        assert all(size>500_000 for size in sizes)  # Includes nested records and typed index keys, not only outer tuples.
        access.cache_limit=max(sizes)+512;clear(access)
        get(store,refs[:1]);first_key=next(iter(access.cache))
        get(store,refs[1024:1025])
        assert len(access.cache)==1 and first_key not in access.cache
        assert access.cache_bytes<=access.cache_limit
        access.cache_limit=1;clear(access)
        original=backend.reconstruct_projection;calls=[]
        def count(*args):calls.append(1);return original(*args)
        monkeypatch.setattr(backend,'reconstruct_projection',count)
        get(store,refs[:1]);get(store,refs[:1])
        assert len(calls)==2048 and access.cache_bytes==0 and not access.cache


def test_cache_key_keeps_authority_and_sqlite_types_distinct(tmp_path):
    def key(value):
        return backend.ProjectionAccess._block_cache_key('authority',1,
            ('kind',b's'*32,1,1,b'body'),[(value,)])
    assert len({key(value) for value in (1,1.0,0.0,-0.0,'1',b'1',None)})==7
    text='same-content-'*20
    clone=text.encode().decode()
    assert text==clone and text is not clone
    assert backend.ProjectionAccess._block_cache_key('a',1,('kind',b's'*32,2,2,b'body'),[(text,),(text,)]) == backend.ProjectionAccess._block_cache_key('a',1,('kind',b's'*32,2,2,b'body'),[(text,),(clone,)])
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_public_projections([quote(i) for i in range(1024)])
        clear(store._projection_state)
        authority=refs[0].authority_uuid;other=str(uuid.uuid4())
        first=store._projection_state._block(authority,1)
        second=store._projection_state._block(other,1)
        assert first[1].reference.authority_uuid==authority
        assert second[1].reference.authority_uuid==other
        assert len(store._projection_state.cache)==2
        with pytest.raises(ScalarAuthorityError):
            store.get_projection_records([1],other)


def test_hot_cache_reuses_verified_record_then_follows_real_packing(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_public_projections([quote(0)])
        clear(store._projection_state)
        original=backend.reconstruct_projection;calls=[]
        def count(*args):calls.append(1);return original(*args)
        monkeypatch.setattr(backend,'reconstruct_projection',count)
        for _ in range(20):assert get(store,refs)[0].projection==quote(0)
        assert len(calls)==1 and store._projection_state.cache_bytes>0
        store.put_public_projections([quote(i) for i in range(1,1024)])
        assert store.projection_stats()['hot_count']==0
        assert get(store,refs)[0].projection==quote(0)


@pytest.mark.parametrize('damage',['clock','condition','token','cells','digest','missing-hot'])
def test_hot_cache_rechecks_current_index_dictionary_and_body(tmp_path,damage):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_public_projections([quote(0)])
        get(store,refs);assert store._projection_state.cache_bytes>0
        statements={
            'clock':('projection_records_pack',"UPDATE projection_records SET source_time='new-clock'"),
            'condition':('scalar_conditions_no_update',"UPDATE scalar_conditions SET condition_id='new-condition'"),
            'token':('projection_tokens_no_update',"UPDATE projection_tokens SET token_id='new-token'"),
            'cells':('projection_hot_no_update',"UPDATE projection_hot SET cells=x'00'"),
            'digest':('projection_hot_no_update',"UPDATE projection_hot SET row_sha=zeroblob(32)"),
            'missing-hot':('projection_hot_pack_delete',"DELETE FROM projection_hot"),
        }
        trigger,sql=statements[damage]
        definition=store._connection.execute('SELECT sql FROM sqlite_master WHERE name=?',(trigger,)).fetchone()[0]
        store._connection.execute('DROP TRIGGER '+trigger);store._connection.execute(sql);store._connection.execute(definition)
        with pytest.raises(CorruptProjectionError):get(store,refs)


def test_bound_record_read_checks_exact_receipts_and_records_once(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_public_projections([quote(0),quote(1)])
        receipts=[ProjectionReceipt('owner','market_snapshots',i,ref.kind,ref.record_id) for i,ref in enumerate(refs)]
        store.append_projection_receipts(receipts,authority_uuid=refs[0].authority_uuid)
        missing=ProjectionReceipt('wrong-owner','market_snapshots',0,refs[0].kind,refs[0].record_id)
        access=store._projection_state;calls=[];original=access._records
        def count(*args):calls.append(args[0]);return original(*args)
        monkeypatch.setattr(access,'_records',count)
        result=access.get_bound_records([r.row() for r in (receipts[0],missing,receipts[1],receipts[0])],authority_uuid=refs[0].authority_uuid)
        assert calls==[[refs[0].record_id,refs[1].record_id]]
        assert [None if row is None else row.projection for row in result]==[quote(0),None,quote(1),quote(0)]
        with pytest.raises(ScalarAuthorityError):
            access.get_bound_records([receipts[0].row()],authority_uuid=str(uuid.uuid4()))


def test_bound_record_missing_body_is_an_error_not_a_missing_receipt(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_public_projections([quote(0)])
        receipt=ProjectionReceipt('owner','market_snapshots',0,refs[0].kind,refs[0].record_id)
        store.append_projection_receipts([receipt],authority_uuid=refs[0].authority_uuid)
        store._connection.execute('PRAGMA foreign_keys=OFF')
        store._connection.execute('DROP TRIGGER projection_records_no_delete')
        store._connection.execute('DELETE FROM projection_records')
        with pytest.raises(MissingProjectionRecordError):
            store._projection_state.get_bound_records([receipt.row()],authority_uuid=refs[0].authority_uuid)


def test_bound_read_does_not_prefetch_unselected_corrupt_record(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=store.put_public_projections([quote(0),quote(1)])
        receipts=[ProjectionReceipt('owner','market_snapshots',i,ref.kind,ref.record_id) for i,ref in enumerate(refs)]
        store.append_projection_receipts(receipts,authority_uuid=refs[0].authority_uuid)
        store._connection.execute('DROP TRIGGER projection_hot_no_update')
        store._connection.execute("UPDATE projection_hot SET cells=x'00' WHERE record_id=?",(refs[1].record_id,))
        result=store._projection_state.get_bound_records([receipts[0].row()],authority_uuid=refs[0].authority_uuid)
        assert result[0].projection==quote(0)
        with pytest.raises(CorruptProjectionError):
            store._projection_state.get_bound_records([receipts[1].row()],authority_uuid=refs[0].authority_uuid)


def test_bound_read_rejects_receipt_stream_kind_relabeling(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        other=PublicProjection('gamma-quote-v1',('condition',.5,None,None,None,None,None,'0'))
        refs=store.put_public_projections([quote(0),other])
        receipt=ProjectionReceipt('owner','market_snapshots',0,refs[0].kind,refs[0].record_id)
        store.append_projection_receipts([receipt],authority_uuid=refs[0].authority_uuid)
        store._connection.execute('DROP TRIGGER projection_receipt_streams_no_update')
        store._connection.execute("UPDATE projection_receipt_streams SET kind_key=(SELECT id FROM projection_kinds WHERE kind='gamma-quote-v1')")
        forged=ProjectionReceipt('owner','market_snapshots',0,'gamma-quote-v1',refs[0].record_id)
        with pytest.raises(CorruptProjectionError,match='kind'):
            store._projection_state.get_bound_records([forged.row()],authority_uuid=refs[0].authority_uuid)


def test_bound_read_duplicate_response_obeys_full_byte_budget(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        value=quote(0);ref=store.put_public_projections([value])[0]
        receipt=ProjectionReceipt('owner','market_snapshots',0,ref.kind,ref.record_id)
        store.append_projection_receipts([receipt],authority_uuid=ref.authority_uuid)
        monkeypatch.setattr(projection_contract,'MAX_PROJECTION_BYTES',value.raw_bytes+1)
        assert store._projection_state.get_bound_records([receipt.row()],authority_uuid=ref.authority_uuid)[0].projection==value
        with pytest.raises(StoreLimitError,match='byte limit'):
            store._projection_state.get_bound_records([receipt.row(),receipt.row()],authority_uuid=ref.authority_uuid)


@pytest.mark.parametrize('field,replacement',[(0,'other-owner'),(1,'other_table'),(2,999)])
def test_actual_bound_pairs_reject_missing_ownership_tuple_with_same_public_record(tmp_path,field,replacement):
    with PayloadStore(tmp_path/'public.db') as store:
        ref=store.put_public_projections([quote(0)])[0]
        receipt=ProjectionReceipt('owner','source_table',7,ref.kind,ref.record_id)
        store.append_projection_receipts([receipt],authority_uuid=ref.authority_uuid)
        changed=list(receipt.row());changed[field]=replacement
        pairs=store._projection_state.get_bound_pairs([receipt.row(),changed,receipt.row()],authority_uuid=ref.authority_uuid)
        assert pairs[1] is None
        assert pairs[0][0]==pairs[2][0]==receipt
        assert pairs[0][1].reference==pairs[2][1].reference==ref
        assert store._projection_state.get_bound_records([receipt.row(),changed,receipt.row()],authority_uuid=ref.authority_uuid)==[pairs[0][1],None,pairs[2][1]]


def test_actual_bound_pairs_use_one_read_transaction_and_body_validation(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        ref=store.put_public_projections([quote(0)])[0]
        receipt=ProjectionReceipt('owner','source_table',7,ref.kind,ref.record_id)
        store.append_projection_receipts([receipt],authority_uuid=ref.authority_uuid)
        sql=[];calls=[];access=store._projection_state;original=access._records
        def observed(*args):
            assert store._connection.in_transaction
            calls.append(args[0]);return original(*args)
        monkeypatch.setattr(access,'_records',observed)
        store._connection.set_trace_callback(sql.append)
        pair=access.get_bound_pairs([receipt.row()],authority_uuid=ref.authority_uuid)[0]
        store._connection.set_trace_callback(None)
        assert pair[0]==receipt and pair[1].projection==quote(0)
        assert calls==[[ref.record_id]]
        assert [value for value in sql if value in ('BEGIN','COMMIT','ROLLBACK')]==['BEGIN','COMMIT']
