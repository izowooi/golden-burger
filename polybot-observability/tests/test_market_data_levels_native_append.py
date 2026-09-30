"""Native level appends must not rebuild a historical main-table key index."""
import sqlite3

import pytest

from polybot_observability.market_data_levels import (
    insert_shared_levels, iter_level_logical_rows,
)
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadStore
from test_market_data_levels import levels, setup_db


def test_native_append_work_is_independent_of_inline_history(tmp_path):
    work = []
    with PayloadStore(tmp_path / 'public.db') as store:
        refs = PayloadReferences(store, store)
        for count in (200, 20000):
            db = setup_db(tmp_path / f'{count}.db')
            db.execute("INSERT INTO orderbook_snapshots VALUES('old','token')")
            db.executemany('INSERT INTO orderbook_levels(rowid,level_id,snapshot_id,side,level_index,price,size) VALUES(?,?,?,?,?,?,?)',
                ((index * 3, f'old-{index}', 'old', 'BID', index, .5, 1.) for index in range(1, count + 1)))
            db.commit()
            db.execute("INSERT INTO orderbook_snapshots VALUES('new','token')")
            steps = []
            db.set_progress_handler(lambda: steps.append(1) or 0, 100)
            insert_shared_levels(db, 'golden-black', 'orderbook_levels', levels('new', 'new-'),
                                 references=refs, preserve_rowid=True)
            db.set_progress_handler(None, 0)
            work.append(len(steps))
            logical = list(iter_level_logical_rows(db, 'golden-black', 'orderbook_levels',
                                                  references=refs, require_rowid=True))
            assert len(logical) == count + 2
            assert [row['__rowid__'] for row in logical[-2:]] == [count * 3 + 1, count * 3 + 2]
            assert [{key: value for key, value in row.items() if key != '__rowid__'} for row in logical[-2:]] == levels('new', 'new-')
            db.close()
    # A 100x history increase used to copy every historical key into TEMP.
    assert work[1] <= work[0] * 3 + 20, work


def test_native_append_preserves_null_id_alias_and_rejects_original_id_duplicate(tmp_path):
    with PayloadStore(tmp_path / 'public.db') as store:
        refs = PayloadReferences(store, store)
        db = setup_db(tmp_path / 'source.db')
        for snapshot in ('old', 'new', 'duplicate'):
            db.execute('INSERT INTO orderbook_snapshots VALUES(?,?)', (snapshot, 'token'))
        old = {**levels('old', 'unused')[0], 'level_id': None, '__rowid__': 1000}
        insert_shared_levels(db, 'golden-black', 'orderbook_levels', [old], references=refs, preserve_rowid=True)
        alias = '__original_level_rowid__:1000'
        incoming = {**levels('new', 'unused')[0], 'level_id': alias}
        insert_shared_levels(db, 'golden-black', 'orderbook_levels', [incoming], references=refs, preserve_rowid=True)
        logical = list(iter_level_logical_rows(db, 'golden-black', 'orderbook_levels', references=refs, require_rowid=True))
        assert [(row['__rowid__'], row['level_id']) for row in logical] == [(1000, None), (1001, alias)]
        with pytest.raises(sqlite3.IntegrityError):
            insert_shared_levels(db, 'golden-black', 'orderbook_levels',
                [{**incoming, 'snapshot_id': 'duplicate'}], references=refs, preserve_rowid=True)
        assert db.execute('SELECT COUNT(*) FROM _market_data_level_bindings').fetchone()[0] == 2
        db.close()


def test_explicit_rowid_validation_still_detects_native_id_after_same_connection_switch(tmp_path):
    with PayloadStore(tmp_path / 'public.db') as store:
        refs = PayloadReferences(store, store)
        db = setup_db(tmp_path / 'source.db')
        for snapshot in ('explicit', 'native', 'collision'):
            db.execute('INSERT INTO orderbook_snapshots VALUES(?,?)', (snapshot, 'token'))
        insert_shared_levels(db, 'golden-black', 'orderbook_levels',
            [{**levels('explicit', 'old-')[0], '__rowid__': 30}], references=refs, preserve_rowid=True)
        insert_shared_levels(db, 'golden-black', 'orderbook_levels', levels('native', 'new-'),
                             references=refs, preserve_rowid=True)
        with pytest.raises(sqlite3.IntegrityError):
            insert_shared_levels(db, 'golden-black', 'orderbook_levels',
                [{**levels('collision', 'other-')[0], '__rowid__': 31}], references=refs, preserve_rowid=True)
        assert [row['__rowid__'] for row in iter_level_logical_rows(db, 'golden-black', 'orderbook_levels', references=refs, require_rowid=True)] == [30, 31, 32]
        db.close()


def test_native_append_rolls_back_allocated_ids_and_keeps_legacy_unknown_rowids_rejected(tmp_path):
    with PayloadStore(tmp_path / 'public.db') as store:
        refs = PayloadReferences(store, store)
        db = setup_db(tmp_path / 'source.db')
        db.execute("INSERT INTO orderbook_snapshots VALUES('new','token')")
        db.commit()
        db.execute('BEGIN')
        insert_shared_levels(db, 'golden-black', 'orderbook_levels', levels('new', 'first-'), references=refs, preserve_rowid=True)
        db.rollback()
        db.execute('BEGIN')
        insert_shared_levels(db, 'golden-black', 'orderbook_levels', levels('new', 'retry-'), references=refs, preserve_rowid=True)
        assert [row['__rowid__'] for row in iter_level_logical_rows(db, 'golden-black', 'orderbook_levels', references=refs, require_rowid=True)] == [1, 2]
        db.rollback()
        db.execute('BEGIN')
        insert_shared_levels(db, 'golden-black', 'orderbook_levels', levels('new', 'legacy-'), references=refs)
        db.execute("INSERT INTO orderbook_snapshots VALUES('next','token')")
        with pytest.raises(ValueError, match='original level rowid is unknown'):
            insert_shared_levels(db, 'golden-black', 'orderbook_levels', levels('next', 'next-'), references=refs, preserve_rowid=True)
        db.close()
