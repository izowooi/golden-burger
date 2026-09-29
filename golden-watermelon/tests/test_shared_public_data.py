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

from polybot.db.raw_repository import RawRepository, fingerprint
from test_repository import repository


def test_public_main_and_sidecar_payloads_keep_original_hash_and_schema(tmp_path, shared_payloads):
    parent = tmp_path / "trades_sim.db"
    repo = repository(parent)
    original = b'{"public":"exact original bytes"}'
    payload = repo.payload_row(run_id="r", kind="PUBLIC", request_id="q", observed_at="2026-09-08T00:00:00Z", raw=original)
    with repo.connect() as c:
        before = schema(c)
        repo._insert_many(c, "raw_payloads", [payload])
        repo._insert_many(c, "orderbook_snapshots", [{
            "snapshot_id": "s", "run_id": "r", "token_id": "t", "request_id": "q",
            "observed_at": "2026-09-08T00:00:00Z", "raw_book_sha256": "a" * 64,
            "bid_level_count": 0, "ask_level_count": 1,
        }])
        repo._insert_many(c, "orderbook_levels", [{
            "level_id": "level", "snapshot_id": "s", "side": "ASK",
            "level_index": 0, "price": .72, "size": 10.,
        }])
    with repo.connect() as c:
        assert schema(c) == before
        row = c.execute("SELECT payload_gzip,sha256 FROM raw_payloads").fetchone()
        assert gzip.decompress(row[0]) == original
        assert hashlib.sha256(original).hexdigest() == row[1]
    with sqlite3.connect(parent) as raw:
        assert parse_reference(raw.execute("SELECT payload_gzip FROM raw_payloads").fetchone()[0])
        assert raw.execute("SELECT COUNT(*) FROM main.orderbook_levels").fetchone()[0] == 0
    repo.close()
    repo = repository(parent)
    with repo.connect() as c:
        assert c.execute("SELECT SUM(price*size) FROM orderbook_levels").fetchone()[0] == pytest.approx(7.2)
    repo.close()
    sidecar = RawRepository(parent)
    try:
        before = fingerprint(sidecar.connection)
        with sidecar.transaction() as c:
            sidecar.insert(c, "raw_events", {
                "run_id": "r", "event_id": "e", "family": "soccer", "metadata_status": "OBSERVED",
                "metadata_received_at": "2026-09-08T00:00:00Z", "metadata_request_id": "q",
                "source_kind": "FIXTURE", "source_ref_json": '{"owned":"lineage"}',
                "event_json": '{"id":"e","markets":[]}', "slots_json": "[]",
                "identity_valid": 1, "lifecycle_state": "TRACKING", "terminal_json": None, "missing_count": 0,
            })
        assert fingerprint(sidecar.connection) == before
        row = sidecar.connection.execute("SELECT event_json,source_ref_json FROM raw_events").fetchone()
        assert json.loads(row[0])["id"] == "e" and json.loads(row[1]) == {"owned": "lineage"}
        with sqlite3.connect(sidecar.path) as raw:
            row = raw.execute("SELECT event_json,source_ref_json FROM raw_events").fetchone()
            assert parse_reference(row[0]) and not parse_reference(row[1])
    finally:
        sidecar.close()
    # Existing fingerprint checks must accept the same schema after public indirection.
    RawRepository(parent).close()
