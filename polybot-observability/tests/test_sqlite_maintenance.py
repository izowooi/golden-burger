import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from polybot_observability import (
    SQLiteMaintenanceRequirements,
    policy_for,
    prepare_database,
)


def _seed_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE order_submissions (submission_id TEXT PRIMARY KEY);
        CREATE TABLE market_snapshots (
            id INTEGER PRIMARY KEY,
            condition_id TEXT NOT NULL,
            probability REAL NOT NULL,
            liquidity REAL,
            volume_24h REAL,
            timestamp TEXT,
            run_id TEXT
        );
        CREATE INDEX ix_market_snapshots_condition_id
            ON market_snapshots(condition_id);
        CREATE INDEX ix_market_snapshots_timestamp
            ON market_snapshots(timestamp);
        CREATE INDEX market_snapshots_condition_timestamp_idx
            ON market_snapshots(condition_id, timestamp);
        CREATE INDEX ix_market_snapshots_run_id
            ON market_snapshots(run_id);
        CREATE INDEX market_snapshots_run_idx
            ON market_snapshots(run_id);
        CREATE TABLE market_sweeps (
            sweep_id TEXT PRIMARY KEY,
            run_id TEXT,
            started_at TEXT,
            completed_at TEXT,
            cursor_complete INTEGER NOT NULL,
            qualified_market_count INTEGER NOT NULL
        );
        CREATE TABLE market_sweep_memberships (
            sweep_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            raw_seen_count INTEGER NOT NULL,
            qualified INTEGER NOT NULL,
            qualification_reason TEXT NOT NULL,
            snapshot_eligible INTEGER NOT NULL,
            snapshotted INTEGER NOT NULL,
            snapshot_reason TEXT NOT NULL,
            PRIMARY KEY (sweep_id, condition_id)
        );
        CREATE INDEX ix_market_sweep_memberships_qualified
            ON market_sweep_memberships(qualified);
        CREATE INDEX ix_market_sweep_memberships_snapshotted
            ON market_sweep_memberships(snapshotted);
        INSERT INTO trades(id, status) VALUES (1, 'holding');
        INSERT INTO order_submissions(submission_id) VALUES ('submission-1');
        """
    )
    start = datetime(2026, 7, 1)
    for number in range(0, 49 * 4):
        observed_at = start + timedelta(minutes=15 * number)
        sweep_id = f"sweep-{number:04d}"
        run_id = f"run-{number:04d}"
        timestamp = observed_at.isoformat(timespec="seconds")
        connection.execute(
            "INSERT INTO market_sweeps VALUES (?, ?, ?, ?, 1, 2)",
            (sweep_id, run_id, timestamp, timestamp),
        )
        for condition_number, condition_id in enumerate(("condition-a", "condition-b")):
            probability = 0.1 + ((number // 16 + condition_number) % 4) / 100
            connection.execute(
                "INSERT INTO market_snapshots(condition_id, probability, liquidity, "
                "volume_24h, timestamp, run_id) VALUES (?, ?, 10000, 5000, ?, ?)",
                (condition_id, probability, timestamp, run_id),
            )
            connection.execute(
                "INSERT INTO market_sweep_memberships VALUES "
                "(?, ?, 1, 1, 'qualified', 1, 1, 'snapshot_saved')",
                (sweep_id, condition_id),
            )
    connection.commit()
    connection.close()


def test_policy_is_strategy_aware(monkeypatch):
    for name in (
        "POLYBOT_DB_HOT_HOURS",
        "POLYBOT_DB_ROLLUP_HOURS",
        "POLYBOT_DB_RETENTION_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)

    assert policy_for("golden-nectarine").selector == "minimum"
    assert policy_for("golden-papaya").selector == "extrema"
    assert policy_for("golden-queen").selector == "extrema"
    assert policy_for("golden-queen").retention_days == 60
    assert policy_for("golden-queen").hot_hours == 1
    assert policy_for("golden-elderberry").selector == "extrema"
    assert policy_for("golden-honeydew").selector == "latest"
    assert policy_for("golden-orange").retention_days == 21
    assert policy_for("golden-orange").hot_hours == 168
    assert policy_for("golden-nectarine").hot_hours == 1
    assert policy_for("golden-nectarine").rollup_hours == 12


def test_policy_rejects_non_finite_number(monkeypatch):
    monkeypatch.setenv("POLYBOT_DB_ROLLUP_HOURS", "inf")
    with pytest.raises(ValueError, match="positive number"):
        policy_for("golden-apple")


@pytest.mark.parametrize(
    ("strategy", "environment"),
    (
        ("golden-honeydew", {"POLYBOT_DB_HOT_HOURS": "23"}),
        ("golden-papaya", {"POLYBOT_DB_HOT_HOURS": "0.49"}),
        ("golden-papaya", {"POLYBOT_DB_RETENTION_DAYS": "59"}),
        ("golden-queen", {"POLYBOT_DB_HOT_HOURS": "0.24"}),
        ("golden-queen", {"POLYBOT_DB_RETENTION_DAYS": "59"}),
        ("golden-nectarine", {"POLYBOT_DB_RETENTION_DAYS": "19"}),
        ("golden-elderberry", {"POLYBOT_DB_HOT_HOURS": "0.5"}),
        ("golden-elderberry", {"POLYBOT_DB_RETENTION_DAYS": "1"}),
        (
            "golden-elderberry",
            {"POLYBOT_REF_EXCLUDE_RECENT_HOURS": "40"},
        ),
        ("golden-orange", {"POLYBOT_DB_HOT_HOURS": "167"}),
        ("golden-orange", {"POLYBOT_DB_RETENTION_DAYS": "6"}),
        (
            "golden-fig",
            {"POLYBOT_RISE_LOOKBACK_HOURS": "25"},
        ),
        (
            "golden-grape",
            {"POLYBOT_DRIFT_LOOKBACK_HOURS": "25"},
        ),
        (
            "golden-grape",
            {"POLYBOT_DEATH_WINDOW_HOURS": "25"},
        ),
        (
            "golden-banana",
            {"POLYBOT_MOMENTUM_LONG_WINDOW": "300"},
        ),
        (
            "golden-date",
            {"POLYBOT_MOMENTUM_LOOKBACK_HOURS": "25"},
        ),
        (
            "golden-lime",
            {"POLYBOT_JUMP_WINDOW_HOURS": "25"},
        ),
        (
            "golden-mango",
            {"POLYBOT_MOMENTUM_LOOKBACK_HOURS": "25"},
        ),
        (
            "golden-apple",
            {"POLYBOT_DB_RETENTION_DAYS": "0.5"},
        ),
    ),
)
def test_policy_rejects_signal_breaking_overrides(strategy, environment, monkeypatch):
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        policy_for(strategy)


def test_compact_v1_is_backed_up_atomic_and_idempotent(tmp_path, monkeypatch):
    database = tmp_path / "trades.db"
    backup_root = tmp_path / "backups"
    _seed_database(database)
    bytes_before = database.stat().st_size
    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))
    monkeypatch.setenv("POLYBOT_DB_HOT_HOURS", "1")
    monkeypatch.setenv("POLYBOT_DB_ROLLUP_HOURS", "4")
    monkeypatch.setenv("POLYBOT_DB_MEMBERSHIP_DETAIL_HOURS", "24")

    report = prepare_database(database, "golden-nectarine")

    assert report is not None
    assert report.bytes_before == bytes_before
    assert report.bytes_after < report.bytes_before
    assert report.snapshots_after < report.snapshots_before / 4
    assert report.memberships_after <= 6
    assert report.sweeps_after == report.sweeps_before
    assert report.backup_path is not None
    manifest_path = tmp_path / "backups" / "golden-nectarine"
    manifests = list(manifest_path.glob("*.manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest["backup_sha256"] == report.backup_sha256

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM order_submissions").fetchone()[0]
            == 1
        )
        state = connection.execute(
            "SELECT active, strategy_name FROM polybot_db_maintenance "
            "WHERE profile='compact-v1'"
        ).fetchone()
        assert state == (1, "golden-nectarine")
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='index'"
            )
        }
        assert "ix_market_snapshots_condition_id" not in indexes
        assert "market_snapshots_run_idx" in indexes
        assert "ix_market_snapshots_run_id" not in indexes

    # Leaving the one-shot flag in place cannot run or back up the migration twice.
    assert prepare_database(database, "golden-nectarine") is None
    assert len(list(manifest_path.glob("*.manifest.json"))) == 1


def test_active_state_rejects_strategy_and_schema_identity_mismatch(
    tmp_path, monkeypatch
):
    database = tmp_path / "trades.db"
    backup_root = tmp_path / "backups"
    _seed_database(database)
    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))
    prepare_database(database, "golden-nectarine")

    with pytest.raises(RuntimeError, match="strategy identity mismatch"):
        prepare_database(database, "golden-apple")

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE polybot_db_maintenance SET schema_version = 999 "
            "WHERE profile = 'compact-v1'"
        )
        connection.commit()
    with pytest.raises(RuntimeError, match="schema_version"):
        prepare_database(database, "golden-nectarine")


def test_active_state_rejects_policy_and_resolved_requirement_drift(
    tmp_path, monkeypatch
):
    database = tmp_path / "trades.db"
    backup_root = tmp_path / "backups"
    _seed_database(database)
    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))
    prepare_database(database, "golden-nectarine")

    monkeypatch.setenv("POLYBOT_DB_ROLLUP_HOURS", "6")
    with pytest.raises(RuntimeError, match="policy mismatch"):
        prepare_database(database, "golden-nectarine")

    monkeypatch.delenv("POLYBOT_DB_ROLLUP_HOURS")
    changed_requirements = SQLiteMaintenanceRequirements(
        retention_days=21.0,
        boundary_interval_hours=480.0,
        max_rollup_hours=24.0,
    )
    with pytest.raises(RuntimeError, match="requirements mismatch"):
        prepare_database(
            database,
            "golden-nectarine",
            requirements=changed_requirements,
        )


def test_nectarine_rejects_rollup_gap_that_breaks_window_coverage(monkeypatch):
    monkeypatch.setenv("POLYBOT_DB_ROLLUP_HOURS", "25")
    with pytest.raises(ValueError, match="maximum rollup gap"):
        policy_for("golden-nectarine")


def test_banana_preserves_exact_latest_n_rows_across_outages(tmp_path, monkeypatch):
    database = tmp_path / "banana.db"
    backup_root = tmp_path / "backups"
    anchor = datetime.utcnow() - timedelta(days=1)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE market_snapshots ("
            "id INTEGER PRIMARY KEY, condition_id TEXT NOT NULL, "
            "probability REAL NOT NULL, timestamp TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO market_snapshots(id, condition_id, probability, timestamp) "
            "VALUES (?, 'condition-a', ?, ?)",
            [
                (
                    number,
                    number / 1000.0,
                    (anchor - timedelta(days=100 - number)).isoformat(),
                )
                for number in range(1, 101)
            ],
        )

    requirements = SQLiteMaintenanceRequirements(
        full_cadence_hours=(72 - 1) * 5.0 / 60.0,
        minimum_latest_points=82,
    )
    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))
    prepare_database(
        database,
        "golden-banana",
        requirements=requirements,
    )

    with sqlite3.connect(database) as connection:
        retained_ids = {
            row[0]
            for row in connection.execute(
                "SELECT id FROM market_snapshots WHERE condition_id='condition-a'"
            )
        }

    assert set(range(19, 101)).issubset(retained_ids)


def test_migration_fails_closed_when_source_has_a_writer(tmp_path, monkeypatch):
    database = tmp_path / "trades.db"
    backup_root = tmp_path / "backups"
    _seed_database(database)
    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))
    writer = sqlite3.connect(database, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(RuntimeError, match="busy"):
            prepare_database(database, "golden-nectarine")
    finally:
        writer.rollback()
        writer.close()
    assert list(backup_root.rglob("*.db")) == []


def test_migration_fails_closed_for_wal_source(tmp_path, monkeypatch):
    database = tmp_path / "trades.db"
    backup_root = tmp_path / "backups"
    _seed_database(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))

    with pytest.raises(RuntimeError, match="refuses WAL mode"):
        prepare_database(database, "golden-nectarine")

    assert list(backup_root.rglob("*.db")) == []


def test_same_named_strategy_databases_get_distinct_backups(tmp_path, monkeypatch):
    backup_root = tmp_path / "backups"
    first = tmp_path / "job-a" / "trades.db"
    second = tmp_path / "job-b" / "trades.db"
    first.parent.mkdir()
    second.parent.mkdir()
    _seed_database(first)
    _seed_database(second)
    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))

    first_report = prepare_database(first, "golden-apple")
    second_report = prepare_database(second, "golden-apple")

    assert first_report is not None and second_report is not None
    assert first_report.backup_path != second_report.backup_path
    manifests = [json.loads(path.read_text()) for path in backup_root.rglob("*.json")]
    assert len(manifests) == 2
    assert len({row["source_id_sha256_prefix"] for row in manifests}) == 2


def test_invalid_profile_fails_before_database_mutation(tmp_path, monkeypatch):
    database = tmp_path / "trades.db"
    _seed_database(database)
    original = database.read_bytes()
    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "delete-everything")

    with pytest.raises(ValueError, match="must be empty or"):
        prepare_database(database, "golden-nectarine")

    assert database.read_bytes() == original


def test_inactive_database_does_not_resolve_compaction_overrides(tmp_path, monkeypatch):
    database = tmp_path / "inactive.db"
    _seed_database(database)
    original = database.read_bytes()
    monkeypatch.setenv("POLYBOT_DB_HOT_HOURS", "1")
    monkeypatch.setenv("POLYBOT_RISE_LOOKBACK_HOURS", "25")

    assert prepare_database(database, "golden-fig") is None
    assert database.read_bytes() == original


def test_flag_activates_a_brand_new_database(tmp_path, monkeypatch):
    database = tmp_path / "new-job" / "trades.db"
    backup_root = tmp_path / "backups"
    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))

    report = prepare_database(database, "golden-apple")

    assert report is not None
    assert database.exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT active, strategy_name FROM polybot_db_maintenance "
            "WHERE profile = 'compact-v1'"
        ).fetchone() == (1, "golden-apple")


def test_maintenance_continues_after_one_shot_flag_is_removed(tmp_path, monkeypatch):
    database = tmp_path / "trades.db"
    backup_root = tmp_path / "backups"
    _seed_database(database)
    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))
    monkeypatch.setenv("POLYBOT_DB_HOT_HOURS", "1")
    monkeypatch.setenv("POLYBOT_DB_ROLLUP_HOURS", "4")
    prepare_database(database, "golden-nectarine")

    with sqlite3.connect(database) as connection:
        old_timestamp = connection.execute(
            "SELECT MIN(timestamp) FROM market_snapshots"
        ).fetchone()[0]
        for number in range(20):
            connection.execute(
                "INSERT INTO market_snapshots(condition_id, probability, timestamp) "
                "VALUES ('condition-a', ?, ?)",
                (0.4 + number / 1000, old_timestamp),
            )
        connection.execute(
            "UPDATE polybot_db_maintenance "
            "SET last_maintained_at = '2000-01-01T00:00:00+00:00' "
            "WHERE profile = 'compact-v1'"
        )
        connection.commit()
        count_before = connection.execute(
            "SELECT COUNT(*) FROM market_snapshots"
        ).fetchone()[0]

    monkeypatch.delenv("POLYBOT_DB_MAINTENANCE")
    report = prepare_database(database, "golden-nectarine")

    assert report is not None
    assert report.backup_path is None
    assert report.snapshots_before == count_before
    assert report.snapshots_after < count_before
    assert len(list(backup_root.rglob("*.manifest.json"))) == 1


def test_ongoing_thinning_never_promotes_summary_sweep_over_real_detail(
    tmp_path, monkeypatch
):
    database = tmp_path / "trades.db"
    backup_root = tmp_path / "backups"
    _seed_database(database)
    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))
    monkeypatch.setenv("POLYBOT_DB_HOT_HOURS", "1")
    prepare_database(database, "golden-nectarine")

    with sqlite3.connect(database) as connection:
        detail_sweep = connection.execute(
            "SELECT sweep_id FROM market_sweeps "
            "WHERE membership_detail_stored = 1 ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()[0]
        completed_at = connection.execute(
            "SELECT completed_at FROM market_sweeps WHERE sweep_id = ?",
            (detail_sweep,),
        ).fetchone()[0]
        summary_sweep = "newer-summary"
        connection.execute(
            "INSERT INTO market_sweeps("
            "sweep_id, run_id, started_at, completed_at, cursor_complete, "
            "qualified_market_count, membership_detail_stored"
            ") VALUES (?, 'run-summary', ?, datetime(?, '+5 minutes'), 1, 2, 0)",
            (summary_sweep, completed_at, completed_at),
        )
        connection.execute(
            "UPDATE polybot_db_maintenance SET last_maintained_at = "
            "'2000-01-01T00:00:00+00:00' WHERE profile = 'compact-v1'"
        )
        connection.commit()

    monkeypatch.delenv("POLYBOT_DB_MAINTENANCE")
    prepare_database(database, "golden-nectarine")

    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT membership_detail_stored FROM market_sweeps WHERE sweep_id = ?",
                (detail_sweep,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM market_sweep_memberships WHERE sweep_id = ?",
                (detail_sweep,),
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT membership_detail_stored FROM market_sweeps WHERE sweep_id = ?",
                (summary_sweep,),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("strategy_name", ("golden-papaya", "golden-queen"))
def test_first_crossing_trade_entry_and_immediate_prior_snapshots_are_never_deleted(
    tmp_path, monkeypatch, strategy_name
):
    database = tmp_path / f"{strategy_name}.db"
    backup_root = tmp_path / "backups"
    _seed_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE trades ADD COLUMN entry_snapshot_id INTEGER")
        prior_id, entry_id = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM market_snapshots "
                "WHERE condition_id = 'condition-a' ORDER BY timestamp, id LIMIT 2"
            )
        ]
        unrelated_id = connection.execute(
            "SELECT id FROM market_snapshots "
            "WHERE condition_id = 'condition-b' ORDER BY timestamp, id LIMIT 1"
        ).fetchone()[0]
        old = datetime.utcnow() - timedelta(days=61)
        connection.execute(
            "UPDATE market_snapshots SET timestamp = ? WHERE id = ?",
            (old.isoformat(), prior_id),
        )
        connection.execute(
            "UPDATE market_snapshots SET timestamp = ? WHERE id = ?",
            ((old + timedelta(minutes=5)).isoformat(), entry_id),
        )
        connection.execute(
            "UPDATE market_snapshots SET timestamp = ? WHERE id = ?",
            (old.isoformat(), unrelated_id),
        )
        connection.execute(
            "UPDATE trades SET entry_snapshot_id = ? WHERE id = 1",
            (entry_id,),
        )
        connection.execute(
            "INSERT INTO market_snapshots(condition_id, probability, timestamp) "
            "VALUES ('current-condition', 0.5, ?)",
            (datetime.utcnow().isoformat(),),
        )
        connection.commit()

    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))
    monkeypatch.setenv("POLYBOT_DB_HOT_HOURS", "0.5")
    prepare_database(database, strategy_name)

    with sqlite3.connect(database) as connection:
        remaining = {
            row[0]
            for row in connection.execute(
                "SELECT id FROM market_snapshots WHERE id IN (?, ?, ?)",
                (prior_id, entry_id, unrelated_id),
            )
        }
        state_payload = json.loads(
            connection.execute(
                "SELECT last_report_json FROM polybot_db_maintenance "
                "WHERE profile = 'compact-v1'"
            ).fetchone()[0]
        )

    assert {prior_id, entry_id}.issubset(remaining)
    assert unrelated_id not in remaining
    assert state_payload["snapshot_lineage_gaps"] == {
        "entry_snapshot_missing": 0,
        "prior_snapshot_missing": 0,
    }


def test_future_timestamp_cannot_advance_destructive_retention_cutoff(
    tmp_path, monkeypatch
):
    database = tmp_path / "future-anchor.db"
    backup_root = tmp_path / "backups"
    now = datetime.utcnow()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE market_snapshots ("
            "id INTEGER PRIMARY KEY, condition_id TEXT NOT NULL, "
            "probability REAL NOT NULL, timestamp TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO market_snapshots(condition_id, probability, timestamp) "
            "VALUES (?, 0.5, ?)",
            (
                ("recent", (now - timedelta(days=1)).isoformat()),
                ("future", (now + timedelta(days=365)).isoformat()),
            ),
        )

    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))
    prepare_database(database, "golden-apple")

    with sqlite3.connect(database) as connection:
        assert {
            row[0]
            for row in connection.execute("SELECT condition_id FROM market_snapshots")
        } == {"recent", "future"}


def test_nectarine_rollup_preserves_sliding_window_boundary_minima(
    tmp_path, monkeypatch
):
    database = tmp_path / "nectarine-boundaries.db"
    backup_root = tmp_path / "backups"
    observations = [
        ("2026-07-01T00:00:00", 0.50),
        ("2026-07-01T01:00:00", 0.20),
        ("2026-07-01T02:00:00", 0.40),
        ("2026-07-01T03:00:00", 0.30),
        ("2026-07-01T04:00:00", 0.10),
        ("2026-07-01T05:00:00", 0.60),
    ]
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE market_snapshots ("
            "id INTEGER PRIMARY KEY, condition_id TEXT NOT NULL, "
            "probability REAL NOT NULL, timestamp TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO market_snapshots(condition_id, probability, timestamp) "
            "VALUES ('condition-a', ?, ?)",
            [(probability, timestamp) for timestamp, probability in observations],
        )
        connection.execute(
            "INSERT INTO market_snapshots(condition_id, probability, timestamp) "
            "VALUES ('current', 0.5, '2026-07-02T00:00:00')"
        )

    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))
    prepare_database(database, "golden-nectarine")

    with sqlite3.connect(database) as connection:
        retained = [
            (str(row[0]), float(row[1]))
            for row in connection.execute(
                "SELECT timestamp, probability FROM market_snapshots "
                "WHERE condition_id = 'condition-a' ORDER BY timestamp"
            )
        ]

    # A long sliding window can cut only the first bucket as a suffix and the
    # last bucket as a prefix.  The retained envelope must reproduce both
    # boundary minima for every possible cut point.
    for index, (timestamp, _) in enumerate(observations):
        original_suffix_min = min(value for _, value in observations[index:])
        retained_suffix_min = min(
            value for retained_at, value in retained if retained_at >= timestamp
        )
        assert retained_suffix_min == original_suffix_min

        original_prefix_min = min(value for _, value in observations[: index + 1])
        retained_prefix_min = min(
            value for retained_at, value in retained if retained_at <= timestamp
        )
        assert retained_prefix_min == original_prefix_min


def test_elderberry_rollup_preserves_arbitrary_window_extrema(tmp_path, monkeypatch):
    database = tmp_path / "elderberry-boundaries.db"
    backup_root = tmp_path / "backups"
    observations = [
        ("2026-07-01T00:00:00", 0.50),
        ("2026-07-01T01:00:00", 0.80),
        ("2026-07-01T02:00:00", 0.40),
        ("2026-07-01T03:00:00", 0.70),
        ("2026-07-01T04:00:00", 0.20),
        ("2026-07-01T05:00:00", 0.60),
    ]
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE market_snapshots ("
            "id INTEGER PRIMARY KEY, condition_id TEXT NOT NULL, "
            "probability REAL NOT NULL, timestamp TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO market_snapshots(condition_id, probability, timestamp) "
            "VALUES ('condition-a', ?, ?)",
            [(probability, timestamp) for timestamp, probability in observations],
        )
        connection.execute(
            "INSERT INTO market_snapshots(condition_id, probability, timestamp) "
            "VALUES ('current', 0.5, '2026-07-02T00:00:00')"
        )

    monkeypatch.setenv("POLYBOT_DB_MAINTENANCE", "compact-v1")
    monkeypatch.setenv("POLYBOT_DB_BACKUP_DIR", str(backup_root))
    prepare_database(database, "golden-elderberry")

    with sqlite3.connect(database) as connection:
        retained = [
            (str(row[0]), float(row[1]))
            for row in connection.execute(
                "SELECT timestamp, probability FROM market_snapshots "
                "WHERE condition_id = 'condition-a' ORDER BY timestamp"
            )
        ]

    for index, (timestamp, _) in enumerate(observations):
        original_suffix = [value for _, value in observations[index:]]
        retained_suffix = [
            value for retained_at, value in retained if retained_at >= timestamp
        ]
        assert min(retained_suffix) == min(original_suffix)
        assert max(retained_suffix) == max(original_suffix)

        original_prefix = [value for _, value in observations[: index + 1]]
        retained_prefix = [
            value for retained_at, value in retained if retained_at <= timestamp
        ]
        assert min(retained_prefix) == min(original_prefix)
        assert max(retained_prefix) == max(original_prefix)
