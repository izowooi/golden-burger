"""Immutable public catalog versions and transactional private current pointers.

The original main table remains the constraint/schema authority. Its real rowids
are preserved in context and receipts. A TEMP view preserves original SELECT *;
SQLite view.rowid is not the rowid interface: use catalog_original_rowid or
iter_catalog_rows(include_rowid=True). No publication commits the caller's DB.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from collections import OrderedDict

from .market_data_catalog_profiles import catalog_profile, split_catalog_row
from .market_data_projection_profiles import projection_profile
from .market_data_projection_links import _affinity, _quote, _refs
from .market_data_refs import MissingMarketDataConfiguration
from .market_data_scalar_links import _authority, _configured_namespace, _namespace

CONTRACT = "public-catalog-links-v1"
TABLE = "market_catalog"
LAYOUT_TABLE = "_public_catalog_layout"
CONTEXT_TABLE = "_public_catalog_context"
ATTACHMENT = "public_catalog"
CACHE_BYTES = 8 << 20
CACHE_ROWS = 1024
RECORD_CACHE_ROWS = 8192


class MissingCatalogRowError(LookupError):
    """An update cannot silently resurrect a missing catalog condition."""


def _raw(connection, query, parameters=()):
    cursor = sqlite3.Connection.cursor(connection)
    cursor.row_factory = None
    try:
        return cursor.execute(query, parameters).fetchall()
    finally:
        cursor.close()


def _main_info(connection, strategy):
    profile = catalog_profile(strategy)
    info = _raw(connection, "PRAGMA main.table_xinfo(market_catalog)")
    columns = {row[1]: row for row in info}
    if set(columns) != set(profile.column_names) or any(row[6] for row in info):
        raise ValueError("catalog schema differs from its reviewed profile")
    for column in profile.columns:
        row = columns[column.name]
        nullable = {column.nullable}
        if column.legacy_nullable is not None:
            nullable.add(column.legacy_nullable)
        if (
            _affinity(row[2]) != column.affinity
            or (not bool(row[3])) not in nullable
            or bool(row[5]) != (column.name == "condition_id")
        ):
            raise ValueError("catalog affinity/nullability/key differs from profile")
    ddl = _raw(
        connection,
        "SELECT sql FROM main.sqlite_master WHERE type='table' AND name='market_catalog'",
    )[0][0]
    if (
        "WITHOUT ROWID" in ddl.upper()
        or _raw(connection, "PRAGMA main.foreign_key_list(market_catalog)")
        or _raw(
            connection,
            "SELECT 1 FROM main.sqlite_master WHERE type='trigger' AND tbl_name='market_catalog'",
        )
    ):
        raise ValueError("unsupported catalog rowid/trigger/foreign-key layout")
    indexes = []
    for index in _raw(connection, "PRAGMA main.index_list(market_catalog)"):
        details = _raw(connection, "PRAGMA main.index_xinfo(" + _quote(index[1]) + ")")
        keys = tuple(row[2] for row in details if row[5])
        if index[4] or any(
            row[2] is None or row[3] or row[4] != "BINARY" for row in details if row[5]
        ):
            raise ValueError("unsupported catalog partial/expression/collation index")
        if index[2]:
            if index[3] != "pk" or keys != ("condition_id",):
                raise ValueError("unsupported catalog unique constraint")
        else:
            indexes.append(keys)
    return profile, info, tuple(sorted(indexes))


def _retained(profile, info, indexes):
    # Preserve indexed public identifiers locally for selective legacy lookups.
    indexed = {name for keys in indexes for name in keys}
    keep = set(profile.private_columns) | indexed | {"condition_id"}
    return tuple(row[1] for row in info if row[1] in keep)


def _schema(connection, strategy):
    profile, info, indexes = _main_info(connection, strategy)
    retained = _retained(profile, info, indexes)
    clauses = ["_rowid INTEGER PRIMARY KEY"]
    for row in info:
        if row[1] in retained:
            clause = _quote(row[1]) + " " + _affinity(row[2])
            if row[3] or row[1] == "condition_id":
                clause += " NOT NULL"
            if row[1] == "condition_id":
                clause += " UNIQUE"
            clauses.append(clause)
    clauses += [
        f"projection_{i}_id INTEGER NOT NULL CHECK(projection_{i}_id>0)"
        for i in range(len(profile.groups))
    ]
    statements = [
        f"CREATE TABLE {LAYOUT_TABLE}(singleton INTEGER PRIMARY KEY CHECK(singleton=1),contract TEXT NOT NULL,strategy TEXT NOT NULL,namespace TEXT NOT NULL,authority_uuid TEXT NOT NULL)",
        f"CREATE TABLE {CONTEXT_TABLE}(" + ",".join(clauses) + ")",
    ]
    for i, keys in enumerate(indexes):
        statements.append(
            f"CREATE INDEX _public_catalog_source_{i} ON {CONTEXT_TABLE}("
            + ",".join(map(_quote, keys))
            + ")"
        )
    for i in range(len(profile.groups)):
        statements.append(
            f"CREATE INDEX _public_catalog_group_{i} ON {CONTEXT_TABLE}(projection_{i}_id)"
        )
    return tuple(statements)


def catalog_layout_metadata(connection):
    if not _raw(
        connection, "SELECT 1 FROM main.sqlite_master WHERE name=?", (LAYOUT_TABLE,)
    ):
        if _raw(
            connection,
            "SELECT 1 FROM main.sqlite_master WHERE name GLOB '_public_catalog_*'",
        ):
            raise ValueError("catalog layout is incomplete")
        return None
    rows = _raw(
        connection,
        f"SELECT singleton,contract,strategy,namespace,authority_uuid FROM main.{LAYOUT_TABLE}",
    )
    if len(rows) != 1 or rows[0][:2] != (1, CONTRACT):
        raise ValueError("unsupported catalog layout identity")
    _, _, strategy, namespace, authority = rows[0]
    profile = catalog_profile(strategy)
    _namespace(namespace, strategy)
    _authority(authority)
    return dict(
        strategy=strategy,
        namespace=namespace,
        authority_uuid=authority,
        groups=profile.groups,
    )


def validate_catalog_layout(connection):
    metadata = catalog_layout_metadata(connection)
    if metadata is None:
        return frozenset()
    actual = _raw(
        connection,
        "SELECT type,name,tbl_name,sql FROM main.sqlite_master WHERE name GLOB '_public_catalog_*' AND sql IS NOT NULL ORDER BY type,name",
    )
    temporary = sqlite3.connect(":memory:")
    try:
        for statement in _schema(connection, metadata["strategy"]):
            temporary.execute(statement)
        expected = temporary.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
        ).fetchall()
    finally:
        temporary.close()
    if actual != expected:
        raise ValueError("catalog auxiliary schema differs")
    return frozenset(row[1] for row in actual)


def initialize_catalog_links(connection, strategy, namespace, authority_uuid):
    _namespace(namespace, strategy)
    _authority(authority_uuid)
    from .market_data_projection_links import (
        projection_layout_metadata,
        validate_projection_layout,
    )

    if validate_projection_layout(connection):
        sibling = projection_layout_metadata(connection)
        if (sibling["strategy"], sibling["namespace"], sibling["authority_uuid"]) != (
            strategy, namespace, authority_uuid
        ):
            raise ValueError("catalog and snapshot projection ownership differ")
    previous = catalog_layout_metadata(connection)
    if previous is not None:
        validate_catalog_layout(connection)
        if previous != dict(
            strategy=strategy,
            namespace=namespace,
            authority_uuid=authority_uuid,
            groups=catalog_profile(strategy).groups,
        ):
            raise ValueError("catalog ownership changed")
        return
    if not connection.in_transaction:
        raise ValueError("catalog initialization requires caller transaction")
    for statement in _schema(connection, strategy):
        connection.execute(statement)
    connection.execute(
        f"INSERT INTO main.{LAYOUT_TABLE} VALUES(1,?,?,?,?)",
        (CONTRACT, strategy, namespace, authority_uuid),
    )


def catalog_original_rowid(connection, condition_id):
    query = "SELECT rowid FROM main.market_catalog WHERE condition_id=?"
    parameters = (condition_id,)
    if catalog_layout_metadata(connection):
        query += (
            f" UNION ALL SELECT _rowid FROM main.{CONTEXT_TABLE} WHERE condition_id=?"
        )
        parameters += (condition_id,)
    rows = _raw(connection, query, parameters)
    if len(rows) > 1:
        raise ValueError("catalog condition overlaps legacy and shared context")
    return rows[0][0] if rows else None


def iter_catalog_link_rows(connection):
    if not validate_catalog_layout(connection):
        return
    groups = catalog_layout_metadata(connection)["groups"]
    cursor = sqlite3.Connection.cursor(connection)
    cursor.row_factory = None
    try:
        for rowid, condition, *ids in cursor.execute(
            "SELECT _rowid,condition_id,"
            + ",".join(f"projection_{i}_id" for i in range(len(groups)))
            + f" FROM main.{CONTEXT_TABLE} ORDER BY _rowid"
        ):
            if (
                type(rowid) is not int
                or type(condition) is not str
                or any(type(value) is not int or value <= 0 for value in ids)
            ):
                raise ValueError("invalid catalog association identity")
            for kind, record_id in zip(groups, ids, strict=True):
                yield rowid, condition, kind, record_id
    finally:
        cursor.close()


def iter_catalog_record_ids(connection):
    if not validate_catalog_layout(connection):
        return
    groups = catalog_layout_metadata(connection)["groups"]
    query = (
        " UNION ".join(
            f"SELECT DISTINCT projection_{i}_id AS record_id FROM main.{CONTEXT_TABLE}"
            for i in range(len(groups))
        )
        + " ORDER BY record_id"
    )
    cursor = sqlite3.Connection.cursor(connection)
    cursor.row_factory = None
    try:
        for (record_id,) in cursor.execute(query):
            if type(record_id) is not int or record_id <= 0:
                raise ValueError("invalid catalog record identity")
            yield record_id
    finally:
        cursor.close()


def iter_catalog_private_rows(connection, strategy, columns):
    profile, info, indexes = _main_info(connection, strategy)
    retained = _retained(profile, info, indexes)
    columns = tuple(columns)
    if (
        not columns
        or len(set(columns)) != len(columns)
        or not set(columns) <= set(retained) | {"__rowid__"}
    ):
        raise ValueError("catalog private verification requested unowned columns")
    metadata = catalog_layout_metadata(connection)
    if metadata is not None:
        validate_catalog_layout(connection)
        if metadata["strategy"] != strategy:
            raise ValueError("catalog private verification strategy differs")
    query = (
        "SELECT rowid AS ordered_id,"
        + ",".join("rowid" if name == "__rowid__" else _quote(name) for name in columns)
        + " FROM main.market_catalog"
    )
    if metadata is not None:
        query += (
            " UNION ALL SELECT _rowid,"
            + ",".join(
                "_rowid" if name == "__rowid__" else _quote(name) for name in columns
            )
            + f" FROM main.{CONTEXT_TABLE}"
        )
    cursor = sqlite3.Connection.cursor(connection)
    cursor.row_factory = None
    previous = None
    try:
        for rowid, *values in cursor.execute(query + " ORDER BY ordered_id"):
            if type(rowid) is not int or (previous is not None and rowid <= previous):
                raise ValueError("catalog original rowids overlap or changed type")
            previous = rowid
            yield tuple(values)
    finally:
        cursor.close()


def iter_catalog_rows(connection, strategy, *, include_rowid=False):
    """Logical rows in original schema order, optionally preceded by real rowid."""
    _, info, _ = _main_info(connection, strategy)
    metadata = catalog_layout_metadata(connection)
    if metadata is not None and metadata["strategy"] != strategy:
        raise ValueError("catalog logical verification strategy differs")
    if metadata is not None:
        install_catalog_views(connection)
    identity = "catalog_rowid(condition_id)" if metadata else "rowid"
    cursor = connection.execute(
        "SELECT "
        + identity
        + ","
        + ",".join(_quote(row[1]) for row in info)
        + " FROM market_catalog ORDER BY "
        + identity
    )
    previous = None
    try:
        for row in cursor:
            rowid = row[0]
            if type(rowid) is not int or (previous is not None and rowid <= previous):
                raise ValueError("catalog original rowids overlap or changed type")
            previous = rowid
            yield tuple(row) if include_rowid else tuple(row[1:])
    finally:
        cursor.close()


def install_catalog_views(connection, *, references=None):
    if not validate_catalog_layout(connection):
        return
    metadata = catalog_layout_metadata(connection)
    profile, info, indexes = _main_info(connection, metadata["strategy"])
    retained = _retained(profile, info, indexes)
    codec = _refs(connection, references)
    authority = metadata["authority_uuid"]
    path = getattr(codec.reader, "path", None)
    if (
        path is None
        or path.resolve(strict=True) != path
        or codec.reader.scalar_authority_identity() != authority
    ):
        raise MissingMarketDataConfiguration(
            "catalog view needs its canonical authoritative reader"
        )
    databases = dict(
        (row[1], row[2]) for row in _raw(connection, "PRAGMA database_list")
    )
    if databases.get("main") == str(path):
        raise ValueError("public catalog store cannot be the private runtime DB")
    if ATTACHMENT in databases:
        if databases[ATTACHMENT] != str(path):
            raise ValueError("catalog public attachment changed")
    else:
        connection.execute(
            f"ATTACH DATABASE ? AS {ATTACHMENT}", (path.as_uri() + "?mode=ro",)
        )
    binding = (metadata["strategy"], metadata["namespace"], authority, str(path))
    if getattr(connection, "_catalog_view_binding", None) == binding and _raw(
        connection,
        "SELECT 1 FROM temp.sqlite_master WHERE type='view' AND name='market_catalog'",
    ):
        return
    from .market_data_projections import PROJECTION_BLOCK_ROWS
    from .market_data_scalars import scalar_cell_bytes

    records, contexts = OrderedDict(), OrderedDict()
    sizes = {"records": {}, "contexts": {}}
    totals = {"records": 0, "contexts": 0}

    def clear():
        records.clear()
        contexts.clear()
        for section in totals:
            sizes[section].clear()
            totals[section] = 0

    def remember(cache, section, key, value, size):
        if size > CACHE_BYTES:
            return
        if key in cache:
            totals[section] -= sizes[section].pop(key)
            del cache[key]
        maximum = RECORD_CACHE_ROWS if section == "records" else CACHE_ROWS
        while cache and (len(cache) >= maximum or totals[section] + size > CACHE_BYTES):
            removed, _ = cache.popitem(last=False)
            totals[section] -= sizes[section].pop(removed)
        cache[key] = value
        sizes[section][key] = size
        totals[section] += size

    if hasattr(connection, "_catalog_cache_clear"):
        connection._catalog_cache_clear = clear

    def public(record_id, kind):
        use_cache = getattr(connection, "_scalar_cache_enabled", False)
        item = records.get(record_id) if use_cache else None
        if item is not None:
            records.move_to_end(record_id)
        if item is None:
            current = (
                _refs(connection, None) if hasattr(connection, "_references") else codec
            )
            if (
                getattr(current.reader, "path", None) != path
                or current.reader.scalar_authority_identity() != authority
            ):
                raise ValueError("catalog reader path/authority changed")
            ids = [record_id]
            if use_cache:
                pointers = _raw(
                    connection,
                    f"SELECT block_id FROM {ATTACHMENT}.projection_records WHERE id=?",
                    (record_id,),
                )
                if pointers and pointers[0][0] is not None:
                    ids = [
                        row[0]
                        for row in _raw(
                            connection,
                            f"SELECT id FROM {ATTACHMENT}.projection_records WHERE block_id=? ORDER BY ordinal LIMIT ?",
                            (pointers[0][0], PROJECTION_BLOCK_ROWS + 1),
                        )
                    ]
                    if len(ids) > PROJECTION_BLOCK_ROWS or record_id not in ids:
                        raise ValueError("catalog block index exceeds bounded cache")
            found = current.reader.get_projection_records(ids, authority)
            if len(found) != len(ids):
                raise ValueError("catalog record readback is incomplete")
            for wanted, record in zip(ids, found, strict=True):
                if (
                    record.reference.authority_uuid != authority
                    or record.reference.record_id != wanted
                    or record.reference.kind != record.projection.kind
                    or record.reference.sha256 != record.projection.sha256
                ):
                    raise ValueError("catalog public record identity/checksum differs")
                if use_cache:
                    size = (
                        sys.getsizeof(record)
                        + sys.getsizeof(record.projection)
                        + sys.getsizeof(record.reference)
                        + sys.getsizeof(record.projection.row())
                        + sum(sys.getsizeof(value) for value in record.projection.row())
                        + sys.getsizeof(record.projection.__dict__)
                        + sys.getsizeof(record.reference.__dict__)
                        + sum(
                            sys.getsizeof(value)
                            for value in record.reference.__dict__.values()
                        )
                    )
                    remember(records, "records", wanted, record, size)
                if wanted == record_id:
                    item = record
        if item.reference.kind != kind or item.projection.kind != kind:
            raise ValueError("catalog group kind differs from profile")
        return dict(
            zip(projection_profile(kind).columns, item.projection.row(), strict=True)
        )

    def context(rowid):
        use_cache = getattr(connection, "_scalar_cache_enabled", False)
        cached = contexts.get(rowid) if use_cache else None
        if cached is not None:
            contexts.move_to_end(rowid)
            return cached
        names = (*retained, *(f"projection_{i}_id" for i in range(len(profile.groups))))
        rows = _raw(
            connection,
            "SELECT "
            + ",".join(map(_quote, names))
            + f" FROM main.{CONTEXT_TABLE} WHERE _rowid=?",
            (rowid,),
        )
        if len(rows) != 1:
            raise ValueError("catalog private context is missing")
        cached = dict(zip(names, rows[0], strict=True))
        if _raw(
            connection,
            "SELECT 1 FROM main.market_catalog WHERE rowid=? OR condition_id=?",
            (rowid, cached["condition_id"]),
        ):
            raise ValueError("catalog private identity overlaps legacy rows")
        for i, kind in enumerate(profile.groups):
            values = public(cached[f"projection_{i}_id"], kind)
            for name in values.keys() & set(retained):
                if scalar_cell_bytes(cached[name]) != scalar_cell_bytes(values[name]):
                    raise ValueError(
                        "catalog indexed private identity differs from public group"
                    )
            cached.update(values)
        if use_cache:
            remember(
                contexts,
                "contexts",
                rowid,
                cached,
                sys.getsizeof(cached)
                + sum(
                    sys.getsizeof(key) + sys.getsizeof(value)
                    for key, value in cached.items()
                ),
            )
        return cached

    connection.create_function(
        "public_catalog_valid", 1, lambda rowid: int(bool(context(rowid)))
    )
    connection.create_function(
        "public_catalog_cell", 2, lambda rowid, name: context(rowid)[name]
    )
    connection.create_function(
        "catalog_rowid",
        1,
        lambda condition: catalog_original_rowid(connection, condition),
    )
    expressions = []
    for row in info:
        name = row[1]
        value = (
            "c." + _quote(name)
            if name in retained
            else f"CAST(public_catalog_cell(CAST(c._rowid AS INTEGER),'{name}') AS {_affinity(row[2])})"
        )
        expressions.append(value + " AS " + _quote(name))
    connection.execute(
        "CREATE TEMP VIEW IF NOT EXISTS market_catalog AS SELECT "
        + ",".join(_quote(row[1]) for row in info)
        + " FROM main.market_catalog UNION ALL SELECT "
        + ",".join(expressions)
        + f" FROM main.{CONTEXT_TABLE} c WHERE public_catalog_valid(CAST(c._rowid AS INTEGER))=1"
    )
    if hasattr(connection, "_catalog_view_binding"):
        connection._catalog_view_binding = binding


def _next_rowid(connection):
    maximum = _raw(
        connection,
        f"SELECT MAX(value) FROM (SELECT MAX(rowid) value FROM main.market_catalog UNION ALL SELECT MAX(_rowid) FROM main.{CONTEXT_TABLE})",
    )[0][0]
    if maximum == 9223372036854775807:
        for _ in range(100):
            candidate = _raw(connection, "SELECT random() & 9223372036854775807")[0][0]
            if candidate and not _raw(
                connection,
                f"SELECT 1 FROM main.market_catalog WHERE rowid=? UNION ALL SELECT 1 FROM main.{CONTEXT_TABLE} WHERE _rowid=?",
                (candidate, candidate),
            ):
                return candidate
        raise sqlite3.OperationalError("database or disk is full")
    return 1 if maximum is None else maximum + 1


def upsert_shared_catalog(
    connection,
    strategy,
    values,
    *,
    references=None,
    namespace=None,
    insert_only=False,
    original_rowid=None,
):
    """Publish a complete insert/upsert row; preserve an existing row's real rowid.

    SQLAlchemy Python defaults are the caller's responsibility. SQLite affinity,
    CHECK/NOT NULL/default behavior is evaluated by the original main table.
    Dirty ORM objects must use update_shared_catalog with only changed columns.
    """
    codec = _refs(connection, references)
    metadata = catalog_layout_metadata(connection)
    if codec.writer is None:
        if metadata is not None or codec.reader is not None:
            raise MissingMarketDataConfiguration(
                "catalog publication requires public writer"
            )
        return None
    profile, info, indexes = _main_info(connection, strategy)
    if set(values) - set(profile.column_names) or "condition_id" not in values:
        raise ValueError("catalog publication contains absent/unknown source fields")
    explicit_namespace = namespace is not None
    namespace = namespace or (
        metadata["namespace"]
        if metadata
        else _configured_namespace(connection, strategy)
    )
    _namespace(namespace, strategy)
    if (
        metadata is not None
        and not explicit_namespace
        and (os.environ.get("PUBLIC_MARKET_DATA_SOURCE") or os.environ.get("JOB_NAME"))
        and _configured_namespace(connection, strategy) != namespace
    ):
        raise ValueError("catalog source/job/runtime differs from bound namespace")
    if codec.reader is None:
        raise MissingMarketDataConfiguration(
            "catalog publication requires independent reader"
        )
    authority = codec.reader.scalar_authority_identity()
    if codec.writer.scalar_authority_identity() != authority:
        raise ValueError("catalog writer and reader authorities differ")
    own_transaction = not connection.in_transaction
    if own_transaction:
        connection.execute("BEGIN")
    savepoint = "catalog_publish_" + uuid.uuid4().hex
    connection.execute("SAVEPOINT " + savepoint)
    try:
        initialize_catalog_links(connection, strategy, namespace, authority)
        condition = values["condition_id"]
        existing = catalog_original_rowid(connection, condition)
        if insert_only and existing is not None:
            raise sqlite3.IntegrityError("catalog condition already exists")
        if original_rowid is not None and (
            type(original_rowid) is not int
            or not -(1 << 63) <= original_rowid < (1 << 63)
        ):
            raise ValueError("catalog rowid must be an exact SQLite integer")
        if (
            existing is not None
            and original_rowid is not None
            and original_rowid != existing
        ):
            raise ValueError("catalog original rowid changed")
        rowid = (
            existing
            if existing is not None
            else original_rowid
            if original_rowid is not None
            else _next_rowid(connection)
        )
        if existing is None and _raw(
            connection,
            f"SELECT 1 FROM main.market_catalog WHERE rowid=? UNION ALL SELECT 1 FROM main.{CONTEXT_TABLE} WHERE _rowid=?",
            (rowid, rowid),
        ):
            raise sqlite3.IntegrityError("catalog original rowid already exists")
        connection.execute("SAVEPOINT catalog_affinity")
        try:
            connection.execute(
                "DELETE FROM main.market_catalog WHERE rowid=?", (rowid,)
            )
            names = tuple(name for name in profile.column_names if name in values)
            connection.execute(
                "INSERT INTO main.market_catalog(rowid,"
                + ",".join(map(_quote, names))
                + ") VALUES(?,"
                + ",".join("?" for _ in names)
                + ")",
                (rowid, *(values[name] for name in names)),
            )
            selected = _raw(
                connection,
                "SELECT "
                + ",".join(map(_quote, profile.column_names))
                + " FROM main.market_catalog WHERE rowid=?",
                (rowid,),
            )[0]
            normalized = dict(zip(profile.column_names, selected, strict=True))
        finally:
            connection.execute("ROLLBACK TO catalog_affinity")
            connection.execute("RELEASE catalog_affinity")
        # Decode only body references crossing into this public group. All other
        # context cells retain their stored private bytes and exact SQLite types.
        from .market_data_policy import public_columns

        logical = dict(normalized)
        for name in public_columns(strategy, TABLE) & profile.public_columns:
            logical[name] = codec.decode_many([logical[name]])[0]
        split = split_catalog_row(strategy, logical)
        from .market_data_projections import PublicProjection, ProjectionReceipt

        projections = [
            PublicProjection(group.kind, group.values) for group in split.groups
        ]
        acknowledgements = codec.writer.put_public_projections(projections)
        if len(acknowledgements) != len(projections) or any(
            ref.authority_uuid != authority
            or ref.kind != value.kind
            or ref.sha256 != value.sha256
            for ref, value in zip(acknowledgements, projections, strict=True)
        ):
            raise ValueError("catalog public acknowledgement differs")
        receipts = [
            ProjectionReceipt(namespace, TABLE, rowid, ref.kind, ref.record_id)
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
            raise ValueError(
                "catalog public record/receipt independent readback differs"
            )
        retained = _retained(profile, info, indexes)
        target_names = (
            "_rowid",
            *retained,
            *(f"projection_{i}_id" for i in range(len(profile.groups))),
        )
        target_values = (
            rowid,
            *(logical[name] for name in retained),
            *(ref.record_id for ref in acknowledgements),
        )
        connection.execute(
            f"INSERT INTO main.{CONTEXT_TABLE}("
            + ",".join(map(_quote, target_names))
            + ") VALUES("
            + ",".join("?" for _ in target_names)
            + ") ON CONFLICT(_rowid) DO UPDATE SET "
            + ",".join(
                _quote(name) + "=excluded." + _quote(name) for name in target_names[1:]
            ),
            target_values,
        )
        connection.execute("DELETE FROM main.market_catalog WHERE rowid=?", (rowid,))
        install_catalog_views(connection, references=codec)
        connection.execute("RELEASE " + savepoint)
        return logical
    except BaseException:
        connection.execute("ROLLBACK TO " + savepoint)
        connection.execute("RELEASE " + savepoint)
        if own_transaction:
            connection.rollback()
        raise


def update_shared_catalog(
    connection, strategy, condition_id, changes, *, references=None, namespace=None
):
    """Merge only dirty attributes into the current row, without resurrection."""
    profile = catalog_profile(strategy)
    if "condition_id" in changes or not set(changes) <= set(profile.column_names):
        raise ValueError(
            "catalog updates cannot change the condition key or unknown fields"
        )
    codec = _refs(connection, references)
    metadata = catalog_layout_metadata(connection)
    if codec.writer is None and codec.reader is None and metadata is None:
        return None
    own_transaction = not connection.in_transaction
    if own_transaction:
        connection.execute("BEGIN")
    savepoint = "catalog_update_" + uuid.uuid4().hex
    connection.execute("SAVEPOINT " + savepoint)
    try:
        if metadata is not None:
            install_catalog_views(connection, references=codec)
        rows = _raw(
            connection,
            "SELECT "
            + ",".join(map(_quote, profile.column_names))
            + " FROM market_catalog WHERE condition_id=?",
            (condition_id,),
        )
        if not rows:
            raise MissingCatalogRowError("catalog condition no longer exists")
        if len(rows) != 1:
            raise ValueError("catalog condition has conflicting private identities")
        values = dict(zip(profile.column_names, rows[0], strict=True))
        values.update(changes)
        result = upsert_shared_catalog(
            connection, strategy, values, references=codec, namespace=namespace
        )
        connection.execute("RELEASE " + savepoint)
        return result
    except BaseException:
        connection.execute("ROLLBACK TO " + savepoint)
        connection.execute("RELEASE " + savepoint)
        if own_transaction:
            connection.rollback()
        raise
