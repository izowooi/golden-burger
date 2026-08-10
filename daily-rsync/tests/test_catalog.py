from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from daily_rsync.catalog import Catalog
from daily_rsync.models import RemoteArtifact, artifact_source_key


def artifact(path: Path, *, source: str = "macmini") -> RemoteArtifact:
    path.write_text("hello", encoding="utf-8")
    stat = path.stat()
    return RemoteArtifact(
        kind="bot_log",
        remote_path="/remote/20260729.log",
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        jenkins_job="polybot-king",
        strategy="golden-queen",
        runtime_job="queen-live-12h",
        completed_at="2026-07-29T00:00:00+00:00",
        source=source,
    )


def test_catalog_detects_unchanged_artifact(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    local = tmp_path / "local.log"
    item = artifact(local, source="test")
    assert not catalog.artifact_is_current(item)

    catalog.upsert_artifact(
        item,
        source="test",
        local_path=local,
        local_sha256="digest",
    )

    assert catalog.artifact_is_current(item)
    rows = catalog.list_artifacts(
        source="test", job="polybot-king", strategy="golden-queen"
    )
    assert len(rows) == 1
    assert rows[0]["runtime_job"] == "queen-live-12h"


def test_catalog_uses_composite_sqlite_fingerprint_over_main_stat(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    local = tmp_path / "trades.db"
    local.write_bytes(b"database")
    first = RemoteArtifact(
        kind="database_live",
        remote_path="/remote/trades.db",
        size_bytes=100,
        mtime_ns=200,
        fingerprint="main-wal-state-1",
        jenkins_job="polybot-king",
        strategy="golden-queen",
        runtime_job="queen-live",
        source="macmini",
    )
    catalog.upsert_artifact(first, source="macmini", local_path=local, local_sha256="x")
    wal_only_change = RemoteArtifact(
        **{
            **first.__dict__,
            "size_bytes": 100,
            "mtime_ns": 200,
            "fingerprint": "main-wal-state-2",
        }
    )

    assert catalog.artifact_is_current(first)
    assert not catalog.artifact_is_current(wal_only_change)


def test_catalog_migrates_legacy_source_keys_without_orphaning_references(
    tmp_path: Path,
) -> None:
    path = tmp_path / "catalog.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE artifacts (
            source_key TEXT PRIMARY KEY, source TEXT NOT NULL,
            jenkins_job TEXT NOT NULL, strategy TEXT, runtime_job TEXT,
            kind TEXT NOT NULL, build_number INTEGER, remote_path TEXT NOT NULL,
            remote_size_bytes INTEGER NOT NULL, remote_mtime_ns INTEGER NOT NULL,
            remote_sha256 TEXT, local_path TEXT, local_sha256 TEXT,
            status TEXT NOT NULL, synced_at TEXT, metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE pins (
            pin_id TEXT PRIMARY KEY,
            source_key TEXT NOT NULL REFERENCES artifacts(source_key),
            pinned_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            manifest_json TEXT NOT NULL
        );
        CREATE TABLE artifact_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at TEXT NOT NULL,
            conflict_type TEXT NOT NULL,
            source_key TEXT NOT NULL,
            source TEXT NOT NULL,
            jenkins_job TEXT NOT NULL,
            strategy TEXT,
            runtime_job TEXT,
            kind TEXT NOT NULL,
            remote_path TEXT NOT NULL,
            archive_date TEXT,
            local_path TEXT NOT NULL,
            existing_source_key TEXT,
            existing_remote_path TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'OPEN'
        );
        """
    )
    legacy_key = "legacy-host-agnostic-key"
    connection.execute(
        """
        INSERT INTO artifacts(
            source_key, source, jenkins_job, strategy, runtime_job, kind,
            build_number, remote_path, remote_size_bytes, remote_mtime_ns,
            remote_sha256, local_path, local_sha256, status, synced_at,
            metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            legacy_key,
            "host-a",
            "polybot-king",
            "golden-queen",
            "queen-live",
            "database_live",
            None,
            "/remote/trades.db",
            100,
            200,
            "remote-digest",
            str(tmp_path / "trades.db"),
            "local-digest",
            "SYNCED",
            "2026-08-06T00:00:00+00:00",
            "{}",
        ),
    )
    connection.execute(
        "INSERT INTO pins VALUES (?, ?, ?, ?, ?)",
        (
            "pin-1",
            legacy_key,
            str(tmp_path / "pin.db"),
            "2026-08-06T00:00:00+00:00",
            "{}",
        ),
    )
    connection.execute(
        """
        INSERT INTO artifact_conflicts(
            detected_at, conflict_type, source_key, source, jenkins_job,
            strategy, runtime_job, kind, remote_path, archive_date, local_path,
            existing_source_key, existing_remote_path, details_json, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-08-06T00:00:00+00:00",
            "SOURCE_PATH_COLLISION",
            legacy_key,
            "host-a",
            "polybot-king",
            "golden-queen",
            "queen-live",
            "database_live",
            "/remote/trades.db",
            None,
            str(tmp_path / "trades.db"),
            legacy_key,
            "/remote/trades.db",
            "{}",
            "OPEN",
        ),
    )
    connection.commit()
    connection.close()

    catalog = Catalog(path)
    migrated_key = artifact_source_key(
        source="host-a",
        jenkins_job="polybot-king",
        kind="database_live",
        remote_path="/remote/trades.db",
    )

    with catalog.connect() as migrated:
        columns = {row[1] for row in migrated.execute("PRAGMA table_info(artifacts)")}
        schema_version = migrated.execute(
            "SELECT value FROM catalog_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        key_version = migrated.execute(
            "SELECT value FROM catalog_meta WHERE key = 'source_key_version'"
        ).fetchone()[0]
        artifact_key = migrated.execute("SELECT source_key FROM artifacts").fetchone()[0]
        pin_key = migrated.execute("SELECT source_key FROM pins").fetchone()[0]
        conflict = migrated.execute(
            "SELECT source_key, existing_source_key FROM artifact_conflicts"
        ).fetchone()
    assert "remote_fingerprint" in columns
    assert schema_version == "4"
    assert key_version == "2"
    assert artifact_key == migrated_key
    assert pin_key == migrated_key
    assert tuple(conflict) == (migrated_key, migrated_key)


def test_catalog_coalesces_stale_legacy_duplicate_when_key_version_is_current(
    tmp_path: Path,
) -> None:
    path = tmp_path / "catalog.sqlite3"
    catalog = Catalog(path)
    local = tmp_path / "trades.db"
    local.write_bytes(b"current")
    item = RemoteArtifact(
        kind="database_live",
        remote_path="/remote/trades.db",
        size_bytes=local.stat().st_size,
        mtime_ns=200,
        fingerprint="current-fingerprint",
        jenkins_job="polybot-yellow",
        strategy="golden-cherry",
        runtime_job="default",
        source="macmini",
    )
    catalog.upsert_artifact(
        item,
        source="macmini",
        local_path=local,
        local_sha256="current-digest",
    )

    legacy_key = "stale-v2-key-from-an-older-key-formula"
    with catalog.connect() as connection:
        connection.execute(
            """
            INSERT INTO artifacts(
                source_key, source, jenkins_job, strategy, runtime_job, kind,
                build_number, remote_path, remote_size_bytes, remote_mtime_ns,
                remote_fingerprint, remote_sha256, local_path, local_sha256,
                status, synced_at, metadata_json
            )
            SELECT
                ?, source, jenkins_job, strategy, runtime_job, kind,
                build_number, remote_path, remote_size_bytes - 1, 100,
                NULL, 'stale-remote-digest', local_path, 'stale-local-digest',
                status, '2026-08-08T00:00:00+00:00', metadata_json
            FROM artifacts
            WHERE source_key = ?
            """,
            (legacy_key, item.source_key),
        )

    Catalog(path)

    with catalog.connect() as connection:
        rows = connection.execute(
            """
            SELECT source_key, local_sha256, remote_fingerprint
            FROM artifacts
            WHERE source = ? AND jenkins_job = ? AND kind = ? AND remote_path = ?
            """,
            ("macmini", "polybot-yellow", "database_live", "/remote/trades.db"),
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        (item.source_key, "current-digest", "current-fingerprint")
    ]


def test_catalog_keeps_identical_remote_paths_from_two_hosts_separate(
    tmp_path: Path,
) -> None:
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    remote_path = "/remote/shared/workspace/trades.db"
    artifacts = []
    for source in ("host-a", "host-b"):
        local = tmp_path / source / "trades.db"
        local.parent.mkdir()
        local.write_bytes(source.encode())
        item = RemoteArtifact(
            kind="database_live",
            remote_path=remote_path,
            size_bytes=local.stat().st_size,
            mtime_ns=local.stat().st_mtime_ns,
            jenkins_job="polybot-king",
            strategy="golden-queen",
            runtime_job="queen-live",
            source=source,
        )
        catalog.upsert_artifact(
            item,
            source=source,
            local_path=local,
            local_sha256=f"digest-{source}",
        )
        artifacts.append(item)

    assert artifacts[0].source_key != artifacts[1].source_key
    assert len(catalog.list_artifacts(job="polybot-king")) == 2
    for item in artifacts:
        rows = catalog.list_artifacts(source=item.source, job="polybot-king")
        assert len(rows) == 1
        assert rows[0]["source_key"] == item.source_key
        assert catalog.artifact_is_current(item)


def test_account_epoch_is_upserted(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    catalog.add_account_epoch(
        source="macmini",
        job="polybot-king",
        strategy="golden-queen",
        account_alias="golden-king",
        first_build=1,
    )
    catalog.add_account_epoch(
        source="macmini",
        job="polybot-king",
        strategy="golden-queen",
        account_alias="golden-king-renamed",
        first_build=1,
    )
    with catalog.connect() as connection:
        row = connection.execute("SELECT * FROM account_epochs").fetchone()
    assert row["account_alias"] == "golden-king-renamed"


def test_missing_source_keeps_local_file_and_updates_status(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    local = tmp_path / "local.log"
    item = artifact(local)
    catalog.upsert_artifact(
        item,
        source="macmini",
        local_path=local,
        local_sha256="digest",
    )

    changed = catalog.mark_source_missing(
        source="macmini",
        job="polybot-king",
        observed_paths=set(),
        log_cutoff_ns=0,
    )

    assert changed == 1
    assert local.is_file()
    assert catalog.get_artifact(item.source_key)["status"] == "SOURCE_MISSING"

    assert catalog.artifact_is_current(item)
    assert catalog.get_artifact(item.source_key)["status"] == "SYNCED"


def test_date_scoped_scan_does_not_mark_other_research_shards_missing(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    items = []
    for day in ("20260804", "20260805"):
        local = tmp_path / f"local-{day}.db"
        local.write_bytes(b"sqlite-placeholder")
        item = RemoteArtifact(
            kind="database_research_archive",
            remote_path=f"/remote/trades_sim_{day}.db",
            size_bytes=local.stat().st_size,
            mtime_ns=local.stat().st_mtime_ns,
            jenkins_job="polybot-king",
            source="macmini",
            strategy="golden-queen",
            runtime_job="queen-research",
            canonical=False,
            archive_date=f"{day[:4]}-{day[4:6]}-{day[6:]}",
        )
        catalog.upsert_artifact(
            item,
            source="macmini",
            local_path=local,
            local_sha256="digest",
        )
        items.append(item)

    changed = catalog.mark_source_missing(
        source="macmini",
        job="polybot-king",
        observed_paths=set(),
        log_cutoff_ns=0,
        archive_from_date=date(2026, 8, 5),
        archive_to_date=date(2026, 8, 5),
    )

    assert changed == 1
    assert catalog.get_artifact(items[0].source_key)["status"] == "SYNCED"
    assert catalog.get_artifact(items[1].source_key)["status"] == "SOURCE_MISSING"


def test_historical_scan_does_not_mark_current_research_shard_missing(
    tmp_path: Path,
) -> None:
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    local = tmp_path / "trades_sim.db"
    local.write_bytes(b"sqlite-placeholder")
    item = RemoteArtifact(
        kind="database_sim",
        remote_path="/remote/golden-pomegranate/data/research/trades_sim.db",
        size_bytes=local.stat().st_size,
        mtime_ns=local.stat().st_mtime_ns,
        jenkins_job="polybot-pomegranate",
        source="macmini",
        strategy="golden-pomegranate",
        runtime_job="research",
        mode="sim",
        data_contract="research-full-v1",
    )
    catalog.upsert_artifact(
        item,
        source="macmini",
        local_path=local,
        local_sha256="digest",
        metadata={"data_contract": "research-full-v1"},
    )

    changed = catalog.mark_source_missing(
        source="macmini",
        job="polybot-pomegranate",
        observed_paths=set(),
        log_cutoff_ns=0,
        archive_from_date=date(1999, 1, 1),
        archive_to_date=date(1999, 1, 1),
    )

    assert changed == 0
    assert catalog.get_artifact(item.source_key)["status"] == "SYNCED"


def test_date_scoped_scan_never_marks_omitted_canonical_databases_missing(
    tmp_path: Path,
) -> None:
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    items = []
    for name, kind in (("trades.db", "database_live"), ("trades_sim.db", "database_sim")):
        local = tmp_path / name
        local.write_bytes(b"sqlite-placeholder")
        item = RemoteArtifact(
            kind=kind,
            remote_path=f"/remote/golden-queen/data/default/{name}",
            size_bytes=local.stat().st_size,
            mtime_ns=local.stat().st_mtime_ns,
            jenkins_job="polybot-queen",
            source="macmini",
            strategy="golden-queen",
            runtime_job="default",
        )
        catalog.upsert_artifact(
            item,
            source="macmini",
            local_path=local,
            local_sha256="digest",
        )
        items.append(item)

    changed = catalog.mark_source_missing(
        source="macmini",
        job="polybot-queen",
        observed_paths=set(),
        log_cutoff_ns=0,
        archive_from_date=date(2026, 8, 5),
        archive_to_date=date(2026, 8, 5),
        include_canonical_databases=False,
    )

    assert changed == 0
    assert all(catalog.get_artifact(item.source_key)["status"] == "SYNCED" for item in items)
