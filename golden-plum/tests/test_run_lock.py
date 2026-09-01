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
    config = SimpleNamespace(simulation_mode=True, job_name="plum-shadow-test")
    error = main_module.OverlappingCycleSkipped("overlap")

    main_module._record_simulation_failure(config, error)

    assert calls == [
        ("start", "plum-shadow-test", "golden-plum"),
        ("fail", "OverlappingCycleSkipped", "overlap"),
    ]


def test_live_lock_skip_does_not_mutate_run_audit(monkeypatch) -> None:
    class ForbiddenAudit:
        @classmethod
        def start(cls, *args, **kwargs):
            raise AssertionError("live lock skip must not write a new audit")

    monkeypatch.setattr(main_module, "RunAudit", ForbiddenAudit)
    config = SimpleNamespace(simulation_mode=False, job_name="plum-live-king")

    main_module._record_simulation_failure(
        config,
        main_module.OverlappingCycleSkipped("overlap"),
    )


def test_simulation_lock_skip_persists_failed_audit(tmp_path) -> None:
    database = tmp_path / "trades_sim.db"
    config = SimpleNamespace(
        simulation_mode=True,
        job_name="plum-shadow-test",
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


def test_external_collector_preflight_is_invoked_with_exact_db(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    workspace = "/Volumes/t7/jenkins/polybot-gold"
    database = Path(workspace) / (
        "golden-plum/data/plum-shadow-gold-mlb-1m-v1/trades_sim.db"
    )
    monkeypatch.setenv("WORKSPACE", workspace)
    monkeypatch.setattr(main_module.subprocess, "run", fake_run)

    result = main_module._verify_external_collector_workspace(
        "plum-shadow-gold-mlb-1m-v1"
    )

    assert result == database
    command = captured["command"]
    assert command[command.index("--job") + 1] == "polybot-gold"
    assert command[command.index("--workspace") + 1] == workspace
    assert command[command.index("--database") + 1] == str(database)
    assert "--write-daily-rsync-marker" in command
    assert captured["kwargs"]["timeout"] == 10


def test_external_collector_preflight_fails_without_jenkins_workspace(
    monkeypatch,
) -> None:
    monkeypatch.delenv("WORKSPACE", raising=False)

    with pytest.raises(RuntimeError, match="requires Jenkins WORKSPACE"):
        main_module._verify_external_collector_workspace(
            "plum-shadow-silver-1m-v1"
        )


def test_live_runtime_has_no_external_collector_preflight(monkeypatch) -> None:
    monkeypatch.delenv("WORKSPACE", raising=False)
    assert (
        main_module._verify_external_collector_workspace(
            "plum-live-king-90-1m-v1"
        )
        is None
    )


def test_run_preflight_happens_before_config_load(monkeypatch) -> None:
    calls = []

    def fail_preflight(runtime_job):
        calls.append(("preflight", runtime_job))
        raise RuntimeError("unsafe workspace")

    def forbidden_load(*args, **kwargs):
        calls.append(("load", None))
        raise AssertionError("configuration must not load before preflight")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "polybot",
            "run",
            "--simulate",
            "--job",
            "plum-shadow-gold-mlb-1m-v1",
        ],
    )
    monkeypatch.setattr(
        main_module, "_verify_external_collector_workspace", fail_preflight
    )
    monkeypatch.setattr(main_module, "_load", forbidden_load)

    with pytest.raises(SystemExit) as exit_info:
        main_module.main()

    assert exit_info.value.code == 1
    assert calls == [("preflight", "plum-shadow-gold-mlb-1m-v1")]


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
        trading=SimpleNamespace(
            cycle_hard_deadline_seconds=(50.0 if simulation_mode else None)
        ),
    )

    @contextmanager
    def fake_lock(path):
        yield True

    @contextmanager
    def fake_deadline(*, hard_limit_seconds, enforce_deadline):
        observed.append((hard_limit_seconds, enforce_deadline))
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

    assert observed == [
        (50.0 if simulation_mode else None, expected_enforcement),
        "run",
    ]


def test_relative_config_database_is_resolved_against_checkout(
    monkeypatch, tmp_path
) -> None:
    checkout = tmp_path / "golden-plum"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    runtime_job = "plum-shadow-gold-mlb-1m-v1"
    relative_database = Path("data") / runtime_job / "trades_sim.db"
    expected = (checkout / relative_database).resolve()
    config = SimpleNamespace(
        job_name=runtime_job,
        db_path=relative_database,
        simulation_mode=True,
        trading=SimpleNamespace(
            external_workspace_path="/Volumes/t7/jenkins/polybot-gold",
            cycle_hard_deadline_seconds=50.0,
            cadence_seconds=60,
        ),
    )

    main_module._validate_resolved_external_contract(config, expected)
    assert main_module._resolved_database_path(relative_database) == expected


def test_runtime_spec_external_path_drift_is_rejected(tmp_path) -> None:
    runtime_job = "plum-shadow-gold-mlb-1m-v1"
    config = SimpleNamespace(
        job_name=runtime_job,
        db_path=Path("data") / runtime_job / "trades_sim.db",
        simulation_mode=True,
        trading=SimpleNamespace(
            external_workspace_path="/Volumes/wrong",
            cycle_hard_deadline_seconds=50.0,
            cadence_seconds=60,
        ),
    )

    with pytest.raises(RuntimeError, match="atomic external workspace contract"):
        main_module._validate_resolved_external_contract(
            config,
            tmp_path / "trades_sim.db",
        )
