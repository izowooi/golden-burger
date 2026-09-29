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

from contextlib import closing
from polybot.evidence import Repository
from test_evidence import CONTRACT, CONFIG, T0, T1, T2, event, book, feature, receipt


def test_public_guava_bytes_are_shared_without_moving_derived_envelopes(tmp_path, shared_payloads):
    path = tmp_path / "trades_sim.db"
    with closing(Repository(path, CONTRACT)) as repo:
        before = schema(repo.connection)
        repo.start_run("run", T0, CONFIG)
        sample = book()
        repo.record_request("run", receipt(), sample["raw"])
        repo.publish_cycle("run", T2, [event()], [sample], [feature()], {"cursor_complete": True})
        assert schema(repo.connection) == before
        row = repo.connection.execute("SELECT b.raw_gzip,b.raw_sha256,r.payload_gzip,b.book_json,b.fee_evidence_json FROM book_attempts b JOIN source_requests r USING(run_id,request_id) WHERE b.token_id='public-yes'").fetchone()
        assert row[0] == row[2]
        assert json.loads(gzip.decompress(row[0])) == sample["raw"]
        assert hashlib.sha256(gzip.decompress(row[0])).hexdigest() == row[1]
        assert json.loads(row[3]) == sample and json.loads(row[4]) == sample["fee_evidence"]
        assert repo.previous_events()["event-1"]["eligible"] is True
    with sqlite3.connect(path) as raw:
        row = raw.execute("SELECT raw_gzip,book_json,fee_evidence_json FROM book_attempts WHERE token_id='public-yes'").fetchone()
        assert parse_reference(row[0]) and not parse_reference(row[1]) and not parse_reference(row[2])
        assert not parse_reference(raw.execute("SELECT config_json FROM strategy_configs").fetchone()[0])
    with closing(Repository(path, CONTRACT, read_only=True)) as reopened:
        assert reopened.status()["latest_run"]["status"] == "SUCCEEDED"
