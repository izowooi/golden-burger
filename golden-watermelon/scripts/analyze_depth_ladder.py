#!/usr/bin/env python3
"""Read-only full-depth replay sidecar for Golden Watermelon v3c evidence."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Sequence

from polybot.config import (
    CLASSIFIER_VERSION,
    DATA_CONTRACT,
    LEAGUE_MAPPING_SHA256,
    SCHEMA_PROFILE,
    UNIVERSE_PROFILE,
    league_registry_payload,
)
from polybot.db.repository import (
    APPLICATION_ID,
    EXPECTED_SCHEMA_SHA256,
    MIGRATION_PATH,
    SCHEMA_USER_VERSION,
)


SIDECAR_CONTRACT = "golden-watermelon-depth-ladder-sidecar-v1"
DEPTH_LADDER_USDC = (
    5,
    10,
    15,
    20,
    25,
    30,
    40,
    50,
    75,
    100,
    150,
    250,
    500,
    750,
    1000,
)
ASK_STATES = ("FULL", "PARTIAL", "NO_DEPTH", "BOOK_UNAVAILABLE")
BID_STATES = ("FULL", "PARTIAL", "NO_DEPTH", "NOT_EVALUABLE")
QUALIFYING_ASK_VWAP = 0.95
EPSILON = 1e-9


@dataclass(frozen=True)
class CohortSelector:
    config_hash: str
    strategy_source_digest: str
    mode: str
    job_name: str

    def as_tuple(self) -> tuple[str, str, str, str]:
        return (
            self.config_hash,
            self.strategy_source_digest,
            self.mode,
            self.job_name,
        )


def _source_sha256() -> str:
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def _canonical_database_path(path: Path) -> Path:
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"database not found: {path}") from error
    if not resolved.is_file():
        raise ValueError(f"database is not a regular file: {resolved}")
    return resolved


def _connect_read_only(path: Path) -> sqlite3.Connection:
    resolved = _canonical_database_path(path)
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
            raise ValueError("SQLite query_only could not be enabled")
    except BaseException:
        connection.close()
        raise
    return connection


def _live_schema_sha256(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT type,name,tbl_name,sql
        FROM sqlite_master
        WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
        ORDER BY type,name,tbl_name
        """
    ).fetchall()
    payload = [tuple(str(value) for value in row) for row in rows]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _validate_database_contract(connection: sqlite3.Connection) -> dict[str, Any]:
    quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    if quick_check != "ok":
        raise ValueError(f"SQLite quick_check failed: {quick_check}")

    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if (application_id, user_version) != (APPLICATION_ID, SCHEMA_USER_VERSION):
        raise ValueError(
            "depth sidecar application/schema epoch mismatch: "
            f"application_id={application_id}, user_version={user_version}"
        )

    metadata_rows = connection.execute(
        """
        SELECT singleton,data_contract,schema_profile,universe_profile,
               classifier_version,league_mapping_sha256,migration_sha256,
               schema_sha256
        FROM schema_metadata
        """
    ).fetchall()
    migration_sha256 = hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest()
    expected_metadata = (
        1,
        DATA_CONTRACT,
        SCHEMA_PROFILE,
        UNIVERSE_PROFILE,
        CLASSIFIER_VERSION,
        LEAGUE_MAPPING_SHA256,
        migration_sha256,
        EXPECTED_SCHEMA_SHA256,
    )
    if len(metadata_rows) != 1 or tuple(metadata_rows[0]) != expected_metadata:
        actual = [tuple(row) for row in metadata_rows]
        raise ValueError(f"depth sidecar metadata contract mismatch: {actual!r}")
    if _live_schema_sha256(connection) != EXPECTED_SCHEMA_SHA256:
        raise ValueError("depth sidecar live schema fingerprint mismatch")

    registry_rows = connection.execute(
        """
        SELECT league_mapping_sha256,classifier_version,universe_profile,
               mapping_json
        FROM league_registry_versions
        """
    ).fetchall()
    expected_mapping = league_registry_payload()
    if len(registry_rows) != 1:
        raise ValueError("depth sidecar requires exactly one frozen league registry")
    registry = registry_rows[0]
    try:
        actual_mapping = json.loads(str(registry["mapping_json"]))
    except json.JSONDecodeError as error:
        raise ValueError("depth sidecar league registry is not valid JSON") from error
    if (
        str(registry["league_mapping_sha256"]) != LEAGUE_MAPPING_SHA256
        or str(registry["classifier_version"]) != CLASSIFIER_VERSION
        or str(registry["universe_profile"]) != UNIVERSE_PROFILE
        or actual_mapping != expected_mapping
    ):
        raise ValueError("depth sidecar frozen league registry mismatch")

    return {
        "quick_check": quick_check,
        "application_id": application_id,
        "user_version": user_version,
        "data_contract": DATA_CONTRACT,
        "schema_profile": SCHEMA_PROFILE,
        "universe_profile": UNIVERSE_PROFILE,
        "classifier_version": CLASSIFIER_VERSION,
        "league_mapping_sha256": LEAGUE_MAPPING_SHA256,
        "migration_sha256": migration_sha256,
        "schema_sha256": EXPECTED_SCHEMA_SHA256,
    }


def _available_cohorts(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT c.config_hash,c.strategy_source_digest,c.mode,c.job_name,
               c.preregistration_sha256,c.first_seen_at,
               COUNT(DISTINCT e.run_id) AS run_count,
               MAX(e.observed_at) AS latest_run_event_at
        FROM research_config_versions c
        JOIN research_run_events e
          ON e.config_hash=c.config_hash
         AND e.strategy_source_digest=c.strategy_source_digest
        GROUP BY c.config_hash,c.strategy_source_digest,c.mode,c.job_name,
                 c.preregistration_sha256,c.first_seen_at
        ORDER BY c.first_seen_at,c.config_hash,c.strategy_source_digest,
                 c.mode,c.job_name
        """
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def _cohort_tuple(cohort: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(
        str(cohort[key])
        for key in ("config_hash", "strategy_source_digest", "mode", "job_name")
    )  # type: ignore[return-value]


def _select_cohort(
    connection: sqlite3.Connection,
    *,
    selector: CohortSelector | None,
    latest_cohort: bool,
) -> dict[str, Any]:
    if selector is not None and latest_cohort:
        raise ValueError("explicit cohort selection cannot be combined with latest_cohort")
    cohorts = _available_cohorts(connection)
    if not cohorts:
        raise ValueError("database has no run-backed research cohort")

    if selector is not None:
        matches = [row for row in cohorts if _cohort_tuple(row) == selector.as_tuple()]
        if len(matches) != 1:
            raise ValueError(
                "explicit config_hash × strategy_source_digest × mode × job_name "
                f"cohort was not found: {selector.as_tuple()!r}"
            )
        selected = matches[0]
        selection = "EXPLICIT"
    elif latest_cohort:
        selected = max(
            cohorts,
            key=lambda row: (
                str(row["latest_run_event_at"]),
                str(row["first_seen_at"]),
                *_cohort_tuple(row),
            ),
        )
        selection = "LATEST_COHORT"
    elif len(cohorts) == 1:
        selected = cohorts[0]
        selection = "ONLY_COHORT"
    else:
        identities = [_cohort_tuple(row) for row in cohorts]
        raise ValueError(
            "multiple config_hash × strategy_source_digest × mode × job_name "
            "cohorts are present; select all four fields or pass --latest-cohort: "
            f"{identities!r}"
        )

    return {
        **selected,
        "config_hash": str(selected["config_hash"]),
        "strategy_source_digest": str(selected["strategy_source_digest"]),
        "mode": str(selected["mode"]),
        "job_name": str(selected["job_name"]),
        "preregistration_sha256": str(selected["preregistration_sha256"]),
        "first_seen_at": str(selected["first_seen_at"]),
        "latest_run_event_at": str(selected["latest_run_event_at"]),
        "run_count": int(selected["run_count"]),
        "selection": selection,
        "available_cohort_count": len(cohorts),
    }


def _eligible_observations(
    connection: sqlite3.Connection, cohort: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        WITH cohort_runs AS (
            SELECT DISTINCT run_id
            FROM research_run_events
            WHERE config_hash=? AND strategy_source_digest=?
        ),
        decision_rollup AS (
            SELECT run_id,token_id,
                   COUNT(*) AS decision_row_count,
                   COUNT(DISTINCT threshold) AS decision_threshold_count
            FROM signal_decisions
            WHERE run_id IN (SELECT run_id FROM cohort_runs)
            GROUP BY run_id,token_id
        )
        SELECT o.run_id,o.event_id,o.condition_id,o.token_id,o.outcome_index,
               o.outcome_label,o.observed_at AS outcome_observed_at,
               a.status AS book_attempt_status,a.request_id AS attempt_request_id,
               a.observed_at AS attempt_observed_at,a.error_type,a.error_message,
               s.snapshot_id,s.request_id AS snapshot_request_id,
               s.observed_at AS snapshot_observed_at,s.raw_book_sha256,
               s.source_timestamp,s.tick_size,s.min_order_size,
               COALESCE(d.decision_row_count,0) AS decision_row_count,
               COALESCE(d.decision_threshold_count,0) AS decision_threshold_count
        FROM outcome_observations o
        LEFT JOIN orderbook_token_attempts a
          ON a.run_id=o.run_id AND a.token_id=o.token_id
        LEFT JOIN orderbook_snapshots s
          ON s.run_id=o.run_id AND s.token_id=o.token_id
        LEFT JOIN decision_rollup d
          ON d.run_id=o.run_id AND d.token_id=o.token_id
        WHERE o.entry_eligible=1
          AND o.run_id IN (SELECT run_id FROM cohort_runs)
        ORDER BY o.run_id,o.token_id
        """,
        (cohort["config_hash"], cohort["strategy_source_digest"]),
    ).fetchall()
    result = [{key: row[key] for key in row.keys()} for row in rows]
    keys = [(str(row["run_id"]), str(row["token_id"])) for row in result]
    duplicates = [key for key, count in Counter(keys).items() if count != 1]
    if duplicates:
        raise ValueError(f"eligible run × token identity is not unique: {duplicates!r}")
    return result


def _book_levels(
    connection: sqlite3.Connection, cohort: dict[str, Any]
) -> dict[str, dict[str, list[tuple[float, float]]]]:
    rows = connection.execute(
        """
        WITH cohort_runs AS (
            SELECT DISTINCT run_id
            FROM research_run_events
            WHERE config_hash=? AND strategy_source_digest=?
        )
        SELECT s.snapshot_id,l.side,l.level_index,l.price,l.size
        FROM orderbook_snapshots s
        JOIN orderbook_levels l USING(snapshot_id)
        WHERE s.run_id IN (SELECT run_id FROM cohort_runs)
          AND EXISTS (
              SELECT 1
              FROM outcome_observations o
              WHERE o.run_id=s.run_id AND o.token_id=s.token_id
                AND o.entry_eligible=1
          )
        ORDER BY s.snapshot_id,l.side,l.level_index
        """,
        (cohort["config_hash"], cohort["strategy_source_digest"]),
    ).fetchall()
    levels: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(
        lambda: {"ASK": [], "BID": []}
    )
    indexes: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in rows:
        snapshot_id = str(row["snapshot_id"])
        side = str(row["side"])
        price = float(row["price"])
        size = float(row["size"])
        if side not in {"ASK", "BID"}:
            raise ValueError(f"unknown orderbook side at {snapshot_id}: {side!r}")
        if not math.isfinite(price) or not math.isfinite(size):
            raise ValueError(f"non-finite normalized level at {snapshot_id}")
        if not 0 < price <= 1 or size <= 0:
            raise ValueError(f"invalid normalized level at {snapshot_id}: {(price, size)!r}")
        levels[snapshot_id][side].append((price, size))
        indexes[(snapshot_id, side)].append(int(row["level_index"]))

    for (snapshot_id, side), observed_indexes in indexes.items():
        if observed_indexes != list(range(len(observed_indexes))):
            raise ValueError(
                f"non-contiguous {side} level indexes at snapshot {snapshot_id}"
            )
        prices = [price for price, _size in levels[snapshot_id][side]]
        expected = sorted(prices, reverse=side == "BID")
        if prices != expected:
            raise ValueError(f"non-normalized {side} price order at {snapshot_id}")
    return dict(levels)


def _ask_replay(
    levels: list[tuple[float, float]],
    notional: float,
    *,
    book_available: bool,
) -> dict[str, Any]:
    if not book_available:
        return {
            "ask_state": "BOOK_UNAVAILABLE",
            "ask_requested_cash": notional,
            "ask_spent_cash": 0.0,
            "ask_unspent_cash": notional,
            "ask_filled_shares": 0.0,
            "ask_vwap": None,
            "ask_worst_price": None,
            "ask_levels_used": 0,
        }
    if not levels:
        return {
            "ask_state": "NO_DEPTH",
            "ask_requested_cash": notional,
            "ask_spent_cash": 0.0,
            "ask_unspent_cash": notional,
            "ask_filled_shares": 0.0,
            "ask_vwap": None,
            "ask_worst_price": None,
            "ask_levels_used": 0,
        }

    remaining = notional
    shares = 0.0
    spent = 0.0
    worst_price: float | None = None
    levels_used = 0
    for price, size in levels:
        consumed_cash = min(remaining, price * size)
        if consumed_cash <= 0:
            continue
        spent += consumed_cash
        shares += consumed_cash / price
        remaining -= consumed_cash
        worst_price = price
        levels_used += 1
        if remaining <= EPSILON:
            remaining = 0.0
            spent = notional
            break
    state = "FULL" if remaining <= EPSILON else "PARTIAL"
    return {
        "ask_state": state,
        "ask_requested_cash": notional,
        "ask_spent_cash": spent,
        "ask_unspent_cash": max(0.0, notional - spent),
        "ask_filled_shares": shares,
        "ask_vwap": spent / shares if shares > 0 else None,
        "ask_worst_price": worst_price,
        "ask_levels_used": levels_used,
    }


def _bid_replay(
    levels: list[tuple[float, float]], ask: dict[str, Any]
) -> dict[str, Any]:
    requested_shares = float(ask["ask_filled_shares"])
    if requested_shares <= EPSILON:
        return {
            "immediate_bid_state": "NOT_EVALUABLE",
            "immediate_bid_requested_shares": requested_shares,
            "immediate_bid_filled_shares": 0.0,
            "immediate_bid_residual_shares": requested_shares,
            "immediate_bid_gross_proceeds": 0.0,
            "immediate_bid_vwap": None,
            "immediate_bid_worst_price": None,
            "immediate_bid_levels_used": 0,
        }
    if not levels:
        return {
            "immediate_bid_state": "NO_DEPTH",
            "immediate_bid_requested_shares": requested_shares,
            "immediate_bid_filled_shares": 0.0,
            "immediate_bid_residual_shares": requested_shares,
            "immediate_bid_gross_proceeds": 0.0,
            "immediate_bid_vwap": None,
            "immediate_bid_worst_price": None,
            "immediate_bid_levels_used": 0,
        }

    remaining = requested_shares
    filled = 0.0
    proceeds = 0.0
    worst_price: float | None = None
    levels_used = 0
    for price, size in levels:
        consumed_shares = min(remaining, size)
        if consumed_shares <= 0:
            continue
        filled += consumed_shares
        proceeds += consumed_shares * price
        remaining -= consumed_shares
        worst_price = price
        levels_used += 1
        if remaining <= EPSILON:
            remaining = 0.0
            filled = requested_shares
            break
    state = "FULL" if remaining <= EPSILON else "PARTIAL"
    return {
        "immediate_bid_state": state,
        "immediate_bid_requested_shares": requested_shares,
        "immediate_bid_filled_shares": filled,
        "immediate_bid_residual_shares": max(0.0, requested_shares - filled),
        "immediate_bid_gross_proceeds": proceeds,
        "immediate_bid_vwap": proceeds / filled if filled > 0 else None,
        "immediate_bid_worst_price": worst_price,
        "immediate_bid_levels_used": levels_used,
    }


def _replay_rows(
    *,
    database: Path,
    cohort: dict[str, Any],
    observations: list[dict[str, Any]],
    levels_by_snapshot: dict[str, dict[str, list[tuple[float, float]]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for observation in observations:
        snapshot_id = (
            str(observation["snapshot_id"])
            if observation["snapshot_id"] is not None
            else None
        )
        attempt_status = str(observation["book_attempt_status"] or "NO_ATTEMPT")
        book_available = attempt_status == "OBSERVED" and snapshot_id is not None
        snapshot_levels = levels_by_snapshot.get(
            snapshot_id or "", {"ASK": [], "BID": []}
        )
        five_ask = _ask_replay(
            snapshot_levels["ASK"], 5.0, book_available=book_available
        )
        qualifies = (
            five_ask["ask_state"] == "FULL"
            and float(five_ask["ask_vwap"]) >= QUALIFYING_ASK_VWAP - EPSILON
        )
        identity = {
            "db": str(database),
            "config_hash": cohort["config_hash"],
            "strategy_source_digest": cohort["strategy_source_digest"],
            "mode": cohort["mode"],
            "job_name": cohort["job_name"],
            "run_id": str(observation["run_id"]),
            "event_id": str(observation["event_id"]),
            "condition_id": str(observation["condition_id"]),
            "token_id": str(observation["token_id"]),
            "outcome_index": int(observation["outcome_index"]),
            "outcome_label": str(observation["outcome_label"]),
            "outcome_observed_at": observation["outcome_observed_at"],
            "book_attempt_status": attempt_status,
            "attempt_request_id": observation["attempt_request_id"],
            "attempt_observed_at": observation["attempt_observed_at"],
            "attempt_error_type": observation["error_type"],
            "attempt_error_message": observation["error_message"],
            "snapshot_id": snapshot_id,
            "snapshot_request_id": observation["snapshot_request_id"],
            "snapshot_observed_at": observation["snapshot_observed_at"],
            "raw_book_sha256": observation["raw_book_sha256"],
            "source_timestamp": observation["source_timestamp"],
            "tick_size": observation["tick_size"],
            "min_order_size": observation["min_order_size"],
            "collapsed_decision_rows": int(observation["decision_row_count"]),
            "decision_threshold_count": int(
                observation["decision_threshold_count"]
            ),
            "in_all_eligible_denominator": True,
            "in_full_5_usdc_ask_vwap_gte_0_95_denominator": qualifies,
        }
        for notional in DEPTH_LADDER_USDC:
            ask = _ask_replay(
                snapshot_levels["ASK"], float(notional), book_available=book_available
            )
            bid = _bid_replay(snapshot_levels["BID"], ask)
            full_round_trip_return = None
            if ask["ask_state"] == "FULL" and bid["immediate_bid_state"] == "FULL":
                full_round_trip_return = (
                    float(bid["immediate_bid_gross_proceeds"]) / float(notional) - 1
                )
            rows.append(
                {
                    **identity,
                    "notional_usdc": notional,
                    **ask,
                    **bid,
                    "full_round_trip_return": full_round_trip_return,
                }
            )

    expected_rows = len(observations) * len(DEPTH_LADDER_USDC)
    if len(rows) != expected_rows:
        raise RuntimeError(
            f"depth ladder cardinality mismatch: {len(rows)} != {expected_rows}"
        )
    return rows


def _state_counts(
    rows: Iterable[dict[str, Any]], field: str, states: Sequence[str]
) -> dict[str, int]:
    counts = Counter(str(row[field]) for row in rows)
    return {state: counts.get(state, 0) for state in states}


def _denominator_summary(
    rows: list[dict[str, Any]],
    *,
    membership_field: str,
    definition: str,
) -> dict[str, Any]:
    selected = [row for row in rows if bool(row[membership_field])]
    base_rows = [row for row in selected if int(row["notional_usdc"]) == 5]
    base_keys = {
        (str(row["db"]), str(row["run_id"]), str(row["token_id"]))
        for row in base_rows
    }
    unique_event_keys = {
        (str(row["db"]), str(row["event_id"])) for row in base_rows
    }
    unique_condition_keys = {
        (str(row["db"]), str(row["condition_id"])) for row in base_rows
    }
    unique_token_keys = {
        (str(row["db"]), str(row["token_id"])) for row in base_rows
    }
    distinct_event_ids = {str(row["event_id"]) for row in base_rows}
    distinct_condition_ids = {str(row["condition_id"]) for row in base_rows}
    distinct_token_ids = {str(row["token_id"]) for row in base_rows}
    expected_rows = len(base_keys) * len(DEPTH_LADDER_USDC)
    if len(selected) != expected_rows:
        raise RuntimeError(
            "denominator does not contain exactly one run × token × notional row: "
            f"{len(selected)} != {expected_rows}"
        )

    by_notional: dict[str, Any] = {}
    for notional in DEPTH_LADDER_USDC:
        notional_rows = [
            row for row in selected if int(row["notional_usdc"]) == notional
        ]
        by_notional[str(notional)] = {
            "row_count": len(notional_rows),
            "ask_states": _state_counts(notional_rows, "ask_state", ASK_STATES),
            "immediate_bid_states": _state_counts(
                notional_rows, "immediate_bid_state", BID_STATES
            ),
            "full_round_trip_return_count": sum(
                row["full_round_trip_return"] is not None for row in notional_rows
            ),
        }
    attempt_counts = Counter(str(row["book_attempt_status"]) for row in base_rows)
    return {
        "definition": definition,
        "eligible_run_token_count": len(base_keys),
        "database_scoped_unique_event_count": len(unique_event_keys),
        "database_scoped_unique_condition_count": len(unique_condition_keys),
        "database_scoped_unique_token_count": len(unique_token_keys),
        "cross_database_distinct_event_id_count": len(distinct_event_ids),
        "cross_database_distinct_condition_id_count": len(distinct_condition_ids),
        "cross_database_distinct_token_id_count": len(distinct_token_ids),
        "ladder_row_count": len(selected),
        "expected_ladder_row_count": expected_rows,
        "book_attempt_status_counts": dict(sorted(attempt_counts.items())),
        "by_notional_usdc": by_notional,
    }


def _denominators(rows: list[dict[str, Any]]) -> dict[str, Any]:
    all_eligible = _denominator_summary(
        rows,
        membership_field="in_all_eligible_denominator",
        definition=(
            "every entry_eligible run × token, including failed, malformed, empty, "
            "missing, or absent book attempts"
        ),
    )
    qualifying = _denominator_summary(
        rows,
        membership_field="in_full_5_usdc_ask_vwap_gte_0_95_denominator",
        definition="the $5 ask replay is FULL and its VWAP is >= 0.95",
    )
    total = int(all_eligible["eligible_run_token_count"])
    selected = int(qualifying["eligible_run_token_count"])
    qualifying["pct_of_all_eligible"] = selected / total if total else None
    return {
        "all_eligible": all_eligible,
        "full_5_usdc_ask_vwap_gte_0_95": qualifying,
    }


def analyze_database(
    path: Path,
    *,
    config_hash: str | None = None,
    strategy_source_digest: str | None = None,
    mode: str | None = None,
    job_name: str | None = None,
    latest_cohort: bool = False,
) -> dict[str, Any]:
    selector_values = (config_hash, strategy_source_digest, mode, job_name)
    if any(value is not None for value in selector_values) and not all(
        value is not None for value in selector_values
    ):
        raise ValueError(
            "explicit cohort selection requires config_hash, "
            "strategy_source_digest, mode, and job_name"
        )
    selector = (
        CohortSelector(*(str(value) for value in selector_values))
        if all(value is not None for value in selector_values)
        else None
    )
    resolved = _canonical_database_path(path)
    connection = _connect_read_only(resolved)
    try:
        contract = _validate_database_contract(connection)
        cohort = _select_cohort(
            connection, selector=selector, latest_cohort=latest_cohort
        )
        observations = _eligible_observations(connection, cohort)
        levels = _book_levels(connection, cohort)
    finally:
        connection.close()

    rows = _replay_rows(
        database=resolved,
        cohort=cohort,
        observations=observations,
        levels_by_snapshot=levels,
    )
    return {
        "sidecar_contract": SIDECAR_CONTRACT,
        "sidecar_source_sha256": _source_sha256(),
        "interpretation": "DISPLAYED_BOOK_COUNTERFACTUAL_ONLY_NOT_GUARANTEED_FILL",
        "db": str(resolved),
        "read_only": {"sqlite_uri_mode": "ro", "query_only": True},
        **contract,
        "cohort": cohort,
        "ladder_usdc": list(DEPTH_LADDER_USDC),
        "eligible_run_token_count": len(observations),
        "ladder_row_count": len(rows),
        "denominators": _denominators(rows),
        "rows": rows,
    }


def analyze_databases(
    paths: Iterable[Path],
    *,
    cohorts: Sequence[CohortSelector] | None = None,
    latest_cohort: bool = False,
) -> dict[str, Any]:
    materialized = [Path(path) for path in paths]
    if not materialized:
        raise ValueError("at least one database is required")
    if cohorts is not None and len(cohorts) != len(materialized):
        raise ValueError("one explicit cohort selector is required per database")
    reports: list[dict[str, Any]] = []
    for index, path in enumerate(materialized):
        selector = cohorts[index] if cohorts is not None else None
        reports.append(
            analyze_database(
                path,
                config_hash=selector.config_hash if selector else None,
                strategy_source_digest=(
                    selector.strategy_source_digest if selector else None
                ),
                mode=selector.mode if selector else None,
                job_name=selector.job_name if selector else None,
                latest_cohort=latest_cohort,
            )
        )
    source_digests = {
        str(report["cohort"]["strategy_source_digest"]) for report in reports
    }
    if len(reports) > 1 and len(source_digests) != 1:
        raise ValueError(
            "paired strategy_source_digest mismatch: "
            f"{sorted(source_digests)!r}"
        )
    rows = [row for report in reports for row in report["rows"]]
    return {
        "sidecar_contract": SIDECAR_CONTRACT,
        "sidecar_source_sha256": _source_sha256(),
        "interpretation": "DISPLAYED_BOOK_COUNTERFACTUAL_ONLY_NOT_GUARANTEED_FILL",
        "database_count": len(reports),
        "paired_strategy_source_digest": (
            next(iter(source_digests)) if len(reports) > 1 else None
        ),
        "ladder_usdc": list(DEPTH_LADDER_USDC),
        "eligible_run_token_count": sum(
            int(report["eligible_run_token_count"]) for report in reports
        ),
        "ladder_row_count": len(rows),
        "denominators": _denominators(rows),
        "databases": reports,
    }


def _selectors_from_args(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> list[CohortSelector] | None:
    values = (
        args.config_hash,
        args.strategy_source_digest,
        args.mode,
        args.job_name,
    )
    if args.latest_cohort and any(value for value in values):
        parser.error("--latest-cohort cannot be combined with explicit cohort fields")
    if not any(value for value in values):
        return None
    if not all(value for value in values):
        parser.error(
            "explicit selection requires --config-hash, --strategy-source-digest, "
            "--mode, and --job-name"
        )
    if any(len(value) != len(args.db) for value in values):
        parser.error("repeat every explicit cohort field once per --db, in DB order")
    return [
        CohortSelector(
            args.config_hash[index],
            args.strategy_source_digest[index],
            args.mode[index],
            args.job_name[index],
        )
        for index in range(len(args.db))
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay the independent Golden Watermelon depth ladder read-only"
    )
    parser.add_argument("--db", type=Path, action="append", required=True)
    parser.add_argument("--config-hash", action="append")
    parser.add_argument("--strategy-source-digest", action="append")
    parser.add_argument("--mode", action="append")
    parser.add_argument("--job-name", action="append")
    parser.add_argument("--latest-cohort", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    selectors = _selectors_from_args(args, parser)
    result = (
        analyze_database(
            args.db[0],
            config_hash=selectors[0].config_hash if selectors else None,
            strategy_source_digest=(
                selectors[0].strategy_source_digest if selectors else None
            ),
            mode=selectors[0].mode if selectors else None,
            job_name=selectors[0].job_name if selectors else None,
            latest_cohort=args.latest_cohort,
        )
        if len(args.db) == 1
        else analyze_databases(
            args.db, cohorts=selectors, latest_cohort=args.latest_cohort
        )
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = args.output.expanduser().resolve()
        source_databases = {_canonical_database_path(path) for path in args.db}
        if output in source_databases:
            parser.error("--output must not overwrite a source database")
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
