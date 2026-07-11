from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from slack_data_collector.portfolio import (
    CURRENT_REPORT_SCHEMA_VERSION,
    LEGACY_REPORT_SCHEMA_VERSION,
    PortfolioParseError,
    parse_portfolio_message,
    transform_portfolio_reports,
)
from slack_data_collector.portfolio_sql import export_upsert_sql


def report_message(
    ts: str, report_date: str, total: str = "47037.06"
) -> dict[str, object]:
    names_and_values = [
        ("GOLDEN-APPLE (1)", "$1792.39 (Position: $1367.28, Cash: $425.11)"),
        ("GOLDEN-BANANA", "$28883.37 (Position: $25246.27, Cash: $3637.10)"),
        ("GOLDEN-CHERRY", "$3578.21 (Position: $2455.17, Cash: $1123.05)"),
        ("GOLDEN-APPLE (2)", "$12783.08 (Position: $10243.53, Cash: $2539.56)"),
    ]
    return {
        "type": "message",
        "subtype": "bot_message",
        "ts": ts,
        "attachments": [
            {
                "title": ":bar_chart: Polymarket 전체 포트폴리오",
                "text": f"일일 통합 리포트 - {report_date} 19:45:59 기준",
                "fields": [
                    {
                        "title": "💰 총 자산",
                        "value": f"${total} (Position: $39312.25, Cash: $7724.81)",
                    }
                ],
            },
            *[
                {
                    "author_name": name,
                    "fields": [{"title": "자산 가치", "value": value}],
                }
                for name, value in names_and_values
            ],
        ],
    }


def current_report_message(ts: str, report_date: str) -> dict[str, object]:
    names_and_values = [
        ("GOLDEN-APPLE (1)", "$10.00 (Position: $6.00, Cash: $4.00)"),
        ("GOLDEN-BANANA", "$11.00 (Position: $7.00, Cash: $4.00)"),
        ("GOLDEN-CHERRY", "$12.00 (Position: $8.00, Cash: $4.00)"),
        ("GOLDEN-APPLE (2)", "$13.00 (Position: $9.00, Cash: $4.00)"),
        ("GOLDEN-ECO", "$14.00 (Position: $10.00, Cash: $4.00)"),
        ("GOLDEN-FOX", "$15.00 (Position: $11.00, Cash: $4.00)"),
    ]
    return {
        "type": "message",
        "subtype": "bot_message",
        "ts": ts,
        "text": "[pb-portfolio/v2 COMPLETE] daily report",
        "attachments": [
            {
                "title": "📊 Polymarket 전체 포트폴리오",
                "text": f"일일 통합 리포트 - {report_date} 09:00:00 기준",
                "footer": (
                    "Polymarket Bot • pb-portfolio/v2 • COMPLETE • tz=Asia/Seoul"
                ),
                "fields": [
                    {
                        "title": "💰 총 자산",
                        "value": "$75.00 (Position: $51.00, Cash: $24.00)",
                    }
                ],
            },
            *[
                {"author_name": name, "text": f"{value}\n7d 손익 N/A"}
                for name, value in names_and_values
            ],
        ],
    }


class PortfolioParserTests(unittest.TestCase):
    def test_parses_expected_accounts_and_amounts(self) -> None:
        report = parse_portfolio_message(
            report_message("1782211560.242069", "2026-06-23")
        )

        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report.schema_version, LEGACY_REPORT_SCHEMA_VERSION)
        self.assertEqual(report.total.total_value, Decimal("47037.06"))
        self.assertEqual(report.algorithms[0].account_id, "golden-apple-1")
        self.assertEqual(report.algorithms[0].jenkins_name, "GOLDEN-APPLE (1)")
        self.assertEqual(report.algorithms[-1].balance.cash_value, Decimal("2539.55"))

    def test_parses_current_six_account_text_contract(self) -> None:
        report = parse_portfolio_message(
            current_report_message("1782211560.242069", "2026-06-23")
        )

        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report.schema_version, CURRENT_REPORT_SCHEMA_VERSION)
        self.assertEqual(len(report.algorithms), 6)
        self.assertEqual(report.algorithms[-1].account_id, "golden-fox")
        self.assertEqual(report.algorithms[-1].balance.total_value, Decimal("15.00"))

    def test_rejects_partial_five_account_current_report(self) -> None:
        message = current_report_message("1782211560.242069", "2026-06-23")
        assert isinstance(message["attachments"], list)
        message["attachments"].pop()

        with self.assertRaisesRegex(PortfolioParseError, "legacy4/current6"):
            parse_portfolio_message(message)

    def test_rejects_explicit_error_report(self) -> None:
        message = {
            "type": "message",
            "subtype": "bot_message",
            "ts": "1782211560.242069",
            "text": "[pb-portfolio/error-v1] Error in Daily Report",
            "attachments": [
                {
                    "title": "⚠️ Error in Daily Report",
                    "footer": "Polymarket Bot Error • pb-portfolio/error-v1",
                }
            ],
        }

        with self.assertRaisesRegex(PortfolioParseError, "오류 상태"):
            parse_portfolio_message(message)

    def test_rejects_non_complete_or_ambiguous_current_status(self) -> None:
        for status in ("FAILED", "STARTED"):
            message = current_report_message("1782211560.242069", "2026-06-23")
            message["text"] = f"[pb-portfolio/v2 {status}] daily report"
            assert isinstance(message["attachments"], list)
            message["attachments"][0]["footer"] = (
                f"Polymarket Bot • pb-portfolio/v2 • {status} • tz=Asia/Seoul"
            )
            with self.subTest(status=status), self.assertRaisesRegex(
                PortfolioParseError, "COMPLETE만"
            ):
                parse_portfolio_message(message)

        ambiguous = current_report_message("1782211560.242069", "2026-06-23")
        ambiguous["text"] += " FAILED"
        with self.assertRaisesRegex(PortfolioParseError, "COMPLETE만"):
            parse_portfolio_message(ambiguous)

        negated = current_report_message("1782211560.242069", "2026-06-23")
        negated["text"] = "pb-portfolio/v2 is not COMPLETE"
        with self.assertRaisesRegex(PortfolioParseError, "COMPLETE만"):
            parse_portfolio_message(negated)

    def test_rejects_current_report_without_redundant_v2_complete_markers(self) -> None:
        message = current_report_message("1782211560.242069", "2026-06-23")
        message["text"] = "daily report"

        with self.assertRaisesRegex(PortfolioParseError, "message text"):
            parse_portfolio_message(message)

        missing_timezone = current_report_message("1782211560.242069", "2026-06-23")
        assert isinstance(missing_timezone["attachments"], list)
        missing_timezone["attachments"][0]["footer"] = (
            "Polymarket Bot • pb-portfolio/v2 • COMPLETE"
        )
        with self.assertRaisesRegex(PortfolioParseError, "tz=<IANA timezone>"):
            parse_portfolio_message(missing_timezone)

    def test_rejects_contradictory_status_anywhere_in_current_payload(self) -> None:
        mutations = (
            lambda message: message["attachments"][0].update(
                title="📊 Polymarket 전체 포트폴리오 FAILED"
            ),
            lambda message: message["attachments"][1].update(
                text=message["attachments"][1]["text"] + "\nINCOMPLETE"
            ),
            lambda message: message.update(
                blocks=[{"type": "context", "text": {"text": "ERROR"}}]
            ),
        )
        for mutate in mutations:
            message = current_report_message("1782211560.242069", "2026-06-23")
            mutate(message)
            with self.subTest(message=message), self.assertRaisesRegex(
                PortfolioParseError, "전체 payload"
            ):
                parse_portfolio_message(message)

    def test_rejects_internally_inconsistent_money_breakdown(self) -> None:
        message = current_report_message("1782211560.242069", "2026-06-23")
        assert isinstance(message["attachments"], list)
        message["attachments"][-1]["text"] = (
            "$15.00 (Position: $1.00, Cash: $2.00)\n7d 손익 N/A"
        )

        with self.assertRaisesRegex(PortfolioParseError, r"position \+ cash"):
            parse_portfolio_message(message)

    def test_rejects_summary_that_does_not_match_account_sums(self) -> None:
        message = current_report_message("1782211560.242069", "2026-06-23")
        assert isinstance(message["attachments"], list)
        message["attachments"][0]["fields"][0]["value"] = (
            "$76.00 (Position: $52.00, Cash: $24.00)"
        )

        with self.assertRaisesRegex(PortfolioParseError, "계정 합계"):
            parse_portfolio_message(message)

    def test_latest_slack_message_wins_for_same_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / "messages.jsonl"
            records = [
                {
                    "message": report_message(
                        "1782210000.000001", "2026-06-23", "47037.05"
                    )
                },
                {
                    "message": report_message(
                        "1782211560.242069", "2026-06-23", "47037.06"
                    )
                },
            ]
            raw_path.write_text(
                "".join(
                    json.dumps(record, ensure_ascii=False) + "\n" for record in records
                ),
                encoding="utf-8",
            )

            result = transform_portfolio_reports(raw_path, root / "portfolio")
            totals = json.loads(result.totals_path.read_text(encoding="utf-8"))

        self.assertEqual(result.parsed_messages, 2)
        self.assertEqual(result.unique_dates, 1)
        self.assertEqual(result.overwritten_messages, 1)
        self.assertEqual(totals["total_value"], "47037.06")
        self.assertEqual(totals["source_schema_version"], LEGACY_REPORT_SCHEMA_VERSION)

    def test_exports_conditional_upsert_sql(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / "messages.jsonl"
            raw_path.write_text(
                json.dumps(
                    {"message": report_message("1782211560.242069", "2026-06-23")}
                )
                + "\n",
                encoding="utf-8",
            )
            result = transform_portfolio_reports(raw_path, root / "portfolio")
            paths = export_upsert_sql(
                result.output_directory, root / "sql", batch_size=2
            )
            sql = "\n".join(path.read_text(encoding="utf-8") for path in paths)

        self.assertIn("pb_daily_portfolio_totals", sql)
        self.assertIn("pb_daily_algorithm_balances", sql)
        self.assertIn("where excluded.source_message_ts >=", sql)
        self.assertIn("golden-eco", sql)
        self.assertIn("golden-fox", sql)

    def test_history_v2_schema_is_additive_and_documents_flow_boundary(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1] / "sql" / "pb_portfolio_history_v2.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("pb_strategy_deployments", migration)
        self.assertIn("pb_snapshot_runs", migration)
        self.assertIn("pb_external_cash_flows", migration)
        self.assertIn("add column if not exists snapshot_run_id", migration)
        self.assertIn("tstzrange", migration)
        self.assertIn("MAKER_REBATE", migration)
        self.assertIn("pg_advisory_xact_lock", migration)
        self.assertIn("pb_text_array_is_unique_nonempty", migration)
        self.assertIn("expected_account_count = cardinality(expected_account_ids)", migration)
        self.assertIn("pb_portfolio_writer_preflight_v2", migration)
        self.assertIn("pb_write_complete_portfolio_snapshot_v2", migration)
        self.assertIn("security invoker", migration.lower())
        self.assertIn("set search_path = pg_catalog, public", migration.lower())
        self.assertIn("total_value = position_value + cash_value", migration)
        self.assertIn("expected_account_count = 6", migration)
        self.assertIn("('NaN', 'Infinity', '-Infinity')", migration)
        self.assertIn("not isfinite(p_reported_at)", migration)
        self.assertIn("grant select, insert, update", migration.lower())
        self.assertIn("to service_role", migration.lower())
        self.assertNotIn("drop table", migration.lower())
