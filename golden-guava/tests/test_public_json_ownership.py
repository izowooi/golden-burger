"""Real insertion hooks preserve copied source JSON and local scalar context."""
import sqlite3

import pytest

from polybot.evidence import Repository
from polybot_observability import market_data_refs
from polybot_observability.market_data_refs import PayloadReferences, is_external_value
from polybot_observability.market_data_store import PayloadStore
from polybot_observability.market_data_sqlite import connect


@pytest.mark.parametrize("table,bodies,mixed_column", [('events', {'clock_json': ' {"elapsed":1e+1} ', 'event_json': ' {"raw":{},"clock":{"elapsed":1e+1},"rules_evidence":{"event":{"rules":"published"}},"eligible":false} '}, 'event_json'), ('book_attempts', {'book_json': ' {"raw":{},"fee_evidence":{"raw":{"feeRateBps":12.50},"source":"PRIVATE"}} ', 'fee_evidence_json': ' {"raw":{"feeRateBps":12.50},"source":"PRIVATE"} '}, 'book_json')])
def test_public_json_insert_and_reader_roundtrip(tmp_path, monkeypatch, table, bodies, mixed_column):
    path = tmp_path / "private.db"
    original = {**bodies, "private_context": "PRIVATE_CONTEXT", "raw_scalar": 12.5}
    connection = sqlite3.connect(path)
    connection.execute(f"CREATE TABLE {table} ({','.join(original)})")
    with PayloadStore(tmp_path / "public.db") as store:
        refs = PayloadReferences(store, store)
        monkeypatch.setattr(market_data_refs, "configured_references", lambda: refs)
        repository = object.__new__(Repository)
        repository.connection = connection
        repository._insert(table, original)
        connection.commit()
        physical = dict(zip(original, connection.execute(f"SELECT * FROM {table}").fetchone()))
        assert all(is_external_value(physical[column]) for column in bodies)
        assert physical["private_context"] == original["private_context"]
        assert physical["raw_scalar"] == original["raw_scalar"]
        with connect(path, references=PayloadReferences(reader=store)) as reader:
            actual = dict(zip(original, reader.execute(f"SELECT * FROM {table}").fetchone()))
        assert actual == original
    connection.close()
