"""A matched BUY must survive a wallet-zero cleanup until fill proof arrives."""

import importlib.util
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "reconcile_positions_under_test", ROOT / "tools/reconcile_positions.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def make_db(path, *, latest_status, matched_size, confirmed_size=None):
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY, token_id TEXT, buy_order_id TEXT,
                status TEXT, buy_price REAL, buy_amount REAL, buy_shares REAL,
                question TEXT, exit_reason TEXT, updated_at TEXT
            );
            CREATE TABLE order_submissions (
                submission_id TEXT PRIMARY KEY, order_id TEXT, token_id TEXT,
                side TEXT, latest_order_status TEXT, latest_size_matched REAL,
                needs_reconciliation INTEGER
            );
            CREATE TABLE order_fills (
                submission_id TEXT, side TEXT, status TEXT, size REAL
            );
            """
        )
        db.execute(
            "INSERT INTO trades VALUES (1,'token','order','HOLDING',.95,5,5.263,'game',NULL,NULL)"
        )
        db.execute(
            "INSERT INTO order_submissions VALUES ('submission','order','token','BUY',?,?,0)",
            (latest_status, matched_size),
        )
        if confirmed_size is not None:
            db.execute(
                "INSERT INTO order_fills VALUES ('submission','BUY','CONFIRMED',?)",
                (confirmed_size,),
            )


def run_cleanup(monkeypatch, path, expected_closes):
    monkeypatch.setattr(MODULE, "fetch_positions", lambda _funder: {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reconcile_positions.py",
            "--db",
            str(path),
            "--funder",
            "0x" + "a" * 40,
            "--execute",
            "--confirm",
            f"CLOSE_{expected_closes}",
        ],
    )
    assert MODULE.main() == 0
    with sqlite3.connect(path) as db:
        return db.execute("SELECT status,exit_reason FROM trades WHERE id=1").fetchone()


def test_wallet_zero_does_not_mark_matched_buy_unfilled_without_local_fill(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "matched.db"
    make_db(path, latest_status="MATCHED", matched_size=5.263)

    assert run_cleanup(monkeypatch, path, 0) == ("HOLDING", None)
    assert "MATCHED/양수 매칭·대사중, 지갑 X → 보존 :    1건" in capsys.readouterr().out


def test_late_confirmed_fill_uses_exact_buy_order_to_close_offledger(
    tmp_path, monkeypatch
):
    path = tmp_path / "confirmed.db"
    make_db(path, latest_status="MATCHED", matched_size=5.263, confirmed_size=5.263)

    assert run_cleanup(monkeypatch, path, 1) == (
        "COMPLETED",
        "wallet_reconciled_closed_offledger",
    )


def test_terminal_zero_size_without_fill_can_still_close_unfilled(tmp_path, monkeypatch):
    path = tmp_path / "zero.db"
    make_db(path, latest_status="CANCELED", matched_size=0)

    assert run_cleanup(monkeypatch, path, 1) == (
        "UNFILLED",
        "wallet_reconciled_no_fill_evidence",
    )


def test_existing_unfilled_confirmed_buy_blocks_further_cleanup(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "historical-conflict.db"
    make_db(path, latest_status="MATCHED", matched_size=5.263, confirmed_size=5.263)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE trades SET status='UNFILLED' WHERE id=1")
    monkeypatch.setattr(MODULE, "fetch_positions", lambda _funder: {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reconcile_positions.py",
            "--db",
            str(path),
            "--funder",
            "0x" + "a" * 40,
            "--execute",
            "--confirm",
            "CLOSE_0",
        ],
    )

    assert MODULE.main() == 3
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM trades WHERE id=1").fetchone()[0] == "UNFILLED"
    assert "기존 UNFILLED + exact CONFIRMED BUY 모순:    1건" in capsys.readouterr().out
