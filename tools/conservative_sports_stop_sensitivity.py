#!/usr/bin/env python3
"""Focused, paired stop sensitivity on immutable conservative-sports grid paths.

Reads frozen archive code/data only; no network, orders, credentials, or source
writes. Entry mapping, TP contract, fee convention, cutoff and execution envelope
stay fixed. This post-hoc analysis does not choose a live parameter or create OOS.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import csv
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import sys
from types import SimpleNamespace

DELTAS=(.01,.02,.03,.05,.08,.10,.12,.15,.20,.30)
_ENGINE_CACHE={}


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def write_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))


def load_frozen_engine(archive):
    """Load exact frozen dependencies even when mutable tools are already imported."""
    archive=Path(archive).resolve()
    protocol=json.loads((archive/'PROTOCOL.json').read_text())
    if protocol.get('fee_collection')!='v2_cash':raise ValueError('archive must explicitly use current V2 cash fees')
    declared=json.loads((archive/'OUTPUT_SHA256.json').read_text())
    required=('PROTOCOL.json','DEPENDENCIES.json','results.json','entries.csv.gz','paths.jsonl.gz',
              'conservative_sports_grid.py','sports_visual_data.py','catdog_takeprofit_replay.py')
    checked={}
    for name in required:
        actual=sha256(archive/name)
        if actual!=declared.get(name):raise ValueError(f'archive checksum mismatch: {name}')
        checked[name]=actual
    if checked['conservative_sports_grid.py']!=protocol.get('code_sha256'):
        raise ValueError('frozen engine does not match protocol')
    deps=json.loads((archive/'DEPENDENCIES.json').read_text())
    dep_map={d['filename']:d['sha256'] for d in deps['dependencies']}
    for name in required[-3:]:
        if dep_map.get(name)!=checked[name]:raise ValueError('dependency manifest mismatch')
    cache_key=tuple(checked[n] for n in required[-3:])
    if cache_key not in _ENGINE_CACHE:
        saved={name:sys.modules.get(name) for name in ('sports_visual_data','catdog_takeprofit_replay')}
        def load(name,path):
            spec=importlib.util.spec_from_file_location(name,path)
            module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
            return module
        saved_bytecode=sys.dont_write_bytecode;sys.dont_write_bytecode=True
        try:
            load('sports_visual_data',archive/'sports_visual_data.py')
            load('catdog_takeprofit_replay',archive/'catdog_takeprofit_replay.py')
            key=hashlib.sha256(repr(cache_key).encode()).hexdigest()[:16]
            _ENGINE_CACHE[cache_key]=load('frozen_stop_grid_'+key,archive/'conservative_sports_grid.py')
        finally:
            sys.dont_write_bytecode=saved_bytecode
            for name,value in saved.items():
                if value is None:sys.modules.pop(name,None)
                else:sys.modules[name]=value
    return _ENGINE_CACHE[cache_key],protocol,checked


@dataclass(frozen=True)
class Focus:
    case_id: str
    policy: str
    rank: int
    entry: float
    target_mode: str
    target: float
    tp_policy: str='positive_full'


def focus_cases(engine,cohort):
    sport=cohort['sport']
    if sport not in ('soccer','mlb'):return []
    policies=engine.policy_names(SimpleNamespace(source=cohort['source'],sport=sport))
    cases=[]
    if 'watermelon_rank' in policies:
        for entry,target in ((.95,.99),(.97,.99),(.96,.97)):
            cases.append(Focus(f'wm-{round(entry*100):02d}-to-{round(target*100):02d}','watermelon_rank',1,entry,'absolute',target))
    if 'plum_price_rank' in policies:
        cases.append(Focus('plum-price-70-to-72','plum_price_rank',1,.70,'absolute',.72))
    if 'plum_trend_rank' in policies:
        cases.append(Focus('plum-trend-74-plus-01','plum_trend_rank',1,.74,'relative',.01))
        entry,targets=(.75,(.90,.95)) if sport=='soccer' else (.55,(.65,.70))
        for arm,target in zip(('A','B'),targets):
            cases.append(Focus('plum-published-'+arm,'plum_trend_rank',1,entry,'absolute',target,'plum_published'))
    for policy in policies:
        if not policy.startswith('peach'):continue
        for rank in range(1,7 if sport=='soccer' else 3):
            for target in (.02,.03,.05):
                cases.append(Focus(f'{policy}-rank{rank}-60-plus-{round(target*100):02d}',policy,rank,.60,'relative',target))
    return cases


def retarget_stop(path,delta):
    """Only replace the stop threshold and its per-observation trigger."""
    if not math.isfinite(delta) or delta<=0:raise ValueError('positive finite delta required')
    if path.get('end_reason')=='SL':raise ValueError('source path was truncated at its old stop')
    entry=path['entry_vwap'];knobs={**path['knobs'],'stop_delta':delta}
    price=max(knobs['stop_floor'],entry-delta)
    output={**path,'knobs':knobs,'stop_price':price,'observations':[]}
    previous=path['entry_time']
    for obs in path['observations']:
        if obs['time']<=previous:raise ValueError('same-bar/non-increasing future observation')
        if 'bids' not in obs:raise ValueError('full future bid levels missing')
        previous=obs['time']
        cutoff=knobs['stop_cutoff'];minute=obs.get('minute');bid=obs.get('best_bid')
        active=cutoff is None or (minute is not None and minute<cutoff-1e-9)
        changed={**obs,'stop_trigger':bool(active and bid is not None and bid<=price+1e-9)}
        # The frozen fee-specific executor recomputes executable stop from the
        # new trigger; an old gross-quantity diagnostic must not drive it.
        changed.pop('stop',None)
        output['observations'].append(changed)
    return output


def entered(result):return result['status'] not in ('NO_ENTRY','REJECTED')


def bounds(result):
    if result['status']=='COMPLETE':return result['net'],result['net']
    if result['status']=='CENSORED':return result.get('lower'),result.get('upper')
    return None,None


def paired_result(alternative,baseline):
    if entered(alternative)!=entered(baseline):
        raise ValueError('stop-only intervention changed entry eligibility')
    both=alternative['status']=='COMPLETE' and baseline['status']=='COMPLETE'
    a,b=bounds(alternative),bounds(baseline)
    known=all(x is not None for x in (*a,*b))
    return {'paired_complete':both,
            'paired_net_delta':alternative['net']-baseline['net'] if both else None,
            'paired_delta_lower':a[0]-b[1] if known else None,
            'paired_delta_upper':a[1]-b[0] if known else None,
            'newly_complete':alternative['status']=='COMPLETE' and baseline['status']=='CENSORED',
            'newly_censored':alternative['status']=='CENSORED' and baseline['status']=='COMPLETE'}


@dataclass
class Stats:
    events: int=0
    entered: int=0
    no_entry: int=0
    rejected: int=0
    complete: int=0
    censored: int=0
    positive: int=0
    negative: int=0
    zero: int=0
    known_net: float=0.
    lower: float=0.
    upper: float=0.
    unknown_bounds: int=0
    worst: float | None=None
    baseline_complete: int=0
    paired_complete: int=0
    paired_alt: float=0.
    paired_base: float=0.
    delta_lower: float=0.
    delta_upper: float=0.
    unknown_delta_bounds: int=0
    newly_complete: int=0
    newly_censored: int=0
    positive_to_nonpositive: int=0
    nonpositive_to_positive: int=0
    baseline_tp_to_sl: int=0
    reasons: Counter=field(default_factory=Counter)

    def add(self,a,b):
        self.events+=1;self.reasons[a['reason']]+=1
        if a['status']=='NO_ENTRY':self.no_entry+=1;return
        if a['status']=='REJECTED':self.rejected+=1;return
        if not entered(a):raise ValueError('unsupported result status')
        self.entered+=1
        if a['status']=='COMPLETE':
            self.complete+=1;net=a['net'];self.known_net+=net
            self.positive+=net>0;self.negative+=net<0;self.zero+=net==0
            self.worst=net if self.worst is None else min(self.worst,net)
        else:self.censored+=1
        lo,hi=bounds(a)
        if lo is None or hi is None:self.unknown_bounds+=1
        else:self.lower+=lo;self.upper+=hi
        p=paired_result(a,b)
        self.baseline_complete+=b['status']=='COMPLETE'
        self.newly_complete+=p['newly_complete'];self.newly_censored+=p['newly_censored']
        if p['paired_complete']:
            self.paired_complete+=1;self.paired_alt+=a['net'];self.paired_base+=b['net']
            self.positive_to_nonpositive+=b['net']>0 and a['net']<=0
            self.nonpositive_to_positive+=b['net']<=0 and a['net']>0
            self.baseline_tp_to_sl+=b['reason'] in ('TP','LATE_TP') and a['reason']=='SL'
        if p['paired_delta_lower'] is None:self.unknown_delta_bounds+=1
        else:self.delta_lower+=p['paired_delta_lower'];self.delta_upper+=p['paired_delta_upper']

    def row(self,engine):
        return {'events':self.events,'entered':self.entered,'no_entry':self.no_entry,'rejected':self.rejected,
                'complete':self.complete,'censored':self.censored,'positive':self.positive,'negative':self.negative,'zero':self.zero,
                'completion_rate':self.complete/self.entered if self.entered else None,
                'positive_rate_complete':self.positive/self.complete if self.complete else None,
                'positive_rate_all_entries':self.positive/self.entered if self.entered else None,
                'wilson95_lower_censored_as_failure':engine.wilson_lower(self.positive,self.entered),
                'complete_net':self.known_net if self.complete else None,'worst_complete':self.worst,
                'all_entry_net_lower':self.lower if self.entered and not self.unknown_bounds else None,
                'all_entry_net_upper':self.upper if self.entered and not self.unknown_bounds else None,
                'unknown_bound_entries':self.unknown_bounds,'baseline_complete':self.baseline_complete,
                'paired_complete':self.paired_complete,'paired_alternative_net':self.paired_alt if self.paired_complete else None,
                'paired_baseline_net':self.paired_base if self.paired_complete else None,
                'paired_net_change':self.paired_alt-self.paired_base if self.paired_complete else None,
                'all_entry_change_lower':self.delta_lower if self.entered and not self.unknown_delta_bounds else None,
                'all_entry_change_upper':self.delta_upper if self.entered and not self.unknown_delta_bounds else None,
                'newly_complete_not_proven_saved_loss':self.newly_complete,'newly_censored':self.newly_censored,
                'positive_to_nonpositive':self.positive_to_nonpositive,'nonpositive_to_positive':self.nonpositive_to_positive,
                'baseline_tp_to_sl':self.baseline_tp_to_sl,'reasons':json.dumps(dict(self.reasons),sort_keys=True,separators=(',',':'))}


EVENT_FIELDS=['archive','source','cohort','sport','case_id','policy','rank','entry_threshold','entry_width','target_mode','target_value','tp_policy','fee_model','fee_collection','event','title','partition','day','path_id','entry_utc','entry_vwap','token','result','side','baseline_delta','stop_delta','effective_stop','is_baseline','status','reason','exit_utc','exit_vwap','net','lower','upper','sell_shares','usd_cost','tp_fee_nonpositive_observations','blocked_full_depth_observations','baseline_status','baseline_reason','baseline_exit_utc','baseline_exit_vwap','baseline_net','paired_complete','paired_net_delta','paired_delta_lower','paired_delta_upper','newly_complete','newly_censored']


def run(archives,output):
    output=Path(output).resolve()
    if output.exists() and any(output.iterdir()):raise ValueError('new/empty output directory required')
    output.mkdir(parents=True,exist_ok=True)
    bundles=[];meta={};case_map={};source_audits=[]
    for raw in archives:
        path=Path(raw).resolve();engine,protocol,checked=load_frozen_engine(path)
        if protocol.get('entry_width',.03)!=.03:raise ValueError('focus requires original .03 grid entry width')
        result=json.loads((path/'results.json').read_text())
        bundle={'path':path,'engine':engine,'protocol':protocol,'checked':checked,'result':result};bundles.append(bundle)
        source_audits.extend(result['audits'])
        for c in result['cohorts']:
            if c['cohort'] in meta:raise ValueError('duplicate cohort across archives')
            meta[c['cohort']]={**c,'archive':path.name}
            for focus in focus_cases(engine,c):case_map[(c['cohort'],focus.case_id)]=focus
    if len({b['checked']['conservative_sports_grid.py'] for b in bundles})!=1:raise ValueError('archives use different replay engines')
    starts={b['protocol']['start'] for b in bundles};ends={b['protocol']['end_exclusive'] for b in bundles}
    if len(starts)!=1 or len(ends)!=1:raise ValueError('frozen evidence ranges differ')
    protocol={'schema':'focused-stop-sensitivity-v1','study_type':'post_hoc_fixed_entry_stop_sensitivity',
              'start':next(iter(starts)),'end_exclusive':next(iter(ends)),'entry_width':.03,'stop_deltas':DELTAS,
              'fee_collection':'v2_cash','same_entry_required':True,'oos':False,'live_promotion_allowed':False,
              'python':platform.python_version(),'tool_sha256':sha256(__file__),
              'input_archives':[{'path':str(b['path']),'checked_sha256':b['checked'],'source_pins':b['result']['audits']} for b in bundles],
              'case_count_by_cohort':dict(Counter(cohort for cohort,case in case_map)),
              'limitations':['WM .03 grid bands differ from former .989-capped proposals.',
                             'Peach .60..63 differs from published .60..94 entry band.',
                             'Earlier completion before a data gap is not proof of an avoided loss.',
                             'Five-decimal per-price-level fee model is not authenticated fills or maker fragmentation.']}
    write_json(output/'PROTOCOL.json',protocol)
    (output/'ANALYZER.py').write_text(Path(__file__).read_text())
    summaries=defaultdict(Stats);summary_meta={};event_rows=0;baseline_checks=0;path_count=0;raw_paths=0
    source_events=defaultdict(set);case_entry_keys=set();noentry=[];event_periods={};requested_paths=set();seen_paths=set()
    with gzip.open(output/'event_outcomes.csv.gz','wt',encoding='utf-8',newline='',compresslevel=3) as f:
        writer=csv.DictWriter(f,fieldnames=EVENT_FIELDS);writer.writeheader()
        def add(cohort,focus,event,path,delta,fee,a,b):
            nonlocal event_rows
            c=meta[cohort];baseline=path['knobs']['stop_delta'] if path else engine.policy_knobs(focus.policy,c['sport'])['stop_delta']
            period,day,title=event_periods.get((cohort,event),('UNASSIGNED_NO_EXECUTION_PATH',None,None))
            common={'archive':c['archive'],'source':c['source'],'cohort':cohort,'sport':c['sport'],'case_id':focus.case_id,
                    'policy':focus.policy,'rank':focus.rank,'entry_threshold':focus.entry,'entry_width':.03,
                    'target_mode':focus.target_mode,'target_value':focus.target,'tp_policy':focus.tp_policy,
                    'fee_model':fee,'fee_collection':'v2_cash','baseline_delta':baseline,'stop_delta':delta,
                    'is_baseline':math.isclose(delta,baseline,abs_tol=1e-12)}
            paired=paired_result(a,b)
            row={**common,'event':event,'title':title,'partition':period,'day':day,'path_id':path['id'] if path else None,
                 'entry_utc':path['entry_utc'] if path else None,'entry_vwap':path['entry_vwap'] if path else None,
                 'token':path['entry_token'] if path else None,'result':path['entry_result'] if path else None,
                 'side':path['entry_side'] if path else None,
                 'effective_stop':max(path['knobs']['stop_floor'],path['entry_vwap']-delta) if path else None,
                 **{k:a.get(k) for k in ('status','reason','exit_utc','exit_vwap','net','lower','upper','sell_shares','usd_cost','tp_fee_nonpositive_observations','blocked_full_depth_observations')},
                 'baseline_status':b['status'],'baseline_reason':b['reason'],'baseline_exit_utc':b.get('exit_utc'),
                 'baseline_exit_vwap':b.get('exit_vwap'),'baseline_net':b.get('net'),**paired}
            writer.writerow(row);event_rows+=1
            periods=['all',period]+(['day:'+day] if day else [])
            for p in periods:
                key=(cohort,focus.case_id,fee,delta,p)
                summaries[key].add(a,b);summary_meta[key]={**common,'period':p}
        for bundle in bundles:
            engine=bundle['engine'];archive=bundle['path'];requests=defaultdict(list);case_counts=Counter();cohort_events=defaultdict(set)
            focus_keys=defaultdict(list)
            for (cohort,_),focus in case_map.items():
                if meta[cohort]['archive']==archive.name:focus_keys[(cohort,focus.policy,focus.rank,focus.entry)].append(focus)
            with gzip.open(archive/'entries.csv.gz','rt',encoding='utf-8',newline='') as source:
                for e in csv.DictReader(source):
                    cohort=e['cohort'];event=e['event'];c=meta[cohort]
                    source_events[c['source']].add((c['sport'],event));cohort_events[cohort].add(event)
                    for focus in focus_keys.get((cohort,e['policy'],int(e['rank']),float(e['entry_threshold'])),[]):
                        unique=(cohort,event,focus.case_id)
                        if unique in case_entry_keys:raise ValueError('duplicate fixed candidate mapping')
                        case_entry_keys.add(unique);case_counts[(cohort,focus.case_id)]+=1
                        if e['path_id']:requests[e['path_id']].append((cohort,event,focus));requested_paths.add(e['path_id'])
                        else:noentry.append((cohort,event,focus))
            for cohort,c in meta.items():
                if c['archive']==archive.name and len(cohort_events[cohort])!=c['events']:raise ValueError('frozen event denominator mismatch')
            for (cohort,case),focus in case_map.items():
                if meta[cohort]['archive']==archive.name and case_counts[(cohort,case)]!=meta[cohort]['events']:raise ValueError('focused case mapping is incomplete')
            with gzip.open(archive/'paths.jsonl.gz','rt',encoding='utf-8') as source:
                for line in source:
                    path=json.loads(line);raw_paths+=1
                    key=(path['cohort'],path['event'])
                    period=(path['partition'],path['day'],path['title'])
                    if key in event_periods and event_periods[key]!=period:raise ValueError('event partition/title drift')
                    event_periods[key]=period
                    if path['id'] not in requests:continue
                    if path['id'] in seen_paths:raise ValueError('duplicate requested path')
                    seen_paths.add(path['id']);path_count+=1
                    if not engine.visual.timestamp(bundle['protocol']['start'])<=path['entry_time']<engine.visual.timestamp(bundle['protocol']['end_exclusive']):raise ValueError('path entry outside frozen range')
                    if any(o['time']>=engine.visual.timestamp(bundle['protocol']['end_exclusive']) for o in path['observations']):raise ValueError('path observation outside frozen range')
                    variants={delta:retarget_stop(path,delta) for delta in DELTAS}
                    c=meta[path['cohort']]
                    fees=tuple(engine.FEE_MODELS)+(('recorded_schedule',) if c['primary_fee_model']=='recorded_schedule' else ())
                    for cohort,event,focus in requests[path['id']]:
                        if path['cohort']!=cohort or path['event']!=event or path['policy']!=focus.policy:raise ValueError('entry/path identity mismatch')
                        for fee in fees:
                            base=engine.replay_path(path,focus.target_mode,focus.target,fee,tp_policy=focus.tp_policy,fee_collection='v2_cash')
                            for delta,variant in variants.items():
                                result=engine.replay_path(variant,focus.target_mode,focus.target,fee,tp_policy=focus.tp_policy,fee_collection='v2_cash')
                                if math.isclose(delta,path['knobs']['stop_delta'],abs_tol=1e-12):
                                    if result!=base:raise ValueError('baseline delta does not reproduce frozen result')
                                    baseline_checks+=1
                                add(cohort,focus,event,path,delta,fee,result,base)
            print(json.dumps({'archive':archive.name,'requested_paths_processed':path_count,'event_rows_written':event_rows}),flush=True)
        if requested_paths!=seen_paths:raise ValueError('requested frozen paths are missing')
        no={'status':'NO_ENTRY','reason':'no_qualifying_frozen_entry','net':None}
        for cohort,event,focus in noentry:
            c=meta[cohort];fees=tuple(engine.FEE_MODELS)+(('recorded_schedule',) if c['primary_fee_model']=='recorded_schedule' else ())
            for fee in fees:
                for delta in DELTAS:add(cohort,focus,event,None,delta,fee,no,no)
    rows=[]
    for key,stats in sorted(summaries.items()):rows.append({**summary_meta[key],**stats.row(engine)})
    with gzip.open(output/'summaries.csv.gz','wt',encoding='utf-8',newline='',compresslevel=3) as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    primary=[r for r in rows if r['period']=='all' and r['fee_model']==meta[r['cohort']]['primary_fee_model']]
    with (output/'primary_paired_summary.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(primary[0]));writer.writeheader();writer.writerows(primary)
    games=set().union(*source_events.values())
    audit={'unique_source_pins':len({s['path'] for s in source_audits}),'unique_games':len(games),
           'games_by_sport':dict(Counter(s for s,e in games)),'cohorts':len(meta),'fixed_case_cohort_units':len(case_map),
           'fixed_candidate_event_units':len(case_entry_keys),'raw_paths_read':raw_paths,'requested_paths_used':path_count,
           'event_outcome_rows':event_rows,'summary_rows':len(rows),'primary_summary_rows':len(primary),
           'baseline_exact_reproduction_checks':baseline_checks,'baseline_mismatches':0,'source_events':{s:len(es) for s,es in source_events.items()},
           'source_audits':source_audits,'no_orders':True,'post_hoc_only':True}
    write_json(output/'AUDIT.json',audit)
    write_report(primary,meta,audit,output)
    write_json(output/'OUTPUT_SHA256.json',{p.relative_to(output).as_posix():sha256(p) for p in output.rglob('*') if p.is_file() and p.name!='OUTPUT_SHA256.json'})
    return audit


def write_report(rows,meta,audit,output):
    lines=['# 고정 진입 후보의 stop sensitivity','',
           'UTC [2026-08-30T00:00:00Z, 2026-09-07T11:30:00Z). 기존 최종 결과를 변경하지 않은 사후 focused sensitivity다. 진입과 가격 경로를 고정하고 stop delta만 바꿨다. 실체결·최적 손절·확정 수익·live 승격을 주장하지 않는다.','',
           f"{audit['unique_source_pins']}개 source pin, {audit['unique_games']}개 고유 경기(축구 {audit['games_by_sport'].get('soccer',0)}, MLB {audit['games_by_sport'].get('mlb',0)}), {audit['fixed_case_cohort_units']}개 고정 case/cohort. event별 결과 {audit['event_outcome_rows']:,}행이며 대안들을 독립 거래로 세지 않는다. 현재 delta의 원결과 재현 {audit['baseline_exact_reproduction_checks']:,}회, 불일치 0.",'',
           'WM .95/.97의 width .03 band는 이전 .989 상한 proposal과 다르다. Peach .60..63도 기존 전체 .60..94 band가 아니다. 이전 표의 −.78267과 이 결과를 동일 baseline으로 비교하지 않는다. published Plum은 entry/TP 숫자 대조이며 부분 TP lifecycle은 검열한다.','',
           '원래 stop floor, Peach 80분 cutoff/late half-target, full depth·spread·OPEN gate, V2 cash fee·SDK dust, FAILED/90초 gap 처리를 그대로 유지했다. Stop은 보장 체결가가 아니다. 더 이른 stop으로 후속 gap 전에 종결한 행을 회피한 손실로 단정하지 않는다.','',
           'primary_paired_summary.csv는 모든 case/cohort/delta, summaries.csv.gz는 fee·early/late/일별 결과, event_outcomes.csv.gz는 경기별 실제 진입가격·출구·손익·baseline 대비 변화와 검열을 보존한다. paired 손익 차이는 두 정책 모두 완결한 경기만 계산하며 전체 진입의 변화는 별도 하한/상한이다.','',
           '|source|sport|cohort|관측 경기|case 수|상세|','|---|---|---|---:|---:|---|']
    (output/'cohorts').mkdir(exist_ok=True)
    for cohort,c in sorted(meta.items(),key=lambda x:(x[1]['source'],x[1]['sport'],x[0])):
        selected=[r for r in rows if r['cohort']==cohort]
        if not selected:continue
        cases=sorted({r['case_id'] for r in selected})
        lines.append(f"|{c['source']}|{c['sport']}|{cohort}|{c['events']}|{len(cases)}|[paired表](cohorts/{cohort}.md)|")
        detail=[f"# {c['source']} · {c['sport']} · {cohort}",'',
                '독립 cohort이며 다른 source/cohort와 합산하지 않는다. 정렬은 case와 stop delta 순서이지 수익 순위가 아니다. 숫자는 USDC 표시호가 재생이다.','',
                '|case|Δstop|기존|진입/완결/양수/검열|양수율(완결)|최악 완결|확인부분net|전체net하한|paired 완결|paired net변화|새 완결/새 검열|','|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|']
        def f(v):return '미확정' if v is None else f'{v:.6f}'
        for r in sorted(selected,key=lambda r:(r['case_id'],r['stop_delta'])):
            detail.append(f"|{r['case_id']}|{r['stop_delta']:.2f}|{'✓' if r['is_baseline'] else ''}|{r['entered']}/{r['complete']}/{r['positive']}/{r['censored']}|{f(r['positive_rate_complete'])}|{f(r['worst_complete'])}|{f(r['complete_net'])}|{f(r['all_entry_net_lower'])}|{r['paired_complete']}|{f(r['paired_net_change'])}|{r['newly_complete_not_proven_saved_loss']}/{r['newly_censored']}|")
        (output/'cohorts'/f'{cohort}.md').write_text('\n'.join(detail)+'\n')
    lines+=['','양수 확률·최악 손실·검열을 우선해 비교한다. 총손익이 가장 큰 stop을 최적으로 정하거나, 이미 본 자료를 재분할해 새로운 OOS라고 부르지 않는다.','']
    (output/'REPORT.md').write_text('\n'.join(lines))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();audit=run(args.archive,args.output)
    print(json.dumps({k:v for k,v in audit.items() if k!='source_audits'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
