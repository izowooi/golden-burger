#!/usr/bin/env python3
"""Fail-closed diagnostics for Blueberry Shadow 2x2 evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Sequence

try:  # project-root import in tests/build tooling
    from scripts.analyze_experiment import _db_utc_key, _display_utc, _window
except ImportError:  # direct ``python scripts/analyze_shadow.py`` execution
    from analyze_experiment import _db_utc_key, _display_utc, _window


COHORT_COLUMNS = {"config_hash", "strategy_source_digest", "mode", "job_name"}
EXPECTED_CELLS = {(0.02, 72.0), (0.02, 168.0), (0.05, 72.0), (0.05, 168.0)}


def analyze_shadow(
    database: Path,
    output_dir: Path,
    review_start: str,
    review_end: str,
) -> Path:
    start, end = _window(review_start, review_end)
    if not database.is_file():
        raise ValueError(f"database not found: {database}")
    uri = f"file:{database.resolve().as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.create_function("utc_key", 1, _db_utc_key, deterministic=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {"shadow_signals", "run_audits"}
        if missing := sorted(required - tables):
            raise ValueError(f"database missing shadow evidence tables: {missing}")
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(shadow_signals)")
        }
        issues: list[str] = []
        if not COHORT_COLUMNS.issubset(columns):
            issues.append("shadow_cohort_columns_missing")
            cohort_projection = (
                "NULL AS config_hash, NULL AS strategy_source_digest, "
                "NULL AS mode, NULL AS job_name"
            )
        else:
            cohort_projection = (
                "config_hash, strategy_source_digest, mode, job_name"
            )
        rows = connection.execute(
            f"""
            SELECT id, condition_id, min_surge, horizon_hours, status,
                   classification, hypothetical_gross_pnl,
                   {cohort_projection}
            FROM shadow_signals
            WHERE utc_key(first_observed_at) >= ?
              AND utc_key(first_observed_at) < ?
            ORDER BY id
            """,
            (start, end),
        ).fetchall()
        missing_identity = sum(
            any(row[key] in (None, "") for key in COHORT_COLUMNS) for row in rows
        )
        if missing_identity:
            issues.append("shadow_cohort_identity_missing")

        exact_groups: Counter[tuple] = Counter()
        condition_cells: dict[tuple, set[tuple[float, float]]] = {}
        for row in rows:
            cohort = tuple(row[key] for key in sorted(COHORT_COLUMNS))
            cell = (float(row["min_surge"]), float(row["horizon_hours"]))
            key = cohort + (str(row["condition_id"]),) + cell
            exact_groups[key] += 1
            condition_cells.setdefault(cohort + (str(row["condition_id"]),), set()).add(
                cell
            )
        duplicate_groups = sum(count > 1 for count in exact_groups.values())
        duplicate_rows = sum(count - 1 for count in exact_groups.values() if count > 1)
        invalid_grid_conditions = sum(
            cells != EXPECTED_CELLS for cells in condition_cells.values()
        )
        if duplicate_groups:
            issues.append("duplicate_shadow_treatment_rows")
        if invalid_grid_conditions:
            issues.append("incomplete_or_unexpected_shadow_grid")

        run_rows = connection.execute(
            """
            SELECT status, started_at, finished_at FROM run_audits
            WHERE strategy_name = 'golden-blueberry' AND mode = 'sim'
              AND utc_key(started_at) >= ? AND utc_key(started_at) < ?
            """,
            (start, end),
        ).fetchall()
        runtimes = []
        for row in run_rows:
            if row["finished_at"]:
                try:
                    began = datetime.fromisoformat(str(row["started_at"]).replace("Z", "+00:00"))
                    finished = datetime.fromisoformat(str(row["finished_at"]).replace("Z", "+00:00"))
                    seconds = (finished - began).total_seconds()
                    if seconds >= 0:
                        runtimes.append(seconds)
                except ValueError:
                    issues.append("run_runtime_timestamp_invalid")

        closed_summary = None
        if not issues:
            cells: dict[str, dict[str, float | int]] = {}
            for row in rows:
                if str(row["status"]) != "CLOSED":
                    continue
                label = f"{float(row['min_surge']):.2f}x{float(row['horizon_hours']):.0f}h"
                cell = cells.setdefault(label, {"closed": 0, "gross_pnl_usdc": 0.0})
                cell["closed"] = int(cell["closed"]) + 1
                pnl = row["hypothetical_gross_pnl"]
                if pnl is not None:
                    cell["gross_pnl_usdc"] = float(cell["gross_pnl_usdc"]) + float(pnl)
            closed_summary = dict(sorted(cells.items()))

        result = {
            "schema_version": 1,
            "strategy": "golden-blueberry-shadow",
            "review_window": {
                "start_utc": _display_utc(start),
                "end_exclusive_utc": _display_utc(end),
            },
            "status": "NOT_EVALUABLE_EVIDENCE_CONTRACT" if issues else "EVALUABLE",
            "issues": sorted(set(issues)),
            "rows": {
                "raw": len(rows),
                "unique_conditions": len({str(row["condition_id"]) for row in rows}),
                "missing_cohort_identity": missing_identity,
                "exact_duplicate_groups": duplicate_groups,
                "exact_duplicate_rows": duplicate_rows,
                "invalid_grid_conditions": invalid_grid_conditions,
            },
            "runs": {
                "total": len(run_rows),
                "status_counts": dict(
                    sorted(Counter(str(row["status"]) for row in run_rows).items())
                ),
                "runtime_observed": len(runtimes),
                "runtime_avg_seconds": (
                    sum(runtimes) / len(runtimes) if runtimes else None
                ),
                "runtime_max_seconds": max(runtimes, default=None),
            },
            # Deliberately absent when duplicates/cohort gaps exist.  No
            # first/last/min row heuristic silently deduplicates economics.
            "closed_summary": closed_summary,
        }
    finally:
        connection.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "blueberry-shadow-diagnostics.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "blueberry-shadow-diagnostics.md").write_text(
        "\n".join(
            [
                "# Golden Blueberry Shadow diagnostics",
                "",
                f"- Status: `{result['status']}`",
                f"- Issues: `{result['issues'] or 'none'}`",
                f"- Raw rows / unique conditions: {len(rows)} / {result['rows']['unique_conditions']}",
                f"- Exact duplicate groups / rows: {duplicate_groups} / {duplicate_rows}",
                f"- Closed summary emitted: `{closed_summary is not None}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--review-start", required=True)
    parser.add_argument("--review-end", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print(
        analyze_shadow(
            args.db, args.output_dir, args.review_start, args.review_end
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
