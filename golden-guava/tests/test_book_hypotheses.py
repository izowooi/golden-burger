from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from polybot import hypotheses
from polybot.book import walk,fee_stress,depth_metrics
from polybot.hypotheses import compute


def side(p,q):return {'price':str(p),'size':str(q)}


def test_same_shares_and_partial_depth():
    buy=walk([side('.2',10),side('.3',10)],5)
    assert buy['complete'] and Decimal(buy['shares'])==20
    sell=walk([side('.18',8),side('.1',4)],buy['shares'],buy=False)
    assert not sell['complete'] and Decimal(sell['remaining'])==8
    assert Decimal(sell['notional_usdc'])==Decimal('1.84')
    assert fee_stress(buy,'.05')==Decimal('.185')


@pytest.mark.parametrize('raw',[[side('NaN',10)],[side(0,10)],[side('.5',-1)],
    [side('.5',1),side('.50',2)],None])
def test_bad_book(raw):
    with pytest.raises(ValueError):walk(raw,5)


def fixture():
    event={'event_id':'e','sport_family':'soccer','league_code':'epl','cohort_key':'fixture-cohort',
           'h1_partition_comparable':True,'clock':{'score':'0-0'},'side_definitions':[]}
    books={}
    for i,yes in enumerate(('.5','.3','.2')):
        for outcome in ('YES','NO'):
            token=f'{i}-{outcome}';p=Decimal(yes) if outcome=='YES' else 1-Decimal(yes)
            event['side_definitions'].append({'token_id':token,'condition_id':str(i),'outcome_side':outcome})
            books[token]={'status':'OK','request_id':'fixture-books',
                'started_at':'2026-09-06T00:00:00.500000Z','observed_at':'2026-09-06T00:00:01Z','raw':{
                'bids':[side(p-Decimal('.01'),1000)],'asks':[side(p+Decimal('.01'),1000)]}}
    return event,books


def test_parity_is_not_free_profit_and_all_hypotheses_recorded():
    event,books=fixture()
    result=compute(event,books,None,observed_at='2026-09-06T00:00:02Z')
    assert [x['hypothesis_id'] for x in result]==['H1','H2','H3','H4','H5']
    assert result[0]['applicable']
    assert all(Decimal(x['gross_gap_per_share'])<0 for x in result[0]['metrics']['comparisons'])
    assert all(not x['live_promotion_ready'] for x in result[0]['metrics']['comparisons'])
    assert not result[1]['metrics']['independent_goal_or_var_verified']
    assert result[3]['metrics']['realized_pnl_usdc'] is None


def test_missing_no_is_not_synthesized():
    event,books=fixture();books.pop('1-NO')
    result=compute(event,books,None,observed_at='2026-09-06T00:00:02Z')
    assert not result[0]['applicable'] and result[0]['metrics']['comparisons']==[]


def test_depth_ladder_keeps_insufficient_large_sizes():
    rows=depth_metrics({'asks':[side('.5',20)],'bids':[side('.4',20)]},[5,100],[0,.05])
    assert rows[0]['buy']['complete'] and not rows[1]['buy']['complete']


def stamp(seconds=0):
    return (datetime(2026,9,6,tzinfo=timezone.utc)+timedelta(seconds=seconds)).isoformat()


def timed_fixture(seconds=0,score=(1,0),known=True):
    event,books=fixture()
    event['observed_at']=stamp(-1000)  # Gamma time is NOT a news/baseline clock.
    event['official_news']={
        'provider':'espn','provider_game_id':'game-1','sport_family':'soccer','league_code':'epl',
        'scheduled_at':stamp(-1800),'request_id':f'news-{seconds}',
        'started_at':stamp(seconds-.2),'received_at':stamp(seconds),
        'valid_for_matching':True,'source_coverage_complete':True,
        'matching_evidence':{'venue_event_id':'e','sport_family':'soccer','league_code':'epl'},
        'home':{'team_id':'home','name':'Alpha','score':score[0]},
        'away':{'team_id':'away','name':'Beta','score':score[1]},
        'result_scope':'REGULATION','regulation_result_known':known,'final':known,
    }
    for side in event['side_definitions']:
        side['tradable']=True
    for book in books.values():
        book.update(started_at=stamp(seconds+.5),observed_at=stamp(seconds+1),request_id=f'books-{seconds}')
    return event,books


def mock_payout(monkeypatch,event):
    payout={'status':'OK','token_payouts':{s['token_id']:int(s['token_id']=='0-YES') for s in event['side_definitions']},
            'rule_authority_verified':False,'basis':'PROVIDER_RESULT_EXPECTATION_NOT_VENUE_SETTLEMENT',
            'result_scope':'REGULATION','reason':'fixture_result_expectation'}
    monkeypatch.setattr(hypotheses,'derive_expected_payouts',lambda e,n:deepcopy(payout))
    return payout


def hypothesis(rows,name):
    return next(row for row in rows if row['hypothesis_id']==name)


@pytest.mark.parametrize('flag',[None,False,1,'true'])
def test_h1_requires_explicit_comparable_partition_without_discarding_raw_books(flag):
    event,books=fixture()
    event['h1_partition_comparable']=flag
    before=deepcopy(books)
    row=hypothesis(compute(event,books,None,observed_at=stamp(2)),'H1')
    assert not row['applicable'] and row['reason']=='partition_comparability_unproven'
    assert row['metrics']['complete_direct_six'] is True
    assert row['metrics']['comparisons']==[]
    assert books==before and len(books)==6


def test_h5_fresh_post_news_book_uses_receipt_as_first_known_not_feature_time(monkeypatch):
    event,books=timed_fixture()
    mock_payout(monkeypatch,event)
    before=deepcopy(books)
    row=hypothesis(compute(event,books,None,observed_at=stamp(1.5),ladder=(5,)),'H5')
    assert row['applicable'] and len(row['metrics']['discounts'])==1
    timing=row['metrics']['book_timing']['0-YES']
    assert timing['valid'] and timing['news_to_book_start_seconds']==.5
    assert timing['book_request_seconds']==.5 and timing['book_age_at_evaluation_seconds']==.5
    assert timing['strict_2s'] and timing['latency_stratum']=='STRICT_2S_RECEIPTS'
    assert row['metrics']['first_result_news_received_at']==stamp(0)
    assert event['guava_signal_state']['first_result_news_received_at']==stamp(0)
    assert event['guava_signal_state']['observed_at']==stamp(1.5)
    assert books==before


@pytest.mark.parametrize('field,value',[
    ('started_at',None),('observed_at',None),('status',None),('status','INVALID'),
    ('status','ERROR'),('request_id',None),('started_at','2026-09-06T00:00:00'),
    ('observed_at','not-a-date'),('started_at',stamp(-.1)),
    ('started_at',stamp(1.1)),('observed_at',stamp(2)),
])
def test_h5_missing_failed_pre_news_or_reversed_receipt_never_emits_discount(monkeypatch,field,value):
    event,books=timed_fixture()
    mock_payout(monkeypatch,event)
    books['0-YES'][field]=value
    row=hypothesis(compute(event,books,None,observed_at=stamp(1.5),ladder=(5,)),'H5')
    assert not row['applicable'] and row['metrics']['discounts']==[]
    assert not row['metrics']['book_timing']['0-YES']['valid']
    assert row['metrics']['book_timing']['0-YES']['reason']


@pytest.mark.parametrize('started,received,evaluated',[
    (30.001,31,32),(.5,15.501,16),(.5,1,31.001),(30,40,45.001),
])
def test_h5_each_research_receipt_bound_is_enforced(monkeypatch,started,received,evaluated):
    event,books=timed_fixture()
    mock_payout(monkeypatch,event)
    books['0-YES'].update(started_at=stamp(started),observed_at=stamp(received))
    row=hypothesis(compute(event,books,None,observed_at=stamp(evaluated),ladder=(5,)),'H5')
    assert not row['applicable'] and row['metrics']['discounts']==[]


@pytest.mark.parametrize('started,received,evaluated',[(30,45,45),(1,2,32)])
def test_h5_exact_research_boundaries_are_accepted_as_delayed_observations(monkeypatch,started,received,evaluated):
    event,books=timed_fixture()
    mock_payout(monkeypatch,event)
    books['0-YES'].update(started_at=stamp(started),observed_at=stamp(received))
    row=hypothesis(compute(event,books,None,observed_at=stamp(evaluated),ladder=(5,)),'H5')
    assert row['applicable']
    assert row['metrics']['discounts'][0]['latency_stratum']=='DELAYED_ORDERED_RECEIPTS'
    assert row['metrics']['discounts'][0]['strict_2s'] is False


@pytest.mark.parametrize('received',[None,'2026-09-06T00:00:00',stamp(2),stamp(-46)])
def test_h5_invalid_future_or_stale_news_receipt_not_overridden_by_mock_payout(monkeypatch,received):
    event,books=timed_fixture()
    mock_payout(monkeypatch,event)
    event['official_news']['received_at']=received
    row=hypothesis(compute(event,books,None,observed_at=stamp(1.5),ladder=(5,)),'H5')
    assert not row['applicable'] and row['metrics']['discounts']==[]


def test_h5_optional_explicit_ready_time_is_honored_not_invented(monkeypatch):
    event,books=timed_fixture()
    mock_payout(monkeypatch,event)
    event['official_news']['result_ready_at']=stamp(.75)
    row=hypothesis(compute(event,books,None,observed_at=stamp(1.5)),'H5')
    assert not row['applicable']
    assert row['metrics']['book_timing']['0-YES']['reason']=='book_started_before_news_anchor'
    event['official_news']['result_ready_at']=stamp(.25)
    row=hypothesis(compute(event,books,None,observed_at=stamp(1.5)),'H5')
    assert row['applicable']
    assert row['metrics']['book_timing']['0-YES']['ordering_anchor_basis']=='explicit_result_ready_at'


def test_h5_temporal_gate_is_per_token_not_an_all_six_book_gate(monkeypatch):
    event,books=timed_fixture()
    mock_payout(monkeypatch,event)
    books['1-NO']['started_at']=stamp(-1)  # Not a winning token in the deterministic fixture.
    row=hypothesis(compute(event,books,None,observed_at=stamp(1.5),ladder=(5,)),'H5')
    assert row['applicable'] and {d['token_id'] for d in row['metrics']['discounts']}=={'0-YES'}
    assert not row['metrics']['book_timing']['1-NO']['valid']


def test_h5_empty_or_incomplete_depth_does_not_become_an_applicable_discount(monkeypatch):
    event,books=timed_fixture()
    mock_payout(monkeypatch,event)
    books['0-YES']['raw']['asks']=[side('.5','.1')]
    row=hypothesis(compute(event,books,None,observed_at=stamp(1.5),ladder=(5,)),'H5')
    assert not row['applicable']
    assert row['metrics']['discounts'][0]['gross_discount_per_share'] is None


def test_h5_first_receipt_survives_distinct_confirmations_and_replays_do_not_increment(monkeypatch):
    previous,old_books=timed_fixture()
    mock_payout(monkeypatch,previous)
    compute(previous,old_books,None,observed_at=stamp(1.5),ladder=(5,))
    current,books=timed_fixture(60)
    row=hypothesis(compute(current,books,previous,observed_at=stamp(61.5),ladder=(5,)),'H5')
    assert row['metrics']['consecutive_confirmations']==2
    assert row['metrics']['first_result_news_received_at']==stamp(0)
    replay=deepcopy(current)
    row=hypothesis(compute(replay,books,current,observed_at=stamp(61.6),ladder=(5,)),'H5')
    assert row['metrics']['consecutive_confirmations']==2
    assert row['metrics']['first_result_news_received_at']==stamp(0)


def h2_pair():
    previous,old_books=timed_fixture(0,score=(0,0),known=False)
    compute(previous,old_books,None,observed_at=stamp(1.5))
    current,books=timed_fixture(60,score=(1,0),known=False)
    for book in books.values():
        for key in ('bids','asks'):
            book['raw'][key][0]['price']=str(Decimal(book['raw'][key][0]['price'])+Decimal('.01'))
    return previous,current,books


def test_h2_uses_news_and_per_book_receipts_not_gamma_or_feature_gap():
    previous,current,books=h2_pair()
    previous['observed_at']=stamp(-100000)
    row=hypothesis(compute(current,books,previous,observed_at=stamp(61.5)),'H2')
    assert row['applicable'] and row['metrics']['observed_score_change'] is True
    assert row['metrics']['observation_gap_seconds']==60
    assert set(row['metrics']['midpoint_changes'])==set(books)
    assert all(Decimal(change)==Decimal('.01') for change in row['metrics']['midpoint_changes'].values())
    assert not row['metrics']['pre_goal_baseline_proven']
    assert not row['metrics']['reversal_measured']
    assert row['metrics']['baseline_basis']=='previous_post_receipt_book_not_proven_pre_score_change'
    assert current['guava_signal_state']['quote_receipts']['0-YES']['received_at']==stamp(61)


@pytest.mark.parametrize('score',[(None,None),(None,0),(True,0),('1',0),(0,0)])
def test_h2_missing_invalid_or_unchanged_scores_are_not_applicable(score):
    previous,current,books=h2_pair()
    current['official_news']['home']['score'],current['official_news']['away']['score']=score
    row=hypothesis(compute(current,books,previous,observed_at=stamp(61.5)),'H2')
    assert not row['applicable']
    assert row['metrics']['observed_score_change'] is not True


@pytest.mark.parametrize('field,value',[
    ('league_code','bun'),('provider','another'),('provider_game_id','another'),
    ('scheduled_at',stamp(-1900)),('request_id','news-0'),('matching_evidence',None),
    ('source_coverage_complete',False),('received_at',stamp(0)),
])
def test_h2_mismatched_replayed_or_unconfirmed_news_has_no_valid_comparison(field,value):
    previous,current,books=h2_pair()
    current['official_news'][field]=value
    row=hypothesis(compute(current,books,previous,observed_at=stamp(61.5)),'H2')
    assert not row['applicable'] and row['metrics']['midpoint_changes']=={}


def test_h2_long_gap_legacy_baseline_and_cross_cohort_fail_closed():
    previous,current,books=h2_pair()
    for mutate in (lambda p:p['guava_signal_state'].pop('quote_receipts'),
                   lambda p:p.update(cohort_key='other'),
                   lambda p:p['official_news'].update(received_at=stamp(-100))):
        old=deepcopy(previous); mutate(old)
        row=hypothesis(compute(deepcopy(current),books,old,observed_at=stamp(61.5)),'H2')
        assert not row['applicable'] and row['metrics']['midpoint_changes']=={}


def test_h2_old_receipts_are_revalidated_not_trusted_from_cached_valid_boolean():
    previous,current,books=h2_pair()
    old=previous['guava_signal_state']['quote_receipts']['0-YES']
    old['started_at']=stamp(-1)
    old['valid']=True
    row=hypothesis(compute(current,books,previous,observed_at=stamp(61.5)),'H2')
    assert '0-YES' not in row['metrics']['midpoint_changes']
    assert not row['metrics']['baseline_timing']['0-YES']['valid']


def test_h2_pre_news_current_book_is_not_rescued_by_other_fresh_tokens():
    previous,current,books=h2_pair()
    books['0-YES']['started_at']=stamp(59)
    row=hypothesis(compute(current,books,previous,observed_at=stamp(61.5)),'H2')
    assert '0-YES' not in row['metrics']['midpoint_changes']
    assert not row['metrics']['book_timing']['0-YES']['valid']


def test_h5_five_second_delay_is_retained_as_delayed_not_retimestamped(monkeypatch):
    event,books=timed_fixture()
    mock_payout(monkeypatch,event)
    row=hypothesis(compute(event,books,None,observed_at=stamp(6),ladder=(5,)),'H5')
    assert row['applicable'] and len(row['metrics']['discounts'])==1
    assert row['metrics']['discounts'][0]['latency_stratum']=='DELAYED_ORDERED_RECEIPTS'
    assert row['metrics']['discounts'][0]['strict_2s'] is False
    assert row['metrics']['source_cache_freshness_proven'] is False
    assert row['metrics']['book_timing']['0-YES']['received_at']==stamp(1)
    assert row['metrics']['first_result_news_received_at']==stamp(0)


def test_h2_delayed_sequential_news_books_are_retained_with_latency_stratum():
    previous,current,books=h2_pair()
    for book in books.values():book.update(started_at=stamp(80),observed_at=stamp(85))
    row=hypothesis(compute(current,books,previous,observed_at=stamp(86)),'H2')
    assert row['applicable'] and len(row['metrics']['midpoint_changes'])==6
    assert all(t['latency_stratum']=='DELAYED_ORDERED_RECEIPTS' for t in row['metrics']['book_timing'].values())
    assert row['metrics']['source_cache_freshness_proven'] is False


@pytest.mark.parametrize('field,value',[('asset_id','other-token'),('market','other-condition'),
                                       ('asset_id',None),('market',None)])
def test_raw_identity_mismatch_is_retained_but_never_used_for_features(monkeypatch,field,value):
    event,books=timed_fixture()
    mock_payout(monkeypatch,event)
    books['0-YES']['raw'][field]=value
    before=deepcopy(books)
    rows=compute(event,books,None,observed_at=stamp(1.5),ladder=(5,))
    assert not hypothesis(rows,'H1')['applicable']
    assert not hypothesis(rows,'H5')['applicable']
    assert hypothesis(rows,'H5')['metrics']['discounts']==[]
    assert '0-YES' not in event['guava_signal_state']['midpoints']
    assert books==before


def test_matching_asset_only_is_allowed_without_inventing_market_identity(monkeypatch):
    event,books=timed_fixture()
    mock_payout(monkeypatch,event)
    books['0-YES']['raw']['asset_id']='0-YES'
    row=hypothesis(compute(event,books,None,observed_at=stamp(1.5),ladder=(5,)),'H5')
    assert row['applicable']
    assert row['metrics']['discounts'][0]['raw_identity_basis']=='asset_id_only'
    assert 'market' not in books['0-YES']['raw']


@pytest.mark.parametrize('bids',[[],None])
def test_h5_valid_ask_does_not_require_bid_but_other_hypotheses_still_do(monkeypatch,bids):
    event,books=timed_fixture()
    mock_payout(monkeypatch,event)
    books['0-YES']['raw'].update(asset_id='0-YES',market='0',bids=bids)
    before=deepcopy(books)
    rows=compute(event,books,None,observed_at=stamp(1.5),ladder=(5,))
    h5=hypothesis(rows,'H5')
    assert h5['applicable'] and h5['metrics']['discounts'][0]['full_depth']
    assert h5['metrics']['discounts'][0]['gross_discount_per_share'] is not None
    assert h5['metrics']['ask_only_tokens']==['0-YES']
    assert not hypothesis(rows,'H1')['applicable']
    assert '0-YES' not in hypothesis(rows,'H4')['metrics']['spreads']
    assert '0-YES' not in event['guava_signal_state']['midpoints']
    assert books==before


def test_h5_ask_only_still_requires_fresh_post_news_request(monkeypatch):
    event,books=timed_fixture()
    mock_payout(monkeypatch,event)
    books['0-YES']['raw']['bids']=[]
    books['0-YES']['started_at']=stamp(-1)
    row=hypothesis(compute(event,books,None,observed_at=stamp(1.5),ladder=(5,)),'H5')
    assert not row['applicable'] and row['metrics']['discounts']==[]
