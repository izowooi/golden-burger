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
FAMILY_PREFIX = "\x1ePMDATA"
_REFERENCE = re.compile(r"\x1ePMDATA1:([TB]):([0-9a-f]{64})\Z")


def _is_local_packet(value: object) -> bool:
    return ((isinstance(value, str) and value.startswith('\x1ePMPKT')) or
            (isinstance(value, bytes) and value.startswith(b'\x1ePMPKT')))


class PayloadAccess(Protocol):
    def put_many(self, values: list[bytes]) -> list[str]: ...
    def get_many(self, hashes: list[str]) -> list[bytes]: ...


class MissingMarketDataConfiguration(RuntimeError):
    pass


def parse_reference(value: object) -> tuple[str, str] | None:
    if isinstance(value, bytes):
        if not value.startswith(FAMILY_PREFIX.encode()):
            return None
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError("malformed public payload reference") from error
    elif isinstance(value, str):
        text = value
        if not text.startswith(FAMILY_PREFIX):
            return None
    else:
        return None
    match = _REFERENCE.fullmatch(text)
    if not match:
        raise ValueError("unsupported or malformed public payload reference")
    kind, digest = match.groups()
    # The SQLite column storage class must remain the same as the original.
    if (kind == "B") != isinstance(value, bytes):
        raise ValueError("public payload reference storage type mismatch")
    return kind, digest


def is_external_value(value: object) -> bool:
    if _is_local_packet(value):
        return True
    if isinstance(value, bytes) and value.startswith((b'\x1ePMAPPLEFRAME',b'\x1ePMMIX')):
        return True
    if isinstance(value, str) and value.startswith('\x1ePMMIX'):
        return True
    return parse_reference(value) is not None


def value_reference_hashes(value: object) -> list[str]:
    if _is_local_packet(value):
        raise ValueError('private packet dependency requires its owning database')
    if isinstance(value,bytes) and value.startswith(b'\x1ePMAPPLEFRAME'):
        from .market_data_apple import frame_reference_hashes
        return frame_reference_hashes(value)
    if ((isinstance(value,bytes) and value.startswith(b'\x1ePMMIX')) or
        (isinstance(value,str) and value.startswith('\x1ePMMIX'))):
        from .market_data_mixed import mixed_reference_hashes
        return mixed_reference_hashes(value)
    ref=parse_reference(value)
    return [ref[1]] if ref else []


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
            if _is_local_packet(value):
                raise ValueError('private packet cannot be published as a public payload')
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
        # Mixed envelopes never enter the public payload table: only their
        # explicit source fragments are fetched to restore the local state.
        specialized = {}
        for index,value in enumerate(values):
            if _is_local_packet(value):
                raise ValueError('private packet resolution requires its owning database')
            if isinstance(value,bytes) and value.startswith(b'\x1ePMAPPLEFRAME'):
                from .market_data_apple import resolve_frame
                specialized[index]=resolve_frame(value,self)
            elif ((isinstance(value,bytes) and value.startswith(b'\x1ePMMIX')) or
                  (isinstance(value,str) and value.startswith('\x1ePMMIX'))):
                from .market_data_mixed import resolve_mixed_payload
                specialized[index]=resolve_mixed_payload(value,self)
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
        for index,value in specialized.items():
            decoded[index]=value
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
                    references: PayloadReferences | None = None, receipt_context=None) -> dict[str, object]:
    return externalize_rows(strategy,table,[row],references=references,receipt_context=receipt_context)[0]


def externalize_rows(strategy: str,table: str,rows,*,references: PayloadReferences | None=None,
                     receipt_context=None) -> list[dict]:
    """ACK bounded public-body batches before yielding any local row for publish.

    The ordinary bodies of a migration batch share one durable transaction.
    Mixed encoders prepare the private/public split, then ACK bounded batches
    after ordinary bodies so sibling gzip references can be reused exactly.
    """
    from .market_data_policy import public_columns
    from .market_data_mixed import mixed_columns,externalize_mixed_rows
    from .market_data_store import MAX_BATCH_BYTES,MAX_BATCH_ITEMS
    rows=list(rows)
    results=[dict(row) for row in rows]
    allowed=public_columns(strategy,table)
    apple_frame = strategy == 'golden-apple' and table == 'runs' and any('frame' in row for row in rows)
    if not any(name in allowed for row in rows for name in row) and not mixed_columns(strategy,table) and not apple_frame:
        return results
    codec = references or configured_references()
    if codec.writer is None:
        # Legacy mode is only for unconverted jobs; migrated runtime launchers set
        # REQUIRED=1. A missing configured writer must never fall back to inline.
        if os.environ.get("PUBLIC_MARKET_DATA_DB") or codec.reader is not None or references is not None:
            raise MissingMarketDataConfiguration("read-only market-data configuration cannot write public bodies")
        return results
    pending=[];locations=[];size=0
    def flush():
        nonlocal size
        if pending:
            encoded=codec.encode_many(pending)
            for (index,column),value in zip(locations,encoded):
                results[index][column]=value
        pending.clear();locations.clear();size=0
    for index,row in enumerate(rows):
        for column,value in row.items():
            if column not in allowed or value is None:
                continue
            if is_external_value(value):
                value=codec.decode_many([value])[0]
            raw_size=len(value.encode('utf-8')) if isinstance(value,str) else len(value)
            if pending and (len(pending)>=MAX_BATCH_ITEMS or size+raw_size>MAX_BATCH_BYTES):
                flush()
            pending.append(value);locations.append((index,column));size+=raw_size
    flush()
    for index,row in enumerate(rows):
        if apple_frame and row.get('frame') is not None:
            from .market_data_apple import externalize_frame
            results[index]['frame']=externalize_frame(row['frame'],codec,
                                                      raw_sha256=row.get('frame_sha256'),
                                                      raw_size=row.get('frame_raw_bytes'))
    results = externalize_mixed_rows(strategy, table, rows, results, codec)
    for index,row in enumerate(rows):
        if receipt_context is not None:
            from .market_data_index import index_public_row
            index_public_row(strategy,table,row,results[index],references=codec,context=receipt_context)
    return results
