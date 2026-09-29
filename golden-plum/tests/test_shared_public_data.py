"""Public bodies round-trip through the shared store without moving ledgers."""
from __future__ import annotations

from decimal import Decimal
import hashlib
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Integer, LargeBinary, MetaData, String, Table, Text, create_engine, select, text
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.exc import StatementError
from sqlalchemy.schema import CreateTable

from polybot.api.clob_client import ClobClientWrapper
from polybot.db.models import MarketCatalog, MarketSnapshot, Trade, init_database
from polybot_observability import market_data_sqlalchemy as adapter
from polybot_observability import market_data_sqlite
from polybot_observability.market_data_refs import (
    MissingMarketDataConfiguration, PayloadReferences, parse_reference,
)


class MemoryStore:
    def __init__(self):
        self.payloads = {}

    def put_many(self, values):
        hashes = [hashlib.sha256(value).hexdigest() for value in values]
        self.payloads.update(zip(hashes, values))
        return hashes

    def get_many(self, hashes):
        return [self.payloads[digest] for digest in hashes]


@pytest.fixture(autouse=True)
def explicit_test_environment(monkeypatch):
    for name in ("PUBLIC_MARKET_DATA_REQUIRED", "PUBLIC_MARKET_DATA_DB", "PUBLIC_MARKET_DATA_SOCKET"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def shared_codec(monkeypatch):
    store = MemoryStore()
    codec = PayloadReferences(store, store)
    monkeypatch.setattr(adapter, "configured_references", lambda: codec)
    monkeypatch.setattr(market_data_sqlite, "configured_references", lambda: codec)
    return codec, store


@pytest.mark.parametrize("type_,body", [
    (String(73), '{"경기":"source","price":"0.72"}'),
    (Text(collation="BINARY"), ""),
    (LargeBinary(128), b"\x00\x1f\x8b\xff"),
])
def test_real_ddl_bind_read_null_and_private_type_preservation(tmp_path, shared_codec, type_, body):
    codec, store = shared_codec
    metadata = MetaData()
    table = Table("market_snapshots", metadata,
                  Column("id", Integer, primary_key=True),
                  Column("book_json", type_),
                  Column("execution_capacity_json", String(137)))
    original_private = table.c.execution_capacity_json.type
    before = str(CreateTable(table).compile(dialect=sqlite_dialect()))
    assert adapter.install_public_types(metadata, "golden-plum") == 1
    wrapped = table.c.book_json.type
    assert adapter.install_public_types(metadata, "golden-plum") == 0
    assert table.c.book_json.type is wrapped
    assert adapter.original_public_type(wrapped) is type_
    assert table.c.execution_capacity_json.type is original_private
    assert str(CreateTable(table).compile(dialect=sqlite_dialect())) == before
    path = tmp_path / "typed.db"
    # A plain DBAPI connection deliberately exercises TypeDecorator's decoder;
    # the production Plum integration below also exercises ResolvingConnection.
    engine = create_engine(f"sqlite:///{path}")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(table.insert(), [
                {"id": 1, "book_json": body, "execution_capacity_json": "private-capacity"},
                {"id": 2, "book_json": None, "execution_capacity_json": "private-fee"},
            ])
        with sqlite3.connect(path) as raw:
            stored = raw.execute("SELECT book_json,execution_capacity_json FROM market_snapshots ORDER BY id").fetchall()
        assert parse_reference(stored[0][0])
        assert type(stored[0][0]) is type(body)
        assert stored[0][1] == "private-capacity"
        assert stored[1] == (None, "private-fee")
        with engine.connect() as connection:
            assert connection.execute(select(table.c.book_json).order_by(table.c.id)).scalars().all() == [body, None]
        restored = wrapped.process_result_value(stored[0][0], sqlite_dialect())
        assert wrapped.process_result_value(restored, sqlite_dialect()) == body
        assert b"private-capacity" not in store.payloads.values()
    finally:
        engine.dispose()


def test_real_plum_orm_raw_sql_and_fee_reader_use_original_public_values(tmp_path, shared_codec):
    codec, store = shared_codec
    path = tmp_path / "plum.db"
    Session = init_database(str(path), maintenance_on_start=False)
    book = '{"token_id":"source-token","asks":[{"price":0.72,"size":10}],"bids":[]}'
    private_capacity = '{"requested_notional":5,"private_decision":"hold"}'
    private_resolution = '{"selected_trade":"private-ledger-proof"}'
    try:
        with Session() as session:
            snapshot = MarketSnapshot(condition_id="condition", token_id="source-token",
                                      probability=.72, book_json=book,
                                      execution_capacity_json=private_capacity)
            session.add(snapshot)
            session.add(MarketCatalog(condition_id="condition", token_ids_json='["source-token"]',
                                      outcomes_json='["Yes"]', outcome_prices_json='[0.72]', tags_json="[]",
                                      fees_enabled=1, fee_rate=.05, fee_exponent=1, fee_taker_only=1))
            session.add(Trade(condition_id="condition", token_id="source-token", outcome="Yes",
                              resolution_evidence=private_resolution))
            session.commit()
            snapshot_id = snapshot.id
            session.expire_all()
            assert session.get(MarketSnapshot, snapshot_id).book_json == book
            raw_read = session.execute(text(
                "SELECT book_json AS source_body,execution_capacity_json FROM market_snapshots WHERE id=:id"
            ), {"id": snapshot_id}).one()
            assert tuple(raw_read) == (book, private_capacity)
            # ORM UPDATE is also intercepted, including overwriting a body with NULL.
            snapshot.book_json = None
            session.commit()
            snapshot.book_json = book
            session.commit()
        with sqlite3.connect(path) as raw:
            stored = raw.execute("SELECT book_json,execution_capacity_json FROM market_snapshots").fetchone()
            assert parse_reference(stored[0]) and stored[1] == private_capacity
            assert raw.execute("SELECT resolution_evidence FROM trades").fetchone()[0] == private_resolution
            assert parse_reference(raw.execute("SELECT token_ids_json FROM market_catalog").fetchone()[0])
        # Construct the wrapper without initializing SDK/auth/network state.
        client = object.__new__(ClobClientWrapper)
        client.execution_ledger = SimpleNamespace(db_path=path)
        schedule, placeholder = client._catalog_fee_schedule("source-token")
        assert schedule.condition_id == "condition"
        assert schedule.rate == Decimal("0.05") and not placeholder
        assert private_capacity.encode() not in store.payloads.values()
        assert private_resolution.encode() not in store.payloads.values()
    finally:
        Session.kw["bind"].dispose()


def test_legacy_without_shared_configuration_keeps_inline_bytes(tmp_path):
    metadata = MetaData()
    table = Table("market_snapshots", metadata, Column("book_json", String))
    adapter.install_public_types(metadata, "golden-plum")
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(table.insert().values(book_json="legacy-source"))
        with sqlite3.connect(path) as raw:
            assert raw.execute("SELECT book_json FROM market_snapshots").fetchone()[0] == "legacy-source"
    finally:
        engine.dispose()


def test_required_store_failure_precedes_local_database_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_MARKET_DATA_REQUIRED", "1")
    path = tmp_path / "must-not-exist.db"
    with pytest.raises(MissingMarketDataConfiguration, match="socket"):
        init_database(str(path), maintenance_on_start=False)
    assert not path.exists()


def test_failed_body_ack_rolls_back_local_orm_write(tmp_path, monkeypatch):
    class FailedStore(MemoryStore):
        def put_many(self, values):
            raise OSError("shared writer unavailable")
    codec = PayloadReferences(writer=FailedStore())
    monkeypatch.setattr(adapter, "configured_references", lambda: codec)
    Session = init_database(str(tmp_path / "failed.db"), maintenance_on_start=False)
    try:
        with Session() as session:
            session.add(MarketSnapshot(condition_id="c", probability=.72, book_json="public"))
            with pytest.raises(StatementError, match="shared writer unavailable"):
                session.commit()
            session.rollback()
            assert session.query(MarketSnapshot).count() == 0
    finally:
        Session.kw["bind"].dispose()


def test_no_writer_in_explicit_readonly_configuration_cannot_fall_back(monkeypatch):
    monkeypatch.setattr(adapter, "configured_references", lambda: PayloadReferences())
    monkeypatch.setenv("PUBLIC_MARKET_DATA_DB", "/configured/read-only.db")
    type_ = adapter.PublicPayloadType(String(), "golden-plum", "market_snapshots", "book_json")
    with pytest.raises(MissingMarketDataConfiguration, match="writer"):
        type_.process_bind_param("source", sqlite_dialect())


def test_install_rejects_owner_changes_and_invalid_type_without_partial_mutation():
    metadata = MetaData()
    table = Table("market_catalog", metadata, Column("outcomes_json", String), Column("token_ids_json", String))
    adapter.install_public_types(metadata, "golden-plum")
    with pytest.raises(ValueError, match="different owner"):
        adapter.install_public_types(metadata, "golden-peach")
    other = MetaData()
    invalid = Table("market_catalog", other, Column("outcomes_json", String), Column("token_ids_json", Integer))
    first_type = invalid.c.outcomes_json.type
    with pytest.raises(TypeError, match="TEXT/BLOB"):
        adapter.install_public_types(other, "golden-plum")
    assert invalid.c.outcomes_json.type is first_type
    with pytest.raises(ValueError, match="unknown"):
        adapter.install_public_types(other, "typo")
