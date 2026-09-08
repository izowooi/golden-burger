from __future__ import annotations
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sqlite3
from unittest.mock import patch
from types import SimpleNamespace
from dataclasses import make_dataclass

PATH = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sports_trade_report', PATH/'sports_trade_report.py')
report = importlib.util.module_from_spec(spec); spec.loader.exec_module(report)
spec2 = importlib.util.spec_from_file_location('sports_trade_prepare', PATH/'sports_trade_report_prepare.py')
prepare = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(prepare)

START = report.utc('2026-09-07T13:33:38Z')
END = report.utc('2026-09-08T13:33:38Z')

def submission(side='BUY', size=10, **extra):
    return dict(submission_id='sub-'+side, order_id='order-'+side, token_id='token-yes', side=side,
                simulation=0, latest_order_status='MATCHED', latest_size_matched=size,
                needs_reconciliation=0, reconciliation_error=None, **extra)

def fill(side='BUY', size=10, price=.9, fee=.1, at='2026-09-07T14:00:00Z', **extra):
    return dict(submission_id='sub-'+side, order_id='order-'+side, trade_id='fill-'+side,
                bucket_index=0, status='CONFIRMED', side=side, size=size, price=price,
                fee_amount_usdc=fee, fee_rate_bps=None, liquidity_role='TAKER',
                matched_at=at, domain_error=None, **extra)

def order(side='BUY', size=10, price=.9, fee=.1, at='2026-09-07T14:00:00Z'):
    return report.order_evidence(submission(side,size),[fill(side,size,price,fee,at)],end=END)

class ReportingTests(unittest.TestCase):
    def test_timezone_conversion_and_naive_boundary_rejection(self):
        self.assertEqual(report.utc('2026-09-08T22:33:38+09:00'), END)
        with self.assertRaises(ValueError): report.utc('2026-09-08 13:33:38')
        self.assertEqual(report.utc('2026-09-08 13:33:38',naive_utc=True),END)

    def test_accepted_or_matched_without_confirmed_fill_is_not_profit(self):
        x=report.order_evidence(submission(),[],end=END)
        self.assertEqual(x['state'],'UNRESOLVED_NO_CONFIRMED_FILL')
        result=report.position_economics(x,[],{},start=START,end=END)
        self.assertIsNone(result['confirmed_economic_net_in_window'])

    def test_maker_fee_exception_requires_explicit_source_contract(self):
        f=fill(fee=None);f['liquidity_role']='MAKER'
        self.assertIsNone(report.fee_evidence(f)[0])
        self.assertEqual(report.fee_evidence(f,maker_zero_fee_contract=True)[0],0)
        f['fee_rate_bps']=100
        self.assertIsNone(report.fee_evidence(f,maker_zero_fee_contract=True)[0])
        f['fee_rate_bps']=0
        self.assertEqual(report.fee_evidence(f)[0],0)

    def test_unknown_fee_does_not_become_zero(self):
        buy=order(fee=None)
        self.assertTrue(buy['complete_now'])
        self.assertFalse(buy['fee_complete'])
        self.assertIsNone(report.position_economics(buy,[],{},start=START,end=END)['confirmed_economic_net_in_window'])

    def test_partial_sell_cost_allocation_and_remaining_claim(self):
        buy=order()
        sell=order('SELL',4,.95,.02,'2026-09-07T15:00:00Z')
        result=report.position_economics(buy,[sell],{'verified':True,'payout':'1','observed_at':'2026-09-07T16:00:00Z'},start=START,end=END)
        self.assertEqual(report.number(result['realized_sold_net_in_window']),report.number('.14'))
        self.assertEqual(report.number(result['settlement_value_net_in_window']),report.number('.54'))
        self.assertEqual(report.number(result['residual_shares']),6)
        self.assertFalse(result['cash_redemption_proven'])

    def test_carry_in_sale_is_included_by_sell_match_time(self):
        buy=order(at='2026-09-06T15:00:00Z')
        sell=order('SELL',10,.95,.05,'2026-09-07T16:00:00Z')
        result=report.position_economics(buy,[sell],{},start=START,end=END)
        self.assertEqual(report.number(result['realized_sold_net_in_window']),report.number('.35'))

    def test_cutoff_is_half_open_even_if_order_later_completed(self):
        buy=order()
        sell=order('SELL',10,.95,.05,'2026-09-08T13:33:38Z')
        self.assertEqual(sell['confirmed_size'],'0')
        self.assertEqual(sell['post_cutoff_fill_count'],1)
        self.assertIsNone(report.position_economics(buy,[sell],{},start=START,end=END)['confirmed_economic_net_in_window'])

    def test_duplicate_or_wrong_order_fill_blocks_completion(self):
        f=fill()
        x=report.order_evidence(submission(),[f,f],end=END)
        self.assertFalse(x['complete_now'])
        bad={**f,'order_id':'another'}
        self.assertFalse(report.order_evidence(submission(),[bad],end=END)['complete_now'])

    def test_fully_matched_quantized_size_uses_actual_size_not_requested(self):
        sub=submission(size=5.2083,requested_size=5)
        x=report.order_evidence(sub,[fill(size=5.2083,price=.96)],end=END)
        self.assertTrue(x['complete_now'])
        self.assertEqual(x['confirmed_size'],'5.2083')

    def test_exact_payout_hash_and_token_identity(self):
        trade={'condition_id':'condition','token_id':'token-yes','outcome':'Yes'}
        payload={'closed':True,'tokens':[{'token_id':'token-yes','outcome':'Yes','price':1,'winner':True},{'token_id':'token-no','outcome':'No','price':0,'winner':False}]}
        text=json.dumps(payload,separators=(',',':'),sort_keys=True)
        row={'condition_id':'condition','selected_token_id':'token-yes','selected_outcome':'Yes','selected_payout':1,'winner_index':0,'winner_token_id':'token-yes','winner_outcome':'Yes','observed_at':'2026-09-07T16:00:00Z','evidence_json':text,'evidence_sha256':hashlib.sha256(text.encode()).hexdigest()}
        self.assertTrue(report.resolution_evidence(trade,{},[row],end=END)['verified'])
        self.assertFalse(report.resolution_evidence(trade,{},[{**row,'winner_index':1}],end=END)['verified'])
        self.assertFalse(report.resolution_evidence(trade,{},[{**row,'evidence_sha256':'0'*64}],end=END)['verified'])
        self.assertFalse(report.resolution_evidence({**trade,'token_id':'unrelated'},{},[row],end=END)['verified'])

    def test_resolution_value_or_explanation_alone_is_not_proof(self):
        trade={'condition_id':'c','token_id':'t','outcome':'Yes','resolution_value':1,'resolution_status':'resolved','resolution_evidence':'claimed final','resolution_observed_at':'2026-09-07T16:00:00Z'}
        self.assertFalse(report.resolution_evidence(trade,{},[],end=END)['verified'])

    def test_prepare_distinguishes_run_from_uv_run_config(self):
        def cfg(script):return {'builders':[{'script':script}],'triggers':[{'spec':'* * * * *'}],'disabled':False}
        self.assertEqual(prepare.classify_operation(cfg('uv run polybot config --live --job guava-live-lion-a-v1'))['operation'],'CONFIGURATION_ONLY')
        self.assertEqual(prepare.classify_operation(cfg('uv run polybot run-account --live --account polybot-cat'))['operation'],'LIVE_DECLARED')
        self.assertEqual(prepare.classify_operation(cfg('uv run polybot run --simulate --job raw'))['operation'],'SIMULATION_DECLARED')

    def test_report_rejects_duplicate_source_before_reading_data(self):
        source={'source_key':'same','db_path':'/nonexistent/pinned/fake.db'}
        with self.assertRaises(ValueError):
            report.build_report({'schema':'sports-trade-report-inputs-v1','sources':[source,source]},start=START,end=END)

    def test_standard_sync_sqlite_rows_and_interrupted_pin_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            db=root/'pinned'/'one'/'trades.db';db.parent.mkdir(parents=True);db.write_bytes(b'fixture-bytes')
            digest=hashlib.sha256(db.read_bytes()).hexdigest()
            manifest={'source_key':'source','pinned_path':str(db),'sha256':digest,'quick_check':['ok']}
            db.with_name('manifest.json').write_text(json.dumps(manifest))
            conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
            artifact=conn.execute("SELECT 'source' source_key,'database_live' kind,'runtime' runtime_job,'SYNCED' status,? local_sha256,? metadata_json",(digest,json.dumps({'completed_at':'2026-09-08T14:00:00Z'}))).fetchone()
            pin=conn.execute('SELECT ? pinned_path, ? source_key',(str(db),'source')).fetchone()
            prior=conn.execute("SELECT 'p' plan_id,'SUCCESS' status,'old-run' run_id").fetchone()
            state={'execute':0,'pins':0,'verify':0,'prior':None}
            Sync=make_dataclass('Sync',[('status',str),('run_id',str)])
            def execute(plan):state['execute']+=1;state['prior']=prior;return Sync('SUCCESS','old-run')
            def verify(**kwargs):state['verify']+=1;return {'status':'SUCCESS'}
            def pin_database(key):state['pins']+=1;return db
            cat=SimpleNamespace(latest_sync_run=lambda **kw:state['prior'],list_artifacts=lambda **kw:[artifact],list_pins=lambda **kw: [pin] if state['pins'] else [])
            svc=SimpleNamespace(config=SimpleNamespace(data_root=root,minimum_free_bytes=0,ssh_host='fixture'),catalog=cat,load_plan=lambda p:SimpleNamespace(plan_id='p',jenkins_job='job',strategy='golden-peach'),execute=execute,verify=verify,pin_database=pin_database)
            plans=root/'plans.json';plans.write_text(json.dumps({'schema':'sports-trade-report-plans-v1','total_estimated_bytes':0,'discovery':'fixture','gaps':[],'plans':[{'plan_id':'p','jenkins_job':'job','strategy':'golden-peach','live_runtime_candidates':['runtime']}]}))
            with patch.object(prepare,'verify_repository'),patch.object(prepare,'service',return_value=svc):
                first=prepare.sync(plans,root/'inputs.json')
                second=prepare.sync(plans,root/'inputs.json')
            self.assertEqual(first['sources'],second['sources'])
            self.assertEqual(first['sources'][0]['source_completed_at'],'2026-09-08T14:00:00Z')
            self.assertEqual(state['execute'],1);self.assertEqual(state['pins'],1);self.assertEqual(state['verify'],2)
            self.assertTrue(second['preparation'][0]['reused_successful_plan'])
            conn.close()

    def test_verified_pin_end_to_end_ignores_trade_assumption_pnl(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'pinned'/'snapshot'/'trades.db';path.parent.mkdir(parents=True)
            c=sqlite3.connect(path)
            c.executescript('''
                CREATE TABLE run_audits(run_id TEXT PRIMARY KEY,started_at TEXT,finished_at TEXT,status TEXT,config_hash TEXT,job_name TEXT,strategy_name TEXT,mode TEXT,cycle_stats_json TEXT);
                CREATE TABLE strategy_configs(config_hash TEXT,config_json TEXT);
                CREATE TABLE trades(id INTEGER,condition_id TEXT,event_id TEXT,event_slug TEXT,question TEXT,outcome TEXT,token_id TEXT,buy_order_id TEXT,sell_order_id TEXT,buy_timestamp TEXT,status TEXT,sport_family TEXT,realized_pnl REAL);
                CREATE TABLE market_catalog(condition_id TEXT,event_id TEXT,event_title TEXT,sport_family TEXT,league_code TEXT,token_ids_json TEXT);
                CREATE TABLE order_submissions(submission_id TEXT,order_id TEXT,token_id TEXT,side TEXT,simulation INTEGER,latest_order_status TEXT,latest_size_matched REAL,needs_reconciliation INTEGER,reconciliation_error TEXT,run_id TEXT,submitted_at TEXT);
                CREATE TABLE order_fills(submission_id TEXT,order_id TEXT,trade_id TEXT,bucket_index INTEGER,status TEXT,side TEXT,size REAL,price REAL,fee_amount_usdc REAL,fee_rate_bps REAL,liquidity_role TEXT,matched_at TEXT,domain_error TEXT);
            ''')
            for side,hour in [('BUY',14),('SELL',15)]:
                at=f'2026-09-07T{hour}:00:00Z'
                c.execute('INSERT INTO run_audits VALUES(?,?,?,?,?,?,?,?,?)',('run-'+side,at,at,'SUCCESS','cfg','runtime-mlb','golden-peach','live','{}'))
                sub=submission(side);sub.update(run_id='run-'+side,submitted_at=at)
                columns=[r[1] for r in c.execute('PRAGMA table_info(order_submissions)')]
                c.execute('INSERT INTO order_submissions VALUES('+','.join('?' for _ in columns)+')',[sub.get(k) for k in columns])
                f=fill(side,price=.9 if side=='BUY' else .95,fee=.1 if side=='BUY' else .05,at=at)
                columns=[r[1] for r in c.execute('PRAGMA table_info(order_fills)')]
                c.execute('INSERT INTO order_fills VALUES('+','.join('?' for _ in columns)+')',[f.get(k) for k in columns])
            c.execute('INSERT INTO strategy_configs VALUES(?,?)',('cfg',json.dumps({'trading':{'sport_family':'mlb','strategy_source_digest':'fixture-code'}})))
            c.execute('INSERT INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(1,'c','e','a-b','Will A win?','A','token-yes','order-BUY','order-SELL','2026-09-07T14:00:00Z','COMPLETED','mlb',99999))
            c.execute('INSERT INTO market_catalog VALUES(?,?,?,?,?,?)',('c','e','A vs B','mlb','mlb','["token-yes","token-no"]'))
            c.commit();c.close()
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            (path.parent/'manifest.json').write_text(json.dumps({'pinned_path':str(path),'source_key':'fixture-source','sha256':before,'quick_check':['ok']}))
            source={'db_path':str(path),'mode':'live','pinned':True,'sha256':before,'source_key':'fixture-source','strategy':'golden-peach','jenkins_job':'fixture-job','runtime_job':'runtime-mlb','timestamp_contract':'repository_sqlite_utc'}
            result=report.build_report({'schema':'sports-trade-report-inputs-v1','sources':[source]},start=START,end=END)
            self.assertEqual(report.number(result['summary_by_source'][0]['known_economic_subtotal']),report.number('0.35'))
            self.assertFalse(result['sources'][0]['positions'][0]['trade_pnl_column_used'])
            position=result['sources'][0]['positions'][0]
            position['entry_policy']={'take_profit_price_at_buy':.95}
            position['exit_reason']='take_profit'
            rendered=report.render_markdown(result)
            self.assertIn('+0.350000',rendered)
            self.assertIn('원장 청산 사유: take_profit',rendered)
            self.assertIn('take_profit_price_at_buy=0.95',rendered)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
            with patch.object(report, 'sport_for', return_value='unknown'):
                unmapped=report.read_source(source,start=START,end=END)
            self.assertEqual(unmapped['classification_coverage']['period_requests_outside_supported_families_or_unclassified'],1)
            self.assertEqual(unmapped['positions'],[])

if __name__=='__main__':unittest.main()
