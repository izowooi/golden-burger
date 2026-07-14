"""Strict cross-field validation for Final Five configuration."""

from __future__ import annotations

import os

import pytest

from polybot.config import load_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith("POLYBOT_") or key.startswith("POLYMARKET_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0x" + "22" * 20)


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("POLYBOT_BUY_AMOUNT", "nan", "buy_amount_usdc"),
        ("POLYBOT_BUY_AMOUNT", "0", "buy_amount_usdc"),
        ("POLYBOT_MIN_LIQUIDITY", "-1", "min_liquidity"),
        ("POLYBOT_MIN_VOLUME_24H", "-1", "min_volume_24h"),
        ("POLYBOT_MAX_POSITIONS", "0", "position limits"),
        ("POLYBOT_MAX_EVENT_POSITIONS", "0", "position limits"),
        ("POLYBOT_REENTRY_COOLDOWN_HOURS", "0", "reentry_cooldown_hours"),
        ("POLYBOT_MAX_SNAPSHOT_GAP_MINUTES", "0", "max_snapshot_gap_minutes"),
        ("POLYBOT_MAX_SNAPSHOT_GAP_MINUTES", "nan", "max_snapshot_gap_minutes"),
        ("POLYBOT_MIN_ORDER_SIZE", "0", "minimum order size"),
        ("POLYBOT_MIN_ORDER_BUFFER_SHARES", "-0.1", "minimum order size"),
        ("POLYBOT_SNAPSHOT_RETENTION_DAYS", "59", "at least 60"),
        ("POLYBOT_LIFECYCLE_MODE", "pause", "LIFECYCLE_MODE"),
        ("POLYMARKET_SIGNATURE_TYPE", "2", "signature_type"),
    ],
)
def test_invalid_scalar_values_are_rejected(monkeypatch, key, value, match):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=match):
        load_config("missing.yaml")


@pytest.mark.parametrize(
    ("values", "match"),
    [
        (
            {"POLYBOT_STOP_PRICE": "0.95", "POLYBOT_ENTRY_PROB_MIN": "0.95"},
            "stop_price < prob_min",
        ),
        (
            {"POLYBOT_ENTRY_PROB_MIN": "0.98", "POLYBOT_ENTRY_PROB_MAX": "0.97"},
            "stop_price < prob_min",
        ),
        (
            {"POLYBOT_ENTRY_HOURS_MIN": "72", "POLYBOT_ENTRY_HOURS_MAX": "72"},
            "entry hours",
        ),
        (
            {"POLYBOT_ENTRY_HOURS_MIN": "1.99"},
            "hours_min",
        ),
        (
            {"POLYBOT_ENTRY_HOURS_MAX": "72.01"},
            "hours_max",
        ),
        (
            {"POLYBOT_ARCHIVE_PROB_MIN": "0.86"},
            "archive.prob_min",
        ),
        (
            {"POLYBOT_ARCHIVE_HOURS_MAX": "71"},
            "entry horizon",
        ),
        (
            {"POLYBOT_MAX_POSITIONS": "1", "POLYBOT_MAX_EVENT_POSITIONS": "2"},
            "max_event_positions",
        ),
        (
            {"POLYBOT_MIN_ORDER_BUFFER_SHARES": "0.2"},
            "too small",
        ),
    ],
)
def test_invalid_cross_field_values_are_rejected(monkeypatch, values, match):
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=match):
        load_config("missing.yaml")


def test_entry_and_archive_boundaries_are_accepted(monkeypatch):
    monkeypatch.setenv("POLYBOT_ENTRY_PROB_MIN", "0.95")
    monkeypatch.setenv("POLYBOT_ENTRY_PROB_MAX", "0.97")
    monkeypatch.setenv("POLYBOT_STOP_PRICE", "0.90")
    monkeypatch.setenv("POLYBOT_ENTRY_HOURS_MIN", "2")
    monkeypatch.setenv("POLYBOT_ENTRY_HOURS_MAX", "72")
    monkeypatch.setenv("POLYBOT_ARCHIVE_PROB_MIN", "0.85")
    config = load_config("missing.yaml")
    assert config.trading.archive.prob_min == 0.85


def test_default_order_has_minimum_share_buffer():
    trading = load_config("missing.yaml").trading
    smallest_order = trading.buy_amount_usdc / trading.entry.prob_max
    assert smallest_order >= trading.min_order_size + trading.min_order_buffer_shares


def test_yaml_sections_and_numeric_types_are_strict(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("trading:\n  entry: nope\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be mappings"):
        load_config(str(path))

    path.write_text("trading:\n  entry:\n    prob_min: '0.95'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="numeric"):
        load_config(str(path))

    path.write_text("trading:\n  archive:\n    retention_days: 60.5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="integer"):
        load_config(str(path))


def test_simulation_yaml_must_be_boolean(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text('simulation_mode: "false"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="simulation_mode"):
        load_config(str(path))
