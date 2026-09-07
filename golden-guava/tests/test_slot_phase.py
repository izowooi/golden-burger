"""Phase admission keeps actual timestamps and all failed claim evidence."""
from dataclasses import replace
from datetime import datetime
from copy import deepcopy
import json,sqlite3
import pytest
from polybot.config import load_config,validate,TradingConfig
from polybot.evidence import Repository,claimed_slot_window
from polybot import runtime

def now(value):return datetime.fromisoformat(value.replace('Z','+00:00'))
class EmptyClient:
    calls=0
    def __init__(self,config,budget,sink):pass
    def fetch_events(self,family):
        EmptyClient.calls+=1
        return [],{'cursor_complete':True,'sport_family':family}
    def close(self):pass
class EmptyNews:
    def __init__(self,budget,sink):pass
    def fetch_for_events(self,events,now):return {'by_event':{},'context':[],'errors':[]}
    def close(self):pass

def run(cfg,at,client=EmptyClient):
    return runtime.run_research(cfg,now=now(at),client_factory=client,news_factory=EmptyNews,
        stream_reader=lambda *args:{'status':'NO_TOKENS','messages':[]})

@pytest.fixture
def cfg(tmp_path,monkeypatch):
    monkeypatch.setattr(runtime,'verify_workspace',lambda *a,**k:{'status':'fixture_only'})
    return replace(load_config(environment={'POLYBOT_SLOT_PHASE_SECONDS':'30'}),root=tmp_path/'golden-guava')

@pytest.mark.parametrize('phase',[-1,60,1.5,True,'30'])
def test_invalid_phase_types_and_bounds(phase):
    with pytest.raises(ValueError,match='slot_phase'):validate(replace(TradingConfig(),slot_phase_seconds=phase))

@pytest.mark.parametrize('phase',['-1','60','1.5','true',' 30','030',''])
def test_invalid_phase_environment(phase):
    with pytest.raises(ValueError,match='slot phase override'):load_config(environment={'POLYBOT_SLOT_PHASE_SECONDS':phase})

def test_phase_is_public_stable_config_and_research_only():
    a=load_config(environment={});b=load_config(environment={'POLYBOT_SLOT_PHASE_SECONDS':'30'})
    assert a.trading.slot_phase_seconds==0 and b.trading.slot_phase_seconds==30
    assert a.config_hash!=b.config_hash and a.strategy_source_digest==b.strategy_source_digest
    assert b.public_snapshot()==b.public_snapshot()
    assert b.public_snapshot()['slot_claim_policy']['phase_seconds']==30
    assert 'claimed_window' not in b.public_snapshot()
    with pytest.raises(ValueError,match='research mode'):
        load_config(job_name='guava-live-lion-a-v1',mode='live',environment={'POLYBOT_SLOT_PHASE_SECONDS':'30'})

def test_boundaries_and_no_backdating():
    a=claimed_slot_window('2026-09-06T08:00:29.999999Z',30)
    b=claimed_slot_window('2026-09-06T08:00:30Z',30)
    assert a['start_utc']=='2026-09-06T07:59:30.000000Z'
    assert a['end_exclusive_utc']==b['start_utc']=='2026-09-06T08:00:30.000000Z'
    assert a['actual_run_started_at']=='2026-09-06T08:00:29.999999Z'

def test_delayed_previous_and_next_timer_are_distinct_phase_windows(cfg):
    EmptyClient.calls=0
    a=run(cfg,'2026-09-06T08:01:00Z');b=run(cfg,'2026-09-06T08:01:39Z')
    assert EmptyClient.calls==10 and a['claimed_window']['start_utc']!=b['claimed_window']['start_utc']
    duplicate=run(cfg,'2026-09-06T08:01:50Z')
    assert duplicate['skipped'] and EmptyClient.calls==10
    assert duplicate['attempted_window']['actual_run_started_at']=='2026-09-06T08:01:50.000000Z'
    assert run(cfg,'2026-09-06T08:00:00Z')['skipped'] and EmptyClient.calls==10
    with sqlite3.connect(cfg.db_path) as c:
        rows=c.execute('select started_at,contract_json from run_audits order by started_at').fetchall()
        assert len(rows)==2 and rows[0][0]=='2026-09-06T08:01:00.000000Z'
        assert json.loads(rows[1][1])['claimed_window']==b['claimed_window']
        assert len(c.execute('select * from strategy_configs').fetchall())==1

def test_phase_cutover_does_not_reclaim_overlapping_prior_start(cfg):
    old=replace(cfg,trading=replace(cfg.trading,slot_phase_seconds=0),config_hash='legacy-phase-zero')
    run(old,'2026-09-06T08:01:50Z')
    assert run(cfg,'2026-09-06T08:02:00Z')['skipped']
    assert 'run_id' in run(cfg,'2026-09-06T08:02:30Z')
    assert run(old,'2026-09-06T08:02:40Z')['skipped']
    assert 'run_id' in run(old,'2026-09-06T08:03:00Z')

def test_failed_run_keeps_claim_and_blocks_same_phase_http(cfg):
    class Fail(EmptyClient):
        def fetch_events(self,family):raise RuntimeError('fixture source failure')
    with pytest.raises(RuntimeError,match='fixture source failure'):run(cfg,'2026-09-06T08:00:40Z',Fail)
    with sqlite3.connect(cfg.db_path) as c:
        row=c.execute('select r.started_at,r.contract_json,e.status from run_audits r join run_events e on e.run_id=r.run_id where e.status=\'FAILED\'').fetchone()
        assert row[0]=='2026-09-06T08:00:40.000000Z'
        assert json.loads(row[1])['claimed_window']==claimed_slot_window(row[0],30)
    assert run(cfg,'2026-09-06T08:01:05Z')['skipped']
    assert 'run_id' in run(cfg,'2026-09-06T08:01:35Z')

@pytest.mark.parametrize('damage',['start','end','phase','actual','extra','config','missing'])
def test_run_window_and_other_config_tampering_fail_closed(cfg,damage):
    stamp='2026-09-06T08:00:40Z';window=claimed_slot_window(stamp,30);snapshot=cfg.public_snapshot()
    if damage=='start':window['start_utc']='2026-09-06T08:00:00.000000Z'
    if damage=='end':window['end_exclusive_utc']='2026-09-06T08:02:30.000000Z'
    if damage=='phase':window['phase_seconds']=0
    if damage=='actual':window['actual_run_started_at']='2026-09-06T08:00:30.000000Z'
    if damage=='extra':window['pretend_success']=True
    if damage=='config':snapshot=deepcopy(snapshot);snapshot['trading']['min_volume']=0
    if damage=='missing':window=None
    cfg.db_path.parent.mkdir(parents=True,exist_ok=True)
    repo=Repository(cfg.db_path,cfg.public_snapshot())
    try:
        with pytest.raises(ValueError):repo.start_run('bad',stamp,snapshot,claimed_window=window)
        assert repo.connection.execute('select count(*) from run_audits').fetchone()[0]==0
    finally:repo.close()
