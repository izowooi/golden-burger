"""Strict resolved-configuration validation tests."""
import os

import pytest

from polybot.config import load_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith("POLYBOT_") or key == "POLYMARKET_SIGNATURE_TYPE":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0x" + "22" * 20)


@pytest.mark.parametrize(("key", "value", "match"), [
    ("POLYBOT_BUY_AMOUNT", "nan", "buy_amount_usdc"),
    ("POLYBOT_BUY_AMOUNT", "0", "buy_amount_usdc"),
    ("POLYBOT_MIN_LIQUIDITY", "-1", "min_liquidity"),
    ("POLYBOT_MAX_POSITIONS", "0", "max_positions"),
    ("POLYBOT_MAX_POSITIONS", "-1", "max_positions"),
    ("POLYBOT_MAX_OPEN_NOTIONAL_USDC", "0", "max_open_notional_usdc"),
    ("POLYBOT_MAX_ORDER_LIQUIDITY_RATIO", "0", "max_order_liquidity_ratio"),
    ("POLYBOT_GAME_START_BUFFER_MINUTES", "1441", "entry_buffer_minutes"),
    ("POLYMARKET_SIGNATURE_TYPE", "2", "signature_type"),
])
def test_invalid_env_values_are_rejected(monkeypatch, key, value, match):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=match):
        load_config("missing.yaml")


@pytest.mark.parametrize("values,match", [
    ({"POLYBOT_BUY_THRESHOLD": "0.95", "POLYBOT_SELL_THRESHOLD": "0.90"}, "buy_threshold"),
    ({"POLYBOT_ENTRY_HOURS_MIN": "48", "POLYBOT_ENTRY_HOURS_MAX": "24"}, "entry_hours_min"),
    ({"POLYBOT_BUY_AMOUNT": "101", "POLYBOT_MAX_BUY_AMOUNT_USDC": "100"}, "max_buy_amount_usdc"),
])
def test_invalid_cross_field_values_are_rejected(monkeypatch, values, match):
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=match):
        load_config("missing.yaml")


def test_zero_hour_entry_and_disabled_time_exit_are_supported(monkeypatch):
    monkeypatch.setenv("POLYBOT_ENTRY_HOURS_MIN", "0")
    monkeypatch.setenv("POLYBOT_ENTRY_HOURS_MAX", "120")
    monkeypatch.setenv("POLYBOT_EXIT_HOURS", "0")

    timing = load_config("missing.yaml").trading.time_based

    assert timing.entry_hours_min == 0
    assert timing.entry_hours_max == 120
    assert timing.exit_hours == 0


def test_live_safety_defaults_are_finite():
    trading = load_config("missing.yaml").trading

    assert trading.time_based.entry_hours_min == 0
    assert trading.time_based.entry_hours_max == 120
    assert trading.time_based.exit_hours == 0
    assert trading.max_positions == 100
    assert trading.max_open_notional_usdc == 5000
    assert trading.max_new_positions_per_cycle == 5
    assert trading.game_start.enabled is True
    assert trading.game_start.entry_buffer_minutes == 5
    assert trading.effective_min_liquidity == 50_000


@pytest.mark.parametrize(("key", "value"), [
    ("POLYBOT_ENTRY_HOURS_MIN", "-1"),
    ("POLYBOT_EXIT_HOURS", "-1"),
])
def test_negative_time_window_values_are_rejected(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="time_based windows"):
        load_config("missing.yaml")


def test_invalid_boolean_env_is_rejected(monkeypatch):
    monkeypatch.setenv("POLYBOT_TRAILING_STOP_ENABLED", "tru")
    with pytest.raises(ValueError, match="boolean"):
        load_config("missing.yaml")


def test_invalid_game_start_boolean_env_is_rejected(monkeypatch):
    monkeypatch.setenv("POLYBOT_GAME_START_FILTER_ENABLED", "tru")
    with pytest.raises(ValueError, match="boolean"):
        load_config("missing.yaml")


def test_excluded_categories_yaml_must_be_a_list(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("trading:\n  excluded_categories: Sports\n", encoding="utf-8")
    with pytest.raises(ValueError, match="excluded_categories"):
        load_config(str(path))


def test_simulation_mode_yaml_must_be_boolean(tmp_path):
    path = tmp_path / "bad-mode.yaml"
    path.write_text('simulation_mode: "false"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="simulation_mode"):
        load_config(str(path))


def test_lifecycle_mode_defaults_to_active():
    assert load_config("missing.yaml").trading.lifecycle_mode == "active"


def test_lifecycle_mode_loads_from_yaml(tmp_path):
    path = tmp_path / "lifecycle.yaml"
    path.write_text("trading:\n  lifecycle_mode: archive_only\n", encoding="utf-8")

    assert load_config(str(path)).trading.lifecycle_mode == "archive_only"


def test_lifecycle_mode_env_overrides_yaml_and_normalizes(monkeypatch, tmp_path):
    path = tmp_path / "lifecycle.yaml"
    path.write_text("trading:\n  lifecycle_mode: archive_only\n", encoding="utf-8")
    monkeypatch.setenv("POLYBOT_LIFECYCLE_MODE", " Close-Only ")

    assert load_config(str(path)).trading.lifecycle_mode == "close_only"


@pytest.mark.parametrize("value", ["disabled", "close", "1", ""])
def test_invalid_lifecycle_mode_is_rejected(monkeypatch, value):
    monkeypatch.setenv("POLYBOT_LIFECYCLE_MODE", value)
    with pytest.raises(ValueError, match="POLYBOT_LIFECYCLE_MODE"):
        load_config("missing.yaml")


@pytest.mark.parametrize(("yaml_text", "match"), [
    ("trading:\n  buy_amount_usdc: true\n", "numeric"),
    ("trading:\n  buy_amount_usdc: '5'\n", "numeric"),
    ("trading:\n  max_positions: 1.5\n", "integer"),
])
def test_yaml_numeric_types_are_strict(tmp_path, yaml_text, match):
    path = tmp_path / "bad-number.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        load_config(str(path))
