from __future__ import annotations

import gzip
import hashlib
import sqlite3
from datetime import date

from daily_rsync.bundle import create_bundle
from daily_rsync.catalog import Catalog
from daily_rsync.models import RemoteArtifact


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
        strategy="golden-queen",
        runtime_job="queen-live-12h",
    )
    catalog.upsert_artifact(
        db_artifact,
        source="test-host",
        local_path=database,
        local_sha256=digest(database_bytes),
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
    assert (bundle / "logs" / "jenkins-725.log").read_bytes() == log_content
    assert (bundle / "manifest.json").is_file()
