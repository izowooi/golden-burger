from datetime import datetime, timedelta
from pathlib import Path

import pytest

from polybot.config import (
    FROZEN_ENTRY_END_UTC,
    FROZEN_FOLLOWUP_END_UTC,
    FROZEN_START_UTC,
    SIMULATION_SCALING_NOTIONALS_USDC,
    load_config,
)


def _credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0x" + "2" * 40)


def _no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_FUNDER_ADDRESS",
        "POLYMARKET_SIGNATURE_TYPE",
    ):
        monkeypatch.delenv(key, raising=False)


def test_eco_live_arm_loads_the_frozen_contract(monkeypatch) -> None:
    _credentials(monkeypatch)
    config = load_config(
        "config.yaml", "peach-live-eco-3pp-1m-v1", simulation_mode=False
    )

    assert config.simulation_mode is False
    assert config.db_path == Path("data/peach-live-eco-3pp-1m-v1/trades.db")
    entry = config.trading.entry
    assert (entry.prob_min, entry.prob_max) == (0.60, 0.94)
    assert entry.take_profit_delta == 0.03
    assert entry.stop_loss_delta == 0.10
    assert entry.max_source_minute == 10
    assert entry.late_exit_minute == 80
    assert entry.late_profit_fraction == 0.50
    assert config.trading.yes_only_mode is False
    assert config.trading.max_positions == 10
    assert config.trading.max_emergency_sells_per_cycle == 10
    assert config.trading.stop_sell_quarantine_timeout_minutes == 180
    assert config.trading.experiment_start_utc == FROZEN_START_UTC
    assert config.trading.experiment_entry_end_utc == FROZEN_ENTRY_END_UTC
    assert config.trading.experiment_followup_end_utc == FROZEN_FOLLOWUP_END_UTC
    start = datetime.fromisoformat(FROZEN_START_UTC.replace("Z", "+00:00"))
    entry_end = datetime.fromisoformat(FROZEN_ENTRY_END_UTC.replace("Z", "+00:00"))
    followup_end = datetime.fromisoformat(
        FROZEN_FOLLOWUP_END_UTC.replace("Z", "+00:00")
    )
    assert entry_end - start == timedelta(days=14)
    assert followup_end - entry_end == timedelta(days=7)
    assert len(config.trading.strategy_source_digest) == 64
    assert len(config.trading.preregistration_sha256) == 64
    assert config.api.private_key == "1" * 64
    assert config.trading.scaling_notionals_usdc == ()
    assert config.trading.expected_token_count == 6


def test_fruit_differs_only_by_five_point_profit_target(monkeypatch) -> None:
    _credentials(monkeypatch)
    monkeypatch.setenv("POLYBOT_TAKE_PROFIT_DELTA", "0.05")
    config = load_config(
        "config.yaml", "peach-live-fruit-5pp-1m-v1", simulation_mode=False
    )
    assert config.trading.entry.take_profit_delta == 0.05
    assert config.trading.entry.stop_loss_delta == 0.10


def test_grey_is_credential_free_simulation(monkeypatch) -> None:
    _no_credentials(monkeypatch)
    monkeypatch.setenv("POLYBOT_TAKE_PROFIT_DELTA", "0.05")
    config = load_config(
        "config.yaml", "peach-shadow-1m-v1", simulation_mode=True
    )
    assert config.db_path == Path("data/peach-shadow-1m-v1/trades_sim.db")
    assert config.api.private_key == ""
    assert config.api.funder_address == ""
    assert config.trading.scaling_notionals_usdc == SIMULATION_SCALING_NOTIONALS_USDC


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("POLYBOT_BUY_AMOUNT", "4.99", "notional"),
        ("POLYBOT_MAX_POSITIONS", "19", "exposure"),
        ("POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE", "20", "exposure"),
        ("POLYBOT_MIN_LIQUIDITY", "1", "liquidity gate"),
        ("POLYBOT_MIN_CUMULATIVE_VOLUME", "1", "liquidity gate"),
        ("POLYBOT_ENTRY_PROB_MIN", "0.61", "entry executable"),
        ("POLYBOT_MAX_SOURCE_MINUTE", "11", "kickoff/leader"),
        ("POLYBOT_MIN_LEADER_MARGIN", "0.01", "kickoff/leader"),
        ("POLYBOT_MAX_ENTRY_SPREAD", "0.06", "kickoff/leader"),
        ("POLYBOT_STOP_LOSS_DELTA", "0.09", "kickoff/leader"),
        ("POLYBOT_LATE_EXIT_MINUTE", "85", "kickoff/leader"),
        ("POLYBOT_MAX_EMERGENCY_SELLS_PER_CYCLE", "1", "ten independent"),
        ("POLYBOT_STOP_SELL_QUARANTINE_TIMEOUT_MINUTES", "179", "180"),
        ("POLYBOT_YES_ONLY", "true", "YES and NO"),
        ("POLYBOT_EXPERIMENT_END_UTC", "2026-09-01T00:00:00Z", "timestamps"),
    ],
)
def test_contract_drift_is_rejected(monkeypatch, key, value, message) -> None:
    _credentials(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=message):
        load_config(
            "config.yaml", "peach-live-eco-3pp-1m-v1", simulation_mode=False
        )


def test_runtime_name_mode_and_credentials_fail_closed(monkeypatch) -> None:
    _credentials(monkeypatch)
    with pytest.raises(ValueError, match="unsupported"):
        load_config("config.yaml", "unknown", simulation_mode=False)
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("POLYMARKET_FUNDER_ADDRESS", raising=False)
    monkeypatch.delenv("POLYMARKET_SIGNATURE_TYPE", raising=False)
    with pytest.raises(ValueError, match="frozen to live"):
        load_config(
            "config.yaml", "peach-live-eco-3pp-1m-v1", simulation_mode=True
        )

    _no_credentials(monkeypatch)
    with pytest.raises(ValueError, match="PRIVATE_KEY"):
        load_config(
            "config.yaml", "peach-live-eco-3pp-1m-v1", simulation_mode=False
        )

    _credentials(monkeypatch)
    monkeypatch.setenv("POLYBOT_TAKE_PROFIT_DELTA", "0.05")
    with pytest.raises(ValueError, match="must not receive wallet credentials"):
        load_config("config.yaml", "peach-shadow-1m-v1", simulation_mode=True)


@pytest.mark.parametrize("amount", [5.01, 10, 250, 1000])
def test_adaptive_target_buy_amount_is_accepted(monkeypatch, amount) -> None:
    _credentials(monkeypatch)
    monkeypatch.setenv("POLYBOT_BUY_AMOUNT", str(amount))
    config = load_config(
        "config.yaml", "peach-live-eco-3pp-1m-v1", simulation_mode=False
    )
    assert config.trading.buy_amount_usdc == amount


@pytest.mark.parametrize(
    ("family", "job"),
    [
        ("mlb", "peach-shadow-mlb-1m-v2"),
        ("nba", "peach-shadow-nba-1m-v2"),
        ("nfl", "peach-shadow-nfl-1m-v2"),
        ("nhl", "peach-shadow-nhl-1m-v2"),
    ],
)
def test_direct_sport_profiles_are_shadow_only(monkeypatch, family, job) -> None:
    _no_credentials(monkeypatch)
    monkeypatch.setenv("POLYBOT_TAKE_PROFIT_DELTA", "0.05")
    config = load_config("config.yaml", job, simulation_mode=True)
    assert config.trading.sport_family == family
    assert config.trading.expected_market_count == 1
    assert config.trading.expected_token_count == 2
    assert config.trading.source_clock_required is False

    _credentials(monkeypatch)
    with pytest.raises(ValueError, match="shadow-only"):
        load_config("config.yaml", job, simulation_mode=False)


def test_live_runtime_rejects_direct_sport_override(monkeypatch) -> None:
    _credentials(monkeypatch)
    monkeypatch.setenv("POLYBOT_SPORT_FAMILY", "mlb")
    with pytest.raises(ValueError, match="must remain soccer"):
        load_config(
            "config.yaml", "peach-live-eco-3pp-1m-v1", simulation_mode=False
        )
