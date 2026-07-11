from __future__ import annotations

import json
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
