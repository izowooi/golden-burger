from contextlib import contextmanager
from multiprocessing import Process, Queue
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from polybot import main as main_module
from polybot.utils.run_lock import exclusive_job_run_lock


def _try_lock(path: str, queue: Queue) -> None:
    with exclusive_job_run_lock(Path(path)) as acquired:
        queue.put(acquired)


def test_second_process_skips_while_runtime_lock_is_held(tmp_path) -> None:
    lock_path = tmp_path / ".cycle-run.lock"
    queue: Queue = Queue()
    with exclusive_job_run_lock(lock_path) as first:
        assert first is True
        process = Process(target=_try_lock, args=(str(lock_path), queue))
        process.start()
        process.join(timeout=5)
        assert process.exitcode == 0
        assert queue.get(timeout=1) is False

    with exclusive_job_run_lock(lock_path) as after_release:
        assert after_release is True


def test_runtime_lock_releases_after_cycle_exception(tmp_path) -> None:
    lock_path = tmp_path / ".cycle-run.lock"
    with pytest.raises(RuntimeError, match="cycle failed"):
        with exclusive_job_run_lock(lock_path) as acquired:
            assert acquired is True
            raise RuntimeError("cycle failed")

    with exclusive_job_run_lock(lock_path) as after_failure:
        assert after_failure is True


def test_simulation_lock_skip_is_recorded_as_failed_run_audit(monkeypatch) -> None:
    calls = []

    class FakeAudit:
        @classmethod
        def start(cls, config, *, strategy_name):
            calls.append(("start", config.job_name, strategy_name))
            return cls()

        def fail(self, error):
            calls.append(("fail", type(error).__name__, str(error)))

    monkeypatch.setattr(main_module, "RunAudit", FakeAudit)
    config = SimpleNamespace(simulation_mode=True, job_name="peach-shadow-test")
    error = main_module.OverlappingCycleSkipped("overlap")

    main_module._record_simulation_failure(config, error)

    assert calls == [
        ("start", "peach-shadow-test", "golden-peach"),
        ("fail", "OverlappingCycleSkipped", "overlap"),
    ]


def test_live_lock_skip_does_not_mutate_run_audit(monkeypatch) -> None:
    class ForbiddenAudit:
        @classmethod
        def start(cls, *args, **kwargs):
            raise AssertionError("live lock skip must not write a new audit")

    monkeypatch.setattr(main_module, "RunAudit", ForbiddenAudit)
    config = SimpleNamespace(simulation_mode=False, job_name="peach-live-eco")

    main_module._record_simulation_failure(
        config,
        main_module.OverlappingCycleSkipped("overlap"),
    )


def test_simulation_lock_skip_persists_failed_audit(tmp_path) -> None:
    database = tmp_path / "trades_sim.db"
    config = SimpleNamespace(
        simulation_mode=True,
        job_name="peach-shadow-test",
        db_path=database,
        trading={"lifecycle_mode": "active"},
    )

    main_module._record_simulation_failure(
        config,
        main_module.OverlappingCycleSkipped("overlap"),
    )

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT status, error_type FROM run_audits"
        ).fetchone()
    assert row == ("FAILED", "OverlappingCycleSkipped")


@pytest.mark.parametrize(
    ("mode_flag", "simulation_mode", "expected_enforcement"),
    [("--simulate", True, True), ("--live", False, False)],
)
def test_main_enforces_deadline_only_for_simulation(
    monkeypatch,
    tmp_path,
    mode_flag,
    simulation_mode,
    expected_enforcement,
) -> None:
    observed = []
    config = SimpleNamespace(
        simulation_mode=simulation_mode,
        job_name="test-runtime",
        db_path=tmp_path / "trades.db",
    )

    @contextmanager
    def fake_lock(_path):
        yield True

    @contextmanager
    def fake_deadline(*, enforce_deadline):
        observed.append(enforce_deadline)
        yield object()

    class FakeBot:
        def __init__(self, resolved_config, *, cycle_budget):
            assert resolved_config is config

        def run(self):
            observed.append("run")

        def close(self):
            return []

    monkeypatch.setattr(
        sys,
        "argv",
        ["polybot", "run", mode_flag, "--job", "test-runtime"],
    )
    monkeypatch.setattr(main_module, "_load", lambda *args, **kwargs: config)
    monkeypatch.setattr(main_module, "setup_logger", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "exclusive_job_run_lock", fake_lock)
    monkeypatch.setattr(main_module, "enforced_cycle_deadline", fake_deadline)
    monkeypatch.setattr(main_module, "PolymarketBot", FakeBot)

    main_module.main()

    assert observed == [expected_enforcement, "run"]


def test_simulation_bot_init_failure_is_recorded_as_incomplete_run(
    monkeypatch, tmp_path
) -> None:
    recorded = []
    config = SimpleNamespace(
        simulation_mode=True,
        job_name="test-runtime",
        db_path=tmp_path / "trades_sim.db",
    )

    @contextmanager
    def fake_lock(_path):
        yield True

    @contextmanager
    def fake_deadline(*, enforce_deadline):
        assert enforce_deadline is True
        yield object()

    def fail_bot(*_args, **_kwargs):
        raise RuntimeError("schema incompatible")

    monkeypatch.setattr(
        sys, "argv", ["polybot", "run", "--simulate", "--job", "test-runtime"]
    )
    monkeypatch.setattr(main_module, "_load", lambda *args, **kwargs: config)
    monkeypatch.setattr(main_module, "setup_logger", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "exclusive_job_run_lock", fake_lock)
    monkeypatch.setattr(main_module, "enforced_cycle_deadline", fake_deadline)
    monkeypatch.setattr(main_module, "PolymarketBot", fail_bot)
    monkeypatch.setattr(
        main_module,
        "_record_simulation_failure",
        lambda resolved, error: recorded.append(
            (resolved.job_name, type(error).__name__, str(error))
        ),
    )

    with pytest.raises(SystemExit) as exit_info:
        main_module.main()

    assert exit_info.value.code == 1
    assert recorded == [("test-runtime", "RuntimeError", "schema incompatible")]
