from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from scripts.replay_direct_six_book import Snapshot
from scripts.replay_mlb_exit_grid import levels_fee, replay_exit, select_entries


def snap(n, ask, *, bid=None, token='home', kind='HOME', size=1000):
    bid = ask-.01 if bid is None else bid
    return Snapshot(n,'event','condition',token,f'run-{n}',kind,'DIRECT',None,
                    datetime(2026,9,4)+timedelta(minutes=n),ask,(ask+bid)/2,
                    ask-bid,((bid,size),),((ask,size),),sport_family='mlb',config_hash='cohort')


def test_first_qualified_two_team_crossing_is_same_for_every_exit_grid():
    rows=[]
    for n,p in enumerate([.51,.52,.53,.54,.55,.56]):
        rows.extend([snap(n,p),snap(n,1-p,token='away',kind='AWAY')])
    assert [(e.snapshot_id,e.token_id) for e in select_entries(rows)]==[(4,'home')]


def test_fee_adjusted_full_exit_uses_real_displayed_price_not_target():
    entry=snap(0,.55)
    r=replay_exit(entry,[entry,snap(1,.68,bid=.67)],target=.65,stop=.12,fee_rate=.05)
    assert r['reason']=='take_profit'
    assert r['exit_vwap']==pytest.approx(.67)
    assert r['sell_shares']==9.09
    expected=9.09*.67-9.09*.55-levels_fee(entry.asks,9.09,.05)-9.09*.05*.67*.33
    assert r['net_pnl']==pytest.approx(expected)
    assert r['sdk_dust_shares']>0


def test_gap_does_not_jump_to_later_profitable_quote():
    e=snap(0,.55)
    r=replay_exit(e,[snap(4,.80)],target=.65,stop=.12,fee_rate=.05)
    assert r['reason']=='observation_gap_censored'
    assert r['net_pnl'] is None


def test_failed_or_incomplete_cycle_censors_before_next_quote():
    e=snap(0,.55)
    r=replay_exit(e,[snap(1,.80)],target=.65,stop=.12,fee_rate=.05,
                  invalid_times=[e.observed_at+timedelta(seconds=30)])
    assert r['reason']=='observation_gap_censored'


def test_stop_is_full_position_and_price_gap_is_not_filled_at_stop_line():
    e=snap(0,.55)
    r=replay_exit(e,[snap(1,.25,bid=.24,size=1),snap(2,.34,bid=.33)],
                  target=.65,stop=.12,fee_rate=.05)
    assert r['blocked_book_observations']==1
    assert r['reason']=='stop'
    assert r['exit_vwap']==pytest.approx(.33)
    assert r['sell_shares']==9.09


def test_different_cohort_cannot_complete_old_path():
    e=snap(0,.55)
    r=replay_exit(e,[replace(snap(1,.8),config_hash='new')],target=.65,stop=.12,fee_rate=.05)
    assert r['reason']=='cohort_boundary_censored'


def test_shallow_profitable_depth_does_not_create_unsellable_partial():
    e=snap(0,.55)
    r=replay_exit(e,[snap(1,.7,bid=.69,size=6)],target=.65,stop=.12,fee_rate=.05)
    assert r['reason']=='right_censored'
    assert r['net_pnl'] is None
    assert r['blocked_book_observations']==1
