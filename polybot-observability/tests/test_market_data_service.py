from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import base64
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import threading

import pytest

from polybot_observability import market_data_service
from polybot_observability.market_data_client import (
    MAX_FRAME_BYTES,
    ProtocolError,
    ServiceBusyError,
    ServiceUnavailableError,
    StoreClient,
)
from polybot_observability.market_data_service import MarketDataService
from polybot_observability.market_data_store import (
    CorruptPayloadError,
    MissingPayloadError,
    Observation,
    ObservationConflictError,
    PayloadReader,
    PayloadStore,
    StoreError,
)


@pytest.fixture
def storage_root():
    # AF_UNIX paths on macOS are shorter than pytest's generated temporary paths.
    with tempfile.TemporaryDirectory(prefix="market-", dir="/tmp") as directory:
        yield Path(directory).resolve()


@pytest.fixture
def service(storage_root):
    with MarketDataService(
        storage_root / "market.sqlite", storage_root / "service.sock", storage_root=storage_root,
    ) as running:
        yield running


def socket_path(storage_root):
    return storage_root / "service.sock"


def test_storage_gate_blocks_writes_but_keeps_verified_reads_available(service, monkeypatch):
    from types import SimpleNamespace
    client = StoreClient(service.socket_path)
    sha = client.put_many([b"existing quote"])[0]
    service.min_free_bytes = 50
    service.max_used_ratio = .90
    monkeypatch.setattr(market_data_service.os, "statvfs", lambda path: SimpleNamespace(
        f_bavail=100, f_frsize=1, f_blocks=1000, f_bfree=100))
    with pytest.raises(ServiceUnavailableError, match="storage gate"):
        client.put_many([b"new quote"])
    assert client.get_many([sha]) == [b"existing quote"]
    assert client.stats()["payload_count"] == 1
    monkeypatch.setattr(market_data_service.os, "statvfs", lambda path: SimpleNamespace(
        f_bavail=200, f_frsize=1, f_blocks=1000, f_bfree=200))
    assert client.put_many([b"resumed quote"])


def receive_exact(connection, size):
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            raise EOFError("service closed the connection")
        chunks.extend(chunk)
    return bytes(chunks)


def receive_message(connection):
    length = struct.unpack("!I", receive_exact(connection, 4))[0]
    assert 0 < length <= MAX_FRAME_BYTES
    return json.loads(receive_exact(connection, length))


def raw_request(path, message):
    body = message if isinstance(message, bytes) else json.dumps(message).encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(str(path))
        connection.sendall(struct.pack("!I", len(body)) + body)
        return receive_message(connection)


def test_service_ack_is_durable_and_visible_to_independent_reader(service, storage_root):
    path = socket_path(storage_root)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_ISSOCK(path.stat().st_mode)
    with PayloadReader(storage_root / "market.sqlite") as reader:
        with StoreClient(path) as client:
            values = [b"\x00\xff", b"book" * 100, b"\x00\xff"]
            hashes = client.put_many(values)
            assert reader.get_many(hashes) == values
            assert client.get_many(hashes[::-1]) == values[::-1]
            record = Observation("white", "receipt-1", "2026-09-29T12:00:00Z", "book", hashes[0])
            assert client.append_observations([record, record]) is None
            assert reader.stats()["observation_count"] == 1
            assert client.stats() == reader.stats()
    with pytest.raises(ServiceUnavailableError, match="closed"):
        client.stats()


def test_concurrent_clients_share_dedup_without_losing_independent_receipts(service, storage_root):
    barrier = threading.Barrier(8)

    def write(observer):
        with StoreClient(socket_path(storage_root), timeout=5) as client:
            barrier.wait(timeout=5)
            values = [b"shared book", f"receipt-{observer}".encode()]
            hashes = client.put_many(values)
            client.append_observations([
                Observation(observer, "receipt-1", "2026-09-29T12:00:00Z", "book", hashes[0]),
            ])
            with PayloadReader(storage_root / "market.sqlite") as reader:
                assert reader.get_many(hashes) == values
            assert client.get_many(hashes) == values
            return hashes

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, [f"observer-{index}" for index in range(8)]))
    assert len({result[0] for result in results}) == 1
    assert len({result[1] for result in results}) == 8
    with StoreClient(socket_path(storage_root)) as client:
        assert client.stats()["payload_count"] == 9
        assert client.stats()["observation_count"] == 8


def test_service_preserves_structured_missing_conflict_and_corruption_errors(service, storage_root):
    with StoreClient(socket_path(storage_root)) as client:
        with pytest.raises(MissingPayloadError) as missing:
            client.get_many(["f" * 64, "f" * 64])
        assert missing.value.hashes == ["f" * 64]
        digest = client.put_many([b"receipt"])[0]
        record = Observation("white", "receipt-1", "2026-09-29T12:00:00Z", "book", digest)
        client.append_observations([record])
        with pytest.raises(ObservationConflictError):
            client.append_observations([
                replace(record, observation_id="receipt-2"), replace(record, kind="changed"),
            ])
        assert client.stats()["observation_count"] == 1
        with sqlite3.connect(storage_root / "market.sqlite") as connection:
            connection.execute("UPDATE payloads SET body=? WHERE sha256=?", (b"changed", digest))
        with pytest.raises(CorruptPayloadError):
            client.get_many([digest])
        with pytest.raises(CorruptPayloadError):
            client.put_many([b"new", b"receipt"])
        assert client.stats()["payload_count"] == 1


@pytest.mark.parametrize(
    "message",
    [
        {"v": 1, "op": "execute", "sql": "DROP TABLE payloads"},
        {"v": 2, "op": "stats"},
        {"op": "stats"},
        {"v": 1},
        {"v": 1, "op": "put_many", "payloads": ["%%%"]},
        {"v": 1, "op": "put_many", "payloads": "not a list"},
        {"v": 1, "op": "get_many", "hashes": ["invalid"]},
        {"v": 1, "op": "append_observations", "observations": [{}]},
        b"[]",
        b"{",
        b"\xff",
    ],
)
def test_invalid_requests_fail_without_stopping_service(service, storage_root, message):
    response = raw_request(socket_path(storage_root), message)
    assert response["v"] == 1
    assert response["ok"] is False
    assert response["error"] == "ProtocolError"
    with StoreClient(socket_path(storage_root)) as client:
        assert client.stats()["payload_count"] == 0


@pytest.mark.parametrize("length", [0, 2049, 0xFFFFFFFF])
def test_frame_bounds_are_rejected_before_reading_body(storage_root, length):
    with MarketDataService(
        storage_root / "market.sqlite", socket_path(storage_root),
        storage_root=storage_root, max_frame_bytes=2048,
    ):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(socket_path(storage_root)))
            connection.sendall(struct.pack("!I", length))
            response = receive_message(connection)
        assert response["ok"] is False
        assert response["error"] == "StoreLimitError"
        with StoreClient(socket_path(storage_root)) as client:
            assert client.stats()["payload_count"] == 0


def test_request_item_limit_cannot_be_bypassed_with_raw_protocol(service, storage_root):
    response = raw_request(socket_path(storage_root), {
        "v": 1, "op": "put_many", "payloads": [base64.b64encode(b"a").decode()] * 1025,
    })
    assert response["ok"] is False
    assert response["error"] == "StoreLimitError"
    with StoreClient(socket_path(storage_root)) as client:
        assert client.stats()["payload_count"] == 0


def test_partial_request_expires_and_releases_single_io_worker(storage_root):
    with MarketDataService(
        storage_root / "market.sqlite", socket_path(storage_root),
        storage_root=storage_root, io_workers=1, timeout=0.1,
    ):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stalled:
            stalled.settimeout(3)
            stalled.connect(str(socket_path(storage_root)))
            stalled.sendall(struct.pack("!I", 100) + b"{")
            try:
                response = receive_message(stalled)
                assert response["ok"] is False
            except (EOFError, ConnectionResetError):
                pass
        with StoreClient(socket_path(storage_root), timeout=3) as client:
            assert client.stats()["payload_count"] == 0


def test_service_stop_restart_preserves_data_and_releases_resources(storage_root):
    db_path = storage_root / "market.sqlite"
    path = socket_path(storage_root)
    first = MarketDataService(db_path, path, storage_root=storage_root)
    first.start()
    try:
        with StoreClient(path) as client:
            hashes = client.put_many([b"survives restart"])
    finally:
        first.stop()
    assert not path.exists()
    first.stop()
    with MarketDataService(db_path, path, storage_root=storage_root):
        with StoreClient(path) as client:
            assert client.get_many(hashes) == [b"survives restart"]
            assert client.stats()["payload_count"] == 1


def test_client_deadline_rejects_a_server_that_never_replies(storage_root):
    path = storage_root / "stalled.sock"
    accepted = threading.Event()
    release = threading.Event()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    server.settimeout(3)

    def stall():
        with server:
            connection, _ = server.accept()
            with connection:
                accepted.set()
                assert release.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(stall)
        try:
            with StoreClient(path, timeout=0.1) as client:
                with pytest.raises(TimeoutError):
                    client.stats()
            assert accepted.wait(timeout=3)
        finally:
            release.set()
        pending.result(timeout=5)


@pytest.mark.parametrize(
    "response,method,argument,error",
    [
        ({"v": 1, "ok": True, "result": ["a" * 64]}, "put_many", [b"receipt"], ProtocolError),
        ({"v": 1, "ok": True}, "put_many", [b"receipt"], ProtocolError),
        ({"v": 1, "ok": True, "result": ["Y29ycnVwdGVk"]}, "get_many", [hashlib.sha256(b"receipt").hexdigest()], CorruptPayloadError),
        ({"v": 2, "ok": True, "result": {}}, "stats", None, ProtocolError),
    ],
)
def test_client_does_not_accept_false_or_malformed_ack(storage_root, response, method, argument, error):
    path = storage_root / "false-ack.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    server.settimeout(3)

    def reply():
        with server:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(3)
                receive_message(connection)
                body = json.dumps(response).encode()
                connection.sendall(struct.pack("!I", len(body)) + body)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(reply)
        with StoreClient(path) as client:
            with pytest.raises(error):
                getattr(client, method)(argument) if argument is not None else getattr(client, method)()
        pending.result(timeout=5)


def test_cli_requires_explicit_storage_root(storage_root):
    result = subprocess.run(
        [sys.executable, "-m", "polybot_observability.market_data_service",
         "--db", str(storage_root / "market.sqlite"), "--socket", str(socket_path(storage_root))],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "--storage-root" in result.stderr
    assert not (storage_root / "market.sqlite").exists()


@pytest.mark.parametrize("same_database", [True, False])
def test_second_service_cannot_take_writer_or_socket_lock(service, storage_root, same_database):
    second = MarketDataService(
        storage_root / ("market.sqlite" if same_database else "other.sqlite"),
        storage_root / ("other.sock" if same_database else "service.sock"),
        storage_root=storage_root,
    )
    with pytest.raises(StoreError, match="lock"):
        second.start()
    second.stop()
    with StoreClient(socket_path(storage_root)) as client:
        hashes = client.put_many([b"original writer still owns store"])
        assert client.get_many(hashes) == [b"original writer still owns store"]


def test_missing_storage_root_is_not_created(storage_root):
    missing = storage_root / "missing-volume"
    with pytest.raises(FileNotFoundError):
        with MarketDataService(missing / "market.sqlite", socket_path(storage_root), storage_root=missing):
            pytest.fail("missing root accepted")
    assert not missing.exists()


def test_database_cannot_escape_storage_root(storage_root):
    root = storage_root / "configured"
    root.mkdir(mode=0o700)
    outside = storage_root / "outside.sqlite"
    with pytest.raises(ValueError, match="inside"):
        with MarketDataService(outside, socket_path(storage_root), storage_root=root):
            pytest.fail("outside database accepted")
    assert not outside.exists()


@pytest.mark.parametrize("linked_path", ["root", "database", "socket_parent"])
def test_symlink_paths_are_rejected(storage_root, linked_path):
    actual = storage_root / "actual"
    actual.mkdir(mode=0o700)
    link = storage_root / "alias"
    link.symlink_to(actual, target_is_directory=True)
    root = link if linked_path == "root" else actual
    db = (link if linked_path == "database" else actual) / "market.sqlite"
    sock = (link if linked_path == "socket_parent" else actual) / "service.sock"
    with pytest.raises(ValueError, match="canonical"):
        with MarketDataService(db, sock, storage_root=root):
            pytest.fail("symlink path accepted")
    assert not (actual / "market.sqlite").exists()


def test_nonprivate_socket_directory_is_rejected(storage_root):
    directory = storage_root / "public-sockets"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    with pytest.raises(StoreError, match="0700"):
        with MarketDataService(storage_root / "market.sqlite", directory / "service.sock", storage_root=storage_root):
            pytest.fail("public socket directory accepted")
    assert not (directory / "service.sock").exists()


def test_existing_regular_socket_path_is_preserved(storage_root):
    path = socket_path(storage_root)
    path.write_text("do not overwrite")
    with pytest.raises(StoreError, match="socket path"):
        with MarketDataService(storage_root / "market.sqlite", path, storage_root=storage_root):
            pytest.fail("regular file overwritten")
    assert path.read_text() == "do not overwrite"
    path.unlink()
    with MarketDataService(storage_root / "market.sqlite", path, storage_root=storage_root):
        with StoreClient(path) as client:
            assert client.stats()["payload_count"] == 0


def test_volume_marker_checked_at_start_and_before_each_request(storage_root):
    marker = storage_root / ".polybot-market-data-volume-id"
    marker.write_text("wrong-volume\n")
    service = MarketDataService(
        storage_root / "market.sqlite", socket_path(storage_root),
        storage_root=storage_root, expected_volume_id="expected-volume",
    )
    with pytest.raises(StoreError, match="identity mismatch"):
        service.start()
    assert not (storage_root / "market.sqlite").exists()
    marker.write_text("expected-volume\n")
    with service:
        with StoreClient(socket_path(storage_root)) as client:
            hashes = client.put_many([b"valid volume"])
            marker.write_text("replacement-volume\n")
            with pytest.raises(StoreError, match="identity mismatch"):
                client.put_many([b"must not write"])
            marker.write_text("expected-volume\n")
            assert client.get_many(hashes) == [b"valid volume"]
            assert client.stats()["payload_count"] == 1


def test_database_path_replacement_stops_successful_ack(service, storage_root):
    db = storage_root / "market.sqlite"
    with StoreClient(socket_path(storage_root)) as client:
        client.put_many([b"original inode"])
        db.rename(storage_root / "original.sqlite")
        db.write_bytes(b"")
        with pytest.raises(StoreError, match="identity changed"):
            client.put_many([b"must not write"])
        with pytest.raises(StoreError, match="identity changed"):
            client.stats()


def test_full_writer_queue_rejects_excess_work_without_false_ack(storage_root, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    queued = threading.Event()
    original_put = PayloadStore.put_many

    def blocked_put(store, values):
        entered.set()
        assert release.wait(timeout=10)
        return original_put(store, values)

    monkeypatch.setattr(PayloadStore, "put_many", blocked_put)
    with MarketDataService(
        storage_root / "market.sqlite", socket_path(storage_root), storage_root=storage_root,
        queue_size=1, io_workers=3, timeout=5,
    ) as service:
        original_enqueue = service._requests.put_nowait

        def enqueue(request):
            original_enqueue(request)
            if request.message["op"] == "stats":
                queued.set()

        monkeypatch.setattr(service._requests, "put_nowait", enqueue)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(StoreClient(socket_path(storage_root), timeout=5).put_many, [b"blocked"])
            try:
                assert entered.wait(timeout=3)
                second = pool.submit(StoreClient(socket_path(storage_root), timeout=5).stats)
                assert queued.wait(timeout=3)
                with StoreClient(socket_path(storage_root), timeout=3) as client:
                    with pytest.raises(ServiceBusyError, match="writer queue"):
                        client.put_many([b"overflow"])
            finally:
                release.set()
            assert first.result(timeout=5) == [hashlib.sha256(b"blocked").hexdigest()]
            assert second.result(timeout=5)["payload_count"] == 1
        with StoreClient(socket_path(storage_root)) as client:
            assert client.stats()["payload_count"] == 1


def test_full_connection_queue_rejects_excess_connections(storage_root, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    queued = threading.Event()
    original_receive = market_data_service._receive_frame

    def blocked_receive(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return original_receive(*args, **kwargs)

    monkeypatch.setattr(market_data_service, "_receive_frame", blocked_receive)
    with MarketDataService(
        storage_root / "market.sqlite", socket_path(storage_root), storage_root=storage_root,
        queue_size=1, io_workers=1, timeout=5,
    ) as service:
        original_enqueue = service._connections.put_nowait
        accepted_count = 0

        def enqueue(connection):
            nonlocal accepted_count
            original_enqueue(connection)
            accepted_count += 1
            if accepted_count == 2:
                queued.set()

        monkeypatch.setattr(service._connections, "put_nowait", enqueue)
        message = json.dumps({"v": 1, "op": "stats"}).encode()
        frame = struct.pack("!I", len(message)) + message
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as first:
            first.settimeout(3)
            first.connect(str(socket_path(storage_root)))
            first.sendall(frame)
            try:
                assert entered.wait(timeout=3)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as second:
                    second.settimeout(3)
                    second.connect(str(socket_path(storage_root)))
                    second.sendall(frame)
                    assert queued.wait(timeout=3)
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as excess:
                        excess.settimeout(3)
                        excess.connect(str(socket_path(storage_root)))
                        response = receive_message(excess)
                    assert response["ok"] is False
                    assert response["error"] == "ServiceBusyError"
                    assert "connection queue" in response["message"]
                    release.set()
                    assert receive_message(second)["ok"] is True
            finally:
                release.set()
            assert receive_message(first)["ok"] is True
        with StoreClient(socket_path(storage_root)) as client:
            assert client.stats()["payload_count"] == 0


def test_shutdown_drains_submitted_write_before_releasing_writer_lock(storage_root, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    stopping = threading.Event()
    original_put = PayloadStore.put_many

    def blocked_put(store, values):
        entered.set()
        assert release.wait(timeout=10)
        return original_put(store, values)

    monkeypatch.setattr(PayloadStore, "put_many", blocked_put)
    db = storage_root / "market.sqlite"
    path = socket_path(storage_root)
    service = MarketDataService(db, path, storage_root=storage_root, timeout=5).start()
    original_set = service._stopping.set

    def signal_stopping():
        original_set()
        stopping.set()

    monkeypatch.setattr(service._stopping, "set", signal_stopping)
    with ThreadPoolExecutor(max_workers=2) as pool:
        write = pool.submit(StoreClient(path, timeout=5).put_many, [b"pending shutdown"])
        try:
            assert entered.wait(timeout=3)
            stop = pool.submit(service.stop)
            assert stopping.wait(timeout=3)
            assert not stop.done()
            second = MarketDataService(db, storage_root / "second.sock", storage_root=storage_root)
            with pytest.raises(StoreError, match="lock"):
                second.start()
        finally:
            release.set()
        result = write.result(timeout=5)
        stop.result(timeout=5)
    digest = hashlib.sha256(b"pending shutdown").hexdigest()
    assert result == [digest]
    with PayloadReader(db) as reader:
        assert reader.get_many([digest]) == [b"pending shutdown"]
    with MarketDataService(db, path, storage_root=storage_root):
        with StoreClient(path) as client:
            assert client.put_many([b"pending shutdown"]) == [digest]
            assert client.stats()["payload_count"] == 1


def test_cli_rejects_wrong_volume_identity_before_creating_database(storage_root):
    (storage_root / ".polybot-market-data-volume-id").write_text("wrong-volume")
    result = subprocess.run(
        [sys.executable, "-m", "polybot_observability.market_data_service",
         "--db", str(storage_root / "market.sqlite"), "--socket", str(socket_path(storage_root)),
         "--storage-root", str(storage_root), "--expected-volume-id", "expected-volume"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "identity mismatch" in result.stderr
    assert not (storage_root / "market.sqlite").exists()


def test_cli_cannot_start_another_writer_process(service, storage_root):
    result = subprocess.run(
        [sys.executable, "-m", "polybot_observability.market_data_service",
         "--db", str(storage_root / "market.sqlite"), "--socket", str(storage_root / "second.sock"),
         "--storage-root", str(storage_root)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "lock" in result.stderr
    assert not (storage_root / "second.sock").exists()
    with StoreClient(socket_path(storage_root)) as client:
        assert client.stats()["payload_count"] == 0
