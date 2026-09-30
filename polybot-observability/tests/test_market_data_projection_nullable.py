"""Physical v2 stores real absent/nullable subjects without changing logical v1 cells."""

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import MappingProxyType

import pytest

import polybot_observability.market_data_projection_profiles as profiles
from polybot_observability.market_data_projection_profiles import (
    ProjectionField,
    ProjectionProfile,
)
from polybot_observability.market_data_projections import (
    CorruptProjectionError,
    PROJECTION_STORAGE_CONTRACT,
    PROJECTION_TABLE_SQL,
    PROJECTION_INDEX_SQL,
    PROJECTION_TRIGGER_SQL,
    PublicProjection,
    UnsupportedProjectionStorageError,
    projection_schema_sha,
)
from polybot_observability.market_data_scalars import (
    ScalarSnapshot,
    ScalarAuthorityError,
)
from polybot_observability.market_data_store import (
    PayloadReader,
    PayloadStore,
    StoreLimitError,
)


@pytest.fixture(autouse=True)
def nullable_kinds(monkeypatch):
    registry = dict(profiles.PROJECTION_PROFILES)
    registry.update(
        {
            "test-null-condition-v1": ProjectionProfile(
                "test-null-condition-v1",
                (
                    ProjectionField("condition_id", "TEXT"),
                    ProjectionField("price", "REAL", False),
                    ProjectionField("source_updated_at", "TEXT"),
                ),
                source_time_field="source_updated_at",
            ),
            "test-token-only-v1": ProjectionProfile(
                "test-token-only-v1",
                (
                    ProjectionField("token_id", "TEXT"),
                    ProjectionField("price", "REAL", False),
                    ProjectionField("source_updated_at", "TEXT"),
                ),
                condition_field=None,
                token_field="token_id",
                source_time_field="source_updated_at",
            ),
            "test-unindexed-v1": ProjectionProfile(
                "test-unindexed-v1",
                (ProjectionField("value", "REAL", False),),
                condition_field=None,
            ),
        }
    )
    monkeypatch.setattr(profiles, "PROJECTION_PROFILES", MappingProxyType(registry))
    projection_schema_sha.cache_clear()
    yield
    projection_schema_sha.cache_clear()


def value(kind="test-token-only-v1", subject=None, number=0.5, clock=None):
    cells = (number,) if kind == "test-unindexed-v1" else (subject, number, clock)
    return PublicProjection(kind, cells)


def read(store, references):
    return store.get_projection_records(
        [ref.record_id for ref in references], references[0].authority_uuid
    )


def test_absent_condition_nullable_condition_and_empty_unknown_subject_are_distinct(
    tmp_path,
):
    values = [
        value(),
        value(subject=""),
        value(subject="token"),
        value("test-null-condition-v1"),
        value("test-null-condition-v1", subject=""),
        value("test-null-condition-v1", subject="condition"),
        value("test-unindexed-v1"),
    ]
    assert len({row.sha256 for row in values}) == len(values)
    with PayloadStore(tmp_path / "public.db") as store:
        refs = store.put_public_projections(values)
        assert [row.projection for row in read(store, refs)] == values
        assert store.put_public_projections(values) == refs
        rows = store._connection.execute(
            "SELECT condition_key,token_key,source_time FROM projection_records ORDER BY id"
        ).fetchall()
        assert rows[0] == (None, None, None) and rows[3] == (None, None, None)
        assert rows[2][0] is None and rows[2][1] is not None
        assert store._connection.execute(
            "SELECT condition_id FROM scalar_conditions ORDER BY condition_id"
        ).fetchall() == [("",), ("condition",)]
        assert store._connection.execute(
            "SELECT count(*) FROM projection_unkeyed"
        ).fetchone() == (3,)
        assert store._connection.execute(
            "SELECT * FROM projection_storage"
        ).fetchall() == [(1, PROJECTION_STORAGE_CONTRACT)]
        for item in values:
            assert (
                PublicProjection.from_wire(json.loads(json.dumps(item.to_wire())))
                == item
            )
    with pytest.raises(ValueError, match="required public"):
        PublicProjection(
            "gamma-quote-v1", (None, 0.5, None, None, None, None, None, None)
        )
    with pytest.raises(ValueError, match="subjects must be exact TEXT"):
        ProjectionProfile("invalid", (ProjectionField("condition_id", "INTEGER"),))


def test_token_only_query_and_dedup_use_real_token_index(tmp_path):
    with PayloadStore(tmp_path / "public.db") as store:
        values = [
            value(subject="a", clock="2026-01-01"),
            value(subject="a", clock=None),
            value(subject="b", clock="2026-01-02"),
        ]
        refs = store.put_public_projections(values)
        assert store.put_public_projections(values) == refs
        result = list(
            store.query_projection_records(
                authority_uuid=refs[0].authority_uuid,
                token_id="a",
                start="2026",
                end="2027",
            )
        )
        assert [record.projection for record in result] == values[:1]
        assert (
            list(
                store.query_projection_records(
                    authority_uuid=refs[0].authority_uuid, condition_id="a"
                )
            )
            == []
        )
        queries = []
        store._connection.set_trace_callback(queries.append)
        assert store.put_public_projections([values[0]]) == refs[:1]
        store._connection.set_trace_callback(None)
        assert any(
            "INDEXED BY projection_records_token_time" in query for query in queries
        )
        assert store._connection.execute(
            "SELECT count(*) FROM projection_unkeyed"
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    "kind", ["test-unindexed-v1", "test-token-only-v1", "test-null-condition-v1"]
)
def test_nullable_hot_cold_restart_and_idempotent_replica_import(tmp_path, kind):
    path = tmp_path / "origin.db"
    values = [value(kind, number=float(index)) for index in range(1024)]
    with PayloadStore(path) as origin, PayloadStore(tmp_path / "replica.db") as replica:
        refs = origin.put_public_projections(values[:1023])
        assert origin.projection_stats()["hot_count"] == 1023
        refs += origin.put_public_projections(values[1023:])
        assert (
            origin.projection_stats()["block_count"] == 1
            and origin.projection_stats()["hot_count"] == 0
        )
        assert origin.put_public_projections([values[0], values[-1]]) == [
            refs[0],
            refs[-1],
        ]
        records = read(origin, refs)
        replica.import_projection_records(refs[0].authority_uuid, records)
        replica.import_projection_records(refs[0].authority_uuid, records)
        assert read(replica, refs) == records
        assert replica._connection.execute(
            "SELECT count(*) FROM projection_unkeyed"
        ).fetchone() == (1024,)
        with pytest.raises(ScalarAuthorityError):
            replica.put_public_projections([values[0]])
    with PayloadReader(path) as reopened:
        assert [row.projection for row in read(reopened, refs)] == values


def mutate(path, sql, args=()):
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA foreign_keys=OFF")
        for name in PROJECTION_TRIGGER_SQL:
            database.execute("DROP TRIGGER " + name)
        database.execute(sql, args)
        for statement in PROJECTION_TRIGGER_SQL.values():
            database.execute(statement)


@pytest.mark.parametrize("cold", [False, True])
@pytest.mark.parametrize(
    "damage",
    ["missing-condition-dict", "missing-token-dict", "missing-unkeyed", "bad-unkeyed"],
)
def test_null_is_not_a_dangling_dictionary_or_missing_sparse_integrity_dependency(
    tmp_path, cold, damage
):
    path = tmp_path / "public.db"
    with PayloadStore(path) as store:
        refs = store.put_public_projections(
            [value(number=float(index)) for index in range(1024 if cold else 1)]
        )
    sql = {
        "missing-condition-dict": "UPDATE projection_records SET condition_key=123456 WHERE id=?",
        "missing-token-dict": "UPDATE projection_records SET token_key=123456 WHERE id=?",
        "missing-unkeyed": "DELETE FROM projection_unkeyed WHERE record_id=?",
        "bad-unkeyed": "UPDATE projection_unkeyed SET content_sha=zeroblob(32) WHERE record_id=?",
    }[damage]
    mutate(path, sql, (refs[0].record_id,))
    with PayloadReader(path) as reader:
        with pytest.raises(CorruptProjectionError):
            read(reader, refs[:1])


def test_unkeyed_retry_lookup_is_indexed_and_candidate_bounded(tmp_path, monkeypatch):
    with PayloadStore(tmp_path / "public.db") as store:
        values = [
            value("test-unindexed-v1", number=float(index)) for index in range(200)
        ]
        refs = store.put_public_projections(values)
        assert store.put_public_projections(values[-1:]) == refs[-1:]
        plan = store._connection.execute(
            "EXPLAIN QUERY PLAN SELECT record_id FROM projection_unkeyed WHERE content_sha=? ORDER BY record_id LIMIT 1025",
            (bytes.fromhex(values[-1].sha256),),
        ).fetchall()
        assert any("projection_unkeyed_sha" in row[3] for row in plan)
        # Deliberately lower the bound to exercise the same fail-closed branch
        # without inventing SHA collisions or large resident data.
        monkeypatch.setattr(
            "polybot_observability.market_data_projection_store.MAX_UNKEYED_CANDIDATES",
            0,
        )
        with pytest.raises(StoreLimitError, match="bounded limit"):
            store.put_public_projections(values[-1:])


def legacy_v1(path):
    with PayloadStore(path) as store:
        sha = store.put_many([b"original body"])[0]
        scalar = store.put_scalar_snapshots(
            [ScalarSnapshot("s", 0.5, None, None, None)]
        )[0]
    with sqlite3.connect(path) as database:
        for name in PROJECTION_TRIGGER_SQL:
            database.execute("DROP TRIGGER " + name)
        for name in reversed(tuple(PROJECTION_TABLE_SQL)):
            database.execute("DROP TABLE " + name)
        # Recreate the complete prior physical typed namespace, not merely a
        # similarly named corrupt table; no production artifact is modified.
        for name, definition in PROJECTION_TABLE_SQL.items():
            if name in {"projection_storage", "projection_unkeyed"}:
                continue
            database.execute(
                definition.replace(
                    "condition_key INTEGER REFERENCES",
                    "condition_key INTEGER NOT NULL REFERENCES",
                )
            )
        for name, definition in PROJECTION_INDEX_SQL.items():
            if name != "projection_unkeyed_sha":
                database.execute(definition)
        for name, definition in PROJECTION_TRIGGER_SQL.items():
            if name.startswith(("projection_storage_", "projection_unkeyed_")):
                continue
            database.execute(
                definition.replace(
                    "NEW.condition_key IS NOT OLD.condition_key",
                    "NEW.condition_key!=OLD.condition_key",
                ).replace(" OR EXISTS(SELECT 1 FROM projection_unkeyed)", "")
            )
        database.execute("PRAGMA journal_mode=DELETE")
    return sha, scalar


def test_v1_reader_and_writer_reject_before_any_file_or_schema_mutation(tmp_path):
    path = tmp_path / "old.db"
    legacy_v1(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with sqlite3.connect(path) as db:
        before = db.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
    for cls in (PayloadReader, PayloadStore):
        with pytest.raises(
            UnsupportedProjectionStorageError, match="explicit verified migration"
        ):
            cls(path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        with sqlite3.connect(path) as db:
            assert (
                db.execute(
                    "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
                == before
            )
            assert db.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert not Path(str(path) + "-wal").exists()


@pytest.mark.parametrize("damage", ["missing", "wrong", "duplicate"])
def test_v2_version_metadata_is_exact_and_fails_readonly_and_writer_open(
    tmp_path, damage
):
    path = tmp_path / "version.db"
    with PayloadStore(path):
        pass
    with sqlite3.connect(path) as database:
        database.execute("DROP TRIGGER projection_storage_no_delete")
        database.execute("DROP TRIGGER projection_storage_no_update")
        database.execute("PRAGMA ignore_check_constraints=ON")
        if damage == "missing":
            database.execute("DELETE FROM projection_storage")
        elif damage == "wrong":
            database.execute(
                "UPDATE projection_storage SET contract='public-projection-block-store-v1'"
            )
        else:
            database.execute(
                "INSERT INTO projection_storage VALUES(2,'public-projection-block-store-v2')"
            )
        database.execute(PROJECTION_TRIGGER_SQL["projection_storage_no_delete"])
        database.execute(PROJECTION_TRIGGER_SQL["projection_storage_no_update"])
    frozen = hashlib.sha256(path.read_bytes()).hexdigest()
    for cls in (PayloadReader, PayloadStore):
        with pytest.raises(
            UnsupportedProjectionStorageError, match="single exact version"
        ):
            cls(path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == frozen


@pytest.mark.parametrize("damage", ["missing-trigger", "weakened-trigger", "wrong-index"])
def test_v2_schema_damage_is_rejected_without_implicit_repair(tmp_path, damage):
    path = tmp_path / "schema.db"
    with PayloadStore(path) as store:
        store.put_public_projections([value()])
    with sqlite3.connect(path) as database:
        if damage == "wrong-index":
            database.execute("DROP INDEX projection_unkeyed_sha")
            database.execute(
                "CREATE INDEX projection_unkeyed_sha ON projection_unkeyed(record_id)"
            )
        else:
            database.execute("DROP TRIGGER projection_storage_no_update")
            if damage == "weakened-trigger":
                database.execute(
                    "CREATE TRIGGER projection_storage_no_update "
                    "BEFORE UPDATE ON projection_storage BEGIN SELECT 1; END"
                )
        database.execute("PRAGMA journal_mode=DELETE")
    frozen = hashlib.sha256(path.read_bytes()).hexdigest()
    for cls in (PayloadReader, PayloadStore):
        with pytest.raises(UnsupportedProjectionStorageError, match="schema definition"):
            cls(path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == frozen
        assert not Path(str(path) + "-wal").exists()


def test_body_scalar_only_database_can_initialize_v2_without_changing_data_or_authority(
    tmp_path,
):
    path = tmp_path / "body-scalar.db"
    with PayloadStore(path) as store:
        sha = store.put_many([b"unchanged"])[0]
        ref = store.put_scalar_snapshots(
            [ScalarSnapshot("condition", 0.75, None, None, "private clock")]
        )[0]
    with sqlite3.connect(path) as database:
        for name in PROJECTION_TRIGGER_SQL:
            database.execute("DROP TRIGGER " + name)
        for name in reversed(tuple(PROJECTION_TABLE_SQL)):
            database.execute("DROP TABLE " + name)
    with PayloadReader(path) as reader:
        assert reader.get_many([sha]) == [b"unchanged"]
        assert (
            reader.get_scalar_records([ref.record_id], ref.authority_uuid)[0].reference
            == ref
        )
    with PayloadStore(path) as writer:
        assert writer.scalar_authority_identity() == ref.authority_uuid
        assert writer.get_many([sha]) == [b"unchanged"]
        assert (
            writer.get_scalar_records([ref.record_id], ref.authority_uuid)[0].reference
            == ref
        )
        assert (
            writer.put_public_projections([value()])[0].authority_uuid
            == ref.authority_uuid
        )


def test_existing_logical_profile_hashes_are_byte_identical():
    expected = {
        "gamma-quote-v1": "2c6ffc084018936b05368385ea40cdf6f53bc6c29bda3eb60ddfa0006ac2a61e",
        "token-quote-v1": "0b19885bacb543606827e7d55f532de3328285e10fca92030a396940f115304a",
        "event-token-quote-v1": "49e785e5d4b4a44d7ef9b3190ee46faad405ae4e783cdcd19c29a2b8bd331548",
        "kiwi-catalog-v1": "0e5dae40eb75ae00d7ccd0a3a06604b94649cd085b4888d44d8a1defcc897a5e",
        "catalog-identity-v1": "294aa9838224fe1e04744ee567d5262b3c8a3ef28e7c37e5969ebb18609c5ea1",
        "catalog-state-v1": "378019b6debbcf417192e8b43f205b1954306a2be5d153a175429661a7ce2cf4",
    }
    assert {kind: projection_schema_sha(kind).hex() for kind in expected} == expected


def test_old_typed_writer_admission_uses_only_a_readonly_handle(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    legacy_v1(path)
    original_connect = sqlite3.connect
    attempts = []

    def observed(database, *args, **kwargs):
        attempts.append((database, kwargs.copy()))
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", observed)
    with pytest.raises(UnsupportedProjectionStorageError):
        PayloadStore(path)
    assert len(attempts) == 1
    assert attempts[0][0] == path.as_uri() + "?mode=ro"
    assert attempts[0][1]["uri"] is True


def test_sparse_orphan_cannot_allow_scalar_authority_rebinding(tmp_path):
    path = tmp_path / "orphan.db"
    with PayloadStore(path):
        pass
    mutate(path, "INSERT INTO projection_unkeyed VALUES(999,zeroblob(32))")
    with PayloadStore(path) as store:
        authority = store.scalar_authority_identity()
        with pytest.raises((sqlite3.IntegrityError, ScalarAuthorityError)):
            store.put_scalar_snapshots(
                [ScalarSnapshot("condition", 0.5, None, None, None)]
            )
        assert store.scalar_authority_identity() == authority
        assert store.scalar_authority_role() == "UNCLAIMED"
        with pytest.raises(ScalarAuthorityError, match="projection data already bind"):
            store.put_public_projections([value()])


def test_unsupported_v1_hot_journal_is_not_recovered_during_writer_admission(tmp_path):
    """A crashed rollback transaction must not be recovered during admission.

    A read-write schema inspection is itself enough to modify the original:
    SQLite recovers the hot journal before returning sqlite_schema. The initial
    read-only handle must instead reject until recovery is separately authorized.
    """
    path = tmp_path / "hot-v1.db"
    legacy_v1(path)
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE crash_probe(id INTEGER PRIMARY KEY, value BLOB)")
        database.executemany(
            "INSERT INTO crash_probe VALUES(?,?)",
            [(index, b"a" * 4096) for index in range(64)],
        )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,sqlite3,sys; "
            "db=sqlite3.connect(sys.argv[1]); "
            "db.execute('PRAGMA cache_size=5'); "
            "db.execute('PRAGMA synchronous=FULL'); "
            "db.execute('BEGIN IMMEDIATE'); "
            "db.execute('UPDATE crash_probe SET value=zeroblob(4096)'); "
            "os._exit(0)",
            str(path),
        ],
        check=True,
        timeout=10,
    )
    journal = Path(str(path) + "-journal")
    # This is SQLite's hot-journal header, not an inert sidecar fixture.
    assert journal.read_bytes()[:8] == bytes.fromhex("d9d505f920a163d7")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, journal)}
    with pytest.raises(sqlite3.OperationalError) as raised:
        PayloadStore(path)
    assert raised.value.sqlite_errorcode == sqlite3.SQLITE_READONLY_ROLLBACK
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, journal)} == before
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()

    # Prove this really was recoverable, using a disposable copy only.
    recovered = tmp_path / "disposable-recovery.db"
    recovered.write_bytes(path.read_bytes())
    Path(str(recovered) + "-journal").write_bytes(journal.read_bytes())
    with sqlite3.connect(recovered) as database:
        assert database.execute("SELECT value FROM crash_probe LIMIT 1").fetchone() == (b"a" * 4096,)
    assert not Path(str(recovered) + "-journal").exists()
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, journal)} == before
