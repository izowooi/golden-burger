"""Tests for Supabase daily snapshot persistence."""

import base64
import json
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from polybot_reporter.notifications.slack_notifier import SlackNotifier
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
    {"account_id": "golden-lion", "jenkins_name": "GOLDEN-LION"},
    {"account_id": "golden-tiger", "jenkins_name": "GOLDEN-TIGER"},
    {"account_id": "golden-wolf", "jenkins_name": "GOLDEN-WOLF"},
    {"account_id": "golden-eagle", "jenkins_name": "GOLDEN-EAGLE"},
    {"account_id": "golden-bear", "jenkins_name": "GOLDEN-BEAR"},
]
CONFIGURED_NAMES = [row["jenkins_name"] for row in CATALOG]


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
        "golden-lion": report(3000.0, 0.0, 3000.0),
        "golden-tiger": report(3000.0, 0.0, 3000.0),
        "golden-wolf": report(3000.0, 0.0, 3000.0),
        "golden-eagle": report(3000.0, 0.0, 3000.0),
        "golden-bear": report(3000.0, 0.0, 3000.0),
    }


def report(total, position, cash):
    return {
        "total_value": total,
        "position_value": position,
        "cash_balance": cash,
        "num_positions": 0,
        "positions": [],
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
    def __init__(self, catalog=None, history=None, *, rpc_available=True):
        self.catalog = catalog or CATALOG
        self.history = history or []
        self.operations = []
        self.rpc_available = rpc_available

    def table(self, table_name):
        return FakeQuery(self, table_name)

    def rpc(self, function_name, params):
        if not self.rpc_available:
            raise RuntimeError("function does not exist")
        return FakeRpcQuery(self, function_name, params)


class FakeRpcQuery:
    def __init__(self, client, function_name, params):
        self.client = client
        self.function_name = function_name
        self.params = params

    def execute(self):
        if self.function_name == SupabasePortfolioWriter.PREFLIGHT_RPC:
            return SimpleNamespace(
                data={
                    "contract_version": "pb-portfolio/v3",
                    "account_count": len(self.client.catalog),
                }
            )
        if self.function_name == SupabasePortfolioWriter.CATALOG_SYNC_RPC:
            accounts = self.params["p_accounts"]
            self.client.operations.append(
                {"rpc": self.function_name, "params": self.params}
            )
            self.client.catalog.extend(
                {
                    "account_id": row["account_id"],
                    "jenkins_name": row["jenkins_name"],
                }
                for row in accounts
            )
            return SimpleNamespace(
                data={
                    "requested_count": len(accounts),
                    "inserted_count": len(accounts),
                    "catalog_count": len(self.client.catalog),
                }
            )
        if self.function_name != SupabasePortfolioWriter.SNAPSHOT_RPC:
            raise RuntimeError("unknown RPC")
        self.client.operations.append(
            {"rpc": self.function_name, "params": self.params}
        )
        balances = self.params["p_balances"]
        return SimpleNamespace(
            data={
                "report_date": self.params["p_report_date"],
                "account_count": len(balances),
                "total_value": round(sum(row["total_value"] for row in balances), 2),
                "position_value": round(
                    sum(row["position_value"] for row in balances), 2
                ),
                "cash_value": round(sum(row["cash_value"] for row in balances), 2),
            }
        )


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
        secret_key="sb_" + "secret_" + "example_server_key",
    )

    assert writer.check_connection(CONFIGURED_NAMES) == 11


def test_permission_error_explains_required_key_type():
    writer = SupabasePortfolioWriter(client=PermissionDeniedClient())

    with pytest.raises(SupabaseWriteError, match="sb_secret"):
        writer.check_connection(CONFIGURED_NAMES)


def test_check_connection_rejects_name_mismatch_not_just_equal_count():
    writer = SupabasePortfolioWriter(client=FakeClient())
    wrong_names = [*CONFIGURED_NAMES[:-1], "GOLDEN-UNKNOWN"]

    with pytest.raises(SupabaseWriteError, match="GOLDEN-BEAR") as error:
        writer.check_connection(wrong_names)

    assert "GOLDEN-UNKNOWN" in str(error.value)


def test_check_connection_rejects_configured_account_missing_from_catalog():
    ten = CATALOG[:-1]
    writer = SupabasePortfolioWriter(client=FakeClient(catalog=ten))

    with pytest.raises(SupabaseWriteError, match="Supabase 카탈로그"):
        writer.check_connection(CONFIGURED_NAMES)


def test_check_connection_rejects_duplicate_stable_account_ids():
    duplicated = [dict(row) for row in CATALOG]
    duplicated[-1]["account_id"] = duplicated[-2]["account_id"]

    with pytest.raises(SupabaseWriteError, match="stable ID가 중복"):
        SupabasePortfolioWriter(client=FakeClient(catalog=duplicated)).check_connection(
            CONFIGURED_NAMES
        )


def test_sync_catalog_adds_only_missing_accounts_and_verifies_contract():
    client = FakeClient(catalog=[dict(row) for row in CATALOG])
    writer = SupabasePortfolioWriter(client=client)

    result = writer.sync_catalog([*CONFIGURED_NAMES, "golden-cat", "golden-dog"])

    assert result.requested_count == 2
    assert result.inserted_count == 2
    assert result.catalog_count == 13
    operation = client.operations[0]
    assert operation["rpc"] == SupabasePortfolioWriter.CATALOG_SYNC_RPC
    assert operation["params"]["p_accounts"] == [
        {
            "account_id": "golden-cat",
            "jenkins_name": "GOLDEN-CAT",
            "algorithm_code": "golden-cat",
            "instance_no": None,
            "sort_order": 12,
        },
        {
            "account_id": "golden-dog",
            "jenkins_name": "GOLDEN-DOG",
            "algorithm_code": "golden-dog",
            "instance_no": None,
            "sort_order": 13,
        },
    ]


def test_sync_catalog_is_noop_when_contract_already_matches():
    client = FakeClient(catalog=[dict(row) for row in CATALOG])

    result = SupabasePortfolioWriter(client=client).sync_catalog(CONFIGURED_NAMES)

    assert result.requested_count == 0
    assert result.inserted_count == 0
    assert result.catalog_count == 11
    assert client.operations == []


def test_sync_catalog_refuses_to_delete_accounts_missing_from_jenkins():
    writer = SupabasePortfolioWriter(
        client=FakeClient(catalog=[dict(row) for row in CATALOG])
    )

    with pytest.raises(SupabaseWriteError, match="삭제하지 않습니다"):
        writer.sync_catalog(CONFIGURED_NAMES[:-1])


def test_writes_complete_snapshot_with_one_atomic_rpc_call():
    client = FakeClient()
    writer = SupabasePortfolioWriter(client=client)
    result = writer.write_daily_snapshot(
        make_reports(),
        report_date=date(2026, 6, 23),
        reported_at=datetime(2026, 6, 23, 9, 30, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    assert len(client.operations) == 1
    operation = client.operations[0]
    assert operation["rpc"] == SupabasePortfolioWriter.SNAPSHOT_RPC
    assert {row["account_id"] for row in operation["params"]["p_balances"]} == {
        "golden-apple-1",
        "golden-banana",
        "golden-cherry",
        "golden-apple-2",
        "golden-eco",
        "golden-fox",
        "golden-lion",
        "golden-tiger",
        "golden-wolf",
        "golden-eagle",
        "golden-bear",
    }
    assert operation["params"]["p_report_date"] == "2026-06-23"
    assert operation["params"]["p_source_schema_version"] == "pb-portfolio/v3"
    assert result.report_date == "2026-06-23"
    assert result.account_count == 11
    assert result.total_value == 68037.05
    assert result.position_value == 39312.25
    assert result.cash_value == 28724.80
    assert result.total_value == result.position_value + result.cash_value
    assert all(
        Decimal(str(row["total_value"]))
        == Decimal(str(row["position_value"])) + Decimal(str(row["cash_value"]))
        for row in operation["params"]["p_balances"]
    )


def test_preflight_and_snapshot_support_twenty_catalog_accounts():
    catalog = [
        {
            "account_id": f"golden-account-{index}",
            "jenkins_name": f"GOLDEN-ACCOUNT-{index}",
        }
        for index in range(1, 21)
    ]
    reports = {
        row["jenkins_name"]: report(10.0, 4.0, 6.0) for row in catalog
    }
    writer = SupabasePortfolioWriter(client=FakeClient(catalog=catalog))

    assert writer.check_connection(list(reports)) == 20
    result = writer.write_daily_snapshot(reports)

    assert result.account_count == 20
    assert result.total_value == 200.0


def test_missing_atomic_rpc_fails_closed_without_table_write():
    client = FakeClient(rpc_available=False)
    writer = SupabasePortfolioWriter(client=client)

    with pytest.raises(SupabaseWriteError, match="unsafe fallback 없음"):
        writer.write_daily_snapshot(make_reports())

    assert client.operations == []


def test_preflight_requires_atomic_rpc_migration_before_collection():
    client = FakeClient(rpc_available=False)

    with pytest.raises(SupabaseWriteError, match="migration을 먼저 적용"):
        SupabasePortfolioWriter(client=client).check_connection(CONFIGURED_NAMES)

    assert client.operations == []


def test_preflight_pgrst202_has_specific_safe_recovery_without_fallback():
    class MissingRpcClient(FakeClient):
        def rpc(self, function_name, params):
            del function_name, params
            raise RuntimeError(
                {
                    "message": "schema cache did not contain function",
                    "code": "PGRST202",
                }
            )

    client = MissingRpcClient()

    with pytest.raises(SupabaseWriteError) as captured:
        SupabasePortfolioWriter(client=client).check_connection(CONFIGURED_NAMES)

    message = str(captured.value)
    assert "PGRST202" in message
    assert "SUPABASE_MIGRATION.md" in message
    assert "NOTIFY pgrst" in message
    assert "unsafe fallback은 없습니다" in message
    assert client.operations == []


def test_double_digit_raw_mismatches_share_one_canonical_cent_contract():
    reports = {
        name: report(10.02, 5.0, 5.0)
        for name in (
            "golden-apple (1)",
            "golden-banana",
            "golden-cherry",
            "golden-apple (2)",
            "golden-eco",
            "golden-fox",
            "golden-lion",
            "golden-tiger",
            "golden-wolf",
            "golden-eagle",
            "golden-bear",
        )
    }
    client = FakeClient()
    slack_payload = {}
    notifier = SlackNotifier(webhook_url="https://example.invalid/hook")
    notifier.send_message = lambda text, attachments=None, blocks=None: (
        slack_payload.update(attachments=attachments) or True
    )

    notifier.send_multi_account_report(reports)
    result = SupabasePortfolioWriter(client=client).write_daily_snapshot(reports)

    balances = client.operations[0]["params"]["p_balances"]
    assert all(
        (row["total_value"], row["position_value"], row["cash_value"])
        == (10.02, 5.0, 5.02)
        for row in balances
    )
    assert all(
        attachment["text"].splitlines()[0]
        == "$10.02 (Position: $5.00, Cash: $5.02)"
        for attachment in slack_payload["attachments"][1:]
    )
    assert (result.total_value, result.position_value, result.cash_value) == (
        110.22,
        55.0,
        55.22,
    )


def test_rejects_failed_account_without_writing():
    client = FakeClient()
    reports = make_reports()
    reports["golden-cherry"]["error"] = "upstream timeout"

    with pytest.raises(SupabaseWriteError, match="collection error"):
        SupabasePortfolioWriter(client=client).write_daily_snapshot(reports)

    assert client.operations == []


def test_rejects_incomplete_snapshot_without_writing():
    client = FakeClient()
    reports = make_reports()
    del reports["golden-apple (2)"]

    with pytest.raises(SupabaseWriteError, match="일부 DB 계정"):
        SupabasePortfolioWriter(client=client).write_daily_snapshot(reports)

    assert client.operations == []


def test_rejects_missing_valuation_field_without_writing():
    client = FakeClient()
    reports = make_reports()
    del reports["golden-fox"]["cash_balance"]

    with pytest.raises(SupabaseWriteError, match="필수 valuation field"):
        SupabasePortfolioWriter(client=client).write_daily_snapshot(reports)

    assert client.operations == []


def test_compute_period_pnl_picks_earliest_in_window_and_handles_missing():
    history = [
        {"report_date": "2026-06-02", "account_id": "a", "total_value": 100.0},
        {"report_date": "2026-06-28", "account_id": "a", "total_value": 130.0},
        # report_date == as_of is ignored so the live in-memory total is "current".
        {"report_date": "2026-06-30", "account_id": "a", "total_value": 999.0},
    ]
    out = SupabasePortfolioWriter._compute_period_pnl(
        {"a": 150.0, "b": 50.0}, history, date(2026, 6, 30), (7, 30)
    )
    # 7d floor = 6/24; 6/28 is too late to prove seven-day coverage.
    assert out["a"][7] is None
    # 30d floor = 6/1; a baseline on 6/2 is within the one-day allowance.
    assert out["a"][30] == 50.0
    # account "b" has no history -> undefined for every window
    assert out["b"][7] is None
    assert out["b"][30] is None


def test_get_period_pnl_uses_total_change_from_history():
    history = [
        {"report_date": "2026-06-02", "account_id": "golden-cherry", "total_value": 3578.21},
        {"report_date": "2026-06-25", "account_id": "golden-cherry", "total_value": 4222.59},
        # apple-2 only has a row far inside the 30d window, not near its floor.
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
    # No baseline near either floor means the named period is undefined.
    assert pnl["golden-apple (2)"][7] is None
    assert pnl["golden-apple (2)"][30] is None
