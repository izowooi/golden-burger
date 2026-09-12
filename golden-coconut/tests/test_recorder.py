from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import gzip,hashlib,importlib.util,json,sqlite3
import pytest
from polybot.recorder_config import RecorderConfig,registry,slot_start_utc
from polybot.recorder_store import RecorderStore
from polybot.recorder import Recorder,slots_for,window_status,end_anchor
from polybot.recorder_export import iter_rows,carryovers,file_sha
from polybot.classifier import classify_event
from polybot.api.transport import iso_utc

NOW=datetime(2026,9,8,23,59,40,tzinfo=timezone.utc)

def soccer_event(start=NOW-timedelta(minutes=1)):
    reg=registry();family=reg.by_code['soccer'];league=family.payload['domestic_leagues'][0]
    tags=sorted(set(family.payload['required_common_tag_ids']+league['required_tag_ids']))
    event={'id':'9001','gameId':'123','title':'Home FC vs Away FC','slug':'epl-home-away','live':True,'ended':False,'active':True,'closed':False,
      'gameStartTime':iso_utc(start),'sport':{'id':league['sport_id'],'sport':league['code'],'name':league['name'],'primaryTagId':league['primary_tag_id'],'series':league['series_id'],'tags':','.join(map(str,tags))},
      'tags':[{'id':t,'slug':f'tag-{t}'} for t in tags],'series':[{'id':league['series_id'],'slug':league['series_slug']}],'seriesSlug':league['series_slug'],
      'teams':[{'id':'h','name':'Home FC','alias':'Home','league':league['team_league'],'ordering':'home'},{'id':'a','name':'Away FC','alias':'Away','league':league['team_league'],'ordering':'away'}], 'markets':[]}
    for i,caption in enumerate(('Home FC','Draw','Away FC')):
        event['markets'].append({'id':str(50+i),'conditionId':str(80+i),'question':caption+' result','groupItemTitle':caption,'sportsMarketType':'moneyline','outcomes':['Yes','No'],
          'clobTokenIds':[str(100+i*2),str(101+i*2)],'outcomePrices':['0.5','0.5'],'negRisk':True,
          'active':True,'closed':False,'enableOrderBook':True,'acceptingOrders':True,'liquidityNum':0,'volumeNum':0,
          'feeSchedule':{'rate':0.03,'exponent':1}})
    return event

class FakeClient:
    source=soccer_event()
    current=NOW
    calls=[]
    fail_clock=False
    def __init__(self,config,registry,store,budget):self.config,self.registry,self.store,self.budget=config,registry,store,budget;self.receipts={}
    def receipt(self,run,req,kind,body,start,finish):
        raw=json.dumps(body,sort_keys=True).encode();sha=hashlib.sha256(raw).hexdigest()
        row={'attempt_id':req,'run_id':run,'request_id':req,'request_kind':kind,'started_at':iso_utc(start),'received_at':iso_utc(finish),
          'status':'SUCCESS','http_status':200,'sha256':sha,'raw_gzip':gzip.compress(raw,mtime=0),'raw_complete':1,'receipt_json':'{}'}
        self.receipts[req]=row
        with self.store.transaction() as c:self.store.insert(c,'requests',row)
    def discovery(self,run,slot):
        self.calls.append('discovery');out=[]
        for family in self.registry.families:
            events=[deepcopy(self.source)] if family.code=='soccer' else []
            req=run+'-'+family.code;self.receipt(run,req,'gamma_events_keyset',{'events':events},self.current,self.current)
            page=SimpleNamespace(events=events,request_id=req,received_at=iso_utc(self.current))
            out.append((family.code,SimpleNamespace(pages=[page],cursor_complete=True),None))
        return out
    def event(self,run,eid,family):
        self.calls.append(('event',eid));req=run+'-followup';self.receipt(run,req,'gamma_event_followup',self.source,self.current,self.current)
        return SimpleNamespace(event=deepcopy(self.source),request_id=req,received_at=iso_utc(self.current))
    def clocks(self,run,events):
        if self.fail_clock:raise RuntimeError('fake clock crash')
        return SimpleNamespace(raw_messages=(),updates={},status='NO_MATCH',request_id=run+'-clock',completed_at=iso_utc(self.current))
    def books(self,run,tokens,*,groups=None):
        self.calls.append(('books',tuple(tokens)));req=run+'-books';rows=[]
        for token in tokens:
            condition=next(m['conditionId'] for m in self.source['markets'] if token in m['clobTokenIds'])
            rows.append({'asset_id':token,'market':condition,'bids':[{'price':'0.45','size':'20'}],'asks':[{'price':'0.46','size':'20'}]})
        self.receipt(run,req,'recorder_books',rows,self.current+timedelta(seconds=1),self.current+timedelta(seconds=2))
        return {r['asset_id']:{'status':'RECEIVED','raw':r,'request_id':req,'received_at':iso_utc(self.current+timedelta(seconds=2))} for r in rows}
    def close(self):pass

@pytest.fixture(autouse=True)
def fake_reset(monkeypatch):
    FakeClient.source=soccer_event();FakeClient.current=NOW;FakeClient.calls=[];FakeClient.fail_clock=False
    monkeypatch.setattr('polybot.recorder.iso_utc',lambda value=None:iso_utc(value if value is not None else FakeClient.current+timedelta(seconds=4)))


def record(path,at):
    FakeClient.current=at;store=RecorderStore(path,slot_start_utc(at).date().isoformat())
    try:return Recorder(RecorderConfig(),store,FakeClient).run(at)
    finally:store.close()


def test_six_real_books_without_liquidity_or_gamma_probability_gate(tmp_path):
    for m in FakeClient.source['markets']:
        m.pop('outcomePrices');m.update(active=False,closed=True,acceptingOrders=False,enableOrderBook=False)
    path=tmp_path/'trades_sim.db';out=record(path,NOW)
    assert out['status']=='SUCCEEDED' and out['expected_tokens']==6
    rows=list(iter_rows(path,file_sha(path),include_depth=True))
    assert len(rows)==6 and {r['outcome_side'] for r in rows}=={'YES','NO'}
    assert all(r['raw_point_in_time_identity_proven'] for r in rows)
    assert all(r['fee_evidence']['fields']['feeSchedule']['rate']==.03 for r in rows)


def test_jitter_next_due_uses_phase_slot_not_invocation_seconds(tmp_path):
    at=NOW.replace(hour=12,minute=0,second=50);FakeClient.source['gameStartTime']=iso_utc(at-timedelta(minutes=1))
    path=tmp_path/'trades_sim.db';record(path,at);FakeClient.calls=[]
    result=record(path,at.replace(minute=1,second=40))
    assert result['expected_tokens']==6 and ('event','9001') in FakeClient.calls


def test_day_rollover_has_same_cohort_and_proven_working_carry(tmp_path):
    path=tmp_path/'trades_sim.db';first=record(path,NOW)
    second=record(path,NOW+timedelta(seconds=60))
    old=tmp_path/'trades_sim_20260908.db'
    assert old.exists()
    rows=list(iter_rows(old))+list(iter_rows(path))
    assert len(rows)==12 and len({r['config_hash'] for r in rows})==1
    assert len({r['strategy_source_digest'] for r in rows})==1
    carried=list(carryovers(path));assert len(carried)==1 and carried[0]['source_shard']==old.name
    assert rows[-1]['raw_point_in_time_identity_proven']


def test_duplicate_slot_not_repeated_after_utc_rotation(tmp_path):
    path=tmp_path/'trades_sim.db';record(path,NOW);record(path,NOW+timedelta(seconds=60))
    result=record(path,NOW+timedelta(seconds=70))
    assert result['status']=='SKIPPED_DUPLICATE_SLOT'


def test_source_end_anchor_never_uses_older_gamma_receipt_for_later_wss():
    clock={'received_at':iso_utc(NOW+timedelta(seconds=9)),'payload':{'ended':True}}
    end,basis=end_anchor({'ended':False},clock,iso_utc(NOW),{})
    assert end==clock['received_at'] and basis=='FIRST_EXPLICIT_ENDED_RECEIPT_UPPER_BOUND'
    assert window_status(NOW+timedelta(seconds=609),None,end,True)=='IN_WINDOW'
    assert window_status(NOW+timedelta(seconds=610),None,end,True)=='POST_WINDOW_COMPLETE'


def test_unexpected_phase_failure_preserves_expected_slots(tmp_path):
    path=tmp_path/'trades_sim.db';FakeClient.fail_clock=True;result=record(path,NOW)
    assert result['status']=='FAILED' and result['expected_tokens']==6
    assert result['book_status_counts']=={'NOT_ATTEMPTED':6}


def test_export_module_loads_without_project_dependencies_and_honors_publication_cutoff(tmp_path):
    path=tmp_path/'trades_sim.db';record(path,NOW)
    reader=Path(__file__).parents[1]/'src/polybot/recorder_export.py'
    spec=importlib.util.spec_from_file_location('standalone_reader',reader);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert len(list(module.iter_rows(path,end=iso_utc(NOW+timedelta(seconds=5)))))==6
    assert list(module.iter_rows(path,end=iso_utc(NOW+timedelta(seconds=4))))==[]


def test_foreign_database_is_rejected_without_journal_mutation(tmp_path):
    path=tmp_path/'trades_sim.db';c=sqlite3.connect(path);c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE foreign_data(x)');c.commit();c.close();before=path.read_bytes()
    with pytest.raises(Exception):RecorderStore(path,'2026-09-08')
    assert path.read_bytes()==before


def test_probe_is_separate_config_and_outside_window_without_affecting_production(tmp_path):
    FakeClient.source['gameStartTime']=iso_utc(NOW+timedelta(hours=4));FakeClient.source['live']=False
    scheduled=RecorderConfig().snapshot();probe=RecorderConfig().snapshot('PROBE')
    assert scheduled['config_hash']!=probe['config_hash']
    assert probe['observation_mode']=='PROBE'
    production=record(tmp_path/'scheduled'/'trades_sim.db',NOW)
    assert production['expected_tokens']==0
    path=tmp_path/'probe'/'trades_sim.db';store=RecorderStore(path,NOW.date().isoformat())
    try:result=Recorder(RecorderConfig(),store,FakeClient).run(NOW,probe=True)
    finally:store.close()
    rows=list(iter_rows(path));assert result['expected_tokens']==6
    assert all(r['config']['observation_mode']=='PROBE' and r['window_status']=='PROBE_OUTSIDE_WINDOW' for r in rows)


def test_capture_stream_body_cap_preserves_each_failed_retry_receipt(tmp_path,monkeypatch):
    import io,time,requests
    from urllib3.response import HTTPResponse
    from polybot.recorder_http import RecorderClient,MAX_BYTES
    from polybot.api.transport import CycleBudget,PublicApiError
    raw=b'['+b' '*MAX_BYTES+b']'
    def request(self,*args,**kwargs):
        response=requests.Response();response.status_code=200
        response.raw=HTTPResponse(body=io.BytesIO(raw),preload_content=False)
        return response
    monkeypatch.setattr(requests.Session,'request',request)
    path=tmp_path/'trades_sim.db';store=RecorderStore(path,NOW.date().isoformat())
    client=RecorderClient(RecorderConfig(),registry(),store,CycleBudget(time.monotonic(),50,8,50))
    try:
        with pytest.raises(PublicApiError):
            client.transports['soccer'].request_json('GET','https://gamma-api.polymarket.com/events',request_kind='gamma_event_followup',run_id='cap',budget=client.budget)
        rows=store.c.execute('SELECT * FROM requests').fetchall()
        assert len(rows)==3
        assert all(row['status']=='ERROR' and row['raw_complete']==0 for row in rows)
        for row in rows:
            body=gzip.decompress(row['raw_gzip'])
            assert len(body)==MAX_BYTES and hashlib.sha256(body).hexdigest()==row['sha256']
    finally:client.close();store.close()


def test_recorder_retries_each_family_transient_failure_twice(tmp_path):
    from polybot.recorder_http import RecorderClient
    from polybot.api.transport import CycleBudget
    import time

    path=tmp_path/'trades_sim.db';store=RecorderStore(path,NOW.date().isoformat())
    client=RecorderClient(RecorderConfig(),registry(),store,CycleBudget(time.monotonic(),50,8,50))
    try:
        assert all(transport.max_retries == 2 for transport in client.transports.values())
        assert all(transport.attempt_wall_seconds == 10 for transport in client.transports.values())
    finally:client.close();store.close()


def test_strict_json_accepts_current_nfl_page_shape_and_keeps_a_hard_cap():
    from polybot.recorder_http import MAX_JSON_NODES, strict_json

    assert MAX_JSON_NODES == 1_000_000
    assert strict_json(b'{"events":[{"id":"1"}]}')['events'][0]['id'] == '1'


def test_export_cli_terminal_cutoff_and_independent_payout_validation(tmp_path,monkeypatch):
    from polybot.recorder_export import iter_terminals
    for i,m in enumerate(FakeClient.source['markets']):m.update(closed=True,outcomePrices=['1','0'] if i==0 else ['0','1'])
    path=tmp_path/'trades_sim.db';record(path,NOW)
    terminal_rows=list(iter_terminals(path));assert len(terminal_rows)==6
    assert all(r['observation_mode']=='SCHEDULED' and r['config_hash'] and r['strategy_source_digest'] and r['job_name'] for r in terminal_rows)
    script=Path(__file__).parents[1]/'scripts/export_recorder.py'
    spec=importlib.util.spec_from_file_location('export_cli_test',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    output=tmp_path/'cutoff.jsonl'
    monkeypatch.setattr('sys.argv',[str(script),'--db',str(path),'--sha256',file_sha(path),'--output',str(output),'--end',iso_utc(NOW+timedelta(seconds=3))])
    module.main();assert json.loads(output.with_suffix('.jsonl.terminals.json').read_text())==[]
    c=sqlite3.connect(path)
    c.execute('DROP TRIGGER event_observations_update')
    row=c.execute('SELECT terminal_json FROM event_observations').fetchone();proof=json.loads(row[0]);proof['tokens'][0]['payout']=.5
    c.execute('UPDATE event_observations SET terminal_json=?',(json.dumps(proof),));c.commit();c.close()
    assert list(iter_terminals(path))==[]


@pytest.mark.parametrize('column,value',[('http_status',201),('request_kind','unrelated')])
def test_reader_rejects_non_200_or_wrong_request_kind(tmp_path,column,value):
    path=tmp_path/'trades_sim.db';record(path,NOW)
    c=sqlite3.connect(path);c.execute('DROP TRIGGER requests_update')
    c.execute('UPDATE requests SET '+column+'=? WHERE request_kind=?',(value,'recorder_books'));c.commit();c.close()
    rows=list(iter_rows(path));assert len(rows)==6 and not any(r['raw_point_in_time_identity_proven'] for r in rows)


@pytest.mark.parametrize('family',['mlb','nba','nfl','nhl'])
def test_direct_families_keep_exact_two_books_and_unknown_venue(family):
    path=Path(__file__).parent/'fixtures/major_sports_lifecycle_cases.json'
    event=json.loads(path.read_text())['positive'][family]
    for team in event['teams']:team.pop('ordering',None)
    reg=registry();classification=classify_event(event,reg.by_code[family],reg)
    assert classification.accepted
    slots,valid=slots_for(event,classification)
    assert valid and len(slots)==2 and len({s['condition_id'] for s in slots})==1
    assert all(s['verified_role']=='UNKNOWN' and s['result_kind'] is None for s in slots)
    assert all(s['question'] for s in slots)


def test_403_denial_prevents_subsequent_http_and_sports_feed(tmp_path,monkeypatch):
    import time,requests
    from polybot.recorder_http import RecorderClient
    from polybot.api.transport import CycleBudget,PublicApiError
    calls=[]
    def request(self,*args,**kwargs):
        from urllib3.response import HTTPResponse
        import io
        calls.append(args);response=requests.Response();response.status_code=403;response._content=b'';response.raw=HTTPResponse(body=io.BytesIO(b''),preload_content=False);return response
    monkeypatch.setattr(requests.Session,'request',request)
    path=tmp_path/'trades_sim.db';store=RecorderStore(path,NOW.date().isoformat())
    client=RecorderClient(RecorderConfig(),registry(),store,CycleBudget(time.monotonic(),50,8,50))
    try:
        for family in ('soccer','mlb'):
            with pytest.raises(PublicApiError):client.transports[family].request_json('GET','https://gamma-api.polymarket.com/events',request_kind='gamma_event_followup',run_id='denied',budget=client.budget)
        with pytest.raises(PublicApiError):client.clocks('denied',[])
        assert len(calls)==1 and store.c.execute('SELECT COUNT(*) FROM requests').fetchone()[0]==1
    finally:client.close();store.close()


class PairClient(FakeClient):
    sources={}
    max_book_count=None
    malformed=False
    book_orders=[]
    def discovery(self,run,slot):
        out=[]
        for fam in self.registry.families:
            events=list(self.sources.values()) if fam.code=='soccer' else []
            req=run+'-'+fam.code;self.receipt(run,req,'gamma_events_keyset',{'events':events},self.current,self.current)
            out.append((fam.code,SimpleNamespace(pages=[SimpleNamespace(events=deepcopy(events),request_id=req,received_at=iso_utc(self.current))],cursor_complete=True),None))
        return out
    def event(self,run,eid,family):
        value=deepcopy(self.sources[eid])
        if self.malformed and eid=='9001':value['markets']=None
        req=run+'-'+eid;self.receipt(run,req,'gamma_event_followup',value,self.current,self.current)
        return SimpleNamespace(event=value,request_id=req,received_at=iso_utc(self.current))
    def books(self,run,tokens,*,groups=None):
        assert [t for group in groups for t in group]==tokens
        self.book_orders.append(list(tokens));result={t:{'status':'NOT_ATTEMPTED','raw':None,'request_id':None,'received_at':None,'reason':'DeadlineExceeded'} for t in tokens};req=run+'-books';rows=[]
        for token in tokens[:self.max_book_count]:
            condition=next(m['conditionId'] for e in self.sources.values() for m in e['markets'] if token in m['clobTokenIds'])
            rows.append({'asset_id':token,'market':condition,'bids':[{'price':'0.45','size':'20'}],'asks':[{'price':'0.46','size':'20'}]})
        self.receipt(run,req,'recorder_books',rows,self.current+timedelta(seconds=1),self.current+timedelta(seconds=2))
        result.update({r['asset_id']:{'status':'RECEIVED','raw':r,'request_id':req,'received_at':iso_utc(self.current+timedelta(seconds=2))} for r in rows});return result


def pair_setup(at=None):
    a=soccer_event((at or NOW)-timedelta(minutes=1));b=deepcopy(a);b.update(id='9002',slug='epl-home-away-2',gameId='124')
    for m in b['markets']:
        m['conditionId']=str(int(m['conditionId'])+1000);m['clobTokenIds']=[str(int(t)+1000) for t in m['clobTokenIds']]
    PairClient.sources={'9001':a,'9002':b};PairClient.malformed=False;PairClient.max_book_count=None;PairClient.book_orders=[]


def test_deferred_group_gets_next_cycle_priority_and_partial_is_failed(tmp_path,monkeypatch):
    at=NOW.replace(hour=12);pair_setup(at);PairClient.max_book_count=6
    path=tmp_path/'trades_sim.db';store=RecorderStore(path,NOW.date().isoformat())
    monkeypatch.setattr('polybot.recorder.iso_utc',lambda value=None:iso_utc(value if value is not None else PairClient.current+timedelta(seconds=4)))
    try:
        for n in range(3):
            PairClient.current=at+timedelta(minutes=n)
            result=Recorder(RecorderConfig(),store,PairClient).run(PairClient.current)
            assert result['status']=='FAILED' and result['book_status_counts']=={'FULL':6,'NOT_ATTEMPTED':6}
        assert [order[0] for order in PairClient.book_orders]==['100','1100','100']
    finally:store.close()


def test_bad_metadata_preserves_received_sibling_book_and_marks_partial(tmp_path,monkeypatch):
    at=NOW.replace(hour=12);pair_setup(at);PairClient.current=at
    path=tmp_path/'trades_sim.db';store=RecorderStore(path,NOW.date().isoformat())
    monkeypatch.setattr('polybot.recorder.iso_utc',lambda value=None:iso_utc(value if value is not None else PairClient.current+timedelta(seconds=4)))
    try:
        Recorder(RecorderConfig(),store,PairClient).run(at)
        PairClient.current=at+timedelta(minutes=1);PairClient.malformed=True
        result=Recorder(RecorderConfig(),store,PairClient).run(PairClient.current)
        assert result['book_status_counts']=={'FULL':12} and result['status']=='FAILED'
        rows=list(iter_rows(path));assert len(rows)==24
        assert {eid:sum(r['event_id']==eid and r['raw_point_in_time_identity_proven'] for r in rows) for eid in ('9001','9002')}=={'9001':6,'9002':12}
    finally:store.close()


def test_duplicate_wss_messages_preserve_ordinals_without_rolling_back_books(tmp_path):
    class DuplicateClock(FakeClient):
        def clocks(self,run,events):
            raw=b'{"slug":"epl-home-away","live":true,"period":"2H"}'
            return SimpleNamespace(raw_messages=(raw,raw),updates={},status='PARTIAL',request_id=run+'-clock',completed_at=iso_utc(self.current))
    path=tmp_path/'trades_sim.db';store=RecorderStore(path,NOW.date().isoformat())
    try:
        result=Recorder(RecorderConfig(),store,DuplicateClock).run(NOW)
        assert result['status']=='SUCCEEDED' and result['book_status_counts']=={'FULL':6}
        rows=store.c.execute('SELECT ordinal,sha256 FROM clock_observations ORDER BY ordinal').fetchall()
        assert [r[0] for r in rows]==[0,1] and rows[0][1]==rows[1][1]
    finally:store.close()


def test_http_success_with_missing_token_is_incomplete_cycle(tmp_path):
    class MissingBook(FakeClient):
        def books(self,run,tokens,*,groups=None):
            rows=super().books(run,tokens,groups=groups)
            rows[tokens[-1]].update(status='MISSING',raw=None)
            return rows
    path=tmp_path/'trades_sim.db';store=RecorderStore(path,NOW.date().isoformat())
    try:
        result=Recorder(RecorderConfig(),store,MissingBook).run(NOW)
        assert result['status']=='FAILED' and result['book_status_counts']=={'FULL':5,'MISSING':1}
    finally:store.close()


def test_gamma_actual_end_is_not_lost_in_compact_clock_and_refines_upper_bound():
    actual=iso_utc(NOW-timedelta(minutes=2));event={'ended':True,'actualEndTime':actual}
    compact={'received_at':iso_utc(NOW),'payload':{'ended':True}}
    for previous in ({},{'end_anchor':iso_utc(NOW-timedelta(minutes=1)),'end_basis':'FIRST_EXPLICIT_ENDED_RECEIPT_UPPER_BOUND'}):
        value,basis=end_anchor(event,compact,iso_utc(NOW),previous)
        assert value==actual and basis=='SOURCE_actualEndTime'


def test_public_sports_clock_transport_matches_exact_alias_and_keeps_native_clock(monkeypatch):
    import time,inspect
    from polybot.api import sports_client
    from polybot.api.sports_client import SportsClockClient,ClockTarget
    from polybot.api.transport import CycleBudget
    from polybot.config import SportsFeedConfig
    payload={'gameId':'native-nfl-123','period':'Q3','clock':'12:34','score':{'home':7,'away':14},'live':True,'ended':False}
    messages=iter(['ping',json.dumps(payload)]);sent=[];calls=[]
    real_signature=inspect.signature(sports_client.connect)
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def recv(self,timeout):return next(messages)
        def send(self,value):sent.append(value)
    def connect(url,**kwargs):
        real_signature.bind(url,**kwargs);calls.append((url,kwargs));return Socket()
    monkeypatch.setattr(sports_client,'connect',connect);receipts=[]
    batch=SportsClockClient(SportsFeedConfig('wss://sports-api.polymarket.com/ws',3,2,100),receipts.append).collect(
        'clock-run',{'nfl-team-a-b':ClockTarget('nfl-team-a-b','100',('native-nfl-123',))},budget=CycleBudget(time.monotonic(),50,8,50))
    assert batch.status=='OBSERVED' and batch.raw_messages==(json.dumps(payload).encode(),)
    assert batch.updates['nfl-team-a-b'].payload==payload and batch.updates['nfl-team-a-b'].matched_by=='GAME_ID_ALIAS'
    assert sent==['pong'] and calls[0][1]['proxy'] is None and len(receipts)==1


def test_early_midnight_unclaimed_slot_belongs_to_previous_date(tmp_path):
    path=tmp_path/'trades_sim.db';at=NOW.replace(day=9,hour=0,minute=0,second=3)
    result=record(path,at);assert result['status']=='SUCCEEDED'
    c=sqlite3.connect(path)
    assert c.execute('SELECT database_utc_date FROM collection_contracts').fetchone()[0]=='2026-09-08'
    assert c.execute('SELECT slot_utc FROM cycles').fetchone()[0]=='2026-09-08T23:59:30Z'
    c.close();assert all(r['timestamp'].startswith('2026-09-09') for r in iter_rows(path))


def test_early_midnight_duplicate_does_not_rotate_or_issue_http(tmp_path):
    path=tmp_path/'trades_sim.db';record(path,NOW);FakeClient.calls=[]
    result=record(path,NOW.replace(day=9,hour=0,minute=0,second=3))
    assert result['status']=='SKIPPED_DUPLICATE_SLOT' and FakeClient.calls==[]
    assert not (tmp_path/'trades_sim_20260908.db').exists()


def test_wrong_invocation_date_store_rejected_before_http(tmp_path):
    at=NOW.replace(day=9,hour=0,minute=0,second=3);store=RecorderStore(tmp_path/'trades_sim.db','2026-09-09');FakeClient.calls=[]
    try:
        with pytest.raises(ValueError,match='claimed UTC slot'):Recorder(RecorderConfig(),store,FakeClient).run(at)
        assert FakeClient.calls==[]
    finally:store.close()


def test_recorder_v2_uses_a_cursor_complete_page_size():
    config = RecorderConfig()
    assert config.gamma_page_size == 100
    assert config.max_pages_per_family == 20
