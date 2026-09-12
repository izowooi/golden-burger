#!/usr/bin/env python3
"""Replay exploratory TP/SL/trailing grids over Cherry shadow full-depth paths.

This is a read-only analysis.  It keeps each event cluster wholly in the
chronological training or validation half and never treats displayed depth as
an actual fill.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import itertools
import json
from pathlib import Path
import sqlite3
from typing import Any

from polybot.shadow.analyzer import parse_utc


TP_GRID = (0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20, None)
SL_GRID = (-0.05, -0.08, -0.10, -0.12, -0.15, -0.20, -0.30, None)
TRAILING_GRID = (0.03, 0.05, 0.08, 0.10, 0.15, 0.20, None)
BANDS = {
    "low_076_078": {"control_low_076_078"},
    "middle_080_082": {"primary_080_082"},
    "high_084_086": {"control_high_084_086"},
    "low_plus_middle": {"control_low_076_078", "primary_080_082"},
    "all_three": {
        "control_low_076_078",
        "primary_080_082",
        "control_high_084_086",
    },
}


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _replay(
    episode: dict[str, Any],
    paths: list[dict[str, Any]],
    resolution: dict[str, Any] | None,
    *,
    take_profit: float | None,
    stop_loss: float | None,
    trailing: float | None,
) -> dict[str, Any]:
    peak = float(episode["entry_vwap"])
    resolution_at = resolution["source_received_at"] if resolution else None
    for path in paths:
        if resolution_at and path["observed_at"] > resolution_at:
            break
        bid = path["executable_bid_vwap"]
        if bid is None:
            continue
        bid = float(bid)
        peak = max(peak, bid)
        roi = bid / float(episode["entry_vwap"]) - 1
        kind = None
        if stop_loss is not None and roi <= stop_loss:
            kind = "STOP_LOSS"
        elif take_profit is not None and roi >= take_profit:
            kind = "TAKE_PROFIT"
        elif trailing is not None and bid < peak * (1 - trailing):
            kind = "TRAILING_STOP"
        if kind:
            return {
                "completed": True,
                "exit_kind": kind,
                "pnl_usdc": float(path["executable_proceeds"])
                - float(episode["entry_cost"]),
            }
    if resolution:
        return {
            "completed": True,
            "exit_kind": "RESOLUTION",
            "pnl_usdc": float(resolution["token_payout"])
            * float(episode["entry_shares"])
            - float(episode["entry_cost"]),
        }
    return {"completed": False, "exit_kind": "CENSORED", "pnl_usdc": 0.0}


def _stats(
    episodes: list[dict[str, Any]],
    replayed: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    completed = [episode for episode in episodes if replayed[episode["episode_id"]]["completed"]]
    pnl = sum(replayed[episode["episode_id"]]["pnl_usdc"] for episode in completed)
    cost = sum(float(episode["entry_cost"]) for episode in completed)
    cluster_pnl: dict[str, float] = defaultdict(float)
    cluster_cost: dict[str, float] = defaultdict(float)
    exits: Counter[str] = Counter()
    for episode in completed:
        result = replayed[episode["episode_id"]]
        cluster = str(episode["event_cluster_id"])
        cluster_pnl[cluster] += float(result["pnl_usdc"])
        cluster_cost[cluster] += float(episode["entry_cost"])
        exits[str(result["exit_kind"])] += 1
    cluster_rois = [cluster_pnl[key] / cluster_cost[key] for key in cluster_pnl]
    return {
        "event_clusters": len({str(row["event_cluster_id"]) for row in episodes}),
        "episodes": len(episodes),
        "completed": len(completed),
        "censored": len(episodes) - len(completed),
        "entry_cost_usdc": round(cost, 6),
        "pnl_usdc": round(pnl, 6),
        "roi": round(pnl / cost, 6) if cost else None,
        "event_cluster_mean_roi": (
            round(sum(cluster_rois) / len(cluster_rois), 6) if cluster_rois else None
        ),
        "exit_counts": dict(sorted(exits.items())),
    }


def analyze(path: Path, start: datetime, end: datetime) -> dict[str, Any]:
    with _connect(path) as connection:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("shadow database quick_check failed")
        valid_runs = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT run_id FROM shadow_run_events GROUP BY run_id
                HAVING SUM(event_type='STARTED')=1
                   AND SUM(event_type='SUCCEEDED')=1
                   AND SUM(event_type='FAILED')=0
                """
            )
        }
        invalid_exit_episode_ids = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT episode_id, run_id FROM shadow_policy_exits"
            )
            if str(row[1]) not in valid_runs
        }
        episodes = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM shadow_episodes WHERE entered_at>=? AND entered_at<?",
                (
                    start.isoformat().replace("+00:00", "Z"),
                    end.isoformat().replace("+00:00", "Z"),
                ),
            )
            if str(row["opened_run_id"]) in valid_runs
            and str(row["episode_id"]) not in invalid_exit_episode_ids
        ]
        paths: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in connection.execute(
            """
            SELECT * FROM shadow_path_observations
            WHERE path_status='FULL_DEPTH' ORDER BY observed_at, path_id
            """
        ):
            if str(row["run_id"]) in valid_runs:
                paths[str(row["episode_id"])].append(dict(row))
        resolutions: dict[tuple[str, str], dict[str, Any]] = {}
        for row in connection.execute(
            """
            SELECT * FROM shadow_resolution_observations
            WHERE resolution_status='PROVEN'
            ORDER BY source_received_at, resolution_id
            """
        ):
            if str(row["run_id"]) in valid_runs:
                resolutions.setdefault(
                    (str(row["condition_id"]), str(row["token_id"])), dict(row)
                )

    universes: dict[str, list[dict[str, Any]]] = {
        name: [episode for episode in episodes if episode["band_id"] in band_ids]
        for name, band_ids in BANDS.items()
    }
    results = []
    for universe, selected in universes.items():
        cluster_times: dict[str, str] = {}
        for episode in selected:
            cluster = str(episode["event_cluster_id"])
            cluster_times[cluster] = min(
                cluster_times.get(cluster, str(episode["entered_at"])),
                str(episode["entered_at"]),
            )
        ordered_clusters = sorted(cluster_times, key=lambda key: (cluster_times[key], key))
        train_clusters = set(ordered_clusters[: len(ordered_clusters) // 2])
        training = [row for row in selected if str(row["event_cluster_id"]) in train_clusters]
        validation = [row for row in selected if str(row["event_cluster_id"]) not in train_clusters]
        for take_profit, stop_loss, trailing in itertools.product(
            TP_GRID, SL_GRID, TRAILING_GRID
        ):
            replayed = {}
            for episode in selected:
                replayed[episode["episode_id"]] = _replay(
                    episode,
                    paths.get(episode["episode_id"], []),
                    resolutions.get((episode["condition_id"], episode["token_id"])),
                    take_profit=take_profit,
                    stop_loss=stop_loss,
                    trailing=trailing,
                )
            results.append(
                {
                    "universe": universe,
                    "take_profit": take_profit,
                    "stop_loss": stop_loss,
                    "trailing": trailing,
                    "training": _stats(training, replayed),
                    "validation": _stats(validation, replayed),
                    "all": _stats(selected, replayed),
                }
            )

    summaries = {}
    for universe, selected in universes.items():
        candidates = [
            row
            for row in results
            if row["universe"] == universe
            and row["training"]["event_clusters"] >= 15
            and row["validation"]["event_clusters"] >= 15
        ]
        training_ranked = sorted(
            candidates,
            key=lambda row: (
                row["training"]["event_cluster_mean_roi"],
                row["training"]["pnl_usdc"],
            ),
            reverse=True,
        )
        all_data_ranked = sorted(
            candidates,
            key=lambda row: (
                row["all"]["event_cluster_mean_roi"],
                row["all"]["pnl_usdc"],
            ),
            reverse=True,
        )
        balanced_ranked = sorted(
            candidates,
            key=lambda row: (
                min(
                    row["training"]["event_cluster_mean_roi"],
                    row["validation"]["event_cluster_mean_roi"],
                ),
                row["all"]["event_cluster_mean_roi"],
            ),
            reverse=True,
        )
        robust = [
            row
            for row in candidates
            if row["training"]["pnl_usdc"] > 0
            and row["validation"]["pnl_usdc"] > 0
        ]
        current = next(
            row
            for row in candidates
            if row["take_profit"] == 0.10
            and row["stop_loss"] == -0.08
            and row["trailing"] == 0.05
        )
        candidate = next(
            row
            for row in candidates
            if row["take_profit"] == 0.20
            and row["stop_loss"] == -0.08
            and row["trailing"] == 0.15
        )
        summaries[universe] = {
            "episodes": len(selected),
            "event_clusters": len(
                {str(row["event_cluster_id"]) for row in selected}
            ),
            "grid_cells": len(candidates),
            "positive_both_halves_cells": len(robust),
            "training_selected": training_ranked[0],
            "all_data_selected_exploratory": all_data_ranked[0],
            "balanced_both_halves_selected_exploratory": balanced_ranked[0],
            "current_tp10_sl08_trail05": current,
            "candidate_tp20_sl08_trail15": candidate,
        }
    return {
        "contract": "cherry-shadow-exploratory-grid-v1",
        "window": {
            "start_inclusive": start.isoformat(),
            "end_exclusive": end.isoformat(),
        },
        "database": str(path.resolve()),
        "valid_episode_count": len(episodes),
        "failed_run_exit_contaminated_episodes_excluded": len(
            invalid_exit_episode_ids
        ),
        "full_depth_path_count": sum(len(rows) for rows in paths.values()),
        "proven_resolution_count": len(resolutions),
        "grid": {
            "take_profit": TP_GRID,
            "stop_loss": SL_GRID,
            "trailing": TRAILING_GRID,
            "cells_per_universe": len(TP_GRID) * len(SL_GRID) * len(TRAILING_GRID),
        },
        "summaries": summaries,
        "limitations": [
            "displayed full-depth VWAP is counterfactual rather than an actual fill",
            "the current prospective database began on 2026-09-04, not 166 days ago",
            "only the three preregistered narrow entry bands have complete paths",
            "training selection searches many correlated cells and remains exploratory",
            "live Cherry has no post-exit path, so its 166-day DB cannot replay arbitrary exits",
            "episodes with any policy exit published by a FAILED run are excluded entirely",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(args.db, parse_utc(args.start), parse_utc(args.end))
    payload = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
