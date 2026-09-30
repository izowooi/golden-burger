"""Guava's copied public facts remain distinct from news/experiment decisions."""
import json

import pytest

from polybot_observability import market_data_mixed as mixed
from polybot_observability.market_data_refs import PayloadReferences, externalize_row
from test_market_data_mixed import MemoryStore, private_template
from test_market_data_mixed_versions import legacy_packet


NEWS = {
    "provider": "espn", "provider_game_id": "provider-game", "status": "IN_PROGRESS",
    "home": {"team_id": "home", "name": "HOME", "score": 1, "private_alias": "PRIVATE_HOME"},
    "away": None, "provider_updated_at": None, "raw_snapshot": {"status": "source"},
    "received_at": "PRIVATE_RECEIPT", "final": "PRIVATE_FINAL",
    "regulation_score": "PRIVATE_CALCULATED_SCORE",
    "matching_evidence": {"venue_event_id": "venue-event", "provider_scheduled_at": None,
                          "alias_intersection": "PRIVATE_MATCH"},
}


@pytest.mark.parametrize("table,column,profile,value", [
    ("events", "event_json", 25, {
        "raw": {"id": "raw-event"}, "event_id": "venue-event", "expected_token_ids": ["a", "b"],
        "side_definitions": [{"condition_id": "condition", "token_id": "a", "outcome_label": "Home",
                              "tradable_flags": {"closed": False}, "tradable": "PRIVATE_GATE"}],
        "official_news": NEWS, "guava_signal_state": "PRIVATE_STATE",
    }),
    ("book_attempts", "book_json", 26, {
        "raw": {"bids": []}, "token_id": "token", "condition_id": "condition",
        "partition_key": "condition", "fee_evidence": {"raw": {"feeRateBps": 50}, "source": "PRIVATE_FEE_SOURCE"},
        "depth": "PRIVATE_CALCULATED_DEPTH",
    }),
    ("cycles", "summary_json", 27, {
        "tracking": {"PRIVATE_TRACK_KEY": {"sport_family": "nfl", "expected_token_ids": ["a", "b"],
                                            "attempts": "PRIVATE_ATTEMPTS"}},
        "news_context": [NEWS, None], "news_errors": "PRIVATE_ERRORS",
    }),
    ("features", "metrics_json", 28, {
        "score": [1, 0], "previous_score": None,
        "reported_book_timestamps": {"PRIVATE_TOKEN_ASSOCIATION": "1787529600000"},
        "source_final_flag": "PRIVATE_DERIVED_FINAL", "spread": "PRIVATE_SPREAD",
    }),
    ("features", "feature_json", 29, {
        "metrics": {"score": [1, 0], "reported_book_timestamps": {"PRIVATE_TOKEN_ASSOCIATION": None},
                    "source_final_flag": "PRIVATE_DERIVED_FINAL"},
        "applicable": "PRIVATE_DECISION",
    }),
])
def test_copied_source_spans_and_private_templates_remain_exact(table, column, profile, value):
    original = "  " + json.dumps(value, ensure_ascii=False, indent=1) + "\n"
    store = MemoryStore(); refs = PayloadReferences(store, store)
    encoded = externalize_row("golden-guava", table, {column: original}, references=refs)[column]
    assert mixed._packet(encoded)["profile"] == profile
    assert mixed.resolve_mixed_payload(encoded, refs) == original
    assert mixed.verify_mixed_ownership("golden-guava", table, column, original, encoded, refs)
    fragments = mixed.inspect_mixed_payload(encoded, refs)["public_fragments"]
    assert fragments and all(b"PRIVATE" not in value for value in fragments)
    assert b"PRIVATE" in private_template(encoded, store)


@pytest.mark.parametrize("table,column,old,new,value", [
    ("events", "event_json", 8, 25,
     ' {"raw":{"id":"source"},"event_id":"new-source","official_news":null,"eligible":"PRIVATE"} '),
    ("book_attempts", "book_json", 9, 26,
     '{"raw":{"bids":[]},"token_id":"new-source","fee_evidence":{},"status":"PRIVATE"}'),
])
def test_old_guava_profile_meaning_is_frozen_and_upgrade_adds_only_new_source_spans(table, column, old, new, value):
    store = MemoryStore(); refs = PayloadReferences(store, store)
    old_packet = legacy_packet(old, column, value, refs)
    assert mixed._packet(old_packet)["profile"] == old
    assert len(mixed.inspect_mixed_payload(old_packet, refs)["public_fragments"]) == 1
    encoded = externalize_row("golden-guava", table, {column: old_packet}, references=refs)[column]
    assert mixed._packet(encoded)["profile"] == new
    assert len(mixed.inspect_mixed_payload(encoded, refs)["public_fragments"]) == 2
    assert mixed.resolve_mixed_payload(encoded, refs) == value


def test_explicit_missing_news_and_empty_tracking_are_not_invented():
    original = '{"official_news":null,"side_definitions":[],"guava_signal_state":"PRIVATE"}'
    store = MemoryStore()
    value = externalize_row("golden-guava", "events", {"event_json": original}, references=PayloadReferences(store, store))
    assert value["event_json"] == original and store.writes == []
