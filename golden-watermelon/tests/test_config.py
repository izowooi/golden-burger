from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from polybot.config import (
    ENTRY_THRESHOLDS,
    FROZEN_ENTRY_END,
    FROZEN_FOLLOWUP_END,
    FROZEN_START,
    JOB_PROFILES,
    LEAGUE_MAPPING_SHA256,
    REQUIRED_COMMON_TAG_IDS,
    SOCCER_TAG_ID,
    STOP_LEVELS,
    assert_no_credentials,
    load_config,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("job", "arm", "minutes"),
    [
        ("watermelon-white-1m-v3a", "FAST_1M", 1),
        ("watermelon-grey-5m-v3a", "CONTROL_5M", 5),
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
    assert config.trading.gamma.tag_id == SOCCER_TAG_ID == 100350
    assert config.trading.gamma.related_tags is False
    assert config.trading.gamma.live_only is True
    assert config.trading.gamma.sport_family == "soccer"
    assert config.trading.gamma.league_codes == ("epl", "bun", "fl1", "lal", "mls")
    assert config.trading.gamma.required_common_tag_ids == REQUIRED_COMMON_TAG_IDS
    assert config.trading.league_mapping_sha256 == LEAGUE_MAPPING_SHA256
    assert LEAGUE_MAPPING_SHA256 == "3b843d62e87ebe9ba84c2986a4229d1fa5760d5e06a39204dc5acb3da6a433bb"
    assert config.trading.gamma.sports_market_types == ("moneyline",)
    assert FROZEN_ENTRY_END - FROZEN_START == timedelta(days=7)
    assert FROZEN_FOLLOWUP_END - FROZEN_ENTRY_END == timedelta(days=7)


def test_job_is_the_only_cadence_treatment() -> None:
    assert set(JOB_PROFILES) == {
        "watermelon-white-1m-v3a",
        "watermelon-grey-5m-v3a",
    }
    white = load_config(ROOT / "config.yaml", "watermelon-white-1m-v3a")
    grey = load_config(ROOT / "config.yaml", "watermelon-grey-5m-v3a")
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


@pytest.mark.parametrize("mutation", ["missing_league", "sport_id_conflict", "related_tags"])
def test_frozen_server_envelope_and_mapping_fail_closed(tmp_path, mutation: str) -> None:
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    if mutation == "missing_league":
        del raw["trading"]["gamma"]["league_mapping"]["mls"]
    elif mutation == "sport_id_conflict":
        raw["trading"]["gamma"]["league_mapping"]["epl"]["sport_id"] = 999
    else:
        raw["trading"]["gamma"]["related_tags"] = True
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="mapping|numeric soccer"):
        load_config(path, "watermelon-white-1m-v3a")
