import copy
import hashlib
import json
import lzma
import random
import sqlite3
import struct
import zlib

import pytest

from polybot_observability import market_data_apple
from polybot_observability.market_data_apple import (
    AppleFrameError,
    externalize_frame,
    externalize_legacy_payload,
    frame_provenance,
    frame_reference_hashes,
    inspect_public_objects,
    is_shared_frame,
    legacy_payload_roles,
    resolve_frame,
)
from polybot_observability.market_data_refs import MissingMarketDataConfiguration, PayloadReferences, parse_reference
from polybot_observability.market_data_store import MissingPayloadError


PREFIX = b"\x1ePMAPPLEFRAME2:"
HEADER = struct.Struct("!BBHIIII32s32s")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def pack(value):
    return zlib.compress(canonical(value), 6)


def packet_parts(blob):
    boundary = len(PREFIX) + HEADER.size
    return list(HEADER.unpack(blob[len(PREFIX):boundary])), blob[boundary:-32]


def packet(fields, private_body):
    body = PREFIX + HEADER.pack(*fields) + private_body
    return body + hashlib.sha256(body).digest()


def public_raw(blob, store):
    fields, _ = packet_parts(blob)
    if not fields[2]:
        return b""
    stored = store.payloads[fields[8].hex()]
    return stored if fields[1] == 0 else zlib.decompress(stored) if fields[1] == 1 else lzma.decompress(stored)


def private_frame(blob, store):
    fields, body = packet_parts(blob)
    if fields[0] == 137:
        decoder = zlib.decompressobj(zdict=public_raw(blob, store)[-32768:])
        raw = decoder.decompress(body) + decoder.flush()
    else:
        raw = zlib.decompress(body)
    return json.loads(raw)


def rewrite_private(blob, store, value, codec=None, raw=None):
    fields, _ = packet_parts(blob)
    fields[0] = fields[0] if codec is None else codec
    raw = canonical(value) if raw is None else raw
    fields[6] = len(raw)
    if fields[0] == 137:
        compressor = zlib.compressobj(9, zdict=public_raw(blob, store)[-32768:])
        body = compressor.compress(raw) + compressor.flush()
    else:
        body = zlib.compress(raw, 9)
    return packet(fields, body)


def rewrite_public(blob, store, stored, codec, raw_size=None):
    fields, body = packet_parts(blob)
    fields[1] = codec
    if raw_size is not None:
        fields[5] = raw_size
    fields[8] = hashlib.sha256(stored).digest()
    store.payloads[fields[8].hex()] = stored
    return packet(fields, body)


class MemoryStore:
    def __init__(self):
        self.payloads = {}
        self.write_calls = []
        self.read_calls = []

    def put_many(self, payloads):
        self.write_calls.append(list(payloads))
        hashes = [hashlib.sha256(value).hexdigest() for value in payloads]
        self.payloads.update(zip(hashes, payloads))
        return hashes

    def get_many(self, hashes):
        self.read_calls.append(list(hashes))
        missing = [digest for digest in hashes if digest not in self.payloads]
        if missing:
            raise MissingPayloadError(missing)
        return [self.payloads[digest] for digest in hashes]


@pytest.fixture
def frame():
    public_market = {"id": "m1", "question": "서울 경기", "clobTokenIds": ["yes", "no"]}
    public_event = {"id": "e1", "slug": "public-event", "markets": [public_market]}
    public_book = {"asset_id": "yes", "bids": [{"price": "0.4", "size": "12"}], "asks": []}
    duplicate_book = {"asset_id": "yes", "bids": [], "asks": [{"price": "0.5", "size": "8"}]}
    value = {
        "format": "apple-filtered-frames-v1",
        "markets": [{"event_id": "e1", "market": public_market, "tokens": ["PRIVATE_SELECTED_TOKENS"]}],
        "events": {"e1": {"event": public_event, "request": {"intent": "PRIVATE_EVENT_REQUEST"}}},
        "books": {
            "yes": {"book": public_book, "capacity": {"limit": "PRIVATE_CAPACITY"},
                    "request": {"trace": "PRIVATE_BOOK_REQUEST"}, "status": "PRIVATE_BOOK_STATUS",
                    "duplicate_books": [duplicate_book, public_book]},
            "no": {"book": None, "capacity": {"limit": "PRIVATE_NULL_CAPACITY"},
                   "request": {"trace": "PRIVATE_NULL_REQUEST"}, "status": "PRIVATE_MISSING_STATUS"},
        },
        "followups": [{
            "event": {"event": public_event, "request": {"trace": "PRIVATE_FOLLOWUP_REQUEST"}},
            "markets": [public_market], "watched_market_ids": ["PRIVATE_WATCHED_MARKET"],
            "unresolved_market_ids": ["PRIVATE_UNRESOLVED_MARKET"],
        }],
        "audit": {"decision": "PRIVATE_AUDIT"}, "fee": {"estimate": "PRIVATE_FEE"},
        "capacity": "PRIVATE_TOP_CAPACITY", "selection": "PRIVATE_SELECTION",
        "features": {"signal": "PRIVATE_FEATURE"},
        "unknown": {"market": {"id": "PRIVATE_UNKNOWN_MARKET"}, "book": "PRIVATE_UNKNOWN_BOOK"},
    }
    return value, [public_market, public_event, public_book, duplicate_book]


def test_only_explicit_public_leaves_are_shared_and_original_bytes_are_restored(frame):
    value, public = frame
    original = pack(value)
    store = MemoryStore()
    assert not is_shared_frame(original)
    envelope = externalize_frame(original, PayloadReferences(reader=store, writer=store))
    assert envelope.startswith(PREFIX)
    assert is_shared_frame(envelope)
    assert len(store.payloads) == 1
    assert [len(batch) for batch in store.write_calls] == [1]
    assert b"PRIVATE_" not in public_raw(envelope, store)
    reads_before = len(store.read_calls)
    assert frame_reference_hashes(envelope) == list(store.payloads)
    assert len(store.read_calls) == reads_before
    assert inspect_public_objects(envelope, PayloadReferences(reader=store)) == public
    provenance = frame_provenance(envelope)
    assert provenance["format"] == "apple-public-frame-envelope-v2"
    assert provenance["public_count"] == len(public)
    assert provenance["original_raw_bytes"] == len(canonical(value))
    assert provenance["original_compressed_bytes"] == len(original)
    assert provenance["original_compressed_sha256"] == hashlib.sha256(original).hexdigest()
    assert provenance["public_payload_sha256"] == next(iter(store.payloads))
    assert "private_body" not in provenance
    assert "PRIVATE_" not in json.dumps(provenance)
    restored = resolve_frame(envelope, PayloadReferences(reader=store))
    assert restored == original
    assert json.loads(zlib.decompress(restored)) == value


def test_externalized_frame_is_idempotent_but_revalidates_dependencies(frame):
    value, _ = frame
    store = MemoryStore()
    envelope = externalize_frame(pack(value), PayloadReferences(reader=store, writer=store))
    writes_before = len(store.write_calls)
    contents_before = dict(store.payloads)
    assert externalize_frame(envelope, PayloadReferences(reader=store, writer=store)) == envelope
    assert len(store.write_calls) == writes_before + 1
    assert store.payloads == contents_before
    assert store.read_calls
    digest = frame_reference_hashes(envelope)[0]
    del store.payloads[digest]
    with pytest.raises(MissingPayloadError):
        externalize_frame(envelope, PayloadReferences(reader=store, writer=store))
    assert len(store.write_calls) == writes_before + 1


def test_missing_or_corrupt_public_dependency_never_returns_partial_frame(frame):
    value, _ = frame
    store = MemoryStore()
    envelope = externalize_frame(pack(value), PayloadReferences(reader=store, writer=store))
    with pytest.raises(MissingMarketDataConfiguration):
        resolve_frame(envelope, PayloadReferences())
    digest = frame_reference_hashes(envelope)[0]
    original = store.payloads.pop(digest)
    with pytest.raises(MissingPayloadError) as error:
        resolve_frame(envelope, PayloadReferences(reader=store))
    assert digest in error.value.hashes
    store.payloads[digest] = original + b" "
    with pytest.raises(ValueError, match="digest"):
        resolve_frame(envelope, PayloadReferences(reader=store))


def test_null_books_and_empty_public_collections_preserve_private_frame():
    value = {
        "format": "apple-filtered-frames-v1", "markets": [], "events": {}, "followups": [],
        "books": {"missing": {"book": None, "capacity": "PRIVATE_CAPACITY", "status": "MISSING"}},
        "audit": "PRIVATE_AUDIT",
    }
    store = MemoryStore()
    original = pack(value)
    envelope = externalize_frame(original, PayloadReferences(reader=store, writer=store))
    assert is_shared_frame(envelope)
    assert frame_reference_hashes(envelope) == []
    assert store.payloads == {}
    assert store.write_calls == []
    assert resolve_frame(envelope, PayloadReferences()) == original


@pytest.mark.parametrize("compression_level", [0, 1, 9])
def test_different_zlib_encoding_is_rejected_before_any_shared_write(frame, compression_level):
    value, _ = frame
    store = MemoryStore()
    raw = canonical(value)
    original = zlib.compress(raw, compression_level)
    assert original != zlib.compress(raw, 6)
    with pytest.raises(ValueError):
        externalize_frame(original, PayloadReferences(reader=store, writer=store))
    assert store.write_calls == []


@pytest.mark.parametrize("encoding", ["pretty", "escaped_unicode", "unsorted", "duplicate_keys"])
def test_noncanonical_json_is_rejected_before_any_shared_write(frame, encoding):
    value, _ = frame
    if encoding == "pretty":
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2).encode()
    elif encoding == "escaped_unicode":
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    elif encoding == "unsorted":
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    else:
        raw = canonical(value)[:-1] + b',"format":"apple-filtered-frames-v1"}'
    assert raw != canonical(value)
    store = MemoryStore()
    with pytest.raises(ValueError):
        externalize_frame(zlib.compress(raw, 6), PayloadReferences(reader=store, writer=store))
    assert store.write_calls == []


@pytest.mark.parametrize("raw", [b"not-zlib", zlib.compress(b"[]", 6), zlib.compress(b"{}", 6)])
def test_invalid_frame_source_does_not_ack_a_whole_frame_payload(raw):
    store = MemoryStore()
    with pytest.raises(ValueError):
        externalize_frame(raw, PayloadReferences(reader=store, writer=store))
    assert store.write_calls == []


@pytest.mark.parametrize("suffix", [b"trailing", zlib.compress(b"{}", 6)])
def test_trailing_compressed_data_is_rejected_before_any_shared_write(frame, suffix):
    value, _ = frame
    store = MemoryStore()
    with pytest.raises(ValueError):
        externalize_frame(pack(value) + suffix, PayloadReferences(reader=store, writer=store))
    assert store.write_calls == []


@pytest.mark.parametrize("limit", ["raw", "packed"])
def test_frame_size_limits_are_enforced_before_any_shared_write(frame, limit):
    value = copy.deepcopy(frame[0])
    if limit == "raw":
        value["unknown"] = "x" * (16 * 1024 * 1024)
    else:
        value["unknown"] = random.Random(0).randbytes(300_000).hex()
    original = pack(value)
    if limit == "raw":
        assert len(canonical(value)) > 16 * 1024 * 1024
        assert len(original) < 200_000
    else:
        assert len(original) > 200_000
    store = MemoryStore()
    with pytest.raises(ValueError):
        externalize_frame(original, PayloadReferences(reader=store, writer=store))
    assert store.write_calls == []


def test_existing_frame_reacks_public_closure_to_a_different_writer(frame):
    value, _ = frame
    source, destination = MemoryStore(), MemoryStore()
    original = pack(value)
    envelope = externalize_frame(original, PayloadReferences(reader=source, writer=source))
    assert externalize_frame(envelope, PayloadReferences(reader=source, writer=destination)) == envelope
    assert destination.payloads == source.payloads
    assert b"PRIVATE_" not in public_raw(envelope, destination)
    assert resolve_frame(envelope, PayloadReferences(reader=destination)) == original


def test_legacy_frame_detection_and_unknown_version_fail_closed(frame):
    original = pack(frame[0])
    assert resolve_frame(original, PayloadReferences()) == original
    assert frame_reference_hashes(original) == []
    assert not is_shared_frame(None)
    assert not is_shared_frame(PREFIX.decode())
    for version in [1, 3]:
        unknown = f"\x1ePMAPPLEFRAME{version}:".encode() + zlib.compress(b"{{}}")
        assert is_shared_frame(unknown)
        for operation in [frame_reference_hashes, lambda value: resolve_frame(value, PayloadReferences()),
                          lambda value: externalize_frame(value, PayloadReferences())]:
            with pytest.raises(AppleFrameError, match="version"):
                operation(unknown)


@pytest.mark.parametrize(
    "field,replacement",
    [
        (0, 6), (1, 99), (2, 0), (2, 4097), (2, 1),
        (3, 0), (3, 16_777_217), (3, 1), (4, 1), (4, 200_001),
        (5, 1), (5, 16_777_217), (6, 1), (7, b"\xff" * 32),
    ],
)
def test_packet_header_tampering_fails_even_with_valid_packet_checksum(frame, field, replacement):
    store = MemoryStore()
    envelope = externalize_frame(pack(frame[0]), PayloadReferences(reader=store, writer=store))
    fields, body = packet_parts(envelope)
    fields[field] = replacement
    with pytest.raises(ValueError):
        resolve_frame(packet(fields, body), PayloadReferences(reader=store))


@pytest.mark.parametrize("index", [True, -1, 4096, "0", None])
def test_invalid_public_indexes_fail_even_with_valid_packet_checksum(frame, index):
    store = MemoryStore()
    envelope = externalize_frame(pack(frame[0]), PayloadReferences(reader=store, writer=store))
    value = private_frame(envelope, store)
    value["books"]["yes"]["book"] = index
    with pytest.raises(AppleFrameError):
        resolve_frame(rewrite_private(envelope, store, value), PayloadReferences(reader=store))


@pytest.mark.parametrize("target", ["public_index", "private_audit", "expanded_indexes"])
def test_valid_index_or_private_field_substitution_cannot_change_reconstructed_frame(frame, target):
    store = MemoryStore()
    envelope = externalize_frame(pack(frame[0]), PayloadReferences(reader=store, writer=store))
    value = private_frame(envelope, store)
    if target == "public_index":
        value["books"]["yes"]["book"] = value["markets"][0]["market"]
    elif target == "private_audit":
        value["audit"]["decision"] = "PRIVATE_TAMPERED_AUDIT"
    else:
        value["markets"] *= 100
    with pytest.raises(AppleFrameError):
        resolve_frame(rewrite_private(envelope, store, value), PayloadReferences(reader=store))


def test_private_json_must_be_canonical_even_with_valid_packet_checksum(frame):
    store = MemoryStore()
    envelope = externalize_frame(pack(frame[0]), PayloadReferences(reader=store, writer=store))
    value = private_frame(envelope, store)
    noncanonical = rewrite_private(envelope, store, value, raw=json.dumps(value, indent=2).encode())
    with pytest.raises(AppleFrameError, match="canonical"):
        resolve_frame(noncanonical, PayloadReferences(reader=store))


@pytest.mark.parametrize("damage", ["packet_digest", "private_body", "truncated", "trailing"])
def test_packet_corruption_is_rejected_without_public_store_reads(frame, damage):
    store = MemoryStore()
    envelope = externalize_frame(pack(frame[0]), PayloadReferences(reader=store, writer=store))
    if damage == "packet_digest":
        tampered = envelope[:-1] + bytes([envelope[-1] ^ 1])
    elif damage == "private_body":
        offset = len(PREFIX) + HEADER.size
        tampered = envelope[:offset] + bytes([envelope[offset] ^ 1]) + envelope[offset + 1:]
    elif damage == "truncated":
        tampered = envelope[:-1]
    else:
        tampered = envelope + b"trailing"
    for operation in [frame_provenance, frame_reference_hashes,
                      lambda value: resolve_frame(value, PayloadReferences(reader=store))]:
        with pytest.raises(AppleFrameError):
            operation(tampered)
    assert store.read_calls == []


@pytest.mark.parametrize("public_codec", [0, 1, 2])
@pytest.mark.parametrize("private_codec", [9, 137])
def test_all_supported_public_and_private_codecs_restore_original_bytes(frame, public_codec, private_codec):
    store = MemoryStore()
    original = pack(frame[0])
    envelope = externalize_frame(original, PayloadReferences(reader=store, writer=store))
    raw = public_raw(envelope, store)
    stored = raw if public_codec == 0 else zlib.compress(raw, 9) if public_codec == 1 else lzma.compress(raw, preset=6)
    changed = rewrite_public(envelope, store, stored, public_codec)
    changed = rewrite_private(changed, store, private_frame(envelope, store), codec=private_codec)
    assert inspect_public_objects(changed, PayloadReferences(reader=store)) == frame[1]
    assert resolve_frame(changed, PayloadReferences(reader=store)) == original


def test_aggregate_compression_uses_smallest_supported_representation(frame):
    store = MemoryStore()
    envelope = externalize_frame(pack(frame[0]), PayloadReferences(reader=store, writer=store))
    raw = public_raw(envelope, store)
    choices = [raw, zlib.compress(raw, 9), lzma.compress(raw, format=lzma.FORMAT_XZ, preset=6)]
    provenance = frame_provenance(envelope)
    stored = store.payloads[provenance["public_payload_sha256"]]
    assert len(stored) == min(map(len, choices))
    assert stored == choices[provenance["public_codec"]]
    assert len(envelope) == len(packet_parts(envelope)[1]) + len(PREFIX) + HEADER.size + 32


@pytest.mark.parametrize("damage", ["invalid", "truncated", "trailing", "concatenated"])
def test_invalid_xz_aggregate_fails_even_with_valid_payload_and_packet_hashes(frame, damage):
    store = MemoryStore()
    envelope = externalize_frame(pack(frame[0]), PayloadReferences(reader=store, writer=store))
    stored = lzma.compress(public_raw(envelope, store), format=lzma.FORMAT_XZ, preset=6)
    broken = {"invalid": b"not-xz", "truncated": stored[:-1], "trailing": stored + b"trailing",
              "concatenated": stored + stored}[damage]
    tampered = rewrite_public(envelope, store, broken, 2)
    with pytest.raises(AppleFrameError, match="XZ"):
        inspect_public_objects(tampered, PayloadReferences(reader=store))


@pytest.mark.parametrize("codec", [1, 2])
def test_public_decompression_expansion_is_bounded(frame, monkeypatch, codec):
    store = MemoryStore()
    envelope = externalize_frame(pack(frame[0]), PayloadReferences(reader=store, writer=store))
    assert frame_provenance(envelope)["original_raw_bytes"] < 2048
    expanded = b"x" * 8192
    stored = zlib.compress(expanded, 9) if codec == 1 else lzma.compress(expanded, preset=6)
    tampered = rewrite_public(envelope, store, stored, codec)
    monkeypatch.setattr(market_data_apple, "RAW_LIMIT", 2048)
    with pytest.raises(AppleFrameError):
        inspect_public_objects(tampered, PayloadReferences(reader=store))


def test_xz_dictionary_memory_limit_fails_closed(frame, monkeypatch):
    store = MemoryStore()
    envelope = externalize_frame(pack(frame[0]), PayloadReferences(reader=store, writer=store))
    stored = lzma.compress(public_raw(envelope, store), format=lzma.FORMAT_XZ, preset=6)
    tampered = rewrite_public(envelope, store, stored, 2)
    monkeypatch.setattr(market_data_apple, "XZ_MEMORY_LIMIT", 1 << 20)
    with pytest.raises(AppleFrameError, match="XZ"):
        inspect_public_objects(tampered, PayloadReferences(reader=store))


@pytest.mark.parametrize("damage", ["noncanonical", "not_array", "not_object"])
def test_public_aggregate_requires_canonical_object_array(frame, damage):
    store = MemoryStore()
    envelope = externalize_frame(pack(frame[0]), PayloadReferences(reader=store, writer=store))
    if damage == "noncanonical":
        raw = json.dumps(frame[1], indent=2).encode()
    elif damage == "not_array":
        raw = canonical({"objects": frame[1]})
    else:
        raw = canonical([1] * len(frame[1]))
    tampered = rewrite_public(envelope, store, raw, 0, raw_size=len(raw))
    with pytest.raises(AppleFrameError, match="canonical"):
        inspect_public_objects(tampered, PayloadReferences(reader=store))


@pytest.mark.parametrize("header", [{"raw_sha256": "f" * 64}, {"raw_size": 1}])
def test_source_header_identity_is_checked_before_any_shared_write(frame, header):
    store = MemoryStore()
    with pytest.raises(AppleFrameError, match="header"):
        externalize_frame(pack(frame[0]), PayloadReferences(reader=store, writer=store), **header)
    assert store.write_calls == []


def test_public_object_count_bound_is_checked_before_any_shared_write(frame, monkeypatch):
    monkeypatch.setattr(market_data_apple, "PUBLIC_OBJECT_LIMIT", 3)
    store = MemoryStore()
    with pytest.raises(AppleFrameError, match="count"):
        externalize_frame(pack(frame[0]), PayloadReferences(reader=store, writer=store))
    assert store.write_calls == []


def test_many_duplicate_public_leaves_use_one_aggregate_write():
    public = {"id": "public-market"}
    value = {"format": "apple-filtered-frames-v1", "events": {}, "books": {}, "followups": [],
             "markets": [{"market": public, "tokens": ["PRIVATE_SELECTION"]} for _ in range(1100)]}
    store = MemoryStore()
    original = pack(value)
    envelope = externalize_frame(original, PayloadReferences(reader=store, writer=store))
    assert [len(batch) for batch in store.write_calls] == [1]
    assert inspect_public_objects(envelope, PayloadReferences(reader=store)) == [public]
    assert len(frame_reference_hashes(envelope)) == 1
    assert resolve_frame(envelope, PayloadReferences(reader=store)) == original


@pytest.mark.parametrize(
    "location,value",
    [
        (("markets",), {}),
        (("events", "e1", "event"), "not a public object"),
        (("books", "yes", "book"), []),
        (("books", "yes", "duplicate_books"), None),
        (("books", "yes", "duplicate_books"), [None]),
        (("followups", 0, "event"), None),
        (("followups", 0, "markets"), [1]),
    ],
)
def test_invalid_public_leaf_layout_never_writes_a_partial_frame(frame, location, value):
    original = copy.deepcopy(frame[0])
    parent = original
    for key in location[:-1]:
        parent = parent[key]
    parent[location[-1]] = value
    store = MemoryStore()
    with pytest.raises(AppleFrameError):
        externalize_frame(pack(original), PayloadReferences(reader=store, writer=store))
    assert store.write_calls == []


def legacy_row(value):
    raw = canonical(value)
    return {"hash": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "encoding": "zlib",
            "compressed": zlib.compress(raw, 9), "retained_local_metadata": "PRIVATE_METADATA"}


@pytest.mark.parametrize(
    "roles",
    [[], ["unknown"], ["private_metrics"], ["private_request_body"], ["unverified_response"],
     ["public_book_depth", "private_metrics"], ["public_http_response", "unverified_response"],
     ["public_market_document", "unknown"]],
)
def test_private_unknown_or_mixed_legacy_roles_are_kept_local_without_store_access(roles):
    row = legacy_row({"metrics": "PRIVATE_METRICS", "features": [1, 2, 3]})
    original = dict(row)
    store = MemoryStore()
    assert externalize_legacy_payload(row, roles, PayloadReferences(reader=store, writer=store)) == original
    assert row == original
    assert store.read_calls == []
    assert store.write_calls == []


@pytest.mark.parametrize(
    "roles", [["public_http_response"], ["public_event_document"], ["public_market_document"],
              ["public_book_depth"], ["public_market_document", "public_http_response"]],
)
def test_exclusively_public_legacy_roles_share_exact_compressed_blob_only(roles):
    row = legacy_row({"asset_id": "public-token", "bids": [], "asks": []})
    store = MemoryStore()
    result = externalize_legacy_payload(row, roles, PayloadReferences(reader=store, writer=store))
    kind, digest = parse_reference(result["compressed"])
    assert kind == "B"
    assert digest == hashlib.sha256(row["compressed"]).hexdigest()
    assert store.payloads == {digest: row["compressed"]}
    assert {key: value for key, value in result.items() if key != "compressed"} == {
        key: value for key, value in row.items() if key != "compressed"
    }
    assert PayloadReferences(reader=store).decode_many([result["compressed"]]) == [row["compressed"]]
    assert externalize_legacy_payload(result, roles, PayloadReferences(reader=store, writer=store)) == result


@pytest.mark.parametrize("damage", [{"encoding": "gzip"}, {"bytes": 1}, {"hash": "f" * 64}, {"compressed": b"bad"}])
def test_corrupt_public_legacy_payload_fails_before_shared_write(damage):
    row = {**legacy_row({"id": "public"}), **damage}
    store = MemoryStore()
    with pytest.raises(ValueError):
        externalize_legacy_payload(row, ["public_event_document"], PayloadReferences(reader=store, writer=store))
    assert store.write_calls == []


@pytest.mark.parametrize('request_columns', ['payload_hash TEXT', 'payload_hash TEXT,url TEXT', 'payload_hash TEXT,method TEXT'])
def test_missing_request_endpoint_blocks_a_shared_public_event_role(request_columns):
    digest='a'*64
    connection=sqlite3.connect(':memory:')
    connection.executescript('CREATE TABLE payloads(hash TEXT PRIMARY KEY);'
                            'CREATE TABLE events(payload_hash TEXT REFERENCES payloads(hash));'
                            f'CREATE TABLE requests({request_columns});')
    connection.execute('INSERT INTO events VALUES(?)',(digest,))
    connection.execute('INSERT INTO requests(payload_hash) VALUES(?)',(digest,))
    roles=market_data_apple.legacy_payload_roles(connection,[digest])
    assert roles[digest]==('public_event_document','unverified_response')
    connection.close()


def test_legacy_roles_combine_explicit_references_without_reading_payload_bytes():
    names = ["event", "market", "depth", "metrics", "mixed", "request", "response", "compact-depth", "compact-metrics", "unknown"]
    hashes = {name: hashlib.sha256(name.encode()).hexdigest() for name in names}
    with sqlite3.connect(":memory:") as connection:
        connection.executescript("""
            CREATE TABLE events(payload_hash);
            CREATE TABLE event_observations(payload_hash);
            CREATE TABLE markets(payload_hash);
            CREATE TABLE market_observations(payload_hash);
            CREATE TABLE book_observations(depth_hash,metrics_hash);
            CREATE TABLE book_observations_legacy(depth_hash,metrics_hash);
            CREATE TABLE requests(request_hash,payload_hash,url,method);
            CREATE TABLE compact_payload_keys(id,payload_hash);
            CREATE TABLE compact_books(depth_key,metrics_key);
        """)
        for table, name in [("events", "event"), ("event_observations", "mixed"),
                            ("markets", "market"), ("market_observations", "market")]:
            connection.execute(f"INSERT INTO {table} VALUES (?)", (hashes[name],))
        connection.execute("INSERT INTO book_observations VALUES (?,?)", (hashes["depth"], hashes["metrics"]))
        connection.execute("INSERT INTO book_observations_legacy VALUES (?,?)", (hashes["depth"], hashes["mixed"]))
        connection.execute("INSERT INTO requests VALUES (?,?,?,?)", (
            hashes["request"], hashes["response"], "https://gamma-api.polymarket.com/events?closed=false", "GET",
        ))
        connection.executemany("INSERT INTO compact_payload_keys VALUES (?,?)", [
            (1, hashes["compact-depth"]), (2, hashes["compact-metrics"]),
        ])
        connection.execute("INSERT INTO compact_books VALUES (1,2)")
        actual = legacy_payload_roles(connection, list(hashes.values()))
        assert actual == {
            hashes["event"]: ("public_event_document",), hashes["market"]: ("public_market_document",),
            hashes["depth"]: ("public_book_depth",), hashes["metrics"]: ("private_metrics",),
            hashes["mixed"]: ("private_metrics", "public_event_document"),
            hashes["request"]: ("private_request_body",), hashes["response"]: ("public_http_response",),
            hashes["compact-depth"]: ("public_book_depth",), hashes["compact-metrics"]: ("private_metrics",),
            hashes["unknown"]: ("unknown",),
        }
        assert legacy_payload_roles(connection, [hashes["unknown"]]) == {hashes["unknown"]: ("unknown",)}
        assert legacy_payload_roles(connection, []) == {}


@pytest.mark.parametrize("target", ["payloads", "compact_payload_keys"])
def test_unclassified_legacy_foreign_key_blocks_otherwise_public_payload(target):
    shared_row = legacy_row({"id": "public-but-also-unknown"})
    public_row = legacy_row({"id": "public-only"})
    digest, clean_digest = shared_row["hash"], public_row["hash"]
    with sqlite3.connect(":memory:") as connection:
        connection.executescript("""
            CREATE TABLE payloads(hash TEXT PRIMARY KEY);
            CREATE TABLE compact_payload_keys(id INTEGER PRIMARY KEY,payload_hash REFERENCES payloads(hash));
            CREATE TABLE events(payload_hash REFERENCES payloads(hash));
        """)
        connection.executemany("INSERT INTO payloads VALUES (?)", [(digest,), (clean_digest,)])
        connection.executemany("INSERT INTO events VALUES (?)", [(digest,), (clean_digest,)])
        if target == "payloads":
            connection.execute("CREATE TABLE opaque_usage(body_hash REFERENCES payloads(hash))")
            connection.execute("INSERT INTO opaque_usage VALUES (?)", (digest,))
        else:
            connection.execute("INSERT INTO compact_payload_keys VALUES (1,?)", (digest,))
            connection.execute("CREATE TABLE opaque_usage(body_key REFERENCES compact_payload_keys(id))")
            connection.execute("INSERT INTO opaque_usage VALUES (1)")
        roles = legacy_payload_roles(connection, [digest, clean_digest])
    assert roles[digest] == ("public_event_document", "unclassified_reference")
    assert roles[clean_digest] == ("public_event_document",)
    store = MemoryStore()
    assert externalize_legacy_payload(shared_row, roles[digest], PayloadReferences(reader=store, writer=store)) == shared_row
    assert store.write_calls == []
    migrated = externalize_legacy_payload(public_row, roles[clean_digest], PayloadReferences(reader=store, writer=store))
    assert parse_reference(migrated["compressed"])[0] == "B"


@pytest.mark.parametrize(
    "url,method,role",
    [
        ("https://clob.polymarket.com/books", "POST", "public_http_response"),
        ("https://clob.polymarket.com/book?token_id=123", "GET", "public_http_response"),
        ("https://gamma-api.polymarket.com/events/123", "GET", "public_http_response"),
        ("https://clob.polymarket.com/orders", "GET", "unverified_response"),
        ("https://gamma-api.polymarket.com/events?access_token=synthetic", "GET", "unverified_response"),
        ("https://gamma-api.polymarket.com/events?API_KEY=synthetic", "GET", "unverified_response"),
        ("https://clob.polymarket.com/book", "POST", "unverified_response"),
        ("https://gamma-api.polymarket.com/unknown", "GET", "unverified_response"),
        ("https://unrelated.example/events", "GET", "unverified_response"),
        ("http://gamma-api.polymarket.com/events", "GET", "unverified_response"),
        ("https://user:synthetic@clob.polymarket.com/books", "POST", "unverified_response"),
    ],
)
def test_legacy_http_response_classification_requires_a_public_endpoint(url, method, role):
    digest = hashlib.sha256(b"response").hexdigest()
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE requests(payload_hash,url,method)")
        connection.execute("INSERT INTO requests VALUES (?,?,?)", (digest, url, method))
        assert legacy_payload_roles(connection, [digest]) == {digest: (role,)}


def test_prepare_has_no_writes_before_publish(frame):
    store = MemoryStore()
    refs = PayloadReferences(reader=store, writer=store)
    original = pack(frame[0])
    plan = market_data_apple.prepare_frame(original, refs)
    assert store.write_calls == []
    assert plan.public_budget_bytes >= len(plan.public_payload) + 16384
    assert plan.publish(refs) == plan.envelope
    assert len(store.write_calls) == 1
    assert resolve_frame(plan.envelope, refs) == original


def test_frame_index_read_without_private_and_idempotent_timestamp(frame, tmp_path):
    from polybot_observability.market_data_store import PayloadStore, PayloadReader, ObservationConflictError, Observation
    path = tmp_path / 'public.db'
    with PayloadStore(path) as store:
        refs = PayloadReferences(reader=store, writer=store)
        envelope = externalize_frame(pack(frame[0]), refs)
        args = dict(observer='m5:apple:do', frame_id='2026-09/42/jenkins:9',
                    observed_at='2026-09-29T12:00:00.123456+00:00', job='polybot-do')
        assert market_data_apple.publish_frame_observation(envelope, refs, **args)
        assert market_data_apple.publish_frame_observation(envelope, refs, **args)
        with pytest.raises(ObservationConflictError):
            market_data_apple.publish_frame_observation(envelope, refs, **{**args, 'observed_at': '2026-09-29T12:00:01Z'})
        assert store.stats()['observation_count'] == 1
        store.append_observations([Observation(args['observer'], 'prior-other-kind', '2026-09-29T11:00:00Z',
                                  'other_receipt', frame_reference_hashes(envelope)[0])])
    del envelope, refs
    with PayloadReader(path) as reader:
        observation, = list(reader.iter_observations(kind=market_data_apple.PUBLIC_FRAME_KIND))
        assert observation.token_id is None and observation.event_id is None
        assert set(json.loads(observation.metadata_json)) == {'job', 'public_codec', 'public_raw_bytes', 'public_count'}
        assert 'PRIVATE_' not in observation.metadata_json
        result, = list(market_data_apple.iter_public_frames(reader, observer=args['observer'],
            start='2026-09-29T21:00:00+09:00', end='2026-09-29T12:00:01Z'))
        assert result['objects'] == frame[1]
        assert result['observed_at'] == args['observed_at']
        assert result['frame_id'] == args['frame_id']
        assert len(list(market_data_apple.iter_public_frames(reader, observer=args['observer'], limit=1))) == 1
        assert list(market_data_apple.iter_public_frames(reader, observer='other')) == []
        assert list(market_data_apple.iter_public_frames(reader, end=args['observed_at'])) == []


def test_frame_index_metadata_and_writer_contract(frame):
    from polybot_observability.market_data_store import Observation
    store = MemoryStore()
    refs = PayloadReferences(reader=store, writer=store)
    envelope = externalize_frame(pack(frame[0]), refs)
    header = frame_provenance(envelope)
    metadata = {key: header[key] for key in ('public_codec', 'public_raw_bytes', 'public_count')}
    metadata.update(job='polybot-do', selection='PRIVATE_SELECTION')
    observation = Observation('do', 'frame', '2026-09-29T12:00:00Z', market_data_apple.PUBLIC_FRAME_KIND,
                              frame_reference_hashes(envelope)[0], metadata_json=json.dumps(metadata))
    with pytest.raises(AppleFrameError, match='metadata'):
        market_data_apple.read_public_frame_observation(observation, refs)
    args = dict(observer='do', frame_id='frame', observed_at='2026-09-29T12:00:00Z', job='polybot-do')
    with pytest.raises(AppleFrameError, match='observation writer'):
        market_data_apple.publish_frame_observation(envelope, refs, **args)
    empty = externalize_frame(pack({'format': market_data_apple.ORIGINAL_FORMAT, 'markets': [], 'events': {}, 'books': {}, 'followups': []}), refs)
    assert not market_data_apple.publish_frame_observation(empty, refs, **args)


def budget_database(path):
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA application_id=' + str(market_data_apple.APPLICATION_ID))
    connection.execute('PRAGMA user_version=1')
    connection.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);'
                             'CREATE TABLE runs(slot INTEGER PRIMARY KEY,frame BLOB);')
    connection.execute('INSERT INTO meta VALUES (?,?)', ('format', market_data_apple.ORIGINAL_FORMAT))
    connection.commit()
    return connection


@pytest.mark.parametrize('autocommit', [False, True])
def test_budget_seed_reservation_durable_idempotent(frame, tmp_path, autocommit):
    path = tmp_path / 'month.sqlite'
    store = MemoryStore()
    refs = PayloadReferences(reader=store, writer=store)
    plan = market_data_apple.prepare_frame(pack(frame[0]), refs)
    db = budget_database(path)
    if autocommit:
        db.isolation_level = None
    with pytest.raises(AppleFrameError, match='SEED_REQUIRED'):
        market_data_apple.reserve_frame_storage(db, plan, slot=1, maximum_bytes=1_000_000)
    assert market_data_apple.seed_month_storage_budget(db, refs, 1_000_000)['reserved_bytes'] == 0
    reserved = market_data_apple.reserve_frame_storage(db, plan, slot=1, maximum_bytes=1_000_000)
    assert reserved == plan.public_budget_bytes and store.write_calls == []
    assert market_data_apple.reserve_frame_storage(db, plan, slot=1, maximum_bytes=1_000_000) == reserved
    assert db.execute('PRAGMA max_page_count').fetchone()[0] * 4096 + reserved <= 1_000_000
    db.close()
    db = sqlite3.connect(path)
    # Failed/unknown public write does not refund durable reservation.
    assert market_data_apple.enforce_month_storage_budget(db, 1_000_000) == reserved
    assert not market_data_apple.seed_month_storage_budget(db, refs, 1_000_000)['seeded']
    changed = copy.deepcopy(frame[0])
    changed['audit'] = {'different': True}
    with pytest.raises(AppleFrameError, match='identity conflict'):
        market_data_apple.reserve_frame_storage(db, market_data_apple.prepare_frame(pack(changed), refs),
                                               slot=1, maximum_bytes=1_000_000)
    db.close()


def test_budget_seed_existing_frames_and_missing_dependency(frame, tmp_path):
    store = MemoryStore()
    refs = PayloadReferences(reader=store, writer=store)
    original = pack(frame[0])
    plan = market_data_apple.prepare_frame(original, refs)
    with budget_database(tmp_path / 'month.sqlite') as db:
        db.executemany('INSERT INTO runs VALUES (?,?)', [(1, original), (2, plan.envelope), (3, plan.envelope)])
        db.commit()
        with pytest.raises(MissingPayloadError):
            market_data_apple.seed_month_storage_budget(db, refs, 1_000_000)
        assert db.execute('SELECT count(*) FROM meta').fetchone()[0] == 1
        plan.publish(refs)
        result = market_data_apple.seed_month_storage_budget(db, refs, 1_000_000)
        assert result == {'seeded': True, 'reserved_bytes': plan.public_budget_bytes * 2, 'frames': 2}
        assert db.execute('SELECT frame FROM runs ORDER BY slot').fetchall() == [(original,), (plan.envelope,), (plan.envelope,)]
        assert market_data_apple.reserve_frame_storage(db, plan, slot=2, maximum_bytes=1_000_000) == plan.public_budget_bytes * 2


@pytest.mark.parametrize('autocommit', [False, True])
def test_budget_failure_rolls_back_counter_and_cap(frame, tmp_path, autocommit):
    store = MemoryStore()
    refs = PayloadReferences(reader=store, writer=store)
    plan = market_data_apple.prepare_frame(pack(frame[0]), refs)
    with budget_database(tmp_path / 'month.sqlite') as db:
        if autocommit:
            db.isolation_level = None
        market_data_apple.seed_month_storage_budget(db, refs, 32768)
        cap = db.execute('PRAGMA max_page_count').fetchone()[0]
        with pytest.raises((AppleFrameError, sqlite3.OperationalError)):
            market_data_apple.reserve_frame_storage(db, plan, slot=1, maximum_bytes=32768)
        assert market_data_apple.enforce_month_storage_budget(db, 32768) == 0
        assert db.execute('PRAGMA max_page_count').fetchone()[0] == cap
        assert db.execute("SELECT count(*) FROM meta WHERE key LIKE 'shared_frame_reservation:%'").fetchone()[0] == 0


def test_prepared_frame_rejects_dependency_substitution_before_reservation(frame):
    store = MemoryStore()
    plan = market_data_apple.prepare_frame(pack(frame[0]), PayloadReferences(reader=store, writer=store))
    with pytest.raises(AppleFrameError, match='does not match'):
        market_data_apple.PreparedFrame(plan.envelope, b'wrong public bytes')
    with pytest.raises(AppleFrameError, match='does not match'):
        market_data_apple.PreparedFrame(plan.envelope, None)


@pytest.mark.parametrize('autocommit', [False, True])
def test_budget_seed_rolls_back_partial_metadata_insert(tmp_path, autocommit):
    db = budget_database(tmp_path / 'month.sqlite')
    if autocommit:
        db.isolation_level = None
    db.execute("""CREATE TRIGGER fail_seed BEFORE INSERT ON meta
                  WHEN NEW.key='shared_frame_reserved_bytes'
                  BEGIN SELECT RAISE(ABORT,'synthetic interrupted seed'); END""")
    previous_cap = db.execute('PRAGMA max_page_count').fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match='interrupted seed'):
        market_data_apple.seed_month_storage_budget(db, PayloadReferences(), 1_000_000)
    assert not db.in_transaction
    assert db.execute('SELECT * FROM meta').fetchall() == [('format', market_data_apple.ORIGINAL_FORMAT)]
    assert db.execute('PRAGMA max_page_count').fetchone()[0] == previous_cap
    db.execute('DROP TRIGGER fail_seed')
    assert market_data_apple.seed_month_storage_budget(db, PayloadReferences(), 1_000_000)['seeded']
    db.close()


@pytest.mark.parametrize('operation', ['seed', 'reserve'])
def test_budget_rejects_active_caller_transaction_without_committing_it(frame, tmp_path, operation):
    db = budget_database(tmp_path / 'month.sqlite')
    refs = PayloadReferences()
    plan = market_data_apple.prepare_frame(pack(frame[0]), refs)
    db.execute('BEGIN')
    db.execute("INSERT INTO meta VALUES ('caller_pending','must not commit')")
    with pytest.raises(AppleFrameError, match='transaction'):
        if operation == 'seed':
            market_data_apple.seed_month_storage_budget(db, refs, 1_000_000)
        else:
            market_data_apple.reserve_frame_storage(db, plan, slot=1, maximum_bytes=1_000_000)
    assert db.in_transaction
    db.rollback()
    assert db.execute('SELECT * FROM meta').fetchall() == [('format', market_data_apple.ORIGINAL_FORMAT)]
    db.close()
