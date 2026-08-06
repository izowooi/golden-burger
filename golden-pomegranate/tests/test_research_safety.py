"""Source-level hard blocks for an accountless, non-trading collector."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from polybot.config import assert_no_credentials, load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CREDENTIAL_KEYS = (
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_FUNDER_ADDRESS",
    "POLYMARKET_SIGNATURE_TYPE",
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_API_PASSPHRASE",
    "CLOB_API_KEY",
    "CLOB_SECRET",
    "CLOB_PASSPHRASE",
)


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_any_credential_environment_value_is_rejected_before_path_io(key):
    with pytest.raises(ValueError, match="accountless|credential|forbidden"):
        load_config(object(), env={key: "forbidden"})  # type: ignore[arg-type]


def test_credential_error_lists_names_but_not_values():
    secret_value = "must-never-appear"

    with pytest.raises(ValueError) as captured:
        assert_no_credentials(
            {
                "POLYMARKET_PRIVATE_KEY": secret_value,
                "POLYMARKET_SIGNATURE_TYPE": "3",
            }
        )

    message = str(captured.value)
    assert "POLYMARKET_PRIVATE_KEY" in message
    assert "POLYMARKET_SIGNATURE_TYPE" in message
    assert secret_value not in message


@pytest.mark.parametrize(
    ("yaml_simulation", "environment", "override"),
    [
        (False, {}, None),
        (True, {"POLYBOT_SIMULATION_MODE": "false"}, None),
        (True, {}, False),
    ],
)
def test_live_mode_is_rejected_without_creating_a_database(
    tmp_path, yaml_simulation, environment, override
):
    payload = yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
    payload["simulation_mode"] = yaml_simulation
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="live mode|research-only|forbidden"):
        load_config(path, env=environment, simulation_mode=override)

    assert not (tmp_path / "data").exists()


def test_source_tree_has_no_authenticated_or_order_execution_path():
    source_root = PROJECT_ROOT / "src" / "polybot"
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(source_root.rglob("*.py"))
    )

    for forbidden in (
        "ExecutionLedger",
        "submit_and_record",
        "post_order",
        "place_limit_order",
        "create_or_derive_api_key",
        "POLYMARKET_PRIVATE_KEY=",
    ):
        assert forbidden not in combined

    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "py-clob-client" not in pyproject.lower()
