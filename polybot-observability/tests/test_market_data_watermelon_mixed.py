"""Reviewed source spans, not classifier decisions, leave Watermelon wrappers."""
import json

import pytest

from polybot_observability import market_data_mixed as mixed
from polybot_observability.market_data_refs import PayloadReferences, externalize_row
from test_market_data_mixed import MemoryStore, private_template
from test_market_data_mixed_ownership import forged_public_span


CASES = [
    ('market_observations', 'normalized_json', 15,
     ' {"condition_id":"source", "event_id":"event", "probabilities":[1e-2,0.9], "fee_rate":"PRIVATE_FEE", "sports_clock":{"matched_by":"PRIVATE_CLOCK"}} ',
     ['source', 'event', [.01, .9]]),
    ('event_observations', 'classification_evidence_json', 16,
     '{"sport_code":"soccer","team_count":2,"sport_tag_ids":["1"],"required_common_tag_ids":"PRIVATE_REQUIRED","identity_kind":"PRIVATE_KIND","classifier_version":"PRIVATE_VERSION"}',
     ['soccer', 2, ['1']]),
    ('market_observations', 'classification_evidence_json', 17,
     '{"team_names":["Home","Away"],"team_aliases":[null,"A"],"neg_risk":true,"description_sha256":"source-sha","selected_team_index":"PRIVATE_SELECTED","settlement_scope":"PRIVATE_SCOPE"}',
     [['Home', 'Away'], [None, 'A'], True, 'source-sha']),
]


@pytest.mark.parametrize('table,column,profile,original,expected', CASES)
def test_source_spans_and_local_decisions_preserve_exact_lexical_bytes(table, column, profile, original, expected):
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    encoded = externalize_row('golden-watermelon', table, {column: original}, references=refs)[column]
    assert mixed._packet(encoded)['profile'] == profile
    assert mixed.resolve_mixed_payload(encoded, refs) == original
    assert mixed.verify_mixed_ownership('golden-watermelon', table, column, original, encoded, refs)
    fragments = mixed.inspect_mixed_payload(encoded, refs)['public_fragments']
    assert [json.loads(value) for value in fragments] == expected
    assert all(b'PRIVATE' not in value for value in fragments)
    assert b'PRIVATE' in private_template(encoded, store)


@pytest.mark.parametrize('table,column,profile,original,expected', CASES)
def test_entire_classification_wrapper_cannot_be_promoted(table, column, profile, original, expected):
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    encoded = externalize_row('golden-watermelon', table, {column: original}, references=refs)[column]
    forged = forged_public_span(original, encoded, store, len(original)-len(original.lstrip()), len(original.rstrip()))
    assert mixed.resolve_mixed_payload(forged, refs) == original
    with pytest.raises(mixed.MixedPayloadError, match='public fragments'):
        externalize_row('golden-watermelon', table, {column: forged}, references=refs)


def test_both_market_wrappers_are_externalized_independently():
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    original = {column: text for table, column, _, text, _ in CASES if table == 'market_observations'}
    encoded = externalize_row('golden-watermelon', 'market_observations', original, references=refs)
    assert {name: refs.decode_many([value])[0] for name, value in encoded.items()} == original
    assert {mixed._packet(value)['profile'] for value in encoded.values()} == {15, 17}
