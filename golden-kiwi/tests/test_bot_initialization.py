"""Bot construction must preserve the frozen research evidence horizon."""

from __future__ import annotations

from pathlib import Path

import pytest

import polybot.bot as bot_module
from polybot.bot import PolymarketBot
from polybot.config import ApiConfig, BotConfig, TradingConfig


def _config(tmp_path: Path, *, simulation_mode: bool = True) -> BotConfig:
    return BotConfig(
        trading=TradingConfig(),
        api=ApiConfig(private_key="", funder_address=""),
        db_path=tmp_path / "data" / "kiwi-test" / "trades_sim.db",
        simulation_mode=simulation_mode,
        job_name="kiwi-test",
    )


def test_bot_preserves_full_five_minute_cadence_for_all_60_days(
    monkeypatch, tmp_path
):
    captured = {}

    def fake_init_database(path, requirements):
        captured["path"] = path
        captured["requirements"] = requirements
        return object()

    monkeypatch.setattr(bot_module, "init_database", fake_init_database)
    monkeypatch.setattr(bot_module, "GammaClient", lambda: object())
    monkeypatch.setattr(bot_module, "ClobClientWrapper", lambda *args, **kwargs: object())

    PolymarketBot(_config(tmp_path))

    requirements = captured["requirements"]
    assert requirements.full_cadence_hours == 60 * 24
    assert requirements.retention_days == 60
    assert requirements.minimum_latest_points == 6
    assert requirements.boundary_interval_hours is None
    assert requirements.max_rollup_hours is None


def test_live_bot_is_blocked_before_database_or_clob_construction(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        bot_module,
        "init_database",
        lambda *_args, **_kwargs: pytest.fail("database must not be opened"),
    )
    monkeypatch.setattr(
        bot_module,
        "ClobClientWrapper",
        lambda *_args, **_kwargs: pytest.fail("CLOB client must not be constructed"),
    )

    with pytest.raises(RuntimeError, match="research/simulation-only"):
        PolymarketBot(_config(tmp_path, simulation_mode=False))
