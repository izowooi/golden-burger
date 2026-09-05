"""Regression evidence from the 2026-09-05 MLB review, with no network/orders."""
from datetime import datetime, timedelta
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import text

import polybot.db.repository as repository_module
from polybot.config import TradingConfig
from polybot.db.models import Trade, TradeStatus, init_database, STOP_SELL_QUARANTINE_REASON
from polybot.db.repository import TradeRepository
from polybot.strategy.scanner import evaluate_trend_confirmation
from polybot.strategy.trader import Trader
from polybot_observability import ExecutionLedger
from tests.test_db_replay_contract import _strict_replay_db
from tests.test_lifecycle_mode import _build_bot
from tests.test_trader import _Repo, _Clob, _active_gamma, _candidate
from polybot.api.clob_client import _normalize_clob_resolution


SUBMITTED = datetime(2026, 9, 5, 1, 4, 2)


def _unknown_sell(tmp_path):
    path = tmp_path / 'unknown-sell.db'
    Session = init_database(str(path))
    ledger = ExecutionLedger(path, strategy_name='golden-plum')
    intent_id = ledger.record_submission(
        token_id='own-colorado-token', side='SELL', requested_price=.27,
        requested_size=8.92, result={'success':False, 'error':'HTTP500 timeout'}, simulation=False,
    )
    session = Session()
    session.execute(text("UPDATE order_submissions SET response_status='SUBMIT_OUTCOME_UNKNOWN', submitted_at=:at WHERE submission_id=:id"),
                    {'at':SUBMITTED.isoformat()+'Z','id':intent_id})
    trade = Trade(
        id=10, condition_id='colorado-condition', event_id='colorado-event',
        token_id='own-colorado-token', outcome='Colorado Rockies', mode='live',
        status=TradeStatus.HOLDING, buy_timestamp=SUBMITTED-timedelta(minutes=16),
        buy_order_id='own-buy', buy_price=.56, buy_shares=8.92857,
        buy_confirmed_size=8.92857, buy_confirmed_vwap=.56, buy_confirmed_fee_usdc=.11,
        # The old retry clock had already been reset when price recovered.
        sell_timestamp=SUBMITTED+timedelta(minutes=130),
        exit_reason='exit_sell_failure_retrying:absolute_stop',
    )
    session.add(trade); session.commit()
    repo = TradeRepository(session)
    trader = Trader(repo, SimpleNamespace(), TradingConfig(), simulation_mode=False)
    return session, repo, trader, trade, intent_id


def test_unknown_sell_recovery_preserves_original_clock_through_price_recovery(tmp_path):
    session,repo,trader,trade,intent = _unknown_sell(tmp_path)
    assert trader._sync_uncertain_sell(trade,now=SUBMITTED+timedelta(minutes=140))=='pending'
    assert trade.status is TradeStatus.PENDING_SELL
    assert trade.pending_sell_submission_id==intent
    assert trade.sell_timestamp==SUBMITTED
    assert trade.sell_order_id is None
    # No-price-signal cleanup may not erase an unknown POST.
    trader._clear_stop_sell_failure(trade)
    assert trade.sell_timestamp==SUBMITTED
    assert trader.reconcile_pending_sell(trade,now=SUBMITTED+timedelta(minutes=179)) is False
    assert trade.status is TradeStatus.PENDING_SELL
    assert trader.reconcile_pending_sell(trade,now=SUBMITTED+timedelta(minutes=180)) is False
    assert trade.status is TradeStatus.QUARANTINED
    assert trade.exit_reason==STOP_SELL_QUARANTINE_REASON
    assert trade.sell_timestamp==SUBMITTED
    assert trade.pending_sell_submission_id==intent
    assert trade.realized_pnl is None
    assert trade.settlement_pnl_assumption is None
    assert repo.get_entry_capacity_state()['total_reserved']==1
    assert repo.get_quarantine_state()['blocking']==0
    assert repo.get_quarantine_state()['isolated_stop_sell']==1
    # Lookup does not invent or mutate operator proof.
    assert session.execute(text('SELECT outcome_resolution FROM order_submissions')).scalar() is None


def test_existing_unknown_sell_prevents_price_and_resolution_side_effects(tmp_path):
    _session,_repo,trader,trade,_intent=_unknown_sell(tmp_path)
    # Empty clob/gamma objects prove no fresh order, book, or resolution is queried.
    assert trader.execute_sell(trade) is False
    assert trade.pending_sell_submission_id
    assert trade.status in (TradeStatus.PENDING_SELL, TradeStatus.QUARANTINED)
    assert trade.realized_pnl is None
    assert trade.resolution_value is None


@pytest.mark.parametrize('valid_proof',[True,False])
def test_unknown_sell_only_rearms_after_exact_persisted_no_order_proof(tmp_path, valid_proof):
    session,repo,trader,trade,intent=_unknown_sell(tmp_path)
    trader._sync_uncertain_sell(trade,now=SUBMITTED+timedelta(minutes=1))
    session.execute(text("UPDATE order_submissions SET outcome_resolution='NO_ORDER_CREATED', outcome_resolved_at=:at,outcome_resolution_reason=:reason WHERE submission_id=:id"),
                    {'id':intent,'at':SUBMITTED.isoformat()+'Z','reason':'explicit venue proof' if valid_proof else ''})
    session.commit()
    assert trader.reconcile_pending_sell(trade,now=SUBMITTED+timedelta(minutes=3)) is False
    if valid_proof:
        assert trade.status is TradeStatus.HOLDING
        assert trade.pending_sell_submission_id is None
        assert trade.sell_timestamp is None
        assert repo.get_uncertain_sell_submission(trade) is None
    else:
        assert trade.status is TradeStatus.QUARANTINED
        assert trade.pending_sell_submission_id==intent
    assert trade.realized_pnl is None


def test_linked_order_returns_to_exact_fill_reconciliation_without_clock_reset(tmp_path):
    session,repo,trader,trade,intent=_unknown_sell(tmp_path)
    trader._sync_uncertain_sell(trade,now=SUBMITTED+timedelta(minutes=1))
    session.execute(text("UPDATE order_submissions SET outcome_resolution='ORDER_ID_LINKED',outcome_resolved_at=:at,outcome_resolution_reason='exact authenticated match',order_id='venue-sell',needs_reconciliation=1 WHERE submission_id=:id"),
                    {'id':intent,'at':SUBMITTED.isoformat()+'Z'})
    session.commit()
    assert trader._sync_uncertain_sell(trade,now=SUBMITTED+timedelta(minutes=10))=='linked'
    assert trade.sell_order_id=='venue-sell'
    assert trade.sell_timestamp==SUBMITTED
    assert trade.realized_pnl is None
    assert repo.get_exact_sell_fill_evidence('venue-sell').needs_reconciliation is True


def test_unknown_sell_intent_cannot_be_rebound_to_another_token(tmp_path):
    _session,repo,trader,trade,intent=_unknown_sell(tmp_path)
    trade.pending_sell_submission_id=intent
    trade.token_id='different-token'
    with pytest.raises(ValueError,match='ownership'):
        repo.get_uncertain_sell_submission(trade)
    assert trader._sync_uncertain_sell(trade)=='invalid'
    assert trade.status is TradeStatus.QUARANTINED


def test_new_http_timeout_binds_intent_instead_of_starting_retryable_clock():
    repo, clob = _Repo(), _Clob(best_bid=.83,best_ask=.84,sell_vwap=.83,sell_limit=.83)
    intent = {
        'submission_id':'new-uncertain-sell','order_id':None,
        'submitted_at':SUBMITTED,'requested_size':5.05,'requested_price':.83,
        'outcome_resolution':None,'outcome_resolved_at':None,'outcome_resolution_reason':None,
    }
    sent = []
    repo.get_uncertain_sell_submission = lambda trade: intent if sent else None
    def post(**order):
        sent.append(order)
        return {'success':False,'submission_outcome_unknown':True,'quarantined':True}
    clob.place_limit_order = post
    trade = SimpleNamespace(
        id=20,status=TradeStatus.HOLDING,condition_id='condition-1',event_id='event-1',
        token_id='own-db-token',outcome='Yes',buy_shares=5.05,buy_price=.99,
        buy_confirmed_vwap=.99,stop_price_at_entry=.94,sell_timestamp=None,exit_reason=None,
    )
    trader=Trader(repo,clob,TradingConfig(),gamma_client=_active_gamma(),simulation_mode=False)
    assert trader.execute_sell(trade) is False
    assert len(sent)==1
    assert trade.pending_sell_submission_id=='new-uncertain-sell'
    assert trade.sell_timestamp==SUBMITTED
    assert all(not str(values.get('exit_reason','')).startswith('exit_sell_failure_retrying:') for _,values in repo.updated)
    assert trader.execute_sell(trade) is False
    assert len(sent)==1


@pytest.mark.parametrize('closed',[True,False])
def test_exhausted_sell_quota_still_resolves_or_clears_recovered_holdings(closed):
    repo,clob=_Repo(),_Clob(best_bid=.69 if closed else .89,best_ask=.70 if closed else .895)
    if closed:
        clob.resolution=_normalize_clob_resolution('condition-1',{
            'condition_id':'condition-1','closed':True,'tokens':[
                {'outcome':'Yes','token_id':'own-db-token','price':1,'winner':True},
                {'outcome':'No','token_id':'no-token','price':0,'winner':False},
            ],
        })
    trade=SimpleNamespace(id=10,condition_id='condition-1',event_id='event-1',
        token_id='own-db-token',outcome='Yes',buy_order_id='buy-1',buy_price=.985,
        buy_shares=5/.985,exit_reason='exit_sell_failure_retrying:absolute_stop',
        sell_timestamp=SUBMITTED)
    trader=Trader(repo,clob,TradingConfig(),gamma_client=_active_gamma(),simulation_mode=False)
    trader.emergency_sell_submissions=trader.config.max_emergency_sells_per_cycle
    assert trader.execute_sell(trade) is False
    assert clob.orders==[]
    if closed:
        assert repo.updated[-1][1]['status'] is TradeStatus.RESOLVED
    else:
        assert repo.updated[-1][1]['exit_reason'] is None
    assert trader.emergency_sell_guard_blocks==0


def test_exhausted_sell_quota_never_posts_an_additional_triggered_exit():
    repo,clob=_Repo(),_Clob(best_bid=.83,best_ask=.84)
    trade=SimpleNamespace(id=10,condition_id='condition-1',event_id='event-1',
        token_id='own-db-token',outcome='Yes',buy_price=.99,buy_shares=5.05,
        buy_confirmed_vwap=.99,stop_price_at_entry=.94)
    trader=Trader(repo,clob,TradingConfig(),gamma_client=_active_gamma(),simulation_mode=False)
    trader.emergency_sell_submissions=trader.config.max_emergency_sells_per_cycle
    assert trader.execute_sell(trade) is False
    assert not clob.orders
    assert trader.emergency_sell_guard_blocks==1


def test_loaded_runtime_buy_refuses_missing_run_context(monkeypatch):
    monkeypatch.setattr('polybot.strategy.trader.current_run_id',lambda:None)
    repo,clob=_Repo(),_Clob()
    trader=Trader(repo,clob,TradingConfig(strategy_source_digest='d'*64),simulation_mode=False)
    assert trader.execute_buy(_candidate()) is None
    assert trader.last_entry_outcome_reason=='missing_run_audit_context'
    assert not clob.orders


@pytest.mark.parametrize('bid',[.91,.59])
def test_simulation_exit_clears_pending_fields_without_duplicate_keywords(bid):
    repo,clob=_Repo(),_Clob(best_bid=bid,best_ask=bid+.01)
    trade=SimpleNamespace(id=10,condition_id='condition-1',event_id='event-1',
        token_id='own-db-token',outcome='Yes',buy_price=.75,buy_shares=5/.75,
        buy_order_id='SIM_BUY_10')
    trader=Trader(repo,clob,TradingConfig(),gamma_client=_active_gamma(),simulation_mode=True)
    assert trader.execute_sell(trade) is True
    update=repo.updated[-1][1]
    assert update['status'] is TradeStatus.COMPLETED
    assert update['pending_sell_requested_shares'] is None
    assert update['pending_sell_remaining_shares'] is None
    assert update['realized_pnl'] is None
    assert update['hypothetical_pnl']==pytest.approx((bid-.75)*6.66)
    assert update['sell_confirmed_size'] is None


@pytest.mark.parametrize('simulation',[True,False])
def test_simulated_losses_do_not_block_research_but_live_loss_limit_still_blocks(monkeypatch,tmp_path,simulation):
    bot,scanner,trader,repo,_session,_gamma=_build_bot(monkeypatch,tmp_path,'active',[],simulation_mode=simulation)
    scanner.scan_buy_candidates.side_effect=None
    scanner.scan_buy_candidates.return_value=[{'event_id':'new-event','entry_episode_id':5}]
    trader.execute_buy.side_effect=None;trader.execute_buy.return_value=12
    repo.get_economic_pnl_guard.return_value.update(economic_pnl=-10,proven_resolution_pnl=-10,recorded_settlement_pnl=-10)
    stats=bot.run_cycle()
    if simulation:
        trader.execute_buy.assert_called_once()
        assert not stats['entry_guard']['blocked']
        assert not stats['drawdown_guard']['triggered']
        assert stats['drawdown_guard']['simulated_threshold_breached']
    else:
        trader.execute_buy.assert_not_called()
        assert stats['drawdown_guard']['triggered']
        assert 'economic_drawdown_limit_reached' in stats['entry_guard']['blocking_reasons']


@pytest.mark.parametrize('damage',[
    None,'failed_run','other_config','other_source','incomplete_snapshot','incomplete_event','wrong_mode','missing_run',
])
def test_trend_history_requires_current_cohort_successful_complete_prior_cycles(tmp_path,monkeypatch,damage):
    path=_strict_replay_db(tmp_path/'trend.db',terminal=False)
    with sqlite3.connect(path) as c:
        c.execute("DELETE FROM market_snapshots WHERE run_id='run-4'")
        c.execute("UPDATE run_audits SET status='RUNNING' WHERE run_id='run-3'")
        edits={
            'failed_run':"UPDATE run_audits SET status='FAILED' WHERE run_id='run-1'",
            'other_config':"UPDATE market_snapshots SET config_hash='other-cohort' WHERE run_id='run-1'",
            'other_source':"UPDATE market_snapshots SET strategy_source_digest='other-source' WHERE run_id='run-1'",
            'incomplete_snapshot':"UPDATE market_snapshots SET event_set_complete=0 WHERE run_id='run-1'",
            'incomplete_event':"UPDATE event_cycle_evidence SET complete=0 WHERE run_id='run-1'",
            'wrong_mode':"UPDATE run_audits SET mode='live' WHERE run_id='run-1'",
            'missing_run':"DELETE FROM run_audits WHERE run_id='run-1'",
        }
        if damage:c.execute(edits[damage])
    Session=init_database(str(path));session=Session()
    monkeypatch.setattr(repository_module,'current_run_id',lambda:'run-3')
    history=TradeRepository(session).get_recent_token_snapshots('mlb-home',limit=3)
    config=TradingConfig(sport_family='mlb',source_clock_required=False)
    trend,reason=evaluate_trend_confirmation(history,current_snapshot_id=5,config=config)
    if damage:
        assert trend is None
        assert reason=='trend_history_incomplete'
    else:
        assert trend is not None
        assert trend.prices==(.72,.74,.75)
        assert reason=='confirmed'
