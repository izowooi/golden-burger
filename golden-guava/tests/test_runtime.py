from dataclasses import replace
from datetime import datetime,timezone
import sqlite3
import pytest
from polybot.config import load_config
from polybot import runtime


class EmptyClient:
    calls=0
    def __init__(self,config,budget,sink):self.sink=sink
    def fetch_events(self,family):
        EmptyClient.calls+=1
        return [],{'sport_family':family,'cursor_complete':True,'pages':1,'status':'OK'}
    def close(self):pass


class EmptyNews:
    def __init__(self,budget,sink):pass
    def fetch_for_events(self,events,now):return {'by_event':{},'context':[],'errors':[]}
    def close(self):pass


def empty_stream(tokens,budget,sink):return {'status':'NO_TOKENS','messages':[]}


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    cfg=replace(load_config(environment={}),root=tmp_path/'golden-guava')
    monkeypatch.setattr(runtime,'verify_workspace',lambda *a,**k:{'status':'fixture_only'})
    return cfg


def test_empty_complete_census_is_truthful_and_same_slot_never_repeats_http(fixture):
    EmptyClient.calls=0
    kwargs=dict(now=datetime(2026,9,6,8,tzinfo=timezone.utc),client_factory=EmptyClient,
                news_factory=EmptyNews,stream_reader=empty_stream)
    first=runtime.run_research(fixture,**kwargs)
    assert first['event_count']==0 and first['census_complete'] and first['actual_orders_submitted']==0
    assert EmptyClient.calls==5
    second=runtime.run_research(fixture,**kwargs)
    assert second['skipped'] and EmptyClient.calls==5
    with sqlite3.connect(fixture.db_path) as c:
        assert c.execute('SELECT count(*) FROM cycles').fetchone()[0]==1
        assert c.execute("SELECT count(*) FROM run_events WHERE status='SUCCEEDED'").fetchone()[0]==1


def test_incomplete_census_does_not_publish_partial_cycle(fixture):
    class Broken(EmptyClient):
        def fetch_events(self,family):return [],{'cursor_complete':False,'status':'FAILED'}
    with pytest.raises(RuntimeError,match='incomplete'):
        runtime.run_research(fixture,now=datetime(2026,9,6,9,tzinfo=timezone.utc),
            client_factory=Broken,news_factory=EmptyNews,stream_reader=empty_stream)
    with sqlite3.connect(fixture.db_path) as c:
        assert c.execute('SELECT count(*) FROM cycles').fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM run_events WHERE status='FAILED'").fetchone()[0]==1


def test_live_spec_cannot_enter_research_runtime(tmp_path):
    cfg=load_config(job_name='guava-live-lion-a-v1',mode='live',environment={})
    with pytest.raises(ValueError,match='never manages real orders'):runtime.run_research(cfg)


def test_full_six_book_cycle_publishes_all_hypotheses(fixture):
    import importlib.util
    from pathlib import Path
    import json
    module_path=Path(__file__).with_name('test_identity.py')
    spec=importlib.util.spec_from_file_location('runtime_identity_fixture',module_path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    event=module.soccer_event()
    event['id']=next(str(i) for i in range(100) if runtime.shard_for(str(i))==fixture.spec.shard)
    stamp='2026-09-06T08:00:01Z'
    order=[]
    class News(EmptyNews):
        def fetch_for_events(self,events,now):
            order.append('news')
            return super().fetch_for_events(events,now)
    class Full(EmptyClient):
        def fetch_events(self,family):
            payload=[event] if family=='soccer' else []
            receipt={'request_id':'gamma-'+family,'source':'gamma','method':'GET','path':'/events/keyset',
                'params':{},'started_at':stamp,'received_at':stamp,'status':200,'error_type':None}
            self.sink(receipt,{'events':payload,'next_cursor':None})
            return payload,{'sport_family':family,'cursor_complete':True,'pages':1}
        def fetch_books(self,tokens):
            order.append('books')
            raw={token:{'asset_id':token,'bids':[{'price':'0.4','size':'500'}],
                'asks':[{'price':'0.6','size':'500'}]} for token in tokens}
            self.sink({'request_id':'books','source':'clob','method':'POST','path':'/books','params':{},
                'started_at':stamp,'received_at':stamp,'status':200,'error_type':None},list(raw.values()))
            return {token:{'status':'OK','raw':book,'observed_at':stamp,'request_id':'books'} for token,book in raw.items()}
    result=runtime.run_research(fixture,now=datetime(2026,9,6,8,tzinfo=timezone.utc),client_factory=Full,
        news_factory=News,stream_reader=empty_stream)
    assert order==['news','books']
    assert result['event_count']==1 and result['book_attempts']==6 and result['feature_count']==5
    with sqlite3.connect(fixture.db_path) as c:
        assert c.execute('SELECT count(*) FROM book_attempts').fetchone()[0]==6
        assert c.execute('SELECT count(*) FROM features').fetchone()[0]==5
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    # Source changes reset statistical feature caches but cannot abandon known events.
    changed=replace(fixture,config_hash='changed-config',strategy_source_digest='changed-source')
    from polybot.evidence import Repository
    repo=Repository(changed.db_path,changed.public_snapshot())
    try:
        assert repo.latest_summary()=={} and repo.previous_events()=={}
        assert event['id'] in repo.tracking_obligations()
        assert repo.tracking_obligations()[event['id']]['origin_cohort']==fixture.cohort_key
    finally:repo.close()

    # A game leaving the live census still needs every minute's direct books:
    # successful postgame fetches must not back off to 2/3/4/5-minute gaps.
    from copy import deepcopy
    postgame=deepcopy(event)
    postgame.update(live=False,ended=True,closed=False)
    followups=[]
    class Followup(Full):
        response_status='OK'
        def fetch_events(self,family):
            return [],{'sport_family':family,'cursor_complete':True,'pages':1}
        def fetch_event(self,event_id,family=None):
            followups.append((event_id,family,self.response_status))
            self.sink({'request_id':'followup','source':'gamma','method':'GET','path':'/events/'+event_id,
                'params':{},'started_at':stamp,'received_at':stamp,'status':200,'error_type':None},postgame)
            return {'status':self.response_status,'raw':deepcopy(postgame),'observed_at':stamp}
    for minute,second in ((1,55),(2,50),(3,40)):
        stamp=f'2026-09-06T08:{minute:02d}:{second+1:02d}Z'
        result=runtime.run_research(fixture,now=datetime(2026,9,6,8,minute,second,tzinfo=timezone.utc),
            client_factory=Followup,news_factory=EmptyNews,stream_reader=empty_stream)
        assert result['book_attempts']==6
        assert result['tracking'][event['id']]['consecutive_failures']==0
        assert runtime.parsed(result['tracking'][event['id']]['next_due_at'])==datetime(
            2026,9,6,8,minute+1,tzinfo=timezone.utc)
    assert len(followups)==3

    Followup.response_status='ERROR'
    for minute,expected_next in ((4,5),(5,7)):
        stamp=f'2026-09-06T08:{minute:02d}:01Z'
        result=runtime.run_research(fixture,now=datetime(2026,9,6,8,minute,tzinfo=timezone.utc),
            client_factory=Followup,news_factory=EmptyNews,stream_reader=empty_stream)
        assert result['book_attempts']==0
        assert runtime.parsed(result['tracking'][event['id']]['next_due_at']).minute==expected_next
    result=runtime.run_research(fixture,now=datetime(2026,9,6,8,6,tzinfo=timezone.utc),
        client_factory=Followup,news_factory=EmptyNews,stream_reader=empty_stream)
    assert result['book_attempts']==0 and len(followups)==5
    Followup.response_status='OK';stamp='2026-09-06T08:07:01Z'
    result=runtime.run_research(fixture,now=datetime(2026,9,6,8,7,tzinfo=timezone.utc),
        client_factory=Followup,news_factory=EmptyNews,stream_reader=empty_stream)
    assert result['book_attempts']==6 and len(followups)==6
    assert result['tracking'][event['id']]['consecutive_failures']==0
    assert runtime.parsed(result['tracking'][event['id']]['next_due_at']).minute==8
    postgame['id']='different-exact-event';stamp='2026-09-06T08:08:01Z'
    with pytest.raises(ValueError,match='followup event identity mismatch'):
        runtime.run_research(fixture,now=datetime(2026,9,6,8,8,tzinfo=timezone.utc),
            client_factory=Followup,news_factory=EmptyNews,stream_reader=empty_stream)
    with sqlite3.connect(fixture.db_path) as c:
        assert c.execute("SELECT count(*) FROM run_events WHERE status='FAILED'").fetchone()[0]==1
