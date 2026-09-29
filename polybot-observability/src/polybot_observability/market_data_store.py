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

    def get_many(self, hashes: list[str]) -> list[bytes]:
        return _get_many(self._connection, hashes)

    def stats(self) -> dict[str, int]:
        count, raw, stored = self._connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(raw_size), 0), "
            "COALESCE(SUM(length(body)), 0) FROM payloads"
        ).fetchone()
        observations = self._connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        return {"payload_count": count, "raw_bytes": raw, "stored_bytes": stored,
                "observation_count": observations}

    def iter_observations(self, token_id: str | None = None,
                          event_id: str | None = None, start: str | None = None,
                          end: str | None = None, observer: str | None = None,
                          limit: int = 1000) -> Iterator[Observation]:
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
        for column, value in (("token_id", token_id), ("event_id", event_id), ("observer", observer)):
            if value is not None:
                if not isinstance(value, str) or len(value) > 1024:
                    raise ValueError("observation filters must be bounded strings")
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
                yield Observation(observer=row[0], observation_id=row[1], observed_at=row[2],
                                  kind=row[3], event_id=row[4], token_id=row[5],
                                  payload_sha=row[6], metadata_json=row[7])
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
            for action in ("UPDATE", "DELETE"):
                self._connection.execute(
                    f"CREATE TRIGGER IF NOT EXISTS observations_no_{action.lower()} "
                    f"BEFORE {action} ON observations BEGIN "
                    "SELECT RAISE(ABORT, 'observations are append only'); END"
                )
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
                compressed = zlib.compress(raw, level=3)
                codec, body = ("zlib", compressed) if len(compressed) < len(raw) else ("identity", raw)
                self._connection.execute("INSERT INTO payloads VALUES (?, ?, ?, ?)",
                                         (sha, codec, len(raw), body))
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        return hashes

    def append_observations(self, observations: list[Observation]) -> None:
        if not isinstance(observations, list) or len(observations) > MAX_BATCH_ITEMS:
            raise StoreLimitError("observation batch must be a bounded list")
        rows = [observation.row() for observation in observations]
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            for row in rows:
                existing = self._connection.execute(
                    "SELECT observer, observation_id, observed_at, kind, event_id, token_id, "
                    "payload_sha, metadata_json FROM observations "
                    "WHERE observer=? AND observation_id=?", row[:2]
                ).fetchone()
                if existing is not None:
                    if existing != row:
                        raise ObservationConflictError("observation idempotency key conflicts")
                    continue
                if self._connection.execute(
                    "SELECT 1 FROM payloads WHERE sha256=?", (row[6],)
                ).fetchone() is None:
                    raise MissingPayloadError([row[6]])
                self._connection.execute("INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
