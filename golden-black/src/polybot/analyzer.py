"""Read-only paired-arm outcome analysis."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from pathlib import Path
import random
import sqlite3
import statistics
from typing import Any


def _utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _wilson(wins: int, total: int, z: float = 1.96) -> list[float] | None:
    if total == 0:
        return None
    p = wins / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [(center - margin) * 100, (center + margin) * 100]


def _trade_roi(entry: float, fee_rate: float, win: int, adverse: float = 0) -> float:
    price = min(entry + adverse, 0.999)
    cost_per_share = price + fee_rate * price * (1 - price)
    return win / cost_per_share - 1


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
        exit_kind = "RESOLUTION_AFTER_PARTIAL_STOP" if filled > 0 else "RESOLUTION"
    else:
        return None
    return settlement / _entry_total_cost(row) - 1, exit_kind


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
    lower = means[int(samples * 0.025)] * 100
    upper = means[min(samples - 1, int(samples * 0.975))] * 100
    return [lower, upper]


def analyze_database(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        rows = connection.execute(
            """
            SELECT e.*, r.winner_index, r.observed_at AS resolved_at
            FROM hypothetical_episodes e
            LEFT JOIN resolution_observations r USING(condition_id)
            ORDER BY e.entered_at, e.episode_id
            """
        ).fetchall()
        run_rows = connection.execute(
            "SELECT event_type,COUNT(*) FROM research_run_events GROUP BY event_type"
        ).fetchall()
        sweep = connection.execute(
            "SELECT COUNT(*),SUM(cursor_complete),SUM(event_count),SUM(market_count),SUM(eligible_market_count),MAX(page_count) FROM market_sweeps"
        ).fetchone()
        issues = connection.execute(
            "SELECT severity,issue_type,COUNT(*) FROM data_quality_issues GROUP BY severity,issue_type"
        ).fetchall()
        policy_rows = connection.execute(
            """
            SELECT
                p.policy_id,p.policy_key,p.stop_price,
                e.episode_id,e.event_id,e.condition_id,e.token_id,e.outcome_index,
                e.threshold,e.entered_at,e.entry_vwap,e.entry_shares,e.entry_cost,e.fee_rate,
                r.winner_index,
                COUNT(a.attempt_id) AS stop_attempt_count,
                COALESCE(SUM(a.filled_shares),0) AS stop_filled_shares,
                COALESCE(SUM(a.net_proceeds),0) AS stop_net_proceeds,
                SUM(CASE WHEN a.status='PARTIAL_FILL' THEN 1 ELSE 0 END) AS partial_attempt_count,
                SUM(CASE WHEN a.status='NO_BID_DEPTH' THEN 1 ELSE 0 END) AS no_bid_attempt_count,
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

    split = datetime(2026, 9, 5, tzinfo=timezone.utc)
    result: dict[str, Any] = {
        "analyzer_contract": "sports-resolution-paired-analyzer-v1",
        "db": str(path.resolve()),
        "quick_check": quick_check,
        "run_events": {str(row[0]): int(row[1]) for row in run_rows},
        "collection": {
            "sweeps": int(sweep[0] or 0), "cursor_complete": int(sweep[1] or 0),
            "events": int(sweep[2] or 0), "markets": int(sweep[3] or 0),
            "eligible_markets": int(sweep[4] or 0), "max_pages": int(sweep[5] or 0),
        },
        "issues": [{"severity": row[0], "type": row[1], "count": row[2]} for row in issues],
        "arms": {},
        "stop_policy_comparison": {},
        "interpretation": "SHADOW_REVIEW_ONLY",
    }
    thresholds = sorted({float(row["threshold"]) for row in rows})
    for threshold in thresholds:
        selected = [row for row in rows if float(row["threshold"]) == threshold]
        arm: dict[str, Any] = {}
        for partition_index, (label, subset) in enumerate((
            ("all", selected),
            ("train", [row for row in selected if _utc(row["entered_at"]) < split]),
            ("validation", [row for row in selected if _utc(row["entered_at"]) >= split]),
        )):
            resolved = [row for row in subset if row["winner_index"] is not None]
            trades = []
            for row in resolved:
                win = int(int(row["outcome_index"]) == int(row["winner_index"]))
                trades.append((row, win, _trade_roi(float(row["entry_vwap"]), float(row["fee_rate"]), win), _trade_roi(float(row["entry_vwap"]), float(row["fee_rate"]), win, 0.01)))
            by_event: defaultdict[str, list[tuple[int, float, float]]] = defaultdict(list)
            for row, win, roi, stressed in trades:
                by_event[str(row["event_id"])].append((win, roi, stressed))
            event_roi = [statistics.fmean(value[1] for value in values) for values in by_event.values()]
            event_stress = [statistics.fmean(value[2] for value in values) for values in by_event.values()]
            wins = sum(value[1] for value in trades)
            bootstrap_seed = 20_260_820 + int(round(threshold * 100)) * 10 + partition_index
            arm[label] = {
                "episodes": len(subset), "resolved": len(resolved), "events": len(by_event),
                "resolution_coverage_pct": len(resolved) / len(subset) * 100 if subset else None,
                "wins": wins, "win_rate_pct": wins / len(resolved) * 100 if resolved else None,
                "win_rate_wilson_95ci_pct": _wilson(wins, len(resolved)),
                "event_equal_fee_net_roi_pct": statistics.fmean(event_roi) * 100 if event_roi else None,
                "event_equal_fee_plus_1c_roi_pct": statistics.fmean(event_stress) * 100 if event_stress else None,
                "event_equal_fee_net_roi_bootstrap_95ci_pct": _bootstrap_mean_ci(
                    event_roi, seed=bootstrap_seed
                ),
                "event_equal_fee_plus_1c_roi_bootstrap_95ci_pct": _bootstrap_mean_ci(
                    event_stress, seed=bootstrap_seed + 1
                ),
            }
        result["arms"][f"{threshold:.2f}"] = arm

    policy_thresholds = sorted({float(row["threshold"]) for row in policy_rows})
    for threshold in policy_thresholds:
        threshold_rows = [
            row for row in policy_rows if float(row["threshold"]) == threshold
        ]
        policy_result: dict[str, Any] = {}
        for policy_key in sorted({str(row["policy_key"]) for row in threshold_rows}):
            selected = [row for row in threshold_rows if str(row["policy_key"]) == policy_key]
            partitions: dict[str, Any] = {}
            for partition_index, (label, subset) in enumerate((
                ("all", selected),
                ("train", [row for row in selected if _utc(row["entered_at"]) < split]),
                ("validation", [row for row in selected if _utc(row["entered_at"]) >= split]),
            )):
                evaluated: list[tuple[sqlite3.Row, float, str]] = []
                for row in subset:
                    outcome = _policy_roi(row)
                    if outcome is not None:
                        evaluated.append((row, outcome[0], outcome[1]))
                by_event: defaultdict[str, list[float]] = defaultdict(list)
                for row, roi, _ in evaluated:
                    by_event[str(row["event_id"])].append(roi)
                event_roi = [statistics.fmean(values) for values in by_event.values()]
                completed_gaps = [
                    float(row["completed_gap_from_stop"])
                    for row in subset
                    if row["completed_gap_from_stop"] is not None
                ]
                completed_vwaps = [
                    float(row["completed_stop_vwap"])
                    for row in subset
                    if row["completed_stop_vwap"] is not None
                ]
                exit_kinds: defaultdict[str, int] = defaultdict(int)
                for _, _, exit_kind in evaluated:
                    exit_kinds[exit_kind] += 1
                seed = (
                    20_260_820
                    + int(round(threshold * 100)) * 100
                    + sum(ord(character) for character in policy_key)
                    + partition_index
                )
                partitions[label] = {
                    "episodes": len(subset),
                    "evaluable": len(evaluated),
                    "events": len(by_event),
                    "evaluable_coverage_pct": len(evaluated) / len(subset) * 100 if subset else None,
                    "triggered_policies": sum(int(row["stop_attempt_count"] or 0) > 0 for row in subset),
                    "completed_stop_exits": sum(row["completed_stop_vwap"] is not None for row in subset),
                    "partial_attempts": sum(int(row["partial_attempt_count"] or 0) for row in subset),
                    "no_bid_attempts": sum(int(row["no_bid_attempt_count"] or 0) for row in subset),
                    "exit_kinds": dict(sorted(exit_kinds.items())),
                    "event_equal_fee_net_roi_pct": statistics.fmean(event_roi) * 100 if event_roi else None,
                    "event_equal_fee_net_roi_bootstrap_95ci_pct": _bootstrap_mean_ci(
                        event_roi, seed=seed
                    ),
                    "completed_stop_vwap_p50": _percentile(completed_vwaps, 0.50),
                    "completed_stop_vwap_p05": _percentile(completed_vwaps, 0.05),
                    "gap_below_stop_p50": _percentile(completed_gaps, 0.50),
                    "gap_below_stop_p95": _percentile(completed_gaps, 0.95),
                }
            policy_result[policy_key] = {
                "stop_price": selected[0]["stop_price"] if selected else None,
                **partitions,
            }
        result["stop_policy_comparison"][f"{threshold:.2f}"] = policy_result
    return result
