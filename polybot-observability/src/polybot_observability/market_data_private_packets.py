"""Private, owner-bound interning of already verified mixed payload packets.

The dictionary never enters the public store. It keeps the exact existing PMMIX
TEXT/BLOB packet, including its private recipe; readers expand this local layer
before invoking the ordinary public payload resolver. No function commits.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import re
import sqlite3
import sys
from uuid import uuid4
import zlib

from .market_data_mixed import (
    MAX_PACKET_BYTES, _packet, is_mixed_payload, mixed_columns,
    supported_profile_ids, verify_mixed_ownership,
)
from .market_data_scalar_links import _namespace

CONTRACT = "private-mixed-packet-dictionary-v1"
LAYOUT_TABLE = "_private_packet_layout"
PACKET_TABLE = "_private_packets"
PREFIX = "\x1ePMPKT1:"
FAMILY_PREFIX = "\x1ePMPKT"
MAX_LOCAL_PACKET_BYTES = 2 * MAX_PACKET_BYTES + len(PREFIX)
CACHE_BYTES = 8 << 20
MAX_BATCH = 500
_MARKER = re.compile(r"\x1ePMPKT1:([0-9a-f]{32}):([1-9][0-9]{0,18})\Z")
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
_TABLE_SQL = {
    LAYOUT_TABLE: f"CREATE TABLE {LAYOUT_TABLE} (singleton INTEGER PRIMARY KEY CHECK(singleton=1),contract TEXT NOT NULL CHECK(contract='{CONTRACT}'),strategy TEXT NOT NULL,namespace TEXT NOT NULL,owner_token TEXT NOT NULL CHECK(length(owner_token)=32))",
    PACKET_TABLE: f"CREATE TABLE {PACKET_TABLE} (id INTEGER PRIMARY KEY CHECK(id>0),origin_table TEXT NOT NULL,origin_column TEXT NOT NULL,profile INTEGER NOT NULL CHECK(profile>0),storage_type TEXT NOT NULL CHECK(storage_type IN ('TEXT','BLOB')),sha256 BLOB NOT NULL CHECK(typeof(sha256)='blob' AND length(sha256)=32),codec TEXT NOT NULL CHECK(codec IN ('identity','zlib')),raw_size INTEGER NOT NULL CHECK(raw_size>0 AND raw_size<={MAX_LOCAL_PACKET_BYTES}),body BLOB NOT NULL CHECK(typeof(body)='blob'),UNIQUE(origin_table,origin_column,storage_type,sha256))",
}
_TRIGGER_SQL = {
    f"{table}_no_{operation.lower()}": f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'private packet dictionary is immutable'); END"
    for table in _TABLE_SQL for operation in ("UPDATE", "DELETE")
}
_TRIGGER_SQL.update({
    f"{LAYOUT_TABLE}_no_replace": f"CREATE TRIGGER {LAYOUT_TABLE}_no_replace BEFORE INSERT ON {LAYOUT_TABLE} WHEN EXISTS(SELECT 1 FROM {LAYOUT_TABLE}) BEGIN SELECT RAISE(ABORT,'private packet owner already exists'); END",
    f"{PACKET_TABLE}_no_replace": f"CREATE TRIGGER {PACKET_TABLE}_no_replace BEFORE INSERT ON {PACKET_TABLE} WHEN EXISTS(SELECT 1 FROM {PACKET_TABLE} WHERE id=NEW.id OR (origin_table=NEW.origin_table AND origin_column=NEW.origin_column AND storage_type=NEW.storage_type AND sha256=NEW.sha256)) BEGIN SELECT RAISE(ABORT,'private packet duplicate replacement is forbidden'); END",
})
_SCHEMA = {**_TABLE_SQL, **_TRIGGER_SQL}
_COLUMNS = "id,origin_table,origin_column,profile,storage_type,sha256,codec,raw_size,body"
_PEER_LAYOUTS = ("_public_raw_layout", "_public_scalar_layout",
                 "_public_projection_layout", "_public_catalog_layout")


class PrivatePacketError(ValueError):
    pass


class PrivatePacketOwnerError(PrivatePacketError):
    pass


@dataclass(frozen=True)
class PrivatePacketRecord:
    record_id: int
    table: str
    column: str
    profile: int
    storage_type: str
    sha256: str
    packet: str | bytes
    marker: str | bytes


@contextmanager
def _raw_cursor(connection):
    # ResolvingCursor would recurse on dictionary packet bodies/markers.
    cursor = sqlite3.Connection.cursor(connection, sqlite3.Cursor)
    cursor.row_factory = None
    try:
        yield cursor
    finally:
        cursor.close()


def _canonical_sql(value):
    from .market_data_sql_schema import canonical_sql_key
    return canonical_sql_key(value, ignore_if_not_exists=False)


def is_private_packet(value):
    """Detect the family, including malformed/unsupported local references."""
    return ((isinstance(value, str) and value.startswith(FAMILY_PREFIX))
            or (isinstance(value, bytes) and value.startswith(FAMILY_PREFIX.encode())))


def parse_private_packet(value):
    if not is_private_packet(value):
        return None
    try:
        text = value.decode("ascii") if isinstance(value, bytes) else value
        match = _MARKER.fullmatch(text)
    except UnicodeError:
        match = None
    if match is None or int(match[2]) >= 2**63:
        raise PrivatePacketError("malformed or unsupported private packet reference")
    return match[1], int(match[2])


def private_packet_layout(connection, *, strategy=None, namespace=None):
    with _raw_cursor(connection) as cursor:
        objects = dict(cursor.execute(
            "SELECT name,sql FROM main.sqlite_master WHERE "
            "name GLOB '_private_packet*' AND sql IS NOT NULL"
        ))
        if not objects:
            return None
        if set(objects) != set(_SCHEMA) or any(
            _canonical_sql(objects[name]) != _canonical_sql(sql)
            for name, sql in _SCHEMA.items()
        ):
            raise PrivatePacketError("private packet dictionary schema is incomplete or changed")
        rows = cursor.execute(f"SELECT * FROM main.{LAYOUT_TABLE}").fetchall()
    if (len(rows) != 1 or len(rows[0]) != 5 or rows[0][:2] != (1, CONTRACT)
            or type(rows[0][4]) is not str or not _TOKEN.fullmatch(rows[0][4])):
        raise PrivatePacketError("private packet dictionary owner metadata is invalid")
    _, _, actual_strategy, actual_namespace, token = rows[0]
    try:
        _namespace(actual_namespace, actual_strategy)
    except (ValueError, TypeError) as error:
        raise PrivatePacketOwnerError("private packet namespace is invalid") from error
    if ((strategy is not None and strategy != actual_strategy)
            or (namespace is not None and namespace != actual_namespace)):
        raise PrivatePacketOwnerError("private packet strategy/namespace differs")
    # Compare peer owner metadata on reads as well as writes. Do not call their
    # full schema validators here: they may in turn validate this dictionary.
    with _raw_cursor(connection) as cursor:
        for peer in _PEER_LAYOUTS:
            if not cursor.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?", (peer,)).fetchone():
                continue
            try:
                owners = cursor.execute(f"SELECT strategy,namespace FROM main.{peer}").fetchall()
            except sqlite3.Error as error:
                raise PrivatePacketOwnerError("peer local layout owner metadata is invalid") from error
            if owners != [(actual_strategy, actual_namespace)]:
                raise PrivatePacketOwnerError("private packet and peer local layout owners differ")
    return dict(contract=CONTRACT, strategy=actual_strategy, namespace=actual_namespace,
                owner_token=token)


def validate_private_packets(connection, *, strategy=None, namespace=None):
    return (frozenset(_SCHEMA) if private_packet_layout(
        connection, strategy=strategy, namespace=namespace) is not None else frozenset())


def _existing_owner(connection, strategy, namespace):
    """A populated DB needs an independently validated matching local owner."""
    with _raw_cursor(connection) as cursor:
        tables = {r[0] for r in cursor.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
    peers = (
        ("_public_raw_layout", "market_data_raw_links", "raw_layout_metadata", "validate_raw_layout"),
        ("_public_scalar_layout", "market_data_scalar_links", "scalar_layout_metadata", "validate_scalar_layout"),
        ("_public_projection_layout", "market_data_projection_links", "projection_layout_metadata", "validate_projection_layout"),
        ("_public_catalog_layout", "market_data_catalog_links", "catalog_layout_metadata", "validate_catalog_layout"),
    )
    matched = False
    for table, module, metadata_name, validator_name in peers:
        if table not in tables:
            continue
        from importlib import import_module
        owner_module = import_module("." + module, __package__)
        metadata = getattr(owner_module, metadata_name)(connection)
        getattr(owner_module, validator_name)(connection)
        if metadata is None or metadata["strategy"] != strategy or metadata["namespace"] != namespace:
            raise PrivatePacketOwnerError("existing local layout belongs to a different owner")
        matched = True
    if matched:
        return
    with _raw_cursor(connection) as cursor:
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            if cursor.execute(f"SELECT 1 FROM main.{quoted} LIMIT 1").fetchone():
                raise PrivatePacketOwnerError("populated unowned database cannot initialize private packets")


def initialize_private_packets(connection, *, strategy, namespace):
    if not connection.in_transaction:
        raise PrivatePacketError("private packet initialization requires caller transaction")
    _namespace(namespace, strategy)
    existing = private_packet_layout(connection, strategy=strategy, namespace=namespace)
    if existing is not None:
        return existing
    _existing_owner(connection, strategy, namespace)
    with _raw_cursor(connection) as cursor:
        cursor.execute("SAVEPOINT initialize_private_packets")
        try:
            for sql in _SCHEMA.values():
                cursor.execute(sql)
            cursor.execute(f"INSERT INTO main.{LAYOUT_TABLE} VALUES(1,?,?,?,?)",
                           (CONTRACT, strategy, namespace, uuid4().hex))
            cursor.execute("RELEASE initialize_private_packets")
        except BaseException:
            cursor.execute("ROLLBACK TO initialize_private_packets")
            cursor.execute("RELEASE initialize_private_packets")
            raise
    return private_packet_layout(connection, strategy=strategy, namespace=namespace)


def _inflate_packet(body, expected):
    if type(body) is not bytes or not 0 < expected <= MAX_LOCAL_PACKET_BYTES:
        raise PrivatePacketError("invalid private packet size/body")
    try:
        decoder = zlib.decompressobj()
        value = decoder.decompress(body, expected + 1)
    except zlib.error as error:
        raise PrivatePacketError("invalid compressed private packet") from error
    if (len(value) != expected or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail):
        raise PrivatePacketError("private packet compressed length/trailing data mismatch")
    return value


def _marker(layout, record_id, storage_type):
    value = PREFIX + layout["owner_token"] + ":" + str(record_id)
    return value if storage_type == "TEXT" else value.encode("ascii")


class PrivatePacketReader:
    """Bounded decompression cache; every lookup rechecks local row integrity."""
    def __init__(self, connection, *, strategy=None, namespace=None, cache_bytes=CACHE_BYTES):
        if type(cache_bytes) is not int or cache_bytes < 0 or cache_bytes > CACHE_BYTES:
            raise ValueError("private packet cache byte budget is invalid")
        self.connection = connection
        self.strategy = strategy
        self.namespace = namespace
        self.cache_limit = cache_bytes
        self.cache_bytes = 0
        self.cache = OrderedDict()

    def clear(self):
        self.cache.clear()
        self.cache_bytes = 0

    def _record(self, row, layout):
        record_id, table, column, profile, storage_type, sha, codec, raw_size, body = row
        if (type(record_id) is not int or not 0 < record_id < 2**63
                or type(profile) is not int or profile not in supported_profile_ids(layout["strategy"], table, column)
                or storage_type not in ("TEXT", "BLOB") or type(sha) is not bytes or len(sha) != 32
                or type(raw_size) is not int or not 0 < raw_size <= MAX_LOCAL_PACKET_BYTES
                or type(body) is not bytes or len(body) > MAX_LOCAL_PACKET_BYTES
                or codec not in ("identity", "zlib")):
            raise PrivatePacketError("private packet dictionary row metadata is invalid")
        key = (layout["owner_token"], record_id, table, column, profile, storage_type,
               sha, codec, raw_size, hashlib.sha256(body).digest())
        if key in self.cache:
            self.cache.move_to_end(key)
            packet = self.cache[key][0]
        else:
            raw = body if codec == "identity" else _inflate_packet(body, raw_size)
            if len(raw) != raw_size or hashlib.sha256(raw).digest() != sha:
                raise PrivatePacketError("private packet dictionary SHA256/length mismatch")
            try:
                packet = raw.decode("utf-8") if storage_type == "TEXT" else raw
            except UnicodeError as error:
                raise PrivatePacketError("private packet TEXT is not UTF-8") from error
            header = _packet(packet)
            if header["profile"] != profile or header["text_type"] != (storage_type == "TEXT"):
                raise PrivatePacketError("private packet profile/type differs")
            size = sys.getsizeof(packet) + sys.getsizeof(key) + sum(sys.getsizeof(x) for x in key) + 512
            if size <= self.cache_limit:
                while self.cache and self.cache_bytes + size > self.cache_limit:
                    _, (_, discarded) = self.cache.popitem(last=False)
                    self.cache_bytes -= discarded
                self.cache[key] = packet, size
                self.cache_bytes += size
        return PrivatePacketRecord(record_id, table, column, profile, storage_type,
                                   sha.hex(), packet, _marker(layout, record_id, storage_type))

    def record(self, value, *, table=None, column=None):
        parsed = parse_private_packet(value)
        if parsed is None:
            raise PrivatePacketError("expected a private packet reference")
        layout = private_packet_layout(self.connection, strategy=self.strategy, namespace=self.namespace)
        if layout is None or parsed[0] != layout["owner_token"]:
            raise PrivatePacketOwnerError("private packet reference has no matching local owner")
        with _raw_cursor(self.connection) as cursor:
            row = cursor.execute(f"SELECT {_COLUMNS} FROM main.{PACKET_TABLE} WHERE id=?", (parsed[1],)).fetchone()
        if row is None:
            raise PrivatePacketError("private packet dictionary record is missing")
        result = self._record(row, layout)
        if ((table is not None and result.table != table)
                or (column is not None and result.column != column)):
            raise PrivatePacketOwnerError("private packet reference belongs to another logical column")
        if type(value) is not type(result.marker):
            raise PrivatePacketError("private packet marker SQLite type differs")
        return result

    def resolve(self, value, *, table=None, column=None):
        return self.record(value, table=table, column=column).packet if is_private_packet(value) else value


def resolve_private_packet(connection, value, *, strategy=None, namespace=None, table=None, column=None):
    return PrivatePacketReader(connection, strategy=strategy, namespace=namespace).resolve(value, table=table, column=column)


def intern_private_packet(connection, packet, *, strategy, namespace, table, column, references):
    if not connection.in_transaction:
        raise PrivatePacketError("private packet insertion requires caller transaction")
    layout = private_packet_layout(connection, strategy=strategy, namespace=namespace)
    if layout is None:
        raise PrivatePacketOwnerError("private packet layout must be explicitly initialized")
    if not supported_profile_ids(strategy, table, column):
        raise PrivatePacketOwnerError("private packet column is not a registered mixed owner")
    if is_private_packet(packet):
        packet = resolve_private_packet(connection, packet, strategy=strategy, namespace=namespace,
                                        table=table, column=column)
    if not is_mixed_payload(packet) or not verify_mixed_ownership(
        strategy, table, column, packet, packet, references
    ):
        raise PrivatePacketError("only ownership-verified mixed packets may be interned")
    header = _packet(packet)
    raw = packet.encode("utf-8") if isinstance(packet, str) else packet
    storage_type = "TEXT" if isinstance(packet, str) else "BLOB"
    sha = hashlib.sha256(raw).digest()
    with _raw_cursor(connection) as cursor:
        existing = cursor.execute(
            f"SELECT {_COLUMNS} FROM main.{PACKET_TABLE} WHERE origin_table=? AND origin_column=? AND storage_type=? AND sha256=?",
            (table, column, storage_type, sha),
        ).fetchone()
        if existing is not None:
            row = PrivatePacketReader(connection)._record(existing, layout)
            if row.packet != packet or type(row.packet) is not type(packet):
                raise PrivatePacketError("private packet identity/content conflict")
            return row.marker
        compressed = zlib.compress(raw, 6)
        codec, body = ("zlib", compressed) if len(compressed) < len(raw) else ("identity", raw)
        cursor.execute(
            f"INSERT INTO main.{PACKET_TABLE}(origin_table,origin_column,profile,storage_type,sha256,codec,raw_size,body) VALUES(?,?,?,?,?,?,?,?)",
            (table, column, header["profile"], storage_type, sha, codec, len(raw), body),
        )
        return _marker(layout, cursor.lastrowid, storage_type)


def intern_private_row(connection, strategy, table, row, *, namespace, references):
    result = dict(row)
    selected = sorted(column for column in mixed_columns(strategy, table)
                      if is_mixed_payload(result.get(column)) or is_private_packet(result.get(column)))
    if not selected:
        return result
    if not connection.in_transaction:
        raise PrivatePacketError("private packet insertion requires caller transaction")
    with _raw_cursor(connection) as cursor:
        cursor.execute("SAVEPOINT intern_private_row")
        try:
            for column in selected:
                result[column] = intern_private_packet(connection, result[column], strategy=strategy,
                    namespace=namespace, table=table, column=column, references=references)
            cursor.execute("RELEASE intern_private_row")
        except BaseException:
            cursor.execute("ROLLBACK TO intern_private_row")
            cursor.execute("RELEASE intern_private_row")
            raise
    return result


def iter_private_packet_records(connection, *, markers=None, strategy=None, namespace=None,
                                batch_size=MAX_BATCH):
    """Yield exact packets once per distinct reachable ID, in bounded chunks.

    With markers omitted, stream every local record. A supplied marker iterator
    is deduplicated in an independent disk-backed SQLite temporary database with
    a bounded page cache, never in the source DB or an unbounded Python set.
    Consequently query_only readers and caller transactions remain untouched.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH:
        raise ValueError("private packet iteration batch must be between 1 and 500")
    layout = private_packet_layout(connection, strategy=strategy, namespace=namespace)
    if layout is None:
        if markers is not None and next(iter(markers), None) is not None:
            raise PrivatePacketOwnerError("private packet roots have no local dictionary")
        return
    reader = PrivatePacketReader(connection, strategy=strategy, namespace=namespace)
    spool = None
    try:
        if markers is not None:
            spool = sqlite3.connect("")
            spool.execute("PRAGMA cache_size=-512")
            spool.execute("CREATE TABLE roots(id INTEGER PRIMARY KEY,storage_type TEXT NOT NULL)")
            for value in markers:
                parsed = parse_private_packet(value)
                if parsed is None or parsed[0] != layout["owner_token"]:
                    raise PrivatePacketOwnerError("private packet root has wrong local owner")
                kind = "TEXT" if isinstance(value, str) else "BLOB"
                prior = spool.execute("SELECT storage_type FROM roots WHERE id=?", (parsed[1],)).fetchone()
                if prior is not None and prior != (kind,):
                    raise PrivatePacketError("private packet roots disagree on SQLite type")
                spool.execute("INSERT OR IGNORE INTO roots VALUES(?,?)", (parsed[1], kind))
        after = 0
        with _raw_cursor(connection) as cursor:
            upper_id = cursor.execute(f"SELECT COALESCE(MAX(id),0) FROM main.{PACKET_TABLE}").fetchone()[0]
            while True:
                if spool is None:
                    ids = [row[0] for row in cursor.execute(
                        f"SELECT id FROM main.{PACKET_TABLE} WHERE id>? AND id<=? ORDER BY id LIMIT ?",
                        (after, upper_id, batch_size),
                    )]
                    roots = [(record_id, None) for record_id in ids]
                else:
                    roots = spool.execute("SELECT id,storage_type FROM roots WHERE id>? ORDER BY id LIMIT ?", (after, batch_size)).fetchall()
                if not roots:
                    break
                # Packet bodies can approach the per-cell byte limit; never
                # materialize batch_size full BLOBs in memory at once.
                for record_id, storage_type in roots:
                    row = cursor.execute(f"SELECT {_COLUMNS} FROM main.{PACKET_TABLE} WHERE id=?", (record_id,)).fetchone()
                    if row is None or (storage_type is not None and row[4] != storage_type):
                        raise PrivatePacketError("private packet root is missing or has wrong SQLite type")
                    yield reader._record(row, layout)
                after = roots[-1][0]
    finally:
        if spool is not None:
            spool.close()
