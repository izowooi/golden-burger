"""Raw payload write time is distinct from its physical HTTP receipt time."""
import sqlite3

from polybot.db.repository import ResearchRepository
from polybot_observability import market_data_index, market_data_refs
from polybot_observability.market_data_refs import PayloadReferences, parse_reference
from polybot_observability.market_data_store import PayloadReader, PayloadStore


def test_record_raw_payload_passes_full_row_and_uses_linked_api_receipt(tmp_path, monkeypatch):
    received = '2026-09-29T01:02:03Z'
    connection = sqlite3.connect(':memory:')
    connection.executescript('''
        CREATE TABLE research_run_events(run_id,job_name);
        INSERT INTO research_run_events VALUES('run','child-runtime');
        CREATE TABLE api_requests(request_id,run_id,completed_at);
        CREATE TABLE raw_payloads(payload_id,request_id,payload_kind,content_encoding,
            payload_sha256,uncompressed_bytes,compressed_bytes,blob_stored,payload_blob,recorded_at);
    ''')
    connection.execute('INSERT INTO api_requests VALUES(?,?,?)', ('req', 'run', received))
    connection.commit()
    repository = object.__new__(ResearchRepository)
    monkeypatch.setattr(repository, '_connect', lambda: connection)
    monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE', 'source')
    monkeypatch.setenv('JOB_NAME', 'jenkins-parent')
    with PayloadStore(tmp_path/'public.db') as store:
        codec = PayloadReferences(store, store)
        monkeypatch.setattr(market_data_refs, 'configured_references', lambda: codec)
        monkeypatch.setattr(market_data_index, 'configured_references', lambda: codec)
        payload_id = repository.record_raw_payload(request_id='req', kind='CLOB_BOOKS',
            content=b'[{"asset_id":"token"}]', store_blob=True)
        row = connection.execute('SELECT payload_id,payload_blob,recorded_at FROM raw_payloads').fetchone()
        assert row[0] == payload_id
        assert parse_reference(row[1])
        assert row[2] != received
        with PayloadReader(tmp_path/'public.db') as reader:
            observation, = reader.iter_observations(token_id='token')
        assert observation.observer == 'source/golden-pomegranate/child-runtime'
        assert observation.observed_at == received
    connection.close()
