"""Native Cherry collection and offline economics read the same source values."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime,timezone
import importlib.util
from itertools import count
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from polybot.shadow import RUNTIME_JOB
from polybot.shadow.db import ShadowRepository
from polybot.shadow.collector import ShadowCollector
from polybot.shadow.analyzer import analyze_shadow_database
from polybot.shadow.transport import CollectionDeadline,iso_utc
from polybot_observability.market_data_raw_profiles import CHERRY_SHADOW_PROFILE_ID
from polybot_observability.market_data_raw_links import raw_layout_metadata,verify_raw_dependencies
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadReader,PayloadStore,StoreError
from polybot_observability.market_data_migrate import file_sha256
from polybot_observability.market_data_scalar_links import scalar_namespace
from tests.test_shadow_runtime import _config,_markets,FakeGamma,FakeClob,NOW


NAMESPACE=scalar_namespace('fixture-source','polybot-cherry-shadow','golden-cherry',RUNTIME_JOB)
START=datetime(2026,9,5,tzinfo=timezone.utc)
END=datetime(2026,10,5,tzinfo=timezone.utc)


def populate(path,config,monkeypatch,refs,*,native=False):
    counter=count()
    with monkeypatch.context() as scope:
        for module in ('polybot.shadow.db','polybot.shadow.collector'):
            scope.setattr(module+'.uuid4',lambda:SimpleNamespace(hex=f'fixture-{next(counter):04}'))
            scope.setattr(module+'.iso_utc',lambda value=None:iso_utc(value or NOW))
        repo=ShadowRepository(path,config,references=refs,
            raw_profile_id=CHERRY_SHADOW_PROFILE_ID if native else None,
            raw_namespace=NAMESPACE if native else None)
        repo.record_config()
        for run,at,gamma,clob in (
            ('first',NOW,FakeGamma(_markets()),FakeClob()),
            ('followup',datetime(2026,9,8,tzinfo=timezone.utc),FakeGamma([],resolve=True),FakeClob(no_books=True)),
        ):
            repo.record_run_event(run,'STARTED')
            stats=ShadowCollector(config,repo,gamma,clob,CollectionDeadline(240,monotonic=lambda:0)).collect(run,now=at)
            repo.record_run_event(run,'SUCCEEDED',stats)
        repo.record_run_event('failed','STARTED')
        repo.record_run_event('failed','FAILED',{'reason':'fixture failed before publication'})
        return repo


def grid_module():
    path=Path(__file__).resolve().parents[1]/'scripts/grid_shadow_parameters.py'
    spec=importlib.util.spec_from_file_location('shared_cherry_grid',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def analytical_result(path,refs):
    analysis=analyze_shadow_database(path,start=START,end=END,references=refs)
    analysis.pop('database')
    grid=grid_module().analyze(path,START,END,references=refs)
    grid.pop('database')
    return analysis,grid


def test_native_derivative_and_inline_replay_preserve_all_cells(tmp_path,monkeypatch):
    monkeypatch.delenv('PUBLIC_MARKET_DATA_SOURCE',raising=False)
    monkeypatch.delenv('JOB_NAME',raising=False)
    monkeypatch.delenv('PUBLIC_MARKET_DATA_RAW',raising=False)
    config=_config(tmp_path)
    original=tmp_path/'inline/trades_sim.db'
    populate(original,config,monkeypatch,PayloadReferences())
    expected=analytical_result(original,PayloadReferences())
    assert len(expected[0]['paired_cells'])==21
    assert expected[0]['run_health']['valid_successful_runs']==2
    assert expected[0]['run_health']['failed_runs']==1
    assert expected[1]['valid_episode_count']==3
    assert all(row['status']=='INSUFFICIENT_INDEPENDENT_EVENT_CLUSTERS' for row in expected[1]['summaries'].values())
    with PayloadStore(tmp_path/'public.db') as public:
        refs=PayloadReferences(public,public)
        native=tmp_path/'native/trades_sim.db'
        repo=populate(native,config,monkeypatch,refs,native=True)
        with repo.connect(read_only=True) as c:
            assert raw_layout_metadata(c)['profile_id']==CHERRY_SHADOW_PROFILE_ID
            verify_raw_dependencies(c,references=refs)
            assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert repo.open_episodes()==[] and len(repo.policy_exits())==21
        before=native.read_bytes()
        # Reopen with RAW flag omitted: bound source remains RAW.
        ShadowRepository(native,config,references=refs,raw_namespace=NAMESPACE)
        assert native.read_bytes()==before
        target=tmp_path/'derivative.db'
        migrate_raw_database(original,target,source_sha256=file_sha256(original),namespace=NAMESPACE,
                             references=refs,profile_id=CHERRY_SHADOW_PROFILE_ID,batch_rows=2)
    with PayloadReader(tmp_path/'public.db') as reader:
        cold=PayloadReferences(reader,cache_bytes=0)
        assert analytical_result(native,cold)==expected
        assert analytical_result(target,cold)==expected
        repo=ShadowRepository(target,config,references=cold,read_only=True)
        assert repo.open_episodes()==[] and len(repo.policy_exits())==21
        with pytest.raises(ValueError,match='read-only'):repo.record_config()


def test_populated_inline_refusal_and_fresh_public_failure_are_retryable(tmp_path,monkeypatch):
    monkeypatch.delenv('PUBLIC_MARKET_DATA_RAW',raising=False)
    config=_config(tmp_path);path=tmp_path/'inline/trades_sim.db'
    populate(path,config,monkeypatch,PayloadReferences());before=path.read_bytes()
    fresh=tmp_path/'fresh/trades_sim.db'
    with pytest.raises((ValueError,RuntimeError),match='capabilit|writer|RAW'):
        ShadowRepository(fresh,config,references=PayloadReferences(),raw_namespace=NAMESPACE)
    assert not fresh.exists() and not list(fresh.parent.glob('.cherry-shadow-init-*'))
    with PayloadStore(tmp_path/'public.db') as public:
        refs=PayloadReferences(public,public)
        with pytest.raises(ValueError,match='offline derivative'):
            ShadowRepository(path,config,references=refs,raw_namespace=NAMESPACE)
        assert path.read_bytes()==before
        repo=ShadowRepository(fresh,config,references=refs,raw_namespace=NAMESPACE)
        with repo.connect(read_only=True) as c:assert raw_layout_metadata(c) is not None
        assert fresh.stat().st_nlink==1


def test_late_publication_failure_rolls_back_source_and_private_evidence(tmp_path,monkeypatch):
    monkeypatch.delenv('PUBLIC_MARKET_DATA_RAW',raising=False)
    config=_config(tmp_path);path=tmp_path/'native/trades_sim.db'
    with PayloadStore(tmp_path/'public.db') as public:
        refs=PayloadReferences(public,public)
        repo=populate(path,config,monkeypatch,refs,native=True)
        with repo.connect(read_only=True) as c:
            book=dict(c.execute('SELECT * FROM shadow_book_snapshots LIMIT 1').fetchone())
            row=dict(c.execute('SELECT * FROM shadow_path_observations LIMIT 1').fetchone())
            before=c.execute('SELECT COUNT(*) FROM shadow_book_snapshots').fetchone()[0]
        book.update(snapshot_id='orphan-publication',run_id='bad-run',best_bid=.63)
        row.update(path_id='invalid-path',episode_id='missing-episode',run_id='bad-run',snapshot_id=book['snapshot_id'])
        with pytest.raises(sqlite3.IntegrityError):
            repo.publish({'shadow_book_snapshots':[book],'shadow_path_observations':[row]})
        with repo.connect(read_only=True) as c:
            assert c.execute('SELECT COUNT(*) FROM shadow_book_snapshots').fetchone()[0]==before
            assert c.execute("SELECT 1 FROM shadow_path_observations WHERE path_id='invalid-path'").fetchone() is None
            verify_raw_dependencies(c,references=refs)


def test_missing_public_body_is_an_error_before_any_analysis_result(tmp_path,monkeypatch):
    monkeypatch.delenv('PUBLIC_MARKET_DATA_RAW',raising=False)
    config=_config(tmp_path);path=tmp_path/'native/trades_sim.db'
    public_path=tmp_path/'public.db'
    with PayloadStore(public_path) as public:
        populate(path,config,monkeypatch,PayloadReferences(public,public),native=True)
    before=file_sha256(path)
    class MissingBodies(PayloadReader):
        def get_many(self,hashes):raise StoreError('missing public source body')
    with MissingBodies(public_path) as reader:
        refs=PayloadReferences(reader,cache_bytes=0)
        with pytest.raises(StoreError,match='missing public source'):
            analyze_shadow_database(path,start=START,end=END,references=refs)
        with pytest.raises(StoreError,match='missing public source'):
            grid_module().analyze(path,START,END,references=refs)
        with pytest.raises(StoreError,match='missing public source'):
            ShadowRepository(path,config,references=refs,read_only=True)
    assert file_sha256(path)==before
