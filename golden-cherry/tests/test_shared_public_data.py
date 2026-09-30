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
            self.observations = []

        def put_many(self, values):
            hashes = [hashlib.sha256(value).hexdigest() for value in values]
            self.payloads.update(zip(hashes, values))
            return hashes

        def get_many(self, hashes):
            return [self.payloads[digest] for digest in hashes]

        def append_observations(self, observations):
            self.observations.extend(observations)

    for key in ("PUBLIC_MARKET_DATA_DB", "PUBLIC_MARKET_DATA_SOCKET", "PUBLIC_MARKET_DATA_REQUIRED"):
        monkeypatch.delenv(key, raising=False)
    store = Store()
    monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE','fixture-source')
    monkeypatch.setenv('JOB_NAME','polybot-cherry-shadow')
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

from polybot.shadow.collector import ShadowCollector
from polybot.shadow.db import ShadowRepository
from polybot.shadow.transport import CollectionDeadline
from tests.test_shadow_runtime import _config, _markets, FakeGamma, FakeClob, NOW


def test_shadow_source_bytes_and_market_arrays_resolve_but_decisions_remain_local(tmp_path, shared_payloads):
    config = _config(tmp_path)
    repo = ShadowRepository(config.db_path, config)
    repo.record_config()
    with repo.connect(read_only=True) as c:
        before = schema(c)
    repo.record_run_event("run", "STARTED")
    stats = ShadowCollector(config, repo, FakeGamma(_markets()), FakeClob(), CollectionDeadline(240)).collect("run", now=NOW)
    repo.record_run_event("run", "SUCCEEDED", stats)
    with repo.connect(read_only=True) as c:
        assert schema(c) == before
        assert c.execute("SELECT SUM(l.price*l.size) FROM shadow_book_levels l JOIN shadow_book_snapshots s USING(snapshot_id)").fetchone()[0] > 0
        for row in c.execute("SELECT payload_gzip,sha256 FROM shadow_raw_payloads"):
            assert hashlib.sha256(gzip.decompress(row[0])).hexdigest() == row[1]
        row = c.execute("SELECT m.token_ids_json,b.token_id FROM shadow_market_observations m JOIN shadow_book_snapshots b ON b.run_id=m.run_id WHERE m.condition_id='condition-primary' AND b.token_id='token-primary'").fetchone()
        assert row[1] in json.loads(row[0])
    with sqlite3.connect(config.db_path) as raw:
        assert raw.execute("SELECT COUNT(*) FROM main.shadow_book_levels").fetchone()[0] == 0
        assert parse_reference(raw.execute("SELECT payload_gzip FROM shadow_raw_payloads LIMIT 1").fetchone()[0])
        assert parse_reference(raw.execute("SELECT token_ids_json FROM shadow_market_observations LIMIT 1").fetchone()[0])
        assert not parse_reference(raw.execute("SELECT details_json FROM shadow_cell_decisions LIMIT 1").fetchone()[0])
        assert not parse_reference(raw.execute("SELECT config_json FROM shadow_config_versions").fetchone()[0])
