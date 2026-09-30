"""Projection RPC verifies commits and ownership, without pretending to test IPC."""

import json
import time

import pytest

from polybot_observability.market_data_client import ProtocolError, ServiceUnavailableError, StoreClient
from polybot_observability.market_data_projections import (
    CorruptProjectionError, MissingProjectionRecordError, PublicProjection,
    ProjectionReceipt, ProjectionRecord, ProjectionReference,
)
from polybot_observability.market_data_service import MarketDataService, _Request, _error_response
from polybot_observability.market_data_store import PayloadReader, PayloadStore, StoreLimitError


def quote(probability=0.81):
    return PublicProjection('gamma-quote-v1', (
        'condition-한글', probability, None, 100.0, 0.80, 0.82, 0.02, '2026-09-29T10:00:00Z',
    ))


def client_for(store, monkeypatch):
    client = StoreClient('in-process-not-an-ipc-test')
    def request(operation, **fields):
        message = json.loads(json.dumps({'v': 1, 'op': operation, **fields}, allow_nan=False))
        result = MarketDataService._dispatch(None, store, message)
        return json.loads(json.dumps(result, allow_nan=False))
    monkeypatch.setattr(client, '_request', request)
    return client


def test_projection_rpc_commit_readback_replicates_ids_and_source_receipts(tmp_path, monkeypatch):
    path = tmp_path / 'public.db'
    with PayloadStore(path) as store, PayloadReader(path) as reader, PayloadStore(tmp_path / 'replica.db') as replica:
        client, receiver = client_for(store, monkeypatch), client_for(replica, monkeypatch)
        authority = client.scalar_authority_identity()
        inputs = [quote(), quote(0.83)]
        refs = client.put_public_projections(inputs)
        ids = [row.record_id for row in refs]
        records = client.get_projection_records(ids, authority)
        assert records == reader.get_projection_records(ids, authority)
        assert [row.projection for row in records] == inputs
        receipts = [ProjectionReceipt('source/runtime', 'market_snapshots', -2 + i, ref.kind, ref.record_id)
                    for i, ref in enumerate(refs)]
        assert client.append_projection_receipts(receipts, authority_uuid=authority) is None
        keys = [row.row() for row in receipts]
        assert client.get_projection_receipts(keys, authority_uuid=authority) == receipts
        missing = ('other-source', 'market_snapshots', -2, refs[0].kind, refs[0].record_id)
        assert client.get_projection_receipts([missing], authority_uuid=authority) == [None]
        assert receiver.import_projection_records(authority, records) is None
        receiver.append_projection_receipts(receipts, authority_uuid=authority)
        assert receiver.get_projection_records(ids[::-1], authority) == records[::-1]
        assert receiver.get_projection_receipts(keys, authority_uuid=authority) == receipts
        assert receiver.scalar_authority_role() == 'REPLICA'
    with PayloadReader(path) as reopened:
        assert reopened.get_projection_records(ids, authority) == records


@pytest.mark.parametrize('profile_id,capability', [
    ('watermelon-research-v401', 'public-raw-watermelon-v401-v1'),
    ('coconut-historical-v6', 'public-raw-coconut-historical-v6-v1'),
])
def test_raw_profile_capability_has_matching_durable_group_support(tmp_path, monkeypatch, profile_id, capability):
    from polybot_observability.market_data_raw_profiles import raw_profile
    path = tmp_path / 'public.db'
    profile = raw_profile(profile_id)
    groups = []
    for table in profile.tables.values():
        info = {row[1]: row for row in table.info}
        values = tuple(0 if info[name][2] == 'INTEGER' else .7 if info[name][2] == 'REAL'
                       else '[]' if name.endswith('_json') else 'public-source'
                       for name in table.public_columns)
        groups.append(PublicProjection(table.kind, values))
    with PayloadStore(path) as writer, PayloadReader(path) as reader:
        client = client_for(writer, monkeypatch)
        client.require_capabilities({capability})
        authority = client.scalar_authority_identity()
        refs = client.put_public_projections(groups)
        ids = [ref.record_id for ref in refs]
        assert [record.projection for record in reader.get_projection_records(ids, authority)] == groups
    with PayloadReader(path) as reopened:
        assert [record.projection for record in reopened.get_projection_records(ids, authority)] == groups


def test_bound_projection_rpc_and_legacy_read_path_preserve_order_missingness_and_identity(tmp_path, monkeypatch):
    with PayloadStore(tmp_path / 'public.db') as store:
        client = client_for(store, monkeypatch)
        authority = store.scalar_authority_identity()
        ref, = store.put_public_projections([quote()])
        receipt = ProjectionReceipt('source/runtime', 'book', 7, ref.kind, ref.record_id)
        store.append_projection_receipts([receipt], authority_uuid=authority)
        missing = ProjectionReceipt('another-runtime', 'book', 7, ref.kind, ref.record_id)
        keys = [receipt.row(), missing.row(), receipt.row()]
        record, = store.get_projection_records([ref.record_id], authority)
        before = (store.stats(), store.projection_stats())
        assert client.get_projection_bound_records(keys, authority_uuid=authority) == [record, None, record]
        # Explicitly advertised older contract uses its original verified pair
        # of reads, rather than sending a new unsupported operation.
        operations = []
        original_request = client._request
        def old_request(operation, **fields):
            operations.append(operation)
            if operation == 'capabilities':
                return {'protocol': 1, 'contracts': ['public-projection-block-store-v1']}
            assert operation != 'get_projection_bound_records'
            return original_request(operation, **fields)
        monkeypatch.setattr(client, '_request', old_request)
        client.capabilities()
        assert client.get_projection_bound_records(keys, authority_uuid=authority) == [record, None, record]
        assert operations == ['capabilities', 'get_projection_receipts', 'get_projection_records']
        assert (store.stats(), store.projection_stats()) == before


@pytest.mark.parametrize('damage', ['count', 'kind', 'id', 'body'])
def test_bound_projection_rpc_rejects_forged_response(tmp_path, monkeypatch, damage):
    with PayloadStore(tmp_path / 'public.db') as store:
        client = StoreClient('unused')
        client._observed_contracts = frozenset({'public-projection-bound-reads-v1'})
        authority = store.scalar_authority_identity()
        refs = store.put_public_projections([quote(), quote(.84)])
        record, = store.get_projection_records([refs[0].record_id], authority)
        receipt = ProjectionReceipt('source', 'table', 1, refs[0].kind, refs[0].record_id)
        value = record.to_wire()
        if damage == 'kind':
            receipt = ProjectionReceipt('source', 'table', 1, 'token-quote-v1', refs[0].record_id)
        elif damage == 'id':
            value = store.get_projection_records([refs[1].record_id], authority)[0].to_wire()
        elif damage == 'body':
            value = {'invalid': 'typed-body'}
        result = [] if damage == 'count' else [{'receipt': receipt.to_wire(), 'record': value}]
        monkeypatch.setattr(client, '_request', lambda *args, **kwargs: result)
        with pytest.raises((ProtocolError, CorruptProjectionError)):
            client.get_projection_bound_records([receipt.row()], authority_uuid=authority)


def test_bound_projection_dispatch_rejects_unrequested_fields(tmp_path):
    with PayloadStore(tmp_path / 'public.db') as store:
        with pytest.raises(ProtocolError):
            MarketDataService._dispatch(None, store, {'v': 1, 'op': 'get_projection_bound_records',
                'authority_uuid': store.scalar_authority_identity(), 'identities': [], 'skip_proof': True})


@pytest.mark.parametrize('field,value', [('namespace', 'other-source'), ('origin_table', 'another-table'), ('original_id', 99)])
def test_bound_projection_rpc_rejects_swapped_receipt_for_identical_public_record(tmp_path, monkeypatch, field, value):
    with PayloadStore(tmp_path / 'public.db') as store:
        client = StoreClient('unused')
        client._observed_contracts = frozenset({'public-projection-bound-reads-v1'})
        authority = store.scalar_authority_identity()
        ref, = store.put_public_projections([quote()])
        record, = store.get_projection_records([ref.record_id], authority)
        receipt = ProjectionReceipt('source', 'table', 1, ref.kind, ref.record_id)
        forged = receipt.to_wire()
        forged[field] = value
        monkeypatch.setattr(client, '_request', lambda *args, **kwargs: [
            {'receipt': forged, 'record': record.to_wire()}])
        with pytest.raises(CorruptProjectionError, match='receipt'):
            client.get_projection_bound_records([receipt.row()], authority_uuid=authority)


@pytest.mark.parametrize('message', [
    {'v': 1, 'op': 'put_public_projections', 'projections': [], 'private_wallet': 'forbidden'},
    {'v': 1, 'op': 'put_public_projections', 'projections': [{'kind': 'private-order-v1', 'values': []}]},
    {'v': 1, 'op': 'put_public_projections', 'projections': [{**quote().to_wire(), 'config_hash': 'private'}]},
    {'v': 1, 'op': 'put_public_projections', 'projections': [quote().to_wire()] * 1025},
])
def test_projection_dispatch_rejects_nonpublic_and_unbounded_writes(tmp_path, message):
    with PayloadStore(tmp_path / 'public.db') as store:
        before = store.projection_stats()
        with pytest.raises((ValueError, TypeError, ProtocolError, StoreLimitError)):
            MarketDataService._dispatch(None, store, message)
        assert store.projection_stats() == before


def test_projection_client_rejects_wrong_ack_and_swapped_readback(tmp_path, monkeypatch):
    with PayloadStore(tmp_path / 'public.db') as store:
        refs = store.put_public_projections([quote(), quote(0.83)])
        authority = store.scalar_authority_identity()
        records = store.get_projection_records([ref.record_id for ref in refs], authority)
        client = StoreClient('unused')
        monkeypatch.setattr(client, '_request', lambda *args, **kwargs: [refs[1].to_wire()])
        with pytest.raises(ProtocolError, match='ACK kind/hash/authority'):
            client.put_public_projections([quote()])
        monkeypatch.setattr(client, '_request', lambda *args, **kwargs: [record.to_wire() for record in records[::-1]])
        with pytest.raises(CorruptProjectionError, match='identities'):
            client.get_projection_records([ref.record_id for ref in refs], authority)
        monkeypatch.setattr(client, '_request', lambda *args, **kwargs: 'accepted-not-confirmed')
        with pytest.raises(ProtocolError, match='import ACK'):
            client.import_projection_records(authority, records)
        receipt = ProjectionReceipt('source', 'market_snapshots', 7, refs[0].kind, refs[0].record_id)
        with pytest.raises(ProtocolError, match='receipt ACK'):
            client.append_projection_receipts([receipt], authority_uuid=authority)


def test_projection_missing_ids_are_machine_readable_and_kind_cannot_be_forged(tmp_path):
    with PayloadStore(tmp_path / 'public.db') as store:
        authority = store.scalar_authority_identity()
        with pytest.raises(MissingProjectionRecordError) as error:
            MarketDataService._dispatch(None, store, {
                'v': 1, 'op': 'get_projection_records', 'record_ids': [999], 'authority_uuid': authority,
            })
        assert _error_response(error.value)['record_ids'] == [999]
        ref = store.put_public_projections([quote()])[0]
        wrong = ProjectionReference(authority, 'token-quote-v1', ref.record_id, ref.sha256)
        with pytest.raises((ValueError, CorruptProjectionError)):
            ProjectionRecord(wrong, quote())


@pytest.mark.parametrize('operation,key', [
    ('put_public_projections', 'projections'),
    ('import_projection_records', 'records'),
    ('append_projection_receipts', 'receipts'),
])
def test_projection_mutations_use_existing_storage_gate(tmp_path, monkeypatch, operation, key):
    service = MarketDataService(tmp_path / 'public.db', tmp_path / 'unused.sock', storage_root=tmp_path)
    calls = []
    monkeypatch.setattr(service, '_check_storage', lambda: None)
    def block():
        calls.append(operation)
        raise ServiceUnavailableError('storage gate')
    monkeypatch.setattr(service, '_check_write_capacity', block)
    message = {'v': 1, 'op': operation, key: []}
    if operation != 'put_public_projections':
        message['authority_uuid'] = '7fb3eb37-fae1-4a12-83d6-09339ab4d5b7'
    request = _Request(message, time.monotonic() + 10)
    service._requests.put_nowait(request)
    service._stopping.set()
    service._write_loop()
    assert calls == [operation]
    assert request.completed.is_set() and request.response['ok'] is False
    with PayloadReader(tmp_path / 'public.db') as reader:
        assert not any(reader.projection_stats().values())


def test_lost_projection_ack_is_safe_to_retry(tmp_path, monkeypatch):
    with PayloadStore(tmp_path / 'public.db') as store:
        client = StoreClient('unused')
        refs = []
        def lose_ack(operation, **fields):
            refs.extend(MarketDataService._dispatch(None, store, {'v': 1, 'op': operation, **fields}))
            raise TimeoutError('unknown commit result')
        monkeypatch.setattr(client, '_request', lose_ack)
        with pytest.raises(TimeoutError):
            client.put_public_projections([quote()])
        retry = client_for(store, monkeypatch).put_public_projections([quote()])
        assert [row.to_wire() for row in retry] == refs
