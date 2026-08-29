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


def test_frozen_arm_a_loads_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _credentials(monkeypatch)
    config = load_config(
        "config.yaml", "watermelon-live-cat-96-1m-v2h", simulation_mode=False
    )

    assert config.simulation_mode is False
    assert config.db_path == Path(
        "data/watermelon-live-cat-96-1m-v2h/trades.db"
    )
    assert (config.trading.entry.prob_min, config.trading.entry.prob_max) == (
        0.96,
        0.999,
    )
    assert config.trading.buy_amount_usdc == 5
    assert config.trading.max_positions == 20
    assert config.trading.max_event_positions == 1
    assert config.trading.max_new_positions_per_cycle == 5
    assert config.trading.max_emergency_sells_per_cycle == 1
    assert config.trading.experiment_capital_usdc == 100
    assert config.trading.max_drawdown_stop == 0.10
    assert config.trading.yes_only_mode is True
    assert config.trading.entry.stop_price == 0.70
    assert config.trading.entry.max_stop_slippage == 0.05
    assert config.trading.entry.max_stop_spread == 0.10
    assert config.trading.entry.max_stop_loss_fraction == 0.35
    assert config.trading.entry.hours_max == 4
    assert config.trading.min_liquidity == 5000
    assert config.trading.min_cumulative_volume == 5000
    assert config.trading.experiment_start_utc == FROZEN_START_UTC
    assert config.trading.experiment_entry_end_utc == FROZEN_ENTRY_END_UTC
    assert config.trading.experiment_followup_end_utc == FROZEN_FOLLOWUP_END_UTC
    start = datetime.fromisoformat(FROZEN_START_UTC.replace("Z", "+00:00"))
    entry_end = datetime.fromisoformat(FROZEN_ENTRY_END_UTC.replace("Z", "+00:00"))
    followup_end = datetime.fromisoformat(
        FROZEN_FOLLOWUP_END_UTC.replace("Z", "+00:00")
    )
    assert entry_end - start == timedelta(days=7)
    assert followup_end - entry_end == timedelta(days=7)
    assert len(config.trading.strategy_source_digest) == 64
    assert len(config.trading.preregistration_sha256) == 64
    assert config.api.private_key == "1" * 64


def test_only_arm_b_threshold_override_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    monkeypatch.setenv("POLYBOT_ENTRY_PROB_MIN", "0.99")
    monkeypatch.setenv("POLYBOT_ENTRY_PROB_MAX", "0.999")
    config = load_config(
        "config.yaml", "watermelon-live-dog-99-1m-v2h", simulation_mode=False
    )
    assert (config.trading.entry.prob_min, config.trading.entry.prob_max) == (
        0.99,
        0.999,
    )


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("POLYBOT_BUY_AMOUNT", "5.01", "notional"),
        ("POLYBOT_MAX_POSITIONS", "19", "exposure"),
        ("POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE", "20", "exposure"),
        ("POLYBOT_MIN_LIQUIDITY", "1", "liquidity gate"),
        ("POLYBOT_MIN_CUMULATIVE_VOLUME", "1", "liquidity gate"),
        ("POLYBOT_ENTRY_PROB_MIN", "0.97", "entry band"),
        ("POLYBOT_ENTRY_HOURS_MAX", "5", "in-play age window"),
        ("POLYBOT_STOP_PRICE", "0.80", "stop_price"),
        ("POLYBOT_MAX_STOP_SLIPPAGE", "0.10", "stop execution safety"),
        ("POLYBOT_MAX_STOP_SPREAD", "0.20", "stop execution safety"),
        (
            "POLYBOT_MAX_STOP_LOSS_FRACTION",
            "0.50",
            "stop execution safety",
        ),
        (
            "POLYBOT_MAX_EMERGENCY_SELLS_PER_CYCLE",
            "2",
            "one emergency SELL",
        ),
        ("POLYBOT_EXPERIMENT_CAPITAL_USDC", "200", "experiment capital"),
        ("POLYBOT_MAX_DRAWDOWN_STOP", "0.20", "drawdown"),
        ("POLYBOT_YES_ONLY", "false", "YES tokens"),
        ("POLYBOT_EXPERIMENT_END_UTC", "2026-09-01T13:00:00Z", "timestamps"),
    ],
)
def test_contract_drift_is_rejected(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str, message: str
) -> None:
    _credentials(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=message):
        load_config("config.yaml", "drift")


def test_credentials_and_live_database_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("POLYMARKET_FUNDER_ADDRESS", raising=False)
    with pytest.raises(ValueError, match="PRIVATE_KEY"):
        load_config("config.yaml", "missing")

    _credentials(monkeypatch)
    sim = load_config("config.yaml", "isolated", simulation_mode=True)
    live = load_config("config.yaml", "isolated", simulation_mode=False)
    assert sim.db_path.name == "trades_sim.db"
    assert live.db_path.name == "trades.db"
    assert sim.db_path != live.db_path
