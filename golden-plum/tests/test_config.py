from datetime import datetime, timedelta
from pathlib import Path

import pytest

from polybot.config import (
    FROZEN_ENTRY_END_UTC,
    FROZEN_FOLLOWUP_END_UTC,
    FROZEN_START_UTC,
    GOLD_ENTRY_END_UTC,
    GOLD_FOLLOWUP_END_UTC,
    GOLD_START_UTC,
    MLB_PREREGISTRATION,
    RUNTIME_SPECS,
    SOCCER_PREREGISTRATION,
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
    assert entry.min_source_minute == 0
    assert entry.max_source_minute is None
    assert entry.hours_max is None
    assert entry.trend_observations == 3
    assert entry.trend_min_cumulative_move == 0.02
    assert entry.trend_max_pullback == 0.01
    assert entry.trend_max_gap_seconds == 90
    assert entry.force_exit_minute is None
    assert config.trading.scaling_notionals_usdc == ()
    assert config.trading.sport_profile_version == "soccer-full-match-v2"
    assert config.trading.book_shape == "direct-six-result-books"
    assert config.trading.expected_token_count == 6
    assert config.trading.source_clock_required is True
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
    assert config.trading.scaling_notionals_usdc == (
        5.0,
        10.0,
        25.0,
        50.0,
        100.0,
        250.0,
        500.0,
    )


def test_gold_is_credential_free_mlb_collection_with_scaling_grid(
    monkeypatch,
) -> None:
    _no_credentials(monkeypatch)
    config = load_config(
        "config.yaml", "plum-shadow-gold-mlb-1m-v1", simulation_mode=True
    )

    assert config.db_path == Path(
        "data/plum-shadow-gold-mlb-1m-v1/trades_sim.db"
    )
    trading = config.trading
    assert trading.lifecycle_mode == "active"
    assert trading.sport_family == "mlb"
    assert trading.protocol_id == "plum-mlb-shadow-v3"
    assert trading.preregistration_path == MLB_PREREGISTRATION
    assert trading.execution_policy == (
        "credential-free-displayed-book-simulation"
    )
    assert trading.cadence_seconds == 60
    assert trading.cycle_hard_deadline_seconds == 50.0
    assert trading.external_workspace_path == (
        "/Volumes/t7/jenkins/polybot-gold"
    )
    assert trading.entry.take_profit_price == 0.95
    assert trading.sport_profile_version == "mlb-collection-uncalibrated-v1"
    assert trading.book_shape == "direct-two-team-moneyline"
    assert trading.expected_result_kinds == ("HOME", "AWAY")
    assert trading.expected_market_count == 1
    assert trading.expected_token_count == 2
    assert trading.source_clock_required is False
    assert trading.scaling_notionals_usdc == (
        5.0,
        10.0,
        25.0,
        50.0,
        100.0,
        250.0,
        500.0,
    )
    assert trading.analysis_entry_thresholds == (
        0.55,
        0.60,
        0.65,
        0.70,
        0.75,
        0.80,
    )
    assert trading.experiment_start_utc == GOLD_START_UTC
    assert trading.experiment_entry_end_utc == GOLD_ENTRY_END_UTC
    assert trading.experiment_followup_end_utc == GOLD_FOLLOWUP_END_UTC


def test_runtime_specs_are_atomic_and_protocol_specific(monkeypatch) -> None:
    _credentials(monkeypatch)
    king = load_config(
        "config.yaml", "plum-live-king-90-1m-v1", simulation_mode=False
    )
    _no_credentials(monkeypatch)
    gold = load_config(
        "config.yaml", "plum-shadow-gold-mlb-1m-v1", simulation_mode=True
    )

    assert king.trading.preregistration_path == SOCCER_PREREGISTRATION
    assert gold.trading.preregistration_path == MLB_PREREGISTRATION
    assert king.trading.preregistration_sha256 != (
        gold.trading.preregistration_sha256
    )
    assert set(RUNTIME_SPECS) == {
        "plum-live-king-90-1m-v1",
        "plum-live-queen-95-1m-v1",
        "plum-shadow-silver-1m-v1",
        "plum-shadow-gold-mlb-1m-v1",
    }


def test_gold_mode_lifecycle_target_and_family_fail_closed(monkeypatch) -> None:
    _no_credentials(monkeypatch)
    with pytest.raises(ValueError, match="frozen to simulation"):
        load_config(
            "config.yaml",
            "plum-shadow-gold-mlb-1m-v1",
            simulation_mode=False,
        )

    monkeypatch.setenv("POLYBOT_LIFECYCLE_MODE", "archive_only")
    with pytest.raises(ValueError, match="runtime or sport-specific"):
        load_config(
            "config.yaml",
            "plum-shadow-gold-mlb-1m-v1",
            simulation_mode=True,
        )
    monkeypatch.delenv("POLYBOT_LIFECYCLE_MODE")

    monkeypatch.setenv("POLYBOT_TAKE_PROFIT_PRICE", "0.90")
    with pytest.raises(ValueError, match="take-profit"):
        load_config(
            "config.yaml",
            "plum-shadow-gold-mlb-1m-v1",
            simulation_mode=True,
        )
    monkeypatch.delenv("POLYBOT_TAKE_PROFIT_PRICE")

    monkeypatch.setenv("POLYBOT_SPORT_FAMILY", "nba")
    with pytest.raises(ValueError, match="must remain mlb"):
        load_config(
            "config.yaml",
            "plum-shadow-gold-mlb-1m-v1",
            simulation_mode=True,
        )


def test_live_jobs_cannot_switch_to_a_direct_sport(monkeypatch) -> None:
    _credentials(monkeypatch)
    monkeypatch.setenv("POLYBOT_SPORT_FAMILY", "mlb")
    with pytest.raises(ValueError, match="must remain soccer"):
        load_config(
            "config.yaml", "plum-live-king-90-1m-v1", simulation_mode=False
        )


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
        ("POLYBOT_MIN_SOURCE_MINUTE", "1", "full-match"),
        ("POLYBOT_MAX_SOURCE_MINUTE", "75", "full-match"),
        ("POLYBOT_TREND_OBSERVATIONS", "2", "full-match"),
        ("POLYBOT_TREND_MIN_CUMULATIVE_MOVE", "0.01", "full-match"),
        ("POLYBOT_TREND_MAX_PULLBACK", "0.02", "full-match"),
        ("POLYBOT_TREND_MAX_GAP_SECONDS", "120", "full-match"),
        ("POLYBOT_MIN_LEADER_MARGIN", "0.01", "full-match"),
        ("POLYBOT_MAX_ENTRY_SPREAD", "0.06", "full-match"),
        ("POLYBOT_STOP_LOSS_DELTA", "0.10", "full-match"),
        ("POLYBOT_FORCE_EXIT_MINUTE", "80", "full-match"),
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
