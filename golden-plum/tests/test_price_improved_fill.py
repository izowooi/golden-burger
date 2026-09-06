from decimal import Decimal
import socket

import pytest
from polybot_observability import ClobResponseContractError
from .test_api_contracts import _fee_evidence_wrapper


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('network forbidden'))


def prepared(wrapper, requested='6.6666', matched='7.042251', limit='.75', *, fixed_detail=False, maker=True, side='BUY'):
    response = dict(success=True, orderID='order-fee', status='matched')
    if maker and side == 'BUY':
        response.update(makingAmount='5000000', takingAmount=str(int(Decimal(requested)*1000000)))
    sid=wrapper.execution_ledger.record_submission(token_id='token-fee',side=side,
        requested_price=float(limit),requested_size=float(requested),result=response,simulation=False)
    scale=Decimal(1000000) if fixed_detail else Decimal(1)
    wrapper.execution_ledger.record_order_status(sid, dict(id='order-fee',status='MATCHED',
        original_size=str(Decimal(requested)*scale),size_matched=str(Decimal(matched)*scale),
        price=limit,associate_trades=['trade-fee']), quantity_tolerance=.0001)
    return sid, wrapper.execution_ledger.pending_submissions()[0]


def payload(size, price, **kw):
    return dict(id='trade-fee',status='CONFIRMED',taker_order_id='order-fee',trader_side='TAKER',
                side='BUY',asset_id='token-fee',size=size,price=price,fee_rate_bps=0,maker_orders=[],**kw)


@pytest.mark.parametrize('requested,matched,limit,price,maker',[
    ('6.6666','7.042251','.75','.71',True),
    ('9.0909','9.615383','.55','.52',False),
])
@pytest.mark.parametrize('fixed_trade',[False,True])
@pytest.mark.parametrize('fixed_detail',[False,True])
def test_price_improvement_over_five_percent_recovers_exact_fee_and_size(tmp_path,requested,matched,limit,price,maker,fixed_trade,fixed_detail):
    w=_fee_evidence_wrapper(tmp_path)
    sid,pending=prepared(w,requested,matched,limit,fixed_detail=fixed_detail,maker=maker)
    raw=str(int(Decimal(matched)*1000000)) if fixed_trade else matched
    enriched=w._attach_clob_v2_fee_evidence(payload(raw,price),pending=pending,order_id='order-fee')
    w.execution_ledger.record_fill(sid,'order-fee',enriched)
    assert w.execution_ledger.finish_reconciliation(sid)
    with w._open_evidence_db_read_only() as c:
        row=c.execute('SELECT size,price,fee_amount_usdc,domain_error FROM order_fills').fetchone()
        assert row['size']==pytest.approx(float(matched))
        assert row['price']==float(price)
        assert row['fee_amount_usdc']==pytest.approx(.0725 if price=='.71' else .12)
        assert row['domain_error'] is None


def test_improved_quantity_does_not_permit_more_cash(tmp_path):
    w=_fee_evidence_wrapper(tmp_path);_,p=prepared(w)
    with pytest.raises(ClobResponseContractError):
        w._attach_clob_v2_fee_evidence(payload('7.042251','.75'),pending=p,order_id='order-fee')
    with w._open_evidence_db_read_only() as c:assert c.execute('SELECT COUNT(*) FROM order_fills').fetchone()[0]==0


def test_cumulative_cost_and_idempotent_retry(tmp_path):
    w=_fee_evidence_wrapper(tmp_path);sid,p=prepared(w,matched='8')
    a=w._attach_clob_v2_fee_evidence(payload('4','.75'),pending=p,order_id='order-fee')
    w.execution_ledger.record_fill(sid,'order-fee',a)
    w._attach_clob_v2_fee_evidence(payload('4','.75'),pending=p,order_id='order-fee')
    b=payload('4','.75');b['id']='second-trade'
    with pytest.raises(ClobResponseContractError,match='cash envelope'):
        w._attach_clob_v2_fee_evidence(b,pending=p,order_id='order-fee')


@pytest.mark.parametrize('change',[{'asset_id':'foreign-token'},{'taker_order_id':'foreign-order'},
                                  {'size':'Infinity'},{'size':'704225100000000000'},{'price':'NaN'}])
def test_invalid_identity_units_and_numbers_do_not_create_fill(tmp_path,change):
    w=_fee_evidence_wrapper(tmp_path);_,p=prepared(w)
    raw=payload('7.042251','.71');raw.update(change)
    with pytest.raises(ClobResponseContractError):
        w._attach_clob_v2_fee_evidence(raw,pending=p,order_id='order-fee')


def test_sell_cannot_expand_quantity_on_price_improvement(tmp_path):
    w=_fee_evidence_wrapper(tmp_path);_,p=prepared(w,requested='5',matched='5',limit='.70',maker=False,side='SELL')
    raw=payload('5.1','.80');raw['side']='SELL'
    with pytest.raises(ClobResponseContractError):
        w._attach_clob_v2_fee_evidence(raw,pending=p,order_id='order-fee')
