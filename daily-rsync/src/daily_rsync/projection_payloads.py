"""Synchronize public projection dependencies; retain private context locally."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from polybot_observability.market_data_projection_bundle import CONTRACT, import_projection_bundle
from polybot_observability.market_data_projection_closure import (
    iter_projection_ids, projection_identity, read_projection_records, verify_projection_closure,
)
from polybot_observability.market_data_projections import MissingProjectionRecordError
from polybot_observability.market_data_scalar_closure import batches
from polybot_observability.market_data_store import PayloadReader

from .remote import PublicPayloadBundleLimitError


def _missing_records(reader, ids, authority):
    """Validate present records too, including batches split by the byte cap."""
    try:
        # Streaming avoids retaining a large catalog batch just to test presence.
        for _ in read_projection_records(reader, ids, authority):
            pass
    except MissingProjectionRecordError as error:
        missing = set(error.record_ids)
        if not missing or not missing <= set(ids):
            raise RuntimeError("public projection missing-record response differs from request") from error
        present = [record_id for record_id in ids if record_id not in missing]
        return missing | (_missing_records(reader, present, authority) if present else set())
    return set()


def synchronize_projection_closure(config, remote, database, *, strategy, ensure_capacity):
    from .public_payloads import _safe_local, local_payload_writer

    owner = projection_identity(database, strategy)
    if owner is None:
        return None
    authority = owner["authority_uuid"]
    identity = None
    pages = 0
    index = hashlib.sha256()
    cleanup = []
    root_identity = (config.data_root.stat().st_dev, config.data_root.stat().st_ino)

    def transfer(batch, missing):
        nonlocal identity, pages
        after = None
        while True:
            requested = sorted(missing)
            try:
                response = remote.export_public_projections(
                    batch, authority_uuid=authority, transferred_ids=requested, receipt_after=after,
                )
            except PublicPayloadBundleLimitError:
                # A single oversized group must fail. Splitting only the first
                # page keeps continuation semantics tied to its exact selection.
                if len(batch) < 2 or after is not None:
                    raise
                middle = len(batch) // 2
                for part in (batch[:middle], batch[middle:]):
                    transfer(part, missing & set(part))
                return
            manifest = response["manifest"]
            remote_path = response["bundle_path"]
            try:
                if (manifest.get("contract") != CONTRACT or manifest.get("status") != "VERIFIED"
                        or manifest.get("record_ids") != batch
                        or manifest.get("transferred_ids") != requested
                        or manifest.get("receipt_after") != after
                        or manifest.get("authority_uuid") != authority):
                    raise RuntimeError("public projection bundle contains unrequested records")
                current = response["source_identity"]
                if identity is not None and current != identity:
                    raise RuntimeError("public projection source identity changed")
                identity = current
                ensure_capacity(
                    (int(manifest["raw_bytes"]) + int(manifest["index_bytes"])) * 4 + (8 << 20)
                )
                _safe_local(config, config.incoming_root)
                with tempfile.TemporaryDirectory(prefix="public-projection-", dir=config.incoming_root) as folder:
                    incoming = Path(folder) / "payloads.db.partial"
                    remote.rsync(remote_path=remote_path, local_path=incoming, compress=False)
                    _safe_local(config, incoming)
                    if not incoming.is_file() or incoming.stat().st_size > 512 << 20:
                        raise RuntimeError("public projection bundle file missing or oversized")
                    if (config.data_root.stat().st_dev, config.data_root.stat().st_ino) != root_identity:
                        raise RuntimeError("public projection data root changed")
                    with local_payload_writer(config) as writer:
                        imported = import_projection_bundle(incoming, manifest, writer)
                missing.difference_update(requested)
                pages += 1
                index.update(json.dumps(
                    [manifest["closure_sha256"], after, manifest["next_receipt_after"],
                     imported["receipt_sha256"]], separators=(",", ":"),
                ).encode() + b"\n")
                next_after = manifest["next_receipt_after"]
                if next_after is not None and after is not None and next_after <= after:
                    raise RuntimeError("public projection continuation did not advance")
                after = next_after
            finally:
                try:
                    remote.cleanup_public_payloads(remote_path, manifest["file_sha256"])
                except Exception as error:
                    cleanup.append({"bundle_path": remote_path, "error": type(error).__name__})
            if after is None:
                return

    # Even zero private rows bind an authority and require the configured store.
    transfer([], set())
    for ids in batches(iter_projection_ids(database, strategy)):
        with PayloadReader(_safe_local(config, config.public_store_path)) as reader:
            missing = _missing_records(reader, ids, authority)
        transfer(ids, missing)
    with PayloadReader(_safe_local(config, config.public_store_path)) as reader:
        result = verify_projection_closure(reader, database, strategy)
    result["transfer"] = {
        "contract": "public-projection-transfer-v1", "page_count": pages,
        "page_sha256": index.hexdigest(), "source_identity": identity,
        "snapshot_scope": "per-page-read-snapshot-v1",
        "concurrent_append_policy": "earlier-cursor-receipts-require-next-refresh",
    }
    if cleanup:
        result["cleanup_pending_count"] = len(cleanup)
        result["cleanup_pending_examples"] = cleanup[:10]
    return result
