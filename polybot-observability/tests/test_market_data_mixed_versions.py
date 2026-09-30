"""Versioned lexical contracts centralize source fees without changing old capsules."""
import json
import sqlite3

import pytest

from polybot_observability import market_data_mixed as mixed
from polybot_observability.market_data_bundle import reference_closure
from polybot_observability.market_data_migrate import file_sha256, migrate_public_bodies, validate_private_reference_ownership
from polybot_observability.market_data_refs import PayloadReferences, externalize_row
from test_market_data_mixed import MemoryStore, private_template
from test_market_data_mixed_ownership import forged_public_span


CASES = [
    ("golden-guava", "events", "event_json", 1, 25,
     ' {"raw":{"id":"source"}, "clock" : {"elapsed":1e+1}, "rules_evidence":{"event":{"rules":"source"}}, "eligibility":"PRIVATE"} ',
     [{"id": "source"}, {"elapsed": 10}, {"event": {"rules": "source"}}]),
    ("golden-guava", "book_attempts", "book_json", 2, 26,
     '{"raw":{"asks":[]},"fee_evidence":{"raw":{"feeRateBps":12.50},"source":"PRIVATE_SOURCE","actual_fill_fee_proven":false},"depth_metrics":"PRIVATE"}',
     [{"asks": []}, {"feeRateBps": 12.5}]),
    ("golden-pomegranate", "market_metadata_versions", "metadata_json", 5, 10,
     '{"question":"source","fee_metadata":{"rebates":[null,1e-2,"\\u0031"]},"decision":"PRIVATE"}',
     ["source", {"rebates": [None, .01, "1"]}]),
    ("golden-strawberry", "clob_snapshots", "source_metadata_json", 6, 11,
     '{"hash":"source","fee_rate_bps":-0.00,"derived_fee":"PRIVATE"}',
     ["source", 0]),
]


def legacy_packet(old_profile, column, original, refs):
    row = {column: original}
    envelope, body = mixed._prepare(old_profile, original, row, row, refs)
    refs.encode_many([body])
    return envelope


@pytest.mark.parametrize("strategy,table,column,old_profile,current_profile,original,expected", CASES)
def test_old_profiles_keep_lexical_contract_and_upgrade_exactly(
    tmp_path, strategy, table, column, old_profile, current_profile, original, expected,
):
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    old = legacy_packet(old_profile, column, original, refs)
    old_public = mixed.inspect_mixed_payload(old, refs)["public_fragments"]
    assert len(old_public) == 1
    assert mixed.resolve_mixed_payload(old, refs) == original
    assert mixed.verify_mixed_ownership(strategy, table, column, old, old, refs)
    upgraded = externalize_row(strategy, table, {column: old}, references=refs)[column]
    assert mixed._packet(upgraded)["profile"] == current_profile
    assert mixed.resolve_mixed_payload(upgraded, refs) == original
    assert mixed.verify_mixed_ownership(strategy, table, column, old, upgraded, refs)
    assert [json.loads(v) for v in mixed.inspect_mixed_payload(upgraded, refs)["public_fragments"]] == expected
    assert b"PRIVATE" in private_template(upgraded, store)
    assert all(b"PRIVATE" not in v for v in mixed.inspect_mixed_payload(upgraded, refs)["public_fragments"])

    # Immutable old packet semantics must still hold after producing the new one.
    assert mixed.inspect_mixed_payload(old, refs)["public_fragments"] == old_public
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    with sqlite3.connect(source) as db:
        db.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY, {column} TEXT, decision TEXT)')
        db.executemany(f'INSERT INTO {table} VALUES(?,?,?)', [(1, original, "PRIVATE_ROW"), (2, old, "PRIVATE_ROW")])
        validate_private_reference_ownership(db, strategy, references=refs)
    manifest = migrate_public_bodies(source, target, strategy=strategy,
        source_sha256=file_sha256(source), references=refs)
    assert manifest["status"] == "VERIFIED"
    with sqlite3.connect(target) as db:
        values = [row[0] for row in db.execute(f'SELECT {column} FROM {table} ORDER BY id')]
        assert all(mixed._packet(v)["profile"] == current_profile for v in values)
        assert refs.decode_many(values) == [original, original]
        validate_private_reference_ownership(db, strategy, references=refs)


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("whole_wrapper", [False, True])
def test_malformed_existing_old_packets_never_reack_or_upgrade(case, whole_wrapper):
    strategy, table, column, old_profile, _, original, _ = case
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    old = legacy_packet(old_profile, column, original, refs)
    start, end = (len(original) - len(original.lstrip()), len(original.rstrip())) if whole_wrapper else (
        original.index('"PRIVATE"'), original.index('"PRIVATE"') + len('"PRIVATE"'))
    forged = forged_public_span(original, old, store, start, end)
    assert mixed.resolve_mixed_payload(forged, refs) == original
    writes = len(store.writes)
    with pytest.raises(mixed.MixedPayloadError, match="public fragments"):
        externalize_row(strategy, table, {column: forged}, references=refs)
    assert len(store.writes) == writes


@pytest.mark.parametrize("raw", ['{}', '{"feeSchedule":[1e-2,null,"\\u0032"]}', '[]', '12.50', 'null'])
def test_coconut_fee_fields_are_exact_json_values_and_provenance_stays_private(raw):
    source = '{ "fields":' + raw + ',"source":"PRIVATE_SOURCE","received_at":"PRIVATE_RECEIPT"}'
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    encoded = externalize_row("golden-coconut", "book_observations", {"fee_json": source}, references=refs)["fee_json"]
    assert mixed._packet(encoded)["profile"] == 12
    assert mixed.inspect_mixed_payload(encoded, refs)["public_fragments"] == [raw.encode()]
    assert mixed.resolve_mixed_payload(encoded, refs) == source
    assert mixed.verify_mixed_ownership("golden-coconut", "book_observations", "fee_json", source, encoded, refs)
    assert b"PRIVATE_SOURCE" in private_template(encoded, store)
    assert b"PRIVATE_RECEIPT" in private_template(encoded, store)


def test_guava_both_mixed_columns_have_complete_closure_and_migration(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    original = {
        "book_json": '{"raw":{"bids":[]},"fee_evidence":{"raw":{"feeRateBps":12.50},"actual_fill_fee_proven":false}}',
        "fee_evidence_json": '{ "raw":{"feeRateBps":12.50}, "source":"PRIVATE_SOURCE","actual_fill_fee_proven":false}',
        "depth_metrics_json": '[{"PRIVATE_FEE_CALCULATION":1.23}]',
    }
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    encoded = externalize_row("golden-guava", "book_attempts", original, references=refs)
    assert mixed._packet(encoded["book_json"])["profile"] == 26
    assert mixed._packet(encoded["fee_evidence_json"])["profile"] == 13
    assert encoded["depth_metrics_json"] == original["depth_metrics_json"]
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE book_attempts(book_json TEXT,fee_evidence_json TEXT,depth_metrics_json TEXT)")
        db.execute("INSERT INTO book_attempts VALUES(?,?,?)", tuple(encoded.values()))
        validate_private_reference_ownership(db, "golden-guava", references=refs)
    assert reference_closure(source, "golden-guava") == sorted(store.payloads)
    result = migrate_public_bodies(source, target, strategy="golden-guava", source_sha256=file_sha256(source), references=refs)
    assert result["status"] == "VERIFIED"
    with sqlite3.connect(target) as db:
        cells = db.execute("SELECT * FROM book_attempts").fetchone()
    assert refs.decode_many(cells) == list(original.values())
    assert not mixed.supported_profile_ids("golden-guava", "book_attempts", "depth_metrics_json")


def test_unchanged_old_packet_cannot_move_equal_public_bytes_to_private_field():
    original = '{"question":"same-value","private_decision":"same-value","fee_metadata":{"feeRateBps":12.5}}'
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    old = legacy_packet(5, "metadata_json", original, refs)
    start = original.rindex('"same-value"')
    forged = forged_public_span(original, old, store, start, start + len('"same-value"'))
    assert mixed.resolve_mixed_payload(forged, refs) == original
    assert mixed.inspect_mixed_payload(forged, refs)["public_fragments"] == mixed.inspect_mixed_payload(old, refs)["public_fragments"]
    count = len(store.writes)
    with pytest.raises(mixed.MixedPayloadError, match="private template"):
        externalize_row("golden-pomegranate", "market_metadata_versions", {"metadata_json": forged}, references=refs)
    assert len(store.writes) == count


def test_black_normalized_source_is_public_but_fallback_fee_and_unknown_keys_stay_private():
    source = '{ "event_id":"public-e","condition_id":"public-c","labels":["Yes","No"],"tokens":["a","b"],"probabilities":[9e-1,null],"end_date":null,"game_start_time":null,"liquidity":1e+4,"volume_total":5.00,"neg_risk":false,"fee_rate":0.073,"future_private":"PRIVATE" }'
    store=MemoryStore();refs=PayloadReferences(store,store)
    envelope=externalize_row('golden-black','market_observations',{'normalized_json':source},references=refs)['normalized_json']
    assert mixed._packet(envelope)['profile']==14
    assert mixed.resolve_mixed_payload(envelope,refs)==source
    assert mixed.verify_mixed_ownership('golden-black','market_observations','normalized_json',source,envelope,refs)
    fragments=mixed.inspect_mixed_payload(envelope,refs)['public_fragments']
    assert b'0.073' not in b''.join(fragments) and b'PRIVATE' not in b''.join(fragments)
    template=private_template(envelope,store)
    assert b'0.073' in template and b'PRIVATE' in template
