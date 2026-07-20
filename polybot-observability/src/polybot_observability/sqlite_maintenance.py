"""Safe, opt-in compaction for strategy SQLite databases.

Trading and execution evidence is never removed.  The compact-v1 profile only
rolls up high-volume market telemetry (snapshots and detailed sweep members),
then keeps doing the same bounded maintenance after the one-time migration.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)

PROFILE = "compact-v1"
SCHEMA_VERSION = 2
ENV_PROFILE = "POLYBOT_DB_MAINTENANCE"
ENV_BACKUP_DIR = "POLYBOT_DB_BACKUP_DIR"
ENV_HOT_HOURS = "POLYBOT_DB_HOT_HOURS"
ENV_ROLLUP_HOURS = "POLYBOT_DB_ROLLUP_HOURS"
ENV_RETENTION_DAYS = "POLYBOT_DB_RETENTION_DAYS"
ENV_RUN_INTERVAL_HOURS = "POLYBOT_DB_MAINTENANCE_INTERVAL_HOURS"
ENV_MEMBERSHIP_DETAIL_HOURS = "POLYBOT_DB_MEMBERSHIP_DETAIL_HOURS"

_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS polybot_db_maintenance (
    profile TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    active INTEGER NOT NULL,
    activated_at TEXT NOT NULL,
    last_maintained_at TEXT,
    last_report_json TEXT NOT NULL DEFAULT '{}'
)
"""

_PROTECTED_TABLES = (
    "trades",
    "strategy_configs",
    "run_audits",
    "order_submissions",
    "order_status_events",
    "order_fills",
    "quantity_scale_repairs",
)


@dataclass(frozen=True)
class SQLiteMaintenancePolicy:
    strategy_name: str
    hot_hours: float
    rollup_hours: float
    retention_days: float
    selector: str
    run_interval_hours: float
    membership_detail_hours: float


@dataclass(frozen=True)
class SQLiteMaintenanceRequirements:
    """Resolved strategy windows that compact telemetry must preserve exactly."""

    full_cadence_hours: float = 0.0
    retention_days: float = 0.0
    boundary_interval_hours: float | None = None
    max_rollup_hours: float | None = None
    minimum_latest_points: int = 0


@dataclass(frozen=True)
class SQLiteMaintenanceReport:
    strategy_name: str
    profile: str
    snapshots_before: int
    snapshots_after: int
    memberships_before: int
    memberships_after: int
    sweeps_before: int
    sweeps_after: int
    bytes_before: int
    bytes_after: int
    backup_path: str | None
    backup_sha256: str | None


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive number") from error
    if not math.isfinite(value) or not (value > 0):
        raise ValueError(f"{name} must be a positive number")
    return value


def _validate_requirements(
    policy: SQLiteMaintenancePolicy,
    requirements: SQLiteMaintenanceRequirements,
) -> None:
    values = {
        "full_cadence_hours": requirements.full_cadence_hours,
        "retention_days": requirements.retention_days,
    }
    if requirements.boundary_interval_hours is not None:
        values["boundary_interval_hours"] = requirements.boundary_interval_hours
    if requirements.max_rollup_hours is not None:
        values["max_rollup_hours"] = requirements.max_rollup_hours
    for name, raw_value in values.items():
        value = float(raw_value)
        if not math.isfinite(value) or value < 0:
            raise ValueError(
                f"SQLite maintenance requirement {name} must be finite and >= 0"
            )
    latest_points = requirements.minimum_latest_points
    if (
        isinstance(latest_points, bool)
        or not isinstance(latest_points, int)
        or latest_points < 0
    ):
        raise ValueError(
            "SQLite maintenance requirement minimum_latest_points must be "
            "an integer >= 0"
        )
    if policy.hot_hours < requirements.full_cadence_hours:
        raise ValueError(
            f"{ENV_HOT_HOURS}={policy.hot_hours}h cannot cover the resolved "
            f"strategy full-cadence window {requirements.full_cadence_hours}h"
        )
    if policy.retention_days < requirements.retention_days:
        raise ValueError(
            f"{ENV_RETENTION_DAYS}={policy.retention_days}d cannot cover the "
            f"resolved strategy retention window {requirements.retention_days}d"
        )
    boundary = requirements.boundary_interval_hours
    if boundary is not None and boundary < policy.rollup_hours:
        raise ValueError(
            f"resolved strategy boundary interval {boundary}h must be at least "
            f"one {ENV_ROLLUP_HOURS} bucket ({policy.rollup_hours}h)"
        )
    max_rollup = requirements.max_rollup_hours
    if max_rollup is not None and policy.rollup_hours > max_rollup:
        raise ValueError(
            f"{ENV_ROLLUP_HOURS}={policy.rollup_hours}h exceeds the resolved "
            f"strategy maximum rollup gap {max_rollup}h"
        )


def requirements_for(strategy_name: str) -> SQLiteMaintenanceRequirements:
    """Resolve signal windows from legacy environment-compatible defaults."""

    normalized = str(strategy_name).strip().lower()
    if normalized == "golden-banana":
        long_window = _positive_float("POLYBOT_MOMENTUM_LONG_WINDOW", 72.0)
        if not long_window.is_integer():
            raise ValueError("POLYBOT_MOMENTUM_LONG_WINDOW must be an integer")
        return SQLiteMaintenanceRequirements(
            full_cadence_hours=max(5.0 / 60.0, (long_window - 1.0) * 5.0 / 60.0),
            # Scanner/trader request long_window + 10 rows. Preserve that
            # exact tail even when an outage makes those rows older than the
            # time-based hot/retention windows.
            minimum_latest_points=int(long_window) + 10,
        )
    if normalized in {"golden-date", "golden-mango"}:
        return SQLiteMaintenanceRequirements(
            full_cadence_hours=_positive_float("POLYBOT_MOMENTUM_LOOKBACK_HOURS", 6.0)
        )
    if normalized == "golden-elderberry":
        reference_hours = _positive_float("POLYBOT_REF_WINDOW_HOURS", 48.0)
        reference_exclude_hours = _positive_float(
            "POLYBOT_REF_EXCLUDE_RECENT_HOURS", 3.0
        )
        return SQLiteMaintenanceRequirements(
            full_cadence_hours=_positive_float("POLYBOT_STAB_WINDOW_MINUTES", 45.0)
            / 60.0,
            retention_days=reference_hours / 24.0,
            boundary_interval_hours=reference_hours - reference_exclude_hours,
            max_rollup_hours=reference_hours * (1.0 - 0.5),
        )
    if normalized == "golden-fig":
        return SQLiteMaintenanceRequirements(
            full_cadence_hours=_positive_float("POLYBOT_RISE_LOOKBACK_HOURS", 24.0)
        )
    if normalized == "golden-grape":
        return SQLiteMaintenanceRequirements(
            full_cadence_hours=max(
                _positive_float("POLYBOT_DRIFT_LOOKBACK_HOURS", 24.0),
                _positive_float("POLYBOT_DEATH_WINDOW_HOURS", 6.0),
            )
        )
    if normalized == "golden-honeydew":
        return SQLiteMaintenanceRequirements(
            full_cadence_hours=_positive_float("POLYBOT_MEDIAN_LOOKBACK_HOURS", 24.0)
        )
    if normalized == "golden-lime":
        return SQLiteMaintenanceRequirements(
            full_cadence_hours=max(
                24.0,
                _positive_float("POLYBOT_JUMP_WINDOW_HOURS", 6.0),
                _positive_float("POLYBOT_DEATH_WINDOW_HOURS", 3.0),
            )
        )
    if normalized == "golden-nectarine":
        lookback_days = _positive_float("POLYBOT_LOOKBACK_DAYS", 20.0)
        exclude_recent_hours = _positive_float("POLYBOT_EXCLUDE_RECENT_HOURS", 24.0)
        return SQLiteMaintenanceRequirements(
            retention_days=lookback_days,
            boundary_interval_hours=lookback_days * 24.0 - exclude_recent_hours,
            max_rollup_hours=lookback_days * 24.0 * (1.0 - 0.95),
        )
    if normalized == "golden-orange":
        base_window_days = _positive_float("POLYBOT_BASE_WINDOW_DAYS", 7.0)
        return SQLiteMaintenanceRequirements(
            full_cadence_hours=base_window_days * 24.0,
            retention_days=base_window_days,
        )
    if normalized == "golden-papaya":
        return SQLiteMaintenanceRequirements(
            full_cadence_hours=_positive_float("POLYBOT_MAX_SNAPSHOT_GAP_MINUTES", 30.0)
            / 60.0,
            retention_days=_positive_float("POLYBOT_SNAPSHOT_RETENTION_DAYS", 60.0),
        )
    return SQLiteMaintenanceRequirements()


def policy_for(
    strategy_name: str,
    requirements: SQLiteMaintenanceRequirements | None = None,
) -> SQLiteMaintenancePolicy:
    """Resolve a conservative strategy-aware telemetry policy.

    The hot window is kept at full cadence so live signal calculations are not
    changed.  Only observations older than that window are rolled up.
    """

    normalized = str(strategy_name).strip().lower()
    hot_defaults = {
        "golden-apple": 24.0,
        "golden-banana": 24.0,
        "golden-cherry": 72.0,
        "golden-date": 24.0,
        # Panic Fade needs raw points only for its 45-minute stabilization
        # window. Older prefix/suffix extrema preserve the exact 48h favorite
        # peak (YES max and, through inversion, NO max).
        "golden-elderberry": 1.0,
        "golden-fig": 24.0,
        "golden-grape": 24.0,
        "golden-honeydew": 24.0,
        "golden-lime": 24.0,
        "golden-mango": 24.0,
        # Bottom Fisher excludes the most recent 24h from its reference low.
        # One hour of raw observations is enough for current-cycle continuity;
        # older prefix/suffix minima below preserve exact sliding-window lows.
        "golden-nectarine": 1.0,
        # Fear Spike Fade computes an unweighted 7-day median.  That statistic
        # cannot be reconstructed from extrema/latest rollups, so its complete
        # base window stays at full cadence.
        "golden-orange": 7.0 * 24.0,
        # Papaya needs the immediately previous <=30 minute observation at
        # full fidelity; older extrema preserve the never-crossed predicate.
        "golden-papaya": 1.0,
    }
    retention_defaults = {
        "golden-honeydew": 60.0,
        "golden-nectarine": 60.0,
        "golden-orange": 21.0,
        "golden-papaya": 60.0,
    }
    selector = "latest"
    if normalized == "golden-nectarine":
        selector = "minimum"
    elif normalized in {"golden-elderberry", "golden-papaya"}:
        selector = "extrema"
    policy = SQLiteMaintenancePolicy(
        strategy_name=normalized,
        hot_hours=_positive_float(ENV_HOT_HOURS, hot_defaults.get(normalized, 24.0)),
        rollup_hours=_positive_float(ENV_ROLLUP_HOURS, 12.0),
        retention_days=_positive_float(
            ENV_RETENTION_DAYS, retention_defaults.get(normalized, 7.0)
        ),
        selector=selector,
        run_interval_hours=_positive_float(ENV_RUN_INTERVAL_HOURS, 1.0),
        membership_detail_hours=_positive_float(ENV_MEMBERSHIP_DETAIL_HOURS, 24.0),
    )
    if policy.retention_days * 24.0 < policy.hot_hours:
        raise ValueError(f"{ENV_RETENTION_DAYS} must cover {ENV_HOT_HOURS}")
    resolved_requirements = requirements or requirements_for(normalized)
    _validate_requirements(policy, resolved_requirements)
    return policy


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _quote_identifier(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        raise ValueError(f"unsafe SQLite identifier: {identifier!r}")
    return f'"{identifier}"'


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table'"
        )
    }


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")
    }


def _count(connection: sqlite3.Connection, table: str) -> int:
    if table not in _tables(connection):
        return 0
    return int(
        connection.execute(
            f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
        ).fetchone()[0]
    )


def _protected_counts(connection: sqlite3.Connection) -> dict[str, int]:
    existing = _tables(connection)
    return {
        table: int(
            connection.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
            ).fetchone()[0]
        )
        for table in _PROTECTED_TABLES
        if table in existing
    }


def _snapshot_lineage_gaps(connection: sqlite3.Connection) -> dict[str, int]:
    """Count pre-existing/current Papaya snapshot reference gaps.

    Comparing these counts before and after compaction makes a dangling
    reference regression fail the migration even though the protected trade
    row count itself would remain unchanged.
    """

    existing = _tables(connection)
    if not {"trades", "market_snapshots"}.issubset(existing):
        return {}
    trade_columns = _columns(connection, "trades")
    snapshot_columns = _columns(connection, "market_snapshots")
    if "entry_snapshot_id" not in trade_columns or not {
        "id",
        "condition_id",
        "timestamp",
    }.issubset(snapshot_columns):
        return {}
    entry_missing = int(
        connection.execute(
            "SELECT COUNT(*) FROM trades AS trade "
            "WHERE trade.entry_snapshot_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM market_snapshots AS entry "
            "WHERE entry.id = trade.entry_snapshot_id)"
        ).fetchone()[0]
    )
    prior_missing = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM trades AS trade
            JOIN market_snapshots AS entry ON entry.id = trade.entry_snapshot_id
            WHERE trade.entry_snapshot_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM market_snapshots AS prior
                  WHERE prior.condition_id = entry.condition_id
                    AND (
                        prior.timestamp < entry.timestamp
                        OR (
                            prior.timestamp = entry.timestamp
                            AND prior.id < entry.id
                        )
                    )
              )
            """
        ).fetchone()[0]
    )
    gaps = {
        "entry_snapshot_missing": entry_missing,
        "prior_snapshot_missing": prior_missing,
    }
    if "prior_snapshot_id_at_entry" in trade_columns:
        gaps["explicit_prior_snapshot_invalid"] = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM trades AS trade
                LEFT JOIN market_snapshots AS entry
                  ON entry.id = trade.entry_snapshot_id
                LEFT JOIN market_snapshots AS prior
                  ON prior.id = trade.prior_snapshot_id_at_entry
                WHERE trade.prior_snapshot_id_at_entry IS NOT NULL
                  AND (
                      entry.id IS NULL
                      OR prior.id IS NULL
                      OR prior.condition_id != entry.condition_id
                      OR prior.timestamp > entry.timestamp
                      OR (
                          prior.timestamp = entry.timestamp
                          AND prior.id >= entry.id
                      )
                  )
                """
            ).fetchone()[0]
        )
    return gaps


def _quick_check(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA quick_check").fetchone()
    if result is None or str(result[0]).lower() != "ok":
        raise RuntimeError(f"SQLite quick_check failed: {result!r}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_path(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _backup_root(strategy_name: str) -> Path:
    configured = os.getenv(ENV_BACKUP_DIR)
    root = (
        Path(configured).expanduser()
        if configured and configured.strip()
        else Path.home() / ".polybot" / "db-backups"
    )
    target = root / strategy_name
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        target.chmod(0o700)
    except OSError:
        pass
    return target


def _online_backup(
    source: Path,
    destination: Path,
    *,
    reserved_destination: bool = False,
) -> None:
    if reserved_destination:
        if not destination.exists() or destination.stat().st_size != 0:
            raise RuntimeError(f"reserved backup destination is invalid: {destination}")
    else:
        destination.unlink(missing_ok=True)
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection, pages=8192)
        destination_connection.commit()
        _quick_check(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    destination.chmod(0o600)


def _ensure_sweep_detail_column(connection: sqlite3.Connection) -> None:
    if "market_sweeps" not in _tables(connection):
        return
    if "membership_detail_stored" not in _columns(connection, "market_sweeps"):
        connection.execute(
            "ALTER TABLE market_sweeps ADD COLUMN "
            "membership_detail_stored INTEGER NOT NULL DEFAULT 1"
        )


def _build_protected_snapshot_ids(
    connection: sqlite3.Connection,
    requirements: SQLiteMaintenanceRequirements,
) -> None:
    """Protect immutable trade lineage from telemetry retention/rollup.

    Papaya stores the current entry snapshot ID on the trade.  Its immediately
    preceding snapshot is equally important because the strategy proves a
    <=30 minute crossing from that row.  The temporary set is harmless for
    strategies whose trade schema has no ``entry_snapshot_id`` column.
    """

    connection.execute("DROP TABLE IF EXISTS temp._polybot_protected_snapshot_ids")
    connection.execute(
        "CREATE TEMP TABLE _polybot_protected_snapshot_ids "
        "(id INTEGER PRIMARY KEY) WITHOUT ROWID"
    )
    existing = _tables(connection)
    if "market_snapshots" not in existing:
        return
    snapshot_columns = _columns(connection, "market_snapshots")
    if not {"id", "condition_id", "timestamp"}.issubset(snapshot_columns):
        return
    if requirements.minimum_latest_points:
        connection.execute(
            """
            INSERT OR IGNORE INTO _polybot_protected_snapshot_ids(id)
            SELECT id
            FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY condition_id
                           ORDER BY datetime(timestamp) DESC, id DESC
                       ) AS latest_rank
                FROM market_snapshots
            )
            WHERE latest_rank <= ?
            """,
            (requirements.minimum_latest_points,),
        )
    if "trades" not in existing:
        return
    if "entry_snapshot_id" not in _columns(connection, "trades"):
        return
    connection.execute(
        "INSERT OR IGNORE INTO _polybot_protected_snapshot_ids(id) "
        "SELECT entry_snapshot_id FROM trades "
        "WHERE entry_snapshot_id IS NOT NULL"
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO _polybot_protected_snapshot_ids(id)
        SELECT prior_id FROM (
            SELECT (
                SELECT prior.id
                FROM market_snapshots AS prior
                WHERE prior.condition_id = entry.condition_id
                  AND (
                      prior.timestamp < entry.timestamp
                      OR (
                          prior.timestamp = entry.timestamp
                          AND prior.id < entry.id
                      )
                  )
                ORDER BY prior.timestamp DESC, prior.id DESC
                LIMIT 1
            ) AS prior_id
            FROM trades AS trade
            JOIN market_snapshots AS entry
              ON entry.id = trade.entry_snapshot_id
            WHERE trade.entry_snapshot_id IS NOT NULL
        ) inferred
        WHERE prior_id IS NOT NULL
        """
    )
    if "prior_snapshot_id_at_entry" in _columns(connection, "trades"):
        connection.execute(
            "INSERT OR IGNORE INTO _polybot_protected_snapshot_ids(id) "
            "SELECT prior_snapshot_id_at_entry FROM trades "
            "WHERE prior_snapshot_id_at_entry IS NOT NULL"
        )


def _trusted_latest_timestamp(
    connection: sqlite3.Connection,
    table: str,
    column: str,
) -> str | None:
    """Return the latest parseable timestamp no later than the current clock."""

    return connection.execute(
        f"SELECT MAX(datetime({_quote_identifier(column)})) "
        f"FROM {_quote_identifier(table)} "
        f"WHERE datetime({_quote_identifier(column)}) <= datetime('now')"
    ).fetchone()[0]


def _delete_expired(
    connection: sqlite3.Connection, policy: SQLiteMaintenancePolicy
) -> None:
    existing = _tables(connection)
    modifier = f"-{policy.retention_days:.9f} days"
    if "market_snapshots" in existing:
        snapshot_anchor = _trusted_latest_timestamp(
            connection, "market_snapshots", "timestamp"
        )
        if snapshot_anchor is not None:
            connection.execute(
                "DELETE FROM market_snapshots "
                "WHERE datetime(timestamp) < datetime(?, ?) "
                "AND id NOT IN (SELECT id FROM _polybot_protected_snapshot_ids)",
                (snapshot_anchor, modifier),
            )
    if "market_sweeps" in existing:
        sweep_anchor = _trusted_latest_timestamp(
            connection, "market_sweeps", "completed_at"
        )
        expired_ids = """
            SELECT sweep_id FROM market_sweeps
            WHERE datetime(completed_at) < datetime(?, ?)
        """
        if sweep_anchor is not None and "market_sweep_memberships" in existing:
            connection.execute(
                f"DELETE FROM market_sweep_memberships WHERE sweep_id IN ({expired_ids})",
                (sweep_anchor, modifier),
            )
        if sweep_anchor is not None:
            connection.execute(
                f"DELETE FROM market_sweeps WHERE sweep_id IN ({expired_ids})",
                (sweep_anchor, modifier),
            )


def _roll_up_snapshots(
    connection: sqlite3.Connection, policy: SQLiteMaintenancePolicy
) -> None:
    if "market_snapshots" not in _tables(connection):
        return
    required = {"id", "condition_id", "probability", "timestamp"}
    if not required.issubset(_columns(connection, "market_snapshots")):
        return
    max_timestamp = _trusted_latest_timestamp(
        connection, "market_snapshots", "timestamp"
    )
    if max_timestamp is None:
        return
    hot_modifier = f"-{policy.hot_hours:.9f} hours"
    bucket_seconds = max(1, round(policy.rollup_hours * 3600))
    if policy.selector == "minimum":
        ranks = """
            MIN(probability) OVER (
                PARTITION BY condition_id,
                    CAST(strftime('%s', timestamp) / ? AS INTEGER)
                ORDER BY timestamp, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS earlier_min,
            MIN(probability) OVER (
                PARTITION BY condition_id,
                    CAST(strftime('%s', timestamp) / ? AS INTEGER)
                ORDER BY timestamp, id
                ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING
            ) AS later_min
        """
        # Keep the prefix- and suffix-minimum change points.  Their union
        # reconstructs the exact minimum for any interval that starts or ends
        # inside a rollup bucket, unlike a single bucket minimum.
        predicate = (
            "earlier_min IS NOT NULL AND probability >= earlier_min "
            "AND later_min IS NOT NULL AND probability >= later_min"
        )
        parameters: tuple[Any, ...] = (
            bucket_seconds,
            bucket_seconds,
            max_timestamp,
            hot_modifier,
        )
    elif policy.selector == "extrema":
        ranks = """
            MIN(probability) OVER (
                PARTITION BY condition_id,
                    CAST(strftime('%s', timestamp) / ? AS INTEGER)
                ORDER BY timestamp, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS earlier_min,
            MIN(probability) OVER (
                PARTITION BY condition_id,
                    CAST(strftime('%s', timestamp) / ? AS INTEGER)
                ORDER BY timestamp, id
                ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING
            ) AS later_min,
            MAX(probability) OVER (
                PARTITION BY condition_id,
                    CAST(strftime('%s', timestamp) / ? AS INTEGER)
                ORDER BY timestamp, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS earlier_max,
            MAX(probability) OVER (
                PARTITION BY condition_id,
                    CAST(strftime('%s', timestamp) / ? AS INTEGER)
                ORDER BY timestamp, id
                ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING
            ) AS later_max
        """
        predicate = (
            "earlier_min IS NOT NULL AND probability >= earlier_min "
            "AND later_min IS NOT NULL AND probability >= later_min "
            "AND earlier_max IS NOT NULL AND probability <= earlier_max "
            "AND later_max IS NOT NULL AND probability <= later_max"
        )
        parameters = (
            bucket_seconds,
            bucket_seconds,
            bucket_seconds,
            bucket_seconds,
            max_timestamp,
            hot_modifier,
        )
    else:
        ranks = """
            ROW_NUMBER() OVER (
                PARTITION BY condition_id,
                    CAST(strftime('%s', timestamp) / ? AS INTEGER)
                ORDER BY timestamp DESC, id DESC
            ) AS keep_rank
        """
        predicate = "keep_rank > 1"
        parameters = (bucket_seconds, max_timestamp, hot_modifier)
    connection.execute(
        f"""
        DELETE FROM market_snapshots
        WHERE id IN (
            SELECT id FROM (
                SELECT id, probability, {ranks}
                FROM market_snapshots
                WHERE datetime(timestamp) < datetime(?, ?)
            ) ranked
            WHERE {predicate}
        )
        AND id NOT IN (SELECT id FROM _polybot_protected_snapshot_ids)
        """,
        parameters,
    )


def _thin_membership_details(
    connection: sqlite3.Connection, policy: SQLiteMaintenancePolicy
) -> None:
    existing = _tables(connection)
    if not {"market_sweeps", "market_sweep_memberships"}.issubset(existing):
        return
    _ensure_sweep_detail_column(connection)
    interval_seconds = max(1, round(policy.membership_detail_hours * 3600))
    connection.execute("DROP TABLE IF EXISTS temp._polybot_keep_detail_sweeps")
    connection.execute(
        "CREATE TEMP TABLE _polybot_keep_detail_sweeps "
        "(sweep_id TEXT PRIMARY KEY) WITHOUT ROWID"
    )
    connection.execute(
        """
        INSERT INTO _polybot_keep_detail_sweeps(sweep_id)
        SELECT sweep_id FROM (
            SELECT sweep_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY CAST(strftime('%s', completed_at) / ? AS INTEGER)
                       ORDER BY completed_at DESC, sweep_id DESC
                   ) AS keep_rank
            FROM market_sweeps
            WHERE cursor_complete = 1
              AND membership_detail_stored = 1
        ) ranked
        WHERE keep_rank = 1
        """,
        (interval_seconds,),
    )
    connection.execute(
        "DELETE FROM market_sweep_memberships "
        "WHERE sweep_id NOT IN (SELECT sweep_id FROM _polybot_keep_detail_sweeps)"
    )
    connection.execute(
        "UPDATE market_sweeps SET membership_detail_stored = "
        "CASE WHEN sweep_id IN (SELECT sweep_id FROM _polybot_keep_detail_sweeps) "
        "THEN 1 ELSE 0 END"
    )
    connection.execute("DROP TABLE _polybot_keep_detail_sweeps")


def _drop_redundant_indexes(connection: sqlite3.Connection) -> None:
    indexes = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='index'"
        )
    }
    for name in (
        "ix_market_sweep_memberships_qualified",
        "ix_market_sweep_memberships_snapshotted",
    ):
        if name in indexes:
            connection.execute(f"DROP INDEX {_quote_identifier(name)}")
    if (
        "market_snapshots_condition_timestamp_idx" in indexes
        and "ix_market_snapshots_condition_id" in indexes
    ):
        connection.execute("DROP INDEX ix_market_snapshots_condition_id")
    if (
        "market_snapshots_run_idx" in indexes
        and "ix_market_snapshots_run_id" in indexes
    ):
        connection.execute("DROP INDEX ix_market_snapshots_run_id")


def _compact_connection(
    connection: sqlite3.Connection,
    policy: SQLiteMaintenancePolicy,
    requirements: SQLiteMaintenanceRequirements,
    *,
    activate: bool,
) -> dict[str, int]:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    _quick_check(connection)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(_STATE_TABLE_SQL)
        _ensure_sweep_detail_column(connection)
        _build_protected_snapshot_ids(connection, requirements)
        before = {
            "snapshots": _count(connection, "market_snapshots"),
            "memberships": _count(connection, "market_sweep_memberships"),
            "sweeps": _count(connection, "market_sweeps"),
        }
        protected_before = _protected_counts(connection)
        lineage_gaps_before = _snapshot_lineage_gaps(connection)
        snapshot_anchor = (
            _trusted_latest_timestamp(connection, "market_snapshots", "timestamp")
            if "market_snapshots" in _tables(connection)
            else None
        )
        _delete_expired(connection, policy)
        _roll_up_snapshots(connection, policy)
        _thin_membership_details(connection, policy)
        _drop_redundant_indexes(connection)
        after = {
            "snapshots": _count(connection, "market_snapshots"),
            "memberships": _count(connection, "market_sweep_memberships"),
            "sweeps": _count(connection, "market_sweeps"),
        }
        protected_after = _protected_counts(connection)
        if protected_after != protected_before:
            raise RuntimeError(
                "protected trade/execution evidence changed during compaction"
            )
        lineage_gaps_after = _snapshot_lineage_gaps(connection)
        if lineage_gaps_after != lineage_gaps_before:
            raise RuntimeError(
                "trade-linked snapshot lineage changed during compaction"
            )
        now = _utc_now()
        active = (
            1
            if activate
            else int(
                connection.execute(
                    "SELECT COALESCE(MAX(active), 0) FROM polybot_db_maintenance "
                    "WHERE profile = ?",
                    (PROFILE,),
                ).fetchone()[0]
            )
        )
        payload = {
            "policy": asdict(policy),
            "requirements": asdict(requirements),
            "snapshot_anchor": snapshot_anchor,
            "before": before,
            "after": after,
            "snapshot_lineage_gaps": lineage_gaps_after,
        }
        connection.execute(
            """
            INSERT INTO polybot_db_maintenance(
                profile, schema_version, strategy_name, active, activated_at,
                last_maintained_at, last_report_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile) DO UPDATE SET
                schema_version=excluded.schema_version,
                strategy_name=excluded.strategy_name,
                active=MAX(polybot_db_maintenance.active, excluded.active),
                last_maintained_at=excluded.last_maintained_at,
                last_report_json=excluded.last_report_json
            """,
            (
                PROFILE,
                SCHEMA_VERSION,
                policy.strategy_name,
                active,
                now,
                now,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    connection.execute("PRAGMA optimize")
    _quick_check(connection)
    return {**before, **{f"{key}_after": value for key, value in after.items()}}


def _state(connection: sqlite3.Connection) -> sqlite3.Row | None:
    if "polybot_db_maintenance" not in _tables(connection):
        return None
    connection.row_factory = sqlite3.Row
    return connection.execute(
        "SELECT * FROM polybot_db_maintenance WHERE profile = ?",
        (PROFILE,),
    ).fetchone()


def _validate_active_state(
    row: Mapping[str, Any],
    strategy_name: str,
    policy: SQLiteMaintenancePolicy,
    requirements: SQLiteMaintenanceRequirements | None = None,
) -> None:
    normalized = str(strategy_name).strip().lower()
    if int(row["active"] or 0) != 1:
        raise RuntimeError("compact-v1 maintenance state is not active")
    if int(row["schema_version"] or 0) != SCHEMA_VERSION:
        raise RuntimeError(
            "unsupported compact-v1 maintenance schema_version: "
            f"{row['schema_version']!r}"
        )
    if str(row["strategy_name"] or "").strip().lower() != normalized:
        raise RuntimeError(
            "compact-v1 strategy identity mismatch: "
            f"stored={row['strategy_name']!r}, requested={normalized!r}"
        )
    try:
        report = json.loads(str(row["last_report_json"] or ""))
        stored_policy = report["policy"]
        stored_requirements = report["requirements"]
        report_strategy = str(stored_policy["strategy_name"]).strip().lower()
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise RuntimeError("invalid compact-v1 maintenance policy report") from error
    if report_strategy != normalized:
        raise RuntimeError(
            "compact-v1 report strategy identity mismatch: "
            f"stored={report_strategy!r}, requested={normalized!r}"
        )
    expected_policy = asdict(policy)
    if not isinstance(stored_policy, dict) or stored_policy != expected_policy:
        raise RuntimeError(
            "compact-v1 maintenance policy mismatch; destructive retention "
            "settings cannot change after activation without a new migration "
            f"profile: stored={stored_policy!r}, requested={expected_policy!r}"
        )
    if not isinstance(stored_requirements, dict):
        raise RuntimeError("invalid compact-v1 maintenance requirements report")
    if requirements is not None:
        expected_requirements = asdict(requirements)
        if stored_requirements != expected_requirements:
            raise RuntimeError(
                "compact-v1 resolved strategy requirements mismatch; signal "
                "windows cannot change after activation without a new migration "
                f"profile: stored={stored_requirements!r}, "
                f"requested={expected_requirements!r}"
            )


def _maintenance_due(
    connection: sqlite3.Connection, policy: SQLiteMaintenancePolicy
) -> bool:
    row = _state(connection)
    if row is None or int(row["active"] or 0) != 1:
        return False
    return bool(
        connection.execute(
            "SELECT datetime(COALESCE(last_maintained_at, activated_at)) "
            "<= datetime('now', ?) FROM polybot_db_maintenance "
            "WHERE profile = ?",
            (f"-{policy.run_interval_hours:.9f} hours", PROFILE),
        ).fetchone()[0]
    )


def _migrate(
    path: Path,
    policy: SQLiteMaintenancePolicy,
    requirements: SQLiteMaintenanceRequirements,
) -> SQLiteMaintenanceReport:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_root = _backup_root(policy.strategy_name)
    source_id = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
    unique_id = uuid.uuid4().hex[:12]
    backup_path = backup_root / (
        f"{path.stem}.{source_id}.{timestamp}.{unique_id}.pre-{PROFILE}.db"
    )
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".manifest.json")
    backup_fd = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(backup_fd)
    source_guard = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        source_guard.execute("PRAGMA busy_timeout=0")
        journal_mode = str(
            source_guard.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()
        if journal_mode == "wal":
            raise RuntimeError(
                "compact-v1 migration refuses WAL mode; stop every writer, "
                "checkpoint WAL, and switch the source DB to journal_mode=DELETE"
            )
        try:
            source_guard.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            raise RuntimeError(
                "source database is busy; stop every SQLite writer before migration"
            ) from error
        source_stat = path.stat()
        before_bytes = source_stat.st_size
        _online_backup(path, backup_path, reserved_destination=True)
        backup_sha = _sha256(backup_path)
        _fsync_path(backup_path)
        manifest = {
            "schema_version": 1,
            "created_at": _utc_now(),
            "strategy_name": policy.strategy_name,
            "profile": PROFILE,
            "source_name": path.name,
            "source_id_sha256_prefix": source_id,
            "source_bytes": before_bytes,
            "backup_name": backup_path.name,
            "backup_bytes": backup_path.stat().st_size,
            "backup_sha256": backup_sha,
        }
        with manifest_path.open("x", encoding="utf-8") as manifest_file:
            manifest_file.write(
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            )
            manifest_file.flush()
            os.fsync(manifest_file.fileno())
        manifest_path.chmod(0o600)
        _fsync_path(backup_root)

        work_fd, work_name = tempfile.mkstemp(
            prefix=f".{path.name}.{PROFILE}.", suffix=".tmp", dir=path.parent
        )
        os.close(work_fd)
        work_path = Path(work_name)
        try:
            _online_backup(path, work_path, reserved_destination=True)
            connection = sqlite3.connect(work_path, timeout=30)
            try:
                # This is a disposable working copy with a verified source
                # backup, not the live DB.  Avoid a multi-gigabyte rollback
                # journal during the one-time DELETE, then restore durable
                # DELETE mode before the atomic replacement.
                connection.execute("PRAGMA journal_mode=OFF")
                protected_before = _protected_counts(connection)
                counts = _compact_connection(
                    connection, policy, requirements, activate=True
                )
                protected_after = _protected_counts(connection)
                if protected_after != protected_before:
                    raise RuntimeError(
                        "protected evidence count mismatch after migration"
                    )
                connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
                connection.execute("VACUUM")
                if (
                    str(
                        connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                    ).lower()
                    != "delete"
                ):
                    raise RuntimeError("failed to restore SQLite journal_mode=DELETE")
                _quick_check(connection)
            finally:
                connection.close()
            if (
                path.stat().st_size != source_stat.st_size
                or path.stat().st_mtime_ns != source_stat.st_mtime_ns
            ):
                raise RuntimeError(
                    "source database changed while compact migration was running"
                )
            work_path.chmod(source_stat.st_mode & 0o777)
            os.replace(work_path, path)
            _fsync_path(path.parent)
        finally:
            work_path.unlink(missing_ok=True)
    finally:
        try:
            source_guard.rollback()
        finally:
            source_guard.close()
        if not backup_path.exists() or backup_path.stat().st_size == 0:
            backup_path.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
    after_bytes = path.stat().st_size
    report = SQLiteMaintenanceReport(
        strategy_name=policy.strategy_name,
        profile=PROFILE,
        snapshots_before=counts["snapshots"],
        snapshots_after=counts["snapshots_after"],
        memberships_before=counts["memberships"],
        memberships_after=counts["memberships_after"],
        sweeps_before=counts["sweeps"],
        sweeps_after=counts["sweeps_after"],
        bytes_before=before_bytes,
        bytes_after=after_bytes,
        backup_path=str(backup_path),
        backup_sha256=backup_sha,
    )
    LOGGER.warning(
        "SQLite compact-v1 완료 - strategy=%s bytes=%s->%s snapshots=%s->%s "
        "memberships=%s->%s backup=%s",
        policy.strategy_name,
        before_bytes,
        after_bytes,
        report.snapshots_before,
        report.snapshots_after,
        report.memberships_before,
        report.memberships_after,
        backup_path,
    )
    return report


def prepare_database(
    db_path: str | os.PathLike[str],
    strategy_name: str,
    *,
    requirements: SQLiteMaintenanceRequirements | None = None,
) -> SQLiteMaintenanceReport | None:
    """Run the one-time migration or already-activated lean maintenance.

    Set ``POLYBOT_DB_MAINTENANCE=compact-v1`` for exactly one successful run,
    verify its backup/report, then remove the variable.  The activation marker
    remains in the DB and keeps bounded maintenance enabled on future runs.
    """

    raw_profile = os.getenv(ENV_PROFILE, "").strip()
    if raw_profile and raw_profile != PROFILE:
        raise ValueError(f"{ENV_PROFILE} must be empty or {PROFILE!r}")
    path = Path(db_path).expanduser().resolve()
    if not path.exists() or path.stat().st_size == 0:
        if raw_profile != PROFILE:
            return None
        # A brand-new Jenkins job can activate compact-v1 on its first run as
        # well.  Materialize a valid, schema-light SQLite file so the same
        # backed-up migration path can mark it active before SQLAlchemy creates
        # the strategy tables.  Without this, the flag would require a second
        # run merely because the DB did not exist yet.
        path.parent.mkdir(parents=True, exist_ok=True)
        bootstrap = sqlite3.connect(path)
        try:
            bootstrap.execute(_STATE_TABLE_SQL)
            bootstrap.commit()
        finally:
            bootstrap.close()
    if raw_profile == PROFILE:
        resolved_requirements = requirements or requirements_for(strategy_name)
        policy = policy_for(strategy_name, resolved_requirements)
        probe = sqlite3.connect(path)
        try:
            row = _state(probe)
            if row is not None and int(row["active"] or 0) == 1:
                _validate_active_state(
                    row, strategy_name, policy, resolved_requirements
                )
                LOGGER.info("SQLite compact-v1은 이미 활성화되어 있습니다: %s", path)
                return None
        finally:
            probe.close()
        return _migrate(path, policy, resolved_requirements)

    connection = sqlite3.connect(path, timeout=30)
    try:
        row = _state(connection)
        if row is None or int(row["active"] or 0) != 1:
            return None
        resolved_requirements = requirements or requirements_for(strategy_name)
        policy = policy_for(strategy_name, resolved_requirements)
        _validate_active_state(row, strategy_name, policy, resolved_requirements)
        if not _maintenance_due(connection, policy):
            return None
        before_bytes = path.stat().st_size
        counts = _compact_connection(
            connection, policy, resolved_requirements, activate=False
        )
        if int(connection.execute("PRAGMA auto_vacuum").fetchone()[0]) == 2:
            connection.execute("PRAGMA incremental_vacuum")
        after_bytes = path.stat().st_size
    finally:
        connection.close()
    return SQLiteMaintenanceReport(
        strategy_name=policy.strategy_name,
        profile=PROFILE,
        snapshots_before=counts["snapshots"],
        snapshots_after=counts["snapshots_after"],
        memberships_before=counts["memberships"],
        memberships_after=counts["memberships_after"],
        sweeps_before=counts["sweeps"],
        sweeps_after=counts["sweeps_after"],
        bytes_before=before_bytes,
        bytes_after=after_bytes,
        backup_path=None,
        backup_sha256=None,
    )


def membership_details_due(
    session: Any,
    strategy_name: str,
    interval_hours: float | None = None,
) -> bool:
    """Return whether this sweep should persist the full per-market membership.

    Before compact-v1 activation this deliberately returns True, preserving the
    legacy evidence contract.  Under compact-v1 every sweep still keeps its
    scalar counts and digest, while full membership detail is checkpointed.
    """

    if not compact_maintenance_active(session, strategy_name):
        return True
    connection = session.connection()
    table_exists = connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_schema "
        "WHERE type='table' AND name='polybot_db_maintenance'"
    ).first()
    if not table_exists:
        return True
    sweep_columns = {
        str(row[1])
        for row in connection.exec_driver_sql("PRAGMA table_info(market_sweeps)")
    }
    if "membership_detail_stored" not in sweep_columns:
        return True
    raw_report = connection.exec_driver_sql(
        "SELECT last_report_json FROM polybot_db_maintenance WHERE profile = ?",
        (PROFILE,),
    ).scalar_one()
    try:
        hours = float(json.loads(str(raw_report))["policy"]["membership_detail_hours"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise RuntimeError("invalid compact-v1 membership checkpoint policy") from error
    if interval_hours is not None and not math.isclose(
        float(interval_hours), hours, rel_tol=0.0, abs_tol=1e-12
    ):
        raise RuntimeError(
            "membership checkpoint interval cannot override the activated "
            f"compact-v1 policy: stored={hours}, requested={interval_hours}"
        )
    latest = connection.exec_driver_sql(
        "SELECT MAX(completed_at) FROM market_sweeps WHERE membership_detail_stored = 1"
    ).scalar()
    if latest is None:
        return True
    due = connection.exec_driver_sql(
        "SELECT datetime(?) <= datetime('now', ?)",
        (str(latest), f"-{hours:.9f} hours"),
    ).scalar()
    return bool(due)


def compact_maintenance_active(session: Any, strategy_name: str) -> bool:
    """Return validated compact-v1 activation state for a SQLAlchemy session.

    Legacy repository cleanup must yield deletion ownership to compact-v1 once
    active.  This check is fail-closed for identity/schema/policy drift.
    """

    connection = session.connection()
    table_exists = connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_schema "
        "WHERE type='table' AND name='polybot_db_maintenance'"
    ).first()
    if not table_exists:
        return False
    row = (
        connection.exec_driver_sql(
            "SELECT schema_version, strategy_name, active, last_report_json "
            "FROM polybot_db_maintenance WHERE profile = ?",
            (PROFILE,),
        )
        .mappings()
        .first()
    )
    if row is None or int(row["active"] or 0) != 1:
        return False
    # The Bot validates resolved YAML/environment requirements during
    # prepare_database().  Repository cleanup only verifies the immutable
    # generic compact policy and must not re-read a second signal config.
    policy = policy_for(strategy_name, SQLiteMaintenanceRequirements())
    _validate_active_state(row, strategy_name, policy)
    return True
