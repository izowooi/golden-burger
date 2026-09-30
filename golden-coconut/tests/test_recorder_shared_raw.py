"""Current recorder source projections preserve native cycles and working state."""
from contextlib import closing
from datetime import timedelta
from types import SimpleNamespace
import sqlite3

import pytest

from polybot.recorder import Recorder
from polybot.recorder_config import RecorderConfig, RUNTIME, slot_start_utc
from polybot.recorder_store import RecorderStore
from polybot.recorder_export import iter_rows, carryovers
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadStore, PayloadReader
from polybot_observability.market_data_raw_profiles import COCONUT_RECORDER_PROFILE_ID
from polybot_observability.market_data_raw_links import raw_layout_metadata, verify_raw_dependencies
from polybot_observability.market_data_scalar_links import scalar_namespace
from test_recorder import FakeClient, NOW, soccer_event, fake_reset


NAMESPACE=scalar_namespace('fixture-source','polybot-white','golden-coconut',RUNTIME)


def cycle(path,at,refs,*,native=False):
    FakeClient.current=at
    store=RecorderStore(path,slot_start_utc(at,30).date().isoformat(),references=refs,
        raw_profile_id=COCONUT_RECORDER_PROFILE_ID if native else None,
        raw_namespace=NAMESPACE if native else None)
    try:
        result=Recorder(RecorderConfig(),store,FakeClient).run(at)
        return result,store.pending('2099-01-01T00:00:00Z')
    finally:store.close()


def test_native_recorder_preserves_export_mutable_state_and_rollover(tmp_path,monkeypatch):
    # Reproduce identical upstream evidence/run identities on both storage routes.
    monkeypatch.delenv('PUBLIC_MARKET_DATA_SOURCE',raising=False)
    monkeypatch.delenv('JOB_NAME',raising=False)
    monkeypatch.delenv('PUBLIC_MARKET_DATA_RAW',raising=False)
    histories=[]
    with PayloadStore(tmp_path/'public.db') as public:
        refs=PayloadReferences(public,public)
        for native in (False,True):
            runs=iter(('run-first','run-second','run-third'))
            monkeypatch.setattr('polybot.recorder.uuid4',lambda:SimpleNamespace(hex=next(runs)))
            path=tmp_path/('native' if native else 'inline')/'trades_sim.db'
            outputs=[]
            for seconds in (0,60,120):
                result,pending=cycle(path,NOW+timedelta(seconds=seconds),refs if native else PayloadReferences(),native=native)
                assert result['status']=='SUCCEEDED'
                outputs.append(pending)
            sources=(path.with_name('trades_sim_20260908.db'),path)
            rows=[row for source in sources for row in iter_rows(source,references=refs if native else PayloadReferences())]
            histories.append((outputs,rows,list(carryovers(path,references=refs if native else PayloadReferences()))))
        assert histories[0]==histories[1]
        assert len(histories[1][1])==18
        path=tmp_path/'native/trades_sim.db'
        with sqlite3.connect(path) as physical:
            assert 'scheduled_start' not in {r[1] for r in physical.execute('PRAGMA table_info(tracked_events)')}
            assert 'team_name' not in {r[1] for r in physical.execute('PRAGMA table_info(book_observations)')}
            assert physical.execute('SELECT COUNT(*) FROM tracked_events').fetchone()[0]==1
        # Fresh reader: no hot writer or decoded row cache supplies the answer.
        with PayloadReader(public.path) as reader:
            cold=PayloadReferences(reader,cache_bytes=0)
            assert list(iter_rows(path,references=cold))==histories[1][1][6:]
        with closing(RecorderStore(path,'2026-09-09',references=refs,raw_namespace=NAMESPACE)) as store:
            assert raw_layout_metadata(store.c)['profile_id']==COCONUT_RECORDER_PROFILE_ID
            verify_raw_dependencies(store.c,references=refs)
            plan=store.c.execute("EXPLAIN QUERY PLAN SELECT * FROM tracked_events WHERE state IN ('SCHEDULED','WINDOW','WAIT_SETTLEMENT') AND next_due<=? ORDER BY next_due,event_id",('2099',)).fetchall()
            assert any('tracked_due_idx' in row[3] for row in plan)


def test_populated_inline_activation_is_refused_before_rotation(tmp_path,monkeypatch):
    monkeypatch.delenv('PUBLIC_MARKET_DATA_RAW',raising=False)
    path=tmp_path/'data'/RUNTIME/'trades_sim.db'
    cycle(path,NOW,PayloadReferences())
    before=path.read_bytes()
    with PayloadStore(tmp_path/'public.db') as public:
        with pytest.raises(ValueError,match='offline derivative'):
            RecorderStore(path,'2026-09-09',references=PayloadReferences(public,public),raw_namespace=NAMESPACE)
    assert path.read_bytes()==before and not path.with_name('trades_sim_20260908.db').exists()


def test_raw_update_and_private_demotion_preserve_source_receipts(tmp_path,monkeypatch):
    monkeypatch.delenv('PUBLIC_MARKET_DATA_RAW',raising=False)
    path=tmp_path/'native/trades_sim.db'
    with PayloadStore(tmp_path/'public.db') as public:
        refs=PayloadReferences(public,public)
        cycle(path,NOW,refs,native=True)
        store=RecorderStore(path,'2026-09-08',references=refs,raw_namespace=NAMESPACE)
        try:
            original=dict(store.c.execute('SELECT * FROM tracked_events').fetchone())
            with pytest.raises(sqlite3.IntegrityError,match='duplicate'):
                with store.transaction() as c:store.insert(c,'tracked_events',original)
            row={**original,'state':'DONE','missing_count':3}
            source_record=store.c.execute('SELECT _public_record_id FROM main.tracked_events').fetchone()[0]
            with store.transaction() as c:
                store.update_tracked(c,row)
                store.demote_done(c,[original['event_id']])
            final=dict(store.c.execute('SELECT * FROM tracked_events').fetchone())
            assert final=={**original,'state':'WAIT_SETTLEMENT','missing_count':3}
            assert store.c.execute('SELECT _public_record_id FROM main.tracked_events').fetchone()[0]==source_record
            with pytest.raises(RuntimeError,match='rollback'):
                with store.transaction() as c:
                    store.update_tracked(c,{**final,'scheduled_start':'2026-09-09T00:10:00+00:00'})
                    raise RuntimeError('rollback')
            assert dict(store.c.execute('SELECT * FROM tracked_events').fetchone())==final
            verify_raw_dependencies(store.c,references=refs)
        finally:store.close()


def test_same_day_empty_inline_activation_initializes_before_first_publish(tmp_path,monkeypatch):
    monkeypatch.delenv('PUBLIC_MARKET_DATA_RAW',raising=False)
    path=tmp_path/'empty/trades_sim.db'
    RecorderStore(path,'2026-09-08',references=PayloadReferences()).close()
    with PayloadStore(tmp_path/'public.db') as public:
        refs=PayloadReferences(public,public)
        with closing(RecorderStore(path,'2026-09-08',references=refs,raw_namespace=NAMESPACE)) as store:
            assert raw_layout_metadata(store.c)['profile_id']==COCONUT_RECORDER_PROFILE_ID
            FakeClient.current=NOW
            assert Recorder(RecorderConfig(),store,FakeClient).run(NOW)['status']=='SUCCEEDED'
            original=dict(store.c.execute('SELECT * FROM tracked_events').fetchone())
            with store.transaction() as c:
                store.update_tracked(c,{**original,'state':'DONE','missing_count':3})
            assert tuple(store.c.execute('SELECT state,missing_count FROM tracked_events').fetchone())==('DONE',3)


def test_initial_public_failure_leaves_no_broken_canonical_and_can_retry(tmp_path,monkeypatch):
    monkeypatch.delenv('PUBLIC_MARKET_DATA_RAW',raising=False)
    path=tmp_path/'new/trades_sim.db'
    with pytest.raises((ValueError,RuntimeError),match='capabilit|writer|RAW'):
        RecorderStore(path,'2026-09-08',references=PayloadReferences(),raw_namespace=NAMESPACE)
    assert not path.exists() and not list(path.parent.glob('.recorder-init-*'))
    with PayloadStore(tmp_path/'public.db') as public:
        with closing(RecorderStore(path,'2026-09-08',references=PayloadReferences(public,public),raw_namespace=NAMESPACE)) as store:
            assert raw_layout_metadata(store.c)['profile_id']==COCONUT_RECORDER_PROFILE_ID
    assert path.stat().st_nlink==1


def test_failed_rollover_carry_preserves_archive_and_next_cycle_recovery(tmp_path,monkeypatch):
    monkeypatch.delenv('PUBLIC_MARKET_DATA_RAW',raising=False)
    path=tmp_path/'native/trades_sim.db'
    with PayloadStore(tmp_path/'public.db') as public:
        refs=PayloadReferences(public,public)
        _,expected=cycle(path,NOW,refs,native=True)
        original=path.read_bytes()
        real_insert=RecorderStore.insert
        def fail_carry(self,c,table,row):
            if table=='registry_carryovers':raise RuntimeError('carry publication failed')
            return real_insert(self,c,table,row)
        with monkeypatch.context() as scoped:
            scoped.setattr(RecorderStore,'insert',fail_carry)
            with pytest.raises(RuntimeError,match='carry publication failed'):
                RecorderStore(path,'2026-09-09',references=refs,raw_namespace=NAMESPACE)
        archive=path.with_name('trades_sim_20260908.db')
        assert not path.exists() and archive.read_bytes()==original
        assert not list(path.parent.glob('.recorder-init-*'))
        with closing(RecorderStore(path,'2026-09-09',references=refs,raw_namespace=NAMESPACE)) as store:
            assert store.pending('2099-01-01T00:00:00Z')==expected
            assert store.c.execute('SELECT COUNT(*) FROM claim_carryovers').fetchone()[0]==1
            assert store.c.execute('SELECT COUNT(*) FROM registry_carryovers').fetchone()[0]==1
        assert archive.read_bytes()==original and path.stat().st_nlink==1
