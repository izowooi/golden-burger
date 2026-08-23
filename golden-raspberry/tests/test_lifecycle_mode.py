from __future__ import annotations

import pytest

from polybot.bot import PolymarketResearchBot
from polybot.config import PROJECT_ROOT, load_config


def test_only_archive_only_mode_is_accepted(monkeypatch):
    monkeypatch.setenv("POLYBOT_LIFECYCLE_MODE", "active")
    with pytest.raises(ValueError, match="archive_only"):
        load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")


def test_bot_has_no_live_mode(monkeypatch):
    monkeypatch.delenv("POLYBOT_LIFECYCLE_MODE", raising=False)
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    assert PolymarketResearchBot(config).config.simulation_mode is True
