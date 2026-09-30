"""Black's complete source profile survives actual verified sync generations."""
from pathlib import Path
import json
import shutil

import pytest

from polybot.analyzer import analyze_database
from polybot_observability.market_data_raw_profiles import BLACK_FULL_PROFILE_ID,raw_profile
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadReader,PayloadStore
from test_shared_raw_full import populate,NAMESPACE


@pytest.mark.parametrize('storage',['inline','shared-body','native-full'])
def test_full_black_scan_sync_verify_pin(tmp_path,monkeypatch,storage):
    root=Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root/'daily-rsync/src'))
    monkeypatch.syspath_prepend(str(root/'daily-rsync/tests'))
    from daily_rsync import remote_agent
    from daily_rsync.config import AppConfig,ensure_runtime_directories
    from daily_rsync.sync import SyncService,sha256
    from test_raw_storage_migrations import RawRemote
    remote=RawRemote(tmp_path/'remote',strategy='golden-black');remote.job='polybot-black'
    with PayloadStore(remote.public_db) as public:
        with monkeypatch.context() as scoped:
            cfg,repo=populate(remote.root/remote.job/remote.strategy,scoped,
                public=public if storage!='inline' else None,raw=storage=='native-full')
            expected=analyze_database(repo.path)
        remote.path=cfg.db_path;remote.runtime=cfg.job_name
    expected.pop('db')
    app=AppConfig(project_root=tmp_path,data_root=tmp_path/'daily',ssh_host='fixture-source',minimum_free_bytes=1)
    ensure_runtime_directories(app)
    profile=raw_profile(BLACK_FULL_PROFILE_ID);mirror=remote_agent.RAW_PROFILE_SCHEMAS[profile.profile_id]
    assert mirror['logical_schema_sha256']==profile.logical_schema_sha256
    assert mirror['kinds']=={table:(p.kind,) for table,p in profile.tables.items()}
    assert mirror['level_tables']==profile.level_tables
    service=SyncService(app);service.remote=remote
    plan=service.create_plan(job=remote.job,strategy=remote.strategy)
    result=service.execute(plan);assert result.status=='SUCCESS',result.errors
    artifact=plan.artifacts[0];old_row=service.catalog.get_artifact(artifact.source_key)
    old_pin=service.pin_database(artifact.source_key);old_bytes=old_pin.read_bytes()
    state=remote_agent.sqlite_source_state(remote.path)
    original,target=remote.root/'original.db',remote.root/'derivative.db'
    shutil.copyfile(remote.path,original)
    with PayloadStore(remote.public_db) as public:
        manifest=migrate_raw_database(original,target,source_sha256=sha256(original),namespace=NAMESPACE,
            references=PayloadReferences(public,public),profile_id=BLACK_FULL_PROFILE_ID,batch_rows=2)
    manifest.update(source_snapshot_sha256=old_row['local_sha256'],source_file_sha256=sha256(original),
        original_source_path=str(remote.path),original_source_fingerprint=state['fingerprint'],original_source_members=state['members'])
    shutil.copyfile(target,remote.path)
    Path(str(remote.path)+'.raw-migration.json').write_text(json.dumps(manifest))
    result=service.execute(service.create_plan(job=remote.job,strategy=remote.strategy))
    assert result.status=='SUCCESS',result.errors
    assert service.verify(job=remote.job)['status']=='SUCCESS'
    pin=service.pin_database(artifact.source_key)
    assert pin!=old_pin and old_pin.read_bytes()==old_bytes
    with PayloadReader(app.public_store_path) as reader:
        actual=analyze_database(pin,references=PayloadReferences(reader,cache_bytes=0));actual.pop('db')
        assert actual==expected
