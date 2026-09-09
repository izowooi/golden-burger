import copy
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import white_legacy_grid as w

START='2026-08-29T09:00:00Z';END='2026-08-29T11:00:00Z'
SCHEMA='''
CREATE TABLE research_config_versions(config_hash TEXT PRIMARY KEY,strategy_source_digest TEXT,job_name TEXT,mode TEXT,config_json TEXT);
CREATE TABLE research_run_events(run_id TEXT,event_type TEXT,observed_at TEXT,config_hash TEXT,strategy_source_digest TEXT);
CREATE TABLE api_requests(request_id TEXT PRIMARY KEY,run_id TEXT,method TEXT,url TEXT,started_at TEXT,completed_at TEXT,status TEXT,http_status INTEGER,response_sha256 TEXT);
CREATE TABLE raw_payloads(payload_id TEXT PRIMARY KEY,run_id TEXT,payload_kind TEXT,request_id TEXT,observed_at TEXT,sha256 TEXT,raw_bytes INTEGER,payload_gzip BLOB);
CREATE TABLE market_observations(observation_id TEXT PRIMARY KEY,sweep_id TEXT,run_id TEXT,event_id TEXT,event_title TEXT,condition_id TEXT,observed_at TEXT,outcome_labels_json TEXT,token_ids_json TEXT);
CREATE TABLE outcome_observations(outcome_observation_id TEXT PRIMARY KEY,market_observation_id TEXT,sweep_id TEXT,run_id TEXT,event_id TEXT,condition_id TEXT,token_id TEXT,outcome_index INTEGER,outcome_label TEXT,observed_at TEXT);
CREATE TABLE orderbook_snapshots(snapshot_id TEXT PRIMARY KEY,run_id TEXT,token_id TEXT,request_id TEXT,observed_at TEXT,raw_book_sha256 TEXT);
CREATE TABLE orderbook_token_attempts(attempt_id TEXT PRIMARY KEY,run_id TEXT,token_id TEXT,status TEXT,request_id TEXT,observed_at TEXT);
CREATE TABLE resolution_observations(resolution_id TEXT,run_id TEXT,condition_id TEXT,observed_at TEXT,request_id TEXT,raw_market_sha256 TEXT,evidence_json TEXT);
'''

def insert(c,t,**kw):c.execute('INSERT INTO '+t+'('+','.join(kw)+') VALUES('+','.join('?'for _ in kw)+')',tuple(kw.values()))
def raw_event(family='soccer'):
    _,reg=w.classifier();f=reg.by_code[family]
    if family=='soccer':
        p=f.payload['domestic_leagues'][0];sport={'id':p['sport_id'],'sport':p['code'],'name':p['name'],'primaryTagId':p['primary_tag_id'],'series':p['series_id']};ids=f.payload['required_common_tag_ids']+p['required_tag_ids'];seriesid=p['series_id'];slug=p['series_slug'];league=p['team_league']
    else:
        p=f.payload['sport'];sport={'id':p['id'],'sport':p['code'],'name':p['name'],'primaryTagId':p['primary_tag_id'],'series':p['root_id']};ids=f.payload['required_event_tag_ids'];seriesid=p['root_id'];slug=family;league=family
    sport['tags']=','.join(str(i)for i in ids)
    e={'id':family+'event','title':'Team A vs. Team B','slug':family+'-team-a-team-b-2026-08-29','sport':sport,'tags':[{'id':i}for i in ids],
       'series':[{'id':seriesid,'slug':slug,'ticker':slug,'title':family.upper(),'seriesType':'single','recurrence':'daily'}],'seriesSlug':slug,
       'teams':[{'id':'a','name':'Team A','alias':'A','league':league,'ordering':'away'},{'id':'b','name':'Team B','alias':'B','league':league,'ordering':'home'}],
       'active':True,'closed':False,'live':True,'ended':False,'period':'1H','elapsed':'3','startTime':'2026-08-29T10:00:00Z','markets':[]}
    for i,descriptor in enumerate(['Team A','Draw','Team B']if family=='soccer'else['Team A vs. Team B']):
        labels=['Yes','No']if family=='soccer'else['Team A','Team B'];cid=family+str(i)
        e['markets'].append({'conditionId':cid,'sportsMarketType':'moneyline','question':descriptor,'groupItemTitle':descriptor,'outcomes':labels,'clobTokenIds':[cid+'y',cid+'n'],'outcomePrices':['0.5','0.5'],'negRisk':family=='soccer','active':True,'closed':False,'enableOrderBook':True,'acceptingOrders':True,'feesEnabled':True,'feeSchedule':{'rate':.05,'exponent':1,'takerOnly':True},'liquidityNum':10000,'volumeNum':10000})
    return e

class Fixture:
    runtime='legacy-runtime'
    def __init__(self,path):
        self.path=Path(path).resolve()/'trades_sim.db';self.c=sqlite3.connect(self.path);self.c.executescript(SCHEMA)
    def run(self,n=0,digest='a'*64,status='SUCCEEDED',minutes=5):
        run='r'+str(n);cfg='cfg'+digest[0];ts=[f'2026-08-29T10:{n*5:02}:{s:02}Z'for s in range(5)]
        conf={'job_name':self.runtime,'simulation_mode':True,'trading':{'strategy_source_digest':digest,'cadence_minutes':minutes,'cadence_arm':'FAST_1M'if minutes==1 else'CONTROL_5M'}}
        self.c.execute('INSERT OR IGNORE INTO research_config_versions VALUES(?,?,?,?,?)',(cfg,digest,self.runtime,'sim',json.dumps(conf)))
        for kind,at in [('STARTED',ts[0]),(status,ts[4])]:insert(self.c,'research_run_events',run_id=run,event_type=kind,observed_at=at,config_hash=cfg,strategy_source_digest=digest)
        return run,ts
    def payload(self,run,req,kind,body,start,end):
        data=w.canonical(body).encode();h=hashlib.sha256(data).hexdigest();isgamma=kind=='GAMMA_EVENT_PAGE';book=kind=='CLOB_BOOK_BATCH'
        url='https://gamma-api.polymarket.com/events/keyset'if isgamma else'https://clob.polymarket.com/books'if book else'https://clob.polymarket.com/markets/'+body['condition_id']
        insert(self.c,'api_requests',request_id=req,run_id=run,method='POST'if book else'GET',url=url,started_at=start,completed_at=end,status='SUCCESS',http_status=200,response_sha256=h)
        insert(self.c,'raw_payloads',payload_id=req,run_id=run,payload_kind=kind,request_id=req,observed_at=end,sha256=h,raw_bytes=len(data),payload_gzip=gzip.compress(data))
        return h
    def event(self,family='soccer',n=0,digest='a'*64,status='SUCCEEDED',mutate=None,missing=None):
        run,ts=self.run(n,digest,status);event=raw_event(family)
        if mutate:mutate(event)
        self.payload(run,run+'g','GAMMA_EVENT_PAGE',{'events':[event]},ts[0],ts[1]);books=[]
        for market in event['markets']:
            cid=market['conditionId'];mid=run+cid
            insert(self.c,'market_observations',observation_id=mid,sweep_id=run+'s',run_id=run,event_id=event['id'],event_title=event['title'],condition_id=cid,observed_at=ts[1],outcome_labels_json=json.dumps(market['outcomes']),token_ids_json=json.dumps(market['clobTokenIds']))
            for ix in ([0]if family=='soccer'else[0,1]):
                token=market['clobTokenIds'][ix]
                insert(self.c,'outcome_observations',outcome_observation_id=run+token,market_observation_id=mid,sweep_id=run+'s',run_id=run,event_id=event['id'],condition_id=cid,token_id=token,outcome_index=ix,outcome_label=market['outcomes'][ix],observed_at=ts[1])
                insert(self.c,'orderbook_token_attempts',attempt_id=run+token,run_id=run,token_id=token,status='MISSING'if token==missing else'OBSERVED',request_id=run+'b',observed_at=ts[3])
                if token==missing:continue
                b={'market':cid,'asset_id':token,'asks':[{'price':'.6','size':'100'}],'bids':[{'price':'.59','size':'100'}]};books.append(b)
                insert(self.c,'orderbook_snapshots',snapshot_id=run+token,run_id=run,token_id=token,request_id=run+'b',observed_at=ts[3],raw_book_sha256=hashlib.sha256(w.canonical(b).encode()).hexdigest())
        self.payload(run,run+'b','CLOB_BOOK_BATCH',books,ts[2],ts[3]);return event
    def resolve(self,family='soccer',digest='a'*64):
        run,ts=self.run(1,digest)
        for i,m in enumerate(raw_event(family)['markets']):
            tokens=[{'token_id':t,'outcome':label,'price':int(j==(0 if i==0 else 1)),'winner':j==(0 if i==0 else 1)}for j,(t,label)in enumerate(zip(m['clobTokenIds'],m['outcomes']))]
            body={'condition_id':m['conditionId'],'closed':True,'tokens':tokens};req=run+m['conditionId'];h=self.payload(run,req,'CLOB_MARKET_RESOLUTION',body,ts[2],ts[3])
            insert(self.c,'resolution_observations',resolution_id=req,run_id=run,condition_id=m['conditionId'],observed_at=ts[3],request_id=req,raw_market_sha256=h,evidence_json=json.dumps({'closed':True,'tokens':tokens}))
    def source(self):
        self.c.commit();h=w.sha(self.path);manifest=self.path.with_name('manifest.json');manifest.write_text(json.dumps({'source_key':'src','pinned_path':str(self.path),'sha256':h,'quick_check':['ok']}))
        return {'id':'src','source_key':'src','strategy':'golden-watermelon','runtime_job':self.runtime,'local_path':str(self.path),'local_sha256':h,'manifest':str(manifest),'pinned':True}

class Tests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.f=Fixture(self.tmp.name)
    def tearDown(self):self.f.c.close();self.tmp.cleanup()
    def read(self,end=END):return w.read_source(self.f.source(),START,end)
    def test_soccer_yes3_exact_raw_receipts(self):
        self.f.event();events,a=self.read();self.assertEqual(a['stats']['valid_rows'],3);self.assertTrue(events[0].groups[0].complete)
        self.assertEqual(events[0].config['cadence_seconds'],300);self.assertFalse(events[0].config['one_minute_replay_eligible'])
        self.assertTrue(all(s.fee_rate==.05 and s.open_observed for s in events[0].groups[0].snaps))
    def test_mlb_nfl_no_native_clock_and_verified_venue(self):
        for sport in ('mlb','nfl'):
            with self.subTest(sport=sport),tempfile.TemporaryDirectory()as d:
                f=Fixture(d);f.event(sport);events,a=w.read_source(f.source(),START,END)
                self.assertTrue(events[0].groups[0].complete);self.assertTrue(all(s.minute is None for s in events[0].groups[0].snaps));self.assertEqual({s.verified_role for s in events[0].groups[0].snaps},{'HOME','AWAY'});f.c.close()
    def test_non_major_identity_not_soccer_default(self):
        self.f.event(mutate=lambda e:e['sport'].update(sport='lol'))
        events,a=self.read();self.assertEqual(events,[]);self.assertEqual(a['stats']['unmapped_book:unsupported_or_unproven_major_sport_identity'],3)
    def test_whole_game_prop_rejected(self):
        self.f.event('mlb',mutate=lambda e:e['markets'][0].update(sportsMarketType='child_moneyline'))
        events,a=self.read();self.assertEqual(events,[])
    def test_missing_book_remains_invalid_no_fake_zero(self):
        self.f.event(missing='soccer2y');events,a=self.read();ss=events[0].groups[0].snaps;self.assertEqual(len(ss),3);self.assertEqual(sum(s.valid for s in ss),2);self.assertIsNone(next(s.midpoint for s in ss if not s.valid))
    def test_raw_hash_tamper_fails(self):
        self.f.event();self.f.c.execute("UPDATE raw_payloads SET sha256='bad' WHERE payload_kind='CLOB_BOOK_BATCH'")
        events,a=self.read();self.assertEqual(a['stats']['valid_rows']if'valid_rows'in a['stats']else 0,0)
    def test_book_request_cannot_start_before_metadata_receipt(self):
        self.f.event();self.f.c.execute("UPDATE api_requests SET started_at='2026-08-29T10:00:00.5Z' WHERE method='POST'")
        events,a=self.read();self.assertFalse(any(s.valid for s in events[0].groups[0].snaps))
        self.assertEqual(a['stats']['invalid:book_request_predates_metadata_receipt'],3)
    def test_config_embedded_source_digest_must_match_run(self):
        self.f.event();cfg=json.loads(self.f.c.execute('SELECT config_json FROM research_config_versions').fetchone()[0]);cfg['trading']['strategy_source_digest']='f'*64
        self.f.c.execute('UPDATE research_config_versions SET config_json=?',(json.dumps(cfg),))
        events,a=self.read();self.assertEqual(events,[]);self.assertEqual(a['stats']['attempts_run_identity_missing'],3)
    def test_same_run_outcome_sweep_guard_even_cached_market(self):
        self.f.event('mlb');self.f.c.execute("UPDATE outcome_observations SET sweep_id='foreign' WHERE outcome_index=1")
        events,a=self.read();self.assertEqual(a['stats']['valid_rows'],1);self.assertFalse(events[0].groups[0].complete)
    def test_future_success_publication_invalid(self):
        self.f.event();events,a=self.read('2026-08-29T10:00:04Z');self.assertFalse(any(s.valid for s in events[0].groups[0].snaps))
    def test_failed_run_invalid_preserves_interval(self):
        self.f.event(status='FAILED');events,a=self.read();self.assertFalse(events[0].groups[0].complete);self.assertTrue(all(len(x)==2 for x in events[0].failures))
    def test_source_epoch_separated(self):
        self.f.event();self.f.event(n=1,digest='b'*64);events,a=self.read();self.assertEqual(len(events),2);self.assertEqual(len({e.cohort for e in events}),2)
    def test_terminal_onehot_triad_and_same_cohort(self):
        self.f.event();self.f.resolve();events,a=self.read();self.assertEqual(len(events[0].terminals),6)
        self.assertEqual(a['stats']['verified_terminal_token_facts'],6)
    def test_foreign_cohort_terminal_not_inherited(self):
        self.f.event();self.f.resolve(digest='f'*64);events,a=self.read();self.assertFalse(events[0].terminals)
    def test_source_unchanged_and_pin_guard(self):
        self.f.event();s=self.f.source();before=w.sha(self.f.path);w.read_source(s,START,END);self.assertEqual(before,w.sha(self.f.path));s['source_key']='wrong'
        with self.assertRaises(ValueError):w.read_source(s,START,END)

if __name__=='__main__':unittest.main()
