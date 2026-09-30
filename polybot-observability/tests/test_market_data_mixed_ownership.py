"""Prove ownership independently of an envelope's decoded equality claim."""

import hashlib
import struct
import zlib

import pytest
from polybot_observability import market_data_mixed as mixed
from polybot_observability.market_data_refs import PayloadReferences
from test_market_data_mixed import (
    MemoryStore,
    convert_text,
    membership_blob,
    packet,
    packet_parts,
    sample_envelope,
)
from test_market_data_mixed import text_case as text_case


def forged_public_span(source, envelope, store, start, end):
    """Create a valid checksummed envelope for an arbitrary, potentially private span."""
    fragment = source[start:end].encode()
    parts = mixed._parts([fragment])
    template = (
        mixed._chunk(b"") + mixed._chunk(b"") + struct.pack("!I", 1)
        + mixed._chunk(source[:start].encode()) + struct.pack("!I", 0)
        + mixed._chunk(source[end:].encode())
    )
    fields, _ = packet_parts(envelope)
    fields[2:4] = [0, 1]
    fields[6:9] = [len(template), len(parts), 1]
    fields[10] = hashlib.sha256(parts).digest()
    store.payloads[fields[10].hex()] = parts
    return packet(fields, zlib.compress(template, 9))


def test_real_profile_proves_public_and_private_lexical_bytes_without_writes(text_case):
    strategy, table, column, _ = text_case
    store = MemoryStore()
    original, result = convert_text(text_case, store)
    prior_writes = list(store.writes)
    refs = PayloadReferences(reader=store, writer=store)
    assert mixed.verify_mixed_ownership(
        strategy, table, column, original[column], result[column], refs,
    )
    assert mixed.verify_mixed_ownership(
        strategy, table, column, result[column], result[column], refs,
    )
    assert store.writes == prior_writes


@pytest.mark.parametrize("strategy,table", [
    ("golden-raspberry", "market_sweeps"),
    ("golden-strawberry", "market_membership_blobs"),
])
def test_gzip_private_header_footer_and_spans_are_proved(strategy, table):
    original, _ = membership_blob(strategy)
    store = MemoryStore()
    refs = PayloadReferences(reader=store, writer=store)
    row = {"membership_blob": original}
    result = mixed.externalize_mixed_row(strategy, table, row, row, refs)
    prior_writes = list(store.writes)
    assert mixed.verify_mixed_ownership(
        strategy, table, "membership_blob", original, result["membership_blob"], refs,
    )
    assert store.writes == prior_writes


@pytest.mark.parametrize("whole_wrapper", [False, True])
def test_equal_reconstruction_cannot_promote_private_spans(whole_wrapper):
    source, envelope, store = sample_envelope()
    start, end = (0, len(source)) if whole_wrapper else (
        source.index('"PRIVATE_FEE"'), source.index('"PRIVATE_FEE"') + len('"PRIVATE_FEE"'),
    )
    changed = forged_public_span(source, envelope, store, start, end)
    refs = PayloadReferences(reader=store)
    assert mixed.resolve_mixed_payload(changed, refs) == source
    with pytest.raises(mixed.MixedPayloadError, match="public fragments"):
        mixed.verify_mixed_ownership("golden-guava", "events", "event_json", source, changed, refs)
    with pytest.raises(mixed.MixedPayloadError, match="public fragments"):
        mixed.verify_mixed_ownership("golden-guava", "events", "event_json", changed, changed, refs)


def test_equal_public_and_private_values_cannot_move_the_public_splice():
    source = '{"market_id":"same-value","private_decision":"same-value"}'
    store = MemoryStore()
    row = {"metadata_json": source}
    refs = PayloadReferences(reader=store, writer=store)
    envelope = mixed.externalize_mixed_row(
        "golden-pomegranate", "market_metadata_versions", row, row, refs,
    )["metadata_json"]
    start = source.rindex('"same-value"')
    changed = forged_public_span(source, envelope, store, start, start + len('"same-value"'))
    assert mixed.resolve_mixed_payload(changed, refs) == source
    assert mixed.inspect_mixed_payload(changed, refs)["public_fragments"] == (
        mixed.inspect_mixed_payload(envelope, refs)["public_fragments"]
    )
    with pytest.raises(mixed.MixedPayloadError, match="private template"):
        mixed.verify_mixed_ownership(
            "golden-pomegranate", "market_metadata_versions", "metadata_json",
            source, changed, refs,
        )


def test_profile_change_cannot_borrow_another_public_allowlist():
    source, envelope, store = sample_envelope()
    fields, body = packet_parts(envelope)
    fields[0] = 2
    changed = packet(fields, body)
    refs = PayloadReferences(reader=store)
    assert mixed.resolve_mixed_payload(changed, refs) == source
    with pytest.raises(mixed.MixedPayloadError, match="profile"):
        mixed.verify_mixed_ownership("golden-guava", "events", "event_json", source, changed, refs)


def test_generic_public_marker_and_unlisted_columns_are_not_mixed_ownership_exceptions():
    source, envelope, store = sample_envelope()
    refs = PayloadReferences(reader=store, writer=store)
    reference = refs.encode_many([source])[0]
    assert refs.decode_many([reference])[0] == source
    assert not mixed.verify_mixed_ownership(
        "golden-guava", "events", "event_json", source, reference, refs,
    )
    assert not mixed.verify_mixed_ownership(
        "golden-guava", "events", "private_json", source, envelope, refs,
    )
