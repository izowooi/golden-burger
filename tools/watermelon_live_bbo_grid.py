#!/usr/bin/env python3
"""Read-only historical Watermelon Live BBO diagnostics, never depth or fills.

Snapshots preserve a scanner poll/reference UTC time, not per-token HTTP receipt.
The mutable catalog is used ONLY for an exact stable event/condition/token/label
mapping. Its prices, OPEN flags, fee, dates and resolution are not PIT evidence.
No synthetic quantity, $5 walk, fee, native clock, payout or live entry is emitted.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

import conservative_sports_grid as grid

ORIGIN = 'WATERMELON_LIVE_BBO_POLL_PROXY'
FAMILIES = {'soccer', 'mlb', 'nba', 'nfl', 'nhl'}
SOCCER_CLASSIFIERS = {'soccer-major-league-identity-v1', 'soccer-elite-competition-identity-v3'}
REQUIRED = {
    'market_snapshots': {'id','condition_id','token_id','outcome','best_bid','best_ask','run_id','timestamp'},
    'market_catalog': {'condition_id','event_id','event_title','question','outcomes_json','token_ids_json'},
    'run_audits': {'run_id','strategy_name','job_name','mode','config_hash','started_at','finished_at','status'},
    'strategy_configs': {'config_hash','strategy_name','mode','config_json'},
}


@dataclass
class BBOSnap(grid.Snap):
    bbo_best_bid: float | None = None
    bbo_best_ask: float | None = None
    depth_available: bool = False


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def stamp(value, *, snapshot=False):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        # SQLAlchemy producer explicitly writes UTC without tzinfo to snapshots.
        if dt.tzinfo is None:
            if not snapshot: return None
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def array(value):
    for _ in range(2):
        if not isinstance(value, str): break
        try: value = json.loads(value)
        except (TypeError, ValueError): return None
    return value if isinstance(value, list) else None


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try: result = float(value)
    except (ValueError, OverflowError): return None
    return result if math.isfinite(result) and 0 <= result <= 1 else None


def family(row, config):
    claims = {str(x).lower() for x in (row.get('sport_family'), config.get('sport_family')) if x}
    if config.get('classifier_version') in SOCCER_CLASSIFIERS:
        claims.add('soccer')
    if len(claims) != 1 or not claims <= FAMILIES:
        raise ValueError('missing_or_conflicting_recorded_sport')
    return claims.pop()


def identity(row, catalog, sport):
    """Exact stable mapping; TEAM_A/B are partition labels, not venue roles."""
    if catalog is None or str(catalog.get('condition_id')) != str(row['condition_id']):
        raise ValueError('missing_catalog_condition_identity')
    event = str(catalog.get('event_id') or '')
    title, question = catalog.get('event_title'), catalog.get('question')
    labels, tokens = array(catalog.get('outcomes_json')), array(catalog.get('token_ids_json'))
    if (not event or not isinstance(title, str) or not isinstance(question, str)
            or not isinstance(labels, list) or not isinstance(tokens, list)
            or len(labels) != 2 or len(tokens) != 2
            or any(not isinstance(x, str) or not x for x in labels+tokens)
            or len(set(tokens)) != 2 or len(set(labels)) != 2):
        raise ValueError('malformed_catalog_event_token_labels')
    token = str(row['token_id'])
    if token not in tokens or labels[tokens.index(token)] != row['outcome']:
        raise ValueError('snapshot_catalog_token_label_mismatch')
    teams = title.split(' vs. ')
    if len(teams) != 2 or not all(teams) or len(set(teams)) != 2:
        raise ValueError('exact_two_team_title_unavailable')
    if sport == 'soccer':
        if {x.lower() for x in labels} != {'yes', 'no'}:
            raise ValueError('soccer_binary_yes_no_required')
        side = str(row['outcome']).upper()
        if question == f'Will {title} end in a draw?': role = 'DRAW'
        else:
            matches = [i for i, team in enumerate(teams)
                       if re.fullmatch(re.escape('Will '+team+' win on ')+r'\d{4}-\d{2}-\d{2}\?', question)]
            if len(matches) != 1:
                raise ValueError('exact_whole_match_proposition_unavailable')
            role = ('TEAM_A', 'TEAM_B')[matches[0]]
    else:
        if question != title or set(labels) != set(teams):
            raise ValueError('exact_direct_two_team_moneyline_unavailable')
        role, side = ('TEAM_A', 'TEAM_B')[teams.index(row['outcome'])], 'DIRECT'
    return event, title, (role, side)


def verify_source(source):
    path = Path(source['local_path']).resolve()
    if source.get('pinned') is not True or source.get('strategy') != 'golden-watermelon-live':
        raise ValueError('pinned_watermelon_live_source_required')
    manifest = json.loads(Path(source['manifest']).read_text())
    if (manifest.get('pinned_path') != str(path) or manifest.get('sha256') != source['local_sha256']
            or manifest.get('source_key') != source['source_key'] or manifest.get('quick_check') != ['ok']
            or sha(path) != source['local_sha256']):
        raise ValueError('pin_path_source_hash_or_check_mismatch')
    return path


def read_source(source, start, end):
    path = verify_source(source)
    start_t, end_t = stamp(start), stamp(end)
    if start_t is None or end_t is None or start_t >= end_t:
        raise ValueError('aware_UTC_half_open_range_required')
    audit = {'adapter':ORIGIN,'supported':True,'physical_snapshot_rows':0,'in_range_snapshot_rows':0,
             'mapped_snapshot_rows':0,'valid_bbo_rows':0,'excluded_rows':{},'invalid_bbo_rows':{},
             'depth_available':False,'financial_replay_eligible':False,'timestamp_basis':'BBO_POLL_PROXY',
             'identity_evidence':'EXACT_SNAPSHOT_TOKEN_LABEL_WITH_STABLE_CATALOG_EVENT_CONDITION_MAPPING',
             'catalog_point_in_time_status_used':False,'source_sha256':source['local_sha256'],
             'limitations':['No archived CLOB depth or original HTTP body/receipt for these BBO snapshots.',
                            'Catalog identity is retrospective stable metadata; no PIT OPEN, fee, clock or resolution is inferred.',
                            'A same-run poll timestamp does not prove simultaneous token HTTP receipt.',
                            'Stored snapshot population may already be filtered by the historical $5 ask walk.']}
    excluded, invalid = Counter(), Counter()
    groups, meta, failure_by_cohort = defaultdict(list), {}, defaultdict(list)
    with sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        if [r[0] for r in c.execute('PRAGMA quick_check')] != ['ok']:
            raise ValueError('database_quick_check_failed')
        missing = {t: sorted(cols-{r[1] for r in c.execute('PRAGMA table_info('+t+')')})
                   for t,cols in REQUIRED.items() if cols-{r[1] for r in c.execute('PRAGMA table_info('+t+')')}}
        if missing:
            audit.update(supported=False,missing_schema=missing)
            return [], audit
        configs = {r['config_hash']:dict(r) for r in c.execute('SELECT * FROM strategy_configs')}
        runs = {r['run_id']:dict(r) for r in c.execute('SELECT * FROM run_audits')}
        catalog = {r['condition_id']:dict(r) for r in c.execute('SELECT * FROM market_catalog')}
        audit['physical_snapshot_rows'] = c.execute('SELECT count(*) FROM market_snapshots').fetchone()[0]
        for raw in c.execute('SELECT * FROM market_snapshots ORDER BY timestamp,id'):
            row = dict(raw)
            at = stamp(row['timestamp'], snapshot=True)
            if at is None:
                excluded['invalid_snapshot_UTC_time'] += 1; continue
            if not start_t <= at < end_t: continue
            audit['in_range_snapshot_rows'] += 1
            run = runs.get(row['run_id'])
            try:
                if not run: raise ValueError('missing_run_identity')
                cfgrow = configs.get(run['config_hash'])
                if not cfgrow: raise ValueError('missing_config_identity')
                cfgfull = json.loads(cfgrow['config_json']); cfg = cfgfull.get('trading', {})
                digest = cfg.get('strategy_source_digest')
                if (run['strategy_name'] != source['strategy'] or run['job_name'] != source['runtime_job']
                        or run['mode'] not in {'live','simulation'} or cfgrow['strategy_name'] != run['strategy_name']
                        or cfgrow['mode'] != run['mode'] or cfgfull.get('strategy_name') != run['strategy_name']
                        or cfgfull.get('mode') != run['mode'] or not isinstance(digest,str)
                        or not re.fullmatch('[0-9a-f]{64}',digest)):
                    raise ValueError('run_config_source_identity_mismatch')
                sport = family(row,cfg)
                event, title, slot = identity(row,catalog.get(row['condition_id']),sport)
            except (ValueError,TypeError,KeyError) as exc:
                excluded[str(exc)] += 1; continue
            cohort = ':'.join((source['id'],run['job_name'],run['mode'],run['config_hash'],digest,ORIGIN))
            key = (cohort,event)
            meta[key] = (sport,title,{**cfg,'config_hash':run['config_hash'],'strategy_source_digest':digest,
                'job_name':run['job_name'],'mode':run['mode'],'_analysis_adapter':'watermelon_live_bbo',
                'evidence_origin':ORIGIN,'timestamp_basis':'BBO_POLL_PROXY','depth_available':False,
                'one_minute_replay_eligible':False,'financial_replay_eligible':False,
                'season_phase':'UNKNOWN','cadence_seconds':0,
                'cadence_reason':'NOT_RECORDED_IN_RESOLVED_CONFIG_NO_RUNTIME_NAME_INFERENCE'})
            begin, finish = stamp(run['started_at']), stamp(run['finished_at'])
            bid, ask = number(row['best_bid']), number(row['best_ask'])
            reasons = []
            if run['status'] != 'SUCCESS': reasons.append('run_not_SUCCESS')
            if begin is None or finish is None or not begin <= at <= finish < end_t:
                reasons.append('run_snapshot_publication_time_unproven_or_cutoff')
            if bid is None or ask is None: reasons.append('missing_or_invalid_BBO')
            elif bid > ask: reasons.append('crossed_BBO')
            valid = not reasons
            snap = BBOSnap(token=str(row['token_id']),condition=str(row['condition_id']),slot=slot,
                time=at,run=row['run_id'],minute=None,midpoint=(bid+ask)/2 if bid is not None and ask is not None else None,
                asks=(),bids=(),valid=valid,open_observed=False,gate_observed=False,fee_rate=None,
                spread=ask-bid if bid is not None and ask is not None else None,ask_state='BBO_ONLY_NO_DEPTH',
                source_reason='BBO_POLL_PROXY_NOT_HTTP_RECEIPT',buy=None,entry_set_complete=False,
                evidence_origin=ORIGIN,observation_status='BBO_VALID' if valid else '|'.join(reasons),
                market_open_observed=False,outcome_label=row['outcome'],verified_role=None,
                verified_team_name=None,role_evidence_scope='STABLE_CATALOG_PARTITION_NOT_VENUE_ROLE',
                legacy_result_kind=slot[0],legacy_role_semantics='TEAM_A_B_TITLE_PARTITION_NOT_HOME_AWAY',
                bbo_best_bid=bid,bbo_best_ask=ask,depth_available=False)
            groups[(key,row['run_id'])].append(snap)
            audit['mapped_snapshot_rows'] += 1
            if not valid:
                invalid.update(reasons)
                failure_by_cohort[cohort].append(at)
        events = []
        group_counts = Counter()
        for (cohort,event), (sport,title,cfg) in meta.items():
            result_groups = []
            for (key,run_id), snaps in groups.items():
                if key != (cohort,event): continue
                if len({s.token for s in snaps}) != len(snaps):
                    for snap in snaps: snap.valid=False; snap.observation_status='duplicate_run_token'
                    invalid['duplicate_run_token'] += len(snaps)
                selected = [s for s in snaps if s.slot[1] == ('YES' if sport=='soccer' else 'DIRECT')]
                expected = {('TEAM_A','YES'),('DRAW','YES'),('TEAM_B','YES')} if sport=='soccer' else {('TEAM_A','DIRECT'),('TEAM_B','DIRECT')}
                topology = (len(selected)==len(expected) and {s.slot for s in selected}==expected
                            and len({s.condition for s in selected})==(3 if sport=='soccer' else 1)
                            and len({s.token for s in selected})==len(expected))
                group_counts['bbo_partition_complete' if topology else 'bbo_partition_incomplete'] += 1
                if topology and all(s.valid for s in selected): group_counts['valid_bbo_partition_groups'] += 1
                # Group.complete and entry_set_complete concern executable depth and stay False.
                result_groups.append(grid.Group(max(s.time for s in snaps),run_id,snaps,False))
            result_groups.sort(key=lambda g:(g.time,g.run))
            events.append(grid.Event(source['id'],cohort,sport,event,title,cfg,result_groups,
                                     [(at,at) for at in sorted(set(failure_by_cohort[cohort]))],{}))
        audit['valid_bbo_rows'] = sum(s.valid for e in events for g in e.groups for s in g.snaps)
        audit.update(events=len(events),cohorts=len({e.cohort for e in events}),groups=dict(group_counts),
                     excluded_rows=dict(excluded),invalid_bbo_rows=dict(invalid))
    if sha(path) != source['local_sha256']: raise ValueError('source_changed_during_read')
    return events,audit
