"""Read-only v6 collection-health and preregistered strata analysis."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Any, Iterable, Mapping

from .config import (
    CLASSIFIER_VERSION,
    COLLECTION_CONTRACT,
    DATA_CONTRACT,
    NOTIONAL_LADDER,
    SCHEMA_PROFILE,
    THRESHOLD_GRID,
    UNIVERSE_PROFILE,
)
from .registry import FAMILY_ORDER


ANALYZER_CONTRACT = "major-sports-lifecycle-health-v6"
SEASON_PHASES = (
    "PRESEASON",
    "REGULAR",
    "POSTSEASON",
    "UNKNOWN",
    "NOT_APPLICABLE",
)
TERMINAL_STATES = frozenset({"CANCELLED", "RESOLVED", "VOID", "TIE"})
BOOK_LIFECYCLE_STATES = ("DISCOVERED_OPEN", "PREGAME", "IN_PLAY")
SCHEDULE_WINDOW_STATUSES = frozenset(
    {"MISSING", "INVALID", "WITHIN_WINDOW", "OUTSIDE_WINDOW"}
)
SCHEDULE_REASONS = {
    "MISSING": "DISCOVERY_SCHEDULE_MISSING",
    "INVALID": "DISCOVERY_SCHEDULE_INVALID",
    "OUTSIDE_WINDOW": "DISCOVERY_SCHEDULE_OUTSIDE_WINDOW",
}


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
        *(
            f"{left:g}_TO_LT_{right:g}"
            for left, right in zip(boundaries, boundaries[1:])
        ),
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
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if user_version != 6:
            raise ValueError("analyzer database schema epoch must be v6")
        metadata_row = connection.execute("SELECT * FROM schema_metadata").fetchone()
        if metadata_row is None:
            raise ValueError("analyzer database has no schema metadata")
        expected_metadata = {
            "data_contract": DATA_CONTRACT,
            "collection_contract": COLLECTION_CONTRACT,
            "schema_profile": SCHEMA_PROFILE,
            "universe_profile": UNIVERSE_PROFILE,
            "classifier_version": CLASSIFIER_VERSION,
        }
        actual_metadata = {
            key: str(metadata_row[key]) for key in expected_metadata
        }
        if actual_metadata != expected_metadata:
            raise ValueError("analyzer database contract mismatch")
        table_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        forbidden = {"orders", "fills", "positions", "wallets", "trades", "pnl", "p&l"}
        if {name.casefold() for name in table_names} & forbidden:
            raise ValueError("analyzer found a forbidden transactional table")
        sweep_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(sport_sweeps)")
        }
        if not {"start_time_min", "start_time_max"} <= sweep_columns or {
            "start_date_min",
            "start_date_max",
        } & sweep_columns:
            raise ValueError("analyzer sport_sweeps schedule columns are not v6")
        result = {
            "path": str(path.resolve()),
            "file_bytes": path.stat().st_size,
            "quick_check": quick,
            "metadata": dict(metadata_row),
            "user_version": user_version,
        }
        for table in (
            "research_config_versions",
            "research_run_events",
            "api_requests",
            "collection_cycles",
            "sport_sweeps",
            "event_observations",
            "game_lifecycle_observations",
            "schedule_revision_observations",
            "market_observations",
            "outcome_observations",
            "book_token_attempts",
            "book_snapshots",
            "book_ladder_observations",
            "threshold_vectors",
            "threshold_episodes",
            "episode_path_observations",
            "game_anchor_observations",
            "resolution_attempts",
            "resolution_observations",
            "sports_clock_observations",
            "data_quality_issues",
            "storage_metrics",
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


def _cohort(config_rows: list[dict[str, Any]]) -> dict[str, str]:
    keys = {
        (
            str(row["config_hash"]),
            str(row["strategy_source_digest"]),
            str(row["mode"]),
            str(row["job_name"]),
        )
        for row in config_rows
    }
    if len(keys) != 1:
        raise ValueError(
            "analyzer inputs must contain exactly one "
            "config_hash x strategy_source_digest x mode x job_name cohort"
        )
    config_hash, source_digest, mode, job_name = next(iter(keys))
    return {
        "config_hash": config_hash,
        "strategy_source_digest": source_digest,
        "mode": mode,
        "job_name": job_name,
    }


def _successful_cycle_selection(
    cycles: list[dict[str, Any]],
    sweeps: list[dict[str, Any]],
    run_events: list[dict[str, Any]],
) -> tuple[set[str], dict[str, Any]]:
    cycle_ids = [str(row["cycle_id"]) for row in cycles]
    if len(cycle_ids) != len(set(cycle_ids)):
        raise ValueError("analyzer inputs contain duplicate cycle identities across shards")
    terminal_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in run_events:
        terminal_counts[str(row["run_id"])][str(row["event_type"])] += 1
    successful_runs = {
        run_id
        for run_id, counts in terminal_counts.items()
        if counts["SUCCEEDED"] == 1 and counts["FAILED"] == 0
    }
    sweeps_by_cycle: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sweeps:
        sweeps_by_cycle[str(row["cycle_id"])].append(row)
    selected: set[str] = set()
    rejection_counts: Counter[str] = Counter()
    for cycle in cycles:
        cycle_id = str(cycle["cycle_id"])
        run_id = str(cycle["run_id"])
        if run_id not in successful_runs:
            rejection_counts["NOT_UNIQUELY_SUCCEEDED"] += 1
            continue
        rows = sweeps_by_cycle[cycle_id]
        family_set = {str(row["sport_family"]) for row in rows}
        sweep_contract = (
            len(rows) == len(FAMILY_ORDER)
            and family_set == set(FAMILY_ORDER)
            and all(int(row["cursor_complete"]) == 1 for row in rows)
        )
        if not sweep_contract or int(cycle["all_families_cursor_complete"]) != 1:
            rejection_counts["FIVE_FAMILY_CURSOR_INCOMPLETE"] += 1
            continue
        selected.add(cycle_id)
    return selected, {
        "all_published_cycles": len(cycles),
        "uniquely_succeeded_runs": len(successful_runs),
        "selected_cycles": len(selected),
        "excluded_cycles": len(cycles) - len(selected),
        "exclusion_reasons": dict(sorted(rejection_counts.items())),
    }


def _selected(rows: list[dict[str, Any]], cycle_ids: set[str]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("cycle_id") or "") in cycle_ids]


def _anchor_report(
    anchors: list[dict[str, Any]], outcomes: list[dict[str, Any]]
) -> dict[str, Any]:
    denominators = {
        lifecycle_state: {
            (str(row["event_cluster_id"]), str(row["token_id"]))
            for row in outcomes
            if row["threshold_eligible"]
            and str(row["lifecycle_state"]) == lifecycle_state
        }
        for lifecycle_state in ("PREGAME", "DISCOVERED_OPEN")
    }
    grouped: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in anchors:
        grouped[(str(row["event_cluster_id"]), str(row["token_id"]))].append(row)

    def target_summary(
        denominator: set[tuple[str, str]],
        target_minutes: float,
        *,
        last: bool = False,
    ) -> dict[str, Any]:
        errors: list[float] = []
        selected_count = 0
        for key in denominator:
            candidates = grouped.get(key, [])
            if not candidates:
                continue
            chosen = min(
                candidates,
                key=lambda row: (
                    float(row["minutes_to_scheduled_start"])
                    if last
                    else abs(float(row["minutes_to_scheduled_start"]) - target_minutes),
                    str(row["observed_at"]),
                ),
            )
            selected_count += 1
            observed = float(chosen["minutes_to_scheduled_start"])
            errors.append(observed if last else abs(observed - target_minutes))
        return {
            "eligible_token_games": len(denominator),
            "selected": selected_count,
            "missing": len(denominator) - selected_count,
            "coverage_pct": _ratio(selected_count, len(denominator)),
            "absolute_error_minutes_p50": _percentile(errors, 0.50),
            "absolute_error_minutes_p95": _percentile(errors, 0.95),
        }

    return {
        "sample_unit": "unique_event_cluster_x_token",
        "by_lifecycle_state": {
            lifecycle_state: {
                "t_minus_24h": target_summary(denominator, 1440.0),
                "t_minus_60m": target_summary(denominator, 60.0),
                "last_prestart": target_summary(denominator, 0.0, last=True),
            }
            for lifecycle_state, denominator in denominators.items()
        },
        "lifecycle_strata_are_never_pooled": True,
        "missingness_is_reported_and_never_imputed": True,
    }


def _schedule_window_accounting(events: list[dict[str, Any]]) -> dict[str, Any]:
    discovery = [row for row in events if str(row["source_kind"]) == "DISCOVERY"]
    by_family: dict[str, Any] = {}
    violations: Counter[str] = Counter()
    accounted = 0

    for family in FAMILY_ORDER:
        rows = [row for row in discovery if str(row["sport_family"]) == family]
        statuses: Counter[str] = Counter()
        classifications: Counter[str] = Counter()
        rejection_reasons: Counter[str] = Counter()
        tracked_outside = 0
        family_accounted = 0

        for row in rows:
            classification = str(row["classification_status"])
            classifications[classification] += 1
            reasons = {
                reason
                for reason in str(row.get("classification_reason") or "").split(";")
                if reason
            }
            for reason in reasons & set(SCHEDULE_REASONS.values()):
                rejection_reasons[reason] += 1
            try:
                evidence = json.loads(str(row["classification_evidence_json"]))
            except (json.JSONDecodeError, TypeError):
                evidence = None
            validation = (
                evidence.get("discovery_window_validation")
                if isinstance(evidence, Mapping)
                else None
            )
            if not isinstance(validation, Mapping):
                violations["MISSING_DISCOVERY_WINDOW_VALIDATION"] += 1
                continue
            status = str(validation.get("status") or "")
            tracked = validation.get("tracked_event") is True
            half_open = validation.get("half_open") is True
            if status not in SCHEDULE_WINDOW_STATUSES or not half_open:
                violations["INVALID_DISCOVERY_WINDOW_VALIDATION"] += 1
                continue
            family_accounted += 1
            accounted += 1
            statuses[status] += 1
            if tracked and status == "OUTSIDE_WINDOW":
                tracked_outside += 1
            expected_reason = SCHEDULE_REASONS.get(status)
            if not tracked and status != "WITHIN_WINDOW":
                if classification != "REJECTED":
                    violations["NEW_OUT_OF_WINDOW_EVENT_NOT_REJECTED"] += 1
                if expected_reason not in reasons:
                    violations["SCHEDULE_REJECTION_REASON_MISSING"] += 1

        by_family[family] = {
            "discovery_event_observations": len(rows),
            "accounted_observations": family_accounted,
            "accounting_coverage_pct": _ratio(family_accounted, len(rows)),
            "window_statuses": dict(sorted(statuses.items())),
            "classification_statuses": dict(sorted(classifications.items())),
            "schedule_rejection_reasons": dict(sorted(rejection_reasons.items())),
            "tracked_outside_window_observations": tracked_outside,
        }

    return {
        "contract": "UTC_HALF_OPEN_START_TIME_V5",
        "discovery_event_observations": len(discovery),
        "accounted_observations": accounted,
        "accounting_coverage_pct": _ratio(accounted, len(discovery)),
        "by_sport": by_family,
        "violations": dict(sorted(violations.items())),
        "gate_passed": accounted == len(discovery) and not violations,
        "tracked_events_may_remain_followed_outside_window": True,
    }


def _query_tag_accounting(
    cycles: list[dict[str, Any]],
    sweeps: list[dict[str, Any]],
    api_requests: list[dict[str, Any]],
) -> dict[str, Any]:
    gamma_requests = [
        row
        for row in api_requests
        if str(row["request_kind"]) == "gamma_events_keyset"
        and str(row["status"]) == "SUCCESS"
    ]
    by_run_family: defaultdict[tuple[str, str], set[int]] = defaultdict(set)
    violations: Counter[str] = Counter()
    for row in gamma_requests:
        try:
            params = json.loads(str(row["params_json"]))
            tag_id = params.get("tag_id") if isinstance(params, Mapping) else None
        except (json.JSONDecodeError, TypeError):
            tag_id = None
        if isinstance(tag_id, bool) or not isinstance(tag_id, int):
            violations["INVALID_GAMMA_QUERY_TAG_EVIDENCE"] += 1
            continue
        by_run_family[(str(row["run_id"]), str(row["sport_family"]))].add(tag_id)

    selected_run_ids = {str(row["run_id"]) for row in cycles}
    by_family: dict[str, Any] = {}
    exact_sweeps = 0
    for family in FAMILY_ORDER:
        rows = [row for row in sweeps if str(row["sport_family"]) == family]
        expected_union: set[int] = set()
        observed_union: set[int] = set()
        family_exact = 0
        for sweep in rows:
            try:
                envelope = json.loads(str(sweep["request_envelope_json"]))
                query_tags = (
                    envelope.get("query_tag_ids")
                    if isinstance(envelope, Mapping)
                    else None
                )
            except (json.JSONDecodeError, TypeError):
                query_tags = None
            if (
                not isinstance(query_tags, list)
                or not query_tags
                or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in query_tags
                )
            ):
                violations["INVALID_SWEEP_QUERY_TAG_ENVELOPE"] += 1
                continue
            expected = set(query_tags)
            observed = by_run_family[(str(sweep["run_id"]), family)]
            expected_union.update(expected)
            observed_union.update(observed)
            if expected == observed:
                family_exact += 1
                exact_sweeps += 1
            else:
                violations["QUERY_TAG_SET_MISMATCH"] += 1
        by_family[family] = {
            "selected_sweeps": len(rows),
            "exact_query_tag_sweeps": family_exact,
            "expected_query_tag_ids": sorted(expected_union),
            "observed_successful_query_tag_ids": sorted(observed_union),
        }

    expected_sweeps = len(sweeps)
    orphan_request_runs = {
        str(row["run_id"]) for row in gamma_requests
    } - selected_run_ids
    if orphan_request_runs:
        violations["UNSELECTED_RUN_QUERY_EVIDENCE_INCLUDED"] += len(
            orphan_request_runs
        )
    return {
        "selected_sweeps": expected_sweeps,
        "exact_query_tag_sweeps": exact_sweeps,
        "coverage_pct": _ratio(exact_sweeps, expected_sweeps),
        "by_sport": by_family,
        "violations": dict(sorted(violations.items())),
        "gate_passed": exact_sweeps == expected_sweeps and not violations,
    }


def _storage_growth(shards: list[dict[str, Any]]) -> dict[str, Any]:
    by_database: list[dict[str, Any]] = []
    for shard in shards:
        rows = sorted(shard["storage_metrics"], key=lambda row: str(row["observed_at"]))
        first = int(rows[0]["database_bytes"]) if rows else None
        last = int(rows[-1]["database_bytes"]) if rows else None
        by_database.append(
            {
                "path": shard["path"],
                "database_utc_date": shard["metadata"]["database_utc_date"],
                "file_bytes_at_analysis": shard["file_bytes"],
                "first_metric_database_bytes": first,
                "last_metric_database_bytes": last,
                "metric_growth_bytes": last - first if first is not None and last is not None else None,
                "metric_observations": len(rows),
            }
        )
    return {
        "total_file_bytes_at_analysis": sum(int(shard["file_bytes"]) for shard in shards),
        "by_database": by_database,
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

    cohort = _cohort(combined["research_config_versions"])
    selected_cycle_ids, selection = _successful_cycle_selection(
        combined["collection_cycles"],
        combined["sport_sweeps"],
        combined["research_run_events"],
    )
    cycles = _selected(combined["collection_cycles"], selected_cycle_ids)
    sweeps = _selected(combined["sport_sweeps"], selected_cycle_ids)
    events = _selected(combined["event_observations"], selected_cycle_ids)
    lifecycle = _selected(combined["game_lifecycle_observations"], selected_cycle_ids)
    schedule_revisions = _selected(
        combined["schedule_revision_observations"], selected_cycle_ids
    )
    markets = _selected(combined["market_observations"], selected_cycle_ids)
    outcomes = _selected(combined["outcome_observations"], selected_cycle_ids)
    attempts = _selected(combined["book_token_attempts"], selected_cycle_ids)
    snapshots = _selected(combined["book_snapshots"], selected_cycle_ids)
    ladders = _selected(combined["book_ladder_observations"], selected_cycle_ids)
    vectors = _selected(combined["threshold_vectors"], selected_cycle_ids)
    episodes = _selected(combined["threshold_episodes"], selected_cycle_ids)
    anchors = _selected(combined["game_anchor_observations"], selected_cycle_ids)
    resolutions = _selected(combined["resolution_observations"], selected_cycle_ids)
    clocks = _selected(combined["sports_clock_observations"], selected_cycle_ids)
    selected_run_ids = {str(row["run_id"]) for row in cycles}
    api_requests = [
        row
        for row in combined["api_requests"]
        if str(row["run_id"]) in selected_run_ids
    ]
    issues = [
        row
        for row in combined["data_quality_issues"]
        if str(row.get("cycle_id") or "") in selected_cycle_ids
        or str(row.get("run_id") or "") in selected_run_ids
    ]

    accepted_events = [row for row in events if row["classification_status"] == "ACCEPTED"]
    schedule_accounting = _schedule_window_accounting(events)
    query_tag_accounting = _query_tag_accounting(cycles, sweeps, api_requests)
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
        family_sweeps = [row for row in sweeps if row["sport_family"] == family]
        family_events = [row for row in accepted_events if row["sport_family"] == family]
        family_outcomes = [row for row in eligible_outcomes if row["sport_family"] == family]
        observed = sum(
            (str(row["cycle_id"]), str(row["token_id"])) in observed_book_keys
            for row in family_outcomes
        )
        book_pct = _ratio(observed, len(family_outcomes))
        lifecycle_book_coverage = {}
        for lifecycle_state in BOOK_LIFECYCLE_STATES:
            state_outcomes = [
                row
                for row in family_outcomes
                if str(row["lifecycle_state"]) == lifecycle_state
            ]
            state_observed = sum(
                (str(row["cycle_id"]), str(row["token_id"])) in observed_book_keys
                for row in state_outcomes
            )
            lifecycle_book_coverage[lifecycle_state] = {
                "eligible_outcome_observations": len(state_outcomes),
                "observed_public_books": state_observed,
                "public_book_coverage_pct": _ratio(
                    state_observed, len(state_outcomes)
                ),
            }
        if not family_events:
            missing_sports.append(family)
        if book_pct is not None:
            family_book_percentages.append(book_pct)
        family_coverage[family] = {
            "sweeps": len(family_sweeps),
            "cursor_complete_sweeps": sum(int(row["cursor_complete"]) for row in family_sweeps),
            "cursor_complete_pct": _ratio(
                sum(int(row["cursor_complete"]) for row in family_sweeps),
                len(family_sweeps),
            ),
            "accepted_event_observations": len(family_events),
            "unique_event_clusters": len({row["event_cluster_id"] for row in family_events}),
            "eligible_outcome_observations": len(family_outcomes),
            "observed_public_books": observed,
            "public_book_coverage_pct": book_pct,
            "by_lifecycle_state": lifecycle_book_coverage,
            "by_season_phase": {
                phase: {
                    "accepted_event_observations": sum(
                        row["season_phase"] == phase for row in family_events
                    ),
                    "eligible_outcome_observations": sum(
                        row["season_phase"] == phase for row in family_outcomes
                    ),
                }
                for phase in SEASON_PHASES
                if any(
                    row["season_phase"] == phase
                    for row in [*family_events, *family_outcomes]
                )
            },
        }
    macro_coverage = (
        statistics.fmean(family_book_percentages)
        if not missing_sports and len(family_book_percentages) == len(FAMILY_ORDER)
        else None
    )
    lifecycle_macro_coverage = {
        lifecycle_state: (
            statistics.fmean(percentages)
            if len(percentages) == len(FAMILY_ORDER)
            else None
        )
        for lifecycle_state in BOOK_LIFECYCLE_STATES
        if (
            percentages := [
                float(family_coverage[family]["by_lifecycle_state"][lifecycle_state]["public_book_coverage_pct"])
                for family in FAMILY_ORDER
                if family_coverage[family]["by_lifecycle_state"][lifecycle_state]["public_book_coverage_pct"]
                is not None
            ]
        )
    }

    vector_context = {
        (str(row["cycle_id"]), str(row["token_id"]), float(row["notional_usdc"])): (
            str(row["sport_family"]),
            str(row["season_phase"]),
        )
        for row in vectors
    }
    depth: dict[str, Any] = {}
    for notional in NOTIONAL_LADDER:
        rows = [row for row in ladders if float(row["notional_usdc"]) == notional]
        depth[f"{notional:g}"] = {
            family: {
                phase: {
                    "observations": len(selected_rows),
                    "full_ask": sum(row["ask_status"] == "FULL" for row in selected_rows),
                    "full_ask_pct": _ratio(
                        sum(row["ask_status"] == "FULL" for row in selected_rows),
                        len(selected_rows),
                    ),
                }
                for phase in SEASON_PHASES
                if (
                    selected_rows := [
                        row
                        for row in rows
                        if vector_context.get(
                            (
                                str(row["cycle_id"]),
                                str(row["token_id"]),
                                float(row["notional_usdc"]),
                            )
                        )
                        == (family, phase)
                    ]
                )
            }
            for family in FAMILY_ORDER
        }

    threshold_counts: defaultdict[tuple[str, str, float, str], Counter[str]] = (
        defaultdict(Counter)
    )
    for vector in vectors:
        try:
            states = json.loads(str(vector["states_json"]))
        except json.JSONDecodeError:
            states = {}
        if not isinstance(states, Mapping):
            continue
        for threshold, state in states.items():
            threshold_counts[
                (
                    str(vector["sport_family"]),
                    str(vector["season_phase"]),
                    float(vector["notional_usdc"]),
                    str(threshold),
                )
            ][str(state)] += 1
    thresholds = {
        family: {
            phase: {
                f"{notional:g}": {
                    f"{threshold:.2f}": dict(
                        sorted(
                            threshold_counts[
                                (family, phase, notional, f"{threshold:.2f}")
                            ].items()
                        )
                    )
                    for threshold in THRESHOLD_GRID
                    if threshold_counts[
                        (family, phase, notional, f"{threshold:.2f}")
                    ]
                }
                for notional in NOTIONAL_LADDER
                if any(
                    threshold_counts[(family, phase, notional, f"{threshold:.2f}")]
                    for threshold in THRESHOLD_GRID
                )
            }
            for phase in SEASON_PHASES
            if any(
                threshold_counts[(family, phase, notional, f"{threshold:.2f}")]
                for notional in NOTIONAL_LADDER
                for threshold in THRESHOLD_GRID
            )
        }
        for family in FAMILY_ORDER
    }

    clusters: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in markets:
        if row["eligible"]:
            clusters[
                (
                    str(row["sport_family"]),
                    str(row["season_phase"]),
                    str(row["event_cluster_id"]),
                )
            ].append(row)
    cluster_sizes = [len(rows) for rows in clusters.values()]
    soccer_incomplete = 0
    us_invalid = 0
    for (family, _phase, _cluster), rows in clusters.items():
        result_kinds = {str(row["result_kind"]) for row in rows}
        if family == "soccer" and not {"HOME", "DRAW", "AWAY"} <= result_kinds:
            soccer_incomplete += 1
        if family != "soccer" and any(
            row["structure_kind"] != "US_DIRECT_TWO_TEAM_NON_NEGRISK" for row in rows
        ):
            us_invalid += 1

    all_notional_values = {float(value) for value in NOTIONAL_LADDER}
    vector_keys: defaultdict[tuple[str, str], set[float]] = defaultdict(set)
    ladder_keys: defaultdict[tuple[str, str], set[float]] = defaultdict(set)
    for row in vectors:
        vector_keys[(str(row["cycle_id"]), str(row["token_id"]))].add(
            float(row["notional_usdc"])
        )
    for row in ladders:
        ladder_keys[(str(row["cycle_id"]), str(row["token_id"]))].add(
            float(row["notional_usdc"])
        )
    snapshot_keys = {
        (str(row["cycle_id"]), str(row["token_id"])) for row in snapshots
    }
    complete_vectors = sum(vector_keys[key] == all_notional_values for key in snapshot_keys)
    complete_ladders = sum(ladder_keys[key] == all_notional_values for key in snapshot_keys)
    notional_completeness = {
        "frozen_ladder_usdc": list(NOTIONAL_LADDER),
        "canonical_books": len(snapshot_keys),
        "books_with_complete_ladder_rows": complete_ladders,
        "books_with_complete_threshold_vectors": complete_vectors,
        "ladder_complete_pct": _ratio(complete_ladders, len(snapshot_keys)),
        "threshold_vector_complete_pct": _ratio(complete_vectors, len(snapshot_keys)),
    }

    lifecycle_by_cluster: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in lifecycle:
        lifecycle_by_cluster[str(row["event_cluster_id"])].append(row)
    ended_clusters = {
        cluster
        for cluster, rows in lifecycle_by_cluster.items()
        if any(str(row["lifecycle_state"]) == "ENDED" for row in rows)
        or any(str(row["lifecycle_state"]) in TERMINAL_STATES for row in rows)
    }
    terminal_clusters = {
        cluster
        for cluster, rows in lifecycle_by_cluster.items()
        if any(str(row["lifecycle_state"]) in TERMINAL_STATES for row in rows)
    }
    accepted_clusters = {str(row["event_cluster_id"]) for row in accepted_events}
    clock_by_source = Counter(str(row["source_kind"]) for row in clocks)
    lifecycle_health = {
        "accepted_unique_games": len(accepted_clusters),
        "followup_event_observations": sum(
            str(row["source_kind"]) == "FOLLOWUP" for row in accepted_events
        ),
        "cycles_followup_complete": sum(int(row["followup_complete"]) for row in cycles),
        "cycles_followup_complete_pct": _ratio(
            sum(int(row["followup_complete"]) for row in cycles), len(cycles)
        ),
        "lifecycle_state_observations": dict(
            sorted(Counter(str(row["lifecycle_state"]) for row in lifecycle).items())
        ),
        "ended_or_terminal_unique_games": len(ended_clusters),
        "explicit_terminal_unique_games": len(terminal_clusters),
        "terminal_coverage_for_ended_games_pct": _ratio(
            len(terminal_clusters & ended_clusters), len(ended_clusters)
        ),
        "schedule_revision_observations": len(schedule_revisions),
        "sports_clock_observations_by_source": dict(sorted(clock_by_source.items())),
        "sports_clock_unique_games": len(
            {str(row["event_cluster_id"]) for row in clocks}
        ),
        "wall_time_is_never_used_as_match_clock": True,
    }

    issue_counts = Counter(
        (str(row["severity"]), str(row["issue_type"])) for row in issues
    )
    critical_or_high = sum(
        count
        for (severity, _kind), count in issue_counts.items()
        if severity in {"CRITICAL", "HIGH"}
    )
    collection_dates = {
        str(row["slot_start_utc"])[:10]
        for row in cycles
        if str(row.get("slot_start_utc") or "")
    }
    all_quick = all(shard["quick_check"] == "ok" for shard in shards)
    five_family_games = all(
        family_coverage[family]["unique_event_clusters"] > 0 for family in FAMILY_ORDER
    )
    complete_notional = bool(snapshot_keys) and (
        complete_vectors == len(snapshot_keys) == complete_ladders
    )
    terminal_complete = not ended_clusters or terminal_clusters >= ended_clusters
    health_gate_checks = {
        "single_cohort": True,
        "has_selected_successful_cycle": bool(cycles),
        "all_shards_quick_check_ok": all_quick,
        "selected_cycle_critical_or_high_issues_zero": critical_or_high == 0,
        "minimum_seven_distinct_utc_dates": len(collection_dates) >= 7,
        "at_least_one_unique_game_per_sport": five_family_games,
        "query_tag_accounting": bool(query_tag_accounting["gate_passed"]),
        "schedule_window_accounting": bool(schedule_accounting["gate_passed"]),
        "complete_notional_rows_per_canonical_book": complete_notional,
        "explicit_terminal_coverage_for_discovered_ended_games": terminal_complete,
        "anchor_missingness_reported_without_imputation": True,
    }

    return {
        "analyzer_contract": ANALYZER_CONTRACT,
        "cohort": cohort,
        "databases": [
            {
                "path": shard["path"],
                "database_utc_date": shard["metadata"]["database_utc_date"],
                "quick_check": shard["quick_check"],
                "file_bytes": shard["file_bytes"],
            }
            for shard in shards
        ],
        "cycle_selection": selection,
        "health": {
            "all_quick_check_ok": all_quick,
            "run_events": dict(
                Counter(row["event_type"] for row in combined["research_run_events"])
            ),
            "selected_cycles": len(cycles),
            "selected_distinct_utc_dates": len(collection_dates),
            "critical_or_high_issues": critical_or_high,
            "issues": [
                {"severity": severity, "type": kind, "count": count}
                for (severity, kind), count in sorted(issue_counts.items())
            ],
            "gate_checks": health_gate_checks,
            "first_health_gate_passed": all(health_gate_checks.values()),
        },
        "sport_coverage": {
            "required_sports": list(FAMILY_ORDER),
            "by_sport": family_coverage,
            "missing_sports": missing_sports,
            "sport_equal_macro_public_book_coverage_pct": macro_coverage,
            "macro_public_book_coverage_pct_by_lifecycle_state": lifecycle_macro_coverage,
            "lifecycle_states_are_never_pooled": True,
            "macro_is_null_when_any_sport_is_missing": True,
        },
        "season_phase_contract": {
            "official_major_league_preseason_policy": "INCLUDED_AS_SEPARATE_STRATUM",
            "phases_are_never_pooled": True,
            "phases": list(SEASON_PHASES),
            "observed_preseason_unique_games_by_sport": {
                family: len(
                    {
                        str(row["event_cluster_id"])
                        for row in accepted_events
                        if row["sport_family"] == family
                        and row["season_phase"] == "PRESEASON"
                    }
                )
                for family in FAMILY_ORDER
            },
        },
        "lifecycle_health": lifecycle_health,
        "query_tag_accounting": query_tag_accounting,
        "schedule_window_accounting": schedule_accounting,
        "schedule_anchor_health": _anchor_report(anchors, outcomes),
        "liquidity_strata": _metric_strata(
            markets, "liquidity_num", (10_000, 50_000, 100_000)
        ),
        "volume_total_strata": _metric_strata(
            markets, "volume_num", (5_000, 25_000, 100_000)
        ),
        "volume_24hr_strata": _metric_strata(
            markets, "volume_24hr", (1_000, 10_000, 50_000)
        ),
        "notional_evidence_completeness": notional_completeness,
        "displayed_depth_ladder": depth,
        "threshold_state_strata_by_notional": thresholds,
        "event_clustering": {
            "unique_game_clusters": len(clusters),
            "markets_per_cluster_p50": _percentile(cluster_sizes, 0.50),
            "markets_per_cluster_p95": _percentile(cluster_sizes, 0.95),
            "soccer_clusters_missing_home_draw_away": soccer_incomplete,
            "us_clusters_with_non_direct_structure": us_invalid,
            "by_sport_and_phase": {
                family: {
                    phase: sum(
                        key[0] == family and key[1] == phase for key in clusters
                    )
                    for phase in SEASON_PHASES
                    if any(key[0] == family and key[1] == phase for key in clusters)
                }
                for family in FAMILY_ORDER
            },
        },
        "crossing_evidence": {
            "origin_episode_count": len({row["episode_id"] for row in episodes}),
            "left_and_gap_censored_are_not_episodes": True,
            "notional_and_threshold_rows_from_one_game_are_correlated": True,
        },
        "resolution_evidence": {
            "observations": len(resolutions),
            "statuses": dict(
                sorted(Counter(str(row["resolution_status"]) for row in resolutions).items())
            ),
        },
        "storage_growth": _storage_growth(shards),
        "selection_contract": {
            "liquidity_discovery_gate": None,
            "volume_discovery_gate": None,
            "best_sport": None,
            "best_threshold": None,
            "best_notional": None,
        },
        "interpretation": "HEALTH_AND_DISPLAYED_BOOK_RESEARCH_EVIDENCE_ONLY",
        "profitability_conclusion": None,
        "actual_execution_evidence": False,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def analyze_database(path: Path) -> dict[str, Any]:
    return analyze_databases([path])
