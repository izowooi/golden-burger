"""One-minute direct book recorder; no entry signals, positions or orders."""
from collections import Counter
from datetime import datetime,timedelta,timezone
from dataclasses import asdict
import gzip,hashlib,json,math,time
from uuid import uuid4
from .api.transport import CycleBudget,iso_utc,canonical_json
from .api.clob_client import parse_book,MalformedBookError
from .classifier import classify_event,classify_market,classify_season_phase,_team_forms,_normalize
from .lifecycle import parse_source_utc,gamma_clock_fallback
from .recorder_config import registry,slot_start_utc
from .recorder_http import RecorderClient


def schedule_evidence(event):
    # Event/listing startDate is not proof of kickoff. Prefer explicit game-time
    # fields, then consistent whole-game market gameStartTime values.
    for field in ('startTime','gameStartTime','eventStartTime'):
        if event.get(field) not in (None,''):
            value=parse_source_utc(event[field])
            return field,str(event[field]),iso_utc(value) if value else None
    times={str(m['gameStartTime']) for m in (event.get('markets') if isinstance(event.get('markets'),list) else []) if isinstance(m,dict)
           and m.get('sportsMarketType')=='moneyline' and m.get('gameStartTime') not in (None,'')}
    if len(times)==1:
        raw=times.pop();value=parse_source_utc(raw)
        return 'markets[].gameStartTime',raw,iso_utc(value) if value else None
    return None,None,None


def slots_for(event,classification):
    """Ignore trading availability and Gamma prices, never invent a probability."""
    family=classification.family;slots=[];teams=event.get('teams') or []
    for market in event.get('markets',[]) if isinstance(event.get('markets'),list) else []:
        if not isinstance(market,dict):continue
        c=classify_market(event,market,classification)
        if len(c.labels)!=2 or len(c.token_ids)!=2 or len(set(c.token_ids))!=2 or not all(c.token_ids):continue
        # Alignment of price arrays is unnecessary for a raw token identity.
        bad=set(c.reasons)-{'MARKET_NOT_OPEN','PUBLIC_BOOK_NOT_ENABLED','TWO_OUTCOME_ALIGNMENT_REQUIRED'}
        if bad:continue
        condition=str(market.get('conditionId') or market.get('condition_id') or '')
        if not condition:continue
        for index,(label,token) in enumerate(zip(c.labels,c.token_ids)):
            legacy=c.result_kind if family=='soccer' else 'DIRECT'
            team=None
            if family=='soccer' and c.result_kind in ('HOME','AWAY'):
                team=teams[0 if c.result_kind=='HOME' else 1]
            elif family!='soccer':
                matched=[t for t in teams if _normalize(label) in _team_forms(t)]
                if len(matched)==1:team=matched[0]
            ordering=str((team or {}).get('ordering') or '').lower()
            role=ordering.upper() if ordering in ('home','away') else 'UNKNOWN'
            result='DRAW' if legacy=='DRAW' else role if role!='UNKNOWN' else None
            slots.append({'slot':condition+':'+str(index),'condition_id':condition,'token_id':token,'outcome':label,
                'outcome_side':label.upper() if family=='soccer' else 'DIRECT','result_kind':result,
                'legacy_result_kind':legacy,'verified_role':role,'team_name':(team or {}).get('name'),
                'question':market.get('question'),'group_item_title':market.get('groupItemTitle'),
                'all_tokens':list(c.token_ids),'all_outcomes':list(c.labels)})
    if family=='soccer':
        groups={s['condition_id']:s['legacy_result_kind'] for s in slots}
        complete=(len(slots)==6 and len(groups)==3 and set(groups.values())=={'HOME','DRAW','AWAY'})
    else:complete=len(slots)==2 and len({s['condition_id'] for s in slots})==1
    complete=complete and len({s['token_id'] for s in slots})==len(slots)
    return sorted(slots,key=lambda s:s['slot']) if complete else [],bool(complete)


def identity_signature(slots):
    return sorted((s['condition_id'],s['token_id'],s['outcome'],tuple(sorted(zip(s['all_tokens'],s['all_outcomes'])))) for s in slots)


def terminal(event,slots,family):
    if not slots or not isinstance(event.get('markets'),list):return None
    by_condition={}
    for slot in slots:by_condition.setdefault(slot['condition_id'],slot)
    values=[];proof=[]
    for cid,slot in by_condition.items():
        matches=[m for m in event.get('markets',[]) if isinstance(m,dict) and str(m.get('conditionId') or m.get('condition_id') or '')==cid]
        if len(matches)!=1 or matches[0].get('closed') is not True:return None
        market=matches[0]
        try:
            raw=lambda key:json.loads(market[key]) if isinstance(market[key],str) else market[key]
            tokens,labels,prices=raw('clobTokenIds'),raw('outcomes'),raw('outcomePrices')
            if len(tokens)!=2 or len(set(tokens))!=2 or dict(zip(tokens,labels))!=dict(zip(slot['all_tokens'],slot['all_outcomes'])):return None
            if any(isinstance(p,bool) for p in prices):return None
            payouts=[float(p) for p in prices]
            void=payouts==[.5,.5] and str(market.get('umaResolutionStatus') or '').lower()=='resolved'
            if payouts not in ([1.,0.],[0.,1.]) and not void:return None
            values.append(dict(zip(labels,payouts)).get('Yes') if family=='soccer' else 1.)
            proof.extend({'condition_id':cid,'token_id':t,'outcome':l,'payout':p} for t,l,p in zip(tokens,labels,payouts))
        except (KeyError,ValueError,TypeError):return None
    if family=='soccer' and sorted(values) not in ([0.,0.,1.],[.5,.5,.5]):return None
    return {'source':'GAMMA_CLOSED_EXACT_TOKEN_PAYOUT','tokens':proof}


def end_anchor(event,clock,receipt,previous):
    payload=(clock or {}).get('payload') or {}
    clock_receipt=(clock or {}).get('received_at') or receipt
    if previous.get('end_anchor') and str(previous.get('end_basis') or '').startswith('SOURCE_'):
        return previous['end_anchor'],previous['end_basis']
    # The compact Gamma clock intentionally omits actual-end fields. Recheck
    # the same raw event before falling back to a receipt upper bound, and
    # refine a previous upper bound when an authoritative timestamp arrives.
    for source,observed in ((payload,clock_receipt),(event,receipt)):
        if source.get('ended') is not True:continue
        observed_at=parse_source_utc(observed)
        for key in ('gameEndTime','actualEndTime','endedAt','completedAt'):
            value=parse_source_utc(source.get(key))
            if value is not None and observed_at is not None and value<=observed_at:return iso_utc(value),'SOURCE_'+key
    if previous.get('end_anchor'):return previous['end_anchor'],previous['end_basis']
    if payload.get('ended') is True:return clock_receipt,'FIRST_EXPLICIT_ENDED_RECEIPT_UPPER_BOUND'
    if event.get('ended') is True:return receipt,'FIRST_EXPLICIT_ENDED_RECEIPT_UPPER_BOUND'
    return None,None


def window_status(now,scheduled,end,ever_live,pre=600,post=600):
    finish=parse_source_utc(end)
    if finish is not None and now>finish+timedelta(seconds=post):return 'POST_WINDOW_COMPLETE'
    start=parse_source_utc(scheduled)
    if ever_live:return 'IN_WINDOW'
    if start is None:return 'SCHEDULE_UNKNOWN'
    return 'BEFORE_WINDOW' if now<start-timedelta(seconds=pre) else 'IN_WINDOW'


def discovery_due(slot,last,interval_seconds,phase_seconds,force=False):
    if force or not last:return True
    if interval_seconds<=0 or interval_seconds%60:raise ValueError('discovery interval must be whole positive minutes')
    interval_minutes=interval_seconds//60
    if not 0<=phase_seconds<60:raise ValueError('slot phase must be within one minute')
    nominal_trigger=slot+timedelta(seconds=(60-phase_seconds)%60)
    scheduled=(int(nominal_trigger.timestamp())//60)%interval_minutes==0
    overdue=(slot-parse_source_utc(last)).total_seconds()>=interval_seconds
    return scheduled or overdue


class Recorder:
    def __init__(self,config,store,client_factory=RecorderClient):
        self.config,self.store,self.client_factory=config,store,client_factory
        self.registry=registry()

    def run(self,now=None,*,force_discovery=False,probe=False):
        now=now or datetime.now(timezone.utc)
        if now.tzinfo is None:raise ValueError('UTC-aware reference required')
        now=now.astimezone(timezone.utc);reference=iso_utc(now)
        slot_dt=slot_start_utc(now,self.config.slot_phase_seconds)
        if self.store.day!=slot_dt.date().isoformat():raise ValueError('database day must match claimed UTC slot')
        slot=iso_utc(slot_dt);next_slot=slot_dt+timedelta(seconds=60);run=uuid4().hex
        config=self.config.snapshot('PROBE' if probe else 'SCHEDULED')
        provenance={'strategy_name':'golden-coconut','job_name':config['job_name'],'mode':'sim','observation_mode':config['observation_mode'],'config_hash':config['config_hash'],'strategy_source_digest':config['source_digest']}
        claimed=self.store.claim(slot,run,reference,config)
        if claimed:return {'status':'SKIPPED_DUPLICATE_SLOT','owner_run_id':claimed,'slot_utc':slot,**provenance}
        available=50.
        request_time=max(0.,min(42.,available-8.))
        budget=CycleBudget(time.monotonic(),available,available-request_time,available)
        client=self.client_factory(self.config,self.registry,self.store,budget)
        events={};due={};errors=[];sweeps=[];observations=[];books=[];updates={};clock_rows=[]
        prior=self.store.c.execute('SELECT stats_json FROM cycles ORDER BY rowid DESC LIMIT 1').fetchone()
        last=json.loads(prior[0]).get('last_complete_discovery_at') if prior else None
        try:
            discover=discovery_due(slot_dt,last,self.config.discovery_seconds,self.config.slot_phase_seconds,force_discovery)
            if discover:
                for family,sweep,error in client.discovery(run,slot):
                    sweeps.append({'family':family,'cursor_complete':bool(sweep and sweep.cursor_complete),'pages':len(sweep.pages) if sweep else 0,'error':error})
                    if error or not sweep or not sweep.cursor_complete:errors.append('census:'+family)
                    if not sweep:continue
                    for page in sweep.pages:
                        for raw in page.events:
                            classification=classify_event(raw,self.registry.by_code[family],self.registry)
                            if not classification.accepted:continue
                            _,_,scheduled=schedule_evidence(raw)
                            if not scheduled or not now-timedelta(hours=24)<=parse_source_utc(scheduled)<now+timedelta(hours=48):continue
                            identity,valid=slots_for(raw,classification)
                            eid=str(raw['id'])
                            if eid in events and events[eid]['family']!=family:raise ValueError('event crossed family')
                            events[eid]={'raw':raw,'family':family,'received_at':page.received_at,'request_id':page.request_id,'classification':classification,'slots':identity,'valid':valid,'status':'OBSERVED'}
                if all(s['cursor_complete'] for s in sweeps) and len(sweeps)==5:last=reference
            # Persist initial tracking obligations independently of subsequent API failure.
            with self.store.transaction() as c:
                for eid,e in events.items():
                    _,_,scheduled=schedule_evidence(e['raw'])
                    row={'event_id':eid,'family':e['family'],'first_run_id':run,'state':'SCHEDULED','slots_json':canonical_json(e['slots']),
                        'scheduled_start':scheduled,'end_anchor':None,'end_basis':None,'ever_live':0,'terminal_json':None,
                        'next_due':reference,'missing_count':0,'anchor_json':canonical_json({'source_run':run,'request_id':e['request_id'],'received_at':e['received_at']})}
                    if c.execute('SELECT 1 FROM tracked_events WHERE event_id=?',(eid,)).fetchone() is None:self.store.insert(c,'tracked_events',row)
            due={r['event_id']:r for r in self.store.pending(reference)}
            for eid in events:
                r=self.store.c.execute('SELECT * FROM tracked_events WHERE event_id=?',(eid,)).fetchone()
                if r and r['state']!='DONE':due[eid]=dict(r)
            for eid,old in sorted(due.items(),key=lambda x:(x[1]['next_due'],x[0])):
                if eid in events:continue
                if budget.request_stop_at-time.monotonic()<.2:
                    events[eid]={'raw':None,'family':old['family'],'received_at':None,'request_id':None,'status':'BUDGET_DEFERRED'};errors.append('metadata_deferred');continue
                try:
                    reply=client.event(run,eid,old['family'])
                    classification=classify_event(reply.event,self.registry.by_code[old['family']],self.registry)
                    identity,valid=slots_for(reply.event,classification) if classification.accepted else ([],False)
                    events[eid]={'raw':reply.event,'family':old['family'],'received_at':reply.received_at,'request_id':reply.request_id,
                        'classification':classification,'slots':identity,'valid':valid,'status':'OBSERVED'}
                except Exception as error:
                    events[eid]={'raw':None,'family':old['family'],'received_at':None,'request_id':getattr(error,'request_id',None),'status':'ERROR','reason':type(error).__name__};errors.append('metadata_error')
            events={eid:e for eid,e in events.items() if eid in due}
            clock_targets=[e['raw'] for eid,e in events.items() if e['raw'] and window_status(now,due[eid]['scheduled_start'],due[eid]['end_anchor'],due[eid]['ever_live'])=='IN_WINDOW']
            clocks=client.clocks(run,clock_targets)
            for ordinal,raw in enumerate(clocks.raw_messages):clock_rows.append({'run_id':run,'request_id':clocks.request_id,'ordinal':ordinal,'sha256':hashlib.sha256(raw).hexdigest(),'received_at':clocks.completed_at,'raw_gzip':gzip.compress(raw,mtime=0)})
            probe_ids=set()
            if probe:
                for family in self.registry.by_code:
                    candidates=[eid for eid,e in events.items() if e.get('valid') and e['family']==family]
                    if candidates:probe_ids.add(min(candidates,key=lambda eid:(due[eid]['scheduled_start'] or '',eid)))
            wanted=[];contexts={}
            for eid,old in sorted(due.items(),key=lambda item:(item[1]['next_due'],item[0])):
                e=events[eid];raw=e['raw'];valid=e.get('valid',False);oldslots=json.loads(old['slots_json']);fresh=e.get('slots',[])
                if oldslots and identity_signature(fresh)!=identity_signature(oldslots):valid=False
                slots=fresh if valid else oldslots or fresh
                clock_update=clocks.updates.get(str((raw or {}).get('slug') or '').lower())
                clock={'source':'POLYMARKET_SPORTS_WSS','received_at':clock_update.received_at,'payload':dict(clock_update.payload),'matched_by':clock_update.matched_by} if clock_update else {'source':'GAMMA_EXPLICIT','received_at':e['received_at'],'payload':gamma_clock_fallback(raw) if raw else None}
                scheduled=schedule_evidence(raw)[2] if raw else old['scheduled_start'];scheduled=scheduled or old['scheduled_start']
                live=old['ever_live'] or bool(raw and raw.get('live') is True) or bool((clock.get('payload') or {}).get('live') is True)
                end,basis=end_anchor(raw or {},clock,e['received_at'] or reference,old)
                window=window_status(now,scheduled,end,live,self.config.pre_seconds,self.config.post_seconds)
                if probe and eid in probe_ids:window='PROBE_OUTSIDE_WINDOW'
                proof=terminal(raw,slots,old['family']) if raw and valid else None
                state='WAIT_SETTLEMENT' if window=='POST_WINDOW_COMPLETE' else 'SCHEDULED' if window in ('BEFORE_WINDOW','SCHEDULE_UNKNOWN') else 'WINDOW'
                if proof and window=='POST_WINDOW_COMPLETE':state='DONE'
                missing=0 if raw and valid else old['missing_count']+1
                if e['status']=='BUDGET_DEFERRED':next_due=old['next_due']
                elif window=='BEFORE_WINDOW':next_due=iso_utc(parse_source_utc(scheduled)-timedelta(seconds=self.config.pre_seconds))
                elif window=='POST_WINDOW_COMPLETE':next_due=iso_utc(slot_dt+timedelta(seconds=self.config.settlement_poll_seconds))
                else:next_due=iso_utc(next_slot)
                updates[eid]={**old,'state':state,'slots_json':canonical_json(fresh if valid else oldslots or fresh),'scheduled_start':scheduled,'end_anchor':end,'end_basis':basis,
                    'ever_live':int(live),'terminal_json':canonical_json(proof) if proof else old['terminal_json'],'next_due':next_due,'missing_count':missing}
                row={'run_id':run,'event_id':eid,'family':old['family'],'league':getattr(e.get('classification'),'competition_code',None),
                    'season_phase':classify_season_phase(raw,old['family']) if raw else None,'metadata_status':e['status'],'request_id':e['request_id'],
                    'received_at':e['received_at'],'event_json':canonical_json(raw) if raw else None,'clock_json':canonical_json(clock),'slots_json':canonical_json(slots),
                    'identity_valid':int(valid),'window_status':window,'lifecycle_state':state,'scheduled_start':scheduled,'end_anchor':end,'end_basis':basis,
                    'terminal_json':canonical_json(proof) if proof else None,'reason':e.get('reason','')}
                observations.append(row)
                if window not in ('IN_WINDOW','SCHEDULE_UNKNOWN','PROBE_OUTSIDE_WINDOW'):continue
                if not valid:errors.append('identity_incomplete')
                if not slots:
                    for index in range(6 if old['family']=='soccer' else 2):books.append(self.empty_book(run,eid,{'slot':'UNIDENTIFIED_'+str(index)},'IDENTITY_MISSING','token identity unavailable'))
                    continue
                for slotdata in slots:
                    token=slotdata['token_id'];wanted.append(token);contexts[token]=(row,slotdata,raw)
            if len(set(wanted))!=len(wanted):raise ValueError('token across multiple events')
            selected=[];selected_groups=[]
            for eid in dict.fromkeys(contexts[token][0]['event_id'] for token in wanted):
                group=[token for token in wanted if contexts[token][0]['event_id']==eid]
                if len(selected)+len(group)<=self.config.max_tokens_per_cycle:selected.extend(group);selected_groups.append(group)
                else:updates[eid]['next_due']=due[eid]['next_due']
            attempts=client.books(run,selected,groups=selected_groups) if selected else {}
            if len(wanted)>len(selected):errors.append('token_budget')
            for token in wanted:
                event,slotdata,raw_event=contexts[token];attempt=attempts.get(token)
                if not attempt:
                    books.append(self.empty_book(run,event['event_id'],slotdata,'NOT_ATTEMPTED','budget'));updates[event['event_id']]['next_due']=due[event['event_id']]['next_due'];errors.append('book_deferred');continue
                record=self.empty_book(run,event['event_id'],slotdata,attempt['status'],attempt.get('reason',''))
                receipt=client.receipts.get(attempt.get('request_id'),{});record.update(request_id=attempt.get('request_id'),requested_at=receipt.get('started_at'),received_at=attempt.get('received_at'))
                raw=attempt.get('raw')
                if raw is not None:
                    try:
                        if any(isinstance(level.get(k),bool) for side in ('bids','asks') for level in raw.get(side,[]) if isinstance(level,dict) for k in ('price','size')):raise ValueError('boolean book level')
                        parsed=parse_book(raw,token)
                        if raw.get('market') not in (None,slotdata['condition_id']):raise ValueError('condition mismatch')
                        status='EMPTY_BOOK' if not parsed.bids and not parsed.asks else 'EMPTY_BIDS' if not parsed.bids else 'EMPTY_ASKS' if not parsed.asks else 'FULL'
                        blob=canonical_json(raw).encode();record.update(status=status,book_sha256=hashlib.sha256(blob).hexdigest(),book_gzip=gzip.compress(blob,mtime=0))
                    except Exception as error:record.update(status='ERROR',reason=type(error).__name__)
                market_rows=(raw_event or {}).get('markets');market_rows=market_rows if isinstance(market_rows,list) else []
                markets=[m for m in market_rows if isinstance(m,dict) and str(m.get('conditionId') or m.get('condition_id'))==slotdata['condition_id']]
                fee={k:markets[0].get(k) for k in ('feesEnabled','feeSchedule','feeType','feeRate','feeRateBps','makerBaseFee','takerBaseFee') if k in markets[0]} if len(markets)==1 else {}
                record['fee_json']=canonical_json({'source':'GAMMA_CURRENT','received_at':event['received_at'],'fields':fee})
                times=[parse_source_utc(event['received_at']),parse_source_utc(record['requested_at']),parse_source_utc(record['received_at'])]
                record['point_in_time_valid']=int(bool(event['identity_valid'] and all(t is not None for t in times) and times==sorted(times) and record['status'] in ('FULL','EMPTY_BOOK','EMPTY_ASKS','EMPTY_BIDS')))
                if record['status'] in ('ERROR','NOT_ATTEMPTED','MISSING'):errors.append('book_error_missing_or_deferred')
                if record['status']=='NOT_ATTEMPTED':updates[event['event_id']]['next_due']=due[event['event_id']]['next_due']
                books.append(record)
        except Exception as error:errors.append(type(error).__name__)
        finally:client.close()
        # Even an unexpected phase failure leaves every known due token slot
        # visible; do not report zero expected work merely because processing aborted.
        observed_ids={e['event_id'] for e in observations}
        for eid,old in due.items():
            if eid in observed_ids:continue
            window=window_status(now,old['scheduled_start'],old['end_anchor'],old['ever_live'])
            observations.append({'run_id':run,'event_id':eid,'family':old['family'],'league':None,'season_phase':None,
                'metadata_status':'NOT_ATTEMPTED','request_id':None,'received_at':None,'event_json':None,'clock_json':'{}',
                'slots_json':old['slots_json'],'identity_valid':0,'window_status':window,'lifecycle_state':old['state'],
                'scheduled_start':old['scheduled_start'],'end_anchor':old['end_anchor'],'end_basis':old['end_basis'],
                'terminal_json':None,'reason':'cycle_aborted_before_event_processing'})
        occupied={(b['event_id'],b['slot']) for b in books}
        for event in observations:
            if event['window_status'] not in ('IN_WINDOW','SCHEDULE_UNKNOWN','PROBE_OUTSIDE_WINDOW'):continue
            slots=json.loads(event['slots_json']) or [{'slot':'UNIDENTIFIED_'+str(i)} for i in range(6 if event['family']=='soccer' else 2)]
            for item in slots:
                if (event['event_id'],item['slot']) not in occupied:
                    books.append(self.empty_book(run,event['event_id'],item,'NOT_ATTEMPTED','cycle_aborted'))
                    errors.append('book_deferred')
        if budget.elapsed()>available:errors.append('cycle_deadline')
        if any(b['status']=='IDENTITY_MISSING' for b in books):errors.append('identity_incomplete')
        status='FAILED' if errors else 'SUCCEEDED'
        if errors:
            for row in updates.values():
                if row['state']=='DONE':row['state']='WAIT_SETTLEMENT'
        stats={'events':len(observations),'expected_tokens':len(books),'book_status_counts':dict(Counter(b['status'] for b in books)),
               'trusted_books':sum(b['point_in_time_valid'] for b in books),'errors':sorted(set(errors)),'sweeps':sweeps,
               'last_complete_discovery_at':last,'elapsed_seconds':budget.elapsed(),'slot_available_seconds':available,'request_available_seconds':request_time,'clock_status':getattr(locals().get('clocks'),'status',None)}
        with self.store.transaction() as c:
            for table,rows in (('clock_observations',clock_rows),('event_observations',observations),('book_observations',books)):
                for row in rows:self.store.insert(c,table,row)
            for eid,row in updates.items():
                keys=[k for k in row if k!='event_id'];c.execute('UPDATE tracked_events SET '+','.join(k+'=?' for k in keys)+' WHERE event_id=?',tuple(row[k] for k in keys)+(eid,))
            if budget.elapsed()>available:
                status='FAILED';stats['errors']=sorted(set(stats['errors']+['publication_budget']))
                c.execute("UPDATE tracked_events SET state='WAIT_SETTLEMENT' WHERE state='DONE' AND event_id IN ("+','.join('?' for _ in updates)+")",tuple(updates)) if updates else None
            self.store.insert(c,'cycles',{'run_id':run,'slot_utc':slot,'reference_at':reference,'published_at':iso_utc(),
                'status':status,'config_json':canonical_json(config),'stats_json':canonical_json(stats)})
        return {'run_id':run,'status':status,**provenance,**stats}

    @staticmethod
    def empty_book(run,eid,slot,status,reason):
        return {'run_id':run,'event_id':eid,'slot':slot['slot'],'condition_id':slot.get('condition_id'),'token_id':slot.get('token_id'),
            'outcome':slot.get('outcome'),'result_kind':slot.get('result_kind'),'verified_role':slot.get('verified_role','UNKNOWN'),
            'team_name':slot.get('team_name'),'status':status,'request_id':None,'requested_at':None,'received_at':None,
            'book_sha256':None,'book_gzip':None,'fee_json':'{}','point_in_time_valid':0,'reason':reason}
