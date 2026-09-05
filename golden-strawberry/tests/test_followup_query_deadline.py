from __future__ import annotations

import pytest
from polybot.db.followup_repository import FollowupRepository
from polybot.utils.retry import CooperativeDeadline, CycleDeadlineExceeded
from polybot.v1_source import V1SourceReader
from tests.followup_support import build_v1_handoff, build_followup_evidence


def test_latest_lookup_preserves_ranked_results_and_seed_fallback(config,followup_config):
    build_v1_handoff(config)
    snapshot=V1SourceReader(followup_config.trading.v1_source).capture()
    evidence=build_followup_evidence(followup_config,snapshot,cycles=3)
    repo=evidence.repository
    with repo.read_connect() as c:
        ids=[r[0] for r in c.execute('SELECT episode_id FROM imported_episodes')]
        expected={r[0]:r[1] for r in c.execute('SELECT episode_id,source_last_executable_bid_vwap FROM imported_episodes') if r[1] is not None}
        expected.update({r[0]:r[1] for r in c.execute('''
          WITH ranked AS (SELECT episode_id,exit_bid_vwap,ROW_NUMBER() OVER (
            PARTITION BY episode_id ORDER BY observed_at DESC,path_observation_id DESC) AS n
            FROM episode_path_observations WHERE path_status='EXECUTABLE' AND exit_bid_vwap IS NOT NULL)
          SELECT episode_id,exit_bid_vwap FROM ranked WHERE n=1
        ''')})
        path_plan=' '.join(r[3] for r in c.execute('''EXPLAIN QUERY PLAN
          SELECT exit_bid_vwap FROM episode_path_observations WHERE episode_id=?
          AND path_status='EXECUTABLE' AND exit_bid_vwap IS NOT NULL
          ORDER BY observed_at DESC,path_observation_id DESC LIMIT 1''',(ids[0],)))
        resolution_plan=' '.join(r[3] for r in c.execute('''EXPLAIN QUERY PLAN
          SELECT 1 FROM resolution_observations WHERE condition_id=? AND resolution_status='RESOLVED' ''',('condition',)))
    assert repo.latest_path_vwaps(ids+['missing'])==expected
    assert 'followup_path_latest_executable_idx' in path_plan
    assert 'followup_resolution_resolved_condition_idx' in resolution_plan


def test_sql_read_is_interrupted_inside_query_not_only_after_it_returns(followup_config):
    repo=FollowupRepository(followup_config.db_path);repo.initialize(followup_config)
    now=[0.0]
    deadline=CooperativeDeadline.after(1,monotonic=lambda:now[0],label='test-read')
    with pytest.raises(CycleDeadlineExceeded):
        with repo.read_connect(deadline=deadline) as c:
            now[0]=2.0
            c.execute('''WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<10000000)
                         SELECT sum(x) FROM n''').fetchone()
    with repo.read_connect() as c:
        assert c.execute('SELECT 1').fetchone()[0]==1
        with pytest.raises(Exception):c.execute("DELETE FROM source_anchors")


@pytest.mark.parametrize('method', ['unresolved_episodes','latest_path_vwaps','threshold_event_keys'])
def test_lookup_rejects_an_expired_deadline(followup_config,method):
    repo=FollowupRepository(followup_config.db_path);repo.initialize(followup_config)
    now=[0.0];deadline=CooperativeDeadline.after(1,monotonic=lambda:now[0],label='expired')
    now[0]=2
    with pytest.raises(CycleDeadlineExceeded):
        if method=='unresolved_episodes':getattr(repo,method)(deadline=deadline)
        else:getattr(repo,method)(['missing'],deadline=deadline)
