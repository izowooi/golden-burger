"""Source-only sidecar JSON spans never contain tracking or proof decisions."""
import json

import pytest

from polybot_observability.market_data_refs import PayloadReferences,externalize_row
from polybot_observability.market_data_mixed import inspect_mixed_payload,is_mixed_payload,verify_mixed_ownership
from test_market_data_mixed import MemoryStore,private_template


@pytest.mark.parametrize('table',['raw_events','raw_tracked_events'])
def test_slots_preserve_lexical_bytes_and_only_expose_source(table):
    original=' [ {"slot":"PRIVATE_HOME_ROLE","condition_id":"c","token_id":"t","outcome":"Y\\u0065s","all_tokens":["t","n"],"all_outcomes":["Yes","No"],"unknown":"PRIVATE_UNKNOWN"} ] '
    store=MemoryStore();refs=PayloadReferences(store,store)
    value=externalize_row('golden-watermelon',table,{'slots_json':original},references=refs)['slots_json']
    assert is_mixed_payload(value) and refs.decode_many([value])==[original]
    assert verify_mixed_ownership('golden-watermelon',table,'slots_json',original,value,refs)
    assert all(b'PRIVATE' not in fragment for fragment in inspect_mixed_payload(value,refs)['public_fragments'])
    assert b'PRIVATE_HOME_ROLE' in private_template(value,store)
    assert externalize_row('golden-watermelon',table,{'slots_json':'[]'},references=refs)=={'slots_json':'[]'}


def test_terminal_source_payout_is_separate_from_void_interpretation():
    original=' {"source":"PRIVATE_PROOF_BASIS","token_payouts":[{"condition_id":"c","token_id":"t","payout":0.500e0,"void":true,"future":"PRIVATE_UNKNOWN"}]} '
    store=MemoryStore();refs=PayloadReferences(store,store)
    value=externalize_row('golden-watermelon','raw_events',{'terminal_json':original},references=refs)['terminal_json']
    assert refs.decode_many([value])==[original]
    info=inspect_mixed_payload(value,refs)
    assert b'0.500e0' in info['public_fragments']
    assert all(b'PRIVATE' not in fragment and fragment!=b'true' for fragment in info['public_fragments'])
    assert b'"void":true' in private_template(value,store)
