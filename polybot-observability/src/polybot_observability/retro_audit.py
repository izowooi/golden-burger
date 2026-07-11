"""Read-only SQLite audit and consistent backup support for monthly retrospectives."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class AuditIssue:
    severity: str
    code: str
    message: str


def discover_databases(root: Path, *, include_sim: bool = False) -> list[Path]:
    """Find trade DBs, excluding simulation evidence from the live cohort by default."""
    if root.is_file():
        return [root.resolve()]
    patterns = ("trades.db", "trades_sim.db") if include_sim else ("trades.db",)
    candidates = {
        path.resolve()
        for pattern in patterns
        for path in root.rglob(pattern)
        if path.is_file()
    }
    return sorted(candidates)


def audit_database(
    db_path: Path,
    *,
    days: int,
    as_of: datetime,
    log_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    """Return a secret-safe, machine-readable audit for one bot database."""
    resolved = db_path.expanduser().resolve()
    is_simulation_database = resolved.stem.endswith("_sim")
    cutoff = as_of - timedelta(days=days)
    issues: list[AuditIssue] = []
    result: dict[str, Any] = {
        "database": str(resolved),
        "cohort": "simulation_assumption" if is_simulation_database else "live",
        "simulation_database": is_simulation_database,
        "period": {"start": cutoff.isoformat(), "end": as_of.isoformat(), "days": days},
        "issues": [],
    }

    snapshot_directory = tempfile.TemporaryDirectory(prefix="polybot-retro-")
    snapshot_path = Path(snapshot_directory.name) / "audit-snapshot.db"
    try:
        source_connection = sqlite3.connect(
            f"file:{resolved}?mode=ro", uri=True, timeout=30
        )
        target_connection = sqlite3.connect(snapshot_path, timeout=30)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        result["database_sha256"] = _sha256(snapshot_path)
        result["database_snapshot"] = "sqlite_online_backup"
        connection = sqlite3.connect(
            f"file:{snapshot_path}?mode=ro", uri=True, timeout=30
        )
    except (OSError, sqlite3.Error) as error:
        issues.append(AuditIssue("CRITICAL", "database_unreadable", str(error)))
        result["issues"] = [asdict(issue) for issue in issues]
        snapshot_directory.cleanup()
        return result

    connection.row_factory = sqlite3.Row
    try:
        integrity_rows = [row[0] for row in connection.execute("PRAGMA quick_check")]
        result["integrity"] = integrity_rows
        if integrity_rows != ["ok"]:
            issues.append(
                AuditIssue("CRITICAL", "integrity_failed", "; ".join(integrity_rows))
            )

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        result["tables"] = sorted(tables)
        strategy_name = _strategy_name(connection, resolved, tables)
        result["strategy_name"] = strategy_name
        cohort_modes = _cohort_mode_summary(connection, tables)
        result["cohort_mode_evidence"] = cohort_modes
        cohort_mismatch = (
            is_simulation_database and cohort_modes["has_live_evidence"]
        ) or (not is_simulation_database and cohort_modes["has_simulation_evidence"]) or cohort_modes[
            "has_invalid_mode_evidence"
        ]
        if cohort_mismatch:
            issues.append(
                AuditIssue(
                    "CRITICAL",
                    "cohort_mode_mismatch",
                    "DB filename cohort와 persisted run/order mode가 일치하지 않습니다: "
                    f"{cohort_modes}",
                )
            )
        treat_as_simulation = is_simulation_database and not cohort_modes[
            "has_live_evidence"
        ]
        if not treat_as_simulation:
            result["cohort"] = "live"

        if "trades" not in tables:
            issues.append(AuditIssue("CRITICAL", "trades_missing", "trades table이 없습니다"))
            result["trades"] = None
        else:
            result["trades"] = _trade_summary(connection, cutoff, as_of)
            trade_columns = _columns(connection, "trades")
            result["trade_columns"] = sorted(trade_columns)
            fill_ledger = _fill_ledger_summary(connection, tables, cutoff, as_of)
            result["fill_ledger"] = fill_ledger
            result["trades"]["pnl_quality"] = (
                "SIMULATION_ASSUMPTION"
                if treat_as_simulation
                else "ORDER_ASSUMPTION"
            )
            if not treat_as_simulation and (
                fill_ledger
                and fill_ledger["contract_complete"]
                and fill_ledger["completed_trade_fill_coverage"] == 1.0
            ):
                result["trades"]["pnl_quality"] = (
                    "FILL_LEDGER_NET"
                    if fill_ledger["confirmed_fill_net_pnl_usdc"] is not None
                    else "FILL_LEDGER_GROSS_ONLY"
                    if fill_ledger["confirmed_fill_gross_pnl_usdc"] is not None
                    else "FILL_LEDGER_NO_COMPLETED_TRADES"
                )
            if treat_as_simulation:
                pass
            elif fill_ledger is None:
                issues.append(
                    AuditIssue(
                        "CRITICAL",
                        "execution_ledger_missing",
                        "실제 fill/partial fill/fee 원장이 없어 trades P&L은 주문 체결 가정치입니다",
                    )
                )
            elif not fill_ledger["contract_complete"]:
                issues.append(
                    AuditIssue(
                        "CRITICAL",
                        "execution_ledger_incomplete",
                        "fill 원장이 비었거나 order/size/price/fee/status 계약을 충족하지 않습니다",
                    )
                )
            else:
                if fill_ledger["completed_trade_fill_coverage"] < 1.0:
                    issues.append(
                        AuditIssue(
                            "CRITICAL",
                            "completed_trade_fill_gap",
                            "COMPLETED trade의 BUY/SELL CONFIRMED fill 커버리지가 "
                            f"{fill_ledger['completed_trade_fill_coverage']:.1%}입니다",
                        )
                    )
                if fill_ledger["overfilled_orders"]:
                    issues.append(
                        AuditIssue(
                            "CRITICAL",
                            "fill_quantity_overflow",
                            "CONFIRMED fill 합계가 해당 주문의 latest_size_matched를 "
                            f"초과한 주문이 {fill_ledger['overfilled_orders']}건 있습니다",
                        )
                    )
                if fill_ledger["invalid_confirmed_fill_domains"]:
                    issues.append(
                        AuditIssue(
                            "CRITICAL",
                            "confirmed_fill_domain_invalid",
                            "CONFIRMED fill의 size/price/bucket domain이 잘못된 row가 "
                            f"{fill_ledger['invalid_confirmed_fill_domains']}건 있습니다",
                        )
                    )
                if (
                    fill_ledger["invalid_submission_domains"]
                    or fill_ledger["invalid_order_status_domains"]
                ):
                    issues.append(
                        AuditIssue(
                            "CRITICAL",
                            "order_execution_domain_invalid",
                            "submission/order status 수치 domain이 잘못되었습니다 "
                            f"(submission={fill_ledger['invalid_submission_domains']}, "
                            f"status={fill_ledger['invalid_order_status_domains']})",
                        )
                    )
                if fill_ledger["closed_trade_quantity_mismatches"]:
                    issues.append(
                        AuditIssue(
                            "CRITICAL",
                            "closed_trade_fill_quantity_mismatch",
                            "COMPLETED trade의 실제 BUY/SELL fill 수량이 일치하지 않는 건이 "
                            f"{fill_ledger['closed_trade_quantity_mismatches']}건 있습니다",
                        )
                    )
                if fill_ledger["legacy_share_mismatches"]:
                    issues.append(
                        AuditIssue(
                            "MEDIUM",
                            "legacy_trade_share_mismatch",
                            "실제 reconciled fill과 legacy trades share 컬럼이 다른 건이 "
                            f"{fill_ledger['legacy_share_mismatches']}건 있습니다",
                        )
                    )
                if fill_ledger["stale_reconciliations"]:
                    issues.append(
                        AuditIssue(
                            "HIGH",
                            "stale_order_reconciliation",
                            f"1시간 넘은 미완료 주문 대사 {fill_ledger['stale_reconciliations']}건",
                        )
                    )
                if fill_ledger["uncertain_submission_outcomes"]:
                    issues.append(
                        AuditIssue(
                            "CRITICAL",
                            "uncertain_submission_outcome",
                            "POST 전후 결과가 확정되지 않은 order intent가 "
                            f"{fill_ledger['uncertain_submission_outcomes']}건 있습니다",
                        )
                    )
                if fill_ledger["legacy_unavailable_evidence_gaps"]:
                    issues.append(
                        AuditIssue(
                            "HIGH",
                            "legacy_order_evidence_gap",
                            "normal/pre-migration catalog에서 찾을 수 없어 fill을 "
                            "확정하지 못한 legacy 주문이 "
                            f"{fill_ledger['legacy_unavailable_evidence_gaps']}건 있습니다",
                        )
                    )
                if fill_ledger["operator_catalog_evidence_gaps"]:
                    issues.append(
                        AuditIssue(
                            "HIGH",
                            "operator_catalog_evidence_gap",
                            "operator가 live gate에서는 격리했지만 fill을 확정하지 못한 "
                            "catalog-missing 주문이 "
                            f"{fill_ledger['operator_catalog_evidence_gaps']}건 있습니다",
                        )
                    )
                if fill_ledger["confirmed_fills"] and fill_ledger["fee_known_ratio"] < 1.0:
                    issues.append(
                        AuditIssue(
                            "HIGH",
                            "fill_fee_missing",
                            "CONFIRMED fill 중 실제 fee amount를 확정할 수 없는 비율이 "
                            f"{1 - fill_ledger['fee_known_ratio']:.1%}입니다",
                        )
                    )
                if (
                    fill_ledger["confirmed_fills"]
                    and fill_ledger["liquidity_role_known_ratio"] < 1.0
                ):
                    issues.append(
                        AuditIssue(
                            "HIGH",
                            "fill_liquidity_role_missing",
                            "CONFIRMED fill 중 MAKER/TAKER role이 확정되지 않은 비율이 "
                            f"{1 - fill_ledger['liquidity_role_known_ratio']:.1%}입니다",
                        )
                    )
            if (
                not treat_as_simulation
                and not {"buy_order_id", "sell_order_id"}.issubset(trade_columns)
            ):
                issues.append(
                    AuditIssue(
                        "HIGH",
                        "order_ids_missing",
                        "주문 ID 컬럼이 없어 CLOB 대사가 불가능합니다",
                    )
                )

        if {"run_audits", "strategy_configs"}.issubset(tables):
            result["runs"] = _run_summary(connection, cutoff, as_of)
            if result["runs"]["success"] == 0:
                issues.append(
                    AuditIssue("HIGH", "no_successful_runs", "선택 기간 SUCCESS run이 없습니다")
                )
            if result["runs"]["failed"]:
                issues.append(
                    AuditIssue(
                        "HIGH",
                        "failed_runs",
                        f"선택 기간 FAILED run {result['runs']['failed']}건",
                    )
                )
            if result["runs"]["stale_running"]:
                issues.append(
                    AuditIssue(
                        "HIGH",
                        "stale_running_runs",
                        f"1시간 넘게 RUNNING인 run {result['runs']['stale_running']}건",
                    )
                )
            if result["runs"]["unknown_git_runs"]:
                issues.append(
                    AuditIssue(
                        "HIGH",
                        "unknown_code_version",
                        f"Git commit을 알 수 없는 run {result['runs']['unknown_git_runs']}건",
                    )
                )
            max_gap = result["runs"].get("max_success_gap_hours")
            if max_gap is not None and max_gap > 1.0:
                issues.append(
                    AuditIssue(
                        "HIGH",
                        "run_schedule_gap",
                        f"SUCCESS run 최대 간격 {max_gap:.2f}시간",
                    )
                )
        else:
            result["runs"] = None
            issues.append(
                AuditIssue(
                    "HIGH",
                    "run_provenance_missing",
                    "run_audits/strategy_configs가 없어 실제 config와 code version을 복원할 수 없습니다",
                )
            )

        minimum_history_hours = {
            "golden-honeydew": 24.0,
            "golden-nectarine": 19.0 * 24.0,
        }.get(strategy_name, 0.0)
        if "market_snapshots" in tables:
            result["market_snapshots"] = _snapshot_summary(
                connection,
                cutoff,
                as_of,
                minimum_history_hours=minimum_history_hours,
            )
        else:
            result["market_snapshots"] = None

        result["market_sweeps"] = _sweep_summary(connection, tables, cutoff, as_of)
        result["market_catalog"] = _catalog_summary(
            connection, tables, cutoff, as_of
        )

        if strategy_name in {"golden-honeydew", "golden-nectarine"}:
            snapshots = result.get("market_snapshots") or {}
            invalid_snapshot_rows = snapshots.get("invalid_value_rows") or 0
            if invalid_snapshot_rows:
                issues.append(
                    AuditIssue(
                        "CRITICAL",
                        "archive_snapshot_domain_invalid",
                        "market snapshot value domain이 잘못된 row가 "
                        f"{invalid_snapshot_rows}건 있습니다: "
                        f"{snapshots.get('invalid_value_reasons', {})}",
                    )
                )
            window_ratio = snapshots.get("requested_window_coverage_ratio") or 0
            cadence_ratio = snapshots.get("five_minute_bucket_coverage_ratio") or 0
            per_market_p10 = snapshots.get("per_market_cadence_p10") or 0
            history_p10 = snapshots.get("per_market_history_depth_p10") or 0
            sweeps = result.get("market_sweeps") or {}
            sweep_bucket_ratio = (
                sweeps.get("complete_sweep_bucket_coverage_ratio") or 0
            )
            attested_market_p10 = (
                sweeps.get("per_market_attested_snapshot_p10") or 0
            )
            eligible_snapshot_ratio = (
                sweeps.get("snapshot_eligible_coverage_ratio") or 0
            )
            qualified_eligibility_ratio = (
                sweeps.get("qualified_snapshot_eligibility_ratio") or 0
            )
            if (
                window_ratio < 0.9
                or cadence_ratio < 0.8
                or per_market_p10 < 0.8
                or history_p10 < 0.8
                or sweep_bucket_ratio < 0.8
                or attested_market_p10 < 0.8
                or eligible_snapshot_ratio < 0.99
                or qualified_eligibility_ratio < 0.99
            ):
                issues.append(
                    AuditIssue(
                        "HIGH",
                        "archive_window_short",
                        "중앙 아카이브가 요청 기간/5분 sweep/전략 lookback을 충분히 덮지 못합니다 "
                        f"(window={window_ratio:.1%}, global cadence={cadence_ratio:.1%}, "
                        f"observed per-market p10={per_market_p10:.1%}, "
                        f"history p10={history_p10:.1%}, sweep={sweep_bucket_ratio:.1%}, "
                        f"attested per-market p10={attested_market_p10:.1%}, "
                        f"eligible snapshots={eligible_snapshot_ratio:.1%}, "
                        f"qualified eligibility={qualified_eligibility_ratio:.1%})",
                    )
                )
            if not sweeps or not sweeps.get("contract_complete"):
                issues.append(
                    AuditIssue(
                        "HIGH",
                        "market_sweep_attestation_missing",
                        "완전한 Gamma keyset sweep와 market membership denominator가 없습니다",
                    )
                )
            elif sweeps.get("invariant_failures") or sweeps.get("incomplete_sweeps"):
                issues.append(
                    AuditIssue(
                        "CRITICAL",
                        "market_sweep_attestation_invalid",
                        "Gamma sweep count/digest/hierarchy invariant가 깨졌습니다 "
                        f"(invalid={sweeps.get('invariant_failures', 0)}, "
                        f"incomplete={sweeps.get('incomplete_sweeps', 0)})",
                    )
                )
            elif sweeps.get("valid_complete_sweeps", 0) == 0:
                issues.append(
                    AuditIssue(
                        "HIGH",
                        "market_sweep_attestation_missing",
                        "선택 기간에 cursor-complete Gamma sweep attestation이 없습니다",
                    )
                )
            catalog = result.get("market_catalog") or {}
            catalog_coverage = catalog.get("snapshot_condition_coverage_ratio") or 0
            qualified_coverage = (
                catalog.get("qualified_condition_coverage_ratio") or 0
            )
            metadata_coverage = catalog.get("metadata_completeness_ratio") or 0
            if (
                catalog.get("rows", 0) == 0
                or catalog_coverage < 0.99
                or qualified_coverage < 0.99
                or metadata_coverage < 0.99
            ):
                issues.append(
                    AuditIssue(
                        "HIGH",
                        "market_catalog_missing",
                        "event/endDate/outcomes/token/tags/fee 카탈로그가 비었거나 "
                        "snapshot/qualified membership coverage가 부족합니다 "
                        f"(snapshot={catalog_coverage:.1%}, "
                        f"qualified={qualified_coverage:.1%}, "
                        f"metadata={metadata_coverage:.1%})",
                    )
                )

        result["logs"] = _log_summary(log_paths or _default_log_paths(resolved))
        if result["logs"]["files"] == 0:
            issues.append(
                AuditIssue("MEDIUM", "logs_missing", "연결된 실행 로그 파일을 찾지 못했습니다")
            )
    except sqlite3.Error as error:
        issues.append(AuditIssue("CRITICAL", "audit_query_failed", str(error)))
    finally:
        connection.close()
        snapshot_directory.cleanup()

    result["issues"] = [asdict(issue) for issue in issues]
    result["status"] = _overall_status(issues)
    return result


def audit_many(
    databases: Iterable[Path],
    *,
    days: int,
    as_of: datetime,
) -> dict[str, Any]:
    audits = [audit_database(path, days=days, as_of=as_of) for path in databases]
    severities = Counter(
        issue["severity"] for audit in audits for issue in audit.get("issues", [])
    )
    live_severities = Counter(
        issue["severity"]
        for audit in audits
        if audit.get("cohort") == "live"
        for issue in audit.get("issues", [])
    )
    simulation_severities = Counter(
        issue["severity"]
        for audit in audits
        if audit.get("cohort") == "simulation_assumption"
        for issue in audit.get("issues", [])
    )
    cohort_counts = Counter(str(audit.get("cohort") or "unknown") for audit in audits)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {
            "start": (as_of - timedelta(days=days)).isoformat(),
            "end": as_of.isoformat(),
            "days": days,
        },
        "database_count": len(audits),
        "database_cohorts": dict(sorted(cohort_counts.items())),
        "issue_counts": dict(sorted(severities.items())),
        "live_issue_counts": dict(sorted(live_severities.items())),
        "simulation_issue_counts": dict(sorted(simulation_severities.items())),
        "databases": audits,
    }


def render_markdown(bundle: dict[str, Any]) -> str:
    lines = [
        "# Polybot retrospective data audit",
        "",
        f"- 생성: `{bundle['generated_at']}`",
        f"- 기간: `{bundle['period']['start']}` ~ `{bundle['period']['end']}`",
        f"- DB: {bundle['database_count']}개",
        f"- cohort: {bundle.get('database_cohorts') or {'live': bundle['database_count']}}",
        f"- 이슈: {bundle['issue_counts'] or '없음'}",
        "",
    ]
    for audit in bundle["databases"]:
        lines.extend(
            [
                f"## {audit.get('strategy_name') or 'unknown'}",
                "",
                f"- DB: `{audit['database']}`",
                f"- cohort: `{audit.get('cohort', 'live')}`",
                f"- 상태: **{audit.get('status', 'FAIL')}**",
            ]
        )
        trades = audit.get("trades")
        if trades:
            lines.append(
                f"- trades: {trades['total']}건, 기간 매수 {trades['period_buys']}건, "
                f"기간 청산 {trades['period_sells']}건, status {trades['status_counts']}"
            )
        runs = audit.get("runs")
        if runs:
            lines.append(
                f"- runs: success {runs['success']}, failed {runs['failed']}, "
                f"config {runs['config_versions']}개, commit {runs['git_versions']}개"
            )
        snapshots = audit.get("market_snapshots")
        if snapshots:
            lines.append(
                f"- snapshots: {snapshots['period_rows']}행 / {snapshots['period_markets']}시장, "
                f"window {snapshots['requested_window_coverage_ratio']:.1%}, "
                f"5m cadence {snapshots['five_minute_bucket_coverage_ratio']:.1%}"
            )
        lines.append("- 이슈:")
        if audit.get("issues"):
            lines.extend(
                f"  - **{issue['severity']}** `{issue['code']}` — {issue['message']}"
                for issue in audit["issues"]
            )
        else:
            lines.append("  - 없음")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_audit_bundle(bundle: dict[str, Any], output_directory: Path) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "retro-audit.json"
    markdown_path = output_directory / "retro-audit.md"
    _atomic_write(json_path, json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(markdown_path, render_markdown(bundle))
    return json_path, markdown_path


def backup_databases(databases: Iterable[Path], output_directory: Path) -> Path:
    """Use SQLite's online backup API, then write a checksum manifest."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = output_directory.expanduser().resolve() / timestamp
    destination.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    for index, source in enumerate(sorted({path.resolve() for path in databases}), start=1):
        strategy = _path_strategy_name(source) or "unknown"
        target = destination / f"{index:02d}-{strategy}-{source.name}"
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_connection:
            with sqlite3.connect(target) as target_connection:
                source_connection.backup(target_connection)
        with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as check_connection:
            integrity = [str(row[0]) for row in check_connection.execute("PRAGMA quick_check")]
        if integrity != ["ok"]:
            raise RuntimeError(
                f"backup SQLite quick_check 실패 ({source.name}): {'; '.join(integrity)}"
            )
        records.append(
            {
                "source": str(source),
                "backup": target.name,
                "bytes": target.stat().st_size,
                "sha256": _sha256(target),
                "quick_check": integrity,
            }
        )
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "databases": records,
    }
    _atomic_write(
        destination / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    return destination


def parse_as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        parsed_date = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("--as-of는 YYYY-MM-DD 형식이어야 합니다") from error
    return datetime.combine(
        parsed_date + timedelta(days=1), time.min, tzinfo=timezone.utc
    )


def _trade_summary(
    connection: sqlite3.Connection, cutoff: datetime, as_of: datetime
) -> dict[str, Any]:
    columns = _columns(connection, "trades")
    status_counts = {}
    period_status_counts = {}
    if "status" in columns:
        status_counts = {
            str(row[0]): row[1]
            for row in connection.execute(
                "SELECT status, COUNT(*) FROM trades GROUP BY status ORDER BY status"
            )
        }
        if "buy_timestamp" in columns:
            period_status_counts = {
                str(row[0]): row[1]
                for row in connection.execute(
                    "SELECT status, COUNT(*) FROM trades "
                    "WHERE datetime(buy_timestamp) >= datetime(?) "
                    "AND datetime(buy_timestamp) < datetime(?) GROUP BY status ORDER BY status",
                    (_sqlite_time(cutoff), _sqlite_time(as_of)),
                )
            }
    total = connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    period_buys = _period_count(connection, "trades", "buy_timestamp", cutoff, as_of)
    period_sells = _period_count(connection, "trades", "sell_timestamp", cutoff, as_of)
    realized_pnl = None
    period_realized_pnl = None
    if "realized_pnl" in columns:
        realized_pnl = connection.execute(
            "SELECT ROUND(COALESCE(SUM(realized_pnl), 0), 6) FROM trades "
            "WHERE realized_pnl IS NOT NULL"
        ).fetchone()[0]
        if "sell_timestamp" in columns:
            period_realized_pnl = connection.execute(
                "SELECT ROUND(COALESCE(SUM(realized_pnl), 0), 6) FROM trades "
                "WHERE realized_pnl IS NOT NULL "
                "AND datetime(sell_timestamp) >= datetime(?) "
                "AND datetime(sell_timestamp) < datetime(?)",
                (_sqlite_time(cutoff), _sqlite_time(as_of)),
            ).fetchone()[0]
    return {
        "total": total,
        "period_buys": period_buys,
        "period_sells": period_sells,
        "status_counts": status_counts,
        "period_entry_status_counts": period_status_counts,
        "recorded_realized_pnl_all_time": realized_pnl,
        "recorded_realized_pnl_period": period_realized_pnl,
        "pnl_quality": "ORDER_ASSUMPTION",
    }


def _run_summary(
    connection: sqlite3.Connection, cutoff: datetime, as_of: datetime
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT r.status, r.config_hash, r.git_commit, r.started_at,
               c.config_json
        FROM run_audits r
        JOIN strategy_configs c ON c.config_hash = r.config_hash
        WHERE datetime(r.started_at) >= datetime(?)
          AND datetime(r.started_at) < datetime(?)
        ORDER BY r.started_at
        """,
        (_sqlite_time(cutoff), _sqlite_time(as_of)),
    ).fetchall()
    statuses = Counter(str(row["status"]) for row in rows)
    configs: dict[str, Any] = {}
    for row in rows:
        configs.setdefault(row["config_hash"], json.loads(row["config_json"]))
    successful_times = [
        _parse_sqlite_datetime(row["started_at"])
        for row in rows
        if row["status"] == "SUCCESS"
    ]
    successful_times = sorted(value for value in successful_times if value is not None)
    schedule_points = [cutoff, *successful_times, as_of]
    gaps = [
        max(0.0, (later - earlier).total_seconds() / 3600)
        for earlier, later in zip(schedule_points, schedule_points[1:])
    ] if successful_times else []
    stale_cutoff = as_of - timedelta(hours=1)
    config_epochs: list[dict[str, Any]] = []
    for row in rows:
        if not config_epochs or config_epochs[-1]["config_hash"] != row["config_hash"]:
            config_epochs.append(
                {
                    "config_hash": row["config_hash"],
                    "first_run_at": row["started_at"],
                    "last_run_at": row["started_at"],
                }
            )
        else:
            config_epochs[-1]["last_run_at"] = row["started_at"]
    return {
        "total": len(rows),
        "success": statuses.get("SUCCESS", 0),
        "failed": statuses.get("FAILED", 0),
        "running": statuses.get("RUNNING", 0),
        "stale_running": sum(
            1
            for row in rows
            if row["status"] == "RUNNING"
            and (_parse_sqlite_datetime(row["started_at"]) or as_of) < stale_cutoff
        ),
        "unknown_git_runs": sum(1 for row in rows if row["git_commit"] == "unknown"),
        "max_success_gap_hours": round(max(gaps), 3) if gaps else None,
        "config_versions": len(configs),
        "git_versions": len({row["git_commit"] for row in rows}),
        "configs": [
            {"config_hash": config_hash, "resolved_config": payload}
            for config_hash, payload in configs.items()
        ],
        "config_epochs": config_epochs,
    }


def _snapshot_summary(
    connection: sqlite3.Connection,
    cutoff: datetime,
    as_of: datetime,
    *,
    minimum_history_hours: float = 0.0,
) -> dict[str, Any]:
    columns = _columns(connection, "market_snapshots")
    if "timestamp" not in columns:
        return {"period_rows": 0, "period_markets": 0, "coverage_days": 0.0}
    row = connection.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT condition_id), MIN(timestamp), MAX(timestamp)
        FROM market_snapshots
        WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) < datetime(?)
        """,
        (_sqlite_time(cutoff), _sqlite_time(as_of)),
    ).fetchone()
    first_seen, last_seen = row[2], row[3]
    coverage = 0.0
    if first_seen and last_seen:
        first_dt = _parse_sqlite_datetime(first_seen)
        last_dt = _parse_sqlite_datetime(last_seen)
        if first_dt and last_dt:
            coverage = max(0.0, (last_dt - first_dt).total_seconds() / 86_400)
    requested_seconds = max(1.0, (as_of - cutoff).total_seconds())
    covered_start = max(cutoff, first_dt) if first_seen and first_dt else as_of
    covered_end = (
        min(as_of, last_dt + timedelta(minutes=5))
        if last_seen and last_dt
        else cutoff
    )
    requested_ratio = max(
        0.0, min(1.0, (covered_end - covered_start).total_seconds() / requested_seconds)
    )
    buckets = connection.execute(
        """
        SELECT COUNT(DISTINCT CAST(strftime('%s', timestamp) / 300 AS INTEGER))
        FROM market_snapshots
        WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) < datetime(?)
        """,
        (_sqlite_time(cutoff), _sqlite_time(as_of)),
    ).fetchone()[0]
    expected_buckets = max(1, math.ceil(requested_seconds / 300))
    value_columns = (
        "probability", "liquidity", "volume_24h", "best_bid", "best_ask", "spread"
    )
    value_expressions = [
        column if column in columns else f"NULL AS {column}"
        for column in value_columns
    ]
    invalid_value_reasons: Counter[str] = Counter()
    invalid_value_rows = 0
    for value_row in connection.execute(
        f"""
        SELECT {', '.join(value_expressions)} FROM market_snapshots
        WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) < datetime(?)
        """,
        (_sqlite_time(cutoff), _sqlite_time(as_of)),
    ):
        row_errors = _snapshot_value_errors(value_row)
        if row_errors:
            invalid_value_rows += 1
            invalid_value_reasons.update(row_errors)
    per_market_ratios: list[float] = []
    per_market_history_ratios: list[float] = []
    required_history_seconds = min(
        requested_seconds, max(0.0, minimum_history_hours) * 3600
    )
    for market_row in connection.execute(
        """
        SELECT condition_id, MIN(timestamp), MAX(timestamp),
               COUNT(DISTINCT CAST(strftime('%s', timestamp) / 300 AS INTEGER))
        FROM market_snapshots
        WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) < datetime(?)
        GROUP BY condition_id
        """,
        (_sqlite_time(cutoff), _sqlite_time(as_of)),
    ):
        market_first = _parse_sqlite_datetime(market_row[1])
        market_last = _parse_sqlite_datetime(market_row[2])
        if market_first is None or market_last is None:
            continue
        expected = max(
            1,
            int(max(0.0, (market_last - market_first).total_seconds()) // 300) + 1,
        )
        per_market_ratios.append(
            0.0 if market_row[3] < 2 else min(1.0, market_row[3] / expected)
        )
        observed_seconds = max(
            0.0, (market_last - market_first).total_seconds() + 300
        )
        per_market_history_ratios.append(
            1.0
            if required_history_seconds == 0
            else min(1.0, observed_seconds / required_history_seconds)
        )
    per_market_ratios.sort()
    per_market_history_ratios.sort()
    p10_index = int((len(per_market_ratios) - 1) * 0.1) if per_market_ratios else 0
    median_index = (len(per_market_ratios) - 1) // 2 if per_market_ratios else 0
    history_p10_index = (
        int((len(per_market_history_ratios) - 1) * 0.1)
        if per_market_history_ratios
        else 0
    )
    return {
        "period_rows": row[0],
        "period_markets": row[1],
        "first_seen": first_seen,
        "last_seen": last_seen,
        "coverage_days": round(coverage, 3),
        "five_minute_buckets": buckets,
        "invalid_value_rows": invalid_value_rows,
        "invalid_value_reasons": dict(sorted(invalid_value_reasons.items())),
        "requested_window_coverage_ratio": round(requested_ratio, 6),
        "five_minute_bucket_coverage_ratio": round(
            min(1.0, buckets / expected_buckets), 6
        ),
        "per_market_cadence_min": (
            round(per_market_ratios[0], 6) if per_market_ratios else 0.0
        ),
        "per_market_cadence_p10": (
            round(per_market_ratios[p10_index], 6) if per_market_ratios else 0.0
        ),
        "per_market_cadence_median": (
            round(per_market_ratios[median_index], 6) if per_market_ratios else 0.0
        ),
        "minimum_history_hours": minimum_history_hours,
        "per_market_history_depth_min": (
            round(per_market_history_ratios[0], 6)
            if per_market_history_ratios
            else 0.0
        ),
        "per_market_history_depth_p10": (
            round(per_market_history_ratios[history_p10_index], 6)
            if per_market_history_ratios
            else 0.0
        ),
    }


def _snapshot_value_errors(row: Sequence[Any]) -> list[str]:
    probability, liquidity, volume_24h, best_bid, best_ask, spread = row
    errors: list[str] = []
    if not _is_probability(probability):
        errors.append("probability")
    for name, value in (("liquidity", liquidity), ("volume_24h", volume_24h)):
        if value is not None and not _is_finite_nonnegative(value):
            errors.append(name)
    for name, value in (("best_bid", best_bid), ("best_ask", best_ask)):
        if value is not None and not _is_probability(value):
            errors.append(name)
    if spread is not None and (
        not _is_finite_nonnegative(spread) or float(spread) > 1
    ):
        errors.append("spread")
    if (
        best_bid is not None
        and best_ask is not None
        and _is_probability(best_bid)
        and _is_probability(best_ask)
        and float(best_bid) > float(best_ask) + 0.000001
    ):
        errors.append("bid_ask_order")
    if (
        best_bid is not None
        and best_ask is not None
        and spread is not None
        and _is_probability(best_bid)
        and _is_probability(best_ask)
        and _is_finite_nonnegative(spread)
        and abs(float(spread) - (float(best_ask) - float(best_bid))) > 0.000001
    ):
        errors.append("spread_consistency")
    return errors


def _sweep_summary(
    connection: sqlite3.Connection,
    tables: set[str],
    cutoff: datetime,
    as_of: datetime,
) -> dict[str, Any] | None:
    required_tables = {"market_sweeps", "market_sweep_memberships"}
    if not (required_tables & tables):
        return None
    required_sweep_columns = {
        "sweep_id", "schema_version", "run_id", "started_at", "completed_at",
        "cursor_complete", "pages", "raw_market_count", "unique_condition_count",
        "qualified_market_count", "missing_condition_id_count", "duplicate_raw_count",
        "excluded_condition_count", "exclusion_counts_json",
        "min_liquidity", "min_volume", "membership_digest_sha256",
        "snapshotted_market_count",
    }
    required_membership_columns = {
        "sweep_id", "condition_id", "raw_seen_count", "qualified",
        "qualification_reason", "snapshot_eligible", "snapshotted",
        "snapshot_reason",
    }
    sweep_columns = (
        _columns(connection, "market_sweeps") if "market_sweeps" in tables else set()
    )
    membership_columns = (
        _columns(connection, "market_sweep_memberships")
        if "market_sweep_memberships" in tables
        else set()
    )
    missing_columns = {
        "market_sweeps": sorted(required_sweep_columns - sweep_columns),
        "market_sweep_memberships": sorted(
            required_membership_columns - membership_columns
        ),
    }
    if any(missing_columns.values()):
        return {
            "contract_complete": False,
            "missing_columns": missing_columns,
            "period_sweeps": 0,
            "valid_complete_sweeps": 0,
            "invariant_failures": 0,
            "complete_sweep_bucket_coverage_ratio": 0.0,
            "snapshot_eligible_coverage_ratio": 0.0,
            "qualified_snapshot_eligibility_ratio": 0.0,
            "per_market_attested_snapshot_p10": 0.0,
        }

    sweep_rows = connection.execute(
        """
        SELECT * FROM market_sweeps
        WHERE datetime(COALESCE(completed_at, started_at)) >= datetime(?)
          AND datetime(COALESCE(completed_at, started_at)) < datetime(?)
        ORDER BY started_at, sweep_id
        """,
        (_sqlite_time(cutoff), _sqlite_time(as_of)),
    ).fetchall()
    complete_rows = [row for row in sweep_rows if int(row["cursor_complete"] or 0) == 1]
    invariant_failures: list[dict[str, Any]] = []
    valid_sweep_ids: list[str] = []
    for sweep in complete_rows:
        sweep_id = str(sweep["sweep_id"])
        memberships = connection.execute(
            """
            SELECT condition_id, raw_seen_count, qualified, qualification_reason,
                   snapshot_eligible, snapshotted, snapshot_reason
            FROM market_sweep_memberships
            WHERE sweep_id = ?
            ORDER BY condition_id
            """,
            (sweep_id,),
        ).fetchall()
        digest_payload = [
            {
                "condition_id": str(row["condition_id"]),
                "raw_seen_count": int(row["raw_seen_count"] or 0),
                "qualified": bool(row["qualified"]),
                "qualification_reason": str(row["qualification_reason"] or ""),
            }
            for row in memberships
        ]
        digest = hashlib.sha256(
            json.dumps(
                digest_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        membership_count = len(memberships)
        qualified_count = sum(int(bool(row["qualified"])) for row in memberships)
        snapshotted_count = sum(int(bool(row["snapshotted"])) for row in memberships)
        hierarchy_invalid = sum(
            1
            for row in memberships
            if bool(row["snapshotted"]) and not bool(row["snapshot_eligible"])
            or bool(row["snapshot_eligible"]) and not bool(row["qualified"])
        )
        try:
            exclusion_counts = json.loads(sweep["exclusion_counts_json"] or "{}")
            exclusion_count_sum = (
                sum(int(value) for value in exclusion_counts.values())
                if isinstance(exclusion_counts, dict)
                else -1
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            exclusion_count_sum = -1
        run_id = str(sweep["run_id"] or "")
        run_exists = bool(run_id) and "run_audits" in tables and bool(
            connection.execute(
                "SELECT 1 FROM run_audits WHERE run_id = ? LIMIT 1", (run_id,)
            ).fetchone()
        )
        attested_snapshot_conditions = {
            str(row["condition_id"]) for row in memberships if bool(row["snapshotted"])
        }
        actual_snapshot_conditions: set[str] | None = None
        if (
            "market_snapshots" in tables
            and {"condition_id", "run_id"}.issubset(
                _columns(connection, "market_snapshots")
            )
        ):
            actual_snapshot_conditions = {
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT condition_id FROM market_snapshots WHERE run_id = ?",
                    (run_id,),
                )
            }
        counts_nonnegative = all(
            int(sweep[column] or 0) >= 0
            for column in (
                "pages", "raw_market_count", "unique_condition_count",
                "qualified_market_count", "missing_condition_id_count",
                "duplicate_raw_count", "excluded_condition_count",
                "snapshotted_market_count",
            )
        )
        started_at = _parse_sqlite_datetime(sweep["started_at"])
        completed_at = _parse_sqlite_datetime(sweep["completed_at"])
        finite_nonnegative_filters = all(
            _is_finite_nonnegative(sweep[column])
            for column in ("min_liquidity", "min_volume")
        )
        checks = {
            "membership_count": membership_count
            == int(sweep["qualified_market_count"] or 0),
            "qualified_market_count": qualified_count
            == int(sweep["qualified_market_count"] or 0),
            "raw_market_identity": int(sweep["raw_market_count"] or 0)
            == int(sweep["unique_condition_count"] or 0)
            + int(sweep["duplicate_raw_count"] or 0)
            + int(sweep["missing_condition_id_count"] or 0),
            "unique_condition_identity": int(sweep["unique_condition_count"] or 0)
            == int(sweep["qualified_market_count"] or 0)
            + int(sweep["excluded_condition_count"] or 0),
            "exclusion_count_identity": exclusion_count_sum
            == int(sweep["excluded_condition_count"] or 0),
            "snapshotted_market_count": snapshotted_count
            == int(sweep["snapshotted_market_count"] or 0),
            "membership_digest_sha256": digest
            == str(sweep["membership_digest_sha256"] or ""),
            "membership_hierarchy": hierarchy_invalid == 0,
            "qualified_membership_values": all(
                bool(row["qualified"]) and int(row["raw_seen_count"] or 0) >= 1
                for row in memberships
            ),
            "completed_at": sweep["completed_at"] is not None,
            "timestamp_order": started_at is not None
            and completed_at is not None
            and completed_at >= started_at,
            "finite_nonnegative_filters": finite_nonnegative_filters,
            "run_provenance": run_exists,
            "schema_version": int(sweep["schema_version"] or 0) == 1,
            "pages_positive": int(sweep["pages"] or 0) >= 1,
            "counts_nonnegative": counts_nonnegative,
            "snapshot_rows_match_membership": actual_snapshot_conditions
            == attested_snapshot_conditions,
        }
        failed = sorted(name for name, passed in checks.items() if not passed)
        if failed:
            invariant_failures.append({"sweep_id": sweep_id, "failed": failed})
        else:
            valid_sweep_ids.append(sweep_id)

    requested_seconds = max(1.0, (as_of - cutoff).total_seconds())
    expected_buckets = max(1, math.ceil(requested_seconds / 300))
    valid_bucket_count = 0
    eligible_memberships = 0
    snapshotted_memberships = 0
    qualified_memberships = 0
    per_market_ratios: list[float] = []
    if valid_sweep_ids:
        placeholders = ",".join("?" for _ in valid_sweep_ids)
        valid_bucket_count = connection.execute(
            f"""
            SELECT COUNT(DISTINCT CAST(strftime('%s', completed_at) / 300 AS INTEGER))
            FROM market_sweeps WHERE sweep_id IN ({placeholders})
            """,
            valid_sweep_ids,
        ).fetchone()[0]
        qualified_memberships, eligible_memberships, snapshotted_memberships = (
            connection.execute(
                f"""
                SELECT COALESCE(SUM(qualified), 0),
                       COALESCE(SUM(snapshot_eligible), 0),
                       COALESCE(SUM(snapshotted), 0)
                FROM market_sweep_memberships
                WHERE sweep_id IN ({placeholders})
                """,
                valid_sweep_ids,
            ).fetchone()
        )
        per_market_ratios = [
            min(1.0, int(row[2] or 0) / max(1, int(row[1] or 0)))
            for row in connection.execute(
                f"""
                SELECT condition_id, SUM(snapshot_eligible), SUM(snapshotted)
                FROM market_sweep_memberships
                WHERE sweep_id IN ({placeholders})
                GROUP BY condition_id
                HAVING SUM(snapshot_eligible) > 0
                """,
                valid_sweep_ids,
            )
        ]
    per_market_ratios.sort()
    p10_index = int((len(per_market_ratios) - 1) * 0.1) if per_market_ratios else 0
    return {
        "contract_complete": True,
        "missing_columns": missing_columns,
        "period_sweeps": len(sweep_rows),
        "complete_sweeps": len(complete_rows),
        "incomplete_sweeps": len(sweep_rows) - len(complete_rows),
        "valid_complete_sweeps": len(valid_sweep_ids),
        "invariant_failures": len(invariant_failures),
        "invariant_failure_details": invariant_failures,
        "complete_sweep_buckets": valid_bucket_count,
        "complete_sweep_bucket_coverage_ratio": round(
            min(1.0, valid_bucket_count / expected_buckets), 6
        ),
        "qualified_memberships": int(qualified_memberships or 0),
        "snapshot_eligible_memberships": int(eligible_memberships or 0),
        "snapshotted_memberships": int(snapshotted_memberships or 0),
        "snapshot_eligible_coverage_ratio": round(
            1.0
            if not eligible_memberships
            else snapshotted_memberships / eligible_memberships,
            6,
        ),
        "qualified_snapshot_eligibility_ratio": round(
            0.0
            if not qualified_memberships
            else eligible_memberships / qualified_memberships,
            6,
        ),
        "per_market_attested_snapshot_p10": (
            round(per_market_ratios[p10_index], 6) if per_market_ratios else 0.0
        ),
    }


def _catalog_summary(
    connection: sqlite3.Connection,
    tables: set[str],
    cutoff: datetime,
    as_of: datetime,
) -> dict[str, Any] | None:
    if "market_catalog" not in tables:
        return None
    catalog_columns = _columns(connection, "market_catalog")
    if "condition_id" not in catalog_columns:
        return {
            "rows": 0,
            "snapshot_condition_coverage_ratio": 0.0,
            "qualified_condition_coverage_ratio": 0.0,
            "metadata_completeness_ratio": 0.0,
        }
    catalog_rows = connection.execute("SELECT * FROM market_catalog").fetchall()
    catalog_by_condition = {str(row["condition_id"]): row for row in catalog_rows}
    snapshot_condition_ids: set[str] = set()
    if (
        "market_snapshots" in tables
        and {"condition_id", "timestamp"}.issubset(_columns(connection, "market_snapshots"))
    ):
        snapshot_condition_ids = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT DISTINCT condition_id FROM market_snapshots
                WHERE datetime(timestamp) >= datetime(?)
                  AND datetime(timestamp) < datetime(?)
                """,
                (_sqlite_time(cutoff), _sqlite_time(as_of)),
            )
        }
    qualified_condition_ids: set[str] = set()
    if {"market_sweeps", "market_sweep_memberships"}.issubset(tables):
        sweep_columns = _columns(connection, "market_sweeps")
        membership_columns = _columns(connection, "market_sweep_memberships")
        if {"sweep_id", "completed_at", "cursor_complete"}.issubset(
            sweep_columns
        ) and {"sweep_id", "condition_id", "qualified"}.issubset(
            membership_columns
        ):
            qualified_condition_ids = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT m.condition_id
                    FROM market_sweep_memberships m
                    JOIN market_sweeps s ON s.sweep_id = m.sweep_id
                    WHERE s.cursor_complete = 1 AND m.qualified = 1
                      AND datetime(s.completed_at) >= datetime(?)
                      AND datetime(s.completed_at) < datetime(?)
                    """,
                    (_sqlite_time(cutoff), _sqlite_time(as_of)),
                )
            }
    required_condition_ids = snapshot_condition_ids | qualified_condition_ids
    metadata_complete_ids = {
        condition_id
        for condition_id, row in catalog_by_condition.items()
        if _catalog_row_complete(row, catalog_columns)
    }
    covered_snapshot = snapshot_condition_ids & catalog_by_condition.keys()
    covered_qualified = qualified_condition_ids & catalog_by_condition.keys()
    covered_required = required_condition_ids & catalog_by_condition.keys()
    complete_required = required_condition_ids & metadata_complete_ids

    def ratio(numerator: int, denominator: int) -> float:
        return 1.0 if denominator == 0 else numerator / denominator

    expected_metadata_columns = {
        "event_id", "event_slug", "end_date", "outcomes_json", "token_ids_json",
        "tags_json", "fees_enabled", "fee_rate",
    }
    return {
        "rows": len(catalog_rows),
        "metadata_contract_supported": expected_metadata_columns.issubset(
            catalog_columns
        ),
        "missing_metadata_columns": sorted(expected_metadata_columns - catalog_columns),
        "metadata_complete_rows": len(metadata_complete_ids),
        "snapshot_conditions": len(snapshot_condition_ids),
        "covered_snapshot_conditions": len(covered_snapshot),
        "snapshot_condition_coverage_ratio": round(
            ratio(len(covered_snapshot), len(snapshot_condition_ids)), 6
        ),
        "qualified_conditions": len(qualified_condition_ids),
        "covered_qualified_conditions": len(covered_qualified),
        "qualified_condition_coverage_ratio": round(
            ratio(len(covered_qualified), len(qualified_condition_ids)), 6
        ),
        "required_conditions": len(required_condition_ids),
        "covered_required_conditions": len(covered_required),
        "required_condition_coverage_ratio": round(
            ratio(len(covered_required), len(required_condition_ids)), 6
        ),
        "metadata_complete_required_conditions": len(complete_required),
        "metadata_completeness_ratio": round(
            ratio(len(complete_required), len(required_condition_ids)), 6
        ),
    }


def _catalog_row_complete(row: sqlite3.Row, columns: set[str]) -> bool:
    expected = {
        "event_id", "event_slug", "end_date", "outcomes_json", "token_ids_json",
        "tags_json", "fees_enabled", "fee_rate",
    }
    if not expected.issubset(columns):
        return False
    if not (str(row["event_id"] or "").strip() or str(row["event_slug"] or "").strip()):
        return False
    if not str(row["end_date"] or "").strip():
        return False
    try:
        outcomes = json.loads(row["outcomes_json"] or "[]")
        token_ids = json.loads(row["token_ids_json"] or "[]")
        tags = json.loads(row["tags_json"] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if (
        not isinstance(outcomes, list)
        or len(outcomes) < 2
        or not isinstance(token_ids, list)
        or len(token_ids) != len(outcomes)
        or any(not str(value or "").strip() for value in token_ids)
        or not isinstance(tags, list)
        or not tags
    ):
        return False
    fees_enabled = row["fees_enabled"]
    if fees_enabled not in (0, 1, False, True):
        return False
    return not bool(fees_enabled) or row["fee_rate"] is not None


def _log_summary(paths: Sequence[Path]) -> dict[str, Any]:
    files: set[Path] = set()
    for path in paths:
        if path.is_file() and path.suffix == ".log":
            files.add(path.resolve())
        elif path.is_dir():
            files.update(candidate.resolve() for candidate in path.rglob("*.log"))
    if not files:
        return {"files": 0, "bytes": 0, "first_modified": None, "last_modified": None}
    mtimes = [candidate.stat().st_mtime for candidate in files]
    return {
        "files": len(files),
        "bytes": sum(candidate.stat().st_size for candidate in files),
        "first_modified": datetime.fromtimestamp(min(mtimes), timezone.utc).isoformat(),
        "last_modified": datetime.fromtimestamp(max(mtimes), timezone.utc).isoformat(),
    }


def _strategy_name(
    connection: sqlite3.Connection, path: Path, tables: set[str]
) -> str | None:
    if "run_audits" in tables:
        row = connection.execute(
            "SELECT strategy_name FROM run_audits ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    if "trades" in tables and "strategy_name" in _columns(connection, "trades"):
        row = connection.execute(
            "SELECT strategy_name, COUNT(*) AS n FROM trades "
            "WHERE strategy_name IS NOT NULL GROUP BY strategy_name ORDER BY n DESC LIMIT 1"
        ).fetchone()
        if row and row[0]:
            value = str(row[0])
            return value if value.startswith("golden-") else f"golden-{value}"
    return _path_strategy_name(path)


def _cohort_mode_summary(
    connection: sqlite3.Connection, tables: set[str]
) -> dict[str, Any]:
    run_modes: dict[str, int] = {}
    if "run_audits" in tables and "mode" in _columns(connection, "run_audits"):
        run_modes = {
            str(row[0]).strip().lower(): int(row[1])
            for row in connection.execute(
                "SELECT mode, COUNT(*) FROM run_audits "
                "WHERE mode IS NOT NULL GROUP BY mode"
            )
        }
    submission_modes: dict[str, int] = {}
    invalid_submission_modes = 0
    if "order_submissions" in tables and "simulation" in _columns(
        connection, "order_submissions"
    ):
        mode_rows = list(
            connection.execute(
                "SELECT simulation, COUNT(*) FROM order_submissions GROUP BY simulation"
            )
        )
        submission_modes = {
            "simulation" if int(row[0]) else "live": int(row[1])
            for row in mode_rows
            if row[0] in (0, 1)
        }
        invalid_submission_modes = sum(
            int(row[1]) for row in mode_rows if row[0] not in (0, 1)
        )
    unknown_run_modes = {
        mode: count
        for mode, count in run_modes.items()
        if mode not in {"live", "sim", "simulation"}
    }
    invalid_mode_evidence = {
        "unknown_run_modes": unknown_run_modes,
        "invalid_submission_modes": invalid_submission_modes,
    }
    return {
        "run_modes": dict(sorted(run_modes.items())),
        "submission_modes": dict(sorted(submission_modes.items())),
        "invalid_mode_evidence": invalid_mode_evidence,
        "has_invalid_mode_evidence": bool(
            unknown_run_modes or invalid_submission_modes
        ),
        "has_live_evidence": bool(
            run_modes.get("live", 0)
            or submission_modes.get("live", 0)
            or unknown_run_modes
            or invalid_submission_modes
        ),
        "has_simulation_evidence": bool(
            run_modes.get("sim", 0)
            or run_modes.get("simulation", 0)
            or submission_modes.get("simulation", 0)
        ),
    }


def _path_strategy_name(path: Path) -> str | None:
    for part in reversed(path.parts):
        if part.startswith("golden-"):
            return part
    aliases = {"polybot-eco": "golden-honeydew", "polybot-fox": "golden-nectarine"}
    for part in reversed(path.parts):
        if part in aliases:
            return aliases[part]
    return None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _fill_ledger_summary(
    connection: sqlite3.Connection,
    tables: set[str],
    cutoff: datetime,
    as_of: datetime,
) -> dict[str, Any] | None:
    required_tables = {"order_submissions", "order_status_events", "order_fills"}
    if not (required_tables & tables) and "fills" not in tables:
        return None
    contracts = {
        "order_submissions": {
            "submission_id",
            "run_id",
            "order_id",
            "requested_price",
            "requested_size",
            "submitted_at",
            "simulation",
            "success",
            "response_status",
            "needs_reconciliation",
            "latest_size_matched",
            "outcome_resolution",
            "outcome_resolved_at",
            "outcome_resolution_reason",
        },
        "order_status_events": {
            "submission_id",
            "observed_at",
            "status",
            "size_matched",
            "domain_error",
        },
        "order_fills": {
            "submission_id",
            "order_id",
            "trade_id",
            "bucket_index",
            "status",
            "size",
            "price",
            "liquidity_role",
            "fee_rate_bps",
            "fee_amount_usdc",
            "domain_error",
        },
    }
    columns = {
        table: _columns(connection, table) if table in tables else set()
        for table in required_tables
    }
    complete = all(
        table in tables and expected.issubset(columns[table])
        for table, expected in contracts.items()
    )
    if not complete:
        return {
            "table": "order_fills",
            "rows": 0,
            "columns": {key: sorted(value) for key, value in columns.items()},
            "contract_complete": False,
            "period_submissions": 0,
            "period_fill_rows": 0,
            "confirmed_fills": 0,
            "fee_known_ratio": 0.0,
            "fee_rate_known_ratio": 0.0,
            "liquidity_role_known_ratio": 0.0,
            "stale_reconciliations": 0,
            "uncertain_submission_outcomes": 0,
            "legacy_unavailable_evidence_gaps": 0,
            "operator_catalog_evidence_gaps": 0,
            "overfilled_orders": 0,
            "invalid_confirmed_fill_domains": 0,
            "invalid_submission_domains": 0,
            "invalid_order_status_domains": 0,
            "closed_trade_quantity_mismatches": 0,
            "legacy_share_mismatches": 0,
            "completed_trades": 0,
            "completed_with_confirmed_fills": 0,
            "completed_trade_fill_coverage": 0.0,
            "confirmed_fill_gross_pnl_usdc": None,
            "confirmed_fill_reported_fees_usdc": None,
            "confirmed_fill_net_pnl_usdc": None,
        }

    period_submissions = connection.execute(
        """
        SELECT COUNT(*) FROM order_submissions
        WHERE simulation = 0 AND success = 1
          AND datetime(submitted_at) >= datetime(?)
          AND datetime(submitted_at) < datetime(?)
        """,
        (_sqlite_time(cutoff), _sqlite_time(as_of)),
    ).fetchone()[0]
    invalid_submission_domains = sum(
        1
        for row in connection.execute(
            """
            SELECT requested_price, requested_size FROM order_submissions
            WHERE simulation = 0
              AND datetime(submitted_at) >= datetime(?)
              AND datetime(submitted_at) < datetime(?)
            """,
            (_sqlite_time(cutoff), _sqlite_time(as_of)),
        )
        if not _is_probability(row[0], strict=True)
        or not _is_finite_positive(row[1])
    )
    invalid_order_status_domains = sum(
        1
        for row in connection.execute(
            """
            SELECT e.original_size, e.size_matched, e.price, e.domain_error
            FROM order_status_events e
            JOIN order_submissions s ON s.submission_id = e.submission_id
            WHERE s.simulation = 0
              AND datetime(s.submitted_at) >= datetime(?)
              AND datetime(s.submitted_at) < datetime(?)
            """,
            (_sqlite_time(cutoff), _sqlite_time(as_of)),
        )
        if _order_status_domain_invalid(row)
    )
    rows = connection.execute("SELECT COUNT(*) FROM order_fills").fetchone()[0]
    (
        period_fill_rows,
        confirmed,
        fee_rate_known,
        fee_amount_known,
        liquidity_role_known,
    ) = connection.execute(
        """
        SELECT COUNT(*),
               SUM(CASE WHEN f.status = 'CONFIRMED' THEN 1 ELSE 0 END),
               SUM(CASE WHEN f.status = 'CONFIRMED'
                              AND f.fee_rate_bps IS NOT NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN f.status = 'CONFIRMED'
                              AND (f.fee_amount_usdc IS NOT NULL
                                   OR f.fee_rate_bps = 0) THEN 1 ELSE 0 END),
               SUM(CASE WHEN f.status = 'CONFIRMED'
                              AND f.liquidity_role IN ('MAKER', 'TAKER')
                        THEN 1 ELSE 0 END)
        FROM order_fills f
        JOIN order_submissions s ON s.submission_id = f.submission_id
        WHERE s.simulation = 0
          AND s.success = 1
          AND datetime(s.submitted_at) >= datetime(?)
          AND datetime(s.submitted_at) < datetime(?)
        """,
        (_sqlite_time(cutoff), _sqlite_time(as_of)),
    ).fetchone()
    confirmed = confirmed or 0
    fee_rate_known = fee_rate_known or 0
    fee_amount_known = fee_amount_known or 0
    liquidity_role_known = liquidity_role_known or 0
    stale_reconciliations = connection.execute(
        """
        SELECT COUNT(*) FROM order_submissions
        WHERE needs_reconciliation = 1 AND simulation = 0
          AND datetime(submitted_at) >= datetime(?)
          AND datetime(submitted_at) < datetime(?)
          AND datetime(submitted_at) < datetime(?, '-1 hour')
        """,
        (_sqlite_time(cutoff), _sqlite_time(as_of), _sqlite_time(as_of)),
    ).fetchone()[0]
    uncertain_submission_outcomes = connection.execute(
        """
        SELECT COUNT(*) FROM order_submissions
        WHERE simulation = 0
          AND datetime(submitted_at) >= datetime(?)
          AND datetime(submitted_at) < datetime(?)
          AND (
            response_status IN ('INTENT', 'SUBMIT_OUTCOME_UNKNOWN')
            OR (response_status = 'EVIDENCE_WRITE_FAILED' AND order_id IS NULL)
          )
          AND COALESCE((
            (outcome_resolution = 'NO_ORDER_CREATED' AND order_id IS NULL
             AND outcome_resolved_at IS NOT NULL
             AND NULLIF(TRIM(outcome_resolution_reason), '') IS NOT NULL)
            OR
            (outcome_resolution = 'ORDER_ID_LINKED' AND order_id IS NOT NULL
             AND outcome_resolved_at IS NOT NULL
             AND NULLIF(TRIM(outcome_resolution_reason), '') IS NOT NULL)
          ), 0) = 0
        """,
        (_sqlite_time(cutoff), _sqlite_time(as_of)),
    ).fetchone()[0]
    legacy_unavailable_evidence_gaps = connection.execute(
        """
        SELECT COUNT(*) FROM order_submissions
        WHERE simulation = 0
          AND response_status = 'LEGACY_UNAVAILABLE'
          AND datetime(submitted_at) >= datetime(?)
          AND datetime(submitted_at) < datetime(?)
        """,
        (_sqlite_time(cutoff), _sqlite_time(as_of)),
    ).fetchone()[0]
    operator_catalog_evidence_gaps = connection.execute(
        """
        SELECT COUNT(*) FROM order_submissions
        WHERE simulation = 0
          AND response_status = 'OPERATOR_EVIDENCE_GAP'
          AND datetime(submitted_at) >= datetime(?)
          AND datetime(submitted_at) < datetime(?)
        """,
        (_sqlite_time(cutoff), _sqlite_time(as_of)),
    ).fetchone()[0]
    overfilled_orders = connection.execute(
        """
        WITH confirmed AS (
            SELECT s.submission_id, s.latest_size_matched,
                   SUM(COALESCE(f.size, 0)) AS confirmed_size
            FROM order_submissions s
            JOIN order_fills f ON f.submission_id = s.submission_id
            WHERE s.simulation = 0 AND s.success = 1
              AND f.status = 'CONFIRMED'
              AND datetime(s.submitted_at) >= datetime(?)
              AND datetime(s.submitted_at) < datetime(?)
            GROUP BY s.submission_id, s.latest_size_matched
        )
        SELECT COUNT(*) FROM confirmed
        WHERE latest_size_matched IS NOT NULL
          AND confirmed_size > latest_size_matched + 0.000001
        """,
        (_sqlite_time(cutoff), _sqlite_time(as_of)),
    ).fetchone()[0]
    invalid_confirmed_fill_domains = sum(
        1
        for row in connection.execute(
            """
            SELECT f.size, f.price, f.bucket_index, f.domain_error,
                   f.fee_rate_bps, f.fee_amount_usdc
            FROM order_fills f
            JOIN order_submissions s ON s.submission_id = f.submission_id
            WHERE s.simulation = 0 AND s.success = 1
              AND f.status = 'CONFIRMED'
              AND datetime(s.submitted_at) >= datetime(?)
              AND datetime(s.submitted_at) < datetime(?)
            """,
            (_sqlite_time(cutoff), _sqlite_time(as_of)),
        )
        if not _is_finite_positive(row[0])
        or not _is_probability(row[1], strict=True)
        or type(row[2]) is not int
        or row[2] < 0
        or row[3] is not None
        or (row[4] is not None and not _is_finite_nonnegative(row[4]))
        or (row[5] is not None and not _is_finite_nonnegative(row[5]))
    )

    trade_columns = _columns(connection, "trades") if "trades" in tables else set()
    completed_trades = 0
    completed_with_fills = 0
    needed_trade_columns = {
        "status",
        "buy_order_id",
        "sell_order_id",
        "sell_timestamp",
    }
    gross_pnl = None
    reported_fees = None
    net_pnl = None
    closed_trade_quantity_mismatches = 0
    legacy_share_mismatches = 0
    if needed_trade_columns.issubset(trade_columns):
        legacy_buy_expression = (
            "t.buy_shares" if "buy_shares" in trade_columns else "NULL"
        )
        legacy_sell_expression = (
            "t.sell_shares" if "sell_shares" in trade_columns else "NULL"
        )
        (
            completed_trades,
            completed_with_fills,
            gross_pnl,
            reported_fees,
            fee_complete_trades,
            closed_trade_quantity_mismatches,
            legacy_share_mismatches,
        ) = connection.execute(
            f"""
            WITH confirmed_by_submission AS (
                SELECT s.submission_id, s.order_id, s.latest_size_matched,
                       s.needs_reconciliation, s.reconciliation_error,
                       SUM(COALESCE(f.size, 0)) AS shares,
                       SUM(CASE WHEN f.price IS NOT NULL
                                THEN COALESCE(f.size, 0) ELSE 0 END) AS priced_shares,
                       SUM(CASE WHEN f.size IS NOT NULL AND f.price IS NOT NULL
                                THEN f.size * f.price ELSE 0 END) AS notional,
                       SUM(COALESCE(f.fee_amount_usdc, 0)) AS reported_fees,
                       COUNT(*) AS fill_count,
                       SUM(CASE WHEN f.fee_amount_usdc IS NOT NULL
                                     OR f.fee_rate_bps = 0
                                THEN 1 ELSE 0 END) AS fee_known_count
                       ,SUM(CASE WHEN f.domain_error IS NOT NULL
                                      OR f.size IS NULL OR f.size <= 0
                                      OR f.price IS NULL OR f.price <= 0 OR f.price >= 1
                                      OR typeof(f.bucket_index) != 'integer'
                                      OR f.bucket_index < 0
                                      OR (f.fee_rate_bps IS NOT NULL
                                          AND (f.fee_rate_bps < 0
                                               OR f.fee_rate_bps > 1.0e308))
                                      OR (f.fee_amount_usdc IS NOT NULL
                                          AND (f.fee_amount_usdc < 0
                                               OR f.fee_amount_usdc > 1.0e308))
                                 THEN 1 ELSE 0 END) AS domain_invalid_count
                FROM order_fills f
                JOIN order_submissions s
                  ON s.submission_id = f.submission_id
                WHERE f.status = 'CONFIRMED'
                  AND s.simulation = 0
                  AND s.success = 1
                GROUP BY s.submission_id, s.order_id, s.latest_size_matched,
                         s.needs_reconciliation, s.reconciliation_error
            ), confirmed AS (
                SELECT * FROM confirmed_by_submission
                WHERE needs_reconciliation = 0
                  AND reconciliation_error IS NULL
                  AND latest_size_matched IS NOT NULL
                  AND ABS(shares - latest_size_matched) <= 0.000001
                  AND ABS(priced_shares - shares) <= 0.000001
                  AND domain_invalid_count = 0
            ), joined AS (
                SELECT t.*,
                       {legacy_buy_expression} AS legacy_buy_shares,
                       {legacy_sell_expression} AS legacy_sell_shares,
                       bf.shares AS buy_fill_shares,
                       bf.priced_shares AS buy_priced_shares,
                       bf.notional AS buy_notional,
                       bf.reported_fees AS buy_fees,
                       bf.fill_count AS buy_fill_count,
                       bf.fee_known_count AS buy_fee_known_count,
                       sf.shares AS sell_fill_shares,
                       sf.priced_shares AS sell_priced_shares,
                       sf.notional AS sell_notional,
                       sf.reported_fees AS sell_fees,
                       sf.fill_count AS sell_fill_count,
                       sf.fee_known_count AS sell_fee_known_count,
                       CASE WHEN
                           bf.order_id IS NOT NULL AND sf.order_id IS NOT NULL
                           AND ABS(bf.shares - sf.shares) <= 0.000001
                       THEN 1 ELSE 0 END AS fill_complete
                FROM trades t
                LEFT JOIN confirmed bf ON bf.order_id = t.buy_order_id
                LEFT JOIN confirmed sf ON sf.order_id = t.sell_order_id
                WHERE t.status = 'COMPLETED'
                  AND datetime(t.sell_timestamp) >= datetime(?)
                  AND datetime(t.sell_timestamp) < datetime(?)
            )
            SELECT COUNT(*),
                   COALESCE(SUM(fill_complete), 0),
                   SUM(CASE WHEN fill_complete = 1
                            THEN sell_notional - buy_notional END),
                   SUM(CASE WHEN fill_complete = 1
                            THEN COALESCE(buy_fees, 0) + COALESCE(sell_fees, 0) END),
                   COALESCE(SUM(CASE WHEN fill_complete = 1
                                      AND buy_fee_known_count = buy_fill_count
                                      AND sell_fee_known_count = sell_fill_count
                                     THEN 1 ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN buy_fill_shares IS NOT NULL
                                          AND sell_fill_shares IS NOT NULL
                                          AND ABS(buy_fill_shares - sell_fill_shares)
                                              > 0.000001
                                     THEN 1 ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN fill_complete = 1 AND (
                                          (legacy_buy_shares IS NOT NULL
                                           AND ABS(buy_fill_shares - legacy_buy_shares)
                                               > 0.000001)
                                          OR (legacy_sell_shares IS NOT NULL
                                              AND ABS(sell_fill_shares - legacy_sell_shares)
                                                  > 0.000001)
                                     ) THEN 1 ELSE 0 END), 0)
            FROM joined
            """,
            (_sqlite_time(cutoff), _sqlite_time(as_of)),
        ).fetchone()
        completed_with_fills = completed_with_fills or 0
        if completed_with_fills and fee_complete_trades == completed_with_fills:
            net_pnl = (gross_pnl or 0.0) - (reported_fees or 0.0)
    coverage = 1.0 if completed_trades == 0 else completed_with_fills / completed_trades
    return {
        "table": "order_fills",
        "rows": rows,
        "columns": {key: sorted(value) for key, value in columns.items()},
        "contract_complete": True,
        "period_submissions": period_submissions,
        "period_fill_rows": period_fill_rows,
        "confirmed_fills": confirmed,
        "fee_known_ratio": round(
            1.0 if confirmed == 0 else fee_amount_known / confirmed, 6
        ),
        "fee_rate_known_ratio": round(
            1.0 if confirmed == 0 else fee_rate_known / confirmed, 6
        ),
        "liquidity_role_known_ratio": round(
            1.0 if confirmed == 0 else liquidity_role_known / confirmed, 6
        ),
        "stale_reconciliations": stale_reconciliations,
        "uncertain_submission_outcomes": uncertain_submission_outcomes,
        "legacy_unavailable_evidence_gaps": legacy_unavailable_evidence_gaps,
        "operator_catalog_evidence_gaps": operator_catalog_evidence_gaps,
        "overfilled_orders": overfilled_orders,
        "invalid_confirmed_fill_domains": invalid_confirmed_fill_domains,
        "invalid_submission_domains": invalid_submission_domains,
        "invalid_order_status_domains": invalid_order_status_domains,
        "closed_trade_quantity_mismatches": closed_trade_quantity_mismatches or 0,
        "legacy_share_mismatches": legacy_share_mismatches or 0,
        "completed_trades": completed_trades,
        "completed_with_confirmed_fills": completed_with_fills,
        "completed_trade_fill_coverage": round(coverage, 6),
        "confirmed_fill_gross_pnl_usdc": (
            round(gross_pnl, 6) if gross_pnl is not None else None
        ),
        "confirmed_fill_reported_fees_usdc": (
            round(reported_fees, 6) if reported_fees is not None else None
        ),
        "confirmed_fill_net_pnl_usdc": (
            round(net_pnl, 6) if net_pnl is not None else None
        ),
    }


def _period_count(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    cutoff: datetime,
    as_of: datetime,
) -> int | None:
    if column not in _columns(connection, table):
        return None
    return connection.execute(
        f"SELECT COUNT(*) FROM {table} WHERE datetime({column}) >= datetime(?) "
        f"AND datetime({column}) < datetime(?)",
        (_sqlite_time(cutoff), _sqlite_time(as_of)),
    ).fetchone()[0]


def _sqlite_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _is_finite_nonnegative(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number >= 0


def _is_finite_positive(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def _is_probability(value: Any, *, strict: bool = False) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(number):
        return False
    return 0 < number < 1 if strict else 0 <= number <= 1


def _order_status_domain_invalid(row: Sequence[Any]) -> bool:
    original_size, size_matched, price, domain_error = row
    if domain_error is not None:
        return True
    if original_size is not None and not _is_finite_nonnegative(original_size):
        return True
    if size_matched is not None and not _is_finite_nonnegative(size_matched):
        return True
    if price is not None and not _is_probability(price, strict=True):
        return True
    return bool(
        original_size is not None
        and size_matched is not None
        and _is_finite_nonnegative(original_size)
        and _is_finite_nonnegative(size_matched)
        and float(size_matched) > float(original_size) + 0.000001
    )


def _parse_sqlite_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _default_log_paths(db_path: Path) -> list[Path]:
    return [db_path.parent / "logs", db_path.parent]


def _overall_status(issues: Sequence[AuditIssue]) -> str:
    severities = {issue.severity for issue in issues}
    if "CRITICAL" in severities:
        return "FAIL"
    if "HIGH" in severities:
        return "WARN"
    return "PASS"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
