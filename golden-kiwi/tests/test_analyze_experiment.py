from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "analyze_experiment.py"
)
SPEC = importlib.util.spec_from_file_location("kiwi_analyze_experiment", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
analyzer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analyzer
SPEC.loader.exec_module(analyzer)

UTC = timezone.utc
START = datetime(2026, 8, 1, tzinfo=UTC)
END = START + timedelta(days=30)
GIT_COMMIT = "a" * 40
SOURCE_DIGEST = "d" * 64


def _canonical_json(value):
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _schema(connection):
    connection.executescript(
        """
        CREATE TABLE strategy_configs (
            config_hash TEXT PRIMARY KEY,
            strategy_name TEXT,
            mode TEXT,
            config_json TEXT,
            git_commit TEXT
        );
        CREATE TABLE run_audits (
            run_id TEXT PRIMARY KEY,
            strategy_name TEXT,
            job_name TEXT,
            mode TEXT,
            config_hash TEXT,
            git_commit TEXT,
            started_at TEXT,
            status TEXT
        );
        CREATE TABLE market_sweeps (
            sweep_id TEXT PRIMARY KEY,
            run_id TEXT,
            cursor_complete INTEGER
        );
        CREATE TABLE market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            condition_id TEXT,
            probability REAL,
            run_id TEXT,
            timestamp TEXT,
            catalog_outcomes_json TEXT,
            catalog_outcome_prices_json TEXT,
            catalog_token_ids_json TEXT,
            catalog_neg_risk INTEGER
        );
        CREATE TABLE experiment_state (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            condition_id TEXT,
            event_id TEXT,
            status TEXT,
            mode TEXT,
            entry_run_id TEXT,
            exit_run_id TEXT,
            buy_timestamp TEXT,
            sell_timestamp TEXT,
            best_ask_at_buy REAL,
            best_bid_at_buy REAL,
            best_bid_at_exit REAL,
            entry_snapshot_id INTEGER,
            prior_snapshot_id_at_entry INTEGER,
            trend_start_snapshot_id_at_entry INTEGER,
            signal_timestamp_at_entry TEXT,
            trend_snapshot_ids_json TEXT,
            trend_snapshot_timestamps_json TEXT,
            trend_persisted_prices_json TEXT,
            trend_decision_prices_json TEXT,
            trend_decision_timestamps_json TEXT,
            trend_decision_gap_minutes_json TEXT,
            decision_observed_at_at_entry TEXT,
            decision_price_source_at_entry TEXT,
            trend_gap_minutes_json TEXT,
            confirmation_steps_at_entry INTEGER,
            promotion_eligible INTEGER,
            exit_delay_minutes REAL
        );
        """
    )


def _config_payload(arm, *, move=None, source_digest=SOURCE_DIGEST):
    expected = analyzer.CANONICAL_ARMS[arm]
    trading = {
        **analyzer.FROZEN_TRADING_VALUES,
        "strategy_source_digest": source_digest,
        "entry": {
            **analyzer.FROZEN_ENTRY_VALUES,
            "confirmation_steps": expected["confirmation_steps"],
            "min_cumulative_move": (
                expected["min_cumulative_move"] if move is None else move
            ),
        },
        "archive": dict(analyzer.FROZEN_ARCHIVE_VALUES),
        "excluded_categories": list(analyzer.FROZEN_EXCLUDED_CATEGORIES),
    }
    return {
        "schema_version": 1,
        "strategy_name": "golden-kiwi",
        "mode": "sim",
        "trading": trading,
    }


def _add_run(
    connection,
    arm,
    run_id,
    *,
    status="SUCCESS",
    cursor_complete=1,
    git_commit=GIT_COMMIT,
    source_digest=SOURCE_DIGEST,
    job_name=None,
    config_move=None,
):
    payload = _config_payload(
        arm,
        move=config_move,
        source_digest=source_digest,
    )
    config_json = _canonical_json(payload)
    config_hash = hashlib.sha256(config_json.encode()).hexdigest()
    connection.execute(
        """
        INSERT OR IGNORE INTO strategy_configs
            (config_hash, strategy_name, mode, config_json, git_commit)
        VALUES (?, 'golden-kiwi', 'sim', ?, ?)
        """,
        (config_hash, config_json, git_commit),
    )
    connection.execute(
        """
        INSERT INTO run_audits
            (run_id, strategy_name, job_name, mode, config_hash, git_commit,
             started_at, status)
        VALUES (?, 'golden-kiwi', ?, 'sim', ?, ?, ?, ?)
        """,
        (
            run_id,
            job_name or analyzer.CANONICAL_ARMS[arm]["job_name"],
            config_hash,
            git_commit,
            (START + timedelta(hours=1)).isoformat(),
            status,
        ),
    )
    connection.execute(
        "INSERT INTO market_sweeps VALUES (?, ?, ?)",
        (f"sweep-{run_id}", run_id, cursor_complete),
    )
    return run_id


def _add_trade(
    connection,
    arm,
    index,
    *,
    entry_run_id,
    exit_run_id=None,
    event_id=None,
    neg_risk=0,
    promotion_eligible=1,
    exit_delay=0.0,
    lineage_json=True,
    exit_bid=None,
):
    steps = analyzer.CANONICAL_ARMS[arm]["confirmation_steps"]
    buy_time = START + timedelta(hours=12 * index, minutes=20)
    times = [
        buy_time - timedelta(minutes=5 * (steps - position))
        for position in range(steps + 1)
    ]
    start_price = 0.48
    per_step = max(
        analyzer.CANONICAL_ARMS[arm]["min_cumulative_move"] / steps,
        0.005,
    )
    prices = [
        start_price + per_step * position for position in range(steps + 1)
    ]
    best_bid_at_buy = prices[-1] - 0.005
    best_ask_at_buy = prices[-1] + 0.005
    if exit_bid is None:
        exit_bid = best_ask_at_buy * 1.02
    snapshot_ids = []
    for position, (timestamp, price) in enumerate(zip(times, prices)):
        cursor = connection.execute(
            """
            INSERT INTO market_snapshots (
                condition_id, probability, run_id, timestamp,
                catalog_outcomes_json, catalog_outcome_prices_json,
                catalog_token_ids_json, catalog_neg_risk
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"condition-{index}",
                price,
                entry_run_id,
                timestamp.isoformat(),
                '["Yes", "No"]',
                json.dumps([price, 1 - price]),
                '["yes-token", "no-token"]',
                neg_risk if position == steps else 0,
            ),
        )
        snapshot_ids.append(cursor.lastrowid)
    connection.execute(
        """
        INSERT INTO trades (
            condition_id, event_id, status, mode, entry_run_id, exit_run_id,
            buy_timestamp, sell_timestamp, best_ask_at_buy, best_bid_at_exit,
            best_bid_at_buy,
            entry_snapshot_id, prior_snapshot_id_at_entry,
            trend_start_snapshot_id_at_entry, signal_timestamp_at_entry,
            trend_snapshot_ids_json, trend_snapshot_timestamps_json,
            trend_persisted_prices_json, trend_decision_prices_json,
            trend_decision_timestamps_json, trend_decision_gap_minutes_json,
            decision_observed_at_at_entry, decision_price_source_at_entry,
            trend_gap_minutes_json, confirmation_steps_at_entry,
            promotion_eligible, exit_delay_minutes
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            f"condition-{index}",
            event_id or f"event-{index}",
            "COMPLETED",
            "sim",
            entry_run_id,
            exit_run_id or entry_run_id,
            buy_time.isoformat(),
            (buy_time + timedelta(minutes=60 + exit_delay)).isoformat(),
            best_ask_at_buy,
            exit_bid,
            best_bid_at_buy,
            snapshot_ids[-1],
            snapshot_ids[-2],
            snapshot_ids[0],
            times[-1].isoformat(),
            json.dumps(snapshot_ids) if lineage_json else None,
            json.dumps([value.isoformat() for value in times]),
            json.dumps(prices),
            json.dumps(prices),
            json.dumps([value.isoformat() for value in times]),
            json.dumps([5.0] * steps),
            buy_time.isoformat(),
            "clob_single_order_book_midpoint",
            json.dumps([5.0] * steps),
            steps,
            promotion_eligible,
            exit_delay,
        ),
    )


def _make_db(tmp_path, arm, *, signals=0):
    job = analyzer.CANONICAL_ARMS[arm]["job_name"]
    path = tmp_path / job / "trades_sim.db"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    _schema(connection)
    run_id = _add_run(connection, arm, f"run-{arm}")
    for index in range(signals):
        # 50 signals across 30 event clusters, with repeats 15 days apart.
        _add_trade(
            connection,
            arm,
            index,
            entry_run_id=run_id,
            event_id=f"event-{index % 30}",
        )
    connection.commit()
    connection.close()
    return path


def _set_kill_switch(
    connection,
    *,
    tripped_at,
    economic_pnl=-20.0,
    loss_limit=20.0,
):
    payload = {
        "schema_version": 1,
        "tripped": True,
        "tripped_at": tripped_at.isoformat(),
        "tripped_run_id": "run-B",
        "economic_pnl": economic_pnl,
        "loss_limit_usdc": loss_limit,
        "experiment_capital_usdc": 100.0,
        "max_drawdown_stop": 0.20,
    }
    connection.execute(
        """
        INSERT INTO experiment_state(key, value_json, updated_at)
        VALUES ('drawdown_kill_switch', ?, ?)
        """,
        (json.dumps(payload), tripped_at.isoformat()),
    )


def test_positive_descriptive_result_never_false_passes_without_raw_evidence(
    tmp_path,
):
    databases = {
        "A": _make_db(tmp_path, "A"),
        "B": _make_db(tmp_path, "B", signals=50),
        "C": _make_db(tmp_path, "C"),
        "D": _make_db(tmp_path, "D"),
    }

    result = analyzer.analyze_experiment(databases, start=START, end=END)

    primary = result["arms"]["B"]
    assert primary["quote_complete_signals"] == 50
    assert primary["unique_events"] == 30
    assert primary["target_quote_coverage"] == 1.0
    assert primary["event_equal_return"] == pytest.approx(0.02)
    assert primary["event_equal_lower_ci_98_75"] == pytest.approx(0.02)
    assert primary["cohort_count"] == 1
    assert result["primary_b_gate"]["passed"] is False
    assert (
        result["primary_b_gate"]["verdict"]
        == "NOT_EVALUABLE_FAIL_CLOSED"
    )
    assert (
        result["primary_b_gate"]["checks"]["promotion_evidence_complete"][
            "passed"
        ]
        is False
    )
    assert (
        result["primary_b_gate"]["checks"]["raw_target_quote_coverage"][
            "passed"
        ]
        is False
    )
    assert result["evaluation_scope"]["promotion_decision_supported"] is False
    assert result["primary_metric_status"] == "NOT_RECONSTRUCTED_FAIL_CLOSED"
    assert (
        result["recorded_trade_subset_diagnostics"][
            "must_not_be_used_as_raw_denominator"
        ]
        is True
    )


def test_registered_two_sided_bootstrap_lower_quantile_is_frozen():
    assert analyzer.CI_LOWER_QUANTILE == pytest.approx(0.00625)
    assert analyzer._percentile([0.0, 1.0], analyzer.CI_LOWER_QUANTILE) == (
        pytest.approx(0.00625)
    )
    signals = [
        analyzer.ValidSignal(
            trade_id=index,
            event_id=f"event-{index}",
            cohort=analyzer.Cohort("c", SOURCE_DIGEST, "sim", "job"),
            signal_timestamp=START,
            executable_return=0.01 + index / 10_000,
        )
        for index in range(30)
    ]
    assert "two_sided_98.75pct_lower_q_0.00625" in (
        analyzer._event_metrics(signals)["ci_method"]
    )


def test_missing_and_invalid_evidence_is_censored_with_specific_reasons(tmp_path):
    path = _make_db(tmp_path, "B")
    connection = sqlite3.connect(path)
    entry_run = "run-B"

    failed_exit = _add_run(
        connection, "B", "failed-exit", status="FAILED"
    )
    _add_trade(
        connection,
        "B",
        1,
        entry_run_id=entry_run,
        exit_run_id=failed_exit,
    )
    _add_trade(
        connection,
        "B",
        2,
        entry_run_id=entry_run,
        neg_risk=None,
    )
    _add_trade(
        connection,
        "B",
        3,
        entry_run_id=entry_run,
        promotion_eligible=0,
        exit_delay=16.0,
    )
    _add_trade(
        connection,
        "B",
        4,
        entry_run_id=entry_run,
        lineage_json=False,
    )
    other_source_exit = _add_run(
        connection,
        "B",
        "other-source-exit",
        source_digest="e" * 64,
    )
    _add_trade(
        connection,
        "B",
        5,
        entry_run_id=entry_run,
        exit_run_id=other_source_exit,
    )
    connection.commit()
    connection.close()

    result = analyzer.analyze_arm("B", path, start=START, end=END)

    assert result["quote_complete_signals"] == 0
    assert result["mature_target_signals"] == 5
    assert result["target_quote_coverage"] == 0
    assert result["censor_reasons"] == {
        "entry_exit_cross_cohort": 1,
        "exit_delay_out_of_range": 1,
        "exit_run_not_success": 1,
        "lineage_json_missing_or_invalid": 1,
        "snapshot_neg_risk_or_unknown": 1,
    }
    # A different strategy source digest is a separate collection cohort and
    # is never silently pooled into an event-equal metric.
    assert result["cohort_count"] == 2
    assert result["event_equal_return"] is None
    assert result["ci_method"] == "unavailable_multiple_cohorts_not_pooled"


def test_git_commit_change_does_not_split_strategy_cohort(tmp_path):
    path = _make_db(tmp_path, "B")
    connection = sqlite3.connect(path)
    _add_run(
        connection,
        "B",
        "new-monorepo-commit",
        git_commit="b" * 40,
    )
    connection.commit()
    connection.close()

    result = analyzer.analyze_arm("B", path, start=START, end=END)

    assert result["cohort_count"] == 1
    assert result["cohorts"][0]["strategy_source_digest"] == SOURCE_DIGEST


def test_decision_clock_and_source_are_verified(tmp_path):
    path = _make_db(tmp_path, "B")
    connection = sqlite3.connect(path)
    for index in (1, 2, 3):
        _add_trade(connection, "B", index, entry_run_id="run-B")
    connection.execute(
        """
        UPDATE trades SET trend_decision_gap_minutes_json = '[5,5,4]'
        WHERE id = 1
        """
    )
    connection.execute(
        """
        UPDATE trades SET decision_observed_at_at_entry = ?
        WHERE id = 2
        """,
        ((START + timedelta(hours=25)).isoformat(),),
    )
    connection.execute(
        """
        UPDATE trades SET decision_price_source_at_entry = 'unknown'
        WHERE id = 3
        """
    )
    connection.commit()
    connection.close()

    result = analyzer.analyze_arm("B", path, start=START, end=END)

    assert result["censor_reasons"] == {
        "decision_observation_timestamp_mismatch": 1,
        "decision_price_source_invalid": 1,
        "lineage_decision_gap_mismatch": 1,
    }


def test_wrong_job_or_arm_mapping_fails_closed(tmp_path):
    path = tmp_path / "wrong.db"
    connection = sqlite3.connect(path)
    _schema(connection)
    _add_run(
        connection,
        "B",
        "wrong-job",
        job_name="kiwi-sim-a-3x1",
    )
    connection.commit()
    connection.close()

    result = analyzer.analyze_arm("B", path, start=START, end=END)

    assert result["contract_valid"] is False
    assert result["mapping_errors"]
    assert any(
        "noncanonical jobs" in error for error in result["mapping_errors"]
    )


def test_decision_vector_rejects_bad_step_floor_and_cap(tmp_path):
    path = _make_db(tmp_path, "B")
    connection = sqlite3.connect(path)
    vectors = [
        [0.48, 0.49, 0.49, 0.51],   # nonpositive middle step
        [0.48, 0.505, 0.51, 0.52],  # one step above 2pp
        [0.48, 0.485, 0.49, 0.495], # cumulative below B's 2pp floor
        [0.48, 0.495, 0.51, 0.525], # cumulative above 4pp cap
    ]
    for index, vector in enumerate(vectors, start=1):
        _add_trade(
            connection,
            "B",
            index,
            entry_run_id="run-B",
        )
        trade_id = connection.execute(
            "SELECT MAX(id) FROM trades"
        ).fetchone()[0]
        connection.execute(
            """
            UPDATE trades SET trend_decision_prices_json = ?
            WHERE id = ?
            """,
            (json.dumps(vector), trade_id),
        )
    connection.commit()
    connection.close()

    result = analyzer.analyze_arm("B", path, start=START, end=END)

    assert result["quote_complete_signals"] == 0
    assert result["censor_reasons"] == {
        "lineage_decision_cumulative_not_qualifying": 2,
        "lineage_decision_step_not_qualifying": 2,
    }


def test_persisted_drawdown_trip_censors_every_later_buy(tmp_path):
    path = _make_db(tmp_path, "B")
    connection = sqlite3.connect(path)
    tripped_at = START + timedelta(hours=6)
    _set_kill_switch(connection, tripped_at=tripped_at)
    _add_trade(connection, "B", 1, entry_run_id="run-B")
    connection.commit()
    connection.close()

    result = analyzer.analyze_arm("B", path, start=START, end=END)

    state = result["drawdown_kill_switch"]
    assert state["status"] == "TRIPPED_VALID"
    assert state["contract_valid"] is True
    assert state["post_trip_buy_count"] == 1
    assert result["censor_reasons"]["buy_after_drawdown_kill_switch"] == 1
    assert result["contract_valid"] is False
    assert any(
        "BUY trades exist at/after" in error
        for error in result["mapping_errors"]
    )


def test_invalid_drawdown_trip_threshold_is_contract_error(tmp_path):
    path = _make_db(tmp_path, "B")
    connection = sqlite3.connect(path)
    _set_kill_switch(
        connection,
        tripped_at=START + timedelta(hours=6),
        economic_pnl=-19.0,
    )
    connection.commit()
    connection.close()

    result = analyzer.analyze_arm("B", path, start=START, end=END)

    state = result["drawdown_kill_switch"]
    assert state["status"] == "INVALID"
    assert "economic_pnl_above_trip_threshold" in state["errors"]
    assert result["contract_valid"] is False


def test_exact_30_day_window_and_all_four_arms_are_mandatory(tmp_path):
    databases = {
        "A": _make_db(tmp_path, "A"),
        "B": _make_db(tmp_path, "B"),
        "C": _make_db(tmp_path, "C"),
    }
    with pytest.raises(analyzer.AnalysisContractError, match="A, B, C, D"):
        analyzer.analyze_experiment(databases, start=START, end=END)

    databases["D"] = _make_db(tmp_path, "D")
    with pytest.raises(analyzer.AnalysisContractError, match="exactly 30 days"):
        analyzer.analyze_experiment(
            databases,
            start=START,
            end=END - timedelta(seconds=1),
        )
