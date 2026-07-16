"""Golden Papaya config precedence, defaults, and DB isolation."""

from __future__ import annotations

import logging
import os

import pytest

from polybot.config import load_config
from polybot.utils.logger import resolve_log_level


DUMMY_KEY = "0x" + "11" * 32
DUMMY_ADDRESS = "0x" + "22" * 20


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if (
            key.startswith("POLYBOT_")
            or key.startswith("POLYMARKET_")
            or key == "LOG_LEVEL"
        ):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", DUMMY_KEY)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", DUMMY_ADDRESS)


def test_final_five_defaults_are_fail_closed():
    config = load_config("missing.yaml", job_name="papaya")
    trading = config.trading

    assert trading.lifecycle_mode == "active"
    assert trading.buy_amount_usdc == 5.0
    assert trading.min_liquidity == 10_000.0
    assert trading.min_volume_24h == 2_000.0
    assert trading.max_positions == 20
    assert trading.max_event_positions == 1
    assert trading.reentry_cooldown_hours == 24.0
    assert trading.max_snapshot_gap_minutes == 30.0
    assert trading.min_order_size == 5.0
    assert trading.min_order_buffer_shares == 0.10
    assert trading.yes_only_mode is True
    assert trading.excluded_categories == []
    assert trading.entry.prob_min == 0.95
    assert trading.entry.prob_max == 0.97
    assert trading.entry.stop_price == 0.90
    assert trading.entry.hours_min == 0.0
    assert trading.entry.hours_max == 72.0
    assert trading.archive.prob_min == 0.80
    assert trading.archive.hours_max == 168.0
    assert trading.archive.retention_days == 60


def test_env_overrides_yaml_and_yaml_overrides_default(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        "trading:\n"
        "  min_liquidity: 5000\n"
        "  max_event_positions: 2\n"
        "  max_snapshot_gap_minutes: 45\n"
        "  entry:\n"
        "    prob_min: 0.94\n"
        "    prob_max: 0.965\n"
        "    stop_price: 0.89\n"
        "    hours_min: 3\n"
        "    hours_max: 48\n"
        "  archive:\n"
        "    prob_min: 0.81\n"
        "    hours_max: 120\n"
        "    retention_days: 61\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("POLYBOT_MIN_LIQUIDITY", "10000")
    monkeypatch.setenv("POLYBOT_ENTRY_PROB_MIN", "0.95")
    monkeypatch.setenv("POLYBOT_ARCHIVE_PROB_MIN", "0.82")
    monkeypatch.setenv("POLYBOT_MAX_SNAPSHOT_GAP_MINUTES", "20")

    trading = load_config(str(path)).trading

    assert trading.min_liquidity == 10_000.0  # env > YAML
    assert trading.entry.prob_min == 0.95      # env > YAML
    assert trading.entry.prob_max == 0.965     # YAML > default
    assert trading.entry.stop_price == 0.89
    assert trading.entry.hours_min == 3
    assert trading.entry.hours_max == 48
    assert trading.archive.prob_min == 0.82
    assert trading.archive.hours_max == 120
    assert trading.archive.retention_days == 61
    assert trading.max_event_positions == 2
    assert trading.max_snapshot_gap_minutes == 20  # env > YAML


def test_all_strategy_env_names_resolve(monkeypatch):
    values = {
        "POLYBOT_BUY_AMOUNT": "5.5",
        "POLYBOT_MIN_LIQUIDITY": "5000",
        "POLYBOT_MIN_VOLUME_24H": "500",
        "POLYBOT_MAX_POSITIONS": "9",
        "POLYBOT_MAX_EVENT_POSITIONS": "2",
        "POLYBOT_REENTRY_COOLDOWN_HOURS": "36",
        "POLYBOT_MAX_SNAPSHOT_GAP_MINUTES": "15",
        "POLYBOT_MIN_ORDER_SIZE": "5",
        "POLYBOT_MIN_ORDER_BUFFER_SHARES": "0.1",
        "POLYBOT_ENTRY_PROB_MIN": "0.94",
        "POLYBOT_ENTRY_PROB_MAX": "0.96",
        "POLYBOT_STOP_PRICE": "0.85",
        "POLYBOT_ENTRY_HOURS_MIN": "4",
        "POLYBOT_ENTRY_HOURS_MAX": "60",
        "POLYBOT_ARCHIVE_PROB_MIN": "0.80",
        "POLYBOT_ARCHIVE_HOURS_MAX": "180",
        "POLYBOT_SNAPSHOT_RETENTION_DAYS": "90",
        "POLYBOT_YES_ONLY": "true",
        "POLYBOT_EXCLUDED_CATEGORIES": "Sports, Crypto",
        "POLYBOT_LIFECYCLE_MODE": "close-only",
        "POLYMARKET_SIGNATURE_TYPE": "3",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    config = load_config("missing.yaml")
    trading = config.trading

    assert trading.lifecycle_mode == "close_only"
    assert trading.buy_amount_usdc == 5.5
    assert trading.min_liquidity == 5000
    assert trading.min_volume_24h == 500
    assert trading.max_positions == 9
    assert trading.max_event_positions == 2
    assert trading.reentry_cooldown_hours == 36
    assert trading.max_snapshot_gap_minutes == 15
    assert trading.entry.prob_min == 0.94
    assert trading.entry.prob_max == 0.96
    assert trading.entry.stop_price == 0.85
    assert trading.entry.hours_min == 4
    assert trading.entry.hours_max == 60
    assert trading.archive.prob_min == 0.80
    assert trading.archive.hours_max == 180
    assert trading.archive.retention_days == 90
    assert trading.yes_only_mode is True
    assert trading.excluded_categories == ["Sports", "Crypto"]
    assert config.api.signature_type == 3


def test_yes_only_is_inherent_and_cli_cannot_disable(monkeypatch):
    monkeypatch.setenv("POLYBOT_YES_ONLY", "false")
    with pytest.raises(ValueError, match="yes_only_mode=true"):
        load_config("missing.yaml")
    monkeypatch.setenv("POLYBOT_YES_ONLY", "true")
    with pytest.raises(ValueError, match="yes_only_mode=true"):
        load_config("missing.yaml", yes_only_mode=False)


def test_private_key_prefix_is_stripped_and_missing_credentials_fail(monkeypatch):
    config = load_config("missing.yaml")
    assert config.api.private_key == "11" * 32
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY")
    with pytest.raises(ValueError, match="POLYMARKET_PRIVATE_KEY"):
        load_config("missing.yaml")


def test_simulation_and_live_databases_are_isolated_by_job():
    sim = load_config("missing.yaml", "research", simulation_mode=True)
    live = load_config("missing.yaml", "research", simulation_mode=False)
    another = load_config("missing.yaml", "another", simulation_mode=True)

    assert sim.db_path.name == "trades_sim.db"
    assert live.db_path.name == "trades.db"
    assert sim.db_path.parent.name == "research"
    assert another.db_path.parent.name == "another"
    assert len({sim.db_path, live.db_path, another.db_path}) == 3


def test_missing_yaml_defaults_to_simulation_but_explicit_cli_can_enable_live():
    safe_default = load_config("missing.yaml", "safe-default")
    explicit_live = load_config(
        "missing.yaml", "explicit-live", simulation_mode=False
    )

    assert safe_default.simulation_mode is True
    assert safe_default.db_path.name == "trades_sim.db"
    assert explicit_live.simulation_mode is False
    assert explicit_live.db_path.name == "trades.db"


def test_log_level_resolution(monkeypatch):
    assert resolve_log_level() == logging.INFO
    monkeypatch.setenv("LOG_LEVEL", "warning")
    assert resolve_log_level() == logging.WARNING
    assert resolve_log_level(verbose=True) == logging.DEBUG
