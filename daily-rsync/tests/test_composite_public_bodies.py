import json
import sqlite3

from test_catalog_payloads import create_catalog_schema, catalog_row
from test_projection_payloads import ProjectionRemote, create_schema

from daily_rsync.public_payloads import closure_sidecar
from daily_rsync.sync import SyncService
from polybot_observability.market_data_bundle import reference_closure
from polybot_observability.market_data_catalog_links import upsert_shared_catalog
from polybot_observability.market_data_projection_links import insert_shared_projection_snapshot
from polybot_observability.market_data_projection_profiles import snapshot_profile
from polybot_observability.market_data_refs import PayloadReferences, value_reference_hashes
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore


def test_composite_plum_sync_pins_retained_book_tags_and_legacy_catalog_bodies(app_config, tmp_path):
    remote = ProjectionRemote(tmp_path / 'remote', strategy='golden-plum')
    create_schema(remote)
    create_catalog_schema(remote)
    namespace = scalar_namespace(app_config.ssh_host, remote.job, remote.strategy, remote.runtime)
    book = '{"bids":[{"price":"0.70","size":"20"}], "asks":[]}'
    tags = '[ "soccer", "source-only" ]'
    with PayloadStore(remote.public_db) as store:
        refs = PayloadReferences(reader=store, writer=store)
        book_ref, tags_ref, old_array = refs.encode_many([book, tags, '[ "Old Yes", "Old No" ]'])
        expected = sorted({digest for marker in (book_ref, tags_ref, old_array) for digest in value_reference_hashes(marker)})
        db = connect(remote.path, references=refs)
        snapshot = dict.fromkeys(snapshot_profile(remote.strategy).column_names)
        snapshot.update(id=17, condition_id='condition-a', token_id='token-a', outcome='Yes',
                        probability=.71, best_bid=.70, best_ask=.72, spread=.02,
                        run_id='PRIVATE_RUN', timestamp='2026-09-30 00:00:00',
                        book_json=book_ref, market_tags_json=tags_ref)
        insert_shared_projection_snapshot(db, remote.strategy, snapshot, namespace=namespace, references=refs)
        upsert_shared_catalog(db, remote.strategy, catalog_row(remote.strategy, 'condition-a'),
                              namespace=namespace, references=refs, original_rowid=-7)
        old = catalog_row(remote.strategy, 'legacy-condition')
        old['outcomes_json'] = old_array
        columns = tuple(old)
        db.execute('INSERT INTO main.market_catalog(rowid,' + ','.join(columns) + ') VALUES ('
                   + ','.join('?' for _ in range(len(columns) + 1)) + ')', (99, *(old[name] for name in columns)))
        db.commit()
        db.close()
    assert reference_closure(remote.path, remote.strategy) == expected
    service = SyncService(app_config)
    service.remote = remote
    plan = service.create_plan(job=remote.job, strategy=remote.strategy)
    artifact = plan.artifacts[0]
    result = service.execute(plan)
    assert result.status == 'SUCCESS', result.errors
    descriptor = json.loads(closure_sidecar(service.local_path(artifact)).read_text())
    assert descriptor['payload_count'] == 3
    assert descriptor['public_projection_records']['contract'] == 'public-projection-closure-v2'
    assert service.verify(job=remote.job, strategy=remote.strategy)['status'] == 'SUCCESS'
    pin = service.pin_database(artifact.source_key)
    with PayloadReader(app_config.public_store_path) as reader:
        assert reader.stats()['payload_count'] == 3
        restored = connect(pin, references=PayloadReferences(reader=reader))
        try:
            assert restored.execute('SELECT book_json,market_tags_json FROM market_snapshots WHERE id=17').fetchone() == (book, tags)
            assert restored.execute("SELECT outcomes_json FROM market_catalog WHERE condition_id='legacy-condition'").fetchone() == ('[ "Old Yes", "Old No" ]',)
            assert restored.execute('SELECT * FROM trades').fetchall() == [(7, 'PRIVATE_ACCOUNT_SENTINEL', 12.5)]
        finally:
            restored.close()
