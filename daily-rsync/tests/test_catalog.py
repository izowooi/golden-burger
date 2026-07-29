from __future__ import annotations

from pathlib import Path

from daily_rsync.catalog import Catalog
from daily_rsync.models import RemoteArtifact


def artifact(path: Path) -> RemoteArtifact:
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
    )


def test_catalog_detects_unchanged_artifact(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    local = tmp_path / "local.log"
    item = artifact(local)
    assert not catalog.artifact_is_current(item)

    catalog.upsert_artifact(
        item,
        source="test",
        local_path=local,
        local_sha256="digest",
    )

    assert catalog.artifact_is_current(item)
    rows = catalog.list_artifacts(job="polybot-king", strategy="golden-queen")
    assert len(rows) == 1
    assert rows[0]["runtime_job"] == "queen-live-12h"


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
