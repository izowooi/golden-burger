"""Strict read-only post-mortem contract for Blueberry A/B databases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

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
    pnl = (0.97 - 0.90) * 5.5 - 0.01 - 0.01
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE strategy_configs (
                config_hash TEXT PRIMARY KEY, config_json TEXT NOT NULL
            );
            CREATE TABLE run_audits (
                run_id TEXT PRIMARY KEY, strategy_name TEXT NOT NULL,
                job_name TEXT NOT NULL, mode TEXT NOT NULL,
                config_hash TEXT NOT NULL, started_at TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE entry_signal_decisions (
                decision TEXT NOT NULL, reason TEXT NOT NULL,
                surge REAL NOT NULL, observed_at TEXT NOT NULL
            );
            CREATE TABLE candidate_execution_decisions (
                decision TEXT NOT NULL, stage TEXT NOT NULL,
                reason TEXT NOT NULL, observed_at TEXT NOT NULL
            );
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY, status TEXT NOT NULL,
                condition_id TEXT, event_id TEXT, buy_amount REAL,
                buy_order_id TEXT, sell_order_id TEXT,
                sell_residual_shares REAL, realized_pnl REAL, pnl_basis TEXT,
                settlement_pnl_assumption REAL, buy_timestamp TEXT NOT NULL,
                mode TEXT NOT NULL
            );
            CREATE TABLE order_submissions (
                submission_id TEXT PRIMARY KEY, order_id TEXT, token_id TEXT,
                side TEXT, requested_price REAL, requested_size REAL,
                submitted_at TEXT, simulation INTEGER, success INTEGER,
                response_status TEXT, latest_order_status TEXT,
                latest_size_matched REAL, latest_status_domain_error TEXT,
                needs_reconciliation INTEGER, reconciliation_error TEXT,
                reconciliation_proof TEXT, outcome_resolution TEXT
            );
            CREATE TABLE order_status_events (id INTEGER PRIMARY KEY);
            CREATE TABLE order_fills (
                submission_id TEXT, order_id TEXT, status TEXT, side TEXT,
                size REAL, price REAL, liquidity_role TEXT, fee_rate_bps REAL,
                fee_amount_usdc REAL, domain_error TEXT
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
            "INSERT INTO candidate_execution_decisions VALUES (?, ?, ?, ?)",
            ("submitted", "submission", "accepted", "2026-08-04 12:02:00.000000"),
        )
        connection.execute(
            "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "COMPLETED",
                "condition-paired",
                "event-paired",
                5.0,
                f"buy-{job_name}",
                f"sell-{job_name}",
                0.0,
                pnl,
                "exact_reconciled_buy_sell_confirmed_fills_net_known_fees",
                None,
                "2026-08-04 12:02:00.000000",
                "live",
            ),
        )
        for side, price in (("BUY", 0.90), ("SELL", 0.97)):
            order_id = f"{side.lower()}-{job_name}"
            submission_id = f"submission-{order_id}"
            connection.execute(
                """
                INSERT INTO order_submissions VALUES (
                    ?, ?, 'yes-token', ?, ?, 5.5,
                    '2026-08-04T12:02:00+00:00', 0, 1, 'ACCEPTED',
                    'MATCHED', 5.5, NULL, 0, NULL, NULL, NULL
                )
                """,
                (submission_id, order_id, side, price),
            )
            connection.execute(
                "INSERT INTO order_fills VALUES (?, ?, 'CONFIRMED', ?, 5.5, ?, 'TAKER', 20, 0.01, NULL)",
                (submission_id, order_id, side, price),
            )


def test_window_requires_exact_utc_instants():
    assert _window(
        "2026-08-04T12:34:56.123456Z", "2026-08-04T13:00:00+00:00"
    ) == (
        "2026-08-04 12:34:56.123456",
        "2026-08-04 13:00:00.000000",
    )
    with pytest.raises(ValueError, match="include Z"):
        _window("2026-08-04", "2026-08-05")
    with pytest.raises(ValueError, match="must be UTC"):
        _window("2026-08-04T00:00:00+09:00", "2026-08-05T00:00:00+09:00")


def test_analyzer_scores_exact_ledger_without_mutating_databases(tmp_path):
    arm_a = tmp_path / "a.db"
    arm_b = tmp_path / "b.db"
    _database(arm_a, min_surge=0.02, job_name="blueberry-a")
    _database(arm_b, min_surge=0.05, job_name="blueberry-b")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (arm_a, arm_b)}

    report = analyze(
        arm_a,
        arm_b,
        tmp_path / "report",
        "2026-08-04T12:00:00Z",
        "2026-08-04T13:00:00Z",
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "INSUFFICIENT_CONFIRMED_SAMPLE"
    assert payload["issues"] == []
    assert payload["arms"]["A"]["trades"]["confirmed_closed"] == 1
    assert payload["arms"]["A"]["trades"]["confirmed_net_pnl_usdc"] > 0
    assert payload["paired_overlap"]["shared_condition_count"] == 1
    assert payload["paired_overlap"]["shared_event_count"] == 1
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (arm_a, arm_b)} == before


def test_analyzer_rejects_non_treatment_config_difference(tmp_path):
    arm_a = tmp_path / "a.db"
    arm_b = tmp_path / "b.db"
    _database(arm_a, min_surge=0.02, job_name="blueberry-a")
    _database(arm_b, min_surge=0.05, job_name="blueberry-b", max_positions=20)

    report = analyze(
        arm_a,
        arm_b,
        tmp_path / "report",
        "2026-08-04T12:00:00Z",
        "2026-08-04T13:00:00Z",
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert "arms_have_different_common_contract" in payload["issues"]


def test_analyzer_rejects_size_mismatch_instead_of_scoring_minimum(tmp_path):
    arm_a = tmp_path / "a.db"
    arm_b = tmp_path / "b.db"
    _database(arm_a, min_surge=0.02, job_name="blueberry-a")
    _database(arm_b, min_surge=0.05, job_name="blueberry-b")
    with sqlite3.connect(arm_a) as connection:
        connection.execute(
            "UPDATE order_submissions SET latest_size_matched=5.4 WHERE side='SELL'"
        )
        connection.execute("UPDATE order_fills SET size=5.4 WHERE side='SELL'")

    report = analyze(
        arm_a,
        arm_b,
        tmp_path / "report",
        "2026-08-04T12:00:00Z",
        "2026-08-04T13:00:00Z",
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["arms"]["A"]["trades"]["confirmed_closed"] == 0
    assert payload["arms"]["A"]["trades"]["evidence_rejection_reasons"] == {
        "buy_sell_size_mismatch": 1
    }
    assert "arm_A_closed_trade_evidence_rejected" in payload["issues"]


def test_analyzer_accepts_only_explicit_sub_point_zero_one_residual_contract(
    tmp_path,
):
    arm_a = tmp_path / "a.db"
    arm_b = tmp_path / "b.db"
    _database(arm_a, min_surge=0.02, job_name="blueberry-a")
    _database(arm_b, min_surge=0.05, job_name="blueberry-b")
    sell_size = 5.495
    allocated_buy_fee = 0.01 * (sell_size / 5.5)
    pnl = (0.97 - 0.90) * sell_size - allocated_buy_fee - 0.01
    with sqlite3.connect(arm_a) as connection:
        connection.execute(
            "UPDATE order_submissions SET latest_size_matched=? WHERE side='SELL'",
            (sell_size,),
        )
        connection.execute(
            "UPDATE order_fills SET size=? WHERE side='SELL'", (sell_size,)
        )
        connection.execute(
            """
            UPDATE trades SET status='RESIDUAL', sell_residual_shares=0.005,
                pnl_basis=?, realized_pnl=?
            """,
            (
                "exact_reconciled_confirmed_fills_net_known_fees_sub_0.01_sell_residual",
                pnl,
            ),
        )

    report = analyze(
        arm_a,
        arm_b,
        tmp_path / "report",
        "2026-08-04T12:00:00Z",
        "2026-08-04T13:00:00Z",
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["arms"]["A"]["trades"]["confirmed_closed"] == 1
    assert payload["arms"]["A"]["trades"]["evidence_rejected_closed"] == 0
    assert payload["arms"]["A"]["unresolved_exposure"]["trade_status_counts"] == {
        "RESIDUAL": 1
    }


def test_analyzer_reports_untracked_unknown_buy_exposure(tmp_path):
    arm_a = tmp_path / "a.db"
    arm_b = tmp_path / "b.db"
    _database(arm_a, min_surge=0.02, job_name="blueberry-a")
    _database(arm_b, min_surge=0.05, job_name="blueberry-b")
    with sqlite3.connect(arm_a) as connection:
        connection.execute(
            """
            INSERT INTO order_submissions VALUES (
                'unknown', NULL, 'other-token', 'BUY', 0.90, 5.5,
                '2026-08-04T12:03:00+00:00', 0, 0,
                'SUBMIT_OUTCOME_UNKNOWN', NULL, NULL, NULL, 0,
                NULL, NULL, NULL
            )
            """
        )
    report = analyze(
        arm_a,
        arm_b,
        tmp_path / "report",
        "2026-08-04T12:00:00Z",
        "2026-08-04T13:00:00Z",
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    exposure = payload["arms"]["A"]["unresolved_exposure"]
    assert exposure["untracked_buy_reservation_count"] == 1
    assert exposure["conservative_open_notional_usdc"] == pytest.approx(4.95)
    assert "arm_A_unresolved_exposure" in payload["issues"]


def test_analyzer_requires_distinct_databases(tmp_path):
    arm = tmp_path / "a.db"
    _database(arm, min_surge=0.02, job_name="blueberry-a")
    with pytest.raises(ValueError, match="different database"):
        analyze(
            arm,
            arm,
            tmp_path / "report",
            "2026-08-04T12:00:00Z",
            "2026-08-04T13:00:00Z",
        )
