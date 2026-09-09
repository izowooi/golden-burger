from pathlib import Path
import gzip
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import coconut_historical_grid as adapter

START = '2026-08-29T09:00:00Z'
END = '2026-08-29T11:00:00Z'

SCHEMA = '''
CREATE TABLE schema_metadata(singleton INTEGER,database_utc_date TEXT,data_contract TEXT,schema_profile TEXT);
CREATE TABLE research_config_versions(config_hash TEXT PRIMARY KEY,strategy_source_digest TEXT,job_name TEXT,mode TEXT,lifecycle_mode TEXT,config_json TEXT);
CREATE TABLE research_run_events(run_id TEXT,event_type TEXT,observed_at TEXT,config_hash TEXT,strategy_source_digest TEXT);
CREATE TABLE collection_cycles(cycle_id TEXT PRIMARY KEY,run_id TEXT,job_name TEXT,mode TEXT,slot_start_utc TEXT,completed_at TEXT);
CREATE TABLE api_requests(logical_request_id TEXT,run_id TEXT,request_kind TEXT,method TEXT,url TEXT,started_at TEXT,completed_at TEXT,status TEXT,http_status INTEGER,response_sha256 TEXT);
CREATE TABLE raw_payloads(raw_payload_id TEXT PRIMARY KEY,cycle_id TEXT,run_id TEXT,logical_request_id TEXT,observed_at TEXT,sha256 TEXT,payload_gzip BLOB,raw_bytes INTEGER,payload_kind TEXT);
CREATE TABLE event_observations(event_observation_id TEXT PRIMARY KEY,cycle_id TEXT,run_id TEXT,event_id TEXT,raw_payload_id TEXT,observed_at TEXT,classification_status TEXT,sport_family TEXT,event_cluster_id TEXT,title TEXT,season_phase TEXT);
CREATE TABLE market_observations(market_observation_id TEXT PRIMARY KEY,event_observation_id TEXT,cycle_id TEXT,run_id TEXT,event_id TEXT,condition_id TEXT,labels_json TEXT,token_ids_json TEXT,result_kind TEXT,structure_eligible INTEGER,observed_at TEXT,sport_family TEXT);
CREATE TABLE outcome_observations(outcome_observation_id TEXT PRIMARY KEY,market_observation_id TEXT,cycle_id TEXT,run_id TEXT,token_id TEXT,condition_id TEXT,outcome_index INTEGER,outcome_label TEXT,sport_family TEXT,observed_at TEXT,structure_eligible INTEGER);
CREATE TABLE book_token_attempts(book_attempt_id TEXT PRIMARY KEY,cycle_id TEXT,run_id TEXT,token_id TEXT,status TEXT,logical_request_id TEXT,observed_at TEXT);
CREATE TABLE book_snapshots(book_snapshot_id TEXT PRIMARY KEY,cycle_id TEXT,run_id TEXT,token_id TEXT,logical_request_id TEXT,observed_at TEXT,canonical_sha256 TEXT,book_gzip BLOB,canonical_bytes INTEGER);
CREATE TABLE resolution_observations(cycle_id TEXT,run_id TEXT,condition_id TEXT,event_cluster_id TEXT,observed_at TEXT,resolution_status TEXT,logical_request_id TEXT,raw_sha256 TEXT,evidence_json TEXT);
'''


def text(x): return json.dumps(x,sort_keys=True,separators=(',',':'))
def digest(x): return hashlib.sha256(x).hexdigest()
def insert(c,t,**kw):
    c.execute('INSERT INTO '+t+'('+','.join(kw)+') VALUES('+','.join('?' for _ in kw)+')',tuple(kw.values()))


class Fixture:
    runtime='coconut-major-sports-lifecycle-5m-v7'
    def __init__(self, directory):
        self.path=Path(directory).resolve()/'pinned'/'trades_sim.db';self.path.parent.mkdir()
        self.c=sqlite3.connect(self.path);self.c.executescript(SCHEMA)
        self.c.execute("INSERT INTO schema_metadata VALUES(1,'2026-08-29','major-sports-lifecycle-census-v7','golden-coconut-create-only-lifecycle-v6')")
    def times(self,n):
        minute=n*5
        return [f'2026-08-29T10:{minute:02}:{second:02}Z' for second in range(5)]
    def run(self,n=0,source='a'*64,status='SUCCEEDED'):
        ts=self.times(n);run='r'+str(n);cycle='c'+str(n);cfg='cfg'+source[0]
        self.c.execute('INSERT OR IGNORE INTO research_config_versions VALUES(?,?,?,?,?,?)',(cfg,source,self.runtime,'sim','archive_only','{}'))
        for kind,t in [('STARTED',ts[0]),(status,ts[4])]:insert(self.c,'research_run_events',run_id=run,event_type=kind,observed_at=t,config_hash=cfg,strategy_source_digest=source)
        insert(self.c,'collection_cycles',cycle_id=cycle,run_id=run,job_name=self.runtime,mode='sim',slot_start_utc=ts[0],completed_at=ts[4])
        return run,cycle,ts
    def payload(self,run,cycle,logical,payload,t,kind):
        body=text(payload).encode();pid=logical+'-raw'
        insert(self.c,'raw_payloads',raw_payload_id=pid,cycle_id=cycle,run_id=run,logical_request_id=logical,observed_at=t,sha256=digest(body),payload_gzip=gzip.compress(body),raw_bytes=len(body),payload_kind=kind)
        return pid,digest(body)
    def request(self,run,logical,kind,host,start,end,hash_,method='GET'):
        insert(self.c,'api_requests',logical_request_id=logical,run_id=run,request_kind=kind,method=method,url='https://'+host+'/public',started_at=start,completed_at=end,status='SUCCESS',http_status=200,response_sha256=hash_)
    def event(self,family='soccer',n=0,source='a'*64,status='SUCCEEDED',missing=None,scheduled=None,event_flags=None):
        run,cycle,ts=self.run(n,source,status);eid=family+'-game';event={'id':eid,'title':family+' fixture','active':True,'closed':False,'live':True,'ended':False,'period':'1H','elapsed':'2','markets':[],'teams':[]}
        if event_flags:event.update(event_flags)
        if scheduled is not None:event['gameStartTime']=scheduled
        kinds=['HOME','DRAW','AWAY'] if family=='soccer' else ['DIRECT']
        for kind in kinds:
            cid=family+'-'+kind;tokens=[cid+'-0',cid+'-1'];labels=['Yes','No'] if family=='soccer' else ['Team A','Team B']
            event['markets'].append({'conditionId':cid,'clobTokenIds':tokens,'outcomes':labels,'active':True,'closed':False,'acceptingOrders':True,'enableOrderBook':True,'feesEnabled':True,'feeSchedule':{'rate':.05,'exponent':1,'takerOnly':True}})
        logical=run+'-meta';pid,hash_=self.payload(run,cycle,logical,{'events':[event]},ts[1],'GAMMA_EVENT_KEYSET_PAGE');self.request(run,logical,'gamma_events_keyset','gamma-api.polymarket.com',ts[0],ts[1],hash_)
        eobs=run+'-event';insert(self.c,'event_observations',event_observation_id=eobs,cycle_id=cycle,run_id=run,event_id=eid,raw_payload_id=pid,observed_at=ts[1],classification_status='ACCEPTED',sport_family=family,event_cluster_id=family+':game',title=event['title'],season_phase='UNKNOWN')
        logical=run+'-book';self.request(run,logical,'clob_full_books','clob.polymarket.com',ts[2],ts[3],'b'*64,'POST')
        for kind,market in zip(kinds,event['markets']):
            cid=market['conditionId'];mid=run+cid
            insert(self.c,'market_observations',market_observation_id=mid,event_observation_id=eobs,cycle_id=cycle,run_id=run,event_id=eid,condition_id=cid,labels_json=text(market['outcomes']),token_ids_json=text(market['clobTokenIds']),result_kind=kind,structure_eligible=1,observed_at=ts[1],sport_family=family)
            for idx in ([0] if family=='soccer' else [0,1]):
                token=market['clobTokenIds'][idx];oid=run+token
                insert(self.c,'outcome_observations',outcome_observation_id=oid,market_observation_id=mid,cycle_id=cycle,run_id=run,token_id=token,condition_id=cid,outcome_index=idx,outcome_label=market['outcomes'][idx],sport_family=family,observed_at=ts[1],structure_eligible=1)
                insert(self.c,'book_token_attempts',book_attempt_id=oid,cycle_id=cycle,run_id=run,token_id=token,status='MISSING' if token==missing else 'OBSERVED',logical_request_id=logical,observed_at=ts[3])
                if token==missing:continue
                book={'asset_id':token,'market':cid,'asks':[{'price':'.6','size':'100'}],'bids':[{'price':'.59','size':'100'}]};body=text(book).encode()
                insert(self.c,'book_snapshots',book_snapshot_id=oid,cycle_id=cycle,run_id=run,token_id=token,logical_request_id=logical,observed_at=ts[3],canonical_sha256=digest(body),book_gzip=gzip.compress(body),canonical_bytes=len(body))
    def terminals(self,source='a'*64):
        run,cycle,ts=self.run(1,source)
        for i,kind in enumerate(['HOME','DRAW','AWAY']):
            cid='soccer-'+kind;payout=1 if i==0 else 0;payload={'condition_id':cid,'closed':True,'tokens':[{'token_id':cid+'-0','outcome':'Yes','price':payout,'winner':bool(payout)},{'token_id':cid+'-1','outcome':'No','price':1-payout,'winner':not bool(payout)}]};logical=run+cid
            _,hash_=self.payload(run,cycle,logical,payload,ts[3],'CLOB_PUBLIC_RESOLUTION');self.request(run,logical,'clob_public_resolution','clob.polymarket.com',ts[2],ts[3],hash_)
            insert(self.c,'resolution_observations',cycle_id=cycle,run_id=run,condition_id=cid,event_cluster_id='soccer:game',observed_at=ts[3],resolution_status='RESOLVED',logical_request_id=logical,raw_sha256=hash_,evidence_json=text(payload))
    def source(self):
        self.c.commit();sha=adapter.sha(self.path);m={'source_key':'test-source','pinned_path':str(self.path),'sha256':sha,'quick_check':['ok']};mp=self.path.with_name('manifest.json');mp.write_text(json.dumps(m));return {'id':'fixture','source_key':'test-source','runtime_job':self.runtime,'pinned':True,'local_path':str(self.path),'local_sha256':sha,'manifest':str(mp)}


class Tests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.f=Fixture(self.temp.name)
    def tearDown(self):self.f.c.close();self.temp.cleanup()
    def read(self,end=END):return adapter.read_source(self.f.source(),START,end)
    def test_actual_yes3_and_no_synthetic_no(self):
        self.f.event();source=self.f.source();before=source['local_sha256'];events,audit=adapter.read_source(source,START,END)
        self.assertEqual(len(events),1);self.assertTrue(events[0].groups[0].complete);self.assertEqual(len(events[0].groups[0].snaps),3)
        self.assertEqual({s.slot[1] for s in events[0].groups[0].snaps},{'YES'});self.assertFalse(events[0].config['one_minute_replay_eligible']);self.assertEqual(adapter.sha(self.f.path),before)
    def test_mlb_and_nfl_direct_two(self):
        for family in ('mlb','nfl'):
            with self.subTest(family=family),tempfile.TemporaryDirectory() as d:
                f=Fixture(d);f.event(family);events,_=adapter.read_source(f.source(),START,END);self.assertTrue(events[0].groups[0].complete);self.assertEqual(len(events[0].groups[0].snaps),2);f.c.close()
    def test_event_open_flags_required_separately_from_valid_raw_book(self):
        for flags in ({'active':None},{'active':False},{'closed':True},{'closed':None}):
            with self.subTest(flags=flags),tempfile.TemporaryDirectory() as d:
                f=Fixture(d);f.event(event_flags=flags);events,_=adapter.read_source(f.source(),START,END)
                snaps=events[0].groups[0].snaps;self.assertTrue(all(s.valid for s in snaps));self.assertFalse(any(s.open_observed or s.gate_observed for s in snaps));f.c.close()
    def test_missing_token_preserved_as_invalid_not_zero(self):
        self.f.event(missing='soccer-AWAY-0');events,audit=self.read();ss=events[0].groups[0].snaps;self.assertEqual(len(ss),3);self.assertEqual(sum(s.valid for s in ss),2);self.assertIsNone(next(s.midpoint for s in ss if not s.valid));self.assertFalse(events[0].groups[0].complete)
    def test_explicit_schedule_age_is_displayed_as_schedule_not_native(self):
        self.f.event('mlb',scheduled='2026-08-29T10:00:00Z');events,_=self.read()
        self.assertTrue(all(s.minute is None for s in events[0].groups[0].snaps))
        self.assertTrue(all(abs(s.scheduled_age-.05)<1e-8 for s in events[0].groups[0].snaps))
    def test_missing_explicit_schedule_does_not_create_native_or_scheduled_clock(self):
        self.f.event('mlb');events,_=self.read()
        self.assertTrue(all(s.minute is None and s.scheduled_age is None for s in events[0].groups[0].snaps))
    def test_runtime_schema_epoch_mismatch_rejected(self):
        self.f.event();self.f.c.execute("UPDATE schema_metadata SET data_contract='major-sports-lifecycle-census-v4'")
        with self.assertRaisesRegex(ValueError,'runtime/schema'):self.read()
    def test_wrong_canonical_hash_rejected(self):
        self.f.event();self.f.c.execute("UPDATE book_snapshots SET canonical_sha256='bad'");events,_=self.read();self.assertFalse(any(s.valid for s in events[0].groups[0].snaps))
    def test_cross_run_metadata_rejected(self):
        self.f.event();self.f.c.execute("UPDATE event_observations SET run_id='different'");events,_=self.read();self.assertFalse(any(s.valid for s in events[0].groups[0].snaps))
    def test_failed_run_and_late_publication_not_valid(self):
        self.f.event(status='FAILED');events,_=self.read();self.assertFalse(events[0].groups[0].complete);self.assertTrue(events[0].failures)
    def test_half_open_publication_cutoff(self):
        self.f.event();events,_=self.read('2026-08-29T10:00:04Z');self.assertFalse(events[0].groups[0].complete)
    def test_phase_changes_remain_explicit_metadata(self):
        self.f.event();self.f.event(n=1);self.f.c.execute("UPDATE event_observations SET season_phase='REGULAR' WHERE run_id='r1'")
        events,_=self.read();self.assertEqual(len(events),1);self.assertEqual(events[0].config['season_phase'],'MIXED_OR_CHANGED');self.assertEqual(events[0].config['observed_season_phases'],['REGULAR','UNKNOWN'])
    def test_source_digest_change_separate(self):
        self.f.event();self.f.event(n=1,source='c'*64);events,_=self.read();self.assertEqual(len(events),2);self.assertEqual(len({e.cohort for e in events}),2)
    def test_wrong_public_host_rejected(self):
        self.f.event();self.f.c.execute("UPDATE api_requests SET url='https://untrusted.example/public'");events,_=self.read();self.assertFalse(events[0].groups[0].complete)
    def test_verified_terminal_triad(self):
        self.f.event();self.f.terminals();events,audit=self.read();self.assertEqual(len(events[0].terminals),6);self.assertEqual(audit['stats']['verified_terminal_token_facts'],6)
    def test_foreign_cohort_terminal_excluded(self):
        self.f.event();self.f.terminals(source='f'*64);events,_=self.read();self.assertFalse(events[0].terminals)

if __name__=='__main__':unittest.main()
