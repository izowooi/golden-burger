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
    finally:
        connection.close()

    split = datetime(2026, 9, 4, tzinfo=timezone.utc)
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
    return result
