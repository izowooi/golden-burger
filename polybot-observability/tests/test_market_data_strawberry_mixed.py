"""Imported source copies do not publish the private hypothetical trade or seed."""
import json

import pytest

from polybot_observability import market_data_mixed as mixed
from polybot_observability.market_data_refs import PayloadReferences, externalize_row, parse_reference
from test_market_data_mixed import MemoryStore, private_template
from test_market_data_mixed_ownership import forged_public_span


def test_imported_episode_exact_lexical_reconstruction_keeps_decision_and_seed_private():
    original = ' {"token_id":"source-token","event_cluster_id":null,"crossing_prior_probability":7.000e-1,"crossing_probability":0.73,"outcome_type":"BINARY","neg_risk":false,"source_fee_rate_bps":5e1,"tags_json":"[ {\\"slug\\":\\"sports\\"} ]","entry_cost_usdc":"PRIVATE_COST","entry_vwap":"PRIVATE_VWAP","source_anchor_id":"PRIVATE_ANCHOR","details":{"token_id":"PRIVATE_NESTED"}} '
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    encoded = externalize_row("golden-strawberry", "imported_episodes",
                              {"episode_json": original}, references=refs)["episode_json"]
    assert mixed._packet(encoded)["profile"] == 24
    assert mixed.resolve_mixed_payload(encoded, refs) == original
    assert mixed.verify_mixed_ownership("golden-strawberry", "imported_episodes",
                                        "episode_json", original, encoded, refs)
    fragments = mixed.inspect_mixed_payload(encoded, refs)["public_fragments"]
    assert len(fragments) == 8
    assert all(b"PRIVATE" not in raw for raw in fragments)
    template = private_template(encoded, store)
    for value in (b"PRIVATE_COST", b"PRIVATE_VWAP", b"PRIVATE_ANCHOR", b"PRIVATE_NESTED"):
        assert value in template
    forged = forged_public_span(original, encoded, store, 1, len(original.rstrip()))
    with pytest.raises(mixed.MixedPayloadError, match="public fragments"):
        externalize_row("golden-strawberry", "imported_episodes",
                        {"episode_json": forged}, references=refs)


def test_episode_tags_are_public_while_unknown_episode_json_fields_stay_private():
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    tags = '[ {"slug":"sports"} ]'
    values = {"tags_json": tags, "entry_fee_json": '{"PRIVATE_FEE":1}'}
    encoded = externalize_row("golden-strawberry", "hypothetical_episodes", values, references=refs)
    assert parse_reference(encoded["tags_json"])
    assert refs.decode_many([encoded["tags_json"]]) == [tags]
    assert encoded["entry_fee_json"] == values["entry_fee_json"]


@pytest.mark.parametrize("field", ["entry_cost_usdc", "entry_shares", "payout", "source_anchor_sha256", "sports_class", "threshold"])
def test_unreviewed_imported_values_are_not_inferred_as_public(field):
    original = json.dumps({field: "PRIVATE"})
    store = MemoryStore()
    result = externalize_row("golden-strawberry", "imported_episodes",
                             {"episode_json": original}, references=PayloadReferences(store, store))
    assert result["episode_json"] == original
    assert store.writes == []
