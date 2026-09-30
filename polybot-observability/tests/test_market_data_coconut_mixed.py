"""Historical Coconut source metrics remain separate from classifier decisions."""
import json

import pytest

from polybot_observability import market_data_mixed as mixed
from polybot_observability.market_data_refs import PayloadReferences, externalize_row
from test_market_data_mixed import MemoryStore, private_template
from test_market_data_mixed_ownership import forged_public_span


CASES = [
    ('event_observations', 'normalized_json', 18,
     ' {"volume_num":1e+3,"volume_24hr":null,"liquidity":12.50,"liquidity_num":0,"family":"PRIVATE_FAMILY","scheduled_start_utc":"PRIVATE_CHOSEN_TIME"} ',
     [1000, None, 12.5, 0]),
    ('market_observations', 'normalized_json', 19,
     '{"volume_num":5,"structure":"PRIVATE_STRUCTURE","event_metrics":{"volume_num":1e+3,"liquidity":null,"chosen_decision":"PRIVATE_NESTED"},"cluster":"PRIVATE_CLUSTER"}',
     [5, 1000, None]),
    ('event_observations', 'classification_evidence_json', 20,
     '{"event_id":null,"canonical_game_slug":"mlb-source","sport_root_id":"1","scheduled_start_raw":"raw-time","raw_lifecycle_json":"{\\"live\\":true}","series_items":[{"id":"2","slug":"source","recurrence":"daily","chosen":"PRIVATE_ITEM"}],"lifecycle_state":"PRIVATE_CLASSIFIED","scheduled_start_field":"PRIVATE_CHOICE","season_phase":"PRIVATE_PHASE"}',
     [None, 'mlb-source', '1', 'raw-time', '{"live":true}', '2', 'source', 'daily']),
    ('market_observations', 'classification_evidence_json', 21,
     '{"sports_market_type":"moneyline","neg_risk":false,"labels":["Home","Away"],"token_ids":["a","b"],"result_kind":"PRIVATE_RESULT","event_cluster_id":"PRIVATE_CLUSTER","competition_code":"PRIVATE_CODE"}',
     ['moneyline', False, ['Home', 'Away'], ['a', 'b']]),
]


@pytest.mark.parametrize('table,column,profile,original,expected', CASES)
def test_source_spans_keep_exact_lexical_form_and_private_classification(table, column, profile, original, expected):
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    encoded = externalize_row('golden-coconut', table, {column: original}, references=refs)[column]
    assert mixed._packet(encoded)['profile'] == profile
    assert mixed.resolve_mixed_payload(encoded, refs) == original
    assert mixed.verify_mixed_ownership('golden-coconut', table, column, original, encoded, refs)
    fragments = mixed.inspect_mixed_payload(encoded, refs)['public_fragments']
    assert [json.loads(value) for value in fragments] == expected
    assert all(b'PRIVATE' not in value for value in fragments)
    assert b'PRIVATE' in private_template(encoded, store)


@pytest.mark.parametrize('table,column,profile,original,expected', CASES)
def test_complete_wrapper_cannot_be_relabelled_public(table, column, profile, original, expected):
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    encoded = externalize_row('golden-coconut', table, {column: original}, references=refs)[column]
    forged = forged_public_span(original, encoded, store, len(original)-len(original.lstrip()), len(original.rstrip()))
    assert mixed.resolve_mixed_payload(forged, refs) == original
    before = len(store.writes)
    with pytest.raises(mixed.MixedPayloadError, match='public fragments'):
        externalize_row('golden-coconut', table, {column: forged}, references=refs)
    assert len(store.writes) == before


@pytest.mark.parametrize('table', ['event_observations', 'market_observations'])
def test_both_historical_wrappers_are_processed_independently(table):
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    original = {column: text for name, column, _, text, _ in CASES if name == table}
    encoded = externalize_row('golden-coconut', table, original, references=refs)
    assert {key: refs.decode_many([value])[0] for key, value in encoded.items()} == original
    assert all(mixed.is_mixed_payload(value) for value in encoded.values())


def test_current_recorder_columns_do_not_activate_historical_profiles():
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    original = {'event_json': '{"id":"provider"}', 'clock_json': '{"score":"1-0"}',
                'slots_json': '[{"PRIVATE_SLOT":"A"}]'}
    encoded = externalize_row('golden-coconut', 'event_observations', original, references=refs)
    assert all(not mixed.is_mixed_payload(value) for value in encoded.values())
    assert {key: refs.decode_many([value])[0] for key, value in encoded.items()} == original
