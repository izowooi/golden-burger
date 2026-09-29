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


PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 96 * 1024 * 1024


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
        errors = {
            "CorruptPayloadError": CorruptPayloadError,
            "ObservationConflictError": ObservationConflictError,
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

    def stats(self) -> dict[str, int]:
        return self._request("stats")

    def close(self) -> None:
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
