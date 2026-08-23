from __future__ import annotations

from pathlib import Path

import pytest

from polybot.config import (
    CANONICAL_JOBS,
    FROZEN_EXPERIMENT_END,
    FROZEN_EXPERIMENT_START,
    PROJECT_ROOT,
    assert_no_credentials,
    load_config,
)
from polybot.source_digest import (
    ACTIVE_DATA_CONTRACT,
    ACTIVE_MANIFEST,
    ACTIVE_PREREGISTRATION,
    SOURCE_PATHS,
)


CONFIG = PROJECT_ROOT / "config.yaml"


def _clear(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("POLYBOT_") or key.startswith("POLYMARKET_") or key.startswith("CLOB_"):
            monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("job,expected", CANONICAL_JOBS.items())
def test_canonical_job_resolves_hash_shard(monkeypatch, job, expected):
    _clear(monkeypatch)
    config = load_config(CONFIG, job, simulation_mode=True)
    assert config.simulation_mode is True
    assert config.trading.lifecycle_mode == "archive_only"
    assert config.trading.experiment.shard_index == expected[0]
    assert config.trading.experiment.cadence_offset_minute == expected[1]
    assert config.trading.experiment.shard_count == 3
    assert config.trading.experiment.start_utc == FROZEN_EXPERIMENT_START
    assert config.trading.experiment.end_utc == FROZEN_EXPERIMENT_END
    assert config.trading.data_contract == "queue-echo-v3"
    assert config.trading.runtime.cooperative_cycle_budget_seconds == 225
    assert config.trading.runtime.hard_cycle_limit_seconds == 240
    assert config.trading.runtime.network_stop_margin_seconds == 30
    assert "-v3-" in config.job_name
    assert config.db_path.parent.name == config.job_name
    assert len(config.config_hash) == 64
    assert len(config.trading.strategy_source_digest) == 64


def test_credential_presence_is_rejected_even_when_empty(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "")
    with pytest.raises(ValueError, match="credential-bearing"):
        assert_no_credentials()
    with pytest.raises(ValueError, match="credential-bearing"):
        load_config(CONFIG, "raspberry-do-v3-shard-0")


def test_unknown_polybot_environment_is_rejected(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("POLYBOT_UNREGISTERED_TUNING", "1")
    with pytest.raises(ValueError, match="unknown POLYBOT"):
        load_config(CONFIG, "raspberry-do-v3-shard-0")


def test_shard_and_job_must_match(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("POLYBOT_SHARD_INDEX", "2")
    with pytest.raises(ValueError, match="hash shard identity"):
        load_config(CONFIG, "raspberry-do-v3-shard-0")


def test_live_contradiction_is_rejected(monkeypatch):
    _clear(monkeypatch)
    with pytest.raises(ValueError, match="contradicts"):
        load_config(CONFIG, "raspberry-do-v3-shard-0", simulation_mode=False)


def test_experiment_window_override_cannot_diverge_from_preregistration(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("POLYBOT_EXPERIMENT_START_UTC", "2026-08-23T20:05:00Z")
    monkeypatch.setenv("POLYBOT_EXPERIMENT_END_UTC", "2026-09-22T20:05:00Z")
    with pytest.raises(ValueError, match="active frozen preregistration"):
        load_config(CONFIG, "raspberry-do-v3-shard-0")


def test_source_digest_covers_collection_and_analysis_runtime():
    assert {
        "pyproject.toml",
        "uv.lock",
        ACTIVE_PREREGISTRATION,
        ACTIVE_DATA_CONTRACT,
        ACTIVE_MANIFEST,
        "README.md",
        "OPERATIONS.md",
        "scripts/analyze_experiment.py",
        "scripts/verify_external_workspace.py",
        "src/polybot/main.py",
        "src/polybot/bot.py",
        "src/polybot/run_audit.py",
        "src/polybot/utils/retry.py",
    }.issubset(SOURCE_PATHS)


def test_external_v2_runtime_names_are_rejected_and_cannot_select_v2_paths(monkeypatch):
    _clear(monkeypatch)
    with pytest.raises(ValueError, match="job must be one of"):
        load_config(CONFIG, "raspberry-do-shard-0")
