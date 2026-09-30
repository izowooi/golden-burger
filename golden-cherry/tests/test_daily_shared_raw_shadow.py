"""Actual sync and pin preserve Cherry's private experiment and public closure."""
from dataclasses import replace
from pathlib import Path
import json
import shutil

import pytest

from polybot.shadow import RUNTIME_JOB
from polybot_observability.market_data_raw_profiles import CHERRY_SHADOW_PROFILE_ID,raw_profile
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadReader,PayloadStore
from polybot_observability.market_data_scalar_links import scalar_namespace
from tests.test_shadow_runtime import _config
from tests.test_shared_raw_shadow import populate,analytical_result


@pytest.mark.parametrize('storage',['inline','shared-bodies','native-raw'])
def test_shadow_full_generation_scan_sync_verify_pin(tmp_path,monkeypatch,storage):
    root=Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root/'daily-rsync/src'))
    monkeypatch.syspath_prepend(str(root/'daily-rsync/tests'))
    from daily_rsync import remote_agent
    from daily_rsync.config import AppConfig,ensure_runtime_directories
    from daily_rsync.sync import SyncService,sha256
    from test_raw_storage_migrations import RawRemote

    monkeypatch.delenv('PUBLIC_MARKET_DATA_RAW',raising=False)
    monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE','fixture-source')
    monkeypatch.setenv('JOB_NAME','polybot-cherry-shadow')
    remote=RawRemote(tmp_path/'remote',strategy='golden-cherry')
    remote.job,remote.runtime='polybot-cherry-shadow',RUNTIME_JOB
    remote.path=remote.root/remote.job/remote.strategy/'data'/remote.runtime/'trades_sim.db'
    config=AppConfig(project_root=tmp_path,data_root=tmp_path/'daily',ssh_host='fixture-source',minimum_free_bytes=1)
    ensure_runtime_directories(config)
    native_config=replace(_config(tmp_path),db_path=remote.path)
    namespace=scalar_namespace(config.ssh_host,remote.job,remote.strategy,remote.runtime)
    with PayloadStore(remote.public_db) as public:
        refs=PayloadReferences(public,public)
        populate(remote.path,native_config,monkeypatch,refs if storage!='inline' else PayloadReferences(),native=storage=='native-raw')
        expected=analytical_result(remote.path,refs)
    profile=raw_profile(CHERRY_SHADOW_PROFILE_ID)
    mirror=remote_agent.RAW_PROFILE_SCHEMAS[profile.profile_id]
    assert mirror['logical_schema_sha256']==profile.logical_schema_sha256
    assert mirror['kinds']=={table:(spec.kind,) for table,spec in profile.tables.items()}
    assert mirror['level_tables']==profile.level_tables
    service=SyncService(config);service.remote=remote
    plan=service.create_plan(job=remote.job,strategy=remote.strategy)
    result=service.execute(plan)
    assert result.status=='SUCCESS',result.errors
    artifact=plan.artifacts[0];old_row=service.catalog.get_artifact(artifact.source_key)
    old_pin=service.pin_database(artifact.source_key);old_bytes=old_pin.read_bytes()
    state=remote_agent.sqlite_source_state(remote.path)
    original,target=remote.root/'original.db',remote.root/'derivative.db'
    shutil.copyfile(remote.path,original)
    with PayloadStore(remote.public_db) as public:
        manifest=migrate_raw_database(original,target,source_sha256=sha256(original),namespace=namespace,
            references=PayloadReferences(public,public),profile_id=profile.profile_id,batch_rows=2)
    manifest.update(source_snapshot_sha256=old_row['local_sha256'],source_file_sha256=sha256(original),
        original_source_path=str(remote.path),original_source_fingerprint=state['fingerprint'],original_source_members=state['members'])
    shutil.copyfile(target,remote.path)
    Path(str(remote.path)+'.raw-migration.json').write_text(json.dumps(manifest))
    plan=service.create_plan(job=remote.job,strategy=remote.strategy)
    result=service.execute(plan)
    assert result.status=='SUCCESS',result.errors
    assert service.verify(job=remote.job)['status']=='SUCCESS'
    pin=service.pin_database(artifact.source_key)
    assert pin!=old_pin and old_pin.read_bytes()==old_bytes
    with PayloadReader(config.public_store_path) as reader:
        assert analytical_result(pin,PayloadReferences(reader,cache_bytes=0))==expected
