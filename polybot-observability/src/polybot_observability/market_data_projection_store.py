"""Bounded typed projection groups using the existing shared-store authority.

Provider source-clock TEXT indexes support lexical ranges, not inferred UTC or
receipt-time order. Strategy time windows continue using their private clocks.
"""

from __future__ import annotations

from contextlib import contextmanager

import hashlib
import marshal
import sys
from types import MappingProxyType
import zlib

from .market_data_scalar_store import ScalarAccess
from .market_data_scalars import (
    ScalarAuthorityError,
    scalar_cell_bytes,
    validate_authority,
)
from .market_data_store import MAX_BATCH_ITEMS, StoreLimitError
from .market_data_projections import (
    PROJECTION_TABLE_SQL,
    PROJECTION_INDEX_SQL,
    PROJECTION_TRIGGER_SQL,
    PROJECTION_BLOCK_ROWS,
    MAX_PROJECTION_BYTES,
    MAX_PROJECTION_PACKED_BYTES,
    PROJECTION_PACK_TARGET_BYTES,
    MAX_PROJECTION_RESULTS,
    PROJECTION_CACHE_BYTES,
    PublicProjection,
    ProjectionReference,
    ProjectionRecord,
    ProjectionReceipt,
    MissingProjectionRecordError,
    CorruptProjectionError,
    ProjectionRecordConflictError,
    validate_projection_ids,
    validate_public_projections,
    validate_projection_records,
    validate_projection_receipts,
    projection_receipt_keys,
    projection_profile,
    projection_schema_sha,
    projection_payload,
    projection_index_bytes,
    index_positions,
    reconstruct_projection,
    encode_projection_cells,
    decode_projection_cells,
    unpack_projection_block,
    projection_hot_sha,
    projection_block_sha,
    PROJECTION_STORAGE_CONTRACT,
    MAX_UNKEYED_CANDIDATES,
    validate_projection_storage_schema,
    _validate_projection_version,
)

_INDEX = (
    "SELECT r.id,k.kind,c.condition_id,t.token_id,r.source_time,r.block_id,r.ordinal,r.condition_key,c.id,r.token_key,t.id,u.record_id,u.content_sha "
    "FROM projection_records r LEFT JOIN projection_kinds k ON k.id=r.kind_key "
    "LEFT JOIN scalar_conditions c ON c.id=r.condition_key "
    "LEFT JOIN projection_tokens t ON t.id=r.token_key LEFT JOIN projection_unkeyed u ON u.record_id=r.id"
)
_RECEIPT_SELECT = (
    "SELECT n.name,s.origin_table,r.original_id,k.kind,r.record_id FROM projection_receipts r "
    "JOIN projection_receipt_streams s ON s.id=r.stream_id "
    "JOIN scalar_sources n ON n.id=s.source_id JOIN projection_kinds k ON k.id=s.kind_key"
)
PUT_PROOF_RESERVE_BYTES = 1 << 20


class ProjectionAccess(ScalarAccess):
    """Reuse transaction/capability discipline, never the five-cell scalar codec."""

    def __init__(
        self,
        connection,
        authority_access,
        *,
        writable=False,
        cache_bytes=PROJECTION_CACHE_BYTES,
    ):
        super().__init__(connection, writable=False, cache_bytes=cache_bytes)
        self.writable = writable
        self.authority_access = authority_access
        self._put_phase = None
        if writable:
            self.db.create_function("projection_write_mode", 0, lambda: self._mode)
            self.db.create_function(
                "projection_typed_cell", 1, scalar_cell_bytes, deterministic=True
            )

    def initialize(self):
        if not self.writable or not self.db.in_transaction:
            raise ValueError(
                "projection initialization requires the owner's schema transaction"
            )
        present = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name GLOB 'projection_*' LIMIT 1"
        ).fetchone()
        if present:
            validate_projection_storage_schema(self.db)
        for kind, definitions in (
            ("TABLE", PROJECTION_TABLE_SQL),
            ("INDEX", PROJECTION_INDEX_SQL),
            ("TRIGGER", PROJECTION_TRIGGER_SQL),
        ):
            for name, sql in definitions.items():
                statement = sql.replace(
                    "CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ", 1
                )
                statement = statement.replace(
                    "CREATE " + kind + " ", "CREATE " + kind + " IF NOT EXISTS ", 1
                )
                self.db.execute(statement)
                row = self.db.execute(
                    "SELECT sql FROM sqlite_master WHERE name=?", (name,)
                ).fetchone()

                def key(value):
                    from .market_data_sql_schema import canonical_sql_key
                    return canonical_sql_key(value)

                if row is None or key(row[0]) != key(sql):
                    raise CorruptProjectionError(
                        "unsupported projection schema definition: " + name
                    )
        if not present:
            self.db.execute(
                "INSERT INTO projection_storage VALUES(1,?)",
                (PROJECTION_STORAGE_CONTRACT,),
            )
        _validate_projection_version(self.db)

    def available(self):
        tables = {
            row[0]
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        present = tables.intersection(PROJECTION_TABLE_SQL)
        if present and present != set(PROJECTION_TABLE_SQL):
            raise CorruptProjectionError("projection storage schema is incomplete")
        if present:
            _validate_projection_version(self.db)
        return bool(present)

    def _claim(self, role, authority_uuid=None):
        if any(
            self.db.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone()
            for table in (
                "projection_records",
                "projection_hot",
                "projection_blocks",
                "projection_receipts",
                "projection_unkeyed",
            )
        ):
            raise ScalarAuthorityError("projection data already bind the authority")
        return self.authority_access._claim(role, authority_uuid)

    def _kind(self, kind, *, insert=False):
        try:
            profile = projection_profile(kind)
        except ValueError as error:
            raise CorruptProjectionError(
                "projection kind dictionary is invalid"
            ) from error
        expected = projection_schema_sha(kind)
        row = self.db.execute(
            "SELECT schema_sha FROM projection_kinds WHERE kind=?", (kind,)
        ).fetchone()
        if row is None and insert:
            self.db.execute(
                "INSERT INTO projection_kinds(kind,schema_sha) VALUES(?,?)",
                (kind, expected),
            )
        elif row is None or type(row[0]) is not bytes or row[0] != expected:
            raise CorruptProjectionError("projection kind schema is missing or changed")
        if profile.source_time_field is not None:
            field = profile.fields[profile.columns.index(profile.source_time_field)]
            if field.storage != "TEXT":
                raise ValueError(
                    "this projection storage version indexes exact provider TEXT clocks only"
                )
        return profile

    def _kind_id(self, kind, *, insert=False):
        self._kind(kind, insert=insert)
        return self.db.execute(
            "SELECT id FROM projection_kinds WHERE kind=?", (kind,)
        ).fetchone()[0]

    def _stream_id(self, receipt):
        self.db.execute(
            "INSERT OR IGNORE INTO scalar_sources(name) VALUES(?)", (receipt.namespace,)
        )
        source = self.db.execute(
            "SELECT id FROM scalar_sources WHERE name=?", (receipt.namespace,)
        ).fetchone()[0]
        key = (source, receipt.origin_table, self._kind_id(receipt.kind))
        self.db.execute(
            "INSERT OR IGNORE INTO projection_receipt_streams(source_id,origin_table,kind_key) VALUES(?,?,?)",
            key,
        )
        return self.db.execute(
            "SELECT id FROM projection_receipt_streams WHERE source_id=? AND origin_table=? AND kind_key=?",
            key,
        ).fetchone()[0]

    def _record(self, authority, record_id, projection):
        return ProjectionRecord(
            ProjectionReference(
                authority, projection.kind, record_id, projection.sha256
            ),
            projection,
        )

    def _check_subject_keys(self, index):
        if index[7] != index[8] or index[9] != index[10]:
            raise CorruptProjectionError(
                "projection subject dictionary dependency is missing"
            )

    def _check_unkeyed(self, index, projection):
        required = index[7] is None and index[9] is None and index[4] is None
        if required:
            if index[11] != index[0] or index[12] != bytes.fromhex(projection.sha256):
                raise CorruptProjectionError(
                    "projection unkeyed content index is missing or changed"
                )
        elif index[11] is not None:
            raise CorruptProjectionError(
                "indexed projection unexpectedly has an unkeyed content row"
            )

    def _hot(self, authority, index):
        self._check_subject_keys(index)
        record_id, kind, condition, token, source_time, block_id, ordinal = index[:7]
        self._kind(kind)
        row = self.db.execute(
            "SELECT CASE WHEN typeof(cells)='blob' AND length(cells)<=? THEN cells END,row_sha "
            "FROM projection_hot WHERE record_id=?",
            (MAX_PROJECTION_BYTES, record_id),
        ).fetchone()
        try:
            if row is None or block_id is not None or ordinal is not None:
                raise ValueError("incomplete projection hot data")
            if type(row[0]) is not bytes or type(row[1]) is not bytes or len(row[1]) != 32:
                raise ValueError("projection hot body/checksum type mismatch")
            key = marshal.dumps(("verified-projection-hot-v1", authority, tuple(index),
                                 row[1], hashlib.sha256(row[0]).digest()), 2)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key][0][0]
            payload = decode_projection_cells(kind, row[0], 1)[0]
            projection = reconstruct_projection(
                kind, condition, token, source_time, payload
            )
            if row[1] != projection_hot_sha(record_id, projection):
                raise ValueError("projection hot identity checksum mismatch")
            self._check_unkeyed(index, projection)
            record = self._record(authority, record_id, projection)
            self._remember_block(key, (record,))
            return record
        except (ValueError, TypeError, OverflowError) as error:
            raise CorruptProjectionError(
                "projection hot row/index checksum mismatch"
            ) from error

    @staticmethod
    def _block_cache_key(authority, block_id, block, rows):
        kind, sha, count, raw_size, body = block
        # This is an in-process cache key, never a persisted/wire format. Format
        # 2 has exact scalar type/binary-float encoding without object-reference
        # aliasing; marshal.dumps performs the large index walk in C. No loads
        # or executable objects are involved. All cells originate in SQLite.
        return marshal.dumps((
            "verified-projection-records-v1", authority,
            (block_id, kind, sha, count, raw_size), hashlib.sha256(body).digest(),
            tuple(tuple(row) for row in rows),
        ), 2)

    @staticmethod
    def _cache_entry_bytes(key, records, by_sha=None):
        # Count the complete retained object graph, including dataclass instance
        # dictionaries and the exact typed index-key bytes. Shared objects are counted
        # once within an entry; counting them again across entries is conservative.
        seen = set()

        def size(value):
            identity = id(value)
            if identity in seen:
                return 0
            seen.add(identity)
            total = sys.getsizeof(value)
            if isinstance(value, dict):
                return total + sum(size(k) + size(v) for k, v in value.items())
            if isinstance(value, (tuple, frozenset)):
                return total + sum(size(item) for item in value)
            if isinstance(value, (ProjectionRecord, ProjectionReference, PublicProjection)):
                return total + size(vars(value))
            return total

        # OrderedDict node, stored (records, tracked_size), and allocator slack.
        return size(key) + size(records) + size(by_sha) + 512

    def _evict_cache_entry(self):
        key, entry = self.cache.popitem(last=False)
        self.cache_bytes -= entry[1]

    def _remember_block(self, key, records):
        records = tuple(records)
        by_sha = {}
        for record in records:
            by_sha.setdefault(record.reference.sha256, record)
        size = self._cache_entry_bytes(key, records, by_sha)
        if size <= self.cache_limit:
            while self.cache and self.cache_bytes + size > self.cache_limit:
                self._evict_cache_entry()
            self.cache[key] = records, size, MappingProxyType(by_sha)
            self.cache_bytes += size
        return records

    @contextmanager
    def _put_proof_scope(self, authority, values):
        """Memoize only this writer's dedup loop; recheck before its COMMIT.

        BEGIN IMMEDIATE excludes other writers. Native dedup only adds HOT
        rows/dictionaries; packing happens after memo reuse is disabled. Every
        reused cold block is nevertheless re-read at scope exit, detecting even
        same-transaction corruption injected after its initial proof. Small
        proof digests survive record-cache eviction, within the same byte budget.
        """
        if not self.writable or not self.db.in_transaction or self._mode != "put" or self._put_phase is not None:
            raise ValueError("projection dedup proof scope requires its owned put transaction")
        old_limit = self.cache_limit
        reserve = min(PUT_PROOF_RESERVE_BYTES, old_limit // 8)
        self.cache_limit = old_limit - reserve
        while self.cache and self.cache_bytes > self.cache_limit:
            self._evict_cache_entry()
        desired = frozenset(value.sha256 for value in values)
        initial_size = self._cache_entry_bytes(("put-proof-state", desired), (), ({}, {}))
        if initial_size > reserve:
            desired = frozenset()
        phase = dict(authority=authority, enabled=bool(desired), desired=desired,
                     proofs={}, matches={}, reserve=reserve)
        self._put_phase = phase
        try:
            yield phase
            phase["enabled"] = False
            phase["matches"].clear()
            if not self.db.in_transaction or self._mode != "put":
                raise ValueError("projection dedup transaction ended before verification")
            if self.require_authority(authority) != "ORIGIN":
                raise ScalarAuthorityError("projection put authority role changed")
            for block_id, expected in phase["proofs"].items():
                def verify(key, expected=expected):
                    if hashlib.sha256(key).digest() != expected:
                        raise CorruptProjectionError("projection cold block changed during put deduplication")
                self._block(authority, block_id, _proof_callback=verify)
        finally:
            self._put_phase = None
            phase["matches"].clear()
            phase["proofs"].clear()
            phase["desired"] = frozenset()
            self.cache_limit = old_limit

    def _find_cold_reference(self, authority, block_id, desired_sha):
        phase = self._put_phase
        enabled = phase is not None and phase["enabled"] and desired_sha in phase["desired"]
        if enabled:
            if authority != phase["authority"] or not self.db.in_transaction or self._mode != "put":
                raise ValueError("projection dedup proof scope owner changed")
            if block_id in phase["proofs"]:
                match = phase["matches"].get(desired_sha)
                return match[1] if match is not None and match[0] == block_id else None
        checked_key = []
        records = self._block(authority, block_id, _proof_callback=checked_key.append)
        key = checked_key[0]
        if key in self.cache:
            by_sha = self.cache[key][2]
        else:
            by_sha = {record.reference.sha256: record for record in records.values()}
        selected = by_sha.get(desired_sha)
        if enabled:
            # One complete current proof establishes membership (including
            # absence) for every immutable input SHA in this put. Keep only
            # relevant references, never whole records after cache eviction.
            matches = dict(phase["matches"])
            collision = False
            for sha in phase["desired"]:
                record = by_sha.get(sha)
                if record is None:
                    continue
                previous = matches.get(sha)
                if previous is not None and previous[0] != block_id:
                    # An imported source may contain duplicate-content IDs in
                    # different blocks. Preserve original per-block lookup
                    # semantics instead of treating a partially retained block
                    # as a proof of absence.
                    collision = True
                    break
                matches[sha] = (block_id, record.reference)
            if not collision:
                proofs = dict(phase["proofs"])
                proofs[block_id] = hashlib.sha256(key).digest()
                retained = self._cache_entry_bytes(("put-proof-state", phase["desired"]), (), (proofs, matches))
                if retained <= phase["reserve"]:
                    phase["proofs"], phase["matches"] = proofs, matches
        return None if selected is None else selected.reference

    def _block(self, authority, block_id, *, _proof_callback=None):
        block = self.db.execute(
            "SELECT k.kind,b.row_sha,b.row_count,b.raw_size,CASE WHEN typeof(b.body)='blob' AND length(b.body)<=? THEN b.body END "
            "FROM projection_blocks b LEFT JOIN projection_kinds k ON k.id=b.kind_key WHERE b.id=?",
            (MAX_PROJECTION_PACKED_BYTES, block_id),
        ).fetchone()
        if block is None:
            raise CorruptProjectionError("projection cold block is missing")
        kind, sha, count, raw_size, body = block
        self._kind(kind)
        if (type(sha) is not bytes or len(sha) != 32 or type(body) is not bytes
                or len(body) > MAX_PROJECTION_PACKED_BYTES
                or type(count) is not int or not 1 <= count <= PROJECTION_BLOCK_ROWS
                or type(raw_size) is not int or not 0 <= raw_size <= MAX_PROJECTION_BYTES):
            raise CorruptProjectionError("projection block metadata types/bounds are invalid")
        rows = self.db.execute(
            _INDEX + " WHERE r.block_id=? ORDER BY r.ordinal LIMIT ?",
            (block_id, PROJECTION_BLOCK_ROWS + 1),
        ).fetchall()
        if (
            len(rows) != count
            or [row[6] for row in rows] != list(range(count))
            or any(row[1] != kind for row in rows)
        ):
            raise CorruptProjectionError("projection block kind/index coverage changed")
        for row in rows:
            self._check_subject_keys(row)
        # A cache hit still proves the current complete block/index identity and
        # absence of HOT leftovers in this read transaction. Never trust only a
        # data_version/total_changes cookie, nor just the selected record index.
        if self.db.execute(
            "SELECT 1 FROM projection_hot h JOIN projection_records r ON r.id=h.record_id WHERE r.block_id=? LIMIT 1",
            (block_id,),
        ).fetchone():
            raise CorruptProjectionError("projection cold data still contains a hot body")
        key = self._block_cache_key(authority, block_id, block, rows)
        if key in self.cache:
            self.cache.move_to_end(key)
            records = self.cache[key][0]
            if _proof_callback is not None:
                _proof_callback(key)
            return MappingProxyType({record.reference.record_id: record for record in records})
        payloads = unpack_projection_block(kind, body, raw_size, count)
        try:
            records = [
                self._record(
                    authority,
                    row[0],
                    reconstruct_projection(kind, row[2], row[3], row[4], payload),
                )
                for row, payload in zip(rows, payloads, strict=True)
            ]
        except (ValueError, TypeError, OverflowError) as error:
            raise CorruptProjectionError(
                "projection cold index metadata is invalid"
            ) from error
        for index, record in zip(rows, records, strict=True):
            self._check_unkeyed(index, record.projection)
        if projection_block_sha(records) != sha:
            raise CorruptProjectionError(
                "projection cold block/index checksum mismatch"
            )
        records = self._remember_block(key, records)
        if _proof_callback is not None:
            _proof_callback(key)
        return MappingProxyType({record.reference.record_id: record for record in records})

    def _records(self, ids, authority):
        if not self.available():
            if ids:
                raise MissingProjectionRecordError(list(dict.fromkeys(ids)))
            return []
        unique, indexes = list(dict.fromkeys(ids)), {}
        for offset in range(0, len(unique), 400):
            batch = unique[offset : offset + 400]
            for row in self.db.execute(
                _INDEX + " WHERE r.id IN (" + ",".join("?" for _ in batch) + ")", batch
            ):
                indexes[row[0]] = row
        missing = [record_id for record_id in unique if record_id not in indexes]
        if missing:
            raise MissingProjectionRecordError(missing)
        found, blocks, raw_bytes = {}, set(), 0
        for record_id, row in indexes.items():
            if row[5] is None:
                added = {record_id: self._hot(authority, row)}
            elif row[5] not in blocks:
                added = {
                    key: value
                    for key, value in self._block(authority, row[5]).items()
                    if key in indexes
                }
                blocks.add(row[5])
            else:
                continue
            raw_bytes += sum(
                record.projection.raw_bytes
                for key, record in added.items()
                if key not in found
            )
            if raw_bytes > MAX_PROJECTION_BYTES:
                raise StoreLimitError("projection read exceeds the byte bound")
            found.update(added)
        result = [found[record_id] for record_id in ids]
        validate_projection_records(result)
        return result

    def get_records(self, ids, authority_uuid):
        validate_projection_ids(ids)
        with self._read():
            self.require_authority(authority_uuid)
            return self._records(ids, authority_uuid)

    def _insert_hot(self, projection, record_id=None):
        kind_key = self._kind_id(projection.kind, insert=True)
        positions = index_positions(projection.kind)
        condition, token, source_time = (
            projection.values[index] if index is not None else None
            for index in positions
        )
        condition_key = self._condition(condition) if condition is not None else None
        token_key = None
        if token is not None:
            self.db.execute(
                "INSERT OR IGNORE INTO projection_tokens(token_id) VALUES(?)", (token,)
            )
            token_key = self.db.execute(
                "SELECT id FROM projection_tokens WHERE token_id=?", (token,)
            ).fetchone()[0]
        cursor = self.db.execute(
            "INSERT INTO projection_records(id,kind_key,condition_key,token_key,source_time) VALUES(?,?,?,?,?)",
            (record_id, kind_key, condition_key, token_key, source_time),
        )
        record_id = cursor.lastrowid if record_id is None else record_id
        if condition is None and token is None and source_time is None:
            self.db.execute(
                "INSERT INTO projection_unkeyed VALUES(?,?)",
                (record_id, bytes.fromhex(projection.sha256)),
            )
        cells = encode_projection_cells([projection_payload(projection)])
        self.db.execute(
            "INSERT INTO projection_hot VALUES(?,?,?)",
            (record_id, cells, projection_hot_sha(record_id, projection)),
        )
        return record_id

    def _find_identical(self, authority, projection):
        desired_sha = projection.sha256
        positions = index_positions(projection.kind)
        condition, token, source_time = (
            projection.values[index] if index is not None else None
            for index in positions
        )
        condition_key = token_key = None
        if condition is not None:
            row = self.db.execute(
                "SELECT id FROM scalar_conditions WHERE condition_id=?", (condition,)
            ).fetchone()
            if row is None:
                return None
            condition_key = row[0]
        if token is not None:
            known = self.db.execute(
                "SELECT id FROM projection_tokens WHERE token_id=?", (token,)
            ).fetchone()
            if known is None:
                return None
            token_key = known[0]
        if condition is None and token is None and source_time is None:
            candidates = self.db.execute(
                "SELECT record_id FROM projection_unkeyed WHERE content_sha=? ORDER BY record_id LIMIT ?",
                (bytes.fromhex(projection.sha256), MAX_UNKEYED_CANDIDATES + 1),
            ).fetchall()
            if len(candidates) > MAX_UNKEYED_CANDIDATES:
                raise StoreLimitError(
                    "unkeyed projection dedup candidates exceed the bounded limit"
                )
            ids = [row[0] for row in candidates]
            for offset in range(0, len(ids), MAX_BATCH_ITEMS):
                for record in self._records(
                    ids[offset : offset + MAX_BATCH_ITEMS], authority
                ):
                    if record.reference.sha256 == desired_sha:
                        return record.reference
            return None
        # A real token is the leading indexed key when no condition is present.
        # Exact nullable source times remain raw provider values.
        hint = (
            " INDEXED BY projection_records_token_time"
            if condition is None and token is not None
            else " INDEXED BY projection_records_condition_time"
        )
        query = _INDEX.replace(
            "projection_records r ", "projection_records r" + hint + " ", 1
        )
        cursor = self.db.execute(
            query
            + " WHERE r.condition_key IS ? AND r.source_time_text IS ? AND r.kind_key=(SELECT id FROM projection_kinds WHERE kind=?) AND r.token_key IS ?"
            + (
                " AND r.token_key IS NOT NULL"
                if condition is None and token is not None
                else ""
            )
            + " ORDER BY r.id",
            (condition_key, source_time, projection.kind, token_key),
        )
        checked_blocks = set()
        try:
            for index in cursor:
                if index[5] is None:
                    record = self._hot(authority, index)
                    if record.reference.sha256 == desired_sha:
                        return record.reference
                elif index[5] not in checked_blocks:
                    reference = self._find_cold_reference(authority, index[5], desired_sha)
                    if reference is not None:
                        return reference
                    checked_blocks.add(index[5])
        finally:
            cursor.close()
        return None

    def _pack(self, authority, kind):
        while True:
            # HOT is bounded independently of the potentially huge cold index.
            candidates = self.db.execute(
                "SELECT h.record_id,length(h.cells),c.condition_id,t.token_id,r.source_time FROM projection_hot h "
                "CROSS JOIN projection_records r ON r.id=h.record_id LEFT JOIN scalar_conditions c ON c.id=r.condition_key "
                "LEFT JOIN projection_tokens t ON t.id=r.token_key WHERE r.kind_key=(SELECT id FROM projection_kinds WHERE kind=?) ORDER BY h.record_id LIMIT ?",
                (kind, PROJECTION_BLOCK_ROWS),
            ).fetchall()
            candidates = [
                (row[0], row[1] + projection_index_bytes(kind, *row[2:]))
                for row in candidates
            ]
            if (
                len(candidates) < PROJECTION_BLOCK_ROWS
                and sum(row[1] for row in candidates) < PROJECTION_PACK_TARGET_BYTES
            ):
                return
            selected, size = [], 0
            for record_id, length in candidates:
                if selected and size + length > MAX_PROJECTION_BYTES:
                    break
                selected.append(record_id)
                size += length
            records = self._records(selected, authority)
            raw = encode_projection_cells(
                [projection_payload(record.projection) for record in records]
            )
            body = zlib.compress(raw, 9)
            if len(body) > MAX_PROJECTION_PACKED_BYTES:
                raise StoreLimitError("projection compressed block exceeds limit")
            with self._mode_scope("pack"):
                block_id = self.db.execute(
                    "INSERT INTO projection_blocks(kind_key,row_sha,row_count,raw_size,body) VALUES(?,?,?,?,?)",
                    (
                        self._kind_id(kind),
                        projection_block_sha(records),
                        len(records),
                        len(raw),
                        body,
                    ),
                ).lastrowid
                self.db.executemany(
                    "UPDATE projection_records SET block_id=?,ordinal=? WHERE id=?",
                    [
                        (block_id, ordinal, record_id)
                        for ordinal, record_id in enumerate(selected)
                    ],
                )
                self.db.executemany(
                    "DELETE FROM projection_hot WHERE record_id=?",
                    [(record_id,) for record_id in selected],
                )
            checked = self._block(authority, block_id)
            if any(checked[record.reference.record_id] != record for record in records):
                raise CorruptProjectionError(
                    "projection packing changed logical fields"
                )

    def put(self, values):
        validate_public_projections(values)
        if not values:
            return []
        values = tuple(values)  # The proof's desired SHA set is fixed for this call.
        with self._write("put"):
            authority, role = self.authority()
            if role == "REPLICA":
                raise ScalarAuthorityError(
                    "replicas cannot allocate projection record IDs"
                )
            if role == "UNCLAIMED":
                authority = self._claim("ORIGIN")
            references = []
            with self._put_proof_scope(authority, values) as phase:
                for value in values:
                    reference = self._find_identical(authority, value)
                    if reference is None:
                        reference = self._record(
                            authority, self._insert_hot(value), value
                        ).reference
                    references.append(reference)
                phase["enabled"] = False
                phase["matches"].clear()
                for kind in dict.fromkeys(value.kind for value in values):
                    self._pack(authority, kind)
                if [
                    record.reference
                    for record in self._records(
                        [ref.record_id for ref in references], authority
                    )
                ] != references:
                    raise CorruptProjectionError("projection ACK differs from stored data")
        return references

    def import_records(self, authority_uuid, records):
        validate_authority(authority_uuid)
        validate_projection_records(records)
        if any(record.reference.authority_uuid != authority_uuid for record in records):
            raise ScalarAuthorityError("projection import contains a foreign authority")
        with self._write("import"):
            current, role = self.authority()
            if role == "UNCLAIMED":
                self._claim("REPLICA", authority_uuid)
            elif role != "REPLICA" or current != authority_uuid:
                raise ScalarAuthorityError(
                    "projection import cannot replace a bound origin/foreign replica"
                )
            for record in records:
                record_id = record.reference.record_id
                if self.db.execute(
                    "SELECT 1 FROM projection_records WHERE id=?", (record_id,)
                ).fetchone():
                    if self._records([record_id], authority_uuid)[0] != record:
                        raise ProjectionRecordConflictError(
                            "projection ID conflicts with its exact kind/fields"
                        )
                else:
                    self._insert_hot(record.projection, record_id)
            for kind in dict.fromkeys(record.projection.kind for record in records):
                self._pack(authority_uuid, kind)
            if (
                self._records(
                    [record.reference.record_id for record in records], authority_uuid
                )
                != records
            ):
                raise CorruptProjectionError("projection import readback differs")

    def append_receipts(self, receipts, *, authority_uuid):
        validate_projection_receipts(receipts)
        with self._write("receipt"):
            self.require_authority(authority_uuid)
            records = {
                record.reference.record_id: record
                for record in self._records(
                    list(dict.fromkeys(row.record_id for row in receipts)),
                    authority_uuid,
                )
            }
            for receipt in receipts:
                if records[receipt.record_id].projection.kind != receipt.kind:
                    raise ProjectionRecordConflictError(
                        "projection receipt kind differs from its record"
                    )
                stream = self._stream_id(receipt)
                self.db.execute(
                    "INSERT OR IGNORE INTO projection_receipts VALUES(?,?,?)",
                    (stream, receipt.original_id, receipt.record_id),
                )

    def get_receipts(self, identities, *, authority_uuid):
        wanted = projection_receipt_keys(identities)
        with self._read():
            self.require_authority(authority_uuid)
            result = []
            for value in wanted:
                row = self.db.execute(
                    _RECEIPT_SELECT
                    + " WHERE n.name=? AND s.origin_table=? AND r.original_id=? AND k.kind=? AND r.record_id=?",
                    value.row(),
                ).fetchone()
                result.append(ProjectionReceipt(*row) if row else None)
            present = [row for row in result if row is not None]
            self._check_receipts(present, authority_uuid)
            return result

    def get_bound_records(self, identities, *, authority_uuid):
        """Read exact receipt bindings and their records in one read transaction.

        None denotes a missing requested receipt, never a missing/corrupt body.
        Only records named by present exact bindings are read; unrelated blocks
        are not prefetched. This avoids get_records + get_receipts validating
        the same current block/index twice at a native SQL view call site.
        """
        return [None if pair is None else pair[1] for pair in self.get_bound_pairs(
            identities, authority_uuid=authority_uuid
        )]

    def get_bound_pairs(self, identities, *, authority_uuid):
        """Return actual stored receipt + verified record from one transaction.

        The receipt comes from the database row, not an echo reconstructed by a
        service wrapper. This lets transport clients verify all five requested
        ownership fields independently from the public record identity.
        """
        wanted = projection_receipt_keys(identities)
        with self._read():
            self.require_authority(authority_uuid)
            present = []
            for value in wanted:
                row = self.db.execute(
                    _RECEIPT_SELECT
                    + " WHERE n.name=? AND s.origin_table=? AND r.original_id=? AND k.kind=? AND r.record_id=?",
                    value.row(),
                ).fetchone()
                present.append(ProjectionReceipt(*row) if row else None)
            ids = list(dict.fromkeys(row.record_id for row in present if row is not None))
            records = {
                record.reference.record_id: record
                for record in self._records(ids, authority_uuid)
            }
            if any(records[row.record_id].projection.kind != row.kind
                   for row in present if row is not None):
                raise CorruptProjectionError("projection receipt kind does not match its body")
            result = [None if row is None else (row, records[row.record_id]) for row in present]
            validate_projection_records([pair[1] for pair in result if pair is not None])
            return result

    def _check_receipts(self, receipts, authority):
        ids = list(dict.fromkeys(row.record_id for row in receipts))
        kinds = {}
        for offset in range(0, len(ids), MAX_BATCH_ITEMS):
            kinds.update(
                (record.reference.record_id, record.projection.kind)
                for record in self._records(
                    ids[offset : offset + MAX_BATCH_ITEMS], authority
                )
            )
        if any(kinds[row.record_id] != row.kind for row in receipts):
            raise CorruptProjectionError(
                "projection receipt kind does not match its body"
            )

    def iter_receipts(
        self,
        *,
        authority_uuid,
        namespace=None,
        origin_table=None,
        after=None,
        limit=1000,
        record_ids=None,
    ):
        if type(limit) is not int or not 1 <= limit <= MAX_PROJECTION_RESULTS:
            raise StoreLimitError("projection receipt result limit is invalid")
        clauses, parameters = [], []
        if namespace is not None:
            clauses.append("n.name=?")
            parameters.append(namespace)
        if origin_table is not None:
            clauses.append("s.origin_table=?")
            parameters.append(origin_table)
        if after is not None:
            cursor = projection_receipt_keys([after])[0]
            if namespace is not None and cursor.namespace != namespace:
                raise ValueError("projection cursor crosses namespace")
            if origin_table is not None and cursor.origin_table != origin_table:
                raise ValueError("projection cursor crosses origin table")
            clauses.append(
                "(n.name,s.origin_table,r.original_id,k.kind,r.record_id)>(?,?,?,?,?)"
            )
            parameters.extend(cursor.row())
        if record_ids is not None:
            validate_projection_ids(record_ids)
            if not record_ids:
                self.require_authority(authority_uuid)
                return iter(())
            clauses.append("r.record_id IN (" + ",".join("?" for _ in record_ids) + ")")
            parameters.extend(record_ids)
        with self._read():
            self.require_authority(authority_uuid)
            query = (
                _RECEIPT_SELECT
                + (" WHERE " + " AND ".join(clauses) if clauses else "")
                + " ORDER BY n.name,s.origin_table,r.original_id,k.kind,r.record_id LIMIT ?"
            )
            result = [
                ProjectionReceipt(*row)
                for row in self.db.execute(query, [*parameters, limit])
            ]
            self._check_receipts(result, authority_uuid)
            return iter(result)

    def query(
        self,
        *,
        authority_uuid,
        kind=None,
        condition_id=None,
        token_id=None,
        start=None,
        end=None,
        namespace=None,
        limit=1000,
    ):
        """Exact provider-clock TEXT [start,end) ranges; no chronology normalization."""
        if type(limit) is not int or not 1 <= limit <= MAX_PROJECTION_RESULTS:
            raise StoreLimitError("projection query result limit is invalid")
        if condition_id is None and token_id is None:
            raise ValueError("projection query requires an indexed condition or token")
        clauses, parameters = [], []
        if kind is not None:
            projection_profile(kind)
            clauses.append("r.kind_key=(SELECT id FROM projection_kinds WHERE kind=?)")
            parameters.append(kind)
        for value, key, table, column in (
            (condition_id, "condition_key", "scalar_conditions", "condition_id"),
            (token_id, "token_key", "projection_tokens", "token_id"),
        ):
            if value is not None:
                if type(value) is not str or len(value.encode()) > 4096:
                    raise ValueError("projection query identity must be bounded TEXT")
                clauses.append(f"r.{key}=(SELECT id FROM {table} WHERE {column}=?)")
                parameters.append(value)
        for operator, bound in ((">=", start), ("<", end)):
            if bound is not None:
                if type(bound) is not str or len(bound.encode()) > 4096:
                    raise ValueError(
                        "projection source-clock range must be raw bounded TEXT"
                    )
                clauses.append("r.source_time_text" + operator + "?")
                parameters.append(bound)
        if namespace is not None:
            clauses.append(
                "EXISTS(SELECT 1 FROM projection_receipts p JOIN projection_receipt_streams s ON s.id=p.stream_id JOIN scalar_sources n ON n.id=s.source_id WHERE p.record_id=r.id AND s.kind_key=r.kind_key AND n.name=?)"
            )
            parameters.append(namespace)
        with self._read():
            self.require_authority(authority_uuid)
            ids = [
                row[0]
                for row in self.db.execute(
                    "SELECT r.id FROM projection_records r WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY r.source_time_text,r.id LIMIT ?",
                    [*parameters, limit],
                )
            ]
            result, size = [], 0
            for offset in range(0, len(ids), MAX_BATCH_ITEMS):
                batch = self._records(
                    ids[offset : offset + MAX_BATCH_ITEMS], authority_uuid
                )
                size += sum(record.projection.raw_bytes for record in batch)
                if size > MAX_PROJECTION_BYTES:
                    raise StoreLimitError("projection query exceeds the byte bound")
                result.extend(batch)
            return iter(result)

    def stats(self):
        present = self.available()
        tables = {
            "kind_count": "projection_kinds",
            "token_count": "projection_tokens",
            "record_count": "projection_records",
            "hot_count": "projection_hot",
            "block_count": "projection_blocks",
            "receipt_count": "projection_receipts",
            "stream_count": "projection_receipt_streams",
        }
        return {
            key: self.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            if present
            else 0
            for key, table in tables.items()
        }


def insert_transport_projection_receipt(
    connection, receipt, *, authority_uuid, allow_missing_records=False
):
    """Only an explicitly isolated replica transport may omit a receipt's body."""
    validate_projection_receipts([receipt])
    if (
        allow_missing_records is not True
        or not connection.in_transaction
        or connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0
    ):
        raise ValueError(
            "projection transport receipts require an explicit FK-off transaction"
        )
    authority = ScalarAccess(connection)
    if authority.require_authority(authority_uuid) != "REPLICA":
        raise ScalarAuthorityError("projection transport requires a bound replica")
    schema = projection_schema_sha(receipt.kind)
    row = connection.execute(
        "SELECT schema_sha FROM projection_kinds WHERE kind=?", (receipt.kind,)
    ).fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO projection_kinds(kind,schema_sha) VALUES(?,?)",
            (receipt.kind, schema),
        )
    elif row[0] != schema:
        raise CorruptProjectionError("projection transport kind schema differs")
    existing = connection.execute(
        "SELECT k.kind FROM projection_records r LEFT JOIN projection_kinds k ON k.id=r.kind_key WHERE r.id=?",
        (receipt.record_id,),
    ).fetchone()
    if existing is not None and existing[0] != receipt.kind:
        raise ProjectionRecordConflictError("projection transport receipt kind differs")
    connection.execute(
        "INSERT OR IGNORE INTO scalar_sources(name) VALUES(?)", (receipt.namespace,)
    )
    source = connection.execute(
        "SELECT id FROM scalar_sources WHERE name=?", (receipt.namespace,)
    ).fetchone()[0]
    kind_key = connection.execute(
        "SELECT id FROM projection_kinds WHERE kind=?", (receipt.kind,)
    ).fetchone()[0]
    key = (source, receipt.origin_table, kind_key)
    connection.execute(
        "INSERT OR IGNORE INTO projection_receipt_streams(source_id,origin_table,kind_key) VALUES(?,?,?)",
        key,
    )
    stream = connection.execute(
        "SELECT id FROM projection_receipt_streams WHERE source_id=? AND origin_table=? AND kind_key=?",
        key,
    ).fetchone()[0]
    connection.execute(
        "INSERT OR IGNORE INTO projection_receipts VALUES(?,?,?)",
        (stream, receipt.original_id, receipt.record_id),
    )
