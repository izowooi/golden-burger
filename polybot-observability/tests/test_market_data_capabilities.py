"""Upgrade admission: a healthy old service is not proof of newer contracts.

The dispatcher and supervisor are exercised without socket creation here. IPC
integration remains the separate service/supervisor suite, not this fixture.
"""

import json

import pytest

from polybot_observability import market_data_supervisor as supervisor
from polybot_observability.market_data_client import (
    PROTOCOL_VERSION, SERVICE_CAPABILITIES, ProtocolError, StoreClient,
)
from polybot_observability.market_data_service import MarketDataService
from polybot_observability.market_data_store import PayloadStore


def test_service_contracts_are_read_only_and_client_checks_them(tmp_path, monkeypatch):
    with PayloadStore(tmp_path / 'public.db') as store:
        client = StoreClient('unused')
        def dispatch(operation, **fields):
            return MarketDataService._dispatch(None, store, {'v': 1, 'op': operation, **fields})
        monkeypatch.setattr(client, '_request', dispatch)
        before = (store.stats(), store.scalar_stats())
        assert client.require_capabilities(SERVICE_CAPABILITIES) == SERVICE_CAPABILITIES
        assert (store.stats(), store.scalar_stats()) == before
        assert store.scalar_authority_role() == 'UNCLAIMED'
        with pytest.raises(ProtocolError, match='missing contracts: future-v10'):
            client.require_capabilities(['future-v10'])


@pytest.mark.parametrize('reply', [
    None, [], {'protocol': 1, 'contracts': [], 'private': 'not-allowed'},
    {'protocol': True, 'contracts': []}, {'protocol': 2, 'contracts': []},
    {'protocol': 1, 'contracts': 'not-a-list'},
    {'protocol': 1, 'contracts': ['same', 'same']},
    {'protocol': 1, 'contracts': ['invalid name']},
    {'protocol': 1, 'contracts': ['a'] * 65},
])
def test_client_rejects_malformed_capability_responses(monkeypatch, reply):
    client = StoreClient('unused')
    monkeypatch.setattr(client, '_request', lambda *args, **kwargs: reply)
    with pytest.raises(ProtocolError):
        client.require_capabilities(['public-scalar-block-store-v1'])


@pytest.mark.parametrize('failure', [
    ProtocolError('unsupported operation on old service'),
    ProtocolError('public service upgrade required; missing contracts: public-scalar-block-store-v1'),
    TimeoutError('busy existing writer'),
])
def test_upgrade_mismatch_or_timeout_never_restarts_owned_service(tmp_path, monkeypatch, failure):
    root = tmp_path.resolve()
    (root / '.polybot-market-data-volume-id').write_text('fixture-volume')
    metadata = root / 'service-process.json'
    metadata.write_text(json.dumps({'pid': 98765, 'fixture': 'preserve'}))
    original = metadata.read_bytes()
    monkeypatch.setattr(supervisor, '_owned_pid', lambda *args: 98765)
    monkeypatch.setattr(StoreClient, 'get_many', lambda self, hashes: [])
    def fail(self, required):
        assert required == {'public-scalar-block-store-v1'}
        raise failure
    monkeypatch.setattr(StoreClient, 'require_capabilities', fail)
    def no_spawn(*args, **kwargs):
        pytest.fail('a capability mismatch must preserve the running writer')
    monkeypatch.setattr(supervisor.subprocess, 'Popen', no_spawn)
    with pytest.raises(type(failure), match=str(failure)):
        supervisor.ensure_service(storage_root=root, expected_volume_id='fixture-volume',
                                  required_capabilities=['public-scalar-block-store-v1'])
    assert metadata.read_bytes() == original
    assert not (root / 'public.db').exists()


def test_supervisor_reports_matching_contracts_and_keeps_legacy_probe(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / '.polybot-market-data-volume-id').write_text('fixture-volume')
    monkeypatch.setattr(supervisor, '_owned_pid', lambda *args: 98765)
    monkeypatch.setattr(StoreClient, 'get_many', lambda self, hashes: [])
    calls = []
    def capability(self, required):
        calls.append(required)
        return SERVICE_CAPABILITIES
    monkeypatch.setattr(StoreClient, 'require_capabilities', capability)
    common = dict(storage_root=root, expected_volume_id='fixture-volume')
    assert supervisor.ensure_service(**common) == {
        'status': 'RUNNING', 'pid': 98765, 'probe': 'empty_read',
    }
    assert calls == []
    result = supervisor.ensure_service(**common, required_capabilities=['public-payload-cas-v1'])
    assert result['capabilities'] == sorted(SERVICE_CAPABILITIES)
    assert calls == [{'public-payload-cas-v1'}]


def test_capability_operation_rejects_extra_arguments(tmp_path):
    with PayloadStore(tmp_path / 'public.db') as store:
        with pytest.raises(ProtocolError):
            MarketDataService._dispatch(None, store, {
                'v': PROTOCOL_VERSION, 'op': 'capabilities', 'sql': 'forbidden',
            })


@pytest.mark.parametrize('scalar', [False, True])
def test_migration_cli_rejects_old_writer_before_derivative_creation(tmp_path, monkeypatch, scalar):
    from polybot_observability import market_data_client, market_data_migrate

    source = tmp_path / 'source.db'
    source.write_bytes(b'original must not be touched')
    destination = tmp_path / 'derivative.db'
    public = tmp_path / 'public.db'
    with PayloadStore(public):
        pass
    seen = []
    class OldWriter:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def require_capabilities(self, contracts):
            seen.append(contracts)
            raise ProtocolError('old writer requires an upgrade')
    monkeypatch.setattr(market_data_client, 'StoreClient', OldWriter)
    arguments = [
        '--source', str(source), '--source-sha256', '0' * 64,
        '--strategy', 'golden-date', '--output', str(destination),
        '--storage-root', str(tmp_path.resolve()), '--socket', str(tmp_path / 'not-bound.sock'),
        '--public-db', str(public), '--source-identity', 'fixture-source',
        '--runtime', 'runtime-a', '--jenkins-job', 'polybot-fixture',
        '--min-free-gib', '0', '--max-used-ratio', '1',
    ]
    if scalar:
        arguments.append('--include-scalars')
    with pytest.raises(ProtocolError, match='requires an upgrade'):
        market_data_migrate.main(arguments)
    expected = {'public-payload-cas-v1', 'public-observation-subjects-v2'}
    if scalar:
        expected.add('public-scalar-block-store-v1')
    assert seen == [expected]
    assert not destination.exists()
    assert not destination.with_suffix('.db.migration.json').exists()
    assert source.read_bytes() == b'original must not be touched'
