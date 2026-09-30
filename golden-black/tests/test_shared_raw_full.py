"""Full public source copies preserve Black's paired experiment and ladder."""
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import hashlib
from itertools import count
import json
import sqlite3
from types import SimpleNamespace

import pytest

from polybot.config import load_config
from polybot.collector import Collector
from polybot.db.repository import ResearchRepository
from polybot.analyzer import analyze_database
from polybot.api.clob_client import ResolutionResult,RawPayload
from polybot_observability.market_data_store import PayloadStore,PayloadReader,StoreError
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_raw_profiles import BLACK_FULL_PROFILE_ID,raw_profile
from polybot_observability.market_data_raw_links import raw_layout_metadata,verify_raw_dependencies,iter_raw_logical_rows
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_migrate import file_sha256
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from test_shared_raw_parents import codec,NOW
from test_collector import ROOT,FakeGamma,FakeClob,PartialFallingClob,RetryFallingClob

NAMESPACE=scalar_namespace('fixture-source','polybot-black','golden-black','black-shadow-paired')


class ResolvedClob(FakeClob):
    def fetch_resolution(self,run_id,condition_id):
        market={'closed':True,'tokens':[{'token_id':'yes','winner':True},{'token_id':'no','winner':False}]}
        raw=json.dumps(market).encode();at='2026-08-21T03:00:01Z'
        return ResolutionResult(condition_id,'RESOLVED',at,'resolution-request',0,market,
            RawPayload('resolution-request',at,hashlib.sha256(raw).hexdigest(),raw))


def populate(tmp_path,monkeypatch,*,public=None,raw=False):
    config=load_config(ROOT/'config.yaml')
    config=replace(config,db_path=tmp_path/'data'/config.job_name/'trades_sim.db')
    if public is not None:codec(monkeypatch,public)
    monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE','fixture-source')
    monkeypatch.setenv('JOB_NAME','polybot-black')
    monkeypatch.setenv('PUBLIC_MARKET_DATA_RAW','1' if raw else '0')
    counter=count()
    for module in ('polybot.collector','polybot.db.repository'):
        monkeypatch.setattr(module+'.uuid4',lambda:SimpleNamespace(hex=f'fixed-{next(counter):05}'))
    repo=ResearchRepository(config.db_path,busy_timeout_ms=1000,data_contract=config.trading.data_contract)
    repo.record_config(dict(config_hash=config.config_hash,strategy_source_digest=config.trading.strategy_source_digest,
        preregistration_sha256=config.trading.experiment.preregistration_sha256,job_name=config.job_name,mode='sim',
        config_json=json.dumps(config.redacted_dict(),sort_keys=True),first_seen_at=NOW.isoformat()))
    for index,(minutes,clob) in enumerate(((0,FakeClob()),(5,PartialFallingClob()),(10,RetryFallingClob()),(180,ResolvedClob()))):
        run=f'run-{index}';at=NOW+timedelta(minutes=minutes)
        for state in ('STARTED','SUCCEEDED'):
            if state=='SUCCEEDED':Collector(config,repo,FakeGamma(),clob).collect(run,now=at)
            repo.record_run_event(dict(event_id=run+'-'+state,run_id=run,event_type=state,observed_at=at.isoformat(),
                config_hash=config.config_hash,strategy_source_digest=config.trading.strategy_source_digest,detail_json='{}'))
    # Stable standalone source for offline conversion, after all handles close.
    with closing(sqlite3.connect(config.db_path)) as c:c.execute('PRAGMA journal_mode=DELETE')
    return config,repo


def test_full_native_and_offline_source_replay_are_equal(tmp_path,monkeypatch):
    with monkeypatch.context() as scoped:
        config,original=populate(tmp_path/'inline',scoped)
        baseline=analyze_database(original.path)
    profile=raw_profile(BLACK_FULL_PROFILE_ID)
    with PayloadStore(tmp_path/'public.db') as public:
        refs=PayloadReferences(public,public)
        with monkeypatch.context() as scoped:
            _,native=populate(tmp_path/'native',scoped,public=public,raw=True)
            with native.connect() as c:
                assert raw_layout_metadata(c)['profile_id']==BLACK_FULL_PROFILE_ID
                verify_raw_dependencies(c)
                assert all(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in profile.tables)
                assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
                counts={t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in profile.tables}
                assert c.execute("SELECT COUNT(*) FROM stop_execution_attempts WHERE status='PARTIAL_FILL'").fetchone()[0]==1
            with closing(sqlite3.connect(native.path)) as c:
                assert 'entry_best_ask' not in {r[1] for r in c.execute('PRAGMA table_info(hypothetical_episodes)')}
                assert 'entry_cost' in {r[1] for r in c.execute('PRAGMA table_info(hypothetical_episodes)')}
                assert c.execute('SELECT COUNT(*) FROM main.orderbook_levels').fetchone()[0]==0
        target=tmp_path/'derivative.db'
        manifest=migrate_raw_database(original.path,target,source_sha256=file_sha256(original.path),namespace=NAMESPACE,
            references=refs,profile_id=BLACK_FULL_PROFILE_ID,batch_rows=2)
        assert manifest['raw_profile_id']==BLACK_FULL_PROFILE_ID
        assert manifest['tables']['orderbook_levels']['externalized_level_rows']>0
    with PayloadReader(tmp_path/'public.db') as reader:
        cold=PayloadReferences(reader,cache_bytes=0)
        expected={key:value for key,value in baseline.items() if key!='db'}
        for path in (target,native.path):
            actual=analyze_database(path,references=cold)
            assert actual['db']==str(path.resolve())
            assert {key:value for key,value in actual.items() if key!='db'}==expected
        with closing(connect(target,references=cold)) as c:
            assert {t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in profile.tables}==counts


def test_readonly_missing_public_body_and_original_winner_check(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as public:
        with monkeypatch.context() as scoped:
            config,repo=populate(tmp_path/'native',scoped,public=public,raw=True)
            with repo.connect() as c:
                row=dict(next(iter_raw_logical_rows(c,'resolution_observations')))
                row.pop('__rowid__');row.update(resolution_id='bad',condition_id='bad-condition',winner_index=2)
                c.execute('BEGIN IMMEDIATE')
                with pytest.raises(sqlite3.IntegrityError):repo._insert(c,'resolution_observations',row)
    class MissingBodies(PayloadReader):
        def get_many(self,hashes):raise StoreError('missing source body')
    with MissingBodies(tmp_path/'public.db') as reader:
        with pytest.raises(StoreError,match='missing source'):
            analyze_database(repo.path,references=PayloadReferences(reader,cache_bytes=0))
