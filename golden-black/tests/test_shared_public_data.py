"""Exact public body references preserve this collector's evidence contract."""

import gzip
import hashlib
import json
import sqlite3

import pytest

from polybot_observability import market_data_refs, market_data_sqlite, market_data_levels
from polybot_observability.market_data_refs import PayloadReferences, parse_reference


@pytest.fixture
def shared_payloads(monkeypatch):
    class Store:
        def __init__(self):
            self.payloads = {}

        def put_many(self, values):
            hashes = [hashlib.sha256(value).hexdigest() for value in values]
            self.payloads.update(zip(hashes, values))
            return hashes

        def get_many(self, hashes):
            return [self.payloads[digest] for digest in hashes]

    for key in ("PUBLIC_MARKET_DATA_DB", "PUBLIC_MARKET_DATA_SOCKET", "PUBLIC_MARKET_DATA_REQUIRED"):
        monkeypatch.delenv(key, raising=False)
    store = Store()
    codec = PayloadReferences(store, store)
    monkeypatch.setattr(market_data_refs, "configured_references", lambda: codec)
    monkeypatch.setattr(market_data_sqlite, "configured_references", lambda: codec)
    monkeypatch.setattr(market_data_levels, "configured_references", lambda: codec)
    return store


def schema(connection):
    auxiliary = market_data_levels.validate_level_layout(connection)
    return [tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
    ) if row[1] not in auxiliary]

from dataclasses import replace
from datetime import datetime, timezone
from polybot.collector import Collector
from polybot.config import load_config
from polybot.db.repository import ResearchRepository
from test_collector import ROOT, FakeGamma, FakeClob


def test_collector_public_bodies_preserve_hashes_schema_and_decisions(tmp_path, shared_payloads):
    config = replace(load_config(ROOT / "config.yaml"), db_path=tmp_path / "trades_sim.db")
    repo = ResearchRepository(config.db_path, busy_timeout_ms=1000, data_contract=config.trading.data_contract)
    with repo.connect() as c:
        before = schema(c)
    Collector(config, repo, FakeGamma(), FakeClob()).collect("run-1", now=datetime(2026, 8, 21, tzinfo=timezone.utc))
    with sqlite3.connect(config.db_path) as raw:
        assert raw.execute("SELECT COUNT(*) FROM main.orderbook_levels").fetchone()[0] == 0
        assert parse_reference(raw.execute("SELECT payload_gzip FROM raw_payloads LIMIT 1").fetchone()[0])
        assert parse_reference(raw.execute("SELECT token_ids_json FROM market_observations").fetchone()[0])
        private = raw.execute("SELECT details_json FROM signal_decisions LIMIT 1").fetchone()[0]
        assert not parse_reference(private)
    with repo.connect() as c:
        assert schema(c) == before
        assert c.execute("SELECT COUNT(*) FROM orderbook_levels").fetchone()[0] == 4
        assert c.execute("SELECT SUM(l.price*l.size) FROM orderbook_levels l JOIN orderbook_snapshots s USING(snapshot_id)").fetchone()[0] > 0
        for row in c.execute("SELECT payload_gzip,sha256 FROM raw_payloads"):
            assert hashlib.sha256(gzip.decompress(row[0])).hexdigest() == row[1]
        row = c.execute("SELECT m.token_ids_json AS tokens,s.token_id FROM market_observations m JOIN orderbook_snapshots s ON s.run_id=m.run_id LIMIT 1").fetchone()
        assert row[1] in json.loads(row[0])
    assert private.encode() not in shared_payloads.payloads.values()
