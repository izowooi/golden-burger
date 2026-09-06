"""Pure displayed-depth arithmetic. Never an actual-fill assertion."""
from decimal import Decimal,InvalidOperation


def number(value):
    if isinstance(value,bool):raise ValueError('boolean is not an economic number')
    try:result=Decimal(str(value))
    except (InvalidOperation,ValueError):raise ValueError('invalid economic number') from None
    if not result.is_finite():raise ValueError('non-finite economic number')
    return result


def levels(raw,*,bids=False):
    if not isinstance(raw,list):raise ValueError('book side must be an array')
    result=[];seen=set()
    for row in raw:
        if not isinstance(row,dict):raise ValueError('book level must be object')
        price,size=number(row.get('price')),number(row.get('size'))
        if not 0<price<=1 or size<=0 or price in seen:raise ValueError('invalid or duplicate book level')
        seen.add(price);result.append((price,size))
    return sorted(result,reverse=bids)


def walk(raw,amount,*,buy=True):
    remaining=number(amount)
    if remaining<=0:raise ValueError('positive walk amount required')
    cost=Decimal(0);shares=Decimal(0);used=[]
    for price,available in levels(raw,bids=not buy):
        take=min(available,remaining/price if buy else remaining)
        if take<=0:break
        cost+=take*price;shares+=take
        remaining-=take*price if buy else take
        used.append({'price':str(price),'shares':str(take)})
        if remaining<=Decimal('1e-18'):remaining=Decimal(0);break
    return {'complete':remaining==0,'shares':str(shares),'notional_usdc':str(cost),
        'vwap':str(cost/shares) if shares else None,'remaining':str(remaining),
        'limit_price':used[-1]['price'] if used else None,'used_levels':used,
        'basis':'displayed_book_not_actual_fill'}


def fee_stress(walk_result,rate,exponent=1):
    r=number(rate)
    if not 0<=r<=1 or exponent not in (1,2):raise ValueError('invalid stress schedule')
    return sum((number(x['shares'])*r*(number(x['price'])*(1-number(x['price'])))**exponent
                for x in walk_result['used_levels']),Decimal(0))


def depth_metrics(book,ladder,rates):
    result=[]
    for amount in ladder:
        buy=walk(book.get('asks'),amount)
        sell=walk(book.get('bids'),buy['shares'],buy=False) if number(buy['shares'])>0 else None
        result.append({'target_usdc':amount,'buy':buy,'same_snapshot_sell':sell,
            'fee_stress':[{ 'rate':rate,'exponent':1,
                'buy_fee_usdc':str(fee_stress(buy,rate)),
                'sell_fee_usdc':str(fee_stress(sell,rate)) if sell else None,
                'basis':'assumed_schedule_not_actual_fee'} for rate in rates]})
    return result
