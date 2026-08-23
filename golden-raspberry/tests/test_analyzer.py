from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

from polybot.config import PROJECT_ROOT, load_config
from polybot.db.repository import ResearchRepository

from scripts.analyze_experiment import _primary_gate, _summarize_outcomes, analyze_db


def test_analyzer_is_read_only_and_reports_empty_health(monkeypatch, tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    path = tmp_path / "trades_sim.db"
    repository = ResearchRepository(path)
    repository.initialize(config)
    result = analyze_db(
        "DO",
        path,
        datetime(2026, 8, 23, 20, tzinfo=timezone.utc),
        datetime(2026, 8, 24, 20, tzinfo=timezone.utc),
    )
    assert result["quick_check"] == "ok"
    assert result["contract"]["shard_index"] == 0
    assert result["health_pass"] is False


def test_primary_gate_uses_one_fleet_cluster_bootstrap_and_day_union():
    rows = []
    for index in range(60):
        day = index % 30 + 1
        common = {
            "namespace": f"shard-{index % 3}",
            "arm": "MI",
            "condition_id": f"condition-{index}",
            "event_id": f"event-{index % 30}",
            "cluster_key": f"shard-{index % 3}:event-{index % 30}",
            "evaluated_at": f"2026-08-{day:02d}T01:00:00Z",
            "matched_pair_id": f"pair-{index}",
            "pair_key": f"shard-{index % 3}:pair-{index}",
            "case_id": f"case-{index}",
        }
        rows.extend(
            [
                {
                    **common,
                    "case_kind": "SIGNAL",
                    "executable_return_bps": 100.0,
                    "base_stressed_return_bps": 89.6,
                    "severe_stressed_return_bps": 27.5,
                },
                {
                    **common,
                    "arm": "DO",
                    "evaluated_at": f"2026-08-{day:02d}T00:50:00Z",
                    "case_kind": "SIGNAL",
                    "executable_return_bps": 10.0,
                    "base_stressed_return_bps": -0.4,
                    "severe_stressed_return_bps": -62.5,
                },
                {
                    **common,
                    "case_kind": "CONTROL",
                    "executable_return_bps": 0.0,
                    "base_stressed_return_bps": -10.4,
                    "severe_stressed_return_bps": -72.5,
                },
                {
                    **common,
                    "case_kind": "OPPOSITE",
                    "executable_return_bps": -50.0,
                    "base_stressed_return_bps": -60.4,
                    "severe_stressed_return_bps": -122.5,
                },
            ]
        )

    fleet = _summarize_outcomes(rows)
    gate = _primary_gate([{"health_pass": True}] * 3, fleet, 30)

    assert fleet["MI"]["distinct_utc_days"] == 30
    assert fleet["MI"]["event_clusters"] == 30
    assert gate["totals"]["distinct_utc_days"] == 30
    assert gate["checks"]["mi_fleet_severe_lower_positive"] is True
    assert gate["checks"]["mi_minus_do_severe_lower_positive"] is True
    assert gate["pass"] is True


def test_analyzer_owns_lifecycle_by_started_range_and_gates_failed_duration(tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    path = tmp_path / "trades_sim.db"
    repository = ResearchRepository(path)
    repository.initialize(config)
    repository.register_config(config, git_commit=None)
    repository.claim_cycle_slot(
        config,
        claimed_at=datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc),
        invocation_id="invocation-failed",
        run_id="run-failed",
    )
    for event_id, run_id, event_type, event_at, details in (
        ("e1", "run-failed", "STARTED", "2026-08-23T20:00:00Z", {}),
        (
            "e2",
            "run-failed",
            "FAILED",
            "2026-08-23T20:03:50Z",
            {
                "duration_seconds": 230.0,
                "cooperative_cycle_budget_seconds": 225,
                "hard_cycle_limit_seconds": 240,
            },
        ),
        ("e3", "outside-run", "STARTED", "2026-08-23T19:59:59Z", {}),
        (
            "e4",
            "outside-run",
            "SUCCEEDED",
            "2026-08-23T20:00:30Z",
            {"duration_seconds": 31.0},
        ),
    ):
        repository.record_research_run_event(
            {
                "event_id": event_id,
                "run_id": run_id,
                "config_hash": config.config_hash,
                "event_type": event_type,
                "event_at": event_at,
                "details_json": json.dumps(details),
                "error_type": "RuntimeError" if event_type == "FAILED" else None,
                "error_message": "deadline" if event_type == "FAILED" else None,
            }
        )

    result = analyze_db(
        "DO",
        path,
        datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 23, 20, 5, tzinfo=timezone.utc),
    )

    assert result["run_lifecycle"]["ownership"] == "STARTED_IN_REVIEW_RANGE"
    assert result["run_lifecycle"]["total_runs"] == 1
    assert result["run_lifecycle"]["failed_runs"] == 1
    assert result["run_lifecycle"]["terminal_events_in_range_owned_elsewhere"] == 1
    assert result["runtime"]["max_seconds"] == 230.0
    assert result["runtime"]["failed_terminal_durations_seconds"] == [230.0]
    assert result["health_checks"]["cooperative_deadline"] is False
    assert result["health_checks"]["runtime_max"] is True


def test_analyzer_marks_review_outside_frozen_v3_window_unhealthy(tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    path = tmp_path / "trades_sim.db"
    repository = ResearchRepository(path)
    repository.initialize(config)

    result = analyze_db(
        "DO",
        path,
        datetime(2026, 8, 23, 19, 55, tzinfo=timezone.utc),
        datetime(2026, 8, 23, 20, 5, tzinfo=timezone.utc),
    )

    assert result["health_checks"]["frozen_contract_window"] is True
    assert result["health_checks"]["review_within_frozen_window"] is False
    assert result["health_pass"] is False


def test_analyzer_separates_atomic_universe_pair_from_availability_and_followup(tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    path = tmp_path / "trades_sim.db"
    repository = ResearchRepository(path)
    repository.initialize(config)
    repository.register_config(config, git_commit=None)
    repository.claim_cycle_slot(
        config,
        claimed_at=datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc),
        invocation_id="invocation-success",
        run_id="run-success",
    )
    for event_id, event_type, event_at in (
        ("started", "STARTED", "2026-08-23T20:00:00Z"),
        ("succeeded", "SUCCEEDED", "2026-08-23T20:00:01Z"),
    ):
        repository.record_research_run_event(
            {
                "event_id": event_id,
                "run_id": "run-success",
                "config_hash": config.config_hash,
                "event_type": event_type,
                "event_at": event_at,
                "details_json": json.dumps(
                    {
                        "duration_seconds": 1.0,
                        "cooperative_cycle_budget_seconds": 225,
                        "hard_cycle_limit_seconds": 240,
                    }
                ),
                "error_type": None,
                "error_message": None,
            }
        )
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """
            INSERT INTO market_sweeps VALUES(
              'sweep','run-success',1,?,?, '2026-08-23T20:00:00Z',
              '2026-08-23T20:00:01Z',1,1,1,1,1,?,'gzip-json-v1',?, '{}','{}','queue-echo-v3'
            )
            """,
            (config.config_hash, config.trading.strategy_source_digest, "0" * 64, b"x"),
        )
        connection.execute(
            """
            INSERT INTO market_observations VALUES(
              'observation','sweep','run-success','condition','market','event',NULL,NULL,
              '2026-08-23T20:00:00Z','2026-08-24T20:00:00Z',24,50000,100000,20000,
              '["yes","no"]','["Yes","No"]','[0.5,0.5]',NULL,NULL,NULL,?, ?,1,0,1,'[]'
            )
            """,
            ("1" * 64, "2" * 64),
        )
        for attempt_id, token, outcome_index, status in (
            ("attempt-yes", "yes", 0, "OBSERVED"),
            ("attempt-no", "no", 1, "EMPTY_BOOK"),
        ):
            connection.execute(
                """
                INSERT INTO orderbook_token_attempts(
                  attempt_id,sweep_id,run_id,condition_id,token_id,outcome_index,
                  outcome_label,attempt_role,status,request_id,logical_request_id,
                  request_started_at,received_at,error_type,error_message
                ) VALUES(?,?,?,?,?,?,?,'UNIVERSE',?,'same-request','logical-pair',
                         '2026-08-23T20:00:00Z','2026-08-23T20:00:01Z',NULL,NULL)
                """,
                (
                    attempt_id,
                    "sweep",
                    "run-success",
                    "condition",
                    token,
                    outcome_index,
                    "Yes" if outcome_index == 0 else "No",
                    status,
                ),
            )
        connection.execute(
            """
            INSERT INTO followup_claims VALUES(
              'follow-claim','case','run-success','2026-08-23T20:00:00Z',
              '2026-08-23T20:00:00Z','2026-08-23T20:15:00Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO followup_attempts VALUES(
              'followup','case','follow-claim','run-success','2026-08-23T20:00:00Z',
              'EMPTY_BOOK',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'{}'
            )
            """
        )

    result = analyze_db(
        "DO",
        path,
        datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 23, 20, 5, tzinfo=timezone.utc),
    )
    universe = result["source"]["universe"]
    followup = result["source"]["followup_only"]
    assert universe["same_request_atomicity_coverage"] == 1.0
    assert universe["normalized_pair_availability"] == 0.0
    assert universe["empty_book_tokens"] == 1
    assert followup["empty_book"] == 1
    assert followup["censored"] == 1
