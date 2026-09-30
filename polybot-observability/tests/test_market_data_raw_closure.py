"""A RAW pin closes over both public groups and retained mixed/body references."""
import json
import sqlite3

import pytest

from polybot_observability.market_data_bundle import (
    export_bundle, import_bundle, reference_closure,
)
from polybot_observability.market_data_projection_bundle import (
    export_projection_bundle, import_projection_bundle,
)
from polybot_observability.market_data_projection_closure import (
    iter_projection_ids, iter_projection_receipt_keys, projection_identity,
    verify_projection_closure,
)
from polybot_observability.market_data_raw_links import (
    INDEX_SQL, LAYOUT_TABLE, RAW_TABLES, immutable_sql, initialize_raw_links,
    insert_raw_rows, iter_raw_logical_rows,
)
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadStore


STRATEGY = 'golden-black'
NAMESPACE = scalar_namespace('source', 'polybot-black', STRATEGY, 'black-raw')


def seed_raw(path, store):
    refs = PayloadReferences(store, store)
    with connect(path, references=refs) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('CREATE TABLE market_sweeps(sweep_id TEXT PRIMARY KEY)')
        for table, profile in RAW_TABLES.items():
            db.execute(profile.source_sql)
            for op in ('UPDATE', 'DELETE'):
                db.execute(immutable_sql(table, op))
        for sql in INDEX_SQL.values():
            db.execute(sql)
        db.execute("INSERT INTO market_sweeps VALUES('sweep')")
        initialize_raw_links(db, NAMESPACE, store.scalar_authority_identity(), references=refs)
        for rowid, (table, profile) in zip((-7, 0, 99), RAW_TABLES.items(), strict=True):
            row = {name: (0 if affinity == 'INTEGER' else .8 if affinity == 'REAL' else
                          '{}' if name.endswith('_json') else 'source')
                   for _, name, affinity, *_ in profile.info}
            row.update(__rowid__=rowid, **{profile.primary_key: table})
            for name, value in {
                'sweep_id': 'sweep', 'market_observation_id': 'market_observations',
                'condition_id': 'condition', 'event_id': 'event', 'token_id': 'token',
                'observed_at': '2026-09-29T23:00:00Z',
                'source_timestamp': '1790722800000',
                'normalized_json': '{"condition_id":"condition","fee_rate":0.02}',
            }.items():
                if name in row:
                    row[name] = value
            insert_raw_rows(db, STRATEGY, table, [row], references=refs)
            if table == 'orderbook_snapshots':
                row.update(__rowid__=101, snapshot_id='second-book', run_id='another-run')
                insert_raw_rows(db, STRATEGY, table, [row], references=refs)
        db.commit()


def test_raw_closure_groups_deduplicate_but_receipts_keep_table_rowid_and_namespace(tmp_path):
    private = tmp_path / 'private.db'
    with PayloadStore(tmp_path / 'origin.db') as store:
        seed_raw(private, store)
        identity = projection_identity(private, STRATEGY)
        assert identity['namespace'] == NAMESPACE
        assert identity['raw_layout_contract'] == 'black-raw-parent-skeleton-v1'
        result = verify_projection_closure(store, private, STRATEGY)
        assert result['contract'] == 'public-projection-closure-v3'
        assert result['record_count'] == 3
        assert result['receipt_count'] == 4
        assert result['layout_receipt_counts'] == {
            'market_observations': 1, 'outcome_observations': 1, 'orderbook_snapshots': 2,
        }
        keys = list(iter_projection_receipt_keys(private, STRATEGY))
        assert {(k[1], k[2]) for k in keys} == {
            ('market_observations', -7), ('outcome_observations', 0),
            ('orderbook_snapshots', 99), ('orderbook_snapshots', 101),
        }
        ids = list(iter_projection_ids(private, STRATEGY))
        assert ids == sorted(set(k[4] for k in keys))
        with pytest.raises(ValueError, match='strategy'):
            projection_identity(private, 'golden-plum')


def test_raw_pin_can_sync_groups_before_body_and_then_reproduce_exact_logical_rows(tmp_path):
    private = tmp_path / 'private.db'
    with PayloadStore(tmp_path / 'origin.db') as source, PayloadStore(tmp_path / 'replica.db') as target:
        seed_raw(private, source)
        ids = list(iter_projection_ids(private, STRATEGY))
        package = tmp_path / 'groups.db'
        manifest = export_projection_bundle(source, ids, package, authority_uuid=source.scalar_authority_identity())
        import_projection_bundle(package, manifest, target)
        # Mixed bodies have not been copied, but group identity proof is already possible.
        expected = verify_projection_closure(source, private, STRATEGY)
        assert verify_projection_closure(target, private, STRATEGY) == expected
        hashes = reference_closure(private, STRATEGY)
        assert hashes
        body_package = tmp_path / 'bodies.db'
        body_manifest = export_bundle(source, hashes, body_package)
        import_bundle(body_package, body_manifest, target)
        before, after = PayloadReferences(source, source), PayloadReferences(target, target)
        with connect(private, references=before) as left, connect(private, references=after) as right:
            for table in RAW_TABLES:
                assert list(iter_raw_logical_rows(left, table, references=before)) == list(
                    iter_raw_logical_rows(right, table, references=after))
            row = right.execute('SELECT normalized_json FROM market_observations').fetchone()[0]
            assert json.loads(row)['fee_rate'] == .02


@pytest.mark.parametrize('damage', ['identity', 'rowid', 'record', 'namespace'])
def test_raw_closure_rejects_wrong_subject_or_receipt_even_when_group_exists(tmp_path, damage):
    private = tmp_path / 'private.db'
    with PayloadStore(tmp_path / 'origin.db') as store:
        seed_raw(private, store)
        with sqlite3.connect(private) as db:
            table = LAYOUT_TABLE if damage == 'namespace' else 'orderbook_snapshots'
            db.execute('DROP TRIGGER ' + table + '_forbid_update')
            if damage == 'identity':
                db.execute("UPDATE orderbook_snapshots SET token_id='another-token' WHERE rowid=99")
            elif damage == 'rowid':
                db.execute('UPDATE orderbook_snapshots SET rowid=199 WHERE rowid=99')
            elif damage == 'record':
                other = db.execute('SELECT _public_record_id FROM outcome_observations').fetchone()[0]
                db.execute('UPDATE orderbook_snapshots SET _public_record_id=? WHERE rowid=99', (other,))
            else:
                changed = scalar_namespace('source', 'polybot-black', STRATEGY, 'another-runtime')
                db.execute(f'UPDATE {LAYOUT_TABLE} SET namespace=?', (changed,))
            db.execute(immutable_sql(table, 'UPDATE'))
        with pytest.raises(ValueError, match='identity|receipt|kind'):
            verify_projection_closure(store, private, STRATEGY)


def test_raw_closure_requires_its_original_public_authority(tmp_path):
    private = tmp_path / 'private.db'
    with PayloadStore(tmp_path / 'origin.db') as store:
        seed_raw(private, store)
    with PayloadStore(tmp_path / 'wrong.db') as wrong:
        with pytest.raises(ValueError, match='authority'):
            verify_projection_closure(wrong, private, STRATEGY)
