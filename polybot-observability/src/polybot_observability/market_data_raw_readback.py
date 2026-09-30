"""Recheck a derivative against a separately trusted original-transition proof.

This does not authorize retirement or establish trust in the supplied proof.
The caller must bind it to an independently verified immutable catalog record.
"""
from __future__ import annotations

from .market_data_bundle import _guarded_sqlite
from .market_data_migrate import validate_private_reference_ownership
from .market_data_raw_links import raw_layout_metadata, validate_raw_source_schema
from .market_data_raw_migrate import raw_private_fingerprint, raw_schema_evidence
from .market_data_raw_profiles import raw_profile
from .market_data_raw_transition import _logical_fingerprint


def verify_raw_readback(connection, references, verification, *, profile_id, guard=None):
    """Compare every typed logical row and physical private cell, preserving rowids."""
    profile = raw_profile(profile_id)
    with _guarded_sqlite(connection, guard):
        validate_raw_source_schema(connection, profile_id=profile_id)
        owner = raw_layout_metadata(connection)
        expected_owner = verification.get("public_projection_records") or {}
        if (owner is None or owner["profile_id"] != profile_id
                or owner["namespace"] != expected_owner.get("namespace")
                or owner["authority_uuid"] != expected_owner.get("authority_uuid")
                or references.reader is None
                or references.reader.scalar_authority_identity() != owner["authority_uuid"]):
            raise RuntimeError("RAW derivative readback owner differs from trusted proof")
        schema = raw_schema_evidence(connection)
        if schema != verification.get("raw_schema_transition", {}).get("target"):
            raise RuntimeError("RAW derivative readback physical schema differs")
        expected_tables = verification.get("tables")
        expected_private = verification.get("private_storage")
        tables = {name for kind, name, _, _ in profile.source_objects if kind == "table"}
        if (not isinstance(expected_tables, dict) or set(expected_tables) != tables
                or not isinstance(expected_private, dict) or set(expected_private) != tables):
            raise RuntimeError("RAW derivative readback table proof is incomplete")
        validate_private_reference_ownership(connection, profile.strategy, references=references)
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("RAW derivative readback has foreign-key violations")
        logical, private = {}, {}
        for table in sorted(tables):
            if guard:
                guard()
            logical[table] = _logical_fingerprint(connection, table, references, profile, owner["namespace"])
            if logical[table] != expected_tables[table]:
                raise RuntimeError("RAW derivative readback logical evidence differs: " + table)
            private[table] = raw_private_fingerprint(connection, table, profile_id=profile_id, references=references)
            claim = expected_private[table]
            if private[table] != {"rows": claim.get("rows"), "columns": claim.get("columns"),
                                  "sha256": claim.get("target_sha256")}:
                raise RuntimeError("RAW derivative readback private evidence differs: " + table)
        return {"physical_schema": schema, "tables": logical, "private_storage": private}
