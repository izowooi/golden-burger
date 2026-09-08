#!/usr/bin/env python3
"""Offline exhaustive, event-paired direct-book entry/exit research.

No order client, network, credentials, or source writes. Cell counts are NOT
independent trials: the same event supplies many alternative policies. All
historical partitions are retrospective; no cell can authorize promotion.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
import gzip
import hashlib
import json
import math
import re
from pathlib import Path
import sqlite3
import sys

import sports_visual_data as visual
from catdog_takeprofit_replay import WhiteOriginalAskEvidence, ask_side_state, failed_between

EPS = 1e-9
NOTIONAL = 5.0
PRICE_GRID = tuple(i / 100 for i in range(1, 100))
FEE_MODELS = ("zero", "sports_005", "flat_100bps")
POLICIES = ("peach_rank", "plum_price_rank", "plum_trend_rank", "watermelon_rank")


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def levels(raw, side):
    rows = raw.get(side)
    if not isinstance(rows, list):
        raise ValueError("missing_book_side")
    output, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_book_level")
        p, q = visual.number(row.get("price")), visual.number(row.get("size"))
        if p is None or q is None or not 0 < p <= 1 or q <= 0 or p in seen:
            raise ValueError("invalid_or_duplicate_book_level")
        output.append((p, q)); seen.add(p)
    return tuple(sorted(output, reverse=side == "bids"))


@dataclass(frozen=True)
class Walk:
    shares: float
    amount: float
    vwap: float
    worst: float
    fee_base: float
    consumed: tuple = ()


def walk(levels_, amount, buy):
    left, quantity, cash, fee_base, worst = amount, 0.0, 0.0, 0.0, 0.0
    consumed=[]
    for p, available in levels_:
        q = min(left / p, available) if buy else min(left, available)
        quantity += q; cash += q * p; fee_base += q * p * (1 - p)
        consumed.append((p,q))
        left -= q * p if buy else q
        worst = p
        if left <= 1e-8:
            return Walk(quantity, cash, cash / quantity, worst, fee_base,tuple(consumed))
    return None


def rounded_fee(raw):
    return float(Decimal(str(raw)).quantize(Decimal(".00001"),rounding=ROUND_HALF_UP))


def level_fees(w,model,rate=None):
    if model=="recorded_schedule" and rate is None:return None
    if model not in FEE_MODELS+("recorded_schedule",):raise ValueError("unknown fee model")
    result=[]
    for p,q in w.consumed:
        raw=(0. if model=="zero" else p*q*.01 if model=="flat_100bps"
             else q*p*(1-p)*(.05 if model=="sports_005" else rate))
        result.append((p,q,rounded_fee(raw)))
    return result


def fee(w, model, rate=None):
    rows=level_fees(w,model,rate)
    if rows is None:return None
    if w.consumed:return sum(f for p,q,f in rows)
    if model=="zero":return 0.
    raw=w.amount*.01 if model=="flat_100bps" else w.fee_base*(.05 if model=="sports_005" else rate)
    return rounded_fee(raw)


def buy_economics(path,model,fee_collection="v2_cash"):
    buy=Walk(**path["buy_walk"])
    if not buy.consumed:raise ValueError("BUY consumed levels missing; historical cash-fee paths are not interchangeable")
    fees=level_fees(buy,model,path["entry_fee_rate"])
    if fees is None:return None
    if fee_collection not in ("v2_cash","legacy_shares"):raise ValueError("unknown fee collection convention")
    cash_fee=sum(f for p,q,f in fees)
    share_fee=sum(f/p for p,q,f in fees) if fee_collection=="legacy_shares" else 0.
    net=max(0.,buy.shares-share_fee)
    sold=math.floor((net+1e-10)*100)/100
    return {"fee_collection":fee_collection,"gross_buy_shares":buy.shares,"buy_fee_usdc_equivalent":cash_fee,
            "buy_fee_shares":share_fee,"net_buy_shares":net,"sell_shares":sold,
            "excluded_dust_shares":net-sold,"usd_cost":5.0+cash_fee if fee_collection=="v2_cash" else 5.0}


def modeled_observations(path,model,economics):
    for obs in path["observations"]:
        w=(walk(obs["bids"],economics["sell_shares"],False) if economics["sell_shares"]>=5-EPS else None)
        spread=obs["spread"]
        executable=(w is not None and obs["open_observed"] and spread is not None and 0<=spread<=.10+EPS
                    and 0<w.vwap<1 and 0<w.worst<1 and 0<obs["best_bid"]<1)
        yield {**obs,"walk":w,"stop":bool(obs["stop_trigger"] and executable),
               "sell_fee":fee(w,model,obs["fee_rate"]) if w else None}


@dataclass
class Snap:
    token: str
    condition: str
    slot: tuple[str, str]
    time: float
    run: str
    minute: float | None
    midpoint: float | None
    asks: tuple
    bids: tuple
    valid: bool
    open_observed: bool
    gate_observed: bool
    fee_rate: float | None
    spread: float | None
    ask_state: str
    source_reason: str
    buy: Walk | None = None
    scheduled_age: float | None = None
    entry_set_complete: bool = True
    evidence_origin: str = "legacy"
    observation_status: str | None = None
    market_open_observed: bool | None = None
    outcome_label: str | None = None
    verified_role: str | None = None
    verified_team_name: str | None = None
    role_evidence_scope: str | None = None
    legacy_result_kind: str | None = None
    legacy_role_semantics: str | None = None


@dataclass
class Group:
    time: float
    run: str
    snaps: list[Snap]
    complete: bool
    ambiguous: set[int] = field(default_factory=set)


@dataclass
class Event:
    source: str
    cohort: str
    sport: str
    event: str
    title: str
    config: dict
    groups: list[Group]
    failures: list
    terminals: dict
    partition: str = ""
    partition_day: str = ""


def expected_slots(sport, white):
    if sport == "soccer":
        return {(kind, side) for kind in ("HOME", "DRAW", "AWAY")
                for side in (("YES",) if white else ("YES", "NO"))}
    return {("HOME", "DIRECT"), ("AWAY", "DIRECT")}


def normalize_group(snaps, sport, white):
    t = max(s.time for s in snaps)
    expected = expected_slots(sport, white)
    complete = (len(snaps) == len(expected) and {s.slot for s in snaps} == expected
                and len({s.token for s in snaps}) == len(expected)
                and all(s.valid and s.entry_set_complete and s.buy is not None and s.midpoint is not None for s in snaps)
                and max(s.time for s in snaps)-min(s.time for s in snaps) <= 5)
    ranked = sorted(snaps, key=lambda s: (-(s.midpoint if s.midpoint is not None else -1), s.token))
    ambiguous = set()
    for i, s in enumerate(ranked):
        for j in (i-1, i+1):
            if 0 <= j < len(ranked) and s.midpoint is not None and ranked[j].midpoint is not None:
                if abs(s.midpoint-ranked[j].midpoint) < .005-EPS:
                    ambiguous.add(i+1)
    return Group(t, snaps[0].run, ranked, complete, ambiguous)


def raw_source_minute(fields,sport):
    """Decode only the source's soccer clock; a schedule is never a clock."""
    if sport!="soccer":return None
    period=str(fields.get("period") or "").strip().casefold()
    if period in {"ht","half time","halftime"}:return 45.
    if period in {"ft","full time","fulltime"}:return 90.
    raw=fields.get("elapsed")
    if raw is None or isinstance(raw,bool):return None
    text=re.sub(r"(?:minutes?|mins?|min|m|')$","",str(raw).strip().casefold()).strip()
    added=re.fullmatch(r"(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)",text)
    if added:minute=float(added.group(1))+float(added.group(2))
    else:
        try:parts=[float(x) for x in text.split(":")]
        except ValueError:return None
        if any(not math.isfinite(x) or x<0 for x in parts):return None
        if len(parts)==1:minute=parts[0]
        elif len(parts)==2 and parts[1]<60:minute=parts[0]+parts[1]/60
        elif len(parts)==3 and parts[1]<60 and parts[2]<60:minute=parts[0]*60+parts[1]+parts[2]/60
        else:return None
    if period in {"1h","first half","first_half","1","first"}:return minute
    if period in {"2h","second half","second_half","2","second"}:return minute+45 if minute<45 else minute
    return None


def raw_fee_rate(market):
    if market.get("feesEnabled") is False:return 0.
    schedule=market.get("feeSchedule")
    if market.get("feesEnabled") is not True or not isinstance(schedule,dict):return None
    raw_rate=schedule.get("rate");exponent=schedule.get("exponent")
    if isinstance(raw_rate,bool) or isinstance(exponent,bool):return None
    rate=visual.number(raw_rate)
    if exponent!=1 or schedule.get("takerOnly") is not True or rate is None or not 0<=rate<=1:return None
    return rate


def uses_recorded_fees(event):
    return event.source.startswith(("polybot-white:","polybot-sim-guava-")) or event.config.get("raw_point_in_time_archive") is True


def read_source(source, start, end):
    path = Path(source["local_path"]).resolve()
    for suffix in ("-wal", "-journal"):
        p = Path(str(path)+suffix)
        if p.exists() and p.stat().st_size:
            raise ValueError("source not a standalone verified pin")
    digest = visual.sha256(path)
    if digest != source["local_sha256"] or not source.get("pinned"):
        raise ValueError("source is not the verified pinned manifest")
    conn = sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ValueError("source SQLite quick_check failed")
    white = source["strategy"] == "golden-watermelon"
    runs, configs = visual.read_runs(conn, white), visual.read_configs(conn, white)
    end_time = visual.timestamp(end)
    # A SUCCESS written after the range end was not evidence available by cutoff.
    for r in runs.values():
        finish = visual.timestamp(r.get("finished_at"))
        if finish is None or finish >= end_time:
            r["status"] = "INCOMPLETE_AT_CUTOFF"
    terminals = visual.terminal_records(conn, white, runs, end=end)
    raw_configs = {}
    table = "research_config_versions" if white else "strategy_configs"
    for row in conn.execute(f"SELECT config_hash,config_json FROM {table}"):
        raw_configs[row["config_hash"]] = json.loads(row["config_json"]).get("trading", {})
    metadata = {}
    if white:
        for raw in conn.execute("SELECT run_id,condition_id,liquidity,volume_total,event_live,event_ended,active,closed,accepting_orders,fee_rate,fee_schedule_json FROM market_observations WHERE eligible=1"):
            r = dict(raw); key = (r["run_id"], r["condition_id"])
            if key in metadata and metadata[key] != r:
                metadata[key] = {}
            else: metadata[key] = r
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    catalogs = {} if white or "market_catalog" not in tables else {r["condition_id"]: dict(r) for r in conn.execute("SELECT * FROM market_catalog")}
    original = WhiteOriginalAskEvidence(conn) if white else None
    grouped = defaultdict(lambda: defaultdict(list)); titles={}; stats=Counter()
    get_rows = visual.white_rows if white else visual.trading_rows
    for row in get_rows(conn, start, end):
        stats["raw_rows"] += 1
        run = runs.get(row["run_id"], {})
        cfg_hash = run.get("config_hash", "missing")
        cfg = configs.get(cfg_hash, {})
        is_raw=bool(row.get("raw_evidence_origin"))
        if is_raw:stats["point_in_time_raw_rows"]+=1
        sport = row.get("sport_family") or cfg.get("sport") or "unknown"
        if sport not in {"soccer", "mlb", "nfl", "nba", "nhl"}:
            stats["unsupported_sport_rows"] += 1; continue
        valid = (run.get("status") == "SUCCESS" and bool(cfg.get("strategy_source_digest"))
                 and row.get("config_hash", cfg_hash) == cfg_hash
                 and row.get("strategy_source_digest",cfg.get("strategy_source_digest")) == cfg.get("strategy_source_digest")
                 )
        if not white:
            for key in ("sport_profile_version", "protocol_sha256", "classifier_version", "league_mapping_sha256"):
                valid = valid and row.get(key,cfg.get(key)) == cfg.get(key)
            if is_raw:
                valid=(valid and row.get("raw_evidence_origin")=="full-sports-raw-v1"
                       and row.get("raw_point_in_time_identity_proven") is True
                       and row.get("raw_book_valid") is True
                       and row.get("raw_book_integrity_error") is False
                       and row.get("raw_publication_within_cutoff") is True
                       and bool(row.get("raw_event_observation_id"))
                       and bool(row.get("condition_id")) and bool(row.get("token_id")))
            else:
                cat = catalogs.get(row["condition_id"], {})
                try:
                    labels = json.loads(cat["outcomes_json"]); tokens = json.loads(cat["token_ids_json"])
                    valid = valid and cat.get("event_id") == row["event_id"] and len(set(tokens)) == len(labels) == 2
                    valid = valid and tokens[labels.index(row["outcome"])] == row["token_id"]
                except (KeyError, ValueError, TypeError): valid=False
        raw = row["book"]
        asks = bids = ()
        ask_state = ask_side_state(raw)
        try:
            if str(raw.get("token_id", row["token_id"])) != str(row["token_id"]):
                raise ValueError("book_token_mismatch")
            asks, bids = levels(raw, "asks"), levels(raw, "bids")
            if asks and bids and bids[0][0] > asks[0][0] + EPS: raise ValueError("crossed_book")
        except ValueError:
            valid=False; stats["invalid_book_rows"] += 1
        if white and bids and bids[0][0] >= .99:
            ask_state = original.state(row["id"],row["token_id"])
            valid = valid and ask_state != "INVALID"
        spread = asks[0][0]-bids[0][0] if asks and bids else None
        minute = visual.number(row.get("source_elapsed_minutes"))
        meta = metadata.get((row["run_id"],row["condition_id"]), {})
        point_rate = None
        raw_market_open=None
        raw_scheduled_age=None
        if is_raw:
            fields=row.get("clock",{}).get("source_sport_context",{}).get("fields",{})
            market=row.get("point_in_time_market_fields") or {}
            minute=raw_source_minute(fields,sport)
            archive=row.get("clock",{}).get("raw_archive",{})
            if row.get("raw_book_valid") is True and archive.get("timestamp_basis")=="BOOK_RECEIPT":
                try:
                    scheduled=visual.timestamp(row.get("game_start"))
                    if scheduled is not None:raw_scheduled_age=(visual.timestamp(row["timestamp"])-scheduled)/60
                except (TypeError,ValueError):pass
            raw_market_open=(str(market.get("conditionId") or "")==str(row["condition_id"])
                             and market.get("active") is True and market.get("closed") is False
                             and market.get("enableOrderBook") is True and market.get("acceptingOrders") is True)
            opened=(valid and row.get("raw_tradable_observed") is True
                    and str(fields.get("id") or "")==str(row["event_id"])
                    and fields.get("active") is True and fields.get("closed") is False
                    and fields.get("live") is True and fields.get("ended") is False
                    and str(market.get("conditionId") or "")==str(row["condition_id"])
                    and market.get("active") is True and market.get("closed") is False
                    and market.get("enableOrderBook") is True and market.get("acceptingOrders") is True)
            liquidity=visual.number(market.get("liquidityNum",market.get("liquidity")))
            volume=visual.number(market.get("volumeNum",market.get("volume")))
            gated=opened and liquidity is not None and liquidity>=5000 and volume is not None and volume>=5000
            point_rate=raw_fee_rate(market)
        elif white:
            opened = (meta.get("event_live") == 1 and meta.get("event_ended") == 0
                      and meta.get("active") == 1 and meta.get("closed") == 0 and meta.get("accepting_orders") == 1)
            gated = opened and (meta.get("liquidity") or 0)>=5000 and (meta.get("volume_total") or 0)>=5000
            try:
                schedule = json.loads(meta["fee_schedule_json"])
                if schedule.get("exponent") == 1 and schedule.get("rate") == meta.get("fee_rate"):
                    point_rate = visual.number(schedule["rate"])
            except (KeyError,TypeError,ValueError): pass
        else:
            # The source classifies these rows as live observations. Direct MLB
            # has no native elapsed minute; this does not invent an inning clock.
            if source["strategy"]=="golden-peach":
                opened=valid and (minute is not None or row.get("source_clock_reason")=="SCHEDULED_START_AGE_SHADOW_ONLY")
            else:
                opened = bool(row.get("event_cycle_id")) and valid
            gated = False
        snapshot = Snap(str(row["token_id"]), row["condition_id"],
                        (row.get("result_kind"),row.get("outcome_side")),
                        visual.timestamp(row["timestamp"]),row["run_id"],minute,
                        visual.number(row.get("midpoint")),asks,bids,bool(valid),bool(opened),bool(gated),
                        point_rate,spread,ask_state,str(row.get("source_clock_reason") or ""),walk(asks,5,True),
                        visual.number(row.get("clock",{}).get("scheduled_age_minutes")) if not is_raw else raw_scheduled_age,
                        row.get("raw_entry_set_complete") is True if is_raw else row.get("event_set_complete",1)==1,
                        str(row.get("raw_evidence_origin") or "legacy"),row.get("raw_observation_status"),raw_market_open,
                        row.get("outcome"),row.get("verified_role"),row.get("verified_team_name"),
                        row.get("role_evidence_scope"),row.get("legacy_result_kind",row.get("result_kind")),
                        row.get("legacy_role_semantics"))
        grouped[(cfg_hash,sport,row["event_id"])][row["run_id"]].append(snapshot)
        titles[row["event_id"]] = row["title"]
        stats["valid_rows" if valid else "invalid_metadata_or_run_rows"] += 1
    events=[]
    for (cfg_hash,sport,event_id), by_run in grouped.items():
        cfg=configs.get(cfg_hash,{})
        groups=sorted((normalize_group(ss,sport,white) for ss in by_run.values()),key=lambda g:g.time)
        failures=[(visual.timestamp(r["started_at"]),visual.timestamp(r.get("finished_at")))
                  for r in runs.values() if r.get("status") != "SUCCESS"
                  and r.get("config_hash")==cfg_hash and visual.timestamp(r["started_at"])<end_time]
        filtered_terminals={}
        for k, ts in terminals.items():
            if source["strategy"] == "golden-plum":
                ts=[p for p in ts if all(p.get(pkey)==cfg.get(ckey) for pkey,ckey in
                    (("profile","sport_profile_version"),("protocol","protocol_sha256"),("classifier","classifier_version"),("mapping","league_mapping_sha256")))]
            filtered_terminals[k]=ts
        cohort=visual.identifier([source["id"],cfg_hash,sport])
        config={**cfg,"trading":raw_configs.get(cfg_hash,{})}
        origins={s.evidence_origin for g in groups for s in g.snaps}
        if "full-sports-raw-v1" in origins:
            config["raw_point_in_time_archive"]=origins=={"full-sports-raw-v1"}
            config["evidence_origins"]=sorted(origins)
        events.append(Event(source["id"],cohort,sport,event_id,titles[event_id],config,groups,failures,filtered_terminals))
    conn.close()
    if visual.sha256(path)!=digest: raise ValueError("source changed during replay")
    return events,{"source":source["id"],"path":str(path),"sha256":digest,"stats":dict(stats),
                   "events":len(events),"original_ask_audit":dict(original.audit) if original else None}


def policy_names(event):
    if event.source.startswith("polybot-sim-guava-"):
        peach=(("peach_rank","peach_cross_rank","peach_scheduled_age_rank","peach_scheduled_age_cross_rank")
               if event.sport!="soccer" else ("peach_rank","peach_cross_rank"))
        return peach+("plum_price_rank","plum_trend_rank","watermelon_rank")
    if event.source.startswith("polybot-grey:"):
        return (("peach_rank","peach_cross_rank","peach_scheduled_age_rank","peach_scheduled_age_cross_rank")
                if event.sport!="soccer" else ("peach_rank","peach_cross_rank"))
    if event.source.startswith(("polybot-silver:","polybot-gold:")): return ("plum_price_rank","plum_trend_rank")
    return ("watermelon_rank",)


def policy_rank_count(event,policy):
    return (3 if policy=="watermelon_rank" else 6) if event.sport=="soccer" else 2


def observation_groups(event,policy):
    if policy!="watermelon_rank" or event.sport!="soccer":return event.groups
    if not hasattr(event,"_watermelon_groups"):
        projected=[]
        for g in event.groups:
            ss=[s for s in g.snaps if s.slot[1]=="YES"]
            projected.append(normalize_group(ss,event.sport,True) if ss else Group(g.time,g.run,[],False))
        event._watermelon_groups=projected
    return event._watermelon_groups


def policy_knobs(policy,sport):
    if policy.startswith("peach"):
        return {"stop_delta":.10 if sport=="soccer" else .20,"stop_floor":.01,
                "stop_cutoff":80 if sport=="soccer" else None,"late_minute":80 if sport=="soccer" else None,
                "stop_max_loss":1.0,"stop_max_spread":.10,"stop_slippage":.05,"execution":"current_source_full_book_gap_fallback"}
    if policy.startswith("plum"):
        return {"stop_delta":.15 if sport=="soccer" else .12,"stop_floor":.01,"stop_cutoff":None,"late_minute":None,
                "stop_max_loss":1.0,"stop_max_spread":.10,"stop_slippage":.05,"execution":"current_source_full_book_gap_fallback"}
    return {"stop_delta":.30,"stop_floor":.70,"stop_cutoff":None,"late_minute":None,
            "stop_max_loss":.35,"stop_max_spread":.10,"stop_slippage":.05,"execution":"current_source_full_book_gap_fallback"}


def trend_matches(history,current,threshold,entry_width=.03,max_gap=90,source_clock_required=True,observations=3,min_move=.02):
    values=history[-(observations-1):]+[current]
    if len(values)!=observations or any(s.buy is None for s in values):return False
    prices=[s.buy.vwap for s in values]
    if any(not math.isfinite(p) or not 0<p<1 for p in prices):return False
    minutes=[s.minute for s in values]
    if any(m is None for m in minutes):
        if source_clock_required or not all(m is None for m in minutes):return False
    elif any(not math.isfinite(m) or m<0 for m in minutes) or any(b+EPS<a for a,b in zip(minutes,minutes[1:])):
        return False
    if any(not 0<b.time-a.time<=max_gap+EPS for a,b in zip(values,values[1:])):return False
    return (prices[-2]<threshold-EPS and threshold-EPS<=prices[-1]<=threshold+entry_width+EPS
            and prices[-1]-prices[0]>=min_move-EPS
            and max(0.,max(a-b for a,b in zip(prices,prices[1:])))<=.01+EPS)


def candidates(event,policy,rank,threshold,max_gap=90,entry_width=.03,require_clear_rank=False,selection_mode="rank_midpoint"):
    """First band-qualified observation; no future-dependent rank selection."""
    history=defaultdict(list); diagnostics=Counter()
    observation_sequence=observation_groups(event,policy)
    for i,g in enumerate(observation_sequence):
        # Every physical row can invalidate continuity, never success-only rows.
        if i and (g.time-event.groups[i-1].time>max_gap or failed_between(event.failures,event.groups[i-1].time,g.time)):
            history.clear()
        if not g.complete:
            history.clear(); diagnostics["incomplete_set"]+=1; continue
        if rank>len(g.snaps): continue
        s=g.snaps[rank-1]
        if selection_mode=="max_eligible_ask":
            eligible_asks=[q for q in g.snaps if q.buy and q.gate_observed and q.spread is not None and 0<=q.spread<=.05+EPS
                           and threshold-EPS<=q.buy.vwap<=min(.999,threshold+entry_width)+EPS]
            if not eligible_asks:continue
            s=sorted(eligible_asks,key=lambda q:(-q.buy.vwap,q.token))[0]
        eligible=True
        if policy.startswith("peach"):
            native=s.scheduled_age if "scheduled_age" in policy else s.minute
            eligible=native is not None and 0<=native<=10 and s.open_observed
            if not eligible: diagnostics["missing_native_kickoff_clock_or_outside_window"]+=1
        elif policy=="watermelon_rank":
            eligible=s.gate_observed
        else:
            eligible=s.open_observed and (event.sport!="soccer" or (s.minute is not None and s.minute>=0))
        if not s.buy or s.spread is None or not 0<=s.spread<=.05+EPS: eligible=False
        if require_clear_rank and rank in g.ambiguous:eligible=False
        if policy.startswith("peach") and "cross" in policy:
            h=history[s.token]
            eligible=eligible and bool(h) and h[-1].buy.vwap<threshold-EPS and s.buy.vwap>=threshold-EPS
            if not h: diagnostics["left_censored_crossing"]+=1
        if policy=="plum_trend_rank":
            good=trend_matches(history[s.token],s,threshold,entry_width,max_gap,event.sport=="soccer",
                               3 if event.sport=="soccer" else 5,.02 if event.sport=="soccer" else .01)
            eligible=eligible and good
        if eligible and threshold-EPS<=s.buy.vwap<=min(.999,threshold+entry_width)+EPS:
            return (i,s,rank in g.ambiguous),dict(diagnostics)
        for q in g.snaps:
            history[q.token].append(q)
            history[q.token]=history[q.token][-5:]
    return None,dict(diagnostics)


def make_path(event,policy,candidate,max_gap=90):
    i,entry,ambiguous=candidate
    q=math.floor((entry.buy.shares+1e-10)*100)/100
    knob=policy_knobs(policy,event.sport)
    result={"id":visual.identifier([event.cohort,event.event,policy,entry.run,entry.token]),
            "event":event.event,"title":event.title,"cohort":event.cohort,"sport":event.sport,
            "source":event.source,"policy":policy,"partition":event.partition,"day":event.partition_day,
            "entry_time":entry.time,"entry_utc":iso(entry.time),"entry_token":entry.token,
            "entry_condition":entry.condition,"entry_result":entry.slot[0],"entry_side":entry.slot[1],
            "entry_outcome_label":entry.outcome_label,
            "entry_team_name":(entry.verified_team_name or entry.outcome_label) if entry.slot[1]=="DIRECT" else None,
            "entry_verified_role":(entry.verified_role or "UNKNOWN") if entry.slot[1]=="DIRECT" else None,
            "entry_verified_team_name":entry.verified_team_name,
            "entry_role_evidence_scope":entry.role_evidence_scope,
            "entry_legacy_result_kind":entry.legacy_result_kind if entry.legacy_result_kind is not None else entry.slot[0],
            "entry_result_kind_semantics":entry.legacy_role_semantics or "LEGACY_STORED_RESULT_SLOT",
            "entry_vwap":entry.buy.vwap,"buy_walk":asdict(entry.buy),"entry_fee_rate":entry.fee_rate,
            "gross_sell_shares_diagnostic":q,"gross_sdk_dust_diagnostic":entry.buy.shares-q,"ambiguous_rank":ambiguous,
            "end_reason":"right_censored","stop_price":max(knob["stop_floor"],entry.buy.vwap-knob["stop_delta"]),
            "knobs":knob,"observations":[],"terminal_payout":None}
    proofs=[p for p in event.terminals.get((entry.condition,entry.token),[])
            if visual.timestamp(p["observed_at"])>=entry.time]
    earliest_proof=(min(proofs,key=lambda p:visual.timestamp(p["observed_at"]))
                    if proofs and len({p["payout"] for p in proofs})==1 else None)
    previous=event.groups[i].time
    for g in event.groups[i+1:]:
        if earliest_proof is not None:
            terminal_time=visual.timestamp(earliest_proof["observed_at"])
            if (previous<=terminal_time<=g.time and terminal_time-previous<=max_gap
                    and not failed_between(event.failures,previous,terminal_time)):
                result["end_reason"]="RESOLUTION"
                break
        if g.time<=previous: result["end_reason"]="non_increasing_observation";break
        if g.time-previous>max_gap or failed_between(event.failures,previous,g.time):
            result["end_reason"]="path_gap_or_failed_run";break
        same=[s for s in g.snaps if s.token==entry.token and s.condition==entry.condition]
        if len(same)!=1 or not same[0].valid:
            result["end_reason"]="selected_token_or_lineage_gap";break
        s=same[0];previous=g.time
        if (s.evidence_origin in {"full-sports-raw-v1", "watermelon-independent-raw-lifecycle-v1"} and policy.startswith("peach")
                and event.sport=="soccer" and s.minute is None):
            result["end_reason"]="required_source_clock_gap";break
        if not s.bids:
            if s.evidence_origin in {"full-sports-raw-v1", "watermelon-independent-raw-lifecycle-v1"} and s.observation_status in ("EMPTY_BIDS","EMPTY_BOOK"):
                result["observations"].append({"time":g.time,"utc":iso(g.time),"minute":s.minute,
                    "gross_walk_diagnostic":None,"bids":(),"spread":s.spread,"open_observed":s.open_observed,
                    "market_open_observed":s.market_open_observed,"best_bid":None,"fee_rate":s.fee_rate,
                    "stop":False,"stop_trigger":False,"tp_preflight":False,
                    "observation_status":s.observation_status,"explicit_no_marketable_depth":True})
                continue
            result["end_reason"]="missing_bid_depth";break
        w=walk(s.bids,q,False)
        best=s.bids[0][0]
        stop_active=knob["stop_cutoff"] is None or (s.minute is not None and s.minute<knob["stop_cutoff"]-EPS)
        stop_trigger=stop_active and best<=result["stop_price"]+EPS
        stop_execution=(w is not None and s.open_observed and s.spread is not None
                        and 0<=s.spread<=knob["stop_max_spread"]+EPS
                        and 0<w.vwap<1 and 0<w.worst<1 and 0<best<1)
        tp_preflight=(s.open_observed and (s.ask_state=="EMPTY_VALID" or
                       (s.ask_state=="PRESENT_VALID" and s.spread is not None and 0<=s.spread<=.10+EPS)))
        result["observations"].append({"time":g.time,"utc":iso(g.time),"minute":s.minute,
            "gross_walk_diagnostic":asdict(w) if w else None,"bids":s.bids,"spread":s.spread,"open_observed":s.open_observed,
            "market_open_observed":s.market_open_observed,"observation_status":s.observation_status,
            "best_bid":best,"fee_rate":s.fee_rate,
            "stop":stop_trigger and stop_execution,"stop_trigger":stop_trigger,"tp_preflight":tp_preflight})
    proofs=[p for p in event.terminals.get((entry.condition,entry.token),[])
            if visual.timestamp(p["observed_at"])>=entry.time]
    if proofs and len({p["payout"] for p in proofs})==1:
        p=min(proofs,key=lambda v:visual.timestamp(v["observed_at"]))
        result["terminal_payout"]=p["payout"]
        result["terminal_observer_config"]=p.get("observer_config")
        result["terminal_entry_config"]=event.config.get("config_hash")
        result["terminal_same_config"]=p.get("observer_config")==event.config.get("config_hash")
        result["terminal_observed_utc"]=p["observed_at"]
        t=visual.timestamp(p["observed_at"])
        if result["end_reason"]=="right_censored" and 0<=t-previous<=max_gap and not failed_between(event.failures,previous,t):
            result["end_reason"]="RESOLUTION"
    return result


def initial_result(path,target_mode,target_value,model,fee_collection="v2_cash"):
    entry=path["entry_vwap"]
    target=2. if target_mode=="hold" else target_value if target_mode=="absolute" else entry+target_value
    out={"status":None,"net":None,"reason":None,"exit_utc":None,"exit_vwap":None,
         "tp_fee_nonpositive_observations":0,"blocked_full_depth_observations":0}
    if target<=entry+EPS:return target,{**out,"status":"REJECTED","reason":"target_not_above_actual_entry"},None
    if target>1-EPS and target_mode!="hold":return target,{**out,"status":"REJECTED","reason":"target_outside_binary_price"},None
    if path["stop_price"]>=entry-EPS:return target,{**out,"status":"REJECTED","reason":"stop_not_below_actual_entry"},None
    economics=buy_economics(path,model,fee_collection)
    if economics is None:return target,{**out,"status":"CENSORED","reason":"entry_fee_evidence_missing","lower":None,"upper":None},None
    return target,{**out,**economics,"lower":-economics["usd_cost"],"upper":economics["sell_shares"]-economics["usd_cost"]},economics


def finish_unexited(path,out,economics):
    if path["end_reason"]=="RESOLUTION":
        return {**out,"status":"COMPLETE","reason":"RESOLUTION",
                "net":economics["sell_shares"]*path["terminal_payout"]-economics["usd_cost"],
                "exit_utc":path["terminal_observed_utc"],"exit_vwap":path["terminal_payout"]}
    return {**out,"status":"CENSORED","reason":path["end_reason"]}


def replay_path(path,target_mode,target_value,fee_model,tp_policy="positive_full",fee_collection="v2_cash"):
    target,out,economics=initial_result(path,target_mode,target_value,fee_model,fee_collection)
    if out["status"] is not None:return out
    for obs in modeled_observations(path,fee_model,economics):
        w=obs["walk"]
        if w is None:
            out["blocked_full_depth_observations"]+=1;continue
        if obs["sell_fee"] is None:
            return {**out,"status":"CENSORED","reason":"exit_fee_evidence_missing","lower":None,"upper":None}
        net=w.amount-obs["sell_fee"]-economics["usd_cost"]
        late=path["knobs"]["late_minute"]
        late_now=late is not None and obs["minute"] is not None and obs["minute"]>=late
        effective=path["entry_vwap"]+(target-path["entry_vwap"])*.5 if late_now else target
        if obs["stop"]:
            return {**out,"status":"COMPLETE","reason":"SL","net":net,"exit_utc":obs["utc"],"exit_vwap":w.vwap}
        if tp_policy=="plum_published" and obs["tp_preflight"]:
            at_target=sum(q for p,q in obs["bids"] if p>=effective-EPS)
            if min(5.,economics["sell_shares"])-EPS<=at_target<economics["sell_shares"]-EPS:
                return {**out,"status":"CENSORED","reason":"published_partial_tp_lifecycle_not_replayed"}
        hit=(w.vwap if tp_policy=="peach_published" else w.worst)>=effective-EPS
        if obs["tp_preflight"] and hit:
            if tp_policy!="positive_full" or net>.001:
                return {**out,"status":"COMPLETE","reason":"LATE_TP" if late_now else "TP","net":net,"exit_utc":obs["utc"],"exit_vwap":w.vwap}
            out["tp_fee_nonpositive_observations"]+=1
    return finish_unexited(path,out,economics)


def path_grid(path,grid,fee_model,fee_collection="v2_cash"):
    """All targets share a fee-specific net position, not a gross-share path."""
    results={};pending={};economics=None
    for mode in ("absolute","relative"):
        for value in grid:
            target,out,econ=initial_result(path,mode,value,fee_model,fee_collection)
            if out["status"] is not None:results[(mode,value)]=out
            else:pending[(mode,value)]=(target,out);economics=econ
    if not pending:return results
    for obs in modeled_observations(path,fee_model,economics):
        if not pending:break
        w=obs["walk"]
        if w is None:
            for target,out in pending.values():out["blocked_full_depth_observations"]+=1
            continue
        if obs["sell_fee"] is None:
            for key,(target,out) in pending.items():
                results[key]={**out,"status":"CENSORED","reason":"exit_fee_evidence_missing","lower":None,"upper":None}
            pending.clear();break
        net=w.amount-obs["sell_fee"]-economics["usd_cost"]
        late=path["knobs"]["late_minute"]
        late_now=late is not None and obs["minute"] is not None and obs["minute"]>=late
        done=[]
        for key,(target,out) in pending.items():
            effective=path["entry_vwap"]+(target-path["entry_vwap"])*.5 if late_now else target
            if obs["stop"]:
                results[key]={**out,"status":"COMPLETE","reason":"SL","net":net,"exit_utc":obs["utc"],"exit_vwap":w.vwap};done.append(key)
            elif obs["tp_preflight"] and w.worst>=effective-EPS:
                if net>.001:
                    results[key]={**out,"status":"COMPLETE","reason":"LATE_TP" if late_now else "TP","net":net,"exit_utc":obs["utc"],"exit_vwap":w.vwap};done.append(key)
                else:out["tp_fee_nonpositive_observations"]+=1
        for key in done:del pending[key]
    for key,(target,out) in pending.items():results[key]=finish_unexited(path,out,economics)
    return results


def wilson_lower(wins,n,z=1.959963984540054):
    if n<=0:return None
    p=wins/n;z2=z*z
    return max(0,(p+z2/(2*n)-z*math.sqrt(p*(1-p)/n+z2/(4*n*n)))/(1+z2/n))


@dataclass
class Cell:
    events: int=0
    entered: int=0
    rejected: int=0
    completed: int=0
    censored: int=0
    positive: int=0
    ambiguous: int=0
    fee_nonpositive: int=0
    depth_blocked: int=0
    unknown_bounds: int=0
    net: float=0.0
    lower: float=0.0
    upper: float=0.0
    worst: float | None=None
    reasons: Counter=field(default_factory=Counter)

    def add(self,path,result):
        self.events+=1
        if path is None:return
        if result["status"]=="REJECTED":
            self.rejected+=1;self.reasons[result["reason"]]+=1;return
        self.entered+=1;self.ambiguous+=bool(path["ambiguous_rank"])
        self.reasons[result["reason"]]+=1
        self.fee_nonpositive+=result["tp_fee_nonpositive_observations"]
        self.depth_blocked+=result["blocked_full_depth_observations"]
        if result["status"]=="COMPLETE":
            v=result["net"];self.completed+=1;self.positive+=v>0
            self.net+=v;self.lower+=v;self.upper+=v
            self.worst=v if self.worst is None else min(self.worst,v)
        else:
            self.censored+=1
            if result.get("lower") is None or result.get("upper") is None:
                self.unknown_bounds+=1
            else:
                self.lower+=result["lower"];self.upper+=result["upper"]


    def summary(self,multiplicity=1):
        # Multiplicity Bonferroni Hoeffding is valid without independent cells;
        # events still need independence, explicitly unproven in this archive.
        conservative=self.positive/self.entered if self.entered else None
        family_lb=max(0,conservative-math.sqrt(math.log(max(1,multiplicity)/.05)/(2*self.entered))) if self.entered else None
        return {"events":self.events,"entered":self.entered,"no_entry":self.events-self.entered-self.rejected,
                "rejected_overshoot_or_domain":self.rejected,"complete":self.completed,"censored":self.censored,
                "positive":self.positive,"ambiguous_rank_entries":self.ambiguous,
                "tp_fee_nonpositive_observations":self.fee_nonpositive,"blocked_full_depth_observations":self.depth_blocked,
                "completion_rate":self.completed/self.entered if self.entered else None,
                "positive_rate_complete":self.positive/self.completed if self.completed else None,
                "positive_rate_censored_as_failure":conservative,
                "wilson95_lower_censored_as_failure":wilson_lower(self.positive,self.entered),
                "family95_hoeffding_lower":family_lb,"complete_net":self.net if self.completed else None,
                "unknown_bound_entries":self.unknown_bounds,
                "all_entry_net_lower":self.lower if self.entered and not self.unknown_bounds else None,
                "all_entry_net_upper":self.upper if self.entered and not self.unknown_bounds else None,
                "worst_complete":self.worst,"reasons":json.dumps(dict(self.reasons),sort_keys=True,separators=(",",":"))}


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))


def run_grid(events,output,grid=PRICE_GRID,fee_models=FEE_MODELS,max_gap=90,entry_width=.03,fee_collection="v2_cash"):
    identities=[(e.cohort,e.event) for e in events]
    if len(set(identities))!=len(identities):raise ValueError("duplicate source-cohort-event input")
    output.mkdir(parents=True,exist_ok=True)
    by_cohort=defaultdict(list)
    for e in events:by_cohort[e.cohort].append(e)
    global_hypotheses=sum(sum(policy_rank_count(es[0],p) for p in policy_names(es[0]))*len(grid)**2*2 for es in by_cohort.values())
    summaries=[];cohort_meta=[];path_count=0;cell_count=0;entry_count=0
    csv_columns=None
    with gzip.open(output/"cells.csv.gz","wt",encoding="utf-8",newline="",compresslevel=3) as cell_file, \
         gzip.open(output/"paths.jsonl.gz","wt",encoding="utf-8",compresslevel=3) as paths_file, \
         gzip.open(output/"entries.csv.gz","wt",encoding="utf-8",newline="",compresslevel=3) as entries_file:
        entry_writer=csv.DictWriter(entries_file,fieldnames=["cohort","event","policy","rank","entry_threshold","path_id","diagnostics"])
        entry_writer.writeheader()
        cell_writer=None
        for cohort,cohort_events in by_cohort.items():
            first=cohort_events[0]
            meta={"cohort":cohort,"source":first.source,"sport":first.sport,"config":first.config,
                  "events":len(cohort_events),"first":iso(min(e.groups[0].time for e in cohort_events)),
                  "last":iso(max(e.groups[-1].time for e in cohort_events)),"all_raw_groups":sum(len(e.groups) for e in cohort_events),
                  "complete_groups":sum(sum(g.complete for g in e.groups) for e in cohort_events)}
            cohort_meta.append(meta)
            policies=policy_names(first)
            cohort_models=tuple(fee_models)+(("recorded_schedule",) if uses_recorded_fees(first) else ())
            primary_model="recorded_schedule" if uses_recorded_fees(first) else "sports_005"
            meta["primary_fee_model"]=primary_model
            rank_total=sum(policy_rank_count(first,p) for p in policies)
            multiplicity=rank_total*len(grid)**2*2
            cache={}
            top=[];signatures=set();nonempty_signatures=set()
            for policy in policies:
                for rank in range(1,policy_rank_count(first,policy)+1):
                    for entry in grid:
                        mapped=[]
                        for ev in cohort_events:
                            found,diag=candidates(ev,policy,rank,entry,max_gap,entry_width)
                            path=None
                            if found:
                                key=(ev.event,policy,found[1].run,found[1].token)
                                if key not in cache:
                                    cache[key]=make_path(ev,policy,found,max_gap)
                                    paths_file.write(json.dumps(cache[key],ensure_ascii=False,separators=(",",":"))+"\n");path_count+=1
                                path=cache[key]
                            mapped.append((ev,path))
                            entry_writer.writerow({"cohort":cohort,"event":ev.event,"policy":policy,"rank":rank,
                                                   "entry_threshold":entry,"path_id":path["id"] if path else "",
                                                   "diagnostics":json.dumps(diag,separators=(",",":"))})
                            entry_count+=1
                        evaluations={}
                        for ev,path in mapped:
                            if path is not None:
                                evaluations[path["id"]]={model:path_grid(path,grid,model,fee_collection) for model in cohort_models}
                        for mode in ("absolute","relative"):
                            for target in grid:
                                for model in cohort_models:
                                    all_cell=Cell();partitions=defaultdict(Cell);signature_parts=[]
                                    for ev,path in mapped:
                                        result=evaluations[path["id"]][model][(mode,target)] if path else None
                                        if path and result["status"]!="REJECTED":
                                            signature_parts.append((ev.event,path["entry_time"],path["entry_token"],result["status"],result["reason"],result["exit_utc"]))
                                        all_cell.add(path,result);partitions[ev.partition].add(path,result);partitions["day:"+ev.partition_day].add(path,result)
                                    signature=visual.identifier(signature_parts)
                                    if model==primary_model:
                                        signatures.add(signature)
                                        if signature_parts:nonempty_signatures.add(signature)
                                    base={"fee_collection":fee_collection,"execution_signature":signature,"cohort":cohort,"source":first.source,"sport":first.sport,"policy":policy,
                                          "rank":rank,"entry_threshold":entry,"target_mode":mode,"target_value":target,"fee_model":model}
                                    for partition,cell in [("all",all_cell),*sorted(partitions.items())]:
                                        record={**base,"partition":partition,**cell.summary(multiplicity)}
                                        record["global_batch95_hoeffding_lower"]=(max(0,cell.positive/cell.entered-math.sqrt(math.log(global_hypotheses/.05)/(2*cell.entered))) if cell.entered else None)
                                        if cell_writer is None:
                                            csv_columns=list(record);cell_writer=csv.DictWriter(cell_file,fieldnames=csv_columns);cell_writer.writeheader()
                                        cell_writer.writerow(record);cell_count+=1
                                        if partition=="all" and model==primary_model and cell.entered>=3 and cell.completed and not cell.unknown_bounds:
                                            top.append(record)
            meta.update(parameter_cell_count=rank_total*len(grid)**2*2,
                        distinct_execution_signatures=len(signatures),distinct_nonempty_execution_signatures=len(nonempty_signatures))
            # Preserve a modest diagnostic shortlist only; raw ALL cells remain above.
            top.sort(key=lambda r:(r["wilson95_lower_censored_as_failure"] or 0,r["completion_rate"] or 0,r["all_entry_net_lower"] if r["all_entry_net_lower"] is not None else -1e12,r["worst_complete"] if r["worst_complete"] is not None else -1e12),reverse=True)
            used_signatures=set();selected=[]
            for candidate in top:
                if candidate["execution_signature"] not in used_signatures:
                    selected.append(candidate);used_signatures.add(candidate["execution_signature"])
                    if len(selected)==30:break
            summaries.extend(selected)
            print(json.dumps({"cohort":cohort,"source":first.source,"sport":first.sport,"events":len(cohort_events),
                              "paths":len(cache),"total_cells_written":cell_count}),flush=True)
    return {"cohorts":cohort_meta,"shortlist_diagnostic_only":summaries,
            "path_count":path_count,"entry_mapping_rows":entry_count,"cell_rows":cell_count,"columns":csv_columns,
            "parameter_hypotheses_this_batch":global_hypotheses,"family_bound_scope":"family95=within_cohort; global_batch95=all_cohorts_in_this_batch"}


def baseline_rows(events,output,fee_collection="v2_cash"):
    """Named existing policies plus predeclared small-profit alternatives.

    These are paired *per entry*, using the same source-cohort-event and fees;
    no live trade ledger filters the raw market opportunity population.
    """
    rows=[]
    for ev in events:
        if ev.source.startswith("polybot-grey:"):
            policy="peach_rank" if ev.sport=="soccer" else "peach_scheduled_age_rank"
            definitions=[("peach_existing_A",policy,.60,.34,"relative",.03 if ev.sport=="soccer" else .07),
                         ("peach_existing_B",policy,.60,.34,"relative",.05 if ev.sport=="soccer" else .10),
                         ("peach_user_60_plus02",policy,.60,.03,"relative",.02)]
        elif ev.source.startswith(("polybot-silver:","polybot-gold:")):
            policy="plum_trend_rank"
            threshold=.75 if ev.sport=="soccer" else .55
            targets=(.90,.95) if ev.sport=="soccer" else (.65,.70)
            definitions=[("plum_existing_A",policy,threshold,.03,"absolute",targets[0]),
                         ("plum_existing_B",policy,threshold,.03,"absolute",targets[1]),
                         ("plum_user_70_to72","plum_price_rank",.70,.03,"absolute",.72),
                         ("plum_user_70_plus02","plum_price_rank",.70,.03,"relative",.02)]
        else:
            definitions=[("watermelon_existing_cat_hold","watermelon_rank",.96,.039,"hold",0),
                         ("watermelon_existing_dog_hold","watermelon_rank",.99,.009,"hold",0),
                         ("watermelon_95_to99","watermelon_rank",.95,.039,"absolute",.99),
                         ("watermelon_97_to99","watermelon_rank",.97,.019,"absolute",.99),
                         ("watermelon_user_92_to96","watermelon_rank",.92,.03,"absolute",.96),
                         ("watermelon_user_96_to97","watermelon_rank",.96,.03,"absolute",.97)]
        if ev.source.startswith("polybot-sim-guava-"):
            peach_policy="peach_rank" if ev.sport=="soccer" else "peach_scheduled_age_rank"
            plum_policy="plum_trend_rank"
            threshold=.75 if ev.sport=="soccer" else .55
            targets=(.90,.95) if ev.sport=="soccer" else (.65,.70)
            definitions += [("peach_existing_A",peach_policy,.60,.34,"relative",.03 if ev.sport=="soccer" else .07),
                            ("peach_existing_B",peach_policy,.60,.34,"relative",.05 if ev.sport=="soccer" else .10),
                            ("peach_user_60_plus02",peach_policy,.60,.03,"relative",.02),
                            ("plum_existing_A",plum_policy,threshold,.03,"absolute",targets[0]),
                            ("plum_existing_B",plum_policy,threshold,.03,"absolute",targets[1]),
                            ("plum_user_70_to72","plum_price_rank",.70,.03,"absolute",.72),
                            ("plum_user_70_plus02","plum_price_rank",.70,.03,"relative",.02)]
        for label,policy,threshold,width,mode,target in definitions:
            c,diag=candidates(ev,policy,1,threshold,entry_width=width,require_clear_rank=policy!="watermelon_rank",
                              selection_mode="max_eligible_ask" if policy=="watermelon_rank" else "rank_midpoint")
            path=make_path(ev,policy,c) if c else None
            for model in FEE_MODELS+(("recorded_schedule",) if uses_recorded_fees(ev) else ()):
                tp_policy=("peach_published" if "existing" in label and policy.startswith("peach") else
                           "plum_published" if "existing" in label and policy.startswith("plum") else "positive_full")
                r=replay_path(path,mode,target,model,tp_policy=tp_policy,fee_collection=fee_collection) if path else {"status":"NO_ENTRY","net":None,"reason":"no_qualifying_entry"}
                rows.append({"fee_collection":fee_collection,"label":label.replace("existing","published_params"),"source":ev.source,"cohort":ev.cohort,"sport":ev.sport,"event":ev.event,"title":ev.title,
                             "partition":ev.partition,"day":ev.partition_day,"policy":policy,"threshold":threshold,"width":width,
                             "target_mode":mode,"target":target,"fee_model":model,"entry_utc":path["entry_utc"] if path else None,
                             "entry_vwap":path["entry_vwap"] if path else None,"token":path["entry_token"] if path else None,
                             "result":path["entry_result"] if path else None,"side":path["entry_side"] if path else None,
                             "team_name":path.get("entry_team_name") if path else None,
                             "verified_role":path.get("entry_verified_role") if path else None,
                             "verified_team_name":path.get("entry_verified_team_name") if path else None,
                             "role_evidence_scope":path.get("entry_role_evidence_scope") if path else None,
                             "legacy_result_kind":path.get("entry_legacy_result_kind",path["entry_result"]) if path else None,
                             "result_kind_semantics":path.get("entry_result_kind_semantics","LEGACY_STORED_RESULT_SLOT") if path else None,
                             "tp_signal_contract":tp_policy,
                             "clock_contract":"scheduled_age_proxy" if "scheduled_age" in policy else "source_classified_in_play",
                             "execution_contract":"current_source_fullbook_gap_fallback_opportunity_proxy",
                             "status":r["status"],"exit_reason":r["reason"],"exit_utc":r.get("exit_utc"),"exit_vwap":r.get("exit_vwap"),
                             "net":r["net"],"lower":r.get("lower"),"upper":r.get("upper"),
                             "gross_buy_shares":r.get("gross_buy_shares"),"net_buy_shares":r.get("net_buy_shares"),
                             "sell_shares":r.get("sell_shares"),"buy_fee_shares":r.get("buy_fee_shares"),
                             "buy_fee_usdc_equivalent":r.get("buy_fee_usdc_equivalent"),"excluded_dust_shares":r.get("excluded_dust_shares"),
                             "tp_fee_nonpositive_observations":r.get("tp_fee_nonpositive_observations",0)})
    if rows:
        with (output/"named_policy_event_outcomes.csv").open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    return rows


def report(result,protocol,output):
    lines=["# 보수적 파라미터 전수 표시호가 재생","",f"UTC [{protocol['start']}, {protocol['end_exclusive']})", "",
           "실제 체결·확정 수익이 아니다. 모든 경기의 raw snapshot을 읽으며 trades 원장으로 진입 모집단을 제한하지 않는다.",
           "서로 다른 source/config/sport는 합산하지 않는다. 같은 경기의 수천 셀은 독립 경기가 아니다.", "",
           f"총 {result['cell_rows']:,}개 cohort/partition/fee 셀, {result['entry_mapping_rows']:,}개 경기별 진입 매핑, {result['path_count']:,}개 고유 실행 경로.","",
           "DIRECT2의 entry_result/result는 기존 저장 slot이며 venue HOME/AWAY 증명이 아니다. 검증된 역할은 별도 verified_role/verified_team_name/role_evidence_scope로만 표시하고 UNKNOWN을 HOME으로 추정하지 않는다. 이 표시 metadata는 rank·token·가격·PnL에 사용하지 않는다.","",
           "진입은 same-run 직접 midpoint 내림차순 rank(동률 token ID)이며 인접 순위 간 차이 <0.005는 ambiguous로 보존한다. 0.01~0.99 entry와 absolute target, actual BUY +0.01~0.99 relative target을 0.01 간격으로 전수 계산한다. entry band 상한은 threshold+0.03이다. absolute target<=actual BUY 또는 상대 target>=1은 진입 제외하고 같은 분봉 익절은 금지한다.","",
           "Peach는 source minute 0~10이 입증된 자료만 진입한다. 각 셀은 최초 band 적격 후보 1개만 평가하며 target overshoot로 제외된 뒤 가격이 되돌아온 두 번째 진입은 찾지 않는다. MLB의 scheduled-start-age는 native clock으로 대체하지 않는다. 별도 peach_scheduled_age 정책만 일정시각기준 0~10분을 diagnostic으로 재생하며 strict native 정책과 합치지 않는다. Plum은 가격 band와 종목별 기존 추세(축구 3회/누적+0.02, MLB 5회/누적+0.01, 공통 되돌림<=0.01/상향교차)를 분리한다. Watermelon은 직접 YES3 또는 DIRECT2만 사용하며 당시 live/order-open/liquidity>=5000/cumulative-volume>=5000을 요구한다.","",
           "SL: Peach 축구 -0.10/MLB -0.20, Plum 축구 -0.15/MLB -0.12, Watermelon max(0.70,BUY-0.30). Peach source80분부터 SL을 끄고 TP 상승폭을 절반으로 줄인다. STOP은 current source의 gap fallback처럼 full-depth, 유한 spread<=0.10을 만족하는 실제 후속 bid를 사용한다. 저장된 nominal loss/slippage cap은 gap fallback을 차단하지 않으며, 옛 frozen 운영 cap을 재현한 것으로 해석하지 않는다. API 재확인·주문 지연·FOK 거절/partial/계좌 한도는 재현하지 못하므로 live-compatible 완전 재생이 아니다.","",
           "현재 Exchange V2 primary는 BUY와 SELL fee 모두 USDC다. $5 BUY gross 수량을 SELL 0.01주 내림하고 순익=매도대금-SELLfee-$5-BUYfee로 계산한다. legacy_shares는 명시적 별도 민감도에서만 BUY fee를 outcome shares에서 차감하고 USD 비용 $5를 사용한다. 두 convention을 섞거나 fee를 이중 차감하지 않는다. 각 convention의 수량으로 실제 후속 전체 bid를 다시 걷고 minimum SELL 5주를 요구한다. dust=0회수로 계산한다. dust를 실제 소멸·확정손실로 기록한 뜻은 아니다. fee 0 / sports rate=.05×각level q*p*(1-p) / 매수·매도 각100bps를 독립 민감도로 제공한다. 각 가격 level를 한 fee fragment로 근사해 USDC fee를 5자리 반올림한다. 실제 maker별 체결 분할과 반올림 분배는 book에서 관측되지 않는다. Silver/Gold/Grey의 당시 fee가 저장되지 않아 sports .05는 가정이며 White는 recorded_schedule을 추가로 전수 재생하고 primary로 사용하며 이는 당시 metadata다.","",
           "90초 초과 공백, FAILED/미완료 run, 선택token/lineage 누락, bid 누락 이후는 즉시 검열한다. 미완결을 0으로 채우지 않는다. 이미 지나간 공백 뒤 terminal을 이용해 stop/TP가 없었다고 가정하지 않는다. 전체 손익 lower/upper는 검열된 각 거래를 최대손실/최대지급으로 범위화한다.","",
           "SUCCESS를 나중에 기록한 run만 수용하는 규칙 자체도 사후 건강성 선택이다. 수집 모집단 누락, 전체 경기 시작 이전 공백과 체결지연이 남아 있다. early/late는 이미 본 과거 자료의 시간 robustness이며 진짜 OOS가 아니다. shortlist는 검열을 실패로 둔 순익양수 확률의 Wilson 하한, 완결률, 전체 손익하한 순서다. 탐색 다중성에 대한 Bonferroni-Hoeffding 하한도 모든 셀에 보존하되 event 독립성은 검증되지 않았다. 어떤 셀도 자동 승격·확정수익을 증명하지 않는다.","",
           "## source/cohort 분모","","|source|sport|cohort|경기|전체/완전group|","|---|---|---|---:|---:|"]
    for c in result["cohorts"]:
        lines.append(f"|{c['source']}|{c['sport']}|{c['cohort']}|{c['events']}|{c['all_raw_groups']}/{c['complete_groups']}|")
    lines += ["","## 진단 shortlist — 수익 순위로 live 파라미터를 고르지 않음","","|cohort|policy|rank|entry|target|진입/완결|양수|Wilson하한|확인부분net|전체net하한|","|---|---|---:|---:|---|---:|---:|---:|---:|---:|"]
    for r in result["shortlist_diagnostic_only"]:
        lines.append(f"|{r['cohort']}|{r['policy']}|{r['rank']}|{r['entry_threshold']:.2f}|{r['target_mode']} {r['target_value']:.2f}|{r['entered']}/{r['complete']}|{r['positive']}|{r['wilson95_lower_censored_as_failure']:.3f}|{r['complete_net']:.4f}|{r['all_entry_net_lower']:.4f}|")
    lines += ["","`cells.csv.gz`는 no-entry/불가능 target 포함 모든 셀이다. `entries.csv.gz`의 path_id와 `paths.jsonl.gz`를 연결하면 모든 경기별 셀을 replay_path로 재현할 수 있다. 현금화되지 않은 표시호가를 실제 fill로 바꾸지 않는다.",""]
    (output/"REPORT.md").write_text("\n".join(lines))


def query_cell(directory,cohort,policy,rank,entry,target_mode,target,fee_model,fee_collection="v2_cash"):
    entries=[]
    with gzip.open(directory/"entries.csv.gz","rt",encoding="utf-8",newline="") as f:
        for row in csv.DictReader(f):
            if (row["cohort"]==cohort and row["policy"]==policy and int(row["rank"])==rank
                    and abs(float(row["entry_threshold"])-entry)<EPS):entries.append(row)
    wanted={r["path_id"] for r in entries if r["path_id"]}
    paths={}
    with gzip.open(directory/"paths.jsonl.gz","rt",encoding="utf-8") as f:
        for line in f:
            p=json.loads(line)
            if p["id"] in wanted:paths[p["id"]]=p
    if wanted-set(paths):raise ValueError("entry mapping references missing execution evidence")
    rows=[]
    for entry_row in entries:
        p=paths.get(entry_row["path_id"])
        r=replay_path(p,target_mode,target,fee_model,fee_collection=fee_collection) if p else {"status":"NO_ENTRY","reason":"no_qualifying_entry","net":None}
        rows.append({"event":entry_row["event"],"title":p["title"] if p else None,
                     "entry_utc":p["entry_utc"] if p else None,"entry_vwap":p["entry_vwap"] if p else None,
                     "token":p["entry_token"] if p else None,"result":p["entry_result"] if p else None,
                     "side":p["entry_side"] if p else None,"day":p["day"] if p else None,
                     "team_name":p.get("entry_team_name") if p else None,
                     "verified_role":p.get("entry_verified_role","UNKNOWN" if p.get("entry_side")=="DIRECT" else None) if p else None,
                     "verified_team_name":p.get("entry_verified_team_name") if p else None,
                     "role_evidence_scope":p.get("entry_role_evidence_scope","LEGACY_NO_ROLE_EVIDENCE_IN_FROZEN_PATH") if p else None,
                     "legacy_result_kind":p.get("entry_legacy_result_kind",p["entry_result"]) if p else None,
                     "result_kind_semantics":p.get("entry_result_kind_semantics","LEGACY_STORED_RESULT_SLOT") if p else None,
                     "partition":p["partition"] if p else None,**r})
    return rows


def query_main():
    parser=argparse.ArgumentParser(description="Reproduce every event in one saved grid cell")
    parser.add_argument("--directory",type=Path,required=True)
    parser.add_argument("--cohort",required=True);parser.add_argument("--policy",required=True)
    parser.add_argument("--rank",type=int,required=True);parser.add_argument("--entry",type=float,required=True)
    parser.add_argument("--target-mode",choices=("absolute","relative","hold"),required=True)
    parser.add_argument("--target",type=float,required=True)
    parser.add_argument("--fee-model",choices=FEE_MODELS+("recorded_schedule",),default="sports_005")
    parser.add_argument("--fee-collection",choices=("v2_cash","legacy_shares"),default="v2_cash")
    parser.add_argument("--output",type=Path)
    args=parser.parse_args(sys.argv[2:])
    result=query_cell(args.directory,args.cohort,args.policy,args.rank,args.entry,args.target_mode,args.target,args.fee_model,args.fee_collection)
    if args.output:write_json(args.output,result)
    else:print(json.dumps(result,ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources",type=Path,required=True)
    parser.add_argument("--start",required=True);parser.add_argument("--end",required=True)
    parser.add_argument("--split",required=True,help="Fixed event-block time; not a new OOS holdout")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--max-gap-seconds",type=float,default=90)
    parser.add_argument("--entry-width",type=float,default=.03)
    parser.add_argument("--source-id",action="append",default=[])
    parser.add_argument("--fee-collection",choices=("v2_cash","legacy_shares"),default="v2_cash")
    parser.add_argument("--grid-step",type=int,default=1,help="Integer cents; 1 required for exhaustive run")
    args=parser.parse_args()
    a,b,c=map(visual.timestamp,(args.start,args.split,args.end))
    if not a<b<c or args.max_gap_seconds<=0 or args.entry_width<0 or not 1<=args.grid_step<=99:
        parser.error("invalid frozen protocol")
    if args.output.exists() and any(args.output.iterdir()):parser.error("output directory must be new or empty")
    sources=json.loads(args.sources.read_text())
    selected=[s for s in sources if not args.source_id or s["id"] in args.source_id]
    if not selected:parser.error("no sources selected")
    args.output.mkdir(parents=True,exist_ok=True)
    protocol={"schema":"conservative-sports-grid-v3-v2-cash-fees","created_at":datetime.now(timezone.utc).isoformat(),
              "start":args.start,"end_exclusive":args.end,"retrospective_split":args.split,
              "grid_step_cents":args.grid_step,"entry_width":args.entry_width,"max_gap_seconds":args.max_gap_seconds,
              "fee_collection":args.fee_collection,"fee_settlement":{"BUY":"USDC" if args.fee_collection=="v2_cash" else "outcome_shares","SELL":"USDC","rounding":"per_price_level_5dp_approximation","min_sell_shares":5},
              "fee_models":FEE_MODELS,"white_primary_fee":"recorded_schedule","notional":5,"oos":False,"promotion_allowed":False,
              "code_sha256":visual.sha256(Path(__file__)),"sources_manifest_sha256":visual.sha256(args.sources)}
    write_json(args.output/"PROTOCOL.json",protocol)
    events=[];audits=[]
    for s in selected:
        evs,audit=read_source(s,args.start,args.end);events.extend(evs);audits.append(audit)
        print(json.dumps(audit,ensure_ascii=False),flush=True)
    firsts={}
    for e in events:
        key=(e.sport,e.event);firsts[key]=min(firsts.get(key,float("inf")),e.groups[0].time)
    for e in events:
        t=firsts[(e.sport,e.event)];e.partition="early" if t<b else "late"
        e.partition_day=iso(t)[:10]
    result=run_grid(events,args.output,tuple(i/100 for i in range(1,100,args.grid_step)),
                    max_gap=args.max_gap_seconds,entry_width=args.entry_width,fee_collection=args.fee_collection)
    baseline=baseline_rows(events,args.output,args.fee_collection)
    result.update(audits=audits,unique_events=len(firsts),named_policy_outcome_rows=len(baseline))
    write_json(args.output/"results.json",result);report(result,protocol,args.output)
    hashes={p.name:visual.sha256(p) for p in args.output.iterdir() if p.is_file()}
    write_json(args.output/"OUTPUT_SHA256.json",hashes)


if __name__=="__main__":
    query_main() if len(sys.argv)>1 and sys.argv[1]=="query" else main()
