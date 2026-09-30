"""Independent original-to-derivative proof for reviewed RAW schema transitions.

A migration claim cannot replace comparing the existing verified original with
the derivative. Callers establish file identity and immutable read boundaries;
this shared verifier checks all logical rows, private cells and dependencies.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing

from polybot_observability.market_data_projection_closure import verify_projection_closure
from polybot_observability.market_data_raw_links import (
    LAYOUT_TABLE,
    iter_raw_logical_rows,
    raw_layout_metadata,
    validate_raw_layout,
    validate_raw_source_schema,
)
from polybot_observability.market_data_raw_profiles import BLACK_PROFILE_ID, raw_profile
from polybot_observability.market_data_scalars import scalar_cell_bytes


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _logical_fingerprint(connection, table, references, profile, namespace):
    from polybot_observability.market_data_private_packets import PrivatePacketReader

    if table in profile.tables:
        columns = ("__rowid__", *profile.tables[table].columns)
        rows = (
            tuple(row[column] for column in columns)
            for row in iter_raw_logical_rows(
                connection, table, references=references, profile_id=profile.profile_id
            )
        )
    elif table in profile.level_tables:
        from polybot_observability.market_data_levels import iter_level_logical_rows
        columns = ('__rowid__', *(row[1] for row in connection.execute(
            'PRAGMA main.table_info(' + _quote(table) + ')')))
        rows = (tuple(row[column] for column in columns) for row in iter_level_logical_rows(
            connection, profile.strategy, table, references=references, require_rowid=True))
    else:
        from polybot_observability.market_data_migrate import _select

        columns, query = _select(connection, table, include_generated=True)
        query = query.replace(" FROM " + _quote(table), " FROM main." + _quote(table), 1)
        rows = connection.execute(query)
    digest = hashlib.sha256()
    count = 0
    packets = PrivatePacketReader(connection, strategy=profile.strategy, namespace=namespace)
    for row in rows:
        expanded = [
            packets.resolve(cell, table=table, column=column)
            for column, cell in zip(columns, row, strict=True)
        ]
        for cell in references.decode_many(expanded):
            encoded = (
                b"B" + len(cell).to_bytes(8, "big") + cell
                if type(cell) is bytes
                else scalar_cell_bytes(cell)
            )
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        count += 1
    return {"rows": count, "logical_sha256": digest.hexdigest()}


def verify_raw_transition(old, new, references, manifest, *, original, incoming, strategy):
    from polybot_observability.market_data_migrate import validate_private_reference_ownership
    from polybot_observability.market_data_private_packets import validate_private_packets
    from polybot_observability.market_data_levels import validate_level_layout
    from polybot_observability.market_data_raw_migrate import (
        verify_raw_private_storage,
        verify_raw_schema_transition,
    )

    profile_id = manifest.get("raw_profile_id", BLACK_PROFILE_ID)
    profile = raw_profile(profile_id)
    if (
        strategy != profile.strategy
        or manifest.get("digest_contract") != "raw-typed-scalar-cell-length-prefix-v1"
    ):
        raise RuntimeError("RAW migration strategy or digest contract is unsupported")
    if profile_id != BLACK_PROFILE_ID and (
        manifest.get("contract") != "shared-raw-parent-derivative-v2"
        or type(manifest.get("raw_profile_version")) is not int
        or manifest["raw_profile_version"] != profile.version
        or manifest.get("raw_logical_schema_sha256") != profile.logical_schema_sha256
    ):
        raise RuntimeError("RAW derivative schema profile differs")
    validate_raw_source_schema(old, profile_id=profile_id)
    old_raw_auxiliary = validate_raw_layout(old)
    validate_raw_layout(new)
    owner = raw_layout_metadata(new)
    if (
        owner is None
        or (owner["namespace"], owner["authority_uuid"])
        != (
            manifest.get("projection_namespace"),
            manifest.get("projection_authority_uuid"),
        )
        or owner.get("profile_id") != profile_id
    ):
        raise RuntimeError("RAW migration namespace, authority or schema profile differs")
    namespace = owner["namespace"]
    old_packets = validate_private_packets(old, strategy=strategy, namespace=namespace)
    validate_private_packets(new, strategy=strategy, namespace=namespace)
    old_levels = validate_level_layout(old)
    validate_level_layout(new)
    source_owner = raw_layout_metadata(old)
    if source_owner is not None and source_owner != owner:
        raise RuntimeError("RAW migration original ownership changed")
    schema = verify_raw_schema_transition(old, new, profile_id=profile_id)
    if schema["source"]["sha256"] != manifest.get("source_schema_sha256") or schema["target"][
        "sha256"
    ] != manifest.get("target_schema_sha256"):
        raise RuntimeError("RAW migration schema fingerprint claim differs")
    projection = verify_projection_closure(references.reader, incoming, strategy)
    if projection != manifest.get("public_projection_records"):
        raise RuntimeError("RAW migration projection closure claim differs")
    for connection in (old, new):
        validate_private_reference_ownership(connection, strategy, references=references)
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("RAW migration foreign-key evidence is invalid")
    tables = {
        row[0]
        for row in old.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        if row[0] not in old_raw_auxiliary and row[0] not in old_packets and row[0] not in old_levels
    }
    claims = manifest.get("tables")
    if not isinstance(claims, dict) or set(claims) != tables:
        raise RuntimeError("RAW migration table manifest is incomplete")
    verified = {}
    for table in sorted(tables):
        claim = claims[table]
        if table in profile.tables and (
            claim.get("externalized_raw_rows") != claim.get("rows")
            or claim.get("key_basis") != "declared-primary-key+rowid-v1"
            or claim.get("implicit_rowid_preserved") is not True
            or projection["layout_receipt_counts"].get(table) != claim.get("rows")
        ):
            raise RuntimeError("RAW migration original row identity claim differs")
        if table in profile.level_tables and (
            claim.get('externalized_level_rows') != claim.get('rows')
            or claim.get('key_basis') != 'declared-primary-key+rowid-v1'
            or claim.get('implicit_rowid_preserved') is not True
        ):
            raise RuntimeError('RAW level original row identity claim differs')
        expected = {key: claim.get(key) for key in ("rows", "logical_sha256")}
        before = _logical_fingerprint(old, table, references, profile, namespace)
        after = _logical_fingerprint(new, table, references, profile, namespace)
        if before != expected or after != expected:
            raise RuntimeError("RAW migration logical cells or original rowids changed: " + table)
        verified[table] = before
    # Plain sqlite connections preserve physical private markers. Resolving
    # connections would otherwise conceal a private value laundered into CAS.
    with closing(sqlite3.connect(original.as_uri() + "?mode=ro&immutable=1", uri=True)) as raw_old:
        with closing(
            sqlite3.connect(incoming.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as raw_new:
            private = verify_raw_private_storage(
                raw_old, raw_new, references, profile_id=profile_id
            )
    if private != manifest.get("private_storage"):
        raise RuntimeError("RAW migration private storage claim differs")
    result = {
        "tables": verified,
        "public_projection_records": projection,
        "raw_schema_transition": schema,
        "private_storage": private,
    }
    return result
