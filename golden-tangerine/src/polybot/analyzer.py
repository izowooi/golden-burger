"""Exact-range, read-only Golden Tangerine A/B evidence analyzer."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Any, Iterable
from urllib.parse import quote


OPEN_STATUSES = {"PENDING_BUY", "HOLDING", "PENDING_SELL", "QUARANTINED"}


def parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid ISO-8601 timestamp: {value}") from error
    if parsed.tzinfo is None:
        raise ValueError("analysis range timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _sqlite_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def database_checksums(db_path: Path) -> dict[str, Any]:
    parts = {}
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db_path) + suffix)
        if path.exists():
            parts[path.name] = {"bytes": path.stat().st_size, "sha256": _sha256(path)}
    return parts


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def analyze_database(
    db_path: str | Path,
    *,
    start: datetime,
    end_exclusive: datetime,
    label: str | None = None,
) -> dict[str, Any]:
    """Analyze one immutable entry cohort without writing/checkpointing its DB."""
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    before = database_checksums(path)
    lower, upper = _sqlite_time(start), _sqlite_time(end_exclusive)
    connection = _connect_read_only(path)
    try:
        tables = _table_names(connection)
        required = {
            "trades",
            "entry_episodes",
            "entry_candidate_events",
            "order_submissions",
            "order_fills",
            "resolution_observations",
            "market_sweeps",
        }
        missing = sorted(required - tables)
        if missing:
            raise RuntimeError(f"required evidence tables missing: {missing}")

        trades = connection.execute(
            """
            SELECT * FROM trades
            WHERE buy_timestamp >= ? AND buy_timestamp < ?
            ORDER BY buy_timestamp, id
            """,
            (lower, upper),
        ).fetchall()
        trade_ids = [int(row["id"]) for row in trades]
        placeholders = ",".join("?" for _ in trade_ids) or "NULL"
        fills = connection.execute(
            f"""
            SELECT submission.order_id, submission.token_id,
                   submission.latest_order_status, submission.needs_reconciliation,
                   fill.trade_id, fill.status, fill.side, fill.size, fill.price,
                   fill.liquidity_role, fill.fee_rate_bps, fill.fee_amount_usdc,
                   fill.domain_error
            FROM trades AS trade
            JOIN order_submissions AS submission
              ON submission.order_id = trade.buy_order_id
            JOIN order_fills AS fill
              ON fill.submission_id = submission.submission_id
             AND fill.order_id = submission.order_id
            WHERE trade.id IN ({placeholders})
            ORDER BY trade.id, fill.trade_id, fill.bucket_index
            """,
            trade_ids,
        ).fetchall() if trade_ids else []
        confirmed = [
            row for row in fills
            if str(row["status"] or "").upper().removeprefix("TRADE_STATUS_") == "CONFIRMED"
        ]
        fee_complete = 0
        for row in confirmed:
            amount = row["fee_amount_usdc"]
            rate = row["fee_rate_bps"]
            proven = False
            try:
                proven = amount is not None and math.isfinite(float(amount)) and float(amount) >= 0
            except (TypeError, ValueError):
                proven = False
            if not proven:
                try:
                    proven = rate is not None and float(rate) == 0.0
                except (TypeError, ValueError):
                    proven = False
            fee_complete += int(proven)

        resolved = [row for row in trades if str(row["status"]) == "RESOLVED"]
        proven_resolved = [
            row for row in resolved
            if row["resolution_evidence"]
            and row["resolution_status"]
            and row["resolution_confirmed_buy_size"] is not None
            and row["resolution_confirmed_buy_vwap"] is not None
            and row["resolution_confirmed_buy_fee_usdc"] is not None
            and row["settlement_assumption_basis"]
            == "confirmed_buy_fill_net_known_buy_fee"
            and row["resolution_value"] in (0.0, 0.5, 1.0)
        ]
        proof_counts = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                f"""
                SELECT settlement_kind, COUNT(*)
                FROM resolution_observations
                WHERE trade_id IN ({placeholders})
                GROUP BY settlement_kind
                """,
                trade_ids,
            )
        } if trade_ids else {}

        clusters: dict[str, dict[str, Any]] = {}
        for row in proven_resolved:
            event_id = str(row["event_id"] or f"MISSING:{row['condition_id']}")
            item = clusters.setdefault(event_id, {"trades": 0, "net_pnl_usdc": 0.0})
            item["trades"] += 1
            item["net_pnl_usdc"] += float(row["settlement_pnl_assumption"])

        episodes = connection.execute(
            """
            SELECT * FROM entry_episodes
            WHERE observed_at >= ? AND observed_at < ?
            ORDER BY observed_at, id
            """,
            (lower, upper),
        ).fetchall()
        episode_ids = [int(row["id"]) for row in episodes]
        episode_placeholders = ",".join("?" for _ in episode_ids) or "NULL"
        event_rows = connection.execute(
            f"""
            SELECT state, COUNT(*) AS count
            FROM entry_candidate_events
            WHERE episode_id IN ({episode_placeholders})
            GROUP BY state
            """,
            episode_ids,
        ).fetchall() if episode_ids else []

        unresolved = [row for row in trades if str(row["status"]) in OPEN_STATUSES]
        orphan_rows = connection.execute(
            """
            SELECT submission_id, order_id, token_id, requested_price,
                   requested_size, making_amount, response_status,
                   latest_order_status, latest_size_matched
            FROM order_submissions AS submission
            WHERE simulation = 0 AND UPPER(side) = 'BUY'
              AND submitted_at >= ? AND submitted_at < ?
              AND NOT EXISTS (
                  SELECT 1 FROM trades
                  WHERE trades.buy_order_id = submission.order_id
                    AND submission.order_id IS NOT NULL
              )
              AND NOT (
                  submission.order_id IS NULL AND submission.success = 0
                  AND submission.needs_reconciliation = 0
                  AND UPPER(COALESCE(submission.response_status, '')) = 'FAILED'
              )
              AND NOT (
                  submission.outcome_resolution = 'NO_ORDER_CREATED'
                  AND submission.order_id IS NULL
                  AND submission.outcome_resolved_at IS NOT NULL
                  AND NULLIF(TRIM(submission.outcome_resolution_reason), '') IS NOT NULL
              )
              AND NOT (
                  submission.order_id IS NOT NULL
                  AND submission.needs_reconciliation = 0
                  AND REPLACE(UPPER(COALESCE(submission.latest_order_status, '')),
                              'ORDER_STATUS_', '') IN
                      ('CANCELED', 'CANCELLED', 'CANCELED_MARKET_RESOLVED', 'INVALID')
                  AND COALESCE(submission.latest_size_matched, 0) = 0
              )
            """,
            (start.isoformat(), end_exclusive.isoformat()),
        ).fetchall()

        sweep_times = []
        for row in connection.execute(
            """
            SELECT completed_at FROM market_sweeps
            WHERE completed_at >= ? AND completed_at < ?
            ORDER BY completed_at
            """,
            (lower, upper),
        ):
            parsed = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            sweep_times.append(parsed.astimezone(timezone.utc))
        gaps = [
            (right - left).total_seconds()
            for left, right in zip(sweep_times, sweep_times[1:])
        ]

        issues = []
        if len(proven_resolved) != len(resolved):
            issues.append("resolved_trade_proof_or_fee_gap")
        if fee_complete != len(confirmed):
            issues.append("confirmed_fill_fee_gap")
        if any(row["domain_error"] for row in confirmed):
            issues.append("confirmed_fill_domain_error")
        if orphan_rows:
            issues.append("untracked_buy_exposure")
        result = {
            "label": label or path.parent.name,
            "db_path": str(path),
            "range": {
                "start_inclusive": start.isoformat().replace("+00:00", "Z"),
                "end_exclusive": end_exclusive.isoformat().replace("+00:00", "Z"),
            },
            "entry_cohort": {
                "trades": len(trades),
                "confirmed_buy_fill_rows": len(confirmed),
                "fee_complete_fill_rows": fee_complete,
                "resolved": len(resolved),
                "proven_resolved": len(proven_resolved),
                "void_resolved": sum(row["resolution_value"] == 0.5 for row in proven_resolved),
                "net_settlement_pnl_usdc": sum(
                    float(row["settlement_pnl_assumption"]) for row in proven_resolved
                ),
                "resolution_proof_counts": proof_counts,
            },
            "event_clustering": {
                "unique_events": len(clusters),
                "max_trades_per_event": max(
                    (item["trades"] for item in clusters.values()), default=0
                ),
                "clusters": clusters,
            },
            "episode_funnel": {
                "first_band_episodes": len(episodes),
                "candidate_events_by_state": {str(row[0]): int(row[1]) for row in event_rows},
                "trade_linked": sum(row["trade_id"] is not None for row in episodes),
                "confirmed_trade_count": len(
                    {str(row["order_id"]) for row in confirmed if row["order_id"]}
                ),
                "proven_resolved": len(proven_resolved),
            },
            "unresolved_exposure": {
                "trade_count": len(unresolved),
                "trade_notional_usdc": sum(float(row["buy_amount"] or 0) for row in unresolved),
                "status_counts": {
                    status: sum(str(row["status"]) == status for row in unresolved)
                    for status in sorted(OPEN_STATUSES)
                },
                "untracked_buy_submissions": len(orphan_rows),
            },
            "cadence": {
                "sweeps": len(sweep_times),
                "timestamps_utc": [item.isoformat().replace("+00:00", "Z") for item in sweep_times],
                "gap_seconds_median": statistics.median(gaps) if gaps else None,
                "gap_seconds_p95": _percentile(gaps, 0.95),
                "gap_seconds_max": max(gaps) if gaps else None,
            },
            "issues": issues,
            "strict_evidence_complete": not issues,
            "checksums_before": before,
        }
    finally:
        connection.close()
    after = database_checksums(path)
    result["checksums_after"] = after
    result["database_stable_during_read"] = before == after
    if before != after:
        result["issues"].append("database_changed_during_analysis")
        result["strict_evidence_complete"] = False
    return result


def analyze_ab(
    db_specs: Iterable[tuple[str, str | Path]],
    *,
    start: datetime,
    end_exclusive: datetime,
) -> dict[str, Any]:
    if not start < end_exclusive:
        raise ValueError("analysis start must precede end-exclusive")
    specs = [(str(label).strip(), Path(path).expanduser().resolve()) for label, path in db_specs]
    if len(specs) != 2:
        raise ValueError("A/B analysis requires exactly two --db inputs")
    if len({label for label, _ in specs}) != 2:
        raise ValueError("A/B analysis labels must be distinct")
    if len({str(path) for _, path in specs}) != 2:
        raise ValueError("A/B analysis database paths must be distinct")
    databases = [
        analyze_database(path, start=start, end_exclusive=end_exclusive, label=label)
        for label, path in specs
    ]
    timestamps = [
        [parse_utc(value) for value in item["cadence"]["timestamps_utc"]]
        for item in databases
    ]
    async_diag: dict[str, Any] = {"comparable": len(timestamps) == 2}
    if len(timestamps) == 2:
        left_to_right = [
            min(abs((value - other).total_seconds()) for other in timestamps[1])
            for value in timestamps[0]
        ] if timestamps[1] else []
        right_to_left = [
            min(abs((value - other).total_seconds()) for other in timestamps[0])
            for value in timestamps[1]
        ] if timestamps[0] else []
        skews = left_to_right + right_to_left
        async_diag.update(
            {
                "left_sweeps": len(timestamps[0]),
                "right_sweeps": len(timestamps[1]),
                "left_to_right_nearest_skew_seconds_p95": _percentile(left_to_right, 0.95),
                "right_to_left_nearest_skew_seconds_p95": _percentile(right_to_left, 0.95),
                "nearest_skew_seconds_median": statistics.median(skews) if skews else None,
                "nearest_skew_seconds_p95": _percentile(skews, 0.95),
                "nearest_skew_seconds_max": max(skews) if skews else None,
            }
        )
    report = {
        "schema": "golden-tangerine-ab-analyzer-v1",
        "range": {
            "start_inclusive": start.isoformat().replace("+00:00", "Z"),
            "end_exclusive": end_exclusive.isoformat().replace("+00:00", "Z"),
        },
        "databases": databases,
        "async_cadence": async_diag,
        "strict_evidence_complete": all(
            item["strict_evidence_complete"] for item in databases
        ),
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), default=str)
    report["report_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return report
