from __future__ import annotations

import sqlite3

import pytest

from polybot.db.repository import ResearchRepository


def test_schema_quick_check_and_append_only(tmp_path) -> None:
    repository = ResearchRepository(tmp_path / "trades_sim.db", busy_timeout_ms=1000, data_contract="sports-resolution-paired-v1")
    assert repository.quick_check() == "ok"
    repository.record_config({
        "config_hash": "c", "strategy_source_digest": "s", "preregistration_sha256": "p",
        "job_name": "black-shadow-paired", "mode": "sim", "config_json": "{}",
        "first_seen_at": "2026-08-20T00:00:00Z",
    })
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with repository.connect() as connection:
            connection.execute("UPDATE research_config_versions SET mode='live'")


def test_payload_is_compressed_deterministically(tmp_path) -> None:
    repository = ResearchRepository(tmp_path / "trades_sim.db", busy_timeout_ms=1000, data_contract="sports-resolution-paired-v1")
    row = repository.payload_row(run_id="r", kind="X", request_id="q", observed_at="2026-08-20T00:00:00Z", raw=b"x" * 1000)
    assert row["gzip_bytes"] < row["raw_bytes"]
    assert len(row["sha256"]) == 64
