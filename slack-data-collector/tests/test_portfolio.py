from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from slack_data_collector.portfolio import (
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


class PortfolioParserTests(unittest.TestCase):
    def test_parses_expected_accounts_and_amounts(self) -> None:
        report = parse_portfolio_message(
            report_message("1782211560.242069", "2026-06-23")
        )

        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report.total.total_value, Decimal("47037.06"))
        self.assertEqual(report.algorithms[0].account_id, "golden-apple-1")
        self.assertEqual(report.algorithms[0].jenkins_name, "GOLDEN-APPLE (1)")
        self.assertEqual(report.algorithms[-1].balance.cash_value, Decimal("2539.56"))

    def test_latest_slack_message_wins_for_same_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / "messages.jsonl"
            records = [
                {
                    "message": report_message(
                        "1782210000.000001", "2026-06-23", "47000.00"
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
