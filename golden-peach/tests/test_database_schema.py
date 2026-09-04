import sqlite3

import pytest

from polybot.db.models import init_database


def _create_legacy_market_sweeps(path, *, schema_version_type="INTEGER") -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            f"""
            CREATE TABLE market_sweeps (
                sweep_id TEXT PRIMARY KEY,
                schema_version {schema_version_type} NOT NULL,
                run_id TEXT,
                started_at DATETIME NOT NULL,
                completed_at DATETIME NOT NULL,
                cursor_complete INTEGER NOT NULL,
                pages INTEGER NOT NULL,
                raw_market_count INTEGER NOT NULL,
                unique_condition_count INTEGER NOT NULL,
                qualified_market_count INTEGER NOT NULL,
                excluded_condition_count INTEGER NOT NULL,
                exclusion_counts_json TEXT NOT NULL,
                missing_condition_id_count INTEGER NOT NULL,
                duplicate_raw_count INTEGER NOT NULL,
                min_liquidity REAL NOT NULL,
                min_volume REAL NOT NULL,
                membership_digest_sha256 TEXT NOT NULL,
                snapshot_eligible_count INTEGER NOT NULL,
                snapshotted_market_count INTEGER NOT NULL
            )
            """
        )


def test_repeated_additive_migration_is_deterministic(tmp_path) -> None:
    path = tmp_path / "repeat.db"
    _create_legacy_market_sweeps(path)

    first = init_database(str(path))
    first().close()
    second = init_database(str(path))
    second().close()

    with sqlite3.connect(path) as connection:
        columns = [
            row[1]
            for row in connection.execute("PRAGMA table_info(market_sweeps)")
        ]
    assert columns.count("membership_detail_stored") == 1


def test_incompatible_type_affinity_fails_closed(tmp_path) -> None:
    path = tmp_path / "wrong-affinity.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE skipped_markets (
                id TEXT PRIMARY KEY,
                condition_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                skipped_at DATETIME
            )
            """
        )

    with pytest.raises(RuntimeError, match="incompatible skipped_markets schema"):
        init_database(str(path))


def test_failed_schema_validation_rolls_back_additive_migration(tmp_path) -> None:
    path = tmp_path / "rollback.db"
    _create_legacy_market_sweeps(path, schema_version_type="TEXT")

    with pytest.raises(RuntimeError, match="incompatible market_sweeps schema"):
        init_database(str(path))

    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(market_sweeps)")
        }
    assert "membership_detail_stored" not in columns
