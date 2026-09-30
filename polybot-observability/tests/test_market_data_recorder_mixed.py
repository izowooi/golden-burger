"""Recorder source fragments preserve both carryover JSON encoding layers."""
import hashlib
import json

import pytest

from polybot_observability import market_data_mixed as mixed
from polybot_observability.market_data_refs import PayloadReferences, externalize_row
from test_market_data_mixed import MemoryStore, private_template
from test_market_data_mixed_ownership import forged_public_span


SLOTS = r''' [ {"slot":"PRIVATE_SLOT","condition_id":"condition","token_id":"token","outcome":"Y\u0065s","team_name":"서울 🏟","question":"quoted \"question\"","group_item_title":null,"all_tokens":["token","other"],"all_outcomes":["Yes","No"],"result_kind":"PRIVATE_RESULT","verified_role":"PRIVATE_ROLE","unknown":{"token_id":"PRIVATE_NESTED"}} ] '''
TERMINAL = ' {"source":"PRIVATE_INTERPRETATION","tokens":[{"condition_id":"condition","token_id":"token","outcome":"Yes","payout":1.000e0,"extra":"PRIVATE_EXTRA"}]} '


@pytest.mark.parametrize('table', ['tracked_events', 'event_observations'])
def test_slots_terminal_only_source_values_are_public(table):
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    original = {'slots_json': SLOTS, 'terminal_json': TERMINAL, 'anchor_json': '{"token_id":"PRIVATE_ANCHOR"}'}
    encoded = externalize_row('golden-coconut', table, original, references=refs)
    assert refs.decode_many(list(encoded.values())) == list(original.values())
    assert encoded['anchor_json'] == original['anchor_json']
    for field, profile in [('slots_json', 30), ('terminal_json', 31)]:
        value = encoded[field]
        assert mixed._packet(value)['profile'] == profile
        assert mixed.verify_mixed_ownership('golden-coconut', table, field, original[field], value, refs)
        public = mixed.inspect_mixed_payload(value, refs)['public_fragments']
        assert all(b'PRIVATE' not in fragment for fragment in public)
        assert b'PRIVATE' in private_template(value, store)
    assert b'1.000e0' in mixed.inspect_mixed_payload(encoded['terminal_json'], refs)['public_fragments']


@pytest.mark.parametrize('ensure_ascii', [False, True])
def test_carryover_preserves_nested_string_escapes_hash_and_private_values(ensure_ascii):
    original = json.dumps({'event_id': 'event', 'slots_json': SLOTS, 'terminal_json': TERMINAL,
                          'scheduled_start': '2026-09-30T00:00:00Z', 'family': 'PRIVATE_FAMILY',
                          'first_run_id': 'PRIVATE_RUN', 'state': 'PRIVATE_STATE',
                          'end_anchor': 'PRIVATE_END', 'next_due': 'PRIVATE_DUE',
                          'anchor_json': '{"token_id":"PRIVATE_ANCHOR"}',
                          'unknown': {'slots_json': SLOTS}}, ensure_ascii=ensure_ascii, indent=2)
    # Preserve deliberately noncanonical escapes in the outer JSON as well.
    original = original.replace('"event"', '"ev\\u0065nt"')
    digest = hashlib.sha256(original.encode()).hexdigest()
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    encoded = externalize_row('golden-coconut', 'registry_carryovers',
                              {'state_json': original, 'source_state_sha256': digest}, references=refs)
    value = encoded['state_json']
    assert mixed._packet(value)['profile'] == 32
    restored = refs.decode_many([value])[0]
    assert restored == original
    assert hashlib.sha256(restored.encode()).hexdigest() == encoded['source_state_sha256']
    assert mixed.verify_mixed_ownership('golden-coconut', 'registry_carryovers', 'state_json', original, value, refs)
    assert all(b'PRIVATE' not in fragment for fragment in mixed.inspect_mixed_payload(value, refs)['public_fragments'])
    assert b'PRIVATE_ANCHOR' in private_template(value, store)
    assert any(b'1.000e0' == fragment for fragment in mixed.inspect_mixed_payload(value, refs)['public_fragments'])


def test_missing_terminal_empty_slots_and_private_claim_carryover_remain_exact():
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    original = '{"event_id":"event","slots_json":"[]","terminal_json":null,"scheduled_start":null}'
    value = externalize_row('golden-coconut', 'registry_carryovers', {'state_json': original}, references=refs)['state_json']
    assert refs.decode_many([value]) == [original]
    assert externalize_row('golden-coconut', 'claim_carryovers', {'state_json': original}, references=refs) == {'state_json': original}


@pytest.mark.parametrize('inner', ['{', '[{"token_id":"x","token_id":"y"}]', 'null', '{}'])
def test_malformed_nested_slots_fail_before_public_writes(inner):
    store = MemoryStore()
    with pytest.raises(mixed.MixedPayloadError):
        externalize_row('golden-coconut', 'registry_carryovers',
                        {'state_json': json.dumps({'slots_json': inner})}, references=PayloadReferences(store, store))
    assert not store.writes


def test_nested_wrapper_cannot_be_laundered_into_public_fragments():
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    original = json.dumps({'event_id': 'event', 'slots_json': SLOTS, 'private': 'PRIVATE'})
    value = externalize_row('golden-coconut', 'registry_carryovers', {'state_json': original}, references=refs)['state_json']
    forged = forged_public_span(original, value, store, 0, len(original))
    with pytest.raises(mixed.MixedPayloadError, match='public fragments'):
        externalize_row('golden-coconut', 'registry_carryovers', {'state_json': forged}, references=refs)


def test_historical_coconut_profile_ids_are_unchanged():
    assert mixed.current_profile_id('golden-coconut', 'event_observations', 'normalized_json') == 18
    assert mixed.current_profile_id('golden-coconut', 'event_observations', 'classification_evidence_json') == 20
    assert mixed.current_profile_id('golden-coconut', 'book_observations', 'fee_json') == 12
