import json
import struct
import time
import uuid

import pytest

from polybot_observability.market_data_client import ProtocolError, ServiceUnavailableError, StoreClient
from polybot_observability.market_data_scalars import (
    CorruptScalarSnapshotError, MissingScalarRecordError, ScalarReceipt, ScalarSnapshot,
    ScalarReceiptConflictError, ScalarAuthorityError, ScalarRecordConflictError, ScalarReference,
)
from polybot_observability.market_data_service import MarketDataService, _Request, _error_response
from polybot_observability.market_data_store import PayloadReader, PayloadStore, StoreLimitError

AUTHORITY='ebf414b1-d7b0-4788-9a85-e3e8b3fa361b'


def value(timestamp=-0.0):
    return ScalarSnapshot('public-condition',float('inf'),float('-inf'),None,timestamp)


def client_for(store,monkeypatch):
    client=StoreClient('unused-no-socket')
    def dispatch(operation,**fields):
        request=json.loads(json.dumps({'v':1,'op':operation,**fields},allow_nan=False))
        response=MarketDataService._dispatch(None,store,request)
        return json.loads(json.dumps(response,allow_nan=False))
    monkeypatch.setattr(client,'_request',dispatch)
    return client


def test_pure_dispatch_client_json_contract_origin_replica_and_durable_hot_ack(tmp_path,monkeypatch):
    path=tmp_path/'public.db'
    with PayloadStore(path) as store,PayloadReader(path) as reader,PayloadStore(tmp_path/'replica.db') as replica:
        client=client_for(store,monkeypatch);replica_client=client_for(replica,monkeypatch)
        authority=client.scalar_authority_identity()
        assert client.scalar_authority_role()=='UNCLAIMED'
        snapshots=[value(),value(1),value(1.0),value(None)]
        refs=client.put_scalar_snapshots(snapshots)
        ids=[ref.record_id for ref in refs]
        assert all(ref.authority_uuid==authority for ref in refs)
        assert [r.snapshot for r in reader.get_scalar_records(ids,authority)]==snapshots
        assert [r.snapshot for r in client.get_scalar_records(ids[::-1],authority)]==snapshots[::-1]
        receipts=[ScalarReceipt('source',1,ref.record_id) for ref in refs]
        assert client.append_scalar_receipts(receipts,authority_uuid=authority) is None
        assert client.get_scalar_receipts([receipts[0].row(),('absent',1,ids[0])],authority_uuid=authority)==[receipts[0],None]
        records=client.get_scalar_records(ids,authority)
        assert replica_client.import_scalar_records(authority,records) is None
        assert replica_client.scalar_authority_role()=='REPLICA'
        assert replica_client.get_scalar_records(ids,authority)==records
        with pytest.raises(ScalarAuthorityError): replica_client.put_scalar_snapshots([value(900)])
        assert client.scalar_stats()['hot_count']==4


@pytest.mark.parametrize('message',[
    {'v':1,'op':'put_scalar_snapshots','snapshots':[],'private_wallet':'forbidden'},
    {'v':1,'op':'put_scalar_snapshots','snapshots':[{**value().to_wire(),'private_order':'forbidden'}]},
    {'v':1,'op':'append_scalar_receipts','authority_uuid':AUTHORITY,'receipts':[{'namespace':'n','original_id':1,'record_id':1,'strategy_state':'forbidden'}]},
    {'v':1,'op':'get_scalar_records','record_ids':['invalid'],'authority_uuid':AUTHORITY},
    {'v':1,'op':'get_scalar_receipts','identities':[['ns',1]],'authority_uuid':AUTHORITY},
    {'v':1,'op':'import_scalar_records','authority_uuid':AUTHORITY,'records':[{'snapshot':value().to_wire()}]},
])
def test_dispatch_rejects_private_malformed_and_unbounded_fields(tmp_path,message):
    with PayloadStore(tmp_path/'public.db') as store:
        with pytest.raises((ValueError,ProtocolError)):
            MarketDataService._dispatch(None,store,message)
        assert store.scalar_stats()['snapshot_count']==store.scalar_stats()['receipt_count']==0


@pytest.mark.parametrize('operation,key,record',[
    ('put_scalar_snapshots','snapshots',value().to_wire()),
    ('append_scalar_receipts','receipts',ScalarReceipt('n',1,1).to_wire()),
    ('import_scalar_records','records',{}),
])
def test_scalar_dispatch_batch_limit(tmp_path,operation,key,record):
    with PayloadStore(tmp_path/'public.db') as store:
        message={'v':1,'op':operation,key:[record]*1025}
        if operation!='put_scalar_snapshots':message['authority_uuid']=AUTHORITY
        with pytest.raises(StoreLimitError):MarketDataService._dispatch(None,store,message)


def test_client_rejects_wrong_or_unacknowledged_scalar_results(monkeypatch):
    client=StoreClient('unused-no-socket')
    monkeypatch.setattr(client,'_request',lambda *args,**kwargs:[ScalarReference(AUTHORITY,1,'0'*64).to_wire()])
    with pytest.raises(ProtocolError,match='ACK scalar hashes'):client.put_scalar_snapshots([value()])
    monkeypatch.setattr(client,'_request',lambda *args,**kwargs:[{'reference':ScalarReference(AUTHORITY,1,value().sha256).to_wire(),'snapshot':value(999).to_wire()}])
    with pytest.raises(CorruptScalarSnapshotError):client.get_scalar_records([1],AUTHORITY)
    monkeypatch.setattr(client,'_request',lambda *args,**kwargs:'not a commit ACK')
    with pytest.raises(ProtocolError,match='receipt ACK'):client.append_scalar_receipts([ScalarReceipt('ns',1,1)],authority_uuid=AUTHORITY)
    with pytest.raises(ProtocolError,match='import ACK'):client.import_scalar_records(AUTHORITY,[])
    monkeypatch.setattr(client,'_request',lambda *args,**kwargs:[ScalarReceipt('other',1,1).to_wire()])
    with pytest.raises(ProtocolError,match='identities'):client.get_scalar_receipts([('ns',1,1)],authority_uuid=AUTHORITY)


def test_missing_scalar_error_has_exact_machine_readable_ids(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        authority=store.scalar_authority_identity()
        with pytest.raises(MissingScalarRecordError) as failure:
            MarketDataService._dispatch(None,store,{'v':1,'op':'get_scalar_records','record_ids':[999],'authority_uuid':authority})
        response=_error_response(failure.value)
        assert response['ok'] is False and response['error']=='MissingScalarRecordError'
        assert response['record_ids']==[999]


@pytest.mark.parametrize('operation,key,records',[
    ('put_scalar_snapshots','snapshots',[value().to_wire()]),
    ('append_scalar_receipts','receipts',[ScalarReceipt('ns',1,1).to_wire()]),
    ('import_scalar_records','records',[]),
])
def test_new_scalar_mutations_pass_existing_capacity_gate_without_socket(tmp_path,monkeypatch,operation,key,records):
    service=MarketDataService(tmp_path/'public.db',tmp_path/'unused.sock',storage_root=tmp_path)
    calls=[]
    def block():
        calls.append(operation);raise ServiceUnavailableError('storage gate')
    monkeypatch.setattr(service,'_check_storage',lambda:None)
    monkeypatch.setattr(service,'_check_write_capacity',block)
    message={'v':1,'op':operation,key:records}
    if operation!='put_scalar_snapshots':message['authority_uuid']=AUTHORITY
    request=_Request(message,time.monotonic()+10)
    service._requests.put_nowait(request);service._stopping.set();service._write_loop()
    assert calls==[operation] and request.completed.is_set()
    assert request.response['ok'] is False and request.response['error']=='ServiceUnavailableError'
    with PayloadReader(tmp_path/'public.db') as reader:assert reader.scalar_stats()['snapshot_count']==0


def test_unknown_ack_outcome_retry_reuses_record_reference(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        client=StoreClient('unused-no-socket')
        def lose_ack(operation,**fields):
            MarketDataService._dispatch(None,store,{'v':1,'op':operation,**fields})
            raise TimeoutError('commit result unknown')
        monkeypatch.setattr(client,'_request',lose_ack)
        with pytest.raises(TimeoutError):client.put_scalar_snapshots([value()])
        ref=store.put_scalar_snapshots([value()])[0]
        assert ref.record_id==1 and store.scalar_stats()['snapshot_count']==1


@pytest.mark.parametrize('error',[MissingScalarRecordError([999]),CorruptScalarSnapshotError('corrupt cells'),
    ScalarReceiptConflictError('identity conflict'),ScalarAuthorityError('foreign authority'),ScalarRecordConflictError('record conflict')])
def test_framed_client_preserves_scalar_error_types_without_socket(monkeypatch,error):
    from polybot_observability import market_data_client as module
    response=json.dumps(_error_response(error)).encode()
    class InProcessTransport:
        def __init__(self):self.remaining=struct.pack('!I',len(response))+response
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def settimeout(self,seconds):assert seconds>0
        def connect(self,path):assert path=='no-real-socket'
        def sendall(self,raw):assert json.loads(raw[4:])['op']=='get_scalar_records'
        def recv(self,count):
            result,self.remaining=self.remaining[:count],self.remaining[count:];return result
    monkeypatch.setattr(module.socket,'socket',lambda *args:InProcessTransport())
    with pytest.raises(type(error)) as caught:StoreClient('no-real-socket').get_scalar_records([999],AUTHORITY)
    if isinstance(error,MissingScalarRecordError):assert caught.value.record_ids==[999]
