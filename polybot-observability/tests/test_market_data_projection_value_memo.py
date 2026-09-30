"""Memoization belongs to immutable values, never mutable SQLite proofs."""
import hashlib

import pytest

import polybot_observability.market_data_projections as projection
from polybot_observability.market_data_store import PayloadStore
from test_market_data_projection_block_cache import quote, get


def test_normalized_immutable_value_size_and_digest_encode_only_once(monkeypatch):
    value = quote(1)
    cells = [projection._cell(cell) for cell in value.values]
    digest = hashlib.sha256(projection._CONTENT_DOMAIN + projection.projection_schema_sha(value.kind))
    for cell in cells:
        digest.update(len(cell).to_bytes(8, 'big'))
        digest.update(cell)
    encode = projection._cell
    calls = []
    monkeypatch.setattr(projection, '_cell', lambda cell: calls.append(cell) or encode(cell))
    for _ in range(100):
        assert value.raw_bytes == sum(map(len, cells))
        assert value.sha256 == digest.hexdigest()
        assert value.row() == value.values
    assert len(calls) == len(value.values)  # one digest; size checked at construction


def test_memoized_cold_values_still_recheck_current_database_index(tmp_path):
    with PayloadStore(tmp_path / 'public.db') as store:
        refs = store.put_public_projections([quote(index) for index in range(1024)])
        original = get(store, refs[:1])[0]
        assert original.projection.sha256 == refs[0].sha256
        assert get(store, refs[:1])[0] == original
        db = store._connection
        trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='projection_records_pack'").fetchone()[0]
        db.execute('DROP TRIGGER projection_records_pack')
        db.execute('UPDATE projection_records SET source_time=? WHERE id=?', ('changed-clock', refs[0].record_id))
        db.execute(trigger)
        with pytest.raises(projection.CorruptProjectionError):
            get(store, refs[:1])
