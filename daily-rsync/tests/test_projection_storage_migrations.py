import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest
from test_projection_payloads import ProjectionRemote, create_schema

from daily_rsync import remote_agent
from daily_rsync.sync import SyncService, sha256
from polybot_observability.market_data_index import ReceiptContext
from polybot_observability.market_data_migrate import migrate_public_bodies
from polybot_observability.market_data_projection_links import CONTEXT_TABLE, RUN_TABLE
from polybot_observability.market_data_projection_profiles import snapshot_profile
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadStore


@pytest.fixture
def transition(app_config, tmp_path):
    remote = ProjectionRemote(tmp_path / 'remote')
    create_schema(remote)
    columns = snapshot_profile(remote.strategy).column_names
    with sqlite3.connect(remote.path) as connection:
        for number in (17, 18):
            values = dict.fromkeys(columns)
            values.update(id=number, condition_id='condition-a', probability=.75,
                          run_id='PRIVATE_RUN', timestamp=f'2026-09-01 00:00:{number}.000000')
            connection.execute('INSERT INTO market_snapshots VALUES(' + ','.join('?' for _ in columns) + ')',
                               tuple(values[name] for name in columns))
    service = SyncService(app_config)
    service.remote = remote
    initial = service.create_plan(job=remote.job, strategy=remote.strategy)
    result = service.execute(initial)
    assert result.status == 'SUCCESS', result.errors
    artifact = initial.artifacts[0]
    destination = service.local_path(artifact)
    old_row = service.catalog.get_artifact(artifact.source_key)
    old_pin = service.pin_database(artifact.source_key)
    state = remote_agent.sqlite_source_state(remote.path)
    original = remote.root / 'original.db'
    target = remote.root / 'derivative.db'
    shutil.copyfile(remote.path, original)
    with PayloadStore(remote.public_db) as store:
        manifest = migrate_public_bodies(original, target, strategy=remote.strategy,
            source_sha256=sha256(original), references=PayloadReferences(store, store),
            include_projections=True,
            receipt_context=ReceiptContext(app_config.ssh_host, remote.runtime, remote.job))
    manifest.update(source_snapshot_sha256=old_row['local_sha256'], source_file_sha256=sha256(original),
                    original_source_path=str(remote.path), original_source_fingerprint=state['fingerprint'],
                    original_source_members=state['members'])
    shutil.copyfile(target, remote.path)
    sidecar = Path(str(remote.path) + '.storage-migration.json')
    sidecar.write_text(json.dumps(manifest))
    return SimpleNamespace(service=service, remote=remote, artifact=artifact, original=original,
                           target=target, manifest=manifest, sidecar=sidecar, old_pin=old_pin,
                           old_row=old_row, destination=destination)


def test_verified_projection_transition_preserves_original_and_new_pin(transition):
    m = transition
    old_bytes = m.old_pin.read_bytes()
    plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    assert plan.artifacts[0].storage_migration['manifest']['public_projection_records']['receipt_count'] == 2
    result = m.service.execute(plan)
    assert result.status == 'SUCCESS', result.errors
    assert m.service.verify(job=m.remote.job, strategy=m.remote.strategy)['status'] == 'SUCCESS'
    pin = m.service.pin_database(m.artifact.source_key)
    manifest = json.loads((pin.parent / 'manifest.json').read_text())
    assert manifest['storage_lineage']['public_projection_records']['receipt_count'] == 2
    assert manifest['public_payloads']['public_projection_records']['route_identity_verified'] is True
    assert pin != m.old_pin and m.old_pin.read_bytes() == old_bytes
    with sqlite3.connect(pin) as db:
        assert db.execute('SELECT COUNT(*) FROM market_snapshots').fetchone()[0] == 0
        assert db.execute('SELECT * FROM trades').fetchall() == [(7, 'PRIVATE_ACCOUNT_SENTINEL', 12.5)]


@pytest.mark.parametrize('damage', ['private_ledger', 'private_clock', 'private_run', 'record', 'count', 'private_reference'])
def test_projection_transition_rejects_changed_economic_or_runtime_evidence(transition, damage):
    m = transition
    before = m.destination.read_bytes()
    if damage == 'count':
        m.manifest['public_projection_records']['receipt_count'] += 1
    else:
        with sqlite3.connect(m.remote.path) as connection:
            if damage == 'private_ledger':
                connection.execute('UPDATE trades SET private_pnl=99')
            elif damage == 'private_clock':
                connection.execute(f"UPDATE {CONTEXT_TABLE} SET timestamp='2099-01-01'")
            elif damage == 'private_run':
                connection.execute(f"UPDATE {RUN_TABLE} SET run_id='wrong-run'")
            elif damage == 'record':
                connection.execute(f'UPDATE {CONTEXT_TABLE} SET projection_0_id=999')
            else:
                with PayloadStore(m.remote.public_db) as store:
                    reference = PayloadReferences(store, store).encode_many(['PRIVATE_RUN'])[0]
                connection.execute(f'UPDATE {RUN_TABLE} SET run_id=?', (reference,))
        m.manifest['destination_sha256'] = sha256(m.remote.path)
    m.sidecar.write_text(json.dumps(m.manifest))
    plan = m.service.create_plan(job=m.remote.job, strategy=m.remote.strategy)
    result = m.service.execute(plan)
    assert result.status == 'FAILED', result.errors
    assert m.destination.read_bytes() == before
    assert m.service.catalog.get_artifact(m.artifact.source_key)['local_sha256'] == m.old_row['local_sha256']
