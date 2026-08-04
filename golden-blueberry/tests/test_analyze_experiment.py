"""Read-only post-mortem contract for the Blueberry A/B databases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

from scripts.analyze_experiment import _window, analyze


def _database(
    path: Path,
    *,
    min_surge: float,
    job_name: str,
    max_positions: int = 10,
) -> None:
    config_hash = f"config-{job_name}"
    config = {
        "schema_version": 1,
        "strategy_name": "golden-blueberry",
        "mode": "live",
        "trading": {
            "strategy_source_digest": "a" * 64,
            "entry": {"min_surge": min_surge},
            "max_positions": max_positions,
        },
    }
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE strategy_configs (
                config_hash TEXT PRIMARY KEY,
                config_json TEXT NOT NULL
            );
            CREATE TABLE run_audits (
                run_id TEXT PRIMARY KEY,
                strategy_name TEXT NOT NULL,
                job_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                started_at TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE entry_signal_decisions (
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                surge REAL NOT NULL,
                observed_at TEXT NOT NULL
            );
            CREATE TABLE trades (
                status TEXT NOT NULL,
                buy_confirmed_size REAL,
                buy_confirmed_vwap REAL,
                buy_confirmed_fee_usdc REAL,
                sell_confirmed_size REAL,
                sell_confirmed_vwap REAL,
                sell_confirmed_fee_usdc REAL,
                settlement_pnl_assumption REAL,
                buy_timestamp TEXT NOT NULL,
                mode TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO strategy_configs VALUES (?, ?)",
            (config_hash, json.dumps(config)),
        )
        connection.execute(
            "INSERT INTO run_audits VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"run-{job_name}",
                "golden-blueberry",
                job_name,
                "live",
                config_hash,
                "2026-08-04 12:00:00.000000",
                "SUCCESS",
            ),
        )
        connection.execute(
            "INSERT INTO entry_signal_decisions VALUES (?, ?, ?, ?)",
            ("candidate", "candidate", min_surge, "2026-08-04 12:01:00.000000"),
        )
        connection.execute(
            "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "COMPLETED",
                5.5,
                0.90,
                0.01,
                5.5,
                0.97,
                0.01,
                None,
                "2026-08-04 12:02:00.000000",
                "live",
            ),
        )


def test_window_uses_sqlite_datetime_representation():
    assert _window("2026-08-04", "2026-08-04") == (
        "2026-08-04 00:00:00",
        "2026-08-05 00:00:00",
    )


def test_analyzer_reads_both_arms_without_mutating_databases(tmp_path):
    arm_a = tmp_path / "a.db"
    arm_b = tmp_path / "b.db"
    _database(arm_a, min_surge=0.02, job_name="blueberry-a")
    _database(arm_b, min_surge=0.05, job_name="blueberry-b")
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (arm_a, arm_b)
    }

    report = analyze(
        arm_a,
        arm_b,
        tmp_path / "report",
        "2026-08-04",
        "2026-08-04",
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "INSUFFICIENT_CONFIRMED_SAMPLE"
    assert payload["issues"] == []
    assert payload["review_window"] == {
        "start_utc": "2026-08-04T00:00:00Z",
        "end_exclusive_utc": "2026-08-05T00:00:00Z",
    }
    assert payload["arms"]["A"]["cohorts"][0]["job_name"] == "blueberry-a"
    assert payload["arms"]["B"]["cohorts"][0]["min_surge"] == 0.05
    assert (
        payload["arms"]["A"]["cohorts"][0]["common_contract_sha256"]
        == payload["arms"]["B"]["cohorts"][0]["common_contract_sha256"]
    )
    assert payload["arms"]["A"]["trades"]["confirmed_closed"] == 1
    assert payload["arms"]["A"]["trades"]["confirmed_net_pnl_usdc"] > 0
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (arm_a, arm_b)
    } == before


def test_analyzer_rejects_non_treatment_config_difference(tmp_path):
    arm_a = tmp_path / "a.db"
    arm_b = tmp_path / "b.db"
    _database(arm_a, min_surge=0.02, job_name="blueberry-a")
    _database(
        arm_b,
        min_surge=0.05,
        job_name="blueberry-b",
        max_positions=20,
    )

    report = analyze(
        arm_a,
        arm_b,
        tmp_path / "report",
        "2026-08-04",
        "2026-08-04",
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "NOT_EVALUABLE_EVIDENCE_CONTRACT"
    assert "arms_have_different_common_contract" in payload["issues"]


def test_analyzer_requires_distinct_databases(tmp_path):
    arm_a = tmp_path / "a.db"
    _database(arm_a, min_surge=0.02, job_name="blueberry-a")

    try:
        analyze(
            arm_a,
            arm_a,
            tmp_path / "report",
            "2026-08-04",
            "2026-08-04",
        )
    except ValueError as error:
        assert str(error) == "A/B arms must use different database files"
    else:
        raise AssertionError("same database must not be accepted for both arms")
