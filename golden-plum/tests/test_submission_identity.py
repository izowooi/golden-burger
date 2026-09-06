from dataclasses import replace
from functools import partial
from decimal import Decimal
import json
import socket
import sqlite3
import httpx

import pytest
from eth_abi import encode
from eth_utils import keccak
from py_clob_client_v2.config import get_contract_config
from py_clob_client_v2.exceptions import PolyApiException
from py_clob_client_v2.order_utils.model.order_data_v2 import SignedOrderV2
from polybot_observability import SubmissionEvidenceError
from polybot.submission_identity import PlumExecutionLedger as IdentityLedger, signed_sell_identity, account_fingerprint

MAKER='0x'+'2'*40
ZERO='0x'+'0'*64
PlumExecutionLedger=partial(IdentityLedger,account_identity=account_fingerprint(137,MAKER,3))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**kw:pytest.fail('actual network forbidden'))
    monkeypatch.setattr(socket,'create_connection',lambda *a,**kw:pytest.fail('actual network forbidden'))


def signed(**changes):
    base=SignedOrderV2(salt='123456',maker=MAKER,signer=MAKER,tokenId='12345',
        makerAmount='5100000',takerAmount='1377000',side=1,signatureType=3,
        timestamp='1788688800000',metadata=ZERO,builder=ZERO,signature='0xdeadbeef')
    return replace(base,**changes)


def identity(order=None, neg=False):
    return signed_sell_identity(order or signed(),chain_id=137,neg_risk=neg,funder=MAKER,signature_type=3)


def kwargs(submit):
    return dict(token_id='12345',side='SELL',requested_price=.27,requested_size=5.1,
                signed_making_amount=5100000,signed_taking_amount=1377000,submit=submit)


def detail(i,**kw):
    return dict(id=i['predicted_order_id'],asset_id=i['token_id'],side='SELL',
                original_size='5.1',price='.27',status='MATCHED',**kw)


def test_sdk_hash_matches_independent_eip712_encoding_and_is_not_signature_hash():
    o=signed();i=identity(o)
    types='Order(uint256 salt,address maker,address signer,uint256 tokenId,uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,uint256 timestamp,bytes32 metadata,bytes32 builder)'
    struct=keccak(encode(['bytes32','uint256','address','address','uint256','uint256','uint256','uint8','uint8','uint256','bytes32','bytes32'],
        [keccak(text=types),123456,MAKER,MAKER,12345,5100000,1377000,1,3,1788688800000,bytes(32),bytes(32)]))
    domain=keccak(encode(['bytes32','bytes32','bytes32','uint256','address'],[
        keccak(text='EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)'),
        keccak(text='Polymarket CTF Exchange'),keccak(text='2'),137,get_contract_config(137).exchange_v2]))
    assert i['predicted_order_id']=='0x'+keccak(b'\x19\x01'+domain+struct).hex()
    assert identity(replace(o,signature='0x00',expiration='123'))==i
    assert identity(neg=True)['predicted_order_id']!=i['predicted_order_id']
    assert identity(signed(salt='999'))['predicted_order_id']!=i['predicted_order_id']
    with pytest.raises(ValueError):
        signed_sell_identity(o,chain_id=137,neg_risk=False,funder='0x'+'3'*40,signature_type=3)


def test_hash_is_durable_before_post_and_response_verified_without_secrets(tmp_path):
    path=tmp_path/'trades.db';ledger=PlumExecutionLedger(path,strategy_name='golden-plum');i=identity()
    def post():
        with sqlite3.connect(path) as con:
            assert con.execute('SELECT predicted_order_id FROM plum_sell_request_identity').fetchone()[0]==i['predicted_order_id']
            assert con.execute('SELECT order_id FROM order_submissions').fetchone()[0] is None
        return dict(success=True,orderID=i['predicted_order_id'],status='delayed')
    ledger.submit_sell_with_identity(i,**kwargs(post))
    with sqlite3.connect(path) as con:
        assert con.execute('SELECT response_verified FROM plum_sell_request_identity').fetchone()[0]==1
        values=str(con.execute('SELECT * FROM plum_sell_request_identity').fetchall())
        assert MAKER not in values and 'deadbeef' not in values
    with pytest.raises(SubmissionEvidenceError,match='resubmit'):
        ledger.submit_sell_with_identity(i,**kwargs(lambda:pytest.fail('duplicate POST')))


def unknown(ledger,i):
    def failed():raise PolyApiException(httpx.Response(500,json={'error':'rpc DeadlineExceeded fixture'}))
    with pytest.raises(SubmissionEvidenceError):
        ledger.submit_sell_with_identity(i,**kwargs(failed))


def test_unknown_sell_links_only_verified_hash_after_restart_without_fill_claim(tmp_path):
    p=tmp_path/'trades.db';l=PlumExecutionLedger(p,strategy_name='golden-plum');i=identity();unknown(l,i)
    l=PlumExecutionLedger(p,strategy_name='golden-plum')
    assert l.recover_sell_order_ids(lambda oid: {})['rejected']==1
    def timeout(oid):raise TimeoutError('offline fixture')
    assert l.recover_sell_order_ids(timeout)['unavailable']==1
    assert l.unresolved_submission_count(side='SELL')==1
    assert l.recover_sell_order_ids(lambda oid:detail(i))['linked']==1
    with sqlite3.connect(p) as c:
        row=c.execute('SELECT response_status,order_id,outcome_resolution,needs_reconciliation FROM order_submissions').fetchone()
        assert row==('SUBMIT_OUTCOME_UNKNOWN',i['predicted_order_id'],'ORDER_ID_LINKED',1)
        assert c.execute('SELECT COUNT(*) FROM order_fills').fetchone()[0]==0


@pytest.mark.parametrize('field,value',[('id','0x'+'f'*64),('asset_id','foreign'),('side','BUY'),
    ('original_size','5.09'),('original_size','NaN'),('price','.26'),('status','UNKNOWN')])
def test_wrong_lookup_never_releases_unknown(tmp_path,field,value):
    l=PlumExecutionLedger(tmp_path/'trades.db',strategy_name='golden-plum');i=identity();unknown(l,i)
    d=detail(i);d[field]=value
    assert l.recover_sell_order_ids(lambda oid:d)['rejected']==1
    assert l.unresolved_submission_count(side='SELL')==1


def test_legacy_unknown_without_original_hash_is_not_guessed_or_cleared(tmp_path):
    l=PlumExecutionLedger(tmp_path/'trades.db',strategy_name='golden-plum')
    sid=l.record_intent(token_id='12345',side='SELL',requested_price=.27,requested_size=8.92,simulation=False)
    l.record_submission_error(sid,PolyApiException(httpx.Response(500,json={'error':'rpc DeadlineExceeded fixture'})))
    stats=l.recover_sell_order_ids(lambda oid:pytest.fail('no hash means no lookup'))
    assert stats['legacy_unidentifiable']==1 and stats['checked']==0
    assert l.unresolved_submission_count(side='SELL')==1


def test_write_failure_means_no_post(tmp_path):
    p=tmp_path/'trades.db';l=PlumExecutionLedger(p,strategy_name='golden-plum');i=identity()
    with sqlite3.connect(p) as c:c.execute("CREATE TRIGGER reject_hash BEFORE INSERT ON plum_sell_request_identity BEGIN SELECT RAISE(ABORT,'fixture write failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        l.submit_sell_with_identity(i,**kwargs(lambda:pytest.fail('must not POST')))
    assert l.unresolved_submission_count(side='SELL')==1


def test_response_hash_mismatch_is_preserved_but_never_blindly_linked(tmp_path):
    p=tmp_path/'trades.db';l=PlumExecutionLedger(p,strategy_name='golden-plum');i=identity()
    with pytest.raises(SubmissionEvidenceError):
        l.submit_sell_with_identity(i,**kwargs(lambda:dict(success=True,orderID='0x'+'b'*64)))
    with sqlite3.connect(p) as c:
        assert c.execute('SELECT mismatch FROM plum_sell_request_identity').fetchone()[0]==1
        assert c.execute('SELECT order_id FROM order_submissions').fetchone()[0] is None
        assert c.execute('SELECT response_status FROM order_submissions').fetchone()[0]=='SUBMIT_OUTCOME_UNKNOWN'
    assert l.recover_sell_order_ids(lambda oid:pytest.fail('mismatch needs review'))['checked']==0


def test_missing_old_order_does_not_starve_next_identity(tmp_path):
    l=PlumExecutionLedger(tmp_path/'trades.db',strategy_name='golden-plum')
    a=identity();unknown(l,a)
    b=identity(signed(tokenId='54321',salt='9'))
    k=kwargs(lambda:(_ for _ in ()).throw(RuntimeError('500 fixture')));k['token_id']='54321'
    with pytest.raises(SubmissionEvidenceError):l.submit_sell_with_identity(b,**k)
    calls=[]
    def lookup(oid):calls.append(oid);return {}
    l.recover_sell_order_ids(lookup)
    l.recover_sell_order_ids(lookup)
    assert calls==[a['predicted_order_id'],b['predicted_order_id']]


def test_account_change_and_access_denial_never_clear_or_reroute(tmp_path):
    path=tmp_path/'trades.db';l=PlumExecutionLedger(path,strategy_name='golden-plum');i=identity();unknown(l,i)
    other=IdentityLedger(path,strategy_name='golden-plum',account_identity='0'*64)
    assert other.recover_sell_order_ids(lambda oid:pytest.fail('foreign account lookup'))['rejected']==1
    calls=[]
    def denied(oid):
        calls.append(oid)
        raise PolyApiException(httpx.Response(451,json={'error':'fixture access denial'}))
    assert l.recover_sell_order_ids(denied)['unavailable']==1
    assert calls==[i['predicted_order_id']]
    assert l.unresolved_submission_count(side='SELL')==1


def test_wrapper_unknown_sell_recovers_then_runs_normal_fill_reconciliation(tmp_path,monkeypatch):
    from .test_api_contracts import _fee_evidence_wrapper
    w=_fee_evidence_wrapper(tmp_path)
    i=identity();i.update(token_id='token-fee',taker_amount_micros='3570000',
                         account_fingerprint=w.execution_ledger.account_identity)
    monkeypatch.setattr(w,'_signed_sell_identity',lambda *a,**kw:i)
    from types import SimpleNamespace
    class Client:
        def __init__(self):self.get_order_calls=0;self.posts=0
        def get_tick_size(self,token):return '.01'
        def get_neg_risk(self,token):return False
        def get_clob_market_info(self,condition):
            return dict(c='condition-fee',t=[dict(t='token-fee',o='Yes'),dict(t='token-no',o='No')],fd=dict(r='.05',e=1,to=True))
        def create_order(self,args,options=None):
            assert options.neg_risk is False
            return SimpleNamespace(makerAmount='5100000',takerAmount='3570000')
        def post_order(self,*args):
            self.posts+=1
            raise PolyApiException(httpx.Response(500,json={'error':'rpc DeadlineExceeded'}))
        def get_order(self,oid):
            self.get_order_calls+=1
            assert oid==i['predicted_order_id']
            return dict(id=oid,asset_id='token-fee',side='SELL',status='MATCHED',original_size='5.1',
                        size_matched='5.1',price='.70',associate_trades=['fill-one'])
        def get_trades(self,params,only_first_page=True):
            assert params.id=='fill-one'
            return [dict(id='fill-one',status='CONFIRMED',taker_order_id=i['predicted_order_id'],
                         trader_side='TAKER',side='SELL',asset_id='token-fee',size='5.1',price='.70',fee_rate_bps=0)]
    client=Client();w._client=client
    result=w.place_limit_order('token-fee',.70,5.1,'SELL',order_type='FOK')
    assert result['submission_outcome_unknown'] is True
    # A new wrapper represents process restart. No second POST is made.
    from polybot.api.clob_client import ClobClientWrapper
    w=ClobClientWrapper(w.config,False,audit_db_path=w.execution_ledger.db_path,strategy_name='golden-plum')
    w._client=client;w._initialized=True
    stats=w.reconcile_order_ledger()
    assert stats['sell_identity_linked']==1 and stats['completed']==1 and stats['errors']==0
    assert client.posts==1 and client.get_order_calls==1
    with w._open_evidence_db_read_only() as con:
        fill=con.execute('SELECT size,price,fee_amount_usdc FROM order_fills').fetchone()
        assert tuple(fill)==pytest.approx((5.1,.7,.05355))
