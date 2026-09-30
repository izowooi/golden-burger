"""Bounded local IPC for public payloads; no network, SQL or trading API."""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import socket
import struct
import time

from .market_data_store import (
    MAX_BATCH_ITEMS, CorruptPayloadError, MissingPayloadError, Observation, ObservationConflictError,
    StoreError, StoreLimitError, validate_hashes, validate_payloads,
)
from .market_data_scalars import (
    CorruptScalarSnapshotError, MissingScalarRecordError, ScalarReceipt, ScalarReference, ScalarRecord,
    ScalarReceiptConflictError, ScalarRecordConflictError, ScalarAuthorityError, ScalarSnapshot,
    receipt_keys, validate_authority, validate_record_ids, validate_scalar_records,
    validate_scalar_receipts, validate_scalar_snapshots,
)


PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 96 * 1024 * 1024
SERVICE_CAPABILITIES = frozenset({
    "public-payload-cas-v1",
    "public-observation-subjects-v2",
    "public-scalar-block-store-v1",
    "public-projection-block-store-v1",
    "public-catalog-groups-v1",
    "public-projection-nullable-subjects-v2",
    "public-raw-watermelon-v401-v1",
    "public-raw-coconut-historical-v6-v1",
    "public-raw-pomegranate-research-full-v4-v1",
    "public-raw-raspberry-queue-echo-v3-v1",
    "public-raw-strawberry-last-mile-v1-v1",
    "public-raw-strawberry-followup-v2a-v4-v1",
    "public-raw-guava-research-v1-v1",
    "public-raw-coconut-recorder-v1-v1",
    "public-raw-cherry-shadow-resolution-v2-v1",
    "public-raw-watermelon-independent-raw-lifecycle-v1-v1",
    "public-raw-black-research-full-v2-v1",
    "public-projection-bound-reads-v1",
})


def validate_capability_names(values):
    if (not isinstance(values, (list, tuple, set, frozenset)) or len(values) > 64
            or any(not isinstance(value, str) or not value or len(value) > 128
                   or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-._" for character in value)
                   for value in values)):
        raise ValueError("service capabilities must be bounded public contract names")
    if len(set(values)) != len(values):
        raise ValueError("service capabilities cannot contain duplicates")
    return frozenset(values)


class ProtocolError(StoreError):
    pass


class ServiceBusyError(StoreError):
    pass


class ServiceUnavailableError(StoreError):
    pass


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("public payload service I/O deadline exceeded")
    return remaining


def _receive_exact(connection: socket.socket, size: int, deadline: float) -> bytes:
    result = bytearray()
    while len(result) < size:
        connection.settimeout(_remaining(deadline))
        chunk = connection.recv(min(size - len(result), 65_536))
        if not chunk:
            raise ProtocolError("incomplete public payload service frame")
        result.extend(chunk)
    return bytes(result)


def _receive_frame(connection: socket.socket, deadline: float, limit: int = MAX_FRAME_BYTES) -> dict:
    size = struct.unpack("!I", _receive_exact(connection, 4, deadline))[0]
    if not 0 < size <= limit:
        raise StoreLimitError("service frame exceeds byte limit")
    try:
        message = json.loads(_receive_exact(connection, size, deadline))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ProtocolError("invalid service JSON frame") from error
    if not isinstance(message, dict) or message.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported service protocol")
    return message


def _send_frame(connection: socket.socket, message: dict, deadline: float,
                limit: int = MAX_FRAME_BYTES) -> None:
    encoded = json.dumps(message, separators=(",", ":"), allow_nan=False).encode()
    if not 0 < len(encoded) <= limit:
        raise StoreLimitError("service frame exceeds byte limit")
    connection.settimeout(_remaining(deadline))
    connection.sendall(struct.pack("!I", len(encoded)) + encoded)


def _decode_payloads(encoded: object) -> list[bytes]:
    if not isinstance(encoded, list):
        raise ProtocolError("payloads must be a list")
    if len(encoded) > MAX_BATCH_ITEMS:
        raise StoreLimitError("payload batch exceeds item limit")
    try:
        payloads = [base64.b64decode(value, validate=True) for value in encoded]
    except (ValueError, TypeError, binascii.Error) as error:
        raise ProtocolError("invalid base64 payload") from error
    validate_payloads(payloads)
    return payloads


class StoreClient:
    """A fresh connection per call; callers may safely retry a timed-out put.

    A timeout has an unknown commit outcome. Only a successful response is an
    ACK; repeating the identical payloads or observation IDs is idempotent.
    """

    def __init__(self, socket_path: str | Path, timeout: float = 5.0,
                 *, max_frame_bytes: int = MAX_FRAME_BYTES):
        if timeout <= 0 or max_frame_bytes <= 0:
            raise ValueError("timeout and frame limit must be positive")
        self.socket_path = str(socket_path)
        self.timeout = timeout
        self.max_frame_bytes = max_frame_bytes
        self._closed = False
        self._observed_contracts = None

    def _request(self, operation: str, **fields):
        if self._closed:
            raise ServiceUnavailableError("public payload client is closed")
        deadline = time.monotonic() + self.timeout
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(_remaining(deadline))
            connection.connect(self.socket_path)
            _send_frame(connection, {"v": PROTOCOL_VERSION, "op": operation, **fields},
                        deadline, self.max_frame_bytes)
            response = _receive_frame(connection, deadline, self.max_frame_bytes)
        if response.get("ok") is True:
            if "result" not in response:
                raise ProtocolError("service ACK is missing a result")
            return response["result"]
        code = response.get("error")
        if code == "MissingPayloadError":
            hashes = response.get("hashes")
            validate_hashes(hashes)
            raise MissingPayloadError(hashes)
        if code == "MissingScalarRecordError":
            ids = response.get("record_ids")
            validate_record_ids(ids)
            raise MissingScalarRecordError(ids)
        if code in {"MissingProjectionRecordError", "CorruptProjectionError", "ProjectionRecordConflictError"}:
            from .market_data_projections import (
                MissingProjectionRecordError, CorruptProjectionError, ProjectionRecordConflictError,
                validate_projection_ids,
            )
            if code == "MissingProjectionRecordError":
                ids = response.get("record_ids")
                validate_projection_ids(ids)
                raise MissingProjectionRecordError(ids)
            error_type = {"CorruptProjectionError": CorruptProjectionError,
                          "ProjectionRecordConflictError": ProjectionRecordConflictError}[code]
            raise error_type(str(response.get("message", "public projection operation failed")))
        errors = {
            "CorruptPayloadError": CorruptPayloadError,
            "ObservationConflictError": ObservationConflictError,
            "CorruptScalarSnapshotError": CorruptScalarSnapshotError,
            "ScalarReceiptConflictError": ScalarReceiptConflictError,
            "ScalarRecordConflictError": ScalarRecordConflictError,
            "ScalarAuthorityError": ScalarAuthorityError,
            "StoreLimitError": StoreLimitError,
            "ServiceBusyError": ServiceBusyError,
            "ServiceUnavailableError": ServiceUnavailableError,
            "ProtocolError": ProtocolError,
            "StoreError": StoreError,
        }
        error_type = errors.get(code, ProtocolError)
        raise error_type(str(response.get("message", "public payload service rejected request")))

    def put_many(self, payloads: list[bytes]) -> list[str]:
        validate_payloads(payloads)
        result = self._request("put_many", payloads=[base64.b64encode(raw).decode("ascii") for raw in payloads])
        expected = [hashlib.sha256(raw).hexdigest() for raw in payloads]
        if result != expected:
            raise ProtocolError("service ACK payload hashes do not match request")
        return result

    def get_many(self, hashes: list[str]) -> list[bytes]:
        validate_hashes(hashes)
        result = _decode_payloads(self._request("get_many", hashes=hashes))
        if [hashlib.sha256(raw).hexdigest() for raw in result] != hashes:
            raise CorruptPayloadError("service payload hashes do not match request")
        return result

    def append_observations(self, observations: list[Observation]) -> None:
        for observation in observations:
            observation.row()
        self._request("append_observations", observations=[asdict(item) for item in observations])

    def scalar_authority_identity(self) -> str:
        return validate_authority(self._request("scalar_authority_identity"))

    def scalar_authority_role(self) -> str:
        role = self._request("scalar_authority_role")
        if role not in {"UNCLAIMED", "ORIGIN", "REPLICA"}:
            raise ProtocolError("service scalar authority role is invalid")
        return role

    def put_scalar_snapshots(self, snapshots: list[ScalarSnapshot]) -> list[ScalarReference]:
        validate_scalar_snapshots(snapshots)
        result = self._request("put_scalar_snapshots", snapshots=[item.to_wire() for item in snapshots])
        if not isinstance(result, list) or len(result) != len(snapshots):
            raise ProtocolError("service scalar ACK count differs from request")
        try:
            references = [ScalarReference.from_wire(value) for value in result]
        except (ValueError, TypeError) as error:
            raise ProtocolError("service scalar ACK has an invalid reference") from error
        if ([item.sha256 for item in references] != [item.sha256 for item in snapshots]
                or len({item.authority_uuid for item in references}) > 1):
            raise ProtocolError("service ACK scalar hashes/authority do not match request")
        return references

    def get_scalar_records(self, ids, authority_uuid) -> list[ScalarRecord]:
        validate_record_ids(ids)
        validate_authority(authority_uuid)
        result = self._request("get_scalar_records", record_ids=ids, authority_uuid=authority_uuid)
        if not isinstance(result, list) or len(result) != len(ids):
            raise ProtocolError("service scalar response count differs from request")
        try:
            records = [ScalarRecord.from_wire(value) for value in result]
        except (ValueError, TypeError, OverflowError) as error:
            raise CorruptScalarSnapshotError("service scalar response has invalid typed values") from error
        if any(record.reference.authority_uuid != authority_uuid or record.reference.record_id != record_id
               for record, record_id in zip(records, ids, strict=True)):
            raise CorruptScalarSnapshotError("service scalar identities differ from request")
        return records

    def import_scalar_records(self, authority_uuid, records) -> None:
        validate_authority(authority_uuid)
        validate_scalar_records(records)
        if any(record.reference.authority_uuid != authority_uuid for record in records):
            raise ScalarAuthorityError("scalar import contains a foreign authority")
        if self._request("import_scalar_records", authority_uuid=authority_uuid,
                         records=[record.to_wire() for record in records]) is not None:
            raise ProtocolError("service scalar import ACK is invalid")

    def append_scalar_receipts(self, receipts: list[ScalarReceipt], *, authority_uuid) -> None:
        validate_scalar_receipts(receipts)
        validate_authority(authority_uuid)
        if self._request("append_scalar_receipts", receipts=[item.to_wire() for item in receipts],
                         authority_uuid=authority_uuid) is not None:
            raise ProtocolError("service scalar receipt ACK is invalid")

    def get_scalar_receipts(self, identities, *, authority_uuid) -> list[ScalarReceipt | None]:
        expected = receipt_keys(identities)
        validate_authority(authority_uuid)
        result = self._request("get_scalar_receipts", identities=[list(value.row()) for value in expected],
                               authority_uuid=authority_uuid)
        if not isinstance(result, list) or len(result) != len(expected):
            raise ProtocolError("service scalar receipt response count differs from request")
        try:
            values = [None if item is None else ScalarReceipt.from_wire(item) for item in result]
        except (ValueError, TypeError) as error:
            raise ProtocolError("service scalar receipt response has invalid values") from error
        if any(actual is not None and actual != wanted for wanted, actual in zip(expected, values, strict=True)):
            raise ProtocolError("service scalar receipt identities do not match request")
        return values

    def scalar_stats(self) -> dict[str, int]:
        return self._request("scalar_stats")

    def put_public_projections(self, projections):
        from .market_data_projections import ProjectionReference, validate_public_projections
        validate_public_projections(projections)
        result = self._request("put_public_projections", projections=[row.to_wire() for row in projections])
        if not isinstance(result, list) or len(result) != len(projections):
            raise ProtocolError("service projection ACK count differs from request")
        try:
            refs = [ProjectionReference.from_wire(row) for row in result]
        except (TypeError, ValueError) as error:
            raise ProtocolError("service projection ACK has an invalid reference") from error
        if ([(ref.kind, ref.sha256) for ref in refs] != [(row.kind, row.sha256) for row in projections]
                or len({ref.authority_uuid for ref in refs}) > 1):
            raise ProtocolError("service projection ACK kind/hash/authority differs from request")
        return refs

    def get_projection_records(self, ids, authority_uuid):
        from .market_data_projections import ProjectionRecord, CorruptProjectionError, validate_projection_ids
        validate_authority(authority_uuid)
        validate_projection_ids(ids)
        result = self._request("get_projection_records", record_ids=ids, authority_uuid=authority_uuid)
        if not isinstance(result, list) or len(result) != len(ids):
            raise ProtocolError("service projection response count differs from request")
        try:
            records = [ProjectionRecord.from_wire(row) for row in result]
        except (TypeError, ValueError, OverflowError) as error:
            raise CorruptProjectionError("service projection response has invalid typed values") from error
        if any(row.reference.record_id != wanted or row.reference.authority_uuid != authority_uuid
               for row, wanted in zip(records, ids, strict=True)):
            raise CorruptProjectionError("service projection identities differ from request")
        return records

    def import_projection_records(self, authority_uuid, records):
        from .market_data_projections import validate_projection_records
        validate_authority(authority_uuid)
        validate_projection_records(records)
        if any(row.reference.authority_uuid != authority_uuid for row in records):
            raise ScalarAuthorityError("projection import contains a foreign authority")
        if self._request("import_projection_records", authority_uuid=authority_uuid,
                         records=[row.to_wire() for row in records]) is not None:
            raise ProtocolError("service projection import ACK is invalid")

    def append_projection_receipts(self, receipts, *, authority_uuid):
        from .market_data_projections import validate_projection_receipts
        validate_authority(authority_uuid)
        validate_projection_receipts(receipts)
        if self._request("append_projection_receipts", authority_uuid=authority_uuid,
                         receipts=[row.to_wire() for row in receipts]) is not None:
            raise ProtocolError("service projection receipt ACK is invalid")

    def get_projection_receipts(self, identities, *, authority_uuid):
        from .market_data_projections import ProjectionReceipt, validate_projection_receipts
        validate_authority(authority_uuid)
        if (not isinstance(identities, list) or len(identities) > MAX_BATCH_ITEMS
                or any(not isinstance(key, (list, tuple)) or len(key) != 5 for key in identities)):
            raise ValueError("projection receipt identities must be bounded five-cell keys")
        expected = [ProjectionReceipt(*key) for key in identities]
        validate_projection_receipts(expected)
        result = self._request("get_projection_receipts", authority_uuid=authority_uuid,
                               identities=[list(row.row()) for row in expected])
        if not isinstance(result, list) or len(result) != len(expected):
            raise ProtocolError("service projection receipt response count differs from request")
        try:
            values = [None if row is None else ProjectionReceipt.from_wire(row) for row in result]
        except (TypeError, ValueError) as error:
            raise ProtocolError("service projection receipt response has invalid values") from error
        if any(actual is not None and actual != wanted for actual, wanted in zip(values, expected, strict=True)):
            raise ProtocolError("service projection receipt identities differ from request")
        return values

    def projection_stats(self):
        return self._request("projection_stats")

    def get_projection_bound_records(self, identities, *, authority_uuid):
        """Read exact receipt-bound records; old daemons keep the prior read path."""
        from .market_data_projections import (
            ProjectionRecord, ProjectionReceipt, CorruptProjectionError, projection_receipt_keys,
            validate_projection_records,
        )
        validate_authority(authority_uuid)
        expected = projection_receipt_keys(identities)
        contracts = self._observed_contracts
        if contracts is None:
            contracts = self.capabilities()
        if "public-projection-bound-reads-v1" not in contracts:
            # This is only a compatibility path for the previous pair of read
            # operations. No unsupported write or unverified value is accepted.
            receipts = self.get_projection_receipts([row.row() for row in expected], authority_uuid=authority_uuid)
            ids = list(dict.fromkeys(row.record_id for row in receipts if row is not None))
            records = self.get_projection_records(ids, authority_uuid) if ids else []
            by_id = {row.reference.record_id: row for row in records}
            values = [None if receipt is None else by_id[receipt.record_id] for receipt in receipts]
        else:
            result = self._request("get_projection_bound_records", authority_uuid=authority_uuid,
                                   identities=[list(row.row()) for row in expected])
            if not isinstance(result, list) or len(result) != len(expected):
                raise ProtocolError("service bound projection response count differs from request")
            values = []
            try:
                for row, wanted in zip(result, expected, strict=True):
                    if row is None:
                        values.append(None)
                        continue
                    if not isinstance(row, dict) or set(row) != {"receipt", "record"}:
                        raise ValueError('bound response must include its actual receipt and record')
                    receipt = ProjectionReceipt.from_wire(row['receipt'])
                    if receipt != wanted:
                        raise ValueError('bound response receipt differs from the requested observation')
                    values.append(ProjectionRecord.from_wire(row['record']))
            except (ValueError, TypeError, OverflowError) as error:
                raise CorruptProjectionError("service bound projection response has invalid receipt or typed values") from error
        if any(row is not None and (row.reference.authority_uuid != authority_uuid
                or row.reference.record_id != wanted.record_id or row.reference.kind != wanted.kind)
               for row, wanted in zip(values, expected, strict=True)):
            raise CorruptProjectionError("service bound projection identities differ from request")
        validate_projection_records([row for row in values if row is not None])
        return values

    def capabilities(self) -> frozenset[str]:
        self._observed_contracts = None
        result = self._request("capabilities")
        if not isinstance(result, dict) or set(result) != {"protocol", "contracts"}:
            raise ProtocolError("service capability response has invalid fields")
        if type(result["protocol"]) is not int or result["protocol"] != PROTOCOL_VERSION:
            raise ProtocolError("service capability protocol differs from the client")
        try:
            actual = validate_capability_names(result["contracts"])
        except ValueError as error:
            raise ProtocolError("service capability contract names are invalid") from error
        self._observed_contracts = actual
        return actual

    def require_capabilities(self, contracts) -> frozenset[str]:
        required = validate_capability_names(contracts)
        # An old service may still respond to stats while lacking newer writes.
        # Do not silently fall back or kill that healthy process on a mismatch.
        actual = self.capabilities()
        missing = required - actual
        if missing:
            raise ProtocolError("public service upgrade required; missing contracts: "
                                + ", ".join(sorted(missing)))
        return actual

    def stats(self) -> dict[str, int]:
        return self._request("stats")

    def close(self) -> None:
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
