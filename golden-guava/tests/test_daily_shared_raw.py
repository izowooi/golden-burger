"""Native Guava guarded lifecycles pass through actual scan/sync/verify/pin."""
from pathlib import Path
from contextlib import closing
import json
import shutil

import pytest

from polybot.evidence import Repository
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_raw_profiles import GUAVA_PROFILE_ID, raw_profile
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_shared_raw_source import GUAVA_CONTRACT, inline_lifecycles, publish


@pytest.mark.parametrize("native", [False, True], ids=["inline-source", "shared-source"])
def test_guava_full_guarded_generation_and_pin(tmp_path, monkeypatch, native):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "daily-rsync/src"))
    monkeypatch.syspath_prepend(str(root / "daily-rsync/tests"))
    from daily_rsync import remote_agent
    from daily_rsync.config import AppConfig, ensure_runtime_directories
    from daily_rsync.sync import SyncService, sha256
    from test_raw_storage_migrations import RawRemote

    remote = RawRemote(tmp_path / "remote", strategy="golden-guava")
    remote.job, remote.runtime = "polybot-sim-guava-a", GUAVA_CONTRACT["job_name"]
    remote.path = remote.root / remote.job / remote.strategy / "data" / remote.runtime / "trades_sim.db"
    # The fake remote owns this workspace setup, just as the native runtime
    # creates data/<runtime> before Repository opens its canonical database.
    remote.path.parent.mkdir(parents=True, exist_ok=True)
    config = AppConfig(project_root=tmp_path, data_root=tmp_path / "daily", ssh_host="fixture-source", minimum_free_bytes=1)
    ensure_runtime_directories(config)
    namespace = scalar_namespace(config.ssh_host, remote.job, remote.strategy, remote.runtime)
    if native:
        with PayloadStore(remote.public_db) as store:
            with closing(Repository(remote.path, GUAVA_CONTRACT, raw_profile_id=GUAVA_PROFILE_ID,
                                    raw_namespace=namespace, references=PayloadReferences(store, store))) as repo:
                publish(repo)
    else:
        inline_lifecycles(remote.path)
    profile = raw_profile(GUAVA_PROFILE_ID)
    mirror = remote_agent.RAW_PROFILE_SCHEMAS[GUAVA_PROFILE_ID]
    assert mirror["logical_schema_sha256"] == profile.logical_schema_sha256
    assert mirror["kinds"] == {table: (p.kind,) for table, p in profile.tables.items()}
    service = SyncService(config); service.remote = remote
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    result = service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    artifact = plan.artifacts[0]
    old_row = service.catalog.get_artifact(artifact.source_key)
    old_pin = service.pin_database(artifact.source_key)
    old_bytes = old_pin.read_bytes()
    original = remote.root / "original.db"; target = remote.root / "derivative.db"
    state = remote_agent.sqlite_source_state(remote.path)
    shutil.copyfile(remote.path, original)
    with PayloadStore(remote.public_db) as store:
        refs = PayloadReferences(store, store)
        with closing(Repository(original, GUAVA_CONTRACT, read_only=True, references=refs)) as repo:
            expected = repo.status(); expected_cache = repo.previous_events()
        manifest = migrate_raw_database(original, target, source_sha256=sha256(original),
            namespace=namespace, references=refs, profile_id=GUAVA_PROFILE_ID, batch_rows=1)
    manifest.update(source_snapshot_sha256=old_row["local_sha256"], source_file_sha256=sha256(original),
                    original_source_path=str(remote.path), original_source_fingerprint=state["fingerprint"],
                    original_source_members=state["members"])
    shutil.copyfile(target, remote.path)
    Path(str(remote.path) + ".raw-migration.json").write_text(json.dumps(manifest))
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    result = service.execute(plan)
    assert result.status == "SUCCESS", result.errors
    assert service.verify(job=remote.job)["status"] == "SUCCESS"
    pin = service.pin_database(artifact.source_key)
    assert pin != old_pin and old_pin.read_bytes() == old_bytes
    with PayloadReader(config.public_store_path) as reader:
        with closing(Repository(pin, GUAVA_CONTRACT, read_only=True,
                                references=PayloadReferences(reader=reader, cache_bytes=0))) as repo:
            assert repo.previous_events() == expected_cache
            actual = repo.status()
            # Status's physical sizes must describe the new derivative, whose
            # RAW/packet schema changes its size without changing research state.
            assert {key: value for key, value in actual.items() if key != "file_sizes"} == {
                key: value for key, value in expected.items() if key != "file_sizes"
            }
            assert actual["file_sizes"] == {
                "database_bytes": pin.stat().st_size, "wal_bytes": 0, "shm_bytes": 0,
            }
            assert not repo.connection.execute("PRAGMA foreign_key_check").fetchall()
