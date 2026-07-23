"""Persisted snapshot-pair evidence contracts for future Queen trades."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import inspect, text

from polybot.db.models import MarketSnapshot, init_database
from polybot.db.repository import TradeRepository


def test_legacy_trade_table_adds_nullable_prior_snapshot_lineage(tmp_path):
    db_path = tmp_path / "legacy-trades.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO trades (id) VALUES (1)")
    connection.commit()
    connection.close()

    Session = init_database(str(db_path))
    session = Session()
    columns = {
        column["name"] for column in inspect(session.get_bind()).get_columns("trades")
    }

    assert "prior_snapshot_id_at_entry" in columns
    assert "entry_snapshot_id" in columns
    row = session.execute(
        text(
            "SELECT prior_snapshot_id_at_entry, entry_snapshot_id "
            "FROM trades WHERE id = 1"
        )
    ).one()
    assert row == (None, None)
    session.close()


def test_repository_retention_preserves_explicit_and_legacy_snapshot_pairs(
    tmp_path,
):
    Session = init_database(str(tmp_path / "lineage-retention.db"))
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
