from __future__ import annotations

from pathlib import Path

import pytest

from polybot.config import (
    CANONICAL_JOB,
    ENTRY_THRESHOLDS,
    STOP_LEVELS,
    assert_no_credentials,
    load_config,
)


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_config_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYBOT_LIFECYCLE_MODE", "archive_only")
    monkeypatch.setenv("POLYBOT_SIMULATION_MODE", "true")
    config = load_config(ROOT / "config.yaml", CANONICAL_JOB, simulation_mode=True)
    assert config.simulation_mode is True
    assert config.trading.experiment.entry_thresholds == ENTRY_THRESHOLDS
    assert config.trading.experiment.stop_levels == STOP_LEVELS
    assert config.trading.gamma.page_size == 500
    assert config.trading.gamma.max_pages == 4
    assert config.trading.experiment.fee_rate_fallback == 0.05


@pytest.mark.parametrize(
    "key",
    ["POLYMARKET_PRIVATE_KEY", "POLYMARKET_FUNDER_ADDRESS", "POLYMARKET_SIGNATURE_TYPE", "CLOB_API_KEY"],
)
def test_credentials_are_rejected_even_when_empty(key: str) -> None:
    with pytest.raises(ValueError, match="credential-bearing"):
        assert_no_credentials({key: ""})


def test_unknown_polybot_override_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYBOT_BUY_AMOUNT", "5")
    with pytest.raises(ValueError, match="unknown POLYBOT"):
        load_config(ROOT / "config.yaml")


def test_live_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="contradicts"):
        load_config(ROOT / "config.yaml", simulation_mode=False)
