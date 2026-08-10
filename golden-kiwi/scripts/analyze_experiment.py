#!/usr/bin/env python3
"""Fail-closed 30-day evaluator for the Golden Kiwi A/B/C/D experiment.

The evaluator deliberately reads only persisted SQLite evidence.  Missing run,
sweep, catalog, lineage, or executable-quote evidence is censored with a
reason; it is never replaced with a price estimate or zero return.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import statistics
import sys
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
ANALYZER_VERSION = 3
EXPERIMENT_CONTRACT_SCHEMA_VERSION = 2
PREREGISTRATION_SHA256 = (
    "65e33146e018ff9b01495af515fd059ba5be33de15758ad438584427ea02223c"
)
WINDOW_DAYS = 30
EXPECTED_CADENCE_MINUTES = 5
MAX_EXIT_DELAY_MINUTES = 15.0
HOLD_MINUTES = 60.0
COST_STRESS = 0.00104  # 10.4 bps, reporting only; pre-fee edge is primary.
CI_CONFIDENCE = 0.9875
CI_LOWER_QUANTILE = (1.0 - CI_CONFIDENCE) / 2.0
BOOTSTRAP_DRAWS = 20_000
BOOTSTRAP_SEED = 20_260_730

# These promotion-critical facts are not reconstructible from the current
# strategy DB alone.  Keeping them explicit prevents a descriptively positive
# sample from being mistaken for an authorization to advance the strategy.
UNSUPPORTED_PROMOTION_EVIDENCE = (
    "raw_signal_and_counterfactual_denominator_not_persisted",
    "full_point_in_time_universe_and_fresh_book_funnel_not_replayable",
    "first_observed_quote_after_60m_not_proven",
    "event_winner_selection_not_replayable",
    "six_hour_event_cooldown_candidate_history_not_replayable",
    "strict_retro_audit_result_not_embedded",
    "sixty_day_cadence_audit_not_embedded",
)

FROZEN_TRADING_VALUES: dict[str, Any] = {
    "lifecycle_mode": "active",
    "max_drawdown_stop": 0.20,
    "experiment_capital_usdc": 100.0,
    "buy_amount_usdc": 5.0,
    "max_buy_amount_usdc": 5.0,
    "min_liquidity": 20_000.0,
    "min_volume_24h": 10_000.0,
    "max_positions": 3,
    "max_event_positions": 1,
    "max_open_notional_usdc": 15.0,
    "max_new_positions_per_cycle": 1,
    "reentry_cooldown_hours": 6.0,
    "min_order_size": 5.0,
    "min_order_buffer_shares": 0.10,
    "max_spread": 0.02,
    "depth_price_window": 0.01,
    "depth_safety_multiple": 1.20,
    "yes_only_mode": True,
}
FROZEN_ENTRY_VALUES: dict[str, float] = {
    "min_step_move": 0.0,
    "max_step_move": 0.02,
    "max_cumulative_move": 0.04,
    "min_snapshot_gap_minutes": 3.0,
    "max_snapshot_gap_minutes": 10.0,
    "prob_min": 0.20,
    "prob_max": 0.80,
    "min_hours_to_resolution": 6.0,
    "hold_minutes": HOLD_MINUTES,
    "max_exit_delay_minutes": MAX_EXIT_DELAY_MINUTES,
}
FROZEN_ARCHIVE_VALUES: dict[str, float | int] = {
    "prob_min": 0.16,
    "prob_max": 0.84,
    "retention_days": 60,
    "fetch_min_liquidity": 20_000.0,
    "fetch_min_total_volume": 10_000.0,
    "max_fetch_pages": 53,
    "max_fetch_markets": 5_330,
    "max_sweep_seconds": 120.0,
}
FROZEN_EXCLUDED_CATEGORIES = [
    "sports",
    "games",
    "esports",
    "crypto-prices",
    "up-or-down",
    "multi-strikes",
    "5m",
    "15m",
    "1h",
]

CANONICAL_ARMS: dict[str, dict[str, Any]] = {
    "A": {
        "job_name": "kiwi-sim-a-3x1",
        "confirmation_steps": 3,
        "min_cumulative_move": 0.01,
        "expected_offset_minute": 0,
    },
    "B": {
        "job_name": "kiwi-sim-b-3x2",
        "confirmation_steps": 3,
        "min_cumulative_move": 0.02,
        "expected_offset_minute": 1,
    },
    "C": {
        "job_name": "kiwi-sim-c-5x1",
        "confirmation_steps": 5,
        "min_cumulative_move": 0.01,
        "expected_offset_minute": 2,
    },
    "D": {
        "job_name": "kiwi-sim-d-5x2",
        "confirmation_steps": 5,
        "min_cumulative_move": 0.02,
        "expected_offset_minute": 3,
    },
}

REQUIRED_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "trades": frozenset(
        {
            "id",
            "condition_id",
            "event_id",
            "status",
            "mode",
            "entry_run_id",
            "exit_run_id",
            "buy_timestamp",
            "sell_timestamp",
            "best_ask_at_buy",
            "best_bid_at_buy",
            "best_bid_at_exit",
            "entry_snapshot_id",
            "prior_snapshot_id_at_entry",
            "trend_start_snapshot_id_at_entry",
            "signal_timestamp_at_entry",
            "trend_snapshot_ids_json",
            "trend_snapshot_timestamps_json",
            "trend_persisted_prices_json",
            "trend_decision_prices_json",
            "trend_decision_timestamps_json",
            "trend_decision_gap_minutes_json",
            "decision_observed_at_at_entry",
            "decision_price_source_at_entry",
            "trend_gap_minutes_json",
            "confirmation_steps_at_entry",
            "promotion_eligible",
            "exit_delay_minutes",
        }
    ),
    "market_snapshots": frozenset(
        {
            "id",
            "condition_id",
            "probability",
            "run_id",
            "timestamp",
            "catalog_outcomes_json",
            "catalog_outcome_prices_json",
            "catalog_token_ids_json",
            "catalog_neg_risk",
        }
    ),
    "market_sweeps": frozenset({"run_id", "cursor_complete"}),
    "run_audits": frozenset(
        {
            "run_id",
            "strategy_name",
            "job_name",
            "mode",
            "config_hash",
            "git_commit",
            "started_at",
            "status",
        }
    ),
    "strategy_configs": frozenset(
        {
            "config_hash",
            "strategy_name",
            "mode",
            "config_json",
            "git_commit",
        }
    ),
    "experiment_state": frozenset({"key", "value_json", "updated_at"}),
}

V2_REQUIRED_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "market_sweeps": frozenset(
        {
            "sweep_id",
            "schema_version",
            "run_id",
            "started_at",
            "completed_at",
            "cursor_complete",
            "pages",
            "raw_market_count",
            "min_liquidity",
            "min_volume",
            "max_pages",
            "max_markets",
            "max_elapsed_seconds",
            "elapsed_seconds",
        }
    ),
    "market_snapshots": frozenset(
        {
            "id",
            "condition_id",
            "probability",
            "best_bid",
            "best_ask",
            "run_id",
            "timestamp",
        }
    ),
    "micro_cascade_experiment_contracts": frozenset(
        {
            "canonical_job",
            "schema_version",
            "analyzer_version",
            "preregistration_sha256",
            "arm",
            "window_start",
            "window_end",
            "expected_cadence_minutes",
            "expected_offset_minute",
        }
    ),
    "micro_cascade_signal_decisions": frozenset(
        {
            "id",
            "run_id",
            "condition_id",
            "event_id",
            "arm",
            "canonical_job",
            "collection_eligible",
            "scan_evaluated_at",
            "trend_snapshot_ids_json",
            "trend_snapshot_timestamps_json",
            "trend_prices_json",
            "trend_gap_minutes_json",
            "entry_snapshot_id",
            "snapshot_probability",
            "snapshot_best_bid",
            "snapshot_best_ask",
            "event_sibling_count",
            "event_rank",
            "event_selected",
            "global_rank",
            "cooldown_allowed",
            "drawdown_tripped",
            "raw_selected",
            "fresh_attempt_order",
            "fresh_attempted",
            "fresh_gate_passed",
            "execution_selected",
            "trade_id",
        }
    ),
    "micro_cascade_followup_observations": frozenset(
        {
            "id",
            "decision_id",
            "observing_run_id",
            "condition_id",
            "target_at",
            "window_end",
            "observed_at",
            "market_seen",
            "source_available",
            "source_reason",
            "best_bid",
            "valid_quote",
        }
    ),
}


class AnalysisContractError(RuntimeError):
    """Raised when a database cannot satisfy the experiment-level contract."""


@dataclass(frozen=True)
class Cohort:
    config_hash: str
    strategy_source_digest: str
    mode: str
    job_name: str

    def key(self) -> tuple[str, str, str, str]:
        return (
            self.config_hash,
            self.strategy_source_digest,
            self.mode,
            self.job_name,
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "config_hash": self.config_hash,
            "strategy_source_digest": self.strategy_source_digest,
            "mode": self.mode,
            "job_name": self.job_name,
        }


@dataclass(frozen=True)
class ValidSignal:
    trade_id: int
    event_id: str
    cohort: Cohort
    signal_timestamp: datetime
    executable_return: float


@dataclass(frozen=True)
class CadenceAssessment:
    """Promotion-eligible run IDs plus public cadence diagnostics."""

    metrics: dict[str, Any]
    eligible_run_ids: frozenset[str]


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_list(value: Any) -> list[Any] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return result if isinstance(result, list) else None


def _same_number(left: Any, right: Any, tolerance: float = 1e-9) -> bool:
    lhs, rhs = _finite(left), _finite(right)
    return (
        lhs is not None
        and rhs is not None
        and math.isclose(lhs, rhs, rel_tol=0.0, abs_tol=tolerance)
    )


def _open_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise AnalysisContractError(f"database does not exist: {resolved}")
    connection = sqlite3.connect(
        f"file:{resolved.as_posix()}?mode=ro",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("BEGIN")
    return connection


def _sha256_file(path: Path) -> str:
    resolved = path.expanduser().resolve()
    digest = hashlib.sha256()
    try:
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise AnalysisContractError(
            f"cannot hash analysis database {resolved}: {error}"
        ) from error
    return digest.hexdigest()


def _table_columns(connection: sqlite3.Connection, table: str) -> frozenset[str]:
    return frozenset(
        str(row["name"]) for row in connection.execute(f'PRAGMA table_info("{table}")')
    )


def _validate_schema(connection: sqlite3.Connection) -> None:
    tables = {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing_tables = sorted(set(REQUIRED_TABLE_COLUMNS) - tables)
    if missing_tables:
        raise AnalysisContractError(
            f"required tables missing: {', '.join(missing_tables)}"
        )
    missing_columns: dict[str, list[str]] = {}
    for table, required in REQUIRED_TABLE_COLUMNS.items():
        missing = sorted(required - _table_columns(connection, table))
        if missing:
            missing_columns[table] = missing
    if missing_columns:
        raise AnalysisContractError(
            "required columns missing: " + json.dumps(missing_columns, sort_keys=True)
        )


def _canonical_config_reason(
    config_row: sqlite3.Row | None,
    expected: Mapping[str, Any],
) -> str | None:
    if config_row is None:
        return "missing_strategy_config"
    if config_row["strategy_name"] != "golden-kiwi":
        return "config_wrong_strategy"
    if config_row["mode"] != "sim":
        return "config_not_simulation"
    raw = str(config_row["config_json"] or "")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "invalid_config_json"
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if expected_hash != config_row["config_hash"]:
        return "config_hash_mismatch"
    trading = payload.get("trading") if isinstance(payload, dict) else None
    if payload.get("strategy_name") != "golden-kiwi":
        return "config_payload_wrong_strategy"
    if payload.get("mode") != "sim":
        return "config_payload_not_simulation"
    if not isinstance(trading, dict):
        return "config_missing_trading"
    entry = trading.get("entry") if isinstance(trading, dict) else None
    if not isinstance(entry, dict):
        return "config_missing_entry"
    if entry.get("confirmation_steps") != expected["confirmation_steps"]:
        return "config_wrong_confirmation_steps"
    if not _same_number(
        entry.get("min_cumulative_move"),
        expected["min_cumulative_move"],
    ):
        return "config_wrong_min_cumulative_move"
    if not _same_number(entry.get("hold_minutes"), HOLD_MINUTES):
        return "config_wrong_hold_minutes"
    if not _same_number(entry.get("max_exit_delay_minutes"), MAX_EXIT_DELAY_MINUTES):
        return "config_wrong_exit_delay"
    for key, expected_value in FROZEN_TRADING_VALUES.items():
        actual = trading.get(key)
        if isinstance(expected_value, bool):
            if actual is not expected_value:
                return f"config_frozen_control_mismatch_{key}"
        elif isinstance(expected_value, str):
            if actual != expected_value:
                return f"config_frozen_control_mismatch_{key}"
        elif not _same_number(actual, expected_value):
            return f"config_frozen_control_mismatch_{key}"
    for key, expected_value in FROZEN_ENTRY_VALUES.items():
        if not _same_number(entry.get(key), expected_value):
            return f"config_frozen_entry_mismatch_{key}"
    archive = trading.get("archive")
    if not isinstance(archive, dict):
        return "config_missing_archive"
    for key, expected_value in FROZEN_ARCHIVE_VALUES.items():
        if not _same_number(archive.get(key), expected_value):
            return f"config_frozen_archive_mismatch_{key}"
    if trading.get("excluded_categories") != FROZEN_EXCLUDED_CATEGORIES:
        return "config_frozen_excluded_categories_mismatch"
    return None


def _load_runs_and_configs(
    connection: sqlite3.Connection,
) -> tuple[dict[str, sqlite3.Row], dict[str, sqlite3.Row]]:
    runs = {
        str(row["run_id"]): row
        for row in connection.execute("SELECT * FROM run_audits")
    }
    configs = {
        str(row["config_hash"]): row
        for row in connection.execute("SELECT * FROM strategy_configs")
    }
    return runs, configs


def _cohort_for_run(
    run: sqlite3.Row | None,
    *,
    expected: Mapping[str, Any],
    configs: Mapping[str, sqlite3.Row],
) -> tuple[Cohort | None, str | None]:
    if run is None:
        return None, "missing_run"
    if run["status"] != "SUCCESS":
        return None, "run_not_success"
    if run["strategy_name"] != "golden-kiwi":
        return None, "run_wrong_strategy"
    if run["job_name"] != expected["job_name"]:
        return None, "run_wrong_job"
    if run["mode"] != "sim":
        return None, "run_not_simulation"
    config = configs.get(str(run["config_hash"]))
    reason = _canonical_config_reason(config, expected)
    if reason is not None:
        return None, reason
    payload = json.loads(str(config["config_json"]))
    source_digest = str(payload["trading"].get("strategy_source_digest") or "")
    try:
        digest_valid = len(source_digest) == 64 and int(source_digest, 16) >= 0
    except ValueError:
        digest_valid = False
    if not digest_valid:
        return None, "config_missing_strategy_source_digest"
    return (
        Cohort(
            config_hash=str(run["config_hash"]),
            strategy_source_digest=source_digest,
            mode=str(run["mode"]),
            job_name=str(run["job_name"]),
        ),
        None,
    )


def _has_complete_sweep(connection: sqlite3.Connection, run_id: str) -> bool:
    return (
        connection.execute(
            """
            SELECT 1 FROM market_sweeps
            WHERE run_id = ? AND cursor_complete = 1
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        is not None
    )


def _sweep_contract_reason(
    connection: sqlite3.Connection,
    run_id: str,
) -> str | None:
    """Require one complete, bounded filtered-universe sweep for one SUCCESS run."""
    rows = connection.execute(
        """
        SELECT schema_version, started_at, completed_at, cursor_complete,
               pages, raw_market_count, min_liquidity, min_volume,
               max_pages, max_markets, max_elapsed_seconds, elapsed_seconds
        FROM market_sweeps
        WHERE run_id = ?
        ORDER BY sweep_id
        """,
        (run_id,),
    ).fetchall()
    if len(rows) != 1:
        return f"row_count_{len(rows)}"
    row = rows[0]
    if row["schema_version"] != 2:
        return "schema_version_not_2"
    if row["cursor_complete"] != 1:
        return "cursor_incomplete"

    pages = row["pages"]
    raw_markets = row["raw_market_count"]
    max_pages = row["max_pages"]
    max_markets = row["max_markets"]
    if isinstance(pages, bool) or not isinstance(pages, int) or pages < 1:
        return "pages_invalid"
    if (
        isinstance(raw_markets, bool)
        or not isinstance(raw_markets, int)
        or raw_markets < 0
    ):
        return "raw_market_count_invalid"
    if max_pages != 53:
        return "max_pages_not_53"
    if max_markets != 5_330:
        return "max_markets_not_5330"
    if pages > max_pages:
        return "page_budget_exceeded"
    if raw_markets > max_markets:
        return "market_budget_exceeded"
    if raw_markets > pages * 100:
        return "raw_market_count_exceeds_page_capacity"
    if not _same_number(row["min_liquidity"], 20_000.0):
        return "min_liquidity_not_20000"
    if not _same_number(row["min_volume"], 10_000.0):
        return "min_cumulative_volume_not_10000"
    if not _same_number(row["max_elapsed_seconds"], 120.0):
        return "max_elapsed_seconds_not_120"
    elapsed = _finite(row["elapsed_seconds"])
    if elapsed is None or elapsed < 0:
        return "elapsed_seconds_invalid"
    if elapsed > 120.0 + 1e-9:
        return "elapsed_budget_exceeded"
    started = _parse_timestamp(row["started_at"])
    completed = _parse_timestamp(row["completed_at"])
    if started is None or completed is None:
        return "wall_clock_invalid"
    if completed < started:
        return "wall_clock_reversed"
    return None


def _analyze_kill_switch(
    connection: sqlite3.Connection,
    *,
    expected: Mapping[str, Any],
    runs: Mapping[str, sqlite3.Row],
    configs: Mapping[str, sqlite3.Row],
    review_end: datetime,
) -> tuple[dict[str, Any], datetime | None]:
    rows = connection.execute(
        """
        SELECT key, value_json, updated_at
        FROM experiment_state
        WHERE key = 'drawdown_kill_switch'
        """
    ).fetchall()
    if not rows:
        return (
            {
                "status": "NOT_TRIPPED",
                "contract_valid": True,
                "errors": [],
                "state_present": False,
            },
            None,
        )
    if len(rows) != 1:
        return (
            {
                "status": "INVALID",
                "contract_valid": False,
                "errors": ["duplicate_drawdown_kill_switch_state"],
                "state_present": True,
            },
            None,
        )
    row = rows[0]
    errors: list[str] = []
    try:
        payload = json.loads(str(row["value_json"] or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None
        errors.append("invalid_value_json")
    required_fields = {
        "schema_version",
        "tripped",
        "tripped_at",
        "tripped_run_id",
        "economic_pnl",
        "loss_limit_usdc",
        "experiment_capital_usdc",
        "max_drawdown_stop",
    }
    if not isinstance(payload, dict):
        payload = {}
    missing = sorted(required_fields - set(payload))
    if missing:
        errors.append(f"missing_fields:{','.join(missing)}")
    if payload.get("schema_version") != 1:
        errors.append("schema_version_not_1")
    if payload.get("tripped") is not True:
        errors.append("tripped_not_true")

    tripped_at = _parse_timestamp(payload.get("tripped_at"))
    updated_at = _parse_timestamp(row["updated_at"])
    if tripped_at is None:
        errors.append("tripped_at_invalid")
    if updated_at is None:
        errors.append("updated_at_invalid")
    if (
        tripped_at is not None
        and updated_at is not None
        and updated_at + timedelta(microseconds=1) < tripped_at
    ):
        errors.append("updated_at_before_tripped_at")

    economic_pnl = _finite(payload.get("economic_pnl"))
    loss_limit = _finite(payload.get("loss_limit_usdc"))
    capital = _finite(payload.get("experiment_capital_usdc"))
    stop = _finite(payload.get("max_drawdown_stop"))
    if economic_pnl is None:
        errors.append("economic_pnl_invalid")
    if loss_limit is None or loss_limit <= 0:
        errors.append("loss_limit_invalid")
    if capital is None or not _same_number(capital, 100.0):
        errors.append("experiment_capital_mismatch")
    if stop is None or not _same_number(stop, 0.20):
        errors.append("max_drawdown_stop_mismatch")
    if (
        loss_limit is not None
        and capital is not None
        and stop is not None
        and not _same_number(loss_limit, capital * stop)
    ):
        errors.append("loss_limit_formula_mismatch")
    if (
        economic_pnl is not None
        and loss_limit is not None
        and economic_pnl > -loss_limit + 1e-9
    ):
        errors.append("economic_pnl_above_trip_threshold")

    tripped_run_id = str(payload.get("tripped_run_id") or "").strip()
    cohort, run_reason = _cohort_for_run(
        runs.get(tripped_run_id),
        expected=expected,
        configs=configs,
    )
    if run_reason is not None or cohort is None:
        errors.append(f"tripped_run_{run_reason or 'cohort_missing'}")
    status = "INVALID" if errors else "TRIPPED_VALID"
    active_trip_at = (
        tripped_at
        if not errors and tripped_at is not None and tripped_at < review_end
        else None
    )
    return (
        {
            "status": status,
            "contract_valid": not errors,
            "errors": sorted(set(errors)),
            "state_present": True,
            "tripped_at": (_iso_z(tripped_at) if tripped_at is not None else None),
            "tripped_run_id": tripped_run_id or None,
            "economic_pnl": economic_pnl,
            "loss_limit_usdc": loss_limit,
            "experiment_capital_usdc": capital,
            "max_drawdown_stop": stop,
            "updated_at": (_iso_z(updated_at) if updated_at is not None else None),
            "effective_in_review_window": active_trip_at is not None,
        },
        active_trip_at,
    )


def _strict_binary_snapshot_reason(row: sqlite3.Row) -> str | None:
    outcomes = _json_list(row["catalog_outcomes_json"])
    if outcomes != ["Yes", "No"]:
        return "snapshot_not_standard_yes_no"
    prices = _json_list(row["catalog_outcome_prices_json"])
    if prices is None or len(prices) != 2:
        return "snapshot_missing_outcome_prices"
    normalized_prices = [_finite(value) for value in prices]
    if any(value is None or not 0 <= value <= 1 for value in normalized_prices):
        return "snapshot_invalid_outcome_prices"
    tokens = _json_list(row["catalog_token_ids_json"])
    if tokens is None or len(tokens) != 2:
        return "snapshot_missing_token_ids"
    normalized_tokens = [str(value or "").strip() for value in tokens]
    if any(not value for value in normalized_tokens):
        return "snapshot_empty_token_id"
    if len(set(normalized_tokens)) != 2:
        return "snapshot_non_distinct_token_ids"
    if row["catalog_neg_risk"] != 0:
        return "snapshot_neg_risk_or_unknown"
    return None


def _lineage_reason(
    connection: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    expected: Mapping[str, Any],
    entry_cohort: Cohort,
    runs: Mapping[str, sqlite3.Row],
    configs: Mapping[str, sqlite3.Row],
) -> str | None:
    steps = int(expected["confirmation_steps"])
    snapshot_ids = _json_list(trade["trend_snapshot_ids_json"])
    timestamps = _json_list(trade["trend_snapshot_timestamps_json"])
    persisted = _json_list(trade["trend_persisted_prices_json"])
    decisions = _json_list(trade["trend_decision_prices_json"])
    gaps = _json_list(trade["trend_gap_minutes_json"])
    decision_timestamps = _json_list(trade["trend_decision_timestamps_json"])
    decision_gaps = _json_list(trade["trend_decision_gap_minutes_json"])
    if any(
        value is None
        for value in (
            snapshot_ids,
            timestamps,
            persisted,
            decisions,
            gaps,
            decision_timestamps,
            decision_gaps,
        )
    ):
        return "lineage_json_missing_or_invalid"
    assert snapshot_ids is not None
    assert timestamps is not None
    assert persisted is not None
    assert decisions is not None
    assert gaps is not None
    assert decision_timestamps is not None
    assert decision_gaps is not None
    if not (
        len(snapshot_ids)
        == len(timestamps)
        == len(persisted)
        == len(decisions)
        == len(decision_timestamps)
        == steps + 1
        and len(gaps) == len(decision_gaps) == steps
    ):
        return "lineage_length_mismatch"
    try:
        normalized_ids = [int(value) for value in snapshot_ids]
    except (TypeError, ValueError):
        return "lineage_snapshot_id_invalid"
    if len(set(normalized_ids)) != len(normalized_ids):
        return "lineage_snapshot_id_duplicate"
    if trade["confirmation_steps_at_entry"] != steps:
        return "trade_wrong_confirmation_steps"
    if (
        trade["entry_snapshot_id"] != normalized_ids[-1]
        or trade["prior_snapshot_id_at_entry"] != normalized_ids[-2]
        or trade["trend_start_snapshot_id_at_entry"] != normalized_ids[0]
    ):
        return "lineage_anchor_mismatch"

    placeholders = ",".join("?" for _ in normalized_ids)
    snapshot_rows = connection.execute(
        f"SELECT * FROM market_snapshots WHERE id IN ({placeholders})",
        normalized_ids,
    ).fetchall()
    by_id = {int(row["id"]): row for row in snapshot_rows}
    if len(by_id) != len(normalized_ids):
        return "lineage_snapshot_missing"

    parsed_times: list[datetime] = []
    for index, snapshot_id in enumerate(normalized_ids):
        snapshot = by_id[snapshot_id]
        if snapshot["condition_id"] != trade["condition_id"]:
            return "lineage_condition_mismatch"
        snapshot_time = _parse_timestamp(snapshot["timestamp"])
        json_time = _parse_timestamp(timestamps[index])
        if snapshot_time is None or json_time is None:
            return "lineage_timestamp_invalid"
        if abs((snapshot_time - json_time).total_seconds()) > 1e-3:
            return "lineage_timestamp_mismatch"
        if not _same_number(snapshot["probability"], persisted[index]):
            return "lineage_persisted_price_mismatch"
        if _finite(decisions[index]) is None:
            return "lineage_decision_price_invalid"
        snapshot_run_id = str(snapshot["run_id"] or "")
        snapshot_cohort, reason = _cohort_for_run(
            runs.get(snapshot_run_id),
            expected=expected,
            configs=configs,
        )
        if reason is not None:
            return f"lineage_{reason}"
        if snapshot_cohort != entry_cohort:
            return "lineage_cross_cohort"
        if not _has_complete_sweep(connection, snapshot_run_id):
            return "lineage_cursor_incomplete"
        strict_reason = _strict_binary_snapshot_reason(snapshot)
        if strict_reason is not None:
            return strict_reason
        parsed_times.append(snapshot_time)

    signal_time = _parse_timestamp(trade["signal_timestamp_at_entry"])
    if signal_time is None:
        return "signal_timestamp_missing"
    if abs((signal_time - parsed_times[-1]).total_seconds()) > 1e-3:
        return "signal_timestamp_mismatch"
    for index, (previous, current) in enumerate(zip(parsed_times, parsed_times[1:])):
        observed_gap = (current - previous).total_seconds() / 60.0
        if not _same_number(observed_gap, gaps[index], tolerance=1e-6):
            return "lineage_gap_mismatch"
        if not 3.0 <= observed_gap <= 10.0:
            return "lineage_gap_out_of_range"
    normalized_decisions = [_finite(value) for value in decisions]
    if any(value is None for value in normalized_decisions):
        return "lineage_decision_price_invalid"
    decision_prices = [float(value) for value in normalized_decisions]
    deltas = [
        current - previous
        for previous, current in zip(decision_prices, decision_prices[1:])
    ]
    if any(delta <= 0 or delta > 0.02 + 1e-9 for delta in deltas):
        return "lineage_decision_step_not_qualifying"
    cumulative = sum(deltas)
    if (
        cumulative + 1e-9 < float(expected["min_cumulative_move"])
        or cumulative > 0.04 + 1e-9
    ):
        return "lineage_decision_cumulative_not_qualifying"
    if not 0.20 <= decision_prices[-1] <= 0.80:
        return "lineage_decision_final_price_out_of_range"
    if any(
        not _same_number(decision_prices[index], persisted[index])
        for index in range(steps)
    ):
        return "lineage_decision_prior_price_mismatch"

    parsed_decision_times = [_parse_timestamp(value) for value in decision_timestamps]
    if any(value is None for value in parsed_decision_times):
        return "lineage_decision_timestamp_invalid"
    decision_times = [value for value in parsed_decision_times if value is not None]
    if any(
        abs((decision_times[index] - parsed_times[index]).total_seconds()) > 1e-3
        for index in range(steps)
    ):
        return "lineage_decision_prior_timestamp_mismatch"
    observed_at = _parse_timestamp(trade["decision_observed_at_at_entry"])
    buy_time = _parse_timestamp(trade["buy_timestamp"])
    if observed_at is None or buy_time is None:
        return "decision_observation_timestamp_missing"
    if (
        abs((decision_times[-1] - observed_at).total_seconds()) > 1e-3
        or abs((observed_at - buy_time).total_seconds()) > 1e-3
    ):
        return "decision_observation_timestamp_mismatch"
    if trade["decision_price_source_at_entry"] != "clob_single_order_book_midpoint":
        return "decision_price_source_invalid"
    for index, (previous, current) in enumerate(
        zip(decision_times, decision_times[1:])
    ):
        observed_gap = (current - previous).total_seconds() / 60.0
        if not _same_number(observed_gap, decision_gaps[index], tolerance=1e-6):
            return "lineage_decision_gap_mismatch"
        if not 3.0 <= observed_gap <= 10.0:
            return "lineage_decision_gap_out_of_range"
    fresh_bid = _finite(trade["best_bid_at_buy"])
    fresh_ask = _finite(trade["best_ask_at_buy"])
    if fresh_bid is None or fresh_ask is None or not 0 < fresh_bid <= fresh_ask < 1:
        return "decision_fresh_book_invalid"
    if not _same_number(decision_prices[-1], (fresh_bid + fresh_ask) / 2.0):
        return "decision_price_not_fresh_book_midpoint"
    return None


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _event_metrics(
    signals: Sequence[ValidSignal],
) -> dict[str, Any]:
    event_returns: dict[str, list[float]] = defaultdict(list)
    for signal in signals:
        event_returns[signal.event_id].append(signal.executable_return)
    equal_returns = [
        statistics.fmean(values) for _, values in sorted(event_returns.items())
    ]
    mean_return = statistics.fmean(equal_returns) if equal_returns else None
    lower_ci = None
    if len(equal_returns) >= 2:
        randomizer = random.Random(BOOTSTRAP_SEED)
        draws = [
            statistics.fmean(
                randomizer.choice(equal_returns) for _ in range(len(equal_returns))
            )
            for _ in range(BOOTSTRAP_DRAWS)
        ]
        lower_ci = _percentile(draws, CI_LOWER_QUANTILE)
    return {
        "signals": len(signals),
        "unique_events": len(event_returns),
        "event_equal_return": mean_return,
        "event_equal_lower_ci_98_75": lower_ci,
        "cost_stressed_event_equal_return": (
            None if mean_return is None else mean_return - COST_STRESS
        ),
        "cost_stressed_lower_ci_98_75": (
            None if lower_ci is None else lower_ci - COST_STRESS
        ),
        "ci_method": (
            (
                "deterministic_event_cluster_bootstrap_"
                f"{BOOTSTRAP_DRAWS}_seed_{BOOTSTRAP_SEED}_"
                "two_sided_98.75pct_lower_q_0.00625"
            )
            if lower_ci is not None
            else "unavailable_fewer_than_2_events"
        ),
    }


def _classify_trade(
    connection: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    expected: Mapping[str, Any],
    end: datetime,
    runs: Mapping[str, sqlite3.Row],
    configs: Mapping[str, sqlite3.Row],
) -> tuple[ValidSignal | None, str | None, bool]:
    """Return signal, censor reason, and whether the target was observable."""
    buy_time = _parse_timestamp(trade["buy_timestamp"])
    if buy_time is None:
        return None, "buy_timestamp_missing", True
    target_observable = (
        buy_time + timedelta(minutes=HOLD_MINUTES + MAX_EXIT_DELAY_MINUTES) <= end
    )
    if not target_observable:
        return None, "target_not_observable_by_window_end", False
    if trade["mode"] != "sim":
        return None, "trade_not_simulation", True

    entry_run_id = str(trade["entry_run_id"] or "")
    entry_cohort, reason = _cohort_for_run(
        runs.get(entry_run_id), expected=expected, configs=configs
    )
    if reason is not None or entry_cohort is None:
        return None, f"entry_{reason or 'cohort_missing'}", True
    if not _has_complete_sweep(connection, entry_run_id):
        return None, "entry_cursor_incomplete", True

    lineage_reason = _lineage_reason(
        connection,
        trade,
        expected=expected,
        entry_cohort=entry_cohort,
        runs=runs,
        configs=configs,
    )
    if lineage_reason is not None:
        return None, lineage_reason, True

    exit_run_id = str(trade["exit_run_id"] or "")
    exit_cohort, reason = _cohort_for_run(
        runs.get(exit_run_id), expected=expected, configs=configs
    )
    if reason is not None or exit_cohort is None:
        return None, f"exit_{reason or 'cohort_missing'}", True
    if exit_cohort != entry_cohort:
        return None, "entry_exit_cross_cohort", True
    if not _has_complete_sweep(connection, exit_run_id):
        return None, "exit_cursor_incomplete", True

    if str(trade["status"] or "").upper() != "COMPLETED":
        return None, "not_completed_time_exit", True
    delay = _finite(trade["exit_delay_minutes"])
    if delay is None:
        return (
            None,
            (
                "promotion_not_eligible"
                if trade["promotion_eligible"] != 1
                else "exit_delay_missing"
            ),
            True,
        )
    if delay < 0 or delay > MAX_EXIT_DELAY_MINUTES + 1e-9:
        return None, "exit_delay_out_of_range", True
    if trade["promotion_eligible"] != 1:
        return None, "promotion_not_eligible", True
    sell_time = _parse_timestamp(trade["sell_timestamp"])
    if sell_time is None:
        return None, "sell_timestamp_missing", True
    if sell_time >= end:
        return None, "exit_after_window", True
    elapsed = (sell_time - buy_time).total_seconds() / 60.0
    if elapsed + 1e-6 < HOLD_MINUTES:
        return None, "exit_before_target", True
    if not _same_number(elapsed - HOLD_MINUTES, delay, tolerance=1e-3):
        return None, "exit_delay_timestamp_mismatch", True

    entry_ask = _finite(trade["best_ask_at_buy"])
    exit_bid = _finite(trade["best_bid_at_exit"])
    if entry_ask is None:
        return None, "entry_ask_missing", True
    if exit_bid is None:
        return None, "exit_bid_missing", True
    if not 0 < entry_ask < 1 or not 0 < exit_bid < 1:
        return None, "executable_quote_invalid", True
    event_id = str(trade["event_id"] or "").strip()
    if not event_id:
        return None, "event_id_missing", True
    return (
        ValidSignal(
            trade_id=int(trade["id"]),
            event_id=event_id,
            cohort=entry_cohort,
            signal_timestamp=(
                _parse_timestamp(trade["signal_timestamp_at_entry"]) or buy_time
            ),
            executable_return=exit_bid / entry_ask - 1.0,
        ),
        None,
        True,
    )


def analyze_arm(
    arm: str,
    db_path: Path,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    normalized_arm = arm.upper()
    if normalized_arm not in CANONICAL_ARMS:
        raise AnalysisContractError(f"unknown arm: {arm}")
    expected = CANONICAL_ARMS[normalized_arm]
    connection = _open_read_only(db_path)
    try:
        _validate_schema(connection)
        runs, configs = _load_runs_and_configs(connection)
        mapping_errors: list[str] = []
        kill_switch, kill_switch_trip_at = _analyze_kill_switch(
            connection,
            expected=expected,
            runs=runs,
            configs=configs,
            review_end=end,
        )
        if not kill_switch["contract_valid"]:
            mapping_errors.append(
                "drawdown kill-switch state invalid: "
                + ", ".join(kill_switch["errors"])
            )
        successful_kiwi_runs = [
            row
            for row in runs.values()
            if row["status"] == "SUCCESS"
            and row["strategy_name"] == "golden-kiwi"
            and (
                (started_at := _parse_timestamp(row["started_at"])) is not None
                and start <= started_at < end
            )
        ]
        wrong_jobs = sorted(
            {
                str(row["job_name"])
                for row in successful_kiwi_runs
                if row["job_name"] != expected["job_name"]
            }
        )
        if wrong_jobs:
            mapping_errors.append(
                f"successful runs use noncanonical jobs: {wrong_jobs}"
            )
        canonical_success_runs = [
            row
            for row in successful_kiwi_runs
            if row["job_name"] == expected["job_name"]
        ]
        if not canonical_success_runs:
            mapping_errors.append(
                f"no SUCCESS run for canonical job {expected['job_name']}"
            )
        for row in canonical_success_runs:
            _, reason = _cohort_for_run(row, expected=expected, configs=configs)
            if reason is not None:
                mapping_errors.append(
                    f"run {row['run_id']} violates arm contract: {reason}"
                )

        # Trade volume is tiny relative to the snapshot archive.  Parse every
        # trade timestamp in Python because SQLite text timestamps can use
        # either a space or "T", making lexical range predicates unsafe.
        rows = connection.execute(
            """
            SELECT * FROM trades
            ORDER BY COALESCE(signal_timestamp_at_entry, buy_timestamp), id
            """
        ).fetchall()
        trades: list[sqlite3.Row] = []
        for row in rows:
            signal_time = _parse_timestamp(
                row["signal_timestamp_at_entry"] or row["buy_timestamp"]
            )
            if signal_time is not None and start <= signal_time < end:
                trades.append(row)

        censors: Counter[str] = Counter()
        valid: list[ValidSignal] = []
        mature_count = 0
        post_trip_buy_trade_ids: list[int] = []
        for trade in trades:
            buy_time = _parse_timestamp(trade["buy_timestamp"])
            if (
                kill_switch_trip_at is not None
                and buy_time is not None
                and buy_time >= kill_switch_trip_at
            ):
                if (
                    buy_time + timedelta(minutes=HOLD_MINUTES + MAX_EXIT_DELAY_MINUTES)
                    <= end
                ):
                    mature_count += 1
                censors["buy_after_drawdown_kill_switch"] += 1
                post_trip_buy_trade_ids.append(int(trade["id"]))
                continue
            signal, reason, target_observable = _classify_trade(
                connection,
                trade,
                expected=expected,
                end=end,
                runs=runs,
                configs=configs,
            )
            if target_observable:
                mature_count += 1
            if reason is not None:
                censors[reason] += 1
            elif signal is not None:
                valid.append(signal)
        if post_trip_buy_trade_ids:
            mapping_errors.append(
                "BUY trades exist at/after drawdown kill-switch trip: "
                + ",".join(str(value) for value in post_trip_buy_trade_ids)
            )
        kill_switch["post_trip_buy_trade_ids"] = post_trip_buy_trade_ids
        kill_switch["post_trip_buy_count"] = len(post_trip_buy_trade_ids)

        collection_cohorts: dict[tuple[str, str, str, str], Cohort] = {}
        for row in canonical_success_runs:
            cohort, reason = _cohort_for_run(row, expected=expected, configs=configs)
            if reason is None and cohort is not None:
                collection_cohorts[cohort.key()] = cohort

        by_cohort: dict[tuple[str, str, str, str], list[ValidSignal]] = defaultdict(
            list
        )
        for signal in valid:
            by_cohort[signal.cohort.key()].append(signal)
        cohort_results: list[dict[str, Any]] = []
        for key, cohort in sorted(collection_cohorts.items()):
            signals = by_cohort.get(key, [])
            cohort_results.append(
                {
                    **cohort.as_dict(),
                    **_event_metrics(signals),
                }
            )
        metrics = (
            _event_metrics(valid)
            if len(collection_cohorts) <= 1
            else {
                "signals": len(valid),
                "unique_events": None,
                "event_equal_return": None,
                "event_equal_lower_ci_98_75": None,
                "cost_stressed_event_equal_return": None,
                "cost_stressed_lower_ci_98_75": None,
                "ci_method": "unavailable_multiple_cohorts_not_pooled",
            }
        )
        coverage = len(valid) / mature_count if mature_count else 0.0
        midpoint = start + (end - start) / 2
        early_metrics = (
            _event_metrics(
                [signal for signal in valid if signal.signal_timestamp < midpoint]
            )
            if len(collection_cohorts) <= 1
            else _event_metrics([])
        )
        late_metrics = (
            _event_metrics(
                [signal for signal in valid if signal.signal_timestamp >= midpoint]
            )
            if len(collection_cohorts) <= 1
            else _event_metrics([])
        )
        return {
            "arm": normalized_arm,
            "canonical_job": expected["job_name"],
            "confirmation_steps": expected["confirmation_steps"],
            "min_cumulative_move": expected["min_cumulative_move"],
            "database": str(db_path.expanduser().resolve()),
            "mapping_errors": sorted(set(mapping_errors)),
            "contract_valid": not mapping_errors,
            "drawdown_kill_switch": kill_switch,
            "trades_in_window": len(trades),
            "mature_target_signals": mature_count,
            "quote_complete_signals": len(valid),
            "target_quote_coverage": coverage,
            "coverage_denominator": (
                "recorded mature trades only; raw eligible/counterfactual "
                "signals are not persisted"
            ),
            "censor_reasons": dict(sorted(censors.items())),
            "cohort_count": len(collection_cohorts),
            "cohorts": cohort_results,
            "early_half": early_metrics,
            "late_half": late_metrics,
            **metrics,
        }
    finally:
        connection.rollback()
        connection.close()


def _check(
    actual: Any,
    passed: bool,
    requirement: str,
) -> dict[str, Any]:
    return {
        "passed": bool(passed),
        "actual": actual,
        "requirement": requirement,
    }


def _analyze_experiment_legacy(
    databases: Mapping[str, Path],
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if end - start != timedelta(days=WINDOW_DAYS):
        raise AnalysisContractError(
            "analysis window must be exactly 30 days and half-open [start, end)"
        )
    normalized = {arm.upper(): Path(path) for arm, path in databases.items()}
    if set(normalized) != set(CANONICAL_ARMS):
        raise AnalysisContractError(
            "exactly one database for each canonical arm A, B, C, D is required"
        )
    arms = {
        arm: analyze_arm(arm, normalized[arm], start=start, end=end)
        for arm in CANONICAL_ARMS
    }
    source_digests = {
        cohort["strategy_source_digest"]
        for result in arms.values()
        for cohort in result["cohorts"]
    }
    shared_source_digest = len(source_digests) == 1 and all(
        result["cohort_count"] == 1 for result in arms.values()
    )

    primary = arms["B"]
    checks = {
        "canonical_mapping": _check(
            all(result["contract_valid"] for result in arms.values()),
            all(result["contract_valid"] for result in arms.values()),
            "all four DBs match canonical job and frozen arm config",
        ),
        "single_cohort_per_arm": _check(
            {arm: result["cohort_count"] for arm, result in arms.items()},
            all(result["cohort_count"] == 1 for result in arms.values()),
            "one config_hash × strategy_source_digest × mode × job cohort per arm",
        ),
        "shared_strategy_source_digest": _check(
            sorted(source_digests),
            shared_source_digest,
            "all four arms use one strategy-relevant source digest",
        ),
        "raw_counterfactual_signals": _check(
            None,
            False,
            ("primary B point-in-time raw selected signals with target quotes >= 50"),
        ),
        "raw_counterfactual_events": _check(
            None,
            False,
            "primary B raw selected unique event clusters >= 30",
        ),
        "raw_target_quote_coverage": _check(
            None,
            False,
            (
                "primary B raw selected signal target/quote coverage >= 90%; "
                "trade-only denominator is not a substitute"
            ),
        ),
        "raw_positive_pre_fee_edge": _check(
            None,
            False,
            "primary B raw event-equal counterfactual pre-fee edge > 0",
        ),
        "raw_lower_ci_positive": _check(
            None,
            False,
            ("primary B raw event-cluster bootstrap 98.75% lower CI > 0"),
        ),
        "raw_cost_stressed_lower_ci_positive": _check(
            None,
            False,
            "primary B 10.4bps-stressed 98.75% lower CI > 0",
        ),
        "raw_early_half_positive": _check(
            None,
            False,
            "predeclared primary B raw early-half event-equal edge > 0",
        ),
        "raw_late_half_positive": _check(
            None,
            False,
            "predeclared primary B raw late-half event-equal edge > 0",
        ),
        "promotion_evidence_complete": _check(
            list(UNSUPPORTED_PROMOTION_EVIDENCE),
            False,
            "all promotion-critical source, audit, and cadence evidence proven",
        ),
    }
    # The present DB schema cannot prove every frozen promotion contract.
    # Descriptive statistics can therefore never create a PASS.
    passed = False
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy": "golden-kiwi",
        "experiment": "Micro-Cascade frozen A/B/C/D",
        "window": {
            "start_inclusive": _iso_z(start),
            "end_exclusive": _iso_z(end),
            "days": WINDOW_DAYS,
        },
        "primary_metric": (
            "raw point-in-time event-equal mean("
            "first +60..75m snapshot best_bid / signal snapshot best_ask - 1)"
        ),
        "primary_metric_status": "NOT_RECONSTRUCTED_FAIL_CLOSED",
        "reported_diagnostic_metric": (
            "recorded-trade subset event-equal mean(exit_best_bid / entry_best_ask - 1)"
        ),
        "missing_evidence_policy": "censor_with_reason_never_impute",
        "evaluation_scope": {
            "descriptive_metrics_supported": True,
            "promotion_decision_supported": False,
            "unsupported_promotion_evidence": list(UNSUPPORTED_PROMOTION_EVIDENCE),
        },
        "arms": arms,
        "recorded_trade_subset_diagnostics": {
            "primary_b_signals": primary["quote_complete_signals"],
            "primary_b_unique_events": primary["unique_events"],
            "primary_b_event_equal_return": primary["event_equal_return"],
            "primary_b_lower_ci_98_75": (primary["event_equal_lower_ci_98_75"]),
            "must_not_be_used_as_raw_denominator": True,
        },
        "experiment_contract": {
            "shared_strategy_source_digest": shared_source_digest,
            "strategy_source_digests": sorted(source_digests),
        },
        "primary_b_gate": {
            "passed": passed,
            "verdict": (
                "ELIGIBLE_FOR_SHADOW_EXECUTION_REVIEW"
                if passed
                else "NOT_EVALUABLE_FAIL_CLOSED"
            ),
            "checks": checks,
            "note": (
                "Passing does not authorize live execution; it only permits "
                "the separately approved shadow-execution review."
            ),
        },
    }


def _validate_v2_schema(connection: sqlite3.Connection) -> None:
    """Validate the append-only raw-decision/follow-up evidence contract."""
    tables = {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing_tables = sorted(set(V2_REQUIRED_TABLE_COLUMNS) - tables)
    if missing_tables:
        raise AnalysisContractError(
            "v2 evidence tables missing: " + ", ".join(missing_tables)
        )
    missing_columns: dict[str, list[str]] = {}
    for table, required in V2_REQUIRED_TABLE_COLUMNS.items():
        missing = sorted(required - _table_columns(connection, table))
        if missing:
            missing_columns[table] = missing
    if missing_columns:
        raise AnalysisContractError(
            "v2 evidence columns missing: "
            + json.dumps(missing_columns, sort_keys=True)
        )


def _database_has_v2_evidence(path: Path) -> bool:
    connection = _open_read_only(path)
    try:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        return set(V2_REQUIRED_TABLE_COLUMNS).issubset(tables)
    finally:
        connection.rollback()
        connection.close()


def _load_v2_contract(
    connection: sqlite3.Connection,
    *,
    arm: str,
    start: datetime,
    end: datetime,
) -> tuple[dict[str, Any], list[str]]:
    expected = CANONICAL_ARMS[arm]
    rows = connection.execute(
        "SELECT * FROM micro_cascade_experiment_contracts"
    ).fetchall()
    errors: list[str] = []
    if len(rows) != 1:
        return (
            {"rows": len(rows), "valid": False},
            [f"experiment_contract_row_count:{len(rows)}"],
        )
    row = rows[0]
    contract_start = _parse_timestamp(row["window_start"])
    contract_end = _parse_timestamp(row["window_end"])
    observed = {
        "canonical_job": row["canonical_job"],
        "arm": row["arm"],
        "schema_version": row["schema_version"],
        "analyzer_version": row["analyzer_version"],
        "preregistration_sha256": row["preregistration_sha256"],
        "window_start": (
            _iso_z(contract_start) if contract_start is not None else None
        ),
        "window_end": (_iso_z(contract_end) if contract_end is not None else None),
        "expected_cadence_minutes": row["expected_cadence_minutes"],
        "expected_offset_minute": row["expected_offset_minute"],
    }
    if row["canonical_job"] != expected["job_name"]:
        errors.append("contract_wrong_canonical_job")
    if row["arm"] != arm:
        errors.append("contract_wrong_arm")
    if row["schema_version"] != EXPERIMENT_CONTRACT_SCHEMA_VERSION:
        errors.append("contract_wrong_schema_version")
    if row["analyzer_version"] != ANALYZER_VERSION:
        errors.append("contract_wrong_analyzer_version")
    if row["preregistration_sha256"] != PREREGISTRATION_SHA256:
        errors.append("contract_wrong_preregistration_sha256")
    if contract_start != start or contract_end != end:
        errors.append("contract_wrong_shared_window")
    if row["expected_cadence_minutes"] != EXPECTED_CADENCE_MINUTES:
        errors.append("contract_wrong_expected_cadence")
    offset = row["expected_offset_minute"]
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset < EXPECTED_CADENCE_MINUTES
    ):
        errors.append("contract_invalid_expected_offset")
    elif offset != expected["expected_offset_minute"]:
        errors.append("contract_wrong_expected_offset")
    return ({**observed, "valid": not errors}, errors)


def _load_strict_audit(
    value: Mapping[str, Any] | Path | str | None,
) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise AnalysisContractError(f"strict audit does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AnalysisContractError(
            f"cannot read strict audit {path}: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise AnalysisContractError(f"strict audit is not an object: {path}")
    return payload


def _validate_strict_audit(
    payload: Mapping[str, Any] | None,
    *,
    database: Path,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    errors: list[str] = []
    if payload is None:
        return {
            "valid": False,
            "errors": ["strict_audit_missing"],
            "critical": None,
            "high": None,
        }
    if payload.get("schema_version") != 1:
        errors.append("strict_audit_wrong_schema_version")
    period = payload.get("period")
    audit_start = (
        _parse_timestamp(period.get("start")) if isinstance(period, Mapping) else None
    )
    audit_end = (
        _parse_timestamp(period.get("end")) if isinstance(period, Mapping) else None
    )
    if audit_start != start or audit_end != end:
        errors.append("strict_audit_wrong_period")
    counts = payload.get("issue_counts")
    if not isinstance(counts, Mapping):
        counts = {}
        errors.append("strict_audit_issue_counts_missing")
    critical = counts.get("CRITICAL", 0)
    high = counts.get("HIGH", 0)
    if isinstance(critical, bool) or not isinstance(critical, int) or critical < 0:
        errors.append("strict_audit_critical_count_invalid")
    elif critical:
        errors.append("strict_audit_has_critical")
    if isinstance(high, bool) or not isinstance(high, int) or high < 0:
        errors.append("strict_audit_high_count_invalid")
    elif high:
        errors.append("strict_audit_has_high")
    database_rows = payload.get("databases")
    expected_path = str(database.expanduser().resolve())
    target_rows = (
        [
            row
            for row in database_rows
            if isinstance(row, Mapping) and str(row.get("database")) == expected_path
        ]
        if isinstance(database_rows, list)
        else []
    )
    observed_paths = (
        {
            str(row.get("database"))
            for row in database_rows
            if isinstance(row, Mapping) and row.get("database")
        }
        if isinstance(database_rows, list)
        else set()
    )
    if observed_paths != {expected_path}:
        errors.append("strict_audit_database_mismatch")
    if (
        not isinstance(database_rows, list)
        or len(database_rows) != 1
        or payload.get("database_count") != 1
    ):
        errors.append("strict_audit_database_count_mismatch")
    if len(target_rows) != 1 or target_rows[0].get("status") != "PASS":
        errors.append("strict_audit_database_status_not_pass")
    audited_digest = (
        str(target_rows[0].get("database_sha256") or "").strip().lower()
        if len(target_rows) == 1
        else ""
    )
    if len(audited_digest) != 64 or any(
        character not in "0123456789abcdef" for character in audited_digest
    ):
        errors.append("strict_audit_database_sha256_missing_or_invalid")
    current_digest = _sha256_file(database)
    if audited_digest and audited_digest != current_digest:
        errors.append("strict_audit_database_sha256_mismatch")
    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "critical": critical,
        "high": high,
        "period_start": _iso_z(audit_start) if audit_start else None,
        "period_end": _iso_z(audit_end) if audit_end else None,
        "database_paths": sorted(observed_paths),
        "audited_database_sha256": audited_digest or None,
        "analysis_database_sha256": current_digest,
    }


def _scheduled_slots(
    start: datetime,
    end: datetime,
    *,
    cadence_minutes: int,
    offset_minute: int,
) -> list[datetime]:
    cursor = start.replace(second=0, microsecond=0)
    if cursor < start:
        cursor += timedelta(minutes=1)
    while cursor.minute % cadence_minutes != offset_minute:
        cursor += timedelta(minutes=1)
    slots: list[datetime] = []
    step = timedelta(minutes=cadence_minutes)
    while cursor < end:
        slots.append(cursor)
        cursor += step
    return slots


def _row_value(row: sqlite3.Row | Mapping[str, Any], key: str) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return None


def _cadence_assessment(
    runs: Sequence[sqlite3.Row],
    *,
    start: datetime,
    end: datetime,
    cadence_minutes: int,
    offset_minute: int,
) -> CadenceAssessment:
    slots = _scheduled_slots(
        start,
        end,
        cadence_minutes=cadence_minutes,
        offset_minute=offset_minute,
    )
    if not slots:
        return CadenceAssessment(
            metrics={
                "valid": False,
                "expected_slots": 0,
                "covered_slots": 0,
                "coverage": 0.0,
                "duplicate_success_runs": 0,
                "off_window_success_runs": len(runs),
                "off_schedule_success_runs": 0,
                "max_missing_slot_streak": 0,
                "eligible_success_runs": 0,
                "excluded_success_runs": len(runs),
                "invalid_reasons": ["no_scheduled_slots"],
            },
            eligible_run_ids=frozenset(),
        )
    first_slot = slots[0]
    cadence = timedelta(minutes=cadence_minutes)
    covered: dict[int, list[sqlite3.Row]] = defaultdict(list)
    outside = 0
    off_schedule = 0
    for run in runs:
        started = _parse_timestamp(run["started_at"])
        if started is None or not start <= started < end or started < first_slot:
            outside += 1
            continue
        # A run in the right five-minute bucket is not sufficient evidence for
        # the preregistered offset.  For example, minute 1 must not satisfy an
        # offset-0 contract merely because it falls in the 00:00-00:05 bucket.
        if started.minute % cadence_minutes != offset_minute:
            off_schedule += 1
            continue
        index = int((started - first_slot) // cadence)
        if (
            index < 0
            or index >= len(slots)
            or not slots[index] <= started < slots[index] + cadence
        ):
            outside += 1
            continue
        covered[index].append(run)
    max_missing = current_missing = 0
    for index in range(len(slots)):
        if index in covered:
            current_missing = 0
        else:
            current_missing += 1
            max_missing = max(max_missing, current_missing)
    covered_count = len(covered)
    coverage = covered_count / len(slots)
    duplicates = sum(
        len(slot_runs) - 1 for slot_runs in covered.values() if len(slot_runs) > 1
    )
    eligible_run_ids = {
        str(run_id).strip()
        for slot_runs in covered.values()
        if len(slot_runs) == 1
        for run_id in (_row_value(slot_runs[0], "run_id"),)
        if str(run_id or "").strip()
    }
    invalid_reasons = []
    if coverage < 0.90:
        invalid_reasons.append("coverage_below_90_percent")
    if duplicates:
        invalid_reasons.append("duplicate_success_runs")
    if outside:
        invalid_reasons.append("off_window_success_runs")
    if off_schedule:
        invalid_reasons.append("off_schedule_success_runs")
    excluded_success_runs = len(runs) - len(eligible_run_ids)
    return CadenceAssessment(
        metrics={
            "valid": not invalid_reasons,
            "expected_slots": len(slots),
            "covered_slots": covered_count,
            "coverage": coverage,
            "duplicate_success_runs": duplicates,
            "off_window_success_runs": outside,
            "off_schedule_success_runs": off_schedule,
            "max_missing_slot_streak": max_missing,
            "cadence_minutes": cadence_minutes,
            "offset_minute": offset_minute,
            "eligible_success_runs": len(eligible_run_ids),
            "excluded_success_runs": excluded_success_runs,
            "invalid_reasons": invalid_reasons,
            "denominator": "predeclared UTC cadence slots in [start,end)",
        },
        eligible_run_ids=frozenset(eligible_run_ids),
    )


def _cadence_metrics(
    runs: Sequence[sqlite3.Row],
    *,
    start: datetime,
    end: datetime,
    cadence_minutes: int,
    offset_minute: int,
) -> dict[str, Any]:
    return _cadence_assessment(
        runs,
        start=start,
        end=end,
        cadence_minutes=cadence_minutes,
        offset_minute=offset_minute,
    ).metrics


def _raw_lineage_reason(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    expected: Mapping[str, Any],
    source_cohort: Cohort,
    runs: Mapping[str, sqlite3.Row],
    configs: Mapping[str, sqlite3.Row],
    cadence_eligible_run_ids: frozenset[str],
) -> str | None:
    steps = int(expected["confirmation_steps"])
    ids = _json_list(row["trend_snapshot_ids_json"])
    timestamps = _json_list(row["trend_snapshot_timestamps_json"])
    prices = _json_list(row["trend_prices_json"])
    gaps = _json_list(row["trend_gap_minutes_json"])
    if any(value is None for value in (ids, timestamps, prices, gaps)):
        return "decision_lineage_json_invalid"
    assert ids is not None
    assert timestamps is not None
    assert prices is not None
    assert gaps is not None
    if not (
        len(ids) == len(timestamps) == len(prices) == steps + 1 and len(gaps) == steps
    ):
        return "decision_lineage_length_mismatch"
    try:
        normalized_ids = [int(value) for value in ids]
    except (TypeError, ValueError):
        return "decision_lineage_snapshot_id_invalid"
    if (
        any(value <= 0 for value in normalized_ids)
        or normalized_ids != sorted(normalized_ids)
        or len(set(normalized_ids)) != len(normalized_ids)
        or normalized_ids[-1] != row["entry_snapshot_id"]
    ):
        return "decision_lineage_snapshot_id_order_invalid"
    normalized_times = [_parse_timestamp(value) for value in timestamps]
    normalized_prices = [_finite(value) for value in prices]
    normalized_gaps = [_finite(value) for value in gaps]
    if (
        any(value is None for value in normalized_times)
        or any(value is None for value in normalized_prices)
        or any(value is None for value in normalized_gaps)
    ):
        return "decision_lineage_value_invalid"
    time_values = [value for value in normalized_times if value is not None]
    price_values = [float(value) for value in normalized_prices if value is not None]
    gap_values = [float(value) for value in normalized_gaps if value is not None]
    placeholders = ",".join("?" for _ in normalized_ids)
    snapshots = connection.execute(
        f"SELECT * FROM market_snapshots WHERE id IN ({placeholders})",
        normalized_ids,
    ).fetchall()
    by_id = {int(snapshot["id"]): snapshot for snapshot in snapshots}
    if len(by_id) != len(normalized_ids):
        return "decision_lineage_snapshot_missing"
    for index, snapshot_id in enumerate(normalized_ids):
        snapshot = by_id[snapshot_id]
        if snapshot["condition_id"] != row["condition_id"]:
            return "decision_lineage_condition_mismatch"
        actual_time = _parse_timestamp(snapshot["timestamp"])
        if (
            actual_time is None
            or abs((actual_time - time_values[index]).total_seconds()) > 1e-3
        ):
            return "decision_lineage_timestamp_mismatch"
        if not _same_number(snapshot["probability"], price_values[index]):
            return "decision_lineage_probability_mismatch"
        snapshot_run_id = str(snapshot["run_id"] or "")
        if snapshot_run_id not in cadence_eligible_run_ids:
            return "decision_lineage_run_not_cadence_eligible"
        snapshot_cohort, reason = _cohort_for_run(
            runs.get(snapshot_run_id),
            expected=expected,
            configs=configs,
        )
        if reason is not None or snapshot_cohort is None:
            return f"decision_lineage_{reason or 'cohort_missing'}"
        if snapshot_cohort != source_cohort:
            return "decision_lineage_cross_cohort"
        if not _has_complete_sweep(connection, snapshot_run_id):
            return "decision_lineage_cursor_incomplete"
    for index, (previous, current) in enumerate(zip(time_values, time_values[1:])):
        observed_gap = (current - previous).total_seconds() / 60.0
        if (
            not _same_number(observed_gap, gap_values[index], tolerance=1e-6)
            or not 3.0 <= observed_gap <= 10.0
        ):
            return "decision_lineage_gap_invalid"
    deltas = [
        current - previous for previous, current in zip(price_values, price_values[1:])
    ]
    if any(delta <= 0 or delta > 0.02 + 1e-9 for delta in deltas):
        return "decision_lineage_step_invalid"
    cumulative = sum(deltas)
    if (
        cumulative + 1e-9 < float(expected["min_cumulative_move"])
        or cumulative > 0.04 + 1e-9
    ):
        return "decision_lineage_cumulative_invalid"
    if not 0.20 <= price_values[-1] <= 0.80:
        return "decision_lineage_final_price_invalid"
    current_snapshot = by_id[normalized_ids[-1]]
    if (
        not _same_number(current_snapshot["probability"], row["snapshot_probability"])
        or not _same_number(current_snapshot["best_bid"], row["snapshot_best_bid"])
        or not _same_number(current_snapshot["best_ask"], row["snapshot_best_ask"])
    ):
        return "decision_snapshot_anchor_mismatch"
    return None


def _funnel_integrity_errors(
    rows: Sequence[sqlite3.Row],
    *,
    arm: str,
    expected_job: str,
) -> list[str]:
    errors: list[str] = []
    by_run: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_run[str(row["run_id"])].append(row)
    for run_id, run_rows in by_run.items():
        by_event: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in run_rows:
            by_event[str(row["event_id"])].append(row)
            row_id = row["id"]
            if row["arm"] != arm or row["canonical_job"] != expected_job:
                errors.append(f"funnel_arm_job_invalid:{run_id}:{row_id}")
            if row["collection_eligible"] != 1:
                errors.append(f"funnel_not_collection_eligible:{run_id}")
            if (
                not str(row["condition_id"] or "").strip()
                or not str(row["event_id"] or "").strip()
                or not str(row["token_id"] or "").strip()
            ):
                errors.append(f"funnel_identity_invalid:{run_id}:{row_id}")
            probability = _finite(row["snapshot_probability"])
            bid = _finite(row["snapshot_best_bid"])
            ask = _finite(row["snapshot_best_ask"])
            spread = _finite(row["snapshot_spread"])
            liquidity = _finite(row["snapshot_liquidity"])
            volume = _finite(row["snapshot_volume_24h"])
            open_notional = _finite(row["open_notional_usdc"])
            scan_at = _parse_timestamp(row["scan_evaluated_at"])
            market_end = _parse_timestamp(row["market_end_date"])
            if (
                probability is None
                or bid is None
                or ask is None
                or spread is None
                or not 0.20 <= probability <= 0.80
                or not 0 < bid <= ask < 1
                or spread < 0
                or not _same_number(spread, ask - bid, tolerance=1e-6)
                or liquidity is None
                or liquidity < 20_000
                or volume is None
                or volume < 10_000
                or scan_at is None
                or market_end is None
                or market_end - scan_at < timedelta(hours=6)
            ):
                errors.append(f"funnel_snapshot_invalid:{run_id}:{row_id}")
            if (
                not isinstance(row["position_count"], int)
                or row["position_count"] < 0
                or open_notional is None
                or open_notional < 0
                or row["drawdown_tripped"] not in (0, 1)
            ):
                errors.append(f"funnel_risk_evidence_invalid:{run_id}:{row_id}")
            if not str(row["cooldown_reason"] or "").strip():
                errors.append(f"funnel_cooldown_reason_missing:{run_id}:{row_id}")
            if row["fresh_attempted"] not in (0, 1):
                errors.append(f"funnel_fresh_attempt_flag_invalid:{run_id}:{row_id}")
            if row["fresh_gate_passed"] not in (None, 0, 1):
                errors.append(f"funnel_fresh_gate_flag_invalid:{run_id}:{row_id}")
            if row["execution_selected"] not in (0, 1):
                errors.append(f"funnel_execution_flag_invalid:{run_id}:{row_id}")
            attempt_order = row["fresh_attempt_order"]
            if attempt_order is not None and (
                not isinstance(attempt_order, int)
                or attempt_order < 1
                or row["event_selected"] != 1
            ):
                errors.append(f"funnel_fresh_attempt_order_invalid:{run_id}:{row_id}")
            if row["fresh_attempted"] == 0 and row["fresh_observed_at"] is not None:
                errors.append(f"funnel_fresh_clock_unexpected:{run_id}:{row_id}")
            if row["fresh_attempted"] == 1 and (
                attempt_order is None
                or _parse_timestamp(row["fresh_observed_at"]) is None
            ):
                errors.append(f"funnel_fresh_clock_missing:{run_id}:{row_id}")
            if not str(row["fresh_fail_reason"] or "").strip():
                errors.append(f"funnel_fresh_reason_missing:{run_id}:{row_id}")
            if row["fresh_gate_passed"] == 1:
                fresh_bid = _finite(row["fresh_best_bid"])
                fresh_ask = _finite(row["fresh_best_ask"])
                fresh_spread = _finite(row["fresh_spread"])
                fresh_depth = _finite(row["fresh_depth_shares"])
                fresh_limit = _finite(row["fresh_depth_limit_price"])
                if (
                    row["fresh_attempted"] != 1
                    or fresh_bid is None
                    or fresh_ask is None
                    or fresh_spread is None
                    or fresh_depth is None
                    or fresh_limit is None
                    or not 0 < fresh_bid <= fresh_ask < 1
                    or not _same_number(
                        fresh_spread, fresh_ask - fresh_bid, tolerance=1e-6
                    )
                    or fresh_depth < 0
                    or not fresh_ask <= fresh_limit < 1
                ):
                    errors.append(f"funnel_fresh_book_invalid:{run_id}:{row_id}")
            if row["execution_selected"] != int(row["fresh_gate_passed"] == 1):
                errors.append(f"funnel_execution_gate_mismatch:{run_id}:{row_id}")
            if (row["trade_id"] is not None) != (row["execution_selected"] == 1):
                errors.append(f"funnel_trade_link_invalid:{run_id}:{row_id}")

        risk_states = {
            (
                row["position_count"],
                row["open_notional_usdc"],
                row["drawdown_tripped"],
            )
            for row in run_rows
        }
        if len(risk_states) != 1:
            errors.append(f"funnel_risk_snapshot_inconsistent:{run_id}")
        winners: list[sqlite3.Row] = []
        for event_id, siblings in by_event.items():
            raw_ranks = [row["event_rank"] for row in siblings]
            ranks = (
                sorted(raw_ranks)
                if all(
                    isinstance(value, int) and not isinstance(value, bool)
                    for value in raw_ranks
                )
                else []
            )
            selected = [row for row in siblings if row["event_selected"] == 1]
            sibling_counts = {row["event_sibling_count"] for row in siblings}
            if ranks != list(range(1, len(siblings) + 1)):
                errors.append(f"funnel_event_rank_invalid:{run_id}:{event_id}")
            if sibling_counts != {len(siblings)}:
                errors.append(f"funnel_sibling_count_invalid:{run_id}:{event_id}")
            expected_order = sorted(
                siblings,
                key=lambda row: (
                    -float(row["snapshot_liquidity"]),
                    str(row["condition_id"]),
                ),
            )
            if any(
                row["event_rank"] != rank
                for rank, row in enumerate(expected_order, start=1)
            ):
                errors.append(f"funnel_event_rank_order_invalid:{run_id}:{event_id}")
            if len(selected) != 1 or selected[0]["event_rank"] != 1:
                errors.append(f"funnel_event_winner_invalid:{run_id}:{event_id}")
            else:
                winners.append(selected[0])
        raw_global_ranks = [row["global_rank"] for row in winners]
        global_ranks = (
            sorted(raw_global_ranks)
            if all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in raw_global_ranks
            )
            else []
        )
        if global_ranks != list(range(1, len(winners) + 1)):
            errors.append(f"funnel_global_rank_invalid:{run_id}")
        expected_winners = sorted(
            winners,
            key=lambda row: (
                -float(row["snapshot_liquidity"]),
                str(row["condition_id"]),
            ),
        )
        if any(
            row["global_rank"] != rank
            for rank, row in enumerate(expected_winners, start=1)
        ):
            errors.append(f"funnel_global_rank_order_invalid:{run_id}")
        for row in run_rows:
            if row["event_selected"] != 1 and (
                row["global_rank"] is not None
                or row["cooldown_allowed"] != 0
                or row["raw_selected"] != 0
                or row["fresh_attempt_order"] is not None
                or row["fresh_attempted"] != 0
                or row["execution_selected"] != 0
            ):
                errors.append(f"funnel_sibling_state_invalid:{run_id}:{row['id']}")
        raw_selected = [row for row in run_rows if row["raw_selected"] == 1]
        if len(raw_selected) > 1:
            errors.append(f"funnel_multiple_raw_selected:{run_id}")
        for row in raw_selected:
            if (
                row["event_selected"] != 1
                or row["cooldown_allowed"] != 1
                or row["drawdown_tripped"] != 0
            ):
                errors.append(f"funnel_raw_selection_invalid:{run_id}")
        drawdown_states = {row["drawdown_tripped"] for row in run_rows}
        drawdown_tripped = drawdown_states == {1}
        eligible = [row for row in expected_winners if row["cooldown_allowed"] == 1]
        expected_raw = None if drawdown_tripped or not eligible else eligible[0]
        if (expected_raw is None and raw_selected) or (
            expected_raw is not None
            and (len(raw_selected) != 1 or raw_selected[0]["id"] != expected_raw["id"])
        ):
            errors.append(f"funnel_raw_selection_order_invalid:{run_id}")

        attempted = [
            row for row in expected_winners if row["fresh_attempt_order"] is not None
        ]
        attempted.sort(key=lambda row: row["fresh_attempt_order"])
        if [row["fresh_attempt_order"] for row in attempted] != list(
            range(1, len(attempted) + 1)
        ):
            errors.append(f"funnel_fresh_attempt_sequence_invalid:{run_id}")
        if [row["id"] for row in attempted] != [
            row["id"] for row in expected_winners[: len(attempted)]
        ]:
            errors.append(f"funnel_fresh_attempt_order_mismatch:{run_id}")
        executions = [row for row in expected_winners if row["execution_selected"] == 1]
        if len(executions) > 1:
            errors.append(f"funnel_multiple_executions:{run_id}")
        if executions:
            if not attempted or executions[0]["id"] != attempted[-1]["id"]:
                errors.append(f"funnel_execution_not_last_attempt:{run_id}")
        elif len(attempted) != len(expected_winners):
            errors.append(f"funnel_attempt_prefix_incomplete:{run_id}")
    return errors


def _raw_decision_reason(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    arm: str,
    expected_job: str,
    expected: Mapping[str, Any],
    source_cohort: Cohort,
    runs: Mapping[str, sqlite3.Row],
    configs: Mapping[str, sqlite3.Row],
    cadence_eligible_run_ids: frozenset[str],
) -> str | None:
    if row["arm"] != arm:
        return "decision_wrong_arm"
    if row["canonical_job"] != expected_job:
        return "decision_wrong_canonical_job"
    if row["collection_eligible"] != 1:
        return "decision_not_collection_eligible"
    if row["event_selected"] != 1 or row["event_rank"] != 1:
        return "decision_not_event_winner"
    if row["cooldown_allowed"] != 1:
        return "decision_cooldown_not_allowed"
    if row["drawdown_tripped"] != 0:
        return "decision_selected_after_drawdown"
    global_rank = row["global_rank"]
    if isinstance(global_rank, bool) or not isinstance(global_rank, int):
        return "decision_global_rank_missing"
    if global_rank < 1:
        return "decision_global_rank_invalid"
    entry_ask = _finite(row["snapshot_best_ask"])
    entry_bid = _finite(row["snapshot_best_bid"])
    if entry_ask is None or entry_bid is None or not 0 < entry_bid <= entry_ask < 1:
        return "decision_snapshot_quote_invalid"
    if not str(row["condition_id"] or "").strip():
        return "decision_condition_missing"
    if not str(row["event_id"] or "").strip():
        return "decision_event_missing"
    return _raw_lineage_reason(
        connection,
        row,
        expected=expected,
        source_cohort=source_cohort,
        runs=runs,
        configs=configs,
        cadence_eligible_run_ids=cadence_eligible_run_ids,
    )


def _classify_raw_followup(
    connection: sqlite3.Connection,
    decision: sqlite3.Row,
    *,
    source_cohort: Cohort,
    expected: Mapping[str, Any],
    runs: Mapping[str, sqlite3.Row],
    configs: Mapping[str, sqlite3.Row],
    cadence_eligible_run_ids: frozenset[str],
) -> tuple[float | None, datetime | None, str]:
    scan_at = _parse_timestamp(decision["scan_evaluated_at"])
    if scan_at is None:
        return None, None, "decision_scan_timestamp_invalid"
    expected_target = scan_at + timedelta(minutes=HOLD_MINUTES)
    expected_window_end = expected_target + timedelta(minutes=MAX_EXIT_DELAY_MINUTES)
    rows = connection.execute(
        """
        SELECT *
        FROM micro_cascade_followup_observations
        WHERE decision_id = ?
        ORDER BY observed_at, id
        """,
        (decision["id"],),
    ).fetchall()
    explicit_reasons: Counter[str] = Counter()
    successful_rows = 0
    for row in rows:
        observing_run_id = str(row["observing_run_id"] or "")
        observing_cohort, reason = _cohort_for_run(
            runs.get(observing_run_id),
            expected=expected,
            configs=configs,
        )
        # FAILED/RUNNING observations are ignored rather than converted to a
        # negative outcome or denominator row.
        if reason == "run_not_success":
            continue
        if reason is not None or observing_cohort is None:
            explicit_reasons[f"observing_{reason or 'cohort_missing'}"] += 1
            continue
        successful_rows += 1
        if observing_run_id not in cadence_eligible_run_ids:
            explicit_reasons["observing_run_not_cadence_eligible"] += 1
            continue
        if observing_cohort != source_cohort:
            explicit_reasons["observing_cross_cohort"] += 1
            continue
        if not _has_complete_sweep(connection, observing_run_id):
            explicit_reasons["observing_cursor_incomplete"] += 1
            continue
        observed_at = _parse_timestamp(row["observed_at"])
        target_at = _parse_timestamp(row["target_at"])
        window_end = _parse_timestamp(row["window_end"])
        if target_at != expected_target or window_end != expected_window_end:
            explicit_reasons["followup_window_contract_mismatch"] += 1
            continue
        if (
            observed_at is None
            or observed_at < expected_target
            or observed_at > expected_window_end
        ):
            explicit_reasons["followup_observed_outside_window"] += 1
            continue
        if row["condition_id"] != decision["condition_id"]:
            explicit_reasons["followup_condition_mismatch"] += 1
            continue
        exit_bid = _finite(row["best_bid"])
        if (
            row["market_seen"] == 1
            and row["source_available"] == 1
            and row["valid_quote"] == 1
            and exit_bid is not None
            and 0 < exit_bid < 1
        ):
            return exit_bid, observed_at, "quote_complete"
        source_reason = str(row["source_reason"] or "").strip()
        explicit_reasons[source_reason or "followup_explicit_reason_missing"] += 1
    if not rows:
        return None, None, "followup_observation_missing"
    if not successful_rows:
        return None, None, "followup_successful_observation_missing"
    if explicit_reasons:
        reason = sorted(explicit_reasons.items(), key=lambda item: (-item[1], item[0]))[
            0
        ][0]
        return None, None, f"followup_censored:{reason}"
    return None, None, "followup_quote_missing"


def _analyze_arm_v2(
    arm: str,
    db_path: Path,
    *,
    start: datetime,
    end: datetime,
    strict_audit: Mapping[str, Any] | Path | str | None,
) -> dict[str, Any]:
    expected = CANONICAL_ARMS[arm]
    connection = _open_read_only(db_path)
    try:
        _validate_schema(connection)
        _validate_v2_schema(connection)
        contract, contract_errors = _load_v2_contract(
            connection, arm=arm, start=start, end=end
        )
        audit = _validate_strict_audit(
            _load_strict_audit(strict_audit),
            database=db_path,
            start=start,
            end=end,
        )
        runs, configs = _load_runs_and_configs(connection)
        successful_runs = [
            row
            for row in runs.values()
            if row["status"] == "SUCCESS"
            and row["strategy_name"] == "golden-kiwi"
            and (
                (started := _parse_timestamp(row["started_at"])) is not None
                and start <= started < end
            )
        ]
        mapping_errors = list(contract_errors)
        wrong_jobs = sorted(
            {
                str(row["job_name"])
                for row in successful_runs
                if row["job_name"] != expected["job_name"]
            }
        )
        if wrong_jobs:
            mapping_errors.append(f"successful_runs_wrong_jobs:{','.join(wrong_jobs)}")
        canonical_runs = [
            row for row in successful_runs if row["job_name"] == expected["job_name"]
        ]
        valid_run_cohorts: dict[str, Cohort] = {}
        for run in canonical_runs:
            run_id = str(run["run_id"])
            sweep_reason = _sweep_contract_reason(connection, run_id)
            if sweep_reason is not None:
                mapping_errors.append(
                    f"sweep_contract:{run_id}:{sweep_reason}"
                )
            cohort, reason = _cohort_for_run(run, expected=expected, configs=configs)
            if reason is not None or cohort is None:
                mapping_errors.append(
                    f"run_contract:{run_id}:{reason or 'missing_cohort'}"
                )
            elif sweep_reason is None:
                valid_run_cohorts[run_id] = cohort
        cohort_keys = {cohort.key() for cohort in valid_run_cohorts.values()}
        if len(cohort_keys) != 1:
            mapping_errors.append(f"collection_cohort_count:{len(cohort_keys)}")

        offset = contract.get("expected_offset_minute")
        cadence_assessment = (
            _cadence_assessment(
                canonical_runs,
                start=start,
                end=end,
                cadence_minutes=EXPECTED_CADENCE_MINUTES,
                offset_minute=offset,
            )
            if isinstance(offset, int) and not isinstance(offset, bool)
            else CadenceAssessment(
                metrics={
                    "valid": False,
                    "expected_slots": 0,
                    "covered_slots": 0,
                    "coverage": 0.0,
                    "eligible_success_runs": 0,
                    "excluded_success_runs": len(canonical_runs),
                    "invalid_reasons": ["invalid_contract_offset"],
                    "error": "invalid_contract_offset",
                },
                eligible_run_ids=frozenset(),
            )
        )
        cadence = cadence_assessment.metrics
        cadence_eligible_run_ids = cadence_assessment.eligible_run_ids
        evidence_run_cohorts = {
            run_id: cohort
            for run_id, cohort in valid_run_cohorts.items()
            if run_id in cadence_eligible_run_ids
        }

        all_decisions = connection.execute(
            """
            SELECT *
            FROM micro_cascade_signal_decisions
            ORDER BY scan_evaluated_at, id
            """
        ).fetchall()
        funnel = Counter()
        censors: Counter[str] = Counter()
        valid_signals: list[ValidSignal] = []
        mature = 0
        ignored_failed_source = 0
        ignored_noncanonical_cadence_source = 0
        integrity_rows: list[sqlite3.Row] = []
        for row in all_decisions:
            scan_at = _parse_timestamp(row["scan_evaluated_at"])
            if scan_at is None or not start <= scan_at < end:
                continue
            source_run_id = str(row["run_id"] or "")
            source_run = runs.get(source_run_id)
            if source_run is None or source_run["status"] != "SUCCESS":
                ignored_failed_source += 1
                continue
            if source_run_id not in cadence_eligible_run_ids:
                ignored_noncanonical_cadence_source += 1
                continue
            source_cohort = evidence_run_cohorts.get(source_run_id)
            if source_cohort is None:
                censors["decision_source_run_contract_invalid"] += 1
                continue
            source_sweep_reason = _sweep_contract_reason(
                connection, source_run_id
            )
            if source_sweep_reason is not None:
                censors[
                    f"decision_source_sweep_contract:{source_sweep_reason}"
                ] += 1
                continue
            integrity_rows.append(row)
            lineage_reason = _raw_lineage_reason(
                connection,
                row,
                expected=expected,
                source_cohort=source_cohort,
                runs=runs,
                configs=configs,
                cadence_eligible_run_ids=cadence_eligible_run_ids,
            )
            if lineage_reason is not None:
                mapping_errors.append(f"funnel_row:{row['id']}:{lineage_reason}")
            funnel["raw_candidates"] += 1
            if row["event_selected"] == 1:
                funnel["event_selected"] += 1
            if row["fresh_attempted"] == 1:
                funnel["fresh_attempted"] += 1
            if row["fresh_gate_passed"] == 1:
                funnel["fresh_gate_passed"] += 1
            if row["execution_selected"] == 1:
                funnel["execution_selected"] += 1
            if row["raw_selected"] != 1:
                continue
            funnel["raw_selected"] += 1
            reason = _raw_decision_reason(
                connection,
                row,
                arm=arm,
                expected_job=expected["job_name"],
                expected=expected,
                source_cohort=source_cohort,
                runs=runs,
                configs=configs,
                cadence_eligible_run_ids=cadence_eligible_run_ids,
            )
            if reason is not None:
                censors[reason] += 1
                continue
            if scan_at + timedelta(minutes=HOLD_MINUTES + MAX_EXIT_DELAY_MINUTES) > end:
                censors["target_not_mature_by_window_end"] += 1
                continue
            mature += 1
            exit_bid, observed_at, reason = _classify_raw_followup(
                connection,
                row,
                source_cohort=source_cohort,
                expected=expected,
                runs=runs,
                configs=configs,
                cadence_eligible_run_ids=cadence_eligible_run_ids,
            )
            if exit_bid is None or observed_at is None:
                censors[reason] += 1
                continue
            entry_ask = float(row["snapshot_best_ask"])
            valid_signals.append(
                ValidSignal(
                    trade_id=int(row["id"]),
                    event_id=str(row["event_id"]),
                    cohort=source_cohort,
                    signal_timestamp=scan_at,
                    executable_return=exit_bid / entry_ask - 1.0,
                )
            )
        mapping_errors.extend(
            _funnel_integrity_errors(
                integrity_rows,
                arm=arm,
                expected_job=expected["job_name"],
            )
        )

        metrics = (
            _event_metrics(valid_signals)
            if len(cohort_keys) == 1
            else {
                **_event_metrics([]),
                "signals": len(valid_signals),
                "ci_method": "unavailable_multiple_cohorts_not_pooled",
            }
        )
        midpoint = start + (end - start) / 2
        early = _event_metrics(
            [signal for signal in valid_signals if signal.signal_timestamp < midpoint]
        )
        late = _event_metrics(
            [signal for signal in valid_signals if signal.signal_timestamp >= midpoint]
        )
        recorded = analyze_arm(arm, db_path, start=start, end=end)
        return {
            "arm": arm,
            "canonical_job": expected["job_name"],
            "database": str(db_path.expanduser().resolve()),
            "experiment_contract": contract,
            "strict_audit": audit,
            "cadence": cadence,
            "contract_valid": not mapping_errors,
            "mapping_errors": sorted(set(mapping_errors)),
            "cohort_count": len(cohort_keys),
            "cohorts": [Cohort(*key).as_dict() for key in sorted(cohort_keys)],
            "signal_funnel": dict(sorted(funnel.items())),
            "ignored_failed_source_decisions": ignored_failed_source,
            "ignored_noncanonical_cadence_source_decisions": (
                ignored_noncanonical_cadence_source
            ),
            "mature_raw_selected_signals": mature,
            "quote_complete_signals": len(valid_signals),
            "target_quote_coverage": (len(valid_signals) / mature if mature else 0.0),
            "coverage_denominator": (
                "SUCCESS-source, cursor-complete, contract-valid raw_selected "
                "decisions whose +75m window closes by experiment end"
            ),
            "censor_reasons": dict(sorted(censors.items())),
            "early_half": early,
            "late_half": late,
            **metrics,
            "recorded_trade_subset": {
                "diagnostic_only": True,
                "quote_complete_signals": recorded["quote_complete_signals"],
                "unique_events": recorded["unique_events"],
                "event_equal_return": recorded["event_equal_return"],
                "event_equal_lower_ci_98_75": recorded["event_equal_lower_ci_98_75"],
                "must_not_replace_raw_denominator": True,
            },
        }
    finally:
        connection.rollback()
        connection.close()


def _positive(value: Any) -> bool:
    number = _finite(value)
    return number is not None and number > 0


def _analyze_experiment_v2(
    databases: Mapping[str, Path],
    *,
    start: datetime,
    end: datetime,
    strict_audits: Mapping[str, Mapping[str, Any] | Path | str] | None,
) -> dict[str, Any]:
    normalized_audits = {
        arm.upper(): value for arm, value in (strict_audits or {}).items()
    }
    arms = {
        arm: _analyze_arm_v2(
            arm,
            databases[arm],
            start=start,
            end=end,
            strict_audit=normalized_audits.get(arm),
        )
        for arm in CANONICAL_ARMS
    }
    primary = arms["B"]
    source_digests = {
        cohort["strategy_source_digest"]
        for result in arms.values()
        for cohort in result["cohorts"]
    }
    shared_source = len(source_digests) == 1 and all(
        result["cohort_count"] == 1 for result in arms.values()
    )
    contract_evidence_complete = (
        all(
            result["contract_valid"]
            and result["strict_audit"]["valid"]
            and result["cadence"]["valid"]
            for result in arms.values()
        )
        and shared_source
    )
    checks = {
        "canonical_mapping_and_immutable_contract": _check(
            {
                arm: {
                    "contract_valid": result["contract_valid"],
                    "mapping_errors": result["mapping_errors"],
                }
                for arm, result in arms.items()
            },
            all(result["contract_valid"] for result in arms.values()),
            (
                "canonical arm/job, preregistration hash, analyzer/schema, "
                "shared UTC window and one immutable cohort per arm"
            ),
        ),
        "strict_audits": _check(
            {arm: result["strict_audit"] for arm, result in arms.items()},
            all(result["strict_audit"]["valid"] for result in arms.values()),
            ("one exact-window retro-audit JSON per DB with CRITICAL=0 and HIGH=0"),
        ),
        "cadence_coverage": _check(
            {arm: result["cadence"]["coverage"] for arm, result in arms.items()},
            all(result["cadence"]["coverage"] >= 0.90 for result in arms.values()),
            "each arm covers at least 90% of predeclared 5-minute UTC slots",
        ),
        "shared_strategy_source_digest": _check(
            sorted(source_digests),
            shared_source,
            "all four arms have one cohort and one shared strategy source digest",
        ),
        "raw_counterfactual_signals": _check(
            primary["quote_complete_signals"],
            primary["quote_complete_signals"] >= 50,
            "primary B quote-complete raw-selected signals >= 50",
        ),
        "raw_counterfactual_events": _check(
            primary["unique_events"],
            (primary["unique_events"] or 0) >= 30,
            "primary B quote-complete unique event clusters >= 30",
        ),
        "raw_target_quote_coverage": _check(
            primary["target_quote_coverage"],
            primary["target_quote_coverage"] >= 0.90,
            "primary B mature raw-selected follow-up coverage >= 90%",
        ),
        "raw_positive_pre_fee_edge": _check(
            primary["event_equal_return"],
            _positive(primary["event_equal_return"]),
            "primary B event-equal top-of-book return > 0",
        ),
        "raw_lower_ci_positive": _check(
            primary["event_equal_lower_ci_98_75"],
            _positive(primary["event_equal_lower_ci_98_75"]),
            "primary B event-cluster bootstrap 98.75% lower CI > 0",
        ),
        "raw_cost_stressed_lower_ci_positive": _check(
            primary["cost_stressed_lower_ci_98_75"],
            _positive(primary["cost_stressed_lower_ci_98_75"]),
            "primary B 10.4bps-stressed 98.75% lower CI > 0",
        ),
        "raw_early_half_positive": _check(
            primary["early_half"]["event_equal_return"],
            _positive(primary["early_half"]["event_equal_return"]),
            "primary B early-half event-equal edge > 0",
        ),
        "raw_late_half_positive": _check(
            primary["late_half"]["event_equal_return"],
            _positive(primary["late_half"]["event_equal_return"]),
            "primary B late-half event-equal edge > 0",
        ),
    }
    metric_checks = [
        name
        for name in checks
        if name
        not in {
            "canonical_mapping_and_immutable_contract",
            "strict_audits",
            "cadence_coverage",
            "shared_strategy_source_digest",
        }
    ]
    passed = contract_evidence_complete and all(
        checks[name]["passed"] for name in metric_checks
    )
    if passed:
        verdict = "ELIGIBLE_FOR_SHADOW_EXECUTION_REVIEW"
    elif not contract_evidence_complete:
        verdict = "NOT_EVALUABLE_FAIL_CLOSED"
    else:
        verdict = "FAIL_NO_SHADOW_REVIEW"
    return {
        "schema_version": ANALYZER_VERSION,
        "analyzer_version": ANALYZER_VERSION,
        "strategy": "golden-kiwi",
        "experiment": "Micro-Cascade frozen A/B/C/D",
        "window": {
            "start_inclusive": _iso_z(start),
            "end_exclusive": _iso_z(end),
            "days": WINDOW_DAYS,
        },
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "primary_metric": (
            "event-equal mean(earliest valid +60..75m raw Gamma best_bid / "
            "raw-selected signal snapshot best_ask - 1)"
        ),
        "primary_metric_status": "RECONSTRUCTED_FROM_APPEND_ONLY_RAW_EVIDENCE",
        "missing_evidence_policy": (
            "explicit censor reason; never forward-fill, impute, or assign zero"
        ),
        "arms": arms,
        "recorded_trade_subset_diagnostics": {
            arm: result["recorded_trade_subset"] for arm, result in arms.items()
        },
        "experiment_contract": {
            "promotion_evidence_complete": contract_evidence_complete,
            "shared_strategy_source_digest": shared_source,
            "strategy_source_digests": sorted(source_digests),
        },
        "primary_b_gate": {
            "passed": passed,
            "verdict": verdict,
            "checks": checks,
            "note": (
                "Passing permits only a separately approved shadow-execution "
                "review. Golden Kiwi remains source-level simulation-only."
            ),
        },
    }


def analyze_experiment(
    databases: Mapping[str, Path],
    *,
    start: datetime,
    end: datetime,
    strict_audits: Mapping[str, Mapping[str, Any] | Path | str] | None = None,
) -> dict[str, Any]:
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if end - start != timedelta(days=WINDOW_DAYS):
        raise AnalysisContractError(
            "analysis window must be exactly 30 days and half-open [start, end)"
        )
    normalized = {arm.upper(): Path(path) for arm, path in databases.items()}
    if set(normalized) != set(CANONICAL_ARMS):
        raise AnalysisContractError(
            "exactly one database for each canonical arm A, B, C, D is required"
        )
    v2_presence = {
        arm: _database_has_v2_evidence(path) for arm, path in normalized.items()
    }
    if all(v2_presence.values()):
        return _analyze_experiment_v2(
            normalized,
            start=start,
            end=end,
            strict_audits=strict_audits,
        )
    if any(v2_presence.values()):
        raise AnalysisContractError(
            "mixed legacy/v2 evidence is not comparable: "
            + json.dumps(v2_presence, sort_keys=True)
        )
    return _analyze_experiment_legacy(normalized, start=start, end=end)


def _parse_db_specs(
    values: Iterable[str] | None, *, project_root: Path
) -> dict[str, Path]:
    if not values:
        return {
            arm: project_root / "data" / spec["job_name"] / "trades_sim.db"
            for arm, spec in CANONICAL_ARMS.items()
        }
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise AnalysisContractError(f"--db must use ARM=PATH syntax: {value!r}")
        arm, raw_path = value.split("=", 1)
        normalized_arm = arm.strip().upper()
        if normalized_arm not in CANONICAL_ARMS:
            raise AnalysisContractError(f"unknown --db arm: {arm!r}")
        if normalized_arm in result:
            raise AnalysisContractError(
                f"duplicate --db mapping for arm {normalized_arm}"
            )
        result[normalized_arm] = Path(raw_path).expanduser()
    return result


def _parse_arm_path_specs(
    values: Iterable[str] | None,
    *,
    option_name: str,
) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values or []:
        if "=" not in value:
            raise AnalysisContractError(
                f"{option_name} must use ARM=PATH syntax: {value!r}"
            )
        arm, raw_path = value.split("=", 1)
        normalized_arm = arm.strip().upper()
        if normalized_arm not in CANONICAL_ARMS:
            raise AnalysisContractError(f"unknown {option_name} arm: {arm!r}")
        if normalized_arm in result:
            raise AnalysisContractError(
                f"duplicate {option_name} mapping for arm {normalized_arm}"
            )
        result[normalized_arm] = Path(raw_path).expanduser()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the frozen Golden Kiwi 30-day A/B/C/D experiment "
            "from read-only SQLite evidence."
        )
    )
    parser.add_argument(
        "--db",
        action="append",
        metavar="ARM=PATH",
        help=(
            "Override a canonical DB path; repeat exactly for A, B, C and D. "
            "When omitted, data/<canonical-job>/trades_sim.db is used."
        ),
    )
    parser.add_argument(
        "--start",
        required=True,
        help="UTC-inclusive ISO-8601 experiment start",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="UTC-exclusive ISO-8601 experiment end (exactly 30 days later)",
    )
    parser.add_argument(
        "--strict-audit",
        action="append",
        metavar="ARM=PATH",
        help=(
            "Exact-window polybot-retro audit JSON; repeat for A, B, C and D. "
            "Omission makes v2 promotion evidence NOT_EVALUABLE."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output path; stdout is always emitted",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        start, end = _parse_timestamp(args.start), _parse_timestamp(args.end)
        if start is None or end is None:
            raise AnalysisContractError("--start and --end must be ISO timestamps")
        project_root = Path(__file__).resolve().parents[1]
        databases = _parse_db_specs(args.db, project_root=project_root)
        strict_audits = _parse_arm_path_specs(
            args.strict_audit,
            option_name="--strict-audit",
        )
        result = analyze_experiment(
            databases,
            start=start,
            end=end,
            strict_audits=strict_audits,
        )
    except (AnalysisContractError, sqlite3.Error) as error:
        print(f"analysis contract error: {error}", file=sys.stderr)
        return 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output is not None:
        args.output.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        args.output.expanduser().resolve().write_text(f"{rendered}\n", encoding="utf-8")
    gate = result.get("primary_b_gate")
    return 0 if isinstance(gate, Mapping) and gate.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
