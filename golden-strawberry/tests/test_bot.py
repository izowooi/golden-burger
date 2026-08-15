from __future__ import annotations

from collections import namedtuple

import pytest

import polybot.bot as bot_module
from polybot.bot import PolymarketResearchBot, exclusive_job_run_lock
from polybot.db.repository import GIB


def test_single_writer_lock_rejects_overlap(tmp_path):
    path = tmp_path / ".strawberry.lock"
    with exclusive_job_run_lock(path):
        with pytest.raises(RuntimeError, match="single-writer"):
            with exclusive_job_run_lock(path):
                pass


def test_100_gib_guard_stops_before_repository_initialization(config, monkeypatch):
    DiskUsage = namedtuple("DiskUsage", "total used free")
    monkeypatch.setattr(
        bot_module.shutil,
        "disk_usage",
        lambda path: DiskUsage(400 * GIB, 301 * GIB, 99 * GIB),
    )

    class Repository:
        def initialize(self, config):
            raise AssertionError("database initialized after failed disk guard")

    bot = PolymarketResearchBot(config, repository=Repository())
    with pytest.raises(RuntimeError, match="below 100 GiB"):
        bot.run()


def test_90_percent_guard_stops_before_repository_initialization(config, monkeypatch):
    DiskUsage = namedtuple("DiskUsage", "total used free")
    monkeypatch.setattr(
        bot_module.shutil,
        "disk_usage",
        lambda path: DiskUsage(2_000 * GIB, 1_800 * GIB, 200 * GIB),
    )

    class Repository:
        def initialize(self, config):
            raise AssertionError("database initialized after failed disk guard")

    bot = PolymarketResearchBot(config, repository=Repository())
    with pytest.raises(RuntimeError, match="reached 90%"):
        bot.run()
