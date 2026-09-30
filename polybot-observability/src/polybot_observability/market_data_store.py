"""Durable exact-byte public market receipts, independent of trading ledgers.

Payload identity never encodes TEXT/BLOB conversion or a strategy's economic
interpretation. Those belong to the consumer's reference layer. Credentials,
private orders and account ledgers must never be supplied to this store.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import zlib


MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_BATCH_BYTES = 64 * 1024 * 1024
MAX_BATCH_ITEMS = 1024
APPLICATION_ID = 0x504D4453
SCHEMA_VERSION = 1
MAX_OBSERVATION_RESULTS = 10_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class StoreError(Exception):
    """A public payload operation failed; no successful ACK is implied."""


class MissingPayloadError(StoreError):
    def __init__(self, hashes: list[str]):
        self.hashes = hashes
        super().__init__(f"missing payloads: {', '.join(hashes)}")


class CorruptPayloadError(StoreError):
    pass


class StoreLimitError(StoreError):
    pass


class ObservationConflictError(StoreError):
    pass


def validate_hashes(hashes: list[str]) -> None:
    if not isinstance(hashes, list) or len(hashes) > MAX_BATCH_ITEMS:
        raise StoreLimitError("hash batch must be a bounded list")
    if any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in hashes):
        raise ValueError("payload SHA256 must be lowercase hexadecimal")


def validate_payloads(payloads: list[bytes]) -> None:
    if not isinstance(payloads, list) or len(payloads) > MAX_BATCH_ITEMS:
        raise StoreLimitError("payload batch must be a bounded list")
    total = 0
    for payload in payloads:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be exact original bytes")
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise StoreLimitError("payload exceeds byte limit")
        total += len(payload)
    if total > MAX_BATCH_BYTES:
        raise StoreLimitError("payload batch exceeds byte limit")


def _decode(sha: str, codec: str, raw_size: int, body: bytes) -> bytes:
    if not isinstance(raw_size, int) or not 0 <= raw_size <= MAX_PAYLOAD_BYTES:
        raise CorruptPayloadError(f"invalid payload size: {sha}")
    if not isinstance(body, bytes):
        raise CorruptPayloadError(f"invalid payload body: {sha}")
    try:
        if codec == "identity":
            raw = body
        elif codec == "zlib":
            decoder = zlib.decompressobj()
            raw = decoder.decompress(body, raw_size + 1)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise CorruptPayloadError(f"invalid compressed payload: {sha}")
        else:
            raise CorruptPayloadError(f"unknown payload codec: {sha}")
    except zlib.error as error:
        raise CorruptPayloadError(f"invalid compressed payload: {sha}") from error
    if len(raw) != raw_size or hashlib.sha256(raw).hexdigest() != sha:
        raise CorruptPayloadError(f"payload length or SHA256 mismatch: {sha}")
    return raw


def _get_many(connection: sqlite3.Connection, hashes: list[str]) -> list[bytes]:
    validate_hashes(hashes)
    found: dict[str, bytes] = {}
    missing = []
    total = 0
    for sha, occurrences in Counter(hashes).items():
        row = connection.execute(
            "SELECT codec, raw_size, body FROM payloads WHERE sha256 = ?", (sha,)
        ).fetchone()
        if row is None:
            missing.append(sha)
        else:
            if not isinstance(row[1], int) or not 0 <= row[1] <= MAX_PAYLOAD_BYTES:
                raise CorruptPayloadError(f"invalid payload size: {sha}")
            total += row[1] * occurrences
            if total > MAX_BATCH_BYTES:
                raise StoreLimitError("result batch exceeds byte limit")
            found[sha] = _decode(sha, *row)
    if missing:
        raise MissingPayloadError(missing)
    return [found[sha] for sha in hashes]


def _observation_time_us(value: str) -> int:
    if not isinstance(value, str):
        raise ValueError("observation time must be an ISO8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("observation time must include a timezone")
    elapsed = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return ((elapsed.days * 86_400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds)


@dataclass(frozen=True)
class Observation:
    """An observer's public raw receipt, never an order or confirmed fill."""

    observer: str
    observation_id: str
    observed_at: str
    kind: str
    payload_sha: str
    event_id: str | None = None
    token_id: str | None = None
    metadata_json: str = "{}"
    token_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()

    def subjects(self) -> tuple[tuple[str, str], ...]:
        pairs = []
        for kind, values in (("token", self.token_ids), ("event", self.event_ids)):
            if not isinstance(values, (tuple, list)) or len(values) > 16384:
                raise StoreLimitError("observation subject list exceeds limit")
            if any(not isinstance(value, str) or not value or len(value) > 256 for value in values):
                raise ValueError("public subject identifiers must be bounded strings")
            pairs.extend((kind, value) for value in values)
        return tuple(sorted(set(pairs)))

    def row(self) -> tuple:
        for value in (self.observer, self.observation_id, self.observed_at, self.kind):
            if not isinstance(value, str) or not value or len(value) > 1024:
                raise ValueError("observation identity fields must be nonempty bounded strings")
        for value in (self.event_id, self.token_id):
            if value is not None and (not isinstance(value, str) or len(value) > 1024):
                raise ValueError("event/token identifiers must be bounded strings")
        _observation_time_us(self.observed_at)
        validate_hashes([self.payload_sha])
        if not isinstance(self.metadata_json, str) or len(self.metadata_json.encode()) > 16_384:
            raise StoreLimitError("observation metadata exceeds byte limit")
        metadata = json.loads(self.metadata_json, parse_constant=lambda _: _invalid_json())
        if not isinstance(metadata, dict):
            raise ValueError("observation metadata must be a JSON object")
        self.subjects()
        return (
            self.observer, self.observation_id, self.observed_at, self.kind,
            self.event_id, self.token_id, self.payload_sha, self.metadata_json,
        )


def _invalid_json():
    raise ValueError("nonfinite JSON values are not supported")


class PayloadReader:
    """Read-only SQLite access with integrity validation on every retrieval."""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path).expanduser().resolve(strict=True)
        self._connection = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)
        self._connection.execute("PRAGMA query_only=ON")
        self._scalar_state = None
        self._projection_state = None
        try:
            self._validate_schema()
        except BaseException:
            self._connection.close()
            raise

    def _validate_schema(self) -> None:
        if (
            self._connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or self._connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
        ):
            raise StoreError("unsupported public payload store schema")
        from .market_data_projections import validate_projection_storage_schema
        validate_projection_storage_schema(self._connection)

    def get_many(self, hashes: list[str]) -> list[bytes]:
        return _get_many(self._connection, hashes)

    def _scalar_access(self):
        if self._scalar_state is None:
            from .market_data_scalar_store import ScalarAccess
            self._scalar_state = ScalarAccess(self._connection)
        return self._scalar_state

    def scalar_authority_identity(self) -> str:
        return self._scalar_access().authority()[0]

    def scalar_authority_role(self) -> str:
        return self._scalar_access().authority()[1]

    def get_scalar_records(self, ids, authority_uuid):
        return self._scalar_access().get_records(ids, authority_uuid)

    def get_scalar_receipts(self, identities, *, authority_uuid):
        return self._scalar_access().get_receipts(identities, authority_uuid=authority_uuid)

    def iter_scalar_receipts(self, *, authority_uuid, namespace=None, after=None, limit=1000, record_ids=None):
        return self._scalar_access().iter_receipts(authority_uuid=authority_uuid, namespace=namespace,
                                                   after=after, limit=limit, record_ids=record_ids)

    def query_scalar_snapshots(self, condition_id, *, authority_uuid, start=None, end=None, namespace=None, limit=1000):
        return self._scalar_access().query(condition_id, authority_uuid=authority_uuid, start=start,
                                           end=end, namespace=namespace, limit=limit)

    def scalar_stats(self):
        return self._scalar_access().stats()

    def _projection_access(self):
        if self._projection_state is None:
            from .market_data_projection_store import ProjectionAccess
            self._projection_state = ProjectionAccess(self._connection, self._scalar_access())
        return self._projection_state

    def get_projection_records(self, ids, authority_uuid):
        return self._projection_access().get_records(ids, authority_uuid)

    def get_projection_receipts(self, identities, *, authority_uuid):
        return self._projection_access().get_receipts(identities, authority_uuid=authority_uuid)

    def get_projection_bound_records(self, identities, *, authority_uuid):
        return self._projection_access().get_bound_records(identities, authority_uuid=authority_uuid)

    def get_projection_bound_pairs(self, identities, *, authority_uuid):
        return self._projection_access().get_bound_pairs(identities, authority_uuid=authority_uuid)

    def iter_projection_receipts(self, *, authority_uuid, namespace=None, origin_table=None, after=None, limit=1000, record_ids=None):
        return self._projection_access().iter_receipts(authority_uuid=authority_uuid, namespace=namespace,
            origin_table=origin_table, after=after, limit=limit, record_ids=record_ids)

    def query_projection_records(self, *, authority_uuid, kind=None, condition_id=None, token_id=None,
                                 start=None, end=None, namespace=None, limit=1000):
        return self._projection_access().query(authority_uuid=authority_uuid, kind=kind, condition_id=condition_id,
            token_id=token_id, start=start, end=end, namespace=namespace, limit=limit)

    def projection_stats(self):
        return self._projection_access().stats()

    def stats(self) -> dict[str, int]:
        count, raw, stored = self._connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(raw_size), 0), "
            "COALESCE(SUM(length(body)), 0) FROM payloads"
        ).fetchone()
        observations = self._connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        return {"payload_count": count, "raw_bytes": raw, "stored_bytes": stored,
                "observation_count": observations}

    def get_observations(self, identities: list[tuple[str, str]]) -> list[Observation | None]:
        """Lookup exact receipt identities, retaining input order and missingness.

        This is independent of timestamps, so backfill verification does not
        lose records that happen to share the same source clock.
        """
        if not isinstance(identities,list) or len(identities)>MAX_BATCH_ITEMS:
            raise StoreLimitError('observation identities must be a bounded list')
        keys=[]
        for identity in identities:
            if (not isinstance(identity,(tuple,list)) or len(identity)!=2
                    or any(not isinstance(x,str) or not x or len(x)>1024 for x in identity)):
                raise ValueError('observation lookup requires exact observer and observation_id')
            keys.append(tuple(identity))
        unique=list(dict.fromkeys(keys));found={}
        has_subjects=self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='observation_subject_sets'"
        ).fetchone() is not None
        for offset in range(0,len(unique),400):
            batch=unique[offset:offset+400]
            placeholders=','.join('(?,?)' for _ in batch)
            rows=self._connection.execute(
                'SELECT observer,observation_id,observed_at,kind,event_id,token_id,payload_sha,metadata_json '
                f'FROM observations WHERE (observer,observation_id) IN ({placeholders})',
                [value for pair in batch for value in pair],
            ).fetchall()
            for row in rows:
                subjects=self._connection.execute(
                    'SELECT m.subject_kind,m.subject_id FROM observation_subject_sets l '
                    'JOIN public_subject_set_members m ON m.set_sha=l.set_sha '
                    'WHERE l.observer=? AND l.observation_id=? ORDER BY m.subject_kind,m.subject_id',row[:2]
                ).fetchall() if has_subjects else ()
                found[row[:2]]=Observation(
                    observer=row[0],observation_id=row[1],observed_at=row[2],kind=row[3],
                    event_id=row[4],token_id=row[5],payload_sha=row[6],metadata_json=row[7],
                    token_ids=tuple(value for kind,value in subjects if kind=='token'),
                    event_ids=tuple(value for kind,value in subjects if kind=='event'),
                )
        return [found.get(key) for key in keys]

    def iter_observations(self, token_id: str | None = None,
                          event_id: str | None = None, start: str | None = None,
                          end: str | None = None, observer: str | None = None,
                          limit: int = 1000, *, kind: str | None = None) -> Iterator[Observation]:
        """Bounded receipts in chronological order, in the UTC interval [start,end).

        ISO8601 offsets and original timestamp strings are preserved. An exact
        integer-microsecond comparison avoids lexical offset mismatches and
        SQLite julianday's sub-millisecond rounding at interval boundaries.
        The reader must remain open while the iterator is consumed.
        """
        if type(limit) is not int or not 1 <= limit <= MAX_OBSERVATION_RESULTS:
            raise StoreLimitError("observation result limit must be between 1 and 10000")
        conditions = []
        parameters: list = []
        has_subjects = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='observation_subject_sets'"
        ).fetchone() is not None
        for column, value in (("token_id", token_id), ("event_id", event_id), ("observer", observer), ("kind",kind)):
            if value is not None:
                if not isinstance(value, str) or len(value) > 1024:
                    raise ValueError("observation filters must be bounded strings")
                if has_subjects and column in ("token_id", "event_id"):
                    conditions.append(
                        f"(observations.{column} = ? OR EXISTS (SELECT 1 FROM observation_subject_sets links "
                        "JOIN public_subject_set_members members ON members.set_sha=links.set_sha "
                        "WHERE links.observer=observations.observer AND links.observation_id=observations.observation_id "
                        "AND members.subject_kind=? AND members.subject_id=?))"
                    )
                    parameters.extend((value, column.removesuffix('_id'), value))
                else:
                    conditions.append(f"{column} = ?")
                    parameters.append(value)
        start_us = _observation_time_us(start) if start is not None else None
        end_us = _observation_time_us(end) if end is not None else None
        if start_us is not None and end_us is not None and start_us > end_us:
            raise ValueError("observation interval end precedes start")
        self._connection.create_function("receipt_time_us", 1, _observation_time_us, deterministic=True)
        for operator, bound in ((">=", start_us), ("<", end_us)):
            if bound is not None:
                conditions.append(f"receipt_time_us(observed_at) {operator} ?")
                parameters.append(bound)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        cursor = self._connection.execute(
            "SELECT observer, observation_id, observed_at, kind, event_id, token_id, "
            "payload_sha, metadata_json FROM observations" + where +
            " ORDER BY receipt_time_us(observed_at), observer, observation_id LIMIT ?",
            [*parameters, limit],
        )
        try:
            for row in cursor:
                subjects = self._connection.execute(
                    "SELECT m.subject_kind,m.subject_id FROM observation_subject_sets l "
                    "JOIN public_subject_set_members m ON m.set_sha=l.set_sha "
                    "WHERE l.observer=? AND l.observation_id=? ORDER BY m.subject_kind,m.subject_id", row[:2]
                ).fetchall() if has_subjects else ()
                yield Observation(observer=row[0], observation_id=row[1], observed_at=row[2],
                                  kind=row[3], event_id=row[4], token_id=row[5],
                                  payload_sha=row[6], metadata_json=row[7],
                                  token_ids=tuple(value for kind,value in subjects if kind=='token'),
                                  event_ids=tuple(value for kind,value in subjects if kind=='event'))
        finally:
            cursor.close()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class PayloadStore(PayloadReader):
    """One-thread writer; a successful return follows a FULL WAL commit."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        # Inspect an existing typed format read-only before opening a writer:
        # even a first schema read on a RW handle could recover a hot journal.
        # Unsupported development projection formats are never implicit upgrades.
        if self.path.exists():
            probe = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
            try:
                if probe.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1").fetchone():
                    if (probe.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                            or probe.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION):
                        raise StoreError("unsupported public payload store schema")
                    from .market_data_projections import validate_projection_storage_schema
                    validate_projection_storage_schema(probe)
            finally:
                probe.close()
        self._connection = sqlite3.connect(self.path, timeout=2.0, isolation_level=None)
        try:
            existing = self._connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if existing:
                self._validate_schema()
            if self._connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise StoreError("public payload store requires WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS payloads ("
                "sha256 TEXT PRIMARY KEY, codec TEXT NOT NULL CHECK(codec IN ('identity','zlib')), "
                "raw_size INTEGER NOT NULL CHECK(raw_size >= 0), body BLOB NOT NULL) WITHOUT ROWID"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS observations ("
                "observer TEXT NOT NULL, observation_id TEXT NOT NULL, observed_at TEXT NOT NULL, "
                "kind TEXT NOT NULL, event_id TEXT, token_id TEXT, payload_sha TEXT NOT NULL "
                "REFERENCES payloads(sha256), metadata_json TEXT NOT NULL, "
                "PRIMARY KEY(observer, observation_id)) WITHOUT ROWID"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS observations_token_time "
                "ON observations(token_id, observed_at)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS observations_event_time "
                "ON observations(event_id, observed_at)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS observations_owner_kind_time "
                "ON observations(observer, kind, observed_at)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS observations_payload_sha "
                "ON observations(payload_sha, observer, observation_id)"
            )
            # One shared subject set for a whole batch, not hundreds of copies
            # of a receipt. Repeated censuses reuse the same member index.
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS public_subject_sets (set_sha TEXT PRIMARY KEY) WITHOUT ROWID"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS public_subject_set_members ("
                "set_sha TEXT NOT NULL REFERENCES public_subject_sets(set_sha),subject_kind TEXT NOT NULL "
                "CHECK(subject_kind IN ('token','event')),subject_id TEXT NOT NULL,"
                "PRIMARY KEY(set_sha,subject_kind,subject_id)) WITHOUT ROWID"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS public_subject_lookup "
                "ON public_subject_set_members(subject_kind,subject_id,set_sha)"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS observation_subject_sets ("
                "observer TEXT NOT NULL,observation_id TEXT NOT NULL,set_sha TEXT NOT NULL "
                "REFERENCES public_subject_sets(set_sha),PRIMARY KEY(observer,observation_id),"
                "FOREIGN KEY(observer,observation_id) REFERENCES observations(observer,observation_id)) WITHOUT ROWID"
            )
            for action in ("UPDATE", "DELETE"):
                self._connection.execute(
                    f"CREATE TRIGGER IF NOT EXISTS observations_no_{action.lower()} "
                    f"BEFORE {action} ON observations BEGIN "
                    "SELECT RAISE(ABORT, 'observations are append only'); END"
                )
            from .market_data_scalars import initialize_scalar_schema
            self._scalar_state = initialize_scalar_schema(self._connection)
            from .market_data_projections import initialize_projection_schema
            self._projection_state = initialize_projection_schema(self._connection, self._scalar_state)
            self._connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self._connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.close()
            raise

    def put_many(self, payloads: list[bytes]) -> list[str]:
        validate_payloads(payloads)
        hashes = [hashlib.sha256(raw).hexdigest() for raw in payloads]
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            for sha, raw in zip(hashes, payloads):
                row = self._connection.execute(
                    "SELECT codec, raw_size, body FROM payloads WHERE sha256=?", (sha,)
                ).fetchone()
                if row is not None:
                    if _decode(sha, *row) != raw:
                        raise CorruptPayloadError(f"payload hash collision: {sha}")
                    continue
                # Level 6 reduces large repeated Gamma documents substantially
                # without changing their bytes or the stored zlib format.
                compressed = zlib.compress(raw, level=6)
                codec, body = ("zlib", compressed) if len(compressed) < len(raw) else ("identity", raw)
                self._connection.execute("INSERT INTO payloads VALUES (?, ?, ?, ?)",
                                         (sha, codec, len(raw), body))
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        return hashes

    def put_scalar_snapshots(self, values):
        return self._scalar_access().put(values)

    def import_scalar_records(self, authority_uuid, records):
        return self._scalar_access().import_records(authority_uuid, records)

    def append_scalar_receipts(self, values, *, authority_uuid):
        return self._scalar_access().append_receipts(values, authority_uuid=authority_uuid)

    def put_public_projections(self, values):
        return self._projection_access().put(values)

    def import_projection_records(self, authority_uuid, records):
        return self._projection_access().import_records(authority_uuid, records)

    def append_projection_receipts(self, values, *, authority_uuid):
        return self._projection_access().append_receipts(values, authority_uuid=authority_uuid)

    def append_observations(self, observations: list[Observation]) -> None:
        if not isinstance(observations, list) or len(observations) > MAX_BATCH_ITEMS:
            raise StoreLimitError("observation batch must be a bounded list")
        rows = [observation.row() for observation in observations]
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            for observation,row in zip(observations,rows):
                subjects = observation.subjects()
                set_sha = hashlib.sha256(json.dumps(subjects,separators=(',',':')).encode()).hexdigest() if subjects else None
                existing = self._connection.execute(
                    "SELECT observer, observation_id, observed_at, kind, event_id, token_id, "
                    "payload_sha, metadata_json FROM observations "
                    "WHERE observer=? AND observation_id=?", row[:2]
                ).fetchone()
                if existing is not None:
                    if existing != row:
                        raise ObservationConflictError("observation idempotency key conflicts")
                    linked = self._connection.execute(
                        "SELECT set_sha FROM observation_subject_sets WHERE observer=? AND observation_id=?", row[:2]
                    ).fetchone()
                    if (linked[0] if linked else None) != set_sha:
                        raise ObservationConflictError("observation subject identities conflict")
                    continue
                if self._connection.execute(
                    "SELECT 1 FROM payloads WHERE sha256=?", (row[6],)
                ).fetchone() is None:
                    raise MissingPayloadError([row[6]])
                self._connection.execute("INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
                if set_sha is not None:
                    if self._connection.execute("SELECT 1 FROM public_subject_sets WHERE set_sha=?", (set_sha,)).fetchone() is None:
                        self._connection.execute("INSERT INTO public_subject_sets VALUES(?)", (set_sha,))
                        self._connection.executemany("INSERT INTO public_subject_set_members VALUES(?,?,?)", [(set_sha,*pair) for pair in subjects])
                    self._connection.execute("INSERT INTO observation_subject_sets VALUES(?,?,?)", (*row[:2],set_sha))
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
