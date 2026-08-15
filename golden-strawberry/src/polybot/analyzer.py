"""Read-only Last Mile health, strata, and frozen policy-grid analyzer."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from statistics import mean, median
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote

from .config import (
    ENTRY_THRESHOLDS,
    PRIMARY_ENTRY_THRESHOLD,
    PRIMARY_STOP_THRESHOLD,
    STOP_THRESHOLDS,
    TARGET_THRESHOLDS,
)
from .db.repository import GIB
from .utils.retry import iso_utc


REQUIRED_TABLES = frozenset(
    {
        "schema_metadata",
        "experiment_contracts",
        "research_config_versions",
        "research_run_events",
        "api_requests",
        "raw_payloads",
        "gamma_sweeps",
        "gamma_membership_blobs",
        "gamma_page_lineage",
        "market_catalog_versions",
        "outcome_observations",
        "crossing_decisions",
        "clob_token_attempts",
        "clob_snapshots",
        "clob_levels",
        "hypothetical_episodes",
        "episode_path_observations",
        "resolution_observations",
        "data_quality_issues",
        "storage_metrics",
    }
)


def parse_utc(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError("analysis clock must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("analysis clock must be explicit UTC")
    return parsed.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_db(path: str | Path) -> Path:
    value = Path(path)
    if not value.is_absolute():
        raise ValueError("analysis database path must be absolute")
    if value.is_symlink() or not value.is_file():
        raise ValueError("analysis database must be a regular non-symlink file")
    return value.resolve(strict=True)


def _connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path))}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _expected_slots(
    start: datetime, end: datetime, cadence_minutes: int
) -> list[datetime]:
    epoch = int(start.timestamp())
    cadence_seconds = cadence_minutes * 60
    first_epoch = ((epoch + cadence_seconds - 1) // cadence_seconds) * cadence_seconds
    result: list[datetime] = []
    cursor = datetime.fromtimestamp(first_epoch, timezone.utc)
    while cursor < end:
        result.append(cursor)
        cursor += timedelta(minutes=cadence_minutes)
    return result


def _slot(value: datetime, cadence_minutes: int) -> tuple[datetime, float]:
    cadence_seconds = cadence_minutes * 60
    epoch = value.timestamp()
    slot_epoch = math.floor(epoch / cadence_seconds) * cadence_seconds
    slot = datetime.fromtimestamp(slot_epoch, timezone.utc)
    return slot, epoch - slot_epoch


def _terminal_runs(
    connection: sqlite3.Connection,
    start_text: str,
    end_text: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = connection.execute(
        """
        WITH scoped AS (
            SELECT DISTINCT run_id
            FROM research_run_events
            WHERE event_type='STARTED' AND event_at>=? AND event_at<?
        ), ranked AS (
            SELECT e.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY e.run_id ORDER BY e.event_at DESC,e.event_id DESC
                   ) AS position
            FROM research_run_events e JOIN scoped s ON s.run_id=e.run_id
        )
        SELECT * FROM ranked WHERE position=1
        ORDER BY event_at,run_id
        """,
        (start_text, end_text),
    ).fetchall()
    materialized = [dict(row) for row in rows]
    counts = Counter(str(row["event_type"]) for row in materialized)
    return materialized, dict(counts)


def _collection_health(
    connection: sqlite3.Connection,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    start_text, end_text = iso_utc(start), iso_utc(end)
    cadence_minutes = int(
        connection.execute(
            "SELECT cadence_minutes FROM experiment_contracts LIMIT 1"
        ).fetchone()[0]
    )
    terminals, terminal_counts = _terminal_runs(connection, start_text, end_text)
    success_ids = {
        str(row["run_id"]) for row in terminals if row["event_type"] == "SUCCEEDED"
    }
    started_rows = connection.execute(
        """
        SELECT run_id,event_at,config_hash,job_name,mode
        FROM research_run_events
        WHERE event_type='STARTED' AND event_at>=? AND event_at<?
        ORDER BY event_at
        """,
        (start_text, end_text),
    ).fetchall()
    slot_counts: Counter[str] = Counter()
    off_slot = 0
    success_slots: set[str] = set()
    for row in started_rows:
        observed = parse_utc(str(row["event_at"]))
        slot, delay = _slot(observed, cadence_minutes)
        key = iso_utc(slot)
        slot_counts[key] += 1
        if delay > 120:
            off_slot += 1
        if str(row["run_id"]) in success_ids:
            success_slots.add(key)
    expected = _expected_slots(start, end, cadence_minutes)
    duplicate_runs = sum(max(0, count - 1) for count in slot_counts.values())
    runtime_values = [
        float(row[0])
        for row in connection.execute(
            "SELECT runtime_seconds FROM cycle_stats WHERE completed_at>=? AND completed_at<?",
            (start_text, end_text),
        )
    ]
    sweep_counts = connection.execute(
        """
        SELECT COUNT(*) AS sweeps,
               SUM(CASE WHEN cursor_complete=1 THEN 1 ELSE 0 END) AS complete_sweeps
        FROM gamma_sweeps WHERE completed_at>=? AND completed_at<?
        """,
        (start_text, end_text),
    ).fetchone()
    membership_link = connection.execute(
        """
        SELECT COUNT(*) AS sweeps,
               SUM(CASE WHEN b.sweep_id IS NOT NULL
                         AND s.membership_sha256=b.membership_sha256
                        THEN 1 ELSE 0 END) AS linked
        FROM gamma_sweeps s
        LEFT JOIN gamma_membership_blobs b ON b.sweep_id=s.sweep_id
        WHERE s.completed_at>=? AND s.completed_at<?
        """,
        (start_text, end_text),
    ).fetchone()
    raw_link_rows = connection.execute(
        """
        SELECT 'gamma_page' AS kind,COUNT(*) AS total,
               SUM(CASE WHEN r.payload_id IS NOT NULL
                         AND r.payload_sha256=p.response_sha256
                        THEN 1 ELSE 0 END) AS linked
        FROM gamma_page_lineage p
        JOIN gamma_sweeps s ON s.sweep_id=p.sweep_id
        LEFT JOIN raw_payloads r ON r.request_id=p.request_id
        WHERE s.completed_at>=? AND s.completed_at<?
        UNION ALL
        SELECT 'clob_attempt',COUNT(*),
               SUM(CASE
                       WHEN a.status='ERROR' AND a.request_id IS NULL THEN 1
                       WHEN a.request_id IS NOT NULL AND r.payload_id IS NOT NULL THEN 1
                       ELSE 0
                   END)
        FROM clob_token_attempts a
        JOIN gamma_sweeps s ON s.sweep_id=a.sweep_id
        LEFT JOIN raw_payloads r ON r.request_id=a.request_id
        WHERE s.completed_at>=? AND s.completed_at<?
        UNION ALL
        SELECT 'resolution',COUNT(*),
               SUM(CASE WHEN o.request_id IS NULL OR r.payload_id IS NOT NULL
                        THEN 1 ELSE 0 END)
        FROM resolution_observations o
        JOIN gamma_sweeps s ON s.sweep_id=o.sweep_id
        LEFT JOIN raw_payloads r ON r.request_id=o.request_id
        WHERE s.completed_at>=? AND s.completed_at<?
        """,
        (start_text, end_text) * 3,
    ).fetchall()
    raw_linkage: dict[str, Any] = {}
    raw_total = 0
    raw_linked = 0
    for row in raw_link_rows:
        total = int(row["total"] or 0)
        linked = int(row["linked"] or 0)
        raw_total += total
        raw_linked += linked
        raw_linkage[str(row["kind"])] = {
            "total": total,
            "linked": linked,
            "coverage": _ratio(linked, total),
        }
    issue_rows = connection.execute(
        """
        SELECT severity,COUNT(*) AS count FROM data_quality_issues
        WHERE recorded_at>=? AND recorded_at<? GROUP BY severity
        """,
        (start_text, end_text),
    ).fetchall()
    issue_counts = {str(row["severity"]): int(row["count"]) for row in issue_rows}
    cohorts = [
        dict(row)
        for row in connection.execute(
            """
            SELECT e.config_hash,c.strategy_source_digest,e.mode,e.job_name,
                   COUNT(DISTINCT e.run_id) AS run_count
            FROM research_run_events e
            JOIN research_config_versions c ON c.config_hash=e.config_hash
            WHERE e.event_type='STARTED' AND e.event_at>=? AND e.event_at<?
            GROUP BY e.config_hash,c.strategy_source_digest,e.mode,e.job_name
            """,
            (start_text, end_text),
        )
    ]
    success_coverage = _ratio(len(success_slots), len(expected))
    cursor_coverage = _ratio(
        int(sweep_counts["complete_sweeps"] or 0), int(sweep_counts["sweeps"] or 0)
    )
    membership_coverage = _ratio(
        int(membership_link["linked"] or 0), int(membership_link["sweeps"] or 0)
    )
    p95 = _percentile(runtime_values, 0.95)
    maximum = max(runtime_values) if runtime_values else None
    raw_coverage = _ratio(raw_linked, raw_total)
    off_slot_ratio = _ratio(off_slot, len(started_rows))
    healthy = bool(
        success_coverage is not None
        and success_coverage >= 0.90
        and cursor_coverage == 1.0
        and membership_coverage == 1.0
        and raw_coverage == 1.0
        and duplicate_runs == 0
        and (off_slot_ratio is None or off_slot_ratio <= 0.05)
        and p95 is not None
        and p95 < cadence_minutes * 60 * 0.8
        and maximum is not None
        and maximum < cadence_minutes * 60
        and issue_counts.get("HIGH", 0) == 0
        and issue_counts.get("CRITICAL", 0) == 0
        and len(cohorts) == 1
    )
    return {
        "healthy": healthy,
        "cadence_minutes": cadence_minutes,
        "expected_slots": len(expected),
        "successful_unique_slots": len(success_slots),
        "success_coverage": success_coverage,
        "terminal_run_counts": terminal_counts,
        "duplicate_runs": duplicate_runs,
        "off_slot_runs": off_slot,
        "off_slot_ratio": off_slot_ratio,
        "runtime_seconds": {
            "count": len(runtime_values),
            "p95": p95,
            "max": maximum,
        },
        "cursor_complete_coverage": cursor_coverage,
        "membership_blob_coverage": membership_coverage,
        "raw_linkage": {
            "overall": _ratio(raw_linked, raw_total),
            "by_kind": raw_linkage,
        },
        "quality_issue_counts": issue_counts,
        "cohorts": cohorts,
    }


def _storage_report(
    connection: sqlite3.Connection,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT * FROM storage_metrics
            WHERE recorded_at>=? AND recorded_at<?
            ORDER BY recorded_at,metric_id
            """,
            (iso_utc(start), iso_utc(end)),
        )
    ]
    if not rows:
        return {
            "observations": 0,
            "actual_growth_bytes_per_day": None,
            "forecast_days_to_guard_stop": None,
        }
    first, last = rows[0], rows[-1]
    elapsed_days = max(
        (
            parse_utc(str(last["recorded_at"])) - parse_utc(str(first["recorded_at"]))
        ).total_seconds()
        / 86400,
        0,
    )
    growth = max(0, int(last["db_bytes"]) - int(first["db_bytes"]))
    growth_per_day = growth / elapsed_days if elapsed_days > 0 else None
    ratio_headroom = max(
        0.0,
        0.90 * int(last["filesystem_total_bytes"]) - int(last["filesystem_used_bytes"]),
    )
    free_headroom = max(0.0, int(last["filesystem_free_bytes"]) - 30 * GIB)
    headroom = min(ratio_headroom, free_headroom)
    forecast = (
        headroom / growth_per_day
        if growth_per_day is not None and growth_per_day > 0
        else None
    )
    return {
        "observations": len(rows),
        "first_recorded_at": first["recorded_at"],
        "last_recorded_at": last["recorded_at"],
        "first_db_bytes": first["db_bytes"],
        "last_db_bytes": last["db_bytes"],
        "actual_growth_bytes": growth,
        "actual_growth_bytes_per_day": growth_per_day,
        "guard_headroom_bytes": headroom,
        "forecast_days_to_guard_stop": forecast,
        "latest_filesystem_free_bytes": last["filesystem_free_bytes"],
        "latest_filesystem_used_ratio": last["filesystem_used_ratio"],
        "latest_guard_state": last["guard_state"],
        "dated_design_estimate_is_not_contract": True,
    }


def _bucket(value: Any, boundaries: Sequence[float]) -> str:
    if value is None:
        return "MISSING"
    number = float(value)
    lower = 0.0
    for boundary in boundaries:
        if number < boundary:
            return f"[{lower:g},{boundary:g})"
        lower = boundary
    return f"[{lower:g},inf)"


def _episode_scope(
    connection: sqlite3.Connection,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT * FROM hypothetical_episodes
            WHERE entry_observed_at>=? AND entry_observed_at<?
            ORDER BY entry_observed_at,episode_id
            """,
            (iso_utc(start), iso_utc(end)),
        )
    ]


def _resolution_by_condition(
    connection: sqlite3.Connection,
    end: datetime,
) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
        WITH ranked AS (
            SELECT r.*,s.cycle_number,
                   ROW_NUMBER() OVER (
                       PARTITION BY r.condition_id
                       ORDER BY s.cycle_number,r.observed_at,r.resolution_observation_id
                   ) AS position
            FROM resolution_observations r
            JOIN gamma_sweeps s ON s.sweep_id=r.sweep_id
            WHERE r.resolution_status='RESOLVED' AND r.observed_at<?
        ) SELECT * FROM ranked WHERE position=1
        """,
        (iso_utc(end),),
    ).fetchall()
    return {str(row["condition_id"]): dict(row) for row in rows}


def _coverage_report(
    connection: sqlite3.Connection,
    episodes: Sequence[Mapping[str, Any]],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    executable = [row for row in episodes if row["entry_status"] == "EXECUTABLE"]
    episode_ids = {str(row["episode_id"]) for row in executable}
    resolutions = _resolution_by_condition(connection, end)
    sweeps = [
        dict(row)
        for row in connection.execute(
            """
            SELECT sweep_id,cycle_number,completed_at FROM gamma_sweeps
            WHERE completed_at>=? AND completed_at<? ORDER BY cycle_number
            """,
            (iso_utc(start), iso_utc(end)),
        )
    ]
    path_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT p.*,s.cycle_number
            FROM episode_path_observations p
            JOIN gamma_sweeps s ON s.sweep_id=p.sweep_id
            WHERE p.observed_at>=? AND p.observed_at<?
            """,
            (iso_utc(start), iso_utc(end)),
        )
        if str(row["episode_id"]) in episode_ids
    ]
    actual_pairs = {(str(row["episode_id"]), str(row["sweep_id"])) for row in path_rows}
    sweep_cycle = {str(row["sweep_id"]): int(row["cycle_number"]) for row in sweeps}
    expected_pairs: set[tuple[str, str]] = set()
    for episode in executable:
        origin_cycle = sweep_cycle.get(str(episode["originating_sweep_id"]))
        if origin_cycle is None:
            origin = connection.execute(
                "SELECT cycle_number FROM gamma_sweeps WHERE sweep_id=?",
                (episode["originating_sweep_id"],),
            ).fetchone()
            origin_cycle = int(origin[0]) if origin else None
        resolution = resolutions.get(str(episode["condition_id"]))
        resolution_cycle = int(resolution["cycle_number"]) if resolution else None
        for sweep in sweeps:
            cycle = int(sweep["cycle_number"])
            if origin_cycle is not None and cycle < origin_cycle:
                continue
            if resolution_cycle is not None and cycle > resolution_cycle:
                continue
            expected_pairs.add((str(episode["episode_id"]), str(sweep["sweep_id"])))
    linked_paths = len(expected_pairs & actual_pairs)
    resolved_episodes = [
        row for row in executable if str(row["condition_id"]) in resolutions
    ]
    crossing_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM crossing_decisions "
            "WHERE decision_status='NEW_CROSSING' AND decided_at>=? AND decided_at<?",
            (iso_utc(start), iso_utc(end)),
        ).fetchone()[0]
    )
    crossing_with_attempt = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM crossing_decisions d
            WHERE d.decision_status='NEW_CROSSING'
              AND d.decided_at>=? AND d.decided_at<?
              AND EXISTS (
                  SELECT 1 FROM clob_token_attempts a
                  WHERE a.sweep_id=d.sweep_id AND a.token_id=d.token_id
              )
            """,
            (iso_utc(start), iso_utc(end)),
        ).fetchone()[0]
    )
    return {
        "new_crossings": crossing_count,
        "crossings_with_clob_attempt": crossing_with_attempt,
        "crossing_clob_coverage": _ratio(crossing_with_attempt, crossing_count),
        "executable_episodes": len(executable),
        "expected_episode_path_observations": len(expected_pairs),
        "observed_episode_path_observations": linked_paths,
        "episode_path_coverage": _ratio(linked_paths, len(expected_pairs)),
        "resolved_executable_episodes": len(resolved_episodes),
        "resolution_coverage": _ratio(len(resolved_episodes), len(executable)),
        "resolved_independent_event_clusters": len(
            {str(row["event_cluster_id"]) for row in resolved_episodes}
        ),
        "path_status_counts": dict(
            Counter(str(row["path_status"]) for row in path_rows)
        ),
    }


def _stratification(
    connection: sqlite3.Connection,
    episodes: Sequence[Mapping[str, Any]],
    end: datetime,
) -> dict[str, Any]:
    resolutions = _resolution_by_condition(connection, end)
    by_entry = Counter(f"{float(row['entry_threshold']):.2f}" for row in episodes)
    by_sports = Counter(str(row["sports_classification"]) for row in episodes)
    by_type = Counter(str(row["outcome_type"]) for row in episodes)
    by_neg = Counter("NEG_RISK" if row["neg_risk"] else "STANDARD" for row in episodes)
    liquidity = Counter(
        _bucket(row["liquidity"], (1_000, 10_000, 100_000)) for row in episodes
    )
    volume_total = Counter(
        _bucket(row["volume_total"], (1_000, 10_000, 100_000, 1_000_000))
        for row in episodes
    )
    volume_24h = Counter(
        _bucket(row["volume_24h"], (100, 1_000, 10_000, 100_000)) for row in episodes
    )
    resolution_outcomes: Counter[str] = Counter()
    for episode in episodes:
        resolution = resolutions.get(str(episode["condition_id"]))
        if resolution is None:
            resolution_outcomes["UNRESOLVED_OR_CENSORED"] += 1
            continue
        payouts = json.loads(str(resolution["token_payouts_json"]))
        payout = payouts.get(str(episode["token_id"]))
        resolution_outcomes[f"PAYOUT_{payout}"] += 1
    episode_ids = {str(row["episode_id"]) for row in episodes}
    threshold_rows = [
        row
        for row in connection.execute(
            """
        SELECT episode_id,event_kind,threshold
        FROM episode_threshold_events
        WHERE observed_at<?
        """,
            (iso_utc(end),),
        )
        if str(row["episode_id"]) in episode_ids
    ]
    path_thresholds = dict(
        Counter(
            f"{row['event_kind']}_{float(row['threshold']):.2f}"
            for row in threshold_rows
        )
    )
    source_metadata = {
        "executable_episodes": sum(
            1 for row in episodes if row["entry_status"] == "EXECUTABLE"
        ),
        "tick_size_present": sum(
            row["source_tick_size"] is not None for row in episodes
        ),
        "min_order_size_present": sum(
            row["source_min_order_size"] is not None for row in episodes
        ),
        "fee_rate_bps_present": sum(
            row["source_fee_rate_bps"] is not None for row in episodes
        ),
        "fee_metadata_is_source_evidence_not_assumed_exact_fee": True,
    }
    return {
        "entry_threshold": dict(sorted(by_entry.items())),
        "sports": dict(sorted(by_sports.items())),
        "outcome_type": dict(sorted(by_type.items())),
        "neg_risk": dict(sorted(by_neg.items())),
        "liquidity_bucket": dict(sorted(liquidity.items())),
        "volume_total_bucket": dict(sorted(volume_total.items())),
        "volume_24h_bucket": dict(sorted(volume_24h.items())),
        "resolution_outcome": dict(sorted(resolution_outcomes.items())),
        "first_observed_path_thresholds": path_thresholds,
        "crossing_source_metadata": source_metadata,
    }


def _mean_or_none(values: Iterable[float]) -> float | None:
    materialized = list(values)
    return mean(materialized) if materialized else None


def _policy_grid(
    connection: sqlite3.Connection,
    episodes: Sequence[Mapping[str, Any]],
    end: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    executable = [row for row in episodes if row["entry_status"] == "EXECUTABLE"]
    episode_ids = {str(row["episode_id"]) for row in executable}
    path_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """
        SELECT p.*,s.cycle_number
        FROM episode_path_observations p
        JOIN gamma_sweeps s ON s.sweep_id=p.sweep_id
        WHERE p.path_status='EXECUTABLE' AND p.entry_cycle_baseline=0
          AND p.observed_at<?
        ORDER BY s.cycle_number,p.observed_at,p.path_observation_id
        """,
        (iso_utc(end),),
    ):
        if str(row["episode_id"]) in episode_ids:
            path_rows[str(row["episode_id"])].append(dict(row))
    resolutions = _resolution_by_condition(connection, end)
    policy_results: dict[
        tuple[float, float | None, float | None], list[dict[str, Any]]
    ] = defaultdict(list)
    for entry_threshold in ENTRY_THRESHOLDS:
        for stop in [None] + [
            value for value in STOP_THRESHOLDS if value < entry_threshold
        ]:
            for target in [None] + [
                value for value in TARGET_THRESHOLDS if value > entry_threshold
            ]:
                policy_results[(entry_threshold, stop, target)]

    for episode in executable:
        entry_threshold = float(episode["entry_threshold"])
        stop_options: list[float | None] = [None] + [
            value for value in STOP_THRESHOLDS if value < entry_threshold
        ]
        target_options: list[float | None] = [None] + [
            value for value in TARGET_THRESHOLDS if value > entry_threshold
        ]
        resolution = resolutions.get(str(episode["condition_id"]))
        for stop in stop_options:
            for target in target_options:
                candidates: list[dict[str, Any]] = []
                for path in path_rows.get(str(episode["episode_id"]), []):
                    value = float(path["exit_bid_vwap"])
                    if stop is not None and value <= stop:
                        candidates.append(
                            {
                                "cycle": int(path["cycle_number"]),
                                "priority": 0,
                                "observed_at": path["observed_at"],
                                "reason": "STOP",
                                "proceeds": float(path["exit_proceeds_usdc"]),
                                "sweep_id": path["sweep_id"],
                            }
                        )
                    if target is not None and value >= target:
                        candidates.append(
                            {
                                "cycle": int(path["cycle_number"]),
                                "priority": 1,
                                "observed_at": path["observed_at"],
                                "reason": "TARGET",
                                "proceeds": float(path["exit_proceeds_usdc"]),
                                "sweep_id": path["sweep_id"],
                            }
                        )
                if resolution is not None:
                    payouts = json.loads(str(resolution["token_payouts_json"]))
                    payout = payouts.get(str(episode["token_id"]))
                    if payout in {0, 1}:
                        candidates.append(
                            {
                                "cycle": int(resolution["cycle_number"]),
                                "priority": 2,
                                "observed_at": resolution["observed_at"],
                                "reason": "TERMINAL_RESOLUTION",
                                "proceeds": float(payout)
                                * float(episode["fixed_shares"]),
                                "sweep_id": resolution["sweep_id"],
                            }
                        )
                ambiguous = False
                if resolution is not None and stop is not None:
                    ambiguous = any(
                        path["sweep_id"] == resolution["sweep_id"]
                        and float(path["exit_bid_vwap"]) <= stop
                        for path in path_rows.get(str(episode["episode_id"]), [])
                    )
                if candidates:
                    # Cycle is the poll clock. Priority enforces conservative
                    # stop-before-target-before-resolution within one poll.
                    chosen = min(
                        candidates,
                        key=lambda row: (
                            row["cycle"],
                            row["priority"],
                            row["observed_at"],
                        ),
                    )
                    gross_bps = (
                        (
                            float(chosen["proceeds"])
                            - float(episode["entry_notional_usdc"])
                        )
                        / float(episode["entry_notional_usdc"])
                        * 10_000
                    )
                    result = {
                        "complete": True,
                        "exit_reason": chosen["reason"],
                        "gross_bps": gross_bps,
                        "base_stressed_bps": gross_bps - 10.4,
                        "severe_stressed_bps": gross_bps - 72.5,
                        "same_poll_stop_resolution_ambiguous": ambiguous,
                    }
                else:
                    result = {
                        "complete": False,
                        "exit_reason": "CENSORED_NO_TERMINAL_EVIDENCE",
                        "gross_bps": None,
                        "base_stressed_bps": None,
                        "severe_stressed_bps": None,
                        "same_poll_stop_resolution_ambiguous": False,
                    }
                policy_results[(entry_threshold, stop, target)].append(result)

    grid: list[dict[str, Any]] = []
    primary: dict[str, Any] | None = None
    for key in sorted(
        policy_results,
        key=lambda value: (
            value[0],
            -1 if value[1] is None else value[1],
            -1 if value[2] is None else value[2],
        ),
    ):
        entry, stop, target = key
        rows = policy_results[key]
        complete = [row for row in rows if row["complete"]]
        item = {
            "entry_threshold": entry,
            "stop_threshold": stop,
            "target_threshold": target,
            "policy_role": (
                "PRIMARY"
                if entry == PRIMARY_ENTRY_THRESHOLD
                and stop == PRIMARY_STOP_THRESHOLD
                and target is None
                else "SENSITIVITY_ONLY"
            ),
            "episode_count": len(rows),
            "complete_count": len(complete),
            "censored_count": len(rows) - len(complete),
            "exit_reason_counts": dict(Counter(row["exit_reason"] for row in rows)),
            "same_poll_stop_resolution_ambiguous_count": sum(
                bool(row["same_poll_stop_resolution_ambiguous"]) for row in rows
            ),
            "gross_counterfactual_bps": {
                "mean": _mean_or_none(float(row["gross_bps"]) for row in complete),
                "median": (
                    median(float(row["gross_bps"]) for row in complete)
                    if complete
                    else None
                ),
            },
            "round_trip_cost_stress_bps": {
                "10.4": _mean_or_none(
                    float(row["base_stressed_bps"]) for row in complete
                ),
                "72.5": _mean_or_none(
                    float(row["severe_stressed_bps"]) for row in complete
                ),
                "stress_is_not_exact_fee_claim": True,
            },
            "hypothetical_displayed_book_counterfactual": True,
            "censored_cases_are_not_completed": True,
        }
        grid.append(item)
        if item["policy_role"] == "PRIMARY":
            primary = item
    return grid, primary


def analyze_database(
    db_path: str | Path,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("analysis range must be timezone-aware")
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if start >= end:
        raise ValueError("analysis range must satisfy start < end")
    path = _canonical_db(db_path)
    connection = _connect(path)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise ValueError(
                "database is missing Last Mile tables: " + ", ".join(missing)
            )
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        contract = dict(
            connection.execute("SELECT * FROM experiment_contracts").fetchone()
        )
        health = _collection_health(connection, start, end)
        storage = _storage_report(connection, start, end)
        episodes = _episode_scope(connection, start, end)
        coverage = _coverage_report(connection, episodes, start, end)
        censoring_rows = connection.execute(
            """
            SELECT decision_status,COUNT(*) AS count FROM crossing_decisions
            WHERE decided_at>=? AND decided_at<?
              AND decision_status IN ('LEFT_CENSORED','GAP_CENSORED')
            GROUP BY decision_status
            """,
            (iso_utc(start), iso_utc(end)),
        ).fetchall()
        censoring = {
            str(row["decision_status"]): int(row["count"]) for row in censoring_rows
        }
        strata = _stratification(connection, episodes, end)
        grid, primary = _policy_grid(connection, episodes, end)
    finally:
        connection.close()

    candidate_gates = {
        "executable_episodes_at_least_50": coverage["executable_episodes"] >= 50,
        "resolved_independent_event_clusters_at_least_30": (
            coverage["resolved_independent_event_clusters"] >= 30
        ),
        "episode_path_coverage_at_least_90pct": (
            coverage["episode_path_coverage"] is not None
            and coverage["episode_path_coverage"] >= 0.90
        ),
        "resolution_coverage_at_least_90pct": (
            coverage["resolution_coverage"] is not None
            and coverage["resolution_coverage"] >= 0.90
        ),
    }
    if quick_check != "ok" or not health["healthy"]:
        verdict = "HEALTH_ONLY"
    elif not all(candidate_gates.values()):
        verdict = "PILOT_UNDERPOWERED"
    else:
        verdict = "PILOT_CANDIDATE"
    return {
        "schema": "golden-strawberry-analysis-v1",
        "generated_at": iso_utc(),
        "database": {
            "path": str(path),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "opened_read_only_immutable": True,
            "quick_check": quick_check,
        },
        "review_range": {
            "start": iso_utc(start),
            "end_exclusive": iso_utc(end),
            "timezone": "UTC",
            "duration_days": (end - start).total_seconds() / 86400,
        },
        "contract": contract,
        "collection_health": health,
        "storage_growth_and_forecast": storage,
        "crossing_episode_resolution_coverage": coverage,
        "crossing_censoring": {
            "left_censored": censoring.get("LEFT_CENSORED", 0),
            "gap_censored": censoring.get("GAP_CENSORED", 0),
            "continuous_threshold_passage_asserted": False,
        },
        "stratified_counts": strata,
        "policy_grid": grid,
        "frozen_primary_policy": {
            "entry_threshold": PRIMARY_ENTRY_THRESHOLD,
            "stop_threshold": PRIMARY_STOP_THRESHOLD,
            "target_threshold": None,
            "otherwise_exit": "PROVEN_TERMINAL_RESOLUTION",
            "summary": primary,
        },
        "interpretation": {
            "verdict": verdict,
            "candidate_gates": candidate_gates,
            "maximum_possible_label": "PILOT_CANDIDATE",
            "profitability_claim_allowed": False,
            "parameter_winner_selection_allowed": False,
            "same_week_parameter_selection_requires_new_cohort": True,
            "frozen_30_day_oos_confirmation_required": True,
            "target_0_99_is_resolution": False,
            "same_poll_stop_resolution_ordering": "AMBIGUOUS_CONSERVATIVE_STOP_FIRST",
            "all_results_hypothetical_and_censored": True,
        },
    }


def write_analysis(
    db_path: str | Path,
    *,
    start: datetime,
    end: datetime,
    output: str | Path,
) -> dict[str, Any]:
    result = analyze_database(db_path, start=start, end=end)
    destination = Path(output)
    if not destination.is_absolute():
        raise ValueError("analysis output path must be absolute")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


__all__ = ["analyze_database", "parse_utc", "write_analysis"]
