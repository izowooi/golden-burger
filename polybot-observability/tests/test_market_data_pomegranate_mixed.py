"""Copied source previews and prior resolution values retain local decisions."""
import json

import pytest

from polybot_observability import market_data_mixed as mixed
from polybot_observability.market_data_refs import PayloadReferences, externalize_row
from test_market_data_mixed import MemoryStore, private_template
from test_market_data_mixed_ownership import forged_public_span


@pytest.mark.parametrize('field', ['volume', 'outcomePrices', 'outcomePrices[0]', 'outcomePrices[12]', 'feesEnabled', 'tags'])
def test_reviewed_parse_preview_leaves_reason_and_source_type_private(field):
    original = json.dumps({field: {'raw_preview': 'raw source <not numeric>',
        'reason': 'PRIVATE_REASON', 'source_type': 'PRIVATE_TYPE'},
        'unknown_private_field': {'raw_preview': 'PRIVATE_UNKNOWN'}})
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    encoded = externalize_row('golden-pomegranate', 'market_observations',
                              {'parse_quality_json': original}, references=refs)['parse_quality_json']
    assert mixed._packet(encoded)['profile'] == 22
    assert mixed.resolve_mixed_payload(encoded, refs) == original
    assert mixed.verify_mixed_ownership('golden-pomegranate', 'market_observations',
                                        'parse_quality_json', original, encoded, refs)
    fragments = mixed.inspect_mixed_payload(encoded, refs)['public_fragments']
    assert [json.loads(fragment) for fragment in fragments] == ['raw source <not numeric>']
    assert all(b'PRIVATE' not in fragment for fragment in fragments)
    for private in (b'PRIVATE_REASON', b'PRIVATE_TYPE', b'PRIVATE_UNKNOWN'):
        assert private in private_template(encoded, store)


@pytest.mark.parametrize('field', ['outcomePrices[-1]', 'outcomePrices[01]', 'outcomePrices[1].private', 'decision'])
def test_unknown_parse_field_is_not_inferred_as_public(field):
    original = json.dumps({field: {'raw_preview': 'PRIVATE'}})
    store = MemoryStore()
    encoded = externalize_row('golden-pomegranate', 'market_observations',
                              {'parse_quality_json': original}, references=PayloadReferences(store, store))
    assert encoded['parse_quality_json'] == original
    assert store.writes == []


def test_rotation_prior_resolution_copy_is_not_a_new_one_hot_or_fill_claim():
    original = ' {"closed":1,"resolution_value_raw":"0.000","redeemable":true,"one_hot_outcome_label":"Home","lookup_status":"PRIVATE_LOOKUP","one_hot":"PRIVATE_CHECK","one_hot_outcome_index":"PRIVATE_ALIGNMENT","observed_at":"PRIVATE_RECEIPT"} '
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    encoded = externalize_row('golden-pomegranate', 'resolution_watchlist',
                              {'prior_state_json': original}, references=refs)['prior_state_json']
    assert mixed._packet(encoded)['profile'] == 23
    assert mixed.resolve_mixed_payload(encoded, refs) == original
    assert [json.loads(value) for value in mixed.inspect_mixed_payload(encoded, refs)['public_fragments']] == [1, '0.000', True, 'Home']
    assert b'PRIVATE_ALIGNMENT' in private_template(encoded, store)
    forged = forged_public_span(original, encoded, store, len(original)-len(original.lstrip()), len(original.rstrip()))
    with pytest.raises(mixed.MixedPayloadError, match='public fragments'):
        externalize_row('golden-pomegranate', 'resolution_watchlist', {'prior_state_json': forged}, references=refs)
