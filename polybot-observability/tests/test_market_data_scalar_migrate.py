import sqlite3

import pytest
from polybot_observability.market_data_index import ReceiptContext
from polybot_observability.market_data_migrate import file_sha256, migrate_public_bodies
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadStore


def source_db(path, count=7):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,condition_id TEXT NOT NULL,
          probability REAL NOT NULL,liquidity REAL,volume_24h REAL,timestamp DATETIME);
        CREATE INDEX snapshot_condition ON market_snapshots(condition_id);
        CREATE INDEX snapshot_timestamp ON market_snapshots(timestamp);
        CREATE TABLE trades(id INTEGER PRIMARY KEY,private_state TEXT);
        INSERT INTO trades VALUES(9,'private-account-sentinel');
    """)
    connection.executemany(
        "INSERT INTO market_snapshots VALUES(?,?,?,?,?,?)",
        [
            (
                17 + i,
                "condition-" + str(i % 3),
                0.70 + i / 10000,
                None if i % 2 else 12000.0,
                0.0,
                f"2026-09-01 00:{i // 60:02}:{i % 60:02}.000000",
            )
            for i in range(count)
        ],
    )
    connection.commit()
    connection.close()


def test_scalar_history_moves_values_but_preserves_ids_private_rows_and_queries(
    tmp_path,
):
    original = tmp_path / "original.db"
    source_db(original, 1030)
    original_sha = file_sha256(original)
    destination = tmp_path / "linked.db"
    with PayloadStore(tmp_path / "public.db") as store:
        refs = PayloadReferences(reader=store, writer=store)
        result = migrate_public_bodies(
            original,
            destination,
            strategy="golden-date",
            source_sha256=original_sha,
            references=refs,
            include_scalars=True,
            batch_rows=1024,
            receipt_context=ReceiptContext("source", "default", "polybot-red"),
        )
        assert result["status"] == "VERIFIED"
        assert result["tables"]["market_snapshots"]["externalized_scalar_rows"] == 1030
        assert result["public_scalar_records"]["record_count"] == 1030
        assert store.scalar_stats()["block_count"] == 1
        assert store.scalar_stats()["hot_count"] == 6
        with (
            sqlite3.connect(original) as before,
            connect(destination, references=refs) as after,
        ):
            assert (
                before.execute("SELECT * FROM market_snapshots ORDER BY id").fetchall()
                == after.execute(
                    "SELECT * FROM market_snapshots ORDER BY id"
                ).fetchall()
            )
            assert after.execute(
                "SELECT COUNT(*) FROM main.market_snapshots"
            ).fetchone() == (0,)
            assert after.execute("SELECT * FROM trades").fetchall() == [
                (9, "private-account-sentinel")
            ]
        assert file_sha256(original) == original_sha
        assert result["public_scalar_records"]["receipt_count"] == 1030


def test_scalar_migration_requires_proven_source_namespace(tmp_path):
    original = tmp_path / "source.db"
    source_db(original)
    with PayloadStore(tmp_path / "public.db") as store:
        with pytest.raises(ValueError, match="explicit source"):
            migrate_public_bodies(
                original,
                tmp_path / "bad.db",
                strategy="golden-date",
                source_sha256=file_sha256(original),
                references=PayloadReferences(store, store),
                include_scalars=True,
            )
        assert store.scalar_stats()["snapshot_count"] == 0


def test_body_only_manifest_discloses_remaining_scalar_rows(tmp_path):
    original = tmp_path / "source.db"
    source_db(original)
    with PayloadStore(tmp_path / "public.db") as store:
        result = migrate_public_bodies(
            original,
            tmp_path / "body-only.db",
            strategy="golden-date",
            source_sha256=file_sha256(original),
            references=PayloadReferences(store, store),
        )
        assert result["remaining_public_scalar_rows"] == 7
        assert store.scalar_stats()["snapshot_count"] == 0
