"""Tests for Supabase daily snapshot persistence."""

import base64
import json
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from polybot_reporter.storage.supabase_writer import (
    SupabaseConfigurationError,
    SupabasePortfolioWriter,
    SupabaseWriteError,
)

CATALOG = [
    {"account_id": "golden-apple-1", "jenkins_name": "GOLDEN-APPLE (1)"},
    {"account_id": "golden-banana", "jenkins_name": "GOLDEN-BANANA"},
    {"account_id": "golden-cherry", "jenkins_name": "GOLDEN-CHERRY"},
    {"account_id": "golden-apple-2", "jenkins_name": "GOLDEN-APPLE (2)"},
    {"account_id": "golden-eco", "jenkins_name": "GOLDEN-ECO"},
    {"account_id": "golden-fox", "jenkins_name": "GOLDEN-FOX"},
]


def make_reports():
    # Raw API values can have more precision than the two decimals shown in Slack.
    # Summing raw values before rounding preserves the Slack portfolio total.
    return {
        "golden-apple (1)": report(1792.39125, 1367.27975, 425.1065),
        "golden-banana": report(28883.37125, 25246.26975, 3637.0965),
        "golden-cherry": report(3578.21125, 2455.16975, 1123.0465),
        "golden-apple (2)": report(12783.08125, 10243.52975, 2539.5565),
        # 2026-07 신규 테스트 슬롯 (이름 중복 없음 - display_name == name)
        "golden-eco": report(3000.0, 0.0, 3000.0),
        "golden-fox": report(3000.0, 0.0, 3000.0),
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
        self.filtered = False

    def select(self, _columns):
        return self

    def upsert(self, payload, on_conflict):
        self.payload = payload
        self.on_conflict = on_conflict
        return self

    def in_(self, _column, _values):
        self.filtered = True
        return self

    def gte(self, _column, _value):
        self.filtered = True
        return self

    def lt(self, _column, _value):
        self.filtered = True
        return self

    def execute(self):
        if self.payload is None:
            if self.table_name == SupabasePortfolioWriter.BALANCE_TABLE and self.filtered:
                return SimpleNamespace(data=self.client.history)
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
    def __init__(self, catalog=None, history=None):
        self.catalog = catalog or CATALOG
        self.history = history or []
        self.operations = []

    def table(self, table_name):
        return FakeQuery(self, table_name)


class PermissionDeniedQuery:
    def select(self, _columns):
        return self

    def execute(self):
        raise Exception({"message": "permission denied", "code": "42501"})


class PermissionDeniedClient:
    def table(self, _table_name):
        return PermissionDeniedQuery()


def legacy_key(role):
    def encode(payload):
        value = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    return f"{encode({'alg': 'HS256', 'typ': 'JWT'})}.{encode({'role': role})}.signature"


def test_rejects_publishable_key_before_creating_client():
    with pytest.raises(SupabaseConfigurationError, match="sb_publishable"):
        SupabasePortfolioWriter(
            url="https://example.supabase.co",
            secret_key="sb_publishable_example_key",
        )


def test_rejects_legacy_anon_key():
    with pytest.raises(SupabaseConfigurationError, match="legacy anon"):
        SupabasePortfolioWriter(
            url="https://example.supabase.co",
            secret_key=legacy_key("anon"),
        )


def test_accepts_server_secret_key(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(
        "polybot_reporter.storage.supabase_writer.create_client",
        lambda _url, _key: client,
    )

    writer = SupabasePortfolioWriter(
        url="https://example.supabase.co",
        secret_key="sb_secret_example_server_key",
    )

    assert writer.check_connection() == 6


def test_permission_error_explains_required_key_type():
    writer = SupabasePortfolioWriter(client=PermissionDeniedClient())

    with pytest.raises(SupabaseWriteError, match="sb_secret"):
        writer.check_connection()


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
        "golden-eco",
        "golden-fox",
    }
    assert all(row["report_date"] == "2026-06-23" for row in balances["payload"])
    assert total["table"] == "pb_daily_portfolio_totals"
    assert total["on_conflict"] == "report_date"
    assert result.report_date == "2026-06-23"
    assert result.account_count == 6
    assert result.total_value == 53037.06
    assert result.position_value == 39312.25
    assert result.cash_value == 13724.81


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


def test_compute_period_pnl_picks_earliest_in_window_and_handles_missing():
    history = [
        {"report_date": "2026-06-20", "account_id": "a", "total_value": 100.0},
        {"report_date": "2026-06-28", "account_id": "a", "total_value": 130.0},
        # report_date == as_of is ignored so the live in-memory total is "current".
        {"report_date": "2026-06-30", "account_id": "a", "total_value": 999.0},
    ]
    out = SupabasePortfolioWriter._compute_period_pnl(
        {"a": 150.0, "b": 50.0}, history, date(2026, 6, 30), (7, 30)
    )
    # 7d floor = 6/24 -> earliest >= 6/24 is 6/28 (130) -> 150 - 130
    assert out["a"][7] == 20.0
    # 30d floor = 6/1 -> earliest is 6/20 (100) -> 150 - 100
    assert out["a"][30] == 50.0
    # account "b" has no history -> undefined for every window
    assert out["b"][7] is None
    assert out["b"][30] is None


def test_get_period_pnl_uses_total_change_from_history():
    history = [
        {"report_date": "2026-06-23", "account_id": "golden-cherry", "total_value": 3578.21},
        {"report_date": "2026-06-29", "account_id": "golden-cherry", "total_value": 4222.59},
        # apple-2 only has a row older than the 7d window floor (6/24)
        {"report_date": "2026-06-23", "account_id": "golden-apple-2", "total_value": 12783.08},
    ]
    writer = SupabasePortfolioWriter(client=FakeClient(history=history))
    reports = {
        "golden-cherry": {"total_value": 4245.11},
        "golden-apple (2)": {"total_value": 13144.75},
    }

    pnl = writer.get_period_pnl(reports, windows=(7, 30), as_of=date(2026, 6, 30))

    assert pnl["golden-cherry"][7] == round(4245.11 - 4222.59, 6)
    assert pnl["golden-cherry"][30] == round(4245.11 - 3578.21, 6)
    # no snapshot on/after the 7d floor -> 7d undefined; 30d anchors on 6/23
    assert pnl["golden-apple (2)"][7] is None
    assert pnl["golden-apple (2)"][30] == round(13144.75 - 12783.08, 6)
