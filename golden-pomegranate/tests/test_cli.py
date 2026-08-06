"""CLI routing and pre-I/O research-only safety tests."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import polybot.main as main_module
import pytest
from polybot.config import load_config
from polybot.db.repository import ResearchRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path):
    loaded = load_config(PROJECT_ROOT / "config.yaml", env={})
    return replace(loaded, db_path=tmp_path / "data" / "cli-job" / "trades_sim.db")


def test_live_is_rejected_before_config_repository_logging_or_bot(monkeypatch, capsys):
    calls = []

    def unexpected(*_args, **_kwargs):
        calls.append("unexpected")
        raise AssertionError("unsafe construction happened")

    monkeypatch.setattr(main_module, "load_config", unexpected)
    monkeypatch.setattr(main_module, "ResearchRepository", unexpected)
    monkeypatch.setattr(main_module, "setup_logging", unexpected)
    monkeypatch.setattr(main_module, "PolymarketResearchBot", unexpected)

    code = main_module.main(["run", "--live", "--job", "never-live"])

    assert code == 2
    assert calls == []
    assert "--live is forbidden" in capsys.readouterr().err


def test_credential_configuration_error_does_not_echo_value_or_construct_db(
    monkeypatch, capsys
):
    secret = "test-secret-must-not-be-printed"
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", secret)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("repository/logging/bot must not be constructed")

    monkeypatch.setattr(main_module, "ResearchRepository", unexpected)
    monkeypatch.setattr(main_module, "setup_logging", unexpected)
    monkeypatch.setattr(main_module, "PolymarketResearchBot", unexpected)

    code = main_module.main(
        [
            "run",
            "--simulate",
            "--config",
            str(PROJECT_ROOT / "config.yaml"),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "Configuration error" in captured.err
    assert "POLYMARKET_PRIVATE_KEY" in captured.err
    assert secret not in captured.err


def test_simulate_flag_does_not_override_explicit_false_environment(
    monkeypatch, capsys
):
    monkeypatch.setenv("POLYBOT_SIMULATION_MODE", "false")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("repository/logging/bot must not be constructed")

    monkeypatch.setattr(main_module, "ResearchRepository", unexpected)
    monkeypatch.setattr(main_module, "setup_logging", unexpected)
    monkeypatch.setattr(main_module, "PolymarketResearchBot", unexpected)

    code = main_module.main(
        [
            "run",
            "--simulate",
            "--config",
            str(PROJECT_ROOT / "config.yaml"),
        ]
    )

    assert code == 2
    assert "live mode" in capsys.readouterr().err


def test_config_is_secret_free_and_does_not_construct_repository(
    monkeypatch, capsys, tmp_path
):
    config = _config(tmp_path)
    monkeypatch.setattr(main_module, "load_config", lambda *_args, **_kwargs: config)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("config command must not construct repository")

    monkeypatch.setattr(main_module, "ResearchRepository", unexpected)

    code = main_module.main(["config", "--simulate"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["simulation_mode"] is True
    assert payload["trading"]["lifecycle_mode"] == "archive_only"
    assert payload["trading"]["data_contract"] == "research-full-v1"
    rendered = json.dumps(payload)
    assert "private_key" not in rendered.lower()
    assert "api_secret" not in rendered.lower()


def test_status_health_and_manifest_route_to_read_only_repository(
    monkeypatch, capsys, tmp_path
):
    config = _config(tmp_path)
    monkeypatch.setattr(main_module, "load_config", lambda *_args, **_kwargs: config)
    instances = []

    class FakeRepository:
        def __init__(self, db_path, *, busy_timeout_ms):
            self.db_path = db_path
            self.busy_timeout_ms = busy_timeout_ms
            self.calls = []
            instances.append(self)

        def status(self, storage, *, cadence_minutes):
            self.calls.append(("status", storage.min_free_gib, cadence_minutes))
            return {"exists": False, "db_path": str(self.db_path)}

        def health(self, storage, *, cadence_minutes):
            self.calls.append(("health", storage.min_free_gib, cadence_minutes))
            return {"healthy": True, "state": "NEW_DB_READY"}

        def export_manifest(
            self,
            output,
            *,
            storage,
            cadence_minutes,
            include_sibling_shards,
        ):
            self.calls.append(
                (
                    "manifest",
                    output,
                    storage.min_free_gib,
                    cadence_minutes,
                    include_sibling_shards,
                )
            )
            return {"healthy": True, "health": {"healthy": True}, "files": []}

    monkeypatch.setattr(main_module, "ResearchRepository", FakeRepository)

    assert main_module.main(["status", "--simulate"]) == 0
    assert json.loads(capsys.readouterr().out)["exists"] is False
    assert instances[-1].calls == [("status", 150.0, 60)]

    assert main_module.main(["health", "--simulate"]) == 0
    assert json.loads(capsys.readouterr().out)["healthy"] is True
    assert instances[-1].calls == [("health", 150.0, 60)]

    output = tmp_path / "manifest.json"
    assert (
        main_module.main(["export-manifest", "--simulate", "--output", str(output)])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["files"] == []
    assert instances[-1].calls == [("manifest", str(output), 150.0, 60, True)]


def test_simulated_run_constructs_one_bot_and_returns_its_outcome(
    monkeypatch, tmp_path
):
    config = _config(tmp_path)
    events = []

    class RunRepository:
        def inspect_storage(self, **_kwargs):
            events.append("preflight")
            return {"guard_state": "OK"}

    repository = RunRepository()

    monkeypatch.setattr(main_module, "load_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(
        main_module,
        "ResearchRepository",
        lambda *_args, **_kwargs: events.append("repository") or repository,
    )
    monkeypatch.setattr(
        main_module,
        "setup_logging",
        lambda *_args, **_kwargs: events.append("logging") or Path("summary.log"),
    )

    class FakeBot:
        def __init__(self, received_config, *, repository):
            assert received_config is config
            assert repository is not None
            events.append("bot")

        def run(self):
            events.append("run")
            return {"market_sweeps": 1}

    monkeypatch.setattr(main_module, "PolymarketResearchBot", FakeBot)

    code = main_module.main(["run", "--simulate", "--verbose"])

    assert code == 0
    assert events == ["repository", "preflight", "logging", "bot", "run"]


def test_status_accepts_one_canonical_absolute_verified_db(
    monkeypatch, capsys, tmp_path
):
    config = _config(tmp_path)
    db_path = (tmp_path / "daily-rsync" / "verified.db").resolve()
    repository = ResearchRepository(db_path)
    repository.initialize(database_utc_date="2026-08-06")
    monkeypatch.setattr(main_module, "load_config", lambda *_args, **_kwargs: config)

    code = main_module.main(["status", "--simulate", "--db", str(db_path)])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["db_path"] == str(db_path)
    assert payload["read_only_input_validation"]["healthy"] is True
    assert payload["read_only_input_validation"]["data_contract"] == (
        "research-full-v1"
    )
    assert not Path(f"{db_path}-wal").exists()
    assert not Path(f"{db_path}-shm").exists()
    assert payload["shards"]["selected_role"] == ("operator_supplied_verified_snapshot")
    assert payload["shards"]["sibling_scan_performed"] is False


def test_read_only_db_rejects_relative_symlink_corrupt_and_wrong_profile(
    monkeypatch, capsys, tmp_path
):
    config = _config(tmp_path)
    valid = (tmp_path / "valid.db").resolve()
    ResearchRepository(valid).initialize(database_utc_date="2026-08-06")
    symlink = tmp_path / "linked.db"
    symlink.symlink_to(valid)
    corrupt = (tmp_path / "corrupt.db").resolve()
    corrupt.write_bytes(b"not sqlite")
    wrong_profile = (tmp_path / "wrong.db").resolve()
    with sqlite3.connect(wrong_profile) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    guardless = (tmp_path / "guardless.db").resolve()
    ResearchRepository(guardless).initialize(database_utc_date="2026-08-06")
    with sqlite3.connect(guardless) as connection:
        connection.execute("DROP TRIGGER market_sweeps_append_only_delete")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.commit()
        # Read-only inspection uses SQLite immutable mode and therefore reads
        # only durable database pages, not a live writer's WAL sidecar.
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    monkeypatch.setattr(main_module, "load_config", lambda *_args, **_kwargs: config)

    assert main_module.main(["status", "--simulate", "--db", "relative.db"]) == 2
    assert "absolute" in capsys.readouterr().err
    assert (
        main_module.main(
            [
                "status",
                "--simulate",
                "--db",
                str(symlink.resolve().parent / symlink.name),
            ]
        )
        == 2
    )
    assert "symlink" in capsys.readouterr().err
    assert main_module.main(["status", "--simulate", "--db", str(corrupt)]) == 1
    corrupt_result = json.loads(capsys.readouterr().out)
    assert corrupt_result["read_only_input_validation"]["healthy"] is False
    assert main_module.main(["status", "--simulate", "--db", str(wrong_profile)]) == 1
    wrong_result = json.loads(capsys.readouterr().out)
    assert wrong_result["read_only_input_validation"]["healthy"] is False
    assert main_module.main(["health", "--simulate", "--db", str(guardless)]) == 1
    guard_result = json.loads(capsys.readouterr().out)
    assert guard_result["healthy"] is False
    assert (
        guard_result["append_only_trigger_count"]
        < guard_result["expected_append_only_trigger_count"]
    )


def test_export_manifest_db_override_includes_only_exact_verified_artifact(
    monkeypatch, capsys, tmp_path
):
    config = _config(tmp_path)
    selected = (tmp_path / "selected.db").resolve()
    sibling = (tmp_path / "trades_sim_20260805.db").resolve()
    ResearchRepository(selected).initialize(database_utc_date="2026-08-06")
    ResearchRepository(sibling).initialize(database_utc_date="2026-08-05")
    monkeypatch.setattr(main_module, "load_config", lambda *_args, **_kwargs: config)

    code = main_module.main(["export-manifest", "--simulate", "--db", str(selected)])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [item["path"] for item in payload["files"]] == [str(selected)]
    assert payload["files"][0]["role"] == "archive"
    assert payload["status"]["shards"]["sibling_scan_performed"] is False


def test_db_override_is_not_a_run_or_config_option():
    with pytest.raises(SystemExit):
        main_module.main(["run", "--simulate", "--db", "/tmp/evidence.db"])
    with pytest.raises(SystemExit):
        main_module.main(["config", "--simulate", "--db", "/tmp/evidence.db"])


def test_cli_storage_stop_precedes_log_and_bot_construction(monkeypatch, tmp_path):
    config = _config(tmp_path)
    monkeypatch.setattr(main_module, "load_config", lambda *_args, **_kwargs: config)

    class StopRepository:
        def __init__(self, *_args, **_kwargs):
            pass

        def inspect_storage(self, **_kwargs):
            return {"guard_state": "STOP"}

    def unexpected(*_args, **_kwargs):
        raise AssertionError("STOP must precede logs and bot construction")

    monkeypatch.setattr(main_module, "ResearchRepository", StopRepository)
    monkeypatch.setattr(main_module, "setup_logging", unexpected)
    monkeypatch.setattr(main_module, "PolymarketResearchBot", unexpected)

    assert main_module.main(["run", "--simulate"]) == 1
