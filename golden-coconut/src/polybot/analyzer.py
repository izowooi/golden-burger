"""Read-only collection health and predeclared strata analysis."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Any, Iterable, Mapping

from .config import DATA_CONTRACT, NOTIONAL_LADDER, THRESHOLD_GRID
from .registry import FAMILY_ORDER


ANALYZER_CONTRACT = "major-sports-five-family-health-v1"
SEASON_PHASES = ("PRESEASON", "REGULAR", "POSTSEASON", "UNKNOWN", "NOT_APPLICABLE")


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator * 100 if denominator else None


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _bin(value: Any, boundaries: tuple[float, ...]) -> str:
    if value is None:
        return "MISSING"
    number = float(value)
    labels = [
        f"LT_{boundaries[0]:g}",
        *(f"{left:g}_TO_LT_{right:g}" for left, right in zip(boundaries, boundaries[1:])),
        f"GE_{boundaries[-1]:g}",
    ]
    for index, boundary in enumerate(boundaries):
        if number < boundary:
            return labels[index]
    return labels[-1]


def _open(path: Path) -> sqlite3.Connection:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"analyzer database is absent or unsafe: {path}")
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _read_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]


def _read_shard(path: Path) -> dict[str, Any]:
    connection = _open(path)
    try:
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        metadata_row = connection.execute("SELECT * FROM schema_metadata").fetchone()
        if metadata_row is None or str(metadata_row["data_contract"]) != DATA_CONTRACT:
            raise ValueError("analyzer database contract mismatch")
        table_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        forbidden = {"orders", "fills", "positions", "wallets", "trades", "pnl", "p&l"}
        if {name.casefold() for name in table_names} & forbidden:
            raise ValueError("analyzer found a forbidden transactional table")
        result = {
            "path": str(path.resolve()),
            "quick_check": quick,
            "metadata": dict(metadata_row),
        }
        for table in (
            "research_run_events", "collection_cycles", "sport_sweeps",
            "event_observations", "market_observations", "outcome_observations",
            "book_token_attempts", "book_snapshots", "book_ladder_observations",
            "threshold_vectors", "threshold_episodes", "episode_path_observations",
            "resolution_observations", "data_quality_issues", "storage_metrics",
        ):
            result[table] = _read_rows(connection, table)
        return result
    finally:
        connection.close()


def _metric_strata(
    markets: list[dict[str, Any]], metric: str, boundaries: tuple[float, ...]
) -> dict[str, Any]:
    by_family_phase: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in markets:
        if not row["eligible"]:
            continue
        value = row.get(metric)
        if metric == "liquidity_num" and value is None:
            value = row.get("liquidity")
        by_family_phase[(str(row["sport_family"]), str(row["season_phase"]))][
            _bin(value, boundaries)
        ] += 1
    return {
        family: {
            phase: dict(sorted(by_family_phase[(family, phase)].items()))
            for phase in SEASON_PHASES
            if by_family_phase[(family, phase)]
        }
        for family in FAMILY_ORDER
    }


def analyze_databases(paths: Iterable[Path]) -> dict[str, Any]:
    resolved = [Path(path).resolve() for path in paths]
    if not resolved:
        raise ValueError("analyzer requires at least one explicit database")
    identities = [(path.stat().st_dev, path.stat().st_ino) for path in resolved]
    if len(identities) != len(set(identities)):
        raise ValueError("analyzer database list contains the same shard inode twice")
    shards = [_read_shard(path) for path in resolved]
    combined: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for shard in shards:
        for key, value in shard.items():
            if isinstance(value, list):
                combined[key].extend(value)

    events = combined["event_observations"]
    markets = combined["market_observations"]
    outcomes = combined["outcome_observations"]
    attempts = combined["book_token_attempts"]
    ladders = combined["book_ladder_observations"]
    vectors = combined["threshold_vectors"]
    episodes = combined["threshold_episodes"]
    accepted_events = [row for row in events if row["classification_status"] == "ACCEPTED"]
    eligible_outcomes = [row for row in outcomes if row["threshold_eligible"]]
    observed_book_keys = {
        (str(row["cycle_id"]), str(row["token_id"]))
        for row in attempts
        if row["status"] == "OBSERVED"
    }

    family_coverage: dict[str, Any] = {}
    missing_sports: list[str] = []
    family_book_percentages: list[float] = []
    for family in FAMILY_ORDER:
        family_sweeps = [row for row in combined["sport_sweeps"] if row["sport_family"] == family]
        family_events = [row for row in accepted_events if row["sport_family"] == family]
        family_outcomes = [row for row in eligible_outcomes if row["sport_family"] == family]
        observed = sum(
            (str(row["cycle_id"]), str(row["token_id"])) in observed_book_keys
            for row in family_outcomes
        )
        book_pct = _ratio(observed, len(family_outcomes))
        if not family_events:
            missing_sports.append(family)
        if book_pct is not None:
            family_book_percentages.append(book_pct)
        family_coverage[family] = {
            "sweeps": len(family_sweeps),
            "cursor_complete_sweeps": sum(int(row["cursor_complete"]) for row in family_sweeps),
            "cursor_complete_pct": _ratio(
                sum(int(row["cursor_complete"]) for row in family_sweeps), len(family_sweeps)
            ),
            "accepted_event_observations": len(family_events),
            "unique_event_clusters": len({row["event_cluster_id"] for row in family_events}),
            "eligible_outcome_observations": len(family_outcomes),
            "observed_public_books": observed,
            "public_book_coverage_pct": book_pct,
            "by_season_phase": {
                phase: {
                    "accepted_event_observations": sum(row["season_phase"] == phase for row in family_events),
                    "eligible_outcome_observations": sum(row["season_phase"] == phase for row in family_outcomes),
                }
                for phase in SEASON_PHASES
                if any(row["season_phase"] == phase for row in [*family_events, *family_outcomes])
            },
        }
    macro_coverage = (
        statistics.fmean(family_book_percentages)
        if not missing_sports and len(family_book_percentages) == len(FAMILY_ORDER)
        else None
    )

    depth: dict[str, Any] = {}
    for notional in NOTIONAL_LADDER:
        rows = [row for row in ladders if float(row["notional_usdc"]) == notional]
        depth[f"{notional:g}"] = {
            family: {
                phase: {
                    "observations": len(selected),
                    "full_ask": sum(row["ask_status"] == "FULL" for row in selected),
                    "full_ask_pct": _ratio(
                        sum(row["ask_status"] == "FULL" for row in selected), len(selected)
                    ),
                }
                for phase in SEASON_PHASES
                if (selected := [
                    row for row in rows
                    if next(
                        (
                            vector["season_phase"]
                            for vector in vectors
                            if vector["cycle_id"] == row["cycle_id"] and vector["token_id"] == row["token_id"]
                        ),
                        None,
                    ) == phase
                    and next(
                        (
                            vector["sport_family"]
                            for vector in vectors
                            if vector["cycle_id"] == row["cycle_id"] and vector["token_id"] == row["token_id"]
                        ),
                        None,
                    ) == family
                ])
            }
            for family in FAMILY_ORDER
        }

    threshold_counts: defaultdict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    for vector in vectors:
        try:
            states = json.loads(str(vector["states_json"]))
        except json.JSONDecodeError:
            states = {}
        if not isinstance(states, Mapping):
            continue
        for threshold, state in states.items():
            threshold_counts[(str(vector["sport_family"]), str(vector["season_phase"]), str(threshold))][str(state)] += 1
    thresholds = {
        family: {
            phase: {
                f"{threshold:.2f}": dict(
                    sorted(threshold_counts[(family, phase, f"{threshold:.2f}")].items())
                )
                for threshold in THRESHOLD_GRID
                if threshold_counts[(family, phase, f"{threshold:.2f}")]
            }
            for phase in SEASON_PHASES
            if any(threshold_counts[(family, phase, f"{threshold:.2f}")] for threshold in THRESHOLD_GRID)
        }
        for family in FAMILY_ORDER
    }

    clusters: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in markets:
        if row["eligible"]:
            clusters[(str(row["sport_family"]), str(row["season_phase"]), str(row["event_cluster_id"]))].append(row)
    cluster_sizes = [len(rows) for rows in clusters.values()]
    soccer_incomplete = 0
    us_invalid = 0
    for (family, _phase, _cluster), rows in clusters.items():
        result_kinds = {str(row["result_kind"]) for row in rows}
        if family == "soccer" and not {"HOME", "DRAW", "AWAY"} <= result_kinds:
            soccer_incomplete += 1
        if family != "soccer" and any(row["structure_kind"] != "US_DIRECT_TWO_TEAM_NON_NEGRISK" for row in rows):
            us_invalid += 1

    issue_counts = Counter(
        (str(row["severity"]), str(row["issue_type"]))
        for row in combined["data_quality_issues"]
    )
    result = {
        "analyzer_contract": ANALYZER_CONTRACT,
        "databases": [
            {
                "path": shard["path"],
                "database_utc_date": shard["metadata"]["database_utc_date"],
                "quick_check": shard["quick_check"],
            }
            for shard in shards
        ],
        "health": {
            "all_quick_check_ok": all(shard["quick_check"] == "ok" for shard in shards),
            "run_events": dict(Counter(row["event_type"] for row in combined["research_run_events"])),
            "cycles": len(combined["collection_cycles"]),
            "critical_or_high_issues": sum(
                count for (severity, _kind), count in issue_counts.items() if severity in {"CRITICAL", "HIGH"}
            ),
            "issues": [
                {"severity": severity, "type": kind, "count": count}
                for (severity, kind), count in sorted(issue_counts.items())
            ],
        },
        "sport_coverage": {
            "required_sports": list(FAMILY_ORDER),
            "by_sport": family_coverage,
            "missing_sports": missing_sports,
            "sport_equal_macro_public_book_coverage_pct": macro_coverage,
            "macro_is_null_when_any_sport_is_missing": True,
        },
        "season_phase_contract": {
            "official_major_league_preseason_is_collected": True,
            "phases_are_never_pooled": True,
            "phases": list(SEASON_PHASES),
        },
        "liquidity_strata": _metric_strata(markets, "liquidity_num", (10_000, 50_000, 100_000)),
        "volume_total_strata": _metric_strata(markets, "volume_num", (5_000, 25_000, 100_000)),
        "volume_24hr_strata": _metric_strata(markets, "volume_24hr", (1_000, 10_000, 50_000)),
        "displayed_depth_ladder": depth,
        "threshold_state_strata": thresholds,
        "event_clustering": {
            "unique_game_clusters": len(clusters),
            "markets_per_cluster_p50": _percentile(cluster_sizes, 0.50),
            "markets_per_cluster_p95": _percentile(cluster_sizes, 0.95),
            "soccer_clusters_missing_home_draw_away": soccer_incomplete,
            "us_clusters_with_non_direct_structure": us_invalid,
            "by_sport_and_phase": {
                family: {
                    phase: sum(key[0] == family and key[1] == phase for key in clusters)
                    for phase in SEASON_PHASES
                    if any(key[0] == family and key[1] == phase for key in clusters)
                }
                for family in FAMILY_ORDER
            },
        },
        "crossing_evidence": {
            "origin_episode_count": len({row["episode_id"] for row in episodes}),
            "left_and_gap_censored_are_not_episodes": True,
        },
        "selection_contract": {
            "liquidity_discovery_gate": None,
            "volume_discovery_gate": None,
        },
        "interpretation": "HEALTH_AND_DISPLAYED_BOOK_RESEARCH_EVIDENCE_ONLY",
        "profitability_conclusion": None,
        "actual_execution_evidence": False,
    }
    return result


def analyze_database(path: Path) -> dict[str, Any]:
    return analyze_databases([path])
