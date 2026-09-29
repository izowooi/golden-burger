from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from polybot_observability.market_data_migrate import migrate_public_bodies
from polybot_observability.market_data_refs import PayloadReferences

from daily_rsync.catalog import Catalog
from daily_rsync.models import RemoteArtifact
from daily_rsync.public_payloads import (
    closure_sidecar,
    local_payload_writer,
    synchronize_database_closure,
    write_closure_descriptor,
)
from daily_rsync.sports import SportsStore
from daily_rsync.sync import sha256

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tools"))
sys.path.insert(0, str(REPOSITORY / "tools" / "tests"))


def test_ui_build_uses_verified_scoped_public_receipts(app_config, monkeypatch):
    from test_conservative_sports_grid import raw_fixture_pin

    config = replace(app_config, project_root=REPOSITORY / "daily-rsync")
    source, fixture = raw_fixture_pin(config.data_root)
    fixture.c.close()
    database = Path(source["local_path"])
    artifact = RemoteArtifact(
        "database_sim", "/remote/data/shadow/trades_sim.db", database.stat().st_size, 1,
        "polybot-grey", strategy="golden-peach", runtime_job="shadow", source=config.ssh_host,
    )
    catalog = Catalog(config.catalog_path)

    def record():
        catalog.upsert_artifact(
            artifact, source=config.ssh_host, local_path=database, local_sha256=sha256(database),
            remote_sha256=sha256(database),
        )

    record()
    store = SportsStore(config, catalog)
    monkeypatch.setenv("PUBLIC_MARKET_DATA_DB", str(config.data_root / "wrong.db"))
    start, end = "2026-09-07T00:00:00Z", "2026-09-08T00:00:00Z"
    first = store.build([artifact.source_key], start, end, lambda _: None)
    assert first["matches"] > 0
    first_version = store._version()
    expected = {path.name: path.read_bytes() for path in (first_version / "events").iterdir()}

    destination = database.with_name("shared-copy.db")
    with local_payload_writer(config) as writer:
        migrate_public_bodies(
            database, destination, strategy="golden-peach", source_sha256=sha256(database),
            references=PayloadReferences(reader=writer, writer=writer),
        )
    os.replace(destination, database)
    descriptor = synchronize_database_closure(
        config, object(), database, strategy="golden-peach", source_key=artifact.source_key,
        database_sha256=sha256(database), ensure_capacity=lambda _: None,
    )
    write_closure_descriptor(config, database, descriptor)
    record()
    second = store.build([artifact.source_key], start, end, lambda _: None)
    actual = {path.name: path.read_bytes() for path in (store._version() / "events").iterdir()}
    assert second == first
    assert actual == expected
    assert os.environ["PUBLIC_MARKET_DATA_DB"].endswith("wrong.db")

    published = (store.root / "current.json").read_bytes()
    closure_sidecar(database).unlink()
    with pytest.raises(RuntimeError, match="attestation"):
        store.build([artifact.source_key], start, end, lambda _: None)
    assert (store.root / "current.json").read_bytes() == published
    assert len(json.loads((store._version() / "index.json").read_text())["matches"]) > 0
