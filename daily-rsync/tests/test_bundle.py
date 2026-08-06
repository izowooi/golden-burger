from __future__ import annotations

import gzip
import hashlib
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from daily_rsync import bundle as bundle_module
from daily_rsync.bundle import create_bundle
from daily_rsync.catalog import Catalog
from daily_rsync.models import RemoteArtifact


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def add_research_contract(path: Path, *, database_utc_date: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE collection_contracts (
                contract_name TEXT NOT NULL,
                database_utc_date TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO collection_contracts VALUES (?, ?)",
            ("research-full-v1", database_utc_date),
        )


def add_database(
    app_config,
    *,
    name: str = "source.db",
    local_sha256: str | None = None,
) -> tuple[Catalog, RemoteArtifact, Path]:
    catalog = Catalog(app_config.catalog_path)
    database = app_config.data_root / name
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('ok')")
    payload = database.read_bytes()
    artifact = RemoteArtifact(
        kind="database_live",
        remote_path=f"/remote/{name}",
        size_bytes=len(payload),
        mtime_ns=database.stat().st_mtime_ns,
        jenkins_job="polybot-king",
        source="test-host",
        strategy="golden-queen",
        runtime_job="queen-live",
    )
    catalog.upsert_artifact(
        artifact,
        source="test-host",
        local_path=database,
        local_sha256=local_sha256 or digest(payload),
    )
    return catalog, artifact, database


def add_research_archive(
    app_config,
    *,
    archive_day: date,
    runtime_job: str,
) -> tuple[Catalog, RemoteArtifact, Path]:
    catalog = Catalog(app_config.catalog_path)
    label = archive_day.strftime("%Y%m%d")
    database = app_config.data_root / f"{runtime_job}-{label}.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT)")
    add_research_contract(database, database_utc_date=archive_day.isoformat())
    payload = database.read_bytes()
    artifact = RemoteArtifact(
        kind="database_research_archive",
        remote_path=f"/remote/{runtime_job}/trades_sim_{label}.db",
        size_bytes=len(payload),
        mtime_ns=database.stat().st_mtime_ns,
        jenkins_job="polybot-pomegranate",
        source="test-host",
        strategy="golden-pomegranate",
        runtime_job=runtime_job,
        canonical=False,
        archive_date=archive_day.isoformat(),
        mode="sim",
        data_contract="research-full-v1",
        database_utc_date=archive_day.isoformat(),
    )
    catalog.upsert_artifact(
        artifact,
        source="test-host",
        local_path=database,
        local_sha256=digest(payload),
        metadata={
            "archive_date": archive_day.isoformat(),
            "data_contract": "research-full-v1",
            "database_utc_date": archive_day.isoformat(),
        },
    )
    return catalog, artifact, database


def test_bundle_contains_database_and_expanded_logs(app_config) -> None:
    catalog = Catalog(app_config.catalog_path)
    database = app_config.data_root / "source.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE test(id INTEGER)")
    connection.commit()
    connection.close()
    database_bytes = database.read_bytes()
    db_artifact = RemoteArtifact(
        kind="database_live",
        remote_path="/remote/trades.db",
        size_bytes=len(database_bytes),
        mtime_ns=database.stat().st_mtime_ns,
        jenkins_job="polybot-king",
        source="test-host",
        strategy="golden-queen",
        runtime_job="queen-live-12h",
    )
    catalog.upsert_artifact(
        db_artifact,
        source="test-host",
        local_path=database,
        local_sha256=digest(database_bytes),
    )
    for day in ("20260728", "20260729"):
        archive = app_config.data_root / f"trades_sim_{day}.db"
        connection = sqlite3.connect(archive)
        connection.execute("CREATE TABLE research(id INTEGER)")
        connection.commit()
        connection.close()
        archive_date = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        add_research_contract(archive, database_utc_date=archive_date)
        archive_bytes = archive.read_bytes()
        archive_artifact = RemoteArtifact(
            kind="database_research_archive",
            remote_path=f"/remote/trades_sim_{day}.db",
            size_bytes=len(archive_bytes),
            mtime_ns=archive.stat().st_mtime_ns,
            jenkins_job="polybot-king",
            source="test-host",
            strategy="golden-queen",
            runtime_job="queen-live-12h",
            canonical=False,
            archive_date=archive_date,
            data_contract="research-full-v1",
            database_utc_date=archive_date,
        )
        catalog.upsert_artifact(
            archive_artifact,
            source="test-host",
            local_path=archive,
            local_sha256=digest(archive_bytes),
            metadata={
                "canonical": False,
                "archive_date": archive_artifact.archive_date,
                "data_contract": "research-full-v1",
                "database_utc_date": archive_date,
            },
        )

    active_research = app_config.data_root / "current-research.db"
    with sqlite3.connect(active_research) as connection:
        connection.execute("CREATE TABLE current_research(id INTEGER)")
    add_research_contract(active_research, database_utc_date="2026-08-04")
    active_bytes = active_research.read_bytes()
    active_artifact = RemoteArtifact(
        kind="database_sim",
        remote_path="/remote/golden-pomegranate/data/research/trades_sim.db",
        size_bytes=len(active_bytes),
        mtime_ns=active_research.stat().st_mtime_ns,
        jenkins_job="polybot-king",
        source="test-host",
        strategy="golden-queen",
        runtime_job="queen-live-12h",
        mode="sim",
        data_contract="research-full-v1",
        database_utc_date="2026-08-04",
    )
    catalog.upsert_artifact(
        active_artifact,
        source="test-host",
        local_path=active_research,
        local_sha256=digest(active_bytes),
        metadata={
            "mode": "sim",
            "data_contract": "research-full-v1",
            "database_utc_date": "2026-08-04",
        },
    )

    log_content = b"secret-bearing exact log\n"
    compressed_log = app_config.data_root / "725.log.gz"
    with gzip.open(compressed_log, "wb") as handle:
        handle.write(log_content)
    log_artifact = RemoteArtifact(
        kind="jenkins_console",
        remote_path="/remote/builds/725/log",
        size_bytes=len(log_content),
        mtime_ns=1,
        jenkins_job="polybot-king",
        source="test-host",
        strategy="golden-queen",
        build_number=725,
        completed_at="2026-07-29T00:00:00+00:00",
    )
    catalog.upsert_artifact(
        log_artifact,
        source="test-host",
        local_path=compressed_log,
        local_sha256=digest(log_content),
        metadata={"completed_at": log_artifact.completed_at},
    )

    bundle = create_bundle(
        app_config,
        job="polybot-king",
        strategy="golden-queen",
        from_date=date(2026, 7, 29),
        to_date=date(2026, 7, 29),
    )

    assert (bundle / "databases" / "source.db").is_file()
    assert (bundle / "databases" / "trades_sim_20260729.db").is_file()
    assert not (bundle / "databases" / "trades_sim_20260728.db").exists()
    assert not (bundle / "databases" / "current-research.db").exists()
    assert (bundle / "logs" / "jenkins-725.log").read_bytes() == log_content
    assert (bundle / "manifest.json").is_file()


def test_research_bundle_refuses_partial_requested_date_coverage(app_config) -> None:
    catalog = Catalog(app_config.catalog_path)
    archive = app_config.data_root / "trades_sim_20260804.db"
    with sqlite3.connect(archive) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT)")
    add_research_contract(archive, database_utc_date="2026-08-04")
    payload = archive.read_bytes()
    artifact = RemoteArtifact(
        kind="database_research_archive",
        remote_path="/remote/trades_sim_20260804.db",
        size_bytes=len(payload),
        mtime_ns=archive.stat().st_mtime_ns,
        jenkins_job="polybot-pomegranate",
        source="test-host",
        strategy="golden-pomegranate",
        runtime_job="research",
        canonical=False,
        archive_date="2026-08-04",
        mode="sim",
        data_contract="research-full-v1",
        database_utc_date="2026-08-04",
    )
    catalog.upsert_artifact(
        artifact,
        source="test-host",
        local_path=archive,
        local_sha256=digest(payload),
        metadata={
            "archive_date": "2026-08-04",
            "data_contract": "research-full-v1",
            "database_utc_date": "2026-08-04",
        },
    )

    with pytest.raises(RuntimeError, match="missing UTC date.*2026-08-05"):
        create_bundle(
            app_config,
            job="polybot-pomegranate",
            strategy="golden-pomegranate",
            from_date=date(2026, 8, 4),
            to_date=date(2026, 8, 5),
        )


def test_bundle_refuses_catalog_checksum_mismatch(app_config) -> None:
    add_database(app_config, local_sha256=digest(b"a stale catalog digest"))

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        create_bundle(
            app_config,
            job="polybot-king",
            strategy="golden-queen",
            from_date=date(2026, 8, 4),
            to_date=date(2026, 8, 4),
        )


def test_bundle_refuses_database_that_fails_quick_check(app_config, monkeypatch) -> None:
    add_database(app_config)
    monkeypatch.setattr(
        bundle_module,
        "_quick_check",
        lambda _path: ["database disk image is malformed"],
    )

    with pytest.raises(RuntimeError, match="quick_check failed"):
        create_bundle(
            app_config,
            job="polybot-king",
            strategy="golden-queen",
            from_date=date(2026, 8, 4),
            to_date=date(2026, 8, 4),
        )


def test_bundle_refuses_open_catalog_conflict(app_config) -> None:
    catalog, artifact, database = add_database(app_config)
    catalog.record_conflict(
        conflict_type="PROVENANCE_CHANGED",
        source="test-host",
        artifact=artifact,
        local_path=database,
        existing=catalog.get_artifact(artifact.source_key),
    )

    with pytest.raises(RuntimeError, match="unresolved artifact conflict"):
        create_bundle(
            app_config,
            job="polybot-king",
            strategy="golden-queen",
            from_date=date(2026, 8, 4),
            to_date=date(2026, 8, 4),
        )


def test_research_bundle_refuses_dates_split_across_runtime_jobs(app_config) -> None:
    add_research_archive(
        app_config,
        archive_day=date(2026, 8, 4),
        runtime_job="research-a",
    )
    add_research_archive(
        app_config,
        archive_day=date(2026, 8, 5),
        runtime_job="research-b",
    )

    with pytest.raises(RuntimeError, match="split across runtime jobs"):
        create_bundle(
            app_config,
            job="polybot-pomegranate",
            strategy="golden-pomegranate",
            from_date=date(2026, 8, 4),
            to_date=date(2026, 8, 5),
        )
