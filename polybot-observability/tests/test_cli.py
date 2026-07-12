from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from polybot_observability import ExecutionLedger
from polybot_observability.cli import main


def _catalog_missing_db(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.4,
        requested_size=5,
        result={"success": True, "orderID": "missing-order"},
        simulation=False,
    )
    ledger.record_reconciliation_error(
        submission_id,
        RuntimeError(
            "phase=match_authoritative_order_catalogs "
            "error=ClobResponseUnavailableError "
            "response_shape=sequence(len=0,item_type=none)"
        ),
    )
    return db_path


def test_catalog_gap_cli_lists_then_backs_up_and_resolves(
    tmp_path, monkeypatch, capsys
):
    db_path = _catalog_missing_db(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "polybot-retro",
            "catalog-gaps",
            "--db",
            str(db_path),
            "--strategy",
            "golden-test",
        ],
    )

    main()

    listed = json.loads(capsys.readouterr().out)
    assert [row["order_id"] for row in listed] == ["missing-order"]

    backup_dir = tmp_path / "outside-workspace-backups"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "polybot-retro",
            "resolve-catalog-gaps",
            "--db",
            str(db_path),
            "--strategy",
            "golden-test",
            "--expected-count",
            "1",
            "--confirm",
            "ACKNOWLEDGE_1_CLOB_EVIDENCE_GAPS",
            "--reason",
            "authenticated catalogs reviewed",
            "--backup-dir",
            str(backup_dir),
        ],
    )

    main()

    resolved = json.loads(capsys.readouterr().out)
    assert resolved["resolved"] == 1
    assert resolved["status"] == "OPERATOR_EVIDENCE_GAP"
    backup = Path(resolved["backup"])
    assert backup.parent == backup_dir
    assert (backup / "manifest.json").is_file()
    assert ExecutionLedger(
        db_path, strategy_name="golden-test"
    ).pending_submissions() == []


def test_unresolved_intent_cli_lists_then_backs_up_and_resolves(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_intent(
        token_id="token-secret-free",
        side="BUY",
        requested_price=0.8,
        requested_size=12.5,
        simulation=False,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "polybot-retro",
            "unresolved-intents",
            "--db",
            str(db_path),
            "--strategy",
            "golden-test",
        ],
    )

    main()

    [listed] = json.loads(capsys.readouterr().out)
    assert listed["submission_id"] == submission_id
    assert listed["response_status"] == "INTENT"
    assert listed["order_id"] is None
    assert listed["token_id"] == "token-secret-free"
    assert listed["requested_price"] == 0.8
    assert listed["requested_size"] == 12.5

    backup_dir = tmp_path / "intent-backups"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "polybot-retro",
            "resolve-intent",
            "--db",
            str(db_path),
            "--strategy",
            "golden-test",
            "--submission-id",
            submission_id,
            "--resolution",
            "NO_ORDER_CREATED",
            "--confirm",
            f"RESOLVE_{submission_id}_AS_NO_ORDER_CREATED",
            "--reason",
            "authenticated venue history proves no order",
            "--backup-dir",
            str(backup_dir),
        ],
    )

    main()

    resolved = json.loads(capsys.readouterr().out)
    assert resolved["submission_id"] == submission_id
    assert resolved["resolution"] == "NO_ORDER_CREATED"
    assert resolved["order_id"] is None
    assert (Path(resolved["backup"]) / "manifest.json").is_file()
    assert ledger.unresolved_submission_outcomes() == []


def test_quantity_scale_cli_lists_then_backs_up_and_repairs(
    tmp_path, monkeypatch, capsys
):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.5,
        requested_size=10,
        result={"success": True, "orderID": "scaled", "status": "live"},
        simulation=False,
    )
    ledger.record_order_status(
        submission_id,
        {
            "status": "MATCHED",
            "original_size": "10",
            "size_matched": "10",
            "associate_trades": ["scaled-trade"],
        },
    )
    ledger.record_fill(
        submission_id,
        "scaled",
        {
            "id": "scaled-trade",
            "status": "CONFIRMED",
            "size": "10",
            "price": "0.5",
            "taker_order_id": "scaled",
            "trader_side": "TAKER",
            "fee_rate_bps": "0",
        },
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE order_submissions SET latest_size_matched = "
            "latest_size_matched / 1000000, quantity_scale = NULL"
        )
        connection.execute(
            "UPDATE order_status_events SET original_size = original_size / 1000000, "
            "size_matched = size_matched / 1000000"
        )
        connection.execute("UPDATE order_fills SET size = size / 1000000")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "polybot-retro",
            "quantity-scale-diagnostics",
            "--db",
            str(db_path),
            "--strategy",
            "golden-test",
        ],
    )
    main()
    diagnostics = json.loads(capsys.readouterr().out)
    assert diagnostics[0]["repair_eligible"] is True
    assert diagnostics[0]["rejection_reasons"] == []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "polybot-retro",
            "quantity-scale-repairs",
            "--db",
            str(db_path),
            "--strategy",
            "golden-test",
        ],
    )
    main()
    assert len(json.loads(capsys.readouterr().out)) == 1

    backup_dir = tmp_path / "quantity-backups"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "polybot-retro",
            "repair-quantity-scale",
            "--db",
            str(db_path),
            "--strategy",
            "golden-test",
            "--expected-count",
            "1",
            "--confirm",
            "REPAIR_1_CLOB_QUANTITIES_X1000000",
            "--reason",
            "runtime scale mismatch reviewed",
            "--backup-dir",
            str(backup_dir),
        ],
    )
    main()

    result = json.loads(capsys.readouterr().out)
    assert result["repaired"] == 1
    assert result["completed"] == 1
    assert result["pending"] == 0
    assert result["status"] == "QUANTITY_SCALE_REPAIRED"
    assert (Path(result["backup"]) / "manifest.json").is_file()
