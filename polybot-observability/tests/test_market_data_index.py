import gzip
import json
import sqlite3
import zlib

import pytest

from polybot_observability import market_data_index as index
from polybot_observability import market_data_refs
from polybot_observability.market_data_index import ReceiptContext
from polybot_observability.market_data_refs import PayloadReferences, externalize_row
from polybot_observability.market_data_store import PayloadReader, PayloadStore


RECEIVED = '2026-09-29T01:02:03.123456Z'
WRITTEN = '2026-09-29T05:00:00Z'
BODY = {'events': [{'id': 'event-1', 'markets': [
    {'clobTokenIds': '["token-1", "token-2"]'}]}]}


@pytest.mark.parametrize('mismatch', ['source', 'job', 'runtime', 'strategy'])
def test_bound_receipt_owner_rejects_conflicting_live_identity(tmp_path, monkeypatch, mismatch):
    from polybot_observability.market_data_scalar_links import scalar_namespace

    monkeypatch.delenv('PUBLIC_MARKET_DATA_SOURCE', raising=False)
    monkeypatch.delenv('JOB_NAME', raising=False)
    owner = dict(source='source', jenkins_job='job', strategy='golden-guava', runtime='runtime')
    if mismatch == 'source':
        monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE', 'other-source')
    elif mismatch == 'job':
        monkeypatch.setenv('JOB_NAME', 'other-job')
    elif mismatch == 'runtime':
        owner['runtime'] = 'other-runtime'
    else:
        owner['strategy'] = 'golden-black'
    connection = sqlite3.connect(':memory:')
    try:
        connection.executescript("CREATE TABLE run_audits(run_id,job_name); INSERT INTO run_audits VALUES('run','runtime');")
        with PayloadStore(tmp_path/'public.db') as store:
            codec = PayloadReferences(store, store)
            with pytest.raises(ValueError, match='owner|namespace/strategy'):
                index.collector_receipt_context('golden-guava', 'source_requests',
                    {'run_id': 'run', 'payload_gzip': b'body'}, connection,
                    references=codec, namespace=scalar_namespace(**owner))
            assert store.stats()['observation_count'] == 0
    finally:
        connection.close()


def test_explicit_receipt_writer_without_source_identity_is_not_silent(tmp_path, monkeypatch):
    monkeypatch.delenv('PUBLIC_MARKET_DATA_SOURCE', raising=False)
    monkeypatch.setattr(index, 'configured_references', lambda: PayloadReferences())
    with PayloadStore(tmp_path/'public.db') as store:
        with pytest.raises(ValueError, match='PUBLIC_MARKET_DATA_SOURCE'):
            index.collector_receipt_context('golden-guava', 'source_requests',
                {'run_id': 'run', 'payload_gzip': b'body'}, None,
                references=PayloadReferences(store, store), runtime='runtime')


def compressed(body, encoding):
    raw = json.dumps(body).encode()
    if encoding == 'gzip':
        return gzip.compress(raw, mtime=0)
    if encoding == 'zlib':
        return zlib.compress(raw)
    return raw.decode()


@pytest.mark.parametrize('strategy,table,column,clock,encoding,keys', [
    ('golden-black', 'raw_payloads', 'payload_gzip', 'observed_at', 'gzip', {'payload_id': 'raw-1'}),
    ('golden-watermelon', 'raw_payloads', 'payload_gzip', 'observed_at', 'gzip', {'payload_id': 'raw-1'}),
    ('golden-watermelon', 'raw_payloads', 'payload_gzip', 'received_at', 'gzip', {'payload_id': 'raw-1', 'kind': 'BOOKS'}),
    ('golden-watermelon', 'raw_events', 'event_json', 'metadata_received_at', 'utf8-json', {'event_id': 'event-1'}),
    ('golden-cherry', 'shadow_raw_payloads', 'payload_gzip', 'source_received_at', 'gzip', {'payload_id': 'raw-1'}),
    ('golden-raspberry', 'raw_payloads', 'payload_blob', 'recorded_at', 'gzip', {'payload_id': 'raw-1'}),
    ('golden-pomegranate', 'raw_payloads', 'payload_blob', 'api_requests.completed_at', 'zlib', {'payload_id': 'raw-1'}),
    ('golden-pomegranate', 'resolution_observations', 'raw_market_json', 'observed_at', 'utf8-json', {'resolution_observation_id': 'resolution-1'}),
    ('golden-strawberry', 'raw_payloads', 'payload_blob', 'source_received_at', 'gzip', {'payload_id': 'raw-1'}),
    ('golden-strawberry', 'compact_books', 'book_blob', 'source_received_at', 'gzip', {'book_id': 'book-1', 'token_id': 'token-1'}),
    ('golden-strawberry', 'resolution_observations', 'market_blob', 'observed_at', 'gzip', {'resolution_observation_id': 'resolution-1'}),
    ('golden-guava', 'source_requests', 'payload_gzip', 'received_at', 'gzip', {}),
    ('golden-guava', 'events', 'raw_gzip', 'source_requests.received_at', 'gzip', {'event_id': 'event-1'}),
    ('golden-guava', 'book_attempts', 'raw_gzip', 'source_requests.received_at', 'gzip', {'event_id': 'event-1', 'token_id': 'token-1'}),
    *[(strategy, 'raw_book_observations', 'book_json', 'received_at', 'utf8-json', {'observation_id': 'book-1', 'event_id': 'event-1', 'token_id': 'token-1'})
      for strategy in ('golden-apricot', 'golden-peach', 'golden-plum')],
])
def test_each_schema_indexes_one_receipt_with_all_public_subjects(
    tmp_path, strategy, table, column, clock, encoding, keys,
):
    connection = sqlite3.connect(':memory:')
    connection.execute('CREATE TABLE api_requests(request_id,run_id,completed_at)')
    connection.execute('INSERT INTO api_requests VALUES(?,?,?)', ('req-1', 'real-run', RECEIVED))
    connection.execute('CREATE TABLE source_requests(run_id,request_id,received_at)')
    connection.execute('INSERT INTO source_requests VALUES(?,?,?)', ('real-run', 'req-1', RECEIVED))
    body = {**BODY, '_guava': {'request_id': 'req-1'}} if strategy == 'golden-guava' else BODY
    context = ReceiptContext('verified-source', 'actual-runtime', 'jenkins-job', connection)
    row = {'run_id': 'real-run', 'request_id': 'req-1', 'recorded_at': WRITTEN,
           'payload_kind': 'GAMMA_EVENT_PAGE', 'fee_usdc': 12.3, 'config_json': 'private',
           'decision_json': 'private', 'winning_token_id': 'not-a-source-token',
           column: compressed(body, encoding), **keys}
    if clock not in ('api_requests.completed_at', 'source_requests.received_at'):
        row[clock] = RECEIVED
    with PayloadStore(tmp_path / 'public.db') as store:
        codec = PayloadReferences(store, store)
        encoded = externalize_row(strategy, table, row, references=codec, receipt_context=context)
        # The original bytes and TEXT/BLOB class survive the projection.
        assert codec.decode_many([encoded[column]])[0] == row[column]
        assert store.stats()['observation_count'] == 1
        assert context.indexed_count == 1
        with PayloadReader(tmp_path / 'public.db') as reader:
            observations = list(reader.iter_observations(token_id='token-2', event_id='event-1'))
        assert len(observations) == 1
        observation = observations[0]
        assert observation.observer == f'verified-source/{strategy}/actual-runtime'
        assert observation.observed_at.replace('+00:00', 'Z') == RECEIVED
        metadata = json.loads(observation.metadata_json)
        assert metadata['receipt_time_basis'] == clock
        assert metadata['run_id'] == 'real-run'
        assert metadata['job_name'] == 'jenkins-job'
        assert metadata['evidence_scope'] == 'raw_receipt_not_cycle_or_trade_confirmation'
        assert not any(key in metadata for key in ('fee_usdc', 'config_json', 'decision_json', 'winning_token_id'))
        assert 'not-a-source-token' not in observation.token_ids
    connection.close()


def test_receipt_batch_does_not_duplicate_observation_for_each_token(tmp_path):
    body = [{'asset_id': f'token-{i}', 'bids': [], 'asks': []} for i in range(501)]
    row = {'payload_id': 'batch', 'run_id': 'run', 'payload_kind': 'CLOB_BOOK_BATCH',
           'observed_at': RECEIVED, 'payload_gzip': compressed(body, 'gzip')}
    context = ReceiptContext('source', 'runtime')
    with PayloadStore(tmp_path/'store.db') as store:
        codec = PayloadReferences(store, store)
        for _ in range(2):
            externalize_row('golden-black', 'raw_payloads', row, references=codec, receipt_context=context)
        assert store.stats()['observation_count'] == 1
        with PayloadReader(tmp_path/'store.db') as reader:
            assert len(list(reader.iter_observations(token_id='token-500'))) == 1
            assert list(reader.iter_observations(token_id='unseen')) == []


def test_missing_request_time_is_gap_never_payload_write_time(tmp_path):
    row = {'payload_id': 'raw', 'request_id': 'request', 'recorded_at': WRITTEN,
           'payload_blob': compressed(BODY, 'zlib')}
    context = ReceiptContext('source', 'runtime')
    with PayloadStore(tmp_path/'store.db') as store:
        externalize_row('golden-pomegranate', 'raw_payloads', row,
                        references=PayloadReferences(store, store), receipt_context=context)
        assert store.stats()['payload_count'] == 1
        assert store.stats()['observation_count'] == 0
        assert context.gap_counts == {'raw_payloads:missing_original_receipt_time': 1}


def test_offline_context_never_reads_launcher_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE', 'wrong-source')
    monkeypatch.setenv('JOB_NAME', 'wrong-job')
    row = {'run_id': 'run', 'event_id': 'event-1', 'received_at': RECEIVED,
           'event_json': json.dumps({'id': 'event-1'})}
    with PayloadStore(tmp_path/'store.db') as store:
        externalize_row('golden-coconut', 'event_observations', row,
                        references=PayloadReferences(store, store),
                        receipt_context=ReceiptContext('archive', 'white-runtime'))
        with PayloadReader(tmp_path/'store.db') as reader:
            observation, = reader.iter_observations()
        assert observation.observer == 'archive/golden-coconut/white-runtime'
        assert 'job_name' not in json.loads(observation.metadata_json)


def test_white_event_and_clock_use_their_independent_receipts(tmp_path):
    row = {'run_id': 'run', 'event_id': 'event-1', 'received_at': RECEIVED,
           'event_json': json.dumps({'id': 'event-1', 'markets': [{'clobTokenIds': ['token-1']}]}),
           'clock_json': json.dumps({'received_at': WRITTEN, 'payload': {'event_id': 'event-1'}})}
    context = ReceiptContext('source', 'white-runtime')
    with PayloadStore(tmp_path/'store.db') as store:
        externalize_row('golden-coconut', 'event_observations', row,
                        references=PayloadReferences(store, store), receipt_context=context)
        with PayloadReader(tmp_path/'store.db') as reader:
            observations = list(reader.iter_observations(event_id='event-1'))
        assert [(json.loads(o.metadata_json).get('source_column', 'event_json'), o.observed_at) for o in observations] == [
            ('event_json', RECEIVED), ('clock_json', WRITTEN)]
        assert context.indexed_count == 2


def test_white_clock_does_not_borrow_gamma_time(tmp_path):
    row = {'run_id': 'run', 'event_id': 'event-1', 'received_at': RECEIVED,
           'clock_json': '{}'}
    context = ReceiptContext('source', 'white-runtime')
    with PayloadStore(tmp_path/'store.db') as store:
        externalize_row('golden-coconut', 'event_observations', row,
                        references=PayloadReferences(store, store), receipt_context=context)
        assert store.stats()['observation_count'] == 0
        assert context.gap_counts == {'event_observations.clock_json:missing_original_receipt_time': 1}


@pytest.mark.parametrize('strategy,table,relation,row', [
    ('golden-black', 'raw_payloads', 'research_config', {'payload_gzip': b'', 'run_id': 'run'}),
    ('golden-watermelon', 'raw_payloads', 'research_config', {'payload_gzip': b'', 'run_id': 'run'}),
    ('golden-watermelon', 'raw_payloads', 'sidecar', {'payload_gzip': b'', 'run_id': 'run', 'kind': 'BOOKS'}),
    ('golden-cherry', 'shadow_raw_payloads', 'shadow_config', {'payload_gzip': b'', 'run_id': 'run'}),
    ('golden-raspberry', 'raw_payloads', 'research_config', {'payload_blob': b'', 'run_id': 'run'}),
    ('golden-pomegranate', 'raw_payloads', 'run', {'payload_blob': b'', 'request_id': 'req'}),
    ('golden-strawberry', 'raw_payloads', 'run', {'payload_blob': b'', 'run_id': 'run'}),
    ('golden-strawberry', 'compact_books', 'run', {'book_blob': b'', 'run_id': 'run'}),
    ('golden-guava', 'source_requests', 'audit', {'payload_gzip': b'', 'run_id': 'run'}),
])
def test_live_context_joins_exact_run_instead_of_jenkins_job(monkeypatch, strategy, table, relation, row):
    connection = sqlite3.connect(':memory:')
    if relation in ('research_config', 'shadow_config'):
        prefix = 'research' if relation == 'research_config' else 'shadow'
        field = 'job_name' if prefix == 'research' else 'runtime_job'
        connection.executescript(f'CREATE TABLE {prefix}_run_events(run_id,config_hash);'
                                 f'CREATE TABLE {prefix}_config_versions(config_hash,{field});'
                                 f"INSERT INTO {prefix}_run_events VALUES('run','config');"
                                 f"INSERT INTO {prefix}_config_versions VALUES('config','child-runtime');")
    else:
        name = {'sidecar': 'raw_cycles', 'run': 'research_run_events', 'audit': 'run_audits'}[relation]
        connection.executescript(f'CREATE TABLE {name}(run_id,job_name);'
                                 f"INSERT INTO {name} VALUES('run','child-runtime');")
    connection.executescript('CREATE TABLE api_requests(request_id,run_id,completed_at);'
                             f"INSERT INTO api_requests VALUES('req','run','{RECEIVED}');")
    monkeypatch.setattr(index, 'configured_references', lambda: PayloadReferences(writer=object()))
    monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE', 'source')
    monkeypatch.setenv('JOB_NAME', 'jenkins-parent')
    context = index.collector_receipt_context(strategy, table, row, connection)
    assert (context.source, context.runtime, context.job_name) == ('source', 'child-runtime', 'jenkins-parent')
    connection.close()


def test_live_identity_fails_closed_and_explicit_sidecar_runtime_needs_no_cycle(monkeypatch):
    monkeypatch.setattr(index, 'configured_references', lambda: PayloadReferences(writer=object()))
    monkeypatch.setenv('JOB_NAME', 'not-a-runtime')
    monkeypatch.delenv('PUBLIC_MARKET_DATA_SOURCE', raising=False)
    row = {'payload_gzip': b'', 'kind': 'BOOKS', 'run_id': 'run'}
    connection = sqlite3.connect(':memory:')
    connection.execute('CREATE TABLE raw_cycles(run_id,job_name)')
    with pytest.raises(ValueError, match='SOURCE'):
        index.collector_receipt_context('golden-watermelon', 'raw_payloads', row, connection)
    monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE', 'source')
    with pytest.raises(ValueError, match='missing or ambiguous'):
        index.collector_receipt_context('golden-watermelon', 'raw_payloads', row, connection)
    context = index.collector_receipt_context('golden-watermelon', 'raw_payloads', row, connection,
                                              runtime='actual-config-runtime')
    assert context.runtime == 'actual-config-runtime'
    connection.close()


def test_no_writer_needs_no_source_identity_or_runtime_query(monkeypatch):
    monkeypatch.setattr(index, 'configured_references', lambda: PayloadReferences())
    assert index.collector_receipt_context('golden-black', 'raw_payloads',
                                          {'payload_gzip': b''}, None) is None


@pytest.mark.parametrize('strategy', ['golden-apricot', 'golden-peach', 'golden-plum'])
def test_reviewed_orm_received_at_is_utc_even_when_sqlite_stored_it_naive(tmp_path, strategy):
    row = {'observation_id': 'book', 'run_id': 'run', 'token_id': 'token', 'event_id': 'event',
           'received_at': '2026-09-29 01:02:03.123456', 'book_json': '{"bids":[],"asks":[]}'}
    with PayloadStore(tmp_path/'store.db') as store:
        externalize_row(strategy, 'raw_book_observations', row,
                        references=PayloadReferences(store, store), receipt_context=ReceiptContext('source', 'runtime'))
        with PayloadReader(tmp_path/'store.db') as reader:
            observation, = reader.iter_observations(token_id='token', event_id='event')
        assert observation.observed_at == '2026-09-29T01:02:03.123456+00:00'


def test_white_existing_observation_retry_preserves_immutable_metadata_and_subjects(tmp_path):
    import hashlib
    from polybot_observability.market_data_store import Observation

    row = {'run_id': 'run', 'event_id': 'event-1', 'received_at': RECEIVED,
           'family': 'soccer', 'event_json': json.dumps(BODY)}
    with PayloadStore(tmp_path/'store.db') as store:
        codec = PayloadReferences(store, store)
        encoded = externalize_row('golden-coconut', 'event_observations', row, references=codec)
        digest = market_data_refs.parse_reference(encoded['event_json'])[1]
        metadata = {'run_id': 'run', 'received_at': RECEIVED, 'family': 'soccer',
                    'evidence_scope': 'raw_receipt_not_cycle_or_trade_confirmation',
                    'source_table': 'event_observations', 'encoding': 'utf8-json', 'storage_type': 'T'}
        observation = Observation(
            observer='source/golden-coconut/white-runtime',
            observation_id=hashlib.sha256(json.dumps(
                ['event_observations', 'event_json', ['run', 'event-1']], separators=(',', ':')).encode()).hexdigest(),
            observed_at=RECEIVED, kind='event_observations', payload_sha=digest, event_id='event-1',
            metadata_json=json.dumps(metadata, sort_keys=True, separators=(',', ':')),
        )
        store.append_observations([observation])
        externalize_row('golden-coconut', 'event_observations', row, references=codec,
                        receipt_context=ReceiptContext('source', 'white-runtime', 'explicit-job'))
        assert store.stats()['observation_count'] == 1


def test_guava_missing_linked_receipt_never_uses_runtime_observed_at_fallback(tmp_path):
    row = {'run_id': 'run', 'event_id': 'event', 'token_id': 'token', 'request_id': 'missing',
           'observed_at': WRITTEN, 'raw_gzip': compressed({'asset_id': 'token'}, 'gzip')}
    context = ReceiptContext('source', 'runtime')
    with PayloadStore(tmp_path/'store.db') as store:
        externalize_row('golden-guava', 'book_attempts', row,
                        references=PayloadReferences(store, store), receipt_context=context)
        assert store.stats()['observation_count'] == 0
        assert context.gap_counts == {'book_attempts:missing_original_receipt_time': 1}


@pytest.mark.parametrize('source,runtime', [(None, 'runtime'), ('source', None), ('', 'runtime'), ('source', '')])
def test_receipt_context_requires_explicit_source_and_runtime(source, runtime):
    with pytest.raises(ValueError, match='identity'):
        ReceiptContext(source, runtime)


def coconut_archive(tmp_path, *, payload_kind='GAMMA_EVENT_KEYSET_PAGE', receipt_time=RECEIVED):
    """Use the retired v6/v7 production migration, including its real PK/FKs."""
    import hashlib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    migration = root/'golden-coconut/src/polybot/db/migrations/0006_major_sports_lifecycle_v6.sql'
    path = tmp_path/'coconut-archive.db'
    connection = sqlite3.connect(path)
    connection.executescript(migration.read_text())

    def insert(table, **values):
        # Unrelated required evidence stays only in the source fixture. These
        # defaults let SQLite enforce the full production schema and its FKs.
        columns = connection.execute(f'PRAGMA table_info({table})').fetchall()
        for _, name, kind, required, default, primary in columns:
            if name not in values and (required or primary) and default is None:
                values[name] = 1 if kind in ('INTEGER', 'REAL') else 'private-unrelated-evidence'
        connection.execute(f"INSERT INTO {table}({','.join(values)}) VALUES({','.join('?' for _ in values)})",
                           tuple(values.values()))

    insert('collection_cycles', cycle_id='cycle', run_id='run', job_name='retired-runtime', mode='sim')
    body = {'events': [{'id': 'event-1', 'markets': [{'clobTokenIds': ['token-1', 'token-2']}]}]}
    raw = json.dumps(body).encode()
    insert('raw_payloads', raw_payload_id='raw-id', cycle_id='cycle', run_id='run',
           payload_kind=payload_kind, sport_family='soccer', logical_request_id='http-request',
           observed_at=receipt_time, sha256=hashlib.sha256(raw).hexdigest(), raw_bytes=len(raw),
           gzip_bytes=len(gzip.compress(raw, mtime=0)), payload_gzip=gzip.compress(raw, mtime=0))
    insert('event_observations', event_observation_id='event-observation', cycle_id='cycle',
           raw_payload_id='raw-id', run_id='run', source_kind='DISCOVERY', sport_family='soccer',
           event_id='event-1', season_phase='REGULAR', lifecycle_state='IN_PLAY',
           classification_status='ACCEPTED')
    insert('market_observations', market_observation_id='market-observation', cycle_id='cycle',
           event_observation_id='event-observation', run_id='run', event_id='event-1',
           token_ids_json='["token-1","token-2"]')
    insert('outcome_observations', outcome_observation_id='outcome-observation',
           market_observation_id='market-observation', cycle_id='cycle', run_id='run', token_id='token-1')
    book = b'{"asset_id":"token-1","bids":[{"price":"0.9","size":"5"}],"asks":[]}'
    book_gzip = gzip.compress(book, mtime=0)
    insert('book_snapshots', book_snapshot_id='book-id', cycle_id='cycle', run_id='run', token_id='token-1',
           logical_request_id='books-request', observed_at=receipt_time,
           source_timestamp='not-the-receipt', canonical_sha256=hashlib.sha256(book).hexdigest(),
           canonical_bytes=len(book), gzip_bytes=len(book_gzip), book_gzip=book_gzip,
           best_bid=0.9, fee_status='DERIVED_PRIVATE_STATUS', public_fee_rate_bps=123)
    connection.commit()
    assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    connection.close()
    reader = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    reader.row_factory = sqlite3.Row
    return reader


def test_historical_coconut_actual_schema_raw_and_book_projection_are_searchable(tmp_path):
    source = coconut_archive(tmp_path)
    context = ReceiptContext('verified-archive', 'retired-runtime', connection=source)
    try:
        with PayloadStore(tmp_path/'public.db') as store:
            codec = PayloadReferences(store, store)
            for table, column in [('raw_payloads', 'payload_gzip'), ('book_snapshots', 'book_gzip')]:
                row = dict(source.execute(f'SELECT * FROM {table}').fetchone())
                encoded = externalize_row('golden-coconut', table, row, references=codec, receipt_context=context)
                assert codec.decode_many([encoded[column]])[0] == row[column]
            with PayloadReader(tmp_path/'public.db') as reader:
                observations = list(reader.iter_observations(token_id='token-1', event_id='event-1'))
                receipts = list(reader.iter_observations(kind='public_receipt:raw_payloads'))
                projections = list(reader.iter_observations(kind='public_source_projection:book_snapshots'))
            assert len(observations) == 2
            assert len(receipts) == len(projections) == 1
            assert context.indexed_count == 2
            assert all(o.observed_at == RECEIVED for o in observations)
            assert all(o.observer == 'verified-archive/golden-coconut/retired-runtime' for o in observations)
            assert projections[0].event_id == 'event-1'
            assert projections[0].token_id == 'token-1'
            for observation in observations:
                metadata = json.loads(observation.metadata_json)
                assert metadata['receipt_time_basis'] == 'observed_at'
                assert 'private' not in observation.metadata_json.lower()
                assert 'fee_status' not in metadata and 'public_fee_rate_bps' not in metadata
                assert 'source_timestamp' not in metadata
            assert json.loads(projections[0].metadata_json)['body_basis'] == 'retained_public_source_projection'
            assert json.loads(receipts[0].metadata_json)['body_basis'] == 'retained_public_response_bytes'
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            source.execute("DELETE FROM raw_payloads")
    finally:
        source.close()


def test_historical_coconut_book_event_join_is_scoped_to_exact_cycle_and_run(tmp_path):
    source = coconut_archive(tmp_path)
    row = dict(source.execute('SELECT * FROM book_snapshots').fetchone())
    row['cycle_id'] = 'another-cycle'
    with PayloadStore(tmp_path/'public.db') as store:
        externalize_row('golden-coconut', 'book_snapshots', row,
                        references=PayloadReferences(store, store),
                        receipt_context=ReceiptContext('source', 'runtime', connection=source))
        with PayloadReader(tmp_path/'public.db') as reader:
            observation, = reader.iter_observations(token_id='token-1')
            assert list(reader.iter_observations(event_id='event-1')) == []
        assert observation.event_id is None
        assert observation.event_ids == ()
    source.close()


def test_historical_coconut_wss_batch_end_is_not_each_frame_receipt(tmp_path):
    source = coconut_archive(tmp_path, payload_kind='SPORTS_CLOCK_MESSAGE', receipt_time=WRITTEN)
    row = dict(source.execute('SELECT * FROM raw_payloads').fetchone())
    context = ReceiptContext('source', 'runtime', connection=source)
    with PayloadStore(tmp_path/'public.db') as store:
        encoded = externalize_row('golden-coconut', 'raw_payloads', row,
                                  references=PayloadReferences(store, store), receipt_context=context)
        assert market_data_refs.parse_reference(encoded['payload_gzip'])
        assert store.stats()['payload_count'] == 1
        assert store.stats()['observation_count'] == 0
        assert context.gap_counts == {'raw_payloads:missing_per_message_receipt_time': 1}
    source.close()
