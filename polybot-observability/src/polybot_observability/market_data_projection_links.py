"""Extended snapshot public groups with transactional private runtime context.

The original full table remains the schema/constraint authority and a source of
legacy inline rows. Runtime clocks and decisions never enter immutable public
groups. Readers recover the original column order through a connection-local view.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from collections import OrderedDict

from .market_data_projection_profiles import (
    projection_profile,
    snapshot_profile,
    split_snapshot_row,
    validate_private_updates,
)
from .market_data_refs import MissingMarketDataConfiguration, configured_references
from .market_data_scalar_links import (
    _authority,
    _configured_namespace,
    _namespace,
)

CONTRACT = "public-projection-links-v1"
LAYOUT_TABLE = "_public_projection_layout"
CONTEXT_TABLE = "_public_projection_context"
CONDITION_TABLE = "_public_projection_conditions"
RUN_TABLE = "_public_projection_runs"
ATTACHMENT = "public_projection"
CACHE_SIZE = 1024
RECORD_CACHE_SIZE = 8192
CACHE_BYTES = 8 << 20
TABLE = "market_snapshots"


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _affinity(declaration):
    declaration = declaration.upper()
    if "INT" in declaration:
        return "INTEGER"
    if any(part in declaration for part in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in declaration or not declaration:
        return "BLOB"
    if any(part in declaration for part in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _main_info(connection, strategy):
    profile = snapshot_profile(strategy)
    info = connection.execute("PRAGMA main.table_xinfo(market_snapshots)").fetchall()
    by_name = {row[1]: row for row in info}
    if set(by_name) != set(profile.column_names) or any(row[6] for row in info):
        raise ValueError("extended snapshot schema differs from its reviewed profile")
    for column in profile.columns:
        actual = by_name[column.name]
        nullable = {column.nullable}
        if column.legacy_nullable is not None:
            nullable.add(column.legacy_nullable)
        if (
            _affinity(actual[2]) != column.affinity
            or (column.name != "id" and (not bool(actual[3])) not in nullable)
            or bool(actual[5]) != (column.name == "id")
        ):
            raise ValueError(
                "extended snapshot affinity/nullability/key differs from profile"
            )
    ddl = connection.execute(
        "SELECT sql FROM main.sqlite_master WHERE type='table' AND name='market_snapshots'"
    ).fetchone()[0]
    if (
        by_name["id"][2].upper() != "INTEGER"
        or "WITHOUT ROWID" in ddl.upper()
        or connection.execute(
            "PRAGMA main.foreign_key_list(market_snapshots)"
        ).fetchone()
        or connection.execute(
            "SELECT 1 FROM main.sqlite_master WHERE type='trigger' AND tbl_name='market_snapshots'"
        ).fetchone()
    ):
        raise ValueError(
            "unsupported extended snapshot rowid/trigger/foreign-key layout"
        )
    unique = []
    for row in connection.execute("PRAGMA main.index_list(market_snapshots)"):
        if not row[2]:
            continue
        columns = tuple(
            value[2]
            for value in connection.execute(
                "PRAGMA main.index_info(" + _quote(row[1]) + ")"
            )
        )
        if row[4] or columns not in profile.unique_constraints:
            raise ValueError("unsupported extended snapshot unique constraint")
        unique.append(columns)
    return profile, info, tuple(sorted(unique)), ddl


def _identity_columns(profile):
    return tuple(
        name
        for name in ("condition_id", "token_id", "event_id", "outcome")
        if name in profile.public_columns
    )


def _context_columns(profile, info):
    retained = (
        set(profile.private_columns)
        | set(profile.body_columns)
        | set(_identity_columns(profile))
    )
    return tuple(row[1] for row in info if row[1] in retained)


def _physical(name):
    return {"condition_id": "condition_key", "run_id": "run_key"}.get(name, name)


def _context_from():
    return (
        f"main.{CONTEXT_TABLE} AS c LEFT JOIN main.{CONDITION_TABLE} AS cd "
        "ON cd.id=c.condition_key "
        f"LEFT JOIN main.{RUN_TABLE} AS rd ON rd.id=c.run_key"
    )


def _context_expression(name):
    return {"condition_id": "cd.condition_id", "run_id": "rd.run_id"}.get(
        name, "c." + _quote(name)
    )


def _intern(connection, table, column, value):
    if value is None:
        return None
    found = connection.execute(
        f"SELECT id FROM main.{table} WHERE {_quote(column)}=?", (value,)
    ).fetchone()
    if found is not None:
        return found[0]
    return connection.execute(
        f"INSERT INTO main.{table}({_quote(column)}) VALUES(?)", (value,)
    ).lastrowid


def _schema(connection, strategy):
    profile, info, unique, _ = _main_info(connection, strategy)
    retained = _context_columns(profile, info)
    clauses = []
    for row in info:
        if row[1] not in retained:
            continue
        declaration = (
            _quote(_physical(row[1]))
            + " "
            + ("INTEGER" if row[1] in {"condition_id", "run_id"} else _affinity(row[2]))
        )
        if row[1] == "id":
            declaration += " PRIMARY KEY"
        elif row[3]:
            declaration += " NOT NULL"
        if row[1] == "condition_id":
            declaration += f" REFERENCES {CONDITION_TABLE}(id)"
        elif row[1] == "run_id":
            declaration += f" REFERENCES {RUN_TABLE}(id)"
        clauses.append(declaration)
    clauses.extend(
        f"projection_{index}_id INTEGER NOT NULL CHECK(projection_{index}_id>0)"
        for index in range(len(profile.groups))
    )
    clauses.extend(
        "UNIQUE(" + ",".join(_quote(_physical(name)) for name in key) + ")"
        for key in unique
    )
    statements = [
        (
            f"CREATE TABLE {LAYOUT_TABLE}(singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
            "contract TEXT NOT NULL,strategy TEXT NOT NULL,namespace TEXT NOT NULL,"
            "authority_uuid TEXT NOT NULL)"
        ),
        f"CREATE TABLE {CONDITION_TABLE}(id INTEGER PRIMARY KEY,condition_id TEXT NOT NULL UNIQUE)",
        f"CREATE TABLE {RUN_TABLE}(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL UNIQUE)",
        f"CREATE TABLE {CONTEXT_TABLE}(" + ",".join(clauses) + ")",
        (
            f"CREATE INDEX _public_projection_condition_time ON {CONTEXT_TABLE}"
            "(condition_key,timestamp,id)"
        ),
    ]
    for index in range(len(profile.groups)):
        statements.append(
            f"CREATE INDEX _public_projection_group_{index} ON "
            f"{CONTEXT_TABLE}(projection_{index}_id)"
        )
    if "run_id" in retained:
        statements.append(
            f"CREATE INDEX _public_projection_run ON {CONTEXT_TABLE}(run_key)"
        )
    if "token_id" in retained:
        statements.append(
            f"CREATE INDEX _public_projection_token_time ON "
            f"{CONTEXT_TABLE}(condition_key,token_id,timestamp,id)"
        )
    return tuple(statements)


def projection_layout_metadata(connection):
    present = connection.execute(
        "SELECT 1 FROM main.sqlite_master WHERE name=?", (LAYOUT_TABLE,)
    ).fetchone()
    if not present:
        if connection.execute(
            "SELECT 1 FROM main.sqlite_master WHERE name GLOB '_public_projection_*'"
        ).fetchone():
            raise ValueError("extended projection layout is incomplete")
        return None
    rows = connection.execute(
        f"SELECT singleton,contract,strategy,namespace,authority_uuid FROM main.{LAYOUT_TABLE}"
    ).fetchall()
    if len(rows) != 1 or tuple(rows[0])[:2] != (1, CONTRACT):
        raise ValueError("unsupported extended projection layout identity")
    _, _, strategy, namespace, authority = rows[0]
    snapshot_profile(strategy)
    _namespace(namespace, strategy)
    _authority(authority)
    return {
        "strategy": strategy,
        "namespace": namespace,
        "authority_uuid": authority,
        "groups": snapshot_profile(strategy).groups,
    }


def validate_projection_layout(connection):
    metadata = projection_layout_metadata(connection)
    if metadata is None:
        return frozenset()
    actual = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM main.sqlite_master "
            "WHERE name GLOB '_public_projection_*' AND sql IS NOT NULL ORDER BY type,name"
        )
    )
    temporary = sqlite3.connect(":memory:")
    try:
        for statement in _schema(connection, metadata["strategy"]):
            temporary.execute(statement)
        expected = tuple(
            temporary.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE sql IS NOT NULL ORDER BY type,name"
            )
        )
    finally:
        temporary.close()
    if actual != expected:
        raise ValueError("extended projection auxiliary schema differs")
    return frozenset(row[1] for row in actual)


def initialize_projection_links(connection, strategy, namespace, authority_uuid):
    _namespace(namespace, strategy)
    _authority(authority_uuid)
    from .market_data_catalog_links import (
        catalog_layout_metadata,
        validate_catalog_layout,
    )

    if validate_catalog_layout(connection):
        sibling = catalog_layout_metadata(connection)
        if (sibling["strategy"], sibling["namespace"], sibling["authority_uuid"]) != (
            strategy, namespace, authority_uuid
        ):
            raise ValueError("catalog and snapshot projection ownership differ")
    metadata = projection_layout_metadata(connection)
    if metadata is not None:
        validate_projection_layout(connection)
        if metadata != {
            "strategy": strategy,
            "namespace": namespace,
            "authority_uuid": authority_uuid,
            "groups": snapshot_profile(strategy).groups,
        }:
            raise ValueError("extended projection ownership changed")
        return
    if not connection.in_transaction:
        raise ValueError(
            "projection layout initialization requires the caller transaction"
        )
    for statement in _schema(connection, strategy):
        connection.execute(statement)
    connection.execute(
        f"INSERT INTO main.{LAYOUT_TABLE} VALUES(1,?,?,?,?)",
        (CONTRACT, strategy, namespace, authority_uuid),
    )


def iter_projection_link_rows(connection):
    """Stream original snapshot IDs and each immutable group association."""
    if not validate_projection_layout(connection):
        return
    metadata = projection_layout_metadata(connection)
    groups = metadata["groups"]
    columns = ",".join(f"projection_{index}_id" for index in range(len(groups)))
    cursor = connection.execute(
        f"SELECT id,{columns} FROM main.{CONTEXT_TABLE} ORDER BY id"
    )
    try:
        for row in cursor:
            original_id, *record_ids = row
            if type(original_id) is not int or any(
                type(value) is not int or value <= 0 for value in record_ids
            ):
                raise ValueError("invalid private projection membership identity")
            for kind, record_id in zip(groups, record_ids, strict=True):
                yield original_id, kind, record_id
    finally:
        cursor.close()


def iter_projection_record_ids(connection):
    """Use indexed SQL UNION to stream distinct group IDs without a Python set."""
    if not validate_projection_layout(connection):
        return
    groups = projection_layout_metadata(connection)["groups"]
    query = (
        " UNION ".join(
            f"SELECT DISTINCT projection_{index}_id AS record_id FROM main.{CONTEXT_TABLE}"
            for index in range(len(groups))
        )
        + " ORDER BY record_id"
    )
    cursor = connection.execute(query)
    try:
        for (record_id,) in cursor:
            if type(record_id) is not int or record_id <= 0:
                raise ValueError("invalid private projection record identity")
            yield record_id
    finally:
        cursor.close()


def iter_projection_private_rows(connection, strategy, columns):
    """Stream physical local private cells, expanding only local key dictionaries."""
    profile, _info, _, _ = _main_info(connection, strategy)
    allowed = (
        set(profile.private_columns)
        | set(profile.body_columns)
        | set(_identity_columns(profile))
    )
    columns = tuple(columns)
    if not columns or len(columns) != len(set(columns)) or not set(columns) <= allowed:
        raise ValueError("private projection verification requested unowned columns")
    has_layout = bool(validate_projection_layout(connection))
    if has_layout and projection_layout_metadata(connection)["strategy"] != strategy:
        raise ValueError("private projection verification strategy differs")
    main = (
        "SELECT id AS ordered_id,"
        + ",".join(map(_quote, columns))
        + ",0,NULL,NULL,NULL,NULL FROM main.market_snapshots"
    )
    if has_layout:
        main += (
            " UNION ALL SELECT c.id,"
            + ",".join(_context_expression(name) for name in columns)
            + ",1,c.condition_key,cd.id,c.run_key,rd.id FROM "
            + _context_from()
        )
    cursor = sqlite3.Connection.cursor(connection)
    cursor.row_factory = None
    previous = None
    try:
        for row in cursor.execute(main + " ORDER BY ordered_id"):
            original_id, *rest = row
            guard = rest[len(columns) :]
            if type(original_id) is not int or (
                previous is not None and original_id <= previous
            ):
                raise ValueError(
                    "private projection snapshot IDs overlap or changed type"
                )
            if guard[0] and (
                guard[1] != guard[2] or (guard[3] is not None and guard[3] != guard[4])
            ):
                raise ValueError("private projection dictionary dependency is missing")
            previous = original_id
            yield tuple(rest[: len(columns)])
    finally:
        cursor.close()


def _refs(connection, references):
    return (
        references
        or getattr(connection, "_references", None)
        or configured_references()
    )


def install_projection_views(connection, *, references=None):
    if not validate_projection_layout(connection):
        return
    metadata = projection_layout_metadata(connection)
    profile, info, _, _ = _main_info(connection, metadata["strategy"])
    codec = _refs(connection, references)
    authority = metadata["authority_uuid"]
    path = getattr(codec.reader, "path", None)
    if (
        path is None
        or path.resolve(strict=True) != path
        or codec.reader.scalar_authority_identity() != authority
    ):
        raise MissingMarketDataConfiguration(
            "projection view needs its canonical authoritative reader"
        )
    databases = {row[1]: row[2] for row in connection.execute("PRAGMA database_list")}
    if databases.get("main") == str(path):
        raise ValueError("public projection store cannot be the private runtime DB")
    if ATTACHMENT in databases:
        if databases[ATTACHMENT] != str(path):
            raise ValueError("public projection attachment path changed")
    else:
        connection.execute(
            f"ATTACH DATABASE ? AS {ATTACHMENT}", (path.as_uri() + "?mode=ro",)
        )
    binding = (metadata["strategy"], metadata["namespace"], authority, str(path))
    if (
        getattr(connection, "_projection_view_binding", None) == binding
        and connection.execute(
            "SELECT 1 FROM temp.sqlite_master WHERE type='view' AND name='market_snapshots'"
        ).fetchone()
    ):
        return
    from .market_data_projections import PROJECTION_BLOCK_ROWS
    from .market_data_scalars import scalar_cell_bytes

    records, contexts = OrderedDict(), OrderedDict()
    record_sizes, context_sizes = {}, {}
    cache_bytes = {"records": 0, "contexts": 0}

    def clear():
        records.clear()
        contexts.clear()
        record_sizes.clear()
        context_sizes.clear()
        cache_bytes.update(records=0, contexts=0)

    def remember(cache, sizes, section, key, value, size):
        if size > CACHE_BYTES:
            return
        if key in cache:
            cache_bytes[section] -= sizes.pop(key)
            del cache[key]
        maximum = RECORD_CACHE_SIZE if section == "records" else CACHE_SIZE
        while cache and (
            len(cache) >= maximum or cache_bytes[section] + size > CACHE_BYTES
        ):
            evicted, _ = cache.popitem(last=False)
            cache_bytes[section] -= sizes.pop(evicted)
        cache[key] = value
        sizes[key] = size
        cache_bytes[section] += size

    if hasattr(connection, "_projection_cache_clear"):
        connection._projection_cache_clear = clear

    def record(record_id, kind):
        if type(record_id) is not int or record_id <= 0:
            raise ValueError("invalid public projection record ID")
        cached = (
            records.get(record_id)
            if getattr(connection, "_scalar_cache_enabled", False)
            else None
        )
        if cached is not None:
            records.move_to_end(record_id)
        if cached is None:
            current = (
                _refs(connection, None) if hasattr(connection, "_references") else codec
            )
            if (
                getattr(current.reader, "path", None) != path
                or current.reader.scalar_authority_identity() != authority
            ):
                raise ValueError("projection reader authority/path changed")
            cursor = sqlite3.Connection.cursor(connection)
            cursor.row_factory = None
            try:
                query = (
                    f"SELECT r.id,k.kind,c.condition_id,t.token_id,r.source_time,r.block_id "
                    f"FROM {ATTACHMENT}.projection_records r "
                    f"LEFT JOIN {ATTACHMENT}.projection_kinds k ON k.id=r.kind_key "
                    f"LEFT JOIN {ATTACHMENT}.scalar_conditions c ON c.id=r.condition_key "
                    f"LEFT JOIN {ATTACHMENT}.projection_tokens t ON t.id=r.token_key "
                )
                selected = cursor.execute(
                    query + "WHERE r.id=?", (record_id,)
                ).fetchone()
                expected = [selected] if selected is not None else []
                if (
                    selected is not None
                    and selected[5] is not None
                    and getattr(connection, "_scalar_cache_enabled", False)
                ):
                    expected = cursor.execute(
                        query + "WHERE r.block_id=? ORDER BY r.ordinal LIMIT ?",
                        (selected[5], PROJECTION_BLOCK_ROWS + 1),
                    ).fetchall()
                    if len(expected) > PROJECTION_BLOCK_ROWS or not any(
                        row[0] == record_id for row in expected
                    ):
                        raise ValueError(
                            "projection block index exceeds the bounded cache"
                        )
            finally:
                cursor.close()
            ids = [row[0] for row in expected] if expected else [record_id]
            found = current.reader.get_projection_records(ids, authority)
            if len(found) != len(ids):
                raise ValueError("public projection record readback is incomplete")
            expected_by_id = {row[0]: row for row in expected}
            for wanted, item in zip(ids, found, strict=True):
                if (
                    item.reference.authority_uuid != authority
                    or item.reference.record_id != wanted
                    or item.reference.kind != item.projection.kind
                    or item.reference.sha256 != item.projection.sha256
                ):
                    raise ValueError(
                        "public projection record checksum/authority differs"
                    )
                index = expected_by_id.get(wanted)
                if index is not None:
                    public = dict(
                        zip(
                            projection_profile(item.projection.kind).columns,
                            item.projection.row(),
                            strict=True,
                        )
                    )
                    fields = projection_profile(item.projection.kind)
                    projected = (
                        item.projection.kind,
                        public[fields.condition_field],
                        public[fields.token_field] if fields.token_field else None,
                        public[fields.source_time_field]
                        if fields.source_time_field
                        else None,
                    )
                    if any(
                        scalar_cell_bytes(a) != scalar_cell_bytes(b)
                        for a, b in zip(index[1:5], projected, strict=True)
                    ):
                        raise ValueError(
                            "projection SQL index metadata differs from its verified group"
                        )
                if getattr(connection, "_scalar_cache_enabled", False):
                    size = (
                        sys.getsizeof(item)
                        + sys.getsizeof(item.projection)
                        + sys.getsizeof(item.reference)
                        + sys.getsizeof(item.projection.row())
                        + sys.getsizeof(item.reference.__dict__)
                        + sys.getsizeof(item.projection.__dict__)
                        + sum(
                            sys.getsizeof(value)
                            for value in item.reference.__dict__.values()
                        )
                        + sum(sys.getsizeof(value) for value in item.projection.row())
                    )
                    remember(records, record_sizes, "records", wanted, item, size)
                if wanted == record_id:
                    cached = item
        if cached.reference.kind != kind or cached.projection.kind != kind:
            raise ValueError("public projection kind differs from the private profile")
        return dict(
            zip(projection_profile(kind).columns, cached.projection.row(), strict=True)
        )

    def context(row_id):
        use_cache = getattr(connection, "_scalar_cache_enabled", False)
        cached = contexts.get(row_id) if use_cache else None
        if cached is not None:
            return cached
        cursor = sqlite3.Connection.cursor(connection)
        cursor.row_factory = None
        try:
            retained = _context_columns(profile, info)
            cursor.execute(
                "SELECT "
                + ",".join(
                    _context_expression(name) + " AS " + _quote(name)
                    for name in retained
                )
                + ","
                + ",".join(
                    f"c.projection_{index}_id" for index in range(len(profile.groups))
                )
                + ",c.condition_key,cd.id,c.run_key,rd.id FROM "
                + _context_from()
                + " WHERE c.id=?",
                (row_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("private projection context is missing")
            if row[-4] != row[-3] or (row[-2] is not None and row[-2] != row[-1]):
                raise ValueError("private projection dictionary dependency is missing")
            cached = dict(
                zip(
                    (column[0] for column in cursor.description[:-4]),
                    row[:-4],
                    strict=True,
                )
            )
        finally:
            cursor.close()
        for index, kind in enumerate(profile.groups):
            public = record(cached[f"projection_{index}_id"], kind)
            for name in _identity_columns(profile):
                if name in public and scalar_cell_bytes(
                    public[name]
                ) != scalar_cell_bytes(cached[name]):
                    raise ValueError(
                        "private projection identity differs from the public group"
                    )
            cached.update(public)
        if use_cache:
            size = sys.getsizeof(cached) + sum(
                sys.getsizeof(key) + sys.getsizeof(value)
                for key, value in cached.items()
            )
            remember(contexts, context_sizes, "contexts", row_id, cached, size)
        return cached

    connection.create_function(
        "public_projection_valid", 1, lambda row_id: int(bool(context(row_id)))
    )
    connection.create_function(
        "public_projection_cell", 2, lambda row_id, name: context(row_id)[name]
    )
    retained = _context_columns(profile, info)
    expressions = []
    for row in info:
        name = row[1]
        if name in retained:
            expression = _context_expression(name)
        else:
            expression = f"public_projection_cell(CAST(c.id AS INTEGER),'{name}')"
            expression = "CAST(" + expression + " AS " + _affinity(row[2]) + ")"
        expressions.append(expression + " AS " + _quote(name))
    names = ",".join(_quote(row[1]) for row in info)
    connection.execute(
        f"CREATE TEMP VIEW IF NOT EXISTS market_snapshots AS SELECT {names} "
        "FROM main.market_snapshots UNION ALL SELECT "
        + ",".join(expressions)
        + " FROM "
        + _context_from()
        + " WHERE public_projection_valid(CAST(c.id AS INTEGER))=1"
    )
    if hasattr(connection, "_projection_view_binding"):
        connection._projection_view_binding = binding


def _next_id(connection, ddl):
    maximum = connection.execute(
        "SELECT MAX(id) FROM (SELECT MAX(id) AS id FROM main.market_snapshots "
        f"UNION ALL SELECT MAX(id) FROM main.{CONTEXT_TABLE})"
    ).fetchone()[0]
    if "AUTOINCREMENT" in ddl.upper():
        sequence = connection.execute(
            "SELECT seq FROM main.sqlite_sequence WHERE name='market_snapshots'"
        ).fetchone()
        maximum = max(maximum or 0, sequence[0] if sequence else 0)
    if maximum == 9223372036854775807:
        if "AUTOINCREMENT" not in ddl.upper():
            for _ in range(100):
                candidate = connection.execute(
                    "SELECT random() & 9223372036854775807"
                ).fetchone()[0]
                if (
                    candidate
                    and not connection.execute(
                        "SELECT 1 FROM main.market_snapshots WHERE id=? "
                        f"UNION ALL SELECT 1 FROM main.{CONTEXT_TABLE} WHERE id=?",
                        (candidate, candidate),
                    ).fetchone()
                ):
                    return candidate
        raise sqlite3.OperationalError("database or disk is full")
    return 1 if maximum is None else maximum + 1


def insert_shared_projection_snapshot(
    connection, strategy, values, *, namespace=None, references=None
):
    namespace_was_explicit = namespace is not None
    codec = _refs(connection, references)
    metadata = projection_layout_metadata(connection)
    if codec.writer is None:
        if metadata is not None or codec.reader is not None:
            raise MissingMarketDataConfiguration(
                "projection publication requires the public writer"
            )
        return None
    profile, info, unique, ddl = _main_info(connection, strategy)
    if set(values) - set(profile.column_names):
        raise ValueError("snapshot insert contains unregistered fields")
    namespace = namespace or (
        metadata["namespace"]
        if metadata
        else _configured_namespace(connection, strategy)
    )
    _namespace(namespace, strategy)
    if (
        metadata is not None
        and not namespace_was_explicit
        and (os.environ.get("PUBLIC_MARKET_DATA_SOURCE") or os.environ.get("JOB_NAME"))
        and _configured_namespace(connection, strategy) != namespace
    ):
        raise ValueError(
            "projection source/job/runtime differs from the bound namespace"
        )
    authority = codec.reader.scalar_authority_identity()
    if codec.writer.scalar_authority_identity() != authority:
        raise ValueError("projection writer and reader authorities differ")
    own_transaction = not connection.in_transaction
    if own_transaction:
        connection.execute("BEGIN")
    savepoint = "projection_publish_" + uuid.uuid4().hex
    connection.execute("SAVEPOINT " + savepoint)
    try:
        initialize_projection_links(connection, strategy, namespace, authority)
        row = dict(values)
        row["id"] = (
            row.get("id") if row.get("id") is not None else _next_id(connection, ddl)
        )
        if connection.execute(
            f"SELECT 1 FROM main.{CONTEXT_TABLE} WHERE id=?", (row["id"],)
        ).fetchone():
            raise sqlite3.IntegrityError(
                "snapshot ID already belongs to a projection context"
            )
        names = tuple(name for name in profile.column_names if name in row)
        connection.execute("SAVEPOINT projection_affinity")
        try:
            connection.execute(
                "INSERT INTO main.market_snapshots ("
                + ",".join(map(_quote, names))
                + ") VALUES("
                + ",".join("?" for _ in names)
                + ")",
                tuple(row[name] for name in names),
            )
            raw = sqlite3.Connection.cursor(connection)
            raw.row_factory = None
            try:
                selected = raw.execute(
                    "SELECT "
                    + ",".join(map(_quote, profile.column_names))
                    + " FROM main.market_snapshots WHERE id=?",
                    (row["id"],),
                ).fetchone()
                normalized = dict(zip(profile.column_names, selected, strict=True))
            finally:
                raw.close()
        finally:
            connection.execute("ROLLBACK TO projection_affinity")
            connection.execute("RELEASE projection_affinity")
        for columns in unique:
            if (
                all(normalized[name] is not None for name in columns)
                and connection.execute(
                    "SELECT 1 FROM "
                    + _context_from()
                    + " WHERE "
                    + " AND ".join(
                        _context_expression(name) + "=?" for name in columns
                    ),
                    tuple(normalized[name] for name in columns),
                ).fetchone()
            ):
                raise sqlite3.IntegrityError(
                    "snapshot unique constraint conflicts with a private context"
                )
        # Only catalog-body columns explicitly moving into a public group are
        # decoded here. Retained CAS body columns keep their physical markers.
        from .market_data_policy import public_columns

        logical = dict(normalized)
        for name in public_columns(strategy, TABLE) & profile.public_columns:
            logical[name] = codec.decode_many([logical[name]])[0]
        split = split_snapshot_row(strategy, logical)
        from .market_data_projections import ProjectionReceipt, PublicProjection

        projections = [
            PublicProjection(group.kind, group.values) for group in split.groups
        ]
        acknowledgements = codec.writer.put_public_projections(projections)
        if len(acknowledgements) != len(projections) or any(
            ref.authority_uuid != authority
            or ref.kind != projection.kind
            or ref.sha256 != projection.sha256
            for ref, projection in zip(acknowledgements, projections, strict=True)
        ):
            raise ValueError("public projection acknowledgement differs")
        receipts = [
            ProjectionReceipt(
                namespace, TABLE, normalized["id"], ref.kind, ref.record_id
            )
            for ref in acknowledgements
        ]
        codec.writer.append_projection_receipts(receipts, authority_uuid=authority)
        restored = codec.reader.get_projection_records(
            [ref.record_id for ref in acknowledgements], authority
        )
        if (
            [record.reference for record in restored] != acknowledgements
            or [record.projection for record in restored] != projections
            or codec.reader.get_projection_receipts(
                [receipt.row() for receipt in receipts], authority_uuid=authority
            )
            != receipts
        ):
            raise ValueError("public projection/receipt independent readback differs")
        retained = _context_columns(profile, info)
        retained_values = dict(normalized)
        retained_values["condition_id"] = _intern(
            connection, CONDITION_TABLE, "condition_id", normalized["condition_id"]
        )
        retained_values["run_id"] = _intern(
            connection, RUN_TABLE, "run_id", normalized["run_id"]
        )
        target_names = (
            *(_physical(name) for name in retained),
            *(f"projection_{index}_id" for index in range(len(profile.groups))),
        )
        target_values = (
            *(retained_values[name] for name in retained),
            *(ref.record_id for ref in acknowledgements),
        )
        connection.execute(
            f"INSERT INTO main.{CONTEXT_TABLE} ("
            + ",".join(map(_quote, target_names))
            + ") VALUES("
            + ",".join("?" for _ in target_names)
            + ")",
            target_values,
        )
        if "AUTOINCREMENT" in ddl.upper():
            updated = connection.execute(
                "UPDATE main.sqlite_sequence SET seq=MAX(seq,?) WHERE name='market_snapshots'",
                (normalized["id"],),
            )
            if not updated.rowcount:
                connection.execute(
                    "INSERT INTO main.sqlite_sequence VALUES('market_snapshots',?)",
                    (max(normalized["id"], 0),),
                )
        install_projection_views(connection, references=codec)
        connection.execute("RELEASE " + savepoint)
        return normalized
    except BaseException:
        connection.execute("ROLLBACK TO " + savepoint)
        connection.execute("RELEASE " + savepoint)
        if own_transaction:
            connection.rollback()
        raise


def delete_snapshot_rows(connection, where_sql, parameters=()):
    if not validate_projection_layout(connection):
        from .market_data_scalar_links import delete_snapshot_rows as scalar_delete

        return scalar_delete(connection, where_sql, parameters)
    install_projection_views(connection)
    temporary = "projection_delete_" + uuid.uuid4().hex
    connection.execute(f"CREATE TEMP TABLE {temporary}(id INTEGER PRIMARY KEY)")
    try:
        connection.execute(
            f"INSERT INTO {temporary} SELECT id FROM market_snapshots WHERE "
            + where_sql,
            parameters,
        )
        count = connection.execute(f"SELECT COUNT(*) FROM {temporary}").fetchone()[0]
        for table in (CONTEXT_TABLE, TABLE):
            connection.execute(
                f"DELETE FROM main.{_quote(table)} WHERE id IN (SELECT id FROM {temporary})"
            )
        for dictionary, key in (
            (CONDITION_TABLE, "condition_key"),
            (RUN_TABLE, "run_key"),
        ):
            connection.execute(
                f"DELETE FROM main.{dictionary} WHERE NOT EXISTS "
                f"(SELECT 1 FROM main.{CONTEXT_TABLE} AS c WHERE c.{key}={dictionary}.id)"
            )
        return count
    finally:
        connection.execute(f"DROP TABLE {temporary}")


def _bound_model_values(session, model, values, strategy=None):
    dialect = session.get_bind().dialect
    result = {}
    for name, value in values.items():
        source_type = model.__table__.columns[name].type
        if strategy is not None and name in snapshot_profile(strategy).public_columns:
            from .market_data_sqlalchemy import original_public_type

            source_type = original_public_type(source_type)
        processor = source_type.dialect_impl(dialect).bind_processor(dialect)
        result[name] = processor(value) if processor else value
    return result


def update_private_projection_snapshot(
    connection, strategy, snapshot_id, values, *, references=None
):
    """Validate through the original table, then update only reviewed private cells."""
    validate_private_updates(strategy, values)
    if not values:
        return 0
    if not validate_projection_layout(connection):
        return connection.execute(
            "UPDATE main.market_snapshots SET "
            + ",".join(_quote(name) + "=?" for name in values)
            + " WHERE id=?",
            (*values.values(), snapshot_id),
        ).rowcount
    metadata = projection_layout_metadata(connection)
    if metadata["strategy"] != strategy:
        raise ValueError("private projection update strategy differs")
    install_projection_views(connection, references=references)
    profile = snapshot_profile(strategy)
    is_context = connection.execute(
        f"SELECT 1 FROM main.{CONTEXT_TABLE} WHERE id=?", (snapshot_id,)
    ).fetchone()
    if not is_context:
        return connection.execute(
            "UPDATE main.market_snapshots SET "
            + ",".join(_quote(name) + "=?" for name in values)
            + " WHERE id=?",
            (*values.values(), snapshot_id),
        ).rowcount
    current = connection.execute(
        "SELECT "
        + ",".join(map(_quote, profile.column_names))
        + " FROM market_snapshots WHERE id=?",
        (snapshot_id,),
    ).fetchone()
    own_transaction = not connection.in_transaction
    if own_transaction:
        connection.execute("BEGIN")
    savepoint = "projection_private_" + uuid.uuid4().hex
    connection.execute("SAVEPOINT " + savepoint)
    try:
        connection.execute(
            "INSERT INTO main.market_snapshots ("
            + ",".join(map(_quote, profile.column_names))
            + ") VALUES("
            + ",".join("?" for _ in profile.column_names)
            + ")",
            tuple(current),
        )
        connection.execute(
            "UPDATE main.market_snapshots SET "
            + ",".join(_quote(name) + "=?" for name in values)
            + " WHERE id=?",
            (*values.values(), snapshot_id),
        )
        normalized = tuple(
            connection.execute(
                "SELECT "
                + ",".join(map(_quote, values))
                + " FROM main.market_snapshots WHERE id=?",
                (snapshot_id,),
            ).fetchone()
        )
    finally:
        connection.execute("ROLLBACK TO " + savepoint)
        connection.execute("RELEASE " + savepoint)
    return connection.execute(
        f"UPDATE main.{CONTEXT_TABLE} SET "
        + ",".join(_quote(name) + "=?" for name in values)
        + " WHERE id=?",
        (*normalized, snapshot_id),
    ).rowcount


def update_projection_private(
    session, model, strategy, snapshot_id, values, *, commit=False
):
    validate_private_updates(strategy, values)
    session.flush()
    connection = session.connection().connection.driver_connection
    count = update_private_projection_snapshot(
        connection, strategy, snapshot_id, _bound_model_values(session, model, values)
    )
    if commit:
        session.commit()
    session.expire_all()
    return count


def save_projection_snapshot(session, model, strategy, values, *, commit=True):
    connection = session.connection().connection.driver_connection
    codec = _refs(connection, None)
    if (
        codec.writer is None
        and codec.reader is None
        and projection_layout_metadata(connection) is None
    ):
        return None
    session.flush()
    row = insert_shared_projection_snapshot(
        connection,
        strategy,
        _bound_model_values(session, model, values, strategy),
        references=codec,
    )
    if commit:
        session.commit()
    return session.get(model, row["id"])


def install_deferred_projection_flush(factory, model, strategy):
    """Keep legacy pending snapshots in the factory's shared storage flush batch."""
    if strategy not in {"golden-honeydew", "golden-nectarine"}:
        raise ValueError("deferred projection flush is not reviewed for this strategy")
    from .market_data_orm_flush import register_shared_flush

    def enabled(connection):
        codec = _refs(connection, None)
        return (
            codec.writer is not None
            or codec.reader is not None
            or projection_layout_metadata(connection) is not None
        )

    def publish(session, values, is_new):
        connection = session.connection().connection.driver_connection
        return insert_shared_projection_snapshot(
            connection,
            strategy,
            _bound_model_values(session, model, values, strategy),
        )

    register_shared_flush(factory, model, enabled=enabled, publish=publish)
