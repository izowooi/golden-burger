"""Compact public legacy numeric projections, independent of private membership.

These values retain the source's timestamp basis and probability interpretation;
they are not exchange-clock receipts or universally YES-side probabilities.
Source receipts are historical facts. Retention of a private membership row does
not erase its public receipt, and legacy integer IDs may be reused after deletion.

Storage contract public-scalar-block-store-v1 binds immutable positive record IDs
to one persistent authority UUID. UNCLAIMED can become ORIGIN or REPLICA once;
only the origin allocates IDs, and replicas preserve the supplied ID/value pair.
The canonical timestamp has BLOB affinity; the generated NUMERIC projection is
only the legacy DATETIME query interface and is never substituted into the hash.

HOT stores three REAL cells plus a domain-separated checksum of its record ID and
complete snapshot SHA; the public reference retains the content-only SHA. At 1024 HOT rows the
owner packs column-major probability/liquidity/volume cells: N for nullable NULL,
or F plus one big-endian IEEE-754 double, then one bounded zlib stream. The block
digest binds its row count and every ordered global ID plus the five-cell content
SHA, including the fresh condition/timestamp index. HOT removal and pointer
publication share one FULL transaction; no successful ACK relies on a RAM buffer.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import struct

from .market_data_store import MAX_BATCH_ITEMS, StoreError, StoreLimitError, validate_hashes

SCALAR_CONTRACT = "public-scalar-snapshot-v1"
SCALAR_HASH_DOMAIN = b"PM-SCALAR-SNAPSHOT\x00v1\x00"
MAX_SCALAR_RESULTS = 10000
MAX_SCALAR_TEXT_BYTES = 4096
_FIELDS = ("condition_id", "probability", "liquidity", "volume_24h", "timestamp")

SCALAR_STORAGE_CONTRACT = "public-scalar-block-store-v1"
BLOCK_HASH_DOMAIN = b"PM-SCALAR-NUMERIC-BLOCK\x00v1\x00"
HOT_HASH_DOMAIN = b"PM-SCALAR-HOT-ROW\x00v1\x00"
BLOCK_ROWS = 1024
MAX_BLOCK_RAW_BYTES = BLOCK_ROWS * 27
MAX_BLOCK_PACKED_BYTES = MAX_BLOCK_RAW_BYTES + 1024
DEFAULT_BLOCK_CACHE_BYTES = 8 << 20

SCALAR_TABLE_SQL = {
    "scalar_authority": "CREATE TABLE scalar_authority (singleton INTEGER PRIMARY KEY CHECK(singleton=1),contract TEXT NOT NULL,authority_uuid TEXT NOT NULL UNIQUE,role TEXT NOT NULL CHECK(role IN ('UNCLAIMED','ORIGIN','REPLICA')))",
    "scalar_conditions": "CREATE TABLE scalar_conditions (id INTEGER PRIMARY KEY,condition_id TEXT NOT NULL UNIQUE)",
    "scalar_sources": "CREATE TABLE scalar_sources (id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE)",
    "scalar_blocks": "CREATE TABLE scalar_blocks (id INTEGER PRIMARY KEY,row_sha BLOB NOT NULL UNIQUE CHECK(typeof(row_sha)='blob' AND length(row_sha)=32),row_count INTEGER NOT NULL CHECK(row_count BETWEEN 1 AND 1024),raw_size INTEGER NOT NULL CHECK(raw_size BETWEEN 1 AND 27648),body BLOB NOT NULL)",
    "scalar_snapshots": "CREATE TABLE scalar_snapshots (id INTEGER PRIMARY KEY CHECK(id>0),condition_key INTEGER NOT NULL REFERENCES scalar_conditions(id),timestamp BLOB CHECK(typeof(timestamp) IN ('null','text','integer','real')),timestamp_numeric NUMERIC GENERATED ALWAYS AS(timestamp) VIRTUAL,block_id INTEGER REFERENCES scalar_blocks(id),ordinal INTEGER,CHECK((block_id IS NULL AND ordinal IS NULL) OR (block_id IS NOT NULL AND ordinal BETWEEN 0 AND 1023)))",
    "scalar_hot": "CREATE TABLE scalar_hot (record_id INTEGER PRIMARY KEY REFERENCES scalar_snapshots(id),probability REAL NOT NULL,liquidity REAL,volume_24h REAL,row_sha BLOB NOT NULL CHECK(typeof(row_sha)='blob' AND length(row_sha)=32))",
    "scalar_receipts": "CREATE TABLE scalar_receipts (source_id INTEGER NOT NULL REFERENCES scalar_sources(id),original_id INTEGER NOT NULL CHECK(typeof(original_id)='integer'),record_id INTEGER NOT NULL REFERENCES scalar_snapshots(id),PRIMARY KEY(source_id,original_id,record_id)) WITHOUT ROWID",
}
SCALAR_INDEX_SQL = {
    "scalar_snapshots_condition_time": "CREATE INDEX scalar_snapshots_condition_time ON scalar_snapshots(condition_key,timestamp_numeric,id)",
    "scalar_snapshots_block": "CREATE UNIQUE INDEX scalar_snapshots_block ON scalar_snapshots(block_id,ordinal) WHERE block_id IS NOT NULL",
    "scalar_receipts_record": "CREATE INDEX scalar_receipts_record ON scalar_receipts(record_id,source_id,original_id)",
}
SCALAR_TRIGGER_SQL = {
    f"{table}_no_{action.lower()}": f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'public scalar logical data are immutable'); END"
    for table in ("scalar_conditions", "scalar_sources", "scalar_blocks", "scalar_receipts")
    for action in ("UPDATE", "DELETE")
}
SCALAR_TRIGGER_SQL.update({
    "scalar_authority_insert": "CREATE TRIGGER scalar_authority_insert BEFORE INSERT ON scalar_authority WHEN scalar_write_mode()!='initialize' OR EXISTS(SELECT 1 FROM scalar_authority) OR NEW.role!='UNCLAIMED' BEGIN SELECT RAISE(ABORT, 'scalar authority initialization is not repeatable'); END",
    "scalar_authority_no_delete": "CREATE TRIGGER scalar_authority_no_delete BEFORE DELETE ON scalar_authority BEGIN SELECT RAISE(ABORT, 'scalar authority cannot be removed'); END",
    "scalar_authority_claim": "CREATE TRIGGER scalar_authority_claim BEFORE UPDATE ON scalar_authority WHEN scalar_write_mode()!='claim' OR OLD.role!='UNCLAIMED' OR NEW.singleton!=OLD.singleton OR NEW.contract!=OLD.contract OR NEW.role NOT IN ('ORIGIN','REPLICA') OR (NEW.role='ORIGIN' AND NEW.authority_uuid!=OLD.authority_uuid) OR EXISTS(SELECT 1 FROM scalar_snapshots) OR EXISTS(SELECT 1 FROM scalar_hot) OR EXISTS(SELECT 1 FROM scalar_blocks) OR EXISTS(SELECT 1 FROM scalar_receipts) BEGIN SELECT RAISE(ABORT, 'scalar authority is already bound'); END",
    "scalar_snapshots_no_delete": "CREATE TRIGGER scalar_snapshots_no_delete BEFORE DELETE ON scalar_snapshots BEGIN SELECT RAISE(ABORT, 'public scalar logical data are immutable'); END",
    "scalar_snapshots_insert": "CREATE TRIGGER scalar_snapshots_insert BEFORE INSERT ON scalar_snapshots WHEN scalar_write_mode() NOT IN ('put','import') OR NEW.block_id IS NOT NULL OR NEW.ordinal IS NOT NULL BEGIN SELECT RAISE(ABORT, 'scalar records require the owner write path'); END",
    "scalar_snapshots_pack": "CREATE TRIGGER scalar_snapshots_pack BEFORE UPDATE ON scalar_snapshots WHEN scalar_write_mode()!='pack' OR OLD.block_id IS NOT NULL OR NEW.block_id IS NULL OR NEW.ordinal IS NULL OR NEW.id!=OLD.id OR NEW.condition_key!=OLD.condition_key OR scalar_typed_cell(NEW.timestamp)!=scalar_typed_cell(OLD.timestamp) OR NEW.ordinal<0 OR NEW.ordinal>=(SELECT row_count FROM scalar_blocks WHERE id=NEW.block_id) BEGIN SELECT RAISE(ABORT, 'scalar update is not an exact hot-to-cold transition'); END",
    "scalar_hot_no_update": "CREATE TRIGGER scalar_hot_no_update BEFORE UPDATE ON scalar_hot BEGIN SELECT RAISE(ABORT, 'public scalar hot values are immutable'); END",
    "scalar_hot_insert": "CREATE TRIGGER scalar_hot_insert BEFORE INSERT ON scalar_hot WHEN scalar_write_mode() NOT IN ('put','import') OR (SELECT block_id FROM scalar_snapshots WHERE id=NEW.record_id) IS NOT NULL BEGIN SELECT RAISE(ABORT, 'scalar hot rows require the owner write path'); END",
    "scalar_hot_pack_delete": "CREATE TRIGGER scalar_hot_pack_delete BEFORE DELETE ON scalar_hot WHEN scalar_write_mode()!='pack' OR (SELECT block_id FROM scalar_snapshots WHERE id=OLD.record_id) IS NULL BEGIN SELECT RAISE(ABORT, 'scalar hot deletion requires a committed block pointer'); END",
    "scalar_blocks_insert": "CREATE TRIGGER scalar_blocks_insert BEFORE INSERT ON scalar_blocks WHEN scalar_write_mode()!='pack' BEGIN SELECT RAISE(ABORT, 'scalar blocks require the owner pack path'); END",
})


class MissingScalarRecordError(StoreError):
    def __init__(self, record_ids: list[int]):
        self.record_ids = record_ids
        super().__init__("missing scalar records: " + ", ".join(map(str, record_ids)))


class CorruptScalarSnapshotError(StoreError):
    pass


class ScalarReceiptConflictError(StoreError):
    pass


class ScalarAuthorityError(StoreError):
    pass


class ScalarRecordConflictError(StoreError):
    pass


def _text(value, label, *, maximum_characters=1024):
    if (type(value) is not str or not value or len(value) > maximum_characters
            or len(value.encode("utf-8")) > MAX_SCALAR_TEXT_BYTES):
        raise ValueError(label + " must be a nonempty bounded string")
    return value


def _integer(value):
    if type(value) is not int or not -(1 << 63) <= value < (1 << 63):
        raise ValueError("scalar integer must fit SQLite signed 64-bit storage")
    return value


def _real(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not float or math.isnan(value):
        raise ValueError("scalar REAL values must be floats representable by SQLite")
    # REAL affinity canonicalizes signed zero; timestamp BLOB affinity does not.
    return 0.0 if value == 0.0 else value


def _timestamp(value):
    if value is None:
        return None
    if type(value) is int:
        return _integer(value)
    if type(value) is float and not math.isnan(value):
        return value
    if type(value) is str and len(value.encode("utf-8")) <= MAX_SCALAR_TEXT_BYTES:
        return value
    raise ValueError("scalar timestamp must retain a bounded SQLite text/integer/real/null value")


def scalar_cell_bytes(value) -> bytes:
    """Domain hash cells retain storage type, UTF-8 bytes and IEEE-754 bits."""
    if value is None:
        return b"N"
    if type(value) is str:
        return b"T" + value.encode("utf-8")
    if type(value) is int:
        return b"I" + struct.pack("!q", _integer(value))
    if type(value) is float and not math.isnan(value):
        return b"F" + struct.pack("!d", value)
    raise ValueError("unsupported scalar hash cell")


def _hash_row(row) -> str:
    digest = hashlib.sha256(SCALAR_HASH_DOMAIN)
    for value in row:
        cell = scalar_cell_bytes(value)
        digest.update(len(cell).to_bytes(8, "big"))
        digest.update(cell)
    return digest.hexdigest()


def _float_from_wire(value, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > 32:
        raise ValueError("scalar wire float must be a bounded canonical hexadecimal string")
    try:
        result = float.fromhex(value)
    except (ValueError, OverflowError) as error:
        raise ValueError("scalar wire float cannot be decoded") from error
    if math.isnan(result) or result.hex() != value:
        raise ValueError("scalar wire float is not canonical")
    return result


def timestamp_to_wire(value):
    value = _timestamp(value)
    kind = {type(None): "null", str: "text", int: "integer", float: "real"}[type(value)]
    return {"type": kind, "value": value.hex() if type(value) is float else value}


def timestamp_from_wire(value):
    if not isinstance(value, dict) or set(value) != {"type", "value"}:
        raise ValueError("scalar timestamp wire fields mismatch")
    kind, raw = value["type"], value["value"]
    expected = {"null": type(None), "text": str, "integer": int, "real": str}
    if kind not in expected or type(raw) is not expected[kind]:
        raise ValueError("scalar timestamp wire type mismatch")
    return _timestamp(_float_from_wire(raw) if kind == "real" else raw)


@dataclass(frozen=True, eq=False)
class ScalarSnapshot:
    condition_id: str
    probability: float
    liquidity: float | None
    volume_24h: float | None
    timestamp: str | int | float | None

    def __post_init__(self):
        _text(self.condition_id, "scalar condition identity")
        for name in ("probability", "liquidity", "volume_24h"):
            object.__setattr__(self, name, _real(getattr(self, name), nullable=name != "probability"))
        _timestamp(self.timestamp)

    def row(self) -> tuple:
        return tuple(getattr(self, name) for name in _FIELDS)

    def __eq__(self, other):
        if type(other) is not ScalarSnapshot:
            return NotImplemented
        return all(scalar_cell_bytes(a) == scalar_cell_bytes(b) for a, b in zip(self.row(), other.row(), strict=True))

    def __hash__(self):
        return hash(self.sha256)

    @property
    def sha256(self) -> str:
        return _hash_row(self.row())

    @classmethod
    def from_row(cls, row):
        if not isinstance(row, (list, tuple)) or len(row) != len(_FIELDS):
            raise ValueError("scalar snapshot requires exactly five public cells")
        return cls(*row)

    def to_wire(self) -> dict:
        return {"condition_id": self.condition_id, "probability": self.probability.hex(),
            "liquidity": self.liquidity.hex() if self.liquidity is not None else None,
            "volume_24h": self.volume_24h.hex() if self.volume_24h is not None else None,
            "timestamp": timestamp_to_wire(self.timestamp)}

    @classmethod
    def from_wire(cls, value):
        if not isinstance(value, dict) or set(value) != set(_FIELDS):
            raise ValueError("scalar snapshot wire fields mismatch")
        return cls(value["condition_id"], _float_from_wire(value["probability"]),
            _float_from_wire(value["liquidity"], nullable=True),
            _float_from_wire(value["volume_24h"], nullable=True), timestamp_from_wire(value["timestamp"]))


def snapshot_sha256(condition_id, probability, liquidity, volume_24h, timestamp) -> str:
    return ScalarSnapshot(condition_id, probability, liquidity, volume_24h, timestamp).sha256


def validate_authority(value: str) -> str:
    import uuid
    if type(value) is not str or len(value) != 36 or str(uuid.UUID(value)) != value:
        raise ValueError("scalar authority must be a canonical UUID")
    return value


def validate_record_ids(values):
    if not isinstance(values, list) or len(values) > MAX_BATCH_ITEMS:
        raise StoreLimitError("scalar record IDs must be a bounded list")
    for value in values:
        if _integer(value) <= 0:
            raise ValueError("scalar record IDs must be positive")


@dataclass(frozen=True)
class ScalarReference:
    authority_uuid: str
    record_id: int
    sha256: str

    def __post_init__(self):
        validate_authority(self.authority_uuid)
        validate_record_ids([self.record_id])
        validate_hashes([self.sha256])

    def to_wire(self):
        return {"authority_uuid": self.authority_uuid, "record_id": self.record_id, "sha256": self.sha256}

    @classmethod
    def from_wire(cls, value):
        if not isinstance(value, dict) or set(value) != {"authority_uuid", "record_id", "sha256"}:
            raise ValueError("scalar reference wire fields mismatch")
        return cls(**value)


@dataclass(frozen=True)
class ScalarRecord:
    reference: ScalarReference
    snapshot: ScalarSnapshot

    def __post_init__(self):
        if type(self.reference) is not ScalarReference or type(self.snapshot) is not ScalarSnapshot:
            raise TypeError("scalar record requires its typed reference and snapshot")
        if self.reference.sha256 != self.snapshot.sha256:
            raise CorruptScalarSnapshotError("scalar record content hash differs from reference")

    def to_wire(self):
        return {"reference": self.reference.to_wire(), "snapshot": self.snapshot.to_wire()}

    @classmethod
    def from_wire(cls, value):
        if not isinstance(value, dict) or set(value) != {"reference", "snapshot"}:
            raise ValueError("scalar record wire fields mismatch")
        return cls(ScalarReference.from_wire(value["reference"]), ScalarSnapshot.from_wire(value["snapshot"]))


@dataclass(frozen=True)
class ScalarReceipt:
    namespace: str
    original_id: int
    record_id: int

    def __post_init__(self):
        _text(self.namespace, "scalar source namespace")
        _integer(self.original_id)
        validate_record_ids([self.record_id])

    def row(self):
        return self.namespace, self.original_id, self.record_id

    def to_wire(self):
        return dict(zip(("namespace", "original_id", "record_id"), self.row()))

    @classmethod
    def from_wire(cls, value):
        if not isinstance(value, dict) or set(value) != {"namespace", "original_id", "record_id"}:
            raise ValueError("scalar receipt wire fields mismatch")
        return cls(**value)


def _batch(values, expected):
    if not isinstance(values, list) or len(values) > MAX_BATCH_ITEMS:
        raise StoreLimitError("scalar batch must be a bounded list")
    if any(type(value) is not expected for value in values):
        raise TypeError("scalar batch contains an unsupported record type")


def validate_scalar_snapshots(values):
    _batch(values, ScalarSnapshot)


def validate_scalar_records(values):
    _batch(values, ScalarRecord)


def validate_scalar_receipts(values):
    _batch(values, ScalarReceipt)


def receipt_keys(identities):
    if not isinstance(identities, list) or len(identities) > MAX_BATCH_ITEMS:
        raise StoreLimitError("scalar receipt identities must be a bounded list")
    result = []
    for value in identities:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ValueError("scalar receipt lookup requires namespace/original ID/record ID")
        result.append(ScalarReceipt(*value))
    return result


def encode_numeric_block(snapshots):
    import zlib
    if not 1 <= len(snapshots) <= BLOCK_ROWS:
        raise StoreLimitError("scalar block row count is out of bounds")
    raw = b"".join(b"N" if getattr(value, column) is None else b"F" + struct.pack("!d", getattr(value, column))
                   for column in ("probability", "liquidity", "volume_24h") for value in snapshots)
    return len(raw), zlib.compress(raw, 9)


def decode_numeric_block(body, raw_size, row_count):
    import zlib
    if (type(body) is not bytes or not 0 < len(body) <= MAX_BLOCK_PACKED_BYTES
            or type(row_count) is not int or not 1 <= row_count <= BLOCK_ROWS
            or type(raw_size) is not int or not row_count * 11 <= raw_size <= row_count * 27):
        raise CorruptScalarSnapshotError("scalar block bounds are invalid")
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(body, raw_size + 1)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or len(raw) != raw_size:
            raise ValueError("invalid scalar block compressed stream")
        columns, position = [], 0
        for column in range(3):
            values = []
            for _ in range(row_count):
                tag = raw[position:position + 1]
                position += 1
                if tag == b"N" and column != 0:
                    value = None
                elif tag == b"F":
                    value = struct.unpack("!d", raw[position:position + 8])[0]
                    position += 8
                    if math.isnan(value) or (value == 0.0 and math.copysign(1, value) < 0):
                        raise ValueError("noncanonical SQLite REAL value")
                else:
                    raise ValueError("invalid scalar numeric cell")
                values.append(value)
            columns.append(values)
        if position != len(raw):
            raise ValueError("trailing scalar numeric cells")
        return tuple(zip(*columns))
    except (ValueError, IndexError, struct.error, zlib.error) as error:
        raise CorruptScalarSnapshotError("scalar numeric block decoding failed") from error


def block_sha256(records):
    if not 1 <= len(records) <= BLOCK_ROWS:
        raise StoreLimitError("scalar block row count is out of bounds")
    digest = hashlib.sha256(BLOCK_HASH_DOMAIN + len(records).to_bytes(4, "big"))
    for record in records:
        digest.update(record.reference.record_id.to_bytes(8, "big"))
        digest.update(bytes.fromhex(record.snapshot.sha256))
    return digest.digest()


def hot_sha256(record_id, snapshot):
    """Bind HOT content to its authoritative ID before any block exists."""
    validate_record_ids([record_id])
    if type(snapshot) is not ScalarSnapshot:
        raise TypeError("scalar hot checksum requires a typed snapshot")
    return hashlib.sha256(HOT_HASH_DOMAIN + record_id.to_bytes(8, "big")
                          + bytes.fromhex(snapshot.sha256)).digest()


def initialize_scalar_schema(connection):
    from .market_data_scalar_store import ScalarAccess
    state = ScalarAccess(connection, writable=True)
    state.initialize()
    return state
