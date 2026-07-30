"""Strict YAML and frozen-universe validation."""

import pytest

from polybot.config import load_config


@pytest.fixture(autouse=True)
def no_env(monkeypatch, tmp_path):
    for key in list(__import__("os").environ):
        if key.startswith("POLYBOT_") or key.startswith("POLYMARKET_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)


def write(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_unknown_yaml_key_is_rejected(tmp_path):
    path = write(tmp_path, "trading:\n  typo_positions: 3\n")
    with pytest.raises(ValueError, match="unknown trading"):
        load_config(path, "bad")


def test_non_boolean_simulation_value_is_rejected(tmp_path):
    path = write(tmp_path, 'simulation_mode: "true"\n')
    with pytest.raises(ValueError, match="simulation_mode must be a boolean"):
        load_config(path, "bad")


def test_exact_excluded_tag_order_and_membership_are_frozen(monkeypatch):
    monkeypatch.setenv(
        "POLYBOT_EXCLUDED_CATEGORIES",
        "games,sports,esports,crypto-prices,up-or-down,multi-strikes,5m,15m,1h",
    )
    with pytest.raises(ValueError, match="excluded_categories is frozen"):
        load_config("missing.yaml", "bad")


def test_yes_only_cannot_be_disabled(monkeypatch):
    monkeypatch.setenv("POLYBOT_YES_ONLY", "false")
    with pytest.raises(ValueError, match="yes_only_mode"):
        load_config("missing.yaml", "bad")
