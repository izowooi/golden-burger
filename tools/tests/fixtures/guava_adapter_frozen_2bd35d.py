"""Verified Guava pins -> conservative_sports_grid Event objects; no live imports.

HOME/AWAY requires each point-in-time Gamma team's explicit ordering field;
array order is never used. Source cohorts and postgame/failed/missing observations
remain separate. Fees come from that event receipt, never the latest market.
"""
from pathlib import Path
import sqlite3,json,gzip,hashlib,re
from collections import defaultdict,Counter
import conservative_sports_grid as grid


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def raw_value(blob,digest):
    if blob is None:
        if digest is not None:raise ValueError('hash_without_body')
        return None
    payload=gzip.decompress(blob)
    if hashlib.sha256(payload).hexdigest()!=digest:raise ValueError('raw_hash_mismatch')
    return json.loads(payload)
def array(x):return json.loads(x) if isinstance(x,str) else x
def norm(x):return ' '.join(''.join(c if c.isalnum() else ' ' for c in str(x or '')).casefold().split())
def number(x):return grid.visual.number(x)
def ts(x):return grid.visual.timestamp(x)
def roles(event):
    teams=event['raw'].get('teams',[])
    if len(teams)!=2 or {t.get('ordering') for t in teams}!={'home','away'}:raise ValueError('explicit_home_away_unproven')
    forms={t['ordering'].upper():{norm(t.get(k)) for k in ('name','alias','abbreviation')}-{''} for t in teams}
    if forms['HOME']&forms['AWAY']:raise ValueError('ambiguous_team_identity')
    markets={m.get('conditionId') or m.get('condition_id'):m for m in event['raw'].get('markets',[])}
    result={}
    for s in event['side_definitions']:
        market=markets[s['condition_id']];labels,tokens=array(market['outcomes']),array(market['clobTokenIds'])
        if len(tokens)!=2 or len(set(tokens))!=2 or labels[tokens.index(s['token_id'])]!=s['outcome_label']:raise ValueError('token_label_identity')
        if s['outcome_side'] in ('YES','NO'):
            if s['result_kind']=='DRAW':role='DRAW'
            else:
                matched=[role for role,f in forms.items() if norm(market.get('groupItemTitle')) in f]
                if len(matched)!=1:raise ValueError('unproven_proposition_team')
                role=matched[0]
        else:
            matched=[role for role,f in forms.items() if norm(s['outcome_label']) in f]
            if len(matched)!=1:raise ValueError('unproven_direct_team')
            role=matched[0]
        result[s['token_id']]=(role,s['outcome_side'])
    if set(result.values())!=grid.expected_slots(event['sport_family'],False):raise ValueError('incomplete_exact_slots')
    return result,markets

def minute(raw,sport):
    if sport!='soccer':return None
    v=raw.get('elapsed');period=raw.get('period')
    if period=='HT':return 45.0
    if period=='FT':return 90.0
    if period not in ('1H','2H') or not isinstance(v,(str,int,float)):return None
    text=str(v).strip()
    if not re.fullmatch(r'\d+(?:\.\d+)?(?:\s*\+\s*\d+(?:\.\d+)?)?',text):return None
    n=sum(float(x) for x in text.split('+'))
    if period=='2H' and n<45:n+=45
    if (period=='1H' and 0<=n<=70) or (period=='2H' and 45<=n<=130):return n
    return None

def read_source(source,start,end):
    path=Path(source['local_path']).resolve();manifest=json.loads(path.with_name('manifest.json').read_text())
    before=(path.stat().st_size,path.stat().st_mtime_ns);digest=sha(path)
    if not source.get('pinned') or 'pinned' not in path.parts or digest!=source['local_sha256'] or digest!=manifest['sha256'] or manifest['pinned_path']!=str(path) or manifest['quick_check']!=['ok']:raise ValueError('unverified_guava_pin')
    if any(Path(str(path)+s).exists() for s in ('-wal','-shm','-journal')):raise ValueError('snapshot_sidecar')
    db=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    if db.execute('pragma quick_check').fetchone()[0]!='ok' or db.execute('pragma foreign_key_check').fetchall():raise ValueError('sqlite_integrity')
    contract=dict(db.execute('select * from collection_contracts').fetchone())
    if contract['contract_name']!='guava-research-v1' or contract['mode']!='sim':raise ValueError('wrong_guava_contract')
    stats=Counter();a,z=ts(start),ts(end);runs={}
    for row in db.execute("select r.*,e.status,e.occurred_at from run_audits r left join run_events e on r.run_id=e.run_id and e.status!='STARTED'"):
        r=dict(row);r['success']=r['status']=='SUCCEEDED' and ts(r['occurred_at']) is not None and ts(r['occurred_at'])<z;runs[r['run_id']]=r
    configs={(r['config_hash'],r['strategy_source_digest']):json.loads(r['config_json']) for r in db.execute('select * from strategy_configs')}
    allrows=defaultdict(list);bookrows=defaultdict(dict);requests={}
    for row in db.execute('select * from events order by observed_at'):
        r=runs[row['run_id']]
        if a<=ts(r['started_at'])<z and ts(row['observed_at'])<z:allrows[(r['config_hash'],r['strategy_source_digest'],row['sport_family'],row['event_id'])].append(dict(row))
    keys={key for key,rows in allrows.items() if any(r['eligible'] for r in rows)}
    wanted={(r['run_id'],r['event_id']) for key in keys for r in allrows[key]}
    for row in db.execute('select * from book_attempts'):
        if (row['run_id'],row['event_id']) in wanted:bookrows[(row['run_id'],row['event_id'])][row['token_id']]=dict(row)
    request_keys={(r['run_id'],r['request_id']) for books in bookrows.values() for r in books.values() if r['request_id']}
    for row in db.execute('select run_id,request_id,source,path,status,started_at,received_at,payload_gzip,payload_sha256 from source_requests'):
        if (row['run_id'],row['request_id']) in request_keys:requests[(row['run_id'],row['request_id'])]=dict(row)
    events=[]
    for key in sorted(keys):
        cfg_hash,source_digest,sport,eid=key;cfg=configs[(cfg_hash,source_digest)];groups=[];terminal=defaultdict(list);title=eid;immutable_map=None;immutable_labels=None
        def record_terminal(raw,observed,success):
            if not success or not immutable_map or not immutable_labels or str(raw.get('id'))!=eid:return
            markets={m.get('conditionId') or m.get('condition_id'):m for m in raw.get('markets',[])}
            proofs=[]
            for condition in {v[0] for v in immutable_map.values()}:
                m=markets.get(condition,{})
                if m.get('closed') is not True or m.get('umaResolutionStatus')!='resolved':return
                try:tokens,prices,labels=[array(m.get(k)) for k in ('clobTokenIds','outcomePrices','outcomes')]
                except (ValueError,TypeError):return
                expected={t for t,v in immutable_map.items() if v[0]==condition}
                if not all(isinstance(x,list) and len(x)==2 for x in (tokens,prices,labels)) or set(tokens)!=expected:return
                if any(immutable_labels[t]!=labels[tokens.index(t)] for t in tokens):return
                values=[number(x) for x in prices]
                if sorted(x for x in values if x is not None) not in ([0.,1.],[.5,.5]):return
                proofs.append((condition,tokens,labels,values))
            yes_values=[v[l.index('Yes')] for _,t,l,v in proofs] if sport=='soccer' else []
            if sport=='soccer' and sorted(yes_values)!=[0.,0.,1.] and yes_values!=[.5,.5,.5]:return
            for condition,tokens,labels,values in proofs:
                for token,payout in zip(tokens,values):terminal[(condition,token)].append({'payout':payout,'observed_at':observed,'source':'GUAVA_GAMMA_RESOLVED','observer_config':cfg_hash})
        for row in allrows[key]:
            event=json.loads(row['event_json']);raw=raw_value(row['raw_gzip'],row['raw_sha256']);run=runs[row['run_id']];title=raw.get('title') or title;stats['event_rows']+=1
            if raw!=event['raw']:raise ValueError('event_projection_raw_mismatch')
            if not row['eligible']:
                record_terminal(raw,row['observed_at'],run['success'])
                groups.append(grid.Group(ts(row['observed_at']),row['run_id'],[],False));stats['excluded_event_group']+=1;continue
            try:slots,markets=roles(event)
            except (ValueError,KeyError,TypeError) as err:
                groups.append(grid.Group(ts(row['observed_at']),row['run_id'],[],False));stats['unproven_roles_or_mapping']+=1;continue
            mapping={s['token_id']:(s['condition_id'],slots[s['token_id']]) for s in event['side_definitions']}
            if immutable_map is None:
                immutable_map=mapping;immutable_labels={side['token_id']:side['outcome_label'] for side in event['side_definitions']}
            if mapping!=immutable_map:raise ValueError('guava_event_token_identity_drift')
            expected=set(json.loads(row['expected_token_ids_json']));bs=bookrows[(row['run_id'],eid)];snaps=[]
            for side in event['side_definitions']:
                token=side['token_id'];b=bs.get(token);stats['raw_rows']+=1;valid=bool(run['success'] and b and b['status']=='OK' and set(bs)==expected)
                raw_book=raw_value(b['raw_gzip'],b['raw_sha256']) if b else None
                observed=b['observed_at'] if b else row['observed_at'];receipt=requests.get((row['run_id'],b['request_id'])) if b else None
                if receipt:
                    received=ts(receipt['received_at']);began=ts(receipt['started_at']);observed_t=ts(observed)
                    request_payload=raw_value(receipt['payload_gzip'],receipt['payload_sha256'])
                    linked=[q for q in request_payload if str(q.get('asset_id'))==token] if isinstance(request_payload,list) else []
                    valid=valid and receipt['source']=='clob' and receipt['path']=='/books' and receipt['status']==200 and began is not None and received is not None and 0<=received-began<=15 and observed_t==received and len(linked)==1 and linked[0]==raw_book
                else:valid=False
                valid=valid and isinstance(raw_book,dict) and str(raw_book.get('asset_id'))==token and raw_book.get('market')==side['condition_id']
                asks=bids=();ask_state='INVALID'
                if raw_book is not None:
                    try:
                        asks,bids=grid.levels(raw_book,'asks'),grid.levels(raw_book,'bids')
                        if asks and bids and bids[0][0]>asks[0][0]+grid.EPS:raise ValueError('crossed')
                        ask_state='PRESENT_VALID' if asks else 'EMPTY_VALID'
                    except (ValueError,TypeError):valid=False
                rate=None
                if b:
                    f=json.loads(b['fee_evidence_json']);fr=f.get('raw',{});sched=fr.get('feeSchedule',{})
                    if f.get('source')=='gamma_current_receipt' and f.get('received_at')==event['observed_at'] and ts(f['received_at'])<=ts(observed):
                        if fr.get('feesEnabled') is False:rate=0.0
                        elif fr.get('feesEnabled') is True and isinstance(sched,dict) and sched.get('exponent')==1 and sched.get('takerOnly') is True:
                            r=number(sched.get('rate'))
                            if r is not None and 0<=r<=1:rate=r
                opened=bool(valid and side.get('tradable') is True and raw.get('live') is True and raw.get('ended') is False and raw.get('closed') is False)
                market=markets[side['condition_id']]
                gated=opened and (number(market.get('liquidity')) or 0)>=5000 and (number(market.get('volume')) or 0)>=5000
                mid=(asks[0][0]+bids[0][0])/2 if asks and bids else None;spread=asks[0][0]-bids[0][0] if asks and bids else None
                scheduled=next((ts(raw.get(k)) for k in ('startTime','gameStartTime','eventStartTime') if ts(raw.get(k)) is not None),None)
                age=(ts(observed)-scheduled)/60 if scheduled is not None else None
                snap=grid.Snap(token,side['condition_id'],slots[token],ts(observed),row['run_id'],minute(raw,sport),mid,asks,bids,bool(valid),opened,bool(gated),rate,spread,ask_state,'GUAVA_GAMMA_POINT_IN_TIME_DIRECT_ORDERING',grid.walk(asks,5,True),age)
                snaps.append(snap);stats['valid_rows' if valid else 'invalid_rows']+=1
                stats['point_fee_rows' if rate is not None else 'missing_point_fee_rows']+=1
            groups.append(grid.normalize_group(snaps,sport,False))
            record_terminal(raw,event['observed_at'],run['success'])
        failures=[(ts(r['started_at']),ts(r['occurred_at'])) for r in runs.values() if not r['success'] and r['config_hash']==cfg_hash and r['strategy_source_digest']==source_digest and ts(r['started_at'])<z]
        cohort=grid.visual.identifier([source['id'],cfg_hash,source_digest,sport])
        config={**cfg,'config_hash':cfg_hash,'strategy_source_digest':source_digest,'sport':sport,'guava_adapter':'direct-ordering-full-receipt-v1','entry_open_requires_in_play':True}
        events.append(grid.Event(source['id'],cohort,sport,eid,title,config,sorted(groups,key=lambda x:x.time),failures,dict(terminal)))
    db.close()
    if sha(path)!=digest or before!=(path.stat().st_size,path.stat().st_mtime_ns):raise ValueError('pin_changed')
    return events,{'source':source['id'],'path':str(path),'sha256':digest,'stats':dict(stats),'events':len(events),'notes':['Source/Gamma ordering required, no array role inference.','Postgame records retained but open/entry gate requires in-play.','Resolved Gamma one-hot/void with consistent soccer triad; no score payout substitution.']}


def sources():
    root=Path(__file__).resolve().parent.parent/'guava';result=[]
    for s in 'abcd':
        p=Path(json.loads((root/f'polybot-sim-guava-{s}-pins.json').read_text())[0]);m=json.loads(p.with_name('manifest.json').read_text())
        result.append({'id':f'polybot-sim-guava-{s}:guava-research-{s}-v1','strategy':'golden-guava','local_path':str(p),'local_sha256':m['sha256'],'pinned':True})
    return result
