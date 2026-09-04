#!/usr/bin/env python3
"""Read-only Golden Cherry exact-evidence history analyzer.

This analyzer never treats legacy ``trades.realized_pnl`` as actual execution.
Its period is the exact UTC half-open interval ``[start, end)``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from polybot.db.exposure_reservations import UNTRACKED_BUY_RESERVATIONS_SQL


EXACT_PNL_BASIS = "exact_reconciled_buy_sell_confirmed_fills_net_known_fees"
NET_SETTLEMENT_BASIS = "exact_confirmed_buy_remaining_position_net_known_buy_fee"
GROSS_SETTLEMENT_BASIS = "exact_confirmed_buy_remaining_position_gross_fee_unproven"
OPEN_STATUSES = {"PENDING_BUY", "HOLDING", "PENDING_SELL", "QUARANTINED"}


def parse_exact_utc(value: str) -> datetime:
    """Parse an explicit timestamp and reject non-UTC or date-only inputs."""
    raw = str(value or "").strip()
    if "T" not in raw:
        raise ValueError("timestamp must include an exact time and UTC offset")
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"invalid ISO-8601 timestamp: {raw}") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("timestamp must use UTC (Z or +00:00)")
    return parsed.astimezone(timezone.utc)


def _db_timestamp(value: Any) -> datetime | None:
    """Parse ISO-8601 or Unix epoch seconds persisted as text/number."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        epoch_seconds = float(raw)
    except ValueError:
        epoch_seconds = None
    if epoch_seconds is not None:
        if not math.isfinite(epoch_seconds):
            return None
        try:
            return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _first_valid_db_timestamp(*values: Any) -> datetime | None:
    """Use the first parseable timestamp, not merely the first non-empty one."""
    for value in values:
        parsed = _db_timestamp(value)
        if parsed is not None:
            return parsed
    return None


def _in_window(value: Any, start: datetime, end: datetime) -> bool:
    parsed = value if isinstance(value, datetime) else _db_timestamp(value)
    return parsed is not None and start <= parsed < end


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(db_path.resolve()))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_schema(connection: sqlite3.Connection) -> None:
    required = {
        "trades": {
            "id", "condition_id", "market_slug", "question", "token_id",
            "buy_order_id", "sell_order_id", "buy_amount", "status",
            "sell_timestamp", "sell_fill_matched_at", "realized_pnl",
            "pnl_basis", "resolution_observed_at", "resolution_evidence",
            "resolution_outcome", "resolution_value", "resolution_status",
            "resolution_confirmed_buy_size", "resolution_confirmed_buy_vwap",
            "resolution_confirmed_buy_fee_usdc", "resolution_position_size",
            "settlement_pnl_assumption", "settlement_assumption_basis",
        },
        "order_submissions": {
            "submission_id", "run_id", "order_id", "token_id", "side",
            "requested_price", "requested_size", "submitted_at", "simulation",
            "response_status", "latest_order_status", "latest_size_matched",
            "needs_reconciliation", "outcome_resolution", "outcome_resolved_at",
            "outcome_resolution_reason",
        },
        "order_fills": {
            "submission_id", "order_id", "status", "side", "size", "price",
            "fee_amount_usdc", "domain_error",
        },
        "run_audits": {
            "run_id", "config_hash", "git_commit", "mode", "job_name",
            "started_at", "finished_at", "status",
        },
    }
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    for table, columns in required.items():
        if table not in tables:
            raise RuntimeError(f"required evidence table is missing: {table}")
        actual = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        missing = columns - actual
        if missing:
            raise RuntimeError(
                f"{table} is missing required evidence columns: {sorted(missing)}"
            )


def _cohort_key(run: sqlite3.Row | None) -> tuple[str, str, str, str]:
    if run is None:
        return ("unattributed", "unattributed", "unknown", "unknown")
    return (
        str(run["config_hash"]),
        str(run["git_commit"]),
        str(run["mode"]),
        str(run["job_name"]),
    )


def _cohort_dict(key: tuple[str, str, str, str]) -> dict[str, str]:
    return dict(zip(("config_hash", "git_commit", "mode", "job_name"), key))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _confirmed_fill_summary(
    connection: sqlite3.Connection, order_id: Any, side: str
) -> dict[str, float] | None:
    if not order_id:
        return None
    submissions = connection.execute(
        """
        SELECT submission_id
        FROM order_submissions
        WHERE order_id = ? AND simulation = 0 AND UPPER(side) = ?
        """,
        (str(order_id), side),
    ).fetchall()
    if len(submissions) != 1:
        return None
    row = connection.execute(
        """
        SELECT COUNT(*) AS fill_count, SUM(size) AS confirmed_size,
               SUM(size * price) AS gross_value,
               SUM(CASE WHEN fee_amount_usdc IS NULL THEN 1 ELSE 0 END)
                   AS missing_fee_rows,
               SUM(COALESCE(fee_amount_usdc, 0)) AS known_fee_usdc
        FROM order_fills
        WHERE submission_id = ? AND UPPER(side) = ?
          AND UPPER(status) = 'CONFIRMED'
          AND (domain_error IS NULL OR TRIM(domain_error) = '')
          AND size > 0 AND price > 0 AND price <= 1
        """,
        (submissions[0]["submission_id"], side),
    ).fetchone()
    size = _finite(row["confirmed_size"])
    gross = _finite(row["gross_value"])
    if int(row["fill_count"] or 0) < 1 or size is None or size <= 0 or gross is None:
        return None
    return {
        "confirmed_size": size,
        "confirmed_vwap": gross / size,
        "known_fee_usdc": float(row["known_fee_usdc"] or 0.0),
        "missing_fee_rows": int(row["missing_fee_rows"] or 0),
    }


def _submission_has_confirmed_fill(
    connection: sqlite3.Connection, order_id: Any, side: str
) -> bool:
    return _confirmed_fill_summary(connection, order_id, side) is not None


def _run_for_order(
    connection: sqlite3.Connection,
    order_id: Any,
    run_by_id: dict[str, sqlite3.Row],
) -> sqlite3.Row | None:
    if not order_id:
        return None
    rows = connection.execute(
        "SELECT run_id FROM order_submissions WHERE order_id = ? AND simulation = 0",
        (str(order_id),),
    ).fetchall()
    run_ids = {str(row[0]) for row in rows if row[0]}
    if len(run_ids) != 1:
        return None
    return run_by_id.get(next(iter(run_ids)))


def _run_for_time(
    moment: Any, runs: Iterable[sqlite3.Row]
) -> sqlite3.Row | None:
    timestamp = _db_timestamp(moment)
    if timestamp is None:
        return None
    matches = []
    for run in runs:
        started = _db_timestamp(run["started_at"])
        finished = _db_timestamp(run["finished_at"])
        if started and finished and started <= timestamp <= finished:
            matches.append(run)
    return matches[0] if len(matches) == 1 else None


def _untracked_buy_reservations(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(UNTRACKED_BUY_RESERVATIONS_SQL).fetchall()
    reservations = []
    for row in rows:
        price = _finite(row["requested_price"])
        size = _finite(row["requested_size"])
        if price is None or size is None or not 0 < price < 1 or size <= 0:
            raise RuntimeError("untracked BUY reservation has invalid notional evidence")
        reservations.append(
            {
                "submission_id": row["submission_id"],
                "order_id": row["order_id"],
                "token_id": row["token_id"],
                "submitted_at": row["submitted_at"],
                "response_status": row["response_status"],
                "needs_reconciliation": bool(row["needs_reconciliation"]),
                "requested_notional_usdc": round(price * size, 6),
            }
        )
    return reservations


def _buy_reservation_audit(connection: sqlite3.Connection) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT
            SUM(CASE WHEN order_id IS NULL
                          AND UPPER(COALESCE(response_status, '')) =
                              'SUBMIT_OUTCOME_UNKNOWN'
                     THEN 1 ELSE 0 END) AS raw_null_id_unknown,
            SUM(CASE WHEN order_id IS NULL
                          AND UPPER(COALESCE(response_status, '')) =
                              'SUBMIT_OUTCOME_UNKNOWN'
                          AND outcome_resolution = 'NO_ORDER_CREATED'
                          AND outcome_resolved_at IS NOT NULL
                          AND NULLIF(TRIM(outcome_resolution_reason), '') IS NOT NULL
                     THEN 1 ELSE 0 END) AS proved_no_order
        FROM order_submissions
        WHERE simulation = 0 AND UPPER(side) = 'BUY'
        """
    ).fetchone()
    return {
        "raw_order_id_null_submit_outcome_unknown_count": int(row[0] or 0),
        "operator_proven_no_order_created_excluded_count": int(row[1] or 0),
    }


def _valid_resolution_settlement(
    connection: sqlite3.Connection, trade: sqlite3.Row
) -> bool:
    payout = _finite(trade["resolution_value"])
    buy_size = _finite(trade["resolution_confirmed_buy_size"])
    buy_vwap = _finite(trade["resolution_confirmed_buy_vwap"])
    position_size = _finite(trade["resolution_position_size"])
    assumption = _finite(trade["settlement_pnl_assumption"])
    basis = str(trade["settlement_assumption_basis"] or "")
    if (
        str(trade["resolution_status"] or "").strip().lower() != "resolved"
        or payout not in {0.0, 1.0}
        or not str(trade["resolution_outcome"] or "").strip()
        or buy_size is None
        or buy_size <= 0
        or buy_vwap is None
        or not 0 < buy_vwap <= 1
        or position_size is None
        or position_size <= 0
        or position_size > buy_size + 0.010001
        or assumption is None
        or basis not in {NET_SETTLEMENT_BASIS, GROSS_SETTLEMENT_BASIS}
    ):
        return False
    confirmed = _confirmed_fill_summary(connection, trade["buy_order_id"], "BUY")
    if confirmed is None or not math.isclose(
        confirmed["confirmed_size"], buy_size, rel_tol=0, abs_tol=1e-6
    ):
        return False
    if not math.isclose(
        confirmed["confirmed_vwap"], buy_vwap, rel_tol=0, abs_tol=1e-9
    ):
        return False
    expected = (payout - buy_vwap) * position_size
    if basis == NET_SETTLEMENT_BASIS:
        fee = _finite(trade["resolution_confirmed_buy_fee_usdc"])
        if fee is None or fee < 0:
            return False
        expected -= fee * min(1.0, position_size / buy_size)
    return math.isclose(expected, assumption, rel_tol=0, abs_tol=1e-6)


def analyze(db_path: str | Path, start: datetime, end: datetime) -> dict[str, Any]:
    """Analyze exact execution and payout evidence without mutating the DB."""
    path = Path(db_path).expanduser().resolve()
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware UTC timestamps")
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if start >= end:
        raise ValueError("start must be earlier than end")

    digest = _sha256_file(path)
    with _connect_read_only(path) as connection:
        _require_schema(connection)
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check != "ok":
            raise RuntimeError(f"database quick_check failed: {quick_check}")

        runs = list(connection.execute("SELECT * FROM run_audits"))
        run_by_id = {str(run["run_id"]): run for run in runs}

        exact_rows = []
        excluded_legacy_rows = 0
        for trade in connection.execute(
            "SELECT * FROM trades WHERE realized_pnl IS NOT NULL"
        ):
            timestamp = _first_valid_db_timestamp(
                trade["sell_fill_matched_at"], trade["sell_timestamp"]
            )
            if not _in_window(timestamp, start, end):
                continue
            if trade["pnl_basis"] != EXACT_PNL_BASIS:
                excluded_legacy_rows += 1
                continue
            pnl = _finite(trade["realized_pnl"])
            if pnl is None:
                continue
            buy_proven = _submission_has_confirmed_fill(
                connection, trade["buy_order_id"], "BUY"
            )
            sell_proven = _submission_has_confirmed_fill(
                connection, trade["sell_order_id"], "SELL"
            )
            if not (buy_proven and sell_proven):
                continue
            run = _run_for_order(connection, trade["sell_order_id"], run_by_id)
            exact_rows.append((trade, pnl, _cohort_key(run)))

        settlement_rows = []
        for trade in connection.execute(
            """
            SELECT * FROM trades
            WHERE UPPER(status) = 'RESOLVED'
              AND settlement_pnl_assumption IS NOT NULL
            """
        ):
            if not _in_window(trade["resolution_observed_at"], start, end):
                continue
            value = _finite(trade["settlement_pnl_assumption"])
            if (
                value is None
                or not _valid_resolution_settlement(connection, trade)
            ):
                continue
            run = _run_for_time(trade["resolution_observed_at"], runs)
            settlement_rows.append((trade, value, _cohort_key(run)))

        cohort_values: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
            lambda: {
                "confirmed_sell_trade_count": 0,
                "confirmed_sell_pnl_usdc": 0.0,
                "proven_resolution_count": 0,
                "resolution_net_known_fee_assumption_usdc": 0.0,
                "resolution_gross_fee_unproven_assumption_usdc": 0.0,
            }
        )
        for _, pnl, cohort in exact_rows:
            cohort_values[cohort]["confirmed_sell_trade_count"] += 1
            cohort_values[cohort]["confirmed_sell_pnl_usdc"] += pnl
        for trade, value, cohort in settlement_rows:
            cohort_values[cohort]["proven_resolution_count"] += 1
            key = (
                "resolution_net_known_fee_assumption_usdc"
                if trade["settlement_assumption_basis"] == NET_SETTLEMENT_BASIS
                else "resolution_gross_fee_unproven_assumption_usdc"
            )
            cohort_values[cohort][key] += value

        evidence_trade_ids = {
            int(trade["id"]) for trade, _, _ in exact_rows + settlement_rows
        }
        cluster_rows = []
        if evidence_trade_ids:
            placeholders = ",".join("?" for _ in evidence_trade_ids)
            cluster_rows = list(
                connection.execute(
                    f"SELECT id, condition_id, market_slug, question FROM trades "
                    f"WHERE id IN ({placeholders})",
                    sorted(evidence_trade_ids),
                )
            )
        question_clusters: dict[str, set[str]] = defaultdict(set)
        slug_clusters: dict[str, set[str]] = defaultdict(set)
        for row in cluster_rows:
            question_clusters[str(row["question"] or "[missing question]")].add(
                str(row["condition_id"])
            )
            slug_clusters[str(row["market_slug"] or "[missing slug]")].add(
                str(row["condition_id"])
            )

        managed_open = []
        for trade in connection.execute("SELECT * FROM trades"):
            if str(trade["status"] or "").upper() not in OPEN_STATUSES:
                continue
            amount = _finite(trade["buy_amount"])
            managed_open.append(
                {
                    "trade_id": trade["id"],
                    "condition_id": trade["condition_id"],
                    "status": str(trade["status"]).upper(),
                    "requested_notional_usdc": round(amount or 0.0, 6),
                }
            )
        untracked = _untracked_buy_reservations(connection)
        reservation_audit = _buy_reservation_audit(connection)

    cohorts = []
    for cohort, values in sorted(cohort_values.items()):
        rounded = {
            key: round(value, 6) if isinstance(value, float) else value
            for key, value in values.items()
        }
        cohorts.append({**_cohort_dict(cohort), **rounded})

    return {
        "contract": "golden-cherry-exact-history-v1",
        "window": {
            "start_inclusive": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "timezone": "UTC",
        },
        "database": {
            "path": str(path),
            "sha256": digest,
            "quick_check": "ok",
            "opened_read_only": True,
        },
        "confirmed_sell": {
            "trade_count": len(exact_rows),
            "pnl_usdc": round(sum(item[1] for item in exact_rows), 6),
            "legacy_or_unproven_realized_pnl_rows_excluded": excluded_legacy_rows,
            "legacy_realized_pnl_summed_as_actual": False,
        },
        "proven_resolution_settlement": {
            "position_count": len(settlement_rows),
            "net_known_buy_fee_assumption_usdc": round(
                sum(
                    value
                    for trade, value, _ in settlement_rows
                    if trade["settlement_assumption_basis"] == NET_SETTLEMENT_BASIS
                ),
                6,
            ),
            "gross_fee_unproven_assumption_usdc": round(
                sum(
                    value
                    for trade, value, _ in settlement_rows
                    if trade["settlement_assumption_basis"] != NET_SETTLEMENT_BASIS
                ),
                6,
            ),
            "is_sell_cashflow": False,
        },
        "cohorts": cohorts,
        "current_exposure_snapshot": {
            "historical_as_of_reconstruction_available": False,
            "managed_open_count": len(managed_open),
            "managed_open_notional_usdc": round(
                sum(row["requested_notional_usdc"] for row in managed_open), 6
            ),
            "untracked_buy_reservation_count": len(untracked),
            "untracked_buy_reservation_notional_usdc": round(
                sum(row["requested_notional_usdc"] for row in untracked), 6
            ),
            "reservation_count_reconciliation": {
                **reservation_audit,
                "active_repository_semantics_count": len(untracked),
                "explanation": (
                    "active count uses the same shared SQL as TradeRepository; "
                    "operator-proven NO_ORDER_CREATED rows are not exposure"
                ),
            },
            "managed": managed_open,
            "untracked_buy_submissions": untracked,
        },
        "clustering": {
            "exact_question": [
                {
                    "question": question,
                    "condition_count": len(conditions),
                    "condition_ids": sorted(conditions),
                }
                for question, conditions in sorted(question_clusters.items())
            ],
            "market_slug_proxy": [
                {
                    "market_slug": slug,
                    "condition_count": len(conditions),
                    "condition_ids": sorted(conditions),
                }
                for slug, conditions in sorted(slug_clusters.items())
            ],
            "true_event_id_available": False,
        },
        "limitations": [
            "trades has no event_id; market_slug is only an explicitly labeled proxy",
            "trade status is mutable, so open exposure is current DB state, not reconstructed as of end_exclusive",
            "resolution settlement is a payout assumption, not SELL cashflow or redeem/account credit evidence",
            "legacy naive database timestamps are interpreted as UTC by Cherry's persistence contract",
            "Pomegranate displayed-price counterfactual shards are not included in v1",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--start", required=True, help="exact UTC ISO-8601 timestamp")
    parser.add_argument("--end", required=True, help="exclusive exact UTC timestamp")
    parser.add_argument("--output", type=Path, help="optional JSON output path")
    args = parser.parse_args(argv)
    report = analyze(
        args.db,
        parse_exact_utc(args.start),
        parse_exact_utc(args.end),
    )
    payload = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
