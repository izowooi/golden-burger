import json
import shutil
import sqlite3

import pytest

from polybot_observability.market_data_catalog_links import LAYOUT_TABLE, upsert_shared_catalog
from polybot_observability.market_data_projection_closure import (
    iter_projection_ids, iter_projection_receipt_keys, verify_projection_closure,
)
from polybot_observability.market_data_projection_links import insert_shared_projection_snapshot
from polybot_observability.market_data_projection_profiles import snapshot_profile
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_market_data_catalog_links import make_source, namespace, row
from test_market_data_projection_links import schema


@pytest.mark.parametrize('snapshots', [False, True])
def test_catalog_closure_preserves_origins_and_historical_pointer_versions(tmp_path, snapshots):
    private, public = tmp_path / 'private.db', tmp_path / 'public.db'
    strategy = 'golden-blueberry'
    with PayloadStore(public) as writer, PayloadReader(public) as reader:
        refs = PayloadReferences(reader=reader, writer=writer)
        db = connect(private, references=refs)
        make_source(db, strategy)
        value = row(strategy)
        upsert_shared_catalog(db, strategy, value, references=refs, namespace=namespace(strategy), original_rowid=17)
        if snapshots:
            db.execute(schema(strategy))
            snap = dict.fromkeys(snapshot_profile(strategy).column_names)
            snap.update(id=17, condition_id='condition', probability=.8,
                        run_id='PRIVATE_RUN', timestamp='2026-09-30 00:00:00')
            insert_shared_projection_snapshot(db, strategy, snap, namespace=namespace(strategy), references=refs)
        db.commit()
        historical = tmp_path / 'historical.db'
        shutil.copyfile(private, historical)
        old = verify_projection_closure(reader, historical, strategy)
        assert old['contract'] == 'public-projection-closure-v2'
        assert old['layout_receipt_counts']['market_catalog'] == 2
        assert old['receipt_count'] == (3 if snapshots else 2)
        if snapshots:
            assert old['layout_receipt_counts']['market_snapshots'] == 1
        assert {key[1] for key in iter_projection_receipt_keys(private, strategy)} == (
            {'market_catalog', 'market_snapshots'} if snapshots else {'market_catalog'})
        before_ids = set(iter_projection_ids(private, strategy))
        value['outcome_prices_json'] = '[0.3, 0.7]'
        upsert_shared_catalog(db, strategy, value, references=refs)
        db.commit()
        current = verify_projection_closure(reader, private, strategy)
        assert current['receipt_count'] == old['receipt_count']
        assert current['closure_sha256'] != old['closure_sha256']
        assert len(before_ids & set(iter_projection_ids(private, strategy))) == len(before_ids) - 1
        assert verify_projection_closure(reader, historical, strategy) == old
        assert db.execute('SELECT * FROM private_ledger').fetchall() == [(7, 1.25, -43.12)]
        if snapshots:
            changed = json.loads(namespace(strategy))
            changed['runtime'] = 'another-runtime'
            db.execute(f'UPDATE {LAYOUT_TABLE} SET namespace=?',
                       (json.dumps(changed, sort_keys=True, separators=(',', ':')),))
            db.commit()
            with pytest.raises(ValueError, match='private owner'):
                verify_projection_closure(reader, private, strategy)
        db.close()


def test_catalog_only_empty_layout_requires_its_public_authority(tmp_path):
    from polybot_observability.market_data_catalog_links import initialize_catalog_links
    private, public = tmp_path / 'private.db', tmp_path / 'public.db'
    with PayloadStore(public) as writer:
        db = sqlite3.connect(private)
        make_source(db, 'golden-blueberry')
        db.execute('BEGIN')
        initialize_catalog_links(db, 'golden-blueberry', namespace('golden-blueberry'), writer.scalar_authority_identity())
        db.commit()
        db.close()
        with pytest.raises(ValueError, match='authority'):
            verify_projection_closure(None, private, 'golden-blueberry')
        empty = verify_projection_closure(writer, private, 'golden-blueberry')
        assert empty['record_count'] == empty['receipt_count'] == 0
        assert empty['layouts'] == {'market_catalog': ['catalog-identity-v1', 'catalog-state-v1']}
