import sqlite3

import pytest

from polybot.db.followup_repository import FollowupRepository, PUBLICATION_CACHE_KIB
from polybot.utils.retry import CooperativeDeadline, CycleDeadlineExceeded


def test_publication_cache_is_bounded_and_durability_stays_full(followup_config):
    repo=FollowupRepository(followup_config.db_path);repo.initialize(followup_config)
    with repo._connect(cache_kib=PUBLICATION_CACHE_KIB) as c:
        assert c.execute('PRAGMA cache_size').fetchone()[0]==-262144
        assert c.execute('PRAGMA synchronous').fetchone()[0]==2
        assert c.execute('PRAGMA foreign_keys').fetchone()[0]==1
        assert c.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
    for invalid in (True,0,1024,1000000):
        with pytest.raises(ValueError):
            with repo._connect(cache_kib=invalid):pytest.fail('unbounded cache accepted')


def test_expired_write_progress_rolls_back_all_rows(followup_config,monkeypatch):
    repo=FollowupRepository(followup_config.db_path);repo.initialize(followup_config)
    with sqlite3.connect(repo.db_path) as c:c.execute('CREATE TABLE publication_probe (value INTEGER)')
    clock=[0.0];inserted=[0]
    deadline=CooperativeDeadline(expires_at=1,monotonic=lambda:clock[0],label='publication')
    monkeypatch.setattr(repo,'_validate_cycle_bundle',lambda bundle:None)
    def insert(c,bundle):
        def tick(value):
            clock[0]+=.01;inserted[0]+=1
            return value
        c.create_function('advance_clock',1,tick)
        c.executemany('INSERT INTO publication_probe VALUES(advance_clock(?))',((i,) for i in range(100000)))
    monkeypatch.setattr(repo,'_insert_cycle_evidence',insert)
    with pytest.raises(CycleDeadlineExceeded,match='write progress'):
        repo.publish_successful_cycle({'cycle':{'run_id':'test','cycle_id':'test'}},
            storage=followup_config.trading.storage,deadline=deadline,
            finalize=lambda *args:pytest.fail('expired write reached success'))
    assert 0<inserted[0]<1000
    with repo.read_connect() as c:
        assert c.execute('SELECT count(*) FROM publication_probe').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM followup_cycles').fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM research_run_events WHERE event_type='SUCCEEDED'").fetchone()[0]==0


def test_expired_publication_never_enters_transaction(followup_config,monkeypatch):
    repo=FollowupRepository(followup_config.db_path);repo.initialize(followup_config)
    monkeypatch.setattr(repo,'_validate_cycle_bundle',lambda bundle:None)
    monkeypatch.setattr(repo,'_insert_cycle_evidence',lambda *args:pytest.fail('expired write started'))
    with pytest.raises(CycleDeadlineExceeded):
        repo.publish_successful_cycle({},storage=followup_config.trading.storage,
            deadline=CooperativeDeadline(expires_at=0,monotonic=lambda:0),finalize=lambda *args:{})
