"""Real insertion hooks preserve copied source JSON and local scalar context."""
import sqlite3

import pytest

from polybot.db.repository import ResearchRepository
from polybot.recorder_store import RecorderStore
from polybot_observability import market_data_refs
from polybot_observability.market_data_refs import PayloadReferences, is_external_value
from polybot_observability.market_data_store import PayloadStore
from polybot_observability.market_data_sqlite import connect


@pytest.mark.parametrize("table,bodies,mixed_column", [('event_observations', {'sport_json': ' {"sport":"soccer"} '}, None), ('game_lifecycle_observations', {'raw_lifecycle_json': ' {"ended":true} '}, None), ('resolution_observations', {'evidence_json': ' {"closed":true,"tokens":[]} '}, None), ('book_observations', {'fee_json': ' {"fields":{"feeRateBps":12.50},"source":"PRIVATE"} '}, 'fee_json')])
def test_public_json_insert_and_reader_roundtrip(tmp_path, monkeypatch, table, bodies, mixed_column):
    path = tmp_path / "private.db"
    original = {**bodies, "private_context": "PRIVATE_CONTEXT", "raw_scalar": 12.5}
    connection = sqlite3.connect(path)
    connection.execute(f"CREATE TABLE {table} ({','.join(original)})")
    with PayloadStore(tmp_path / "public.db") as store:
        refs = PayloadReferences(store, store)
        monkeypatch.setattr(market_data_refs, "configured_references", lambda: refs)
        if table == "book_observations":
            repository = object.__new__(RecorderStore)
            repository.runtime_job = "test-runtime"
            repository.references = refs
            repository._body_references = refs
            from types import SimpleNamespace
            repository._shared_raw = SimpleNamespace(enabled=False)
            repository.insert(connection, table, original)
        else:
            ResearchRepository._insert_many(connection, table, [original])
        connection.commit()
        physical = dict(zip(original, connection.execute(f"SELECT * FROM {table}").fetchone()))
        assert all(is_external_value(physical[column]) for column in bodies)
        assert physical["private_context"] == original["private_context"]
        assert physical["raw_scalar"] == original["raw_scalar"]
        with connect(path, references=PayloadReferences(reader=store)) as reader:
            actual = dict(zip(original, reader.execute(f"SELECT * FROM {table}").fetchone()))
        assert actual == original
    connection.close()


def test_historical_raw_hook_keeps_source_receipt_index(tmp_path, monkeypatch):
    import gzip
    from polybot_observability import market_data_index
    from polybot_observability.market_data_store import PayloadReader
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE research_run_events(run_id,config_hash);
        CREATE TABLE research_config_versions(config_hash,job_name);
        INSERT INTO research_run_events VALUES('run','config');
        INSERT INTO research_config_versions VALUES('config','historical-runtime');
        CREATE TABLE raw_payloads(run_id,raw_payload_id,observed_at,payload_gzip);
    """)
    original = {"run_id": "run", "raw_payload_id": "payload", "observed_at": "2026-09-29T01:02:03Z",
                "payload_gzip": gzip.compress(b'{"asset_id":"source-token"}', mtime=0)}
    monkeypatch.setenv("PUBLIC_MARKET_DATA_SOURCE", "source")
    with PayloadStore(tmp_path / "public.db") as store:
        refs = PayloadReferences(store, store)
        monkeypatch.setattr(market_data_refs, "configured_references", lambda: refs)
        monkeypatch.setattr(market_data_index, "configured_references", lambda: refs)
        ResearchRepository._insert(connection, "raw_payloads", original)
        assert is_external_value(connection.execute("SELECT payload_gzip FROM raw_payloads").fetchone()[0])
        with PayloadReader(tmp_path / "public.db") as reader:
            receipt, = reader.iter_observations(token_id="source-token")
        assert receipt.observer == "source/golden-coconut/historical-runtime"
        assert receipt.observed_at == original["observed_at"]
    connection.close()
