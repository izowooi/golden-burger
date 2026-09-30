"""Synthetic mixed public/private payloads preserve original lexical bytes."""

import base64
import gzip
import hashlib
import io
import json
import lzma
import random
import struct
import zlib

import pytest

from polybot_observability import market_data_mixed
from polybot_observability.market_data_mixed import (
    MixedPayloadError,
    externalize_mixed_row,
    inspect_mixed_payload,
    is_mixed_payload,
    mixed_reference_hashes,
    resolve_mixed_payload,
    verify_mixed_ownership,
)
from polybot_observability.market_data_refs import MissingMarketDataConfiguration, PayloadReferences
from polybot_observability.market_data_store import MissingPayloadError

PREFIX = "\x1ePMMIX1:"
HEADER = struct.Struct("!BBBBIIIII32s32s")


def packet_parts(value):
    packet = base64.b64decode(value[len(PREFIX):]) if isinstance(value, str) else value[len(PREFIX):]
    return list(HEADER.unpack_from(packet)), packet[HEADER.size:-32]


def packet(fields, private_body, *, text=True):
    body = HEADER.pack(*fields) + private_body
    body += hashlib.sha256(PREFIX.encode() + body).digest()
    return PREFIX + base64.b64encode(body).decode() if text else PREFIX.encode() + body


def public_raw(value, store):
    fields, _ = packet_parts(value)
    body = store.payloads[fields[10].hex()]
    return (body if fields[2] == 0 else zlib.decompress(body) if fields[2] == 1
            else lzma.decompress(body) if fields[2] == 2 else gzip.decompress(body))


def private_template(value, store):
    fields, body = packet_parts(value)
    if fields[3] == 1:
        return zlib.decompress(body)
    if fields[3] == 2:
        return lzma.decompress(body)
    decoder = zlib.decompressobj(zdict=public_raw(value, store)[-32768:])
    return decoder.decompress(body) + decoder.flush()


def rewrite_private(value, store, raw, *, codec=None):
    fields, _ = packet_parts(value)
    fields[3] = fields[3] if codec is None else codec
    fields[6] = len(raw)
    if fields[3] == 1:
        body = zlib.compress(raw, 9)
    elif fields[3] == 2:
        body = lzma.compress(raw, preset=6)
    else:
        encoder = zlib.compressobj(9, zdict=public_raw(value, store)[-32768:])
        body = encoder.compress(raw) + encoder.flush()
    return packet(fields, body, text=isinstance(value, str))


def rewrite_public(value, store, body, codec, *, raw_size=None):
    fields, private = packet_parts(value)
    fields[2] = codec
    if raw_size is not None:
        fields[7] = raw_size
    fields[10] = hashlib.sha256(body).digest()
    store.payloads[fields[10].hex()] = body
    return packet(fields, private, text=isinstance(value, str))


class MemoryStore:
    def __init__(self):
        self.payloads = {}
        self.writes = []
        self.reads = []

    def put_many(self, payloads):
        self.writes.append(list(payloads))
        hashes = [hashlib.sha256(raw).hexdigest() for raw in payloads]
        self.payloads.update(zip(hashes, payloads))
        return hashes

    def get_many(self, hashes):
        self.reads.append(list(hashes))
        missing = [digest for digest in hashes if digest not in self.payloads]
        if missing:
            raise MissingPayloadError(missing)
        return [self.payloads[digest] for digest in hashes]


def public_body():
    return {
        "id": "public-source", "question": "서울 public market " * 800,
        "bids": [{"price": "0.41", "size": "120"}], "asks": [],
        "_guava": {"request_id": "approved-public-request", "observed_at": "2026-09-29T12:00:00Z",
                   "sports_request_id": "public-sports-request", "sport_enrichment": "soccer", "original_sport": None},
    }


def guava_json(raw):
    return '{ "raw" : ' + raw + ',\n "fee" : { "secret":"PRIVATE_FEE", "rate":3e+02 },' \
        ' "depth" : {"derived":"PRIVATE_DEPTH"}, "eligibility":"PRIVATE_ELIGIBILITY",' \
        ' "status":"PRIVATE_STATUS", "unknown":{"unicode":"PRIVATE_\\u0042", "number":-0.0} }'


@pytest.fixture(params=[
    ("golden-guava", "events", "event_json"),
    ("golden-guava", "book_attempts", "book_json"),
    ("golden-pomegranate", "market_metadata_versions", "metadata_json"),
    ("golden-strawberry", "clob_snapshots", "source_metadata_json"),
    ("golden-apricot", "raw_event_observations", "evidence_json"),
    ("golden-peach", "raw_event_observations", "evidence_json"),
    ("golden-plum", "raw_event_observations", "evidence_json"),
])
def text_case(request):
    strategy, table, column = request.param
    source = json.dumps(public_body(), ensure_ascii=True, separators=(",", ":"))
    if strategy == "golden-guava":
        text = guava_json(source)
    elif strategy == "golden-pomegranate":
        text = '{ "fee_metadata":{"feeRateBps":"12.50","feesEnabled":true}, "question":' + json.dumps("public question " * 1500) + \
            ', "condition_id":"public-condition", "outcomes":["Yes", "No"], "tick_size":1e-2,' \
            ' "unknown":{"local":"PRIVATE_UNKNOWN", "lexical":-0.0} }'
        public_fields = {
            "market_id": "public-market-id", "event_id": "public-event-id", "event_slug": "public-event-slug",
            "market_slug": "public-market-slug", "clob_token_ids": ["public-token-yes", "public-token-no"],
            "tags": ["public-tag"], "category": "public-category", "sports": {"name": "public-sport"},
            "start_date": "2026-09-29T12:00:00Z", "end_date": "2026-09-29T14:00:00Z",
            "game_start_time": "2026-09-29T12:15:00Z", "min_order_size": 5,
        }
        text = text[:-1] + "," + ",".join(json.dumps(key) + ":" + json.dumps(value) for key, value in public_fields.items()) + "}"
    elif strategy == "golden-strawberry":
        text = '{"fee_rate_bps":"12.50", "timestamp":123e+1, "market":' + json.dumps("public-market-" * 1500) + \
            ', "tick_size":1e-2, "min_order_size":5.00, "hash":"public-book-hash",' \
            ' "unknown":"PRIVATE_UNKNOWN\\u0042" }'
    else:
        text = '{ "event":' + source + ', "market_context":[' \
            '{"conditionId":"public-condition","liquidityNum":1e+3,"active":true,' \
            '"liquidity":2e+3,"volumeNum":30.00,"volume":4e1,"volume24hr":50,' \
            '"closed":false,"enableOrderBook":true,"acceptingOrders":true,"updatedAt":"public-updated",' \
            '"fee":"PRIVATE_CONTEXT_FEE","unknown":"PRIVATE_CONTEXT_UNKNOWN"}],' \
            '"slots":"PRIVATE_SLOTS","terminal_proofs":"PRIVATE_TERMINAL",' \
            '"decision":{"secret":"PRIVATE_DECISION"}, "unknown":-0.0 }'
    return strategy, table, column, text


def convert_text(case, store):
    strategy, table, column, original = case
    row = {column: original, "id": "local-row", "private_metric": "PRIVATE_ROW_METRIC"}
    result = externalize_mixed_row(strategy, table, row, dict(row), PayloadReferences(reader=store, writer=store))
    return row, result


def test_mixed_text_preserves_original_lexical_bytes_and_unrelated_row_fields(text_case):
    store = MemoryStore()
    original, result = convert_text(text_case, store)
    column = text_case[2]
    assert isinstance(result[column], str)
    assert is_mixed_payload(result[column])
    assert result[column].startswith("\x1ePMMIX1:")
    assert resolve_mixed_payload(result[column], PayloadReferences(reader=store)) == original[column]
    assert original[column] == text_case[3]
    assert result["id"] == original["id"]
    assert result["private_metric"] == original["private_metric"]
    assert len(store.payloads) == 1
    assert [len(batch) for batch in store.writes] == [1]
    read_count = len(store.reads)
    assert mixed_reference_hashes(result[column]) == list(store.payloads)
    assert len(store.reads) == read_count


def test_every_approved_text_fragment_is_public_and_private_siblings_are_absent(text_case):
    store = MemoryStore()
    _, result = convert_text(text_case, store)
    strategy, _, column, original = text_case
    parsed = json.loads(original)
    if strategy == "golden-guava":
        expected = [parsed["raw"]]
    elif strategy == "golden-pomegranate":
        expected = [parsed[key] for key in (
            "condition_id", "market_id", "event_id", "event_slug", "market_slug", "question",
            "outcomes", "clob_token_ids", "tags", "category", "sports", "start_date", "end_date",
            "game_start_time", "tick_size", "min_order_size", "fee_metadata",
        )]
    elif strategy == "golden-strawberry":
        expected = [parsed[key] for key in ("timestamp", "tick_size", "min_order_size", "market", "hash", "fee_rate_bps")]
    else:
        expected = [parsed["event"]] + [market[key] for market in parsed["market_context"] for key in (
            "conditionId", "liquidityNum", "liquidity", "volumeNum", "volume", "volume24hr", "active", "closed",
            "enableOrderBook", "acceptingOrders", "updatedAt",
        )]
    inspected = inspect_mixed_payload(result[column], PayloadReferences(reader=store))
    fragments = inspected["public_fragments"]
    assert all(b"PRIVATE_" not in fragment for fragment in fragments)
    normalize = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False)
    assert {normalize(json.loads(fragment)) for fragment in fragments} == {normalize(value) for value in expected}
    assert "private_body" not in inspected
    assert inspected["original_sha256"] == hashlib.sha256(original.encode()).hexdigest()
    if strategy == "golden-guava":
        assert json.loads(fragments[0])["_guava"]["request_id"] == "approved-public-request"


def test_mixed_missing_or_corrupt_dependency_cannot_return_inline_fallback(text_case):
    store = MemoryStore()
    _, result = convert_text(text_case, store)
    value = result[text_case[2]]
    assert is_mixed_payload(value)
    with pytest.raises(MissingMarketDataConfiguration):
        resolve_mixed_payload(value, PayloadReferences())
    digest = mixed_reference_hashes(value)[0]
    saved = store.payloads.pop(digest)
    with pytest.raises(MissingPayloadError):
        resolve_mixed_payload(value, PayloadReferences(reader=store))
    store.payloads[digest] = saved + b"corruption"
    with pytest.raises(ValueError, match="digest"):
        resolve_mixed_payload(value, PayloadReferences(reader=store))


def test_identical_public_fragments_deduplicate_across_private_decisions(text_case):
    store = MemoryStore()
    original, first = convert_text(text_case, store)
    second_case = (*text_case[:3], text_case[3].replace("PRIVATE_", "PRIVATE_SECOND_"))
    second_original, second = convert_text(second_case, store)
    column = text_case[2]
    assert is_mixed_payload(first[column]) and is_mixed_payload(second[column])
    assert len(store.payloads) == 1
    assert mixed_reference_hashes(first[column]) == mixed_reference_hashes(second[column])
    assert resolve_mixed_payload(first[column], PayloadReferences(reader=store)) == original[column]
    assert resolve_mixed_payload(second[column], PayloadReferences(reader=store)) == second_original[column]


def test_already_externalized_mixed_value_retains_packet_identity(text_case):
    store = MemoryStore()
    _, encoded = convert_text(text_case, store)
    strategy, table, column, _ = text_case
    result = externalize_mixed_row(strategy, table, encoded, dict(encoded), PayloadReferences(reader=store, writer=store))
    assert result == encoded
    assert len(store.payloads) == 1
    assert resolve_mixed_payload(result[column], PayloadReferences(reader=store)) == text_case[3]


@pytest.mark.parametrize("table,column", [("events", "event_json"), ("book_attempts", "book_json")])
def test_guava_reuses_existing_raw_gzip_reference_without_new_public_put(table, column):
    raw = json.dumps(public_body(), ensure_ascii=False, separators=(",", ":")).encode()
    row = {column: guava_json(raw.decode()), "raw_gzip": gzip.compress(raw, mtime=123456),
           "raw_sha256": hashlib.sha256(raw).hexdigest(), "private": "PRIVATE_ROW"}
    store = MemoryStore()
    encoded = dict(row)
    encoded["raw_gzip"] = PayloadReferences(reader=store, writer=store).encode_many([row["raw_gzip"]])[0]
    baseline_writes = len(store.writes)
    result = externalize_mixed_row("golden-guava", table, row, encoded, PayloadReferences(reader=store, writer=store))
    assert is_mixed_payload(result[column])
    assert len(store.writes) == baseline_writes
    assert mixed_reference_hashes(result[column]) == list(store.payloads)
    assert result["raw_gzip"] == encoded["raw_gzip"]
    assert resolve_mixed_payload(result[column], PayloadReferences(reader=store)) == row[column]


@pytest.mark.parametrize("table,column", [("events", "event_json"), ("book_attempts", "book_json")])
def test_guava_null_raw_has_no_shared_dependency(table, column):
    original = {column: guava_json("null"), "raw_gzip": None}
    store = MemoryStore()
    assert externalize_mixed_row("golden-guava", table, original, dict(original), PayloadReferences(reader=store, writer=store)) == original
    assert store.writes == []


def test_guava_rejects_raw_gzip_with_different_json_lexical_bytes_without_new_put():
    source = public_body()
    fragment = json.dumps(source, ensure_ascii=False, separators=(",", ":"))
    raw = json.dumps(source, ensure_ascii=False, indent=2).encode()
    row = {"event_json": guava_json(fragment), "raw_gzip": gzip.compress(raw, mtime=123456)}
    store = MemoryStore()
    encoded = dict(row)
    encoded["raw_gzip"] = PayloadReferences(reader=store, writer=store).encode_many([row["raw_gzip"]])[0]
    with pytest.raises(MixedPayloadError, match="same exact"):
        externalize_mixed_row("golden-guava", "events", row, encoded, PayloadReferences(reader=store, writer=store))
    assert len(store.writes) == 1
    assert encoded["event_json"] == row["event_json"]


@pytest.mark.parametrize("strategy,table,column", [
    ("unknown", "events", "event_json"),
    ("golden-guava", "unknown", "event_json"),
    ("golden-guava", "events", "unknown_json"),
])
def test_unknown_strategy_table_or_column_remains_unchanged(strategy, table, column):
    original = {column: guava_json(json.dumps(public_body())), "private": "PRIVATE_LOCAL"}
    encoded = {**original, "already_encoded_unrelated": "kept"}
    store = MemoryStore()
    assert externalize_mixed_row(strategy, table, original, encoded, PayloadReferences(reader=store, writer=store)) == encoded
    assert store.writes == []


@pytest.mark.parametrize("strategy,table,column,value", [
    ("golden-guava", "events", "event_json", '{"raw":{"id":"a"},"private":1}'),
    ("golden-pomegranate", "market_metadata_versions", "metadata_json", '{"condition_id":"a","fee_metadata":{}}'),
    ("golden-strawberry", "clob_snapshots", "source_metadata_json", '{"timestamp":1,"fee_rate_bps":0}'),
    ("golden-apricot", "raw_event_observations", "evidence_json", '{"event":{"id":"a"}}'),
])
def test_small_recognized_public_fragments_are_centralized_with_exact_roundtrip(strategy, table, column, value):
    original = {column: value}
    store = MemoryStore()
    result = externalize_mixed_row(strategy, table, original, dict(original), PayloadReferences(reader=store, writer=store))
    assert is_mixed_payload(result[column])
    assert len(store.writes) == 1
    assert resolve_mixed_payload(result[column], PayloadReferences(reader=store)) == value


@pytest.mark.parametrize("strategy,table,column,value", [
    ("golden-guava", "events", "event_json", '{"raw":null,"private":1}'),
    ("golden-pomegranate", "market_metadata_versions", "metadata_json", '{"fee_calculation":{"private":1}}'),
    ("golden-strawberry", "clob_snapshots", "source_metadata_json", '{"calculated_fee":0}'),
    ("golden-apricot", "raw_event_observations", "evidence_json", '{"event":null,"slots":[]}'),
])
def test_private_only_recognized_rows_remain_unchanged_without_public_put(strategy, table, column, value):
    original = {column: value}
    store = MemoryStore()
    assert externalize_mixed_row(strategy, table, original, dict(original), PayloadReferences(reader=store, writer=store)) == original
    assert store.writes == []


def membership_blob(strategy, *, count=500, level=6):
    random_source = random.Random(76)
    members = []
    for index in range(count):
        condition = random_source.randbytes(32).hex()
        public = {"condition_id": condition, "market_id": str(index),
                  "raw_market_sha256": random_source.randbytes(32).hex()}
        if strategy == "golden-raspberry":
            public.update(source_market_key=f"market:{condition}", event_id=f"public-event-{index}")
            private = {"selection": "PRIVATE_SELECTION", "provenance": "PRIVATE_PROVENANCE"}
        else:
            public.update(token_ids=[f"public-token-{index}-yes", f"public-token-{index}-no"])
            private = {"tradable": False, "exclusion_reason": "PRIVATE_EXCLUSION", "event_cluster": "PRIVATE_CLUSTER"}
        members.append({**public, **private, "unknown": "PRIVATE_UNKNOWN"})
    raw = json.dumps(members, ensure_ascii=True, indent=1).encode()
    output = io.BytesIO()
    with gzip.GzipFile(filename="membership-original.json", fileobj=output, mode="wb", mtime=123456789, compresslevel=level) as archive:
        archive.write(raw)
    return output.getvalue(), raw


@pytest.mark.parametrize("strategy,table", [("golden-raspberry", "market_sweeps"), ("golden-strawberry", "market_membership_blobs")])
def test_membership_gzip_preserves_original_header_deflate_footer_and_private_members(strategy, table):
    original, raw = membership_blob(strategy)
    row = {"membership_blob": original, "private_metric": "PRIVATE_ROW_METRIC"}
    store = MemoryStore()
    result = externalize_mixed_row(strategy, table, row, dict(row), PayloadReferences(reader=store, writer=store))
    envelope = result["membership_blob"]
    assert isinstance(envelope, bytes)
    assert is_mixed_payload(envelope)
    assert envelope.startswith(b"\x1ePMMIX1:")
    restored = resolve_mixed_payload(envelope, PayloadReferences(reader=store))
    assert restored == original
    assert gzip.decompress(restored) == raw
    assert b"membership-original.json\x00" in restored[:64]
    assert len(store.payloads) == 1
    assert result["private_metric"] == row["private_metric"]
    public = inspect_mixed_payload(envelope, PayloadReferences(reader=store))["public_fragments"]
    assert all(b"PRIVATE_" not in fragment for fragment in public)
    allowed = (("source_market_key", "condition_id", "market_id", "raw_market_sha256", "event_id")
               if strategy == "golden-raspberry" else ("condition_id", "market_id", "token_ids", "raw_market_sha256"))
    normalize = lambda value: json.dumps(value, sort_keys=True)
    assert {normalize(json.loads(fragment)) for fragment in public} == {
        normalize(member[key]) for member in json.loads(raw) for key in allowed
    }


def sample_envelope():
    store = MemoryStore()
    source = guava_json(json.dumps(public_body(), ensure_ascii=False, separators=(",", ":")))
    _, result = convert_text(("golden-guava", "events", "event_json", source), store)
    return source, result["event_json"], store


@pytest.mark.parametrize("version", [0, 2])
def test_unknown_mixed_version_fails_closed_for_text_and_blob(version):
    for value in [f"\x1ePMMIX{version}:not-a-packet", f"\x1ePMMIX{version}:not-a-packet".encode()]:
        assert is_mixed_payload(value)
        with pytest.raises(MixedPayloadError):
            mixed_reference_hashes(value)
        with pytest.raises(MixedPayloadError):
            resolve_mixed_payload(value, PayloadReferences())
    assert not is_mixed_payload(None)
    for value in ["{}", b"original blob"]:
        assert resolve_mixed_payload(value, PayloadReferences()) == value
        assert mixed_reference_hashes(value) == []


@pytest.mark.parametrize("damage", ["digest", "body", "truncated", "extra", "base64"])
def test_packet_corruption_is_rejected_before_dependency_reads(damage):
    _, envelope, store = sample_envelope()
    body = base64.b64decode(envelope[len(PREFIX):])
    if damage == "digest":
        body = body[:-1] + bytes([body[-1] ^ 1])
    elif damage == "body":
        body = body[:HEADER.size] + bytes([body[HEADER.size] ^ 1]) + body[HEADER.size + 1:]
    elif damage == "truncated":
        body = body[:-1]
    elif damage == "extra":
        body += b"extra"
    tampered = PREFIX + ("%%%" if damage == "base64" else base64.b64encode(body).decode())
    with pytest.raises(MixedPayloadError):
        resolve_mixed_payload(tampered, PayloadReferences(reader=store))
    assert store.reads == []


@pytest.mark.parametrize("field,replacement", [
    (0, 0), (0, 255), (1, 1), (2, 4), (3, 0), (3, 4),
    (4, 0), (4, 1), (5, 1), (6, 1), (7, 1), (8, 0), (8, 2), (9, b"\xff" * 32),
])
def test_header_tampering_fails_with_recomputed_packet_checksum(field, replacement):
    _, envelope, store = sample_envelope()
    fields, body = packet_parts(envelope)
    fields[field] = replacement
    with pytest.raises(MixedPayloadError):
        resolve_mixed_payload(packet(fields, body), PayloadReferences(reader=store))


@pytest.mark.parametrize("damage", ["private_value", "public_index", "splice_count", "trailing"])
def test_private_template_tampering_cannot_publish_modified_original(damage):
    _, envelope, store = sample_envelope()
    template = private_template(envelope, store)
    if damage == "private_value":
        assert b"PRIVATE_FEE" in template
        template = template.replace(b"PRIVATE_FEE", b"PRIVATE_BAD")
    elif damage == "trailing":
        template += b"trailing"
    else:
        offset = 0
        for _ in range(2):
            size = struct.unpack_from("!I", template, offset)[0]
            offset += 4 + size
        if damage == "public_index":
            offset += 4
            size = struct.unpack_from("!I", template, offset)[0]
            offset += 4 + size
        template = template[:offset] + struct.pack("!I", 0xFFFFFFFF) + template[offset + 4:]
    with pytest.raises(MixedPayloadError):
        resolve_mixed_payload(rewrite_private(envelope, store, template), PayloadReferences(reader=store))


@pytest.mark.parametrize("public_codec", [0, 1, 2, 3])
@pytest.mark.parametrize("private_codec", [1, 2, 3])
def test_all_public_and_private_codec_recipes_preserve_text_bytes(public_codec, private_codec):
    original, envelope, store = sample_envelope()
    template = private_template(envelope, store)
    raw = public_raw(envelope, store)
    if public_codec == 3:
        raw = inspect_mixed_payload(envelope, PayloadReferences(reader=store))["public_fragments"][0]
    body = (raw if public_codec == 0 else zlib.compress(raw, 9) if public_codec == 1
            else lzma.compress(raw, preset=6) if public_codec == 2 else gzip.compress(raw, mtime=0))
    changed = rewrite_public(envelope, store, body, public_codec, raw_size=len(raw))
    changed = rewrite_private(changed, store, template, codec=private_codec)
    assert resolve_mixed_payload(changed, PayloadReferences(reader=store)) == original
    assert verify_mixed_ownership("golden-guava", "events", "event_json", original, changed,
                                  PayloadReferences(reader=store))


@pytest.mark.parametrize("damage", ["trailing", "concatenated", "truncated", "expansion", "memory"])
def test_xz_public_aggregate_limits_and_trailing_data_fail_closed(damage, monkeypatch):
    _, envelope, store = sample_envelope()
    raw = public_raw(envelope, store)
    stored = lzma.compress(raw, preset=6)
    if damage == "trailing":
        stored += b"trailing"
    elif damage == "concatenated":
        stored += stored
    elif damage == "truncated":
        stored = stored[:-1]
    elif damage == "expansion":
        stored = lzma.compress(b"x" * 100_000, preset=6)
        monkeypatch.setattr(market_data_mixed, "MAX_TEMPLATE_BYTES", 50_000)
    else:
        monkeypatch.setattr(market_data_mixed, "XZ_MEMORY_LIMIT", 1 << 20)
    tampered = rewrite_public(envelope, store, stored, 2)
    with pytest.raises(MixedPayloadError):
        inspect_mixed_payload(tampered, PayloadReferences(reader=store))


@pytest.mark.parametrize("fragment", [b"NaN", b'{"x":1,"x":2}', b"{} trailing", b"\xff"])
def test_public_fragments_require_valid_bounded_json(fragment):
    _, envelope, store = sample_envelope()
    raw = b"PMMIXPARTS1" + struct.pack("!II", 1, len(fragment)) + fragment
    tampered = rewrite_public(envelope, store, raw, 0, raw_size=len(raw))
    with pytest.raises(MixedPayloadError):
        inspect_mixed_payload(tampered, PayloadReferences(reader=store))


@pytest.mark.parametrize("source", [
    '{"raw":{},"raw":{}}', '{"raw":{"value":NaN}}', '{"raw":[]}', '{"raw":{}',
    '{"raw":{}} trailing', '{"raw":{},"private":{"key":1,"key":2}}',
])
def test_invalid_source_json_is_rejected_before_any_shared_write(source):
    store = MemoryStore()
    row = {"event_json": source}
    with pytest.raises(MixedPayloadError):
        externalize_mixed_row("golden-guava", "events", row, dict(row), PayloadReferences(reader=store, writer=store))
    assert store.writes == []


@pytest.mark.parametrize("bound", ["MAX_JSON_BYTES", "MAX_SPLICES", "MAX_PACKET_BYTES"])
def test_source_and_packet_bounds_fail_before_any_shared_write(bound, monkeypatch):
    store = MemoryStore()
    row = {"metadata_json": json.dumps({"question": "public question " * 1000, "condition_id": "public-condition"})}
    monkeypatch.setattr(market_data_mixed, bound, 1 if bound == "MAX_SPLICES" else 32)
    with pytest.raises(MixedPayloadError):
        externalize_mixed_row("golden-pomegranate", "market_metadata_versions", row, dict(row), PayloadReferences(reader=store, writer=store))
    assert store.writes == []


def test_deep_private_tree_is_rejected_before_any_shared_write():
    store = MemoryStore()
    source = '{"raw":{},"private":' + "[" * 130 + "0" + "]" * 130 + "}"
    row = {"event_json": source}
    with pytest.raises(MixedPayloadError, match="nesting"):
        externalize_mixed_row("golden-guava", "events", row, dict(row), PayloadReferences(reader=store, writer=store))
    assert store.writes == []


@pytest.mark.parametrize("level", [0, 1, 9])
def test_membership_gzip_compression_recipes_remain_byte_exact(level):
    blob, _ = membership_blob("golden-strawberry", count=2, level=level)
    row = {"membership_blob": blob}
    store = MemoryStore()
    result = externalize_mixed_row("golden-strawberry", "market_membership_blobs", row, dict(row), PayloadReferences(reader=store, writer=store))
    assert is_mixed_payload(result["membership_blob"])
    assert resolve_mixed_payload(result["membership_blob"], PayloadReferences(reader=store)) == blob


@pytest.mark.parametrize("damage", ["trailing", "concatenated", "truncated", "digest"])
def test_invalid_membership_gzip_or_source_hash_has_no_shared_ack(damage):
    blob, _ = membership_blob("golden-strawberry", count=2)
    row = {"membership_blob": blob}
    if damage == "digest":
        row["membership_sha256"] = "f" * 64
    elif damage == "trailing":
        row["membership_blob"] += b"trailing"
    elif damage == "concatenated":
        row["membership_blob"] += blob
    else:
        row["membership_blob"] = blob[:-1]
    store = MemoryStore()
    with pytest.raises(MixedPayloadError):
        externalize_mixed_row("golden-strawberry", "market_membership_blobs", row, dict(row), PayloadReferences(reader=store, writer=store))
    assert store.writes == []


def test_existing_mixed_envelope_reacks_exact_public_aggregate_to_new_store():
    original, envelope, source = sample_envelope()
    destination = MemoryStore()
    row = {"event_json": envelope}
    result = externalize_mixed_row(
        "golden-guava", "events", row, dict(row), PayloadReferences(reader=source, writer=destination),
    )
    assert result["event_json"] == envelope
    assert destination.payloads == source.payloads
    assert resolve_mixed_payload(envelope, PayloadReferences(reader=destination)) == original


def test_failed_shared_ack_does_not_publish_mixed_marker_or_mutate_caller_rows():
    class FailedStore(MemoryStore):
        def put_many(self, values):
            raise OSError("injected durable write failure")

    original = {"metadata_json": '{"question":"public-question","fee_metadata":{"private":1}}'}
    encoded = dict(original)
    store = FailedStore()
    with pytest.raises(OSError, match="durable write failure"):
        externalize_mixed_row(
            "golden-pomegranate", "market_metadata_versions", original, encoded,
            PayloadReferences(reader=store, writer=store),
        )
    assert encoded == original
    assert not is_mixed_payload(encoded["metadata_json"])


@pytest.mark.parametrize("damage", ["sibling_bytes", "source_hash"])
def test_guava_raw_sibling_identity_mismatch_has_no_extra_public_ack(damage):
    raw = json.dumps(public_body(), ensure_ascii=False, separators=(",", ":")).encode()
    row = {"event_json": guava_json(raw.decode()), "raw_gzip": gzip.compress(raw, mtime=10),
           "raw_sha256": hashlib.sha256(raw).hexdigest()}
    store = MemoryStore()
    encoded = dict(row)
    blob = gzip.compress(raw, mtime=11) if damage == "sibling_bytes" else row["raw_gzip"]
    encoded["raw_gzip"] = PayloadReferences(reader=store, writer=store).encode_many([blob])[0]
    if damage == "source_hash":
        row["raw_sha256"] = "f" * 64
    with pytest.raises(MixedPayloadError):
        externalize_mixed_row("golden-guava", "events", row, encoded, PayloadReferences(reader=store, writer=store))
    assert len(store.writes) == 1


def test_membership_gzip_extra_comment_and_header_crc_are_retained_exactly():
    original, raw = membership_blob("golden-strawberry", count=3)
    assert original[3] == 8
    original_header_end = original.index(b"\0", 10) + 1
    extra = b"synthetic-extra"
    header = original[:3] + bytes([original[3] | 4 | 16 | 2]) + original[4:10]
    header += struct.pack("<H", len(extra)) + extra + original[10:original_header_end] + b"original-comment\0"
    header += struct.pack("<H", zlib.crc32(header) & 0xFFFF)
    blob = header + original[original_header_end:]
    assert gzip.decompress(blob) == raw
    row = {"membership_blob": blob, "membership_sha256": hashlib.sha256(raw).hexdigest()}
    store = MemoryStore()
    result = externalize_mixed_row(
        "golden-strawberry", "market_membership_blobs", row, dict(row), PayloadReferences(reader=store, writer=store),
    )
    assert is_mixed_payload(result["membership_blob"])
    assert resolve_mixed_payload(result["membership_blob"], PayloadReferences(reader=store)) == blob
