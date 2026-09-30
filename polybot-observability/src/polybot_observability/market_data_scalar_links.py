"""Public scalar values with transactional, runtime-owned local snapshot IDs."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections import OrderedDict
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from .market_data_refs import MissingMarketDataConfiguration, configured_references

CONTRACT = "public-scalar-links-v1"
LAYOUT_TABLE = "_public_scalar_layout"
LINK_TABLE = "_public_scalar_links"
ATTACHMENT = "public_scalar"
PENDING_TABLE = "_public_scalar_pending"
MAX_PENDING = 10000
RECORD_CACHE_SIZE = 1024
COLUMNS = ("id", "condition_id", "probability", "liquidity", "volume_24h", "timestamp")
STRATEGIES = frozenset((
    "golden-apple", "golden-banana", "golden-cherry", "golden-date", "golden-elderberry",
    "golden-fig", "golden-grape", "golden-lime", "golden-mango", "golden-orange",
))
SCHEMA = (
    f"CREATE TABLE IF NOT EXISTS {LAYOUT_TABLE}(singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
    "contract TEXT NOT NULL,strategy TEXT NOT NULL,namespace TEXT NOT NULL,"
    "authority_uuid TEXT NOT NULL)",
    f"CREATE TABLE IF NOT EXISTS {LINK_TABLE}(id INTEGER PRIMARY KEY,"
    "record_id INTEGER NOT NULL CHECK(record_id>0))",
    f"CREATE INDEX IF NOT EXISTS _public_scalar_links_record ON {LINK_TABLE}(record_id)",
)


def scalar_namespace(source: str, jenkins_job: str, strategy: str, runtime: str) -> str:
    values = {
        "source": source,
        "jenkins_job": jenkins_job,
        "strategy": strategy,
        "runtime": runtime,
    }
    if any(
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(ord(char) < 32 for char in value)
        for value in values.values()
    ):
        raise ValueError("scalar snapshots require explicit bounded source/job/strategy/runtime")
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode()) > 1024:
        raise ValueError("scalar snapshot namespace exceeds 1024 bytes")
    return encoded


def _namespace(value, strategy):
    try:
        fields = json.loads(value)
        if set(fields) != {"source", "jenkins_job", "strategy", "runtime"}:
            raise ValueError
        canonical = scalar_namespace(**fields)
    except (TypeError, ValueError, KeyError):
        raise ValueError("invalid scalar snapshot namespace") from None
    if canonical != value or fields["strategy"] != strategy:
        raise ValueError("scalar snapshot namespace/strategy mismatch")
    return value


@lru_cache(maxsize=1)
def _expected_layout():
    with closing(sqlite3.connect(":memory:")) as connection:
        for statement in SCHEMA:
            connection.execute(statement)
        return tuple(
            connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE sql IS NOT NULL ORDER BY type,name"
            )
        )


def validate_scalar_layout(connection):
    rows = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM main.sqlite_master "
            "WHERE name GLOB '_public_scalar_*' AND sql IS NOT NULL ORDER BY type,name"
        )
    )
    if rows and rows != _expected_layout():
        raise ValueError("public scalar auxiliary schema mismatch")
    if rows:
        metadata = connection.execute(
            f"SELECT singleton,contract,strategy,namespace,authority_uuid FROM main.{LAYOUT_TABLE}"
        ).fetchall()
        if (
            len(metadata) != 1
            or tuple(metadata[0])[:2] != (1, CONTRACT)
            or metadata[0][2] not in STRATEGIES
        ):
            raise ValueError("public scalar layout identity mismatch")
        _namespace(metadata[0][3], metadata[0][2])
        _authority(metadata[0][4])
        _main_schema(connection)
    return frozenset(row[1] for row in rows)


def _main_schema(connection):
    info = connection.execute("PRAGMA main.table_info(market_snapshots)").fetchall()
    if (
        tuple(row[1] for row in info) != COLUMNS
        or info[0][2].upper() != "INTEGER"
        or info[0][5] != 1
        or any(row[5] for row in info[1:])
    ):
        raise ValueError("public scalar adapter requires the exact six-column snapshot schema")

    def affinity(declaration):
        declaration = declaration.upper()
        if "INT" in declaration:
            return "INTEGER"
        if any(token in declaration for token in ("CHAR", "CLOB", "TEXT")):
            return "TEXT"
        if "BLOB" in declaration or not declaration:
            return "BLOB"
        if any(token in declaration for token in ("REAL", "FLOA", "DOUB")):
            return "REAL"
        return "NUMERIC"

    if tuple(affinity(row[2]) for row in info) != (
        "INTEGER",
        "TEXT",
        "REAL",
        "REAL",
        "REAL",
        "NUMERIC",
    ):
        raise ValueError("public scalar adapter requires the original DATETIME/REAL affinities")
    ddl = connection.execute(
        "SELECT sql FROM main.sqlite_master WHERE type='table' AND name='market_snapshots'"
    ).fetchone()[0]
    if (
        "WITHOUT ROWID" in ddl.upper()
        or connection.execute(
            "SELECT 1 FROM main.sqlite_master WHERE type='trigger' AND tbl_name='market_snapshots'"
        ).fetchone()
    ):
        raise ValueError("public scalar snapshot triggers/rowid layout are unsupported")
    if any(row[2] for row in connection.execute("PRAGMA main.index_list(market_snapshots)")):
        raise ValueError("public scalar snapshots cannot bypass an original unique constraint")
    return ddl


def _metadata(connection):
    if not validate_scalar_layout(connection):
        return None
    return tuple(
        connection.execute(
            f"SELECT strategy,namespace,authority_uuid FROM main.{LAYOUT_TABLE}"
        ).fetchone()
    )


def scalar_layout_metadata(connection):
    value = _metadata(connection)
    return (
        dict(zip(("strategy", "namespace", "authority_uuid"), value, strict=True))
        if value
        else None
    )


def _authority(value):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("scalar layout requires its explicit authoritative store UUID")
    return value


def initialize_scalar_links(connection, strategy: str, namespace: str, authority_uuid: str):
    if strategy not in STRATEGIES:
        raise ValueError("unsupported scalar snapshot strategy")
    namespace = _namespace(namespace, strategy)
    authority_uuid = _authority(authority_uuid)
    _main_schema(connection)
    existing = _metadata(connection)
    if existing is not None:
        if existing != (strategy, namespace, authority_uuid):
            raise ValueError("public scalar runtime ownership changed")
        return
    if not connection.in_transaction:
        raise ValueError("scalar layout initialization requires a caller transaction")
    for statement in SCHEMA:
        connection.execute(statement)
    connection.execute(
        f"INSERT INTO main.{LAYOUT_TABLE} VALUES(1,?,?,?,?)",
        (CONTRACT, strategy, namespace, authority_uuid),
    )


def _references(connection, references):
    return references or getattr(connection, "_references", None) or configured_references()


def install_scalar_views(connection, *, references=None):
    metadata = _metadata(connection)
    if metadata is None:
        return
    refs = _references(connection, references)
    authority_uuid = metadata[2]
    if refs.reader is None or refs.reader.scalar_authority_identity() != authority_uuid:
        raise ValueError("public scalar store authority differs from private layout")
    path = getattr(refs.reader, "path", None)
    if not isinstance(path, Path) or path.resolve(strict=True) != path or not path.is_file():
        raise MissingMarketDataConfiguration(
            "scalar views require the canonical public reader database"
        )
    databases = {row[1]: row[2] for row in connection.execute("PRAGMA database_list")}
    if databases.get("main") and Path(databases["main"]).resolve() == path:
        raise ValueError("public scalar store cannot be the private runtime database")
    if ATTACHMENT in databases:
        if databases[ATTACHMENT] != str(path):
            raise ValueError("public scalar attachment identity changed")
    else:
        connection.execute(f"ATTACH DATABASE ? AS {ATTACHMENT}", (path.as_uri() + "?mode=ro",))
    binding = (metadata, str(path))
    if (
        getattr(connection, "_scalar_view_binding", None) == binding
        and connection.execute(
            "SELECT 1 FROM temp.sqlite_master WHERE type='view' AND name='market_snapshots'"
        ).fetchone()
    ):
        return
    from .market_data_scalars import scalar_cell_bytes

    cache = OrderedDict()
    if hasattr(connection, "_scalar_cache_clear"):
        connection._scalar_cache_clear = cache.clear

    def checked(record_id):
        if type(record_id) is not int or record_id <= 0:
            raise ValueError("missing public scalar record identity")
        use_cache = getattr(connection, "_scalar_cache_enabled", False)
        record = cache.get(record_id) if use_cache else None
        if record is not None:
            return record.snapshot
        current = _references(connection, None) if hasattr(connection, "_references") else refs
        if (
            getattr(current.reader, "path", None) != path
            or current.reader.scalar_authority_identity() != authority_uuid
        ):
            raise ValueError("public scalar reader authority/path changed")
        # Read indexed metadata by immutable ID rather than accepting predicate
        # columns as UDF arguments: SQLite can substitute a WHERE-bound value
        # before evaluating the row's other predicates.
        cursor = sqlite3.Connection.cursor(connection)
        cursor.row_factory = None
        try:
            query = (
                f"SELECT s.id,c.condition_id,s.timestamp,s.block_id "
                f"FROM {ATTACHMENT}.scalar_snapshots AS s "
                f"LEFT JOIN {ATTACHMENT}.scalar_conditions AS c ON c.id=s.condition_key "
            )
            selected = cursor.execute(query + "WHERE s.id=?", (record_id,)).fetchone()
            if selected is None:
                selected = cursor.execute(
                    f"SELECT record_id,condition_id,timestamp,NULL FROM temp.{PENDING_TABLE} "
                    "WHERE record_id=?",
                    (record_id,),
                ).fetchone()
            if selected is None:
                raise ValueError("public scalar record is missing from the selected SQL index")
            expected = [selected]
            if use_cache and selected[3] is not None:
                # The common reader proves complete blocks. Reuse that one
                # bounded proof during this statement instead of rehashing a
                # 1024-row block separately for every row of a maintenance scan.
                expected = cursor.execute(
                    query + "WHERE s.block_id=? ORDER BY s.ordinal LIMIT ?",
                    (selected[3], RECORD_CACHE_SIZE + 1),
                ).fetchall()
                if len(expected) > RECORD_CACHE_SIZE or not any(
                    row[0] == record_id for row in expected
                ):
                    raise ValueError("public scalar block index exceeds the bounded row cache")
        finally:
            cursor.close()
        records = current.reader.get_scalar_records([row[0] for row in expected], authority_uuid)
        if len(records) != len(expected):
            raise ValueError("public scalar record readback is incomplete")
        result = None
        for expected_row, record in zip(expected, records, strict=True):
            if (
                record.reference.authority_uuid != authority_uuid
                or record.reference.record_id != expected_row[0]
                or record.reference.sha256 != record.snapshot.sha256
            ):
                raise ValueError("public scalar record checksum/authority mismatch")
            if expected_row[1] != record.snapshot.condition_id or scalar_cell_bytes(
                expected_row[2]
            ) != scalar_cell_bytes(record.snapshot.timestamp):
                raise ValueError("public scalar index metadata differs from verified record")
            if use_cache:
                cache[record.reference.record_id] = record
                while len(cache) > RECORD_CACHE_SIZE:
                    cache.popitem(last=False)
            if record.reference.record_id == record_id:
                result = record.snapshot
        if result is None:
            raise ValueError("public scalar requested record is missing from block proof")
        return result

    def verified(record_id):
        checked(record_id)
        return 1

    def numeric(record_id, ordinal):
        snapshot = checked(record_id)
        return (snapshot.probability, snapshot.liquidity, snapshot.volume_24h)[ordinal]

    connection.create_function("public_scalar_verified", 1, verified)
    connection.create_function("public_scalar_numeric", 2, numeric)
    connection.execute(
        f"CREATE TEMP TABLE IF NOT EXISTS {PENDING_TABLE} ("
        "record_id INTEGER PRIMARY KEY,condition_id TEXT NOT NULL,timestamp NUMERIC)"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS temp._public_scalar_pending_condition_time "
        f"ON {PENDING_TABLE}(condition_id,timestamp,record_id)"
    )
    connection.execute(f"""
        CREATE TEMP VIEW IF NOT EXISTS market_snapshots AS
        SELECT id,condition_id,probability,liquidity,volume_24h,timestamp
          FROM main.market_snapshots
        UNION ALL
        SELECT l.id,c.condition_id,
               CAST(public_scalar_numeric(l.record_id,0) AS REAL),
               CAST(public_scalar_numeric(l.record_id,1) AS REAL),
               CAST(public_scalar_numeric(l.record_id,2) AS REAL),
               s.timestamp_numeric
          FROM main.{LINK_TABLE} AS l
          LEFT JOIN {ATTACHMENT}.scalar_snapshots AS s ON s.id=l.record_id
          LEFT JOIN {ATTACHMENT}.scalar_conditions AS c ON c.id=s.condition_key
         WHERE (s.id IS NOT NULL OR NOT EXISTS (
                   SELECT 1 FROM temp.{PENDING_TABLE} AS p WHERE p.record_id=l.record_id))
           AND public_scalar_verified(l.record_id)=1
        UNION ALL
        SELECT l.id,p.condition_id,
               CAST(public_scalar_numeric(p.record_id,0) AS REAL),
               CAST(public_scalar_numeric(p.record_id,1) AS REAL),
               CAST(public_scalar_numeric(p.record_id,2) AS REAL),
               p.timestamp
          FROM main.{LINK_TABLE} AS l JOIN temp.{PENDING_TABLE} AS p ON p.record_id=l.record_id
         WHERE NOT EXISTS (SELECT 1 FROM {ATTACHMENT}.scalar_snapshots AS s WHERE s.id=l.record_id)
           AND public_scalar_verified(p.record_id)=1
    """)
    if hasattr(connection, "_scalar_view_binding"):
        connection._scalar_view_binding = binding


def _configured_namespace(connection, strategy):
    databases = {row[1]: row[2] for row in connection.execute("PRAGMA database_list")}
    path = Path(databases.get("main") or "")
    if (
        not path.is_absolute()
        or path.resolve() != path
        or path.parent.parent.name != "data"
        or path.name not in {"trades.db", "trades_sim.db"}
        or path.parent.name in {"", "."}
    ):
        raise ValueError("scalar publication requires an explicit runtime namespace")
    return scalar_namespace(
        os.environ.get("PUBLIC_MARKET_DATA_SOURCE"),
        os.environ.get("JOB_NAME"),
        strategy,
        path.parent.name,
    )


def _next_id(connection, ddl):
    maximum = connection.execute(
        f"SELECT MAX(id) FROM (SELECT MAX(id) AS id FROM main.market_snapshots "
        f"UNION ALL SELECT MAX(id) FROM main.{LINK_TABLE})"
    ).fetchone()[0]
    if "AUTOINCREMENT" in ddl.upper():
        sequence = connection.execute(
            "SELECT seq FROM main.sqlite_sequence WHERE name='market_snapshots'"
        ).fetchone()
        maximum = max(maximum or 0, sequence[0] if sequence else 0)
    if maximum == 9223372036854775807:
        if "AUTOINCREMENT" not in ddl.upper():
            for _ in range(100):
                candidate = connection.execute("SELECT random() & 9223372036854775807").fetchone()[
                    0
                ]
                if (
                    candidate
                    and not connection.execute(
                        "SELECT 1 FROM main.market_snapshots WHERE id=? UNION ALL "
                        f"SELECT 1 FROM main.{LINK_TABLE} WHERE id=?",
                        (candidate, candidate),
                    ).fetchone()
                ):
                    return candidate
        raise sqlite3.OperationalError("database or disk is full")
    return 1 if maximum is None else maximum + 1


def insert_shared_snapshot(
    connection, strategy: str, values: dict, *, namespace=None, references=None
):
    """ACK values and provenance before publishing a local ID; never commit the caller."""
    refs = _references(connection, references)
    metadata = _metadata(connection)
    if refs.writer is None:
        if (
            metadata
            or refs.reader is not None
            or os.environ.get("PUBLIC_MARKET_DATA_REQUIRED") == "1"
        ):
            raise MissingMarketDataConfiguration("shared scalar writes require the public writer")
        return None
    if set(values) - set(COLUMNS) or not {"condition_id", "probability"} <= set(values):
        raise ValueError("scalar snapshot insert contains unknown or missing columns")
    if metadata is not None:
        if metadata[0] != strategy or (namespace is not None and metadata[1] != namespace):
            raise ValueError("scalar snapshot runtime identity changed")
        namespace = metadata[1]
        if os.environ.get("PUBLIC_MARKET_DATA_SOURCE") or os.environ.get("JOB_NAME"):
            if _configured_namespace(connection, strategy) != namespace:
                raise ValueError("scalar source/job/runtime differs from the bound namespace")
    else:
        namespace = namespace or _configured_namespace(connection, strategy)
    authority_uuid = _authority(refs.reader.scalar_authority_identity())
    if refs.writer.scalar_authority_identity() != authority_uuid:
        raise ValueError("public scalar writer and reader authorities differ")
    if metadata is not None and metadata[2] != authority_uuid:
        raise ValueError("public scalar writer authority differs from private layout")
    own_transaction = not connection.in_transaction
    if own_transaction:
        connection.execute("BEGIN")
    savepoint = "public_scalar_" + uuid.uuid4().hex
    connection.execute("SAVEPOINT " + savepoint)
    try:
        initialize_scalar_links(connection, strategy, namespace, authority_uuid)
        install_scalar_views(connection, references=refs)
        connection.execute(
            f"DELETE FROM temp.{PENDING_TABLE} WHERE EXISTS "
            f"(SELECT 1 FROM {ATTACHMENT}.scalar_snapshots AS s "
            f"WHERE s.id={PENDING_TABLE}.record_id) OR NOT EXISTS "
            f"(SELECT 1 FROM main.{LINK_TABLE} AS l "
            f"WHERE l.record_id={PENDING_TABLE}.record_id)"
        )
        if (
            connection.execute(f"SELECT COUNT(*) FROM temp.{PENDING_TABLE}").fetchone()[0]
            >= MAX_PENDING
        ):
            raise ValueError("shared scalar transaction pending overlay limit exceeded")
        ddl = _main_schema(connection)
        row = dict(values)
        row["id"] = row.get("id") if row.get("id") is not None else _next_id(connection, ddl)
        if connection.execute(
            f"SELECT 1 FROM main.{LINK_TABLE} WHERE id=?", (row["id"],)
        ).fetchone():
            raise sqlite3.IntegrityError("snapshot id already belongs to a shared row")
        names = tuple(name for name in COLUMNS if name in row)
        connection.execute("SAVEPOINT public_scalar_affinity")
        try:
            connection.execute(
                "INSERT INTO main.market_snapshots ("
                + ",".join(names)
                + ") VALUES ("
                + ",".join("?" for _ in names)
                + ")",
                tuple(row[name] for name in names),
            )
            normalized = tuple(
                connection.execute(
                    "SELECT " + ",".join(COLUMNS) + " FROM main.market_snapshots WHERE id=?",
                    (row["id"],),
                ).fetchone()
            )
        finally:
            connection.execute("ROLLBACK TO public_scalar_affinity")
            connection.execute("RELEASE public_scalar_affinity")
        from .market_data_scalars import ScalarReceipt, ScalarSnapshot

        snapshot = ScalarSnapshot(*normalized[1:])
        acknowledged = refs.writer.put_scalar_snapshots([snapshot])
        if (
            len(acknowledged) != 1
            or acknowledged[0].authority_uuid != authority_uuid
            or acknowledged[0].sha256 != snapshot.sha256
        ):
            raise ValueError("public scalar snapshot acknowledgement mismatch")
        reference = acknowledged[0]
        receipt = ScalarReceipt(namespace, normalized[0], reference.record_id)
        refs.writer.append_scalar_receipts([receipt], authority_uuid=authority_uuid)
        readback = refs.reader.get_scalar_records([reference.record_id], authority_uuid)
        if (
            len(readback) != 1
            or readback[0].reference != reference
            or readback[0].snapshot != snapshot
            or refs.reader.get_scalar_receipts(
                [(namespace, normalized[0], reference.record_id)],
                authority_uuid=authority_uuid,
            )
            != [receipt]
        ):
            raise ValueError("public scalar snapshot/receipt readback mismatch")
        connection.execute(
            f"INSERT INTO main.{LINK_TABLE} VALUES(?,?)", (normalized[0], reference.record_id)
        )
        connection.execute(
            f"INSERT OR IGNORE INTO temp.{PENDING_TABLE} VALUES(?,?,?)",
            (reference.record_id, snapshot.condition_id, snapshot.timestamp),
        )
        if "AUTOINCREMENT" in ddl.upper():
            cursor = connection.execute(
                "UPDATE main.sqlite_sequence SET seq=MAX(seq,?) WHERE name='market_snapshots'",
                (normalized[0],),
            )
            if not cursor.rowcount:
                connection.execute(
                    "INSERT INTO main.sqlite_sequence VALUES('market_snapshots',?)",
                    (max(normalized[0], 0),),
                )
        connection.execute("RELEASE " + savepoint)
        return dict(zip(COLUMNS, normalized, strict=True))
    except BaseException:
        connection.execute("ROLLBACK TO " + savepoint)
        connection.execute("RELEASE " + savepoint)
        if own_transaction:
            connection.rollback()
        raise


def delete_snapshot_rows(connection, where_sql: str, parameters=()):
    """Delete runtime memberships and legacy rows, never public values or receipts."""
    if not validate_scalar_layout(connection):
        return connection.execute(
            "DELETE FROM main.market_snapshots WHERE " + where_sql, parameters
        ).rowcount
    install_scalar_views(connection)
    temporary = "scalar_delete_" + uuid.uuid4().hex
    connection.execute(f"CREATE TEMP TABLE {temporary}(id INTEGER PRIMARY KEY)")
    try:
        connection.execute(
            f"INSERT INTO {temporary} SELECT id FROM market_snapshots WHERE " + where_sql,
            parameters,
        )
        count = connection.execute(f"SELECT COUNT(*) FROM {temporary}").fetchone()[0]
        connection.execute(
            f"DELETE FROM main.{LINK_TABLE} WHERE id IN (SELECT id FROM {temporary})"
        )
        connection.execute(
            f"DELETE FROM main.market_snapshots WHERE id IN (SELECT id FROM {temporary})"
        )
        return count
    finally:
        connection.execute(f"DROP TABLE {temporary}")


def save_scalar_snapshot(session, model, strategy: str, values: dict, *, commit=True):
    """Use the model's SQLite bind processors, retaining its ORM return shape."""
    connection = session.connection().connection.driver_connection
    refs = _references(connection, None)
    if refs.writer is None and _metadata(connection) is None and refs.reader is None:
        return None
    session.flush()
    dialect = session.get_bind().dialect
    bound = {}
    for name, value in values.items():
        column = model.__table__.columns[name]
        processor = column.type.dialect_impl(dialect).bind_processor(dialect)
        bound[name] = processor(value) if processor else value
    saved = insert_shared_snapshot(connection, strategy, bound, references=refs)
    if saved is None:
        return None
    if commit:
        session.commit()
    return session.get(model, saved["id"])


def cleanup_scalar_snapshots(session, model, cutoff):
    connection = session.connection().connection.driver_connection
    if _metadata(connection) is None:
        return None
    session.flush()
    dialect = session.get_bind().dialect
    processor = model.__table__.columns.timestamp.type.dialect_impl(dialect).bind_processor(dialect)
    deleted = delete_snapshot_rows(
        connection, "timestamp < ?", (processor(cutoff) if processor else cutoff,)
    )
    session.commit()
    session.expire_all()
    return deleted
