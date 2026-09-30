"""Real collector insertion publishes exact source receipts before local references."""
import gzip
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from polybot.evidence import Repository
from polybot_observability import market_data_index, market_data_refs
from polybot_observability.market_data_refs import PayloadReferences, parse_reference
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from polybot_observability.market_data_raw_profiles import GUAVA_PROFILE_ID
from polybot_observability.market_data_scalar_links import scalar_namespace


def test_raw_insertion_hook_publishes_searchable_receipt(tmp_path, monkeypatch):
    received = '2026-09-29T01:02:03Z'
    row = {'run_id': 'run', 'request_id': 'request', 'payload_id': 'payload',
           'received_at': received, 'payload_gzip': gzip.compress(b'{"asset_id":"token"}', mtime=0)}
    row.update({})
    table = 'source_requests'
    connection = sqlite3.connect(':memory:')
    connection.executescript("CREATE TABLE run_audits(run_id,job_name); INSERT INTO run_audits VALUES('run','runtime');")
    connection.execute(f"CREATE TABLE {table} ({','.join(row)})")
    monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE', 'source')
    monkeypatch.setenv('JOB_NAME', 'jenkins-parent')
    with PayloadStore(tmp_path/'public.db') as store:
        codec = PayloadReferences(store, store)
        monkeypatch.setattr(market_data_refs, 'configured_references', lambda: codec)
        monkeypatch.setattr(market_data_index, 'configured_references', lambda: codec)
        repository = object.__new__(Repository)
        repository.connection = connection
        repository._insert(table, row)
        assert parse_reference(connection.execute(f'SELECT payload_gzip FROM {table}').fetchone()[0])
        with PayloadReader(tmp_path/'public.db') as reader:
            observation, = reader.iter_observations(token_id='token')
        assert observation.observer == 'source/golden-guava/runtime'
        assert observation.observed_at == received
        assert store.stats()['observation_count'] == 1
    connection.close()


@pytest.mark.parametrize('partial_status',['FAILED','TRUNCATED'])
@pytest.mark.parametrize('identity_route',['launcher','bound-owner'])
def test_native_partial_stream_receipts_survive_failed_run_and_cold_token_query(tmp_path,monkeypatch,partial_status,identity_route):
    """Actual stream sink -> open run receipt commit -> failed audit -> cold index."""
    from polybot import streams

    started='2026-09-30T01:00:00Z'
    received=('2026-09-30T01:00:00.125Z','2026-09-30T01:00:02.500Z')
    ended='2026-09-30T01:00:05Z'
    payloads=[{'asset_id':'token-a','timestamp':'1788656400000','bids':[]},
              {'market':'condition-not-event','price_changes':[
                  {'asset_id':'token-a','price':'0.55'}, {'asset_id':'token-b','price':'0.45'}]}]
    sent=[]

    class Socket:
        def __init__(self):self.messages=iter(payloads)
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def send(self,message):sent.append(json.loads(message))
        def recv(self,**kwargs):
            try:return json.dumps(next(self.messages))
            except StopIteration:raise ConnectionError('fixture transport ended after retained frames')

    clock=iter((started,*received,ended))
    monkeypatch.setattr(streams,'connect',lambda *args,**kwargs:Socket())
    monkeypatch.setattr(streams,'time',SimpleNamespace(monotonic=lambda:0.0))
    monkeypatch.setattr(streams,'utc',lambda:next(clock))
    if identity_route == 'launcher':
        monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE','fixture-source')
        monkeypatch.setenv('JOB_NAME','polybot-sim-guava-a')
    else:
        monkeypatch.delenv('PUBLIC_MARKET_DATA_SOURCE',raising=False)
        monkeypatch.delenv('JOB_NAME',raising=False)
    contract={'strategy_name':'golden-guava','job_name':'guava-research-a-v1','mode':'sim',
              'data_contract':'guava-research-v1','config_hash':'fixture-config','strategy_source_digest':'fixture-source'}
    namespace=scalar_namespace('fixture-source','polybot-sim-guava-a','golden-guava',contract['job_name'])
    private=tmp_path/'native.db';public=tmp_path/'public.db'
    captured=[]
    with PayloadStore(public) as store:
        refs=PayloadReferences(store,store)
        configured=refs if identity_route == 'launcher' else PayloadReferences()
        monkeypatch.setattr(market_data_refs,'configured_references',lambda:configured)
        monkeypatch.setattr(market_data_index,'configured_references',lambda:configured)
        with closing(Repository(private,contract,raw_profile_id=GUAVA_PROFILE_ID,
                                raw_namespace=namespace,references=refs)) as repository:
            repository.start_run('partial-stream',started,{})
            def sink(receipt,payload):
                captured.append((receipt,payload))
                repository.record_request('partial-stream',receipt,payload)
            result=streams.collect_market_window(['token-a','token-b','subscription-only'],
                SimpleNamespace(require=lambda:20),sink,maximum_messages=2 if partial_status=='TRUNCATED' else 256)
            assert result['status']==partial_status and result['complete_trade_tape'] is False
            receipt,window=captured[0]
            assert receipt['received_at']==ended
            assert [frame['received_at'] for frame in window['messages']]==list(received)
            assert len(sent)==1 and sent[0]['assets_ids']==['subscription-only','token-a','token-b']
            before=store.stats()
            with pytest.raises(sqlite3.IntegrityError):
                repository.record_request('partial-stream',receipt,window)
            assert store.stats()==before  # exact request retry never duplicates frame identities
            repository.fail_run('partial-stream',ended,'ConnectionError','stream-or-later-discovery')
            assert repository.status()['latest_run']['status']=='FAILED'
            assert repository.connection.execute('SELECT COUNT(*) FROM cycles').fetchone()[0]==0
            assert store.stats()['payload_count']==1 and store.stats()['observation_count']==3
        with sqlite3.connect(private) as physical:
            assert parse_reference(physical.execute('SELECT payload_gzip FROM source_requests').fetchone()[0])

    # Both authoritative handles were closed; this reader has no writer or hot cache.
    with PayloadReader(public) as reader:
        with closing(Repository(private,contract,read_only=True,references=PayloadReferences(reader,cache_bytes=0))) as repository:
            assert repository.status()['latest_run']['status']=='FAILED'
            row=repository.connection.execute('SELECT status,received_at,payload_gzip FROM source_requests').fetchone()
            assert row['status']==partial_status and json.loads(gzip.decompress(row['payload_gzip']))==window
        observer='fixture-source/golden-guava/guava-research-a-v1'
        observations=list(reader.iter_observations(token_id='token-a',observer=observer))
        assert [observation.observed_at for observation in observations]==list(received)
        assert len(list(reader.iter_observations(token_id='token-b')))==1
        assert list(reader.iter_observations(token_id='subscription-only'))==[]
        assert list(reader.iter_observations(event_id='condition-not-event'))==[]
        second,=reader.iter_observations(token_id='token-a',start=received[1],end=ended)
        assert second.observed_at==received[1]
        for index,observation in enumerate(observations):
            body,=reader.get_many([observation.payload_sha])
            assert json.loads(gzip.decompress(body))==window
            assert market_data_index.indexed_guava_stream_payload(observation,window)==payloads[index]
            metadata=json.loads(observation.metadata_json)
            assert metadata['message_ordinal']==index and metadata['request_id']==receipt['request_id']
            assert metadata['job_name']=='polybot-sim-guava-a'
        assert len({observation.payload_sha for observation in reader.iter_observations()})==1
        window_observation,=reader.iter_observations(kind='public_receipt:source_requests')
        assert window_observation.observed_at==row['received_at']
