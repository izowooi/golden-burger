"""Read-only normalized rows for the local Sports workbench."""
import gzip,hashlib,json,sqlite3,math
from collections import OrderedDict
from pathlib import Path
from datetime import datetime,timezone
CONTRACT='sports-price-recorder-1m-v1'
APPLICATION_ID=0x43535231

def _utc(value):
    if value is None:return None
    dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if dt.tzinfo is None:raise ValueError('timezone required')
    return dt.astimezone(timezone.utc)

def file_sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def open_verified(path,expected_sha=None):
    path=Path(path).resolve()
    if expected_sha and file_sha(path)!=expected_sha:raise ValueError('pinned database SHA mismatch')
    c=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
    row=c.execute('SELECT data_contract FROM collection_contracts').fetchone()
    if not row or row[0]!=CONTRACT or c.execute('PRAGMA application_id').fetchone()[0]!=APPLICATION_ID:
        c.close();raise ValueError('not a recorder source')
    return c

def _valid_book(raw,token):
    if not isinstance(raw,dict) or str(raw.get('asset_id'))!=token:return False
    for side in ('bids','asks'):
        if not isinstance(raw.get(side),list):return False
        for level in raw[side]:
            if not isinstance(level,dict):return False
            try:
                if any(isinstance(level[k],bool) for k in ('price','size')):return False
                price,size=float(level['price']),float(level['size'])
                if not math.isfinite(price) or not math.isfinite(size) or not 0<price<=1 or size<=0:return False
            except (KeyError,ValueError,TypeError):return False
    return True


def _valid_identity(event,slots,family):
    if not isinstance(slots,list) or len(slots)!=(6 if family=='soccer' else 2):return False
    if len({s.get('token_id') for s in slots})!=len(slots):return False
    conditions={s.get('condition_id') for s in slots};markets=event.get('markets')
    if not isinstance(markets,list) or len(conditions)!=(3 if family=='soccer' else 1):return False
    for cid in conditions:
        group=[s for s in slots if s.get('condition_id')==cid]
        matches=[m for m in markets if isinstance(m,dict) and str(m.get('conditionId') or m.get('condition_id'))==cid]
        if len(group)!=2 or len(matches)!=1:return False
        m=matches[0]
        try:
            get=lambda key:json.loads(m[key]) if isinstance(m[key],str) else m[key]
            tokens,labels=get('clobTokenIds'),get('outcomes')
            if not all(isinstance(x,list) and len(x)==2 for x in (tokens,labels)) or len(set(tokens))!=2 or len(set(labels))!=2:return False
            if dict(zip(tokens,labels))!={s['token_id']:s['outcome'] for s in group}:return False
            if family=='soccer' and set(labels)!={'Yes','No'}:return False
            if m.get('sportsMarketType')!='moneyline':return False
        except (KeyError,ValueError,TypeError):return False
    return True


def iter_rows(path,expected_sha=None,*,start=None,end=None,include_depth=True):
    c=open_verified(path,expected_sha)
    lower,upper=_utc(start),_utc(end)
    query='''SELECT b.*,e.family,e.league,e.season_phase,e.metadata_status,e.received_at AS metadata_received,
      e.request_id AS metadata_request_id,e.slots_json AS expected_slots_json,e.event_json,e.clock_json,e.identity_valid,e.window_status,e.scheduled_start,e.end_anchor,e.end_basis,
      e.terminal_json,e.lifecycle_state,r.slot_utc,r.reference_at,r.published_at,r.status AS run_status,r.config_json
      FROM book_observations b JOIN event_observations e ON e.run_id=b.run_id AND e.event_id=b.event_id
      JOIN cycles r ON r.run_id=b.run_id ORDER BY COALESCE(b.received_at,r.reference_at),b.event_id,b.slot'''
    cache=OrderedDict()
    def request(run,identity):
        key=(run,identity)
        if key in cache:
            cache.move_to_end(key);return cache[key]
        rows=c.execute('SELECT * FROM requests WHERE run_id=? AND request_id=?',(run,identity)).fetchall()
        valid=[dict(x) for x in rows if x['status']=='SUCCESS' and x['raw_complete']==1 and x['http_status']==200]
        result=None
        if len(valid)==1 and valid[0]['raw_gzip']:
            x=valid[0];body=gzip.decompress(x['raw_gzip'])
            if hashlib.sha256(body).hexdigest()==x['sha256']:result=(x,json.loads(body))
        cache[key]=result
        if len(cache)>32:cache.popitem(last=False)
        return result
    try:
        for row in c.execute(query):
            r=dict(row);at=r['received_at'] or r['reference_at']
            point=_utc(at);published=_utc(r['published_at'])
            if lower and point<lower or upper and (point>=upper or published>=upper):continue
            raw=None;valid=False
            if r['book_gzip']:
                body=gzip.decompress(r['book_gzip']);valid=hashlib.sha256(body).hexdigest()==r['book_sha256']
                if valid:
                    raw=json.loads(body);valid=_valid_book(raw,r['token_id'])
            event=json.loads(r['event_json'] or '{}');clock=json.loads(r['clock_json'] or '{}');config=json.loads(r['config_json'])
            market_rows=event.get('markets');market_rows=market_rows if isinstance(market_rows,list) else []
            market=next((m for m in market_rows if isinstance(m,dict) and str(m.get('conditionId') or m.get('condition_id'))==r['condition_id']),{})
            point_identity=False
            metadata=request(r['run_id'],r['metadata_request_id']);quote=request(r['run_id'],r['request_id'])
            if valid and metadata and quote:
                mr,mp=metadata;qr,qp=quote
                candidates=mp.get('events',[]) if isinstance(mp,dict) and 'events' in mp else [mp]
                exact=[x for x in candidates if isinstance(x,dict) and str(x.get('id'))==r['event_id']]
                slots=json.loads(r['expected_slots_json'])
                selected=[x for x in slots if x.get('slot')==r['slot'] and x.get('token_id')==r['token_id']
                          and x.get('condition_id')==r['condition_id'] and x.get('outcome')==r['outcome']]
                actual=[x for x in qp if isinstance(x,dict) and str(x.get('asset_id'))==r['token_id']] if isinstance(qp,list) else []
                points=[_utc(r['reference_at']),_utc(mr['started_at']),_utc(mr['received_at']),_utc(qr['started_at']),_utc(qr['received_at']),published]
                point_identity=bool(_valid_identity(event,slots,r['family']) and mr['request_kind'] in ('gamma_events_keyset','gamma_event_followup') and qr['request_kind']=='recorder_books' and len(exact)==1 and exact[0]==event and len(selected)==1 and len(actual)==1 and actual[0]==raw
                    and raw.get('market') in (None,r['condition_id']) and points==sorted(points)
                    and _utc(r['metadata_received'])==points[2] and _utc(r['requested_at'])==points[3] and _utc(r['received_at'])==points[4])
            bid=max((float(x['price']) for x in raw.get('bids',[])),default=None) if raw and valid else None
            ask=min((float(x['price']) for x in raw.get('asks',[])),default=None) if raw and valid else None
            yield {'id':r['run_id']+':'+r['event_id']+':'+r['slot'],'run_id':r['run_id'],'event_id':r['event_id'],
                'condition_id':r['condition_id'],'token_id':r['token_id'],'outcome':r['outcome'],'outcome_side':r['outcome'].upper() if r['family']=='soccer' and r['outcome'] else 'DIRECT',
                'result_kind':r['result_kind'],'verified_role':r['verified_role'],'verified_team_name':r['team_name'],
                'display_label':str(r['team_name'] or r['result_kind'] or market.get('groupItemTitle') or market.get('question') or r['condition_id'])+' '+str(r['outcome'] or ''),'request_id':r['request_id'],'timestamp':at,'requested_at':r['requested_at'],'received_at':r['received_at'],'published_at':r['published_at'],
                'slot':r['slot'],'reference_at':r['reference_at'],'job_name':config['job_name'],'slot_utc':r['slot_utc'],'sport_family':r['family'],'league':r['league'],'season_phase':r['season_phase'],
                'title':event.get('title'),'slug':event.get('slug'),'question':market.get('question'),'group_item_title':market.get('groupItemTitle'),'outcome_label':r['outcome'],
                'game_start':r['scheduled_start'],'end_anchor':r['end_anchor'],'end_basis':r['end_basis'],
                'best_bid':bid,'best_ask':ask,'midpoint':(bid+ask)/2 if bid is not None and ask is not None else None,
                'source_elapsed_minutes':None,'clock':clock,'book':raw if include_depth else None,
                'book_status':r['status'],'raw_book_valid':bool(valid),'raw_point_in_time_identity_proven':bool(point_identity and r['identity_valid'] and r['point_in_time_valid']),
                'run_status':r['run_status'],'config_hash':config['config_hash'],'strategy_source_digest':config['source_digest'],
                'config':config,'fee_evidence':json.loads(r['fee_json']),'market_fields':market,'fee_market':market,
                'window_status':r['window_status'],'lifecycle_state':r['lifecycle_state'],'source_contract':CONTRACT}
    finally:c.close()

def _terminal_proof(event,slots,family):
    markets=event.get('markets')
    if not isinstance(markets,list) or not isinstance(slots,list):return None
    if len(slots)!=(6 if family=='soccer' else 2):return None
    if len({s.get('token_id') for s in slots})!=len(slots):return None
    conditions={s.get('condition_id') for s in slots}
    if None in conditions or len(conditions)!=(3 if family=='soccer' else 1):return None
    proof=[];winners=[]
    for cid in sorted(conditions):
        group=[s for s in slots if s.get('condition_id')==cid]
        matches=[m for m in markets if isinstance(m,dict) and str(m.get('conditionId') or m.get('condition_id'))==cid]
        if len(group)!=2 or len(matches)!=1 or matches[0].get('closed') is not True:return None
        m=matches[0]
        try:
            get=lambda key:json.loads(m[key]) if isinstance(m[key],str) else m[key]
            tokens,labels,prices=get('clobTokenIds'),get('outcomes'),get('outcomePrices')
            if not all(isinstance(x,list) and len(x)==2 for x in (tokens,labels,prices)):return None
            if len(set(tokens))!=2 or len(set(labels))!=2:return None
            if dict(zip(tokens,labels))!={s['token_id']:s['outcome'] for s in group}:return None
            if any(isinstance(p,bool) for p in prices):return None
            payouts=[float(p) for p in prices]
            if not all(math.isfinite(p) for p in payouts):return None
            void=payouts==[.5,.5] and str(m.get('umaResolutionStatus') or '').lower()=='resolved'
            if payouts not in ([1.,0.],[0.,1.]) and not void:return None
            if family=='soccer':
                if set(labels)!={'Yes','No'}:return None
                winners.append(dict(zip(labels,payouts))['Yes'])
            proof.extend({'condition_id':cid,'token_id':t,'outcome':l,'payout':p} for t,l,p in zip(tokens,labels,payouts))
        except (KeyError,ValueError,TypeError):return None
    if family=='soccer' and sorted(winners) not in ([0.,0.,1.],[.5,.5,.5]):return None
    return {'source':'GAMMA_CLOSED_EXACT_TOKEN_PAYOUT','tokens':proof}


def iter_terminals(path,expected_sha=None,*,end=None):
    c=open_verified(path,expected_sha);upper=_utc(end)
    try:
        for row in c.execute('''SELECT e.*,r.reference_at,r.published_at,r.config_json FROM event_observations e JOIN cycles r USING(run_id)
          WHERE r.status='SUCCEEDED' AND e.identity_valid=1 AND e.terminal_json IS NOT NULL'''):
            try:
                published=_utc(row['published_at']);receipt=_utc(row['received_at'])
                if upper and (published>=upper or receipt>=upper):continue
                requests=c.execute('SELECT * FROM requests WHERE run_id=? AND request_id=?',(row['run_id'],row['request_id'])).fetchall()
                valid=[r for r in requests if r['status']=='SUCCESS' and r['http_status']==200 and r['raw_complete']==1
                       and r['request_kind'] in ('gamma_events_keyset','gamma_event_followup')]
                if len(valid)!=1 or not valid[0]['raw_gzip']:continue
                source=valid[0];body=gzip.decompress(source['raw_gzip'])
                if hashlib.sha256(body).hexdigest()!=source['sha256']:continue
                payload=json.loads(body);events=payload.get('events',[]) if isinstance(payload,dict) and 'events' in payload else [payload]
                exact=[e for e in events if isinstance(e,dict) and str(e.get('id'))==row['event_id']]
                event=json.loads(row['event_json']);proof=json.loads(row['terminal_json'])
                points=[_utc(row['reference_at']),_utc(source['started_at']),_utc(source['received_at']),published]
                if any(p is None for p in points) or points!=sorted(points) or receipt!=points[2]:continue
                computed=_terminal_proof(event,json.loads(row['slots_json']),row['family'])
                canonical=lambda p:json.dumps(p,sort_keys=True)
                if len(exact)!=1 or exact[0]!=event or not computed:continue
                if proof.get('source')!=computed['source'] or sorted(map(canonical,proof.get('tokens',[])))!=sorted(map(canonical,computed['tokens'])):continue
            except (KeyError,ValueError,TypeError,json.JSONDecodeError):continue
            for token in computed['tokens']:
                yield {**token,'event_id':row['event_id'],'run_id':row['run_id'],'observed_at':row['received_at'],
                       'published_at':row['published_at'],'source_contract':CONTRACT,
                       'config_hash':json.loads(row['config_json'])['config_hash'],
                       'strategy_source_digest':json.loads(row['config_json'])['source_digest'],
                       'observation_mode':json.loads(row['config_json'])['observation_mode'],
                       'job_name':json.loads(row['config_json'])['job_name']}
    finally:c.close()


def carryovers(path,expected_sha=None):
    c=open_verified(path,expected_sha)
    try:
        for row in c.execute('SELECT * FROM registry_carryovers'):
            r=dict(row)
            if hashlib.sha256(r['state_json'].encode()).hexdigest()!=r['source_state_sha256']:raise ValueError('carryover state hash mismatch')
            if Path(r['source_shard']).name!=r['source_shard']:raise ValueError('unsafe carryover source')
            yield r
    finally:c.close()
