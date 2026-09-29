"""Public body dependencies of verified snapshots, without importing private rows.

The payload store commits first. A failed import can leave unused immutable
payloads; the snapshot and its closure attestation are published only afterwards.
DB replacement, sidecar replacement and catalog commit are separate operations:
readers must validate their SHA binding and fail closed during a partial publish.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from polybot_observability.market_data_bundle import (
    closure_digest,
    import_bundle,
    missing_payloads,
    reference_closure,
    verify_closure,
)
from polybot_observability.market_data_store import PayloadReader, PayloadStore

from .config import AppConfig, validate_data_root_mount
from .remote import PublicPayloadBundleLimitError

CONTRACT = "daily-rsync-public-closure-v1"
STORE_RELATIVE_PATH = "shared-market-data/public.db"


def _safe_local(config: AppConfig, path: Path) -> Path:
    validate_data_root_mount(config)
    root = config.data_root
    if not root.is_dir() or root.resolve(strict=True) != root:
        raise RuntimeError("public payload data root is unavailable or noncanonical")
    if path.resolve() != path or not path.is_relative_to(root):
        raise RuntimeError("public payload path escapes data root or crosses a symlink")
    parent = path if path.exists() else path.parent
    while not parent.exists():
        parent = parent.parent
    if parent.stat().st_dev != root.stat().st_dev:
        raise RuntimeError("public payload path crosses the configured storage volume")
    return path


@contextmanager
def local_payload_writer(config: AppConfig):
    """One local writer across CLI processes, sharing the service lock name."""
    path = _safe_local(config, config.public_store_path)
    root_identity = (config.data_root.stat().st_dev, config.data_root.stat().st_ino)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = Path(str(path) + ".writer.lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError("unsafe local public payload writer lock")
        deadline = time.monotonic() + 5.0
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("local public payload writer is busy") from None
                time.sleep(0.05)
        _safe_local(config, path)
        if (config.data_root.stat().st_dev, config.data_root.stat().st_ino) != root_identity:
            raise RuntimeError("public payload data root identity changed")
        with PayloadStore(path) as writer:
            os.chmod(path, 0o600)
            yield writer
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def closure_sidecar(database: Path) -> Path:
    return Path(str(database) + ".public-payloads.json")


def write_closure_descriptor(config: AppConfig, database: Path, descriptor: dict) -> None:
    destination = _safe_local(config, closure_sidecar(database))
    temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(descriptor, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        parent = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        temporary.unlink(missing_ok=True)


def _descriptor(
    config, *, source_key, strategy, database_sha256, hashes, verification, remote_identity=None
):
    return {
        "contract": CONTRACT,
        "status": "VERIFIED",
        "source_key": source_key,
        "source": config.ssh_host,
        "strategy": strategy,
        "database_sha256": database_sha256,
        "shared_store": STORE_RELATIVE_PATH if hashes else None,
        "source_identity": {
            "source": config.ssh_host,
            "configured_public_db": config.remote_public_db,
            "configured_storage_root": config.remote_public_storage_root,
            "export_identity": remote_identity,
        },
        "verified_at": datetime.now(UTC).isoformat(),
        **verification,
    }


def synchronize_database_closure(
    config: AppConfig,
    remote,
    database: Path,
    *,
    strategy: str | None,
    source_key: str,
    database_sha256: str,
    ensure_capacity,
) -> dict:
    _safe_local(config, database)
    hashes = reference_closure(database, strategy or "")
    if not hashes:
        return _descriptor(
            config,
            source_key=source_key,
            strategy=strategy,
            database_sha256=database_sha256,
            hashes=[],
            verification={"closure_sha256": closure_digest([]), "payload_count": 0, "raw_bytes": 0},
        )
    store_path = _safe_local(config, config.public_store_path)
    if store_path.exists():
        with PayloadReader(store_path) as reader:
            missing = missing_payloads(reader, hashes)
    else:
        missing = hashes
    export_identity = None
    cleanup_pending = []
    root_identity = (config.data_root.stat().st_dev, config.data_root.stat().st_ino)

    def fetch(batch):
        nonlocal export_identity
        try:
            response = remote.export_public_payloads(batch)
        except PublicPayloadBundleLimitError:
            if len(batch) < 2:
                raise
            middle = len(batch) // 2
            fetch(batch[:middle])
            fetch(batch[middle:])
            return
        manifest = response["manifest"]
        bundle_path = response["bundle_path"]
        try:
            if manifest.get("payload_hashes") != sorted(set(batch)):
                raise RuntimeError("public bundle contains unrequested payloads")
            identity = response["source_identity"]
            if export_identity is not None and identity != export_identity:
                raise RuntimeError("remote public payload store identity changed during sync")
            export_identity = identity
            ensure_capacity(int(manifest["raw_bytes"]) * 4 + (8 << 20))
            _safe_local(config, config.incoming_root)
            with tempfile.TemporaryDirectory(
                prefix="public-payload-", dir=config.incoming_root
            ) as root:
                incoming = Path(root) / "payloads.db.partial"
                remote.rsync(remote_path=bundle_path, local_path=incoming, compress=False)
                _safe_local(config, incoming)
                if not incoming.is_file() or incoming.stat().st_size > 512 << 20:
                    raise RuntimeError("public bundle file is missing or oversized")
                current_root = (config.data_root.stat().st_dev, config.data_root.stat().st_ino)
                if current_root != root_identity:
                    raise RuntimeError("public payload data root changed during transfer")
                with local_payload_writer(config) as writer:
                    import_bundle(incoming, manifest, writer)
        finally:
            try:
                remote.cleanup_public_payloads(bundle_path, manifest["file_sha256"])
            except Exception as error:
                # A leftover remote verified staging bundle is not a failed import.
                cleanup_pending.append({"bundle_path": bundle_path, "error": type(error).__name__})

    for offset in range(0, len(missing), 1024):
        fetch(missing[offset : offset + 1024])
    _safe_local(config, store_path)
    with PayloadReader(store_path) as reader:
        verification = verify_closure(reader, hashes)
    descriptor = _descriptor(
        config,
        source_key=source_key,
        strategy=strategy,
        database_sha256=database_sha256,
        hashes=hashes,
        verification=verification,
        remote_identity=export_identity,
    )
    if cleanup_pending:
        descriptor["remote_staging_cleanup_pending_count"] = len(cleanup_pending)
        descriptor["remote_staging_cleanup_pending_examples"] = cleanup_pending[:10]
    return descriptor


def verify_database_closure(
    config: AppConfig,
    database: Path,
    *,
    strategy: str | None,
    source_key: str,
    database_sha256: str,
    descriptor: dict[str, Any] | None = None,
    source: str | None = None,
) -> dict | None:
    _safe_local(config, database)
    hashes = reference_closure(database, strategy or "")
    sidecar = _safe_local(config, closure_sidecar(database))
    if descriptor is None and sidecar.exists():
        if not sidecar.is_file() or sidecar.stat().st_size > 65_536:
            raise RuntimeError("public payload closure attestation is invalid")
        descriptor = json.loads(sidecar.read_text(encoding="utf-8"))
    if not hashes and descriptor is None:
        return None  # Legacy inline snapshots do not require a shared store.
    source = source or config.ssh_host
    expected = {
        "contract": CONTRACT,
        "status": "VERIFIED",
        "source_key": source_key,
        "source": source,
        "strategy": strategy,
        "database_sha256": database_sha256,
        "shared_store": STORE_RELATIVE_PATH if hashes else None,
        "closure_sha256": closure_digest(hashes),
        "payload_count": len(hashes),
    }
    if not isinstance(descriptor, dict) or any(descriptor.get(k) != v for k, v in expected.items()):
        raise RuntimeError(
            "public payload evidence gap: closure attestation is missing or mismatched"
        )
    source_identity = descriptor.get("source_identity")
    if not isinstance(source_identity, dict) or source_identity.get("source") != source:
        raise RuntimeError("public payload closure source identity is missing")
    if hashes:
        store = _safe_local(config, config.public_store_path)
        with PayloadReader(store) as reader:
            verification = verify_closure(reader, hashes)
        if any(descriptor.get(key) != value for key, value in verification.items()):
            raise RuntimeError("public payload evidence gap: closure verification changed")
    elif descriptor.get("raw_bytes") != 0:
        raise RuntimeError("inline snapshot has an invalid public payload closure")
    return descriptor
