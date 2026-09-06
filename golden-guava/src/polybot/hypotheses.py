"""Descriptive receipt-aligned features, never a live rule or fill/P&L proof.

Research timing policy v2: request starts 0..30s after news receipt (or explicit
result_ready_at), request duration <=15s, local book-receipt age <=30s, news age
<=45s. Earlier/current news receipts must be distinct and 0..90s apart for H2.
All four current receipt intervals <=2s defines a separate strict_2s stratum;
slower ordered observations remain included inside the research envelope.
These are collection bounds for a 45s cycle, NOT live/fresh-price gates.
All times must be supplied, timezone-aware receipts. A provider's source/book
timestamp is NOT substituted for a local receipt. Receipt order does not prove
an uncached provider response, exact goal time, causal reaction or execution.
"""
from datetime import datetime, timezone
from decimal import Decimal
from .book import number,levels,walk,fee_stress
from .official_news import derive_expected_payouts


TIMING_VERSION='guava-receipt-timing-v2'
TIMING_LIMITS={'news_to_book_start_seconds':30.0,'book_request_seconds':15.0,
    'book_age_at_evaluation_seconds':30.0,'news_age_at_evaluation_seconds':45.0,
    'news_interval_seconds':90.0}


def _time(value):
    if not isinstance(value,str):return None
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError,TypeError,OverflowError):return None


def _elapsed(before,after):
    before,after=_time(before),_time(after)
    return (after-before).total_seconds() if before is not None and after is not None else None


def _text(value):return isinstance(value,str) and bool(value.strip())


def _news_identity(event,news):
    if not isinstance(news,dict):return None
    proof=news.get('matching_evidence')
    if (not isinstance(proof,dict) or not proof or news.get('valid_for_matching') is not True
            or news.get('source_coverage_complete') is not True or not _text(news.get('request_id'))
            or _time(news.get('received_at')) is None):return None
    if any(proof.get(key)!=event.get(key) for key in ('sport_family','league_code')):return None
    if proof.get('venue_event_id')!=event.get('event_id'):return None
    if any(news.get(key)!=event.get(key) for key in ('sport_family','league_code')):return None
    home,away=news.get('home'),news.get('away')
    if not isinstance(home,dict) or not isinstance(away,dict):return None
    values=(news.get('provider'),news.get('provider_game_id'),news.get('sport_family'),news.get('league_code'),
        home.get('team_id'),away.get('team_id'),news.get('result_scope'))
    if not all(_text(value) for value in values) or home['team_id']==away['team_id']:return None
    scheduled=_time(news.get('scheduled_at'))
    if scheduled is None:return None
    if 'started_at' in news:
        duration=_elapsed(news['started_at'],news['received_at'])
        if duration is None or duration<0:return None
    return (*values,scheduled)


def _receipt(entry):
    entry=entry if isinstance(entry,dict) else {}
    # PublicClients.observed_at is the HTTP body receipt, not feature time.
    return {'status':entry.get('status'),'request_id':entry.get('request_id'),
        'started_at':entry.get('started_at'),'received_at':entry.get('observed_at')}


def _book_timing(receipt,news,evaluated_at):
    news=news if isinstance(news,dict) else {}
    anchor=news.get('result_ready_at') if 'result_ready_at' in news else news.get('received_at')
    result={**receipt,'valid':False,'reason':None,'news_request_id':news.get('request_id'),
        'news_received_at':news.get('received_at'),'evaluated_at':evaluated_at,
        'ordering_anchor_at':anchor,'ordering_anchor_basis':
        'explicit_result_ready_at' if 'result_ready_at' in news else 'news_body_receipt_only',
        'news_to_book_start_seconds':_elapsed(anchor,receipt.get('started_at')),
        'book_request_seconds':_elapsed(receipt.get('started_at'),receipt.get('received_at')),
        'book_age_at_evaluation_seconds':_elapsed(receipt.get('received_at'),evaluated_at),
        'news_age_at_evaluation_seconds':_elapsed(news.get('received_at'),evaluated_at),
        'source_cache_freshness_proven':False,'strict_2s':False,'latency_stratum':'INVALID_RECEIPT'}
    def fail(reason):result['reason']=reason;return result
    if receipt.get('status')!='OK':return fail('book_status_not_ok')
    if not _text(receipt.get('request_id')) or not _text(news.get('request_id')):return fail('request_identity_missing')
    anchor_delay=_elapsed(news.get('received_at'),anchor)
    if anchor_delay is None or anchor_delay<0:return fail('news_receipt_or_ready_time_invalid')
    checks=('news_to_book_start_seconds','book_request_seconds','book_age_at_evaluation_seconds','news_age_at_evaluation_seconds')
    if any(result[key] is None for key in checks):return fail('missing_or_unzoned_receipt_time')
    if result['news_to_book_start_seconds']<0:return fail('book_started_before_news_anchor')
    if any(result[key]<0 for key in checks):return fail('reversed_or_future_receipt_time')
    for key in checks:
        if result[key]>TIMING_LIMITS[key]:
            result['latency_stratum']='OUTSIDE_RESEARCH_ENVELOPE'
            return fail(key+'_exceeded')
    strict=all(result[key]<=2.0 for key in checks)
    result.update(valid=True,reason='ordered_receipts_within_research_envelope',strict_2s=strict,
        latency_stratum='STRICT_2S_RECEIPTS' if strict else 'DELAYED_ORDERED_RECEIPTS')
    return result


def _score(news):
    if not isinstance(news,dict):return None
    values=[news.get(role,{}).get('score') for role in ('home','away')]
    return values if all(type(value) is int and value>=0 for value in values) else None


def _result_history(confirmed,payout,news,previous_signal,same_news,news_gap):
    if not confirmed:return 0,None
    first=news['received_at'];count=1
    prior_count=previous_signal.get('result_confirmations')
    prior_first=previous_signal.get('first_result_news_received_at')
    prior_receipt=previous_signal.get('result_news_received_at')
    same=(same_news and previous_signal.get('timing_version')==TIMING_VERSION
          and previous_signal.get('expected_payouts')==payout.get('token_payouts')
          and type(prior_count) is int and prior_count>0)
    if same:
        span=_elapsed(prior_first,prior_receipt)
        since=_elapsed(prior_receipt,news['received_at'])
        if span is not None and span>=0 and since is not None and since>=0:first=prior_first
        if (news['request_id']==previous_signal.get('result_news_request_id')
                and _time(news['received_at'])==_time(prior_receipt)):
            count=prior_count  # Replaying a receipt is not another confirmation.
        elif (news['request_id']!=previous_signal.get('result_news_request_id')
                and news_gap is not None and 0<news_gap<=TIMING_LIMITS['news_interval_seconds']):
            count=prior_count+1
    return count,first


def compute(event,books,previous,*,observed_at,ladder=(5,10,25,50,100),rates=(0,.03,.05)):
    """Return H1..H5, retaining explicit missingness and per-token time failures."""
    event_id=event['event_id'];rows=[]
    def emit(name,applicable,reason,metrics):
        rows.append({'event_id':event_id,'hypothesis_id':name,'observed_at':observed_at,
            'applicable':applicable,'reason':reason,'metrics':metrics})
    sides=event.get('side_definitions',[])
    quotes={};valid_books={};errors={};receipts={}
    for side in sides:
        token=side['token_id'];entry=books.get(token,{})
        entry=entry if isinstance(entry,dict) else {}
        receipts[token]=_receipt(entry)
        raw=entry.get('raw')
        try:
            if entry.get('status')!='OK':raise ValueError('book status is not OK')
            if not isinstance(raw,dict):raise ValueError('missing raw book')
            if 'asset_id' in raw and raw['asset_id']!=token:raise ValueError('raw asset_id mismatch')
            if 'market' in raw and raw['market']!=side['condition_id']:raise ValueError('raw market condition mismatch')
            identity_basis=('asset_id_and_market' if 'asset_id' in raw and 'market' in raw else
                'asset_id_only' if 'asset_id' in raw else 'market_only' if 'market' in raw else
                'metadata_only_unverified_raw_identity')
            receipts[token]['raw_identity_basis']=identity_basis
            asks=levels(raw.get('asks'))
            bids=levels(raw['bids'],bids=True) if raw.get('bids') is not None else []
            if not asks:raise ValueError('empty book ask')
            if bids and bids[0][0]>asks[0][0]:raise ValueError('crossed book')
            valid_books[token]={'raw':raw,'raw_identity_basis':identity_basis}
            # H5 holds against an expected terminal result. An absent current bid
            # is not evidence that the displayed ask is absent; other hypotheses
            # still require two-sided quotes and no midpoint is fabricated.
            if not bids:
                errors[token]='empty or missing book bid'
                continue
            quotes[token]={'bid':bids[0][0],'ask':asks[0][0],
                'mid':(bids[0][0]+asks[0][0])/2,'raw':raw,'received_at':entry.get('observed_at')}
        except ValueError as error:errors[token]=str(error)
    partitions={}
    for side in sides:
        partitions.setdefault(side['condition_id'],{})[str(side['outcome_side']).upper()]=side['token_id']
    complete_six=(event.get('sport_family')=='soccer' and len(partitions)==3
        and all(set(x)=={'YES','NO'} for x in partitions.values()) and len(quotes)==6)
    h1=[]
    comparable=event.get('h1_partition_comparable') is True
    if complete_six and comparable:
        for condition,partition in sorted(partitions.items()):
            token=partition['NO'];other=[x['YES'] for key,x in partitions.items() if key!=condition]
            for amount in ladder:
                buy=walk(quotes[token]['raw']['asks'],amount)
                sells=[walk(quotes[x]['raw']['bids'],buy['shares'],buy=False) for x in other] if number(buy['shares'])>0 else []
                full=buy['complete'] and len(sells)==2 and all(x['complete'] for x in sells)
                delta=sum((number(x['notional_usdc']) for x in sells),Decimal(0))-number(buy['notional_usdc'])
                h1.append({'condition_id':condition,'selected_no_token_id':token,'basket_yes_token_ids':other,
                    'target_usdc':amount,'complete_depth':full,'shares':buy['shares'],
                    'gross_gap_per_share':str(delta/number(buy['shares'])) if full else None,
                    'fee_stress_gap_per_share':[{'rate':r,'exponent':1,
                        'gap':str((delta-fee_stress(buy,r)-sum((fee_stress(s,r) for s in sells),Decimal(0)))/number(buy['shares'])) if full else None,
                        'basis':'hypothetical_conversion_cost_stress_not_executed_strategy_pnl'} for r in rates],
                    'payoff_relation':'conditional_normal_regulation_results',
                    'settlement_equivalence_verified':False,'atomic_multileg_execution_assumed':False,
                    'live_promotion_ready':False})
    emit('H1',complete_six and comparable,'incomplete_direct_six' if not complete_six else
         'partition_comparability_unproven' if not comparable else 'conditional_payoff_gap_only',
         {'comparisons':h1,'book_errors':errors,'void_cancel_rule_risk':True,
          'complete_direct_six':complete_six,'h1_partition_comparable':comparable})

    previous=previous or {}
    previous_signal=previous.get('guava_signal_state',{})
    prior_mid=previous_signal.get('midpoints',{})
    news=event.get('official_news') or {};old_news=previous.get('official_news') or {}
    identity=_news_identity(event,news);old_identity=_news_identity(previous,old_news)
    same_news=bool(identity is not None and identity==old_identity and event.get('event_id')==previous.get('event_id')
        and _text(event.get('cohort_key')) and event.get('cohort_key')==previous.get('cohort_key'))
    gap=_elapsed(old_news.get('received_at'),news.get('received_at'))
    distinct_news=bool(same_news and news.get('request_id')!=old_news.get('request_id')
        and gap is not None and 0<gap<=TIMING_LIMITS['news_interval_seconds'])
    timings={token:_book_timing(receipt,news,observed_at) for token,receipt in receipts.items()}
    for token,timing in timings.items():
        if identity is None:timing.update(valid=False,reason='matched_news_identity_missing')
        elif token not in valid_books:timing.update(valid=False,reason=errors.get(token,'book_data_missing'))
    baseline={};changes={}
    prior_receipts=previous_signal.get('quote_receipts',{})
    if distinct_news and previous_signal.get('timing_version')==TIMING_VERSION and isinstance(prior_receipts,dict):
        for token,q in quotes.items():
            receipt=prior_receipts.get(token)
            if not isinstance(receipt,dict):continue
            check=_book_timing(receipt,old_news,previous_signal.get('observed_at'));baseline[token]=check
            # A prior post-news quote is only a pre-*current receipt* baseline;
            # it may already be inside the true, unobserved score-change interval.
            before_current=_elapsed(receipt.get('received_at'),news.get('started_at',news.get('received_at')))
            if before_current is None or before_current<=0:check.update(valid=False,reason='baseline_not_before_current_news_request_or_receipt')
            if receipt.get('request_id')==receipts[token].get('request_id'):check.update(valid=False,reason='reused_book_receipt')
            if check['valid'] and timings[token]['valid'] and token in prior_mid:
                try:
                    prior_value=number(prior_mid[token])
                    if not 0<prior_value<=1:raise ValueError('invalid prior midpoint')
                    changes[token]=str(q['mid']-prior_value)
                except ValueError:check.update(valid=False,reason='invalid_prior_midpoint')
    score=_score(news) if identity is not None else None
    prior_score=_score(old_news) if same_news else None
    score_observed=score is not None and prior_score is not None
    changed=(score!=prior_score) if score_observed else None
    h2_reason=('news_identity_or_receipt_interval_unconfirmed' if not distinct_news else
        'missing_or_invalid_score' if not score_observed else 'no_observed_score_change' if not changed else
        'no_temporally_valid_baseline_pair' if not changes else 'post_receipt_score_change_interval_not_goal_time')
    emit('H2',bool(changes) and distinct_news and changed is True,h2_reason,
        {'observed_score_change':changed,'previous_score':prior_score,'score':score,
         'midpoint_changes':changes,'observation_gap_seconds':gap,
         'news_interval_start_received_at':old_news.get('received_at'),'news_interval_end_received_at':news.get('received_at'),
         'book_timing':timings,'baseline_timing':baseline,'timing_version':TIMING_VERSION,
         'timing_limits_seconds':dict(TIMING_LIMITS),'pre_goal_baseline_proven':False,'reversal_measured':False,
         'baseline_basis':'previous_post_receipt_book_not_proven_pre_score_change',
         'source_cache_freshness_proven':False,
         'independent_goal_or_var_verified':False,'separate_score_provider_matched':bool(same_news),
         'psychological_causation_proven':False})
    times=[_time(q['received_at']) for q in quotes.values() if _time(q['received_at']) is not None]
    span=(max(times)-min(times)).total_seconds() if times else None
    context=event.get('guava_news_context',{})
    emit('H3',bool(same_news and changes and context.get('comparable_games')),'observed_news_load_proxy_not_causal_attention',
        {'book_receipt_span_seconds':span,'midpoint_changes':changes,
         'reported_book_timestamps':{t:q['raw'].get('timestamp') for t,q in quotes.items()},
         'other_event_news_load':context,'participant_attention_measured':False})
    spreads={token:str(q['ask']-q['bid']) for token,q in quotes.items()}
    emit('H4',bool(quotes),'spread_description_not_maker_fill_evidence',
        {'spreads':spreads,'quote_stability':{t:abs(number(v))<=Decimal('.005') for t,v in changes.items()},
         'score_changed':changed,'queue_position_known':False,'maker_fill_assumed':False,
         'realized_pnl_usdc':None,'trade_tape_complete':False})
    payout=derive_expected_payouts(event,news)
    news_age=_elapsed(news.get('received_at'),observed_at)
    known=(news.get('regulation_result_known') is True and news.get('result_scope')=='REGULATION'
        if event.get('sport_family')=='soccer' else news.get('whole_game_result_known') is True
        and news.get('final') is True and news.get('result_scope')=='WHOLE_GAME')
    confirmed=bool(payout.get('status')=='OK' and identity is not None and known
        and payout.get('result_scope')==news.get('result_scope') and news_age is not None
        and 0<=news_age<=TIMING_LIMITS['news_age_at_evaluation_seconds'])
    confirmations,first_result=_result_history(confirmed,payout,news,previous_signal,same_news,gap)
    discounted=[]
    if confirmed:
        for token,expected in payout['token_payouts'].items():
            if type(expected) is not int or expected!=1 or token not in valid_books or not timings[token]['valid']:continue
            side=next(s for s in sides if s['token_id']==token)
            for amount in ladder:
                buy=walk(valid_books[token]['raw']['asks'],amount)
                qty=number(buy['shares']);cost=number(buy['notional_usdc'])
                discounted.append({'token_id':token,'target_usdc':amount,'full_depth':buy['complete'],
                    'raw_identity_basis':valid_books[token]['raw_identity_basis'],
                    'book_request_id':receipts[token]['request_id'],
                    'latency_stratum':timings[token]['latency_stratum'],'strict_2s':timings[token]['strict_2s'],
                    'shares':buy['shares'],'ask_vwap':buy['vwap'],'source_tradable':side.get('tradable') is True,
                    'expected_payout_basis':payout['basis'],'actual_venue_resolution_proven':False,
                    'gross_discount_per_share':str(1-cost/qty) if buy['complete'] and qty>0 else None,
                    'fee_stress_discount':[{'rate':r,'exponent':1,'discount':str(1-(cost+fee_stress(buy,r))/qty)
                        if buy['complete'] and qty>0 else None,'actual_fee_proven':False} for r in rates]})
    h5_applicable=confirmed and any(row['full_depth'] for row in discounted)
    emit('H5',h5_applicable,payout['reason'] if h5_applicable else
        'provider_result_or_news_receipt_unconfirmed' if not confirmed else 'no_ordered_full_depth_book_in_research_envelope',
        {'provider_result':payout,'consecutive_confirmations':confirmations,
        'first_result_news_received_at':first_result,'current_result_news_received_at':news.get('received_at'),
        'book_timing':timings,'timing_version':TIMING_VERSION,'timing_limits_seconds':dict(TIMING_LIMITS),
        'source_cache_freshness_proven':False,'execution_eligibility_proven':False,
        'ask_only_tokens':sorted(set(valid_books)-set(quotes)),'book_errors':errors,
        'discounts':discounted,'source_final_flag':news.get('final'),
        'result_scope':news.get('result_scope'),'settlement_rules_still_require_verification':True,
        'profitability_or_live_promotion_proven':False})
    event['guava_signal_state']={'midpoints':{t:str(q['mid']) for t,q in quotes.items()},
        'spreads':spreads,'observed_at':observed_at,'expected_payouts':payout['token_payouts'],
        'quote_receipts':receipts,'timing_version':TIMING_VERSION,
        'result_news_request_id':news.get('request_id') if confirmed else None,
        'result_news_received_at':news.get('received_at') if confirmed else None,
        'first_result_news_received_at':first_result,'result_confirmations':confirmations}
    return rows
