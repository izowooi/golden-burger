#!/usr/bin/env python3
"""Original early White/Grey HTTP books -> segregated research Event objects.

These epochs scanned all sports. The immutable Coconut-v6 five-family identity
registry is applied retrospectively to original Gamma bodies; no soccer default,
mutable catalog join, synthetic NO, source clock or fee inference is allowed.
"""
from __future__ import annotations
from collections import Counter, defaultdict, OrderedDict
from contextlib import closing
import ast
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys
from types import ModuleType
from urllib.parse import urlparse

import conservative_sports_grid as grid
import sports_visual_data as visual
from coconut_historical_grid import stamp, sha

ORIGIN='VERIFIED_WHITE_LEGACY_HTTP'
REPO=Path(__file__).resolve().parents[1]
COCO=REPO/'golden-coconut'
DEPENDENCY_FILES=tuple(COCO/f for f in (
 'src/polybot/source_digest.py','src/polybot/api/transport.py','src/polybot/lifecycle.py',
 'src/polybot/registry.py','src/polybot/classifier.py',
 'research/frozen-2026-08-28-v6/SPORTS_REGISTRY.json'))
CLASSIFIER_EXECUTED_SHA={}


def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def parsed(value): return json.loads(value) if isinstance(value,str) else value


def classifier():
    name='_white_legacy_coconut_identity'
    if name+'.classifier' not in sys.modules:
        pkg=ModuleType(name);pkg.__path__=[str(COCO/'src/polybot')];sys.modules[name]=pkg
        api=ModuleType(name+'.api');api.__path__=[str(COCO/'src/polybot/api')];sys.modules[name+'.api']=api
        for suffix,relative in [('source_digest','source_digest.py'),('api.transport','api/transport.py'),
                                ('lifecycle','lifecycle.py'),('registry','registry.py'),('classifier','classifier.py')]:
            path=COCO/'src/polybot'/relative
            mod=ModuleType(name+'.'+suffix);mod.__file__=str(path);mod.__package__=name+'.api' if suffix.startswith('api.') else name
            sys.modules[mod.__name__]=mod
            body=path.read_bytes()
            if suffix=='api.transport':
                # Lifecycle needs only these two pure format helpers. Do not load
                # requests, networking code, config, credentials or environment.
                tree=ast.parse(body,str(path));names={'canonical_json','iso_utc'}
                nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in names]
                if {n.name for n in nodes}!=names:raise ValueError('pure_transport_helper_contract_changed')
                future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
                tree=ast.fix_missing_locations(ast.Module(body=[future,*nodes],type_ignores=[]))
                mod.__dict__.update(json=json,datetime=datetime,timezone=timezone)
                exec(compile(tree,str(path),'exec'),mod.__dict__)
            else:exec(compile(body,str(path),'exec'),mod.__dict__)
        CLASSIFIER_EXECUTED_SHA.update({str(p):sha(p)for p in DEPENDENCY_FILES})
    if any(sha(Path(p))!=h for p,h in CLASSIFIER_EXECUTED_SHA.items()):raise ValueError('classifier_changed_since_module_load')
    registry_module=sys.modules[name+'.registry'];path=DEPENDENCY_FILES[-1]
    return sys.modules[name+'.classifier'],registry_module.load_registry(sha(path),path)


class Evidence:
    def __init__(self,c,runs):
        self.c,self.runs=c,runs;self.cache=OrderedDict();self.event_cache=OrderedDict()
        self.requests={r['request_id']:dict(r) for r in c.execute('SELECT * FROM api_requests')}
        self.payloads=defaultdict(list)
        for r in c.execute('SELECT payload_id,run_id,payload_kind,request_id,observed_at,sha256 FROM raw_payloads'):
            self.payloads[r['request_id']].append(dict(r))
        self.gamma=defaultdict(list)
        for r in self.payloads.values():
            for p in r:
                if p['payload_kind']=='GAMMA_EVENT_PAGE':self.gamma[(p['run_id'],p['observed_at'])].append(p['request_id'])
        self.cl,self.registry=classifier()

    def payload(self,run_id,request_id,kind,host):
        key=(run_id,request_id,kind)
        if key in self.cache:self.cache.move_to_end(key);return self.cache[key]
        req=self.requests.get(request_id);rows=self.payloads.get(request_id,[])
        if not req or len(rows)!=1:raise ValueError('missing_or_duplicate_raw_request_payload')
        pr=rows[0];u=urlparse(req['url'])
        if (req['run_id']!=run_id or pr['run_id']!=run_id or pr['payload_kind']!=kind
            or req['status']!='SUCCESS' or req['http_status']!=200 or u.scheme!='https'
            or u.hostname!=host or u.username or u.password or req['response_sha256']!=pr['sha256']):
            raise ValueError('raw_public_HTTP_identity_failed')
        begin,received=stamp(req['started_at']),stamp(req['completed_at']);run=self.runs[run_id]
        if (begin is None or received is None or run['start'] is None or run['finish'] is None
            or not run['start']<=begin<=received<=run['finish'] or stamp(pr['observed_at'])!=received):
            raise ValueError('raw_HTTP_time_invalid')
        raw=dict(self.c.execute('SELECT * FROM raw_payloads WHERE payload_id=?',(pr['payload_id'],)).fetchone())
        body=gzip.decompress(raw['payload_gzip'])
        if hashlib.sha256(body).hexdigest()!=pr['sha256'] or len(body)!=raw['raw_bytes']:
            raise ValueError('raw_body_hash_or_size_mismatch')
        result=(json.loads(body),received,req)
        self.cache[key]=result
        if len(self.cache)>8:self.cache.popitem(last=False)
        return result

    def metadata(self,o,m):
        key=(o['run_id'],m['observed_at'],m['event_id'],m['condition_id'])
        if any(o[k]!=m[other]for k,other in [('run_id','run_id'),('sweep_id','sweep_id'),('event_id','event_id'),('condition_id','condition_id'),('observed_at','observed_at')]):
            raise ValueError('outcome_market_same_run_identity_failed')
        if key in self.event_cache:self.event_cache.move_to_end(key);return self.event_cache[key]
        found=[]
        for reqid in self.gamma.get((m['run_id'],m['observed_at']),[]):
            body,at,request=self.payload(m['run_id'],reqid,'GAMMA_EVENT_PAGE','gamma-api.polymarket.com')
            if request['method']!='GET' or urlparse(request['url']).path!='/events/keyset':raise ValueError('Gamma_request_endpoint_mismatch')
            items=body if isinstance(body,list) else body.get('events',[]) if isinstance(body,dict) else []
            found.extend((e,at)for e in items if isinstance(e,dict) and str(e.get('id'))==m['event_id'])
        if len(found)!=1:raise ValueError('same_run_raw_Gamma_event_missing_or_duplicate')
        event,at=found[0]
        markets=[x for x in event.get('markets',[]) if isinstance(x,dict) and str(x.get('conditionId'))==m['condition_id']]
        if len(markets)!=1:raise ValueError('raw_Gamma_condition_missing_or_duplicate')
        market=markets[0]
        matches=[self.cl.classify_event(event,f,self.registry)for f in self.registry.families]
        matches=[x for x in matches if x.accepted]
        if len(matches)!=1:raise ValueError('unsupported_or_unproven_major_sport_identity')
        ec=matches[0];mc=self.cl.classify_market(event,market,ec)
        if not mc.structure_eligible:raise ValueError('unsupported_whole_game_structure:'+','.join(mc.reasons))
        if (list(mc.labels)!=parsed(m['outcome_labels_json']) or list(mc.token_ids)!=parsed(m['token_ids_json'])
            or ec.family=='soccer' and mc.result_kind not in {'HOME','DRAW','AWAY'}):
            raise ValueError('raw_market_stored_outcome_mapping_conflict')
        result=(event,market,ec,mc,at)
        self.event_cache[key]=result
        if len(self.event_cache)>2000:self.event_cache.popitem(last=False)
        return result

    def book(self,b,metadata_time):
        body,at,request=self.payload(b['run_id'],b['request_id'],'CLOB_BOOK_BATCH','clob.polymarket.com')
        if request['method']!='POST' or urlparse(request['url']).path!='/books':raise ValueError('CLOB_batch_endpoint_mismatch')
        if not metadata_time <= stamp(request['started_at']):raise ValueError('book_request_predates_metadata_receipt')
        if not isinstance(body,list):raise ValueError('CLOB_batch_not_array')
        books=[x for x in body if isinstance(x,dict) and str(x.get('asset_id'))==b['token_id']]
        if len(books)!=1:raise ValueError('CLOB_batch_token_missing_or_duplicate')
        book=books[0]
        if (hashlib.sha256(canonical(book).encode()).hexdigest()!=b['raw_book_sha256']
            or stamp(b['observed_at'])!=at):raise ValueError('canonical_book_hash_or_time_mismatch')
        for side in ('asks','bids'):
            if not isinstance(book.get(side),list):raise ValueError('book_side_missing')
            for level in book[side]:
                if not isinstance(level,dict) or any(isinstance(level.get(k),bool)for k in ('price','size')):raise ValueError('invalid_book_level')
                price,size=visual.number(level.get('price')),visual.number(level.get('size'))
                if price is None or size is None or not 0<=price<=1 or size<0:raise ValueError('invalid_book_level')
        asks,bids=grid.levels(book,'asks'),grid.levels(book,'bids')
        if asks and bids and bids[0][0]>asks[0][0]:raise ValueError('crossed_book')
        return book,asks,bids,at


def get_runs(c,source,cutoff):
    configs={r['config_hash']:dict(r)for r in c.execute('SELECT * FROM research_config_versions')}
    groups=defaultdict(list)
    for row in c.execute('SELECT * FROM research_run_events'):groups[row['run_id']].append(dict(row))
    result={}
    for run_id,rows in groups.items():
        starts=[r for r in rows if r['event_type']=='STARTED'];ends=[r for r in rows if r['event_type']in{'SUCCEEDED','FAILED'}]
        first=starts[0]if len(starts)==1 else rows[0];cfg=configs.get(first['config_hash']);start=stamp(first['observed_at'])
        end=stamp(ends[0]['observed_at'])if len(ends)==1 else None
        body=parsed(cfg['config_json']) if cfg else {}
        tr=body.get('trading',{})
        identity=bool(cfg and cfg['job_name']==source['runtime_job'] and cfg['mode']=='sim'
            and first['strategy_source_digest']==cfg['strategy_source_digest']
            and re.fullmatch('[0-9a-f]{64}',str(first['strategy_source_digest']))
            and tr.get('strategy_source_digest')==first['strategy_source_digest']
            and body.get('job_name')==source['runtime_job'] and body.get('simulation_mode')is True)
        valid=bool(identity and len(starts)==len(ends)==1 and ends[0]['event_type']=='SUCCEEDED'
            and start is not None and end is not None and start<=end<cutoff
            and ends[0]['config_hash']==first['config_hash'] and ends[0]['strategy_source_digest']==first['strategy_source_digest'])
        result[run_id]={'start':start,'finish':end,'valid':valid,'identity':identity,'config':cfg,
                        'config_hash':first['config_hash'],'source_digest':first['strategy_source_digest']}
    return result


def cadence(cfg):
    tr=parsed(cfg['config_json']).get('trading',{})
    mins=tr.get('cadence_minutes');arm=tr.get('cadence_arm')
    expected={'FAST_1M':1,'CONTROL_5M':5,'FAST':1}.get(arm)
    if isinstance(mins,bool) or mins not in (1,5) or expected!=mins:raise ValueError('unknown_or_conflicting_declared_cadence')
    return int(mins*60)


def terminals(c,evidence,runs,known,info,cutoff,stats):
    """Same-cohort original closed/one-hot CLOB facts, no inferred redemption."""
    by_condition=defaultdict(dict)
    for identities in known.values():
        for identity in identities.values():
            by_condition[(identity['key'][0],identity['key'][1],identity['condition'])][identity['key']]=identity
    proofs=defaultdict(dict)
    for row in c.execute('SELECT * FROM resolution_observations ORDER BY observed_at'):
        r=dict(row);stats['resolution_rows_seen']+=1;run=runs.get(r['run_id'])
        if not run or not run['valid']:stats['resolution_unpublished_run']+=1;continue
        keys=by_condition.get((run['config_hash'],run['source_digest'],r['condition_id']),{})
        if len(keys)!=1:stats['resolution_unobserved_or_ambiguous_event']+=1;continue
        key,identity=next(iter(keys.items()))
        try:
            body,at,req=evidence.payload(r['run_id'],r['request_id'],'CLOB_MARKET_RESOLUTION','clob.polymarket.com')
            raw=evidence.payloads[r['request_id']][0]
            if (req['method']!='GET' or urlparse(req['url']).path!='/markets/'+r['condition_id']
                or raw['sha256']!=r['raw_market_sha256'] or stamp(r['observed_at'])!=at
                or not isinstance(body,dict) or body.get('condition_id')!=r['condition_id'] or body.get('closed')is not True):
                raise ValueError('resolution_raw_identity_failed')
            tokens=body.get('tokens')
            if (not isinstance(tokens,list) or len(tokens)!=2 or not all(isinstance(t,dict)for t in tokens)
                or {t.get('token_id'):t.get('outcome')for t in tokens}!=identity['tokens']):
                raise ValueError('resolution_exact_token_labels_failed')
            values={}
            for token in tokens:
                value=token.get('price')
                if isinstance(value,bool) or value not in (0,1) or token.get('winner')is not bool(value):raise ValueError('resolution_not_unique_onehot')
                values[token['token_id']]=float(value)
            if sum(values.values())!=1:raise ValueError('resolution_not_unique_onehot')
            evidence_json=parsed(r['evidence_json'])
            if evidence_json.get('closed')is not True or evidence_json.get('tokens')!=tokens:raise ValueError('resolution_normalized_evidence_conflict')
            if run['finish']>=cutoff:raise ValueError('resolution_after_cutoff')
            proofs[(key,r['run_id'])][r['condition_id']]={'values':values,'mapping':identity['tokens'],'time':run['finish']}
            stats['verified_resolution_condition_facts']+=1
        except (ValueError,TypeError,KeyError,OSError,json.JSONDecodeError)as exc:stats['resolution_invalid:'+str(exc)]+=1
    result=defaultdict(lambda:defaultdict(list));times=defaultdict(dict)
    for (key,run_id),conditions in proofs.items():
        expected=3 if key[2]=='soccer'else 1
        if len(conditions)!=expected:stats['resolution_incomplete_event_partition']+=1;continue
        if key[2]=='soccer':
            ys=[]
            for proof in conditions.values():
                ids=[t for t,label in proof['mapping'].items()if label=='Yes']
                if len(ids)!=1:break
                ys.append(proof['values'][ids[0]])
            if len(ys)!=3 or sum(ys)!=1:stats['resolution_soccer_partition_conflict']+=1;continue
        when=max(p['time']for p in conditions.values());times[key][run_id]=when
        for condition,proof in conditions.items():
            for token,value in proof['values'].items():
                result[key][(condition,token)].append({'payout':value,'observed_at':grid.iso(when),'observer_config':key[0],'source':'VERIFIED_WHITE_LEGACY_CLOB_ONEHOT','time_basis':'SUCCESSFUL_RUN_PUBLICATION_UPPER_BOUND'})
                stats['verified_terminal_token_facts']+=1
    return result,times


def read_source(source,start,end):
    path=Path(source['local_path']).resolve();lo,hi=stamp(start),stamp(end)
    if lo is None or hi is None or lo>=hi:raise ValueError('aware_half_open_range_required')
    manifest=parsed(Path(source['manifest']).read_text());expected=source['local_sha256']
    if (source.get('pinned') is not True or source.get('strategy')!='golden-watermelon'
        or manifest.get('pinned_path')!=str(path) or manifest.get('source_key')!=source['source_key']
        or manifest.get('sha256')!=expected or manifest.get('quick_check')!=['ok'] or sha(path)!=expected):raise ValueError('verified_White_pin_required')
    if any(Path(str(path)+s).exists()for s in ('-wal','-journal')):raise ValueError('standalone_pin_required')
    deps={str(p):sha(p)for p in DEPENDENCY_FILES};stats=Counter();groups=defaultdict(lambda:defaultdict(list));info={};known=defaultdict(dict)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('database_quick_check_failed')
        tables={r[0]for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'event_observations'in tables:raise ValueError('modern_White_requires_dedicated_event_reader')
        runs=get_runs(c,source,hi);evidence=Evidence(c,runs)
        outcomes=defaultdict(list)
        # Original all-sports census can have millions of unrelated outcome rows.
        # Keep exactly the same joins, loading only tokens with a physical attempt.
        for r in c.execute('SELECT o.* FROM outcome_observations o WHERE EXISTS '
                           '(SELECT 1 FROM orderbook_token_attempts a WHERE a.run_id=o.run_id AND a.token_id=o.token_id)'):
            outcomes[(r['run_id'],r['token_id'])].append(dict(r))
        needed=sorted({r['market_observation_id']for rows in outcomes.values()for r in rows});markets={}
        for offset in range(0,len(needed),500):
            ids=needed[offset:offset+500]
            for r in c.execute('SELECT * FROM market_observations WHERE observation_id IN ('+','.join('?'for _ in ids)+')',ids):
                markets[r['observation_id']]=dict(r)
        snapshots=defaultdict(list)
        for r in c.execute('SELECT * FROM orderbook_snapshots'):snapshots[(r['run_id'],r['token_id'])].append(dict(r))
        stats['physical_book_rows']=sum(len(v)for v in snapshots.values())
        attempts=[dict(r)for r in c.execute('SELECT * FROM orderbook_token_attempts ORDER BY observed_at,attempt_id')]
        stats['physical_attempt_rows']=len(attempts)
        attempted_keys={(a['run_id'],a['token_id'])for a in attempts}
        stats['physical_books_without_attempt']=sum(len(v)for k,v in snapshots.items()if k not in attempted_keys)
        for a in attempts:
            run=runs.get(a['run_id']);at=stamp(a['observed_at'])
            if not run or not run['identity']:stats['attempts_run_identity_missing']+=1;continue
            if at is None:at=run['start']
            if at is None or not lo<=at<hi:continue
            stats['in_range_attempt_rows']+=1
            books=snapshots.get((a['run_id'],a['token_id']),[]);stats['in_range_book_rows']+=len(books)
            os=outcomes.get((a['run_id'],a['token_id']),[])
            meta=None;reason='';book={};asks=bids=();valid=False
            try:
                if len(os)!=1:raise ValueError('same_run_outcome_metadata_missing_or_duplicate')
                o=os[0];m=markets.get(o['market_observation_id'])
                if not m:raise ValueError('same_run_market_metadata_missing')
                meta=evidence.metadata(o,m);raw_event,raw_market,ec,mc,mt=meta
                ix=o['outcome_index']
                if (not isinstance(ix,int) or isinstance(ix,bool) or not 0<=ix<len(mc.token_ids)
                    or mc.token_ids[ix]!=a['token_id'] or mc.labels[ix]!=o['outcome_label']):raise ValueError('exact_outcome_index_token_label_conflict')
                if ec.family=='soccer' and ix not in mc.eligible_indices:raise ValueError('not_an_actual_soccer_YES_book')
                slot=(mc.result_kind,'YES')if ec.family=='soccer' else (('HOME','AWAY')[ix],'DIRECT')
                key=(run['config_hash'],run['source_digest'],ec.family,m['event_id'])
                cd=cadence(run['config'])
                info.setdefault(key,{'title':raw_event.get('title',m['event_title']),'config':run['config'],'cadence':cd,'phases':set()})['phases'].add(ec.evidence.get('season_phase','UNKNOWN'))
                known[(key[0],key[1])][a['token_id']]={'key':key,'condition':o['condition_id'],'slot':slot,'label':o['outcome_label'],'tokens':dict(zip(mc.token_ids,mc.labels))}
                if len(books)!=1:raise ValueError('book_missing_or_duplicate')
                book,asks,bids,receipt=evidence.book(books[0],mt)
                if book.get('market')!=o['condition_id']:raise ValueError('CLOB_exact_condition_mismatch')
                if a['status']not in {'OBSERVED','EMPTY_BOOK'} or stamp(a['observed_at'])!=receipt or a['request_id']!=books[0]['request_id'] or not mt<=receipt:raise ValueError('book_attempt_receipt_or_status_mismatch')
                if not run['valid']:raise ValueError('run_not_successfully_published_before_cutoff')
                at=receipt;valid=True
            except (ValueError,TypeError,KeyError,OSError,json.JSONDecodeError) as exc:reason=str(exc)
            identity=known.get((run['config_hash'],run['source_digest']),{}).get(a['token_id'])
            if not identity:
                stats['unmapped_attempt:'+reason]+=1;stats['unmapped_book:'+reason]+=len(books);continue
            key=identity['key'];stats['mapped_book_rows']+=len(books)
            if not valid:
                stats['invalid:'+reason]+=1
                at=run['start'] # untrusted receipt must not jump over a gap barrier
            if at is None or not lo<=at<hi:stats['mapped_outside_valid_time']+=1;continue
            raw_event,raw_market=(meta[0],meta[1])if meta else ({},{})
            market_open=all(raw_market.get(k)is v for k,v in [('active',True),('closed',False),('enableOrderBook',True),('acceptingOrders',True)])
            opened=valid and market_open and all(raw_event.get(k)is v for k,v in [('active',True),('closed',False),('live',True),('ended',False)])
            liq=visual.number(raw_market.get('liquidityNum',raw_market.get('liquidity')));vol=visual.number(raw_market.get('volumeNum',raw_market.get('volume')))
            fee=grid.raw_fee_rate(raw_market)if valid else None
            spread=asks[0][0]-bids[0][0]if asks and bids else None;mid=(asks[0][0]+bids[0][0])/2 if asks and bids else None
            schedule=next((stamp(raw_event[k])for k in ('startTime','gameStartTime','eventStartTime')if raw_event.get(k)),None)
            role=visual.direct_role_metadata({'sport_family':key[2],'event_id':key[3],'condition_id':identity['condition'],'token_id':a['token_id'],'outcome':identity['label'],'outcome_side':identity['slot'][1]},raw_event.get('teams'),scope='WHITE_LEGACY_SAME_RUN_GAMMA',identity_proven=valid,evidence_event_id=raw_event.get('id'),evidence_observed_at=grid.iso(meta[4])if meta else None)
            status='MISSING'if not book else 'EMPTY_BOOK'if not asks and not bids else 'EMPTY_BIDS'if not bids else 'EMPTY_ASKS'if not asks else 'FULL'
            snap=grid.Snap(a['token_id'],identity['condition'],identity['slot'],at,a['run_id'],grid.raw_source_minute(raw_event,key[2]),mid,asks,bids,valid,bool(opened),bool(opened and liq is not None and liq>=5000 and vol is not None and vol>=5000),fee,spread,grid.ask_side_state(book),reason or ORIGIN,grid.walk(asks,5,True),
                scheduled_age=(at-schedule)/60 if valid and schedule is not None else None,entry_set_complete=True,evidence_origin=ORIGIN,observation_status=status,market_open_observed=market_open,
                outcome_label=identity['label'],verified_role=role.get('verified_role'),verified_team_name=role.get('verified_team_name'),role_evidence_scope=role.get('role_evidence_scope'),legacy_result_kind=identity['slot'][0],legacy_role_semantics='SOURCE_PARTITION_NOT_VENUE_ROLE')
            groups[key][a['run_id']].append(snap);stats['valid_rows'if valid else'invalid_rows']+=1
        terminal,terminal_times=terminals(c,evidence,runs,known,info,hi,stats)
        events=[]
        for key,byrun in groups.items():
            cfg=parsed(info[key]['config']['config_json']);phases=info[key]['phases'];cd=info[key]['cadence']
            config={'config_hash':key[0],'strategy_source_digest':key[1],'mode':'sim','job_name':source['runtime_job'],'trading':cfg.get('trading',{}),'cadence_seconds':cd,'one_minute_replay_eligible':cd==60,'raw_point_in_time_archive':True,'_analysis_adapter':'white_legacy_raw','season_phase':next(iter(phases))if len(phases)==1 else'MIXED_OR_CHANGED','observed_season_phases':sorted(phases),'evidence_origins':[ORIGIN]}
            failures=[(r['start'],r['finish'])for r in runs.values()if r['config_hash']==key[0]and r['source_digest']==key[1]and not r['valid']and r['start']is not None and r['start']<hi]
            normalized=[grid.normalize_group(v,key[2],True)for v in byrun.values()]
            # An earlier unknown quote remains a gap. Terminal facts never erase it.
            normalized.extend(grid.Group(at,run_id,[],False)for run_id,at in terminal_times.get(key,{}).items())
            events.append(grid.Event(source['id'],visual.identifier([source['id'],*key[:3]]),key[2],key[3],info[key]['title'],config,sorted(normalized,key=lambda g:g.time),failures,dict(terminal.get(key,{}))))
    if sha(path)!=expected or any(sha(Path(p))!=h for p,h in deps.items()):raise ValueError('source_or_classifier_changed_during_read')
    return events,{'source':source['id'],'supported':True,'events':len(events),'adapter':ORIGIN,'stats':dict(stats),'classifier_dependencies':deps,'dependencies':[{'path':p,'sha256':h}for p,h in deps.items()],'classifier_scope':'RETROSPECTIVE_FROZEN_COCONUT_V6_MAJOR_FIVE_FAMILY_IDENTITY','terminal_import':'EXACT_SAME_COHORT_CLOB_CLOSED_ONEHOT_SAME_RUN_COMPLETE_PARTITION; prior unknown quotes remain gaps','no_synthetic_NO':True}
