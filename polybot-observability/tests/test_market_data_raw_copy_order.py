"""Physical insertion order may change; canonical fingerprints and rows may not."""
import sqlite3

import pytest

from polybot_observability.market_data_migrate import _select, file_sha256
from polybot_observability.market_data_raw_migrate import _private_copy_select, migrate_raw_database
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadStore
from test_market_data_raw_closure import NAMESPACE, seed_raw


def test_private_copy_uses_original_sparse_rowid_without_changing_canonical_order():
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE private_values(k TEXT PRIMARY KEY, v BLOB)")
        db.executemany("INSERT INTO private_values(rowid,k,v) VALUES(?,?,?)", [
            (99, "a", b"last"), (-7, "z", b"first"), (0, None, b"zero"),
            (1 << 35, None, b"large"),
        ])
        canonical_names, canonical_query = _select(db, "private_values")
        names, copy_query = _private_copy_select(db, "private_values")
        assert names == canonical_names
        assert [r[0] for r in db.execute(copy_query)] == [-7, 0, 99, 1 << 35]
        assert [r[0] for r in db.execute(canonical_query)] == [0, 1 << 35, 99, -7]
        assert _select(db, "private_values") == (canonical_names, canonical_query)


@pytest.mark.parametrize("declaration", [
    "rowid TEXT", '"_ROWID_" TEXT', "oid TEXT",
    '"RowID" TEXT GENERATED ALWAYS AS (k) VIRTUAL',
])
def test_declared_alias_keeps_canonical_copy_but_uses_real_hidden_rowid(declaration):
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE private_values(k TEXT PRIMARY KEY," + declaration + ")")
        columns = {r[1].casefold() for r in db.execute("PRAGMA table_xinfo(private_values)")}
        hidden = next(n for n in ("_rowid_", "rowid", "oid") if n not in columns)
        db.executemany("INSERT INTO private_values(" + hidden + ",k) VALUES(?,?)",
                       [(-7, "z"), (99, "a")])
        names, query = _private_copy_select(db, "private_values")
        original_names, original_query = _select(db, "private_values")
        assert names == original_names and names[0] == hidden
        assert list(db.execute(query)) == list(db.execute(original_query))
        assert [r[0] for r in db.execute(query)] == [99, -7]


def test_without_rowid_keeps_declared_composite_key_order():
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE private_values(k TEXT,n INTEGER,v BLOB,PRIMARY KEY(k,n)) WITHOUT ROWID")
        db.executemany("INSERT INTO private_values VALUES(?,?,?)", [("z", 1, b"z"), ("a", 2, b"a")])
        names, query = _private_copy_select(db, "private_values")
        canonical_names, canonical_query = _select(db, "private_values")
        assert names == canonical_names == ["k", "n", "v"]
        assert list(db.execute(query)) == list(db.execute(canonical_query)) == [("a", 2, b"a"), ("z", 1, b"z")]


def test_full_raw_remigration_preserves_private_nullable_keys_rowids_and_foreign_keys(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    with PayloadStore(tmp_path / "public.db") as store:
        seed_raw(source, store)
        with sqlite3.connect(source) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("CREATE TABLE private_values(k TEXT PRIMARY KEY,sweep_id TEXT REFERENCES market_sweeps(sweep_id),v BLOB)")
            rows = [(99, "a", "sweep", b"last"), (-7, "z", "sweep", b"first"),
                    (0, None, "sweep", b"zero"), (1 << 35, None, "sweep", b"large")]
            db.executemany("INSERT INTO private_values(rowid,k,sweep_id,v) VALUES(?,?,?,?)", rows)
        before = file_sha256(source)
        manifest = migrate_raw_database(source, target, source_sha256=before, namespace=NAMESPACE,
                                        references=PayloadReferences(store, store), batch_rows=2)
        assert manifest["status"] == "VERIFIED"
        assert file_sha256(source) == before
        assert manifest["tables"]["private_values"]["rows"] == 4
        assert manifest["private_storage"]["private_values"]["source_sha256"] == manifest["private_storage"]["private_values"]["target_sha256"]
        with sqlite3.connect(target) as db:
            assert db.execute("SELECT rowid,* FROM private_values ORDER BY rowid").fetchall() == sorted(rows)
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
