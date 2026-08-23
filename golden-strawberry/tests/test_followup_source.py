from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from polybot.db.followup_repository import FollowupRepository
from polybot.run_audit import ResearchRunAudit
from polybot.v1_source import V1SourceReader
from tests.followup_support import build_v1_handoff


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_v1_seed_read_is_byte_schema_and_sidecar_immutable(config, followup_config):
    build_v1_handoff(config)
    source = config.db_path
    before_sha = _sha256(source)
    before_size = source.stat().st_size
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as connection:
        before_schema = int(connection.execute("PRAGMA schema_version").fetchone()[0])

    snapshot = V1SourceReader(followup_config.trading.v1_source).capture()

    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as connection:
        after_schema = int(connection.execute("PRAGMA schema_version").fetchone()[0])
    assert _sha256(source) == before_sha
    assert source.stat().st_size == before_size
    assert after_schema == before_schema
    assert snapshot.anchor["source_data_contract"] == "last-mile-clob-v1"
    assert all(
        not source.with_name(source.name + suffix).exists()
        for suffix in ("-journal", "-wal", "-shm")
    )


def test_seed_and_anchor_hash_are_deterministic(config, followup_config):
    build_v1_handoff(config)
    reader = V1SourceReader(followup_config.trading.v1_source)
    first = reader.capture()
    second = reader.capture()
    assert first.anchor_sha256 == second.anchor_sha256
    assert first.episodes == second.episodes
    assert first.condition_statuses == second.condition_statuses
    assert first.threshold_events == second.threshold_events
    assert len(first.episodes) == 4

    repository = FollowupRepository(followup_config.db_path)
    repository.initialize(followup_config)
    persisted = repository.ensure_seed(first)
    assert repository.ensure_seed(second)["anchor_sha256"] == first.anchor_sha256
    assert persisted["anchor_sha256"] == first.anchor_sha256
    assert reader.validate_stored_anchor(persisted)["anchor_sha256"] == first.anchor_sha256


def test_threshold_seed_round_trip_preserves_all_source_fields(
    config, followup_config
):
    build_v1_handoff(config)
    with sqlite3.connect(config.db_path) as connection:
        connection.row_factory = sqlite3.Row
        path = connection.execute(
            """
            SELECT path_observation_id,episode_id,sweep_id,observed_at,
                   exit_bid_vwap,prior_executable_bid_vwap
            FROM episode_path_observations
            WHERE path_status='EXECUTABLE' AND exit_bid_vwap IS NOT NULL
            ORDER BY observed_at,path_observation_id LIMIT 1
            """
        ).fetchone()
        assert path is not None
        connection.execute(
            """
            INSERT INTO episode_threshold_events(
                threshold_event_id,episode_id,path_observation_id,sweep_id,
                event_kind,threshold,observed_at,executable_bid_vwap,
                prior_executable_bid_vwap,interval_censored,conservative_priority
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "threshold-round-trip",
                path["episode_id"],
                path["path_observation_id"],
                path["sweep_id"],
                "STOP",
                0.123456,
                path["observed_at"],
                path["exit_bid_vwap"],
                path["prior_executable_bid_vwap"],
                1,
                7,
            ),
        )
        connection.commit()

    snapshot = V1SourceReader(followup_config.trading.v1_source).capture()
    repository = FollowupRepository(followup_config.db_path)
    repository.initialize(followup_config)
    anchor = repository.ensure_seed(snapshot)
    integrity = repository.verify_seed_integrity(anchor)

    assert integrity["healthy"] is True
    with repository.read_connect() as connection:
        persisted = connection.execute(
            """
            SELECT path_observation_id,sweep_id,interval_censored,
                   conservative_priority
            FROM imported_threshold_events
            WHERE source_threshold_event_id='threshold-round-trip'
            """
        ).fetchone()
    assert persisted is not None
    assert persisted["path_observation_id"] == path["path_observation_id"]
    assert persisted["sweep_id"] == path["sweep_id"]
    assert persisted["interval_censored"] == 1
    assert persisted["conservative_priority"] == 7


def test_pinned_anchor_validation_does_not_rescan_seed_rows(
    config, followup_config, monkeypatch
):
    build_v1_handoff(config)
    reader = V1SourceReader(followup_config.trading.v1_source)
    snapshot = reader.capture()
    repository = FollowupRepository(followup_config.db_path)
    repository.initialize(followup_config)
    persisted = repository.ensure_seed(snapshot)

    def forbidden(*args, **kwargs):
        raise AssertionError("pinned validation rescanned v1 episode evidence")

    monkeypatch.setattr(reader, "_latest_paths", forbidden)
    monkeypatch.setattr(reader, "_threshold_rows", forbidden)
    monkeypatch.setattr(reader, "_resolved_conditions", forbidden)
    validated = reader.validate_stored_anchor(persisted)
    assert validated["source_sweep_id"] == snapshot.anchor["source_sweep_id"]


def test_v1_new_successful_sweep_fails_anchor_closed(config, followup_config):
    _, collector, _, _ = build_v1_handoff(config)
    reader = V1SourceReader(followup_config.trading.v1_source)
    frozen = reader.capture()
    repository = FollowupRepository(followup_config.db_path)
    repository.initialize(followup_config)
    repository.ensure_seed(frozen)

    source_repository = collector.repository
    audit = ResearchRunAudit.start(config, repository=source_repository)
    summary = collector.run_cycle(audit.run_id)
    audit.succeed(summary)
    drifted = reader.capture()
    with pytest.raises(RuntimeError, match="v1 source anchor drift"):
        repository.ensure_seed(drifted)
    with pytest.raises(RuntimeError, match="v1 source anchor drift"):
        reader.validate_stored_anchor(repository.stored_anchor())


def test_v1_sidecar_fails_before_read(config, followup_config):
    build_v1_handoff(config)
    sidecar = config.db_path.with_name(config.db_path.name + "-wal")
    sidecar.write_bytes(b"unexpected")
    with pytest.raises(RuntimeError, match="sidecars"):
        V1SourceReader(followup_config.trading.v1_source).capture()


def test_seed_publication_rolls_back_atomically(
    config, followup_config, monkeypatch
):
    build_v1_handoff(config)
    snapshot = V1SourceReader(followup_config.trading.v1_source).capture()
    repository = FollowupRepository(followup_config.db_path)
    repository.initialize(followup_config)
    original = repository._insert_many

    def fail_on_conditions(connection, table, rows):
        if table == "imported_condition_status":
            raise RuntimeError("injected seed failure")
        return original(connection, table, rows)

    monkeypatch.setattr(repository, "_insert_many", fail_on_conditions)
    with pytest.raises(RuntimeError, match="injected seed failure"):
        repository.ensure_seed(snapshot)
    with repository.read_connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_anchors").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM imported_episodes").fetchone()[0] == 0
