"""Bind transported private source-anchor receipts to independently checked rows.

The strategy's native reader still validates anchor/seed/device semantics. This
layer proves transport, source identity and the complete RAW/body migration.
"""
import hashlib
import json


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _contains(full, projected):
    if isinstance(projected, dict):
        return isinstance(full, dict) and all(
            key in full and _contains(full[key], value) for key, value in projected.items())
    return full == projected and type(full) is type(projected)


def verify_source_storage_transition(artifact, proposal, manifest, checked, closure):
    transport = proposal.get("source_storage_transition")
    if transport is None:
        return {}
    if (artifact.strategy != "golden-strawberry"
            or manifest.get("raw_profile_id") != "strawberry-last-mile-v1"
            or not isinstance(transport, dict)
            or set(transport) != {"sidecar_path", "sidecar_sha256", "receipt"}
            or transport["sidecar_path"] != artifact.remote_path + ".source-storage-transition.json"):
        raise RuntimeError("source storage transition transport identity differs")
    value = transport["receipt"]
    if (not isinstance(value, dict)
            or value.get("contract") != "strawberry-v1-source-storage-transition-v1"
            or hashlib.sha256(_canonical(value) + b"\n").hexdigest() != transport["sidecar_sha256"]
            or hashlib.sha256(_canonical({k: v for k, v in value.items() if k != "proof_sha256"})).hexdigest()
               != value.get("proof_sha256")
            or value.get("runtime_source_path") != artifact.remote_path
            or value.get("original_anchor", {}).get("source_path") != artifact.remote_path
            or value.get("original_sha256") != manifest["source_sha256"]
            or value.get("derivative_sha256") != manifest["destination_sha256"]
            or value.get("raw_manifest_sha256") != proposal["sidecar_sha256"]
            or not _contains(value.get("raw_manifest"), manifest)
            or value.get("verification") != checked):
        raise RuntimeError("source storage transition receipt does not match independent RAW proof")
    body = {key: closure[key] for key in ("payload_count", "raw_bytes", "closure_sha256")}
    if value.get("body_closure") != body:
        raise RuntimeError("source storage transition body closure differs")
    return {
        "source_storage_transition": value,
        "source_storage_transition_sha256": transport["sidecar_sha256"],
        "source_storage_transition_validation": {
            "contract": "daily-rsync-source-storage-transition-transport-v1",
            "raw_rows_reproved": True,
            "body_closure_reproved": True,
            "native_anchor_check_required": True,
        },
    }
