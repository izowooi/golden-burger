#!/usr/bin/env python3
"""Small predeclared S-aware displayed-book research. No network, orders or source writes.

Source readers verify pins/lineage. S arithmetic is shared with the local UI;
execution walks/fee conventions reuse conservative_sports_grid. Every result
is a hypothetical book opportunity, never a confirmed fill or true probability.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import csv
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

EXECUTED_CODE = {}
EXECUTED_BYTES = {}
TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent


def load_exact_module(name, path, expected_sha256=None):
    """Execute the same bytes that were hashed; ignore timestamp-based .pyc."""
    path=Path(path).resolve();body=path.read_bytes();digest=hashlib.sha256(body).hexdigest()
    if expected_sha256 is not None and digest!=expected_sha256:
        raise ValueError('analysis dependency SHA mismatch: '+str(path))
    module=ModuleType(name);module.__file__=str(path);module.__package__=''
    sys.modules[name]=module
    exec(compile(body,str(path),'exec'),module.__dict__)
    EXECUTED_CODE[name]={'path':str(path),'sha256':digest}
    EXECUTED_BYTES[name]=body
    return module


load_exact_module('sports_visual_data',TOOLS/'sports_visual_data.py')
load_exact_module('catdog_takeprofit_replay',TOOLS/'catdog_takeprofit_replay.py')
grid=load_exact_module('conservative_sports_grid',TOOLS/'conservative_sports_grid.py')
normalization=load_exact_module('sports_price_normalization',TOOLS/'sports_price_normalization.py')

EPS = 1e-9
START = '1900-01-01T00:00:00Z'
END = '2026-09-08T22:50:00Z'
SPLIT = '2026-09-08T16:00:00Z'
VARIANTS = ('recorded_control', 'profit_no_stop', 's_entry_only', 's_mid_exit',
            's_bid_exit', 's_bid_tail_stop', 's_ask_entry_diagnostic')
FEE_MODELS = ('recorded_schedule', 'sports_005', 'zero')
MIN_NET = .02


@dataclass(frozen=True)
class Baseline:
    id: str
    strategy: str
    sport: str
    entry_min: float
    entry_max: float
    target_mode: str
    target: float
    stop_delta: float
    stop_floor: float
    min_minute: float | None = None
    max_minute: float | None = None
    source_clock_required: bool = False
    clock_basis: str = 'native'
    require_liquidity_gate: bool = False
    leader_margin: float = .005
    max_entry_spread: float = .05
    max_exit_spread: float = .10
    stop_cutoff_minute: float | None = None
    late_minute: float | None = None
    late_profit_fraction: float = .5
    trend_observations: int = 1
    trend_min_move: float = 0.
    trend_max_pullback: float = 0.
    trend_gap_seconds: float = 90.
    tp_signal_contract: str = 'positive_full'
    hard_take_profit: float | None = None
    require_target_profit_feasibility: bool = False
    provenance: str = 'EXPLICIT_COUNTERFACTUAL_PROFILE'

    def __post_init__(self):
        if self.strategy not in ('watermelon', 'peach', 'plum'):
            raise ValueError('unsupported strategy')
        if self.sport not in {'soccer', 'mlb', 'nfl', 'nba', 'nhl'}:
            raise ValueError('unsupported sport')
        if not 0 < self.entry_min <= self.entry_max < 1:
            raise ValueError('invalid entry band')
        if self.clock_basis not in ('native','scheduled_age_proxy','in_play'):
            raise ValueError('invalid clock basis')
        if self.target_mode not in ('hold', 'absolute', 'relative'):
            raise ValueError('invalid target mode')
        if self.stop_delta <= 0 or not 0 <= self.stop_floor < self.entry_min:
            raise ValueError('invalid stop profile')


def book_of(snap):
    # Existing grid readers expose validated float levels; do not claim that
    # this short decimal representation restores more precision than the input.
    return {'asset_id': snap.token, 'market': snap.condition,
            'asks': [{'price': str(p), 'size': str(q)} for p, q in snap.asks],
            'bids': [{'price': str(p), 'size': str(q)} for p, q in snap.bids]}


def partition_roles(snaps, sport):
    if sport != 'soccer':
        return {}
    yes = [s for s in snaps if s.slot[1] == 'YES']
    draws = [s for s in yes if s.slot[0] == 'DRAW' or s.legacy_result_kind == 'DRAW']
    others = [s for s in yes if s not in draws]
    if len(yes) != 3 or len(draws) != 1 or len(others) != 2:
        return {}
    # This is an outcome-partition ordering, not an inferred home/away venue.
    others.sort(key=lambda s: (s.condition, s.token))
    return {draws[0].condition: 'DRAW', others[0].condition: 'TEAM_A', others[1].condition: 'TEAM_B'}


def timestamp_basis(event, snaps):
    if not snaps or any(not s.valid for s in snaps):
        return 'INVALID_OR_MISSING_SOURCE_OBSERVATION'
    origins={s.evidence_origin for s in snaps}
    if origins=={'WATERMELON_LIVE_BBO_POLL_PROXY'}:
        return 'BBO_POLL_PROXY_NO_DEPTH'
    if origins=={'coconut-historical-5m'}:
        return 'VERIFIED_REQUEST_TIMING_CANONICAL_BOOK'
    if origins <= {'full-sports-raw-v1','watermelon-independent-raw-lifecycle-v1','sports-price-recorder-1m-v1','VERIFIED_WHITE_LEGACY_HTTP'}:
        return 'VERIFIED_HTTP_RECEIPT'
    if event.config.get('_analysis_adapter')=='guava':
        return 'VERIFIED_HTTP_RECEIPT'
    return 'RECORDED_SNAPSHOT_PROXY'


def summarize(event, group, *, max_skew=2):
    partition = partition_roles(group.snaps, event.sport)
    tokens, observations = [], []
    bbo_only=event.config.get('financial_replay_eligible')is False and event.config.get('depth_available')is False
    for s in group.snaps:
        tokens.append({'token_id': s.token, 'condition_id': s.condition,
                       'result_kind': s.slot[0], 'partition_role': partition.get(s.condition),
                       'outcome_side': s.slot[1], 'label': s.outcome_label or s.token})
        # A normalized fee descriptor derived from the reader's validated rate,
        # not a claim to reproduce the original Gamma payload.
        fee_market = None if s.fee_rate is None else {
            'feesEnabled': True, 'feeSchedule': {'rate': str(s.fee_rate), 'exponent': 1, 'takerOnly': True}}
        book=({'asset_id':s.token,'market':s.condition,'best_ask':getattr(s,'bbo_best_ask',None),'best_bid':getattr(s,'bbo_best_bid',None)} if bbo_only else book_of(s))
        observations.append({'token_id': s.token, 't': s.time, 'run_id': s.run,
                             'book': book, 'valid': s.valid,
                             'market_open': s.market_open_observed if s.market_open_observed is not None else s.open_observed,
                             'fee_market': fee_market, 'event_id': event.event, 'cohort_id': event.cohort})
    result = normalization.summarize_group(tokens, observations, event.sport,
                                         common_shares=5, max_skew_seconds=max_skew,bbo_only=bbo_only)
    required=[s for s in group.snaps if s.slot[1] in (('YES',) if event.sport=='soccer' else ('DIRECT',))]
    result['timestamp_basis']=timestamp_basis(event,required)
    result['primary_synchronization_proven']=result['timestamp_basis'] in ('VERIFIED_HTTP_RECEIPT','VERIFIED_REQUEST_TIMING_CANONICAL_BOOK')
    result['input_precision'] = 'VALIDATED_GRID_FLOAT_LEVELS_SHORT_DECIMAL_REPR'
    return result


def policy_members(group, spec):
    if spec.strategy == 'watermelon' and spec.sport == 'soccer':
        return [s for s in group.snaps if s.slot[1] == 'YES']
    return list(group.snaps)


def entry_candidate(event, spec, *, max_gap=90, all_outcome_amount_depth=True):
    """First baseline-qualified observation. S filters this fixed paired decision."""
    history = defaultdict(list)
    diagnostics = Counter()
    previous = None
    for index, group in enumerate(event.groups):
        members = policy_members(group, spec)
        if not members:
            diagnostics['empty_policy_set'] += 1
            continue
        decision = max(s.time for s in members)
        if previous is not None and (decision <= previous or decision-previous > max_gap
                or grid.failed_between(event.failures, previous, decision)):
            history.clear()
        previous = decision
        expected = (3 if spec.strategy == 'watermelon' else 6) if spec.sport == 'soccer' else 2
        if (len(members) != expected or len({s.token for s in members}) != expected
                or any(not s.valid or not s.entry_set_complete or (spec.strategy!='watermelon' and (s.midpoint is None or all_outcome_amount_depth and s.buy is None)) for s in members)
                or max(s.time for s in members)-min(s.time for s in members) > 5):
            history.clear(); diagnostics['incomplete_baseline_set'] += 1
            continue
        if spec.strategy == 'watermelon':
            eligible = [s for s in members if s.buy is not None and s.gate_observed and spec.entry_min-EPS <= s.buy.vwap <= spec.entry_max+EPS
                        and (spec.hard_take_profit is None or s.buy.vwap < spec.hard_take_profit-EPS)
                        and s.spread is not None and 0<=s.spread<=spec.max_entry_spread+EPS]
            chosen = eligible[0] if len(eligible)==1 else None
            if len(eligible)>1:diagnostics['multiple_eligible_event_outcomes']+=1
        else:
            ordered = sorted(members, key=lambda s: (-s.midpoint, s.token))
            chosen = ordered[0]
            if len(ordered) > 1 and chosen.midpoint-ordered[1].midpoint < spec.leader_margin-EPS:
                diagnostics['ambiguous_leader'] += 1; chosen = None
        reason = None
        if chosen is not None:
            clock_value=chosen.minute
            if spec.clock_basis=='scheduled_age_proxy':
                clock_value=chosen.scheduled_age if chosen.scheduled_age is not None else (chosen.minute if chosen.source_reason=='SCHEDULED_START_AGE_SHADOW_ONLY' else None)
            if chosen.buy is None: reason = 'selected_token_entry_amount_depth'
            elif not chosen.open_observed: reason = 'entry_not_in_play'
            elif spec.require_liquidity_gate and not chosen.gate_observed: reason = 'liquidity_volume_gate_unproven'
            elif chosen.spread is None or not 0 <= chosen.spread <= spec.max_entry_spread+EPS: reason = 'entry_spread'
            elif not spec.entry_min-EPS <= chosen.buy.vwap <= spec.entry_max+EPS: reason = 'entry_band'
            elif spec.source_clock_required and clock_value is None: reason = 'native_clock_missing'
            elif spec.clock_basis!='in_play' and spec.min_minute is not None and (clock_value is None or clock_value < spec.min_minute-EPS): reason = 'source_clock_before_window'
            elif spec.clock_basis!='in_play' and spec.max_minute is not None and (clock_value is None or clock_value > spec.max_minute+EPS): reason = 'source_clock_after_window'
            if reason is None and spec.trend_observations > 1:
                values = history[chosen.token][-(spec.trend_observations-1):]+[chosen]
                prices = [v.buy.vwap for v in values]
                if (len(values) != spec.trend_observations or prices[0] >= spec.entry_min-EPS
                        or prices[-1]-prices[0] < spec.trend_min_move-EPS
                        or any(a-b > spec.trend_max_pullback+EPS for a,b in zip(prices,prices[1:]))
                        or any(not 0<b.time-a.time<=spec.trend_gap_seconds for a,b in zip(values,values[1:]))):
                    reason = 'registered_trend_not_met'
            if reason is None:
                return {'index': index, 'snap': chosen, 'decision_time': decision}, dict(diagnostics)
            diagnostics[reason] += 1
        for snap in members:
            if snap.buy is None:
                history[snap.token].clear();continue
            history[snap.token].append(snap)
            history[snap.token] = history[snap.token][-max(5,spec.trend_observations):]
    return None, dict(diagnostics)


def decimal_metric(summary, field):
    value = summary.get(field)
    return Decimal(value) if value is not None else None


def s_entry_check(summary, candidate, variant, policy_members_, *, max_skew=2):
    if variant in ('recorded_control', 'profit_no_stop'):
        return True, None
    if not summary.get('valid') or not summary.get('signal_eligible'):
        return False, 'S_entry_incomplete_or_not_open'
    # Includes a selected NO and every quote used to rank its baseline decision.
    required = [s.time for s in policy_members_]
    if required and (max(required+[float(summary['t'])])-min(required) > max_skew+EPS):
        return False, 'S_and_candidate_or_rank_receipt_skew'
    field = 's_ask' if variant == 's_ask_entry_diagnostic' else 's_mid'
    value = decimal_metric(summary, field)
    low, high = Decimal('.99'), Decimal('1.02' if field == 's_ask' else '1.01')
    if value is None or not low <= value <= high:
        return False, 'S_entry_band'
    return True, None


def event_terminal(event, snap, cutoff):
    proofs = []
    for p in event.terminals.get((snap.condition, snap.token), []):
        at = grid.visual.timestamp(p.get('published_at') or p.get('observed_at'))
        if at is None or at < snap.time or at >= cutoff:
            continue
        if p.get('payout') not in (0, .5, 1) or isinstance(p.get('payout'), bool):
            continue
        observer = p.get('observer_config') or p.get('config_hash')
        own = event.config.get('config_hash')
        if observer is not None and own is not None and observer != own:
            continue
        proofs.append((at, p))
    if not proofs or len({p['payout'] for _,p in proofs}) != 1:
        return None
    return min(proofs, key=lambda item:item[0])


def replay_event(event, spec, variant, fee_model='recorded_schedule', *, cutoff=None, max_gap=90, max_skew=2, summaries=None, candidate_info=None):
    if variant not in VARIANTS or fee_model not in FEE_MODELS:
        raise ValueError('unregistered comparison')
    cutoff = grid.visual.timestamp(END) if cutoff is None else cutoff
    output = {'source':event.source,'cohort':event.cohort,'event':event.event,'title':event.title,
              'sport':event.sport,'season_phase':event.config.get('season_phase','UNSPECIFIED_BY_READER'),'baseline':spec.id,'strategy':spec.strategy,'variant':variant,
              'fee_model':fee_model,'status':'NO_ENTRY','reason':'baseline_no_candidate','net':None,
              'lower':0.,'upper':0.,'partial_depth_observations':0,'S_unavailable_observations':0,
              'entry_quote_utc':None,'entry_decision_utc':None,'exit_utc':None,'exit_vwap':None,
              'clock_contract':spec.clock_basis,'exit_lifecycle_contract':'EVENT_IN_PLAY_AND_MARKET_OPEN' if variant=='recorded_control' else 'MARKET_OPEN_TP_EVENT_IN_PLAY_STOP','baseline_provenance':spec.provenance,'entry_completeness_contract':'ALL_OUTCOMES_FIVE_DOLLAR_WALKS' if variant=='recorded_control' and spec.strategy in ('peach','plum') else 'SELECTED_FIVE_DOLLAR_WALK_AND_VERIFIED_QUOTE_SET','entry_vwap':None,'entry_token':None,'entry_slot':None,'entry_condition':None,'entry_team_name':None,'entry_outcome_label':None,'path_timestamp_bases':[],'exit_S':None,'entry_S':None}
    if event.sport != spec.sport:
        return {**output,'reason':'sport_profile_mismatch'}
    candidate, diag = candidate_info if candidate_info is not None else entry_candidate(event, spec, max_gap=max_gap,all_outcome_amount_depth=variant=='recorded_control')
    output['entry_diagnostics'] = diag
    if candidate is None:
        return output
    index, entry = candidate['index'], candidate['snap']
    summary = summaries[index] if summaries is not None else summarize(event,event.groups[index],max_skew=max_skew)
    accepted, reason = s_entry_check(summary, candidate, variant, policy_members(event.groups[index],spec),max_skew=max_skew)
    basis=timestamp_basis(event,policy_members(event.groups[index],spec))
    output.update(entry_S=summary,entry_vwap=entry.buy.vwap,entry_token=entry.token,entry_slot=list(entry.slot),entry_condition=entry.condition,
                  entry_team_name=entry.verified_team_name,entry_outcome_label=entry.outcome_label,path_timestamp_bases=[basis],timestamp_basis=basis,
                  primary_synchronization_proven=basis=='VERIFIED_HTTP_RECEIPT',entry_floor_basis='ACTUAL_FIVE_DOLLAR_ASK_VWAP',
                  normalized_entry_floor=None,hard_take_profit=spec.hard_take_profit)
    if not accepted:
        return {**output,'status':'FILTERED','reason':reason}
    decision = max(candidate['decision_time'], float(summary['t'])) if variant not in ('recorded_control','profit_no_stop') else candidate['decision_time']
    if decision >= cutoff:
        return {**output,'reason':'entry_after_cutoff'}
    economics = grid.buy_economics({'buy_walk':asdict(entry.buy),'entry_fee_rate':entry.fee_rate},fee_model,'v2_cash')
    output.update(entry_quote_utc=grid.iso(entry.time),entry_decision_utc=grid.iso(decision),status='CENSORED',reason='right_censored')
    if economics is None:
        return {**output,'status':'UNSCORABLE','reason':'entry_fee_evidence_missing','lower':None,'upper':None}
    output.update(economics)
    quantity, cost = economics['sell_shares'], economics['usd_cost']
    output.update(lower=-cost,upper=quantity-cost)
    if quantity < 5-EPS:
        return {**output,'status':'REJECTED','reason':'minimum_sell_quantity'}
    if spec.require_target_profit_feasibility:
        target_walk=grid.walk(((spec.hard_take_profit,quantity),),quantity,False)
        target_fee=grid.fee(target_walk,fee_model,entry.fee_rate)
        room=target_walk.amount-target_fee-cost if target_fee is not None else None
        output['entry_target_net_room']=room
        if room is None or room<=EPS:
            return {**output,'status':'REJECTED','reason':'no_fee_adjusted_profit_room_at_target','lower':0.,'upper':0.}
    terminal = event_terminal(event,entry,cutoff)
    previous = decision
    stop_price = max(spec.stop_floor,entry.buy.vwap-spec.stop_delta) if variant == 'recorded_control' else max(.01,entry.buy.vwap-.20)
    has_stop = variant in ('recorded_control','s_bid_tail_stop')
    s_exit = variant in ('s_mid_exit','s_bid_exit','s_bid_tail_stop','s_ask_entry_diagnostic')
    for i, group in enumerate(event.groups[index+1:], start=index+1):
        same = [s for s in group.snaps if s.token==entry.token and s.condition==entry.condition]
        quote_time = same[0].time if len(same)==1 else group.time
        current_summary = summaries[i] if summaries is not None else summarize(event,group,max_skew=max_skew)
        at = quote_time
        if terminal and previous <= terminal[0] <= at and terminal[0]-previous <= max_gap and not grid.failed_between(event.failures,previous,terminal[0]):
            payout = terminal[1]['payout'];net = quantity*payout-cost
            return {**output,'status':'COMPLETE','reason':'RESOLUTION','net':net,'lower':net,'upper':net,
                    'exit_utc':grid.iso(terminal[0]),'exit_vwap':payout}
        if at >= cutoff:
            break
        if at <= previous or at-previous > max_gap or grid.failed_between(event.failures,previous,at):
            return {**output,'reason':'path_gap_or_failed_run'}
        if len(same)!=1 or not same[0].valid:
            return {**output,'reason':'selected_token_or_lineage_gap'}
        selected = same[0]
        path_basis=timestamp_basis(event,policy_members(group,spec))
        output['path_timestamp_bases']=sorted(set(output['path_timestamp_bases']+[path_basis]))
        output['primary_synchronization_proven']=all(b=='VERIFIED_HTTP_RECEIPT'for b in output['path_timestamp_bases'])
        available = min(quantity,sum(q for _,q in selected.bids))
        walk = grid.walk(selected.bids,quantity,False)
        if walk is None:
            output['partial_depth_observations'] += 1
            output['last_available_sell_shares'] = available
        market_open = selected.market_open_observed if selected.market_open_observed is not None else selected.open_observed
        spread_ok = selected.ask_state == 'EMPTY_VALID' or (selected.spread is not None and 0 <= selected.spread <= spec.max_exit_spread+EPS)
        executable = walk is not None and market_open and spread_ok
        if variant=='recorded_control':executable=executable and selected.open_observed
        stop_enabled = has_stop and (variant!='recorded_control' or spec.stop_cutoff_minute is None
                           or selected.minute is not None and selected.minute < spec.stop_cutoff_minute-EPS)
        stop = bool(stop_enabled and selected.bids and selected.bids[0][0] <= stop_price+EPS)
        if stop and executable and selected.open_observed and selected.spread is not None and 0 <= selected.spread <= spec.max_exit_spread+EPS:
            sell_fee = grid.fee(walk,fee_model,selected.fee_rate)
            if sell_fee is None:
                return {**output,'status':'UNSCORABLE','reason':'stop_fee_evidence_missing','lower':None,'upper':None}
            net = walk.amount-sell_fee-cost
            return {**output,'status':'COMPLETE','reason':'SL','net':net,'lower':net,'upper':net,
                    'exit_utc':grid.iso(quote_time),'exit_vwap':walk.vwap,'sell_fee':sell_fee}
        if spec.hard_take_profit is not None and executable and walk.worst >= spec.hard_take_profit-EPS:
            sell_fee=grid.fee(walk,fee_model,selected.fee_rate)
            if sell_fee is None:
                return {**output,'status':'UNSCORABLE','reason':'hard_tp_fee_evidence_missing','lower':None,'upper':None}
            net=walk.amount-sell_fee-cost
            if net>EPS:
                return {**output,'status':'COMPLETE','reason':'USER_HARD_TP','net':net,'lower':net,'upper':net,
                        'exit_utc':grid.iso(quote_time),'exit_vwap':walk.vwap,'sell_fee':sell_fee}
        if s_exit:
            if not current_summary.get('valid'):
                return {**output,'reason':'S_set_or_sync_gap'}
            s_max=float(current_summary['t']);s_min=s_max-float(current_summary['skew_seconds'])
            if max(quote_time,s_max)-min(quote_time,s_min) > max_skew+EPS:
                return {**output,'reason':'S_and_selected_exit_receipt_skew'}
            at=max(quote_time,s_max)
            if at>=cutoff:break
            if at-previous>max_gap or grid.failed_between(event.failures,previous,at):
                return {**output,'reason':'S_decision_gap_or_failed_run'}
            if terminal and previous<=terminal[0]<=at and terminal[0]-previous<=max_gap and not grid.failed_between(event.failures,previous,terminal[0]):
                payout=terminal[1]['payout'];net=quantity*payout-cost
                return {**output,'status':'COMPLETE','reason':'RESOLUTION','net':net,'lower':net,'upper':net,'exit_utc':grid.iso(terminal[0]),'exit_vwap':payout}
            if not current_summary.get('signal_eligible'):
                previous=at
                output['S_unavailable_observations'] += 1
                continue
        previous=at
        if not executable:
            continue
        sell_fee = grid.fee(walk,fee_model,selected.fee_rate)
        if sell_fee is None:
            return {**output,'status':'UNSCORABLE','reason':'exit_fee_evidence_missing','lower':None,'upper':None}
        net = walk.amount-sell_fee-cost
        if variant == 'recorded_control':
            if spec.target_mode == 'hold':
                continue
            target = spec.target if spec.target_mode=='absolute' else entry.buy.vwap+spec.target
            if spec.late_minute is not None and selected.minute is not None and selected.minute >= spec.late_minute:
                target = entry.buy.vwap+(target-entry.buy.vwap)*spec.late_profit_fraction
            hit = (walk.vwap if spec.tp_signal_contract=='peach_published' else walk.worst) >= target-EPS
            if spec.tp_signal_contract=='plum_published':
                at_target = sum(q for p,q in selected.bids if p>=target-EPS)
                if 5-EPS <= at_target < quantity-EPS:
                    return {**output,'reason':'published_partial_tp_lifecycle_not_replayed'}
        else:
            hit = net >= MIN_NET-EPS
            if s_exit:
                metric = 's_mid' if variant=='s_mid_exit' else 's_bid'
                threshold = Decimal('1.02' if metric=='s_mid' else '1.01')
                value = decimal_metric(current_summary,metric)
                hit = hit and value is not None and value >= threshold
        if hit:
            return {**output,'status':'COMPLETE','reason':'TP' if variant=='recorded_control' else 'S_PROFIT' if s_exit else 'PROFIT',
                    'net':net,'lower':net,'upper':net,'exit_utc':grid.iso(at),'exit_vwap':walk.vwap,
                    'sell_fee':sell_fee,'exit_S':current_summary if s_exit else None}
    if terminal and 0 <= terminal[0]-previous <= max_gap and not grid.failed_between(event.failures,previous,terminal[0]):
        payout=terminal[1]['payout'];net=quantity*payout-cost
        return {**output,'status':'COMPLETE','reason':'RESOLUTION','net':net,'lower':net,'upper':net,'exit_utc':grid.iso(terminal[0]),'exit_vwap':payout}
    return output


def freeze_baselines(config_path, output):
    """Metadata-only preparation; never reads quote outcomes or fills."""
    config_path=Path(config_path);payload=json.loads(config_path.read_text())
    unique={}
    for record in payload['sources']:
        parameters=record['parameters'];entry=parameters['entry']
        strategy={'golden-watermelon-live':'watermelon','golden-peach':'peach','golden-plum':'plum'}.get(record['strategy'])
        if strategy is None:continue
        sport=parameters['sport_family'];native=parameters.get('source_clock_required') is True
        if strategy=='watermelon':
            target_mode,target='hold',0.
            delta=entry['max_entry_drawdown'];clock='in_play'
        elif strategy=='peach':
            target_mode,target='relative',entry['take_profit_delta']
            delta=entry['stop_loss_delta'];clock='native' if native else 'scheduled_age_proxy'
        else:
            target_mode,target='absolute',entry['take_profit_price']
            delta=entry['stop_loss_delta'];clock='native' if native else 'in_play'
        fields=dict(id='pending',strategy=strategy,sport=sport,entry_min=entry['prob_min'],entry_max=entry['prob_max'],
                    target_mode=target_mode,target=target,stop_delta=delta,stop_floor=entry['stop_price'],
                    min_minute=entry.get('min_source_minute',0) if strategy!='watermelon' else None,
                    max_minute=entry.get('max_source_minute'),source_clock_required=native,clock_basis=clock,
                    require_liquidity_gate=strategy=='watermelon',leader_margin=entry.get('min_leader_margin',0),
                    max_entry_spread=entry.get('max_entry_spread',parameters.get('max_entry_spread',.05)),
                    max_exit_spread=entry.get('max_stop_spread',.10),
                    stop_cutoff_minute=entry.get('stop_cutoff_minute') if sport=='soccer' else None,
                    late_minute=entry.get('late_exit_minute') if sport=='soccer' else None,
                    late_profit_fraction=entry.get('late_profit_fraction',.5),
                    trend_observations=entry.get('trend_observations',1),trend_min_move=entry.get('trend_min_cumulative_move',0),
                    trend_max_pullback=entry.get('trend_max_pullback',0),trend_gap_seconds=entry.get('trend_max_gap_seconds',90),
                    tp_signal_contract='peach_published' if strategy=='peach' else 'plum_published' if strategy=='plum' else 'positive_full',
                    provenance='CURRENT_RECORDED_PARAMETERS_APPLIED_TO_HISTORICAL_QUOTES_NOT_ACTUAL_FILLS')
        signature=hashlib.sha256(json.dumps({k:v for k,v in fields.items() if k!='id'},sort_keys=True).encode()).hexdigest()
        fields['id']=f'{strategy}_{sport}_{signature[:10]}'
        spec=Baseline(**fields)
        item=unique.setdefault(signature,{'spec':asdict(spec),'runtime_evidence':[]})
        item['runtime_evidence'].append({k:record[k]for k in ('runtime_job','mode','source_key','config_hash','source_digest','latest_started_at','pin_sha256')})
    result={'schema':'sports-s-baselines-v1','created_at':datetime.now(timezone.utc).isoformat(),
            'metadata_source':str(config_path.resolve()),'metadata_sha256':grid.visual.sha256(config_path),
            'outcomes_read':False,'definitions':list(unique.values()),
            'watermelon_tp_contract':'No take-profit in actual recorded config; hold/stop/resolution reference, not current repository default',
            'clock_contract':'Peach non-soccer current deployed scheduled-age proxy is labelled separately from native source clock',
            'missing_registered_sports':{strategy:[sport for sport in ('soccer','mlb','nfl','nba','nhl')
                if not any(x['spec']['strategy']==strategy and x['spec']['sport']==sport for x in unique.values())]
                for strategy in ('watermelon','peach','plum')}}
    output=Path(output)
    with output.open('x') as handle:json.dump(result,handle,indent=2)
    return result


def read_recorder_source(source, start, end, *, code_directory, data_root=None):
    """Reuse the workbench's verified shard/carryover reader, without writing UI files."""
    parts=source.get('constituents') or [source]
    sources=[{**{k:v for k,v in source.items() if k!='constituents'},**part}for part in parts]
    if any(not x.get('pinned') for x in sources):
        raise ValueError('recorder requires verified pins')
    sources=[{**x,'synced_at':x.get('synced_at') or x.get('source_completed_at') or 'PIN_TIME_NOT_SUPPLIED'} for x in sources]
    projector=load_exact_module('s_recorder_shard_projector',REPO/'daily-rsync/src/daily_rsync/sports_recorder.py')
    original=REPO/'golden-coconut/src/polybot/recorder_export.py';body=original.read_bytes();digest=hashlib.sha256(body).hexdigest()
    code_directory=Path(code_directory);code_directory.mkdir(parents=True,exist_ok=True)
    pinned_reader=code_directory/('recorder_export_'+digest+'.py')
    if not pinned_reader.exists():pinned_reader.write_bytes(body)
    elif pinned_reader.read_bytes()!=body:raise ValueError('immutable reader collision')
    reader=load_exact_module('s_recorder_exact_reader',pinned_reader,digest)
    grouped=defaultdict(lambda:defaultdict(list));configs={};titles={};phases=defaultdict(set);stats=Counter()
    metadata={};terminal_records=defaultdict(list);failures=defaultdict(list)
    cutoff=grid.visual.timestamp(end)
    for physical in sources:
        path=Path(physical['local_path']).resolve()
        with sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True) as connection:
            connection.row_factory=sqlite3.Row
            for row in connection.execute('SELECT * FROM cycles'):
                cfg=json.loads(row['config_json']);t=grid.visual.timestamp(row['reference_at']);finish=grid.visual.timestamp(row['published_at'])
                if t>=cutoff:continue
                if cfg.get('observation_mode')!='SCHEDULED':raise ValueError('probe/unknown recorder mode excluded')
                key=(cfg['config_hash'],cfg['source_digest'])
                metadata[key]=cfg
                if row['status']!='SUCCEEDED' or finish>=cutoff:failures[key].append((t,finish))
        for terminal in reader.iter_terminals(path,physical['local_sha256'],end=end):
            if terminal.get('observation_mode')!='SCHEDULED':continue
            key=(terminal['config_hash'],terminal['strategy_source_digest'])
            terminal_records[key].append(terminal)
    def collect(projected_source, output, lower, upper, index, rows, config_index, runs, terminals, **kwargs):
        for row in rows:
            stats['raw_rows']+=1
            cfg=row['config'];sport=row['sport_family'];key=(row['config_hash'],row['strategy_source_digest'],sport,row['event_id'])
            configs[key]={**cfg,'strategy_source_digest':cfg['source_digest'],'raw_point_in_time_archive':True,'cadence_seconds':60}
            valid=(row['run_status']=='SUCCEEDED' and row['raw_book_valid'] is True and row['raw_point_in_time_identity_proven'] is True)
            raw=row.get('book') or {};asks=bids=()
            try:
                asks,bids=grid.levels(raw,'asks'),grid.levels(raw,'bids')
                if asks and bids and bids[0][0]>asks[0][0]+EPS:raise ValueError('crossed book')
            except ValueError:valid=False
            market=row.get('market_fields') or {};clock=(row.get('clock') or {}).get('payload') or {}
            opened=(str(market.get('conditionId') or market.get('condition_id'))==row['condition_id']
                    and market.get('active') is True and market.get('closed') is False
                    and market.get('enableOrderBook') is True and market.get('acceptingOrders') is True)
            in_play=opened and clock.get('live') is True and clock.get('ended') is False
            liquidity=grid.visual.number(market.get('liquidityNum',market.get('liquidity')))
            volume=grid.visual.number(market.get('volumeNum',market.get('volume')))
            gate=in_play and liquidity is not None and liquidity>=5000 and volume is not None and volume>=5000
            t=grid.visual.timestamp(row['timestamp']);scheduled=grid.visual.timestamp(row.get('game_start'))
            snap=grid.Snap(str(row['token_id']),row['condition_id'],(row.get('result_kind'),row.get('outcome_side')),
                t,row['run_id'],grid.raw_source_minute(clock,sport),row.get('midpoint'),asks,bids,valid,in_play,gate,
                grid.raw_fee_rate(market),asks[0][0]-bids[0][0] if asks and bids else None,
                grid.ask_side_state(raw),'RECORDER_NATIVE_FIELDS',grid.walk(asks,5,True),
                (t-scheduled)/60 if scheduled is not None else None,valid,
                'sports-price-recorder-1m-v1',row.get('book_status'),opened,row.get('outcome'),
                row.get('verified_role'),row.get('verified_team_name'),row.get('role_evidence_scope'),row.get('legacy_result_kind'))
            grouped[key][row['run_id']].append(snap);titles[key]=row.get('title') or row['event_id']
            phases[key].add(row.get('season_phase') or 'UNKNOWN')
            stats['valid_rows' if valid else 'invalid_rows']+=1
    exporter=SimpleNamespace(sha256=grid.visual.sha256,timestamp=grid.visual.timestamp,export_rows=collect)
    projector.export_group(exporter,pinned_reader,sources,Path('.'),start,end,0,Path(data_root) if data_root is not None else REPO/'daily-rsync/data')
    events=[]
    for key,by_run in grouped.items():
        cfg_hash,src,sport,eid=key;cfg=configs[key]
        cfg['season_phase']=next(iter(phases[key])) if len(phases[key])==1 else 'MIXED_OR_CHANGED'
        groups=[]
        for run,snaps in by_run.items():
            complete=(len(snaps)==(6 if sport=='soccer' else 2) and len({s.token for s in snaps})==len(snaps)
                      and all(s.valid and s.buy is not None and s.midpoint is not None for s in snaps)
                      and max(s.time for s in snaps)-min(s.time for s in snaps)<=5)
            groups.append(grid.Group(max(s.time for s in snaps),run,snaps,complete))
        terminal_by_token=defaultdict(list)
        for proof in terminal_records[(cfg_hash,src)]:
            if proof['event_id']!=eid:continue
            terminal_by_token[(proof['condition_id'],proof['token_id'])].append({**proof,'observer_config':cfg_hash})
        events.append(grid.Event(source['id'],grid.visual.identifier([source['id'],cfg_hash,src,sport]),sport,eid,titles[key],cfg,
                      sorted(groups,key=lambda x:x.time),failures[(cfg_hash,src)],dict(terminal_by_token)))
    return events,{'source':source['id'],'view':'WORKBENCH_VERIFIED_RECORDER_SHARDS','events':len(events),'stats':dict(stats),
                   'constituents':[{'path':x['local_path'],'sha256':x['local_sha256'],'source_key':x['source_key']}for x in sources],
                   'reader_sha256':digest,'no_cross_source_quote_join':True}


METRICS=('s_best_ask','s_best_bid','s_mid','s_ask','s_bid','s_ask_with_fees','s_bid_after_fees',
         'total_top_spread','ask_excess_from_mid','ask_excess_from_spread')
PAIR_REFERENCE={'recorded_control':None,'profit_no_stop':'recorded_control','s_entry_only':'profit_no_stop',
                's_mid_exit':'s_entry_only','s_bid_exit':'s_entry_only','s_bid_tail_stop':'s_bid_exit',
                's_ask_entry_diagnostic':'s_bid_exit'}


def dump_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def write_csv(path,rows):
    with Path(path).open('w',newline='',encoding='utf-8') as handle:
        if not rows:return
        fields=list(rows[0]);fields.extend(sorted(set().union(*(row.keys()for row in rows))-set(fields)))
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()})


def quantile(values,p):
    if not values:return None
    values=sorted(values);at=(len(values)-1)*p;low=int(at);high=min(low+1,len(values)-1)
    return values[low]+(values[high]-values[low])*(at-low)


def empty_s_stats():
    return {'groups':0,'valid':0,'signal_eligible':0,'cost_evidence_eligible':0,'s_mid_gt_1':0,'s_mid_ge_1_02':0,
            's_bid_ge_1_01':0,'s_bid_signal_eligible_ge_1_01':0,'s_bid_after_fees_gt_1':0,'quote_interval_contains_1':0,'events':set(),'reasons':Counter(),
            'metrics':{key:[] for key in METRICS}}


def add_s_stats(stats,event,summary):
    stats['groups']+=1;stats['events'].add(event.event)
    stats['valid']+=int(summary['valid']);stats['signal_eligible']+=int(summary['signal_eligible'])
    stats['cost_evidence_eligible']+=int(summary.get('cost_evidence_eligible',False))
    stats['reasons'].update(summary['reasons'])
    for key in METRICS:
        if summary.get(key) is not None:stats['metrics'][key].append(float(summary[key]))
    mid,bid,ba,bb=[decimal_metric(summary,k)for k in ('s_mid','s_bid','s_best_ask','s_best_bid')]
    stats['s_mid_gt_1']+=int(mid is not None and mid>1)
    stats['s_mid_ge_1_02']+=int(mid is not None and mid>=Decimal('1.02'))
    stats['s_bid_ge_1_01']+=int(bid is not None and bid>=Decimal('1.01'))
    stats['s_bid_signal_eligible_ge_1_01']+=int(bid is not None and bid>=Decimal('1.01')and summary['signal_eligible'])
    net_bid=decimal_metric(summary,'s_bid_after_fees')
    stats['s_bid_after_fees_gt_1']+=int(net_bid is not None and net_bid>1 and summary['signal_eligible'])
    stats['quote_interval_contains_1']+=int(ba is not None and bb is not None and bb<=1<=ba)


def final_s_stats(key,stats):
    source,cohort,sport,phase,cadence,basis=key
    row={'source':source,'cohort':cohort,'sport':sport,'season_phase':phase,'cadence_seconds':cadence,
         'timestamp_basis':basis,'independent_events':len(stats['events'])}
    row.update({k:v for k,v in stats.items() if k not in ('metrics','events','reasons')})
    row['reasons']=dict(stats['reasons'])
    for metric,values in stats['metrics'].items():
        row[metric+'_count']=len(values)
        row[metric+'_min']=min(values) if values else None
        row[metric+'_p50']=quantile(values,.5)
        row[metric+'_p95']=quantile(values,.95)
        row[metric+'_max']=max(values) if values else None
    return row


def result_bounds(row):
    if row['status'] in ('NO_ENTRY','FILTERED','REJECTED'):return 0.,0.,0.
    return row.get('lower'),row.get('upper'),row.get('net')


def paired_interval(pairs,seed=20260909,repetitions=2000):
    if not pairs:return {'lower':None,'upper':None,'complete_case_mean':None,'n':0,'all_bounds_known':False}
    low=[];high=[];complete=[]
    for a,b in pairs:
        al,au,ap=result_bounds(a);bl,bu,bp=result_bounds(b)
        low.append(al-bu if al is not None and bu is not None else None)
        high.append(au-bl if au is not None and bl is not None else None)
        if ap is not None and bp is not None:complete.append(ap-bp)
    result={'lower':None,'upper':None,'complete_case_mean':sum(complete)/len(complete) if complete else None,
            'complete_case_n':len(complete),'n':len(pairs),'all_bounds_known':None not in low and None not in high}
    if not result['all_bounds_known']:return result
    if len(set(low))==len(set(high))==1:
        return {**result,'lower':low[0],'upper':high[0]}
    randomizer=random.Random(seed);lower=[];upper=[];n=len(pairs)
    for _ in range(repetitions):
        indices=[randomizer.randrange(n)for _ in range(n)]
        lower.append(sum(low[i]for i in indices)/n);upper.append(sum(high[i]for i in indices)/n)
    result.update(lower=quantile(lower,.025),upper=quantile(upper,.975))
    return result


def load_source(source,start,end,output,modules):
    kind=source['adapter_kind']
    if kind in ('grid','white_legacy'):
        return grid.read_source(source,start,end)
    if kind=='coconut_recorder':
        return read_recorder_source(source,start,end,code_directory=output/'code')
    if kind not in ('white_pair','guava','coconut_historical','watermelon_live_bbo','white_legacy_eventless'):
        raise ValueError('explicit adapter unsupported: '+kind)
    if kind not in modules:
        if kind=='white_pair':
            load_exact_module('watermelon_raw_sidecar',TOOLS/'watermelon_raw_sidecar.py')
        path=REPO/source['adapter_path']
        modules[kind]=load_exact_module('s_adapter_'+kind,path,source.get('adapter_sha256'))
    return modules[kind].read_source(source,start,end)


def verify_pin_metadata(source):
    if source.get('pinned') is not True or not source.get('manifest'):
        raise ValueError('standard pin metadata required')
    manifest=json.loads(Path(source['manifest']).read_text());path=Path(source['local_path']).resolve()
    if manifest.get('sha256')!=source['local_sha256'] or manifest.get('pinned_path')!=str(path):
        raise ValueError('pin manifest identity mismatch')
    if manifest.get('quick_check')!=['ok']:
        raise ValueError('pin quick_check not proven')


def run_study(source_manifest, baseline_paths, output, *, start=START,end=END,split=SPLIT,only_adapters=None):
    output=Path(output).resolve()
    if output.exists() and any(output.iterdir()):raise ValueError('new or empty output required')
    output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads(Path(source_manifest).read_text());sources=manifest['sources']
    if len({s['id']for s in sources})!=len(sources):raise ValueError('duplicate logical source view')
    specs=[];registries=[]
    for path in baseline_paths:
        raw=json.loads(Path(path).read_text());registries.append({'path':str(Path(path).resolve()),'sha256':grid.visual.sha256(Path(path))})
        specs.extend(Baseline(**r['spec'])for r in raw['definitions'])
    if len({x.id for x in specs})!=len(specs):raise ValueError('duplicate baseline profile')
    dump_json(output/'RUN.json',{'schema':'sports-s-calibration-v1','started_at':datetime.now(timezone.utc).isoformat(),
        'source_manifest':str(Path(source_manifest).resolve()),'source_manifest_sha256':grid.visual.sha256(Path(source_manifest)),
        'baseline_registries':registries,'start':start,'end_exclusive':end,'split':split,'variants':VARIANTS,
        'analysis_source_sha256':grid.visual.sha256(Path(__file__)),'minimum_net_profit':MIN_NET,'fee_models':FEE_MODELS,'retrospective_only':True,'promotion_allowed':False,
        'same_source_price_only':True,'only_adapters':only_adapters,'fixed_thresholds_no_search':True})
    dump_json(output/'SOURCES.json',sources)
    modules={};audits=[];coverage=[];global_first={};s_stats={};rows=[];samples=[];source_rows=0
    with gzip.open(output/'group-states.jsonl.gz','wt',encoding='utf-8')as group_file, gzip.open(output/'event-outcomes-unpartitioned.jsonl.gz','wt',encoding='utf-8')as outcome_file:
        for source_number,source in enumerate(sources,1):
            if not source.get('analysis_enabled',True) or (only_adapters and source['adapter_kind'] not in only_adapters):
                coverage.append({'source':source['id'],'adapter':source['adapter_kind'],'status':'EXCLUDED_VIEW',
                    'reason':source.get('reason') or source.get('schema_support_note') or 'separate bounded adapter batch'})
                continue
            print(json.dumps({'phase':'read_source','index':source_number,'sources':len(sources),'source':source['id'],'adapter':source['adapter_kind']}),flush=True)
            low=grid.iso(max(grid.visual.timestamp(start),grid.visual.timestamp(source.get('analysis_start') or start)))
            high=grid.iso(min(grid.visual.timestamp(end),grid.visual.timestamp(source.get('analysis_end_exclusive') or end)))
            if grid.visual.timestamp(low)>=grid.visual.timestamp(high):
                coverage.append({'source':source['id'],'adapter':source['adapter_kind'],'status':'EXCLUDED_RANGE','reason':'no interval'})
                continue
            try:
                verify_pin_metadata(source)
                events,audit=load_source(source,low,high,output,modules)
            except Exception as error:
                coverage.append({'source':source['id'],'adapter':source['adapter_kind'],'status':'READER_REJECTED',
                    'reason':type(error).__name__+': '+str(error),'schema_support_note':source.get('schema_support_note')})
                print(json.dumps({'phase':'source_rejected','source':source['id'],'error_type':type(error).__name__,'reason':str(error)}),flush=True)
                continue
            dependency_rows=list(audit.get('dependencies',[]))+[{'path':p,'sha256':h}for p,h in audit.get('classifier_dependencies',{}).items()]
            for dependency in dependency_rows:
                dependency_path=Path(dependency['path']);body=dependency_path.read_bytes()
                if hashlib.sha256(body).hexdigest()!=dependency['sha256']:raise ValueError('adapter dependency changed before replay')
                name='adapter_dependency_'+dependency_path.stem+'_'+dependency['sha256'][:12]
                EXECUTED_CODE[name]={'path':str(dependency_path),'sha256':dependency['sha256']}
                (output/'code').mkdir(exist_ok=True)
                (output/'code'/(dependency_path.stem+'_'+dependency['sha256']+dependency_path.suffix)).write_bytes(body)
            audits.append(audit);source_rows+=audit.get('stats',{}).get('raw_rows',0)
            if audit.get('supported') is False:
                coverage.append({'source':source['id'],'adapter':source['adapter_kind'],'status':'UNSUPPORTED_SCHEMA','reason':audit.get('reason') or audit.get('unsupported_reason') or str(audit.get('missing_columns','see source audit'))})
                continue
            coverage.append({'source':source['id'],'adapter':source['adapter_kind'],'status':'READ_VERIFIED','events':len(events),'reason':''})
            for event in events:
                event.config['_analysis_adapter']=source['adapter_kind']
                cadence=declared_cadence(event,source)
                # Never infer cadence from gaps or assume an unlabelled archive is1m.
                cadence=int(cadence) if cadence is not None else 0
                phase=event.config.get('season_phase') or 'UNSPECIFIED_BY_READER'
                event.config['season_phase']=phase
                summaries=[summarize(event,gp)for gp in event.groups]
                valid_times=[snap.time for gp in event.groups for snap in gp.snaps if snap.valid]
                if valid_times:
                    event_key=(event.sport,event.event);first=min(valid_times)
                    global_first[event_key]=min(global_first.get(event_key,first),first)
                for group,summary in zip(event.groups,summaries):
                    key=(event.source,event.cohort,event.sport,phase,cadence,summary['timestamp_basis'])
                    add_s_stats(s_stats.setdefault(key,empty_s_stats()),event,summary)
                    diagnostic5=summarize(event,group,max_skew=5) if not summary['valid'] else summary
                    state={'source':event.source,'cohort':event.cohort,'event':event.event,'sport':event.sport,'season_phase':phase,
                           'run_id':group.run,'group_time':group.time,'cadence_seconds':cadence,'summary':summary,
                           'valid_at_five_second_diagnostic':diagnostic5['valid']}
                    group_file.write(json.dumps(state,ensure_ascii=False,allow_nan=False)+'\n')
                    if summary['valid'] and len(samples)<8:
                        samples.append({**state,'books':[{'token':q.token,'condition':q.condition,'slot':q.slot,'t':q.time,
                          'valid':q.valid,'market_open':q.market_open_observed if q.market_open_observed is not None else q.open_observed,
                          'outcome_label':q.outcome_label,'legacy_result_kind':q.legacy_result_kind,'bbo_only':summary.get('quote_depth_basis')=='BBO_ONLY_NO_SIZE',
                          'book':({'asset_id':q.token,'market':q.condition,'best_ask':getattr(q,'bbo_best_ask',None),'best_bid':getattr(q,'bbo_best_bid',None)} if summary.get('quote_depth_basis')=='BBO_ONLY_NO_SIZE'else book_of(q)),'fee_rate':q.fee_rate}for q in group.snaps]})
                if event.config.get('financial_replay_eligible')is False or source.get('financial_replay_eligible')is False:
                    continue
                gap=330 if cadence==300 else 90
                tier='FIVE_MINUTE_SENSITIVITY' if cadence==300 else 'ONE_MINUTE' if cadence==60 else 'CADENCE_UNPROVEN'
                for spec in specs:
                    if spec.sport!=event.sport:continue
                    current_candidate=entry_candidate(event,spec,max_gap=gap,all_outcome_amount_depth=True)
                    new_candidate=entry_candidate(event,spec,max_gap=gap,all_outcome_amount_depth=False)
                    for variant in VARIANTS:
                        for model in FEE_MODELS:
                            result=replay_event(event,spec,variant,model,cutoff=grid.visual.timestamp(high),max_gap=gap,
                                summaries=summaries,candidate_info=current_candidate if variant=='recorded_control' else new_candidate)
                            result.update(cadence_seconds=cadence,cadence_tier=tier,
                                          primary_eligible=tier=='ONE_MINUTE' and result.get('primary_synchronization_proven',False) and model=='recorded_schedule')
                            # Large per-group S payloads are retained once in group-states.
                            for key in ('entry_S','exit_S'):
                                result[key]={k:result[key].get(k)for k in ('s_mid','s_ask','s_bid','skew_seconds','timestamp_basis')} if result[key] else None
                            rows.append(result);outcome_file.write(json.dumps(result,ensure_ascii=False,allow_nan=False)+'\n')
            del events
            dump_json(output/'coverage-progress.json',coverage)
            print(json.dumps({'phase':'source_done','source':source['id'],'outcome_rows':len(rows)}),flush=True)
    split_time=grid.visual.timestamp(split)
    for row in rows:
        first=global_first.get((row['sport'],row['event']))
        row['partition']='RETROSPECTIVE_TIME_HOLDOUT' if first is not None and first>=split_time else 'HISTORICAL_DEVELOPMENT'
    with gzip.open(output/'event-outcomes.jsonl.gz','wt',encoding='utf-8')as handle:
        for row in rows:handle.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
    s_rows=[final_s_stats(k,v)for k,v in sorted(s_stats.items())]
    write_csv(output/'s-distributions.csv',s_rows);dump_json(output/'s-distributions.json',s_rows)
    write_csv(output/'event-outcomes.csv',rows)
    summaries=summarize_outcomes(rows)
    write_csv(output/'policy-summary.csv',summaries);dump_json(output/'policy-summary.json',summaries)
    dump_json(output/'GLOBAL_EVENT_FIRST.json',[{'sport':sport,'event_id':eid,'first_valid_observation':grid.iso(at)}for(sport,eid),at in sorted(global_first.items())]);
    dump_json(output/'SOURCE_AUDITS.json',audits);dump_json(output/'COVERAGE.json',coverage)
    dump_json(output/'NORMALIZATION_SAMPLES.json',samples)
    (output/'code').mkdir(exist_ok=True)
    for name,info in EXECUTED_CODE.items():
        if name in EXECUTED_BYTES:(output/'code'/(name+'_'+info['sha256']+'.py')).write_bytes(EXECUTED_BYTES[name])
    dump_json(output/'EXECUTED_CODE.json',EXECUTED_CODE)
    changed=[name for name,info in EXECUTED_CODE.items()if grid.visual.sha256(Path(info['path']))!=info['sha256']]
    if changed:raise ValueError('analysis code changed during replay: '+','.join(changed))
    result={'status':'BOUNDED_ADAPTER_SMOKE_ONLY' if only_adapters else 'PARTIAL_SOURCE_COVERAGE' if any(x['status']=='READER_REJECTED'for x in coverage) else 'COMPLETE_SUPPORTED_VIEWS',
            'source_views':len(sources),'read_views':sum(x['status']=='READ_VERIFIED'for x in coverage),
            'rejected_views':sum(x['status']=='READER_REJECTED'for x in coverage),'outcome_rows':len(rows),
            'unique_games':len(global_first),'unique_games_by_sport':dict(Counter(sport for sport,eid in global_first)),
            's_distribution_rows':len(s_rows),'promotion_allowed':False,'retrospective_only':True}
    dump_json(output/'RESULT.json',result)
    write_report(output,result,s_rows,summaries,coverage)
    return result


def summarize_outcomes(rows):
    buckets=defaultdict(list)
    keys=('source','cohort','sport','season_phase','cadence_seconds','cadence_tier','baseline','strategy','fee_model','partition')
    for row in rows:buckets[tuple(row[k]for k in keys)].append(row)
    result=[]
    for key,values in sorted(buckets.items()):
        by_variant=defaultdict(dict)
        for row in values:
            if row['event'] in by_variant[row['variant']]:raise ValueError('duplicate event in policy cell')
            by_variant[row['variant']][row['event']]=row
        for variant in VARIANTS:
            examples=list(by_variant[variant].values())
            statuses=Counter(r['status']for r in examples);reasons=Counter(r['reason']for r in examples)
            entered=[r for r in examples if r['status'] not in ('NO_ENTRY','FILTERED','REJECTED')]
            complete=[r for r in entered if r['status']=='COMPLETE']
            bounds=[result_bounds(r)for r in examples]
            lower=sum(x[0]for x in bounds) if all(x[0] is not None for x in bounds) else None
            upper=sum(x[1]for x in bounds) if all(x[1] is not None for x in bounds) else None
            ref=PAIR_REFERENCE[variant]
            pairs=[(r,by_variant[ref][r['event']])for r in examples] if ref else []
            interval=paired_interval(pairs)
            meta=dict(zip(keys,key))
            evidence_gate=(meta['partition']=='RETROSPECTIVE_TIME_HOLDOUT' and meta['cadence_tier']=='ONE_MINUTE'
                and meta['fee_model']=='recorded_schedule' and len(examples)>=30 and len(complete)>=10
                and all(r.get('primary_eligible')for r in entered) and lower is not None)
            actual_lowers=[result_bounds(r)[0]for r in examples]
            control_lowers=[result_bounds(by_variant['recorded_control'][r['event']])[0]for r in examples]
            worst=min(actual_lowers)if actual_lowers and None not in actual_lowers else None
            control_worst=min(control_lowers)if control_lowers and None not in control_lowers else None
            tail_ok=worst is not None and control_worst is not None and worst>=control_worst-EPS
            supported=evidence_gate and interval['lower'] is not None and interval['lower']>0 and tail_ok
            unknown_cost=sum(r.get('usd_cost') is None for r in entered)
            known_cost=sum(r['usd_cost']for r in entered if r.get('usd_cost') is not None)
            result.append({**meta,'variant':variant,'independent_events':len(examples),'entries':len(entered),'complete_exits':len(complete),
                'status_counts':dict(statuses),'exit_reasons':dict(reasons),'complete_net_sum':sum(r['net']for r in complete)if complete else None,
                'conservative_total_lower':lower,'conservative_total_upper':upper,
                'paid_cost_entered':known_cost if unknown_cost==0 else None,'known_total_cost_sum':known_cost,
                'gross_notional_entered':5*len(entered),'unknown_cost_entries':unknown_cost,'comparison':ref,
                'worst_loss_lower_bound':worst,'control_worst_loss_lower_bound':control_worst,'tail_not_worse_than_current_control':tail_ok,
                'paired_interval':interval,'evidence_gate_passed':bool(evidence_gate),'positive_paired_support':bool(supported),
                'promotion_allowed':False,'verdict':'EXPLORATORY_POSITIVE_REQUIRES_PROSPECTIVE_CONFIRMATION' if supported else
                    'INSUFFICIENT_OR_NONPOSITIVE_EVIDENCE_NO_PARAMETER_PROMOTION'})
    return result


def write_report(output,result,s_rows,policy_rows,coverage):
    lines=['# S-aware sports replay and calibration','',
        f"Supported views read: {result['read_views']} / {result['source_views']}; rejected views: {result['rejected_views']}.",
        f"Unique underlying games: {result['unique_games']} ({json.dumps(result['unique_games_by_sport'],ensure_ascii=False)}).",
        f"Fixed policy/event/fee outcomes: {result['outcome_rows']:,}. These are paired scenarios, not independent trades.",'',
        'All P&L below is a displayed-book counterfactual. No orders, wallets, confirmed fills or live parameter changes.',
        'S uses soccer YES3 or one-condition DIRECT2, not six mutually overlapping tokens. Normalized quote weights are not proven probabilities.',
        'Legacy snapshot timestamps are a separate proxy: a2s database timestamp alignment does not prove actual same-time HTTP receipts.',
        'Five-minute sources are FIVE_MINUTE_SENSITIVITY only; they do not enter one-minute promotion evidence.','',
        '## Quote diagnostics','',
        '| Sport | Timing basis | Cadence | Valid groups | Independent games within source cells | S-mid>=1.02 | Full-depth raw S-bid5>=1.01 (open gate separate) | Bid<=1<=ask |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    aggregated=defaultdict(lambda:Counter())
    for row in s_rows:
        key=(row['sport'],row['timestamp_basis'],row['cadence_seconds'])
        for field in ('valid','independent_events','s_mid_ge_1_02','s_bid_ge_1_01','quote_interval_contains_1'):aggregated[key][field]+=row[field]
    for (sport,basis,cadence),counts in sorted(aggregated.items()):
        lines.append(f"| {sport} | {basis} | {cadence} | {counts['valid']} | {counts['independent_events']}* | {counts['s_mid_ge_1_02']} | {counts['s_bid_ge_1_01']} | {counts['quote_interval_contains_1']} |")
    lines +=['','*This column sums source-cell game counts and is deliberately not a pooled independent sample. Global unique games are listed above.',
        'S-best-ask−1 decomposes exactly into (S-mid−1)+half of the total top spread. Depth S-ask also includes order-book depth effects.',
        'S-ask>1 is not a sellable profit: all declared exits additionally require the actual held-token bid quantity and fee-adjusted positive P&L.','',
        '## Strategy verdicts','',
        '| Strategy | Sport | Holdout source-cells | Cells with30 events +10 complete trades +known fees/timing | Positive paired support | Decision |',
        '|---|---|---:|---:|---:|---|']
    for strategy in ('watermelon','peach','plum'):
        for sport in ('soccer','mlb','nfl','nba','nhl'):
            candidates=[r for r in policy_rows if r['strategy']==strategy and r['sport']==sport and r['partition']=='RETROSPECTIVE_TIME_HOLDOUT' and r['fee_model']=='recorded_schedule']
            passed=sum(r['evidence_gate_passed']for r in candidates);positive=sum(r['positive_paired_support']for r in candidates)
            lines.append(f"| {strategy} | {sport} | {len(candidates)} | {passed} | {positive} | No automatic change; prospective confirmation required |")
    lines+=['','Detailed baseline arms and all seven variants are in policy-summary.csv; event outcomes and censor reasons are in event-outcomes.csv.',
        'No source or best-looking threshold is selected after seeing outcomes. The explicit user95/97→99 profiles remain separate from deployed parameter references.',
        'Current-control versus new net-only may change completeness/exit rules; only comparisons sharing the same eligibility isolate an added S gate.',
        'Stops require in-play evidence; new profitable TP opportunities may use a still-open market after play. This is an overall candidate-policy difference, not proof that S alone caused improvement.','',
        '## Unresolved source coverage','']
    audit_path=Path(output)/'SOURCE_AUDITS.json'
    audit_by_source={a.get('source'):a for a in json.loads(audit_path.read_text())} if audit_path.exists() else {}
    for row in coverage:
        if row['status']=='READ_VERIFIED':continue
        audit=audit_by_source.get(row['source'],{})
        detail=''
        if row['status']=='UNSUPPORTED_SCHEMA':
            detail=f"; original canonical books={audit.get('canonical_books','unknown')}; missing schema={json.dumps(audit.get('missing_columns',{}),sort_keys=True)}; epoch={audit.get('source_schema_metadata',{}).get('data_contract','unknown')}"
        lines.append(f"- {row['source']} ({row['adapter']}, {row['status']}): {row['reason']}{detail}")
    lines.append('The standalone White sidecar is already consumed with its parent in the paired view; excluding it prevents duplication, not data loss.')
    lines+=['','Primary references: [Prices and order book](https://docs.polymarket.com/concepts/prices-orderbook), '
        '[market/token identity](https://docs.polymarket.com/concepts/markets-events), [fees](https://docs.polymarket.com/trading/fees).',
        'These explain the arithmetic and market mechanics; they do not validate profitability of this study.']
    (Path(output)/'REPORT.md').write_text('\n'.join(lines)+'\n')


def declared_cadence(event,source):
    found=set()
    def visit(value):
        if not isinstance(value,dict):return
        for key,item in value.items():
            if key in ('cadence_seconds','slot_seconds') and isinstance(item,(int,float)) and not isinstance(item,bool):found.add(int(item))
            if key=='cadence_minutes' and isinstance(item,(int,float))and not isinstance(item,bool):found.add(int(item*60))
            if key=='cadence_arm' and item in ('FAST_1M','SLOW_5M','CONTROL_5M'):found.add(60 if item=='FAST_1M' else 300)
            if isinstance(item,dict):visit(item)
    visit(event.config)
    if source.get('cadence_seconds') is not None:found.add(int(source['cadence_seconds']))
    return next(iter(found)) if len(found)==1 else 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    freeze=sub.add_parser('freeze-baselines');freeze.add_argument('--configs',required=True);freeze.add_argument('--output',required=True)
    run=sub.add_parser('run');run.add_argument('--sources',required=True);run.add_argument('--baselines',required=True,action='append');run.add_argument('--output',required=True)
    run.add_argument('--start',default=START);run.add_argument('--end',default=END);run.add_argument('--split',default=SPLIT)
    run.add_argument('--only-adapter',action='append')
    args=parser.parse_args(argv)
    if args.command=='freeze-baselines':result=freeze_baselines(args.configs,args.output)
    else:result=run_study(args.sources,args.baselines,args.output,start=args.start,end=args.end,split=args.split,only_adapters=args.only_adapter)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
