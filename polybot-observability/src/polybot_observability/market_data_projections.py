"""Exact, versioned public field groups for extended snapshots and catalogs.

The five-cell ScalarSnapshot contract is separate and unchanged. These immutable
groups exclude runtime timestamps, run IDs, strategy VWAPs, sizing and health.
Their shared authority UUID does not make their record IDs scalar record IDs.

HOT rows use a compact typed-cell stream and an ID/kind/content-bound checksum.
Cold blocks pack one registered kind, column-major, up to 1024 rows or 64 MiB.
Only non-index fields are packed; the block digest also verifies fresh condition,
token and source-clock index cells. Strings retain exact UTF-8/JSON lexical bytes.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
import re
import struct
import zlib

from .market_data_projection_profiles import projection_profile
from .market_data_scalars import (
    validate_authority,
)
from .market_data_store import (
    MAX_BATCH_ITEMS,
    StoreError,
    StoreLimitError,
    validate_hashes,
)

PROJECTION_CONTRACT = "public-typed-projection-v1"
PROJECTION_STORAGE_CONTRACT = "public-projection-block-store-v2"
PROJECTION_NULLABLE_CAPABILITY = "public-projection-nullable-subjects-v2"
MAX_UNKEYED_CANDIDATES = 1024
PROJECTION_BLOCK_ROWS = 1024
MAX_PROJECTION_BYTES = 64 << 20
MAX_PROJECTION_PACKED_BYTES = MAX_PROJECTION_BYTES + (1 << 20)
PROJECTION_PACK_TARGET_BYTES = 4 << 20
MAX_PROJECTION_RESULTS = 10000
PROJECTION_CACHE_BYTES = 8 << 20
_CONTENT_DOMAIN = b"PM-PUBLIC-PROJECTION\x00v1\x00"
_HOT_DOMAIN = b"PM-PUBLIC-PROJECTION-HOT\x00v1\x00"
_BLOCK_DOMAIN = b"PM-PUBLIC-PROJECTION-BLOCK\x00v1\x00"

PROJECTION_TABLE_SQL = {
    "projection_storage": "CREATE TABLE projection_storage (singleton INTEGER PRIMARY KEY CHECK(singleton=1),contract TEXT NOT NULL CHECK(contract='public-projection-block-store-v2'))",
    "projection_unkeyed": "CREATE TABLE projection_unkeyed (record_id INTEGER PRIMARY KEY REFERENCES projection_records(id),content_sha BLOB NOT NULL CHECK(typeof(content_sha)='blob' AND length(content_sha)=32))",
    "projection_kinds": "CREATE TABLE projection_kinds (id INTEGER PRIMARY KEY,kind TEXT NOT NULL UNIQUE,schema_sha BLOB NOT NULL CHECK(typeof(schema_sha)='blob' AND length(schema_sha)=32))",
    "projection_tokens": "CREATE TABLE projection_tokens (id INTEGER PRIMARY KEY,token_id TEXT NOT NULL UNIQUE)",
    "projection_blocks": "CREATE TABLE projection_blocks (id INTEGER PRIMARY KEY,kind_key INTEGER NOT NULL REFERENCES projection_kinds(id),row_sha BLOB NOT NULL UNIQUE CHECK(typeof(row_sha)='blob' AND length(row_sha)=32),row_count INTEGER NOT NULL CHECK(row_count BETWEEN 1 AND 1024),raw_size INTEGER NOT NULL CHECK(raw_size BETWEEN 0 AND 67108864),body BLOB NOT NULL)",
    "projection_records": "CREATE TABLE projection_records (id INTEGER PRIMARY KEY CHECK(id>0),kind_key INTEGER NOT NULL REFERENCES projection_kinds(id),condition_key INTEGER REFERENCES scalar_conditions(id),token_key INTEGER REFERENCES projection_tokens(id),source_time BLOB,source_time_text TEXT GENERATED ALWAYS AS(source_time) VIRTUAL,block_id INTEGER REFERENCES projection_blocks(id),ordinal INTEGER,CHECK((block_id IS NULL AND ordinal IS NULL) OR (block_id IS NOT NULL AND ordinal BETWEEN 0 AND 1023)))",
    "projection_hot": "CREATE TABLE projection_hot (record_id INTEGER PRIMARY KEY REFERENCES projection_records(id),cells BLOB NOT NULL,row_sha BLOB NOT NULL CHECK(typeof(row_sha)='blob' AND length(row_sha)=32))",
    "projection_receipt_streams": "CREATE TABLE projection_receipt_streams (id INTEGER PRIMARY KEY,source_id INTEGER NOT NULL REFERENCES scalar_sources(id),origin_table TEXT NOT NULL,kind_key INTEGER NOT NULL REFERENCES projection_kinds(id),UNIQUE(source_id,origin_table,kind_key))",
    "projection_receipts": "CREATE TABLE projection_receipts (stream_id INTEGER NOT NULL REFERENCES projection_receipt_streams(id),original_id INTEGER NOT NULL CHECK(typeof(original_id)='integer'),record_id INTEGER NOT NULL REFERENCES projection_records(id),PRIMARY KEY(stream_id,original_id,record_id)) WITHOUT ROWID",
}
PROJECTION_INDEX_SQL = {
    "projection_unkeyed_sha": "CREATE INDEX projection_unkeyed_sha ON projection_unkeyed(content_sha,record_id)",
    "projection_records_condition_time": "CREATE INDEX projection_records_condition_time ON projection_records(condition_key,source_time_text,kind_key,id)",
    "projection_records_token_time": "CREATE INDEX projection_records_token_time ON projection_records(token_key,source_time_text,kind_key,id) WHERE token_key IS NOT NULL",
    "projection_records_block": "CREATE UNIQUE INDEX projection_records_block ON projection_records(block_id,ordinal) WHERE block_id IS NOT NULL",
    "projection_receipts_record": "CREATE INDEX projection_receipts_record ON projection_receipts(record_id,stream_id,original_id)",
}
PROJECTION_TRIGGER_SQL = {
    f"{table}_no_{action.lower()}": f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'public projection logical data are immutable'); END"
    for table in (
        "projection_storage",
        "projection_unkeyed",
        "projection_kinds",
        "projection_tokens",
        "projection_blocks",
        "projection_receipt_streams",
        "projection_receipts",
    )
    for action in ("UPDATE", "DELETE")
}
PROJECTION_TRIGGER_SQL.update(
    {
        "projection_unkeyed_insert": "CREATE TRIGGER projection_unkeyed_insert BEFORE INSERT ON projection_unkeyed WHEN projection_write_mode() NOT IN ('put','import') OR NOT EXISTS(SELECT 1 FROM projection_records WHERE id=NEW.record_id AND condition_key IS NULL AND token_key IS NULL AND source_time IS NULL) BEGIN SELECT RAISE(ABORT, 'unkeyed projection index requires a subjectless owner record'); END",
        "projection_authority_no_rebind": "CREATE TRIGGER projection_authority_no_rebind BEFORE UPDATE ON scalar_authority WHEN EXISTS(SELECT 1 FROM projection_records) OR EXISTS(SELECT 1 FROM projection_hot) OR EXISTS(SELECT 1 FROM projection_blocks) OR EXISTS(SELECT 1 FROM projection_receipts) OR EXISTS(SELECT 1 FROM projection_unkeyed) BEGIN SELECT RAISE(ABORT, 'projection data already bind the authority'); END",
        "projection_records_no_delete": "CREATE TRIGGER projection_records_no_delete BEFORE DELETE ON projection_records BEGIN SELECT RAISE(ABORT, 'public projection logical data are immutable'); END",
        "projection_records_insert": "CREATE TRIGGER projection_records_insert BEFORE INSERT ON projection_records WHEN projection_write_mode() NOT IN ('put','import') OR (SELECT role FROM scalar_authority WHERE singleton=1)='UNCLAIMED' OR NEW.block_id IS NOT NULL OR NEW.ordinal IS NOT NULL BEGIN SELECT RAISE(ABORT, 'projection records require the owner path'); END",
        "projection_records_pack": "CREATE TRIGGER projection_records_pack BEFORE UPDATE ON projection_records WHEN projection_write_mode()!='pack' OR OLD.block_id IS NOT NULL OR NEW.block_id IS NULL OR NEW.ordinal IS NULL OR NEW.id!=OLD.id OR NEW.kind_key!=OLD.kind_key OR NEW.condition_key IS NOT OLD.condition_key OR NEW.token_key IS NOT OLD.token_key OR projection_typed_cell(NEW.source_time)!=projection_typed_cell(OLD.source_time) OR NEW.ordinal<0 OR NEW.ordinal>=(SELECT row_count FROM projection_blocks WHERE id=NEW.block_id) OR NEW.kind_key!=(SELECT kind_key FROM projection_blocks WHERE id=NEW.block_id) BEGIN SELECT RAISE(ABORT, 'projection update is not an exact packing transition'); END",
        "projection_hot_no_update": "CREATE TRIGGER projection_hot_no_update BEFORE UPDATE ON projection_hot BEGIN SELECT RAISE(ABORT, 'public projection hot values are immutable'); END",
        "projection_hot_insert": "CREATE TRIGGER projection_hot_insert BEFORE INSERT ON projection_hot WHEN projection_write_mode() NOT IN ('put','import') OR (SELECT block_id FROM projection_records WHERE id=NEW.record_id) IS NOT NULL BEGIN SELECT RAISE(ABORT, 'projection hot rows require the owner path'); END",
        "projection_hot_pack_delete": "CREATE TRIGGER projection_hot_pack_delete BEFORE DELETE ON projection_hot WHEN projection_write_mode()!='pack' OR (SELECT block_id FROM projection_records WHERE id=OLD.record_id) IS NULL BEGIN SELECT RAISE(ABORT, 'projection hot deletion requires its cold pointer'); END",
        "projection_blocks_insert": "CREATE TRIGGER projection_blocks_insert BEFORE INSERT ON projection_blocks WHEN projection_write_mode()!='pack' BEGIN SELECT RAISE(ABORT, 'projection blocks require the owner pack path'); END",
    }
)


class MissingProjectionRecordError(StoreError):
    def __init__(self, record_ids):
        self.record_ids = record_ids
        super().__init__(
            "missing projection records: " + ", ".join(map(str, record_ids))
        )


class CorruptProjectionError(StoreError):
    pass


class ProjectionRecordConflictError(StoreError):
    pass


class UnsupportedProjectionStorageError(CorruptProjectionError):
    """A physical typed-store format needs an explicit verified conversion."""


def validate_projection_storage_schema(connection):
    """Read-only format gate, including before writer journal/header changes.

    Body/scalar-only stores have no projection namespace and remain readable.
    V1 development artifacts must be converted explicitly; opening is not a
    migration. Physical v2 metadata does not represent a market data record.
    """
    rows = dict(
        connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE name GLOB 'projection_*'"
        )
    )
    if not rows:
        return False
    if "projection_storage" not in rows:
        raise UnsupportedProjectionStorageError(
            "projection storage v1 or incomplete version metadata; explicit verified migration to v2 required"
        )
    expected = {
        **PROJECTION_TABLE_SQL,
        **PROJECTION_INDEX_SQL,
        **PROJECTION_TRIGGER_SQL,
    }

    def canonical(sql):
        from .market_data_sql_schema import canonical_sql_key
        return canonical_sql_key(sql)

    for name, definition in expected.items():
        if (
            name not in rows
            or rows[name] is None
            or canonical(rows[name]) != canonical(definition)
        ):
            raise UnsupportedProjectionStorageError(
                "unsupported projection storage v2 schema definition: " + name
            )
    _validate_projection_version(connection)
    return True


def _validate_projection_version(connection):
    rows = connection.execute(
        "SELECT singleton,contract FROM projection_storage"
    ).fetchall()
    if rows != [(1, PROJECTION_STORAGE_CONTRACT)]:
        raise UnsupportedProjectionStorageError(
            "projection storage v2 requires its single exact version row"
        )


def validate_projection_ids(values):
    if not isinstance(values, list) or len(values) > MAX_BATCH_ITEMS:
        raise StoreLimitError("projection IDs must be a bounded list")
    if any(type(value) is not int or not 0 < value < 2**63 for value in values):
        raise ValueError("projection record IDs must be positive SQLite integers")


def _canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


@lru_cache(maxsize=64)
def projection_schema_sha(kind):
    profile = projection_profile(kind)
    return hashlib.sha256(
        _canonical(
            {
                "kind": kind,
                "fields": [
                    [f.name, f.storage, f.nullable, f.max_bytes] for f in profile.fields
                ],
                "condition": profile.condition_field,
                "token": profile.token_field,
                "source_time": profile.source_time_field,
            }
        )
    ).digest()


def _field_value(field, value):
    if value is None:
        if field.nullable:
            return None
        raise ValueError("required public projection field is NULL: " + field.name)
    if field.storage == "REAL":
        if type(value) is not float or math.isnan(value):
            raise ValueError(
                "public projection REAL field has a non-SQLite value: " + field.name
            )
        return 0.0 if value == 0.0 else value
    if field.storage == "INTEGER":
        if type(value) is not int or not -(1 << 63) <= value < (1 << 63):
            raise ValueError(
                "public projection INTEGER field is invalid: " + field.name
            )
        return value
    if field.storage == "TEXT":
        if type(value) is not str or len(value.encode("utf-8")) > field.max_bytes:
            raise ValueError(
                "public projection TEXT field exceeds its type/size bound: "
                + field.name
            )
        return value
    if field.storage == "SCALAR":
        if type(value) is str and len(value.encode()) <= field.max_bytes:
            return value
        if type(value) is int and -(1 << 63) <= value < (1 << 63):
            return value
        if type(value) is float and not math.isnan(value):
            return value
    raise ValueError("unsupported public projection field value: " + field.name)


def _cell(value):
    if value is None:
        return b"N"
    if type(value) is float and not math.isnan(value):
        return b"F" + struct.pack("!d", value)
    if type(value) is int and -(1 << 63) <= value < (1 << 63):
        return b"I" + struct.pack("!q", value)
    if type(value) is str:
        raw = value.encode("utf-8")
        return b"T" + len(raw).to_bytes(4, "big") + raw
    raise ValueError("invalid projection cell")


def _wire_cell(value):
    if value is None:
        return {"type": "N", "value": None}
    if type(value) is float:
        return {"type": "F", "value": value.hex()}
    if type(value) is int:
        return {"type": "I", "value": value}
    return {"type": "T", "value": base64.b64encode(value.encode()).decode("ascii")}


def _unwire_cell(value):
    if not isinstance(value, dict) or set(value) != {"type", "value"}:
        raise ValueError("projection wire cell fields mismatch")
    kind, raw = value["type"], value["value"]
    if kind == "N" and raw is None:
        return None
    if kind == "I" and type(raw) is int and -(1 << 63) <= raw < (1 << 63):
        return raw
    if kind == "F" and type(raw) is str and len(raw) <= 32:
        try:
            number = float.fromhex(raw)
        except (ValueError, OverflowError) as error:
            raise ValueError("invalid projection wire float") from error
        if not math.isnan(number) and number.hex() == raw:
            return number
    if (
        kind == "T"
        and type(raw) is str
        and len(raw) <= ((MAX_PROJECTION_BYTES + 2) // 3) * 4
    ):
        try:
            decoded = base64.b64decode(raw, validate=True)
            if base64.b64encode(decoded).decode("ascii") != raw:
                raise ValueError("noncanonical projection base64")
            return decoded.decode("utf-8")
        except (ValueError, UnicodeError, binascii.Error) as error:
            raise ValueError("invalid projection wire text") from error
    raise ValueError("projection wire cell type mismatch")


@dataclass(frozen=True, eq=False)
class PublicProjection:
    kind: str
    values: tuple

    def __post_init__(self):
        profile = projection_profile(self.kind)
        if type(self.values) is not tuple or len(self.values) != len(profile.fields):
            raise ValueError("public projection requires exactly its registered fields")
        normalized = tuple(
            _field_value(field, value)
            for field, value in zip(profile.fields, self.values, strict=True)
        )
        object.__setattr__(self, "values", normalized)
        if self.raw_bytes > MAX_PROJECTION_BYTES:
            raise StoreLimitError("public projection row exceeds the byte limit")

    def row(self):
        return self.values

    @property
    def raw_bytes(self):
        return sum(len(_cell(value)) for value in self.values)

    @property
    def sha256(self):
        digest = hashlib.sha256(_CONTENT_DOMAIN + projection_schema_sha(self.kind))
        for value in self.values:
            cell = _cell(value)
            digest.update(len(cell).to_bytes(8, "big"))
            digest.update(cell)
        return digest.hexdigest()

    def __eq__(self, other):
        if type(other) is not PublicProjection:
            return NotImplemented
        return self.kind == other.kind and all(
            _cell(a) == _cell(b) for a, b in zip(self.values, other.values, strict=True)
        )

    def __hash__(self):
        return hash(self.sha256)

    def to_wire(self):
        return {
            "kind": self.kind,
            "values": [_wire_cell(value) for value in self.values],
        }

    @classmethod
    def from_wire(cls, value):
        if not isinstance(value, dict) or set(value) != {"kind", "values"}:
            raise ValueError("public projection wire fields mismatch")
        profile = projection_profile(value["kind"])
        if not isinstance(value["values"], list) or len(value["values"]) != len(
            profile.fields
        ):
            raise ValueError("public projection wire value count mismatch")
        return cls(value["kind"], tuple(_unwire_cell(cell) for cell in value["values"]))


@dataclass(frozen=True)
class ProjectionReference:
    authority_uuid: str
    kind: str
    record_id: int
    sha256: str

    def __post_init__(self):
        validate_authority(self.authority_uuid)
        projection_profile(self.kind)
        validate_projection_ids([self.record_id])
        validate_hashes([self.sha256])

    def to_wire(self):
        return dict(
            authority_uuid=self.authority_uuid,
            kind=self.kind,
            record_id=self.record_id,
            sha256=self.sha256,
        )

    @classmethod
    def from_wire(cls, value):
        if not isinstance(value, dict) or set(value) != {
            "authority_uuid",
            "kind",
            "record_id",
            "sha256",
        }:
            raise ValueError("projection reference fields mismatch")
        return cls(**value)


@dataclass(frozen=True)
class ProjectionRecord:
    reference: ProjectionReference
    projection: PublicProjection

    def __post_init__(self):
        if (
            type(self.reference) is not ProjectionReference
            or type(self.projection) is not PublicProjection
        ):
            raise TypeError("projection record requires its typed reference and values")
        if (
            self.reference.kind != self.projection.kind
            or self.reference.sha256 != self.projection.sha256
        ):
            raise CorruptProjectionError(
                "projection reference differs from its exact fields"
            )

    def to_wire(self):
        return {
            "reference": self.reference.to_wire(),
            "projection": self.projection.to_wire(),
        }

    @classmethod
    def from_wire(cls, value):
        if not isinstance(value, dict) or set(value) != {"reference", "projection"}:
            raise ValueError("projection record fields mismatch")
        return cls(
            ProjectionReference.from_wire(value["reference"]),
            PublicProjection.from_wire(value["projection"]),
        )


@dataclass(frozen=True)
class ProjectionReceipt:
    namespace: str
    origin_table: str
    original_id: int
    kind: str
    record_id: int

    def __post_init__(self):
        if (
            type(self.namespace) is not str
            or not self.namespace
            or len(self.namespace) > 1024
            or len(self.namespace.encode()) > 4096
        ):
            raise ValueError("projection namespace must be bounded")
        if type(self.origin_table) is not str or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]{0,127}", self.origin_table
        ):
            raise ValueError("projection origin table is invalid")
        if type(self.original_id) is not int or not -(1 << 63) <= self.original_id < (
            1 << 63
        ):
            raise ValueError("projection original ID must be a SQLite integer")
        projection_profile(self.kind)
        validate_projection_ids([self.record_id])

    def row(self):
        return (
            self.namespace,
            self.origin_table,
            self.original_id,
            self.kind,
            self.record_id,
        )

    def to_wire(self):
        return dict(
            zip(
                ("namespace", "origin_table", "original_id", "kind", "record_id"),
                self.row(),
            )
        )

    @classmethod
    def from_wire(cls, value):
        if not isinstance(value, dict) or set(value) != {
            "namespace",
            "origin_table",
            "original_id",
            "kind",
            "record_id",
        }:
            raise ValueError("projection receipt fields mismatch")
        return cls(**value)


def _batch(values, expected):
    if not isinstance(values, list) or len(values) > MAX_BATCH_ITEMS:
        raise StoreLimitError("projection batch must be a bounded list")
    if any(type(value) is not expected for value in values):
        raise TypeError("projection batch contains an unsupported value type")


def validate_public_projections(values):
    _batch(values, PublicProjection)
    if sum(value.raw_bytes for value in values) > MAX_PROJECTION_BYTES:
        raise StoreLimitError("projection batch exceeds the byte limit")


def validate_projection_records(values):
    _batch(values, ProjectionRecord)
    validate_public_projections([value.projection for value in values])


def validate_projection_receipts(values):
    _batch(values, ProjectionReceipt)


def projection_receipt_keys(values):
    if not isinstance(values, list) or len(values) > MAX_BATCH_ITEMS:
        raise StoreLimitError("projection receipt keys must be a bounded list")
    result = []
    for value in values:
        if not isinstance(value, (tuple, list)) or len(value) != 5:
            raise ValueError(
                "projection receipt lookup requires all five identity cells"
            )
        result.append(ProjectionReceipt(*value))
    return result


def index_positions(kind):
    profile = projection_profile(kind)
    return tuple(
        profile.columns.index(name) if name is not None else None
        for name in (
            profile.condition_field,
            profile.token_field,
            profile.source_time_field,
        )
    )


def payload_positions(kind):
    excluded = set(index_positions(kind)) - {None}
    return tuple(
        index
        for index in range(len(projection_profile(kind).fields))
        if index not in excluded
    )


def projection_payload(projection):
    return tuple(
        projection.values[index] for index in payload_positions(projection.kind)
    )


def projection_index_bytes(kind, condition, token, source_time):
    return sum(
        len(_cell(value))
        for index, value in zip(
            index_positions(kind), (condition, token, source_time), strict=True
        )
        if index is not None
    )


def reconstruct_projection(kind, condition, token, source_time, payload):
    profile = projection_profile(kind)
    positions = payload_positions(kind)
    if len(payload) != len(positions):
        raise CorruptProjectionError("projection payload field count differs")
    values = [None] * len(profile.fields)
    for index, value in zip(positions, payload, strict=True):
        values[index] = value
    for index, value in zip(
        index_positions(kind), (condition, token, source_time), strict=True
    ):
        if index is not None:
            values[index] = value
        elif value is not None:
            raise CorruptProjectionError(
                "projection index contains an undeclared field"
            )
    return PublicProjection(kind, tuple(values))


def encode_projection_cells(rows):
    if not rows or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("projection cell matrix is not rectangular")
    raw = b"".join(_cell(row[column]) for column in range(len(rows[0])) for row in rows)
    if len(raw) > MAX_PROJECTION_BYTES:
        raise StoreLimitError("projection encoded cells exceed the byte limit")
    return raw


def decode_projection_cells(kind, raw, row_count):
    if (
        type(raw) is not bytes
        or len(raw) > MAX_PROJECTION_BYTES
        or type(row_count) is not int
        or not 1 <= row_count <= PROJECTION_BLOCK_ROWS
    ):
        raise CorruptProjectionError("projection cell stream bounds are invalid")
    profile = projection_profile(kind)
    fields = [profile.fields[index] for index in payload_positions(kind)]
    columns, offset = [], 0
    try:
        for field in fields:
            column = []
            for _ in range(row_count):
                tag = raw[offset : offset + 1]
                offset += 1
                if tag == b"N":
                    value = None
                elif tag in (b"F", b"I"):
                    value = struct.unpack(
                        "!d" if tag == b"F" else "!q", raw[offset : offset + 8]
                    )[0]
                    offset += 8
                elif tag == b"T":
                    size = int.from_bytes(raw[offset : offset + 4], "big")
                    offset += 4
                    if size > field.max_bytes or offset + size > len(raw):
                        raise ValueError("projection text bounds")
                    value = raw[offset : offset + size].decode("utf-8")
                    offset += size
                else:
                    raise ValueError("unknown projection cell tag")
                normalized = _field_value(field, value)
                if _cell(normalized) != _cell(value):
                    raise ValueError("projection cell is noncanonical")
                column.append(normalized)
            columns.append(column)
        if offset != len(raw):
            raise ValueError("projection stream trailing bytes")
        return tuple(zip(*columns)) if columns else tuple(() for _ in range(row_count))
    except (ValueError, TypeError, struct.error, UnicodeError) as error:
        raise CorruptProjectionError("projection cell stream is invalid") from error


def unpack_projection_block(kind, body, raw_size, row_count):
    if (
        type(body) is not bytes
        or len(body) > MAX_PROJECTION_PACKED_BYTES
        or type(raw_size) is not int
        or not 0 <= raw_size <= MAX_PROJECTION_BYTES
    ):
        raise CorruptProjectionError("projection block bounds are invalid")
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(body, raw_size + 1)
        if (
            not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
            or len(raw) != raw_size
        ):
            raise ValueError()
    except (ValueError, zlib.error) as error:
        raise CorruptProjectionError(
            "projection block compressed stream is invalid"
        ) from error
    return decode_projection_cells(kind, raw, row_count)


def projection_hot_sha(record_id, projection):
    validate_projection_ids([record_id])
    return hashlib.sha256(
        _HOT_DOMAIN + record_id.to_bytes(8, "big") + bytes.fromhex(projection.sha256)
    ).digest()


def projection_block_sha(records):
    if (
        not records
        or len(records) > PROJECTION_BLOCK_ROWS
        or len({record.projection.kind for record in records}) != 1
    ):
        raise ValueError("projection block must contain one bounded kind")
    digest = hashlib.sha256(
        _BLOCK_DOMAIN
        + len(records).to_bytes(4, "big")
        + projection_schema_sha(records[0].projection.kind)
    )
    for record in records:
        digest.update(record.reference.record_id.to_bytes(8, "big"))
        digest.update(bytes.fromhex(record.projection.sha256))
    return digest.digest()


def initialize_projection_schema(connection, authority_access):
    from .market_data_projection_store import ProjectionAccess

    state = ProjectionAccess(connection, authority_access, writable=True)
    state.initialize()
    return state
