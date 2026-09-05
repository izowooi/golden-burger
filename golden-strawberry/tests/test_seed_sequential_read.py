from contextlib import contextmanager
import hashlib

from polybot.db.followup_repository import FollowupRepository, READ_CACHE_KIB
from polybot.v1_source import V1SourceReader
from tests.followup_support import build_v1_handoff


def test_sequential_seed_scan_keeps_frozen_hashes_and_file_bytes(config,followup_config,monkeypatch):
    build_v1_handoff(config)
    snapshot=V1SourceReader(followup_config.trading.v1_source).capture()
    repo=FollowupRepository(followup_config.db_path);repo.initialize(followup_config)
    anchor=repo.ensure_seed(snapshot)
    before=hashlib.sha256(repo.db_path.read_bytes()).hexdigest()
    statements=[];original=repo.read_connect
    @contextmanager
    def traced(**kwargs):
        with original(**kwargs) as c:
            assert c.execute('PRAGMA cache_size').fetchone()[0]==-READ_CACHE_KIB
            c.set_trace_callback(statements.append)
            yield c
    monkeypatch.setattr(repo,'read_connect',traced)
    result=repo.verify_seed_integrity(anchor)
    assert result['healthy'] is True
    for table in ('imported_episodes','imported_condition_status','imported_threshold_events'):
        query=next(q for q in statements if f'SELECT * FROM {table}' in q)
        assert 'ORDER BY' not in query.upper()
    assert hashlib.sha256(repo.db_path.read_bytes()).hexdigest()==before


def test_runtime_compact_projection_keeps_population_order_and_fixed_shares(config,followup_config):
    build_v1_handoff(config)
    snapshot=V1SourceReader(followup_config.trading.v1_source).capture()
    repo=FollowupRepository(followup_config.db_path);repo.initialize(followup_config)
    repo.ensure_seed(snapshot)
    full=repo.unresolved_episodes()
    compact=repo.unresolved_episodes(compact=True)
    keys=('episode_id','condition_id','token_id','fixed_shares')
    assert compact==[{key:row[key] for key in keys} for row in full]


def test_verified_projection_avoids_second_seed_read_and_refreshes_resolutions(config,followup_config,monkeypatch):
    from tests.followup_support import build_followup_evidence
    from polybot.followup_run_audit import FollowupRunAudit
    from polybot.utils.retry import CooperativeDeadline

    build_v1_handoff(config)
    snapshot=V1SourceReader(followup_config.trading.v1_source).capture()
    evidence=build_followup_evidence(followup_config,snapshot,cycles=1)
    repo=evidence.repository;anchor=repo.stored_anchor()
    expected=repo.unresolved_episodes(compact=True)
    repo.verify_seed_integrity(anchor)
    statements=[];original=repo.read_connect
    @contextmanager
    def traced(**kwargs):
        with original(**kwargs) as c:
            c.set_trace_callback(statements.append)
            yield c
    monkeypatch.setattr(repo,'read_connect',traced)
    assert repo.unresolved_episodes(compact=True)==expected
    assert not any('imported_episodes' in q for q in statements)
    assert any("resolution_status='RESOLVED'" in q for q in statements)
    assert repo._verified_runtime_seed['db_path']==str(repo.db_path.resolve())
    audit=FollowupRunAudit.start(followup_config,repository=repo,
        anchor_sha256=snapshot.anchor_sha256,validation_mode='PINNED_FAST')
    evidence.collector.run_cycle(audit.run_id,anchor=snapshot.anchor,audit=audit,
        deadline=CooperativeDeadline.after(450),validation_mode='PINNED_FAST')
    assert repo.unresolved_episodes(compact=True)==[]


def test_failed_revalidation_discards_previous_projection(config,followup_config,monkeypatch):
    build_v1_handoff(config)
    snapshot=V1SourceReader(followup_config.trading.v1_source).capture()
    repo=FollowupRepository(followup_config.db_path);repo.initialize(followup_config)
    anchor=repo.ensure_seed(snapshot);repo.verify_seed_integrity(anchor)
    assert repo._verified_runtime_seed is not None
    def failed(**kwargs):raise RuntimeError('injected source validation failure')
    monkeypatch.setattr(repo,'read_connect',failed)
    import pytest
    with pytest.raises(RuntimeError):repo.verify_seed_integrity(anchor)
    assert repo._verified_runtime_seed is None


def test_verified_latest_price_keeps_fallback_and_reads_fresh_paths(config,followup_config,monkeypatch):
    from tests.followup_support import build_followup_evidence
    build_v1_handoff(config)
    snapshot=V1SourceReader(followup_config.trading.v1_source).capture()
    evidence=build_followup_evidence(followup_config,snapshot,cycles=1)
    repo=evidence.repository
    ids=[row['episode_id'] for row in repo.unresolved_episodes()]+['missing']
    expected=repo.latest_path_vwaps(ids)
    repo.verify_seed_integrity(repo.stored_anchor())
    statements=[];original=repo.read_connect
    @contextmanager
    def traced(**kwargs):
        with original(**kwargs) as c:
            c.set_trace_callback(statements.append)
            yield c
    monkeypatch.setattr(repo,'read_connect',traced)
    assert repo.latest_path_vwaps(ids)==expected
    assert not any('imported_episodes' in q for q in statements)
    assert any('episode_path_observations' in q for q in statements)
