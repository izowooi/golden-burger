"""Put-local cold-block reuse must never weaken post-transaction read proof."""
import sqlite3
from types import MappingProxyType

import pytest

from polybot_observability import market_data_projection_profiles as profiles
from polybot_observability import market_data_projection_store as backend
from polybot_observability.market_data_projection_profiles import ProjectionField,ProjectionProfile
from polybot_observability.market_data_projections import (
    PublicProjection,ProjectionReference,ProjectionRecord,CorruptProjectionError,
    projection_block_sha,projection_schema_sha,
)
from polybot_observability.market_data_store import PayloadStore


def value(index, *, condition=None):
    return PublicProjection('gamma-quote-v1',(
        condition or f'condition-{index%128}',index/10000.0,120.0,None,.5,.6,.1,None))


def test_one_fresh_proof_per_block_in_each_put_then_ack_and_final_recheck(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        rows=[value(i) for i in range(1024)];refs=store.put_public_projections(rows)
        access=store._projection_state;original=access._block;calls=[]
        def counted(*args,**kwargs):calls.append(args[1]);return original(*args,**kwargs)
        monkeypatch.setattr(access,'_block',counted)
        assert store.put_public_projections(rows[:120])==refs[:120]
        assert calls==[1,1,1]  # first dedup proof, normal ACK readback, final touched-block proof
        assert access._put_phase is None
        calls.clear()
        assert store.put_public_projections(rows[:120])==refs[:120]
        assert calls==[1,1,1]  # no memo survives COMMIT
        calls.clear()
        for ref in refs[:3]:store.get_projection_records([ref.record_id],ref.authority_uuid)
        assert calls==[1,1,1]  # ordinary reader still rechecks every time


def test_begin_immediate_excludes_other_connection_writer(tmp_path,monkeypatch):
    path=tmp_path/'public.db'
    with PayloadStore(path) as store:
        store.put_public_projections([value(i) for i in range(1024)])
        access=store._projection_state;original=access._find_identical;attempts=[]
        def checked(*args):
            assert access._put_phase is not None and store._connection.in_transaction
            assert access.cache_bytes+access._put_phase['reserve']<=8<<20
            other=sqlite3.connect(path,timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError,match='locked'):
                    other.execute('BEGIN IMMEDIATE')
                attempts.append(True)
            finally:other.close()
            return original(*args)
        monkeypatch.setattr(access,'_find_identical',checked)
        store.put_public_projections([value(0),value(1)])
        assert len(attempts)==2 and access._put_phase is None


@pytest.mark.parametrize('when',['during_insert','after_pack'])
def test_coherent_same_transaction_neighbor_change_after_memo_cannot_ack(tmp_path,monkeypatch,when):
    with PayloadStore(tmp_path/'public.db') as store:
        rows=[value(i) for i in range(1024)];refs=store.put_public_projections(rows)
        access=store._projection_state;authority=refs[0].authority_uuid
        original_records=list(access._block(authority,1).values())
        original_insert=access._insert_hot;original_pack=access._pack;old_limit=access.cache_limit;triggered=[]
        def corrupt():
            assert access._put_phase['proofs']
            # Coherently alter an unselected neighbour, including the block hash:
            # ACK of the new HOT record alone would not detect this mutation.
            c=store._connection
            c.execute("INSERT INTO scalar_conditions(condition_id) VALUES('injected-neighbor')")
            condition=c.execute("SELECT id FROM scalar_conditions WHERE condition_id='injected-neighbor'").fetchone()[0]
            saved={name:c.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone()[0]
                   for name in ('projection_records_pack','projection_blocks_no_update')}
            for name in saved:c.execute('DROP TRIGGER '+name)
            c.execute('UPDATE projection_records SET condition_key=? WHERE id=?',(condition,refs[1].record_id))
            changed=PublicProjection(rows[1].kind,('injected-neighbor',*rows[1].values[1:]))
            records=list(original_records)
            records[1]=ProjectionRecord(ProjectionReference(authority,changed.kind,refs[1].record_id,changed.sha256),changed)
            c.execute('UPDATE projection_blocks SET row_sha=? WHERE id=1',(projection_block_sha(records),))
            for sql in saved.values():c.execute(sql)
            # Eviction removes big record/key links but not the small proof obligation.
            while access.cache:access._evict_cache_entry()
            assert access._put_phase['proofs'] and not access._put_phase['matches']
            triggered.append(True)
        def corrupted(projection,*args):
            inserted=original_insert(projection,*args)
            corrupt()
            return inserted
        def corrupted_pack(*args):
            assert not access._put_phase['enabled'] and not access._put_phase['matches']
            result=original_pack(*args)
            corrupt()
            return result
        monkeypatch.setattr(access,'_insert_hot' if when=='during_insert' else '_pack',
                            corrupted if when=='during_insert' else corrupted_pack)
        with pytest.raises(CorruptProjectionError,match='changed during put'):
            store.put_public_projections([value(9000,condition='condition-0')])
        assert triggered and access._put_phase is None and access.cache_limit==old_limit
        assert access.cache_bytes==0 and not store._connection.in_transaction
        assert store.projection_stats()['record_count']==1024
        assert store.get_projection_records([refs[1].record_id],authority)[0].projection==rows[1]
        monkeypatch.setattr(access,'_insert_hot',original_insert)
        monkeypatch.setattr(access,'_pack',original_pack)
        assert store.put_public_projections([rows[0]])==refs[:1]


def test_small_cache_falls_back_without_memo_and_remains_bounded(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        rows=[value(i) for i in range(1024)];refs=store.put_public_projections(rows)
        access=store._projection_state
        access.cache.clear();access.cache_bytes=0;access.cache_limit=128
        assert store.put_public_projections(rows[:4])==refs[:4]
        assert access._put_phase is None and access.cache_limit==128 and access.cache_bytes<=128


def test_full_proof_reserve_falls_back_for_new_blocks(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        rows=[value(i) for i in range(2048)]
        refs=store.put_public_projections(rows[:1024])+store.put_public_projections(rows[1024:])
        desired=frozenset((rows[0].sha256,rows[1024].sha256))
        first_sha=next(sha for sha in desired if sha==rows[0].sha256)
        # Size the reconstructed cold reference used by the dedup loop. A PUT
        # ACK may share its SHA string with the caller's immutable value cache.
        cold_ref=store.get_projection_records([refs[0].record_id],refs[0].authority_uuid)[0].reference
        reserve=store._projection_state._cache_entry_bytes(('put-proof-state',desired),(),({1:b'x'*32},{first_sha:(1,cold_ref)}))+64
        monkeypatch.setattr(backend,'PUT_PROOF_RESERVE_BYTES',reserve)
        access=store._projection_state;original=access._block;calls=[]
        def counted(*args,**kwargs):
            calls.append(args[1]);result=original(*args,**kwargs)
            if access._put_phase is not None:
                assert len(access._put_phase['proofs'])<=1
                assert access.cache_bytes+access._put_phase['reserve']<=8<<20
            return result
        monkeypatch.setattr(access,'_block',counted)
        assert store.put_public_projections([rows[0],rows[1024],rows[1024],rows[0]])==[refs[0],refs[1024],refs[1024],refs[0]]
        assert calls.count(1)==3  # first proof, ACK, final proof
        assert calls.count(2)==3  # two fresh dedup reads + ACK, never memoized
        assert access._put_phase is None


def test_next_put_rejects_corruption_committed_after_prior_success(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        rows=[value(i) for i in range(1024)];refs=store.put_public_projections(rows)
        assert store.put_public_projections(rows[:3])==refs[:3]
        assert store._projection_state._put_phase is None
        store._connection.execute('DROP TRIGGER projection_blocks_no_update')
        store._connection.execute("UPDATE projection_blocks SET body=x'00'")
        with pytest.raises(CorruptProjectionError):store.put_public_projections(rows[:1])
        assert store._projection_state._put_phase is None and not store._connection.in_transaction


def test_dedup_sha_respects_scalar_types_and_real_zero_canonicalization(tmp_path,monkeypatch):
    kind='test-put-scalar-v1';registry=dict(profiles.PROJECTION_PROFILES)
    registry[kind]=ProjectionProfile(kind,(ProjectionField('condition_id','TEXT',False),ProjectionField('value','SCALAR',False)))
    monkeypatch.setattr(profiles,'PROJECTION_PROFILES',MappingProxyType(registry));projection_schema_sha.cache_clear()
    try:
        values=[PublicProjection(kind,('target',x)) for x in (1,1.0,-0.0,0.0,'1')]
        filler=[PublicProjection(kind,(f'other-{i}',i)) for i in range(1019)]
        with PayloadStore(tmp_path/'public.db') as store:
            refs=store.put_public_projections(values+filler)
            assert len({ref.record_id for ref in refs[:5]})==5
            assert store.put_public_projections(values)==refs[:5]
            real=[PublicProjection('gamma-quote-v1',('real',zero,None,None,None,None,None,None)) for zero in (0.0,-0.0)]
            acknowledged=store.put_public_projections(real)
            assert acknowledged[0]==acknowledged[1]
    finally:projection_schema_sha.cache_clear()


def test_negative_proof_retains_all_batch_matches_after_full_record_eviction(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        rows=[value(i) for i in range(1024)];refs=store.put_public_projections(rows)
        access=store._projection_state;authority=refs[0].authority_uuid
        negative=value(9000,condition='condition-0')
        calls=[];original=access._block
        def counted(*args,**kwargs):calls.append(args[1]);return original(*args,**kwargs)
        monkeypatch.setattr(access,'_block',counted)
        with access._write('put'):
            with access._put_proof_scope(authority,[negative,rows[0],rows[1]]) as phase:
                assert access._find_cold_reference(authority,1,negative.sha256) is None
                assert set(phase['matches'])=={rows[0].sha256,rows[1].sha256}
                assert all(type(pair[1]) is ProjectionReference for pair in phase['matches'].values())
                while access.cache:access._evict_cache_entry()
                assert access._find_cold_reference(authority,1,rows[0].sha256)==refs[0]
                assert access._find_cold_reference(authority,1,rows[1].sha256)==refs[1]
                assert access._find_cold_reference(authority,1,negative.sha256) is None
                assert calls==[1]
        assert calls==[1,1]  # final fresh proof survives record eviction


def test_unknown_sha_bypasses_fixed_desired_set_negative_proof(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        rows=[value(i) for i in range(1024)];refs=store.put_public_projections(rows)
        access=store._projection_state;authority=refs[0].authority_uuid
        calls=[];original=access._block
        def counted(*args,**kwargs):calls.append(args[1]);return original(*args,**kwargs)
        monkeypatch.setattr(access,'_block',counted)
        with access._write('put'):
            with access._put_proof_scope(authority,[rows[0]]) as phase:
                assert access._find_cold_reference(authority,1,rows[0].sha256)==refs[0]
                assert rows[1].sha256 not in phase['desired']
                assert access._find_cold_reference(authority,1,rows[1].sha256)==refs[1]
                assert calls==[1,1]
        assert calls==[1,1,1]


def test_block_is_not_marked_visited_when_all_batch_matches_do_not_fit(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        rows=[value(i) for i in range(1024)];refs=store.put_public_projections(rows)
        access=store._projection_state;authority=refs[0].authority_uuid
        desired=frozenset(row.sha256 for row in rows[:10])
        base=access._cache_entry_bytes(('put-proof-state',desired),(),({},{}))
        monkeypatch.setattr(backend,'PUT_PROOF_RESERVE_BYTES',base+512)
        with access._write('put'):
            with access._put_proof_scope(authority,rows[:10]) as phase:
                assert phase['enabled']
                assert access._find_cold_reference(authority,1,rows[0].sha256)==refs[0]
                assert phase['proofs']=={} and phase['matches']=={}
                while access.cache:access._evict_cache_entry()
                assert access._find_cold_reference(authority,1,rows[1].sha256)==refs[1]
                assert phase['proofs']=={}
