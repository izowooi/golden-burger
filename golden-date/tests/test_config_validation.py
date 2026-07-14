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
    ("POLYBOT_LIFECYCLE_MODE", "pause", "LIFECYCLE_MODE"),
    ("POLYMARKET_SIGNATURE_TYPE", "2", "signature_type"),
])
def test_invalid_env_values_are_rejected(monkeypatch, key, value, match):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=match):
        load_config("missing.yaml")


@pytest.mark.parametrize("values,match", [
    ({"POLYBOT_LADDER_H1": "72", "POLYBOT_LADDER_H2": "24"}, "ladder hours"),
    ({"POLYBOT_BAND1_MIN": "0.96", "POLYBOT_BAND1_MAX": "0.95"}, "ladder band"),
])
def test_invalid_ladder_cross_fields_are_rejected(monkeypatch, values, match):
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=match):
        load_config("missing.yaml")


def test_invalid_boolean_env_is_rejected(monkeypatch):
    monkeypatch.setenv("POLYBOT_TRAILING_STOP_ENABLED", "tru")
    with pytest.raises(ValueError, match="boolean"):
        load_config("missing.yaml")


def test_excluded_categories_yaml_must_be_a_list(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("trading:\n  excluded_categories: Sports\n", encoding="utf-8")
    with pytest.raises(ValueError, match="list of strings"):
        load_config(str(path))


def test_simulation_mode_yaml_must_be_boolean(tmp_path):
    path = tmp_path / "bad-mode.yaml"
    path.write_text('simulation_mode: "false"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="simulation_mode"):
        load_config(str(path))


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


@pytest.mark.parametrize(
    "yaml_text",
    [
        "tradng: {}\n",
        "trading:\n  buy_amunt_usdc: 5\n",
        "trading:\n  ladder:\n    band1_mni: 0.8\n",
    ],
)
def test_unknown_yaml_keys_are_rejected(tmp_path, yaml_text):
    path = tmp_path / "unknown-key.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        load_config(str(path))
