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

from polybot.db.repository import ResearchRepository
from polybot.followup_collector import decode_compact_book
from polybot.v1_source import V1SourceReader
from tests.support import minimal_bundle
from tests.followup_support import build_v1_handoff, build_followup_evidence


def test_source_publication_preserves_payload_and_private_membership(config, shared_payloads):
    repo = ResearchRepository(config.db_path)
    repo.initialize(config)
    repo.register_config(config, git_commit=None)
    with repo._read_connect() as c:
        before = schema(c)
    bundle = minimal_bundle(config, repo)
    original = bundle["raw_payloads"][0]["payload_blob"]
    membership = bundle["membership"]["membership_blob"]
    repo.publish_cycle(bundle)
    with sqlite3.connect(config.db_path) as raw:
        assert parse_reference(raw.execute("SELECT payload_blob FROM raw_payloads").fetchone()[0])
        assert raw.execute("SELECT membership_blob FROM market_membership_blobs").fetchone()[0] == membership
        assert not parse_reference(raw.execute("SELECT config_json FROM research_config_versions").fetchone()[0])
    with repo._read_connect() as c:
        assert schema(c) == before
        row = c.execute("SELECT p.payload_blob,p.payload_sha256,l.market_count FROM raw_payloads p JOIN market_page_lineage l ON l.raw_payload_id=p.payload_id").fetchone()
        assert row[0] == original and row[2] == 0
        assert hashlib.sha256(gzip.decompress(row[0])).hexdigest() == row[1]
        assert json.loads(gzip.decompress(row[0]))["data"] == []


def test_v1_seed_and_followup_book_join_receive_decoded_evidence(config, followup_config, shared_payloads):
    build_v1_handoff(config)
    snapshot = V1SourceReader(followup_config.trading.v1_source).capture()
    evidence = build_followup_evidence(followup_config, snapshot)
    repo = evidence.repository
    with repo.read_connect() as c:
        rows = c.execute("SELECT b.book_blob,b.book_sha256,p.episode_id FROM compact_books b JOIN episode_path_observations p ON p.book_id=b.book_id").fetchall()
        assert len(rows) == 4
        for row in rows:
            decoded = decode_compact_book(row[0], expected_sha256=row[1])
            assert decoded["bids"] and row[2]
    with sqlite3.connect(followup_config.db_path) as raw:
        assert parse_reference(raw.execute("SELECT book_blob FROM compact_books").fetchone()[0])
        assert not parse_reference(raw.execute("SELECT episode_json FROM imported_episodes LIMIT 1").fetchone()[0])
