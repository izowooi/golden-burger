"""Same-entry MLB exit comparisons with explicit censoring and displayed fees.

Read-only research: one verified observer cohort, full $5 entry, minimum-size
full FOK exits, no interpolation across missing observations. Not actual fills.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polybot.api.clob_client import (
    ClobResponseUnavailableError,
    walk_sell_book_from_evidence,
)
from scripts.replay_direct_six_book import (
    Snapshot, _cohort_identity, load_snapshots, trend_confirmed, walk_buy,
)

TARGETS = (.60, .65, .70, .75, .80, .85, .90, .95)
STOPS = (.05, .10, .12, .15, .20)


def select_entries(snapshots: list[Snapshot]) -> list[Snapshot]:
    groups = defaultdict(list)
    for snapshot in snapshots:
        groups[(snapshot.event_id, snapshot.run_id)].append(snapshot)
    selected, entered, history = [], set(), defaultdict(list)
    for group in sorted(groups.values(), key=lambda g: g[0].observed_at):
        for row in group:
            history[(row.event_id, row.token_id)].append(row)
            history[(row.event_id, row.token_id)] = history[(row.event_id, row.token_id)][-5:]
        if group[0].event_id in entered:
            continue
        if (len(group) != 2 or len({s.token_id for s in group}) != 2
                or {(s.result_kind, s.outcome_side) for s in group}
                != {('HOME', 'DIRECT'), ('AWAY', 'DIRECT')}):
            continue
        ranked = sorted(group, key=lambda s: (-s.midpoint, s.token_id))
        candidate = ranked[0]
        if (candidate.spread > .05 + 1e-9
                or candidate.midpoint - ranked[1].midpoint < .005 - 1e-9):
            continue
        if trend_confirmed(
            history[(candidate.event_id, candidate.token_id)], threshold=.55,
            current_snapshot_id=candidate.snapshot_id, source_clock_required=False,
            observations=5, min_move=.01,
        ):
            selected.append(candidate)
            entered.add(candidate.event_id)
    return selected


def levels_fee(levels, shares: float, rate: float) -> float:
    left, fee = shares, 0.0
    for price, available in levels:
        used = min(left, available)
        fee += used * rate * price * (1 - price)
        left -= used
        if left <= 1e-7:
            return fee
    raise ValueError('fee requires complete matching depth')


def replay_exit(entry: Snapshot, path: list[Snapshot], *, target: float, stop: float,
                fee_rate: float, invalid_times=(), max_gap_seconds: float = 90) -> dict:
    buy = walk_buy(entry.asks)
    if buy is None:
        raise ValueError('missing full $5 entry')
    price, shares = buy
    sell_shares = float(Decimal(str(shares)).quantize(Decimal('.01'), rounding=ROUND_FLOOR))
    # All baseline .55-.58 entries have <10 shares. They cannot split into
    # two independently sellable >=5-share lots under the live TP contract.
    if not 5 <= sell_shares < 10:
        raise ValueError('this replay only supports the baseline $5 MLB entry')
    buy_fee = levels_fee(entry.asks, shares, fee_rate)
    result = {'event_id':entry.event_id, 'condition_id':entry.condition_id,
              'token_id':entry.token_id, 'entry_at':entry.observed_at.isoformat()+'Z',
              'entry_vwap':price, 'shares':shares, 'sell_shares':sell_shares,
              'sdk_dust_shares':shares-sell_shares, 'target':target,
              'stop_delta':stop, 'fee_rate':fee_rate, 'exit_at':None,
              'exit_vwap':None, 'net_pnl':None, 'reason':'right_censored',
              'blocked_book_observations':0}
    last = entry.observed_at
    for row in sorted(path, key=lambda s: s.observed_at):
        if row.observed_at <= entry.observed_at:
            continue
        if row.config_hash != entry.config_hash:
            result['reason'] = 'cohort_boundary_censored'
            return result
        if ((row.observed_at-last).total_seconds() > max_gap_seconds
                or any(last < t <= row.observed_at for t in invalid_times)):
            result['reason'] = 'observation_gap_censored'
            return result
        last = row.observed_at
        bid = row.bids[0][0] if row.bids else None
        if bid is None:
            result['reason'] = 'missing_bid_censored'
            return result
        reason = 'take_profit' if bid >= target-1e-9 else 'stop' if bid <= price-stop+1e-9 else None
        if reason is None:
            continue
        book = json.dumps({'schema_version':1,'token_id':row.token_id,
                           'bids':[{'price':p,'size':q} for p,q in row.bids],
                           'asks':[{'price':p,'size':q} for p,q in row.asks]})
        try:
            walk = walk_sell_book_from_evidence(book, shares=sell_shares)
        except ClobResponseUnavailableError:
            result['blocked_book_observations'] += 1
            continue
        if walk.spread is None or not 0 <= walk.spread <= .10 + 1e-9:
            result['blocked_book_observations'] += 1
            continue
        if reason == 'take_profit' and walk.limit_price < target-1e-9:
            result['blocked_book_observations'] += 1
            continue
        sell_fee = levels_fee(row.bids, sell_shares, fee_rate)
        allocated_cost = 5.0 * sell_shares/shares
        allocated_buy_fee = buy_fee * sell_shares/shares
        result.update(reason=reason, exit_at=row.observed_at.isoformat()+'Z',
                      exit_vwap=walk.vwap, buy_fee_allocated=allocated_buy_fee,
                      sell_fee=sell_fee,
                      net_pnl=walk.proceeds-allocated_cost-allocated_buy_fee-sell_fee)
        return result
    # A later terminal payout cannot fill an unobserved stop/TP path gap.
    return result


def report(db: Path, config_hash: str, start: datetime, end: datetime) -> dict:
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    c = sqlite3.connect(f'file:{db.resolve()}?mode=ro',uri=True)
    identity = _cohort_identity(c,caller_sport_family='mlb',config_hash=config_hash)
    snapshots = [s for s in load_snapshots(c,sport_family='mlb',cohort=identity)
                 if start <= s.observed_at < end]
    if c.execute('pragma quick_check').fetchone()[0] != 'ok':
        raise ValueError('DB integrity check failed')
    fees = dict(c.execute('SELECT condition_id,fee_rate FROM market_catalog '
                         'WHERE fees_enabled=1 AND fee_exponent=1 AND fee_taker_only=1'))
    invalid = defaultdict(list)
    for event_id, at in c.execute('SELECT event_id,observed_at FROM event_cycle_evidence '
                                  'WHERE config_hash=? AND complete=0',(config_hash,)):
        invalid[str(event_id)].append(datetime.fromisoformat(at.replace('Z','+00:00')).replace(tzinfo=None))
    c.close()
    paths = defaultdict(list)
    for s in snapshots:paths[(s.event_id,s.token_id)].append(s)
    entries = select_entries(snapshots)
    grid = []
    for tp in TARGETS:
        for sl in STOPS:
            trades = []
            for entry in entries:
                rate = fees.get(entry.condition_id)
                if rate is None or not 0 <= rate <= .1:
                    raise ValueError('market fee contract missing or unsupported')
                trades.append(replay_exit(entry,paths[(entry.event_id,entry.token_id)],
                                          target=tp,stop=sl,fee_rate=rate,
                                          invalid_times=invalid[entry.event_id]))
            known = [t for t in trades if t['net_pnl'] is not None]
            grid.append({'tp':tp,'sl':sl,'known_n':len(known),'censored_n':len(trades)-len(known),
                         'positive_n':sum(t['net_pnl']>0 for t in known),
                         'net_pnl':sum(t['net_pnl'] for t in known),'trades':trades})
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    return {'contract':'mlb-exit-display-replay-v1','not_actual_fill_or_expected_return':True,
            'db':str(db.resolve()),'sha256':before,'cohort':asdict(identity),
            'start':start.isoformat()+'Z','end_exclusive':end.isoformat()+'Z',
            'snapshot_n':len(snapshots),'event_n':len({s.event_id for s in snapshots}),
            'entry_n':len(entries),'grid':grid,
            'limitations':['historical catalog fee parameters; no future rate guarantee',
                           'displayed depth is not a venue FOK fill guarantee',
                           'no terminal payoff imputation for incomplete paths',
                           'small sample and many exit variants; not optimal parameters']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--config-hash',required=True)
    parser.add_argument('--review-start',required=True)
    parser.add_argument('--review-end-exclusive',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve() == args.db.resolve():raise ValueError('cannot overwrite input DB')
    def utc(v):
        d=datetime.fromisoformat(v.replace('Z','+00:00'))
        if d.tzinfo is None:raise ValueError('explicit timezone required')
        return d.astimezone(timezone.utc).replace(tzinfo=None)
    start,end=utc(args.review_start),utc(args.review_end_exclusive)
    if start>=end:raise ValueError('empty review interval')
    result=report(args.db,args.config_hash,start,end)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    for g in result['grid']:
        if g['tp'] in (.65,.70,.90,.95) and g['sl'] in (.10,.12,.15):
            print({k:g[k] for k in ['tp','sl','known_n','censored_n','positive_n','net_pnl']})


if __name__=='__main__':main()
