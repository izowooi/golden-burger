"""Predeclared descriptive features. These do not select a live rule or claim P&L."""
from datetime import datetime
from decimal import Decimal
from .book import number,levels,walk,fee_stress
from .official_news import derive_expected_payouts


def _elapsed(before,after):
    try:return (datetime.fromisoformat(after.replace('Z','+00:00'))-datetime.fromisoformat(before.replace('Z','+00:00'))).total_seconds()
    except (ValueError,TypeError,AttributeError):return None


def compute(event,books,previous,*,observed_at,ladder=(5,10,25,50,100),rates=(0,.03,.05)):
    """Return all four hypotheses, including non-applicable/missing cases."""
    event_id=event['event_id'];rows=[]
    def emit(name,applicable,reason,metrics):
        rows.append({'event_id':event_id,'hypothesis_id':name,'observed_at':observed_at,
            'applicable':applicable,'reason':reason,'metrics':metrics})
    sides=event.get('side_definitions',[])
    quotes={};errors={}
    for side in sides:
        token=side['token_id'];entry=books.get(token,{})
        raw=entry.get('raw')
        try:
            if not isinstance(raw,dict):raise ValueError('missing raw book')
            bids,asks=levels(raw.get('bids'),bids=True),levels(raw.get('asks'))
            if not bids or not asks:raise ValueError('empty book side')
            if bids[0][0]>asks[0][0]:raise ValueError('crossed book')
            quotes[token]={'bid':bids[0][0],'ask':asks[0][0],
                'mid':(bids[0][0]+asks[0][0])/2,'raw':raw,'received_at':entry.get('observed_at')}
        except ValueError as error:errors[token]=str(error)
    partitions={}
    for side in sides:
        partitions.setdefault(side['condition_id'],{})[str(side['outcome_side']).upper()]=side['token_id']
    complete_six=(event.get('sport_family')=='soccer' and len(partitions)==3
        and all(set(x)=={'YES','NO'} for x in partitions.values()) and len(quotes)==6)
    h1=[]
    if complete_six:
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
    emit('H1',complete_six,'conditional_payoff_gap_only' if complete_six else 'incomplete_direct_six',
         {'comparisons':h1,'book_errors':errors,'void_cancel_rule_risk':True})

    previous=previous or {};gap=_elapsed(previous.get('observed_at'),observed_at)
    previous_signal=previous.get('guava_signal_state',{})
    prior_mid=previous_signal.get('midpoints',{})
    changes={token:str(q['mid']-number(prior_mid[token])) for token,q in quotes.items() if token in prior_mid} if gap is not None and 0<gap<=150 else {}
    news=event.get('official_news') or {};old_news=previous.get('official_news') or {}
    same_news=(news.get('matching_evidence') and old_news.get('matching_evidence')
        and news.get('provider_game_id')==old_news.get('provider_game_id')
        and news.get('provider')==old_news.get('provider'))
    score=[news.get(x,{}).get('score') for x in ('home','away')] if news else None
    prior_score=[old_news.get(x,{}).get('score') for x in ('home','away')] if same_news else None
    score_observed=score is not None and prior_score is not None
    changed=score_observed and score!=prior_score
    emit('H2',bool(changes) and bool(same_news) and score_observed,'provider_score_change_interval' if changed else 'no_verified_goal_news',
        {'observed_score_change':changed,'previous_score':prior_score,'score':score,
         'midpoint_changes':changes,'observation_gap_seconds':gap,
         'independent_goal_or_var_verified':False,'separate_score_provider_matched':bool(same_news),
         'psychological_causation_proven':False})
    times=[q['received_at'] for q in quotes.values() if q['received_at']]
    span=_elapsed(min(times),max(times)) if times else None
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
    prior_payout=previous_signal.get('expected_payouts',{})
    confirmed=(payout['status']=='OK')
    consistent=(confirmed and same_news and prior_payout==payout['token_payouts'] and gap is not None and 0<gap<=150)
    confirmations=(int(previous_signal.get('result_confirmations',0))+1) if consistent else int(confirmed)
    discounted=[]
    if confirmed:
        for token,expected in payout['token_payouts'].items():
            if expected!=1 or token not in quotes:continue
            side=next(s for s in sides if s['token_id']==token)
            for amount in ladder:
                buy=walk(quotes[token]['raw']['asks'],amount)
                qty=number(buy['shares']);cost=number(buy['notional_usdc'])
                discounted.append({'token_id':token,'target_usdc':amount,'full_depth':buy['complete'],
                    'shares':buy['shares'],'ask_vwap':buy['vwap'],'source_tradable':side.get('tradable') is True,
                    'expected_payout_basis':payout['basis'],'actual_venue_resolution_proven':False,
                    'gross_discount_per_share':str(1-cost/qty) if buy['complete'] and qty>0 else None,
                    'fee_stress_discount':[{'rate':r,'exponent':1,'discount':str(1-(cost+fee_stress(buy,r))/qty)
                        if buy['complete'] and qty>0 else None,'actual_fee_proven':False} for r in rates]})
    emit('H5',confirmed,payout['reason'],{'provider_result':payout,'consecutive_confirmations':confirmations,
        'discounts':discounted,'source_final_flag':news.get('final'),
        'result_scope':news.get('result_scope'),'settlement_rules_still_require_verification':True,
        'profitability_or_live_promotion_proven':False})
    event['guava_signal_state']={'midpoints':{t:str(q['mid']) for t,q in quotes.items()},
        'spreads':spreads,'observed_at':observed_at,'expected_payouts':payout['token_payouts'],
        'result_confirmations':confirmations}
    return rows
