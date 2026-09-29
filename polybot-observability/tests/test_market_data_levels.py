import sqlite3

import pytest

from polybot_observability.market_data_levels import insert_shared_levels, install_level_views
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadStore


def setup_db(path):
    c=sqlite3.connect(path)
    c.execute('PRAGMA foreign_keys=ON')
    c.executescript('''
      CREATE TABLE orderbook_snapshots(snapshot_id TEXT PRIMARY KEY,token_id TEXT);
      CREATE TABLE orderbook_levels(level_id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL REFERENCES orderbook_snapshots(snapshot_id),side TEXT CHECK(side IN ('BID','ASK')),level_index INTEGER,price REAL,size REAL,UNIQUE(snapshot_id,side,level_index));
      CREATE TRIGGER no_delete BEFORE DELETE ON orderbook_levels BEGIN SELECT RAISE(ABORT,'append-only');END;
    ''')
    return c


def levels(snapshot, prefix):
    return [dict(level_id=prefix+'a',snapshot_id=snapshot,side='ASK',level_index=0,price=.7,size=20.),
            dict(level_id=prefix+'b',snapshot_id=snapshot,side='BID',level_index=0,price=.69,size=30.)]


def test_public_values_dedup_but_observation_ids_and_queries_survive(tmp_path):
    with PayloadStore(tmp_path/'public.db') as shared:
        codec=PayloadReferences(shared,shared)
        c=setup_db(tmp_path/'private.db')
        c.execute("INSERT INTO orderbook_snapshots VALUES('s1','token')")
        assert insert_shared_levels(c,'golden-black','orderbook_levels',levels('s1','one'),references=codec)
        c.execute("INSERT INTO orderbook_snapshots VALUES('s2','token')")
        assert insert_shared_levels(c,'golden-black','orderbook_levels',levels('s2','two'),references=codec)
        c.commit()
        assert c.execute('SELECT COUNT(*) FROM main.orderbook_levels').fetchone()[0]==0
        assert shared.stats()['payload_count']==1
        assert c.execute('SELECT s.token_id,l.snapshot_id,l.side,l.price,l.size FROM orderbook_snapshots s JOIN orderbook_levels l USING(snapshot_id) ORDER BY snapshot_id,side').fetchall()==[
            ('token','s1','ASK',.7,20.),('token','s1','BID',.69,30.),('token','s2','ASK',.7,20.),('token','s2','BID',.69,30.)]
        assert c.execute('SELECT COUNT(DISTINCT level_id) FROM orderbook_levels').fetchone()[0]==4
        c.close()
        reopened=sqlite3.connect(tmp_path/'private.db')
        install_level_views(reopened,references=PayloadReferences(reader=shared))
        assert reopened.execute('SELECT SUM(size) FROM orderbook_levels WHERE snapshot_id=?',('s1',)).fetchone()[0]==50.
        reopened.close()


def test_local_rollback_never_publishes_shared_group(tmp_path):
    with PayloadStore(tmp_path/'public.db') as shared:
        c=setup_db(tmp_path/'private.db');codec=PayloadReferences(shared,shared)
        c.execute("INSERT INTO orderbook_snapshots VALUES('s','token')")
        insert_shared_levels(c,'golden-black','orderbook_levels',levels('s','p'),references=codec)
        c.rollback()
        assert c.execute('SELECT COUNT(*) FROM orderbook_snapshots').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM orderbook_levels').fetchone()[0]==0
        assert shared.stats()['payload_count']==1  # immutable orphan, not a published path
        c.close()


def test_original_constraints_and_duplicate_ids_still_fail(tmp_path):
    with PayloadStore(tmp_path/'public.db') as shared:
        c=setup_db(tmp_path/'private.db');codec=PayloadReferences(shared,shared)
        c.execute("INSERT INTO orderbook_snapshots VALUES('s','token')")
        invalid=levels('s','bad');invalid[0]['side']='INVALID'
        with pytest.raises(sqlite3.IntegrityError):
            insert_shared_levels(c,'golden-black','orderbook_levels',invalid,references=codec)
        assert shared.stats()['payload_count']==0
        insert_shared_levels(c,'golden-black','orderbook_levels',levels('s','ids'),references=codec)
        c.execute("INSERT INTO orderbook_snapshots VALUES('s2','token')")
        with pytest.raises(sqlite3.IntegrityError):
            insert_shared_levels(c,'golden-black','orderbook_levels',levels('s2','ids'),references=codec)
        assert c.execute('SELECT COUNT(*) FROM _market_data_level_groups').fetchone()[0]==1
        c.rollback();c.close()


def test_resolving_connection_reopens_levels_and_closure(tmp_path):
    from polybot_observability.market_data_bundle import reference_closure,verify_closure
    from polybot_observability.market_data_sqlite import connect
    with PayloadStore(tmp_path/'public.db') as shared:
        codec=PayloadReferences(shared,shared)
        path=tmp_path/'private.db'
        c=setup_db(path)
        c.execute("INSERT INTO orderbook_snapshots VALUES('s','token')")
        insert_shared_levels(c,'golden-black','orderbook_levels',levels('s','l'),references=codec)
        c.commit();c.close()
        reopened=connect(path,references=PayloadReferences(reader=shared))
        assert reopened.execute('SELECT SUM(size) FROM orderbook_levels').fetchone()[0]==50.
        hashes=reference_closure(path,'golden-black')
        assert verify_closure(shared,hashes)['payload_count']==1
        reopened.close()


def test_mixed_experimental_flags_stay_private_while_prices_share(tmp_path):
    import json
    from polybot_observability.market_data_bundle import reference_closure
    with PayloadStore(tmp_path/'public.db') as shared:
        c=setup_db(tmp_path/'private.db');codec=PayloadReferences(shared,shared)
        c.execute('ALTER TABLE orderbook_levels ADD COLUMN in_near_touch_window INTEGER')
        c.execute('ALTER TABLE orderbook_levels ADD COLUMN used_for_entry INTEGER')
        for index,flag in enumerate((0,1)):
            snapshot=str(index)
            c.execute('INSERT INTO orderbook_snapshots VALUES(?,?)',(snapshot,'token'))
            rows=[{**row,'in_near_touch_window':1,'used_for_entry':flag} for row in levels(snapshot,snapshot)]
            insert_shared_levels(c,'golden-raspberry','orderbook_levels',rows,references=codec)
        c.commit()
        assert c.execute('SELECT snapshot_id,MAX(used_for_entry) FROM orderbook_levels GROUP BY snapshot_id').fetchall()==[('0',0),('1',1)]
        assert shared.stats()['payload_count']==1
        hashes=reference_closure(tmp_path/'private.db','golden-raspberry')
        public=json.loads(shared.get_many(hashes)[0])
        assert all('used_for_entry' not in row and 'in_near_touch_window' not in row for row in public)
        c.close()


def test_parent_link_is_required_even_with_sqlite_foreign_keys_off(tmp_path):
    with PayloadStore(tmp_path/'public.db') as shared:
        c=setup_db(tmp_path/'private.db');codec=PayloadReferences(shared,shared)
        c.execute('PRAGMA foreign_keys=OFF');c.execute('BEGIN')
        with pytest.raises(ValueError,match='parent observation'):
            insert_shared_levels(c,'golden-black','orderbook_levels',levels('missing','l'),references=codec)
        assert shared.stats()['payload_count']==0
        c.rollback();c.close()
