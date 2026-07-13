"""Sanitized local daily evidence persistence."""

import json
import os
import sqlite3
import stat
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from polybot_reporter.storage.evidence_store import DailyEvidenceStore, EvidenceStoreError

DISPLAY_NAMES = [
    "golden-apple (1)",
    "golden-banana",
    "golden-cherry",
    "golden-apple (2)",
    "golden-eco",
    "golden-fox",
    "golden-lion",
    "golden-tiger",
    "golden-wolf",
]


def summary(position=None):
    positions = [] if position is None else [position]
    return {
        "address": "0x-wallet-must-not-persist",
        "private_key": "must-not-persist",
        "total_value": 16.0,
        "position_value": 10.0,
        "cash_balance": 6.0,
        "num_positions": len(positions),
        "positions": positions,
    }


def test_complete_run_persists_required_position_fields_without_wallet(tmp_path):
    position = {
        "conditionId": "condition-1",
        "asset": "token-yes",
        "outcome": "Yes",
        "size": "12.5",
        "avgPrice": "0.40",
        "currentValue": "6.25",
        "cashPnl": "1.25",
        "realizedPnl": "0.50",
        "redeemable": True,
        "endDate": "2026-08-01T00:00:00Z",
        "wallet": "0x-position-wallet-must-not-persist",
    }
    reports = {name: summary(position if name == "golden-fox" else None) for name in DISPLAY_NAMES}
    db_path = tmp_path / "daily-evidence.sqlite3"

    result = DailyEvidenceStore(db_path).record_run(
        reports,
        expected_display_names=DISPLAY_NAMES,
        report_date=date(2026, 7, 11),
        reported_at=datetime(2026, 7, 11, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    assert result.status == "COMPLETE"
    assert result.account_count == 9
    assert result.position_count == 1
    with sqlite3.connect(db_path) as connection:
        run = connection.execute(
            "SELECT status, expected_account_count, observed_account_count, "
            "failed_account_ids_json FROM evidence_report_runs"
        ).fetchone()
        row = connection.execute(
            "SELECT account_id, condition_id, asset, outcome, size, avg_price, "
            "current_value, cash_pnl, realized_pnl, redeemable, end_date "
            "FROM evidence_positions"
        ).fetchone()
        delivery = connection.execute(
            "SELECT supabase_status, slack_status, delivery_status "
            "FROM evidence_delivery_status"
        ).fetchone()

    assert run == ("COMPLETE", 9, 9, "[]")
    assert row == (
        "golden-fox",
        "condition-1",
        "token-yes",
        "Yes",
        12.5,
        0.4,
        6.25,
        1.25,
        0.5,
        1,
        "2026-08-01T00:00:00Z",
    )
    assert delivery == ("PENDING", "PENDING", "PENDING")
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    database_bytes = db_path.read_bytes()
    assert b"wallet-must-not-persist" not in database_bytes
    assert b"must-not-persist" not in database_bytes


def test_failed_run_records_completeness_without_synthetic_failed_account(tmp_path):
    reports = {name: summary() for name in DISPLAY_NAMES}
    reports["golden-fox"] = {"error": "upstream timeout", "total_value": 0}
    db_path = tmp_path / "daily-evidence.sqlite3"

    result = DailyEvidenceStore(db_path).record_run(
        reports,
        expected_display_names=DISPLAY_NAMES,
        failed_display_names=["golden-fox"],
    )

    assert result.status == "FAILED"
    assert result.account_count == 8
    with sqlite3.connect(db_path) as connection:
        run = connection.execute(
            "SELECT status, observed_account_count, failed_account_ids_json "
            "FROM evidence_report_runs"
        ).fetchone()
        account_ids = {
            row[0]
            for row in connection.execute("SELECT account_id FROM evidence_account_snapshots")
        }
        delivery = connection.execute(
            "SELECT supabase_status, slack_status, delivery_status "
            "FROM evidence_delivery_status"
        ).fetchone()

    assert run[0:2] == ("FAILED", 8)
    assert json.loads(run[2]) == ["golden-fox"]
    assert "golden-fox" not in account_ids
    assert delivery == ("SKIPPED", "SKIPPED", "NOT_ATTEMPTED")


def test_explicit_failure_excludes_otherwise_success_shaped_report(tmp_path):
    reports = {name: summary() for name in DISPLAY_NAMES}
    db_path = tmp_path / "daily-evidence.sqlite3"

    result = DailyEvidenceStore(db_path).record_run(
        reports,
        expected_display_names=DISPLAY_NAMES,
        failed_display_names=["golden-fox"],
    )

    assert result.status == "FAILED"
    assert result.account_count == 8
    with sqlite3.connect(db_path) as connection:
        ids = {
            row[0]
            for row in connection.execute("SELECT account_id FROM evidence_account_snapshots")
        }
    assert "golden-fox" not in ids


def test_incomplete_report_cannot_be_recorded_as_complete(tmp_path):
    reports = {name: summary() for name in DISPLAY_NAMES}
    del reports["golden-fox"]["cash_balance"]

    result = DailyEvidenceStore(tmp_path / "daily-evidence.sqlite3").record_run(
        reports,
        expected_display_names=DISPLAY_NAMES,
    )

    assert result.status == "FAILED"
    assert result.account_count == 8


def test_fresh_account_snapshot_schema_requires_money_values(tmp_path):
    db_path = tmp_path / "daily-evidence.sqlite3"
    reports = {name: summary() for name in DISPLAY_NAMES}
    DailyEvidenceStore(db_path).record_run(reports, expected_display_names=DISPLAY_NAMES)

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]: row[3]
            for row in connection.execute("PRAGMA table_info(evidence_account_snapshots)")
        }
    assert columns["total_value"] == 1
    assert columns["position_value"] == 1
    assert columns["cash_value"] == 1


def test_delivery_provenance_distinguishes_collection_from_delivery(tmp_path):
    db_path = tmp_path / "daily-evidence.sqlite3"
    reports = {name: summary() for name in DISPLAY_NAMES}
    store = DailyEvidenceStore(db_path)
    run = store.record_run(reports, expected_display_names=DISPLAY_NAMES)

    assert store.mark_delivery(run.run_id, "supabase", "SUCCESS") == "PENDING"
    assert store.mark_delivery(run.run_id, "slack", "SUCCESS") == "COMPLETE"

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT supabase_status, slack_status, delivery_status, finalized_at "
            "FROM evidence_delivery_status WHERE run_id = ?",
            (run.run_id,),
        ).fetchone()
    assert row[:3] == ("SUCCESS", "SUCCESS", "COMPLETE")
    assert row[3] is not None


def test_delivery_failure_is_sanitized_and_terminal(tmp_path):
    db_path = tmp_path / "daily-evidence.sqlite3"
    reports = {name: summary() for name in DISPLAY_NAMES}
    store = DailyEvidenceStore(db_path)
    run = store.record_run(reports, expected_display_names=DISPLAY_NAMES)
    secret = "sb_" + "secret_" + "delivery_fixture_must_not_persist"

    assert (
        store.mark_delivery(
            run.run_id, "supabase", "FAILED", error=f"Authorization={secret}"
        )
        == "FAILED"
    )
    assert store.mark_delivery(run.run_id, "slack", "SKIPPED") == "FAILED"

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT supabase_error, delivery_status FROM evidence_delivery_status "
            "WHERE run_id = ?",
            (run.run_id,),
        ).fetchone()
    assert row[1] == "FAILED"
    assert secret not in row[0]
    assert secret.encode() not in db_path.read_bytes()


def test_simulation_is_explicitly_not_attempted(tmp_path):
    db_path = tmp_path / "daily-evidence.sqlite3"
    reports = {name: summary() for name in DISPLAY_NAMES}

    run = DailyEvidenceStore(db_path).record_run(
        reports,
        expected_display_names=DISPLAY_NAMES,
        delivery_enabled=False,
    )

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT supabase_status, slack_status, delivery_status "
            "FROM evidence_delivery_status WHERE run_id = ?",
            (run.run_id,),
        ).fetchone()
    assert row == ("SKIPPED", "SKIPPED", "NOT_ATTEMPTED")


def test_local_account_money_uses_same_canonical_cent_contract(tmp_path):
    db_path = tmp_path / "daily-evidence.sqlite3"
    reports = {name: summary() for name in DISPLAY_NAMES}
    for report in reports.values():
        report.update(total_value=10.02, position_value=5.0, cash_balance=5.0)

    DailyEvidenceStore(db_path).record_run(
        reports, expected_display_names=DISPLAY_NAMES
    )

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT total_value, position_value, cash_value "
            "FROM evidence_account_snapshots"
        ).fetchall()
    assert rows == [(10.02, 5.0, 5.02)] * 9


def test_evidence_write_fails_if_mode_0600_cannot_be_enforced(monkeypatch, tmp_path):
    reports = {name: summary() for name in DISPLAY_NAMES}

    def fail_chmod(*_args):
        raise OSError("fixture chmod failure")

    monkeypatch.setattr(os, "fchmod", fail_chmod)

    with pytest.raises(EvidenceStoreError, match="0600"):
        DailyEvidenceStore(tmp_path / "daily-evidence.sqlite3").record_run(
            reports, expected_display_names=DISPLAY_NAMES
        )


def test_evidence_store_rejects_symlink_without_chmodding_target(tmp_path):
    reports = {name: summary() for name in DISPLAY_NAMES}
    target = tmp_path / "target.sqlite3"
    target.write_bytes(b"")
    target.chmod(0o644)
    link = tmp_path / "daily-evidence.sqlite3"
    link.symlink_to(target)

    with pytest.raises(EvidenceStoreError, match="0600"):
        DailyEvidenceStore(link).record_run(
            reports, expected_display_names=DISPLAY_NAMES
        )

    assert stat.S_IMODE(target.stat().st_mode) == 0o644
