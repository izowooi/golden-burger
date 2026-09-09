import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import watermelon_live_bbo_grid as reader

START='2026-09-08T00:00:00Z'
END='2026-09-09T00:00:00Z'


class Fixture:
    def __init__(self, directory, sport='soccer'):
        self.path=Path(directory).resolve()/'trades.db'
        self.c=sqlite3.connect(self.path)
        self.c.executescript('''
        CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,condition_id TEXT,token_id TEXT,outcome TEXT,
          best_bid REAL,best_ask REAL,run_id TEXT,timestamp TEXT,sport_family TEXT,probability REAL);
        CREATE TABLE market_catalog(condition_id TEXT PRIMARY KEY,event_id TEXT,event_title TEXT,question TEXT,
          outcomes_json TEXT,token_ids_json TEXT,active INTEGER,closed INTEGER,fee_rate REAL);
        CREATE TABLE run_audits(run_id TEXT PRIMARY KEY,strategy_name TEXT,job_name TEXT,mode TEXT,
          config_hash TEXT,started_at TEXT,finished_at TEXT,status TEXT);
        CREATE TABLE strategy_configs(config_hash TEXT PRIMARY KEY,strategy_name TEXT,mode TEXT,config_json TEXT);
        ''')
        cfg={'mode':'live','strategy_name':'golden-watermelon-live','trading':{
          'strategy_source_digest':'a'*64,'sport_family':sport}}
        self.c.execute('INSERT INTO strategy_configs VALUES(?,?,?,?)',('cfg','golden-watermelon-live','live',json.dumps(cfg)))
        self.c.execute('INSERT INTO run_audits VALUES(?,?,?,?,?,?,?,?)',
          ('run','golden-watermelon-live','runtime','live','cfg','2026-09-08T12:00:00Z','2026-09-08T12:00:04Z','SUCCESS'))
        title='Alpha FC vs. Beta FC'
        items=[('a','Will Alpha FC win on 2026-09-08?'),('d',f'Will {title} end in a draw?'),('b','Will Beta FC win on 2026-09-08?')] if sport=='soccer' else [('m',title)]
        for condition,question in items:
            labels=['Yes','No'] if sport=='soccer' else ['Alpha FC','Beta FC']
            tokens=[condition+'0',condition+'1']
            # Deliberately future/closed catalog flags and arbitrary fee: these MUST NOT become PIT.
            self.c.execute('INSERT INTO market_catalog VALUES(?,?,?,?,?,?,?,?,?)',
              (condition,'event',title,question,json.dumps(json.dumps(labels)),json.dumps(json.dumps(tokens)),0,1,.99))
            for i in ([0] if sport=='soccer' else [0,1]):
                self.c.execute('INSERT INTO market_snapshots VALUES(NULL,?,?,?,?,?,?,?,?,?)',
                  (condition,tokens[i],labels[i],.30,.32,'run','2026-09-08 12:00:01.000000',sport,.99))
        self.c.commit()
    def pin(self):
        self.c.commit()
        digest=hashlib.sha256(self.path.read_bytes()).hexdigest()
        manifest=self.path.parent/'manifest.json'
        manifest.write_text(json.dumps({'source_key':'source','pinned_path':str(self.path),'sha256':digest,'quick_check':['ok']}))
        return {'id':'source','source_key':'source','strategy':'golden-watermelon-live','runtime_job':'runtime',
                'local_path':str(self.path),'local_sha256':digest,'manifest':str(manifest),'pinned':True,'mode':'live'}
    def read(self): return reader.read_source(self.pin(),START,END)
    def close(self): self.c.close()


class BBOTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.fixture=Fixture(self.tmp.name)
        self.addCleanup(self.tmp.cleanup);self.addCleanup(self.fixture.close)
    def test_three_yes_prices_without_depth_fee_open_or_payout(self):
        events,audit=self.fixture.read();self.assertEqual(len(events),1)
        e=events[0];self.assertEqual(len(e.groups[0].snaps),3)
        self.assertEqual(audit['groups']['valid_bbo_partition_groups'],1)
        self.assertFalse(e.groups[0].complete);self.assertEqual(e.terminals,{})
        for s in e.groups[0].snaps:
            self.assertAlmostEqual(s.midpoint,.31) # never stored probability .99
            self.assertTrue(s.valid);self.assertEqual((s.asks,s.bids),((),()))
            self.assertIsNone(s.buy);self.assertIsNone(s.fee_rate);self.assertIsNone(s.minute)
            self.assertFalse(s.open_observed);self.assertFalse(s.gate_observed)
            self.assertFalse(s.depth_available);self.assertFalse(s.entry_set_complete)
            self.assertIsNone(s.verified_role)
    def test_direct_two_team_mlb_and_nfl(self):
        for sport in ('mlb','nfl'):
            with self.subTest(sport=sport), tempfile.TemporaryDirectory() as d:
                f=Fixture(d,sport)
                try:
                    events,audit=f.read();self.assertEqual(events[0].sport,sport)
                    self.assertEqual(audit['groups']['valid_bbo_partition_groups'],1)
                    self.assertEqual({s.slot for s in events[0].groups[0].snaps},{('TEAM_A','DIRECT'),('TEAM_B','DIRECT')})
                finally:f.close()
    def test_nested_json_arrays_supported(self):
        events,audit=self.fixture.read();self.assertEqual(audit['mapped_snapshot_rows'],3)
    def test_token_label_mismatch_excluded_not_reassigned(self):
        self.fixture.c.execute("UPDATE market_snapshots SET token_id='wrong' WHERE condition_id='a'")
        events,audit=self.fixture.read();self.assertEqual(audit['excluded_rows']['snapshot_catalog_token_label_mismatch'],1)
        self.assertEqual(audit['groups']['bbo_partition_incomplete'],1)
    def test_unknown_sport_not_guessed_from_runtime_or_teams(self):
        self.fixture.c.execute('UPDATE market_snapshots SET sport_family=NULL')
        cfg={'mode':'live','strategy_name':'golden-watermelon-live','trading':{'strategy_source_digest':'a'*64}}
        self.fixture.c.execute('UPDATE strategy_configs SET config_json=?',(json.dumps(cfg),))
        events,audit=self.fixture.read();self.assertEqual(events,[])
        self.assertEqual(audit['excluded_rows']['missing_or_conflicting_recorded_sport'],3)
    def test_recorded_soccer_classifier_supports_older_epoch(self):
        self.fixture.c.execute('UPDATE market_snapshots SET sport_family=NULL')
        cfg={'mode':'live','strategy_name':'golden-watermelon-live','trading':{
          'strategy_source_digest':'a'*64,'classifier_version':'soccer-major-league-identity-v1'}}
        self.fixture.c.execute('UPDATE strategy_configs SET config_json=?',(json.dumps(cfg),))
        events,audit=self.fixture.read();self.assertEqual(events[0].sport,'soccer')
        self.assertEqual(audit['valid_bbo_rows'],3)
    def test_failed_run_preserves_invalid_prices(self):
        self.fixture.c.execute("UPDATE run_audits SET status='FAILED'")
        events,audit=self.fixture.read();self.assertEqual(audit['mapped_snapshot_rows'],3)
        self.assertEqual(audit['valid_bbo_rows'],0);self.assertTrue(events[0].failures)
        self.assertTrue(all(len(interval)==2 for interval in events[0].failures))
        self.assertTrue(reader.grid.failed_between(events[0].failures,events[0].failures[0][0]-1,events[0].failures[0][1]+1))
    def test_future_publication_cannot_validate_pre_cutoff_poll(self):
        self.fixture.c.execute("UPDATE run_audits SET finished_at='2026-09-09T00:00:00Z'")
        events,audit=self.fixture.read();self.assertEqual(audit['valid_bbo_rows'],0)
    def test_time_outside_run_is_invalid(self):
        self.fixture.c.execute("UPDATE market_snapshots SET timestamp='2026-09-08 11:59:59'")
        events,audit=self.fixture.read();self.assertEqual(audit['valid_bbo_rows'],0)
    def test_missing_and_crossed_BBO_not_zero(self):
        self.fixture.c.execute("UPDATE market_snapshots SET best_bid=NULL WHERE condition_id='a'")
        self.fixture.c.execute("UPDATE market_snapshots SET best_bid=.50 WHERE condition_id='b'")
        events,audit=self.fixture.read();self.assertEqual(audit['valid_bbo_rows'],1)
        self.assertIsNone(next(s for s in events[0].groups[0].snaps if s.condition=='a').bbo_best_bid)
    def test_duplicate_same_run_token_is_invalid(self):
        self.fixture.c.execute("INSERT INTO market_snapshots SELECT NULL,condition_id,token_id,outcome,best_bid,best_ask,run_id,timestamp,sport_family,probability FROM market_snapshots WHERE condition_id='a'")
        events,audit=self.fixture.read();self.assertEqual(audit['valid_bbo_rows'],0)
        self.assertEqual(audit['invalid_bbo_rows']['duplicate_run_token'],4)
    def test_source_config_digest_change_separates_cohort(self):
        cfg={'mode':'live','strategy_name':'golden-watermelon-live','trading':{'strategy_source_digest':'b'*64,'sport_family':'soccer'}}
        self.fixture.c.execute('INSERT INTO strategy_configs VALUES(?,?,?,?)',('cfg2','golden-watermelon-live','live',json.dumps(cfg)))
        self.fixture.c.execute("INSERT INTO run_audits SELECT 'run2',strategy_name,job_name,mode,'cfg2',started_at,finished_at,status FROM run_audits")
        self.fixture.c.execute("INSERT INTO market_snapshots SELECT NULL,condition_id,token_id,outcome,best_bid,best_ask,'run2',timestamp,sport_family,probability FROM market_snapshots")
        events,audit=self.fixture.read();self.assertEqual(audit['cohorts'],2);self.assertEqual(len(events),2)
    def test_pin_and_source_unchanged(self):
        source=self.fixture.pin();before=self.fixture.path.read_bytes()
        reader.read_source(source,START,END);self.assertEqual(before,self.fixture.path.read_bytes())
        source['source_key']='wrong'
        with self.assertRaisesRegex(ValueError,'pin_path'):reader.read_source(source,START,END)
    def test_numeric_bool_nan_rejected(self):
        for value in (True,False,'NaN','inf',-1,2):self.assertIsNone(reader.number(value))


if __name__=='__main__':unittest.main()
