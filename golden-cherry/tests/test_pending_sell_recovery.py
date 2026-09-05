from copy import deepcopy
from types import SimpleNamespace
import json
import sqlite3

import pytest
from polybot_observability import ExecutionLedger

from polybot.config import TradingConfig
from polybot.db.models import Trade, TradeStatus, init_database
from polybot.db.repository import TradeRepository
from polybot.pending_sell_recovery import PROOF, exact_taker_fills, recover_fully_sold_legacy_position, terminal_ack
from polybot.strategy.trader import Trader
from tests.test_exact_history_analyzer import _add_confirmed_order


def fill(**changes):
    return {"id":"trade-sell", "taker_order_id":"SELL", "asset_id":"token",
            "status":"CONFIRMED", "side":"SELL", "size":"5", "price":"0.9",
            "fee_rate_bps":"0", "trader_side":"TAKER", "bucket_index":0,
            "match_time":"2026-08-20T00:00:00Z", **changes}


class Api:
    def __init__(self):
        self.rows=[fill()]
        self.after=None
        self.cancelled=[]
        self.orders=[]
        self.ack={"canceled":[],"not_canceled":{"SELL":"Can't cancel orders that have already been fully filled."}}

    def get_open_orders(self, params, *, only_first_page):
        assert only_first_page is False
        return self.orders

    def get_pre_migration_orders(self):
        return []

    def get_trades(self, params, *, only_first_page):
        assert only_first_page is False
        return self.after if self.cancelled and self.after is not None else self.rows

    def cancel_orders(self, ids):
        self.cancelled.append(ids)
        return self.ack


@pytest.fixture
def setup(tmp_path):
    db=tmp_path/'trades.db'
    Session=init_database(str(db));ledger=ExecutionLedger(db,strategy_name='golden-cherry')
    _add_confirmed_order(db,'BUY','BUY','token')
    sid=ledger.record_submission(token_id='token',side='SELL',requested_price=.9,requested_size=5,
        result={'success':True,'orderID':'SELL','status':'DELAYED'},simulation=False)
    ledger.record_fill(sid,'SELL',fill())
    ledger.record_recovered_trade_associations(sid,'SELL',['trade-sell'])
    assert ledger.finish_reconciliation(sid) is False
    with sqlite3.connect(db) as c:
        c.execute("UPDATE order_submissions SET submitted_at='2020-01-01T00:00:00Z' WHERE submission_id=?",(sid,))
    session=Session()
    trade=Trade(condition_id='condition',token_id='token',status=TradeStatus.PENDING_SELL,
        buy_order_id='BUY',buy_price=.8,buy_shares=5,buy_amount=4,sell_order_id='SELL',
        exit_reason='take_profit_pending_confirmed_fill')
    session.add(trade);session.commit()
    api=Api();wrapper=SimpleNamespace(client=api,execution_ledger=ledger,simulation_mode=False)
    yield db,session,TradeRepository(session),wrapper,trade,sid
    session.close()


def test_terminal_fill_ack_recovers_without_inventing_original_signed_size(setup):
    db,session,repo,wrapper,trade,sid=setup
    assert Trader(repo,wrapper,TradingConfig()).reconcile_pending_sell(trade)
    assert trade.status==TradeStatus.COMPLETED
    assert trade.realized_pnl==pytest.approx(.5)
    assert wrapper.client.cancelled==[['SELL']]
    with sqlite3.connect(db) as c:
        row=c.execute('select making_amount,taking_amount,latest_size_matched,needs_reconciliation,reconciliation_proof,outcome_resolution_reason from order_submissions where submission_id=?',(sid,)).fetchone()
        assert row[:5]==(None,None,5.0,0,PROOF)
        receipt=json.loads(row[5]);assert receipt['original_signed_amount_reconstructed'] is False
        assert receipt['confirmed_fills'][0]['size']==5
        assert c.execute('select original_size from order_status_events where submission_id=?',(sid,)).fetchone()[0] is None
    assert recover_fully_sold_legacy_position(repo,wrapper,trade) is False
    assert len(wrapper.client.cancelled)==1


@pytest.mark.parametrize('ack',[
    {'canceled':['SELL'],'not_canceled':{}},
    {'canceled':[],'not_canceled':{'SELL':'order not found'}},
])
def test_terminal_cancel_keeps_actual_matched_quantity_not_zero(setup,ack):
    db,session,repo,wrapper,trade,sid=setup
    wrapper.client.ack=ack
    assert recover_fully_sold_legacy_position(repo,wrapper,trade)
    with sqlite3.connect(db) as c:
        assert c.execute('select latest_order_status,latest_size_matched from order_submissions where submission_id=?',(sid,)).fetchone()==('CANCELED',5.0)


@pytest.mark.parametrize('change',[
    {'fee_rate_bps':None}, {'fee_rate_bps':'20'}, {'asset_id':'other'},
    {'status':'MINED'}, {'side':'BUY'}, {'size':'nan'}, {'bucket_index':.5},
])
def test_uncertain_fill_does_not_issue_cancel_or_release_pending(setup,change):
    db,session,repo,wrapper,trade,sid=setup
    wrapper.client.rows=[fill(**change)]
    with pytest.raises((ValueError,TypeError)):
        recover_fully_sold_legacy_position(repo,wrapper,trade)
    assert not wrapper.client.cancelled
    assert trade.status==TradeStatus.PENDING_SELL


def test_partial_quantity_is_not_promoted_to_a_full_sale(setup):
    db,session,repo,wrapper,trade,sid=setup
    wrapper.client.rows=[fill(size='4')]
    assert recover_fully_sold_legacy_position(repo,wrapper,trade) is False
    assert not wrapper.client.cancelled


def test_live_order_is_not_cancelled_by_legacy_recovery(setup):
    db,session,repo,wrapper,trade,sid=setup
    wrapper.client.orders=[{'id':'SELL','status':'LIVE'}]
    with pytest.raises(ValueError,match='still present'):
        recover_fully_sold_legacy_position(repo,wrapper,trade)
    assert not wrapper.client.cancelled


def test_racing_fill_catalog_does_not_publish_terminal_state(setup):
    db,session,repo,wrapper,trade,sid=setup
    wrapper.client.after=[fill(price='.91')]
    with pytest.raises(ValueError,match='changed'):
        recover_fully_sold_legacy_position(repo,wrapper,trade)
    with sqlite3.connect(db) as c:
        assert c.execute('select latest_order_status from order_submissions where submission_id=?',(sid,)).fetchone()[0] is None


def test_persisted_fill_mismatch_prevents_any_terminal_publication(setup):
    db,session,repo,wrapper,trade,sid=setup
    with sqlite3.connect(db) as c:c.execute('update order_fills set price=.91 where submission_id=?',(sid,))
    with pytest.raises(ValueError,match='persisted fills'):
        recover_fully_sold_legacy_position(repo,wrapper,trade)
    with sqlite3.connect(db) as c:
        assert c.execute('select latest_order_status from order_submissions where submission_id=?',(sid,)).fetchone()[0] is None


@pytest.mark.parametrize('ack',[
    {'canceled':['OTHER'],'not_canceled':{}},
    {'canceled':[],'not_canceled':{'SELL':'temporarily unavailable'}},
    {'canceled':[],'not_canceled':{'SELL':'not found','OTHER':'not found'}},
    {},
])
def test_ambiguous_or_wrong_order_ack_rejected(ack):
    with pytest.raises((ValueError,TypeError,RuntimeError)):
        terminal_ack(ack,'SELL')


def test_conflicting_duplicate_authenticated_bucket_rejected():
    with pytest.raises(ValueError,match='conflicting duplicate'):
        exact_taker_fills([fill(),fill(size='4')],'SELL','token')
