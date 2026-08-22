from __future__ import annotations

import json
import sqlite3

import pytest

from polybot.analyzer import analyze_database, analyze_databases
from polybot.db.repository import ResearchRepository


CONTRACT = "sports-inplay-match-winner-v1"


def seeded_database(tmp_path, name: str, job: str, cadence: int, arm: str):
    repository = ResearchRepository(
        tmp_path / name, busy_timeout_ms=1000, data_contract=CONTRACT
    )
    repository.record_config(
        {
            "config_hash": f"config-{arm}",
            "strategy_source_digest": "source",
            "preregistration_sha256": "pre",
            "job_name": job,
            "mode": "sim",
            "config_json": json.dumps(
                {
                    "trading": {
                        "cadence_minutes": cadence,
                        "cadence_arm": arm,
                    }
                }
            ),
            "first_seen_at": "2026-08-22T16:15:00Z",
        }
    )
    repository.record_run_event(
        {
            "event_id": f"success-{arm}",
            "run_id": "run",
            "event_type": "SUCCEEDED",
            "observed_at": "2026-08-22T16:16:00Z",
            "config_hash": f"config-{arm}",
            "strategy_source_digest": "source",
            "detail_json": "{}",
        }
    )
    return repository


def add_winning_episode(repository: ResearchRepository, *, entered_at: str) -> None:
    with sqlite3.connect(repository.path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """
            INSERT INTO hypothetical_episodes(
                episode_id,decision_id,run_id,condition_id,event_id,event_title,
                question,token_id,outcome_index,outcome_label,threshold,cadence_arm,
                match_winner_class,entry_provenance,entered_at,end_date,
                game_start_time,sports_phase,liquidity,volume_total,fee_rate,
                entry_best_ask,entry_vwap,entry_shares,entry_cost
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "episode",
                "decision",
                "run",
                "condition",
                "event",
                "Team A vs Team B",
                "Team A vs Team B",
                "token",
                0,
                "Team A",
                0.97,
                "FAST_1M",
                "ALIGNED_TWO_TEAM_MONEYLINE",
                "UPWARD_CROSS",
                entered_at,
                "2026-08-22T20:00:00Z",
                "2026-08-22T15:00:00Z",
                "IN_PLAY_EXPLICIT",
                100,
                10,
                0.05,
                0.97,
                0.97,
                5 / 0.97,
                5,
            ),
        )
        connection.execute(
            "INSERT INTO resolution_observations VALUES(?,?,?,?,?,?,?,?)",
            (
                "resolution",
                "run-2",
                "condition",
                "2026-08-22T21:00:00Z",
                0,
                "request",
                "a" * 64,
                "{}",
            ),
        )
        shares = 5 / 0.97
        gross = shares * 0.78
        fee = shares * 0.05 * 0.78 * 0.22
        connection.execute(
            "INSERT INTO counterfactual_exit_policies VALUES(?,?,?,?,?,?)",
            (
                "hold",
                "episode",
                "run",
                "HOLD_TO_RESOLUTION",
                None,
                entered_at,
            ),
        )
        connection.execute(
            "INSERT INTO counterfactual_exit_policies VALUES(?,?,?,?,?,?)",
            ("stop", "episode", "run", "STOP_0.80", 0.80, entered_at),
        )
        connection.execute(
            "INSERT INTO stop_execution_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "attempt",
                "stop",
                "episode",
                "run-stop",
                None,
                "2026-08-22T15:35:00Z",
                0.80,
                0.93,
                0.79,
                shares,
                shares,
                0,
                0.78,
                gross,
                0.05,
                fee,
                gross - fee,
                2,
                "FULL_EXIT",
                0.02,
                0.14,
            ),
        )
        connection.execute(
            "INSERT INTO counterfactual_stop_exits VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "exit",
                "stop",
                "episode",
                "run-stop",
                "attempt",
                "2026-08-22T15:35:00Z",
                "2026-08-22T15:35:00Z",
                0.80,
                0.79,
                0.78,
                shares,
                shares,
                gross,
                fee,
                gross - fee,
                1,
                0.02,
            ),
        )


def test_analyzer_uses_fee_resolution_and_stop_depth(tmp_path) -> None:
    repository = seeded_database(
        tmp_path, "white.db", "watermelon-white-1m", 1, "FAST_1M"
    )
    add_winning_episode(repository, entered_at="2026-08-22T15:31:00Z")
    result = analyze_database(repository.path)
    assert result["quick_check"] == "ok"
    assert result["cadence_arm"] == "FAST_1M"
    threshold = result["entry_thresholds"]["0.97"]["all"]
    assert threshold["resolved"] == 1
    assert threshold["wins"] == 1
    assert threshold["event_equal_fee_net_roi_pct"] > 0
    policies = result["stop_policy_comparison"]["0.97"]
    assert policies["HOLD_TO_RESOLUTION"]["event_equal_fee_net_roi_pct"] > 0
    assert policies["STOP_0.80"]["event_equal_fee_net_roi_pct"] < 0
    assert policies["STOP_0.80"]["gap_below_stop_p50"] == pytest.approx(0.02)


def test_analyzer_excludes_failed_prior_source_cohort(tmp_path) -> None:
    repository = seeded_database(
        tmp_path, "white.db", "watermelon-white-1m", 1, "FAST_1M"
    )
    repository.record_run_event(
        {
            "event_id": "old-failure",
            "run_id": "old-run",
            "event_type": "FAILED",
            "observed_at": "2026-08-22T16:11:00Z",
            "config_hash": "old-config",
            "strategy_source_digest": "old-source",
            "detail_json": "{}",
        }
    )
    repository.record_issue(
        run_id="old-run",
        severity="CRITICAL",
        issue_type="GAMMA_CURSOR_INCOMPLETE",
        detail={"pages": 10},
    )
    result = analyze_database(repository.path)
    assert result["cohort_run_count"] == 1
    assert result["run_events"] == {"SUCCEEDED": 1}
    assert result["issues"] == []


def test_multi_database_analyzer_pairs_same_episode_keys(tmp_path) -> None:
    white = seeded_database(
        tmp_path, "white.db", "watermelon-white-1m", 1, "FAST_1M"
    )
    grey = seeded_database(
        tmp_path, "grey.db", "watermelon-grey-5m", 5, "CONTROL_5M"
    )
    add_winning_episode(white, entered_at="2026-08-22T15:31:00Z")
    add_winning_episode(grey, entered_at="2026-08-22T15:35:00Z")
    result = analyze_databases([white.path, grey.path])
    assert result["pairing"]["matched_episode_keys"] == 1
    assert result["pairing"]["entry_time_delta_seconds_p50"] == 240
