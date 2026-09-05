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
