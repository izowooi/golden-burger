"""Read-only, all-cell analyzer for Cherry Shadow Resolution v2."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import quote

from . import DATA_CONTRACT


def parse_utc(value: str) -> datetime:
    raw = str(value or "").strip()
    if "T" not in raw:
        raise ValueError("analysis timestamp must include exact UTC time")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("analysis timestamp must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("analysis timestamp must use UTC")
    return parsed.astimezone(timezone.utc)


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{quote(str(path.resolve()))}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def analyze_shadow_database(
    db_path: str | Path,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    path = Path(db_path).expanduser().resolve()
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if start >= end:
        raise ValueError("analysis start must precede end")
    with _connect(path) as connection:
        contract_rows = connection.execute(
            "SELECT data_contract FROM shadow_schema_metadata"
        ).fetchall()
        if [row[0] for row in contract_rows] != [DATA_CONTRACT]:
            raise RuntimeError("not a Cherry Shadow Resolution v2 database")
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check != "ok":
            raise RuntimeError(f"shadow DB quick_check failed: {quick_check}")
        valid_runs = {
            row[0]
            for row in connection.execute(
                """
                SELECT run_id FROM shadow_run_events
                GROUP BY run_id
                HAVING SUM(event_type='STARTED') = 1
                   AND SUM(event_type='SUCCEEDED') = 1
                   AND SUM(event_type='FAILED') = 0
                """
            )
        }
        placeholders = ",".join("?" for _ in valid_runs) or "NULL"
        run_params = tuple(sorted(valid_runs))
        episodes = connection.execute(
            f"""
            SELECT * FROM shadow_episodes
            WHERE opened_run_id IN ({placeholders})
              AND entered_at >= ? AND entered_at < ?
            ORDER BY band_id, event_cluster_id, condition_id
            """,
            (*run_params, start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")),
        ).fetchall()
        episode_ids = {row["episode_id"] for row in episodes}
        exits = []
        if episode_ids:
            episode_placeholders = ",".join("?" for _ in episode_ids)
            exits = connection.execute(
                f"""
                SELECT * FROM shadow_policy_exits
                WHERE episode_id IN ({episode_placeholders})
                  AND run_id IN ({placeholders})
                  AND exited_at < ?
                """,
                (
                    *sorted(episode_ids),
                    *run_params,
                    end.isoformat().replace("+00:00", "Z"),
                ),
            ).fetchall()
        exit_by_key = {
            (row["episode_id"], row["policy_id"]): row for row in exits
        }
        policies = [
            dict(row)
            for row in connection.execute(
                """
                SELECT DISTINCT policy_id, policy_role, take_profit, stop_loss, trailing
                FROM shadow_episode_policies ORDER BY policy_id
                """
            )
        ]
        band_results: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"episodes": 0, "event_clusters": set(), "time_strata": defaultdict(int)}
        )
        policy_results: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {
                "resolved": 0,
                "censored": 0,
                "pnl_usdc": 0.0,
                "roi_sum": 0.0,
                "event_clusters": set(),
                "event_rois": defaultdict(list),
            }
        )
        for episode in episodes:
            band = band_results[episode["band_id"]]
            band["episodes"] += 1
            band["event_clusters"].add(episode["event_cluster_id"])
            band["time_strata"][episode["time_stratum"]] += 1
            for policy in policies:
                result = policy_results[(episode["band_id"], policy["policy_id"])]
                result["event_clusters"].add(episode["event_cluster_id"])
                exit_row = exit_by_key.get((episode["episode_id"], policy["policy_id"]))
                if exit_row is None:
                    result["censored"] += 1
                else:
                    result["resolved"] += 1
                    result["pnl_usdc"] += float(exit_row["pnl_usdc"])
                    result["roi_sum"] += float(exit_row["roi"])
                    result["event_rois"][episode["event_cluster_id"]].append(
                        float(exit_row["roi"])
                    )

        cells = []
        for (band_id, policy_id), result in sorted(policy_results.items()):
            completed = result["resolved"]
            event_means = [
                sum(values) / len(values)
                for values in result["event_rois"].values()
                if values
            ]
            cells.append(
                {
                    "band_id": band_id,
                    "policy_id": policy_id,
                    "completed_episodes": completed,
                    "censored_episodes": result["censored"],
                    "event_cluster_count": len(result["event_clusters"]),
                    "pnl_usdc": round(result["pnl_usdc"], 6),
                    "mean_roi": round(result["roi_sum"] / completed, 6) if completed else None,
                    "event_cluster_mean_roi": (
                        round(sum(event_means) / len(event_means), 6)
                        if event_means
                        else None
                    ),
                }
            )
        sweeps = connection.execute(
            f"SELECT COUNT(*), SUM(cursor_complete), SUM(raw_market_count), "
            f"SUM(eligible_candidate_count), SUM(capped_candidate_count) "
            f"FROM shadow_market_sweeps WHERE run_id IN ({placeholders})",
            run_params,
        ).fetchone()
        book_counts = {
            row[0]: int(row[1])
            for row in connection.execute(
                f"SELECT status, COUNT(*) FROM shadow_book_attempts "
                f"WHERE run_id IN ({placeholders}) GROUP BY status",
                run_params,
            )
        }
        run_failures = int(
            connection.execute(
                "SELECT COUNT(DISTINCT run_id) FROM shadow_run_events WHERE event_type='FAILED'"
            ).fetchone()[0]
        )
        cohorts = [
            {
                "config_hash": row[0],
                "strategy_source_digest": row[1],
                "preregistration_sha256": row[2],
                "runtime_job": row[3],
                "mode": row[4],
            }
            for row in connection.execute(
                """
                SELECT config_hash, strategy_source_digest,
                       preregistration_sha256, runtime_job, mode
                FROM shadow_config_versions
                ORDER BY first_seen_at, config_hash
                """
            )
        ]
    rendered_bands = []
    for band_id, result in sorted(band_results.items()):
        rendered_bands.append(
            {
                "band_id": band_id,
                "episodes": result["episodes"],
                "event_cluster_count": len(result["event_clusters"]),
                "time_strata": dict(sorted(result["time_strata"].items())),
            }
        )
    return {
        "data_contract": DATA_CONTRACT,
        "window": {
            "start_inclusive": start.isoformat(),
            "end_exclusive": end.isoformat(),
        },
        "database": {
            "path": str(path),
            "sha256": _sha256(path),
            "quick_check": "ok",
            "opened_read_only": True,
        },
        "run_health": {
            "valid_successful_runs": len(valid_runs),
            "failed_runs": run_failures,
            "sweeps": int(sweeps[0] or 0),
            "cursor_complete_sweeps": int(sweeps[1] or 0),
            "raw_market_rows": int(sweeps[2] or 0),
            "eligible_candidates": int(sweeps[3] or 0),
            "capped_candidates": int(sweeps[4] or 0),
            "book_attempt_status": book_counts,
        },
        "cohorts": cohorts,
        "bands": rendered_bands,
        "paired_cells": cells,
        "winner_selected": False,
        "causal_claim": False,
        "limitations": [
            "displayed full-depth books are counterfactual, not fills",
            "unresolved episodes remain censored",
            "bootstrap evidence selected the candidate band and is not part of this prospective cohort",
            "all comparisons must remain event-clustered",
        ],
    }
