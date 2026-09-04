"""Persisted snapshot-pair evidence contracts for future Papaya trades."""

from __future__ import annotations

import csv
import hashlib
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from polybot.db.models import MarketSnapshot, TradeStatus, init_database
from polybot.db.repository import TradeRepository


def test_incomplete_legacy_trade_table_is_rejected_instead_of_partly_migrated(tmp_path):
    db_path = tmp_path / "legacy-trades.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO trades (id) VALUES (1)")
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="incompatible trades schema"):
        init_database(str(db_path))


def test_repository_retention_preserves_explicit_and_legacy_snapshot_pairs(
    tmp_path,
):
    Session = init_database(
        str(tmp_path / "lineage-retention.db"),
        activate_compact_on_create=False,
    )
    session = Session()
    repository = TradeRepository(session)
    old = datetime.utcnow() - timedelta(days=61)

    explicit_prior = MarketSnapshot(
        condition_id="explicit", probability=0.94, timestamp=old
    )
    explicit_entry = MarketSnapshot(
        condition_id="explicit",
        probability=0.95,
        timestamp=old + timedelta(minutes=5),
    )
    legacy_prior = MarketSnapshot(
        condition_id="legacy", probability=0.94, timestamp=old
    )
    legacy_entry = MarketSnapshot(
        condition_id="legacy",
        probability=0.95,
        timestamp=old + timedelta(minutes=5),
    )
    unrelated = MarketSnapshot(
        condition_id="unrelated", probability=0.50, timestamp=old
    )
    session.add_all(
        [explicit_prior, explicit_entry, legacy_prior, legacy_entry, unrelated]
    )
    session.commit()
    repository.create_trade(
        condition_id="explicit",
        token_id="explicit-token",
        outcome="Yes",
        prior_snapshot_id_at_entry=explicit_prior.id,
        entry_snapshot_id=explicit_entry.id,
    )
    repository.create_trade(
        condition_id="legacy",
        token_id="legacy-token",
        outcome="Yes",
        prior_snapshot_id_at_entry=None,
        entry_snapshot_id=legacy_entry.id,
    )

    assert repository.cleanup_old_snapshots(days=60) == 1
    remaining = {
        row.id for row in session.query(MarketSnapshot).order_by(MarketSnapshot.id)
    }
    assert remaining == {
        explicit_prior.id,
        explicit_entry.id,
        legacy_prior.id,
        legacy_entry.id,
    }
    session.close()


def test_trade_csv_atomically_upgrades_legacy_header_before_append(tmp_path):
    Session = init_database(str(tmp_path / "csv-upgrade.db"))
    session = Session()
    repository = TradeRepository(session)
    timestamp = datetime(2026, 7, 20, 12, 0, 0)
    csv_path = tmp_path / "trades_2026-07.csv"
    legacy_headers = [
        "id",
        "condition_id",
        "yes_price_at_exit",
        "stop_price_at_entry",
        "hours_until_resolution_at_buy",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=legacy_headers)
        writer.writeheader()
        writer.writerow(
            {
                "id": 1,
                "condition_id": "legacy",
                "yes_price_at_exit": 0.89,
                "stop_price_at_entry": 0.90,
                "hours_until_resolution_at_buy": 12,
            }
        )

    repository.append_trade_to_csv(
        SimpleNamespace(
            id=2,
            condition_id="new",
            sell_timestamp=timestamp,
            prior_snapshot_id_at_entry=101,
            entry_snapshot_id=102,
        ),
        tmp_path,
    )

    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        assert "prior_snapshot_id_at_entry" in (reader.fieldnames or [])
        assert "entry_snapshot_id" in (reader.fieldnames or [])
    assert rows[0]["condition_id"] == "legacy"
    assert rows[0]["stop_price_at_entry"] == "0.9"
    assert rows[0]["prior_snapshot_id_at_entry"] == ""
    assert rows[1]["condition_id"] == "new"
    assert rows[1]["prior_snapshot_id_at_entry"] == "101"
    assert rows[1]["entry_snapshot_id"] == "102"
    session.close()


def test_clob_resolution_observation_is_append_only_and_commits_with_trade(tmp_path):
    Session = init_database(str(tmp_path / "resolution.db"))
    session = Session()
    repository = TradeRepository(session)
    trade = repository.create_trade(
        condition_id="condition",
        token_id="token-b",
        outcome="Team B",
        status=TradeStatus.HOLDING,
    )
    evidence_json = (
        '{"closed":true,"tokens":['
        '{"outcome":"Team A","price":0,"token_id":"token-a","winner":false},'
        '{"outcome":"Team B","price":1,"token_id":"token-b","winner":true}]}'
    )
    evidence_sha256 = hashlib.sha256(evidence_json.encode()).hexdigest()
    observation = repository.stage_clob_resolution_observation(
        trade_id=trade.id,
        condition_id="condition",
        observed_at=datetime(2026, 8, 21, 11, 0),
        winner_index=1,
        winner_token_id="token-b",
        winner_outcome="Team B",
        selected_token_id="token-b",
        selected_outcome="Team B",
        selected_payout=1,
        evidence_sha256=evidence_sha256,
        evidence_json=evidence_json,
    )
    repository.update_trade(trade.id, status=TradeStatus.RESOLVED)

    row = session.execute(
        text(
            "SELECT trade_id, selected_payout, evidence_sha256 "
            "FROM resolution_observations WHERE resolution_id=:resolution_id"
        ),
        {"resolution_id": observation.resolution_id},
    ).one()
    assert row == (trade.id, 1.0, evidence_sha256)
    with pytest.raises(DatabaseError, match="append-only evidence"):
        session.execute(
            text(
                "UPDATE resolution_observations SET selected_payout=0 "
                "WHERE resolution_id=:resolution_id"
            ),
            {"resolution_id": observation.resolution_id},
        )
        session.commit()
    session.rollback()
    session.close()


def test_clob_resolution_observation_rejects_tampered_evidence(tmp_path):
    Session = init_database(str(tmp_path / "tampered-resolution.db"))
    session = Session()
    repository = TradeRepository(session)
    trade = repository.create_trade(
        condition_id="condition",
        token_id="token-b",
        outcome="Team B",
        status=TradeStatus.HOLDING,
    )
    evidence_json = (
        '{"closed":true,"tokens":['
        '{"outcome":"Team A","price":0,"token_id":"token-a","winner":false},'
        '{"outcome":"Team B","price":1,"token_id":"token-b","winner":true}]}'
    )

    with pytest.raises(ValueError, match="does not match JSON"):
        repository.stage_clob_resolution_observation(
            trade_id=trade.id,
            condition_id="condition",
            observed_at=datetime(2026, 8, 21, 11, 0),
            winner_index=1,
            winner_token_id="token-b",
            winner_outcome="Team B",
            selected_token_id="token-b",
            selected_outcome="Team B",
            selected_payout=1,
            evidence_sha256="0" * 64,
            evidence_json=evidence_json,
        )

    assert (
        session.execute(
            text("SELECT count(*) FROM resolution_observations")
        ).scalar_one()
        == 0
    )
    session.close()
