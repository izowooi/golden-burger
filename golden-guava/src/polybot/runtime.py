"""One bounded, accountless research invocation. Live wiring is separate."""
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
import fcntl
import hashlib
import logging
import os
from pathlib import Path
from uuid import uuid4

from .book import depth_metrics
from .budget import Budget
from .evidence import Repository
from .hypotheses import compute
from .identity import extract_event
from .public_clients import PublicClients
from .streams import collect_market_window
from .workspace import verify_workspace

log=logging.getLogger(__name__)


def utc(value=None):return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
def parsed(value):return datetime.fromisoformat(value.replace('Z','+00:00'))
def shard_for(event_id):return int.from_bytes(hashlib.sha256(str(event_id).encode()).digest()[:8],'big')%4


def _news_context(current,previous):
    def key(row):return (row.get('provider'),row.get('league_code'),row.get('provider_game_id'))
    old={key(row):row for row in previous}
    comparable=[];changed=[]
    for row in current:
        prior=old.get(key(row))
        if not prior:continue
        values=[r.get(side,{}).get('score') for r in (row,prior) for side in ('home','away')]
        if any(v is None for v in values):continue
        comparable.append(str(key(row)))
        if values[:2]!=values[2:]:changed.append(str(key(row)))
    return {'comparable_games':len(comparable),'score_update_games':len(changed),
        'changed_game_keys':changed,'scope':'requested_provider_leagues_only',
        'continuous_news_coverage_proven':False}


def _terminal(raw,expected_tokens):
    import json
    markets=[];covered=set()
    for market in raw.get('markets',[]):
        tokens=market.get('clobTokenIds',[])
        try:tokens=json.loads(tokens) if isinstance(tokens,str) else tokens
        except (ValueError,TypeError):return False
        if not isinstance(tokens,list):return False
        if set(tokens)&set(expected_tokens):markets.append(market);covered.update(tokens)
    if covered!=set(expected_tokens):return False
    if not markets:return False
    for market in markets:
        if market.get('closed') is not True:return False
        try:
            prices=market.get('outcomePrices',[])
            prices=json.loads(prices) if isinstance(prices,str) else prices
            values=[float(x) for x in prices]
        except (ValueError,TypeError):return False
        if not (sorted(values)==[0.0,1.0] or values==[.5,.5]):return False
    return True


def run_research(config,*,now=None,client_factory=PublicClients,news_factory=None,stream_reader=collect_market_window):
    if not config.simulation_mode or config.trading.lifecycle_mode!='archive_only':
        raise ValueError('research runtime never manages real orders')
    now=now or datetime.now(timezone.utc)
    if now<parsed(config.trading.study_start_utc):return {'skipped':True,'reason':'before_research_window'}
    if now>=parsed(config.trading.study_end_utc)+timedelta(days=7):return {'skipped':True,'reason':'after_followup_window'}
    budget=Budget(config.trading.cycle_budget_seconds,margin=config.trading.network_stop_margin_seconds)
    workspace=verify_workspace(config,os.environ.get('WORKSPACE',str(config.expected_workspace)))
    config.db_path.parent.mkdir(parents=True,exist_ok=True)
    with (config.db_path.parent/'.guava-writer.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'skipped':True,'reason':'single_writer_busy'}
        repo=Repository(config.db_path,config.public_snapshot(),budget=budget)
        client=None;news=None;run_id=uuid4().hex;phase='slot';started=False
        try:
            latest=repo.status()['latest_run']
            slot=now.replace(second=0,microsecond=0)
            if latest and parsed(latest['started_at'])>=slot:
                return {'skipped':True,'reason':'slot_already_claimed_or_clock_reversed','owner_run_id':latest['run_id']}
            repo.start_run(run_id,utc(now),config.public_snapshot())
            started=True
            sink=lambda receipt,payload:repo.record_request(run_id,receipt,payload)
            client=client_factory(asdict(config.trading),budget,sink)
            phase='census';events={};sweeps=[]
            previous_summary=repo.latest_summary()
            tracking=dict(repo.tracking_obligations())
            if now<parsed(config.trading.study_end_utc):
                for family in config.trading.sport_families:
                    budget.require()
                    raw_events,sweep=client.fetch_events(family);sweeps.append(sweep)
                    if not sweep.get('cursor_complete'):raise RuntimeError('incomplete '+family+' census')
                    for raw in raw_events:
                        if shard_for(raw['id'])!=config.spec.shard:continue
                        observed=raw.get('_guava',{}).get('observed_at') or utc()
                        view=extract_event(raw,family,observed);view['cohort_key']=config.cohort_key
                        if view['event_id'] in events:raise ValueError('event crossed sport partitions')
                        events[view['event_id']]=view
                        if view['eligible']:
                            existing=tracking.get(view['event_id'],{})
                            if existing.get('expected_token_ids') not in (None,view['expected_token_ids']):
                                raise RuntimeError('tracked token identity changed')
                            tracking[view['event_id']]={**existing,'sport_family':family,'last_live_at':utc(now),
                                'next_due_at':utc(now),'attempts':0,'consecutive_failures':0,
                                'origin_cohort':existing.get('origin_cohort',config.cohort_key),
                                'expected_token_ids':view['expected_token_ids']}
            phase='tracked_followup';due=[]
            for event_id,entry in tracking.items():
                if event_id not in events and parsed(entry['next_due_at'])<=now:due.append((entry['next_due_at'],event_id))
            for _,event_id in sorted(due)[:config.trading.followup_batch_limit]:
                if budget.require()<15:break
                entry=tracking[event_id];result=client.fetch_event(event_id,family=entry['sport_family'])
                entry['attempts']+=1
                if result['status']!='OK':
                    entry['consecutive_failures']=entry.get('consecutive_failures',0)+1
                    entry['next_due_at']=utc(now+timedelta(seconds=min(300,60*entry['consecutive_failures'])))
                    continue
                view=extract_event(result['raw'],entry['sport_family'],result['observed_at'],allow_postgame=True)
                if view['event_id']!=event_id:raise ValueError('followup event identity mismatch')
                # H5 needs the first post-result quote. Successful follow-up
                # returns at the next UTC minute, even if the next Jenkins run
                # starts earlier within its minute. Receipts keep actual times.
                entry['consecutive_failures']=0
                entry['next_due_at']=utc(slot+timedelta(seconds=config.trading.cadence_seconds))
                view['cohort_key']=config.cohort_key;events[event_id]=view
                if view['eligible'] and entry.get('expected_token_ids') not in (None,view['expected_token_ids']):
                    raise RuntimeError('followup token identity changed')
                if _terminal(result['raw'],entry.get('expected_token_ids',[])):tracking.pop(event_id)
            # Put six-token groups before two-token groups; the default 240
            # batch boundary then never splits one event's direct partition.
            selected=sorted(events.values(),key=lambda e:(e['sport_family']!='soccer',e['event_id']))
            tokens=[t for e in selected for t in e['expected_token_ids']]
            if len(set(tokens))!=len(tokens):raise ValueError('token spans multiple event identities')
            if len(tokens)>config.trading.max_tokens_per_cycle:raise RuntimeError('token budget requires new shard plan, not partial success')
            # Acquire knowledge before the book used to describe a possible
            # response. A pre-news ask is not an executable post-news discount.
            phase='independent_news'
            if news_factory is None:
                from .official_news import OfficialNews
                news_factory=OfficialNews
            news=news_factory(budget,sink)
            news_result=news.fetch_for_events([e for e in selected if e['eligible']],now)
            context=_news_context(news_result.get('context',[]),previous_summary.get('news_context',[]))
            for event in selected:
                event['official_news']=news_result.get('by_event',{}).get(event['event_id'])
                event['guava_news_context']=context
            phase='books';raw_books=client.fetch_books(tokens) if tokens else {}
            if set(raw_books)!=set(tokens):raise RuntimeError('book attempt coverage incomplete')
            books=[]
            for event in selected:
                markets={str(m.get('conditionId') or m.get('condition_id')):m for m in event['raw'].get('markets',[])}
                for side in event['side_definitions']:
                    record={**side,**raw_books[side['token_id']],'event_id':event['event_id']}
                    record['observed_at']=record.get('observed_at') or utc()
                    market=markets[side['condition_id']]
                    record['fee_evidence']={'source':'gamma_current_receipt','received_at':event['observed_at'],
                        'raw':{k:market[k] for k in ('feesEnabled','feeSchedule','feeRateBps','makerBaseFee','takerBaseFee') if k in market},
                        'actual_fill_fee_proven':False}
                    record['depth_metrics']=[]
                    if isinstance(record.get('raw'),dict):
                        try:record['depth_metrics']=depth_metrics(record['raw'],config.trading.depth_ladder_usdc,config.trading.fee_stress_rates)
                        except ValueError as error:record['depth_error']=str(error)
                    books.append(record)
            phase='features';prior=repo.previous_events(list(events));features=[];observed=utc()
            for event in selected:
                features.extend(compute(event,raw_books,prior.get(event['event_id']),observed_at=observed,
                    ladder=config.trading.depth_ladder_usdc,rates=config.trading.fee_stress_rates))
            phase='public_stream';stream=stream_reader(tokens,budget,sink)
            summary={'strategy_name':'golden-guava','job_name':config.job_name,'mode':'sim','run_id':run_id,
                'census_complete':all(x.get('cursor_complete') for x in sweeps),'sweeps':sweeps,
                'event_count':len(selected),'eligible_events':sum(e['eligible'] for e in selected),
                'book_attempts':len(books),'book_observed':sum(isinstance(b.get('raw'),dict) for b in books),
                'feature_count':len(features),'tracking':tracking,
                'news_context':news_result.get('context',[]),'news_errors':news_result.get('errors',[]),
                'stream_status':stream['status'],'stream_messages':len(stream.get('messages',[])),
                'complete_trade_tape':False,'actual_orders_submitted':0,'workspace':workspace,
                'git_commit':os.environ.get('GIT_COMMIT','unknown'),
                'precommit_elapsed_seconds':budget.elapsed,'source_digest':config.strategy_source_digest}
            phase='publication';budget.require_commit()
            repo.publish_cycle(run_id,observed,selected,books,features,summary)
            summary['wall_elapsed_seconds']=budget.elapsed
            log.info('research committed run=%s events=%d books=%d elapsed=%.3fs',run_id,len(selected),len(books),budget.elapsed)
            return summary
        except BaseException as error:
            if started:repo.fail_run(run_id,utc(),type(error).__name__,phase)
            raise
        finally:
            if news is not None:news.close()
            if client is not None:client.close()
            repo.close()
