"""Command line entrypoint for retrospective audits and consistent backups."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .execution_ledger import ExecutionLedger
from .intent_probe import (
    IntentProbeConfigurationError,
    authenticated_clob_session_from_environment,
    probe_unresolved_intent,
)
from .retro_audit import (
    audit_many,
    backup_databases,
    discover_databases,
    parse_as_of,
    write_audit_bundle,
)


def _database_paths(
    values: list[Path], root: Path | None, *, include_sim: bool = False
) -> list[Path]:
    paths = {value.expanduser().resolve() for value in values}
    if root is not None:
        paths.update(
            discover_databases(root.expanduser().resolve(), include_sim=include_sim)
        )
    return sorted(path for path in paths if path.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and export Polymarket bot retrospective data"
    )
    parser.add_argument("--version", action="version", version="polybot-observability 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="DB/log 회고 준비 상태 검사")
    audit_parser.add_argument("--db", action="append", default=[], type=Path)
    audit_parser.add_argument(
        "--root", type=Path, help="trades.db 재귀 탐색 루트 (simulation은 --include-sim)"
    )
    audit_parser.add_argument("--days", type=int, default=30)
    audit_parser.add_argument("--as-of", help="재현용 종료일(UTC, YYYY-MM-DD)")
    audit_parser.add_argument("--output-dir", required=True, type=Path)
    audit_parser.add_argument(
        "--include-sim",
        action="store_true",
        help="trades_sim.db를 별도 simulation_assumption cohort로 포함",
    )
    audit_parser.add_argument(
        "--strict", action="store_true", help="CRITICAL/HIGH 이슈가 있으면 exit 1"
    )

    backup_parser = subparsers.add_parser("backup", help="SQLite online backup + checksum")
    backup_parser.add_argument("--db", action="append", default=[], type=Path)
    backup_parser.add_argument("--root", type=Path)
    backup_parser.add_argument("--output-dir", required=True, type=Path)
    backup_parser.add_argument(
        "--include-sim", action="store_true", help="trades_sim.db도 backup"
    )

    gaps_parser = subparsers.add_parser(
        "catalog-gaps", help="operator 격리 가능한 CLOB catalog gap 조회"
    )
    gaps_parser.add_argument("--db", required=True, type=Path)
    gaps_parser.add_argument("--strategy", required=True)
    gaps_parser.add_argument(
        "--include-evidence-linked",
        action="store_true",
        help="trade/status/fill evidence가 연결된 catalog gap도 진단 목록에 포함",
    )

    unresolved_parser = subparsers.add_parser(
        "unresolved-intents",
        help="order ID가 확정되지 않은 live CLOB intent를 read-only 조회",
    )
    unresolved_parser.add_argument("--db", required=True, type=Path)
    unresolved_parser.add_argument("--strategy", required=True)

    probe_intent_parser = subparsers.add_parser(
        "probe-intent",
        help="불확실한 CLOB intent를 authenticated order/trade history와 read-only 대조",
    )
    probe_intent_parser.add_argument("--db", required=True, type=Path)
    probe_intent_parser.add_argument("--strategy", required=True)
    probe_intent_parser.add_argument("--submission-id", required=True)
    probe_intent_parser.add_argument(
        "--window-seconds",
        type=int,
        default=600,
        help="intent 제출 시각 전후 조회 범위(기본 600초, 최대 86400초)",
    )

    resolve_intent_parser = subparsers.add_parser(
        "resolve-intent",
        help="backup 후 불확실한 CLOB intent에 operator 증거를 기록",
    )
    resolve_intent_parser.add_argument("--db", required=True, type=Path)
    resolve_intent_parser.add_argument("--strategy", required=True)
    resolve_intent_parser.add_argument("--submission-id", required=True)
    resolve_intent_parser.add_argument(
        "--resolution",
        required=True,
        choices=("NO_ORDER_CREATED", "ORDER_ID_LINKED"),
    )
    resolve_intent_parser.add_argument("--order-id")
    resolve_intent_parser.add_argument("--confirm", required=True)
    resolve_intent_parser.add_argument("--reason", required=True)
    resolve_intent_parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("~/.polybot/operator-backups"),
        help="workspace 밖 online backup + SHA-256 manifest 저장 위치",
    )

    resolve_parser = subparsers.add_parser(
        "resolve-catalog-gaps",
        help="backup 후 exact CLOB catalog gap 집합을 operator 승인으로 격리",
    )
    resolve_parser.add_argument("--db", required=True, type=Path)
    resolve_parser.add_argument("--strategy", required=True)
    resolve_parser.add_argument("--expected-count", required=True, type=int)
    resolve_parser.add_argument("--confirm", required=True)
    resolve_parser.add_argument("--reason", required=True)
    resolve_parser.add_argument(
        "--include-evidence-linked",
        action="store_true",
        help="강한 확인 문구로 linked evidence gap까지 operator 격리",
    )
    resolve_parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("~/.polybot/operator-backups"),
        help="workspace 밖 online backup + SHA-256 manifest 저장 위치",
    )

    quantity_parser = subparsers.add_parser(
        "quantity-scale-repairs",
        help="10^6 double-scaling이 증명된 terminal CLOB fill 조회",
    )
    quantity_parser.add_argument("--db", required=True, type=Path)
    quantity_parser.add_argument("--strategy", required=True)

    quantity_diagnostics_parser = subparsers.add_parser(
        "quantity-scale-diagnostics",
        help="10^6 scale 의심 주문의 repair 제외 사유를 read-only 출력",
    )
    quantity_diagnostics_parser.add_argument("--db", required=True, type=Path)
    quantity_diagnostics_parser.add_argument("--strategy", required=True)

    repair_parser = subparsers.add_parser(
        "repair-quantity-scale",
        help="backup 후 exact CLOB quantity double-scaling 집합 복구",
    )
    repair_parser.add_argument("--db", required=True, type=Path)
    repair_parser.add_argument("--strategy", required=True)
    repair_parser.add_argument("--expected-count", required=True, type=int)
    repair_parser.add_argument("--confirm", required=True)
    repair_parser.add_argument("--reason", required=True)
    repair_parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("~/.polybot/operator-backups"),
        help="workspace 밖 online backup + SHA-256 manifest 저장 위치",
    )

    args = parser.parse_args()
    operator_commands = {
        "catalog-gaps",
        "unresolved-intents",
        "probe-intent",
        "resolve-intent",
        "resolve-catalog-gaps",
        "quantity-scale-repairs",
        "quantity-scale-diagnostics",
        "repair-quantity-scale",
    }
    if args.command in operator_commands:
        database = args.db.expanduser().resolve()
        if not database.is_file():
            parser.error(f"trades.db를 찾을 수 없습니다: {database}")
        if args.command == "catalog-gaps":
            ledger = ExecutionLedger(database, strategy_name=args.strategy)
            print(
                json.dumps(
                    ledger.catalog_missing_submissions(
                        include_evidence_linked=args.include_evidence_linked
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if args.command == "unresolved-intents":
            ledger = ExecutionLedger(database, strategy_name=args.strategy)
            print(
                json.dumps(
                    ledger.unresolved_submission_outcomes(),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if args.command == "probe-intent":
            if not 1 <= args.window_seconds <= 86_400:
                parser.error("--window-seconds는 1~86400 범위여야 합니다")
            try:
                session = authenticated_clob_session_from_environment()
            except IntentProbeConfigurationError as error:
                parser.error(str(error))
            except Exception as error:
                parser.error(f"CLOB 인증 초기화 실패: {type(error).__name__}")
            try:
                result = probe_unresolved_intent(
                    database,
                    strategy_name=args.strategy,
                    submission_id=args.submission_id,
                    client=session.client,
                    funder_address=session.funder_address,
                    window_seconds=args.window_seconds,
                )
            except ValueError as error:
                parser.error(str(error))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        if args.command == "resolve-intent":
            normalized_order_id = str(args.order_id or "").strip()
            if args.resolution == "NO_ORDER_CREATED":
                expected_confirmation = (
                    f"RESOLVE_{args.submission_id}_AS_NO_ORDER_CREATED"
                )
            else:
                if not normalized_order_id:
                    parser.error("ORDER_ID_LINKED에는 --order-id가 필요합니다")
                expected_confirmation = (
                    f"LINK_{args.submission_id}_TO_{normalized_order_id}"
                )
            if args.confirm != expected_confirmation:
                parser.error(f"확인 문구가 일치하지 않습니다: {expected_confirmation}")
            backup = backup_databases([database], args.backup_dir)
            ledger = ExecutionLedger(database, strategy_name=args.strategy)
            try:
                ledger.resolve_uncertain_submission(
                    args.submission_id,
                    resolution=args.resolution,
                    reason=args.reason,
                    order_id=normalized_order_id or None,
                )
            except (RuntimeError, ValueError) as error:
                parser.error(f"backup={backup}; resolution 실패: {error}")
            print(
                json.dumps(
                    {
                        "submission_id": args.submission_id,
                        "resolution": args.resolution,
                        "order_id": normalized_order_id or None,
                        "backup": str(backup),
                    },
                    ensure_ascii=False,
                )
            )
            return

        if args.command == "quantity-scale-repairs":
            ledger = ExecutionLedger(database, strategy_name=args.strategy)
            print(
                json.dumps(
                    ledger.quantity_scale_repair_candidates(),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if args.command == "quantity-scale-diagnostics":
            ledger = ExecutionLedger(database, strategy_name=args.strategy)
            print(
                json.dumps(
                    ledger.quantity_scale_diagnostics(),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if args.expected_count < 1:
            parser.error("--expected-count는 1 이상이어야 합니다")
        backup = backup_databases([database], args.backup_dir)
        ledger = ExecutionLedger(database, strategy_name=args.strategy)
        try:
            if args.command == "resolve-catalog-gaps":
                result = {
                    "resolved": ledger.resolve_catalog_missing_submissions(
                        expected_count=args.expected_count,
                        confirmation=args.confirm,
                        reason=args.reason,
                        include_evidence_linked=args.include_evidence_linked,
                    ),
                    "status": "OPERATOR_EVIDENCE_GAP",
                    "include_evidence_linked": args.include_evidence_linked,
                }
            else:
                result = ledger.repair_quantity_scale(
                    expected_count=args.expected_count,
                    confirmation=args.confirm,
                    reason=args.reason,
                )
                result["status"] = "QUANTITY_SCALE_REPAIRED"
        except (RuntimeError, ValueError) as error:
            parser.error(f"backup={backup}; resolution 실패: {error}")
        result["backup"] = str(backup)
        print(
            json.dumps(
                result,
                ensure_ascii=False,
            )
        )
        return

    databases = _database_paths(args.db, args.root, include_sim=args.include_sim)
    if not databases:
        parser.error("--db 또는 --root에서 trades.db를 한 개 이상 찾을 수 없습니다")

    if args.command == "backup":
        destination = backup_databases(databases, args.output_dir)
        print(destination)
        return

    if args.days < 1:
        parser.error("--days는 1 이상이어야 합니다")
    try:
        as_of = parse_as_of(args.as_of)
    except ValueError as error:
        parser.error(str(error))
    bundle = audit_many(databases, days=args.days, as_of=as_of)
    json_path, markdown_path = write_audit_bundle(bundle, args.output_dir)
    print(json_path)
    print(markdown_path)
    if args.strict:
        # Simulation assumptions are visible in the bundle but never make a
        # live readiness gate fail. Default discovery excludes them entirely.
        severe = bundle["live_issue_counts"].get("CRITICAL", 0) + bundle[
            "live_issue_counts"
        ].get("HIGH", 0)
        if severe:
            sys.exit(1)


if __name__ == "__main__":
    main()
