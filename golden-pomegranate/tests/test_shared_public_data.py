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

import zlib
from test_repository import _repository, _bundle, _publish


def test_atomic_census_and_direct_payload_writes_resolve_without_schema_change(tmp_path, shared_payloads):
    repo = _repository(tmp_path)
    with repo._read_connect() as c:
        before = schema(c)
    bundle = _bundle()
    _publish(repo, bundle)
    original = zlib.decompress(bundle["raw_payloads"][0]["payload_blob"])
    direct_id = repo.record_raw_payload(request_id="request-1", kind="gamma_markets_page", content=original, store_blob=True)
    with sqlite3.connect(repo.db_path) as raw:
        assert parse_reference(raw.execute("SELECT payload_blob FROM raw_payloads WHERE payload_id=?", (direct_id,)).fetchone()[0])
        row = raw.execute("SELECT outcome_prices_json,fee_metadata_json,parse_quality_json FROM market_observations").fetchone()
        assert parse_reference(row[0]) and parse_reference(row[1]) and row[2] == "{}"
        assert PayloadReferences(reader=shared_payloads).decode_many([row[1]]) == ["{}"]
        assert not parse_reference(raw.execute("SELECT metadata_json FROM market_metadata_versions").fetchone()[0])
    with repo._read_connect() as c:
        assert schema(c) == before
        row = c.execute("SELECT payload_blob,payload_sha256 FROM raw_payloads WHERE payload_id=?", (direct_id,)).fetchone()
        assert zlib.decompress(row[0]) == original
        assert hashlib.sha256(original).hexdigest() == row[1]
        row = c.execute("SELECT m.outcome_prices_json,o.price_raw FROM market_observations m JOIN outcome_observations o ON o.observation_id=m.observation_id ORDER BY o.outcome_index LIMIT 1").fetchone()
        assert json.loads(row[0])[0] == row[1]
