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

from polybot.config import PROJECT_ROOT, load_config
from polybot.db.repository import ResearchRepository


def test_public_raw_bytes_decode_but_membership_judgments_stay_local(tmp_path, shared_payloads):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    repo = ResearchRepository(tmp_path / "trades_sim.db")
    repo.initialize(config)
    repo.register_config(config, git_commit=None)
    with repo._connect() as c:
        before = schema(c)
    original = b'{"asset_id":"token","bids":[],"asks":[]}'
    packed = gzip.compress(original, mtime=0)
    repo.record_api_request({
        "request_id": "q", "logical_request_id": "l", "run_id": "r", "request_kind": "clob_universe_books",
        "attempt_number": 1, "method": "POST", "url": "https://example.test/books", "params_json": "{}",
        "started_at": "2026-08-23T20:00:00Z", "completed_at": "2026-08-23T20:00:01Z",
        "timeout_connect_seconds": 1, "timeout_read_seconds": 1, "status": "SUCCESS", "retryable": 0,
    })
    repo._insert_one("raw_payloads", {
        "payload_id": "p", "run_id": "r", "request_id": "q", "logical_request_id": "l",
        "payload_kind": "clob_universe_books", "content_encoding": "gzip",
        "payload_sha256": hashlib.sha256(original).hexdigest(), "uncompressed_bytes": len(original),
        "compressed_bytes": len(packed), "payload_blob": packed, "recorded_at": "2026-08-23T20:00:01Z",
    })
    with repo._connect() as c:
        assert schema(c) == before
        row = c.execute("SELECT p.payload_blob,p.payload_sha256,a.params_json FROM raw_payloads p JOIN api_requests a USING(request_id)").fetchone()
        assert row[0] == packed and json.loads(gzip.decompress(row[0]))["asset_id"] == "token"
        assert hashlib.sha256(gzip.decompress(row[0])).hexdigest() == row[1]
        assert row[2] == "{}"
    with sqlite3.connect(repo.db_path) as raw:
        assert parse_reference(raw.execute("SELECT payload_blob FROM raw_payloads").fetchone()[0])
        assert not parse_reference(raw.execute("SELECT config_json FROM research_config_versions").fetchone()[0])
    from polybot_observability.market_data_policy import public_columns
    assert "membership_blob" not in public_columns("golden-raspberry", "market_sweeps")


def test_public_levels_leave_experimental_flags_local_and_keep_join_semantics(tmp_path, shared_payloads):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    repo = ResearchRepository(tmp_path / "levels.db")
    repo.initialize(config)
    with repo._connect() as c:
        repo._insert_many(c, "orderbook_snapshots", [{
            "snapshot_id": "s", "run_id": "r", "config_hash": config.config_hash,
            "strategy_source_digest": config.trading.strategy_source_digest,
            "token_id": "t", "snapshot_role": "FOLLOWUP_ONLY", "observed_at": "2026-08-23T20:00:01Z",
            "raw_book_sha256": "a" * 64, "bid_level_count": 0, "ask_level_count": 1,
            "one_tick_spread": 1, "entry_complete": 1, "quote_eligible": 1, "candidate_up": 0,
        }])
        repo._insert_many(c, "orderbook_levels", [{
            "level_id": "l", "snapshot_id": "s", "side": "ASK", "level_index": 0,
            "price": .5, "size": 10., "in_near_touch_window": 1, "used_for_entry": 0,
        }])
        c.commit()
    with repo._connect() as c:
        row = c.execute("SELECT s.token_id,l.price,l.size,l.in_near_touch_window,l.used_for_entry FROM orderbook_levels l JOIN orderbook_snapshots s USING(snapshot_id)").fetchone()
        assert tuple(row) == ("t", .5, 10., 1, 0)
    with sqlite3.connect(repo.db_path) as raw:
        assert raw.execute("SELECT COUNT(*) FROM main.orderbook_levels").fetchone()[0] == 0
        flags = json.loads(raw.execute("SELECT retained_json FROM _market_data_level_bindings").fetchone()[0])
        assert flags == {"in_near_touch_window": 1, "used_for_entry": 0}
        reference = raw.execute("SELECT body_ref FROM _market_data_level_groups").fetchone()[0]
    public = json.loads(PayloadReferences(reader=shared_payloads).decode_many([reference])[0])
    assert public == [{"level_index": 0, "price": .5, "side": "ASK", "size": 10.}]
