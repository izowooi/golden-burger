"""Local packet pointers stay private and resolve before public-source decoding."""
import sqlite3

import pytest

from polybot_observability.market_data_bundle import reference_closure
from polybot_observability.market_data_migrate import validate_private_reference_ownership
from polybot_observability.market_data_mixed import is_mixed_payload
from polybot_observability.market_data_private_packets import (
    PACKET_TABLE, initialize_private_packets, intern_private_packet, is_private_packet,
)
from polybot_observability.market_data_refs import (
    PayloadReferences, externalize_row, is_external_value, value_reference_hashes,
)
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect, expand_private_values
from polybot_observability.market_data_store import PayloadStore


STRATEGY = 'golden-black'
NAMESPACE = scalar_namespace('source', 'job', STRATEGY, 'runtime')
ORIGINAL = ' {"condition_id":"source-condition", "fee_rate":0.0200, "decision":"KEEP_PRIVATE"} '


def private_database(path, refs):
    db = connect(path, references=refs)
    db.execute('CREATE TABLE market_observations(id INTEGER PRIMARY KEY,normalized_json TEXT,decision TEXT)')
    db.execute('BEGIN')
    initialize_private_packets(db, strategy=STRATEGY, namespace=NAMESPACE)
    packet = externalize_row(STRATEGY, 'market_observations', {'normalized_json': ORIGINAL},
                             references=refs)['normalized_json']
    marker = intern_private_packet(db, packet, strategy=STRATEGY, namespace=NAMESPACE,
                                   table='market_observations', column='normalized_json', references=refs)
    db.executemany('INSERT INTO market_observations VALUES(?,?,?)',
                   [(1, marker, 'KEEP_PRIVATE'), (2, marker, 'KEEP_PRIVATE')])
    db.commit()
    return db, packet, marker


def test_alias_row_factories_reopen_and_body_closure_keep_original_private_bytes(tmp_path):
    path = tmp_path / 'private.db'
    with PayloadStore(tmp_path / 'public.db') as store:
        refs = PayloadReferences(store, store)
        db, packet, marker = private_database(path, refs)
        assert is_private_packet(marker) and is_mixed_payload(packet)
        assert expand_private_values(db, [marker, 7, None]) == [packet, 7, None]
        db.row_factory = sqlite3.Row
        assert db.execute('SELECT normalized_json AS evidence FROM market_observations').fetchone()['evidence'] == ORIGINAL
        validate_private_reference_ownership(db, STRATEGY, references=refs)
        expected = value_reference_hashes(packet)
        assert reference_closure(path, STRATEGY) == expected
        db.close()
        with connect(path, references=refs) as reopened:
            assert reopened.execute('SELECT normalized_json,decision FROM market_observations').fetchall() == [
                (ORIGINAL, 'KEEP_PRIVATE'), (ORIGINAL, 'KEEP_PRIVATE')]
        with sqlite3.connect(path) as physical:
            assert physical.execute('SELECT normalized_json FROM market_observations WHERE id=1').fetchone() == (marker,)
            assert physical.execute(f'SELECT count(*) FROM {PACKET_TABLE}').fetchone() == (1,)
        # Only explicit source fragments go through the public CAS writer.
        assert store.stats()['payload_count'] == len(expected)


def test_private_marker_is_never_a_self_contained_public_payload(tmp_path):
    with PayloadStore(tmp_path / 'public.db') as store:
        refs = PayloadReferences(store, store)
        db, packet, marker = private_database(tmp_path / 'private.db', refs)
        try:
            assert is_external_value(marker)
            before = store.stats()
            with pytest.raises(ValueError, match='owning database'):
                refs.decode_many([marker])
            with pytest.raises(ValueError, match='public payload'):
                refs.encode_many([marker])
            with pytest.raises(ValueError, match='owning database'):
                value_reference_hashes(marker)
            assert store.stats() == before
        finally:
            db.close()


def test_unknown_private_column_cannot_hide_behind_valid_dictionary_pointer(tmp_path):
    path = tmp_path / 'private.db'
    with PayloadStore(tmp_path / 'public.db') as store:
        refs = PayloadReferences(store, store)
        db, _, marker = private_database(path, refs)
        try:
            db.execute('UPDATE market_observations SET decision=? WHERE id=1', (marker,))
            db.commit()
            with pytest.raises(ValueError, match='column|owner'):
                validate_private_reference_ownership(db, STRATEGY, references=refs)
        finally:
            db.close()


@pytest.mark.parametrize('damage', ['missing', 'foreign-owner', 'malformed'])
def test_bad_dictionary_dependencies_fail_read_and_closure(tmp_path, damage):
    path = tmp_path / 'private.db'
    with PayloadStore(tmp_path / 'public.db') as store:
        refs = PayloadReferences(store, store)
        db, _, marker = private_database(path, refs)
        db.close()
        with sqlite3.connect(path) as physical:
            if damage == 'missing':
                sql = physical.execute("SELECT sql FROM sqlite_master WHERE name='_private_packets_no_delete'").fetchone()[0]
                physical.execute('DROP TRIGGER _private_packets_no_delete')
                physical.execute(f'DELETE FROM {PACKET_TABLE}')
                physical.execute(sql)
            else:
                parts = marker.split(':')
                parts[1] = '0' * 32 if damage == 'foreign-owner' else 'not-a-token'
                physical.execute('UPDATE market_observations SET normalized_json=?', (':'.join(parts),))
        with pytest.raises(ValueError):
            reference_closure(path, STRATEGY)
        with connect(path, references=refs) as reader:
            with pytest.raises(ValueError):
                reader.execute('SELECT normalized_json FROM market_observations').fetchall()
