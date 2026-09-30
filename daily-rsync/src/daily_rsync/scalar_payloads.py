"""Typed public snapshot closure sync; private memberships stay in their DB."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from polybot_observability.market_data_scalar_bundle import (
    CONTRACT,
    import_scalar_bundle,
)
from polybot_observability.market_data_scalar_closure import (
    batches,
    iter_scalar_ids,
    scalar_identity,
    verify_scalar_closure,
)
from polybot_observability.market_data_scalars import MissingScalarRecordError
from polybot_observability.market_data_store import PayloadReader


def synchronize_scalar_closure(config, remote, database, *, strategy, ensure_capacity):
    from .public_payloads import _safe_local, local_payload_writer

    owner = scalar_identity(database, strategy)
    if owner is None:
        return None
    authority = owner["authority_uuid"]
    identity = None
    index = hashlib.sha256()
    pages = 0
    cleanup = []
    root_identity = (config.data_root.stat().st_dev, config.data_root.stat().st_ino)

    def transfer(batch, missing):
        nonlocal identity, pages
        after = None
        while True:
            requested = sorted(missing)
            response = remote.export_public_scalars(
                batch, authority_uuid=authority, transferred_ids=requested, receipt_after=after
            )
            manifest = response["manifest"]
            remote_path = response["bundle_path"]
            try:
                if (
                    manifest.get("contract") != CONTRACT
                    or manifest.get("record_ids") != batch
                    or manifest.get("transferred_ids") != requested
                    or manifest.get("receipt_after") != after
                    or manifest.get("authority_uuid") != authority
                ):
                    raise RuntimeError("public scalar bundle contains unrequested records")
                current = response["source_identity"]
                if identity is not None and current != identity:
                    raise RuntimeError("public scalar source identity changed")
                identity = current
                ensure_capacity(
                    (int(manifest["raw_bytes"]) + int(manifest["index_bytes"])) * 4 + (8 << 20)
                )
                _safe_local(config, config.incoming_root)
                with tempfile.TemporaryDirectory(
                    prefix="public-scalar-", dir=config.incoming_root
                ) as folder:
                    incoming = Path(folder) / "payloads.db.partial"
                    remote.rsync(remote_path=remote_path, local_path=incoming, compress=False)
                    _safe_local(config, incoming)
                    if not incoming.is_file() or incoming.stat().st_size > 512 << 20:
                        raise RuntimeError("public scalar bundle file missing or oversized")
                    if (
                        config.data_root.stat().st_dev,
                        config.data_root.stat().st_ino,
                    ) != root_identity:
                        raise RuntimeError("public scalar data root changed")
                    with local_payload_writer(config) as writer:
                        imported = import_scalar_bundle(incoming, manifest, writer)
                missing.difference_update(requested)
                pages += 1
                index.update(
                    json.dumps(
                        [
                            manifest["closure_sha256"],
                            after,
                            manifest["next_receipt_after"],
                            imported["receipt_sha256"],
                        ],
                        separators=(",", ":"),
                    ).encode()
                    + b"\n"
                )
                next_after = manifest["next_receipt_after"]
                if next_after is not None and after is not None and next_after <= after:
                    raise RuntimeError("public scalar continuation did not advance")
                after = next_after
            finally:
                try:
                    remote.cleanup_public_payloads(remote_path, manifest["file_sha256"])
                except Exception as error:
                    cleanup.append({"bundle_path": remote_path, "error": type(error).__name__})
            if after is None:
                break

    # An empty bounded export confirms the remote authority before the local
    # replica claims it; an empty private membership still binds that authority.
    transfer([], set())
    for batch in batches(iter_scalar_ids(database, strategy)):
        with PayloadReader(config.public_store_path) as reader:
            try:
                reader.get_scalar_records(batch, authority_uuid=authority)
            except MissingScalarRecordError as error:
                missing = set(error.record_ids)
                present = [value for value in batch if value not in missing]
                if present:
                    reader.get_scalar_records(present, authority_uuid=authority)
            else:
                missing = set()
        transfer(batch, missing)
    _safe_local(config, config.public_store_path)
    if config.public_store_path.exists():
        with PayloadReader(config.public_store_path) as reader:
            result = verify_scalar_closure(reader, database, strategy)
    else:
        result = verify_scalar_closure(None, database, strategy)
    result["transfer"] = {
        "contract": "public-scalar-transfer-v1",
        "page_count": pages,
        "page_sha256": index.hexdigest(),
        "source_identity": identity,
        "snapshot_scope": "per-page-read-snapshot-v1",
        "concurrent_append_policy": "earlier-cursor-receipts-require-next-refresh",
    }
    if cleanup:
        result["cleanup_pending_count"] = len(cleanup)
        result["cleanup_pending_examples"] = cleanup[:10]
    return result
