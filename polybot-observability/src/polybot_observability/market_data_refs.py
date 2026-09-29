"""Exact public payload references; strategy ledgers stay in their own databases.

Only explicitly classified columns may use ``externalize_row``. The reference is
published locally after the shared writer has durably acknowledged its payload.
A failed local transaction can leave an unused immutable payload, never a dangling
acknowledged reference. Garbage collection must therefore trace verified roots.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import os
import re
import threading
from typing import Mapping, Protocol, Sequence


PREFIX = "\x1ePMDATA1:"
_REFERENCE = re.compile(r"\x1ePMDATA1:([TB]):([0-9a-f]{64})\Z")


class PayloadAccess(Protocol):
    def put_many(self, values: list[bytes]) -> list[str]: ...
    def get_many(self, hashes: list[str]) -> list[bytes]: ...


class MissingMarketDataConfiguration(RuntimeError):
    pass


def parse_reference(value: object) -> tuple[str, str] | None:
    if isinstance(value, bytes):
        if not value.startswith(PREFIX.encode()):
            return None
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError:
            return None
    elif isinstance(value, str):
        text = value
    else:
        return None
    match = _REFERENCE.fullmatch(text)
    if not match:
        return None
    kind, digest = match.groups()
    # The SQLite column storage class must remain the same as the original.
    if (kind == "B") != isinstance(value, bytes):
        raise ValueError("public payload reference storage type mismatch")
    return kind, digest


class PayloadReferences:
    def __init__(self, reader: PayloadAccess | None = None,
                 writer: PayloadAccess | None = None, *, cache_bytes: int = 32 << 20):
        self.reader = reader
        self.writer = writer
        self.cache_bytes = cache_bytes
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._cached_bytes = 0

    def _remember(self, digest: str, raw: bytes) -> None:
        if len(raw) > self.cache_bytes or digest in self._cache:
            return
        while self._cache and self._cached_bytes + len(raw) > self.cache_bytes:
            _, evicted = self._cache.popitem(last=False)
            self._cached_bytes -= len(evicted)
        self._cache[digest] = raw
        self._cached_bytes += len(raw)

    def encode_many(self, values: Sequence[object]) -> list[object]:
        if self.writer is None:
            raise MissingMarketDataConfiguration("public market-data writer is not configured")
        encoded = list(values)
        indexes, raw_values = [], []
        for index, value in enumerate(values):
            if value is None:
                continue
            if not isinstance(value, (str, bytes)):
                raise TypeError("public body column must be TEXT, BLOB or NULL")
            if parse_reference(value):
                # Validate dependencies, rather than accepting a reference from a
                # different/missing store and publishing it as if persisted here.
                value = self.decode_many([value])[0]
            raw = value.encode("utf-8") if isinstance(value, str) else value
            indexes.append((index, isinstance(value, str)))
            raw_values.append(raw)
        hashes = self.writer.put_many(raw_values) if raw_values else []
        if len(hashes) != len(raw_values):
            raise ValueError("public writer acknowledgement count mismatch")
        for (index, is_text), raw, digest in zip(indexes, raw_values, hashes):
            if digest != hashlib.sha256(raw).hexdigest():
                raise ValueError("public writer acknowledgement digest mismatch")
            marker = PREFIX + ("T:" if is_text else "B:") + digest
            encoded[index] = marker if is_text else marker.encode("ascii")
            self._remember(digest, raw)
        return encoded

    def decode_many(self, values: Sequence[object]) -> list[object]:
        references = [parse_reference(value) for value in values]
        missing = list(dict.fromkeys(ref[1] for ref in references
                                     if ref and ref[1] not in self._cache))
        fetched: dict[str, bytes] = {}
        if missing:
            if self.reader is None:
                raise MissingMarketDataConfiguration("shared payload reference requires a market-data reader")
            bodies = self.reader.get_many(missing)
            if len(bodies) != len(missing):
                raise ValueError("public reader response count mismatch")
            for digest, raw in zip(missing, bodies):
                if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError("public reader payload digest mismatch")
                fetched[digest] = raw
                self._remember(digest, raw)
        decoded = list(values)
        for index, ref in enumerate(references):
            if ref:
                kind, digest = ref
                raw = fetched.get(digest)
                if raw is None:
                    raw = self._cache[digest]
                    self._cache.move_to_end(digest)
                decoded[index] = raw.decode("utf-8") if kind == "T" else raw
        return decoded


_local = threading.local()


def configured_references() -> PayloadReferences:
    """Resolve only explicit configuration; never create an internal fallback DB."""
    signature = tuple(os.environ.get(key, "") for key in (
        "PUBLIC_MARKET_DATA_DB", "PUBLIC_MARKET_DATA_SOCKET", "PUBLIC_MARKET_DATA_REQUIRED"))
    if getattr(_local, "signature", None) != signature:
        old = getattr(_local, "codec", None)
        if old and old.reader and hasattr(old.reader, "close"):
            old.reader.close()
        db, socket, required = signature
        if required and required not in ("0", "1"):
            raise ValueError("PUBLIC_MARKET_DATA_REQUIRED must be 0 or 1")
        if required == "1" and not socket:
            raise MissingMarketDataConfiguration("required market-data socket is missing")
        reader = writer = None
        if socket:
            from .market_data_client import StoreClient
            writer = StoreClient(socket)
            reader = writer
        if db:
            from .market_data_store import PayloadReader
            reader = PayloadReader(db)
        _local.codec = PayloadReferences(reader=reader, writer=writer)
        _local.signature = signature
    return _local.codec


def externalize_row(strategy: str, table: str, row: Mapping[str, object], *,
                    references: PayloadReferences | None = None) -> dict[str, object]:
    from .market_data_policy import public_columns
    columns = [name for name in row if name in public_columns(strategy, table)]
    if not columns:
        return dict(row)
    codec = references or configured_references()
    if codec.writer is None:
        # Legacy mode is only for unconverted jobs; migrated runtime launchers set
        # REQUIRED=1. A missing configured writer must never fall back to inline.
        if os.environ.get("PUBLIC_MARKET_DATA_DB"):
            raise MissingMarketDataConfiguration("read-only market-data configuration cannot write public bodies")
        return dict(row)
    encoded = codec.encode_many([row[name] for name in columns])
    return {**row, **dict(zip(columns, encoded))}
