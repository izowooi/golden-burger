#!/usr/bin/env python3
"""Read-only health and preregistered Queue Echo experiment analyzer."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import statistics
from typing import Any, Iterable


ANALYZER_VERSION = "queue-echo-analyzer-v3"
LEGACY_ANALYZER_VERSION = "queue-echo-analyzer-v1"
FROZEN_WINDOW_START = "2026-08-23T20:00:00Z"
FROZEN_WINDOW_END = "2026-09-22T20:00:00Z"
EXPECTED_RUNTIME_IDENTITIES = {
    "raspberry-do-v3-shard-0": (0, 0),
    "raspberry-re-v3-shard-1": (1, 1),
    "raspberry-mi-v3-shard-2": (2, 2),
}
BOOTSTRAP_SEED = 20260813
BOOTSTRAP_DRAWS = 20_000
REQUIRED_TABLES = {
    "experiment_contracts",
    "research_config_versions",
    "research_run_events",
    "cycle_slot_claims",
    "cycle_slot_events",
    "api_requests",
    "market_sweeps",
    "market_observations",
    "raw_payloads",
    "orderbook_token_attempts",
    "orderbook_snapshots",
    "signal_decisions",
    "research_cases",
    "followup_claims",
    "followup_claim_leases",
    "followup_request_starts",
    "followup_attempts",
    "cycle_stats",
    "data_quality_issues",
}


def _utc(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_db(value: str) -> Path:
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        raise ValueError("DB paths must be absolute")
    canonical = Path(str(requested.absolute()))
    resolved = requested.resolve(strict=True)
    if requested != canonical or resolved != canonical or resolved.is_symlink():
        raise ValueError("DB path must be canonical and must not traverse a symlink")
    if not resolved.is_file():
        raise ValueError("DB path must be a regular file")
    return resolved


def _connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _expected_slots(start: datetime, end: datetime, offset: int) -> list[datetime]:
    cursor = start.replace(second=0, microsecond=0)
    if cursor < start:
        cursor += timedelta(minutes=1)
    result: list[datetime] = []
    while cursor < end:
        if cursor.minute % 5 == offset:
            result.append(cursor)
        cursor += timedelta(minutes=1)
    return result


def _cadence(
    claims: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    successful_run_ids: set[str],
    started_run_ids: set[str],
    start: datetime,
    end: datetime,
    offset: int,
) -> dict[str, Any]:
    expected = _expected_slots(start, end, offset)
    expected_set = {_iso(slot) for slot in expected}
    accepted = [row for row in claims if row["disposition"] == "CLAIMED"]
    matched = {
        str(row["slot_at"])
        for row in accepted
        if str(row.get("owner_run_id")) in successful_run_ids
        and str(row["slot_at"]) in expected_set
    }
    accepted_owners = {
        str(row["owner_run_id"])
        for row in accepted
        if row.get("owner_run_id") is not None
    }
    invalid_slots = [
        row
        for row in claims
        if str(row["slot_at"]) not in expected_set
        or _utc(str(row["slot_at"])).minute % 5 != offset
    ]
    duplicate_events = sum(
        row["event_type"] == "SKIPPED_DUPLICATE" for row in events
    )
    late_events = sum(row["event_type"] == "SKIPPED_LATE" for row in events)
    coverage = len(matched) / len(expected) if expected else 1.0
    return {
        "expected_slots": len(expected),
        "matched_slots": len(matched),
        "coverage": coverage,
        "missing_slots": len(expected) - len(matched),
        "claimed_slots": len(accepted),
        "duplicate_slots": duplicate_events,
        "late_invocations": late_events,
        "invalid_slot_claims": len(invalid_slots),
        "started_without_claim": len(started_run_ids - accepted_owners),
        "claimed_without_started": len(accepted_owners - started_run_ids),
    }


def _mean(values: Iterable[float]) -> float | None:
    materialized = list(values)
    return statistics.fmean(materialized) if materialized else None


def _cluster_lower_bound(
    rows: list[tuple[str, float]], confidence: float
) -> float | None:
    groups: dict[str, list[float]] = defaultdict(list)
    for cluster, value in rows:
        groups[cluster].append(value)
    keys = sorted(groups)
    if len(keys) < 2:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_DRAWS):
        sampled = [keys[rng.randrange(len(keys))] for _ in keys]
        values = [value for key in sampled for value in groups[key]]
        draws.append(statistics.fmean(values))
    draws.sort()
    index = max(0, min(len(draws) - 1, int((1 - confidence) * len(draws))))
    return draws[index]


def _terminal_runs(
    connection: sqlite3.Connection, start: datetime, end: datetime
) -> tuple[set[str], set[str], dict[str, Any]]:
    """Own a run by STARTED timestamp, then inspect its terminal anywhere in the DB."""

    start_s, end_s = _iso(start), _iso(end)
    starts = connection.execute(
        """
        SELECT rowid, run_id, event_at, details_json
        FROM research_run_events
        WHERE event_type='STARTED' AND event_at>=? AND event_at<?
        ORDER BY event_at, rowid
        """,
        (start_s, end_s),
    ).fetchall()
    started_run_ids = {str(row["run_id"]) for row in starts}
    success: set[str] = set()
    failed: set[str] = set()
    malformed: dict[str, list[str]] = {}
    terminal_durations: list[float] = []
    failed_durations: list[float] = []
    cooperative_breaches = 0
    hard_breaches = 0
    for start_row in starts:
        run_id = str(start_row["run_id"])
        rows = connection.execute(
            """
            SELECT rowid, event_type, event_at, details_json
            FROM research_run_events WHERE run_id=? ORDER BY rowid
            """,
            (run_id,),
        ).fetchall()
        sequence = [str(row["event_type"]) for row in rows]
        if sequence not in (["STARTED", "SUCCEEDED"], ["STARTED", "FAILED"]):
            malformed[run_id] = sequence
            continue
        terminal = rows[-1]
        try:
            details = json.loads(str(terminal["details_json"]))
        except (TypeError, json.JSONDecodeError):
            details = {}
        duration = details.get("duration_seconds")
        if not isinstance(duration, (int, float)) or not math.isfinite(float(duration)):
            duration = (
                _utc(str(terminal["event_at"]))
                - _utc(str(start_row["event_at"]))
            ).total_seconds()
        duration = max(0.0, float(duration))
        terminal_durations.append(duration)
        cooperative = float(details.get("cooperative_cycle_budget_seconds", 225))
        hard = float(details.get("hard_cycle_limit_seconds", 240))
        cooperative_breaches += duration >= cooperative
        hard_breaches += duration >= hard
        if sequence[-1] == "SUCCEEDED":
            success.add(run_id)
        else:
            failed.add(run_id)
            failed_durations.append(duration)
    outside_owned_terminals = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM research_run_events t
            WHERE t.event_type IN ('SUCCEEDED','FAILED')
              AND t.event_at>=? AND t.event_at<?
              AND NOT EXISTS (
                SELECT 1 FROM research_run_events s
                WHERE s.run_id=t.run_id AND s.event_type='STARTED'
                  AND s.event_at>=? AND s.event_at<?
              )
            """,
            (start_s, end_s, start_s, end_s),
        ).fetchone()[0]
    )
    return success, started_run_ids, {
        "ownership": "STARTED_IN_REVIEW_RANGE",
        "total_runs": len(starts),
        "successful_runs": len(success),
        "failed_runs": len(failed),
        "failed_run_ids": sorted(failed),
        "malformed_lifecycle_count": len(malformed),
        "malformed_run_ids_sha256": hashlib.sha256(
            json.dumps(sorted(malformed)).encode()
        ).hexdigest(),
        "terminal_durations_seconds": terminal_durations,
        "failed_terminal_durations_seconds": failed_durations,
        "cooperative_deadline_breaches": cooperative_breaches,
        "hard_limit_breaches": hard_breaches,
        "terminal_events_in_range_owned_elsewhere": outside_owned_terminals,
    }


def _outcome_rows(
    connection: sqlite3.Connection,
    start: str,
    end: str,
    *,
    namespace: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        WITH first_attempt AS (
            SELECT f.case_id, f.status, f.executable_return_bps, f.base_stressed_return_bps,
                   f.severe_stressed_return_bps,
                   ROW_NUMBER() OVER (PARTITION BY f.case_id ORDER BY f.attempted_at, f.followup_id) AS rn
            FROM followup_attempts f
        )
        SELECT d.arm, d.condition_id, d.event_id, d.evaluated_at,
               c.case_kind, c.matched_pair_id,
               c.case_id, o.status AS followup_status,
               o.executable_return_bps, o.base_stressed_return_bps,
               o.severe_stressed_return_bps
        FROM signal_decisions d
        JOIN research_cases c ON c.decision_id=d.decision_id
        LEFT JOIN first_attempt o ON o.case_id=c.case_id AND o.rn=1
        WHERE d.qualified=1 AND d.evaluated_at>=? AND d.evaluated_at<?
        ORDER BY d.evaluated_at, d.decision_id, c.case_kind
        """,
        (start, end),
    ).fetchall()
    return [
        {
            **dict(row),
            "namespace": namespace,
            "cluster_key": f"{namespace}:{row['event_id']}",
            "pair_key": f"{namespace}:{row['matched_pair_id']}",
        }
        for row in rows
    ]


def _summarize_outcomes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm: dict[str, dict[str, Any]] = {}
    for arm in ("DO", "RE", "MI"):
        arm_rows = [row for row in rows if row["arm"] == arm]
        signal = [row for row in arm_rows if row["case_kind"] == "SIGNAL"]
        complete = [
            row
            for row in signal
            if row.get("followup_status") in {None, "QUOTE_COMPLETE"}
            and row["executable_return_bps"] is not None
        ]
        raw_pairs = [
            (str(row["cluster_key"]), float(row["executable_return_bps"]))
            for row in complete
        ]
        base_pairs = [
            (str(row["cluster_key"]), float(row["base_stressed_return_bps"]))
            for row in complete
            if row["base_stressed_return_bps"] is not None
        ]
        severe_pairs = [
            (str(row["cluster_key"]), float(row["severe_stressed_return_bps"]))
            for row in complete
            if row["severe_stressed_return_bps"] is not None
        ]
        days = {str(row["evaluated_at"])[:10] for row in complete}
        sorted_complete = sorted(complete, key=lambda row: str(row["evaluated_at"]))
        half = len(sorted_complete) // 2
        early = [
            row
            for row in sorted_complete[:half]
            if row["severe_stressed_return_bps"] is not None
        ]
        late = [
            row
            for row in sorted_complete[half:]
            if row["severe_stressed_return_bps"] is not None
        ]
        by_kind_pair: dict[tuple[str, str], float] = {}
        for row in arm_rows:
            if (
                row.get("followup_status") in {None, "QUOTE_COMPLETE"}
                and row["executable_return_bps"] is not None
            ):
                by_kind_pair[(str(row["pair_key"]), str(row["case_kind"]))] = float(
                    row["executable_return_bps"]
                )
        control_diffs: list[tuple[str, float]] = []
        opposite_diffs: list[tuple[str, float]] = []
        event_by_pair = {
            str(row["pair_key"]): str(row["cluster_key"]) for row in signal
        }
        for pair_id, cluster_key in event_by_pair.items():
            signal_value = by_kind_pair.get((pair_id, "SIGNAL"))
            if signal_value is None:
                continue
            control = by_kind_pair.get((pair_id, "CONTROL"))
            opposite = by_kind_pair.get((pair_id, "OPPOSITE"))
            if control is not None:
                control_diffs.append((cluster_key, signal_value - control))
            if opposite is not None:
                opposite_diffs.append((cluster_key, signal_value - opposite))
        by_arm[arm] = {
            "qualified_signal_cases": len(signal),
            "quote_complete_signals": len(complete),
            "outcome_coverage": len(complete) / len(signal) if signal else None,
            "event_clusters": len({cluster for cluster, _ in raw_pairs}),
            "distinct_utc_days": len(days),
            "mean_executable_return_bps": _mean(value for _, value in raw_pairs),
            "mean_base_stressed_return_bps": _mean(value for _, value in base_pairs),
            "mean_severe_stressed_return_bps": _mean(value for _, value in severe_pairs),
            "cluster_lower_98_33_raw_bps": _cluster_lower_bound(raw_pairs, 0.9833),
            "cluster_lower_98_33_base_bps": _cluster_lower_bound(base_pairs, 0.9833),
            "cluster_lower_98_33_severe_bps": _cluster_lower_bound(severe_pairs, 0.9833),
            "early_half_mean_bps": _mean(float(row["severe_stressed_return_bps"]) for row in early),
            "late_half_mean_bps": _mean(float(row["severe_stressed_return_bps"]) for row in late),
            "neutral_pair_coverage": len(control_diffs) / len(complete) if complete else None,
            "mean_signal_minus_neutral_bps": _mean(value for _, value in control_diffs),
            "neutral_diff_lower_95_bps": _cluster_lower_bound(control_diffs, 0.95),
            "mean_signal_minus_opposite_bps": _mean(value for _, value in opposite_diffs),
            "opposite_diff_lower_95_bps": _cluster_lower_bound(opposite_diffs, 0.95),
        }
    mi_signals = [
        row
        for row in rows
        if row["arm"] == "MI"
        and row["case_kind"] == "SIGNAL"
        and row.get("followup_status") in {None, "QUOTE_COMPLETE"}
        and row["severe_stressed_return_bps"] is not None
    ]
    do_signals = [
        row
        for row in rows
        if row["arm"] == "DO"
        and row["case_kind"] == "SIGNAL"
        and row.get("followup_status") in {None, "QUOTE_COMPLETE"}
        and row["severe_stressed_return_bps"] is not None
    ]
    paired_differences: list[tuple[str, float]] = []
    pair_deltas: list[float] = []
    for mi in mi_signals:
        mi_at = _utc(str(mi["evaluated_at"]))
        candidates = []
        for do in do_signals:
            if (
                do["namespace"] != mi["namespace"]
                or do["condition_id"] != mi["condition_id"]
                or do["event_id"] != mi["event_id"]
            ):
                continue
            do_at = _utc(str(do["evaluated_at"]))
            delta = (mi_at - do_at).total_seconds() / 60
            if 0 <= delta <= 20:
                candidates.append((delta, do))
        if not candidates:
            continue
        delta, do = min(candidates, key=lambda item: item[0])
        paired_differences.append(
            (
                str(mi["cluster_key"]),
                float(mi["severe_stressed_return_bps"])
                - float(do["severe_stressed_return_bps"]),
            )
        )
        pair_deltas.append(delta)
    by_arm["MI_MINUS_DO_DIAGNOSTIC"] = {
        "eligible_mi_quote_complete": len(mi_signals),
        "paired_cases": len(paired_differences),
        "pair_coverage": (
            len(paired_differences) / len(mi_signals) if mi_signals else None
        ),
        "max_entry_delta_minutes": max(pair_deltas) if pair_deltas else None,
        "mean_severe_stressed_difference_bps": _mean(
            value for _, value in paired_differences
        ),
        "cluster_lower_95_severe_difference_bps": _cluster_lower_bound(
            paired_differences, 0.95
        ),
    }
    return by_arm


def _outcomes(
    connection: sqlite3.Connection,
    start: str,
    end: str,
    *,
    namespace: str,
) -> dict[str, Any]:
    return _summarize_outcomes(
        _outcome_rows(connection, start, end, namespace=namespace)
    )


def _fleet_outcomes(
    specs: list[tuple[str, Path]], start: str, end: str
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for label, path in specs:
        connection = _connect(path)
        try:
            rows.extend(
                _outcome_rows(connection, start, end, namespace=label)
            )
        finally:
            connection.close()
    return _summarize_outcomes(rows)


def _fleet_shard_overlap(
    specs: list[tuple[str, Path]], start: str, end: str
) -> dict[str, Any]:
    owners: dict[str, set[str]] = defaultdict(set)
    for label, path in specs:
        connection = _connect(path)
        try:
            for row in connection.execute(
                """
                SELECT DISTINCT m.condition_id
                FROM market_observations m
                JOIN market_sweeps s ON s.sweep_id=m.sweep_id
                WHERE m.shard_selected=1 AND s.started_at>=? AND s.started_at<?
                """,
                (start, end),
            ):
                owners[str(row["condition_id"])].add(label)
        finally:
            connection.close()
    overlaps = sorted(condition_id for condition_id, labels in owners.items() if len(labels) > 1)
    return {
        "selected_conditions": len(owners),
        "cross_shard_overlap_count": len(overlaps),
        "overlap_condition_ids_sha256": hashlib.sha256(
            json.dumps(overlaps).encode()
        ).hexdigest(),
    }


def analyze_db(label: str, path: Path, start: datetime, end: datetime) -> dict[str, Any]:
    connection = _connect(path)
    try:
        integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        missing = sorted(REQUIRED_TABLES - _tables(connection))
        if missing:
            raise ValueError(f"missing required tables: {', '.join(missing)}")
        contract_rows = connection.execute("SELECT * FROM experiment_contracts").fetchall()
        if len(contract_rows) != 1:
            raise ValueError("each shard DB must have exactly one experiment contract")
        contract = dict(contract_rows[0])
        start_s, end_s = _iso(start), _iso(end)
        success, started_run_ids, lifecycle = _terminal_runs(
            connection, start, end
        )
        slot_claims = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM cycle_slot_claims
                WHERE slot_at>=? AND slot_at<? ORDER BY slot_at, claimed_at
                """,
                (start_s, end_s),
            )
        ]
        slot_events = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM cycle_slot_events
                WHERE slot_at>=? AND slot_at<? ORDER BY slot_at, observed_at
                """,
                (start_s, end_s),
            )
        ]
        cadence = _cadence(
            slot_claims,
            slot_events,
            successful_run_ids=success,
            started_run_ids=started_run_ids,
            start=start,
            end=end,
            offset=int(contract["cadence_offset_minute"]),
        )
        range_sweep_rows = connection.execute(
            """
            SELECT s.* FROM market_sweeps s
            WHERE s.started_at>=? AND s.started_at<?
            """,
            (start_s, end_s),
        ).fetchall()
        sweep_rows = [
            row for row in range_sweep_rows if str(row["run_id"]) in started_run_ids
        ]
        orphan_sweeps = [
            row for row in range_sweep_rows if str(row["run_id"]) not in started_run_ids
        ]
        success_sweeps = [row for row in sweep_rows if str(row["run_id"]) in success]
        failed_sweeps = [row for row in sweep_rows if str(row["run_id"]) not in success]
        success_ids = sorted(success)
        success_placeholders = ",".join("?" for _ in success_ids)

        def successful_scalar(sql: str) -> int:
            if not success_ids:
                return 0
            row = connection.execute(
                sql.format(run_ids=success_placeholders), success_ids
            ).fetchone()
            return int(row[0] or 0)

        expected_pairs = successful_scalar(
            """
            SELECT COUNT(*) FROM market_observations m
            JOIN market_sweeps s ON s.sweep_id=m.sweep_id
            WHERE m.shard_selected=1 AND s.run_id IN ({run_ids})
            """
        )
        expected_tokens = expected_pairs * 2
        attempted_tokens = successful_scalar(
            """
            SELECT COUNT(*) FROM orderbook_token_attempts a
            JOIN market_sweeps s ON s.sweep_id=a.sweep_id
            WHERE a.attempt_role='UNIVERSE' AND s.run_id IN ({run_ids})
            """
        )
        normalized_tokens = successful_scalar(
            """
            SELECT COUNT(*) FROM orderbook_snapshots b
            JOIN market_sweeps s ON s.sweep_id=b.sweep_id
            WHERE b.snapshot_role='UNIVERSE' AND s.run_id IN ({run_ids})
            """
        )
        same_request_pairs = successful_scalar(
            """
            SELECT COUNT(*)
            FROM market_observations m
            JOIN market_sweeps s ON s.sweep_id=m.sweep_id
            JOIN orderbook_token_attempts y
              ON y.sweep_id=m.sweep_id AND y.condition_id=m.condition_id
             AND y.outcome_index=0 AND y.attempt_role='UNIVERSE'
            JOIN orderbook_token_attempts n
              ON n.sweep_id=m.sweep_id AND n.condition_id=m.condition_id
             AND n.outcome_index=1 AND n.attempt_role='UNIVERSE'
            WHERE m.shard_selected=1 AND s.run_id IN ({run_ids})
              AND y.request_id IS NOT NULL AND y.request_id=n.request_id
            """
        )
        normalized_pairs = successful_scalar(
            """
            SELECT COUNT(*)
            FROM market_observations m
            JOIN market_sweeps s ON s.sweep_id=m.sweep_id
            JOIN orderbook_snapshots y
              ON y.sweep_id=m.sweep_id AND y.condition_id=m.condition_id
             AND y.outcome_index=0 AND y.snapshot_role='UNIVERSE'
            JOIN orderbook_snapshots n
              ON n.sweep_id=m.sweep_id AND n.condition_id=m.condition_id
             AND n.outcome_index=1 AND n.snapshot_role='UNIVERSE'
            WHERE m.shard_selected=1 AND s.run_id IN ({run_ids})
            """
        )
        quote_eligible_pairs = successful_scalar(
            """
            SELECT COUNT(*)
            FROM market_observations m
            JOIN market_sweeps s ON s.sweep_id=m.sweep_id
            JOIN orderbook_snapshots y
              ON y.sweep_id=m.sweep_id AND y.condition_id=m.condition_id
             AND y.outcome_index=0 AND y.snapshot_role='UNIVERSE'
            JOIN orderbook_snapshots n
              ON n.sweep_id=m.sweep_id AND n.condition_id=m.condition_id
             AND n.outcome_index=1 AND n.snapshot_role='UNIVERSE'
            WHERE m.shard_selected=1 AND s.run_id IN ({run_ids})
              AND y.quote_eligible=1 AND n.quote_eligible=1
            """
        )
        empty_book_tokens = successful_scalar(
            """
            SELECT COUNT(*) FROM orderbook_token_attempts a
            JOIN market_sweeps s ON s.sweep_id=a.sweep_id
            WHERE a.attempt_role='UNIVERSE' AND a.status='EMPTY_BOOK'
              AND s.run_id IN ({run_ids})
            """
        )
        universe_status_counts: dict[str, int] = {}
        if success_ids:
            universe_status_counts = {
                str(row["status"]): int(row["count"])
                for row in connection.execute(
                    f"""
                    SELECT a.status, COUNT(*) count
                    FROM orderbook_token_attempts a
                    JOIN market_sweeps s ON s.sweep_id=a.sweep_id
                    WHERE a.attempt_role='UNIVERSE'
                      AND s.run_id IN ({success_placeholders})
                    GROUP BY a.status
                    """,
                    success_ids,
                )
            }
        raw_linkage = {"response_backed": 0, "linked": 0}
        if success_ids:
            raw_row = connection.execute(
                f"""
                SELECT
                  SUM(CASE WHEN a.request_id IS NOT NULL THEN 1 ELSE 0 END) response_backed,
                  SUM(CASE WHEN a.request_id IS NOT NULL AND EXISTS(
                    SELECT 1 FROM raw_payloads p WHERE p.request_id=a.request_id
                  ) THEN 1 ELSE 0 END) linked
                FROM orderbook_token_attempts a
                JOIN market_sweeps s ON s.sweep_id=a.sweep_id
                WHERE a.attempt_role='UNIVERSE'
                  AND s.run_id IN ({success_placeholders})
                """,
                success_ids,
            ).fetchone()
            raw_linkage = {
                "response_backed": int(raw_row["response_backed"] or 0),
                "linked": int(raw_row["linked"] or 0),
            }

        followup_status_counts = {
            str(row["status"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT status, COUNT(*) count FROM followup_attempts
                WHERE attempted_at>=? AND attempted_at<? GROUP BY status
                """,
                (start_s, end_s),
            )
        }
        followup_claims = int(
            connection.execute(
                "SELECT COUNT(*) FROM followup_claims WHERE claimed_at>=? AND claimed_at<?",
                (start_s, end_s),
            ).fetchone()[0]
        )
        followup_request_starts = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM followup_request_starts
                WHERE request_started_at>=? AND request_started_at<?
                """,
                (start_s, end_s),
            ).fetchone()[0]
        )
        recovered_followup_leases = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM followup_claim_leases
                WHERE generation>1 AND claimed_at>=? AND claimed_at<?
                """,
                (start_s, end_s),
            ).fetchone()[0]
        )
        quality = {
            str(row["severity"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT q.severity, COUNT(*) count FROM data_quality_issues q
                JOIN market_sweeps s ON s.sweep_id=q.sweep_id
                WHERE s.started_at>=? AND s.started_at<? GROUP BY q.severity
                """,
                (start_s, end_s),
            )
        }
        cycle_stat_runtime_values = [
            float(row[0])
            for row in connection.execute(
                "SELECT runtime_seconds FROM cycle_stats WHERE started_at>=? AND started_at<? ORDER BY runtime_seconds",
                (start_s, end_s),
            )
        ]
        runtime_values = sorted(
            float(value) for value in lifecycle["terminal_durations_seconds"]
        )
        p95 = (
            runtime_values[min(len(runtime_values) - 1, math.ceil(len(runtime_values) * 0.95) - 1)]
            if runtime_values
            else None
        )
        configs = [
            dict(row)
            for row in connection.execute(
                "SELECT config_hash, strategy_source_digest, job_name FROM research_config_versions"
            )
        ]
        cohorts_in_range = [
            dict(row)
            for row in connection.execute(
                """
                SELECT DISTINCT c.config_hash, c.strategy_source_digest, c.job_name
                FROM research_run_events r
                JOIN research_config_versions c ON c.config_hash=r.config_hash
                WHERE r.event_type='STARTED' AND r.event_at>=? AND r.event_at<?
                ORDER BY c.config_hash, c.strategy_source_digest, c.job_name
                """,
                (start_s, end_s),
            )
        ]
        outcomes = _outcomes(
            connection,
            start_s,
            end_s,
            namespace=label,
        )
        result = {
            "label": label,
            "db_path": str(path),
            "database_sha256": _sha256(path),
            "database_bytes": path.stat().st_size,
            "quick_check": integrity,
            "contract": {
                key: contract[key]
                for key in (
                    "job_name",
                    "data_contract",
                    "schema_version",
                    "schema_profile",
                    "shard_index",
                    "shard_count",
                    "cadence_minutes",
                    "cadence_offset_minute",
                    "window_start",
                    "window_end",
                    "preregistration_sha256",
                    "data_contract_sha256",
                )
            },
            "cohorts": configs,
            "cohorts_in_range": cohorts_in_range,
            "run_lifecycle": lifecycle,
            "cadence": cadence,
            "source": {
                "successful_sweeps": len(success_sweeps),
                "failed_run_published_sweeps": len(failed_sweeps),
                "orphan_range_sweeps": len(orphan_sweeps),
                "all_cursor_complete": all(int(row["cursor_complete"]) == 1 for row in success_sweeps),
                "universe": {
                    "expected_pairs": expected_pairs,
                    "expected_tokens": expected_tokens,
                    "attempt_evidence_tokens": attempted_tokens,
                    "attempt_evidence_coverage": (
                        attempted_tokens / expected_tokens if expected_tokens else None
                    ),
                    "normalized_tokens": normalized_tokens,
                    "normalized_token_coverage": (
                        normalized_tokens / expected_tokens if expected_tokens else None
                    ),
                    "same_request_atomic_pairs": same_request_pairs,
                    "same_request_atomicity_coverage": (
                        same_request_pairs / expected_pairs if expected_pairs else None
                    ),
                    "normalized_pairs": normalized_pairs,
                    "normalized_pair_availability": (
                        normalized_pairs / expected_pairs if expected_pairs else None
                    ),
                    "quote_eligible_pairs": quote_eligible_pairs,
                    "quote_eligible_pair_coverage": (
                        quote_eligible_pairs / expected_pairs if expected_pairs else None
                    ),
                    "empty_book_tokens": empty_book_tokens,
                    "status_counts": universe_status_counts,
                    "raw_payload_linkage": (
                        raw_linkage["linked"] / raw_linkage["response_backed"]
                        if raw_linkage["response_backed"]
                        else None
                    ),
                },
                "followup_only": {
                    "claims": followup_claims,
                    "request_starts": followup_request_starts,
                    "recovered_leases": recovered_followup_leases,
                    "terminal_attempts": sum(followup_status_counts.values()),
                    "quote_complete": followup_status_counts.get("QUOTE_COMPLETE", 0),
                    "empty_book": followup_status_counts.get("EMPTY_BOOK", 0),
                    "censored": sum(
                        count
                        for status, count in followup_status_counts.items()
                        if status != "QUOTE_COMPLETE"
                    ),
                    "status_counts": followup_status_counts,
                },
                # Compatibility aliases; unlike v2 they are derived only from UNIVERSE.
                "expected_universe_token_attempts": expected_tokens,
                "observed_universe_tokens": normalized_tokens,
                "pair_token_coverage": normalized_tokens / expected_tokens if expected_tokens else None,
                "same_request_pair_coverage": (
                    same_request_pairs / expected_pairs if expected_pairs else None
                ),
                "raw_payload_linkage": (
                    raw_linkage["linked"] / raw_linkage["response_backed"]
                    if raw_linkage["response_backed"]
                    else None
                ),
            },
            "runtime": {
                "terminal_runs": len(runtime_values),
                "failed_terminal_runs": lifecycle["failed_runs"],
                "p95_seconds": p95,
                "max_seconds": max(runtime_values) if runtime_values else None,
                "failed_terminal_durations_seconds": lifecycle[
                    "failed_terminal_durations_seconds"
                ],
                "cooperative_deadline_breaches": lifecycle[
                    "cooperative_deadline_breaches"
                ],
                "hard_limit_breaches": lifecycle["hard_limit_breaches"],
                "successful_cycle_stat_rows": len(cycle_stat_runtime_values),
            },
            "quality_issues": quality,
            "outcomes": outcomes,
        }
        health_checks = {
            "quick_check": integrity == "ok",
            "frozen_contract_window": (
                contract["window_start"] == FROZEN_WINDOW_START
                and contract["window_end"] == FROZEN_WINDOW_END
            ),
            "review_within_frozen_window": (
                start_s >= str(contract["window_start"])
                and end_s <= str(contract["window_end"])
            ),
            "run_lifecycle": lifecycle["malformed_lifecycle_count"] == 0,
            "cadence_coverage": cadence["coverage"] >= 0.95,
            "no_duplicate_slots": cadence["duplicate_slots"] == 0,
            "no_late_invocations": cadence["late_invocations"] == 0,
            "valid_slot_claims": cadence["invalid_slot_claims"] == 0,
            "started_runs_owned_by_slots": cadence["started_without_claim"] == 0,
            "claimed_slots_have_started_runs": cadence["claimed_without_started"] == 0,
            "cursor_complete": result["source"]["all_cursor_complete"],
            "no_failed_run_sweeps": not failed_sweeps,
            "no_orphan_range_sweeps": not orphan_sweeps,
            "pair_coverage": (result["source"]["pair_token_coverage"] or 0) >= 0.95,
            "same_request_pair_coverage": (
                result["source"]["same_request_pair_coverage"] or 0
            ) == 1,
            "raw_payload_linkage": (result["source"]["raw_payload_linkage"] or 0) == 1,
            "runtime_p95": p95 is not None and p95 < 180,
            "runtime_max": bool(runtime_values) and max(runtime_values) < 240,
            "cooperative_deadline": lifecycle["cooperative_deadline_breaches"] == 0,
            "hard_deadline": lifecycle["hard_limit_breaches"] == 0,
            "no_high_critical": quality.get("HIGH", 0) == 0 and quality.get("CRITICAL", 0) == 0,
            "single_cohort_in_range": len(cohorts_in_range) == 1,
        }
        result["health_checks"] = health_checks
        result["health_pass"] = all(health_checks.values())
        return result
    finally:
        connection.close()


def _primary_gate(
    results: list[dict[str, Any]], fleet_outcomes: dict[str, Any], days: float
) -> dict[str, Any]:
    mi = fleet_outcomes["MI"]
    persistence = fleet_outcomes["MI_MINUS_DO_DIAGNOSTIC"]
    totals = {
        "qualified_signal_cases": int(mi["qualified_signal_cases"]),
        "quote_complete_signals": int(mi["quote_complete_signals"]),
        "event_clusters": int(mi["event_clusters"]),
        "distinct_utc_days": int(mi["distinct_utc_days"]),
    }
    checks = {
        "duration_30_days": days >= 30,
        "fleet_health": all(result["health_pass"] for result in results),
        "mi_quote_complete_50": totals["quote_complete_signals"] >= 50,
        "mi_event_clusters_30": totals["event_clusters"] >= 30,
        "mi_distinct_days_20": totals["distinct_utc_days"] >= 20,
        "mi_fleet_raw_lower_positive": (mi["cluster_lower_98_33_raw_bps"] or -math.inf) > 0,
        "mi_fleet_base_lower_positive": (mi["cluster_lower_98_33_base_bps"] or -math.inf) > 0,
        "mi_fleet_severe_lower_positive": (mi["cluster_lower_98_33_severe_bps"] or -math.inf) > 0,
        "mi_outcome_coverage": (mi["outcome_coverage"] or 0) >= 0.90,
        "mi_neutral_coverage": (mi["neutral_pair_coverage"] or 0) >= 0.80,
        "mi_neutral_difference_positive": (mi["neutral_diff_lower_95_bps"] or -math.inf) > 0,
        "mi_early_late_positive": (
            (mi["early_half_mean_bps"] or -math.inf) > 0
            and (mi["late_half_mean_bps"] or -math.inf) > 0
        ),
        "mi_minus_do_pair_coverage": (persistence["pair_coverage"] or 0) >= 0.80,
        "mi_minus_do_severe_lower_positive": (
            persistence["cluster_lower_95_severe_difference_bps"] or -math.inf
        ) > 0,
    }
    return {
        "fleet_mi": mi,
        "totals": totals,
        "checks": checks,
        "pass": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="inclusive UTC timestamp")
    parser.add_argument("--end", required=True, help="exclusive UTC timestamp")
    parser.add_argument(
        "--db",
        action="append",
        required=True,
        help="LABEL=/absolute/canonical/path/to/trades_sim.db",
    )
    parser.add_argument("--output")
    args = parser.parse_args()
    start, end = _utc(args.start), _utc(args.end)
    if end <= start:
        raise SystemExit("--end must be after --start")
    labels: set[str] = set()
    specs: list[tuple[str, Path]] = []
    for value in args.db:
        if "=" not in value:
            raise SystemExit("--db must use LABEL=/absolute/path")
        label, raw_path = value.split("=", 1)
        if not label or label in labels:
            raise SystemExit("DB labels must be non-empty and unique")
        labels.add(label)
        specs.append((label, _canonical_db(raw_path)))
    results = [analyze_db(label, path, start, end) for label, path in specs]
    start_s, end_s = _iso(start), _iso(end)
    fleet_outcomes = _fleet_outcomes(specs, start_s, end_s)
    shard_overlap = _fleet_shard_overlap(specs, start_s, end_s)
    contracts = [result["contract"] for result in results]
    shard_indices = {int(contract["shard_index"]) for contract in contracts}
    shared_prereg = {str(contract["preregistration_sha256"]) for contract in contracts}
    shared_window = {(contract["window_start"], contract["window_end"]) for contract in contracts}
    source_digests = {
        str(cohort["strategy_source_digest"])
        for result in results
        for cohort in result["cohorts_in_range"]
    }
    fleet_contract_checks = {
        "all_three_shards": shard_indices == {0, 1, 2},
        "shared_preregistration": len(shared_prereg) == 1,
        "shared_window": len(shared_window) == 1,
        "shared_source_digest": len(source_digests) == 1,
        "no_cross_shard_condition_overlap": shard_overlap["cross_shard_overlap_count"] == 0,
        "queue_echo_contract": all(
            contract["data_contract"] == "queue-echo-v3" for contract in contracts
        ),
        "schema_v3": all(
            int(contract["schema_version"]) == 3
            and contract["schema_profile"] == "queue-echo-v3-sqlite-v3"
            for contract in contracts
        ),
        "exact_runtime_identities": (
            len(contracts) == 3
            and {
                str(contract["job_name"]): (
                    int(contract["shard_index"]),
                    int(contract["cadence_offset_minute"]),
                )
                for contract in contracts
            }
            == EXPECTED_RUNTIME_IDENTITIES
        ),
    }
    days = (end - start).total_seconds() / 86400
    primary = _primary_gate(results, fleet_outcomes, days)
    if days < 7:
        verdict = "HEALTH_ONLY_NOT_ENOUGH_DURATION"
    elif not all(fleet_contract_checks.values()) or not all(result["health_pass"] for result in results):
        verdict = "COLLECTION_HEALTH_FAIL"
    elif days < 30:
        verdict = "COLLECTION_HEALTH_PASS_STRATEGY_NOT_YET_EVALUABLE"
    elif primary["pass"]:
        verdict = "SHADOW_REVIEW_ONLY"
    else:
        verdict = "STOP_UNRESEARCHABLE"
    payload = {
        "analyzer_version": ANALYZER_VERSION,
        "bootstrap": {"draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED},
        "review_range": {"start_inclusive": _iso(start), "end_exclusive": _iso(end), "days": days},
        "fleet_contract_checks": fleet_contract_checks,
        "fleet_shard_overlap": shard_overlap,
        "fleet_outcomes": fleet_outcomes,
        "shards": results,
        "primary_mi_gate": primary,
        "verdict": verdict,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            raise SystemExit("--output must be absolute")
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if verdict not in {"COLLECTION_HEALTH_FAIL", "STOP_UNRESEARCHABLE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
