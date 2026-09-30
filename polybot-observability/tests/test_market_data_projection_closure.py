import sqlite3

import pytest

from polybot_observability.market_data_projection_closure import (
    iter_projection_ids, iter_projection_receipt_keys, verify_projection_closure,
)
from polybot_observability.market_data_projection_links import (
    CONTEXT_TABLE, CONDITION_TABLE, insert_shared_projection_snapshot, delete_snapshot_rows,
)
from polybot_observability.market_data_projection_profiles import snapshot_profile
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore


def test_projection_closure_tracks_public_values_and_private_source_receipts(tmp_path):
    path = tmp_path / 'private.db'
    public = tmp_path / 'public.db'
    strategy = 'golden-blueberry'
    profile = snapshot_profile(strategy)
    namespace = scalar_namespace('source', 'job-a', strategy, 'default')
    with PayloadStore(public) as writer, PayloadReader(public) as reader:
        refs = PayloadReferences(reader=reader, writer=writer)
        db = connect(path, references=refs)
        db.execute('CREATE TABLE market_snapshots(' + ','.join(
            column.name + ' ' + column.affinity + (' PRIMARY KEY' if column.name == 'id' else ' NOT NULL' if not column.nullable else '')
            for column in profile.columns
        ) + ')')
        values = dict.fromkeys(profile.column_names)
        values.update(condition_id='public-condition', probability=.81, timestamp='2026-09-29 10:00:00', run_id='PRIVATE_RUN')
        insert_shared_projection_snapshot(db, strategy, values, namespace=namespace, references=refs)
        values['run_id'] = 'PRIVATE_OTHER_RUN'
        values['timestamp'] = '2026-09-29 10:01:00'
        insert_shared_projection_snapshot(db, strategy, values, namespace=namespace, references=refs)
        db.commit()
        verified = verify_projection_closure(reader, path, strategy)
        assert verified['record_count'] == 1 and verified['receipt_count'] == 2
        assert verified['namespace'] == namespace and verified['kinds'] == ['gamma-quote-v1']
        assert len(list(iter_projection_ids(path, strategy))) == 1
        assert len(list(iter_projection_receipt_keys(path, strategy))) == 2
        assert 'PRIVATE_RUN' not in repr(verified)
        db.execute(f"UPDATE {CONDITION_TABLE} SET condition_id='wrong-public-identity'")
        db.commit()
        with pytest.raises(ValueError, match='context/public identity'):
            verify_projection_closure(reader, path, strategy)
        db.execute(f"UPDATE {CONDITION_TABLE} SET condition_id='public-condition'")
        db.commit()
        assert verify_projection_closure(reader, path, strategy) == verified
        with pytest.raises(ValueError, match='source identity'):
            verify_projection_closure(reader, path, 'golden-kiwi')
        # Retention removes the dependency, not its public observation history.
        assert delete_snapshot_rows(db, 'id=1') == 1
        db.commit()
        after = verify_projection_closure(reader, path, strategy)
        assert after['record_count'] == 1 and after['receipt_count'] == 1
        writer._connection.execute('DROP TRIGGER projection_receipts_no_delete')
        writer._connection.execute('DELETE FROM projection_receipts WHERE original_id=2')
        writer._connection.commit()
        with pytest.raises(ValueError, match='source receipt'):
            verify_projection_closure(reader, path, strategy)
        db.close()


def test_missing_layout_is_legacy_but_partial_projection_layout_is_not(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY)')
    result = verify_projection_closure(None, path, 'golden-blueberry')
    assert result['authority_uuid'] is None and result['record_count'] == 0
    with sqlite3.connect(path) as connection:
        connection.execute(f'CREATE TABLE {CONTEXT_TABLE}(id INTEGER PRIMARY KEY)')
    with pytest.raises(ValueError, match='incomplete'):
        verify_projection_closure(None, path, 'golden-blueberry')
