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
    config = load_config("config.yaml", "tangerine-live-a-94", simulation_mode=False)

    assert config.simulation_mode is False
    assert config.db_path == Path("data/tangerine-live-a-94/trades.db")
    assert (config.trading.entry.prob_min, config.trading.entry.prob_max) == (
        0.94,
        0.95,
    )
    assert config.trading.buy_amount_usdc == 5
    assert config.trading.max_positions == 3
    assert config.trading.max_event_positions == 1
    assert config.trading.max_new_positions_per_cycle == 1
    assert config.trading.yes_only_mode is False
    assert config.trading.entry.stop_price == 0
    assert config.trading.entry.hours_max == 6
    assert config.trading.min_liquidity == 10_000
    assert config.trading.min_cumulative_volume == 5_000
    assert config.trading.experiment_start_utc == FROZEN_START_UTC
    assert config.trading.experiment_entry_end_utc == FROZEN_ENTRY_END_UTC
    assert config.trading.experiment_followup_end_utc == FROZEN_FOLLOWUP_END_UTC
    start = datetime.fromisoformat(FROZEN_START_UTC.replace("Z", "+00:00"))
    entry_end = datetime.fromisoformat(FROZEN_ENTRY_END_UTC.replace("Z", "+00:00"))
    followup_end = datetime.fromisoformat(
        FROZEN_FOLLOWUP_END_UTC.replace("Z", "+00:00")
    )
    assert entry_end - start == timedelta(days=30)
    assert followup_end - entry_end == timedelta(days=30)
    assert len(config.trading.strategy_source_digest) == 64
    assert len(config.trading.preregistration_sha256) == 64
    assert config.api.private_key == "1" * 64


def test_only_arm_b_threshold_override_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    monkeypatch.setenv("POLYBOT_ENTRY_PROB_MIN", "0.92")
    monkeypatch.setenv("POLYBOT_ENTRY_PROB_MAX", "0.93")
    config = load_config("config.yaml", "tangerine-live-b-92", simulation_mode=False)
    assert (config.trading.entry.prob_min, config.trading.entry.prob_max) == (
        0.92,
        0.93,
    )


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("POLYBOT_BUY_AMOUNT", "5.01", r"notional must remain exactly \$5"),
        ("POLYBOT_MAX_OPEN_NOTIONAL_USDC", "15.03", "open notional"),
        ("POLYBOT_MAX_POSITIONS", "4", "exposure"),
        ("POLYBOT_MIN_LIQUIDITY", "9999", "universe"),
        ("POLYBOT_MIN_CUMULATIVE_VOLUME", "4999", "universe"),
        ("POLYBOT_ENTRY_PROB_MIN", "0.93", "entry band"),
        ("POLYBOT_ENTRY_HOURS_MAX", "7", "entry window"),
        ("POLYBOT_STOP_PRICE", "0.80", "stop_price"),
        ("POLYBOT_YES_ONLY", "true", "both binary outcomes"),
        ("POLYBOT_EXPERIMENT_END_UTC", "2026-09-20T14:08:00Z", "timestamps"),
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
