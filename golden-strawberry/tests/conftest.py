from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from polybot.config import PROJECT_ROOT, load_config


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
