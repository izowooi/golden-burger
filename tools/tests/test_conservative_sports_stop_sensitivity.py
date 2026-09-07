"""Synthetic paired-path checks; no production database or remote endpoints."""
from __future__ import annotations

import csv
from copy import deepcopy
from dataclasses import asdict
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

TOOLS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOLS))
import conservative_sports_grid as engine
import conservative_sports_stop_sensitivity as sensitivity

T0=engine.visual.timestamp('2026-09-07T01:00:00Z')


def path(entry=.70,*,policy='plum_price_rank',sport='mlb',ticks=()):
    buy=engine.walk(((entry,1000),),5,True)
    knobs=engine.policy_knobs(policy,sport)
    output={'id':'path','event':'event','title':'Synthetic game','cohort':'cohort','sport':sport,
            'source':'polybot-gold:fixture','policy':policy,'partition':'late','day':'2026-09-07',
            'entry_time':T0,'entry_utc':engine.iso(T0),'entry_token':'token','entry_condition':'condition',
            'entry_result':'HOME','entry_side':'DIRECT','entry_vwap':entry,'buy_walk':asdict(buy),
            'entry_fee_rate':.05,'ambiguous_rank':False,'end_reason':'right_censored',
            'stop_price':max(knobs['stop_floor'],entry-knobs['stop_delta']),'knobs':knobs,'observations':[],
            'terminal_payout':None}
    for seconds,bid,minute,size in ticks:
        active=knobs['stop_cutoff'] is None or minute<knobs['stop_cutoff']
        output['observations'].append({'time':T0+seconds,'utc':engine.iso(T0+seconds),'minute':minute,
            'bids':[[bid,size]],'spread':.01,'open_observed':True,'best_bid':bid,'fee_rate':.05,
            'stop_trigger':active and bid<=output['stop_price'],'stop':False,'tp_preflight':True})
    return output


def replay(p,delta,target=.74):
    return engine.replay_path(sensitivity.retarget_stop(p,delta),'absolute',target,'sports_005')


def test_looser_stop_can_reach_a_later_recovery_on_the_same_path():
    p=path(ticks=[(60,.68,1,1000),(120,.76,2,1000)])
    tight=replay(p,.01)
    loose=replay(p,.12)
    assert tight['reason']=='SL' and tight['exit_utc']==engine.iso(T0+60)
    assert loose['reason']=='TP' and loose['exit_utc']==engine.iso(T0+120)
    assert tight['net']<0<loose['net']


def test_frozen_source_path_is_unchanged_and_baseline_reproduces_exactly():
    p=path(ticks=[(60,.68,1,1000),(120,.76,2,1000)])
    before=deepcopy(p)
    restored=sensitivity.retarget_stop(p,p['knobs']['stop_delta'])
    assert engine.replay_path(restored,'absolute',.74,'sports_005')==engine.replay_path(p,'absolute',.74,'sports_005')
    assert p==before


def test_old_stop_truncated_path_is_rejected_instead_of_inventing_recovery():
    p=path();p['end_reason']='SL'
    with pytest.raises(ValueError,match='truncated'):sensitivity.retarget_stop(p,.30)


def test_same_bar_exit_source_is_rejected():
    p=path(ticks=[(0,.76,0,1000)])
    with pytest.raises(ValueError,match='same-bar'):sensitivity.retarget_stop(p,.01)


def test_watermelon_floor_and_peach_late_cutoff_are_preserved():
    w=path(.96,policy='watermelon_rank',ticks=[(60,.69,1,1000)])
    assert sensitivity.retarget_stop(w,.30)['stop_price']==.70
    assert sensitivity.retarget_stop(w,.01)['stop_price']==pytest.approx(.95)
    p=path(.70,policy='peach_rank',sport='soccer',ticks=[(60,.30,80,1000)])
    variant=sensitivity.retarget_stop(p,.01)
    assert variant['knobs']['late_minute']==80
    assert variant['observations'][0]['stop_trigger'] is False
    assert replay(p,.01)['status']=='CENSORED'


def test_full_depth_controls_stop_execution_not_best_bid_touch():
    p=path(ticks=[(60,.68,1,1),(120,.76,2,1000)])
    r=replay(p,.01)
    assert r['reason']=='TP' and r['blocked_full_depth_observations']==1


def test_absolute_overshoot_is_rejected_for_every_stop():
    p=path(.73,ticks=[(60,.76,1,1000)])
    for delta in sensitivity.DELTAS:
        assert replay(p,delta,target=.72)['reason']=='target_not_above_actual_entry'


def test_censored_baseline_is_not_a_zero_or_an_avoided_loss():
    p=path(ticks=[(60,.68,1,1000)]);p['end_reason']='path_gap_or_failed_run'
    tight=replay(p,.01);baseline=replay(p,.12)
    pair=sensitivity.paired_result(tight,baseline)
    assert pair['newly_complete'] and not pair['paired_complete']
    assert pair['paired_net_delta'] is None
    assert pair['paired_delta_lower']<0<pair['paired_delta_upper']
    stats=sensitivity.Stats();stats.add(tight,baseline)
    row=stats.row(engine)
    assert row['paired_net_change'] is None and row['newly_complete_not_proven_saved_loss']==1


def test_fee_positive_tp_guard_remains_while_changing_stop():
    p=path(.60,ticks=[(60,.62,1,1000)])
    assert replay(p,.03,target=.62)['status']=='CENSORED'
    assert replay(p,.03,target=.62)['tp_fee_nonpositive_observations']==1


def make_archive(tmp_path):
    archive=tmp_path/'archive';archive.mkdir()
    for name in ('conservative_sports_grid.py','sports_visual_data.py','catdog_takeprofit_replay.py'):
        shutil.copyfile(TOOLS/name,archive/name)
    proto={'start':'2026-09-07T00:00:00Z','end_exclusive':'2026-09-08T00:00:00Z','entry_width':.03,
           'fee_collection':'v2_cash','code_sha256':sensitivity.sha256(archive/'conservative_sports_grid.py')}
    (archive/'PROTOCOL.json').write_text(json.dumps(proto))
    p=path(ticks=[(60,.68,1,1000),(120,.76,2,1000)])
    cohort={'cohort':'cohort','source':'polybot-gold:fixture','sport':'mlb','primary_fee_model':'sports_005','events':1}
    data={'cohorts':[cohort],'audits':[{'source':'polybot-gold:fixture','path':'synthetic-pin','sha256':'fixture'}]}
    (archive/'results.json').write_text(json.dumps(data))
    with gzip.open(archive/'entries.csv.gz','wt',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['cohort','event','policy','rank','entry_threshold','path_id']);writer.writeheader()
        # Four fixed cases: .55 mapping serves both published TP controls.
        for policy,entry,path_id in [('plum_price_rank',.70,'path'),('plum_trend_rank',.74,''),('plum_trend_rank',.55,'')]:
            writer.writerow({'cohort':'cohort','event':'event','policy':policy,'rank':1,'entry_threshold':entry,'path_id':path_id})
    with gzip.open(archive/'paths.jsonl.gz','wt') as f:f.write(json.dumps(p)+'\n')
    deps={'dependencies':[{'filename':n,'sha256':sensitivity.sha256(archive/n)} for n in ('conservative_sports_grid.py','sports_visual_data.py','catdog_takeprofit_replay.py')]}
    (archive/'DEPENDENCIES.json').write_text(json.dumps(deps))
    (archive/'OUTPUT_SHA256.json').write_text(json.dumps({p.name:sensitivity.sha256(p) for p in archive.iterdir() if p.is_file()}))
    return archive


def test_end_to_end_preserves_fixed_no_entry_denominators(tmp_path):
    archive=make_archive(tmp_path)
    result=sensitivity.run([archive],tmp_path/'output')
    assert result['fixed_case_cohort_units']==4
    assert result['fixed_candidate_event_units']==4
    assert result['event_outcome_rows']==4*10*3
    assert result['baseline_exact_reproduction_checks']==3
    assert result['baseline_mismatches']==0
    assert not (archive/'__pycache__').exists()
    checks=json.loads((tmp_path/'output/OUTPUT_SHA256.json').read_text())
    assert 'cohorts/cohort.md' in checks


def test_frozen_dependency_hash_tampering_is_rejected(tmp_path):
    archive=make_archive(tmp_path)
    (archive/'sports_visual_data.py').write_text('raise RuntimeError("untrusted mutation")')
    with pytest.raises(ValueError,match='checksum'):sensitivity.load_frozen_engine(archive)


def test_tighter_trigger_does_not_assume_better_full_depth_execution_price():
    p=path(ticks=[(60,.69,1,.1),(120,.57,2,1000)])
    p['observations'][0]['bids']=[[.69,.1],[.10,1000]]
    tight=replay(p,.01);baseline=replay(p,.12)
    assert tight['reason']==baseline['reason']=='SL'
    assert tight['exit_vwap']<baseline['exit_vwap']
    assert tight['net']<baseline['net']
