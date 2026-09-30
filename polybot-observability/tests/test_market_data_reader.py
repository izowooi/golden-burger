"""Analysis readers have explicit, scoped public dependencies and no write path."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3

import pytest

from polybot_observability.market_data_mixed import externalize_mixed_row
from polybot_observability.market_data_reader import market_data_connect, public_references
from polybot_observability.market_data_refs import MissingMarketDataConfiguration, PayloadReferences
from polybot_observability.market_data_store import PayloadStore


def test_scoped_reader_resolves_inline_public_and_mixed_rows_without_global_changes(tmp_path, monkeypatch):
    public = tmp_path / "public.sqlite"
    database = tmp_path / "local.sqlite"
    mixed = '{"event": {"id":"e","title":"A vs B"}, "decision": "PRIVATE"}'
    with PayloadStore(public) as store, sqlite3.connect(database) as connection:
        refs = PayloadReferences(reader=store, writer=store)
        encoded = externalize_mixed_row("golden-plum", "raw_event_observations", {"evidence_json": mixed}, {"evidence_json": mixed}, refs)["evidence_json"]
        connection.execute("CREATE TABLE evidence(id,body)")
        connection.executemany("INSERT INTO evidence VALUES(?,?)", [(1, "inline"), (2, refs.encode_many(['{"book":1}'])[0]), (3, encoded)])
    monkeypatch.setenv("PUBLIC_MARKET_DATA_DB", str(tmp_path / "unused.sqlite"))
    environment = dict(os.environ)
    raw_connect = sqlite3.connect
    before = database.read_bytes()
    public_before = public.read_bytes()
    with public_references(public) as refs:
        connection = market_data_connect(database.as_uri() + "?mode=ro", uri=True, references=refs)
        try:
            connection.row_factory = sqlite3.Row
            assert [row["body"] for row in connection.execute("SELECT body FROM evidence ORDER BY id")] == ['inline', '{"book":1}', mixed]
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                connection.execute("INSERT INTO evidence VALUES(4,'not allowed')")
        finally:
            connection.close()
    assert dict(os.environ) == environment
    assert sqlite3.connect is raw_connect
    assert database.read_bytes() == before
    assert public.read_bytes() == public_before
    assert not (tmp_path / "unused.sqlite").exists()
    with pytest.raises(FileNotFoundError):
        with public_references(tmp_path / "missing.sqlite"):
            pass


def test_unconfigured_scoped_reader_remains_inline_compatible_and_fails_on_references(tmp_path, monkeypatch):
    monkeypatch.delenv("PUBLIC_MARKET_DATA_DB", raising=False)
    with public_references() as refs:
        assert refs.decode_many(['{"inline":true}', 0, None]) == ['{"inline":true}', 0, None]
        with pytest.raises(MissingMarketDataConfiguration):
            refs.decode_many(['\x1ePMDATA1:T:' + 'a' * 64])


def test_reader_uses_environment_without_opening_a_writer_and_closes_on_error(tmp_path, monkeypatch):
    public = tmp_path / "public.sqlite"
    with PayloadStore(public) as store:
        refs = PayloadReferences(reader=store, writer=store)
        reference = refs.encode_many(['{"public":true}'])[0]
    monkeypatch.setenv("PUBLIC_MARKET_DATA_DB", str(public))
    before = public.read_bytes()
    with pytest.raises(RuntimeError, match="report failed"):
        with public_references() as refs:
            owned_reader = refs.reader
            assert refs.writer is None
            assert refs.decode_many([reference]) == ['{"public":true}']
            raise RuntimeError("report failed")
    with pytest.raises(sqlite3.ProgrammingError):
        owned_reader.get_many([reference.rsplit(":", 1)[1]])
    assert public.read_bytes() == before


@pytest.mark.parametrize("kind", ["relative", "symlink"])
def test_reader_rejects_noncanonical_public_paths(tmp_path, kind):
    public = tmp_path / "public.sqlite"
    with PayloadStore(public):
        pass
    if kind == "relative":
        configured = Path(os.path.relpath(public))
    else:
        configured = tmp_path / "public-link.sqlite"
        configured.symlink_to(public)
    with pytest.raises(ValueError, match="canonical absolute"):
        with public_references(configured):
            pass
