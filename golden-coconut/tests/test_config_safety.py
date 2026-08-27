from __future__ import annotations

from pathlib import Path

import pytest

from polybot import main as cli
from polybot.config import (
    CANONICAL_JOB,
    assert_safe_environment,
    load_config,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "key",
    [
        "POLYMARKET_PRIVATE_KEY", "POLYMARKET_ANY_NEW_KEY", "CLOB_API_KEY",
        "CLOB_UNKNOWN", "PRIVATE_KEY", "FUNDER_ADDRESS", "API_SECRET", "PK",
    ],
)
def test_credential_key_presence_is_rejected_even_when_empty(key):
    with pytest.raises(ValueError, match="credential-bearing"):
        assert_safe_environment({key: ""})


def test_unknown_polybot_key_is_rejected_even_when_empty():
    with pytest.raises(ValueError, match="unknown POLYBOT"):
        assert_safe_environment({"POLYBOT_SURPRISE": ""})


def test_allowed_polybot_values_are_exact():
    assert_safe_environment(
        {
            "POLYBOT_LIFECYCLE_MODE": "archive_only",
            "POLYBOT_SIMULATION_MODE": "true",
        }
    )
    with pytest.raises(ValueError, match="archive_only"):
        assert_safe_environment({"POLYBOT_LIFECYCLE_MODE": "active"})
    with pytest.raises(ValueError, match="must be true"):
        assert_safe_environment({"POLYBOT_SIMULATION_MODE": "false"})


def test_load_config_freezes_runtime_and_daily_rsync_filename():
    config = load_config(ROOT / "config.yaml", CANONICAL_JOB, mode="shadow")
    assert config.mode == "shadow"
    assert config.simulation_mode is True
    assert config.trading.lifecycle_mode == "archive_only"
    assert config.db_path.as_posix().endswith(
        "/data/coconut-major-sports-5m-v1/trades_sim.db"
    )


def test_unsafe_environment_fails_before_config_read(monkeypatch):
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("config must not be read")

    monkeypatch.setattr(cli, "load_config", forbidden)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "")
    assert cli.main(["config", "--simulate"]) == 2
    assert called is False


@pytest.mark.parametrize("token", ["--live", "active", "close_only", "--mode=live"])
def test_forbidden_cli_mode_fails_before_config(monkeypatch, token):
    monkeypatch.setattr(
        cli, "load_config", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())
    )
    assert cli.main(["run", token]) == 2
