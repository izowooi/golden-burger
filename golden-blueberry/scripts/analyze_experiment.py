#!/usr/bin/env python3
"""Strict read-only A/B evidence analyzer for Golden Blueberry."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from statistics import fmean
from typing import Any, Sequence


EXPECTED_SURGE = {"A": 0.02, "B": 0.05}
MIN_CONFIRMED_CLOSED_PER_ARM = 20
SIZE_TOLERANCE = 1e-6
TERMINAL_ORDER_STATUSES = {
    "MATCHED",
    "CANCELED",
    "CANCELLED",
    "CANCELED_MARKET_RESOLVED",
    "INVALID",
}
FULL_FILL_PROOF = "AUTHENTICATED_TOKEN_TRADE_CATALOG_FULL_FILL"
EXACT_PNL_BASIS = "exact_reconciled_buy_sell_confirmed_fills_net_known_fees"
RESIDUAL_PNL_BASIS = (
    "exact_reconciled_confirmed_fills_net_known_fees_sub_0.01_sell_residual"
)


def _utc_instant(value: str, *, field: str) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError as error:
        raise ValueError(f"{field} must be an exact RFC3339 UTC instant") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include Z or +00:00")
    if parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must be UTC, not a local offset")
    return parsed.astimezone(timezone.utc)


def _window(start: str, end: str) -> tuple[str, str]:
    """Return SQLite UTC-naive half-open bounds from exact UTC instants."""
    begin = _utc_instant(start, field="review start")
    finish = _utc_instant(end, field="review end")
    if finish <= begin:
        raise ValueError("review end must be after review start")
    return (
        begin.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f"),
        finish.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f"),
    )


def _display_utc(value: str) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")
    return parsed.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _db_utc_key(value: Any) -> str | None:
    """Normalize SQLite/ISO timestamp representations without losing micros."""
    if value is None:
        return None
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(
            raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        )
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M:%S.%f")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ValueError(f"database not found: {path}")
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.create_function("utc_key", 1, _db_utc_key, deterministic=True)
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        connection.close()
        raise ValueError(f"database integrity check failed: {path}: {integrity}")
    required = {
        "trades",
        "entry_signal_decisions",
        "candidate_execution_decisions",
        "run_audits",
        "strategy_configs",
        "order_submissions",
        "order_status_events",
        "order_fills",
    }
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(required - tables)
    if missing:
        connection.close()
        raise ValueError(f"database missing evidence tables: {missing}")
    return connection


def _cohorts(
    connection: sqlite3.Connection, start: str, end: str
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT ra.config_hash, ra.job_name, sc.config_json,
               COUNT(*) AS successful_runs
        FROM run_audits ra
        JOIN strategy_configs sc ON sc.config_hash = ra.config_hash
        WHERE ra.strategy_name = 'golden-blueberry'
          AND ra.mode = 'live' AND ra.status = 'SUCCESS'
          AND utc_key(ra.started_at) >= ? AND utc_key(ra.started_at) < ?
        GROUP BY ra.config_hash, ra.job_name, sc.config_json
        ORDER BY ra.config_hash, ra.job_name
        """,
        (start, end),
    ).fetchall()
    cohorts: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["config_json"])
        trading = payload.get("trading") or {}
        entry = trading.get("entry") or {}
        common_trading = json.loads(json.dumps(trading))
        common_entry = common_trading.get("entry") or {}
        common_entry.pop("min_surge", None)
        common_trading["entry"] = common_entry
        common_contract_sha256 = hashlib.sha256(
            json.dumps(
                common_trading,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        cohorts.append(
            {
                "config_hash": row["config_hash"],
                "successful_runs": int(row["successful_runs"]),
                "min_surge": float(entry["min_surge"]),
                "common_contract_sha256": common_contract_sha256,
                "strategy_source_digest": str(
                    trading.get("strategy_source_digest") or ""
                ),
                "job_name": str(row["job_name"]),
                "mode": str(payload.get("mode") or ""),
            }
        )
    return cohorts


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _order_evidence(
    connection: sqlite3.Connection, order_id: Any, expected_side: str
) -> tuple[dict[str, float] | None, list[str]]:
    normalized_order_id = str(order_id or "").strip()
    side = expected_side.upper()
    if not normalized_order_id:
        return None, [f"{side.lower()}_order_id_missing"]
    submissions = connection.execute(
        """
        SELECT submission_id, side, simulation, latest_order_status,
               latest_size_matched, latest_status_domain_error,
               needs_reconciliation, reconciliation_error, reconciliation_proof
        FROM order_submissions WHERE order_id = ?
        """,
        (normalized_order_id,),
    ).fetchall()
    if len(submissions) != 1:
        return None, [f"{side.lower()}_submission_count_{len(submissions)}"]
    submission = submissions[0]
    issues: list[str] = []
    status = str(submission["latest_order_status"] or "").upper()
    proof = str(submission["reconciliation_proof"] or "")
    if str(submission["side"] or "").upper() != side:
        issues.append(f"{side.lower()}_submission_side_mismatch")
    if int(submission["simulation"] or 0) != 0:
        issues.append(f"{side.lower()}_submission_not_live")
    if int(submission["needs_reconciliation"] or 0) != 0:
        issues.append(f"{side.lower()}_reconciliation_pending")
    if str(submission["latest_status_domain_error"] or ""):
        issues.append(f"{side.lower()}_status_domain_error")
    if str(submission["reconciliation_error"] or ""):
        issues.append(f"{side.lower()}_reconciliation_error")
    if status not in TERMINAL_ORDER_STATUSES and proof != FULL_FILL_PROOF:
        issues.append(f"{side.lower()}_order_not_terminal")
    matched_size = _safe_float(submission["latest_size_matched"])
    if (matched_size is None or matched_size <= 0) and proof != FULL_FILL_PROOF:
        issues.append(f"{side.lower()}_matched_size_invalid")

    fills = connection.execute(
        """
        SELECT status, side, size, price, liquidity_role, fee_rate_bps,
               fee_amount_usdc, domain_error
        FROM order_fills
        WHERE submission_id = ? AND order_id = ?
        """,
        (submission["submission_id"], normalized_order_id),
    ).fetchall()
    if not fills:
        issues.append(f"{side.lower()}_confirmed_fills_missing")
    size_total = 0.0
    notional_total = 0.0
    fee_total = 0.0
    for fill in fills:
        fill_status = str(fill["status"] or "").upper().removeprefix("TRADE_STATUS_")
        if fill_status != "CONFIRMED":
            issues.append(f"{side.lower()}_nonconfirmed_fill_present")
            continue
        size = _safe_float(fill["size"])
        price = _safe_float(fill["price"])
        if (
            str(fill["side"] or "").upper() != side
            or size is None
            or size <= 0
            or price is None
            or not 0 < price <= 1
            or str(fill["domain_error"] or "")
        ):
            issues.append(f"{side.lower()}_confirmed_fill_invalid")
            continue
        size_total += size
        notional_total += size * price
        raw_fee = fill["fee_amount_usdc"]
        fee_rate = _safe_float(fill["fee_rate_bps"])
        if fill["fee_rate_bps"] is not None and (fee_rate is None or fee_rate < 0):
            issues.append(f"{side.lower()}_fee_rate_invalid")
        if raw_fee is None:
            role = str(fill["liquidity_role"] or "").upper()
            if fee_rate == 0.0 or (fill["fee_rate_bps"] is None and role == "MAKER"):
                pass
            else:
                issues.append(f"{side.lower()}_fee_incomplete")
        else:
            fee = _safe_float(raw_fee)
            if fee is None or fee < 0:
                issues.append(f"{side.lower()}_fee_invalid")
            else:
                fee_total += fee
    if matched_size is not None and not math.isclose(
        size_total, matched_size, rel_tol=0.0, abs_tol=SIZE_TOLERANCE
    ):
        issues.append(f"{side.lower()}_confirmed_size_not_matched_size")
    if size_total <= 0:
        issues.append(f"{side.lower()}_confirmed_size_empty")
    if issues:
        return None, sorted(set(issues))
    return {
        "size": size_total,
        "vwap": notional_total / size_total,
        "fee": fee_total,
    }, []


def _unresolved_exposure(connection: sqlite3.Connection) -> dict[str, Any]:
    trade_rows = connection.execute(
        """
        SELECT status, buy_amount FROM trades
        WHERE mode = 'live' AND UPPER(status) IN (
            'PENDING_BUY', 'HOLDING', 'PENDING_SELL', 'QUARANTINED', 'RESIDUAL'
        )
        """
    ).fetchall()
    status_counts = Counter(str(row["status"]).upper() for row in trade_rows)
    trade_notional = 0.0
    invalid_notional = False
    for row in trade_rows:
        value = _safe_float(row["buy_amount"])
        if value is None or value < 0:
            invalid_notional = True
        else:
            trade_notional += value
    reservation_rows = connection.execute(
        """
        SELECT requested_price, requested_size
        FROM order_submissions AS submission
        WHERE submission.simulation = 0 AND UPPER(submission.side) = 'BUY'
          AND NOT EXISTS (
              SELECT 1 FROM trades AS trade
              WHERE submission.order_id IS NOT NULL
                AND trade.buy_order_id = submission.order_id
          )
          AND (
              UPPER(submission.response_status) IN (
                  'INTENT', 'SUBMIT_OUTCOME_UNKNOWN', 'EVIDENCE_WRITE_FAILED'
              ) OR submission.success = 1
          )
          AND NOT (
              submission.needs_reconciliation = 0
              AND COALESCE(submission.latest_size_matched, -1) = 0
              AND UPPER(COALESCE(submission.latest_order_status, '')) IN (
                  'CANCELED', 'CANCELLED', 'CANCELED_MARKET_RESOLVED', 'INVALID'
              )
          )
          AND COALESCE(submission.outcome_resolution, '') != 'NO_ORDER_CREATED'
        """
    ).fetchall()
    reserved_notional = 0.0
    for row in reservation_rows:
        price = _safe_float(row["requested_price"])
        size = _safe_float(row["requested_size"])
        if price is None or size is None or price <= 0 or size <= 0:
            invalid_notional = True
        else:
            reserved_notional += price * size
    return {
        "trade_count": len(trade_rows),
        "trade_status_counts": dict(sorted(status_counts.items())),
        "untracked_buy_reservation_count": len(reservation_rows),
        "conservative_position_count": len(trade_rows) + len(reservation_rows),
        "conservative_open_notional_usdc": (
            None if invalid_notional else trade_notional + reserved_notional
        ),
        "notional_evidence_complete": not invalid_notional,
    }


def _arm_metrics(path: Path, label: str, start: str, end: str) -> dict[str, Any]:
    connection = _connect(path)
    try:
        cohorts = _cohorts(connection, start, end)
        signals = connection.execute(
            """
            SELECT decision, reason, surge FROM entry_signal_decisions
            WHERE utc_key(observed_at) >= ? AND utc_key(observed_at) < ?
            """,
            (start, end),
        ).fetchall()
        dispositions = connection.execute(
            """
            SELECT decision, stage, reason FROM candidate_execution_decisions
            WHERE utc_key(observed_at) >= ? AND utc_key(observed_at) < ?
            """,
            (start, end),
        ).fetchall()
        trades = connection.execute(
            """
            SELECT id, status, condition_id, event_id, buy_amount,
                   buy_order_id, sell_order_id, sell_residual_shares,
                   realized_pnl, pnl_basis, settlement_pnl_assumption
            FROM trades
            WHERE utc_key(buy_timestamp) >= ? AND utc_key(buy_timestamp) < ?
              AND mode = 'live'
            """,
            (start, end),
        ).fetchall()

        scored: list[dict[str, Any]] = []
        evidence_rejections: Counter[str] = Counter()
        confirmed_buy = 0
        for trade in trades:
            buy, buy_issues = _order_evidence(
                connection, trade["buy_order_id"], "BUY"
            )
            if buy is not None:
                confirmed_buy += 1
            if str(trade["status"] or "").upper() not in {"COMPLETED", "RESIDUAL"}:
                continue
            sell, sell_issues = _order_evidence(
                connection, trade["sell_order_id"], "SELL"
            )
            issues = buy_issues + sell_issues
            if buy is None or sell is None:
                for issue in issues:
                    evidence_rejections[issue] += 1
                continue
            difference = buy["size"] - sell["size"]
            exact_match = math.isclose(
                buy["size"], sell["size"], rel_tol=0.0, abs_tol=SIZE_TOLERANCE
            )
            residual = _safe_float(trade["sell_residual_shares"])
            explicit_residual = (
                not exact_match
                and SIZE_TOLERANCE < difference < 0.01
                and residual is not None
                and math.isclose(
                    residual, difference, rel_tol=0.0, abs_tol=SIZE_TOLERANCE
                )
                and str(trade["pnl_basis"] or "") == RESIDUAL_PNL_BASIS
            )
            if not exact_match and not explicit_residual:
                evidence_rejections["buy_sell_size_mismatch"] += 1
                continue
            expected_basis = RESIDUAL_PNL_BASIS if explicit_residual else EXACT_PNL_BASIS
            if str(trade["pnl_basis"] or "") != expected_basis:
                evidence_rejections["pnl_basis_mismatch"] += 1
                continue
            allocated_buy_fee = buy["fee"]
            if explicit_residual:
                allocated_buy_fee *= sell["size"] / buy["size"]
            gross = (sell["vwap"] - buy["vwap"]) * sell["size"]
            net = gross - allocated_buy_fee - sell["fee"]
            stored_pnl = _safe_float(trade["realized_pnl"])
            if stored_pnl is None or not math.isclose(
                stored_pnl, net, rel_tol=0.0, abs_tol=1e-6
            ):
                evidence_rejections["realized_pnl_ledger_mismatch"] += 1
                continue
            scored.append(
                {
                    "trade_id": int(trade["id"]),
                    "condition_id": str(trade["condition_id"] or ""),
                    "event_id": str(trade["event_id"] or ""),
                    "gross": gross,
                    "net": net,
                    "residual": difference if explicit_residual else 0.0,
                }
            )

        event_counts = Counter(row["event_id"] for row in scored if row["event_id"])
        clustered_events = {event for event, count in event_counts.items() if count > 1}
        settlement_values = [
            value
            for row in trades
            if (value := _safe_float(row["settlement_pnl_assumption"])) is not None
        ]
        signal_reasons = Counter(str(row["reason"]) for row in signals)
        disposition_reasons = Counter(
            f"{row['stage']}:{row['reason']}" for row in dispositions
        )
        surges = [float(row["surge"]) for row in signals]
        result = {
            "label": label,
            "database": str(path.resolve()),
            "database_sha256": _sha256(path),
            "cohorts": cohorts,
            "signals": {
                "total_first_crossings": len(signals),
                "candidates": sum(row["decision"] == "candidate" for row in signals),
                "rejected": sum(row["decision"] == "rejected" for row in signals),
                "reasons": dict(sorted(signal_reasons.items())),
                "mean_surge": fmean(surges) if surges else None,
            },
            "candidate_execution": {
                "total": len(dispositions),
                "decision_counts": dict(
                    sorted(Counter(str(row["decision"]) for row in dispositions).items())
                ),
                "stage_reason_counts": dict(sorted(disposition_reasons.items())),
            },
            "trades": {
                "submitted": len(trades),
                "terminal_reconciled_confirmed_buy": confirmed_buy,
                "confirmed_closed": len(scored),
                "fee_complete_closed": len(scored),
                "evidence_rejected_closed": sum(evidence_rejections.values()),
                "evidence_rejection_reasons": dict(sorted(evidence_rejections.items())),
                "confirmed_gross_pnl_usdc": sum(row["gross"] for row in scored),
                "confirmed_net_pnl_usdc": (
                    sum(row["net"] for row in scored) if scored else None
                ),
                "confirmed_net_mean_usdc": (
                    fmean(row["net"] for row in scored) if scored else None
                ),
                "confirmed_net_win_rate": (
                    sum(row["net"] > 0 for row in scored) / len(scored)
                    if scored
                    else None
                ),
                "resolution_settlement_assumption_pnl_usdc": sum(settlement_values),
                "status_counts": dict(
                    sorted(Counter(str(row["status"]) for row in trades).items())
                ),
            },
            "event_clustering": {
                "unique_events": len(event_counts),
                "clustered_event_count": len(clustered_events),
                "trades_in_clustered_events": sum(
                    count for event, count in event_counts.items() if event in clustered_events
                ),
                "max_trades_per_event": max(event_counts.values(), default=0),
            },
            "unresolved_exposure": _unresolved_exposure(connection),
        }
        result["_scored"] = scored
        return result
    finally:
        connection.close()


def _paired_overlap(arms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    a_rows = arms["A"]["_scored"]
    b_rows = arms["B"]["_scored"]
    a_conditions = {row["condition_id"] for row in a_rows if row["condition_id"]}
    b_conditions = {row["condition_id"] for row in b_rows if row["condition_id"]}
    a_events = {row["event_id"] for row in a_rows if row["event_id"]}
    b_events = {row["event_id"] for row in b_rows if row["event_id"]}
    a_unique = {row["condition_id"]: row for row in a_rows}
    b_unique = {row["condition_id"]: row for row in b_rows}
    paired = sorted(a_conditions & b_conditions)
    differences = [b_unique[key]["net"] - a_unique[key]["net"] for key in paired]
    return {
        "shared_condition_count": len(paired),
        "shared_event_count": len(a_events & b_events),
        "arm_a_condition_count": len(a_conditions),
        "arm_b_condition_count": len(b_conditions),
        "paired_net_pnl_difference_b_minus_a_usdc": (
            sum(differences) if differences else None
        ),
        "paired_mean_difference_b_minus_a_usdc": (
            fmean(differences) if differences else None
        ),
    }


def analyze(
    arm_a: Path,
    arm_b: Path,
    output_dir: Path,
    review_start: str,
    review_end: str,
) -> Path:
    if arm_a.resolve() == arm_b.resolve():
        raise ValueError("A/B arms must use different database files")
    start, end = _window(review_start, review_end)
    arms = {
        "A": _arm_metrics(arm_a, "A", start, end),
        "B": _arm_metrics(arm_b, "B", start, end),
    }
    issues: list[str] = []
    source_digests: set[str] = set()
    common_contracts: set[str] = set()
    job_names: set[str] = set()
    for label, arm in arms.items():
        cohorts = arm["cohorts"]
        if len(cohorts) != 1:
            issues.append(f"arm_{label}_cohort_count_{len(cohorts)}")
        else:
            cohort = cohorts[0]
            if not math.isclose(cohort["min_surge"], EXPECTED_SURGE[label]):
                issues.append(f"arm_{label}_unexpected_min_surge")
            digest = cohort["strategy_source_digest"]
            if len(digest) != 64:
                issues.append(f"arm_{label}_source_digest_invalid")
            else:
                source_digests.add(digest)
            common_contracts.add(cohort["common_contract_sha256"])
            job_names.add(cohort["job_name"])
        if arm["trades"]["evidence_rejected_closed"]:
            issues.append(f"arm_{label}_closed_trade_evidence_rejected")
        exposure = arm["unresolved_exposure"]
        if exposure["conservative_position_count"]:
            issues.append(f"arm_{label}_unresolved_exposure")
        if not exposure["notional_evidence_complete"]:
            issues.append(f"arm_{label}_unresolved_notional_invalid")
    if len(source_digests) > 1:
        issues.append("arms_have_different_strategy_source_digest")
    if len(common_contracts) > 1:
        issues.append("arms_have_different_common_contract")
    if len(job_names) == 1 and all(arm["cohorts"] for arm in arms.values()):
        issues.append("arms_share_job_name")

    sample_ready = all(
        arm["trades"]["confirmed_closed"] >= MIN_CONFIRMED_CLOSED_PER_ARM
        for arm in arms.values()
    )
    status = (
        "NOT_EVALUABLE_EVIDENCE_CONTRACT"
        if issues
        else "INSUFFICIENT_CONFIRMED_SAMPLE"
        if not sample_ready
        else "EVALUABLE_NO_AUTOMATIC_WINNER"
    )
    dependence = _paired_overlap(arms)
    for arm in arms.values():
        arm.pop("_scored", None)
    display_start = _display_utc(start)
    display_end = _display_utc(end)
    result = {
        "schema_version": 2,
        "strategy": "golden-blueberry",
        "review_window": {
            "start_utc": display_start,
            "end_exclusive_utc": display_end,
        },
        "status": status,
        "issues": sorted(set(issues)),
        "promotion_contract": {
            "minimum_confirmed_closed_per_arm": MIN_CONFIRMED_CLOSED_PER_ARM,
            "sample_ready": sample_ready,
            "terminal_reconciled_confirmed_fills_required": True,
            "fee_coverage_complete_required": True,
            "automatic_winner_selection": False,
        },
        "paired_overlap": dependence,
        "arms": arms,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "blueberry-ab-report.json"
    json_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Golden Blueberry A/B report",
        "",
        f"- Status: `{status}`",
        f"- Window: `{display_start}` to `{display_end}` (exclusive)",
        f"- Issues: `{result['issues'] or 'none'}`",
        f"- Paired overlap: {dependence['shared_condition_count']} conditions / "
        f"{dependence['shared_event_count']} events",
        "",
        "| Arm | First crossings | Candidates | Submitted | Valid closed | Rejected closed evidence | Unresolved exposure | Net P&L |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, arm in arms.items():
        signals = arm["signals"]
        trades = arm["trades"]
        net = trades["confirmed_net_pnl_usdc"]
        lines.append(
            f"| {label} | {signals['total_first_crossings']} | "
            f"{signals['candidates']} | {trades['submitted']} | "
            f"{trades['confirmed_closed']} | {trades['evidence_rejected_closed']} | "
            f"{arm['unresolved_exposure']['conservative_position_count']} | "
            f"{('-' if net is None else f'${net:.4f}')} |"
        )
    lines.extend(
        [
            "",
            "Only terminal, reconciled exact-order CONFIRMED BUY/SELL fills with complete fees are scored. Resolution settlement assumptions remain separate.",
        ]
    )
    (output_dir / "blueberry-ab-report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return json_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm-a", type=Path, required=True)
    parser.add_argument("--arm-b", type=Path, required=True)
    parser.add_argument("--review-start", required=True, help="RFC3339 UTC instant")
    parser.add_argument(
        "--review-end", required=True, help="exclusive RFC3339 UTC instant"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(
        analyze(
            args.arm_a,
            args.arm_b,
            args.output_dir,
            args.review_start,
            args.review_end,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
