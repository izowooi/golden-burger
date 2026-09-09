"""Small S-aware execution counterexamples, without production files or network."""
from dataclasses import replace
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import conservative_sports_grid as g
import sports_s_calibration as s


def snap(token, condition, slot, mid, t, *, size=100, spread=.01, valid=True, rate=.05, run=None):
    asks=((mid+spread/2,size),);bids=((mid-spread/2,size),)
    return g.Snap(token,condition,slot,t,run or str(t),t/60,mid,asks,bids,valid,True,True,
                  rate,spread,'PRESENT_VALID','SOURCE_CLOCK',g.walk(asks,5,True),
                  entry_set_complete=True,outcome_label=token,legacy_result_kind=slot[0])


def direct(t, a=.595, b=.405, *, bid_a=None, size_a=100):
    x=snap('a','match',('HOME','DIRECT'),a,t,size=size_a)
    y=snap('b','match',('AWAY','DIRECT'),b,t)
    if bid_a is not None:x.bids=((bid_a,size_a),);x.spread=x.asks[0][0]-bid_a
    return g.Group(t,str(t),[x,y],True)


def soccer(t):
    yes=(.60,.25,.15);rows=[]
    for index,(role,p) in enumerate(zip(('HOME','DRAW','AWAY'),yes)):
        rows.extend([snap(role+'yes',str(index),(role,'YES'),p,t),
                     snap(role+'no',str(index),(role,'NO'),1-p,t)])
    return g.Group(t,str(t),rows,True)


def event(groups,sport='mlb',failures=None,terminals=None):
    return g.Event('fixture','cohort',sport,'event','Fixture',{'config_hash':'cfg'},groups,failures or [],terminals or {})


def baseline(sport='mlb',strategy='plum'):
    return s.Baseline('baseline',strategy,sport,.55,.94,'relative',.03,.15,.01,
                      require_liquidity_gate=strategy=='watermelon')


def test_s_is_yes_three_not_sum_of_six_and_no_is_reference_only():
    ev=event([soccer(0)],'soccer');summary=s.summarize(ev,ev.groups[0])
    assert summary['valid'] and float(summary['s_mid'])==pytest.approx(1)
    assert sum(x.midpoint for x in ev.groups[0].snaps)==pytest.approx(3)
    assert float(summary['no_reference']['AWAYno']['mid'])==pytest.approx(.85)


def test_unknown_venue_keeps_partition_without_inventing_home():
    group=soccer(0)
    for q in group.snaps:
        if q.slot[0]!='DRAW':q.slot=(None,q.slot[1]);q.legacy_result_kind=None
        q.verified_role='UNKNOWN'
    summary=s.summarize(event([group],'soccer'),group)
    assert summary['valid'] and float(summary['s_mid'])==pytest.approx(1)
    assert all(q.verified_role=='UNKNOWN' for q in group.snaps)


def test_no_book_must_be_aligned_with_s_at_entry():
    group=soccer(0)
    for q in group.snaps:
        if q.slot[1]=='NO':q.time=3
    group.time=3
    ev=event([group],'soccer')
    assert s.summarize(ev,group)['valid']
    result=s.replay_event(ev,baseline('soccer','peach'),'s_entry_only','zero')
    assert result['status']=='FILTERED' and result['reason']=='S_and_candidate_or_rank_receipt_skew'


def test_decision_time_is_not_before_s_knowledge_or_rank_quotes():
    group=soccer(0)
    for q in group.snaps:
        if q.slot[1]=='YES':q.time=1.5
    group.time=1.5
    result=s.replay_event(event([group],'soccer'),baseline('soccer','peach'),'s_entry_only','zero')
    assert result['entry_quote_utc']==g.iso(0)
    assert result['entry_decision_utc']==g.iso(1.5)
    assert result['status']=='CENSORED'


def test_ask_overround_is_not_executable_bid_profit():
    ev=event([direct(0),direct(60,.695,.305)])
    summary=s.summarize(ev,ev.groups[1])
    assert float(summary['s_ask'])>1 and float(summary['s_bid'])<1
    assert s.replay_event(ev,baseline(),'profit_no_stop','zero')['reason']=='PROFIT'
    result=s.replay_event(ev,baseline(),'s_bid_exit','zero')
    assert result['status']=='CENSORED' and result['net'] is None


def test_other_outcome_overround_cannot_sell_selected_loser_for_profit():
    ev=event([direct(0),direct(60,.545,.505)])
    result=s.replay_event(ev,baseline(),'s_mid_exit','zero')
    assert float(s.summarize(ev,ev.groups[1])['s_mid'])>1.02
    assert result['status']=='CENSORED' and result['net'] is None


def test_executable_s_profit_walks_whole_held_quantity_and_both_fees():
    ev=event([direct(0),direct(60,.715,.335)])
    result=s.replay_event(ev,baseline(),'s_bid_exit','recorded_schedule')
    assert result['status']=='COMPLETE' and result['reason']=='S_PROFIT'
    assert result['sell_shares']==pytest.approx(8.33)
    assert result['net']==pytest.approx(8.33*.71-result['sell_fee']-result['usd_cost'])
    assert result['net']>.02


def test_small_gross_gain_can_be_negative_after_fees():
    ev=event([direct(0,.595,.405),direct(60,.615,.385)])
    assert s.replay_event(ev,baseline(),'profit_no_stop','zero')['status']=='COMPLETE'
    result=s.replay_event(ev,baseline(),'profit_no_stop','recorded_schedule')
    assert result['status']=='CENSORED'


def test_partial_bid_does_not_become_full_sell_and_retries_later():
    ev=event([direct(0),direct(60,.715,.335,size_a=4),direct(120,.725,.335)])
    result=s.replay_event(ev,baseline(),'s_bid_exit','zero')
    assert result['status']=='COMPLETE' and result['exit_utc']==g.iso(120)
    assert result['partial_depth_observations']==1 and result['last_available_sell_shares']==4


def test_failure_or_gap_does_not_get_repaired_by_later_profit():
    for groups,failed in (([direct(0),direct(120,.715,.335)],[]),([direct(0),direct(60,.715,.335)],[(30,31)])):
        result=s.replay_event(event(groups,failures=failed),baseline(),'profit_no_stop','zero')
        assert result['status']=='CENSORED' and result['reason']=='path_gap_or_failed_run'
        assert result['net'] is None and result['lower']==-5


def test_missing_s_set_still_allows_independent_executable_tail_stop():
    later=direct(60,.345,.655);later.snaps=later.snaps[:1]
    ev=event([direct(0),later])
    result=s.replay_event(ev,baseline(),'s_bid_tail_stop','zero')
    assert result['status']=='COMPLETE' and result['reason']=='SL' and result['exit_vwap']==pytest.approx(.34)


def test_terminal_after_gap_or_cutoff_is_not_lookahead_exit():
    terminal={('match','a'):[{'observed_at':g.iso(120),'payout':1,'observer_config':'cfg'}]}
    result=s.replay_event(event([direct(0)],terminals=terminal),baseline(),'profit_no_stop','zero',cutoff=100)
    assert result['status']=='CENSORED'
    result=s.replay_event(event([direct(0)],terminals=terminal),baseline(),'profit_no_stop','zero',cutoff=200)
    assert result['status']=='CENSORED'


def test_unknown_fee_does_not_get_zero_fee_primary():
    first=direct(0)
    for q in first.snaps:q.fee_rate=None
    result=s.replay_event(event([first,direct(60,.715,.335)]),baseline(),'profit_no_stop')
    assert result['status']=='UNSCORABLE' and result['net'] is None


def test_loader_hashes_and_executes_same_bytes_despite_stale_same_size_pyc(tmp_path):
    import hashlib,importlib.util,os,py_compile
    p=tmp_path/'math_fixture.py';p.write_text('VALUE = 1.00\n');py_compile.compile(str(p),doraise=True)
    stat=p.stat();p.write_text('VALUE = 1.01\n');os.utime(p,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    sha=hashlib.sha256(p.read_bytes()).hexdigest()
    loaded=s.load_exact_module('s_math_fixture',p,sha)
    assert loaded.VALUE==1.01 and s.EXECUTED_CODE['s_math_fixture']['sha256']==sha


def test_coarse_cadence_does_not_turn_five_minute_trend_into_one_minute():
    spec=replace(baseline(),entry_min=.60,entry_max=.65,trend_observations=3,
                 trend_min_move=.02,trend_max_pullback=.01,trend_gap_seconds=90)
    ev=event([direct(0,.575,.425),direct(300,.585,.415),direct(600,.615,.385)])
    candidate,_=s.entry_candidate(ev,spec,max_gap=330)
    assert candidate is None


def test_recorded_scheduled_age_is_not_called_a_native_clock():
    group=direct(0)
    for q in group.snaps:q.minute=3;q.scheduled_age=None;q.source_reason='SCHEDULED_START_AGE_SHADOW_ONLY'
    spec=replace(baseline(strategy='peach'),clock_basis='scheduled_age_proxy',max_minute=10,min_minute=0)
    result=s.replay_event(event([group]),spec,'profit_no_stop','zero')
    assert result['entry_token']=='a' and result['clock_contract']=='scheduled_age_proxy'


def test_user_hard_99_tp_does_not_wait_for_missing_s():
    spec=replace(baseline(strategy='watermelon'),entry_min=.95,entry_max=.999,target_mode='absolute',target=.99,
                 stop_delta=.3,stop_floor=.7,hard_take_profit=.99,require_target_profit_feasibility=True)
    first=direct(0,.955,.045);later=direct(60,.995,.005)
    later.snaps=later.snaps[:1]
    result=s.replay_event(event([first,later]),spec,'s_bid_exit','zero')
    assert result['status']=='COMPLETE' and result['reason']=='USER_HARD_TP'
    assert result['exit_utc']==g.iso(60)


def test_user_entry_at_or_above_99_and_no_fee_profit_room_are_rejected():
    spec=replace(baseline(strategy='watermelon'),entry_min=.95,entry_max=.999,target_mode='absolute',target=.99,
                 stop_delta=.3,stop_floor=.7,hard_take_profit=.99,require_target_profit_feasibility=True)
    assert s.replay_event(event([direct(0,.990,.010)]),spec,'profit_no_stop','zero')['status']=='NO_ENTRY'
    result=s.replay_event(event([direct(0,.984,.016)]),spec,'profit_no_stop','recorded_schedule')
    assert result['status']=='REJECTED' and result['reason']=='no_fee_adjusted_profit_room_at_target'


def test_later_s_cannot_mask_non_increasing_selected_quote_for_stop():
    first=soccer(0)
    for q in first.snaps:
        if q.slot[1]=='YES':q.time=2
    later=soccer(3)
    for q in later.snaps:
        if q.token=='AWAYno':
            q.time=1;q.bids=((.50,100),);q.asks=((.51,100),);q.spread=.01
    result=s.replay_event(event([first,later],'soccer'),baseline('soccer','peach'),'s_bid_tail_stop','zero')
    assert result['status']=='CENSORED' and result['exit_utc'] is None


def test_snapshot_proxy_does_not_claim_actual_receipt_proof():
    ev=event([direct(0)])
    result=s.summarize(ev,ev.groups[0])
    assert result['valid'] and result['timestamp_basis']=='RECORDED_SNAPSHOT_PROXY'
    assert result['primary_synchronization_proven'] is False


def test_current_pp_control_retains_all_amount_gate_while_s_reference_separates_it():
    group=direct(0,.975,.025,size_a=100)
    group.snaps[1].asks=((.03,100),);group.snaps[1].buy=None
    spec=replace(baseline(),entry_min=.95,entry_max=.999)
    ev=event([group])
    control=s.replay_event(ev,spec,'recorded_control','zero')
    reference=s.replay_event(ev,spec,'profit_no_stop','zero')
    assert control['status']=='NO_ENTRY' and reference['entry_token']=='a'
    assert control['entry_completeness_contract']=='ALL_OUTCOMES_FIVE_DOLLAR_WALKS'
    assert reference['entry_completeness_contract'].startswith('SELECTED_')


def test_postgame_market_open_does_not_invent_protective_stop():
    later=direct(60,.345,.655)
    for q in later.snaps:q.open_observed=False;q.market_open_observed=True
    ev=event([direct(0),later])
    for variant in ('recorded_control','s_bid_tail_stop'):
        result=s.replay_event(ev,baseline(),variant,'zero')
        assert result['status']=='CENSORED' and result['reason']!='SL'


def test_recorder_bridge_reuses_verified_shard_reader_without_crossing_cohorts(tmp_path):
    import json,shutil
    fixture=Path(__file__).resolve().parents[2]/'docs/local/sports-workbench-20260908/fleet/recorder-fixture-phase30-final'
    if not fixture.is_dir():pytest.skip('optional immutable integration fixture unavailable')
    sources=[]
    for name in ('trades_sim_20260908.db','trades_sim.db'):
        path=tmp_path/name;shutil.copyfile(fixture/name,path)
        sources.append({'source_key':name,'source':'fixture','jenkins_job':'polybot-white','runtime_job':'coconut-sports-recorder-1m-v1',
            'local_path':str(path),'local_sha256':g.visual.sha256(path),'pinned':True})
    source={'id':'fixture-recorder','constituents':sources,'source':'fixture','jenkins_job':'polybot-white',
            'runtime_job':'coconut-sports-recorder-1m-v1','pinned':True}
    events,audit=s.read_recorder_source(source,'2026-09-08T00:00:00Z','2026-09-10T00:00:00Z',code_directory=tmp_path/'code',data_root=tmp_path)
    assert len(events)==1 and len(events[0].groups)==2 and audit['stats']['valid_rows']==12
    assert len({q.token for group in events[0].groups for q in group.snaps})==6
    assert all(s.summarize(events[0],group)['primary_synchronization_proven']for group in events[0].groups)


def test_paired_bounds_include_censoring_and_do_not_drop_it_as_profit():
    good={'status':'COMPLETE','lower':.1,'upper':.1,'net':.1}
    censored={'status':'CENSORED','lower':-5,'upper':1,'net':None}
    base={'status':'NO_ENTRY','net':None}
    result=s.paired_interval([(good,base),(censored,base)],repetitions=100)
    assert result['lower']<0 and result['upper']>0 and result['all_bounds_known']


def test_csv_preserves_optional_execution_columns_after_no_entry(tmp_path):
    import csv
    path=tmp_path/'rows.csv';s.write_csv(path,[{'status':'NO_ENTRY','net':None},{'status':'COMPLETE','net':.1,'sell_shares':8.33}])
    rows=list(csv.DictReader(path.open()))
    assert rows[0]['sell_shares']=='' and rows[1]['sell_shares']=='8.33'


def test_primary_timing_is_downgraded_for_proxy_exit():
    first=direct(0);later=direct(60,.715,.335)
    for q in first.snaps:q.evidence_origin='full-sports-raw-v1'
    result=s.replay_event(event([first,later]),baseline(),'profit_no_stop','recorded_schedule')
    assert result['status']=='COMPLETE'
    assert result['primary_synchronization_proven']is False
    assert 'RECORDED_SNAPSHOT_PROXY'in result['path_timestamp_bases']


def test_positive_average_cannot_override_worse_no_stop_tail():
    rows=[]
    for i in range(30):
        common={'source':'x','cohort':'c','event':str(i),'sport':'soccer','season_phase':'REGULAR','cadence_seconds':60,
                'cadence_tier':'ONE_MINUTE','baseline':'b','strategy':'peach','fee_model':'recorded_schedule',
                'partition':'RETROSPECTIVE_TIME_HOLDOUT','status':'COMPLETE','reason':'TP','usd_cost':5,'primary_eligible':True}
        for variant in s.VARIANTS:
            net=(-1 if i==0 else 0) if variant=='recorded_control'else(-2 if i==0 else 1)
            rows.append({**common,'variant':variant,'net':net,'lower':net,'upper':net})
    summary=s.summarize_outcomes(rows)
    candidate=next(r for r in summary if r['variant']=='profit_no_stop')
    assert candidate['paired_interval']['lower']>0
    assert candidate['tail_not_worse_than_current_control']is False
    assert candidate['positive_paired_support']is False


def test_bbo_diagnostic_never_manufactures_quantity_or_financial_entries():
    group=direct(0)
    for q in group.snaps:
        q.bbo_best_ask=q.asks[0][0];q.bbo_best_bid=q.bids[0][0];q.asks=();q.bids=();q.buy=None
        q.entry_set_complete=False;q.evidence_origin='WATERMELON_LIVE_BBO_POLL_PROXY'
    ev=event([group]);ev.config.update(financial_replay_eligible=False,depth_available=False)
    summary=s.summarize(ev,group)
    assert summary['valid'] and float(summary['s_mid'])==pytest.approx(1)
    assert summary['s_ask']is None and summary['s_bid']is None and summary['common_shares']is None
    assert summary['signal_eligible']is False and summary['timestamp_basis']=='BBO_POLL_PROXY_NO_DEPTH'
    assert s.replay_event(ev,baseline(),'profit_no_stop','zero')['status']=='NO_ENTRY'
