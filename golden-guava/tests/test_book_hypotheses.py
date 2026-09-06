from decimal import Decimal
import pytest
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
    event={'event_id':'e','sport_family':'soccer','clock':{'score':'0-0'},'side_definitions':[]}
    books={}
    for i,yes in enumerate(('.5','.3','.2')):
        for outcome in ('YES','NO'):
            token=f'{i}-{outcome}';p=Decimal(yes) if outcome=='YES' else 1-Decimal(yes)
            event['side_definitions'].append({'token_id':token,'condition_id':str(i),'outcome_side':outcome})
            books[token]={'observed_at':'2026-09-06T00:00:01Z','raw':{
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
