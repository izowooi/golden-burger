"""Guava's complete guarded schema, native lifecycle and offline RAW proofs."""
from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from polybot import evidence
from polybot.config import load_config
from polybot.evidence import Repository, _SCHEMA, _triggers
from polybot_observability import market_data_refs, market_data_sqlite
from polybot_observability.market_data_bundle import reference_closure, verify_closure
from polybot_observability.market_data_migrate import file_sha256, _update_digest
from polybot_observability.market_data_raw_guava_reader import guava_read_connection
from polybot_observability.market_data_raw_links import (
    insert_raw_rows, iter_raw_logical_rows, logical_raw_schema_rows, raw_layout_metadata,
    validate_raw_source_schema,
)
from polybot_observability.market_data_raw_migrate import migrate_raw_database, _logical_digest
from polybot_observability.market_data_raw_profiles import GUAVA_PROFILE_ID, raw_profile
from polybot_observability.market_data_raw_schema_guava import GUAVA_LOGICAL_SCHEMA_SHA256, GUAVA_SCHEMA_OBJECTS
from polybot_observability.market_data_refs import PayloadReferences, parse_reference
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_store import PayloadStore
from test_evidence import CONTRACT, CONFIG, T0, T1, T2, event, book, feature, receipt


RUNTIME = 'guava-research-a-v1'
GUAVA_CONTRACT = {**CONTRACT,'job_name':RUNTIME}
NAMESPACE = scalar_namespace('fixture-source','polybot-sim-guava-a','golden-guava',RUNTIME)
TABLES = ('collection_contracts','strategy_configs','run_audits','run_events','source_requests',
          'cycles','events','book_attempts','features','latest_event_state')


def bind(monkeypatch, store, *, writer=None, raw=True):
    refs = PayloadReferences(store,store if writer is None else writer)
    for module in (market_data_refs,market_data_sqlite):
        monkeypatch.setattr(module,'configured_references',lambda:refs)
    monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE','fixture-source')
    monkeypatch.setenv('JOB_NAME','polybot-sim-guava-a')
    monkeypatch.setenv('PUBLIC_MARKET_DATA_RAW','1' if raw else '0')
    return refs


def native(path, refs, contract=None):
    return Repository(path,contract or GUAVA_CONTRACT,raw_profile_id=GUAVA_PROFILE_ID,
                      raw_namespace=NAMESPACE,references=refs)


def publish(repo, run='success', sample=None):
    repo.start_run(run,T0,CONFIG)
    repo.record_request(run,receipt(),book()['raw'])
    repo.publish_cycle(run,T2,[event() if sample is None else sample],[book()],[feature()],
                       {'tracking':{'event-1':{'sport_family':'soccer','expected_token_ids':['public-yes','public-no'],
                         'last_live_at':T1,'attempts':1,'origin_cohort':'private-origin'}}})


def schema_sha(objects):
    digest=hashlib.sha256()
    for row in objects:_update_digest(digest,row)
    return digest.hexdigest()


def test_frozen_full_schema_keys_authority_and_source_clock(tmp_path):
    with closing(Repository(tmp_path/'inline.db',GUAVA_CONTRACT,references=PayloadReferences())) as repo:
        schema=logical_raw_schema_rows(repo.connection,profile_id=GUAVA_PROFILE_ID)
        assert tuple(schema)==GUAVA_SCHEMA_OBJECTS
        assert [sum(row[0]==kind for row in schema) for kind in ('table','index','trigger')]==[10,5,33]
        assert schema_sha(schema)==GUAVA_LOGICAL_SCHEMA_SHA256
        validate_raw_source_schema(repo.connection,profile_id=GUAVA_PROFILE_ID)
        assert tuple(repo.connection.execute('PRAGMA application_id').fetchone())==(0,)
        assert tuple(repo.connection.execute('PRAGMA user_version').fetchone())==(0,)
        repo.connection.execute('CREATE TRIGGER unknown_guard BEFORE INSERT ON cycles BEGIN SELECT 1; END')
        with pytest.raises(ValueError,match='complete logical'):
            validate_raw_source_schema(repo.connection,profile_id=GUAVA_PROFILE_ID)
    profile=raw_profile(GUAVA_PROFILE_ID)
    assert profile.level_tables==profile.mutable_tables==()
    assert profile.tables['events'].public_columns==('event_id','sport_family','league_code')
    assert profile.tables['book_attempts'].primary_key_columns==('run_id','event_id','token_id')
    from polybot_observability.market_data_raw_profiles import RAW_PUBLIC_PROFILES
    assert RAW_PUBLIC_PROFILES['guava-event-source-v1'].condition_field is None
    assert RAW_PUBLIC_PROFILES['guava-event-source-v1'].token_field is None
    assert all(RAW_PUBLIC_PROFILES[p.kind].source_time_field is None for p in profile.tables.values())


def test_native_cold_reader_private_cache_cohort_and_physical_boundary(tmp_path):
    path=tmp_path/'native.db'
    with PayloadStore(tmp_path/'public.db') as store:
        refs=PayloadReferences(store,store)
        with closing(native(path,refs)) as repo:
            publish(repo)
            assert repo.previous_events()=={'event-1':event()}
            assert repo.status()['latest_run']['status']=='SUCCEEDED'
            assert tuple(logical_raw_schema_rows(repo.connection))==GUAVA_SCHEMA_OBJECTS
            assert not repo.connection.execute('PRAGMA foreign_key_check').fetchall()
            repo.start_run('older',T0,CONFIG)
            older=event();older['observed_at']=T0
            repo.publish_cycle('older',T2,[older],[],[],{})
            assert repo.previous_events()=={'event-1':event()}
            repo.start_run('equal',T0,CONFIG)
            equal=event();equal['private_equal_marker']=True
            repo.publish_cycle('equal',T2,[equal],[],[],{})
            assert repo.previous_events()['event-1']['private_equal_marker'] is True
        with sqlite3.connect(path) as physical:
            fields={row[1] for row in physical.execute('PRAGMA table_info(events)')}
            assert 'sport_family' not in fields and 'league_code' not in fields
            assert {'event_id','expected_token_ids_json','_public_record_id'}<=fields
            array_ref,raw_ref=physical.execute('SELECT expected_token_ids_json,raw_gzip FROM events LIMIT 1').fetchone()
            assert parse_reference(array_ref)[0]=='T' and parse_reference(raw_ref)[0]=='B'
            assert physical.execute('SELECT COUNT(*) FROM latest_event_state').fetchone()[0]==1
            assert not physical.execute("SELECT name FROM sqlite_master WHERE name LIKE '%mutable%'").fetchall()
        # A fresh no-cache public reader restores every scalar, mixed and body reference.
        with closing(Repository(path,GUAVA_CONTRACT,read_only=True,references=PayloadReferences(store,cache_bytes=0))) as repo:
            assert repo.previous_events()['event-1']['private_equal_marker'] is True
        with closing(native(path,refs,{**GUAVA_CONTRACT,'config_hash':'other','strategy_source_digest':'other'})) as repo:
            assert repo.previous_events()=={} and repo.latest_summary()=={}
        with guava_read_connection(path,references=PayloadReferences(store,cache_bytes=0),immutable=True) as db:
            assert len(list(iter_raw_logical_rows(db,'book_attempts')))==6
        with pytest.raises((ValueError,RuntimeError)):
            Repository(path,GUAVA_CONTRACT,read_only=True,references=PayloadReferences())


class FailedAck:
    def __init__(self,store,stage):self.store=store;self.stage=stage
    def __getattr__(self,name):return getattr(self.store,name)
    def put_public_projections(self,groups):
        if self.stage=='projection':raise OSError('fixture missing durable scalar ACK')
        return self.store.put_public_projections(groups)
    def append_projection_receipts(self,*args,**kwargs):
        if self.stage=='receipt':raise OSError('fixture missing durable receipt ACK')
        return self.store.append_projection_receipts(*args,**kwargs)


@pytest.mark.parametrize('stage',['projection','receipt'])
def test_failed_ack_rolls_back_whole_publication_preserves_request_and_failure(tmp_path,stage):
    path=tmp_path/'native.db'
    with PayloadStore(tmp_path/'public.db') as store:
        refs=PayloadReferences(store,FailedAck(store,stage))
        with closing(native(path,refs)) as repo:
            repo.start_run('failed',T0,CONFIG)
            repo.record_request('failed',receipt(),book()['raw'])
            with pytest.raises(OSError,match='ACK'):
                repo.publish_cycle('failed',T2,[event()],[book()],[feature()],{})
            for table in ('cycles','events','book_attempts','features','latest_event_state'):
                assert repo.connection.execute(f'SELECT COUNT(*) FROM main.{table}').fetchone()[0]==0
            assert repo.connection.execute('SELECT COUNT(*) FROM source_requests').fetchone()[0]==1
            repo.fail_run('failed',T2,'OSError','publication')
            assert repo.status()['latest_run']['status']=='FAILED'
        with closing(Repository(path,GUAVA_CONTRACT,read_only=True,references=PayloadReferences(store,cache_bytes=0))) as repo:
            assert repo.status()['latest_run']['status']=='FAILED'
            assert repo.connection.execute('SELECT COUNT(*) FROM source_requests').fetchone()[0]==1


def inline_lifecycles(path):
    with closing(Repository(path,GUAVA_CONTRACT,references=PayloadReferences())) as repo:
        # Original SQL accepts negative/sparse rowids; insert them initially with
        # every source guard active, never update or disable evidence guards.
        original=repo._insert
        counts={}
        def sparse(table,row):
            counts[table]=counts.get(table,0)+1
            if table!='collection_contracts':row={'rowid':-91+counts[table]*37,**row}
            return original(table,row)
        repo._insert=sparse
        publish(repo)
        repo.start_run('failed',T0,CONFIG)
        failure=receipt('failed-request');failure.update(status=429,error_type='HttpError')
        repo.record_request('failed',failure,{'partial':True})
        repo.fail_run('failed',T2,'HttpError','discovery')
        repo.start_run('started',T0,CONFIG)
        repo.record_request('started',receipt('pending-request'),None)
        # Excluded census and NULL/missing raw/request/condition remain real rows.
        excluded=event('excluded',tokens=[]);excluded.update(eligible=False,exclusion_reason='unsupported')
        repo.start_run('excluded',T0,CONFIG)
        repo.publish_cycle('excluded',T2,[excluded],[],[],{})


def test_offline_lifecycle_plan_preserves_source_guards_rowids_types_and_full_proofs(tmp_path):
    source=tmp_path/'inline.db';target=tmp_path/'derivative.db'
    inline_lifecycles(source);original=source.read_bytes();sha=file_sha256(source)
    with PayloadStore(tmp_path/'public.db') as store:
        refs=PayloadReferences(store,store)
        result=migrate_raw_database(source,target,source_sha256=sha,namespace=NAMESPACE,references=refs,
                                    profile_id=GUAVA_PROFILE_ID,batch_rows=1)
        assert source.read_bytes()==original
        assert result['status']=='VERIFIED'
        assert result['lifecycle_copy_plan']=='guava-real-started-children-terminal-v1'
        assert result['raw_logical_schema_sha256']==GUAVA_LOGICAL_SCHEMA_SHA256
        with market_data_sqlite.connect(source,references=refs) as before, market_data_sqlite.connect(target,references=refs) as after:
            for table in TABLES:
                assert _logical_digest(before,table,refs,profile_id=GUAVA_PROFILE_ID)==_logical_digest(after,table,refs,profile_id=GUAVA_PROFILE_ID)
            assert tuple(logical_raw_schema_rows(after))==GUAVA_SCHEMA_OBJECTS
            assert not after.execute('PRAGMA foreign_key_check').fetchall()
            rows=after.execute('SELECT rowid,status,typeof(status) FROM main.source_requests ORDER BY rowid').fetchall()
            assert any(row[0]<0 for row in rows)
            assert any(row[1:]==(429,'integer') for row in rows)
        with sqlite3.connect(target) as db:
            db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA recursive_triggers=ON')
            for command in ('INSERT','INSERT OR REPLACE','INSERT OR IGNORE'):
                with pytest.raises(sqlite3.IntegrityError):
                    db.execute(f'{command} INTO run_events SELECT * FROM run_events LIMIT 1')
            with pytest.raises(sqlite3.IntegrityError,match='published cycle cannot fail|run is not open'):
                db.execute("INSERT INTO run_events VALUES('success','FAILED',?,NULL,NULL)",(T2,))
            with pytest.raises(sqlite3.IntegrityError,match='success requires'):
                db.execute("INSERT INTO run_events VALUES('started','SUCCEEDED',?,NULL,NULL)",(T2,))
            columns=[row[1] for row in db.execute('PRAGMA table_info(events)')]
            expressions=["event_id || '-late'" if name=='event_id' else '"'+name+'"' for name in columns]
            with pytest.raises(sqlite3.IntegrityError,match='run is not open'):
                db.execute('INSERT INTO main.events('+','.join(columns)+') SELECT '+','.join(expressions)+
                           " FROM main.events WHERE run_id='success'")
            for action in ('UPDATE features SET reason=reason','DELETE FROM features'):
                with pytest.raises(sqlite3.IntegrityError,match='append-only evidence'):
                    db.execute(action)
        verify_closure(store,reference_closure(target,'golden-guava',immutable=True))
        with guava_read_connection(target,references=PayloadReferences(store,cache_bytes=0),immutable=True) as db:
            assert db.execute("SELECT COUNT(*) FROM run_events WHERE status='FAILED'").fetchone()[0]==1
            assert db.execute("SELECT COUNT(*) FROM run_events WHERE status='SUCCEEDED'").fetchone()[0]==2


def test_reject_populated_inline_and_wrong_owner_without_source_mutation(tmp_path):
    source=tmp_path/'inline.db';inline_lifecycles(source);before=source.read_bytes()
    with PayloadStore(tmp_path/'public.db') as store:
        refs=PayloadReferences(store,store)
        with pytest.raises(ValueError,match='offline derivative'):
            native(source,refs)
        assert source.read_bytes()==before
        with pytest.raises(ValueError,match='runtime/Jenkins'):
            Repository(tmp_path/'bad.db',GUAVA_CONTRACT,raw_profile_id=GUAVA_PROFILE_ID,
                raw_namespace=scalar_namespace('fixture-source','polybot-sim-guava-b','golden-guava',RUNTIME),references=refs)


def test_native_collector_full_six_book_tracking_cohorts_and_failed_history(tmp_path,monkeypatch):
    from polybot import runtime
    from test_runtime import test_full_six_book_cycle_publishes_all_hypotheses
    config=replace(load_config(environment={}),root=tmp_path/'golden-guava')
    monkeypatch.setattr(runtime,'verify_workspace',lambda *args,**kwargs:{'status':'fixture_only'})
    with PayloadStore(tmp_path/'public.db') as store:
        bind(monkeypatch,store)
        # Existing public-client fakes exercise normal identity/features/runtime,
        # continuation books, independent cohorts, and durable failed history.
        test_full_six_book_cycle_publishes_all_hypotheses(config,0)
        with closing(Repository(config.db_path,config.public_snapshot(),read_only=True)) as repo:
            assert raw_layout_metadata(repo.connection)['profile_id']==GUAVA_PROFILE_ID
            assert repo.connection.execute('SELECT COUNT(*) FROM features').fetchone()[0]>0
            assert repo.connection.execute("SELECT COUNT(*) FROM run_events WHERE status='FAILED'").fetchone()[0]==1
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'tools'))
        import guava_collection_health as health
        snapshot=pinned(tmp_path/'pinned/health/trades_sim.db',config.db_path)
        report=health.inspect_snapshot(snapshot['local_path'],health.utc('2026-09-06T08:00:00Z'),
            health.utc('2026-09-06T08:09:00Z'),references=PayloadReferences(store,cache_bytes=0))
        assert report['errors']==[] and report['file_stat_unchanged']
        # The injected old run clock does not backdate runtime's actual terminal
        # clock; health truthfully leaves it pending at the historical cutoff.
        assert report['cadence']['statuses']['PENDING_AT_END']>=1


def pinned(path, source):
    import shutil
    path.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,path)
    path.with_name('manifest.json').write_text(json.dumps({'source':str(source),'pinned_path':str(path),
        'sha256':file_sha256(path),'quick_check':['ok'],'created_at':'2026-09-07T00:00:00Z'}))
    return {'id':'polybot-sim-guava-a:guava-research-a-v1','local_path':str(path),
            'local_sha256':file_sha256(path),'pinned':True}


def replay_fixture(path):
    from polybot.identity import extract_event
    from test_identity import soccer_event
    raw=soccer_event();raw['teams'][0]['ordering']='home';raw['teams'][1]['ordering']='away'
    for market in raw['markets']:
        market.update(active=True,closed=False,acceptingOrders=True,enableOrderBook=True,
                      feesEnabled=False,liquidity=10000,volume=10000)
    sample=extract_event(raw,'soccer',T1);sample['cohort_key']='fixture-cohort'
    assert sample['eligible']
    books=[]
    for side in sample['side_definitions']:
        item=book(side['token_id'])
        item.update(event_id=sample['event_id'],condition_id=side['condition_id'],
                    result_kind=side['result_kind'],outcome_side=side['outcome_side'])
        item['raw']['market']=side['condition_id']
        item['fee_evidence']={'source':'gamma_current_receipt','received_at':T1,'raw':{'feesEnabled':False}}
        books.append(item)
    request=receipt();request.update(method='POST',path='/books',params={'token_ids':sample['expected_token_ids']})
    with closing(Repository(path,GUAVA_CONTRACT)) as repo:
        repo.start_run('replay-run',T0,CONFIG)
        repo.record_request('replay-run',request,[b['raw'] for b in books])
        repo.publish_cycle('replay-run',T2,[sample],books,[],{})
        repo.start_run('failure-run',T0,CONFIG)
        repo.fail_run('failure-run',T2,'TimeoutError','discovery')


def test_frozen_replay_adapter_exact_event_parity_and_reviewed_revision(tmp_path,monkeypatch):
    import importlib.util
    root=Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root/'tools'))
    import guava_public_replay_adapter as current
    import conservative_sports_prospective as prospective
    old_path=root/'tools/tests/fixtures/guava_adapter_frozen_2bd35d.py'
    assert file_sha256(old_path)==current.FROZEN_SOURCE_SHA256
    spec=importlib.util.spec_from_file_location('guava_frozen_original',old_path)
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    original=tmp_path/'inline.db';replay_fixture(original)
    old_source=pinned(tmp_path/'pinned/original/trades_sim.db',original)
    old_events,old_audit=old.read_source(old_source,T0,'2026-09-07T00:00:00Z')
    assert old_audit['stats']['valid_rows']==6 and old_audit['stats']['point_fee_rows']==6
    with PayloadStore(tmp_path/'public.db') as store:
        refs=PayloadReferences(store,store)
        derivative=tmp_path/'derivative.db'
        migrate_raw_database(original,derivative,source_sha256=file_sha256(original),namespace=NAMESPACE,
                             references=refs,profile_id=GUAVA_PROFILE_ID)
        new_source=pinned(tmp_path/'pinned/shared/trades_sim.db',derivative)
        new_events,new_audit=current.read_source(new_source,T0,'2026-09-07T00:00:00Z',
                                                references=PayloadReferences(store,cache_bytes=0))
        assert old_events==new_events
        assert {k:v for k,v in old_audit.items() if k not in ('path','sha256')}=={
            k:v for k,v in new_audit.items() if k not in ('path','sha256')}
        with pytest.raises((ValueError,RuntimeError)):
            current.read_source(new_source,T0,'2026-09-07T00:00:00Z',references=PayloadReferences())
    revision_path=root/'tools/guava_public_replay_adapter.py'
    revision=json.loads((root/'docs/retro/guava-public-reader-v1.json').read_text())
    assert revision['adapter_sha256']==file_sha256(revision_path)
    assert revision['frozen_sha256']==current.FROZEN_SOURCE_SHA256
    with pytest.raises(ValueError,match='candidate freeze'):
        prospective.load_guava_adapter(revision_path,current.FROZEN_SOURCE_SHA256)
    review={'frozen_sha256':current.FROZEN_SOURCE_SHA256,'approved_sha256':file_sha256(revision_path),
            'path':str(revision_path),'review_evidence':'fixture six-source-book exact Event parity',
            'revision_sha256':file_sha256(root/'docs/retro/guava-public-reader-v1.json')}
    assert prospective.load_guava_adapter(revision_path,current.FROZEN_SOURCE_SHA256,review).READER_REVISION==current.READER_REVISION
    with pytest.raises(ValueError,match='candidate freeze'):
        prospective.load_guava_adapter(revision_path,current.FROZEN_SOURCE_SHA256,{**review,'frozen_sha256':'0'*64})


def test_canonical_identity_and_migration_namespace_fail_before_output(tmp_path):
    # This is initially inserted malformed fixture data, not an edit to evidence.
    path=tmp_path/'bad-authority.db'
    with sqlite3.connect(path) as db:
        for statement in (*_SCHEMA,*_triggers()):db.execute(statement)
        identity={k:GUAVA_CONTRACT[k] for k in ('strategy_name','job_name','mode','data_contract')}
        db.execute('INSERT INTO collection_contracts VALUES(1,1,?,?,NULL,?,?,?,?)',
            ('guava-research-v1','guava-research-v1','golden-guava',RUNTIME,'sim',json.dumps(identity)))
        with pytest.raises(ValueError,match='canonical identity'):
            validate_raw_source_schema(db,profile_id=GUAVA_PROFILE_ID)
    good=tmp_path/'source.db';inline_lifecycles(good)
    with PayloadStore(tmp_path/'public.db') as store:
        target=tmp_path/'must-not-exist.db'
        with pytest.raises(ValueError,match='runtime/Jenkins'):
            migrate_raw_database(good,target,source_sha256=file_sha256(good),
                namespace=scalar_namespace('fixture-source','polybot-sim-guava-b','golden-guava','guava-research-b-v1'),
                references=PayloadReferences(store,store),profile_id=GUAVA_PROFILE_ID)
        assert not target.exists()
