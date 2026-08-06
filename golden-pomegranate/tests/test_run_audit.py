"""RunAudit, disk and single-writer lifecycle tests for the real bot wrapper."""

from __future__ import annotations

from collections import namedtuple
from dataclasses import replace
import json
from pathlib import Path
import signal
import sqlite3

import pytest

import polybot.bot as bot_module
from polybot.bot import (
    PolymarketResearchBot,
    ResearchTermination,
    exclusive_job_run_lock,
    graceful_termination,
)
from polybot.config import load_config
from polybot.db.repository import GIB, ResearchRepository
from polybot.run_audit import ResearchRunAudit


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path):
    loaded = load_config(PROJECT_ROOT / "config.yaml", env={})
    return replace(loaded, db_path=tmp_path / "data" / "audit-job" / "trades_sim.db")


class FakeRepository:
    def __init__(self, *, post_state: str = "OK") -> None:
        self.calls: list[str] = []
        self.post_state = post_state

    def rotate_if_utc_day_changed(self, **_kwargs):
        self.calls.append("rotate")
        return Path("trades_sim_20260805.db")

    def initialize(self, **_kwargs):
        self.calls.append("initialize")

    def next_cycle_number(self):
        self.calls.append("next_cycle")
        return 7

    def record_storage_metric(self, *, phase, **_kwargs):
        self.calls.append(f"storage:{phase}")
        state = "OK" if phase == "preflight" else self.post_state
        return {
            "guard_state": state,
            "filesystem_used_ratio": 0.25,
            "filesystem_free_bytes": 900,
            "forecast_next_day_bytes": 123,
            "forecast_days_to_stop": 365.0,
            "db_bytes": 10,
            "wal_bytes": 20,
            "logical_bytes": 30,
        }


class FakeCollector:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.run_ids: list[str] = []

    def run_cycle(self, run_id: str):
        self.run_ids.append(run_id)
        if self.error is not None:
            raise self.error
        return {
            "cycle_number": 7,
            "market_sweeps": 1,
            "markets_observed": 2,
            "orderbooks_observed": 4,
            "trades_observed": 3,
            "resolution_observed": 1,
            "data_quality_issue_count": 0,
        }


class FakeAudit:
    def __init__(self) -> None:
        self.run_id = "audit-run-id"
        self.successes: list[dict] = []
        self.failures: list[BaseException] = []

    def succeed(self, stats):
        self.successes.append(stats)

    def fail(self, error):
        self.failures.append(error)


def _prepare_bot(monkeypatch, tmp_path, *, repository=None, collector=None):
    config = _config(tmp_path)
    bot = PolymarketResearchBot(
        config,
        repository=repository or FakeRepository(),
        collector=collector or FakeCollector(),
    )
    monkeypatch.setattr(bot, "_precreation_disk_guard", lambda: None)
    monkeypatch.setattr(bot_module, "assert_no_credentials", lambda: None)
    return bot


def test_audit_start_failure_prevents_source_collection(monkeypatch, tmp_path):
    repository = FakeRepository()
    collector = FakeCollector()
    bot = _prepare_bot(
        monkeypatch, tmp_path, repository=repository, collector=collector
    )

    def fail_start(*_args, **_kwargs):
        raise sqlite3.OperationalError("audit unavailable")

    monkeypatch.setattr(bot_module.ResearchRunAudit, "start", fail_start)

    with pytest.raises(sqlite3.OperationalError, match="audit unavailable"):
        bot.run()

    assert collector.run_ids == []
    assert repository.calls == [
        "rotate",
        "initialize",
        "next_cycle",
        "storage:preflight",
    ]


def test_successful_cycle_finalizes_audit_with_component_stats(monkeypatch, tmp_path):
    repository = FakeRepository()
    collector = FakeCollector()
    audit = FakeAudit()
    bot = _prepare_bot(
        monkeypatch, tmp_path, repository=repository, collector=collector
    )
    monkeypatch.setattr(
        bot_module.ResearchRunAudit,
        "start",
        lambda *_args, **_kwargs: audit,
    )

    stats = bot.run()

    assert collector.run_ids == [audit.run_id]
    assert audit.failures == []
    assert audit.successes == [stats]
    assert stats["storage"] == {
        "db_bytes": 10,
        "wal_bytes": 20,
        "logical_bytes": 30,
        "forecast_next_day_bytes": 123,
        "guard_state": "OK",
    }
    assert stats["rotated_archive"] == "trades_sim_20260805.db"


def test_collector_or_post_publish_stop_marks_audit_failed(monkeypatch, tmp_path):
    original = RuntimeError("collector failed")
    audit = FakeAudit()
    bot = _prepare_bot(monkeypatch, tmp_path, collector=FakeCollector(error=original))
    monkeypatch.setattr(
        bot_module.ResearchRunAudit,
        "start",
        lambda *_args, **_kwargs: audit,
    )

    with pytest.raises(RuntimeError, match="collector failed") as caught:
        bot.run()

    assert caught.value is original
    assert audit.successes == []
    assert audit.failures == [original]

    stop_audit = FakeAudit()
    stop_bot = _prepare_bot(
        monkeypatch,
        tmp_path / "post-stop",
        repository=FakeRepository(post_state="STOP"),
        collector=FakeCollector(),
    )
    monkeypatch.setattr(
        bot_module.ResearchRunAudit, "start", lambda *_args, **_kwargs: stop_audit
    )
    with pytest.raises(RuntimeError, match="after atomic publish"):
        stop_bot.run()
    assert len(stop_audit.failures) == 1
    assert stop_audit.successes == []


def test_signal_termination_is_not_an_exception_fallback_and_fails_audit(
    monkeypatch, tmp_path
):
    with graceful_termination():
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(ResearchTermination, match="SIGTERM"):
            handler(signal.SIGTERM, None)

    termination = ResearchTermination("research process received SIGTERM")
    audit = FakeAudit()
    bot = _prepare_bot(
        monkeypatch,
        tmp_path,
        collector=FakeCollector(error=termination),
    )
    monkeypatch.setattr(
        bot_module.ResearchRunAudit,
        "start",
        lambda *_args, **_kwargs: audit,
    )

    with pytest.raises(ResearchTermination) as caught:
        bot.run()

    assert caught.value is termination
    assert audit.successes == []
    assert audit.failures == [termination]


def test_real_run_audit_persists_source_digest_profile_and_job(tmp_path, monkeypatch):
    config = _config(tmp_path)
    repository = ResearchRepository(config.db_path)
    repository.initialize()
    monkeypatch.setenv("GIT_COMMIT", "a" * 40)

    audit = ResearchRunAudit.start(config, repository=repository)
    audit.succeed({"gamma_status": "SUCCESS"})

    with sqlite3.connect(config.db_path) as connection:
        events = connection.execute(
            "SELECT strategy_name, job_name, mode, git_commit, event_type, "
            "cycle_stats_json, strategy_source_digest "
            "FROM research_run_events WHERE run_id = ? ORDER BY rowid",
            (audit.run_id,),
        ).fetchall()
        config_json = connection.execute(
            "SELECT config_json FROM research_config_versions WHERE config_hash = ?",
            (audit.config_hash,),
        ).fetchone()[0]

    assert [event[4] for event in events] == ["STARTED", "SUCCEEDED"]
    assert events[-1][:5] == (
        "golden-pomegranate",
        "pomegranate-research",
        "sim",
        "a" * 40,
        "SUCCEEDED",
    )
    assert json.loads(events[-1][5]) == {"gamma_status": "SUCCESS"}
    assert events[-1][6] == config.trading.strategy_source_digest
    resolved = json.loads(config_json)
    assert resolved["trading"]["data_contract"] == "research-full-v1"
    assert resolved["trading"]["strategy_source_digest"] == (
        config.trading.strategy_source_digest
    )


def test_process_lock_and_precreation_disk_stop_before_repository_or_source(
    monkeypatch, tmp_path
):
    lock = tmp_path / "collector.lock"
    with exclusive_job_run_lock(lock):
        with pytest.raises(RuntimeError, match="already holds exclusive lock"):
            with exclusive_job_run_lock(lock):
                pass

    repository = FakeRepository()
    collector = FakeCollector()
    bot = PolymarketResearchBot(
        _config(tmp_path / "disk-stop"),
        repository=repository,
        collector=collector,
    )
    usage = namedtuple("Usage", "total used free")(1_000 * GIB, 100 * GIB, 149 * GIB)
    monkeypatch.setattr(bot_module.shutil, "disk_usage", lambda _path: usage)
    monkeypatch.setattr(bot_module, "assert_no_credentials", lambda: None)

    with pytest.raises(RuntimeError, match="free bytes"):
        bot.run()

    assert repository.calls == []
    assert collector.run_ids == []
