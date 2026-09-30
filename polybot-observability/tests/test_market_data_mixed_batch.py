"""A real publication ACK separates prepared mixed packets from local output."""
import copy
import json

import pytest

from polybot_observability.market_data_mixed import externalize_mixed_rows, verify_mixed_ownership
from polybot_observability.market_data_refs import PayloadReferences, externalize_row, externalize_rows
from polybot_observability import market_data_store
from test_market_data_mixed import MemoryStore


def rows(count):
    return [{'normalized_json': json.dumps({'volume_num': index, 'family': 'PRIVATE_FAMILY'}),
             'classification_evidence_json': json.dumps({'labels': [str(index), 'Away'], 'result_kind': 'PRIVATE_RESULT'}),
             'labels_json': '["Home","Away"]', 'decision': 'PRIVATE_ROW'} for index in range(count)]


def test_one_plain_and_one_mixed_durable_batch_matches_individual_exact_packets():
    originals = rows(16)
    baseline, batched = MemoryStore(), MemoryStore()
    expected = [externalize_row('golden-coconut', 'market_observations', row,
                references=PayloadReferences(baseline, baseline)) for row in originals]
    snapshot = copy.deepcopy(originals)
    refs = PayloadReferences(batched, batched)
    actual = externalize_rows('golden-coconut', 'market_observations', originals, references=refs)
    assert actual == expected
    assert originals == snapshot
    assert len(baseline.writes) == 32  # Each single-row wrapper already batches its two mixed columns.
    assert len(batched.writes) == 2
    for old, new in zip(originals, actual, strict=True):
        for column in ('normalized_json', 'classification_evidence_json'):
            assert verify_mixed_ownership('golden-coconut', 'market_observations', column,
                                          old[column], new[column], refs)
    assert all(b'PRIVATE' not in body for batch in batched.writes for body in batch)


def test_mixed_publication_respects_item_and_byte_bounds(monkeypatch):
    monkeypatch.setattr(market_data_store, 'MAX_BATCH_ITEMS', 3)
    monkeypatch.setattr(market_data_store, 'MAX_BATCH_BYTES', 180)
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    originals = rows(12)
    result = externalize_mixed_rows('golden-coconut', 'market_observations', originals, originals, refs)
    assert len(result) == 12 and len(store.writes) > 1
    assert all(len(batch) <= 3 and sum(map(len, batch)) <= 180 for batch in store.writes)


@pytest.mark.parametrize('failure', ['raise', 'wrong-ack'])
def test_failed_mixed_ack_returns_no_prepared_row_and_does_not_mutate_inputs(failure):
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    originals = rows(4)
    snapshot = copy.deepcopy(originals)
    def fail(values):
        if failure == 'raise':
            raise OSError('durable writer unavailable')
        return ['0' * 64 for _ in values]
    store.put_many = fail
    with pytest.raises((OSError, ValueError)):
        externalize_mixed_rows('golden-coconut', 'market_observations', originals, originals, refs)
    assert originals == snapshot
    assert store.payloads == {}


def test_mixed_batch_input_length_mismatch_has_no_writer_side_effect():
    store = MemoryStore()
    with pytest.raises(ValueError, match='different lengths'):
        externalize_mixed_rows('golden-coconut', 'market_observations', rows(1), [], PayloadReferences(store, store))
    assert store.writes == []
