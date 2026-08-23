from __future__ import annotations

import hashlib

import pytest
import yaml

from polybot.config import PROJECT_ROOT
from polybot.followup_config import (
    FOLLOWUP_CANONICAL_JOB,
    FOLLOWUP_DATA_CONTRACT,
    load_followup_config,
)


def test_followup_config_pins_v2a_epoch_source_batches_and_deadlines(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("POLYBOT_"):
            monkeypatch.delenv(key, raising=False)
    config = load_followup_config(PROJECT_ROOT / "config.followup-v2a.yaml")
    assert config.job_name == FOLLOWUP_CANONICAL_JOB
    assert config.trading.data_contract == FOLLOWUP_DATA_CONTRACT
    assert config.db_path.name == "trades_sim.db"
    assert config.db_path.parent.name == FOLLOWUP_CANONICAL_JOB
    assert config.trading.v1_source.db_path.parent.name == "strawberry-shadow-one"
    assert config.trading.orderbook.batch_token_limit == 250
    assert config.trading.gamma.resolution_batch_size == 50
    assert config.trading.cadence_minutes == 10
    assert config.trading.runtime.network_cycle_deadline_seconds == 450
    assert config.trading.runtime.pinned_fast_hard_sla_seconds == 480
    assert config.trading.runtime.full_seed_budget_seconds == 1800
    assert (
        config.trading.runtime.network_cycle_deadline_seconds
        < config.trading.runtime.pinned_fast_hard_sla_seconds
        < config.trading.cadence_minutes * 60
    )


def test_followup_config_rejects_unknown_shape(tmp_path, monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("POLYBOT_"):
            monkeypatch.delenv(key, raising=False)
    source = yaml.safe_load(
        (PROJECT_ROOT / "config.followup-v2a.yaml").read_text(encoding="utf-8")
    )
    source["followup"]["sampling"] = {"forbidden": True}
    path = tmp_path / "bad-followup.yaml"
    path.write_text(yaml.safe_dump(source), encoding="utf-8")
    with pytest.raises(ValueError, match="keys changed"):
        load_followup_config(path)


def test_followup_v2a_preregistration_manifest_matches():
    frozen = PROJECT_ROOT / "research/frozen-2026-08-24-followup-v2a"
    expected, filename = (frozen / "MANIFEST.sha256").read_text(
        encoding="utf-8"
    ).split()
    assert filename == "PREREGISTRATION.md"
    observed = hashlib.sha256((frozen / filename).read_bytes()).hexdigest()
    assert observed == expected
