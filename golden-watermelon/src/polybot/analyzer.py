"""Read-only collection-health and cadence-paired analysis."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import sqlite3
import statistics
from typing import Any, Iterable


VALIDATION_START = datetime(2026, 8, 29, 15, 30, tzinfo=timezone.utc)


def _utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = int(math.floor(index))
    upper = int(math.ceil(index))
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _wilson(wins: int, total: int, z: float = 1.96) -> list[float] | None:
    if total == 0:
        return None
    probability = wins / total
    denominator = 1 + z * z / total
    center = (probability + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            (probability * (1 - probability) + z * z / (4 * total)) / total
        )
        / denominator
    )
    return [(center - margin) * 100, (center + margin) * 100]


def _bootstrap_mean_ci(
    values: list[float], *, seed: int, samples: int = 10_000
) -> list[float] | None:
    if not values:
        return None
    if len(values) == 1:
        value = values[0] * 100
        return [value, value]
    generator = random.Random(seed)
    count = len(values)
    means = sorted(
        statistics.fmean(values[generator.randrange(count)] for _ in range(count))
        for _ in range(samples)
    )
    return [
        means[int(samples * 0.025)] * 100,
        means[min(samples - 1, int(samples * 0.975))] * 100,
    ]


def _entry_total_cost(row: sqlite3.Row) -> float:
    shares = float(row["entry_shares"])
    price = float(row["entry_vwap"])
    fee_rate = float(row["fee_rate"])
    fee = shares * fee_rate * price * (1 - price)
    return float(row["entry_cost"]) + fee


def _policy_roi(row: sqlite3.Row) -> tuple[float, str] | None:
    shares = float(row["entry_shares"])
    filled = min(shares, float(row["stop_filled_shares"] or 0))
    proceeds = float(row["stop_net_proceeds"] or 0)
    remaining = max(0.0, shares - filled)
    if remaining <= 1e-7:
        settlement = proceeds
        exit_kind = "STOP_FULL"
    elif row["winner_index"] is not None:
        won = int(row["outcome_index"]) == int(row["winner_index"])
        settlement = proceeds + (remaining if won else 0.0)
        exit_kind = (
            "RESOLUTION_AFTER_PARTIAL_STOP" if filled > 0 else "RESOLUTION"
        )
    else:
        return None
    return settlement / _entry_total_cost(row) - 1, exit_kind


def _cadence_summary(times: list[datetime], minutes: int) -> dict[str, Any]:
    ordered = sorted(set(times))
    if not ordered:
        return {
            "successful_runs": 0,
            "first_success_at": None,
            "last_success_at": None,
            "expected_slots_between_first_and_last": 0,
            "coverage_pct": None,
            "gap_seconds_p50": None,
            "gap_seconds_p95": None,
            "max_gap_seconds": None,
            "gaps_over_1_5x_cadence": 0,
        }
    gaps = [
        (current - prior).total_seconds()
        for prior, current in zip(ordered, ordered[1:])
    ]
    expected = (
        math.floor((ordered[-1] - ordered[0]).total_seconds() / (minutes * 60))
        + 1
    )
    return {
        "successful_runs": len(ordered),
        "first_success_at": ordered[0].isoformat().replace("+00:00", "Z"),
        "last_success_at": ordered[-1].isoformat().replace("+00:00", "Z"),
        "expected_slots_between_first_and_last": expected,
        "coverage_pct": len(ordered) / expected * 100 if expected else None,
        "gap_seconds_p50": _percentile(gaps, 0.50),
        "gap_seconds_p95": _percentile(gaps, 0.95),
        "max_gap_seconds": max(gaps) if gaps else None,
        "gaps_over_1_5x_cadence": sum(
            gap > minutes * 60 * 1.5 for gap in gaps
        ),
    }


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator * 100 if denominator else None


def analyze_database(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        contract = connection.execute(
            "SELECT data_contract FROM schema_metadata"
        ).fetchone()
        config_row = connection.execute(
            """
            SELECT config_hash,strategy_source_digest,job_name,config_json,first_seen_at
            FROM research_config_versions
            ORDER BY first_seen_at DESC LIMIT 1
            """
        ).fetchone()
        if config_row is None:
            raise ValueError("database has no research_config_versions")
        config_json = json.loads(str(config_row["config_json"]))
        trading = config_json["trading"]
        cadence_minutes = int(trading["cadence_minutes"])
        cadence_arm = str(trading["cadence_arm"])

        run_rows = connection.execute(
            "SELECT event_type,COUNT(*) AS count FROM research_run_events "
            "GROUP BY event_type"
        ).fetchall()
        success_times = [
            _utc(str(row[0]))
            for row in connection.execute(
                "SELECT observed_at FROM research_run_events "
                "WHERE event_type='SUCCEEDED' ORDER BY observed_at"
            )
        ]
        sweep = connection.execute(
            """
            SELECT COUNT(*) AS sweeps,
                   COALESCE(SUM(cursor_complete),0) AS cursor_complete,
                   COALESCE(SUM(event_count),0) AS events,
                   COALESCE(SUM(market_count),0) AS markets,
                   COALESCE(SUM(eligible_market_count),0) AS eligible_markets,
                   COALESCE(SUM(eligible_outcome_count),0) AS eligible_outcomes,
                   COALESCE(MAX(page_count),0) AS max_pages
            FROM market_sweeps
            """
        ).fetchone()
        eligible_observations = int(
            connection.execute(
                "SELECT COUNT(*) FROM outcome_observations "
                "WHERE entry_eligible=1"
            ).fetchone()[0]
        )
        observed_books = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM outcome_observations o
                JOIN orderbook_token_attempts a
                  ON a.run_id=o.run_id AND a.token_id=o.token_id
                WHERE o.entry_eligible=1 AND a.status='OBSERVED'
                """
            ).fetchone()[0]
        )
        full_depth_quotes = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT run_id,token_id
                    FROM signal_decisions
                    GROUP BY run_id,token_id
                    HAVING MAX(CASE WHEN entry_vwap IS NOT NULL THEN 1 ELSE 0 END)=1
                )
                """
            ).fetchone()[0]
        )
        class_rows = connection.execute(
            """
            SELECT match_winner_class,eligible,COUNT(*) AS count
            FROM market_observations
            GROUP BY match_winner_class,eligible
            ORDER BY match_winner_class,eligible
            """
        ).fetchall()
        exclusion_counter: Counter[str] = Counter()
        for row in connection.execute(
            "SELECT exclusion_reason FROM market_observations WHERE eligible=0"
        ):
            exclusion_counter.update(str(row[0]).split(";"))
        issues = connection.execute(
            "SELECT severity,issue_type,COUNT(*) AS count "
            "FROM data_quality_issues GROUP BY severity,issue_type"
        ).fetchall()
        check_rows = connection.execute(
            "SELECT check_type,result,COUNT(*) AS count,MAX(completed_at) AS latest,"
            "MAX(elapsed_ms) AS max_elapsed FROM database_checks "
            "GROUP BY check_type,result ORDER BY check_type,result"
        ).fetchall()
        storage_rows = connection.execute(
            "SELECT observed_at,db_bytes,free_bytes,total_bytes,used_ratio "
            "FROM storage_metrics ORDER BY observed_at"
        ).fetchall()
        episode_rows = connection.execute(
            """
            SELECT e.*,r.winner_index,r.observed_at AS resolved_at
            FROM hypothetical_episodes e
            LEFT JOIN resolution_observations r USING(condition_id)
            ORDER BY e.entered_at,e.episode_id
            """
        ).fetchall()
        policy_rows = connection.execute(
            """
            SELECT
                p.policy_id,p.policy_key,p.stop_price,
                e.episode_id,e.event_id,e.condition_id,e.token_id,e.outcome_index,
                e.threshold,e.entered_at,e.entry_vwap,e.entry_shares,e.entry_cost,
                e.fee_rate,e.cadence_arm,e.entry_provenance,
                r.winner_index,
                COUNT(a.attempt_id) AS stop_attempt_count,
                COALESCE(SUM(a.filled_shares),0) AS stop_filled_shares,
                COALESCE(SUM(a.net_proceeds),0) AS stop_net_proceeds,
                SUM(CASE WHEN a.status='PARTIAL_FILL' THEN 1 ELSE 0 END)
                    AS partial_attempt_count,
                SUM(CASE WHEN a.status='NO_BID_DEPTH' THEN 1 ELSE 0 END)
                    AS no_bid_attempt_count,
                x.exit_vwap AS completed_stop_vwap,
                x.gap_from_stop AS completed_gap_from_stop,
                x.attempt_count AS completed_attempt_count
            FROM counterfactual_exit_policies p
            JOIN hypothetical_episodes e USING(episode_id)
            LEFT JOIN stop_execution_attempts a USING(policy_id)
            LEFT JOIN counterfactual_stop_exits x USING(policy_id)
            LEFT JOIN resolution_observations r ON r.condition_id=e.condition_id
            GROUP BY p.policy_id
            ORDER BY e.entered_at,p.policy_key
            """
        ).fetchall()
    finally:
        connection.close()

    storage: dict[str, Any]
    if storage_rows:
        first = storage_rows[0]
        last = storage_rows[-1]
        elapsed_days = max(
            (_utc(str(last["observed_at"])) - _utc(str(first["observed_at"]))).total_seconds()
            / 86400,
            0,
        )
        delta = int(last["db_bytes"]) - int(first["db_bytes"])
        storage = {
            "samples": len(storage_rows),
            "first_observed_at": first["observed_at"],
            "last_observed_at": last["observed_at"],
            "first_db_bytes": int(first["db_bytes"]),
            "last_db_bytes": int(last["db_bytes"]),
            "growth_bytes": delta,
            "growth_bytes_per_day": delta / elapsed_days if elapsed_days > 0 else None,
            "latest_free_bytes": int(last["free_bytes"]),
            "latest_used_ratio": float(last["used_ratio"]),
        }
    else:
        storage = {"samples": 0}

    result: dict[str, Any] = {
        "analyzer_contract": "sports-inplay-match-winner-analyzer-v1",
        "db": str(path.resolve()),
        "quick_check": quick_check,
        "data_contract": str(contract[0]) if contract else None,
        "job_name": str(config_row["job_name"]),
        "cadence_arm": cadence_arm,
        "cadence_minutes": cadence_minutes,
        "config_hash": str(config_row["config_hash"]),
        "strategy_source_digest": str(config_row["strategy_source_digest"]),
        "config_first_seen_at": str(config_row["first_seen_at"]),
        "run_events": {str(row["event_type"]): int(row["count"]) for row in run_rows},
        "cadence": _cadence_summary(success_times, cadence_minutes),
        "collection": {
            "sweeps": int(sweep["sweeps"]),
            "cursor_complete": int(sweep["cursor_complete"]),
            "cursor_complete_pct": _safe_ratio(
                int(sweep["cursor_complete"]), int(sweep["sweeps"])
            ),
            "events": int(sweep["events"]),
            "markets": int(sweep["markets"]),
            "eligible_markets": int(sweep["eligible_markets"]),
            "eligible_outcomes": int(sweep["eligible_outcomes"]),
            "max_pages": int(sweep["max_pages"]),
        },
        "book_coverage": {
            "eligible_outcome_observations": eligible_observations,
            "observed_books": observed_books,
            "observed_book_pct": _safe_ratio(
                observed_books, eligible_observations
            ),
            "full_5_usdc_depth_quotes": full_depth_quotes,
            "full_5_usdc_depth_pct": _safe_ratio(
                full_depth_quotes, eligible_observations
            ),
        },
        "classification": {
            "rows": [
                {
                    "match_winner_class": str(row["match_winner_class"]),
                    "eligible": bool(row["eligible"]),
                    "count": int(row["count"]),
                }
                for row in class_rows
            ],
            "exclusions": dict(sorted(exclusion_counter.items())),
        },
        "issues": [
            {
                "severity": str(row["severity"]),
                "type": str(row["issue_type"]),
                "count": int(row["count"]),
            }
            for row in issues
        ],
        "database_checks": [
            {
                "check_type": str(row["check_type"]),
                "result": str(row["result"]),
                "count": int(row["count"]),
                "latest_completed_at": row["latest"],
                "max_elapsed_ms": row["max_elapsed"],
            }
            for row in check_rows
        ],
        "storage": storage,
        "entry_thresholds": {},
        "stop_policy_comparison": {},
        "interpretation": "DISPLAYED_BOOK_COUNTERFACTUAL_ONLY",
        "actual_fill_or_realized_pnl": False,
    }

    thresholds = sorted({float(row["threshold"]) for row in episode_rows})
    for threshold in thresholds:
        selected = [
            row for row in episode_rows if float(row["threshold"]) == threshold
        ]
        partitions: dict[str, Any] = {}
        for partition_index, (label, subset) in enumerate(
            (
                ("all", selected),
                (
                    "calibration",
                    [
                        row
                        for row in selected
                        if _utc(str(row["entered_at"])) < VALIDATION_START
                    ],
                ),
                (
                    "confirmation",
                    [
                        row
                        for row in selected
                        if _utc(str(row["entered_at"])) >= VALIDATION_START
                    ],
                ),
            )
        ):
            resolved = [row for row in subset if row["winner_index"] is not None]
            by_event: defaultdict[str, list[float]] = defaultdict(list)
            wins = 0
            for row in resolved:
                won = int(row["outcome_index"]) == int(row["winner_index"])
                wins += int(won)
                settlement = float(row["entry_shares"]) if won else 0.0
                roi = settlement / _entry_total_cost(row) - 1
                by_event[str(row["event_id"])].append(roi)
            event_roi = [statistics.fmean(values) for values in by_event.values()]
            seed = 20_260_823 + int(round(threshold * 100)) * 10 + partition_index
            partitions[label] = {
                "episodes": len(subset),
                "events": len({str(row["event_id"]) for row in subset}),
                "resolved": len(resolved),
                "resolution_coverage_pct": _safe_ratio(len(resolved), len(subset)),
                "wins": wins,
                "win_rate_pct": _safe_ratio(wins, len(resolved)),
                "win_rate_wilson_95ci_pct": _wilson(wins, len(resolved)),
                "event_equal_fee_net_roi_pct": (
                    statistics.fmean(event_roi) * 100 if event_roi else None
                ),
                "event_equal_fee_net_roi_bootstrap_95ci_pct": _bootstrap_mean_ci(
                    event_roi, seed=seed
                ),
                "entry_provenance": dict(
                    Counter(str(row["entry_provenance"]) for row in subset)
                ),
            }
        result["entry_thresholds"][f"{threshold:.2f}"] = partitions

    for threshold in sorted({float(row["threshold"]) for row in policy_rows}):
        threshold_rows = [
            row for row in policy_rows if float(row["threshold"]) == threshold
        ]
        policy_result: dict[str, Any] = {}
        for policy_key in sorted(
            {str(row["policy_key"]) for row in threshold_rows}
        ):
            selected = [
                row
                for row in threshold_rows
                if str(row["policy_key"]) == policy_key
            ]
            evaluated: list[tuple[sqlite3.Row, float, str]] = []
            for row in selected:
                outcome = _policy_roi(row)
                if outcome is not None:
                    evaluated.append((row, outcome[0], outcome[1]))
            by_event: defaultdict[str, list[float]] = defaultdict(list)
            exit_kinds: Counter[str] = Counter()
            for row, roi, kind in evaluated:
                by_event[str(row["event_id"])].append(roi)
                exit_kinds[kind] += 1
            event_roi = [statistics.fmean(values) for values in by_event.values()]
            gaps = [
                float(row["completed_gap_from_stop"])
                for row in selected
                if row["completed_gap_from_stop"] is not None
            ]
            policy_result[policy_key] = {
                "stop_price": selected[0]["stop_price"] if selected else None,
                "episodes": len(selected),
                "evaluable": len(evaluated),
                "events": len(by_event),
                "evaluable_coverage_pct": _safe_ratio(
                    len(evaluated), len(selected)
                ),
                "triggered_policies": sum(
                    int(row["stop_attempt_count"] or 0) > 0 for row in selected
                ),
                "completed_stop_exits": sum(
                    row["completed_stop_vwap"] is not None for row in selected
                ),
                "partial_attempts": sum(
                    int(row["partial_attempt_count"] or 0) for row in selected
                ),
                "no_bid_attempts": sum(
                    int(row["no_bid_attempt_count"] or 0) for row in selected
                ),
                "exit_kinds": dict(sorted(exit_kinds.items())),
                "event_equal_fee_net_roi_pct": (
                    statistics.fmean(event_roi) * 100 if event_roi else None
                ),
                "event_equal_fee_net_roi_bootstrap_95ci_pct": _bootstrap_mean_ci(
                    event_roi,
                    seed=20_260_823
                    + int(round(threshold * 100)) * 100
                    + sum(ord(character) for character in policy_key),
                ),
                "gap_below_stop_p50": _percentile(gaps, 0.50),
                "gap_below_stop_p95": _percentile(gaps, 0.95),
            }
        result["stop_policy_comparison"][f"{threshold:.2f}"] = policy_result
    return result


def _episode_index(path: Path) -> dict[tuple[str, str, float], sqlite3.Row]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT condition_id,token_id,threshold,entered_at,entry_vwap "
            "FROM hypothetical_episodes"
        ).fetchall()
    finally:
        connection.close()
    return {
        (str(row["condition_id"]), str(row["token_id"]), float(row["threshold"])): row
        for row in rows
    }


def analyze_databases(paths: Iterable[Path]) -> dict[str, Any]:
    resolved_paths = [Path(path).resolve() for path in paths]
    databases = [analyze_database(path) for path in resolved_paths]
    result: dict[str, Any] = {
        "analyzer_contract": "sports-inplay-match-winner-cadence-pair-v1",
        "databases": databases,
        "pairing": None,
        "interpretation": "CADENCE_PAIRED_DISPLAYED_BOOK_COUNTERFACTUAL_ONLY",
    }
    if len(resolved_paths) != 2:
        return result
    left_index = _episode_index(resolved_paths[0])
    right_index = _episode_index(resolved_paths[1])
    common = sorted(set(left_index) & set(right_index))
    time_deltas = [
        abs(
            (
                _utc(str(left_index[key]["entered_at"]))
                - _utc(str(right_index[key]["entered_at"]))
            ).total_seconds()
        )
        for key in common
    ]
    price_deltas = [
        abs(
            float(left_index[key]["entry_vwap"])
            - float(right_index[key]["entry_vwap"])
        )
        for key in common
    ]
    result["pairing"] = {
        "left_job": databases[0]["job_name"],
        "right_job": databases[1]["job_name"],
        "left_episode_keys": len(left_index),
        "right_episode_keys": len(right_index),
        "matched_episode_keys": len(common),
        "matched_pct_of_smaller_arm": _safe_ratio(
            len(common), min(len(left_index), len(right_index))
        ),
        "entry_time_delta_seconds_p50": _percentile(time_deltas, 0.50),
        "entry_time_delta_seconds_p95": _percentile(time_deltas, 0.95),
        "entry_vwap_absolute_delta_p50": _percentile(price_deltas, 0.50),
        "entry_vwap_absolute_delta_p95": _percentile(price_deltas, 0.95),
    }
    return result
