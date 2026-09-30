"""Remote Apple public aggregates with local private state and exact frame replay.

Only the inspected apple-filtered-frames-v1 public leaves are shared. Capacity,
selection, fees, audit, request receipts and watch state remain in the local
frame. The producer did not retain original HTTP bodies; public objects are
canonical projections, not reconstructed HTTP receipts. The unrelated local
legacy golden-apple application is outside this adapter's format boundary.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import lzma
import re
import sqlite3
import struct
from urllib.parse import parse_qsl, urlsplit
import zlib

from .market_data_refs import PREFIX, PayloadReferences, parse_reference
from .market_data_store import MAX_PAYLOAD_BYTES, Observation, validate_hashes

APPLICATION_ID = 0x47415032
ORIGINAL_FORMAT = "apple-filtered-frames-v1"
SHARED_FORMAT = "apple-public-frame-envelope-v2"
FRAME_PREFIX = b"\x1ePMAPPLEFRAME2:"
FRAME_FAMILY_PREFIX = b"\x1ePMAPPLEFRAME"
FRAME_HEADER = struct.Struct("!BBHIIII32s32s")
RAW_LIMIT = 16_777_216
PACKED_LIMIT = 200_000
ENVELOPE_RAW_LIMIT = RAW_LIMIT + (2 << 20)
ENVELOPE_PACKED_LIMIT = 1 << 20
PUBLIC_OBJECT_LIMIT = 4096
XZ_MEMORY_LIMIT = 64 << 20
PUBLIC_FRAME_KIND = "apple_public_frame_v2"
MONTH_BUDGET_CONTRACT = "apple-shared-month-budget-v1"
_BUDGET_CONTRACT_KEY = "shared_frame_budget_contract"
_BUDGET_RESERVED_KEY = "shared_frame_reserved_bytes"
_BUDGET_FRAME_PREFIX = "shared_frame_reservation:"
PUBLIC_LEGACY_ROLES = frozenset({
    "public_http_response", "public_event_document", "public_market_document", "public_book_depth",
})


class AppleFrameError(ValueError):
    pass


def _serialize(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise AppleFrameError("invalid Apple JSON value") from error


def _unpack(blob: bytes, raw_limit: int, packed_limit: int, *, dictionary=None) -> bytes:
    if not isinstance(blob, bytes) or not 0 < len(blob) <= packed_limit:
        raise AppleFrameError("Apple compressed payload exceeds bounds")
    decoder = zlib.decompressobj(zdict=dictionary) if dictionary is not None else zlib.decompressobj()
    try:
        raw = decoder.decompress(blob, raw_limit + 1)
    except zlib.error as error:
        raise AppleFrameError("invalid Apple compressed payload") from error
    if len(raw) > raw_limit or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise AppleFrameError("Apple compressed payload has invalid length or trailing data")
    return raw


def _json(raw: bytes):
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise AppleFrameError("invalid Apple JSON payload") from error


def is_shared_frame(value: object) -> bool:
    return isinstance(value, bytes) and value.startswith(FRAME_FAMILY_PREFIX)


def _public_locations(frame: dict, *, shared: bool):
    if not isinstance(frame, dict) or frame.get("format") != ORIGINAL_FORMAT:
        raise AppleFrameError("unsupported Apple frame format")
    for name, expected in (("markets", list), ("events", dict), ("books", dict), ("followups", list)):
        if not isinstance(frame.get(name), expected):
            raise AppleFrameError("unsupported Apple public object layout")
    found = []

    def add(parent, key, role, *, nullable=False):
        if not isinstance(parent, (dict, list)) or (isinstance(parent, dict) and key not in parent):
            raise AppleFrameError("missing Apple public object leaf")
        value = parent[key]
        if value is None and nullable:
            return
        if shared:
            if type(value) is not int or not 0 <= value < PUBLIC_OBJECT_LIMIT:
                raise AppleFrameError("invalid Apple public object index")
        elif not isinstance(value, dict):
            raise AppleFrameError("Apple public object must be a JSON object")
        found.append((parent, key, role))
        if len(found) > PUBLIC_OBJECT_LIMIT:
            raise AppleFrameError("Apple public object count exceeds bound")

    for market in frame["markets"]:
        add(market, "market", "public_market")
    for key in sorted(frame["events"]):
        add(frame["events"][key], "event", "public_event_projection")
    for key in sorted(frame["books"]):
        item = frame["books"][key]
        add(item, "book", "public_clob_book", nullable=True)
        if "duplicate_books" in item:
            duplicates = item["duplicate_books"]
            if not isinstance(duplicates, list):
                raise AppleFrameError("invalid Apple duplicate books")
            for index in range(len(duplicates)):
                add(duplicates, index, "public_clob_book")
    for followup in frame["followups"]:
        if not isinstance(followup, dict) or not isinstance(followup.get("markets"), list):
            raise AppleFrameError("invalid Apple follow-up layout")
        add(followup.get("event"), "event", "public_event_projection")
        for index in range(len(followup["markets"])):
            add(followup["markets"], index, "public_market")
    return found


def _envelope(blob: bytes) -> dict:
    """Validate the bounded packet and dependency identity without store I/O."""
    if not isinstance(blob, bytes) or not blob.startswith(FRAME_PREFIX):
        raise AppleFrameError("unsupported Apple shared frame version")
    boundary = len(FRAME_PREFIX) + FRAME_HEADER.size
    if not boundary + 32 < len(blob) <= boundary + 32 + ENVELOPE_PACKED_LIMIT:
        raise AppleFrameError("Apple local envelope exceeds bounds")
    if hashlib.sha256(blob[:-32]).digest() != blob[-32:]:
        raise AppleFrameError("Apple local envelope packet checksum mismatch")
    fields = FRAME_HEADER.unpack(blob[len(FRAME_PREFIX):boundary])
    private_codec, public_codec, count, raw_size, packed_size, public_size, private_size, original_sha, public_sha = fields
    if (private_codec not in (9, 137) or public_codec not in (0, 1, 2)
            or not 0 <= count <= PUBLIC_OBJECT_LIMIT or not 0 < raw_size <= RAW_LIMIT
            or not 0 < packed_size <= PACKED_LIMIT or not 0 < private_size <= ENVELOPE_RAW_LIMIT
            or not 0 <= public_size <= RAW_LIMIT):
        raise AppleFrameError("invalid Apple shared frame header")
    if count == 0:
        if public_sha != bytes(32) or public_size != 0 or public_codec != 0 or private_codec != 9:
            raise AppleFrameError("invalid empty Apple public aggregate")
    elif public_size < 2:
        raise AppleFrameError("invalid Apple public aggregate size")
    return {"format": SHARED_FORMAT, "private_codec": private_codec,
            "public_codec": public_codec, "public_count": count,
            "original_raw_bytes": raw_size, "original_compressed_bytes": packed_size,
            "original_compressed_sha256": original_sha.hex(),
            "public_raw_bytes": public_size, "private_raw_bytes": private_size,
            "public_payload_sha256": public_sha.hex() if count else None,
            "private_body": blob[boundary:-32]}


def frame_provenance(blob: bytes) -> dict:
    """Version, codec, sizes and hashes only; never private frame fields."""
    return {key: value for key, value in _envelope(blob).items() if key != "private_body"}


def frame_reference_hashes(blob: bytes) -> list[str]:
    if not is_shared_frame(blob):
        return []
    sha = _envelope(blob)["public_payload_sha256"]
    return [sha] if sha is not None else []


def _public_material(header: dict, references: PayloadReferences):
    if not header["public_count"]:
        return [], b"", None
    marker = (PREFIX + "B:" + header["public_payload_sha256"]).encode("ascii")
    stored = references.decode_many([marker])[0]
    if not isinstance(stored, bytes) or len(stored) > MAX_PAYLOAD_BYTES:
        raise AppleFrameError("Apple public aggregate must be bounded BLOB bytes")
    codec = header["public_codec"]
    if codec == 0:
        raw = stored
    elif codec == 1:
        raw = _unpack(stored, RAW_LIMIT, MAX_PAYLOAD_BYTES)
    else:
        try:
            decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=XZ_MEMORY_LIMIT)
            raw = decoder.decompress(stored, max_length=RAW_LIMIT + 1)
        except lzma.LZMAError as error:
            raise AppleFrameError("invalid Apple public XZ aggregate") from error
        if not decoder.eof or decoder.unused_data:
            raise AppleFrameError("Apple public XZ aggregate has trailing data or exceeds bounds")
    if len(raw) != header["public_raw_bytes"]:
        raise AppleFrameError("Apple public aggregate length mismatch")
    objects = _json(raw)
    if (not isinstance(objects, list) or len(objects) != header["public_count"]
            or any(not isinstance(item, dict) for item in objects) or _serialize(objects) != raw):
        raise AppleFrameError("invalid canonical Apple public aggregate")
    return objects, raw, stored


def inspect_public_objects(blob: bytes, references: PayloadReferences) -> list[dict]:
    """Inspect the public aggregate; private fields are never returned here."""
    return _public_material(_envelope(blob), references)[0]


def resolve_frame(blob: bytes, references: PayloadReferences) -> bytes:
    """Recover the original zlib6 BLOB exactly, including its compressed SHA."""
    if not isinstance(blob, bytes):
        raise AppleFrameError("Apple frame must retain BLOB storage type")
    if not is_shared_frame(blob):
        return blob
    header = _envelope(blob)
    objects, public_raw, _ = _public_material(header, references)
    private = _unpack(header["private_body"], ENVELOPE_RAW_LIMIT, ENVELOPE_PACKED_LIMIT,
                      dictionary=public_raw[-32768:] if header["private_codec"] == 137 else None)
    if len(private) != header["private_raw_bytes"]:
        raise AppleFrameError("Apple private frame length mismatch")
    frame = _json(private)
    if _serialize(frame) != private:
        raise AppleFrameError("Apple private frame is not canonical")
    locations = _public_locations(frame, shared=True)
    indexes = [parent[key] for parent, key, _ in locations]
    if set(indexes) != set(range(len(objects))):
        raise AppleFrameError("Apple public object indexes are incomplete or invalid")
    # Check expanded size BEFORE json.dumps: repeated indexes must not turn a
    # small malicious envelope into a multi-gigabyte allocation.
    sizes = [len(_serialize(value)) for value in objects]
    expanded_size = len(private) + sum(sizes[index] - len(str(index)) for index in indexes)
    if expanded_size != header["original_raw_bytes"] or expanded_size > RAW_LIMIT:
        raise AppleFrameError("reconstructed Apple frame length mismatch")
    for parent, key, _ in locations:
        parent[key] = objects[parent[key]]
    raw = _serialize(frame)
    packed = zlib.compress(raw, 6)
    if (len(packed) != header["original_compressed_bytes"]
            or hashlib.sha256(packed).hexdigest() != header["original_compressed_sha256"]):
        raise AppleFrameError("original Apple compressed bytes cannot be reproduced by this zlib")
    return packed


@dataclass(frozen=True)
class PreparedFrame:
    """Unpublished bytes; planning is explicitly separate from durable ACK."""

    envelope: bytes
    public_payload: bytes | None

    def __post_init__(self):
        header = _envelope(self.envelope)
        if header["public_count"]:
            if (not isinstance(self.public_payload, bytes)
                    or hashlib.sha256(self.public_payload).hexdigest() != header["public_payload_sha256"]):
                raise AppleFrameError("prepared public payload does not match envelope")
        elif self.public_payload is not None:
            raise AppleFrameError("empty prepared frame cannot contain a public payload")

    @property
    def public_budget_bytes(self) -> int:
        if self.public_payload is None:
            return 0
        # Count full dependency bytes per frame without assuming cross-source
        # dedup, plus four SQLite pages for payload and observation indexes.
        return ((len(self.public_payload) + 4095) // 4096 + 4) * 4096

    def publish(self, references: PayloadReferences) -> bytes:
        if self.public_payload is not None:
            references.encode_many([self.public_payload])
        return self.envelope


def prepare_frame(blob: bytes, references: PayloadReferences, *,
                  raw_sha256: str | None = None, raw_size: int | None = None) -> PreparedFrame:
    """Plan one public aggregate and private envelope without any writer call.

    Aggregate compression preserves repetition across public objects. Private
    zlib9 chooses plain or a dictionary derived solely from public bytes. Existing
    envelopes retain the same aggregate bytes for a later explicit ACK.
    """
    previous = blob if is_shared_frame(blob) else None
    if previous is not None:
        blob = resolve_frame(previous, references)
    raw = _unpack(blob, RAW_LIMIT, PACKED_LIMIT)
    frame = _json(raw)
    locations = _public_locations(frame, shared=False)
    if _serialize(frame) != raw or zlib.compress(raw, 6) != blob:
        raise AppleFrameError("Apple frame cannot be reproduced byte-exactly")
    if (raw_sha256 is not None and raw_sha256 != hashlib.sha256(raw).hexdigest()) or (raw_size is not None and raw_size != len(raw)):
        raise AppleFrameError("Apple frame header does not match original bytes")
    if previous is not None:
        header = _envelope(previous)
        _, _, stored = _public_material(header, references)
        return PreparedFrame(previous, stored)
    objects, known = [], {}
    for parent, key, _ in locations:
        encoded = _serialize(parent[key])
        if encoded not in known:
            known[encoded] = len(objects)
            objects.append(parent[key])
        parent[key] = known[encoded]
    private = _serialize(frame)
    public_raw = _serialize(objects) if objects else b""
    if len(private) > ENVELOPE_RAW_LIMIT or len(public_raw) > RAW_LIMIT:
        raise AppleFrameError("Apple envelope raw bytes exceed bounds")
    public_codec, stored = 0, b""
    if objects:
        public_codec, stored = min(
            ((0, public_raw), (1, zlib.compress(public_raw, 9)),
             (2, lzma.compress(public_raw, format=lzma.FORMAT_XZ, preset=6))),
            key=lambda pair: len(pair[1]),
        )
    private_codec, private_packed = 9, zlib.compress(private, 9)
    if objects:
        compressor = zlib.compressobj(9, zdict=public_raw[-32768:])
        candidate = compressor.compress(private) + compressor.flush()
        if len(candidate) < len(private_packed):
            private_codec, private_packed = 137, candidate
    if len(private_packed) > ENVELOPE_PACKED_LIMIT:
        raise AppleFrameError("Apple private envelope exceeds compressed bound")
    public_sha = hashlib.sha256(stored).digest() if objects else bytes(32)
    header = FRAME_HEADER.pack(private_codec, public_codec, len(objects), len(raw), len(blob),
                               len(public_raw), len(private), hashlib.sha256(blob).digest(), public_sha)
    packet = FRAME_PREFIX + header + private_packed
    packet += hashlib.sha256(packet).digest()
    return PreparedFrame(packet, stored if objects else None)


def externalize_frame(blob: bytes, references: PayloadReferences, *,
                      raw_sha256: str | None = None, raw_size: int | None = None) -> bytes:
    """ACK public dependencies durably, then return the local private envelope."""
    return prepare_frame(blob, references, raw_sha256=raw_sha256, raw_size=raw_size).publish(references)


def _observation_metadata(observation: Observation) -> dict:
    if observation.kind != PUBLIC_FRAME_KIND:
        raise AppleFrameError("not an Apple public frame observation")
    metadata = _json(observation.metadata_json.encode("utf-8"))
    if (not isinstance(metadata, dict)
            or set(metadata) != {"job", "public_codec", "public_raw_bytes", "public_count"}
            or not isinstance(metadata["job"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", metadata["job"])
            or type(metadata["public_codec"]) is not int or metadata["public_codec"] not in (0, 1, 2)
            or type(metadata["public_raw_bytes"]) is not int
            or not 0 < metadata["public_raw_bytes"] <= RAW_LIMIT
            or type(metadata["public_count"]) is not int
            or not 0 < metadata["public_count"] <= PUBLIC_OBJECT_LIMIT):
        raise AppleFrameError("invalid Apple public observation metadata")
    observation.row()
    return metadata


def publish_frame_observation(blob: bytes, references: PayloadReferences, *,
                              observer: str, frame_id: str, observed_at: str, job: str) -> bool:
    """Index one captured frame, never per-token rows or private state.

    observed_at is the supplied original frame clock, not ingestion time. A raw
    receipt may survive a later private transaction failure. It does not certify
    a successful cycle, an order, a fill or settlement.
    """
    header = _envelope(blob)
    if not header["public_count"]:
        return False
    resolve_frame(blob, references)
    metadata = {"job": job, "public_codec": header["public_codec"],
                "public_raw_bytes": header["public_raw_bytes"], "public_count": header["public_count"]}
    observation = Observation(observer, frame_id, observed_at, PUBLIC_FRAME_KIND,
                              header["public_payload_sha256"],
                              metadata_json=_serialize(metadata).decode("utf-8"))
    _observation_metadata(observation)
    append = getattr(references.writer, "append_observations", None)
    if not callable(append):
        raise AppleFrameError("Apple public frame index requires an observation writer")
    append([observation])
    return True


def read_public_frame_observation(observation: Observation, references: PayloadReferences) -> dict:
    """Read public objects using only their index and CAS, without a private DB."""
    metadata = _observation_metadata(observation)
    objects, _, _ = _public_material(
        {**metadata, "public_payload_sha256": observation.payload_sha}, references,
    )
    return {"observer": observation.observer, "frame_id": observation.observation_id,
            "observed_at": observation.observed_at, "job": metadata["job"],
            "payload_sha": observation.payload_sha, "objects": objects}


def iter_public_frames(reader, *, observer=None, start=None, end=None, limit=1000):
    """Bounded original frame-time/observer query through the common reader."""
    references = PayloadReferences(reader=reader)
    for observation in reader.iter_observations(observer=observer, start=start, end=end,
                                               limit=limit, kind=PUBLIC_FRAME_KIND):
        yield read_public_frame_observation(observation, references)


def _budget_identity(connection: sqlite3.Connection) -> None:
    if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or connection.execute("PRAGMA user_version").fetchone()[0] != 1):
        raise AppleFrameError("monthly budget requires an Apple collection-v2 database")
    row = connection.execute("SELECT value FROM meta WHERE key='format'").fetchone()
    if row is None or row[0] != ORIGINAL_FORMAT:
        raise AppleFrameError("monthly budget source format mismatch")


def enforce_month_storage_budget(connection: sqlite3.Connection, maximum_bytes: int) -> int:
    """Bound private SQLite pages after charging shared payload/index storage."""
    _budget_identity(connection)
    metadata = dict(connection.execute(
        "SELECT key,value FROM meta WHERE key IN (?,?)", (_BUDGET_CONTRACT_KEY, _BUDGET_RESERVED_KEY),
    ))
    if metadata.get(_BUDGET_CONTRACT_KEY) != MONTH_BUDGET_CONTRACT:
        raise AppleFrameError("SHARED_MONTH_BUDGET_SEED_REQUIRED")
    try:
        reserved = int(metadata[_BUDGET_RESERVED_KEY])
    except (ValueError, KeyError) as error:
        raise AppleFrameError("invalid Apple shared budget counter") from error
    if type(maximum_bytes) is not int or not 0 <= reserved < maximum_bytes:
        raise AppleFrameError("SHARED_MONTH_TOTAL_BUDGET")
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    if page_size != 4096:
        raise AppleFrameError("Apple shared budget requires 4096-byte SQLite pages")
    allowed = (maximum_bytes - reserved) // page_size
    if allowed < 1 or connection.execute(f"PRAGMA max_page_count={allowed}").fetchone()[0] > allowed:
        raise AppleFrameError("SHARED_MONTH_TOTAL_BUDGET")
    return reserved


def _reservation_record(plan: PreparedFrame) -> dict:
    header = _envelope(plan.envelope)
    return {"reserved_bytes": plan.public_budget_bytes,
            "public_sha256": header["public_payload_sha256"],
            "original_sha256": header["original_compressed_sha256"]}


def seed_month_storage_budget(connection: sqlite3.Connection, references: PayloadReferences,
                              maximum_bytes: int) -> dict:
    """Explicit offline seed; only storage-accounting meta keys are added.

    Native startup seeds automatically only for a new empty month. Existing
    converted months must be seeded by the cutover owner before enabling writes.
    Existing reservations, including failed publications, are never discarded.
    """
    _budget_identity(connection)
    if connection.in_transaction:
        raise AppleFrameError("budget seed requires a transaction-free connection")
    prior_max = connection.execute("PRAGMA max_page_count").fetchone()[0]
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute("SELECT value FROM meta WHERE key=?", (_BUDGET_CONTRACT_KEY,)).fetchone()
        if existing is not None:
            result = {"reserved_bytes": enforce_month_storage_budget(connection, maximum_bytes), "seeded": False}
        else:
            records, reserved = [], 0
            # hex() prevents a resolving connection from hiding physical envelopes.
            for slot, encoded in connection.execute("SELECT slot,hex(frame) FROM runs WHERE frame IS NOT NULL"):
                blob = bytes.fromhex(encoded)
                if not is_shared_frame(blob):
                    continue
                header = _envelope(blob)
                if not header["public_count"]:
                    continue
                resolve_frame(blob, references)
                # Do not retain public bodies across the monthly scan.
                plan = PreparedFrame(blob, _public_material(header, references)[2])
                reserved += plan.public_budget_bytes
                records.append((_BUDGET_FRAME_PREFIX + str(slot), _serialize(_reservation_record(plan)).decode()))
            connection.executemany("INSERT INTO meta VALUES (?,?)", [
                (_BUDGET_CONTRACT_KEY, MONTH_BUDGET_CONTRACT),
                (_BUDGET_RESERVED_KEY, str(reserved)), *records,
            ])
            enforce_month_storage_budget(connection, maximum_bytes)
            result = {"reserved_bytes": reserved, "seeded": True, "frames": len(records)}
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        connection.execute(f"PRAGMA max_page_count={prior_max}")
        raise
    return result


def reserve_frame_storage(connection: sqlite3.Connection, plan: PreparedFrame, *,
                           slot: int, maximum_bytes: int) -> int:
    """Commit a conservative reservation BEFORE public payload/index writes.

    Failed or unknown-outcome publication keeps the reservation. Reclamation
    requires later verified evidence instead of assuming no shared write occurred.
    """
    if connection.in_transaction or type(slot) is not int or slot < 0:
        raise AppleFrameError("frame reservation requires a canonical slot and no transaction")
    prior_max = connection.execute("PRAGMA max_page_count").fetchone()[0]
    connection.execute("BEGIN IMMEDIATE")
    try:
        reserved = enforce_month_storage_budget(connection, maximum_bytes)
        if plan.public_payload is not None:
            value = _serialize(_reservation_record(plan)).decode()
            key = _BUDGET_FRAME_PREFIX + str(slot)
            existing = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            if existing is not None:
                if existing[0] != value:
                    raise AppleFrameError("Apple frame storage reservation identity conflict")
            else:
                reserved += plan.public_budget_bytes
                connection.execute("INSERT INTO meta VALUES (?,?)", (key, value))
                connection.execute("UPDATE meta SET value=? WHERE key=?", (str(reserved), _BUDGET_RESERVED_KEY))
                enforce_month_storage_budget(connection, maximum_bytes)
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        connection.execute(f"PRAGMA max_page_count={prior_max}")
        raise
    return reserved


def _public_response(url: str, method: str) -> bool:
    if not isinstance(url, str) or not isinstance(method, str):
        return False
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment
                or parsed.port not in (None, 443)):
            return False
        if any(name.casefold() in {"key", "api_key", "token", "access_token", "authorization",
                                  "password", "secret"} for name, _ in parse_qsl(parsed.query)):
            return False
        if parsed.hostname == "gamma-api.polymarket.com" and method == "GET":
            return bool(re.fullmatch(r"/(?:events(?:/keyset|/[0-9]+)?|markets(?:/[0-9]+)?|sports|tags)",
                                     parsed.path))
        if parsed.hostname == "clob.polymarket.com":
            return ((method == "POST" and parsed.path == "/books")
                    or (method == "GET" and bool(re.fullmatch(
                        r"/(?:book|books|fee-rate|tick-size|markets/[A-Za-z0-9_-]+)", parsed.path))))
    except (TypeError, ValueError):
        return False
    return False


def legacy_payload_roles(connection: sqlite3.Connection, hashes: list[str]) -> dict[str, tuple[str, ...]]:
    """Classify only requested hashes from explicit legacy foreign-key roles.

    Caller must use a verified legacy snapshot. This never reads payload bytes or
    cold archives. Unknown/unproven response, request body or metrics ownership
    blocks migration even when the same hash also has a public reference.
    """
    validate_hashes(hashes)
    roles = {sha: set() for sha in hashes}
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def columns(table):
        return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}

    for offset in range(0, len(hashes), 500):
        batch = hashes[offset:offset + 500]
        placeholders = ",".join("?" for _ in batch)
        if not batch:
            continue
        for table, column, role in (
            ("events", "payload_hash", "public_event_document"),
            ("event_observations", "payload_hash", "public_event_document"),
            ("markets", "payload_hash", "public_market_document"),
            ("market_observations", "payload_hash", "public_market_document"),
            ("book_observations_legacy", "depth_hash", "public_book_depth"),
            ("book_observations_legacy", "metrics_hash", "private_metrics"),
            ("book_observations", "depth_hash", "public_book_depth"),
            ("book_observations", "metrics_hash", "private_metrics"),
            ("requests", "request_hash", "private_request_body"),
        ):
            if table in tables and column in columns(table):
                for (sha,) in connection.execute(
                    f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IN ({placeholders})', batch
                ):
                    roles[sha].add(role)
        if "requests" in tables and {"payload_hash", "url", "method"} <= columns("requests"):
            for sha, url, method in connection.execute(
                f"SELECT payload_hash,url,method FROM requests WHERE payload_hash IN ({placeholders})", batch
            ):
                roles[sha].add("public_http_response" if _public_response(url, method)
                               else "unverified_response")
        elif "requests" in tables and "payload_hash" in columns("requests"):
            # A public event reference does not establish the ownership of an
            # additional response reference whose endpoint cannot be checked.
            for (sha,) in connection.execute(
                f"SELECT DISTINCT payload_hash FROM requests WHERE payload_hash IN ({placeholders})", batch
            ):
                roles[sha].add("unverified_response")
        if {"compact_books", "compact_payload_keys"} <= tables:
            if not {"depth_key", "metrics_key"} <= columns("compact_books"):
                raise ValueError("unsupported Apple compact book references")
            for column, role in (("depth_key", "public_book_depth"), ("metrics_key", "private_metrics")):
                for (sha,) in connection.execute(
                    f"SELECT DISTINCT p.payload_hash FROM compact_books b "
                    f"JOIN compact_payload_keys p ON p.id=b.{column} "
                    f"WHERE p.payload_hash IN ({placeholders})", batch,
                ):
                    roles[sha].add(role)
        classified = {
            ("events", "payload_hash"), ("event_observations", "payload_hash"),
            ("markets", "payload_hash"), ("market_observations", "payload_hash"),
            ("book_observations_legacy", "depth_hash"),
            ("book_observations_legacy", "metrics_hash"),
            ("book_observations", "depth_hash"), ("book_observations", "metrics_hash"),
            ("requests", "request_hash"), ("requests", "payload_hash"),
            ("compact_payload_keys", "payload_hash"),
        }
        for table in tables:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
                for sha in batch:
                    roles[sha].add("unclassified_schema")
                continue
            for foreign in connection.execute(f'PRAGMA foreign_key_list("{table}")'):
                target, column = foreign[2], foreign[3]
                if target not in {"payloads", "compact_payload_keys"}:
                    continue
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column):
                    for sha in batch:
                        roles[sha].add("unclassified_reference")
                elif target == "payloads" and (table, column) not in classified:
                    for (sha,) in connection.execute(
                        f'SELECT DISTINCT "{column}" FROM "{table}" '
                        f'WHERE "{column}" IN ({placeholders})', batch,
                    ):
                        roles[sha].add("unclassified_reference")
                elif (target == "compact_payload_keys"
                      and (table, column) not in {("compact_books", "depth_key"),
                                                 ("compact_books", "metrics_key")}):
                    for (sha,) in connection.execute(
                        f'SELECT DISTINCT p.payload_hash FROM "{table}" t '
                        f'JOIN compact_payload_keys p ON p.id=t."{column}" '
                        f'WHERE p.payload_hash IN ({placeholders})', batch,
                    ):
                        roles[sha].add("unclassified_reference")
    return {sha: tuple(sorted(values or {"unknown"})) for sha, values in roles.items()}


def externalize_legacy_payload(row: Mapping, roles, references: PayloadReferences) -> dict:
    """Explicitly classified public legacy compressed bytes only; never metrics.

    The zlib body is shared as exact original BLOB bytes. Generic PMDATA1 BLOB
    resolution restores it before the old read_payload() checksum/JSON checks.
    Retired sources are never opened or modified by this function.
    """
    result = dict(row)
    roles = set(roles)
    if not roles or not roles <= PUBLIC_LEGACY_ROLES:
        return result
    if result.get("encoding") != "zlib":
        raise ValueError("unsupported Apple legacy payload encoding")
    compressed = result.get("compressed")
    if parse_reference(compressed):
        compressed = references.decode_many([compressed])[0]
    raw = _unpack(compressed, MAX_PAYLOAD_BYTES, MAX_PAYLOAD_BYTES)
    if (type(result.get("bytes")) is not int or len(raw) != result["bytes"]
            or hashlib.sha256(raw).hexdigest() != result.get("hash")):
        raise ValueError("Apple legacy payload integrity mismatch")
    result["compressed"] = references.encode_many([compressed])[0]
    return result
