from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys

import pytest

from polybot.db.models import init_database


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "analyze_experiment.py"
SPEC = importlib.util.spec_from_file_location("kiwi_analyze_experiment_v2", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
analyzer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analyzer
SPEC.loader.exec_module(analyzer)

UTC = timezone.utc
START = datetime(2026, 8, 1, tzinfo=UTC)
END = START + timedelta(days=30)
GIT_COMMIT = "c" * 40


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _config_payload(arm):
    expected = analyzer.CANONICAL_ARMS[arm]
    return {
        "schema_version": 1,
        "strategy_name": "golden-kiwi",
        "mode": "sim",
        "trading": {
            **analyzer.FROZEN_TRADING_VALUES,
            "entry": {
                **analyzer.FROZEN_ENTRY_VALUES,
                "confirmation_steps": expected["confirmation_steps"],
                "min_cumulative_move": expected["min_cumulative_move"],
            },
            "archive": dict(analyzer.FROZEN_ARCHIVE_VALUES),
            "excluded_categories": list(analyzer.FROZEN_EXCLUDED_CATEGORIES),
        },
    }


def _insert_run(connection, *, arm, run_id, started_at, status="SUCCESS"):
    payload = _config_payload(arm)
    config_json = _canonical_json(payload)
    config_hash = hashlib.sha256(config_json.encode()).hexdigest()
    connection.execute(
        """
        INSERT OR IGNORE INTO strategy_configs (
            config_hash, schema_version, strategy_name, mode, config_json,
            first_seen_at, git_commit
        ) VALUES (?, 1, 'golden-kiwi', 'sim', ?, ?, ?)
        """,
        (config_hash, config_json, START.isoformat(), GIT_COMMIT),
    )
    connection.execute(
        """
        INSERT INTO run_audits (
            run_id, schema_version, strategy_name, job_name, mode,
            config_hash, git_commit, started_at, finished_at, status
        ) VALUES (?, 1, 'golden-kiwi', ?, 'sim', ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            analyzer.CANONICAL_ARMS[arm]["job_name"],
            config_hash,
            GIT_COMMIT,
            started_at.isoformat(),
            (started_at + timedelta(seconds=30)).isoformat(),
            status,
        ),
    )
    if status == "SUCCESS":
        connection.execute(
            """
            INSERT INTO market_sweeps (
                sweep_id, schema_version, run_id, started_at, completed_at,
                cursor_complete, pages, raw_market_count,
                unique_condition_count, qualified_market_count,
                excluded_condition_count, exclusion_counts_json,
                missing_condition_id_count, duplicate_raw_count,
                min_liquidity, min_volume, membership_digest_sha256,
                snapshot_eligible_count, snapshotted_market_count
            ) VALUES (
                ?, 1, ?, ?, ?, 1, 1, 1, 1, 1, 0, '{}', 0, 0,
                1000, 0, ?, 1, 1
            )
            """,
            (
                f"sweep-{run_id}",
                run_id,
                started_at.isoformat(),
                (started_at + timedelta(seconds=20)).isoformat(),
                "d" * 64,
            ),
        )
    return config_hash


def _insert_decision(
    connection,
    *,
    arm,
    decision_key,
    scan_at,
    run_id,
    event_id,
    exit_return,
    followup=True,
    first_absent=False,
):
    entry_ask = 0.50
    steps = analyzer.CANONICAL_ARMS[arm]["confirmation_steps"]
    step_move = analyzer.CANONICAL_ARMS[arm]["min_cumulative_move"] / steps
    prices = [0.495 - step_move * (steps - index) for index in range(steps + 1)]
    timestamps = [
        scan_at - timedelta(minutes=5 * (steps - index)) for index in range(steps + 1)
    ]
    first = START + timedelta(minutes="ABCD".index(arm))
    snapshot_ids = []
    for index, (timestamp, price) in enumerate(zip(timestamps, prices)):
        run_index = int((timestamp - first).total_seconds() // 300)
        snapshot_run_id = run_id if index == steps else f"run-{arm}-{run_index}"
        snapshot = connection.execute(
            """
            INSERT INTO market_snapshots (
                condition_id, probability, best_bid, best_ask, spread,
                run_id, timestamp
            ) VALUES (?, ?, ?, ?, 0.01, ?, ?)
            """,
            (
                f"condition-{decision_key}",
                price,
                0.49 if index == steps else price - 0.005,
                entry_ask if index == steps else price + 0.005,
                snapshot_run_id,
                timestamp.isoformat(),
            ),
        )
        snapshot_ids.append(int(snapshot.lastrowid))
    cursor = connection.execute(
        """
        INSERT INTO micro_cascade_signal_decisions (
            run_id, condition_id, event_id, token_id, arm, canonical_job,
            collection_eligible, scan_evaluated_at,
            trend_snapshot_ids_json, trend_snapshot_timestamps_json,
            trend_prices_json, trend_gap_minutes_json, entry_snapshot_id,
            snapshot_probability, snapshot_best_bid, snapshot_best_ask,
            snapshot_spread, snapshot_liquidity, snapshot_volume_24h,
            market_end_date, event_sibling_count, event_rank, event_selected,
            global_rank, cooldown_allowed, cooldown_reason, position_count,
            open_notional_usdc, drawdown_tripped, raw_selected,
            fresh_attempt_order, fresh_attempted, fresh_fail_reason,
            execution_selected, created_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 0.495,
            0.49, ?, 0.01, 50000, 20000, ?, 1, 1, 1, 1, 1, 'allowed',
            0, 0, 0, 1, 1, 0, 'max_positions', 0, ?
        )
        """,
        (
            run_id,
            f"condition-{decision_key}",
            event_id,
            f"token-{decision_key}",
            arm,
            analyzer.CANONICAL_ARMS[arm]["job_name"],
            scan_at.isoformat(),
            json.dumps(snapshot_ids),
            json.dumps([value.isoformat() for value in timestamps]),
            json.dumps(prices),
            json.dumps([5.0] * steps),
            snapshot_ids[-1],
            entry_ask,
            (END + timedelta(days=1)).isoformat(),
            scan_at.isoformat(),
        ),
    )
    decision_id = int(cursor.lastrowid)
    target = scan_at + timedelta(minutes=60)
    window_end = scan_at + timedelta(minutes=75)
    if first_absent:
        connection.execute(
            """
            INSERT INTO micro_cascade_followup_observations (
                decision_id, observing_run_id, condition_id, target_at,
                window_end, observed_at, market_seen, source_available,
                source_reason, valid_quote, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 'market_missing', 0, ?)
            """,
            (
                decision_id,
                f"run-{arm}-{int((target - START).total_seconds() // 300)}",
                f"condition-{decision_key}",
                target.isoformat(),
                window_end.isoformat(),
                target.isoformat(),
                target.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO micro_cascade_followup_observations (
                decision_id, observing_run_id, condition_id, target_at,
                window_end, observed_at, market_seen, source_available,
                source_reason, probability, best_bid, best_ask, valid_quote,
                created_at
            ) VALUES (
                ?, 'failed-observing', ?, ?, ?, ?, 1, 1,
                'quote_available', 0.99, 0.99, 0.995, 1, ?
            )
            """,
            (
                decision_id,
                f"condition-{decision_key}",
                target.isoformat(),
                window_end.isoformat(),
                (target + timedelta(minutes=1)).isoformat(),
                (target + timedelta(minutes=1)).isoformat(),
            ),
        )
    if followup:
        observed_at = target + (timedelta(minutes=5) if first_absent else timedelta())
        observing_index = int((observed_at - START).total_seconds() // 300)
        connection.execute(
            """
            INSERT INTO micro_cascade_followup_observations (
                decision_id, observing_run_id, condition_id, target_at,
                window_end, observed_at, market_seen, source_available,
                source_reason, probability, best_bid, best_ask, liquidity,
                volume_24h, source_updated_at, valid_quote, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, 1, 1, 'quote_available', 0.51, ?, 0.52,
                50000, 20000, ?, 1, ?
            )
            """,
            (
                decision_id,
                f"run-{arm}-{observing_index}",
                f"condition-{decision_key}",
                target.isoformat(),
                window_end.isoformat(),
                observed_at.isoformat(),
                entry_ask * (1 + exit_return),
                observed_at.isoformat(),
                observed_at.isoformat(),
            ),
        )
    return decision_id


def _strict_audit(path):
    resolved = str(path.resolve())
    return {
        "schema_version": 1,
        "period": {
            "start": START.isoformat(),
            "end": END.isoformat(),
            "days": 30,
        },
        "database_count": 1,
        "issue_counts": {"CRITICAL": 0, "HIGH": 0},
        "databases": [
            {
                "database": resolved,
                "database_sha256": analyzer._sha256_file(path),
                "status": "PASS",
            }
        ],
    }


def _build_arm(path, arm, *, add_primary_signals=False):
    init_database(str(path))
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE strategy_configs (
            config_hash TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            strategy_name TEXT NOT NULL,
            mode TEXT NOT NULL,
            config_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            git_commit TEXT NOT NULL
        );
        CREATE TABLE run_audits (
            run_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            strategy_name TEXT NOT NULL,
            job_name TEXT NOT NULL,
            mode TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            git_commit TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            cycle_stats_json TEXT,
            db_summary_json TEXT,
            error_type TEXT,
            error_message TEXT
        );
        """
    )
    offset = "ABCD".index(arm)
    connection.execute(
        """
        INSERT INTO micro_cascade_experiment_contracts (
            canonical_job, schema_version, analyzer_version,
            preregistration_sha256, arm, window_start, window_end,
            expected_cadence_minutes, expected_offset_minute, created_at
        ) VALUES (?, 1, 2, ?, ?, ?, ?, 5, ?, ?)
        """,
        (
            analyzer.CANONICAL_ARMS[arm]["job_name"],
            analyzer.PREREGISTRATION_SHA256,
            arm,
            START.isoformat(),
            END.isoformat(),
            offset,
            START.isoformat(),
        ),
    )
    first = START + timedelta(minutes=offset)
    run_count = int((END - first).total_seconds() // 300) + 1
    for index in range(run_count):
        started_at = first + timedelta(minutes=5 * index)
        if started_at >= END:
            break
        _insert_run(
            connection,
            arm=arm,
            run_id=f"run-{arm}-{index}",
            started_at=started_at,
        )
    if add_primary_signals:
        _insert_run(
            connection,
            arm=arm,
            run_id="failed-observing",
            started_at=first + timedelta(minutes=561),
            status="FAILED",
        )
        # 50 complete observations across 30 clusters; both temporal halves
        # have positive event-equal edge.
        indices = list(range(100, 125)) + list(range(5_000, 5_025))
        for number, run_index in enumerate(indices):
            scan_at = first + timedelta(minutes=5 * run_index)
            _insert_decision(
                connection,
                arm=arm,
                decision_key=f"positive-{number}",
                scan_at=scan_at,
                run_id=f"run-{arm}-{run_index}",
                event_id=f"event-{number % 30}",
                exit_return=0.02,
                first_absent=number == 0,
            )
        # A mature, explicit no-source row is censored, never imputed as zero.
        missing_index = 300
        _insert_decision(
            connection,
            arm=arm,
            decision_key="missing-50",
            scan_at=first + timedelta(minutes=5 * missing_index),
            run_id=f"run-{arm}-{missing_index}",
            event_id="event-missing",
            exit_return=0,
            followup=False,
            first_absent=True,
        )
        # A raw decision attached only to a FAILED run is ignored entirely.
        failed_at = first + timedelta(minutes=5 * 400)
        _insert_run(
            connection,
            arm=arm,
            run_id="failed-source",
            started_at=failed_at,
            status="FAILED",
        )
        _insert_decision(
            connection,
            arm=arm,
            decision_key="failed-51",
            scan_at=failed_at,
            run_id="failed-source",
            event_id="event-failed",
            exit_return=0,
            followup=False,
        )
    connection.commit()
    connection.close()


@pytest.fixture(scope="module")
def positive_experiment(tmp_path_factory):
    root = tmp_path_factory.mktemp("kiwi-v2-positive")
    databases = {}
    audits = {}
    for arm in analyzer.CANONICAL_ARMS:
        path = root / arm / "trades_sim.db"
        path.parent.mkdir(parents=True)
        _build_arm(path, arm, add_primary_signals=arm == "B")
        databases[arm] = path
        audits[arm] = _strict_audit(path)
    result = analyzer.analyze_experiment(
        databases,
        start=START,
        end=END,
        strict_audits=audits,
    )
    return databases, audits, result


def test_v2_primary_uses_raw_quote_path_and_can_pass(positive_experiment):
    _, _, result = positive_experiment
    primary = result["arms"]["B"]

    assert result["schema_version"] == 2
    assert result["primary_metric_status"] == (
        "RECONSTRUCTED_FROM_APPEND_ONLY_RAW_EVIDENCE"
    )
    assert primary["mature_raw_selected_signals"] == 51
    assert primary["quote_complete_signals"] == 50
    assert primary["unique_events"] == 30
    assert primary["target_quote_coverage"] == pytest.approx(50 / 51)
    assert primary["event_equal_return"] == pytest.approx(0.02)
    assert primary["event_equal_lower_ci_98_75"] == pytest.approx(0.02)
    assert primary["cost_stressed_lower_ci_98_75"] == pytest.approx(0.02 - 0.00104)
    assert primary["early_half"]["event_equal_return"] == pytest.approx(0.02)
    assert primary["late_half"]["event_equal_return"] == pytest.approx(0.02)
    assert primary["ignored_failed_source_decisions"] == 1
    assert primary["censor_reasons"] == {"followup_censored:market_missing": 1}
    assert all(
        arm_result["cadence"]["coverage"] == 1.0
        for arm_result in result["arms"].values()
    )
    assert result["primary_b_gate"]["passed"] is True
    assert result["primary_b_gate"]["verdict"] == (
        "ELIGIBLE_FOR_SHADOW_EXECUTION_REVIEW"
    )
    assert result["recorded_trade_subset_diagnostics"]["B"]["diagnostic_only"]


def test_missing_or_severe_strict_audit_fails_closed(positive_experiment):
    databases, audits, _ = positive_experiment
    missing = analyzer.analyze_experiment(
        databases, start=START, end=END, strict_audits={}
    )
    assert missing["primary_b_gate"]["passed"] is False
    assert missing["primary_b_gate"]["verdict"] == ("NOT_EVALUABLE_FAIL_CLOSED")
    assert missing["arms"]["B"]["strict_audit"]["errors"] == ["strict_audit_missing"]

    severe_audits = dict(audits)
    severe_audits["B"] = {
        **audits["B"],
        "issue_counts": {"CRITICAL": 0, "HIGH": 1},
    }
    severe = analyzer.analyze_experiment(
        databases, start=START, end=END, strict_audits=severe_audits
    )
    assert severe["primary_b_gate"]["verdict"] == ("NOT_EVALUABLE_FAIL_CLOSED")
    assert "strict_audit_has_high" in severe["arms"]["B"]["strict_audit"]["errors"]


def test_strict_audit_digest_is_required_and_bound_to_exact_db_bytes(
    positive_experiment,
):
    databases, audits, _ = positive_experiment
    invalid_audits = json.loads(json.dumps(audits))
    invalid_audits["A"]["databases"][0].pop("database_sha256")
    invalid_audits["B"]["databases"][0]["database_sha256"] = "0" * 64

    result = analyzer.analyze_experiment(
        databases,
        start=START,
        end=END,
        strict_audits=invalid_audits,
    )

    assert result["primary_b_gate"]["passed"] is False
    assert result["primary_b_gate"]["verdict"] == ("NOT_EVALUABLE_FAIL_CLOSED")
    assert (
        "strict_audit_database_sha256_missing_or_invalid"
        in result["arms"]["A"]["strict_audit"]["errors"]
    )
    assert (
        "strict_audit_database_sha256_mismatch"
        in result["arms"]["B"]["strict_audit"]["errors"]
    )


def test_negative_raw_edge_is_evaluable_but_fails(positive_experiment, tmp_path):
    databases, _, _ = positive_experiment
    copied = {}
    for arm, source in databases.items():
        destination = tmp_path / arm / "trades_sim.db"
        destination.parent.mkdir(parents=True)
        shutil.copy2(source, destination)
        copied[arm] = destination
    connection = sqlite3.connect(copied["B"])
    first = START + timedelta(minutes=1)
    # More independent negative clusters than the original positive set makes
    # the predeclared B edge and both halves negative without altering rows.
    for number in range(100):
        run_index = 700 + number if number < 50 else 5_500 + number
        _insert_decision(
            connection,
            arm="B",
            decision_key=f"negative-{number + 100}",
            scan_at=first + timedelta(minutes=5 * run_index),
            run_id=f"run-B-{run_index}",
            event_id=f"negative-event-{number % 40}",
            exit_return=-0.05,
        )
    connection.commit()
    connection.close()
    audits = {arm: _strict_audit(path) for arm, path in copied.items()}

    result = analyzer.analyze_experiment(
        copied, start=START, end=END, strict_audits=audits
    )

    assert result["experiment_contract"]["promotion_evidence_complete"] is True
    assert result["arms"]["B"]["event_equal_return"] < 0
    assert result["primary_b_gate"]["passed"] is False
    assert result["primary_b_gate"]["verdict"] == "FAIL_NO_SHADOW_REVIEW"


def _copy_experiment(databases, destination_root):
    copied = {}
    for arm, source in databases.items():
        destination = destination_root / arm / "trades_sim.db"
        destination.parent.mkdir(parents=True)
        shutil.copy2(source, destination)
        copied[arm] = destination
    return copied


@pytest.mark.parametrize("extra_run_kind", ["off_schedule", "duplicate"])
def test_noncanonical_success_source_run_is_excluded_and_fails_closed(
    positive_experiment,
    tmp_path,
    extra_run_kind,
):
    databases, _, baseline = positive_experiment
    copied = _copy_experiment(databases, tmp_path / extra_run_kind)
    connection = sqlite3.connect(copied["B"])
    scheduled_at = START + timedelta(minutes=1 + 5 * 7_000)
    started_at = (
        scheduled_at + timedelta(minutes=1)
        if extra_run_kind == "off_schedule"
        else scheduled_at
    )
    run_id = f"{extra_run_kind}-source"
    _insert_run(
        connection,
        arm="B",
        run_id=run_id,
        started_at=started_at,
    )
    _insert_decision(
        connection,
        arm="B",
        decision_key=f"{extra_run_kind}-positive",
        scan_at=started_at,
        run_id=run_id,
        event_id=f"{extra_run_kind}-event",
        exit_return=0.20,
    )
    connection.commit()
    connection.close()
    audits = {arm: _strict_audit(path) for arm, path in copied.items()}

    result = analyzer.analyze_experiment(
        copied,
        start=START,
        end=END,
        strict_audits=audits,
    )
    primary = result["arms"]["B"]

    assert primary["cadence"]["valid"] is False
    assert extra_run_kind + "_success_runs" in primary["cadence"]["invalid_reasons"]
    assert primary["ignored_noncanonical_cadence_source_decisions"] == 1
    assert (
        primary["quote_complete_signals"]
        == baseline["arms"]["B"]["quote_complete_signals"]
    )
    assert result["primary_b_gate"]["verdict"] == ("NOT_EVALUABLE_FAIL_CLOSED")


def test_off_schedule_observer_cannot_supply_primary_followup(
    positive_experiment,
    tmp_path,
):
    databases, _, baseline = positive_experiment
    copied = _copy_experiment(databases, tmp_path / "observer")
    connection = sqlite3.connect(copied["B"])
    source_index = 7_100
    scan_at = START + timedelta(minutes=1 + 5 * source_index)
    decision_id = _insert_decision(
        connection,
        arm="B",
        decision_key="off-schedule-observer",
        scan_at=scan_at,
        run_id=f"run-B-{source_index}",
        event_id="off-schedule-observer-event",
        exit_return=0.20,
        followup=False,
    )
    target_at = scan_at + timedelta(minutes=60)
    observed_at = target_at + timedelta(minutes=1)
    observer_run_id = "off-schedule-observer"
    _insert_run(
        connection,
        arm="B",
        run_id=observer_run_id,
        started_at=observed_at,
    )
    connection.execute(
        """
        INSERT INTO micro_cascade_followup_observations (
            decision_id, observing_run_id, condition_id, target_at,
            window_end, observed_at, market_seen, source_available,
            source_reason, probability, best_bid, best_ask, liquidity,
            volume_24h, source_updated_at, valid_quote, created_at
        ) VALUES (
            ?, ?, 'condition-off-schedule-observer', ?, ?, ?, 1, 1,
            'valid_raw_gamma_followup', 0.60, 0.60, 0.61, 50000,
            20000, ?, 1, ?
        )
        """,
        (
            decision_id,
            observer_run_id,
            target_at.isoformat(),
            (scan_at + timedelta(minutes=75)).isoformat(),
            observed_at.isoformat(),
            observed_at.isoformat(),
            observed_at.isoformat(),
        ),
    )
    connection.commit()
    connection.close()
    audits = {arm: _strict_audit(path) for arm, path in copied.items()}

    result = analyzer.analyze_experiment(
        copied,
        start=START,
        end=END,
        strict_audits=audits,
    )
    primary = result["arms"]["B"]

    assert primary["cadence"]["valid"] is False
    assert (
        primary["quote_complete_signals"]
        == baseline["arms"]["B"]["quote_complete_signals"]
    )
    assert primary["mature_raw_selected_signals"] == (
        baseline["arms"]["B"]["mature_raw_selected_signals"] + 1
    )
    assert (
        primary["censor_reasons"][
            "followup_censored:observing_run_not_cadence_eligible"
        ]
        == 1
    )
    assert result["primary_b_gate"]["verdict"] == ("NOT_EVALUABLE_FAIL_CLOSED")


def test_cadence_coverage_is_computed_from_success_run_slots():
    short_end = START + timedelta(hours=1)
    half_hourly_slots = [
        {"started_at": (START + timedelta(minutes=10 * index)).isoformat()}
        for index in range(6)
    ]

    result = analyzer._cadence_metrics(
        half_hourly_slots,
        start=START,
        end=short_end,
        cadence_minutes=5,
        offset_minute=0,
    )

    assert result["expected_slots"] == 12
    assert result["covered_slots"] == 6
    assert result["coverage"] == 0.5
    assert result["valid"] is False


def test_cadence_does_not_accept_the_wrong_preregistered_offset():
    short_end = START + timedelta(hours=1)
    wrong_offset_runs = [
        {"started_at": (START + timedelta(minutes=1 + 5 * index)).isoformat()}
        for index in range(12)
    ]

    result = analyzer._cadence_metrics(
        wrong_offset_runs,
        start=START,
        end=short_end,
        cadence_minutes=5,
        offset_minute=0,
    )

    assert result["covered_slots"] == 0
    assert result["coverage"] == 0.0
    assert result["off_schedule_success_runs"] == 12
    assert result["valid"] is False


def test_analyzer_rejects_arm_contract_with_another_arms_offset(
    positive_experiment,
    tmp_path,
):
    databases, _, _ = positive_experiment
    paths = _copy_experiment(databases, tmp_path / "wrong-contract-offset")
    connection = sqlite3.connect(paths["B"])
    connection.execute(
        "DROP TRIGGER micro_cascade_experiment_contracts_append_only_update"
    )
    connection.execute(
        """
        UPDATE micro_cascade_experiment_contracts
        SET expected_offset_minute = 2
        """
    )
    connection.commit()
    connection.close()
    audits = {arm: _strict_audit(path) for arm, path in paths.items()}

    result = analyzer.analyze_experiment(
        paths,
        start=START,
        end=END,
        strict_audits=audits,
    )

    assert result["arms"]["B"]["experiment_contract"]["valid"] is False
    assert "contract_wrong_expected_offset" in result["arms"]["B"]["mapping_errors"]
    assert result["primary_b_gate"]["verdict"] == "NOT_EVALUABLE_FAIL_CLOSED"


@pytest.mark.parametrize(("passed", "expected_exit"), [(True, 0), (False, 1)])
def test_cli_exit_status_matches_promotion_gate(
    monkeypatch,
    capsys,
    passed,
    expected_exit,
):
    monkeypatch.setattr(
        analyzer,
        "_parse_db_specs",
        lambda _values, *, project_root: {},
    )
    monkeypatch.setattr(
        analyzer,
        "analyze_experiment",
        lambda *_args, **_kwargs: {
            "primary_b_gate": {
                "passed": passed,
                "verdict": "PASS" if passed else "NOT_EVALUABLE_FAIL_CLOSED",
            }
        },
    )

    exit_code = analyzer.main(
        [
            "--start",
            START.isoformat(),
            "--end",
            END.isoformat(),
        ]
    )

    assert exit_code == expected_exit
    capsys.readouterr()
