"""Meaningful execution-evidence fixtures; never read production databases."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

TOOLS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOLS))
import conservative_sports_grid as grid


def snap(token="home",slot=("HOME","DIRECT"),p=.60,bid=None,t=0,minute=0,valid=True):
    asks=((p,1000.0),);bids=((bid if bid is not None else p-.01,1000.0),)
    return grid.Snap(token,"condition",slot,t,str(t),minute,(asks[0][0]+bids[0][0])/2,
                     asks,bids,valid,True,True,.05,asks[0][0]-bids[0][0],"PRESENT_VALID","SOURCE_CLOCK",grid.walk(asks,5,True))


def event(groups,failures=None,terminals=None,sport="mlb"):
    return grid.Event("polybot-grey:fixture","cohort",sport,"event","Fixture",{},
                      groups,failures or [],terminals or {},"early","2026-09-05")


def group(t,p=.60,bid=None,**kwargs):
    a=snap(p=p,bid=bid,t=t,minute=t/60,**kwargs)
    b=snap("away",("AWAY","DIRECT"),.40,t=t,minute=t/60)
    return grid.normalize_group([a,b],"mlb",False)


def make(groups,policy="peach_rank",failures=None,terminals=None):
    ev=event(groups,failures,terminals)
    c,_=grid.candidates(ev,policy,1,.60)
    assert c is not None
    return grid.make_path(ev,policy,c)


def test_full_depth_held_size_not_new_five_dollar_sell():
    buy=grid.walk(((.50,3),(.60,100)),5,True)
    assert buy.shares==pytest.approx(3+3.5/.6)
    assert grid.walk(((.99,8),),buy.shares,False) is None
    sell=grid.walk(((.99,8),(.90,100)),buy.shares,False)
    assert sell.worst==.90
    assert sell.vwap<.99


def test_malformed_empty_and_unit_ask_are_distinct():
    assert grid.levels({"asks":[{"price":1,"size":20}]},"asks")==((1.,20.),)
    assert grid.levels({"asks":[]},"asks")==()
    with pytest.raises(ValueError):grid.levels({},"asks")
    with pytest.raises(ValueError):grid.levels({"asks":[{"price":.5,"size":1},{"price":.5,"size":2}]},"asks")


def test_absolute_overshoot_rejected_not_same_bar_profit():
    path=make([group(0),group(60,.65,.64)])
    path["entry_vwap"]=.72
    assert grid.replay_path(path,"absolute",.72,"zero")["reason"]=="target_not_above_actual_entry"
    assert grid.replay_path(path,"absolute",.70,"zero")["status"]=="REJECTED"


def test_same_bar_exit_is_never_considered():
    g=group(0,.60,.60)
    ev=event([g]);c,_=grid.candidates(ev,"peach_rank",1,.60)
    p=grid.make_path(ev,"peach_rank",c)
    assert p["observations"]==[]
    assert grid.replay_path(p,"relative",.01,"zero")["status"]=="CENSORED"


def test_actual_entry_relative_delta_and_fees_reject_false_small_profit():
    p=make([group(0),group(60,.63,.62)])
    zero=grid.replay_path(p,"relative",.02,"zero")
    costly=grid.replay_path(p,"relative",.02,"sports_005")
    assert zero["status"]=="COMPLETE" and zero["net"]>0
    assert costly["status"]=="CENSORED"
    assert costly["tp_fee_nonpositive_observations"]==1
    econ=grid.buy_economics(p,"sports_005")
    observed=next(grid.modeled_observations(p,"sports_005",econ))
    assert observed["walk"].amount-observed["sell_fee"]-econ["usd_cost"]<0


def test_gap_and_failed_run_censor_before_later_target():
    for groups,failures in [([group(0),group(120,.80,.79)],[]),([group(0),group(60,.80,.79)],[(30,40)])]:
        p=make(groups,failures=failures)
        r=grid.replay_path(p,"relative",.02,"zero")
        assert r["status"]=="CENSORED" and r["reason"]=="path_gap_or_failed_run"
        assert r["net"] is None


def test_terminal_after_gap_does_not_overwrite_censor():
    proofs={("condition","home"):[{"observed_at":grid.iso(121),"payout":1.0}]}
    p=make([group(0),group(120,.80,.79)],terminals=proofs)
    assert p["terminal_payout"]==1
    assert grid.replay_path(p,"relative",.02,"zero")["status"]=="CENSORED"


def test_stop_uses_actual_gap_bid_not_trigger():
    p=make([group(0),group(60,.31,.30)])
    r=grid.replay_path(p,"relative",.02,"zero")
    assert r["reason"]=="SL" and r["exit_vwap"]==pytest.approx(.30)
    assert r["net"]<-2.49


def test_missing_native_clock_rejects_peach_mlb():
    g=group(0)
    for s in g.snaps:s.minute=None
    c,d=grid.candidates(event([g]),"peach_rank",1,.60)
    assert c is None and d["missing_native_kickoff_clock_or_outside_window"]==1


def test_six_ranks_use_same_time_midpoint_and_report_ties():
    snaps=[]
    for k,(result,side) in enumerate(( (r,s) for r in ("HOME","DRAW","AWAY") for s in ("YES","NO"))):
        snaps.append(snap(str(k),(result,side),.9-.1*k))
    g=grid.normalize_group(snaps,"soccer",False)
    assert g.complete
    assert [s.token for s in g.snaps]==[str(k) for k in range(6)]
    snaps[1].midpoint=snaps[0].midpoint
    tied=grid.normalize_group(snaps,"soccer",False)
    assert tied.ambiguous=={1,2}
    assert not grid.normalize_group(snaps[:-1],"soccer",False).complete


def test_strict_cross_requires_prior_below_while_level_accepts_first_above():
    ev=event([group(0),group(60,.62,.61)])
    assert grid.candidates(ev,"peach_rank",1,.60)[0] is not None
    assert grid.candidates(ev,"peach_cross_rank",1,.60)[0] is None
    ev=event([group(0,.59,.58),group(60,.61,.60)])
    assert grid.candidates(ev,"peach_cross_rank",1,.60)[0][1].buy.vwap==pytest.approx(.61)


def test_censored_events_are_not_zero_profit_or_wins():
    p=make([group(0),group(120,.80,.79)])
    result=grid.replay_path(p,"relative",.02,"zero")
    c=grid.Cell();c.add(p,result);c.add(None,None)
    s=c.summary(10000)
    assert s["events"]==2 and s["entered"]==1 and s["complete"]==0
    assert s["complete_net"] is None and s["all_entry_net_lower"]==-5
    assert s["positive_rate_censored_as_failure"]==0
    assert s["family95_hoeffding_lower"]==0


def test_exhaustive_grid_emits_impossible_and_no_entry_cells(tmp_path):
    ev=event([group(0),group(60,.66,.65)])
    out=grid.run_grid([ev],tmp_path,grid=(.60,.62),fee_models=("zero",))
    # 4 policies × 2 ranks × 2 entry × 2 target × 2 mode × all/early/day.
    assert out["cell_rows"]==4*2*2*2*2*3
    assert (tmp_path/"paths.jsonl.gz").exists()


def test_fast_batch_matches_scalar_for_all_fee_threshold_outcomes():
    p=make([group(0),group(60,.63,.62),group(120,.66,.65),group(180,.30,.29)])
    values=(.01,.02,.03,.05,.60,.62,.65,.99)
    for model in grid.FEE_MODELS:
        batch=grid.path_grid(p,values,model)
        for mode in ("absolute","relative"):
            for target in values:
                assert batch[(mode,target)]==grid.replay_path(p,mode,target,model)


def test_peach_reader_accepts_run_anchored_metadata_without_inventing_plum_columns(tmp_path):
    import sqlite3
    db=tmp_path/"pin.db"
    cfg={"trading":{"strategy_source_digest":"a"*64,"sport_family":"soccer",
                    "sport_profile_version":"fixture","preregistration_sha256":"b"*64,
                    "classifier_version":"fixture","league_mapping_sha256":"c"*64}}
    with sqlite3.connect(db) as c:
        c.executescript("""
          CREATE TABLE strategy_configs(config_hash TEXT, config_json TEXT,mode TEXT);
          CREATE TABLE run_audits(run_id TEXT, job_name TEXT, mode TEXT, config_hash TEXT,status TEXT,started_at TEXT,finished_at TEXT);
          CREATE TABLE market_catalog(condition_id TEXT,event_id TEXT,outcomes_json TEXT,token_ids_json TEXT,event_title TEXT,event_slug TEXT,question TEXT,league_code TEXT);
          CREATE TABLE market_snapshots(id INTEGER,run_id TEXT,condition_id TEXT,event_id TEXT,token_id TEXT,outcome TEXT,outcome_side TEXT,result_kind TEXT,timestamp TEXT,book_json TEXT,sport_family TEXT,sport_profile_version TEXT,source_elapsed_minutes REAL,source_clock_reason TEXT,source_updated_at TEXT,midpoint REAL);
          CREATE TABLE resolution_observations(condition_id TEXT,run_id TEXT,observed_at TEXT,evidence_json TEXT);
        """)
        c.execute("INSERT INTO strategy_configs VALUES(?,?,?)",("cfg",json.dumps(cfg),"sim"))
        c.execute("INSERT INTO run_audits VALUES(?,?,?,?,?,?,?)",("run","fixture","sim","cfg","SUCCESS","2026-09-01T00:00:00Z","2026-09-01T00:00:05Z"))
        i=0
        for k,result in enumerate(("HOME","DRAW","AWAY")):
            cid=str(k);tokens=[str(2*k),str(2*k+1)]
            c.execute("INSERT INTO market_catalog VALUES(?,?,?,?,?,?,?,?)",(cid,"event",'["Yes","No"]',json.dumps(tokens),"Fixture","fixture","question","EPL"))
            for j,side in enumerate(("YES","NO")):
                token=tokens[j];p=.8-i*.1
                book={"token_id":token,"asks":[{"price":p,"size":100}],"bids":[{"price":p-.01,"size":100}]}
                c.execute("INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(i,"run",cid,"event",token,side.title(),side,result,"2026-09-01 00:00:02",json.dumps(book),"soccer","fixture",2,"SOURCE_TOTAL_ELAPSED","2026-09-01T00:00:00Z",p-.005));i+=1
    source={"id":"polybot-grey:fixture","strategy":"golden-peach","pinned":True,"local_path":str(db),"local_sha256":grid.visual.sha256(db)}
    events,audit=grid.read_source(source,"2026-09-01T00:00:00Z","2026-09-02T00:00:00Z")
    assert audit["stats"]["valid_rows"]==6
    assert events[0].groups[0].complete
    assert all(s.open_observed for s in events[0].groups[0].snaps)


def test_resolution_before_a_later_gap_is_processed_in_chronological_order():
    proofs={("condition","home"):[{"observed_at":grid.iso(60),"payout":1.0}]}
    p=make([group(0),group(120,.80,.79)],terminals=proofs)
    r=grid.replay_path(p,"relative",.02,"zero")
    assert r["status"]=="COMPLETE" and r["reason"]=="RESOLUTION"


def test_resolution_before_stop_takes_priority_but_later_proof_does_not():
    for t,expected in [(30,"RESOLUTION"),(90,"SL")]:
        proofs={("condition","home"):[{"observed_at":grid.iso(t),"payout":1.0}]}
        p=make([group(0),group(60,.30,.29)],terminals=proofs)
        assert grid.replay_path(p,"relative",.02,"zero")["reason"]==expected


def test_guava_watermelon_projects_direct_yes_three_not_higher_no():
    ss=[]
    for k,(r,side,p) in enumerate([("HOME","YES",.80),("HOME","NO",.20),
            ("DRAW","YES",.10),("DRAW","NO",.90),("AWAY","YES",.12),("AWAY","NO",.88)]):
        ss.append(snap(str(k),(r,side),p))
    g=grid.normalize_group(ss,"soccer",False)
    ev=event([g],sport="soccer");ev.source="polybot-sim-guava-a:fixture"
    c,_=grid.candidates(ev,"watermelon_rank",1,.80)
    assert c[1].slot==("HOME","YES")
    assert grid.policy_rank_count(ev,"watermelon_rank")==3
    assert grid.policy_rank_count(ev,"peach_rank")==6
    assert "plum_price_rank" in grid.policy_names(ev)


def test_saved_normalized_evidence_reconstructs_event_cell(tmp_path):
    ev=event([group(0),group(60,.66,.65)])
    grid.run_grid([ev],tmp_path,grid=(.60,.65),fee_models=("zero",))
    rows=grid.query_cell(tmp_path,"cohort","peach_rank",1,.60,"absolute",.65,"zero")
    assert len(rows)==1
    assert rows[0]["status"]=="COMPLETE"
    assert rows[0]["entry_vwap"]==pytest.approx(.60)
    assert rows[0]["exit_vwap"]==pytest.approx(.65)


def test_plum_later_valid_cross_is_not_blocked_by_earlier_unqualified_cross():
    ev=event([group(0,.69,.68),group(60,.701,.691),group(120,.695,.685),group(180,.699,.689),group(240,.72,.71)])
    candidate,_=grid.candidates(ev,"plum_trend_rank",1,.70)
    assert candidate is not None and candidate[1].time==240


def test_policy_with_stop_at_or_above_actual_entry_is_not_admissible():
    p=make([group(0),group(60,.66,.65)],policy="watermelon_rank")
    assert grid.replay_path(p,"relative",.02,"zero")["reason"]=="stop_not_below_actual_entry"
    assert grid.path_grid(p,(.02,),"zero")[("relative",.02)]["status"]=="REJECTED"


def test_plum_trend_rejects_regressed_or_partial_native_clock():
    a,b,c=snap(p=.68,t=0,minute=5),snap(p=.69,t=60,minute=6),snap(p=.72,t=120,minute=7)
    assert grid.trend_matches([a,b],c,.70)
    c.minute=4
    assert not grid.trend_matches([a,b],c,.70)
    c.minute=None
    assert not grid.trend_matches([a,b],c,.70,source_clock_required=False)
    a.minute=b.minute=None
    assert grid.trend_matches([a,b],c,.70,source_clock_required=False)


def test_entry_set_gap_does_not_erase_the_selected_token_exit_book():
    ev=event([group(0),group(60,.66,.65)])
    c,_=grid.candidates(ev,"peach_rank",1,.60)
    for s in ev.groups[1].snaps:s.entry_set_complete=False
    ev.groups[1]=grid.normalize_group(ev.groups[1].snaps,"mlb",False)
    assert not ev.groups[1].complete
    p=grid.make_path(ev,"peach_rank",c)
    assert grid.replay_path(p,"absolute",.65,"zero")["status"]=="COMPLETE"


def test_current_source_gap_stop_can_execute_beyond_nominal_loss_cap():
    ev=event([group(0,.96,.95),group(60,.06,.05)])
    c,_=grid.candidates(ev,"watermelon_rank",1,.96)
    p=grid.make_path(ev,"watermelon_rank",c)
    r=grid.replay_path(p,"hold",0,"zero")
    assert r["reason"]=="SL" and r["net"]<-4.7


def test_missing_recorded_fee_keeps_unknown_bounds_without_invented_fee_cap():
    p=make([group(0),group(60,.66,.65)])
    p['entry_fee_rate']=None
    r=grid.replay_path(p,'absolute',.65,'recorded_schedule')
    c=grid.Cell();c.add(p,r);summary=c.summary()
    assert summary['unknown_bound_entries']==1
    assert summary['all_entry_net_lower'] is None and summary['all_entry_net_upper'] is None
    p['entry_fee_rate']=.05;p['observations'][0]['fee_rate']=None
    r=grid.replay_path(p,'absolute',.65,'recorded_schedule')
    assert r['lower'] is None and r['upper'] is None
    batch=grid.path_grid(p,(.65,),'recorded_schedule')
    assert batch[('absolute',.65)]==r


def test_duplicate_event_input_cannot_inflate_win_probability(tmp_path):
    ev=event([group(0)])
    with pytest.raises(ValueError,match='duplicate source-cohort-event'):
        grid.run_grid([ev,ev],tmp_path,grid=(.6,),fee_models=('zero',))


def test_fee_curve_is_rounded_to_five_decimal_order_approximation():
    w=grid.Walk(1,1,1,1,.123456)
    assert grid.fee(w,'sports_005')==.00617


def test_buy_fee_collected_in_shares_uses_each_execution_price():
    buy=grid.walk(((.2,5),(.8,5)),5,True)
    path={'buy_walk':grid.asdict(buy),'entry_fee_rate':.05}
    econ=grid.buy_economics(path,'recorded_schedule',fee_collection='legacy_shares')
    assert econ['buy_fee_usdc_equivalent']==pytest.approx(.08)
    assert econ['buy_fee_shares']==pytest.approx(.25)
    assert econ['net_buy_shares']==pytest.approx(9.75)
    sell=grid.walk(((.6,10),),econ['sell_shares'],False)
    assert sell.amount-grid.fee(sell,'recorded_schedule',.05)-5==pytest.approx(.733)


def test_net_shares_change_full_depth_and_stop_chronology_by_fee_model():
    first=group(0,.50,.49)
    first.snaps[0].asks=((.2,5),(.8,5));first.snaps[0].buy=grid.walk(first.snaps[0].asks,5,True)
    middle=group(60,.30,.29);next(s for s in middle.snaps if s.token=="home").bids=((.29,9.8),)
    last=group(120,.80,.79)
    ev=event([first,middle,last]);candidate=(0,first.snaps[0],False)
    path=grid.make_path(ev,'plum_price_rank',candidate)
    charged=grid.replay_path(path,'absolute',.6,'sports_005',fee_collection='legacy_shares')
    free=grid.replay_path(path,'absolute',.6,'zero')
    assert charged['reason']=='SL' and charged['exit_utc']==grid.iso(60)
    assert free['reason']=='TP' and free['exit_utc']==grid.iso(120)


def test_share_fee_small_target_examples_are_not_double_charged_in_usdc():
    ev=event([group(0,.96,.95),group(60,.98,.97)])
    candidate,_=grid.candidates(ev,'watermelon_rank',1,.96)
    path=grid.make_path(ev,'watermelon_rank',candidate)
    result=grid.replay_path(path,'absolute',.97,'sports_005',fee_collection='legacy_shares')
    assert result['sell_shares']==pytest.approx(5.19)
    assert result['net']==pytest.approx(.02675)
    p=make([group(0),group(60,.63,.62)])
    blocked=grid.replay_path(p,'relative',.02,'sports_005')
    published=grid.replay_path(p,'relative',.02,'sports_005',tp_policy='peach_published',fee_collection='legacy_shares')
    assert blocked['status']=='CENSORED'
    assert published['reason']=='TP' and published['net']==pytest.approx(-.03692)


def test_fee_stress_net_size_below_venue_minimum_cannot_be_sold():
    ev=event([group(0,.999,.998),group(60,1.,.9999)])
    c,_=grid.candidates(ev,'watermelon_rank',1,.99,entry_width=.009)
    path=grid.make_path(ev,'watermelon_rank',c)
    result=grid.replay_path(path,'hold',0,'flat_100bps',fee_collection='legacy_shares')
    assert result['sell_shares']<5
    assert result['status']=='CENSORED'


def test_current_v2_cash_fees_keep_gross_quantity_and_charge_buy_once():
    buy=grid.walk(((.2,5),(.8,5)),5,True)
    path={'buy_walk':grid.asdict(buy),'entry_fee_rate':.05}
    econ=grid.buy_economics(path,'recorded_schedule')
    assert econ['fee_collection']=='v2_cash'
    assert econ['buy_fee_shares']==0 and econ['net_buy_shares']==10
    assert econ['usd_cost']==pytest.approx(5.08)
    sell=grid.walk(((.6,100),),econ['sell_shares'],False)
    assert sell.amount-grid.fee(sell,'recorded_schedule',.05)-econ['usd_cost']==pytest.approx(.80)
    p=make([group(0),group(60,.63,.62)])
    published=grid.replay_path(p,'relative',.02,'sports_005',tp_policy='peach_published')
    assert published['net']==pytest.approx(-.03353)


def raw_fixture_pin(tmp_path,*,bad_identity=False,empty_bid=False,bid_only=False):
    spec=importlib.util.spec_from_file_location('grid_raw_fixture',TOOLS/'tests/test_sports_raw_archive_data.py')
    fixture_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture_module)
    f=fixture_module.RawFixture();f.cycle('first');f.cycle('second',bid_only=('HOME:YES',) if bid_only else ())
    for run in ('first','second'):
        r=f.c.execute('SELECT evidence_json FROM raw_event_observations WHERE run_id=?',(run,)).fetchone()
        payload=json.loads(r[0]);payload['event'].update(active=True,closed=False)
        for m in payload['market_context']:
            m['enableOrderBook']=True;m['liquidityNum']=10000;m['feeSchedule']['takerOnly']=True
        if bad_identity and run=='first':payload['slots']['HOME:YES']['token_id']='wrong'
        f.c.execute('UPDATE raw_event_observations SET evidence_json=? WHERE run_id=?',(json.dumps(payload),run))
    # Distinct receipts are necessary: same cycle is never a TP.
    for table,columns in [('run_audits',('started_at','finished_at')),('raw_book_cycles',('observed_at','published_at')),
                          ('raw_event_observations',('observed_at',)),('raw_book_observations',('requested_at','received_at'))]:
        for col in columns:f.c.execute(f'UPDATE {table} SET {col}=replace({col},\'01:00:\',\'01:01:\') WHERE run_id=\'second\'')
    for run in ('first','second'):
        row=f.c.execute("SELECT book_json FROM raw_book_observations WHERE run_id=? AND slot='HOME:YES'",(run,)).fetchone()
        book=json.loads(row[0])
        if run=='first':book['asks']=[{'price':.7,'size':100}];book['bids']=[{'price':.69,'size':100}]
        if run=='second' and empty_bid:book['bids']=[]
        raw=json.dumps(book);digest=__import__('hashlib').sha256(raw.encode()).hexdigest()
        status='EMPTY_BOOK' if not book['asks'] and not book['bids'] else 'EMPTY_ASKS' if not book['asks'] else 'EMPTY_BIDS' if not book['bids'] else 'FULL'
        f.c.execute("UPDATE raw_book_observations SET book_json=?,book_sha256=?,status=? WHERE run_id=? AND slot='HOME:YES'",(raw,digest,status,run))
    import sqlite3
    path=tmp_path/'raw.db';f.c.commit()
    with sqlite3.connect(path) as destination:f.c.backup(destination)
    return {'id':'polybot-grey:raw-fixture','strategy':'golden-peach','pinned':True,'local_path':str(path),'local_sha256':grid.visual.sha256(path)},f


def test_raw_point_in_time_books_need_no_mutable_catalog_or_event_cycle_id(tmp_path):
    source,f=raw_fixture_pin(tmp_path,bid_only=True)
    events,audit=grid.read_source(source,'2026-09-07T00:00:00Z','2026-09-08T00:00:00Z')
    assert audit['stats']['point_in_time_raw_rows']==12
    assert events[0].config['raw_point_in_time_archive'] is True
    candidate,_=grid.candidates(events[0],'peach_rank',1,.70)
    assert candidate is not None and candidate[1].minute==5
    path=grid.make_path(events[0],'peach_rank',candidate)
    result=grid.replay_path(path,'absolute',.99,'recorded_schedule')
    assert result['reason']=='TP' and result['net']>0
    assert grid.uses_recorded_fees(events[0])


def test_raw_identity_failure_cannot_fall_back_to_a_catalog(tmp_path):
    source,f=raw_fixture_pin(tmp_path,bad_identity=True)
    events,audit=grid.read_source(source,'2026-09-07T00:00:00Z','2026-09-08T00:00:00Z')
    first=events[0].groups[0]
    assert not first.complete
    assert not next(s for s in first.snaps if s.token=='HOME-YES').valid


def test_raw_clock_is_native_and_does_not_create_mlb_minutes():
    assert grid.raw_source_minute({'period':'2H','elapsed':'2:30'},'soccer')==47.5
    assert grid.raw_source_minute({'period':'HT'},'soccer')==45
    assert grid.raw_source_minute({'period':'1H','elapsed':True},'soccer') is None
    assert grid.raw_source_minute({'period':'1H','elapsed':'5','startTime':'2026-09-07T00:00:00Z'},'mlb') is None


def test_known_raw_empty_bids_continue_to_later_tp_but_missing_does_not():
    ev=event([group(0),group(60),group(120,.7,.69)])
    middle=next(s for s in ev.groups[1].snaps if s.token=='home')
    middle.bids=();middle.observation_status='EMPTY_BIDS';middle.evidence_origin='full-sports-raw-v1'
    c,_=grid.candidates(ev,'peach_rank',1,.6)
    path=grid.make_path(ev,'peach_rank',c)
    assert path['observations'][0]['explicit_no_marketable_depth']
    assert grid.replay_path(path,'absolute',.65,'zero')['reason']=='TP'
    middle.valid=False;middle.observation_status='MISSING'
    assert grid.replay_path(grid.make_path(ev,'peach_rank',c),'absolute',.65,'zero')['status']=='CENSORED'


def test_known_raw_empty_book_can_reach_proven_zero_or_one_resolution():
    for payout in (0.,1.):
        ev=event([group(0),group(60)],terminals={('condition','home'):[{'observed_at':grid.iso(120),'payout':payout}]})
        middle=next(s for s in ev.groups[1].snaps if s.token=='home')
        middle.bids=();middle.asks=();middle.observation_status='EMPTY_BOOK';middle.evidence_origin='full-sports-raw-v1'
        c,_=grid.candidates(ev,'peach_rank',1,.6)
        result=grid.replay_path(grid.make_path(ev,'peach_rank',c),'relative',.02,'zero')
        assert result['reason']=='RESOLUTION' and result['exit_vwap']==payout
        assert (result['net']>0)==bool(payout)


def test_raw_fee_boolean_values_are_not_valid_numeric_fee_evidence():
    market={'feesEnabled':True,'feeSchedule':{'rate':True,'exponent':1,'takerOnly':True}}
    assert grid.raw_fee_rate(market) is None
    market['feeSchedule'].update(rate=.05,exponent=True)
    assert grid.raw_fee_rate(market) is None


def test_raw_soccer_clock_matches_the_existing_official_source_decoder():
    import ast,math,re,typing
    source=TOOLS.parent/'golden-plum/src/polybot/strategy/scanner.py'
    tree=ast.parse(source.read_text())
    body=[x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name in ('_elapsed_minutes','get_source_regulation_minute')]
    env={'math':math,'re':re,'Any':typing.Any,'Optional':typing.Optional,'Dict':typing.Dict}
    exec(compile(ast.Module(body=body,type_ignores=[]),str(source),'exec'),env)
    for period in ('1H','2H','HT','FT','unknown',None):
        for elapsed in (None,True,False,-1,0,2.5,45,90,'2:30',"45+2'",'1:01:30','nan','bad'):
            event={'period':period,'elapsed':elapsed}
            assert grid.raw_source_minute(event,'soccer')==env['get_source_regulation_minute'](event)[0]


def test_unknown_required_raw_peach_clock_censors_before_profitable_quote():
    ev=event([group(0),group(60,.80,.79)],sport='soccer')
    # Focus on held-token exit; entry six-book topology is separately tested.
    held=next(s for s in ev.groups[1].snaps if s.token=='home')
    held.evidence_origin='full-sports-raw-v1';held.minute=None
    path=grid.make_path(ev,'peach_rank',(0,next(s for s in ev.groups[0].snaps if s.token=='home'),False))
    assert grid.replay_path(path,'relative',.02,'zero')['reason']=='required_source_clock_gap'


def test_raw_scheduled_age_is_retained_only_as_an_explicit_proxy(tmp_path):
    import sqlite3
    source,f=raw_fixture_pin(tmp_path)
    for row in f.c.execute('SELECT observation_id,evidence_json FROM raw_event_observations').fetchall():
        payload=json.loads(row['evidence_json']);payload['event']['startTime']='2026-09-07T00:40:01Z'
        f.c.execute('UPDATE raw_event_observations SET evidence_json=? WHERE observation_id=?',(json.dumps(payload),row['observation_id']))
    f.c.commit()
    with sqlite3.connect(source['local_path']) as destination:f.c.backup(destination)
    source['local_sha256']=grid.visual.sha256(Path(source['local_path']))
    events,_=grid.read_source(source,'2026-09-07T00:00:00Z','2026-09-08T00:00:00Z')
    s=next(s for s in events[0].groups[0].snaps if s.token=='HOME-YES')
    assert s.minute==5 and s.scheduled_age==20
    assert grid.candidates(events[0],'peach_rank',1,.7)[0] is not None
    assert grid.candidates(events[0],'peach_scheduled_age_rank',1,.7)[0] is None
