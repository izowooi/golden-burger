"""Read-only combined health analyzer for Last Mile v1 and follow-up v2a."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from .analyzer import (
    _canonical_db,
    _collection_health,
    _connect,
    _expected_slots,
    _percentile,
    _ratio,
    _slot,
    _storage_report,
    parse_utc,
)
from .db.followup_repository import (
    APPEND_ONLY_TABLES,
    FOLLOWUP_SCHEMA_VERSION,
    FollowupRepository,
)
from .followup_collector import BOOK_ENCODING, decode_compact_book
from .followup_config import (
    FOLLOWUP_CANONICAL_JOB,
    FOLLOWUP_DATA_CONTRACT,
    V1_CANONICAL_JOB,
    V1_DATA_CONTRACT,
    V1_SCHEMA_VERSION,
    V1SourceConfig,
)
from .utils.retry import canonical_json, iso_utc
from .v1_source import V1SourceReader, compare_anchor


ANALYSIS_SCHEMA = "golden-strawberry-followup-health-v2a"
REQUIRED_V2_TABLES = frozenset(
    {
        "schema_metadata",
        "followup_contracts",
        "research_config_versions",
        "source_anchors",
        "imported_episodes",
        "imported_condition_status",
        "imported_threshold_events",
        "research_run_events",
        "api_requests",
        "followup_cycles",
        "book_token_attempts",
        "compact_books",
        "episode_path_observations",
        "episode_threshold_events",
        "resolution_observations",
        "data_quality_issues",
        "phase_timings",
        "storage_metrics",
    }
)
EXPECTED_PHASES = frozenset(
    {
        "v1_anchor_validation",
        "load_unresolved",
        "clob_books",
        "normalize_compact_books",
        "fixed_share_paths",
        "threshold_transitions",
        "gamma_resolutions",
        "normalize_resolutions",
        "atomic_publication",
        "total",
    }
)


def _schema_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _v1_report(
    path: Path,
    *,
    stored_anchor: Mapping[str, Any],
    deep_check: bool,
) -> dict[str, Any]:
    connection = _connect(path)
    try:
        metadata = dict(connection.execute("SELECT key,value FROM schema_metadata"))
        contract_rows = connection.execute(
            "SELECT * FROM experiment_contracts"
        ).fetchall()
        if metadata.get("schema_version") != str(V1_SCHEMA_VERSION):
            raise ValueError("v1 schema version changed")
        if metadata.get("data_contract") != V1_DATA_CONTRACT:
            raise ValueError("v1 data contract changed")
        if len(contract_rows) != 1:
            raise ValueError("v1 must contain exactly one experiment contract")
        contract = dict(contract_rows[0])
        if contract["job_name"] != V1_CANONICAL_JOB:
            raise ValueError("v1 runtime job changed")
        entry_start = parse_utc(str(contract["entry_start"]))
        entry_end = parse_utc(str(contract["entry_end"]))
        health = _collection_health(connection, entry_start, entry_end)
        integrity = (
            str(connection.execute("PRAGMA quick_check").fetchone()[0])
            if deep_check
            else "not_run_large_v1"
        )
    finally:
        connection.close()

    source_config = V1SourceConfig(
        db_path=path,
        configured_path=str(path),
        expected_schema_version=V1_SCHEMA_VERSION,
        expected_data_contract=V1_DATA_CONTRACT,
        expected_job_name=V1_CANONICAL_JOB,
        expected_entry_start_utc=parse_utc(
            str(stored_anchor["source_entry_start"])
        ),
        expected_entry_end_utc=parse_utc(str(stored_anchor["source_entry_end"])),
        expected_followup_end_utc=parse_utc(
            str(stored_anchor["source_followup_end"])
        ),
        minimum_successful_cutoff_utc=parse_utc(
            str(stored_anchor["source_entry_end"])
        ),
        require_no_sidecars=True,
    )
    observed = V1SourceReader(source_config).capture()
    anchor_drift = None
    try:
        compare_anchor(
            stored_anchor,
            observed.anchor,
            include_file_fingerprint=False,
        )
    except RuntimeError as error:
        anchor_drift = str(error)
    anchor_matches = anchor_drift is None
    healthy = bool(
        health["healthy"]
        and anchor_matches
        and (not deep_check or integrity == "ok")
    )
    return {
        "healthy": healthy,
        "database": {
            "path": str(path),
            "bytes": path.stat().st_size,
            "opened_read_only_immutable_for_health": True,
            "seed_capture_opened_mode_ro": True,
            "quick_check": integrity,
            "quick_check_explicitly_requested": deep_check,
        },
        "entry_collection_health": health,
        "anchor": {
            "semantic_match": anchor_matches,
            "drift_error": anchor_drift,
            "stored_anchor_sha256": stored_anchor["anchor_sha256"],
            "observed_source_cycle_number": observed.anchor[
                "source_cycle_number"
            ],
            "observed_source_sweep_id": observed.anchor["source_sweep_id"],
            "local_copy_file_identity_intentionally_excluded": True,
        },
    }


def _terminal_runs(
    connection: sqlite3.Connection, start_text: str, end_text: str
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = [
        dict(row)
        for row in connection.execute(
            """
            WITH scoped AS (
                SELECT DISTINCT run_id FROM research_run_events
                WHERE event_type='STARTED' AND event_at>=? AND event_at<?
            ), ranked AS (
                SELECT e.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY e.run_id
                           ORDER BY e.event_at DESC,
                                    CASE e.event_type
                                      WHEN 'SUCCEEDED' THEN 2
                                      WHEN 'FAILED' THEN 2
                                      ELSE 1
                                    END DESC,
                                    e.event_id DESC
                       ) AS position
                FROM research_run_events e JOIN scoped s ON s.run_id=e.run_id
            )
            SELECT * FROM ranked WHERE position=1 ORDER BY event_at,run_id
            """,
            (start_text, end_text),
        )
    ]
    return rows, dict(Counter(str(row["event_type"]) for row in rows))


def _phase_report(
    connection: sqlite3.Connection, success_run_ids: set[str]
) -> tuple[dict[str, Any], set[str]]:
    if not success_run_ids:
        return {"by_phase": {}, "missing_by_run": {}}, set()
    rows = [
        dict(row)
        for row in connection.execute(
            "SELECT run_id,phase_name,elapsed_seconds FROM phase_timings"
        )
        if str(row["run_id"]) in success_run_ids
    ]
    by_run: dict[str, set[str]] = defaultdict(set)
    values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        run_id = str(row["run_id"])
        phase = str(row["phase_name"])
        by_run[run_id].add(phase)
        values[phase].append(float(row["elapsed_seconds"]))
    missing = {
        run_id: sorted(EXPECTED_PHASES - by_run.get(run_id, set()))
        for run_id in sorted(success_run_ids)
        if EXPECTED_PHASES - by_run.get(run_id, set())
    }
    statistics = {
        phase: {
            "count": len(samples),
            "p50": _percentile(samples, 0.50),
            "p95": _percentile(samples, 0.95),
            "max": max(samples),
        }
        for phase, samples in sorted(values.items())
    }
    return {"by_phase": statistics, "missing_by_run": missing}, set(missing)


def _blob_report(
    connection: sqlite3.Connection, start_text: str, end_text: str
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT b.book_id,b.token_id,b.encoding,b.book_sha256,b.book_blob,
               b.uncompressed_bytes,b.compressed_bytes
        FROM compact_books b JOIN followup_cycles c ON c.cycle_id=b.cycle_id
        WHERE c.completed_at>=? AND c.completed_at<?
        ORDER BY c.cycle_number,b.token_id
        """,
        (start_text, end_text),
    ).fetchall()
    errors: list[dict[str, str]] = []
    for row in rows:
        try:
            if row["encoding"] != BOOK_ENCODING:
                raise ValueError("unexpected compact book encoding")
            blob = bytes(row["book_blob"])
            payload = decode_compact_book(
                blob, expected_sha256=str(row["book_sha256"])
            )
            raw = canonical_json(payload).encode("utf-8")
            if len(raw) != int(row["uncompressed_bytes"]):
                raise ValueError("uncompressed byte count mismatch")
            if len(blob) != int(row["compressed_bytes"]):
                raise ValueError("compressed byte count mismatch")
            if payload["token_id"] != row["token_id"]:
                raise ValueError("compact book token mismatch")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            errors.append(
                {"book_id": str(row["book_id"]), "error": str(error)[:500]}
            )
    return {
        "healthy": not errors,
        "checked": len(rows),
        "errors": errors,
    }


def _one_hot_report(
    connection: sqlite3.Connection, start_text: str, end_text: str
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT r.* FROM resolution_observations r
        JOIN followup_cycles c ON c.cycle_id=r.cycle_id
        WHERE c.completed_at>=? AND c.completed_at<?
          AND r.resolution_status='RESOLVED'
        ORDER BY c.cycle_number,r.condition_id
        """,
        (start_text, end_text),
    ).fetchall()
    errors: list[dict[str, str]] = []
    for row in rows:
        condition_id = str(row["condition_id"])
        try:
            payouts = json.loads(str(row["token_payouts_json"]))
            winning = str(row["winning_token_id"])
            if (
                not isinstance(payouts, dict)
                or not payouts
                or any(value not in {0, 1} for value in payouts.values())
                or sum(int(value) for value in payouts.values()) != 1
                or payouts.get(winning) != 1
            ):
                raise ValueError("resolution is not unique one-hot")
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            errors.append({"condition_id": condition_id, "error": str(error)})
    return {"healthy": not errors, "checked": len(rows), "errors": errors}


def _v2_report(
    path: Path, *, start: datetime, end: datetime
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    start_text, end_text = iso_utc(start), iso_utc(end)
    connection = _connect(path)
    try:
        tables = _schema_tables(connection)
        missing = sorted(REQUIRED_V2_TABLES - tables)
        if missing:
            raise ValueError("v2a database is missing tables: " + ", ".join(missing))
        if "clob_levels" in tables:
            raise ValueError("v2a row-per-level table is forbidden")
        metadata = dict(connection.execute("SELECT key,value FROM schema_metadata"))
        expected_metadata = {
            "schema_version": str(FOLLOWUP_SCHEMA_VERSION),
            "data_contract": FOLLOWUP_DATA_CONTRACT,
            "book_storage": "canonical-gzip-one-row-per-token-cycle",
            "v1_source_access": "mode=ro",
        }
        if metadata != expected_metadata:
            raise ValueError("v2a schema metadata changed")
        contracts = connection.execute("SELECT * FROM followup_contracts").fetchall()
        if len(contracts) != 1:
            raise ValueError("v2a must contain exactly one follow-up contract")
        contract = dict(contracts[0])
        if (
            contract["job_name"] != FOLLOWUP_CANONICAL_JOB
            or contract["data_contract"] != FOLLOWUP_DATA_CONTRACT
        ):
            raise ValueError("v2a runtime/data contract changed")
        anchor_rows = connection.execute("SELECT * FROM source_anchors").fetchall()
        if len(anchor_rows) != 1:
            raise ValueError("v2a must contain exactly one source anchor")
        anchor = dict(anchor_rows[0])
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        foreign_key_errors = [
            tuple(row) for row in connection.execute("PRAGMA foreign_key_check")
        ]
        trigger_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='trigger' AND (name LIKE '%_no_update' "
                "OR name LIKE '%_no_delete')"
            ).fetchone()[0]
        )
        try:
            seed_integrity = FollowupRepository(path).verify_seed_integrity(anchor)
        except RuntimeError as error:
            seed_integrity = {
                "healthy": False,
                "error": str(error),
            }
        terminals, terminal_counts = _terminal_runs(connection, start_text, end_text)
        success_run_ids = {
            str(row["run_id"])
            for row in terminals
            if row["event_type"] == "SUCCEEDED"
        }
        started_rows = [
            dict(row)
            for row in connection.execute(
            """
            SELECT run_id,event_at,details_json FROM research_run_events
            WHERE event_type='STARTED' AND event_at>=? AND event_at<?
            ORDER BY event_at,run_id
            """,
            (start_text, end_text),
            )
        ]
        validation_mode_by_run: dict[str, str] = {}
        for row in started_rows:
            try:
                details = json.loads(str(row["details_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                details = {}
            validation_mode_by_run[str(row["run_id"])] = str(
                details.get("validation_mode") or "UNKNOWN"
            )
        full_seed_started_run_ids = {
            run_id
            for run_id, validation_mode in validation_mode_by_run.items()
            if validation_mode == "FULL_SEED"
        }
        cycles = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM followup_cycles
                WHERE completed_at>=? AND completed_at<? ORDER BY cycle_number
                """,
                (start_text, end_text),
            )
        ]
        pinned_cycle_run_ids = {
            str(row["run_id"])
            for row in cycles
            if row["validation_mode"] == "PINNED_FAST"
        }
        full_seed_cycle_run_ids = {
            str(row["run_id"])
            for row in cycles
            if row["validation_mode"] == "FULL_SEED"
        }
        cycle_without_success = sorted(
            str(row["run_id"])
            for row in cycles
            if str(row["run_id"]) not in success_run_ids
        )
        success_without_cycle = sorted(
            success_run_ids - {str(row["run_id"]) for row in cycles}
        )
        pinned_success_run_ids = pinned_cycle_run_ids & success_run_ids
        full_seed_success_run_ids = full_seed_cycle_run_ids & success_run_ids
        cadence_minutes = int(contract["cadence_minutes"])
        offset_minute = int(contract["cadence_offset_minute"])

        natural_pinned_slots: list[datetime] = []
        for row in connection.execute(
            """
            SELECT c.run_id,e.event_at FROM followup_cycles c
            JOIN research_run_events e ON e.run_id=c.run_id
            WHERE c.validation_mode='PINNED_FAST' AND e.event_type='STARTED'
              AND EXISTS (
                SELECT 1 FROM research_run_events terminal
                WHERE terminal.run_id=c.run_id AND terminal.event_type='SUCCEEDED'
              )
            ORDER BY e.event_at,c.cycle_number
            """
        ):
            slot, delay = _slot(
                parse_utc(str(row["event_at"])), cadence_minutes, offset_minute
            )
            if delay <= 120:
                natural_pinned_slots.append(slot)
        rollout_start = min(natural_pinned_slots) if natural_pinned_slots else None
        effective_start = (
            max(start, rollout_start)
            if rollout_start is not None and rollout_start < end
            else None
        )
        expected_slots = (
            _expected_slots(
                effective_start, end, cadence_minutes, offset_minute
            )
            if effective_start is not None
            else []
        )
        slot_counts: Counter[str] = Counter()
        success_slots: set[str] = set()
        off_slot = 0
        recurring_started_rows = [
            row
            for row in started_rows
            if validation_mode_by_run.get(str(row["run_id"])) == "PINNED_FAST"
            and effective_start is not None
            and parse_utc(str(row["event_at"])) >= effective_start
        ]
        for row in recurring_started_rows:
            slot, delay = _slot(
                parse_utc(str(row["event_at"])), cadence_minutes, offset_minute
            )
            slot_key = iso_utc(slot)
            slot_counts[slot_key] += 1
            if delay > 120:
                off_slot += 1
            if str(row["run_id"]) in pinned_success_run_ids:
                success_slots.add(slot_key)
        duplicate_runs = sum(max(0, count - 1) for count in slot_counts.values())
        success_coverage = _ratio(len(success_slots), len(expected_slots))
        off_slot_ratio = _ratio(off_slot, len(recurring_started_rows))
        cycle_ids = {str(row["cycle_id"]) for row in cycles}
        counts = {
            table: sum(
                1
                for row in connection.execute(f"SELECT cycle_id FROM {table}")
                if str(row[0]) in cycle_ids
            )
            for table in (
                "book_token_attempts",
                "compact_books",
                "episode_path_observations",
                "resolution_observations",
            )
        }
        expected = {
            "book_token_attempts": sum(
                int(row["distinct_token_count"]) for row in cycles
            ),
            "compact_books": sum(int(row["book_observed_count"]) for row in cycles),
            "episode_path_observations": sum(
                int(row["unresolved_episode_count"]) for row in cycles
            ),
            "resolution_observations": sum(
                int(row["distinct_condition_count"]) for row in cycles
            ),
        }
        coverage = {
            table: {
                "observed": counts[table],
                "expected": expected[table],
                "coverage": _ratio(counts[table], expected[table]),
            }
            for table in counts
        }
        observed_token_total = expected["book_token_attempts"]
        coverage["displayed_book_observation"] = {
            "observed": counts["compact_books"],
            "expected": observed_token_total,
            "coverage": _ratio(counts["compact_books"], observed_token_total),
            "missing_is_explicit_censoring": True,
        }

        receipt_kinds = {
            str(row["request_kind"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT request_kind,COUNT(*) AS count FROM api_requests
                WHERE started_at>=? AND started_at<? GROUP BY request_kind
                """,
                (start_text, end_text),
            )
        }
        forbidden_requests = sum(
            count
            for kind, count in receipt_kinds.items()
            if kind in {"clob_sampling_markets", "gamma_candidate_metadata"}
        )
        book_lineage_errors = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM compact_books b
                JOIN followup_cycles c ON c.cycle_id=b.cycle_id
                LEFT JOIN api_requests a ON a.request_id=b.request_id
                WHERE c.completed_at>=? AND c.completed_at<?
                  AND (a.request_id IS NULL OR a.status!='SUCCESS'
                       OR a.response_sha256!=b.source_response_sha256)
                """,
                (start_text, end_text),
            ).fetchone()[0]
        )
        later_resolution_requests = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM resolution_observations first
                JOIN followup_cycles c1 ON c1.cycle_id=first.cycle_id
                JOIN resolution_observations later
                  ON later.condition_id=first.condition_id
                JOIN followup_cycles c2 ON c2.cycle_id=later.cycle_id
                WHERE first.resolution_status='RESOLVED'
                  AND c2.cycle_number>c1.cycle_number
                """
            ).fetchone()[0]
        )
        later_book_requests = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM resolution_observations r
                JOIN followup_cycles c1 ON c1.cycle_id=r.cycle_id
                JOIN imported_episodes e ON e.condition_id=r.condition_id
                JOIN compact_books b ON b.token_id=e.token_id
                JOIN followup_cycles c2 ON c2.cycle_id=b.cycle_id
                WHERE r.resolution_status='RESOLVED'
                  AND c2.cycle_number>c1.cycle_number
                """
            ).fetchone()[0]
        )
        recurring_success_run_ids = {
            str(row["run_id"])
            for row in recurring_started_rows
            if str(row["run_id"]) in pinned_success_run_ids
        }
        recurring_phase_report, recurring_missing_phases = _phase_report(
            connection, recurring_success_run_ids
        )
        full_seed_phase_report, full_seed_missing_phases = _phase_report(
            connection, full_seed_success_run_ids
        )
        recurring_total_stats = recurring_phase_report["by_phase"].get(
            "total", {}
        )
        full_seed_total_stats = full_seed_phase_report["by_phase"].get(
            "total", {}
        )
        blob_integrity = _blob_report(connection, start_text, end_text)
        one_hot = _one_hot_report(connection, start_text, end_text)
        quality_counts = {
            str(row["severity"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT severity,COUNT(*) AS count FROM data_quality_issues
                WHERE recorded_at>=? AND recorded_at<? GROUP BY severity
                """,
                (start_text, end_text),
            )
        }
        cohort_rows = [
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
        storage = _storage_report(connection, start, end)
        try:
            contract_payload = json.loads(str(contract["contract_json"]))
            runtime_contract = contract_payload["trading"]["runtime"]
            network_deadline_seconds = float(
                runtime_contract["network_cycle_deadline_seconds"]
            )
            pinned_fast_hard_sla_seconds = float(
                runtime_contract["pinned_fast_hard_sla_seconds"]
            )
            full_seed_budget_seconds = float(
                runtime_contract["full_seed_budget_seconds"]
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("v2a runtime contract is malformed") from error
    finally:
        connection.close()

    exact_coverage = all(
        item["coverage"] == 1.0
        for key, item in coverage.items()
        if key != "displayed_book_observation" and item["expected"] > 0
    )
    recurring_runtime_count = int(recurring_total_stats.get("count", 0))
    recurring_runtime_p95 = recurring_total_stats.get("p95")
    recurring_runtime_max = recurring_total_stats.get("max")
    full_seed_runtime_count = int(full_seed_total_stats.get("count", 0))
    full_seed_runtime_max = full_seed_total_stats.get("max")
    recurring_runtime_healthy = bool(
        recurring_runtime_count > 0
        and recurring_runtime_p95 is not None
        and recurring_runtime_p95 < pinned_fast_hard_sla_seconds
        and recurring_runtime_max is not None
        and recurring_runtime_max < pinned_fast_hard_sla_seconds
    )
    full_seed_runtime_healthy = bool(
        full_seed_runtime_count == 0
        or (
            full_seed_runtime_max is not None
            and full_seed_runtime_max < full_seed_budget_seconds
        )
    )
    cadence_healthy = bool(
        rollout_start is not None
        and success_coverage is not None
        and success_coverage >= 0.90
        and duplicate_runs == 0
        and (off_slot_ratio is None or off_slot_ratio <= 0.05)
    )
    healthy = bool(
        quick_check == "ok"
        and not foreign_key_errors
        and trigger_count == len(APPEND_ONLY_TABLES) * 2
        and seed_integrity["healthy"]
        and cadence_healthy
        and cycles
        and exact_coverage
        and forbidden_requests == 0
        and book_lineage_errors == 0
        and later_resolution_requests == 0
        and later_book_requests == 0
        and blob_integrity["healthy"]
        and one_hot["healthy"]
        and not cycle_without_success
        and not success_without_cycle
        and not recurring_missing_phases
        and not full_seed_missing_phases
        and recurring_runtime_healthy
        and full_seed_runtime_healthy
        and quality_counts.get("HIGH", 0) == 0
        and quality_counts.get("CRITICAL", 0) == 0
        and len(cohort_rows) == 1
    )
    return (
        {
            "healthy": healthy,
            "database": {
                "path": str(path),
                "bytes": path.stat().st_size,
                "opened_read_only_immutable": True,
                "quick_check": quick_check,
                "foreign_key_errors": foreign_key_errors,
                "row_per_level_table_present": False,
                "append_only_trigger_count": trigger_count,
                "expected_append_only_trigger_count": len(APPEND_ONLY_TABLES) * 2,
            },
            "contract": contract,
            "seed_integrity": seed_integrity,
            "cadence": {
                "healthy": cadence_healthy,
                "rollout_health_start": (
                    iso_utc(rollout_start) if rollout_start is not None else None
                ),
                "effective_review_start": (
                    iso_utc(effective_start) if effective_start is not None else None
                ),
                "full_seed_runs_excluded": len(full_seed_started_run_ids),
                "successful_full_seed_runs": len(full_seed_success_run_ids),
                "expected_slots": len(expected_slots),
                "successful_unique_slots": len(success_slots),
                "success_coverage": success_coverage,
                "terminal_run_counts": terminal_counts,
                "duplicate_runs": duplicate_runs,
                "off_slot_runs": off_slot,
                "off_slot_ratio": off_slot_ratio,
            },
            "atomic_success_boundary": {
                "healthy": not cycle_without_success and not success_without_cycle,
                "cycles_without_succeeded": cycle_without_success,
                "succeeded_without_cycle": success_without_cycle,
            },
            "followup_coverage": coverage,
            "request_lineage": {
                "request_kind_counts": receipt_kinds,
                "forbidden_sampling_or_candidate_metadata_requests": (
                    forbidden_requests
                ),
                "compact_book_lineage_errors": book_lineage_errors,
                "failed_run_receipts_are_included": True,
            },
            "resolution_exclusion": {
                "later_resolution_requests": later_resolution_requests,
                "later_book_requests_for_resolved_conditions": later_book_requests,
            },
            "compact_book_integrity": blob_integrity,
            "unique_one_hot_resolution": one_hot,
            "phase_timings": {
                "recurring_pinned_fast": recurring_phase_report,
                "full_seed_maintenance": full_seed_phase_report,
            },
            "runtime_sla": {
                "network_cycle_deadline_seconds": network_deadline_seconds,
                "pinned_fast_hard_sla_seconds": pinned_fast_hard_sla_seconds,
                "full_seed_budget_seconds": full_seed_budget_seconds,
                "recurring_pinned_fast": {
                    **recurring_total_stats,
                    "healthy": recurring_runtime_healthy,
                },
                "full_seed_maintenance": {
                    **full_seed_total_stats,
                    "healthy": full_seed_runtime_healthy,
                },
                "full_seed_counts_as_recurring_sla_violation": False,
                "measurement": (
                    "v1_anchor_start_to_success_transaction_precommit"
                ),
            },
            "quality_issue_counts": quality_counts,
            "cohorts": cohort_rows,
            "storage_growth_and_forecast": storage,
        },
        anchor,
    )


def analyze_followup(
    v1_db_path: str | Path,
    v2a_db_path: str | Path,
    *,
    start: datetime,
    end: datetime,
    deep_v1: bool = False,
) -> dict[str, Any]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("analysis range must be timezone-aware")
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if start >= end:
        raise ValueError("analysis range must satisfy start < end")
    v1_path = _canonical_db(v1_db_path)
    v2a_path = _canonical_db(v2a_db_path)
    if v1_path == v2a_path:
        raise ValueError("v1 and v2a analysis databases must be distinct")
    v2a, anchor = _v2_report(v2a_path, start=start, end=end)
    v1 = _v1_report(v1_path, stored_anchor=anchor, deep_check=deep_v1)
    healthy = bool(v1["healthy"] and v2a["healthy"])
    return {
        "schema": ANALYSIS_SCHEMA,
        "generated_at": iso_utc(),
        "healthy": healthy,
        "review_range": {
            "start": iso_utc(start),
            "end_exclusive": iso_utc(end),
            "timezone": "UTC",
            "duration_days": (end - start).total_seconds() / 86400,
        },
        "v1": v1,
        "v2a": v2a,
        "interpretation": {
            "verdict": "HEALTH_ONLY",
            "profitability_claim_allowed": False,
            "parameter_selection_allowed": False,
            "live_promotion_allowed": False,
            "displayed_books_are_not_actual_fills": True,
            "missing_evidence_is_not_synthesized": True,
        },
    }


def write_followup_analysis(
    v1_db_path: str | Path,
    v2a_db_path: str | Path,
    *,
    start: str | datetime,
    end: str | datetime,
    output: str | Path,
    deep_v1: bool = False,
) -> dict[str, Any]:
    start_clock = parse_utc(start) if isinstance(start, str) else start
    end_clock = parse_utc(end) if isinstance(end, str) else end
    result = analyze_followup(
        v1_db_path,
        v2a_db_path,
        start=start_clock,
        end=end_clock,
        deep_v1=deep_v1,
    )
    destination = Path(output)
    if not destination.is_absolute():
        raise ValueError("analysis output path must be absolute")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return result


__all__ = [
    "ANALYSIS_SCHEMA",
    "analyze_followup",
    "write_followup_analysis",
]
