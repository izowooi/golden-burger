from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from slack_data_collector.portfolio import (
    CURRENT_REPORT_SCHEMA_VERSION,
    LEGACY_REPORT_SCHEMA_VERSION,
    PREVIOUS_REPORT_SCHEMA_VERSION,
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


def text_report_message(
    ts: str,
    report_date: str,
    schema_version: str,
    names_and_values: list[tuple[str, str]],
    total: str,
    position: str,
    cash: str,
) -> dict[str, object]:
    return {
        "type": "message",
        "subtype": "bot_message",
        "ts": ts,
        "text": f"[{schema_version} COMPLETE] daily report",
        "attachments": [
            {
                "title": "📊 Polymarket 전체 포트폴리오",
                "text": f"일일 통합 리포트 - {report_date} 09:00:00 기준",
                "footer": (
                    f"Polymarket Bot • {schema_version} • COMPLETE • "
                    "tz=Asia/Seoul"
                ),
                "fields": [
                    {
                        "title": "💰 총 자산",
                        "value": (
                            f"${total} (Position: ${position}, Cash: ${cash})"
                        ),
                    }
                ],
            },
            *[
                {"author_name": name, "text": f"{value}\n7d 손익 N/A"}
                for name, value in names_and_values
            ],
        ],
    }


def previous_report_message(ts: str, report_date: str) -> dict[str, object]:
    names_and_values = [
        ("GOLDEN-APPLE (1)", "$10.00 (Position: $6.00, Cash: $4.00)"),
        ("GOLDEN-BANANA", "$11.00 (Position: $7.00, Cash: $4.00)"),
        ("GOLDEN-CHERRY", "$12.00 (Position: $8.00, Cash: $4.00)"),
        ("GOLDEN-APPLE (2)", "$13.00 (Position: $9.00, Cash: $4.00)"),
        ("GOLDEN-ECO", "$14.00 (Position: $10.00, Cash: $4.00)"),
        ("GOLDEN-FOX", "$15.00 (Position: $11.00, Cash: $4.00)"),
    ]
    return text_report_message(
        ts,
        report_date,
        PREVIOUS_REPORT_SCHEMA_VERSION,
        names_and_values,
        "75.00",
        "51.00",
        "24.00",
    )


def current_report_message(ts: str, report_date: str) -> dict[str, object]:
    names_and_values = [
        ("GOLDEN-APPLE (1)", "$10.00 (Position: $6.00, Cash: $4.00)"),
        ("GOLDEN-BANANA", "$11.00 (Position: $7.00, Cash: $4.00)"),
        ("GOLDEN-CHERRY", "$12.00 (Position: $8.00, Cash: $4.00)"),
        ("GOLDEN-APPLE (2)", "$13.00 (Position: $9.00, Cash: $4.00)"),
        ("GOLDEN-ECO", "$14.00 (Position: $10.00, Cash: $4.00)"),
        ("GOLDEN-FOX", "$15.00 (Position: $11.00, Cash: $4.00)"),
        ("GOLDEN-LION", "$16.00 (Position: $12.00, Cash: $4.00)"),
        ("GOLDEN-TIGER", "$17.00 (Position: $13.00, Cash: $4.00)"),
        ("GOLDEN-WOLF", "$18.00 (Position: $14.00, Cash: $4.00)"),
        ("GOLDEN-EAGLE", "$19.00 (Position: $15.00, Cash: $4.00)"),
        ("GOLDEN-BEAR", "$20.00 (Position: $16.00, Cash: $4.00)"),
    ]
    message = text_report_message(
        ts,
        report_date,
        CURRENT_REPORT_SCHEMA_VERSION,
        names_and_values,
        "165.00",
        "121.00",
        "44.00",
    )
    message["text"] = "일일 리포트 - 총 자산: $165.00 (7d: $+0.00)"
    return message


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

    def test_parses_current_eleven_account_text_contract(self) -> None:
        report = parse_portfolio_message(
            current_report_message("1782211560.242069", "2026-06-23")
        )

        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report.schema_version, CURRENT_REPORT_SCHEMA_VERSION)
        self.assertEqual(len(report.algorithms), 11)
        self.assertEqual(report.algorithms[-1].account_id, "golden-bear")
        self.assertEqual(report.algorithms[-1].balance.total_value, Decimal("20.00"))

    def test_parses_previous_six_account_text_contract(self) -> None:
        report = parse_portfolio_message(
            previous_report_message("1782211560.242069", "2026-06-23")
        )

        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report.schema_version, PREVIOUS_REPORT_SCHEMA_VERSION)
        self.assertEqual(len(report.algorithms), 6)
        self.assertEqual(report.algorithms[-1].account_id, "golden-fox")

    def test_rejects_partial_ten_account_current_report(self) -> None:
        message = current_report_message("1782211560.242069", "2026-06-23")
        assert isinstance(message["attachments"], list)
        message["attachments"].pop()

        with self.assertRaisesRegex(PortfolioParseError, "legacy4/v2-six/v3-current"):
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
            message["text"] = f"[pb-portfolio/v3 {status}] daily report"
            assert isinstance(message["attachments"], list)
            message["attachments"][0]["footer"] = (
                f"Polymarket Bot • pb-portfolio/v3 • {status} • tz=Asia/Seoul"
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
        negated["text"] = "pb-portfolio/v3 is not COMPLETE"
        with self.assertRaisesRegex(PortfolioParseError, "COMPLETE만"):
            parse_portfolio_message(negated)

    def test_current_report_allows_human_readable_message_text(self) -> None:
        message = current_report_message("1782211560.242069", "2026-06-23")

        report = parse_portfolio_message(message)

        self.assertIsNotNone(report)
        self.assertNotIn("pb-portfolio", str(message["text"]))

    def test_rejects_current_report_without_footer_marker_or_timezone(self) -> None:
        missing_timezone = current_report_message("1782211560.242069", "2026-06-23")
        assert isinstance(missing_timezone["attachments"], list)
        missing_timezone["attachments"][0]["footer"] = (
            "Polymarket Bot • pb-portfolio/v3 • COMPLETE"
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
        self.assertIn("golden-lion", sql)
        self.assertIn("golden-tiger", sql)
        self.assertIn("golden-wolf", sql)
        self.assertIn("golden-eagle", sql)
        self.assertIn("golden-bear", sql)

    def test_history_v2_schema_is_additive_and_documents_flow_boundary(self) -> None:
        sql_directory = Path(__file__).resolve().parents[1] / "sql"
        base_schema = (sql_directory / "pb_portfolio_schema.sql").read_text(
            encoding="utf-8"
        )
        migration = (sql_directory / "pb_portfolio_history_v2.sql").read_text(
            encoding="utf-8"
        )

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
        balance_column = migration.index(
            "alter table public.pb_daily_algorithm_balances\n"
            "  add column if not exists snapshot_run_id"
        )
        balance_index = migration.index(
            "create index if not exists "
            "pb_daily_algorithm_balances_snapshot_run_id_idx"
        )
        self.assertLess(balance_column, balance_index)
        self.assertRegex(migration.lower(), r"\bbegin\s*;")
        self.assertIn("notify pgrst, 'reload schema';", migration.lower())
        self.assertRegex(migration.lower(), r"commit\s*;\s*$")
        self.assertNotIn("drop table", migration.lower())
        self.assertRegex(base_schema.lower(), r"\bbegin\s*;")
        self.assertIn("notify pgrst, 'reload schema';", base_schema.lower())
        self.assertRegex(base_schema.lower(), r"commit\s*;\s*$")

    def test_history_v3_schema_adds_catalog_driven_atomic_contract(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "sql"
            / "pb_portfolio_history_v3.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("pb_portfolio_writer_preflight_v3", migration)
        self.assertIn("pb_write_complete_portfolio_snapshot_v3", migration)
        self.assertIn("pb-portfolio/v3", migration)
        self.assertIn("expected_account_count > 0", migration)
        self.assertIn("cardinality(expected_ids)", migration)
        self.assertIn("golden-lion", migration)
        self.assertIn("golden-tiger", migration)
        self.assertIn("golden-wolf", migration)
        self.assertIn("golden-eagle", migration)
        self.assertIn("golden-bear", migration)
        self.assertIn("security invoker", migration.lower())
        self.assertIn("set search_path = pg_catalog, public", migration.lower())
        self.assertRegex(migration.lower(), r"\bbegin\s*;")
        self.assertIn("notify pgrst, 'reload schema';", migration.lower())
        self.assertRegex(migration.lower(), r"commit\s*;\s*$")

    def test_catalog_sync_rpc_is_add_only_and_service_role_only(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "sql"
            / "pb_algorithm_account_catalog_sync_v1.sql"
        ).read_text(encoding="utf-8")

        lowered = migration.lower()
        self.assertIn("pb_register_algorithm_accounts_v1", migration)
        self.assertIn("security definer", lowered)
        self.assertIn("set search_path = pg_catalog, public", lowered)
        self.assertIn("on conflict (account_id) do nothing", lowered)
        self.assertIn("revoke all", lowered)
        self.assertIn("from anon", lowered)
        self.assertIn("from authenticated", lowered)
        self.assertIn("to service_role", lowered)
        self.assertNotIn("delete from", lowered)
        self.assertNotIn("update public.pb_algorithm_accounts", lowered)
        self.assertIn("notify pgrst, 'reload schema';", lowered)
        self.assertRegex(lowered, r"\bbegin\s*;")
        self.assertRegex(lowered, r"commit\s*;\s*$")
