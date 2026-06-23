"""Tests for Supabase daily snapshot persistence."""

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from polybot_reporter.storage.supabase_writer import (
    SupabasePortfolioWriter,
    SupabaseWriteError,
)

CATALOG = [
    {"account_id": "golden-apple-1", "jenkins_name": "GOLDEN-APPLE (1)"},
    {"account_id": "golden-banana", "jenkins_name": "GOLDEN-BANANA"},
    {"account_id": "golden-cherry", "jenkins_name": "GOLDEN-CHERRY"},
    {"account_id": "golden-apple-2", "jenkins_name": "GOLDEN-APPLE (2)"},
]


def make_reports():
    # Raw API values can have more precision than the two decimals shown in Slack.
    # Summing raw values before rounding preserves the Slack portfolio total.
    return {
        "golden-apple (1)": report(1792.39125, 1367.27975, 425.1065),
        "golden-banana": report(28883.37125, 25246.26975, 3637.0965),
        "golden-cherry": report(3578.21125, 2455.16975, 1123.0465),
        "golden-apple (2)": report(12783.08125, 10243.52975, 2539.5565),
    }


def report(total, position, cash):
    return {
        "total_value": total,
        "position_value": position,
        "cash_balance": cash,
    }


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.payload = None
        self.on_conflict = None

    def select(self, _columns):
        return self

    def upsert(self, payload, on_conflict):
        self.payload = payload
        self.on_conflict = on_conflict
        return self

    def execute(self):
        if self.payload is None:
            return SimpleNamespace(data=self.client.catalog)
        self.client.operations.append(
            {
                "table": self.table_name,
                "payload": self.payload,
                "on_conflict": self.on_conflict,
            }
        )
        data = self.payload if isinstance(self.payload, list) else [self.payload]
        return SimpleNamespace(data=data)


class FakeClient:
    def __init__(self, catalog=None):
        self.catalog = catalog or CATALOG
        self.operations = []

    def table(self, table_name):
        return FakeQuery(self, table_name)


def test_upserts_complete_snapshot_with_date_conflicts():
    client = FakeClient()
    writer = SupabasePortfolioWriter(client=client)
    result = writer.write_daily_snapshot(
        make_reports(),
        report_date=date(2026, 6, 23),
        reported_at=datetime(2026, 6, 23, 9, 30, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    balances, total = client.operations
    assert balances["table"] == "pb_daily_algorithm_balances"
    assert balances["on_conflict"] == "report_date,account_id"
    assert {row["account_id"] for row in balances["payload"]} == {
        "golden-apple-1",
        "golden-banana",
        "golden-cherry",
        "golden-apple-2",
    }
    assert all(row["report_date"] == "2026-06-23" for row in balances["payload"])
    assert total["table"] == "pb_daily_portfolio_totals"
    assert total["on_conflict"] == "report_date"
    assert result.report_date == "2026-06-23"
    assert result.account_count == 4
    assert result.total_value == 47037.06
    assert result.position_value == 39312.25
    assert result.cash_value == 7724.81


def test_rejects_failed_account_without_writing():
    client = FakeClient()
    reports = make_reports()
    reports["golden-cherry"]["error"] = "upstream timeout"

    with pytest.raises(SupabaseWriteError, match="수집 실패"):
        SupabasePortfolioWriter(client=client).write_daily_snapshot(reports)

    assert client.operations == []


def test_rejects_incomplete_snapshot_without_writing():
    client = FakeClient()
    reports = make_reports()
    del reports["golden-apple (2)"]

    with pytest.raises(SupabaseWriteError, match="일부 DB 계정"):
        SupabasePortfolioWriter(client=client).write_daily_snapshot(reports)

    assert client.operations == []
