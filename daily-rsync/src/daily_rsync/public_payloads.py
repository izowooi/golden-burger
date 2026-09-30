"""Public body dependencies of verified snapshots, without importing private rows.

The payload store commits first. A failed import can leave unused immutable
payloads; the snapshot and its closure attestation are published only afterwards.
DB replacement, sidecar replacement and catalog commit are separate operations:
readers must validate their SHA binding and fail closed during a partial publish.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
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
from polybot_observability.market_data_levels import validate_level_layout
from polybot_observability.market_data_mixed import is_mixed_payload, verify_mixed_ownership
from polybot_observability.market_data_policy import public_columns, public_level_projections
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_projection_closure import (
    projection_identity, verify_projection_closure,
)
from polybot_observability.market_data_scalar_closure import scalar_identity, verify_scalar_closure
from polybot_observability.market_data_scalar_links import (
    STRATEGIES as SCALAR_STRATEGIES,
)
from polybot_observability.market_data_scalar_links import (
    scalar_layout_metadata,
    scalar_namespace,
    validate_scalar_layout,
)
from polybot_observability.market_data_sqlite import connect as resolving_connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore

from .config import AppConfig, validate_data_root_mount
from .remote import PublicPayloadBundleLimitError
from .snapshot_manifests import snapshot_manifest_path

CONTRACT = "daily-rsync-public-closure-v1"
STORE_RELATIVE_PATH = "shared-market-data/public.db"
_SNAPSHOT_LINK_FIELDS = frozenset(
    {
        "source_snapshot_sha256",
        "source_file_sha256",
        "original_source_path",
        "original_source_fingerprint",
        "original_source_members",
    }
)


def _apple_monthly_migration(artifact):
    return (
        artifact.kind == "database_sim"
        and artifact.strategy == "golden-apple"
        and artifact.mode == "sim"
        and artifact.data_contract == "apple-filtered-frames-v1"
        and artifact.database_month is not None
        and artifact.database_utc_date is None
        and artifact.archive_date is None
    )


def _scalar_migration(artifact):
    value = artifact.storage_migration
    if (artifact.strategy not in SCALAR_STRATEGIES
            or artifact.kind not in {"database_live", "database_sim", "database_research_archive"}
            or not isinstance(value, dict)):
        return False
    manifest = value.get("manifest")
    if not isinstance(manifest, dict):
        return False
    try:
        expected = scalar_namespace(artifact.source, artifact.jenkins_job,
                                    artifact.strategy, artifact.runtime_job)
    except (ValueError, TypeError):
        return False
    table = manifest.get("tables", {}).get("market_snapshots", {})
    return (manifest.get("scalar_namespace") == expected
            and isinstance(manifest.get("public_scalar_records"), dict)
            and type(table.get("externalized_scalar_rows")) is int)


def _projection_migration(artifact):
    from polybot_observability.market_data_projection_profiles import SNAPSHOT_PROFILES
    value = artifact.storage_migration
    if (artifact.strategy not in SNAPSHOT_PROFILES
            or artifact.kind not in {"database_live", "database_sim", "database_research_archive"}
            or not isinstance(value, dict)):
        return False
    manifest = value.get("manifest")
    if not isinstance(manifest, dict):
        return False
    try:
        expected = scalar_namespace(artifact.source, artifact.jenkins_job,
                                    artifact.strategy, artifact.runtime_job)
    except (ValueError, TypeError):
        return False
    tables = manifest.get("tables", {})
    return (manifest.get("projection_namespace") == expected
            and isinstance(manifest.get("public_projection_records"), dict)
            and (type(tables.get("market_snapshots", {}).get("externalized_projection_rows")) is int
                 or type(tables.get("market_catalog", {}).get("externalized_catalog_rows")) is int))


def _raw_migration(artifact):
    from .remote_agent import RAW_STRATEGIES, _raw_migration_profile

    value = artifact.storage_migration
    if (artifact.strategy not in RAW_STRATEGIES
            or artifact.kind not in {"database_live", "database_sim", "database_research_archive"}
            or not isinstance(value, dict)):
        return False
    manifest = value.get("manifest")
    if not isinstance(manifest, dict):
        return False
    try:
        profile = _raw_migration_profile(manifest)
        expected = scalar_namespace(artifact.source, artifact.jenkins_job,
                                    artifact.strategy, artifact.runtime_job)
    except (ValueError, TypeError, RuntimeError):
        return False
    expected_closure = ("public-projection-closure-v4"
                        if manifest.get("contract") == "shared-raw-parent-derivative-v2"
                        else "public-projection-closure-v3")
    return (profile["strategy"] == artifact.strategy
            and manifest.get("projection_namespace") == expected
            and isinstance(manifest.get("public_projection_records"), dict)
            and manifest["public_projection_records"].get("contract") == expected_closure)


def _scalar_route(owner, strategy, expected_source_identity):
    if owner is None or expected_source_identity is None:
        return None
    if not isinstance(expected_source_identity, dict):
        raise RuntimeError("scalar source route identity is missing")
    route = {key: expected_source_identity.get(key)
             for key in ("source", "jenkins_job", "strategy", "runtime_job")}
    try:
        namespace = scalar_namespace(route["source"], route["jenkins_job"],
                                     route["strategy"], route["runtime_job"])
    except (ValueError, TypeError):
        raise RuntimeError("scalar source route identity is incomplete") from None
    if route["strategy"] != strategy or owner["namespace"] != namespace:
        raise RuntimeError("scalar namespace differs from artifact source/job/strategy/runtime")
    return route


def _projection_route(owner, strategy, expected_source_identity):
    try:
        return _scalar_route(owner, strategy, expected_source_identity)
    except RuntimeError as error:
        raise RuntimeError(str(error).replace("scalar", "projection")) from None


def _verified_snapshot_link(artifact, existing):
    """Bind a distinct source file hash to an already verified local backup.

    SQLite online backup can rewrite header counters. This permits a proposal
    only when the *prior* snapshot manifest and catalog independently attest
    the exact same source state. Full decoded equality is still mandatory.
    """
    value = artifact.storage_migration["manifest"]
    if not _SNAPSHOT_LINK_FIELDS <= value.keys():
        raise RuntimeError("storage migration snapshot linkage is incomplete")
    for key in ("source_snapshot_sha256", "source_file_sha256", "original_source_fingerprint"):
        digest = value[key]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise RuntimeError("storage migration snapshot linkage checksum is invalid")
    original = Path(existing["local_path"])
    path = snapshot_manifest_path(original)
    if path.resolve() != path or original.resolve() != original:
        raise RuntimeError("storage migration prior snapshot crosses a symlink")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 8 * 1024 * 1024:
            raise RuntimeError("storage migration prior snapshot manifest is unbounded")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        after = os.fstat(descriptor)
        if (before.st_ino, before.st_mtime_ns, before.st_size) != (
            after.st_ino,
            after.st_mtime_ns,
            after.st_size,
        ) or len(raw) != before.st_size:
            raise RuntimeError("storage migration prior snapshot manifest changed")
    finally:
        os.close(descriptor)
    prior = json.loads(raw)
    members = value["original_source_members"]
    if (
        not isinstance(members, list)
        or len(members) != 1
        or not isinstance(members[0], dict)
        or set(members[0]) != {"suffix", "size_bytes", "mtime_ns", "inode"}
        or members[0]["suffix"] != "main"
        or any(
            type(members[0][key]) is not int or members[0][key] < 0
            for key in ("size_bytes", "mtime_ns", "inode")
        )
    ):
        raise RuntimeError("storage migration original file metadata is invalid")
    fingerprint = hashlib.sha256(
        json.dumps(members, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    local_sha = existing["local_sha256"]
    matches = (
        isinstance(prior, dict)
        and value["source_snapshot_sha256"] == local_sha == prior.get("sha256")
        and value["source_file_sha256"] == value["source_sha256"]
        and value["original_source_path"] == artifact.remote_path == prior.get("source")
        and prior.get("local_path") == str(original)
        and fingerprint == value["original_source_fingerprint"] == existing["remote_fingerprint"]
        and fingerprint == prior.get("source_fingerprint_before")
        and fingerprint == prior.get("source_fingerprint_after")
        and fingerprint == prior.get("remote_source_fingerprint")
        and members == prior.get("source_members_after")
        and members[0]["size_bytes"] == value.get("source_bytes")
        and members[0]["size_bytes"] == prior.get("source_size_bytes")
        and members[0]["size_bytes"] == prior.get("source_storage_bytes")
        and members[0]["size_bytes"] == existing["remote_size_bytes"]
        and members[0]["mtime_ns"] == existing["remote_mtime_ns"]
        and prior.get("snapshot_size_bytes") == original.stat().st_size
        and prior.get("quick_check") == ["ok"]
        and prior.get("schema_version") == 2
        and prior.get("artifact_kind") == artifact.kind
        and prior.get("data_contract") == artifact.data_contract
        and prior.get("database_utc_date") == artifact.database_utc_date
        and prior.get("snapshot_journal_mode") == "delete"
        and prior.get("snapshot_source_open_mode") in {"read_only_locked", "immutable_stable_main"}
    )
    if _apple_monthly_migration(artifact):
        from .remote_agent import apple_collection_record

        identity = apple_collection_record(
            original, expected_job=artifact.runtime_job,
            expected_month=artifact.database_month, require_path=False,
        )
        matches = (
            matches
            and prior.get("database_month") == artifact.database_month
            and prior.get("apple_collection") == identity
            and prior.get("observation_window") == identity["observation_window"]
            and artifact.observation_window == identity["observation_window"]
        )
    elif _scalar_migration(artifact) or _projection_migration(artifact) or _raw_migration(artifact):
        matches = (
            matches
            and prior.get("mode") == artifact.mode
            and prior.get("canonical") == artifact.canonical
            and prior.get("archive_date") == artifact.archive_date
        )
    else:
        matches = (
            matches
            and artifact.kind == "database_research_archive"
            and artifact.data_contract == "research-full-v1"
        )
    if not matches:
        raise RuntimeError("storage migration prior snapshot provenance mismatch")
    return {
        "contract": "daily-rsync-verified-source-snapshot-link-v1",
        "parent_manifest_path": str(path),
        "parent_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "source_snapshot_sha256": local_sha,
        "source_file_sha256": value["source_sha256"],
        "original_source_path": artifact.remote_path,
        "original_source_fingerprint": fingerprint,
        "original_source_members": members,
        "snapshot_source_open_mode": prior["snapshot_source_open_mode"],
        "physical_byte_equality_claimed": False,
    }


def storage_migration_candidate(artifact, existing) -> bool:
    """Permit verification, never publication, based on a linked original SHA."""
    value = artifact.storage_migration
    if (
        (artifact.kind != "database_research_archive" and not _apple_monthly_migration(artifact)
         and not _scalar_migration(artifact) and not _projection_migration(artifact)
         and not _raw_migration(artifact))
        or not isinstance(value, dict)
        or existing is None
    ):
        return False
    manifest = value.get("manifest", {})
    eligible = (
        isinstance(manifest, dict)
        and value.get("sidecar_path") == artifact.remote_path + (
            ".raw-migration.json" if _raw_migration(artifact) else ".storage-migration.json")
        and isinstance(value.get("sidecar_sha256"), str)
        and len(value["sidecar_sha256"]) == 64
        and all(char in "0123456789abcdef" for char in value["sidecar_sha256"])
        and manifest.get("contract") == (
            manifest.get("contract") if _raw_migration(artifact)
            else "shared-public-bodies-migration-v1")
        and manifest.get("status") == "VERIFIED"
        and manifest.get("strategy") == artifact.strategy
        and manifest.get("source_sha256") != manifest.get("destination_sha256")
        and bool(existing["local_path"])
    )
    if not eligible:
        return False
    if _apple_monthly_migration(artifact):
        if (
            not isinstance(manifest.get("apple_monthly_finalization"), dict)
            or not _SNAPSHOT_LINK_FIELDS <= manifest.keys()
        ):
            return False
    if not _SNAPSHOT_LINK_FIELDS.intersection(manifest):
        return manifest.get("source_sha256") == existing["local_sha256"]
    try:
        _verified_snapshot_link(artifact, existing)
        return True
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return False


def _file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quote(value):
    return '"' + value.replace('"', '""') + '"'


def _logical_cell(value):
    if value is None:
        return b"N"
    if isinstance(value, bytes):
        return b"B" + value
    if isinstance(value, str):
        return b"T" + value.encode("utf-8")
    if isinstance(value, int):
        return b"I" + str(value).encode("ascii")
    if isinstance(value, float):
        return b"F" + value.hex().encode("ascii")
    raise RuntimeError("unsupported SQLite logical cell")


def _table_query(connection, table, *, include_rowid):
    info = connection.execute(f"PRAGMA main.table_xinfo({_quote(table)})").fetchall()
    columns = [row[1] for row in info if row[6] == 0]
    primary = sorted((row[5], row[1]) for row in info if row[5])
    ddl = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    declared = {row[1].casefold() for row in info}
    rowid = next((name for name in ("_rowid_", "rowid", "oid") if name not in declared), None)
    if include_rowid and "WITHOUT ROWID" not in ddl.upper() and rowid:
        columns.insert(0, rowid)
    if not include_rowid and not primary:
        raise RuntimeError("level storage migration lacks a declared primary key")
    order = ",".join(_quote(name) for _, name in primary) if primary else "rowid"
    return columns, "SELECT " + ",".join(map(_quote, columns)) + " FROM " + _quote(
        table
    ) + " ORDER BY " + order


def verify_storage_migration(config, original, incoming, *, artifact, existing, snapshot, closure):
    """Recompute every logical table from the local original and uncached readback.

    A remote VERIFIED label is only a claim. Schema, SQLite state, private cells,
    declared identities and original bytes are independently checked here.
    """
    if not storage_migration_candidate(artifact, existing):
        raise RuntimeError("storage migration has no linked local original")
    proposal = artifact.storage_migration
    if snapshot.get("storage_migration") != proposal:
        raise RuntimeError("storage migration sidecar changed after scan")
    manifest = proposal["manifest"]
    original_sha = existing["local_sha256"]
    snapshot_link = (
        _verified_snapshot_link(artifact, existing)
        if _SNAPSHOT_LINK_FIELDS.intersection(manifest)
        else None
    )
    original, incoming = _safe_local(config, original), _safe_local(config, incoming)
    if not original.is_file() or _file_sha256(original) != original_sha:
        raise RuntimeError("storage migration original checksum mismatch")
    target_sha = _file_sha256(incoming)
    if target_sha != manifest.get("destination_sha256") or target_sha != snapshot.get("sha256"):
        raise RuntimeError("storage migration target checksum mismatch")
    for path in (original, incoming):
        if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")):
            raise RuntimeError("storage migration requires stable standalone databases")
    verify_database_closure(
        config,
        original,
        strategy=artifact.strategy,
        source_key=artifact.source_key,
        database_sha256=original_sha,
        expected_source_identity={
            "source": artifact.source, "jenkins_job": artifact.jenkins_job,
            "strategy": artifact.strategy, "runtime_job": artifact.runtime_job,
        },
    )
    verify_database_closure(
        config,
        incoming,
        strategy=artifact.strategy,
        source_key=artifact.source_key,
        database_sha256=target_sha,
        descriptor=closure,
        expected_source_identity={
            "source": artifact.source, "jenkins_job": artifact.jenkins_job,
            "strategy": artifact.strategy, "runtime_job": artifact.runtime_job,
        },
    )
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        raise RuntimeError("storage migration table manifest missing")

    def attest(verified, monthly=None):
        if _file_sha256(original) != original_sha or _file_sha256(incoming) != target_sha:
            raise RuntimeError("storage migration database changed during verification")
        if (
            snapshot_link is not None
            and _verified_snapshot_link(artifact, existing) != snapshot_link
        ):
            raise RuntimeError("storage migration snapshot provenance changed during verification")
        prior = json.loads(existing["metadata_json"])
        result = {
            "contract": "daily-rsync-storage-migration-lineage-v1",
            "status": "VERIFIED",
            "source_key": artifact.source_key,
            "source": artifact.source,
            "strategy": artifact.strategy,
            "remote_path": artifact.remote_path,
            "original_sha256": original_sha,
            "source_file_sha256": manifest["source_sha256"],
            "source_snapshot_link": snapshot_link,
            "generation_sha256": target_sha,
            "remote_sidecar": proposal,
            "tables": verified,
            "closure_sha256": closure["closure_sha256"],
            "source_completed_at": prior.get("source_completed_at") or prior.get("completed_at"),
            "verified_at": datetime.now(UTC).isoformat(),
        }
        if monthly is not None:
            result["apple_monthly_finalization"] = monthly
            result["database_month"] = artifact.database_month
            result["complete_utc_day"] = False
        return result

    reader = PayloadReader(config.public_store_path) if (
        closure["payload_count"] or closure.get("public_scalar_records") is not None
        or closure.get("public_projection_records") is not None
    ) else None
    connections = []
    try:
        references = PayloadReferences(reader=reader, cache_bytes=0)
        if _apple_monthly_migration(artifact):
            from polybot_observability.market_data_apple_finalize import (
                verify_monthly_finalization,
            )

            finalized = verify_monthly_finalization(
                original, incoming, references, manifest,
                source_snapshot_sha256=original_sha if snapshot_link is not None else None,
            )
            if (
                finalized["job"] != artifact.runtime_job
                or finalized["month"] != artifact.database_month
            ):
                raise RuntimeError("Apple monthly finalization identity changed")
            return attest(finalized["tables"], finalized)
        for path in (original, incoming):
            connection = resolving_connect(
                path.as_uri() + "?mode=ro&immutable=1", uri=True, references=references
            )
            connections.append(connection)
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("storage migration integrity check failed")
        old, new = connections
        if _raw_migration(artifact):
            from .raw_storage_migrations import verify_raw_transition

            checked = verify_raw_transition(
                old, new, references, manifest, original=original, incoming=incoming,
                strategy=artifact.strategy,
            )
            result = attest(checked["tables"])
            result.update({key: checked[key] for key in (
                "public_projection_records", "raw_schema_transition", "private_storage",
            )})
            from .source_storage_transitions import verify_source_storage_transition
            result.update(verify_source_storage_transition(
                artifact, proposal, manifest, checked, closure))
            return result
        from polybot_observability.market_data_projection_links import (
            projection_layout_metadata, validate_projection_layout,
        )
        auxiliary = validate_level_layout(new)
        scalar_auxiliary = validate_scalar_layout(new)
        source_scalar_auxiliary = validate_scalar_layout(old)
        scalar_verified = None
        if scalar_auxiliary:
            if not _scalar_migration(artifact):
                raise RuntimeError("scalar migration source namespace is unknown or changed")
            metadata = scalar_layout_metadata(new)
            if (metadata["namespace"] != manifest.get("scalar_namespace")
                    or metadata["authority_uuid"] != manifest.get("scalar_authority_uuid")
                    or metadata["strategy"] != artifact.strategy):
                raise RuntimeError("scalar migration layout authority or namespace differs")
            if source_scalar_auxiliary and scalar_layout_metadata(old) != metadata:
                raise RuntimeError("scalar migration original ownership changed")
            scalar_verified = verify_scalar_closure(reader, incoming, artifact.strategy)
            if scalar_verified != manifest.get("public_scalar_records"):
                raise RuntimeError("scalar migration receipt or closure claim differs")
            if new.execute("SELECT COUNT(*) FROM main.market_snapshots").fetchone()[0]:
                raise RuntimeError("scalar migration left unlinked inline snapshot rows")
        elif source_scalar_auxiliary or any(key in manifest for key in (
            "scalar_namespace", "scalar_authority_uuid", "public_scalar_records",
        )):
            raise RuntimeError("scalar migration removed or omitted its private layout")
        from polybot_observability.market_data_catalog_links import (
            catalog_layout_metadata, iter_catalog_rows, iter_catalog_private_rows,
            validate_catalog_layout,
        )
        projection_auxiliary = validate_projection_layout(new)
        source_projection_auxiliary = validate_projection_layout(old)
        catalog_auxiliary = validate_catalog_layout(new)
        source_catalog_auxiliary = validate_catalog_layout(old)
        projection_verified = None
        if projection_auxiliary or catalog_auxiliary:
            if not _projection_migration(artifact):
                raise RuntimeError("projection migration source namespace is unknown or changed")
            for table, present, original_present, getter in (
                ("market_snapshots", projection_auxiliary, source_projection_auxiliary, projection_layout_metadata),
                ("market_catalog", catalog_auxiliary, source_catalog_auxiliary, catalog_layout_metadata),
            ):
                if original_present and not present:
                    raise RuntimeError("projection migration removed its original private layout")
                if not present:
                    continue
                metadata = getter(new)
                if (metadata["namespace"] != manifest.get("projection_namespace")
                        or metadata["authority_uuid"] != manifest.get("projection_authority_uuid")
                        or metadata["strategy"] != artifact.strategy):
                    raise RuntimeError("projection migration layout authority or namespace differs")
                if original_present and getter(old) != metadata:
                    raise RuntimeError("projection migration original ownership changed")
                if new.execute("SELECT COUNT(*) FROM main." + _quote(table)).fetchone()[0]:
                    raise RuntimeError("projection migration left unlinked inline rows: " + table)
            projection_verified = verify_projection_closure(reader, incoming, artifact.strategy)
            if projection_verified != manifest.get("public_projection_records"):
                raise RuntimeError("projection migration receipt or closure claim differs")
        elif source_projection_auxiliary or source_catalog_auxiliary or any(key in manifest for key in (
            "projection_namespace", "projection_authority_uuid", "public_projection_records",
        )):
            raise RuntimeError("projection migration removed or omitted its private layout")
        schema_sql = (
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
        schema = old.execute(schema_sql).fetchall()
        schema = [row for row in schema if row[1] not in source_scalar_auxiliary | source_projection_auxiliary | source_catalog_auxiliary]
        source_auxiliary = validate_level_layout(old)
        if source_auxiliary:
            from polybot_observability.market_data_levels import LAYOUT_TABLE
            from polybot_observability.market_data_migrate import _validate_source_level_links

            identity = old.execute(f"SELECT strategy FROM main.{LAYOUT_TABLE}").fetchall()
            if identity != [(artifact.strategy,)]:
                raise RuntimeError("storage migration original level strategy changed")
            _validate_source_level_links(
                old, {rule.table: rule for rule in public_level_projections(artifact.strategy)}
            )
            schema = [row for row in schema if row[1] not in source_auxiliary]
        if schema != [row for row in new.execute(schema_sql)
                      if row[1] not in auxiliary | scalar_auxiliary | projection_auxiliary | catalog_auxiliary]:
            raise RuntimeError("storage migration schema changed")
        expected_tables = {row[1] for row in schema if row[0] == "table"}
        if expected_tables != set(tables):
            raise RuntimeError("storage migration table manifest is incomplete")
        for pragma in ("application_id", "user_version", "encoding"):
            if (
                old.execute("PRAGMA " + pragma).fetchall()
                != new.execute("PRAGMA " + pragma).fetchall()
            ):
                raise RuntimeError("storage migration SQLite identity changed")
        if sorted(old.execute("PRAGMA foreign_key_check").fetchall(), key=repr) != sorted(
            new.execute("PRAGMA foreign_key_check").fetchall(), key=repr
        ):
            raise RuntimeError("storage migration foreign-key evidence changed")
        for table in ("sqlite_sequence", "sqlite_stat1", "sqlite_stat4"):
            present = [
                bool(c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())
                for c in (old, new)
            ]
            if present[0] != present[1] or (
                present[0]
                and sorted(old.execute("SELECT * FROM " + table).fetchall(), key=repr)
                != sorted(new.execute("SELECT * FROM " + table).fetchall(), key=repr)
            ):
                raise RuntimeError("storage migration SQLite internal state changed")
        level_rules = {rule.table for rule in public_level_projections(artifact.strategy)}
        verified = {}
        raw_old = sqlite3.connect(original.as_uri() + "?mode=ro&immutable=1", uri=True)
        raw_new = sqlite3.connect(incoming.as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            for table in sorted(tables):
                expected = tables[table]
                level = "externalized_level_rows" in expected
                scalar = "externalized_scalar_rows" in expected
                projection = "externalized_projection_rows" in expected
                catalog = "externalized_catalog_rows" in expected
                if scalar and (
                    table != "market_snapshots" or not scalar_auxiliary or level or projection or catalog
                    or expected.get("implicit_rowid_preserved") is not False
                    or expected.get("key_basis") != "declared INTEGER primary key"
                    or expected.get("externalized_scalar_rows") != expected.get("rows")
                    or scalar_verified["receipt_count"] != expected.get("rows")
                ):
                    raise RuntimeError("storage migration unapproved scalar identity contract")
                if table == "market_snapshots" and scalar_auxiliary and not scalar:
                    raise RuntimeError("scalar migration table manifest is incomplete")
                if projection and (
                    table != "market_snapshots" or not projection_auxiliary or level or scalar or catalog
                    or expected.get("implicit_rowid_preserved") is not False
                    or expected.get("key_basis") != "declared INTEGER primary key"
                    or expected.get("externalized_projection_rows") != expected.get("rows")
                    or projection_verified.get("layout_receipt_counts", {}).get(
                        "market_snapshots", projection_verified["receipt_count"]
                    ) != expected.get("rows") * len(projection_verified.get("layouts", {}).get(
                        "market_snapshots", projection_verified["kinds"]
                    ))
                ):
                    raise RuntimeError("storage migration unapproved projection identity contract")
                if table == "market_snapshots" and projection_auxiliary and not projection:
                    raise RuntimeError("projection migration table manifest is incomplete")
                if catalog and (
                    table != "market_catalog" or not catalog_auxiliary or level or scalar or projection
                    or expected.get("implicit_rowid_preserved") is not True
                    or expected.get("key_basis") != "declared-primary-key+rowid-v1"
                    or expected.get("externalized_catalog_rows") != expected.get("rows")
                    or projection_verified.get("layout_receipt_counts", {}).get("market_catalog")
                       != expected.get("rows") * len(catalog_layout_metadata(new)["groups"])
                ):
                    raise RuntimeError("storage migration unapproved catalog identity contract")
                if table == "market_catalog" and catalog_auxiliary and not catalog:
                    raise RuntimeError("catalog migration table manifest is incomplete")
                if level and (
                    table not in level_rules
                    or not auxiliary
                    or expected.get("implicit_rowid_preserved") is not False
                    or expected.get("externalized_level_rows") != expected.get("rows")
                ):
                    raise RuntimeError("storage migration unapproved level identity contract")
                if catalog:
                    columns = ["__rowid__", *(row[1] for row in old.execute(
                        "PRAGMA main.table_xinfo(market_catalog)"
                    ) if row[6] == 0)]
                else:
                    columns, query = _table_query(old, table, include_rowid=not (level or scalar or projection))
                digests, counts = [], []
                for connection in (old, new):
                    digest, count = hashlib.sha256(), 0
                    rows = (iter_catalog_rows(connection, artifact.strategy, include_rowid=True)
                            if catalog else connection.execute(query))
                    for row in rows:
                        for cell in row:
                            data = _logical_cell(cell)
                            digest.update(len(data).to_bytes(8, "big"))
                            digest.update(data)
                        count += 1
                    digests.append(digest.hexdigest())
                    counts.append(count)
                if (
                    counts != [expected.get("rows")] * 2
                    or digests != [expected.get("logical_sha256")] * 2
                ):
                    raise RuntimeError("storage migration decoded logical rows changed: " + table)
                if catalog:
                    from polybot_observability.market_data_catalog_profiles import catalog_profile
                    private = tuple(dict.fromkeys(("__rowid__", "condition_id",
                                                   *catalog_profile(artifact.strategy).private_columns)))
                    for left, right in zip(
                        iter_catalog_private_rows(raw_old, artifact.strategy, private),
                        iter_catalog_private_rows(raw_new, artifact.strategy, private), strict=True,
                    ):
                        if any(_logical_cell(a) != _logical_cell(b) for a, b in zip(left, right, strict=True)):
                            raise RuntimeError("storage migration private catalog context changed")
                elif projection:
                    from polybot_observability.market_data_projection_links import iter_projection_private_rows
                    from polybot_observability.market_data_projection_profiles import snapshot_profile
                    private = tuple(name for name in columns if name in snapshot_profile(artifact.strategy).private_columns)
                    for left, right in zip(
                        iter_projection_private_rows(raw_old, artifact.strategy, private),
                        iter_projection_private_rows(raw_new, artifact.strategy, private), strict=True,
                    ):
                        if any(_logical_cell(a) != _logical_cell(b) for a, b in zip(left, right, strict=True)):
                            raise RuntimeError("storage migration private projection context changed")
                elif not (level or scalar):
                    private = [
                        i
                        for i, name in enumerate(columns)
                        if name not in public_columns(artifact.strategy, table)
                    ]
                    for left, right in zip(
                        raw_old.execute(query), raw_new.execute(query), strict=True
                    ):
                        for i in private:
                            mixed = is_mixed_payload(right[i])
                            if mixed and verify_mixed_ownership(
                                artifact.strategy, table, columns[i], left[i], right[i], references,
                            ):
                                continue
                            if mixed or _logical_cell(left[i]) != _logical_cell(right[i]):
                                raise RuntimeError(
                                    "storage migration private cell storage changed: " + table
                                )
                verified[table] = {"rows": counts[0], "logical_sha256": digests[0]}
        finally:
            raw_old.close()
            raw_new.close()
        result = attest(verified)
        if scalar_verified is not None:
            result["public_scalar_records"] = scalar_verified
        if projection_verified is not None:
            result["public_projection_records"] = projection_verified
        return result
    finally:
        for connection in connections:
            connection.close()
        if reader is not None:
            reader.close()


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
    config, *, source_key, strategy, database_sha256, hashes, verification, remote_identity=None,
    scalar_records=None, projection_records=None,
):
    return {
        "contract": CONTRACT,
        "status": "VERIFIED",
        "source_key": source_key,
        "source": config.ssh_host,
        "strategy": strategy,
        "database_sha256": database_sha256,
        "shared_store": STORE_RELATIVE_PATH if (
            hashes or scalar_records is not None or projection_records is not None
        ) else None,
        "source_identity": {
            "source": config.ssh_host,
            "configured_public_db": config.remote_public_db,
            "configured_storage_root": config.remote_public_storage_root,
            "export_identity": remote_identity,
        },
        "verified_at": datetime.now(UTC).isoformat(),
        **verification,
        **({"public_scalar_records": scalar_records} if scalar_records is not None else {}),
        **({"public_projection_records": projection_records} if projection_records is not None else {}),
    }


def _private_reference_preflight(database: Path, strategy: str | None):
    from polybot_observability.market_data_migrate import validate_private_reference_ownership
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        validate_private_reference_ownership(connection, strategy or "")
    finally:
        connection.close()


def synchronize_database_closure(
    config: AppConfig,
    remote,
    database: Path,
    *,
    strategy: str | None,
    source_key: str,
    database_sha256: str,
    ensure_capacity,
    expected_source_identity: dict | None = None,
) -> dict:
    _safe_local(config, database)
    _private_reference_preflight(database, strategy)
    hashes = reference_closure(database, strategy or "")
    route = _scalar_route(scalar_identity(database, strategy or ""), strategy,
                          expected_source_identity)
    projection_route = _projection_route(projection_identity(database, strategy or ""), strategy,
                                         expected_source_identity)
    from .scalar_payloads import synchronize_scalar_closure
    scalar_records = synchronize_scalar_closure(
        config, remote, database, strategy=strategy or "", ensure_capacity=ensure_capacity,
    )
    if scalar_records is not None:
        scalar_records["route_identity"] = route
        scalar_records["route_identity_verified"] = route is not None
    from .projection_payloads import synchronize_projection_closure
    projection_records = synchronize_projection_closure(
        config, remote, database, strategy=strategy or "", ensure_capacity=ensure_capacity,
    )
    if projection_records is not None:
        projection_records["route_identity"] = projection_route
        projection_records["route_identity_verified"] = projection_route is not None
    if not hashes:
        return _descriptor(
            config,
            source_key=source_key,
            strategy=strategy,
            database_sha256=database_sha256,
            hashes=[],
            verification={"closure_sha256": closure_digest([]), "payload_count": 0, "raw_bytes": 0},
            scalar_records=scalar_records,
            projection_records=projection_records,
        )
    store_path = _safe_local(config, config.public_store_path)
    if store_path.exists():
        with PayloadReader(store_path) as reader:
            missing = missing_payloads(reader, hashes)
    else:
        missing = hashes
    missing_set = set(missing)
    export_identity = None
    cleanup_pending = []
    index_digest = hashlib.sha256()
    index_count = index_bytes = index_batches = 0
    root_identity = (config.data_root.stat().st_dev, config.data_root.stat().st_ino)

    def fetch_page(batch, after):
        nonlocal export_identity, index_count, index_bytes, index_batches
        bodies = sorted(set(batch) & missing_set)
        try:
            response = remote.export_public_payloads(
                batch, payload_hashes=bodies, observation_after=after,
            )
        except PublicPayloadBundleLimitError:
            if len(batch) < 2 or after is not None:
                raise
            middle = len(batch) // 2
            fetch(batch[:middle])
            fetch(batch[middle:])
            return
        manifest = response["manifest"]
        bundle_path = response["bundle_path"]
        try:
            if (manifest.get("contract") != "public-payload-bundle-v2"
                    or manifest.get("reference_hashes") != sorted(set(batch))
                    or manifest.get("payload_hashes") != bodies
                    or manifest.get("observation_after") != after):
                raise RuntimeError("public bundle contains unrequested payloads")
            identity = response["source_identity"]
            if export_identity is not None and identity != export_identity:
                raise RuntimeError("remote public payload store identity changed during sync")
            export_identity = identity
            ensure_capacity(
                (int(manifest["raw_bytes"]) + int(manifest["index_bytes"])) * 4 + (8 << 20)
            )
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
                    imported = import_bundle(incoming, manifest, writer)
                missing_set.difference_update(bodies)
                index_count += imported["imported_observation_count"]
                index_bytes += imported["index_bytes"]
                index_batches += 1
                index_digest.update(json.dumps(
                    [manifest["closure_sha256"], after, manifest["next_observation_after"],
                     imported["observation_sha256"]],
                    separators=(",", ":"),
                ).encode() + b"\n")
        finally:
            try:
                remote.cleanup_public_payloads(bundle_path, manifest["file_sha256"])
            except Exception as error:
                # A leftover remote verified staging bundle is not a failed import.
                cleanup_pending.append({"bundle_path": bundle_path, "error": type(error).__name__})

        return manifest["next_observation_after"]

    def fetch(batch):
        after = None
        while True:
            after = fetch_page(batch, after)
            if after is None:
                break

    # Refresh public provenance even when immutable body bytes already exist.
    for offset in range(0, len(hashes), 1024):
        fetch(hashes[offset : offset + 1024])
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
        scalar_records=scalar_records,
        projection_records=projection_records,
    )
    descriptor["public_observation_transfer"] = {
        "contract": "public-observation-transfer-v1",
        "snapshot_scope": "per-page-read-snapshot-v1",
        "concurrent_append_policy": "earlier-cursor-receipts-require-next-refresh",
        "batch_count": index_batches,
        "batch_index_sha256": index_digest.hexdigest(),
        "observation_count": index_count,
        "index_bytes": index_bytes,
    }
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
    expected_source_identity: dict | None = None,
) -> dict | None:
    _safe_local(config, database)
    _private_reference_preflight(database, strategy)
    hashes = reference_closure(database, strategy or "")
    scalar_owner = scalar_identity(database, strategy or "")
    route = _scalar_route(scalar_owner, strategy, expected_source_identity)
    projection_owner = projection_identity(database, strategy or "")
    projection_route = _projection_route(projection_owner, strategy, expected_source_identity)
    sidecar = _safe_local(config, closure_sidecar(database))
    if descriptor is None and sidecar.exists():
        if not sidecar.is_file() or sidecar.stat().st_size > 65_536:
            raise RuntimeError("public payload closure attestation is invalid")
        descriptor = json.loads(sidecar.read_text(encoding="utf-8"))
    if not hashes and scalar_owner is None and projection_owner is None and descriptor is None:
        return None  # Legacy inline snapshots do not require a shared store.
    source = source or config.ssh_host
    expected = {
        "contract": CONTRACT,
        "status": "VERIFIED",
        "source_key": source_key,
        "source": source,
        "strategy": strategy,
        "database_sha256": database_sha256,
        "shared_store": STORE_RELATIVE_PATH if (
            hashes or scalar_owner is not None or projection_owner is not None
        ) else None,
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
    if hashes or scalar_owner is not None or projection_owner is not None:
        store = _safe_local(config, config.public_store_path)
        with PayloadReader(store) as reader:
            verification = verify_closure(reader, hashes)
            scalar_verified = (
                verify_scalar_closure(reader, database, strategy or "") if scalar_owner else None
            )
            projection_verified = (
                verify_projection_closure(reader, database, strategy or "") if projection_owner else None
            )
        if any(descriptor.get(key) != value for key, value in verification.items()):
            raise RuntimeError("public payload evidence gap: closure verification changed")
        if scalar_owner is not None:
            supplied = descriptor.get("public_scalar_records")
            if not isinstance(supplied, dict) or any(
                supplied.get(key) != value for key, value in scalar_verified.items()
            ):
                raise RuntimeError("public scalar evidence gap: closure verification changed")
            if supplied.get("route_identity_verified") is True:
                _scalar_route(scalar_owner, strategy, supplied.get("route_identity"))
                if supplied.get("route_identity") is None:
                    raise RuntimeError("scalar route attestation is incomplete")
            descriptor = {**descriptor, "public_scalar_records": {
                **supplied, "route_identity": route, "route_identity_verified": route is not None,
            }}
        if projection_owner is not None:
            supplied = descriptor.get("public_projection_records")
            if not isinstance(supplied, dict) or any(
                supplied.get(key) != value for key, value in projection_verified.items()
            ):
                raise RuntimeError("public projection evidence gap: closure verification changed")
            if supplied.get("route_identity_verified") is True:
                _projection_route(projection_owner, strategy, supplied.get("route_identity"))
                if supplied.get("route_identity") is None:
                    raise RuntimeError("projection route attestation is incomplete")
            descriptor = {**descriptor, "public_projection_records": {
                **supplied, "route_identity": projection_route,
                "route_identity_verified": projection_route is not None,
            }}
    elif descriptor.get("raw_bytes") != 0:
        raise RuntimeError("inline snapshot has an invalid public payload closure")
    if scalar_owner is None and descriptor.get("public_scalar_records") is not None:
        raise RuntimeError("public scalar closure has no private membership layout")
    if projection_owner is None and descriptor.get("public_projection_records") is not None:
        raise RuntimeError("public projection closure has no private context layout")
    return descriptor
