"""Exact mixed JSON receipts: explicit public spans, local private byte segments.

This adapter never promotes unknown wrapper keys. JSON whitespace, ordering,
number spelling and escapes are retained as bytes. Membership gzip headers and
the original DEFLATE stream are reproduced exactly. Guava's existing raw_gzip
reference is reused rather than writing another copy of normalized source data.
"""
from __future__ import annotations

import base64
import hashlib
import json
import lzma
import re
from collections.abc import Mapping
import struct
import zlib

from .market_data_refs import PREFIX as PAYLOAD_PREFIX, PayloadReferences, parse_reference


PREFIX = "\x1ePMMIX1:"
FAMILY_PREFIX = "\x1ePMMIX"
HEADER = struct.Struct("!BBBBIIIII32s32s")
PARTS_PREFIX = b"PMMIXPARTS1"
MAX_JSON_BYTES = 64 << 20
MAX_TEMPLATE_BYTES = 2 * MAX_JSON_BYTES
MAX_PACKET_BYTES = 64 << 20
MAX_SPLICES = 1_000_000
XZ_MEMORY_LIMIT = 64 << 20

POMEGRANATE_PUBLIC = frozenset({
    "condition_id", "market_id", "event_id", "event_slug", "market_slug", "question",
    "outcomes", "clob_token_ids", "tags", "category", "sports", "start_date", "end_date",
    "game_start_time", "tick_size", "min_order_size",
})
STRAWBERRY_METADATA_PUBLIC = frozenset({"timestamp", "tick_size", "min_order_size", "market", "hash"})
RASPBERRY_MEMBERSHIP_PUBLIC = frozenset({
    "source_market_key", "condition_id", "market_id", "raw_market_sha256", "event_id",
})
STRAWBERRY_MEMBERSHIP_PUBLIC = frozenset({"condition_id", "market_id", "token_ids", "raw_market_sha256"})
SPORTS_MARKET_PUBLIC = frozenset({
    "conditionId", "liquidityNum", "liquidity", "volumeNum", "volume", "volume24hr",
    "active", "closed", "enableOrderBook", "acceptingOrders", "updatedAt",
})
WATERMELON_NORMALIZED_PUBLIC = frozenset({
    "event_id", "condition_id", "labels", "tokens", "probabilities", "end_date",
    "game_start_time", "liquidity", "volume_total", "neg_risk", "sports_market_type",
    "event_live", "event_ended", "event_game_status",
})
WATERMELON_EVENT_CLASSIFICATION_PUBLIC = frozenset({
    "sport_id", "sport_code", "sport_name", "sport_primary_tag_id", "sport_series_id",
    "sport_tag_ids", "series_slug", "tag_ids", "tag_slugs", "series_ids",
    "series_slugs", "team_leagues", "team_count",
})
WATERMELON_MARKET_CLASSIFICATION_PUBLIC = frozenset({
    "sports_market_type", "neg_risk", "group_item_title", "team_names", "team_aliases",
    "outcome_labels", "description_sha256",
})
COCONUT_SOURCE_METRICS = frozenset({"volume_num", "volume_24hr", "liquidity", "liquidity_num"})
COCONUT_EVENT_CLASSIFICATION_PUBLIC = frozenset({
    "event_id", "canonical_game_slug", "game_id", "sport_id", "sport_code", "sport_name",
    "sport_primary_tag_id", "sport_root_id", "sport_tag_ids", "tag_ids", "tag_slugs",
    "series_ids", "series_slugs", "series_slug", "team_count", "team_leagues",
    "scheduled_start_raw", "raw_lifecycle_json",
})
COCONUT_SERIES_ITEM_PUBLIC = frozenset({"id", "slug", "ticker", "title", "series_type", "recurrence"})
COCONUT_MARKET_CLASSIFICATION_PUBLIC = frozenset({"sports_market_type", "neg_risk", "labels", "token_ids"})
RECORDER_SLOT_SOURCE = frozenset({
    "condition_id", "token_id", "outcome", "team_name", "question",
    "group_item_title", "all_tokens", "all_outcomes",
})
RECORDER_TERMINAL_SOURCE = frozenset({"condition_id", "token_id", "outcome", "payout"})
WATERMELON_SLOT_SOURCE = frozenset({"condition_id", "token_id", "outcome", "all_tokens", "all_outcomes"})
POMEGRANATE_PARSE_FIELDS = frozenset({
    "outcomes", "clobTokenIds", "outcomePrices", "tags", "volume", "volume24h",
    "volume1h", "volumeWeek", "volumeMonth", "volumeYear", "liquidity", "bestBid",
    "bestAsk", "spread", "lastTradePrice", "active", "closed", "enableOrderBook",
    "acceptingOrders", "negRisk", "feesEnabled",
})
POMEGRANATE_PRIOR_SOURCE = frozenset({"closed", "resolution_value_raw", "redeemable", "one_hot_outcome_label"})
STRAWBERRY_EPISODE_SOURCE = frozenset({
    "condition_id", "market_id", "event_id", "event_cluster_id", "token_id",
    "outcome_index", "outcome_label", "outcome_type", "neg_risk",
    "crossing_prior_probability", "crossing_probability", "best_ask", "spread",
    "ask_depth_notional", "source_tick_size", "source_min_order_size",
    "source_fee_rate_bps", "liquidity", "volume_total", "volume_24h", "end_date",
    "category", "tags_json",
})
GUAVA_EVENT_SOURCE = frozenset({"event_id", "sport_family", "league_code", "expected_token_ids"})
GUAVA_SIDE_SOURCE = frozenset({"condition_id", "token_id", "partition_key", "outcome_label", "tradable_flags"})
GUAVA_NEWS_SOURCE = frozenset({
    "provider", "provider_game_id", "sport_family", "league_code", "scheduled_at",
    "status", "period", "clock", "raw_snapshot", "end_wallclock", "provider_updated_at",
    "end_wallclock_source_field", "provider_updated_at_source_field", "snapshot_sha256",
    "projection_scope", "sha256_scope",
})
GUAVA_NEWS_MATCH_SOURCE = frozenset({
    "venue_event_id", "sport_family", "league_code", "venue_scheduled_at", "provider_scheduled_at",
})


def _guava_news_source(path):
    return (len(path) == 1 and path[0] in GUAVA_NEWS_SOURCE) or (
        len(path) == 2 and (
            path[0] in {"home", "away"} and path[1] in {"team_id", "name", "score"}
            or path[0] == "matching_evidence" and path[1] in GUAVA_NEWS_MATCH_SOURCE))


def _guava_metrics_source(path):
    return (len(path) == 1 and path[0] in {"score", "previous_score"}) or (
        len(path) == 2 and path[0] == "reported_book_timestamps" and isinstance(path[1], str))


def _pomegranate_parse_field(value):
    return isinstance(value, str) and (value in POMEGRANATE_PARSE_FIELDS or
        re.fullmatch(r"outcomePrices\[(?:0|[1-9][0-9]*)\]", value) is not None)
PROFILES = {
    ("golden-guava", "events"): (25, "event_json"),
    ("golden-guava", "book_attempts"): (26, "book_json"),
    ("golden-guava", "cycles"): (27, "summary_json"),
    ("golden-guava", "features"): (28, "metrics_json"),
    ("golden-raspberry", "market_sweeps"): (3, "membership_blob"),
    ("golden-strawberry", "market_membership_blobs"): (4, "membership_blob"),
    ("golden-pomegranate", "market_metadata_versions"): (10, "metadata_json"),
    ("golden-strawberry", "clob_snapshots"): (11, "source_metadata_json"),
    ("golden-coconut", "book_observations"): (12, "fee_json"),
    ("golden-coconut", "tracked_events"): (30, "slots_json"),
    ("golden-coconut", "registry_carryovers"): (32, "state_json"),
    ("golden-black", "market_observations"): (14, "normalized_json"),
    ("golden-watermelon", "market_observations"): (15, "normalized_json"),
    ("golden-watermelon", "event_observations"): (16, "classification_evidence_json"),
    ("golden-watermelon", "raw_tracked_events"): (33, "slots_json"),
    ("golden-watermelon", "raw_events"): (33, "slots_json"),
    ("golden-coconut", "event_observations"): (18, "normalized_json"),
    ("golden-coconut", "market_observations"): (19, "normalized_json"),
    ("golden-pomegranate", "market_observations"): (22, "parse_quality_json"),
    ("golden-pomegranate", "resolution_watchlist"): (23, "prior_state_json"),
    ("golden-strawberry", "imported_episodes"): (24, "episode_json"),
    **{(strategy, "raw_event_observations"): (7, "evidence_json")
       for strategy in ("golden-apricot", "golden-peach", "golden-plum")},
}


# Profile IDs are persisted lexical contracts: never broaden an existing ID.
SECONDARY_PROFILES = {
    ("golden-guava", "book_attempts"): ((13, "fee_evidence_json"),),
    ("golden-watermelon", "market_observations"): ((17, "classification_evidence_json"),),
    ("golden-watermelon", "raw_events"): ((34, "terminal_json"),),
    ("golden-coconut", "event_observations"): ((20, "classification_evidence_json"), (30, "slots_json"), (31, "terminal_json")),
    ("golden-coconut", "tracked_events"): ((31, "terminal_json"),),
    ("golden-coconut", "market_observations"): ((21, "classification_evidence_json"),),
    ("golden-guava", "features"): ((29, "feature_json"),),
}
LEGACY_PROFILES = {
    ("golden-guava", "events", "event_json"): (1, 8),
    ("golden-guava", "book_attempts", "book_json"): (2, 9),
    ("golden-pomegranate", "market_metadata_versions", "metadata_json"): (5,),
    ("golden-strawberry", "clob_snapshots", "source_metadata_json"): (6,),
}
KNOWN_PROFILE_IDS = frozenset(range(1, 35))


def current_profiles(strategy: str, table: str) -> tuple[tuple[int, str], ...]:
    primary = PROFILES.get((strategy, table))
    return (() if primary is None else (primary,)) + SECONDARY_PROFILES.get((strategy, table), ())


def mixed_columns(strategy: str, table: str) -> frozenset[str]:
    return frozenset(column for _, column in current_profiles(strategy, table))


def current_profile_id(strategy: str, table: str, column: str) -> int | None:
    return next((profile for profile, name in current_profiles(strategy, table) if name == column), None)


def supported_profile_ids(strategy: str, table: str, column: str) -> frozenset[int]:
    current = current_profile_id(strategy, table, column)
    return frozenset() if current is None else frozenset((current, *LEGACY_PROFILES.get((strategy, table, column), ())))


class MixedPayloadError(ValueError):
    pass


def _sha(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def _fail_constant(_):
    raise MixedPayloadError("nonfinite mixed JSON constant")


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise MixedPayloadError("duplicate mixed JSON object key")
        result[key] = value
    return result


_DECODER = json.JSONDecoder(parse_constant=_fail_constant, object_pairs_hook=_pairs)


def _decoded_at(text: str, offset: int, depth: int):
    try:
        value, end = _DECODER.raw_decode(text, offset)
    except (ValueError, RecursionError) as error:
        raise MixedPayloadError("invalid mixed JSON") from error
    stack = [(value, depth)]
    while stack:
        item, level = stack.pop()
        if level > 128:
            raise MixedPayloadError("mixed JSON nesting exceeds bound")
        if isinstance(item, dict):
            stack.extend((child, level + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, level + 1) for child in item)
    return value, end


def _selected(profile: int, path: tuple) -> bool:
    if profile == 33:
        return len(path) == 2 and isinstance(path[0], int) and path[1] in WATERMELON_SLOT_SOURCE
    if profile == 34:
        return len(path) == 3 and path[0] == 'token_payouts' and isinstance(path[1], int) and path[2] in {'condition_id','token_id','payout'}
    if profile == 30:
        return len(path) == 2 and isinstance(path[0], int) and path[1] in RECORDER_SLOT_SOURCE
    if profile == 31:
        return len(path) == 3 and path[0] == "tokens" and isinstance(path[1], int) and path[2] in RECORDER_TERMINAL_SOURCE
    if profile == 32:
        return len(path) == 1 and path[0] in {"event_id", "scheduled_start"}
    if profile == 25:
        return (path in (("raw",), ("clock",), ("rules_evidence",))
                or len(path) == 1 and path[0] in GUAVA_EVENT_SOURCE
                or len(path) == 3 and path[0] == "side_definitions" and isinstance(path[1], int)
                   and path[2] in GUAVA_SIDE_SOURCE
                or path[:1] == ("official_news",) and _guava_news_source(path[1:]))
    if profile == 26:
        return (path in (("raw",), ("fee_evidence", "raw"))
                or len(path) == 1 and path[0] in GUAVA_SIDE_SOURCE | {"event_id"})
    if profile == 27:
        return (len(path) == 3 and path[0] == "tracking" and isinstance(path[1], str)
                and path[2] in {"sport_family", "expected_token_ids"}
                or len(path) >= 3 and path[0] == "news_context" and isinstance(path[1], int)
                and _guava_news_source(path[2:]))
    if profile == 28:
        return _guava_metrics_source(path)
    if profile == 29:
        return path[:1] == ("metrics",) and _guava_metrics_source(path[1:])
    if profile in (1, 2):
        return path == ("raw",)
    if profile in (3, 4):
        allowed = RASPBERRY_MEMBERSHIP_PUBLIC if profile == 3 else STRAWBERRY_MEMBERSHIP_PUBLIC
        return len(path) == 2 and isinstance(path[0], int) and path[1] in allowed
    if profile in (5, 10):
        allowed = POMEGRANATE_PUBLIC | ({"fee_metadata"} if profile == 10 else set())
        return len(path) == 1 and path[0] in allowed
    if profile in (6, 11):
        allowed = STRAWBERRY_METADATA_PUBLIC | ({"fee_rate_bps"} if profile == 11 else set())
        return len(path) == 1 and path[0] in allowed
    if profile == 8:
        return path in (("raw",), ("clock",), ("rules_evidence",))
    if profile == 9:
        return path in (("raw",), ("fee_evidence", "raw"))
    if profile == 12:
        return path == ("fields",)
    if profile == 13:
        return path == ("raw",)
    if profile == 14:
        return len(path) == 1 and path[0] in {"event_id", "condition_id", "labels", "tokens", "probabilities", "end_date", "game_start_time", "liquidity", "volume_total", "neg_risk"}
    if profile in (15, 16, 17):
        allowed = {15: WATERMELON_NORMALIZED_PUBLIC,
                   16: WATERMELON_EVENT_CLASSIFICATION_PUBLIC,
                   17: WATERMELON_MARKET_CLASSIFICATION_PUBLIC}[profile]
        return len(path) == 1 and path[0] in allowed
    if profile in (18, 19):
        return ((len(path) == 1 and path[0] in COCONUT_SOURCE_METRICS) or
                (profile == 19 and len(path) == 2 and path[0] == "event_metrics"
                 and path[1] in COCONUT_SOURCE_METRICS))
    if profile == 20:
        return ((len(path) == 1 and path[0] in COCONUT_EVENT_CLASSIFICATION_PUBLIC) or
                (len(path) == 3 and path[0] == "series_items" and isinstance(path[1], int)
                 and path[2] in COCONUT_SERIES_ITEM_PUBLIC))
    if profile == 21:
        return len(path) == 1 and path[0] in COCONUT_MARKET_CLASSIFICATION_PUBLIC
    if profile == 22:
        return len(path) == 2 and _pomegranate_parse_field(path[0]) and path[1] == "raw_preview"
    if profile == 23:
        return len(path) == 1 and path[0] in POMEGRANATE_PRIOR_SOURCE
    if profile == 24:
        return len(path) == 1 and path[0] in STRAWBERRY_EPISODE_SOURCE
    if profile != 7:
        raise MixedPayloadError("unknown mixed ownership profile")
    return path == ("event",) or (
        len(path) == 3 and path[0] == "market_context" and isinstance(path[1], int)
        and path[2] in SPORTS_MARKET_PUBLIC
    )


def _descend(profile: int, path: tuple) -> bool:
    if profile == 33:
        return not path or len(path) == 1 and isinstance(path[0], int)
    if profile == 34:
        return not path or path == ('token_payouts',) or len(path) == 2 and path[0] == 'token_payouts' and isinstance(path[1], int)
    if profile == 30:
        return not path or len(path) == 1 and isinstance(path[0], int)
    if profile == 31:
        return not path or path == ("tokens",) or len(path) == 2 and path[0] == "tokens" and isinstance(path[1], int)
    if profile == 32:
        return not path
    if profile == 25:
        return (not path or path == ("side_definitions",)
                or len(path) == 2 and path[0] == "side_definitions" and isinstance(path[1], int)
                or path == ("official_news",)
                or len(path) == 2 and path[0] == "official_news" and path[1] in {"home", "away", "matching_evidence"})
    if profile == 26:
        return not path or path == ("fee_evidence",)
    if profile == 27:
        return (not path or path in (("tracking",), ("news_context",))
                or len(path) == 2 and (path[0] == "tracking" and isinstance(path[1], str)
                                       or path[0] == "news_context" and isinstance(path[1], int))
                or len(path) == 3 and path[0] == "news_context" and isinstance(path[1], int)
                   and path[2] in {"home", "away", "matching_evidence"})
    if profile == 28:
        return not path or path == ("reported_book_timestamps",)
    if profile == 29:
        return not path or path in (("metrics",), ("metrics", "reported_book_timestamps"))
    return (not path or (profile in (3, 4) and len(path) == 1)
            or (profile == 9 and path == ("fee_evidence",))
            or (profile == 19 and path == ("event_metrics",))
            or (profile == 22 and len(path) == 1 and _pomegranate_parse_field(path[0]))
            or (profile == 20 and (path == ("series_items",) or
                                  (len(path) == 2 and path[0] == "series_items" and isinstance(path[1], int))))
            or (profile == 7 and path == ("market_context",))
            or (profile == 7 and len(path) == 2 and path[0] == "market_context"))


def _embedded_json_spans(text, start, end, decoded, profile):
    """Map approved inner values to original outer JSON string escapes.

    Carryovers hash json.dumps(original_row), whose slots/terminal fields are
    JSON strings. Neither layer may be re-encoded; retain surrogate pairs and
    every original escape while selecting only reviewed inner source values.
    """
    boundaries = [start + 1]
    index = start + 1
    while index < end - 1:
        if text[index] == "\\":
            if text[index + 1] == "u":
                code = int(text[index + 2:index + 6], 16)
                width = 6
                if (0xD800 <= code <= 0xDBFF and text[index + 6:index + 8] == "\\u"
                        and 0xDC00 <= int(text[index + 8:index + 12], 16) <= 0xDFFF):
                    width = 12
            else:
                width = 2
        else:
            width = 1
        index += width
        boundaries.append(index)
    if index != end - 1 or len(boundaries) != len(decoded) + 1:
        raise MixedPayloadError("nested recorder JSON escape boundaries differ")
    return [(boundaries[a], boundaries[b]) for a, b in _public_spans(decoded, profile)]


def _public_spans(text: str, profile: int):
    """Locate only approved JSON values, keeping all other source text verbatim."""
    spans = []
    size = len(text)

    def whitespace(index):
        while index < size and text[index] in " \t\r\n":
            index += 1
        return index

    def visit(index, path):
        index = whitespace(index)
        if index >= size:
            raise MixedPayloadError("truncated mixed JSON")
        if profile == 32 and path in (("slots_json",), ("terminal_json",)):
            value, end = _decoded_at(text, index, len(path))
            if value is None and path == ("terminal_json",):
                return end
            if not isinstance(value, str):
                raise MixedPayloadError("recorder carryover requires original nested JSON strings")
            spans.extend(_embedded_json_spans(text, index, end, value,
                                             30 if path == ("slots_json",) else 31))
            if len(spans) > MAX_SPLICES:
                raise MixedPayloadError("mixed JSON public span count exceeds bound")
            return end
        if _selected(profile, path):
            value, end = _decoded_at(text, index, len(path))
            if path in (("raw",), ("event",)):
                if value is None:
                    return end
                if not isinstance(value, dict):
                    raise MixedPayloadError("source object has unexpected JSON type")
            spans.append((index, end))
            if len(spans) > MAX_SPLICES:
                raise MixedPayloadError("mixed JSON public span count exceeds bound")
            return end
        if not _descend(profile, path):
            return _decoded_at(text, index, len(path))[1]
        # These reviewed source envelopes can be explicitly missing. Keep the
        # original null in the private template instead of inventing an object.
        if profile in (25, 26, 27, 28, 29) and path and text[index:index + 4] == "null":
            return _decoded_at(text, index, len(path))[1]
        expected = "[" if ((not path and profile in (3, 4, 30, 33)) or path == ("market_context",)
                           or (profile == 31 and path == ("tokens",))
                           or (profile == 34 and path == ("token_payouts",))
                           or (profile == 20 and path == ("series_items",))
                           or (profile == 25 and path == ("side_definitions",))
                           or (profile == 27 and path == ("news_context",))) else "{"
        if text[index] != expected:
            raise MixedPayloadError("unexpected mixed JSON container shape")
        index = whitespace(index + 1)
        closing = "]" if expected == "[" else "}"
        if index < size and text[index] == closing:
            return index + 1
        ordinal, keys = 0, set()
        while True:
            if expected == "{":
                key, index = _decoded_at(text, index, len(path))
                if not isinstance(key, str) or key in keys:
                    raise MixedPayloadError("invalid or duplicate mixed JSON key")
                keys.add(key)
                index = whitespace(index)
                if index >= size or text[index] != ":":
                    raise MixedPayloadError("mixed JSON object colon is missing")
                index += 1
            else:
                key = ordinal
                ordinal += 1
            index = whitespace(visit(index, (*path, key)))
            if index >= size:
                raise MixedPayloadError("truncated mixed JSON container")
            if text[index] == closing:
                return index + 1
            if text[index] != ",":
                raise MixedPayloadError("invalid mixed JSON separator")
            index = whitespace(index + 1)

    end = whitespace(visit(0, ()))
    if end != size:
        raise MixedPayloadError("trailing mixed JSON data")
    return spans


def _inflate(blob: bytes, limit: int, *, gzip=False, dictionary=None) -> bytes:
    try:
        decoder = (zlib.decompressobj(31 if gzip else 15, zdict=dictionary)
                   if dictionary is not None else zlib.decompressobj(31 if gzip else 15))
        raw = decoder.decompress(blob, limit + 1)
    except zlib.error as error:
        raise MixedPayloadError("invalid compressed mixed payload") from error
    if len(raw) > limit or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise MixedPayloadError("compressed mixed payload is oversized or has trailing data")
    return raw


def _xz(blob: bytes, limit: int) -> bytes:
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=XZ_MEMORY_LIMIT)
        raw = decoder.decompress(blob, max_length=limit + 1)
    except lzma.LZMAError as error:
        raise MixedPayloadError("invalid mixed XZ payload") from error
    if len(raw) > limit or not decoder.eof or decoder.unused_data:
        raise MixedPayloadError("mixed XZ payload is oversized or has trailing data")
    return raw


def _deflate(raw: bytes, level: int) -> bytes:
    encoder = zlib.compressobj(level, wbits=-15)
    return encoder.compress(raw) + encoder.flush()


def _gzip_material(blob: bytes):
    if len(blob) < 18 or blob[:3] != b"\x1f\x8b\x08" or blob[3] & 0xe0:
        raise MixedPayloadError("unsupported membership gzip header")
    raw = _inflate(blob, MAX_JSON_BYTES, gzip=True)
    index = 10
    flags = blob[3]
    if flags & 4:
        if index + 2 > len(blob) - 8:
            raise MixedPayloadError("truncated gzip extra field")
        index += 2 + int.from_bytes(blob[index:index + 2], "little")
    for flag in (8, 16):
        if flags & flag:
            end = blob.find(b"\0", index, min(len(blob) - 8, index + 65536))
            if end < 0:
                raise MixedPayloadError("unbounded gzip filename or comment")
            index = end + 1
    if flags & 2:
        index += 2
    if index > len(blob) - 8 or index > 65536:
        raise MixedPayloadError("invalid gzip header extent")
    return raw, blob[:index], blob[-8:], blob[index:-8]


def _gzip_source(blob: bytes):
    raw, header, footer, compressed = _gzip_material(blob)
    for level in (6, 9, 1, 0, 2, 3, 4, 5, 7, 8):
        if _deflate(raw, level) == compressed:
            return raw, level + 1, header, footer
    raise MixedPayloadError("original gzip DEFLATE bytes cannot be reproduced")


def _chunk(value: bytes) -> bytes:
    return struct.pack("!I", len(value)) + value


def _parts(fragments: list[bytes]) -> bytes:
    return PARTS_PREFIX + struct.pack("!I", len(fragments)) + b"".join(_chunk(x) for x in fragments)


def _take(raw: bytes, index: int):
    if index + 4 > len(raw):
        raise MixedPayloadError("truncated mixed length field")
    length = struct.unpack_from("!I", raw, index)[0]
    index += 4
    if index + length > len(raw):
        raise MixedPayloadError("truncated mixed byte field")
    return raw[index:index + length], index + length


def _read_parts(raw: bytes, expected: int, *, escaped_json=False) -> list[bytes]:
    offset = len(PARTS_PREFIX)
    if not raw.startswith(PARTS_PREFIX) or len(raw) < offset + 4:
        raise MixedPayloadError("unsupported mixed public aggregate")
    count = struct.unpack_from("!I", raw, offset)[0]
    if count != expected or not 0 < count <= MAX_SPLICES:
        raise MixedPayloadError("mixed public fragment count mismatch")
    offset += 4
    parts = []
    for _ in range(count):
        part, offset = _take(raw, offset)
        try:
            text = part.decode("utf-8")
        except UnicodeError as error:
            raise MixedPayloadError("public JSON fragment is not UTF-8") from error
        try:
            _, end = _decoded_at(text, 0, 0)
        except MixedPayloadError:
            if not escaped_json:
                raise
            # Profile 32 preserves inner source tokens with their exact outer
            # JSON-string escapes. Validate one decoded string layer without
            # changing the bytes used by its independently checked splice map.
            text, consumed = _decoded_at('"' + text + '"', 0, 0)
            if not isinstance(text, str) or consumed != len(part.decode('utf-8')) + 2:
                raise MixedPayloadError('nested public JSON fragment is not one escaped token')
            _, end = _decoded_at(text, 0, 0)
        if end != len(text):
            raise MixedPayloadError("public JSON fragment has trailing data")
        parts.append(part)
    if offset != len(raw):
        raise MixedPayloadError("mixed public aggregate has trailing bytes")
    return parts


def is_mixed_payload(value: object) -> bool:
    return ((isinstance(value, str) and value.startswith(FAMILY_PREFIX))
            or (isinstance(value, bytes) and value.startswith(FAMILY_PREFIX.encode())))


def _packet(value) -> dict:
    try:
        if isinstance(value, str) and value.startswith(PREFIX):
            if len(value) > 2 * MAX_PACKET_BYTES:
                raise MixedPayloadError("mixed TEXT packet exceeds bound")
            packet = base64.b64decode(value[len(PREFIX):], validate=True)
            text_type = True
        elif isinstance(value, bytes) and value.startswith(PREFIX.encode()):
            packet = value[len(PREFIX):]
            text_type = False
        else:
            raise MixedPayloadError("unsupported mixed payload version or type")
    except (ValueError, UnicodeError) as error:
        raise MixedPayloadError("invalid mixed TEXT packet encoding") from error
    if not HEADER.size + 32 < len(packet) <= MAX_PACKET_BYTES:
        raise MixedPayloadError("mixed packet exceeds bounds")
    if _sha(PREFIX.encode() + packet[:-32]) != packet[-32:]:
        raise MixedPayloadError("mixed packet checksum mismatch")
    fields = HEADER.unpack_from(packet)
    profile, original_codec, public_codec, private_codec, original_size, json_size, template_size, public_size, count, original_sha, public_sha = fields
    if (profile not in KNOWN_PROFILE_IDS or original_codec not in range(11)
            or (original_codec == 0) != text_type or public_codec not in range(4)
            or private_codec not in (1, 2, 3) or not 0 < original_size <= MAX_JSON_BYTES
            or not 0 < json_size <= MAX_JSON_BYTES or not 0 < template_size <= MAX_TEMPLATE_BYTES
            or not 0 < public_size <= MAX_TEMPLATE_BYTES or not 0 < count <= MAX_SPLICES):
        raise MixedPayloadError("invalid mixed packet header")
    if public_codec == 3 and (profile not in (1, 2, 8, 9, 25, 26) or count != 1):
        raise MixedPayloadError("invalid borrowed public payload recipe")
    return {"profile": profile, "original_codec": original_codec, "public_codec": public_codec,
            "private_codec": private_codec, "original_bytes": original_size,
            "json_bytes": json_size, "template_bytes": template_size,
            "public_bytes": public_size, "public_count": count,
            "original_sha256": original_sha.hex(), "public_sha256": public_sha.hex(),
            "private_body": packet[HEADER.size:-32], "text_type": text_type}


def mixed_reference_hashes(value) -> list[str]:
    return [_packet(value)["public_sha256"]] if is_mixed_payload(value) else []


def _public(header, references):
    marker = (PAYLOAD_PREFIX + "B:" + header["public_sha256"]).encode()
    body = references.decode_many([marker])[0]
    if not isinstance(body, bytes) or len(body) > MAX_JSON_BYTES:
        raise MixedPayloadError("mixed public dependency must be bounded BLOB bytes")
    codec = header["public_codec"]
    raw = (body if codec == 0 else _inflate(body, MAX_TEMPLATE_BYTES) if codec == 1
           else _xz(body, MAX_TEMPLATE_BYTES) if codec == 2
           else _inflate(body, MAX_JSON_BYTES, gzip=True))
    if len(raw) != header["public_bytes"]:
        raise MixedPayloadError("mixed public dependency length mismatch")
    if codec == 3:
        try:
            text = raw.decode("utf-8")
        except UnicodeError as error:
            raise MixedPayloadError("borrowed Guava source is not UTF-8") from error
        source, end = _decoded_at(text, 0, 0)
        if not isinstance(source, dict) or end != len(text):
            raise MixedPayloadError("borrowed Guava source is not an exact JSON object")
        fragments = [raw]
    else:
        fragments = _read_parts(raw, header["public_count"], escaped_json=header["profile"] == 32)
    return fragments, raw, body


def inspect_mixed_payload(value, references) -> dict:
    header = _packet(value)
    fragments, _, body = _public(header, references)
    local_bytes = len(value.encode() if isinstance(value, str) else value)
    additional_public = 0 if header["public_codec"] == 3 else len(body)
    return {**{k: v for k, v in header.items() if k != "private_body"},
            "local_bytes": local_bytes, "public_payload_bytes": len(body),
            "incremental_public_bytes_upper_bound": additional_public,
            "component_delta_upper_bound": local_bytes + additional_public - header["original_bytes"],
            "public_reused": header["public_codec"] == 3, "public_fragments": fragments}


def resolve_mixed_payload(value, references):
    if not is_mixed_payload(value):
        return value
    header = _packet(value)
    fragments, public_raw, _ = _public(header, references)
    codec = header["private_codec"]
    template = (_xz(header["private_body"], MAX_TEMPLATE_BYTES) if codec == 2 else
                _inflate(header["private_body"], MAX_TEMPLATE_BYTES,
                         dictionary=public_raw[-32768:] if codec == 3 else None))
    if len(template) != header["template_bytes"]:
        raise MixedPayloadError("mixed private template length mismatch")
    gzip_header, offset = _take(template, 0)
    gzip_footer, offset = _take(template, offset)
    if offset + 4 > len(template):
        raise MixedPayloadError("mixed splice count is missing")
    count = struct.unpack_from("!I", template, offset)[0]
    offset += 4
    if not 0 < count <= MAX_SPLICES:
        raise MixedPayloadError("mixed splice count exceeds bound")
    output, total, used = [], 0, set()
    for _ in range(count):
        before, offset = _take(template, offset)
        if offset + 4 > len(template):
            raise MixedPayloadError("mixed public index is truncated")
        index = struct.unpack_from("!I", template, offset)[0]
        offset += 4
        if index >= len(fragments):
            raise MixedPayloadError("mixed public index is invalid")
        used.add(index)
        total += len(before) + len(fragments[index])
        if total > header["json_bytes"]:
            raise MixedPayloadError("mixed index expansion exceeds original size")
        output.extend((before, fragments[index]))
    tail, offset = _take(template, offset)
    total += len(tail)
    if (offset != len(template) or total != header["json_bytes"]
            or used != set(range(len(fragments)))):
        raise MixedPayloadError("mixed template reconstruction mismatch")
    raw = b"".join([*output, tail])
    if header["original_codec"]:
        if not gzip_header.startswith(b"\x1f\x8b\x08") or len(gzip_footer) != 8:
            raise MixedPayloadError("invalid retained gzip envelope")
        restored = gzip_header + _deflate(raw, header["original_codec"] - 1) + gzip_footer
    else:
        if gzip_header or gzip_footer:
            raise MixedPayloadError("TEXT payload cannot have a gzip envelope")
        restored = raw
    if len(restored) != header["original_bytes"] or hashlib.sha256(restored).hexdigest() != header["original_sha256"]:
        raise MixedPayloadError("restored mixed payload checksum mismatch")
    return restored.decode("utf-8") if header["text_type"] else restored


def verify_mixed_ownership(strategy: str, table: str, column: str, original, encoded,
                           references: PayloadReferences) -> bool:
    """Prove a mixed cell shares only its original, approved lexical JSON spans.

    Restored equality alone cannot prove ownership: an envelope can relocate a
    splice into a private field containing the same bytes, or upload the entire
    wrapper. Compare the independently derived fragments AND local template.
    Compression recipes may vary; their decompressed materials must match.
    """
    supported = supported_profile_ids(strategy, table, column)
    if not supported or not is_mixed_payload(encoded):
        return False
    refs = PayloadReferences(reader=references.reader, cache_bytes=0)

    def verify(value, source):
        header = _packet(value)
        profile = header["profile"]
        if (profile not in supported
                or type(value) is not type(source)
                or isinstance(source, bytes) != (profile in (3, 4))):
            raise MixedPayloadError("mixed ownership profile or SQLite type mismatch")
        if isinstance(source, str):
            raw, gzip_header, gzip_footer = source.encode("utf-8"), b"", b""
        elif isinstance(source, bytes):
            raw, gzip_header, gzip_footer, _ = _gzip_material(source)
        else:
            raise MixedPayloadError("mixed ownership requires original TEXT or gzip BLOB")
        if not 0 < len(raw) <= MAX_JSON_BYTES:
            raise MixedPayloadError("mixed ownership source exceeds byte limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeError as error:
            raise MixedPayloadError("mixed ownership source is not UTF-8") from error
        spans = _public_spans(text, profile)
        if not spans:
            raise MixedPayloadError("mixed ownership has no approved public spans")
        expected_fragments, indexes, pieces = [], {}, []
        last = 0
        for start, end in spans:
            fragment = text[start:end].encode("utf-8")
            if fragment not in indexes:
                indexes[fragment] = len(expected_fragments)
                expected_fragments.append(fragment)
            pieces.append((text[last:start].encode("utf-8"), indexes[fragment]))
            last = end
        expected_template = (
            _chunk(gzip_header) + _chunk(gzip_footer) + struct.pack("!I", len(pieces))
            + b"".join(_chunk(before) + struct.pack("!I", index) for before, index in pieces)
            + _chunk(text[last:].encode("utf-8"))
        )
        fragments, public_raw, _ = _public(header, refs)
        if fragments != expected_fragments:
            raise MixedPayloadError("mixed ownership public fragments differ from approved spans")
        codec = header["private_codec"]
        template = (
            _xz(header["private_body"], MAX_TEMPLATE_BYTES) if codec == 2 else
            _inflate(header["private_body"], MAX_TEMPLATE_BYTES,
                     dictionary=public_raw[-32768:] if codec == 3 else None)
        )
        if template != expected_template or len(template) != header["template_bytes"]:
            raise MixedPayloadError("mixed ownership private template or splice positions changed")
        if resolve_mixed_payload(value, refs) != source:
            raise MixedPayloadError("mixed ownership original bytes changed")

    if is_mixed_payload(original):
        source = resolve_mixed_payload(original, refs)
        verify(original, source)
    else:
        source = original
    verify(encoded, source)
    return True


def _prepare(profile, value, original_row, encoded_row, references):
    if isinstance(value, str):
        original = value.encode("utf-8")
        raw, original_codec, gzip_header, gzip_footer = original, 0, b"", b""
        if profile in (3, 4):
            raise MixedPayloadError("membership must retain gzip BLOB type")
    elif isinstance(value, bytes) and profile in (3, 4):
        original = value
        raw, original_codec, gzip_header, gzip_footer = _gzip_source(value)
        expected = original_row.get("membership_sha256")
        if expected is not None and hashlib.sha256(raw).hexdigest() != expected:
            raise MixedPayloadError("membership source digest mismatch")
    else:
        raise MixedPayloadError("unexpected mixed source SQLite type")
    if not 0 < len(original) <= MAX_JSON_BYTES or len(raw) > MAX_JSON_BYTES:
        raise MixedPayloadError("mixed source exceeds byte limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise MixedPayloadError("mixed source JSON is not UTF-8") from error
    spans = _public_spans(text, profile)
    if not spans:
        return None
    fragments, known, pieces = [], {}, []
    last = 0
    for start, end in spans:
        fragment = text[start:end].encode("utf-8")
        if fragment not in known:
            known[fragment] = len(fragments)
            fragments.append(fragment)
        pieces.append((text[last:start].encode("utf-8"), known[fragment]))
        last = end
    public_raw = _parts(fragments)
    public_codec, public_body, reused = 0, public_raw, False
    if profile in (1, 2, 8, 9, 25, 26):
        sibling = encoded_row.get("raw_gzip")
        reference = parse_reference(sibling)
        if reference is not None:
            if reference[0] != "B":
                raise MixedPayloadError("raw_gzip must retain BLOB reference type")
            body = references.decode_many([sibling])[0]
            supplied = original_row.get("raw_gzip")
            if parse_reference(supplied):
                supplied = references.decode_many([supplied])[0]
            if body != supplied:
                raise MixedPayloadError("Guava raw sibling bytes mismatch")
            normalized = _inflate(body, MAX_JSON_BYTES, gzip=True)
            raw_spans = _public_spans(text, 1)
            if len(raw_spans) != 1 or text[raw_spans[0][0]:raw_spans[0][1]].encode("utf-8") != normalized:
                raise MixedPayloadError("Guava raw JSON is not the same exact normalized source")
            expected = original_row.get("raw_sha256")
            if expected is not None and hashlib.sha256(normalized).hexdigest() != expected:
                raise MixedPayloadError("Guava normalized source digest mismatch")
            if len(fragments) == 1:
                public_codec, public_body, public_raw, reused = 3, body, normalized, True
    if not reused:
        public_codec, public_body = min(
            ((0, public_raw), (1, zlib.compress(public_raw, 9)),
             (2, lzma.compress(public_raw, format=lzma.FORMAT_XZ, preset=6))),
            key=lambda item: len(item[1]),
        )
    template = (_chunk(gzip_header) + _chunk(gzip_footer) + struct.pack("!I", len(pieces))
                + b"".join(_chunk(before) + struct.pack("!I", index) for before, index in pieces)
                + _chunk(text[last:].encode("utf-8")))
    if len(template) > MAX_TEMPLATE_BYTES or len(public_body) > MAX_JSON_BYTES:
        raise MixedPayloadError("mixed private/public material exceeds bound")
    encoder = zlib.compressobj(9, zdict=public_raw[-32768:])
    dictionary_body = encoder.compress(template) + encoder.flush()
    private_codec, private_body = min(
        ((1, zlib.compress(template, 9)),
         (2, lzma.compress(template, format=lzma.FORMAT_XZ, preset=6)),
         (3, dictionary_body)), key=lambda item: len(item[1]),
    )
    header = HEADER.pack(profile, original_codec, public_codec, private_codec, len(original),
                         len(raw), len(template), len(public_raw), len(fragments),
                         _sha(original), _sha(public_body))
    packet = header + private_body
    packet += _sha(PREFIX.encode() + packet)
    if len(packet) > MAX_PACKET_BYTES:
        raise MixedPayloadError("mixed result packet exceeds bound")
    result = (PREFIX + base64.b64encode(packet).decode("ascii") if original_codec == 0
              else PREFIX.encode() + packet)
    return result, None if reused else public_body


def _prepare_mixed_row(strategy: str, table: str, original_row: Mapping,
                       encoded_row: Mapping, references: PayloadReferences):
    """Prepare exact envelopes and public bodies without publishing new bytes."""
    result = dict(encoded_row)
    public_bodies = []
    for profile, column in current_profiles(strategy, table):
        value = original_row.get(column)
        if value is None:
            continue
        working_row = original_row
        if is_mixed_payload(value):
            # Equal reconstruction is insufficient: validate approved spans and
            # splice positions even when an existing packet is copied unchanged.
            verify_mixed_ownership(strategy, table, column, value, value, references)
            header = _packet(value)
            if header["profile"] == profile or references.writer is None:
                if references.writer is not None:
                    sibling = parse_reference(result.get("raw_gzip")) if profile in (1, 2, 8, 9, 25, 26) else None
                    if sibling is None or sibling[1] != header["public_sha256"]:
                        _, _, body = _public(header, references)
                        public_bodies.append(body)
                result[column] = value
                continue
            value = resolve_mixed_payload(value, references)
            working_row = {**original_row, column: value}
        if references.writer is None:
            continue
        prepared = _prepare(profile, value, working_row, result, references)
        if prepared is None:
            continue
        envelope, new_public_body = prepared
        if new_public_body is not None:
            public_bodies.append(new_public_body)
        result[column] = envelope
    return result, public_bodies


def externalize_mixed_rows(strategy: str, table: str, original_rows,
                           encoded_rows, references: PayloadReferences) -> list[dict]:
    """ACK bounded batches before returning any prepared private envelope.

    Preparing a recipe is not a writer acknowledgement. The only publisher is
    the real PayloadReferences writer, which verifies the durable response hash.
    A later failure can leave public orphans, but no successful local row result.
    """
    from .market_data_store import MAX_BATCH_BYTES, MAX_BATCH_ITEMS
    originals, encoded = list(original_rows), list(encoded_rows)
    if len(originals) != len(encoded):
        raise ValueError('mixed row inputs have different lengths')
    pending, results, size = {}, [], 0

    def flush():
        nonlocal size
        if pending:
            references.encode_many(list(pending.values()))
            pending.clear()
            size = 0

    for original, current in zip(originals, encoded, strict=True):
        result, bodies = _prepare_mixed_row(strategy, table, original, current, references)
        for body in bodies:
            digest = _sha(body)
            if digest in pending:
                if pending[digest] != body:
                    raise MixedPayloadError('mixed public body digest conflict')
                continue
            if pending and (len(pending) >= MAX_BATCH_ITEMS or size + len(body) > MAX_BATCH_BYTES):
                flush()
            pending[digest] = body
            size += len(body)
        results.append(result)
    flush()
    return results


def externalize_mixed_row(strategy: str, table: str, original_row: Mapping,
                          encoded_row: Mapping, references: PayloadReferences) -> dict:
    return externalize_mixed_rows(strategy, table, [original_row], [encoded_row], references)[0]
