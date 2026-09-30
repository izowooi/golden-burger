"""Historical v6 real producer/reader/rotation contracts with shared source fields."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
import sqlite3

import pytest

from polybot.collector import Collector
from polybot.db.repository import ResearchRepository,_schema_sha256
from polybot.db import repository as repository_module
from polybot.analyzer import analyze_database
from polybot.api.transport import CycleBudget,canonical_json
from polybot.run_audit import ResearchRunAudit
from polybot_observability import market_data_refs,market_data_sqlite,market_data_index,market_data_raw_links
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadStore
from polybot_observability.market_data_raw_profiles import COCONUT_PROFILE_ID,raw_profile
from polybot_observability.market_data_raw_links import raw_layout_metadata,verify_raw_dependencies,iter_raw_logical_rows,insert_raw_rows
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_migrate import file_sha256
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_private_packets import is_private_packet
from test_collector_analyzer import FakeGamma,FakeClob,FakeClock,FakeClockNoMessage,source_events,fake_storage_metric

PROFILE=raw_profile(COCONUT_PROFILE_ID)


def bind(monkeypatch,store,*,raw=True):
    refs=PayloadReferences(store,store)
    for module in (market_data_refs,market_data_sqlite,market_data_index,market_data_raw_links,repository_module):
        monkeypatch.setattr(module,'configured_references',lambda:refs)
    monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE','fixture-source')
    monkeypatch.setenv('JOB_NAME','polybot-gold')
    monkeypatch.setenv('PUBLIC_MARKET_DATA_RAW','1' if raw else '0')
    return refs


@pytest.fixture
def events(config,make_us_event,make_us_market,make_soccer_event,make_soccer_market):
    return source_events(config,make_us_event,make_us_market,make_soccer_event,make_soccer_market)


def cycle(repo,config,events,minute,price,*,pregame=False):
    run='run-'+str(minute);when=f'2026-08-27T00:{minute:02}:00Z'
    repo.record_run_event(ResearchRunAudit(config,run).event_row('STARTED'))
    product=Collector(config,repo,FakeGamma(events,when),FakeClob(price,when),
        FakeClockNoMessage(when) if pregame else FakeClock(when)).collect(run,slot_start=when,
        budget=CycleBudget(0,monotonic=lambda:0),now=datetime(2026,8,27,0,minute,tzinfo=timezone.utc))
    assert product.fatal_error is None
    return product,ResearchRunAudit(config,run).event_row('SUCCEEDED',product.summary)


def populate(repo,config,events,monkeypatch):
    monkeypatch.setattr('polybot.collector.storage_metric_row',fake_storage_metric)
    repo.register_config()
    for minute,price in ((0,.80),(5,.82)):
        product,terminal=cycle(repo,config,events,minute,price)
        repo.publish_cycle(product.bundle,terminal_event=terminal)
    revised=deepcopy(events)
    for event in revised.values():event.update(startTime='2026-08-27T13:00:00Z',live=False,ended=False)
    product,terminal=cycle(repo,config,revised,10,.82,pregame=True)
    repo.publish_cycle(product.bundle,terminal_event=terminal)


def test_real_historical_publish_reader_and_rotation_preserve_private_state(config,events,monkeypatch,tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=bind(monkeypatch,store)
        repo=ResearchRepository(config,database_utc_date='2026-08-27')
        populate(repo,config,events,monkeypatch)
        with repo.read_connect() as c:
            assert raw_layout_metadata(c)['profile_id']==COCONUT_PROFILE_ID
            assert _schema_sha256(c)==PROFILE.logical_schema_sha256
            assert not c.execute('PRAGMA foreign_key_check').fetchall()
            verify_raw_dependencies(c,references=refs)
            for table in ('event_observations','market_observations','outcome_observations','book_snapshots',
                          'event_tag_observations','event_series_observations','event_team_observations',
                          'sports_clock_observations','game_lifecycle_observations','schedule_revision_observations',
                          'threshold_episodes','game_anchor_observations','episode_path_observations'):
                assert c.execute('SELECT count(*) FROM '+table).fetchone()[0]>0,table
        analysis=analyze_database(repo.path)
        assert analysis['cycle_selection']['selected_cycles']==3
        assert analysis['profitability_conclusion'] is None
        expected_episodes=repo.open_episodes()
        expected_states=repo.latest_threshold_states()
        expected_games=repo.tracked_games()
        assert expected_episodes and expected_states and expected_games
        old_inode=repo.path.stat().st_ino
        monkeypatch.delenv('PUBLIC_MARKET_DATA_RAW')  # Layout inheritance, not an environment-only accident.
        current=ResearchRepository.prepare(config,now=datetime(2026,8,28,0,1,tzinfo=timezone.utc))
        archive=config.db_path.with_name('trades_sim_20260827.db')
        assert archive.stat().st_ino==old_inode and current.path.stat().st_ino!=old_inode
        assert current.open_episodes()==expected_episodes
        actual_states=current.latest_threshold_states()
        for key,old in expected_states.items():
            for field in ('condition_id','token_id','notional_usdc','executable_ask_vwap','executable_ask_shares','observed_at'):
                assert actual_states[key][field]==old[field]
        assert {r['event_cluster_id'] for r in current.tracked_games()}=={r['event_cluster_id'] for r in expected_games}
        with current.read_connect() as c:
            assert raw_layout_metadata(c)['profile_id']==COCONUT_PROFILE_ID
            verify_raw_dependencies(c,references=refs)
            assert c.execute('SELECT count(*) FROM episode_carryovers').fetchone()[0]==len(expected_episodes)
            for row in c.execute('SELECT * FROM tracked_game_carryovers'):
                payload={key:row[key] for key in ('sport_family','event_id','canonical_game_slug','game_id_alias',
                    'event_cluster_id','lifecycle_state','scheduled_start_field','scheduled_start_raw','scheduled_start_utc')}
                assert row['prior_lifecycle_sha256']==hashlib.sha256(canonical_json(payload).encode()).hexdigest()
        with market_data_sqlite.connect(archive.as_uri()+'?mode=ro&immutable=1',uri=True,references=refs) as c:
            assert _schema_sha256(c)==PROFILE.logical_schema_sha256
        with sqlite3.connect(archive) as physical:
            columns={r[1] for r in physical.execute('PRAGMA table_info(event_observations)')}
            assert 'event_id' in columns and 'title' not in columns
            assert is_private_packet(physical.execute('SELECT normalized_json FROM event_observations LIMIT 1').fetchone()[0])
            assert 'entry_ask_vwap' in {r[1] for r in physical.execute('PRAGMA table_info(threshold_episodes)')}
            assert 'outcome_label' not in {r[1] for r in physical.execute('PRAGMA table_info(threshold_episodes)')}


def test_cycle_failure_rolls_back_every_local_parent_and_private_episode(config,events,monkeypatch,tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=bind(monkeypatch,store)
        repo=ResearchRepository(config,database_utc_date='2026-08-27');repo.register_config()
        monkeypatch.setattr('polybot.collector.storage_metric_row',fake_storage_metric)
        product,terminal=cycle(repo,config,events,0,.8)
        def fail(*args):raise RuntimeError('injected before publication commit')
        monkeypatch.setattr(repo,'_before_publish_commit',fail)
        with pytest.raises(RuntimeError,match='before publication'):
            repo.publish_cycle(product.bundle,terminal_event=terminal)
        with repo.read_connect() as c:
            assert c.execute('SELECT count(*) FROM collection_cycles').fetchone()[0]==0
            assert all(c.execute('SELECT count(*) FROM '+table).fetchone()[0]==0 for table in PROFILE.tables)
            assert not c.execute('PRAGMA foreign_key_check').fetchall()
            assert c.execute("SELECT count(*) FROM research_run_events WHERE event_type='SUCCEEDED'").fetchone()[0]==0


def test_inline_historical_derivative_has_identical_health_and_carries(config,events,monkeypatch,tmp_path):
    monkeypatch.setenv('PUBLIC_MARKET_DATA_RAW','0')
    repo=ResearchRepository(config,database_utc_date='2026-08-27');populate(repo,config,events,monkeypatch)
    expected=analyze_database(repo.path)
    checkpoint=sqlite3.connect(repo.path);checkpoint.execute('PRAGMA journal_mode=DELETE');checkpoint.close()
    original_sha=file_sha256(repo.path)
    with PayloadStore(tmp_path/'public.db') as store:
        refs=bind(monkeypatch,store,raw=False)
        target=tmp_path/'derivative.db'
        result=migrate_raw_database(repo.path,target,source_sha256=original_sha,namespace=scalar_namespace(
            'fixture-source','polybot-gold','golden-coconut',config.job_name),references=refs,profile_id=COCONUT_PROFILE_ID)
        assert result['status']=='VERIFIED' and result['raw_profile_id']==COCONUT_PROFILE_ID
        actual=analyze_database(target)
        # Report creation time and physical paths/sizes naturally differ. Every
        # research, denominator, health and evidence field must still agree.
        report_metadata={'generated_at','databases','storage_growth'}
        assert {k:v for k,v in actual.items() if k not in report_metadata} == {
            k:v for k,v in expected.items() if k not in report_metadata}
        assert file_sha256(repo.path)==original_sha
        assert len([t for t,m in result['tables'].items() if 'externalized_raw_rows' in m])==len(PROFILE.tables)


@pytest.mark.parametrize('case',['outcome_index','lifecycle_check','missing_foreign_key'])
def test_original_public_and_private_constraints_precede_ack(config,events,monkeypatch,tmp_path,case):
    with PayloadStore(tmp_path/'public.db') as store:
        bind(monkeypatch,store)
        repo=ResearchRepository(config,database_utc_date='2026-08-27');populate(repo,config,events,monkeypatch)
        table='outcome_observations' if case=='outcome_index' else 'event_observations'
        with repo.read_connect() as c:row=dict(next(iter_raw_logical_rows(c,table)))
        row.pop('__rowid__');row[PROFILE.tables[table].primary_key]='new-key'
        if case=='outcome_index':row.update(outcome_index=2,token_id='new-token')
        if case=='lifecycle_check':row.update(lifecycle_state='UNKNOWN_BAD',event_id='new-event')
        if case=='missing_foreign_key':row.update(raw_payload_id='absent-payload',event_id='new-event')
        writes=[];monkeypatch.setattr(store,'put_public_projections',lambda groups:writes.append(groups))
        with repo.write_connect() as c:
            c.execute('BEGIN IMMEDIATE')
            with pytest.raises(sqlite3.IntegrityError):ResearchRepository._insert(c,table,row)
            c.rollback()
        assert writes==[]


def test_missing_event_identity_stays_local_and_writer_rebind_is_rejected(config,events,monkeypatch,tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        refs=bind(monkeypatch,store)
        repo=ResearchRepository(config,database_utc_date='2026-08-27');populate(repo,config,events,monkeypatch)
        with repo.read_connect() as c:row=dict(next(iter_raw_logical_rows(c,'event_observations')))
        row.pop('__rowid__');row.update(event_observation_id='missing-identity',event_id='MISSING:local-only',
            classification_status='REJECTED',classification_reason='EVENT_ID_MISSING')
        with repo.write_connect() as c:
            c.execute('BEGIN IMMEDIATE');ResearchRepository._insert(c,'event_observations',row);c.commit()
            rid=c.execute("SELECT _public_record_id FROM main.event_observations WHERE event_observation_id='missing-identity'").fetchone()[0]
            record,=store.get_projection_records([rid],store.scalar_authority_identity())
            assert 'event_id' not in PROFILE.tables['event_observations'].public_columns
            assert 'MISSING:local-only' not in record.projection.values
            assert c.execute("SELECT event_id FROM event_observations WHERE event_observation_id='missing-identity'").fetchone()[0]=='MISSING:local-only'
        monkeypatch.setenv('JOB_NAME','other-owner')
        with pytest.raises(RuntimeError,match='source/job/runtime'):
            ResearchRepository(config,database_utc_date='2026-08-27')
        with pytest.raises(RuntimeError,match='source/job/runtime'):
            with repo.write_connect():pass


@pytest.mark.parametrize('fail_second_ack',[False,True])
def test_raw_market_bodies_publish_in_real_batches_before_local_rows(config,events,monkeypatch,tmp_path,fail_second_ack):
    with PayloadStore(tmp_path/'public.db') as store:
        bind(monkeypatch,store)
        repo=ResearchRepository(config,database_utc_date='2026-08-27');populate(repo,config,events,monkeypatch)
        with repo.read_connect() as c:base=dict(next(iter_raw_logical_rows(c,'market_observations')))
        base.pop('__rowid__')
        rows=[{**base,'market_observation_id':f'batch-{i}','condition_id':f'batch-condition-{i}',
            'market_id':f'batch-market-{i}','normalized_json':json.dumps({'volume_num':123+i,'private_decision':'KEEP_LOCAL'}),
            'classification_evidence_json':json.dumps({'sports_market_type':'moneyline','neg_risk':False,
                'labels':['A','B'],'token_ids':[f'batch-{i}-a',f'batch-{i}-b'],'private_rule':'KEEP_LOCAL'})}
            for i in range(12)]
        original_put=store.put_many;calls=[]
        def put(payloads):
            calls.append(len(payloads))
            if fail_second_ack and len(calls)==2:raise RuntimeError('injected second public ACK failure')
            return original_put(payloads)
        monkeypatch.setattr(store,'put_many',put)
        with repo.write_connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if fail_second_ack:
                with pytest.raises(RuntimeError,match='second public ACK'):
                    ResearchRepository._insert_many(c,'market_observations',rows)
                assert c.in_transaction
                assert c.execute("SELECT count(*) FROM main.market_observations WHERE market_observation_id LIKE 'batch-%'").fetchone()[0]==0
                c.rollback()
            else:
                ResearchRepository._insert_many(c,'market_observations',rows);c.commit()
                assert c.execute("SELECT count(*) FROM main.market_observations WHERE market_observation_id LIKE 'batch-%'").fetchone()[0]==len(rows)
                verify_raw_dependencies(c)
        assert len(calls)==2
        assert max(calls)>len(rows)
