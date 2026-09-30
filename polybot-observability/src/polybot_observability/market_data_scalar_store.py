"""Authority-bound scalar IDs with durable HOT rows and verified numeric blocks.

The only physical rewrite is the owner's atomic hot-to-cold transaction. Readers
pin one SQLite read snapshot, verify fresh index/dictionary metadata, and may reuse
only bounded decoded numeric bytes. Cache entries never certify mutable SQL rows.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import sys
import uuid

from .market_data_scalars import (
    BLOCK_ROWS, DEFAULT_BLOCK_CACHE_BYTES, MAX_BLOCK_PACKED_BYTES, MAX_SCALAR_RESULTS, SCALAR_STORAGE_CONTRACT,
    SCALAR_TABLE_SQL, SCALAR_INDEX_SQL, SCALAR_TRIGGER_SQL, ScalarAuthorityError,
    ScalarRecordConflictError, CorruptScalarSnapshotError, MissingScalarRecordError,
    ScalarReference, ScalarRecord, ScalarSnapshot, ScalarReceipt, _text, _timestamp,
    block_sha256, hot_sha256, encode_numeric_block, decode_numeric_block, scalar_cell_bytes,
    receipt_keys, validate_authority, validate_record_ids, validate_scalar_snapshots,
    validate_scalar_records, validate_scalar_receipts,
)
from .market_data_store import MAX_BATCH_ITEMS, StoreLimitError

_INDEX = ("SELECT s.id,c.condition_id,s.timestamp,s.block_id,s.ordinal "
          "FROM scalar_snapshots s LEFT JOIN scalar_conditions c ON c.id=s.condition_key")


class ScalarAccess:
    def __init__(self, connection, *, writable=False, cache_bytes=DEFAULT_BLOCK_CACHE_BYTES):
        if type(cache_bytes) is not int or cache_bytes < 0:
            raise ValueError("scalar block cache size must be nonnegative")
        self.db, self.writable = connection, writable
        self.cache_limit = cache_bytes
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self._mode = ""
        if writable:
            self.db.create_function("scalar_write_mode", 0, lambda: self._mode)
            self.db.create_function("scalar_typed_cell", 1, scalar_cell_bytes, deterministic=True)

    def initialize(self):
        if not self.writable or not self.db.in_transaction:
            raise ValueError("scalar initialization requires the owner's schema transaction")
        with self._mode_scope("initialize"):
            for kind, definitions in (("TABLE", SCALAR_TABLE_SQL), ("INDEX", SCALAR_INDEX_SQL),
                                      ("TRIGGER", SCALAR_TRIGGER_SQL)):
                for name, sql in definitions.items():
                    statement = sql.replace("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ", 1)
                    statement = statement.replace("CREATE " + kind + " ", "CREATE " + kind + " IF NOT EXISTS ", 1)
                    self.db.execute(statement)
                    actual = self.db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
                    from .market_data_sql_schema import canonical_sql_key
                    normalize = canonical_sql_key
                    if actual is None or normalize(actual[0]) != normalize(sql):
                        raise CorruptScalarSnapshotError("unsupported scalar storage definition: " + name)
            if self.db.execute("SELECT 1 FROM scalar_authority").fetchone() is None:
                self.db.execute("INSERT INTO scalar_authority VALUES(1,?,?,?)",
                                (SCALAR_STORAGE_CONTRACT, str(uuid.uuid4()), "UNCLAIMED"))
        self.authority()

    def available(self):
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        present = tables.intersection(SCALAR_TABLE_SQL)
        if present and present != set(SCALAR_TABLE_SQL):
            raise CorruptScalarSnapshotError("public scalar storage schema is incomplete")
        return bool(present)

    def authority(self):
        try:
            row = self.db.execute("SELECT contract,authority_uuid,role FROM scalar_authority WHERE singleton=1").fetchone()
        except Exception as error:
            raise ScalarAuthorityError("public scalar authority has not been initialized") from error
        if row is None or row[0] != SCALAR_STORAGE_CONTRACT or row[2] not in {"UNCLAIMED", "ORIGIN", "REPLICA"}:
            raise ScalarAuthorityError("public scalar authority metadata is invalid")
        validate_authority(row[1])
        return row[1], row[2]

    def require_authority(self, expected):
        validate_authority(expected)
        actual, role = self.authority()
        if actual != expected:
            raise ScalarAuthorityError("scalar reference belongs to another authority")
        return role

    @contextmanager
    def _mode_scope(self, mode):
        previous = self._mode
        self._mode = mode
        try:
            yield
        finally:
            self._mode = previous

    @contextmanager
    def _write(self, mode):
        if not self.writable or self.db.in_transaction:
            raise ValueError("scalar mutation requires a transaction-free owner connection")
        with self._mode_scope(mode):
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                self.cache.clear()
                self.cache_bytes = 0
                raise

    @contextmanager
    def _read(self):
        owns = not self.db.in_transaction
        if owns:
            self.db.execute("BEGIN")
        try:
            yield
        except BaseException:
            if owns and self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        else:
            if owns:
                self.db.execute("COMMIT")

    def _claim(self, role, authority_uuid=None):
        current, existing_role = self.authority()
        if existing_role != "UNCLAIMED":
            raise ScalarAuthorityError("scalar authority has already been claimed")
        if any(self.db.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone()
               for table in ("scalar_snapshots", "scalar_hot", "scalar_blocks", "scalar_receipts")):
            raise ScalarAuthorityError("nonempty scalar data cannot adopt an authority")
        value = validate_authority(authority_uuid or current)
        with self._mode_scope("claim"):
            self.db.execute("UPDATE scalar_authority SET authority_uuid=?,role=? WHERE singleton=1", (value, role))
        return value

    def _record(self, authority, record_id, snapshot):
        return ScalarRecord(ScalarReference(authority, record_id, snapshot.sha256), snapshot)

    def _hot(self, authority, index):
        record_id, condition, timestamp, block_id, ordinal = index
        row = self.db.execute("SELECT probability,liquidity,volume_24h,row_sha FROM scalar_hot WHERE record_id=?",
                              (record_id,)).fetchone()
        try:
            if block_id is not None or ordinal is not None or row is None:
                raise ValueError("incomplete scalar hot row")
            snapshot = ScalarSnapshot(condition, row[0], row[1], row[2], timestamp)
            if type(row[3]) is not bytes or row[3] != hot_sha256(record_id, snapshot):
                raise ValueError("scalar hot row hash mismatch")
            return self._record(authority, record_id, snapshot)
        except (ValueError, TypeError, OverflowError) as error:
            raise CorruptScalarSnapshotError("scalar hot row/index checksum mismatch") from error

    def _numeric(self, block_id, sha, row_count, raw_size, body):
        if type(sha) is not bytes or len(sha) != 32 or type(body) is not bytes:
            raise CorruptScalarSnapshotError("scalar block has invalid checksum/body types")
        key = block_id, sha, row_count, raw_size, hashlib.sha256(body).digest()
        existing = self.cache.get(key)
        if existing is not None:
            self.cache.move_to_end(key)
            return existing[0]
        values = decode_numeric_block(body, raw_size, row_count)
        size = (512 + sys.getsizeof(key) + sum(sys.getsizeof(item) for item in key) + sys.getsizeof(values)
                + sum(sys.getsizeof(row) + sum(sys.getsizeof(v) for v in row) for row in values))
        if size <= self.cache_limit:
            while self.cache and self.cache_bytes + size > self.cache_limit:
                _, (_, evicted_size) = self.cache.popitem(last=False)
                self.cache_bytes -= evicted_size
            self.cache[key] = values, size
            self.cache_bytes += size
        return values

    def _block(self, authority, block_id):
        block = self.db.execute("SELECT row_sha,row_count,raw_size,CASE WHEN typeof(body)='blob' "
            "AND length(body)<=? THEN body END FROM scalar_blocks WHERE id=?", (MAX_BLOCK_PACKED_BYTES, block_id)).fetchone()
        if block is None:
            raise CorruptScalarSnapshotError("scalar cold block is missing")
        sha, count, raw_size, body = block
        numbers = self._numeric(block_id, sha, count, raw_size, body)
        rows = self.db.execute(_INDEX + " WHERE s.block_id=? ORDER BY s.ordinal LIMIT ?", (block_id, BLOCK_ROWS + 1)).fetchall()
        if len(rows) != count or [row[4] for row in rows] != list(range(count)):
            raise CorruptScalarSnapshotError("scalar block index coverage/ordinals changed")
        records = []
        try:
            for row, numeric in zip(rows, numbers, strict=True):
                records.append(self._record(authority, row[0], ScalarSnapshot(row[1], *numeric, row[2])))
        except (ValueError, TypeError, OverflowError) as error:
            raise CorruptScalarSnapshotError("scalar cold index metadata is invalid") from error
        if block_sha256(records) != sha:
            raise CorruptScalarSnapshotError("scalar cold block/index checksum mismatch")
        if self.db.execute("SELECT 1 FROM scalar_hot h JOIN scalar_snapshots s ON s.id=h.record_id WHERE s.block_id=? LIMIT 1", (block_id,)).fetchone():
            raise CorruptScalarSnapshotError("scalar cold record unexpectedly retains a hot body")
        return {record.reference.record_id: record for record in records}

    def _records(self, ids, authority):
        unique, indexes = list(dict.fromkeys(ids)), {}
        for offset in range(0, len(unique), 400):
            batch = unique[offset:offset + 400]
            for row in self.db.execute(_INDEX + " WHERE s.id IN (" + ",".join("?" for _ in batch) + ")", batch):
                indexes[row[0]] = row
        missing = [value for value in unique if value not in indexes]
        if missing:
            raise MissingScalarRecordError(missing)
        found, blocks = {}, set()
        for record_id, row in indexes.items():
            if row[3] is None:
                found[record_id] = self._hot(authority, row)
            elif row[3] not in blocks:
                # A sparse request can span 1024 different blocks. Verify each
                # bounded block, but retain only the requested records.
                found.update((key, value) for key, value in self._block(authority, row[3]).items() if key in indexes)
                blocks.add(row[3])
        return [found[value] for value in ids]

    def get_records(self, ids, authority_uuid):
        validate_record_ids(ids)
        with self._read():
            self.require_authority(authority_uuid)
            return self._records(ids, authority_uuid)

    def _condition(self, condition):
        row = self.db.execute("SELECT id FROM scalar_conditions WHERE condition_id=?", (condition,)).fetchone()
        if row is not None:
            return row[0]
        return self.db.execute("INSERT INTO scalar_conditions(condition_id) VALUES(?)", (condition,)).lastrowid

    def _insert_hot(self, value, *, record_id=None):
        condition = self._condition(value.condition_id)
        cursor = self.db.execute("INSERT INTO scalar_snapshots(id,condition_key,timestamp) VALUES(?,?,?)",
                                 (record_id, condition, value.timestamp))
        record_id = cursor.lastrowid if record_id is None else record_id
        self.db.execute("INSERT INTO scalar_hot VALUES(?,?,?,?,?)",
                        (record_id, value.probability, value.liquidity, value.volume_24h, hot_sha256(record_id, value)))
        return record_id

    def _find_identical(self, authority, value):
        condition = self.db.execute("SELECT id FROM scalar_conditions WHERE condition_id=?", (value.condition_id,)).fetchone()
        if condition is None:
            return None
        cursor = self.db.execute("SELECT id FROM scalar_snapshots WHERE condition_key=? AND timestamp_numeric IS ? ORDER BY id",
                                 (condition[0], value.timestamp))
        try:
            while batch := cursor.fetchmany(MAX_BATCH_ITEMS):
                for record in self._records([row[0] for row in batch], authority):
                    if record.snapshot == value:
                        return record.reference
        finally:
            cursor.close()
        return None

    def _pack(self, authority):
        while True:
            ids = [row[0] for row in self.db.execute("SELECT record_id FROM scalar_hot ORDER BY record_id LIMIT ?", (BLOCK_ROWS,))]
            if len(ids) < BLOCK_ROWS:
                return
            records = self._records(ids, authority)
            raw_size, body = encode_numeric_block([record.snapshot for record in records])
            sha = block_sha256(records)
            with self._mode_scope("pack"):
                block_id = self.db.execute("INSERT INTO scalar_blocks(row_sha,row_count,raw_size,body) VALUES(?,?,?,?)",
                                           (sha, len(records), raw_size, body)).lastrowid
                self.db.executemany("UPDATE scalar_snapshots SET block_id=?,ordinal=? WHERE id=?",
                                    [(block_id, ordinal, record_id) for ordinal, record_id in enumerate(ids)])
                self.db.executemany("DELETE FROM scalar_hot WHERE record_id=?", [(value,) for value in ids])
            # Verification before commit covers the exact narrow physical rewrite.
            checked = self._block(authority, block_id)
            if any(checked[record.reference.record_id] != record for record in records):
                raise CorruptScalarSnapshotError("scalar packing changed a logical record")

    def put(self, values):
        validate_scalar_snapshots(values)
        if not values:
            return []
        with self._write("put"):
            authority, role = self.authority()
            if role == "REPLICA":
                raise ScalarAuthorityError("replicas cannot allocate scalar record IDs")
            if role == "UNCLAIMED":
                authority = self._claim("ORIGIN")
            references = []
            for value in values:
                reference = self._find_identical(authority, value)
                if reference is None:
                    reference = self._record(authority, self._insert_hot(value), value).reference
                references.append(reference)
            self._pack(authority)
            checked = self._records([ref.record_id for ref in references], authority)
            if any(record.reference != reference for record, reference in zip(checked, references, strict=True)):
                raise CorruptScalarSnapshotError("scalar put readback differs from its ACK")
        return references

    def import_records(self, authority_uuid, records):
        validate_authority(authority_uuid)
        validate_scalar_records(records)
        if any(record.reference.authority_uuid != authority_uuid for record in records):
            raise ScalarAuthorityError("scalar import contains a foreign record authority")
        with self._write("import"):
            current, role = self.authority()
            if role == "UNCLAIMED":
                self._claim("REPLICA", authority_uuid)
            elif role != "REPLICA" or current != authority_uuid:
                raise ScalarAuthorityError("scalar import cannot replace a bound origin/foreign replica")
            for record in records:
                record_id, value = record.reference.record_id, record.snapshot
                if self.db.execute("SELECT 1 FROM scalar_snapshots WHERE id=?", (record_id,)).fetchone():
                    existing = self._records([record_id], authority_uuid)[0]
                    if existing != record:
                        raise ScalarRecordConflictError("scalar imported record ID conflicts with existing exact cells")
                else:
                    self._insert_hot(value, record_id=record_id)
            self._pack(authority_uuid)
            if self._records([record.reference.record_id for record in records], authority_uuid) != records:
                raise CorruptScalarSnapshotError("scalar replica readback differs from imported records")

    def append_receipts(self, receipts, *, authority_uuid):
        validate_scalar_receipts(receipts)
        with self._write("receipt"):
            self.require_authority(authority_uuid)
            self._records(list(dict.fromkeys(receipt.record_id for receipt in receipts)), authority_uuid)
            for receipt in receipts:
                self.db.execute("INSERT OR IGNORE INTO scalar_sources(name) VALUES(?)", (receipt.namespace,))
                source = self.db.execute("SELECT id FROM scalar_sources WHERE name=?", (receipt.namespace,)).fetchone()[0]
                self.db.execute("INSERT OR IGNORE INTO scalar_receipts VALUES(?,?,?)", (source, receipt.original_id, receipt.record_id))

    def get_receipts(self, identities, *, authority_uuid):
        values = receipt_keys(identities)
        with self._read():
            self.require_authority(authority_uuid)
            result = []
            for value in values:
                row = self.db.execute("SELECT n.name,r.original_id,r.record_id FROM scalar_receipts r JOIN scalar_sources n ON n.id=r.source_id "
                    "WHERE n.name=? AND r.original_id=? AND r.record_id=?", value.row()).fetchone()
                result.append(ScalarReceipt(*row) if row else None)
            self._records(list(dict.fromkeys(receipt.record_id for receipt in result if receipt is not None)), authority_uuid)
            return result

    def iter_receipts(self, *, authority_uuid, namespace=None, after=None, limit=1000, record_ids=None):
        if type(limit) is not int or not 1 <= limit <= MAX_SCALAR_RESULTS:
            raise StoreLimitError("scalar receipt result limit must be between 1 and 10000")
        clauses, parameters = [], []
        if namespace is not None:
            _text(namespace, "scalar source namespace")
            clauses.append("n.name=?"); parameters.append(namespace)
        if after is not None:
            cursor = receipt_keys([after])[0]
            if namespace is not None and cursor.namespace != namespace:
                raise ValueError("scalar receipt cursor crosses namespace")
            clauses.append("(n.name,r.original_id,r.record_id)>(?,?,?)"); parameters.extend(cursor.row())
        if record_ids is not None:
            validate_record_ids(record_ids)
            if not record_ids:
                self.require_authority(authority_uuid)
                return iter(())
            clauses.append("r.record_id IN (" + ",".join("?" for _ in record_ids) + ")")
            parameters.extend(record_ids)
        with self._read():
            self.require_authority(authority_uuid)
            query = ("SELECT n.name,r.original_id,r.record_id FROM scalar_receipts r JOIN scalar_sources n ON n.id=r.source_id" +
                (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY n.name,r.original_id,r.record_id LIMIT ?")
            result = [ScalarReceipt(*row) for row in self.db.execute(query, [*parameters, limit])]
            ids = list(dict.fromkeys(value.record_id for value in result))
            for offset in range(0, len(ids), MAX_BATCH_ITEMS):
                self._records(ids[offset:offset + MAX_BATCH_ITEMS], authority_uuid)
            return iter(result)

    def query(self, condition_id, *, authority_uuid, start=None, end=None, namespace=None, limit=1000):
        """Historical source-time lookup using original DATETIME/NUMERIC affinity."""
        _text(condition_id, "scalar condition identity")
        if type(limit) is not int or not 1 <= limit <= MAX_SCALAR_RESULTS:
            raise StoreLimitError("scalar snapshot result limit must be between 1 and 10000")
        clauses, parameters = ["s.condition_key=(SELECT id FROM scalar_conditions WHERE condition_id=?)"], [condition_id]
        for operator, bound in ((">=", start), ("<", end)):
            if bound is not None:
                clauses.append("s.timestamp_numeric" + operator + "?"); parameters.append(_timestamp(bound))
        if namespace is not None:
            _text(namespace, "scalar source namespace")
            clauses.append("EXISTS(SELECT 1 FROM scalar_receipts r JOIN scalar_sources n ON n.id=r.source_id WHERE r.record_id=s.id AND n.name=?)")
            parameters.append(namespace)
        with self._read():
            self.require_authority(authority_uuid)
            ids = [row[0] for row in self.db.execute("SELECT s.id FROM scalar_snapshots s WHERE " + " AND ".join(clauses) +
                " ORDER BY s.timestamp_numeric,s.id LIMIT ?", [*parameters, limit])]
            result = []
            for offset in range(0, len(ids), MAX_BATCH_ITEMS):
                result.extend(self._records(ids[offset:offset + MAX_BATCH_ITEMS], authority_uuid))
            return iter(result)

    def stats(self):
        present = self.available()
        tables = {"condition_count": "scalar_conditions", "snapshot_count": "scalar_snapshots",
                  "source_count": "scalar_sources", "receipt_count": "scalar_receipts",
                  "hot_count": "scalar_hot", "block_count": "scalar_blocks"}
        return {key: self.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] if present else 0
                for key, table in tables.items()}


def insert_transport_scalar_receipt(connection, receipt, *, authority_uuid, allow_missing_records=False):
    """Insert a receipt only in an isolated replica transport transaction.

    This deliberately permits an omitted record body only when the caller names
    that transport operation, disables SQLite foreign keys before its transaction,
    and binds the initialized replica authority. The receiving importer must prove
    the omitted record against its verified closure. Live append never uses this.
    """
    validate_scalar_receipts([receipt])
    if allow_missing_records is not True or not connection.in_transaction:
        raise ValueError("scalar transport receipts require an explicit isolated transaction")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
        raise ValueError("scalar transport omitted bodies require the dedicated foreign-key boundary")
    state = ScalarAccess(connection)
    if state.require_authority(authority_uuid) != "REPLICA":
        raise ScalarAuthorityError("scalar transport receipts require a bound replica")
    connection.execute("INSERT OR IGNORE INTO scalar_sources(name) VALUES(?)", (receipt.namespace,))
    source_id = connection.execute("SELECT id FROM scalar_sources WHERE name=?", (receipt.namespace,)).fetchone()[0]
    connection.execute("INSERT OR IGNORE INTO scalar_receipts VALUES(?,?,?)", (source_id, receipt.original_id, receipt.record_id))
