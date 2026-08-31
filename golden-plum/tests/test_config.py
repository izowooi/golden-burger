from datetime import datetime, timedelta
from pathlib import Path

import pytest

from polybot.config import (
    FROZEN_ENTRY_END_UTC,
    FROZEN_FOLLOWUP_END_UTC,
    FROZEN_START_UTC,
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


def test_king_live_arm_loads_the_frozen_contract(monkeypatch) -> None:
    _credentials(monkeypatch)
    config = load_config(
        "config.yaml", "plum-live-king-90-1m-v1", simulation_mode=False
    )

    assert config.simulation_mode is False
    assert config.db_path == Path("data/plum-live-king-90-1m-v1/trades.db")
    entry = config.trading.entry
    assert (entry.prob_min, entry.prob_max) == (0.75, 0.78)
    assert entry.take_profit_price == 0.90
    assert entry.stop_loss_delta == 0.15
    assert (entry.min_source_minute, entry.max_source_minute) == (5, 75)
    assert entry.trend_observations == 3
    assert entry.trend_min_cumulative_move == 0.02
    assert entry.trend_max_pullback == 0.01
    assert entry.trend_max_gap_seconds == 90
    assert entry.force_exit_minute == 80
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


def test_queen_differs_only_by_absolute_profit_target(monkeypatch) -> None:
    _credentials(monkeypatch)
    monkeypatch.setenv("POLYBOT_TAKE_PROFIT_PRICE", "0.95")
    config = load_config(
        "config.yaml", "plum-live-queen-95-1m-v1", simulation_mode=False
    )
    assert config.trading.entry.take_profit_price == 0.95
    assert config.trading.entry.stop_loss_delta == 0.15


def test_silver_is_credential_free_simulation(monkeypatch) -> None:
    _no_credentials(monkeypatch)
    monkeypatch.setenv("POLYBOT_TAKE_PROFIT_PRICE", "0.95")
    config = load_config(
        "config.yaml", "plum-shadow-silver-1m-v1", simulation_mode=True
    )
    assert config.db_path == Path(
        "data/plum-shadow-silver-1m-v1/trades_sim.db"
    )
    assert config.api.private_key == ""
    assert config.api.funder_address == ""


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("POLYBOT_BUY_AMOUNT", "5.01", "notional"),
        ("POLYBOT_MAX_POSITIONS", "19", "exposure"),
        ("POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE", "20", "exposure"),
        ("POLYBOT_MIN_LIQUIDITY", "1", "liquidity gate"),
        ("POLYBOT_MIN_CUMULATIVE_VOLUME", "1", "liquidity gate"),
        ("POLYBOT_ENTRY_PROB_MIN", "0.74", "first-cross"),
        ("POLYBOT_ENTRY_PROB_MAX", "0.79", "first-cross"),
        ("POLYBOT_MIN_SOURCE_MINUTE", "4", "midgame trend"),
        ("POLYBOT_MAX_SOURCE_MINUTE", "76", "midgame trend"),
        ("POLYBOT_TREND_OBSERVATIONS", "2", "midgame trend"),
        ("POLYBOT_TREND_MIN_CUMULATIVE_MOVE", "0.01", "midgame trend"),
        ("POLYBOT_TREND_MAX_PULLBACK", "0.02", "midgame trend"),
        ("POLYBOT_TREND_MAX_GAP_SECONDS", "120", "midgame trend"),
        ("POLYBOT_MIN_LEADER_MARGIN", "0.01", "midgame trend"),
        ("POLYBOT_MAX_ENTRY_SPREAD", "0.06", "midgame trend"),
        ("POLYBOT_STOP_LOSS_DELTA", "0.10", "midgame trend"),
        ("POLYBOT_FORCE_EXIT_MINUTE", "85", "midgame trend"),
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
            "config.yaml", "plum-live-king-90-1m-v1", simulation_mode=False
        )


def test_runtime_name_mode_and_credentials_fail_closed(monkeypatch) -> None:
    _credentials(monkeypatch)
    with pytest.raises(ValueError, match="unsupported"):
        load_config("config.yaml", "unknown", simulation_mode=False)
    _no_credentials(monkeypatch)
    with pytest.raises(ValueError, match="frozen to live"):
        load_config(
            "config.yaml", "plum-live-king-90-1m-v1", simulation_mode=True
        )
    with pytest.raises(ValueError, match="PRIVATE_KEY"):
        load_config(
            "config.yaml", "plum-live-king-90-1m-v1", simulation_mode=False
        )

    _credentials(monkeypatch)
    monkeypatch.setenv("POLYBOT_TAKE_PROFIT_PRICE", "0.95")
    with pytest.raises(ValueError, match="must not receive wallet credentials"):
        load_config(
            "config.yaml", "plum-shadow-silver-1m-v1", simulation_mode=True
        )
