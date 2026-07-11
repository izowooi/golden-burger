"""Regression tests for the legacy simulation entrypoint."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.simulate import load_simulation_config


def test_simulation_loader_selects_sim_database(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0x" + "22" * 20)

    config = load_simulation_config("missing.yaml", "safe-sim")

    assert config.simulation_mode is True
    assert config.db_path == Path("data/safe-sim/trades_sim.db")


def test_simulation_loader_rejects_live_database(monkeypatch):
    def fake_load_config(*args, **kwargs):
        assert kwargs["simulation_mode"] is True
        return SimpleNamespace(simulation_mode=False, db_path=Path("data/job/trades.db"))

    monkeypatch.setattr("polybot.config.load_config", fake_load_config)
    with pytest.raises(RuntimeError, match="live database"):
        load_simulation_config("config.yaml", "job")
