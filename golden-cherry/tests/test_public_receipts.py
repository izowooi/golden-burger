"""Real collector insertion publishes exact source receipts before local references."""
import gzip
import sqlite3

from polybot.shadow.db import ShadowRepository
from polybot_observability import market_data_index, market_data_refs
from polybot_observability.market_data_refs import PayloadReferences, parse_reference
from polybot_observability.market_data_store import PayloadReader, PayloadStore


def test_raw_insertion_hook_publishes_searchable_receipt(tmp_path, monkeypatch):
    received = '2026-09-29T01:02:03Z'
    row = {'run_id': 'run', 'request_id': 'request', 'payload_id': 'payload',
           'source_received_at': received, 'payload_gzip': gzip.compress(b'{"asset_id":"token"}', mtime=0)}
    row.update({})
    table = 'shadow_raw_payloads'
    connection = sqlite3.connect(':memory:')
    connection.executescript("CREATE TABLE shadow_run_events(run_id,config_hash); CREATE TABLE shadow_config_versions(config_hash,runtime_job); INSERT INTO shadow_run_events VALUES('run','config'); INSERT INTO shadow_config_versions VALUES('config','runtime');")
    connection.execute(f"CREATE TABLE {table} ({','.join(row)})")
    monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE', 'source')
    monkeypatch.setenv('JOB_NAME', 'jenkins-parent')
    with PayloadStore(tmp_path/'public.db') as store:
        codec = PayloadReferences(store, store)
        monkeypatch.setattr(market_data_refs, 'configured_references', lambda: codec)
        monkeypatch.setattr(market_data_index, 'configured_references', lambda: codec)
        ShadowRepository._insert_rows(connection, table, [row])
        assert parse_reference(connection.execute(f'SELECT payload_gzip FROM {table}').fetchone()[0])
        with PayloadReader(tmp_path/'public.db') as reader:
            observation, = reader.iter_observations(token_id='token')
        assert observation.observer == 'source/golden-cherry/runtime'
        assert observation.observed_at == received
        assert store.stats()['observation_count'] == 1
    connection.close()
