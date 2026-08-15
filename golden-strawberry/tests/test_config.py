from __future__ import annotations

import os

import pytest
import yaml

from polybot.config import (
    CANONICAL_JOB,
    DATA_CONTRACT,
    ENTRY_THRESHOLDS,
    FROZEN_ENTRY_END,
    FROZEN_ENTRY_START,
    FROZEN_FOLLOWUP_END,
    _CREDENTIAL_ENV_KEYS,
    assert_no_credentials,
    load_config,
)
from polybot.source_digest import SOURCE_PATHS, compute_strategy_source_digest


def _clean(monkeypatch):
    for key in list(os.environ):
        if key.startswith("POLYBOT_") or key in _CREDENTIAL_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)


def test_frozen_config_contract(project_root, monkeypatch):
    _clean(monkeypatch)
    config = load_config(project_root / "config.yaml")
    assert config.job_name == CANONICAL_JOB
    assert config.trading.data_contract == DATA_CONTRACT
    assert config.trading.cadence_minutes == 10
    assert config.trading.gamma.page_size == 100
    assert config.trading.gamma.max_pages == 500
    assert config.trading.gamma.min_liquidity == 0
    assert config.trading.gamma.min_total_volume == 0
    assert config.trading.experiment.entry_thresholds == ENTRY_THRESHOLDS
    assert config.trading.experiment.entry_start_utc == FROZEN_ENTRY_START
    assert config.trading.experiment.entry_end_utc == FROZEN_ENTRY_END
    assert config.trading.experiment.followup_end_utc == FROZEN_FOLLOWUP_END
    assert config.trading.experiment.primary_entry_threshold == 0.95
    assert config.trading.experiment.primary_stop_threshold == 0.85
    assert config.trading.experiment.base_cost_stress_bps == 10.4
    assert config.trading.experiment.severe_cost_stress_bps == 72.5
    assert config.db_path.name == "trades_sim.db"
    assert config.db_path.parent.name == "strawberry-shadow-one"


def test_credential_deny_list_is_exact():
    assert _CREDENTIAL_ENV_KEYS == {
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_FUNDER_ADDRESS",
        "POLYMARKET_SIGNATURE_TYPE",
        "POLYMARKET_API_KEY",
        "POLYMARKET_API_SECRET",
        "POLYMARKET_API_PASSPHRASE",
        "CLOB_API_KEY",
        "CLOB_SECRET",
        "CLOB_PASSPHRASE",
    }


@pytest.mark.parametrize("key", sorted(_CREDENTIAL_ENV_KEYS))
def test_every_credential_key_fails_even_when_empty(key):
    with pytest.raises(ValueError, match=key):
        assert_no_credentials({key: ""})


def test_unknown_polybot_environment_key_fails(project_root, monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("POLYBOT_UNREGISTERED", "1")
    with pytest.raises(ValueError, match="unknown POLYBOT"):
        load_config(project_root / "config.yaml")


def test_only_canonical_job_is_accepted(project_root, monkeypatch):
    _clean(monkeypatch)
    with pytest.raises(ValueError, match=CANONICAL_JOB):
        load_config(project_root / "config.yaml", "strawberry-other")


def test_config_rejects_loosened_source_filter(project_root, tmp_path, monkeypatch):
    _clean(monkeypatch)
    raw = yaml.safe_load((project_root / "config.yaml").read_text(encoding="utf-8"))
    raw["trading"]["gamma"]["min_liquidity"] = 1
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="server filters"):
        load_config(path)


def test_config_rejects_live_resolution(project_root, monkeypatch):
    _clean(monkeypatch)
    with pytest.raises(ValueError, match="contradicts"):
        load_config(project_root / "config.yaml", simulation_mode=False)


def test_source_digest_includes_runtime_and_analyzer(project_root, monkeypatch):
    _clean(monkeypatch)
    assert "scripts/analyze_experiment.py" in SOURCE_PATHS
    assert "src/polybot/analyzer.py" in SOURCE_PATHS
    assert "src/polybot/collector.py" in SOURCE_PATHS
    assert len(compute_strategy_source_digest(project_root)) == 64
