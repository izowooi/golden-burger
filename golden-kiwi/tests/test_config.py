"""Golden Kiwi config, arm isolation, and research-only safety."""

import json
from datetime import timedelta
import sqlite3

import pytest

import polybot.config as config_module
from polybot.config import (
    ANALYZER_SCHEMA_VERSION,
    PREREGISTRATION_SHA256,
    REQUIRED_EXCLUDED_CATEGORIES,
    load_config,
)


ARM_CASES = [
    (3, 0.01, "A", "kiwi-sim-a-3x1"),
    (3, 0.02, "B", "kiwi-sim-b-3x2"),
    (5, 0.01, "C", "kiwi-sim-c-5x1"),
    (5, 0.02, "D", "kiwi-sim-d-5x2"),
]
COLLECTION_CASES = [
    (3, 0.01, "kiwi-sim-a-3x1", 0),
    (3, 0.02, "kiwi-sim-b-3x2", 1),
    (5, 0.01, "kiwi-sim-c-5x1", 2),
    (5, 0.02, "kiwi-sim-d-5x2", 3),
]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    for key in list(__import__("os").environ):
        if key.startswith("POLYBOT_") or key.startswith("POLYMARKET_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)


def test_default_simulation_needs_no_wallet_secrets():
    config = load_config("missing.yaml", "kiwi-sim-b-3x2")
    assert config.simulation_mode is True
    assert config.api.private_key == ""
    assert config.api.funder_address == ""
    assert config.db_path == (
        config_module.PROJECT_ROOT
        / "data"
        / "kiwi-sim-b-3x2"
        / "trades_sim.db"
    )
    assert config.trading.arm_name == "B"
    assert config.trading.buy_amount_usdc == 5
    assert config.trading.max_positions == 3
    assert config.trading.max_open_notional_usdc == 15
    assert tuple(config.trading.excluded_categories) == REQUIRED_EXCLUDED_CATEGORIES
    assert config.trading.archive.fetch_min_liquidity == 20_000
    assert config.trading.archive.fetch_min_total_volume == 10_000
    assert config.trading.archive.max_fetch_pages == 53
    assert config.trading.archive.max_fetch_markets == 5_330
    assert config.trading.archive.max_sweep_seconds == 120


@pytest.mark.parametrize(("steps", "move", "arm", "job"), ARM_CASES)
def test_only_four_registered_arms_resolve(monkeypatch, steps, move, arm, job):
    monkeypatch.setenv("POLYBOT_CONFIRMATION_STEPS", str(steps))
    monkeypatch.setenv("POLYBOT_MIN_CUMULATIVE_MOVE", str(move))
    assert load_config("missing.yaml", job).trading.arm_name == arm


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("POLYBOT_CONFIRMATION_STEPS", "4"),
        ("POLYBOT_MIN_CUMULATIVE_MOVE", "0.015"),
        ("POLYBOT_BUY_AMOUNT", "6"),
        ("POLYBOT_MAX_POSITIONS", "4"),
        ("POLYBOT_MAX_OPEN_NOTIONAL_USDC", "20"),
        ("POLYBOT_MIN_LIQUIDITY", "19999"),
        ("POLYBOT_MIN_VOLUME_24H", "9999"),
        ("POLYBOT_HOLD_MINUTES", "61"),
        ("POLYBOT_MAX_SPREAD", "0.03"),
        ("POLYBOT_SNAPSHOT_RETENTION_DAYS", "61"),
        ("POLYBOT_FETCH_MIN_LIQUIDITY", "19999"),
        ("POLYBOT_FETCH_MIN_TOTAL_VOLUME", "9999"),
        ("POLYBOT_MAX_FETCH_PAGES", "52"),
        ("POLYBOT_MAX_FETCH_MARKETS", "5329"),
        ("POLYBOT_MAX_SWEEP_SECONDS", "119"),
    ],
)
def test_every_non_arm_runtime_treatment_knob_is_frozen(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="frozen|registered arm"):
        load_config("missing.yaml", "bad")


def test_explicit_live_override_fails_without_credentials():
    with pytest.raises(ValueError, match="research/simulation-only"):
        load_config("missing.yaml", "live", simulation_mode=False)


def test_yaml_live_mode_fails_without_credentials(tmp_path):
    path = tmp_path / "live.yaml"
    path.write_text("simulation_mode: false\n", encoding="utf-8")
    with pytest.raises(ValueError, match="research/simulation-only"):
        load_config(str(path), "live")


def test_job_names_isolate_simulation_databases(monkeypatch):
    first = load_config("missing.yaml", "kiwi-sim-b-3x2")
    monkeypatch.setenv("POLYBOT_CONFIRMATION_STEPS", "3")
    monkeypatch.setenv("POLYBOT_MIN_CUMULATIVE_MOVE", "0.01")
    second = load_config("missing.yaml", "kiwi-sim-a-3x1")
    assert first.db_path != second.db_path


def test_database_path_is_project_root_absolute_not_process_cwd(
    monkeypatch, tmp_path
):
    first = load_config("missing.yaml", "kiwi-sim-b-3x2").db_path
    elsewhere = tmp_path / "nested" / "working-directory"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    second = load_config("missing.yaml", "kiwi-sim-b-3x2").db_path
    assert first == second
    assert first.is_absolute()


def test_canonical_job_cannot_run_another_arm(monkeypatch):
    monkeypatch.setenv("POLYBOT_CONFIRMATION_STEPS", "5")
    monkeypatch.setenv("POLYBOT_MIN_CUMULATIVE_MOVE", "0.02")
    with pytest.raises(ValueError, match="arm B"):
        load_config("missing.yaml", "kiwi-sim-b-3x2")


def test_noncanonical_job_is_rejected():
    with pytest.raises(ValueError, match="canonical job"):
        load_config("missing.yaml", "default")


def test_existing_database_with_another_arm_is_rejected(tmp_path):
    db_path = tmp_path / "data" / "kiwi-sim-b-3x2" / "trades_sim.db"
    db_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE strategy_configs (
            config_hash TEXT PRIMARY KEY,
            config_json TEXT NOT NULL
        );
        CREATE TABLE run_audits (
            run_id TEXT PRIMARY KEY,
            strategy_name TEXT,
            job_name TEXT,
            config_hash TEXT,
            status TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO strategy_configs VALUES (?, ?)",
        (
            "arm-a",
            json.dumps(
                {
                    "trading": {
                        "entry": {
                            "confirmation_steps": 3,
                            "min_cumulative_move": 0.01,
                        }
                    }
                }
            ),
        ),
    )
    connection.execute(
        "INSERT INTO run_audits VALUES (?, ?, ?, ?, ?)",
        (
            "run-a",
            "golden-kiwi",
            "kiwi-sim-b-3x2",
            "arm-a",
            "SUCCESS",
        ),
    )
    connection.commit()
    connection.close()

    with pytest.raises(ValueError, match="다른 arm cohort"):
        load_config("missing.yaml", "kiwi-sim-b-3x2")


def test_ambient_wallet_credentials_are_ignored(monkeypatch):
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "must-not-be-read")
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "must-not-be-read")
    config = load_config("missing.yaml", "kiwi-sim-b-3x2")
    assert config.api.private_key == ""
    assert config.api.funder_address == ""


def test_absent_experiment_window_is_nonpromotion_smoke_mode():
    config = load_config("missing.yaml", "kiwi-sim-b-3x2")
    assert config.experiment.enabled is False
    assert config.experiment.window_start is None
    assert config.experiment.window_end is None


def test_collection_window_env_is_all_or_none(monkeypatch):
    monkeypatch.setenv(
        "POLYBOT_EXPERIMENT_START_UTC", "2026-08-13T00:00:00Z"
    )
    with pytest.raises(ValueError, match="셋 모두"):
        load_config("missing.yaml", "kiwi-sim-b-3x2")


def test_collection_window_is_exact_shared_30_day_utc_contract(monkeypatch):
    monkeypatch.setenv(
        "POLYBOT_EXPERIMENT_START_UTC", "2026-08-13T00:00:00Z"
    )
    monkeypatch.setenv(
        "POLYBOT_EXPERIMENT_END_UTC", "2026-09-12T00:00:00Z"
    )
    monkeypatch.setenv("POLYBOT_CADENCE_OFFSET_MINUTE", "1")
    experiment = load_config(
        "missing.yaml", "kiwi-sim-b-3x2"
    ).experiment
    assert experiment.enabled is True
    assert experiment.window_end - experiment.window_start == timedelta(
        days=30
    )
    assert experiment.expected_cadence_minutes == 5
    assert experiment.expected_offset_minute == 1
    assert experiment.analyzer_version == ANALYZER_SCHEMA_VERSION
    assert experiment.preregistration_sha256 == PREREGISTRATION_SHA256


def test_collection_rejects_another_exact_30_day_window(monkeypatch):
    monkeypatch.setenv(
        "POLYBOT_EXPERIMENT_START_UTC", "2026-08-14T00:00:00Z"
    )
    monkeypatch.setenv(
        "POLYBOT_EXPERIMENT_END_UTC", "2026-09-13T00:00:00Z"
    )
    monkeypatch.setenv("POLYBOT_CADENCE_OFFSET_MINUTE", "1")

    with pytest.raises(ValueError, match="preregistered"):
        load_config("missing.yaml", "kiwi-sim-b-3x2")


@pytest.mark.parametrize(
    ("steps", "move", "job", "offset"),
    COLLECTION_CASES,
)
def test_collection_offset_is_fixed_per_canonical_job(
    monkeypatch,
    steps,
    move,
    job,
    offset,
):
    monkeypatch.setenv("POLYBOT_CONFIRMATION_STEPS", str(steps))
    monkeypatch.setenv("POLYBOT_MIN_CUMULATIVE_MOVE", str(move))
    monkeypatch.setenv(
        "POLYBOT_EXPERIMENT_START_UTC", "2026-08-13T00:00:00Z"
    )
    monkeypatch.setenv(
        "POLYBOT_EXPERIMENT_END_UTC", "2026-09-12T00:00:00Z"
    )
    monkeypatch.setenv("POLYBOT_CADENCE_OFFSET_MINUTE", str(offset))

    experiment = load_config("missing.yaml", job).experiment

    assert experiment.expected_offset_minute == offset


def test_collection_rejects_offset_from_another_canonical_job(monkeypatch):
    monkeypatch.setenv(
        "POLYBOT_EXPERIMENT_START_UTC", "2026-08-13T00:00:00Z"
    )
    monkeypatch.setenv(
        "POLYBOT_EXPERIMENT_END_UTC", "2026-09-12T00:00:00Z"
    )
    monkeypatch.setenv("POLYBOT_CADENCE_OFFSET_MINUTE", "3")

    with pytest.raises(ValueError, match="1으로 고정"):
        load_config("missing.yaml", "kiwi-sim-b-3x2")


@pytest.mark.parametrize("mode", ["active", "close_only", "archive_only"])
def test_lifecycle_is_an_operational_control_not_an_arm(monkeypatch, mode):
    monkeypatch.setenv("POLYBOT_LIFECYCLE_MODE", mode)
    config = load_config("missing.yaml", "kiwi-sim-b-3x2")
    assert config.trading.lifecycle_mode == mode
    assert config.trading.arm_name == "B"
