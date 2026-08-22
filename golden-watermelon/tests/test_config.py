from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from polybot.config import (
    ENTRY_THRESHOLDS,
    FROZEN_ENTRY_END,
    FROZEN_FOLLOWUP_END,
    FROZEN_START,
    JOB_PROFILES,
    STOP_LEVELS,
    assert_no_credentials,
    load_config,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("job", "arm", "minutes"),
    [
        ("watermelon-white-1m", "FAST_1M", 1),
        ("watermelon-grey-5m", "CONTROL_5M", 5),
    ],
)
def test_frozen_job_profiles_load(
    job: str, arm: str, minutes: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POLYBOT_LIFECYCLE_MODE", "archive_only")
    monkeypatch.setenv("POLYBOT_SIMULATION_MODE", "true")
    config = load_config(ROOT / "config.yaml", job, simulation_mode=True)
    assert config.simulation_mode is True
    assert config.trading.cadence_arm == arm
    assert config.trading.cadence_minutes == minutes
    assert config.trading.experiment.entry_thresholds == ENTRY_THRESHOLDS
    assert config.trading.experiment.stop_levels == STOP_LEVELS
    assert config.trading.gamma.page_size == 500
    assert config.trading.gamma.max_pages == 4
    assert config.trading.gamma.tag_slug == "sports"
    assert config.trading.gamma.live_only is True
    assert config.trading.gamma.sports_market_types == ("moneyline",)
    assert FROZEN_ENTRY_END - FROZEN_START == timedelta(days=14)
    assert FROZEN_FOLLOWUP_END - FROZEN_ENTRY_END == timedelta(days=14)


def test_job_is_the_only_cadence_treatment() -> None:
    assert set(JOB_PROFILES) == {
        "watermelon-white-1m",
        "watermelon-grey-5m",
    }
    white = load_config(ROOT / "config.yaml", "watermelon-white-1m")
    grey = load_config(ROOT / "config.yaml", "watermelon-grey-5m")
    assert white.trading.experiment == grey.trading.experiment
    assert white.trading.gamma == grey.trading.gamma
    assert white.trading.orderbook == grey.trading.orderbook
    assert white.config_hash != grey.config_hash


@pytest.mark.parametrize(
    "key",
    [
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_FUNDER_ADDRESS",
        "POLYMARKET_SIGNATURE_TYPE",
        "CLOB_API_KEY",
    ],
)
def test_credentials_are_rejected_even_when_empty(key: str) -> None:
    with pytest.raises(ValueError, match="credential-bearing"):
        assert_no_credentials({key: ""})


def test_unknown_polybot_override_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLYBOT_BUY_AMOUNT", "5")
    with pytest.raises(ValueError, match="unknown POLYBOT"):
        load_config(ROOT / "config.yaml")


def test_unknown_job_is_rejected() -> None:
    with pytest.raises(ValueError, match="job must be one of"):
        load_config(ROOT / "config.yaml", "watermelon-unknown")


def test_live_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="contradicts"):
        load_config(ROOT / "config.yaml", simulation_mode=False)
