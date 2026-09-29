"""Owner-local single-writer service for public raw market receipts.

Run with ``python -m polybot_observability.market_data_service --db ...
--socket ... --storage-root ...``. The storage root must already exist; there
is no disk fallback. ``--expected-volume-id`` checks an operator-provisioned
``.polybot-market-data-volume-id`` text marker on that root before each request.
The socket parent must be an owner-private directory.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
import errno
import fcntl
import os
from pathlib import Path
import queue
import signal
import socket
import stat
import threading
import time

from .market_data_client import (
    MAX_FRAME_BYTES, PROTOCOL_VERSION, ProtocolError, ServiceBusyError,
    ServiceUnavailableError, _decode_payloads, _receive_frame, _send_frame,
)
from .market_data_store import (
    MAX_BATCH_ITEMS, MissingPayloadError, Observation, PayloadStore, StoreError,
    StoreLimitError,
)


@dataclass
class _Request:
    message: dict
    deadline: float
    completed: threading.Event = field(default_factory=threading.Event)
    response: dict | None = None


def _error_response(error: Exception) -> dict:
    if isinstance(error, StoreError):
        response = {"v": PROTOCOL_VERSION, "ok": False,
                    "error": type(error).__name__, "message": str(error)}
    elif isinstance(error, (ValueError, TypeError, KeyError)):
        response = {"v": PROTOCOL_VERSION, "ok": False,
                    "error": "ProtocolError", "message": "invalid public payload request"}
    else:
        response = {"v": PROTOCOL_VERSION, "ok": False,
                    "error": "StoreError", "message": "public payload storage operation failed"}
    if isinstance(error, MissingPayloadError):
        response["hashes"] = error.hashes
    return response


class MarketDataService:
    """Bounded I/O workers feed one SQLite owner thread.

    Shutdown rejects requests not yet submitted and drains submitted requests.
    Expired requests may have committed without an ACK; identical puts are safe
    to retry. A success response is never generated before a durable commit.
    """

    def __init__(self, db_path: str | Path, socket_path: str | Path, *,
                 storage_root: str | Path, queue_size: int = 64, io_workers: int = 4,
                 timeout: float = 5.0, max_frame_bytes: int = MAX_FRAME_BYTES,
                 expected_volume_id: str | None = None,
                 min_free_bytes: int = 0, max_used_ratio: float = 1.0):
        if queue_size < 1 or io_workers < 1 or timeout <= 0 or max_frame_bytes < 1:
            raise ValueError("queue, worker, deadline and frame limits must be positive")
        if min_free_bytes < 0 or not 0 < max_used_ratio <= 1:
            raise ValueError("storage capacity limits are invalid")
        self.db_path = Path(db_path).expanduser()
        self.socket_path = Path(socket_path).expanduser()
        self.storage_root = Path(storage_root).expanduser()
        self.timeout = timeout
        self.max_frame_bytes = max_frame_bytes
        self.expected_volume_id = expected_volume_id
        self.min_free_bytes = min_free_bytes
        self.max_used_ratio = max_used_ratio
        self._io_workers = io_workers
        self._connections: queue.Queue = queue.Queue(queue_size)
        self._requests: queue.Queue = queue.Queue(queue_size)
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._submit_lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._locks: list[int] = []
        self._listener: socket.socket | None = None
        self._socket_identity: tuple[int, int] | None = None
        self._db_identity: tuple[int, int] | None = None
        self._startup_error: Exception | None = None
        self._started = False

    @staticmethod
    def _identity(path: Path) -> tuple[int, int]:
        info = path.stat()
        return info.st_dev, info.st_ino

    def _validate_paths(self) -> None:
        for path in (self.db_path, self.socket_path, self.storage_root):
            if not path.is_absolute():
                raise ValueError("service paths must be absolute")
            if path.resolve() != path:
                raise ValueError("service paths must be canonical without symlinks or '..'")
        self.storage_root = self.storage_root.resolve(strict=True)
        if not self.storage_root.is_dir():
            raise ValueError("storage root must be an existing directory")
        self.db_path = self.db_path.resolve()
        if not self.db_path.is_relative_to(self.storage_root) or self.db_path == self.storage_root:
            raise ValueError("database must be inside the configured storage root")
        if self.db_path == self.socket_path:
            raise ValueError("database and socket paths must differ")
        parts = self.storage_root.parts
        if len(parts) >= 3 and parts[1] == "Volumes":
            mount = Path(*parts[:3])
            if not mount.is_mount() or mount.stat().st_dev == Path("/").stat().st_dev:
                raise StoreError("external storage volume is not mounted")
        self._root_identity = self._identity(self.storage_root)
        self._check_volume_marker()

        existing_parent = self.db_path.parent
        while not existing_parent.exists():
            existing_parent = existing_parent.parent
        if existing_parent.stat().st_dev != self._root_identity[0]:
            raise StoreError("database path crosses a different storage volume")
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = self.socket_path.parent.stat()
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
            raise StoreError("socket parent must be owned by this user with mode 0700")
        self._check_storage()

    def _check_volume_marker(self) -> None:
        if self.expected_volume_id is None:
            return
        if not self.expected_volume_id or len(self.expected_volume_id) > 256:
            raise ValueError("expected volume ID must be a nonempty bounded string")
        marker = self.storage_root / ".polybot-market-data-volume-id"
        descriptor = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 257:
                raise StoreError("invalid storage volume identity marker")
            value = os.read(descriptor, 258).decode("utf-8").strip()
            if value != self.expected_volume_id:
                raise StoreError("storage volume identity mismatch")
        finally:
            os.close(descriptor)

    def _check_storage(self) -> None:
        if self.storage_root.resolve(strict=True) != self.storage_root:
            raise StoreError("storage root resolution changed")
        if self._identity(self.storage_root) != self._root_identity:
            raise StoreError("storage root identity changed")
        if self.db_path.parent.resolve(strict=True) != self.db_path.parent:
            raise StoreError("database parent resolution changed")
        if self.db_path.parent.stat().st_dev != self._root_identity[0]:
            raise StoreError("database volume identity changed")
        if self.db_path.is_symlink():
            raise StoreError("database path became a symlink")
        if self._db_identity is not None and self._identity(self.db_path) != self._db_identity:
            raise StoreError("database file identity changed")
        self._check_volume_marker()

    def _check_write_capacity(self) -> None:
        usage = os.statvfs(self.storage_root)
        free = usage.f_bavail * usage.f_frsize
        used_ratio = (usage.f_blocks - usage.f_bfree) / usage.f_blocks if usage.f_blocks else 1.0
        if free < self.min_free_bytes or used_ratio >= self.max_used_ratio:
            raise ServiceUnavailableError(
                f"shared market-data storage gate: free_bytes={free}, used_ratio={used_ratio:.6f}"
            )

    def _acquire_lock(self, path: Path) -> None:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise StoreError("unsafe public payload service lock")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise StoreError("another public payload service holds the writer/socket lock") from error
            os.fchmod(descriptor, 0o600)
        except BaseException:
            os.close(descriptor)
            raise
        self._locks.append(descriptor)

    def _bind(self) -> None:
        if self.socket_path.exists() or self.socket_path.is_symlink():
            info = self.socket_path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise StoreError("refusing to replace a non-owner socket path")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(min(self.timeout, 0.2))
                try:
                    probe.connect(str(self.socket_path))
                except OSError as error:
                    if error.errno not in (errno.ECONNREFUSED, errno.ENOENT):
                        raise StoreError("socket path has an active or unverified listener") from error
                else:
                    raise StoreError("socket path has an active listener")
            self.socket_path.unlink(missing_ok=True)
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self._socket_identity = self._identity(self.socket_path)
        self._listener.listen(self._connections.maxsize)
        self._listener.settimeout(0.1)

    def start(self):
        if self._started:
            raise StoreError("public payload service is already started")
        self._validate_paths()
        self._stopping.clear()
        self._ready.clear()
        self._startup_error = None
        try:
            self._acquire_lock(Path(str(self.db_path) + ".writer.lock"))
            self._acquire_lock(Path(str(self.socket_path) + ".lock"))
            self._started = True
            writer = threading.Thread(target=self._write_loop, name="market-data-writer", daemon=True)
            self._threads = [writer]
            writer.start()
            if not self._ready.wait(5.0):
                raise ServiceUnavailableError("public payload store startup timed out")
            if self._startup_error is not None:
                raise self._startup_error
            self._bind()
            for index in range(self._io_workers):
                worker = threading.Thread(target=self._io_loop, name=f"market-data-io-{index}", daemon=True)
                self._threads.append(worker)
                worker.start()
            acceptor = threading.Thread(target=self._accept_loop, name="market-data-accept", daemon=True)
            self._threads.append(acceptor)
            acceptor.start()
        except BaseException:
            self.stop()
            raise
        return self

    def _reject(self, connection: socket.socket, error: Exception, deadline: float) -> None:
        try:
            _send_frame(connection, _error_response(error), deadline, self.max_frame_bytes)
        except (OSError, StoreError, ValueError):
            pass

    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            deadline = time.monotonic() + self.timeout
            try:
                with self._submit_lock:
                    if self._stopping.is_set():
                        connection.close()
                        return
                    self._connections.put_nowait((connection, deadline))
            except queue.Full:
                with connection:
                    self._reject(connection, ServiceBusyError("connection queue is full"), deadline)

    def _io_loop(self) -> None:
        while not self._stopping.is_set() or not self._connections.empty():
            try:
                connection, deadline = self._connections.get(timeout=0.1)
            except queue.Empty:
                continue
            with connection:
                try:
                    if self._stopping.is_set():
                        raise ServiceUnavailableError("public payload service is stopping")
                    message = _receive_frame(connection, deadline, self.max_frame_bytes)
                    request = _Request(message, deadline)
                    with self._submit_lock:
                        if self._stopping.is_set():
                            raise ServiceUnavailableError("public payload service is stopping")
                        try:
                            self._requests.put_nowait(request)
                        except queue.Full as error:
                            raise ServiceBusyError("writer queue is full") from error
                    if not request.completed.wait(max(0, deadline - time.monotonic())):
                        raise ServiceUnavailableError("request deadline exceeded; commit outcome unknown")
                    _send_frame(connection, request.response, deadline, self.max_frame_bytes)
                except Exception as error:
                    self._reject(connection, error, deadline)
            self._connections.task_done()

    def _dispatch(self, store: PayloadStore, message: dict):
        operation = message.get("op")
        required = {
            "put_many": {"v", "op", "payloads"},
            "get_many": {"v", "op", "hashes"},
            "append_observations": {"v", "op", "observations"},
            "stats": {"v", "op"},
        }
        if operation not in required or set(message) != required[operation]:
            raise ProtocolError("unsupported public payload operation or fields")
        if operation == "put_many":
            return store.put_many(_decode_payloads(message["payloads"]))
        if operation == "get_many":
            return [base64.b64encode(raw).decode("ascii") for raw in store.get_many(message["hashes"])]
        if operation == "stats":
            return store.stats()
        values = message["observations"]
        if not isinstance(values, list) or len(values) > MAX_BATCH_ITEMS:
            raise StoreLimitError("observation batch must be a bounded list")
        return store.append_observations([Observation(**value) for value in values])

    def _write_loop(self) -> None:
        try:
            store = PayloadStore(self.db_path)
            self._db_identity = self._identity(self.db_path)
        except Exception as error:
            self._startup_error = error
            self._ready.set()
            return
        self._ready.set()
        try:
            while not self._stopping.is_set() or not self._requests.empty():
                try:
                    request = self._requests.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    if time.monotonic() >= request.deadline:
                        raise ServiceUnavailableError("request expired before execution")
                    self._check_storage()
                    if request.message.get("op") in {"put_many", "append_observations"}:
                        self._check_write_capacity()
                    result = self._dispatch(store, request.message)
                    request.response = {"v": PROTOCOL_VERSION, "ok": True, "result": result}
                except Exception as error:
                    request.response = _error_response(error)
                finally:
                    request.completed.set()
                    self._requests.task_done()
        finally:
            store.close()

    def stop(self) -> None:
        with self._submit_lock:
            self._stopping.set()
        if self._listener is not None:
            self._listener.close()
        for thread in self._threads:
            thread.join()
        self._threads.clear()
        if self._socket_identity is not None:
            try:
                if not self.socket_path.is_symlink() and self._identity(self.socket_path) == self._socket_identity:
                    self.socket_path.unlink()
            except FileNotFoundError:
                pass
        self._socket_identity = None
        self._listener = None
        for descriptor in reversed(self._locks):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        self._locks.clear()
        self._started = False

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--expected-volume-id")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--min-free-gib", type=float, default=50.0)
    parser.add_argument("--max-used-ratio", type=float, default=0.90)
    args = parser.parse_args(argv)
    stopped = threading.Event()
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, lambda *_: stopped.set())
    try:
        with MarketDataService(args.db, args.socket, storage_root=args.storage_root,
                               expected_volume_id=args.expected_volume_id, timeout=args.timeout,
                               min_free_bytes=int(args.min_free_gib * (1 << 30)),
                               max_used_ratio=args.max_used_ratio):
            stopped.wait()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
