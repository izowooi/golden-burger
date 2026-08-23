from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from polybot.config import PROJECT_ROOT, load_config
from polybot.followup_config import load_followup_config


@pytest.fixture
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture
def config(tmp_path, monkeypatch):
    for key in list(__import__("os").environ):
        if (
            key.startswith("POLYBOT_")
            or key.startswith("POLYMARKET_")
            or key.startswith("CLOB_")
        ):
            monkeypatch.delenv(key, raising=False)
    loaded = load_config(PROJECT_ROOT / "config.yaml")
    return replace(
        loaded,
        db_path=tmp_path / "data" / "strawberry-shadow-one" / "trades_sim.db",
    )


@pytest.fixture
def followup_config(tmp_path, monkeypatch):
    for key in list(__import__("os").environ):
        if (
            key.startswith("POLYBOT_")
            or key.startswith("POLYMARKET_")
            or key.startswith("CLOB_")
        ):
            monkeypatch.delenv(key, raising=False)
    loaded = load_followup_config(PROJECT_ROOT / "config.followup-v2a.yaml")
    source = replace(
        loaded.trading.v1_source,
        db_path=tmp_path / "data" / "strawberry-shadow-one" / "trades_sim.db",
    )
    trading = replace(loaded.trading, v1_source=source)
    return replace(
        loaded,
        db_path=(
            tmp_path
            / "data"
            / "strawberry-shadow-one-followup-v2a"
            / "trades_sim.db"
        ),
        trading=trading,
    )
