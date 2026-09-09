#!/usr/bin/env python3
"""Verified historical Coconut 5-minute books -> research Event objects.

No network/source writes. Soccer exposes only real YES3 books, never synthetic
NO. Five-minute paths are not one-minute strategy replays. The original archive
stores canonical per-token books, not the complete CLOB batch response body;
we validate the canonical hash and exact successful public-request lineage, and
report that original-batch membership cannot be independently reconstructed.
"""
from __future__ import annotations

from collections import Counter, defaultdict, OrderedDict
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse

import conservative_sports_grid as grid
import sports_visual_data as visual

ORIGIN = "coconut-historical-5m"
FAMILIES = {"soccer", "mlb", "nba", "nfl", "nhl"}
REQUIRED = {
    "book_token_attempts": {"book_attempt_id", "cycle_id", "run_id", "token_id", "status", "logical_request_id", "observed_at"},
    "book_snapshots": {"book_snapshot_id", "cycle_id", "run_id", "token_id", "logical_request_id", "observed_at", "canonical_sha256", "book_gzip"},
    "outcome_observations": {"outcome_observation_id", "market_observation_id", "cycle_id", "run_id", "token_id", "condition_id", "outcome_index", "outcome_label", "sport_family", "observed_at", "structure_eligible"},
    "market_observations": {"market_observation_id", "event_observation_id", "cycle_id", "run_id", "event_id", "condition_id", "labels_json", "token_ids_json", "result_kind", "structure_eligible", "observed_at"},
    "event_observations": {"event_observation_id", "cycle_id", "run_id", "event_id", "raw_payload_id", "observed_at", "classification_status", "sport_family"},
    "collection_cycles": {"cycle_id", "run_id", "job_name", "mode", "slot_start_utc", "completed_at"},
    "research_run_events": {"run_id", "event_type", "observed_at", "config_hash", "strategy_source_digest"},
    "research_config_versions": {"config_hash", "strategy_source_digest", "job_name", "mode", "config_json"},
    "raw_payloads": {"raw_payload_id", "cycle_id", "run_id", "logical_request_id", "observed_at", "sha256", "payload_gzip"},
    "api_requests": {"logical_request_id", "run_id", "request_kind", "method", "url", "started_at", "completed_at", "status", "http_status", "response_sha256"},
}


def stamp(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.timestamp() if dt.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def schema_missing(c):
    return {t: sorted(cols - {r[1] for r in c.execute("PRAGMA table_info(" + t + ")")})
            for t, cols in REQUIRED.items()
            if cols - {r[1] for r in c.execute("PRAGMA table_info(" + t + ")")}}


class Evidence:
    def __init__(self, c):
        self.c = c
        self.cache = OrderedDict()

    def request(self, run, logical, kinds, host):
        rows = [dict(r) for r in self.c.execute(
            "SELECT * FROM api_requests WHERE run_id=? AND logical_request_id=?", (run, logical))]
        good = []
        for row in rows:
            u = urlparse(row["url"])
            if (row["status"] == "SUCCESS" and row["http_status"] == 200
                    and row["request_kind"] in kinds and u.hostname == host
                    and u.scheme == "https" and not u.username and not u.password
                    and row.get("response_sha256")):
                good.append(row)
        if len(good) != 1:
            raise ValueError("missing_or_ambiguous_public_success_receipt")
        return good[0]

    def payload(self, payload_id):
        if payload_id in self.cache:
            self.cache.move_to_end(payload_id)
            return self.cache[payload_id]
        row = self.c.execute("SELECT * FROM raw_payloads WHERE raw_payload_id=?", (payload_id,)).fetchone()
        if row is None:
            raise ValueError("missing_raw_metadata_payload")
        row = dict(row)
        body = gzip.decompress(row["payload_gzip"])
        if hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise ValueError("raw_metadata_hash_mismatch")
        if row.get("raw_bytes") is not None and row["raw_bytes"] != len(body):
            raise ValueError("raw_metadata_length_mismatch")
        value = (row, json.loads(body))
        self.cache[payload_id] = value
        if len(self.cache) > 24:
            self.cache.popitem(last=False)
        return value

    def event(self, e, m, o, cycle, run):
        raw, payload = self.payload(e["raw_payload_id"])
        if raw["run_id"] != e["run_id"] or raw["cycle_id"] != e["cycle_id"]:
            raise ValueError("raw_metadata_run_cycle_mismatch")
        req = self.request(e["run_id"], raw["logical_request_id"],
                           {"gamma_events_keyset", "gamma_event_followup"}, "gamma-api.polymarket.com")
        if req["method"] != "GET" or req["response_sha256"] != raw["sha256"]:
            raise ValueError("metadata_receipt_hash_mismatch")
        candidates = payload if isinstance(payload, list) else payload.get("events", []) if isinstance(payload, dict) and "events" in payload else [payload]
        matches = [x for x in candidates if isinstance(x, dict) and str(x.get("id")) == e["event_id"]]
        if len(matches) != 1:
            raise ValueError("metadata_event_identity_ambiguous")
        event = matches[0]
        markets = event.get("markets")
        if not isinstance(markets, list):
            raise ValueError("metadata_markets_missing")
        ms = [x for x in markets if isinstance(x, dict) and str(x.get("conditionId") or x.get("condition_id")) == m["condition_id"]]
        if len(ms) != 1:
            raise ValueError("metadata_condition_ambiguous")
        market = ms[0]
        labels, tokens = decoded(market.get("outcomes")), decoded(market.get("clobTokenIds"))
        if (not isinstance(labels, list) or not isinstance(tokens, list) or len(labels) != 2 or len(tokens) != 2
                or len(set(tokens)) != 2 or len(set(labels)) != 2
                or tokens != decoded(m["token_ids_json"]) or labels != decoded(m["labels_json"])
                or not isinstance(o["outcome_index"], int) or o["outcome_index"] not in (0, 1)
                or str(tokens[o["outcome_index"]]) != o["token_id"] or labels[o["outcome_index"]] != o["outcome_label"]):
            raise ValueError("exact_token_outcome_mapping_failed")
        times = [run["start"], stamp(req["started_at"]), stamp(req["completed_at"])]
        if any(t is None for t in times) or times != sorted(times):
            raise ValueError("metadata_receipt_time_invalid")
        if any(stamp(x) != times[2] for x in (raw["observed_at"], e["observed_at"], m["observed_at"], o["observed_at"])):
            raise ValueError("metadata_observation_receipt_mismatch")
        return event, market, times[2], dict(zip(tokens, labels))

    def book(self, attempt, b, metadata_time, run):
        if b is None or attempt["status"] != "OBSERVED":
            raise ValueError("missing_book_" + str(attempt["status"]))
        body = gzip.decompress(b["book_gzip"])
        if hashlib.sha256(body).hexdigest() != b["canonical_sha256"]:
            raise ValueError("canonical_book_hash_mismatch")
        if b.get("canonical_bytes") is not None and len(body) != b["canonical_bytes"]:
            raise ValueError("canonical_book_size_mismatch")
        book = json.loads(body)
        if str(book.get("asset_id", book.get("token_id"))) != attempt["token_id"]:
            raise ValueError("book_token_mismatch")
        req = self.request(attempt["run_id"], attempt["logical_request_id"], {"clob_full_books"}, "clob.polymarket.com")
        if req["method"] not in {"GET", "POST"}:
            raise ValueError("book_public_request_method_invalid")
        times = [metadata_time, stamp(req["started_at"]), stamp(req["completed_at"]), run["finish"]]
        if any(t is None for t in times) or times != sorted(times):
            raise ValueError("book_receipt_time_invalid")
        if stamp(b["observed_at"]) != times[2] or stamp(attempt["observed_at"]) != times[2]:
            raise ValueError("book_snapshot_receipt_mismatch")
        if any(isinstance(x.get(k), bool) for side in ("asks", "bids") for x in book.get(side, []) if isinstance(x, dict) for k in ("price", "size")):
            raise ValueError("boolean_book_level")
        asks, bids = grid.levels(book, "asks"), grid.levels(book, "bids")
        if asks and bids and bids[0][0] > asks[0][0] + grid.EPS:
            raise ValueError("crossed_book")
        return book, asks, bids, times[2]


def runs_and_configs(c, runtime, cutoff):
    configs = {r["config_hash"]: dict(r) for r in c.execute("SELECT * FROM research_config_versions")}
    history = defaultdict(list)
    for r in c.execute("SELECT * FROM research_run_events"):
        history[r["run_id"]].append(dict(r))
    runs = {}
    for key, events in history.items():
        starts = [r for r in events if r["event_type"] == "STARTED"]
        ends = [r for r in events if r["event_type"] in {"SUCCEEDED", "FAILED"}]
        first = starts[0] if len(starts) == 1 else events[0]
        config = configs.get(first["config_hash"], {})
        begin = stamp(first["observed_at"])
        finish = stamp(ends[0]["observed_at"]) if len(ends) == 1 else None
        valid = (len(starts) == len(ends) == 1 and ends[0]["event_type"] == "SUCCEEDED"
                 and config.get("job_name") == runtime and config.get("mode") == "sim"
                 and config.get("lifecycle_mode", "archive_only") == "archive_only"
                 and begin is not None and finish is not None and begin <= finish < cutoff
                 and all(r["config_hash"] == first["config_hash"] and r["strategy_source_digest"] == config.get("strategy_source_digest") for r in events))
        runs[key] = {"start": begin, "finish": finish, "valid": bool(valid), "config": config,
                     "config_hash": first["config_hash"], "source_digest": config.get("strategy_source_digest")}
    return runs, configs


def _load(c, source, start, end):
    lo, hi = stamp(start), stamp(end)
    if lo is None or hi is None or lo >= hi:
        raise ValueError("invalid UTC interval")
    missing = schema_missing(c)
    if missing:
        return [], {"source": source["id"], "supported": False, "missing_columns": missing,
                    "canonical_books": c.execute("SELECT count(*) FROM book_snapshots").fetchone()[0], "reason": "UNSUPPORTED_HISTORICAL_SCHEMA"}
    cycles = {r["cycle_id"]: dict(r) for r in c.execute("SELECT * FROM collection_cycles")}
    runs, configs = runs_and_configs(c, source["runtime_job"], hi)
    evidence = Evidence(c)
    stats = Counter()
    stats["physical_canonical_books"]=c.execute("SELECT count(*) FROM book_snapshots").fetchone()[0]
    stats["canonical_without_attempt"]=c.execute("SELECT count(*) FROM book_snapshots b WHERE NOT EXISTS(SELECT 1 FROM book_token_attempts a WHERE a.cycle_id=b.cycle_id AND a.run_id=b.run_id AND a.token_id=b.token_id AND a.logical_request_id=b.logical_request_id)").fetchone()[0]
    groups = defaultdict(lambda: defaultdict(list))
    info, identities = {}, defaultdict(dict)
    lookup = defaultdict(list)
    for r in c.execute("""SELECT a.book_attempt_id,o.outcome_observation_id FROM book_token_attempts a
            JOIN outcome_observations o ON o.cycle_id=a.cycle_id AND o.run_id=a.run_id AND o.token_id=a.token_id"""):
        lookup[r[0]].append(r[1])
    for rr in c.execute("SELECT * FROM book_token_attempts ORDER BY observed_at,book_attempt_id"):
        a = dict(rr);stats["attempt_rows"] += 1
        cycle, run = cycles.get(a["cycle_id"]), runs.get(a["run_id"])
        if not cycle or not run or run["start"] is None:
            stats["unassociated_run_or_cycle"] += 1;continue
        at = stamp(a["observed_at"]) or run["start"]
        if not lo <= at < hi:
            stats["outside_interval"] += 1;continue
        matches = lookup[a["book_attempt_id"]]
        if len(matches) != 1:
            stats["missing_or_ambiguous_same_run_outcome"] += 1;continue
        o = dict(c.execute("SELECT * FROM outcome_observations WHERE outcome_observation_id=?", (matches[0],)).fetchone())
        mr = c.execute("SELECT * FROM market_observations WHERE market_observation_id=?", (o["market_observation_id"],)).fetchone()
        m = dict(mr) if mr else {}
        er = c.execute("SELECT * FROM event_observations WHERE event_observation_id=?", (m.get("event_observation_id"),)).fetchone()
        e = dict(er) if er else {}
        family = o.get("sport_family")
        if family not in FAMILIES or not e.get("event_id"):
            stats["missing_event_or_sport"] += 1;continue
        if family == "soccer" and o["outcome_label"] != "Yes":
            stats["non_yes_soccer_book_not_synthesized"] += 1;continue
        slot = (m.get("result_kind"), "YES") if family == "soccer" else ({0: "HOME", 1: "AWAY"}.get(o["outcome_index"]), "DIRECT")
        key = (run["config_hash"], run["source_digest"], family, e["event_id"])
        title = e.get("title") or e.get("slug") or e["event_id"]
        if key not in info:
            info[key] = {"title": title, "config": run["config"], "season_phases": set(), "event_cluster_id": e.get("event_cluster_id")}
        info[key]["season_phases"].add(e.get("season_phase") or "UNKNOWN")
        valid, reason = run["valid"], "VERIFIED_CANONICAL_BOOK_AND_PUBLIC_RECEIPT"
        asks = bids = ();book = {};event = {};market = {};fee = None;mapping = None
        try:
            if cycle["run_id"] != a["run_id"] or cycle["job_name"] != source["runtime_job"] or cycle["mode"] != "sim":
                raise ValueError("cycle_runtime_mismatch")
            if any(x.get("run_id") != a["run_id"] or x.get("cycle_id") != a["cycle_id"] for x in (o,m,e)):
                raise ValueError("metadata_cross_run_or_cycle")
            if (e["classification_status"] != "ACCEPTED" or o["structure_eligible"] != 1 or m["structure_eligible"] != 1
                    or e.get("sport_family") != family or m.get("sport_family") != family
                    or m["event_id"] != e["event_id"] or m["condition_id"] != o["condition_id"]):
                raise ValueError("structure_or_event_identity_invalid")
            event,market,mt,mapping=evidence.event(e,m,o,cycle,run)
            bs=[dict(r) for r in c.execute("SELECT * FROM book_snapshots WHERE cycle_id=? AND run_id=? AND token_id=? AND logical_request_id=?",(a["cycle_id"],a["run_id"],a["token_id"],a["logical_request_id"]))]
            book,asks,bids,at=evidence.book(a,bs[0] if len(bs)==1 else None,mt,run)
            if book.get("market") not in (None,o["condition_id"]):raise ValueError("book_condition_mismatch")
            if not run["valid"]:raise ValueError("run_not_successfully_published_before_cutoff")
            if stamp(cycle["completed_at"]) is None or stamp(cycle["completed_at"]) > run["finish"]:
                raise ValueError("cycle_publication_time_invalid")
            fee=grid.raw_fee_rate(market)
        except (ValueError,TypeError,KeyError,OSError,json.JSONDecodeError) as error:
            valid=False;reason=str(error);at=run["start"];stats["invalid:"+reason]+=1
        spread=asks[0][0]-bids[0][0] if asks and bids else None
        midpoint=(asks[0][0]+bids[0][0])/2 if asks and bids else None
        market_open=(market.get("active") is True and market.get("closed") is False and market.get("acceptingOrders") is True and market.get("enableOrderBook") is True)
        opened=valid and market_open and event.get("active") is True and event.get("closed") is False and event.get("live") is True and event.get("ended") is False
        liquidity=visual.number(market.get("liquidityNum",market.get("liquidity")));volume=visual.number(market.get("volumeNum",market.get("volume")))
        gate=opened and liquidity is not None and liquidity>=5000 and volume is not None and volume>=5000
        ident={"sport_family":family,"event_id":e["event_id"],"condition_id":o["condition_id"],"token_id":a["token_id"],"outcome":o["outcome_label"],"outcome_side":slot[1],"result_kind":slot[0]}
        role=visual.direct_role_metadata(ident,event.get("teams"),scope="HISTORICAL_COCONUT_EXACT_RAW_EVENT",identity_proven=valid,evidence_event_id=event.get("id"),evidence_observed_at=e.get("observed_at"))
        status="MISSING" if not book else "EMPTY_BOOK" if not asks and not bids else "EMPTY_ASKS" if not asks else "EMPTY_BIDS" if not bids else "FULL"
        scheduled=next((stamp(event.get(field)) for field in ("startTime","gameStartTime","eventStartTime") if event.get(field) not in (None,"")),None)
        scheduled_age=(at-scheduled)/60 if valid and scheduled is not None else None
        snap=grid.Snap(a["token_id"],o["condition_id"],slot,at,a["run_id"],grid.raw_source_minute(event,family),midpoint,asks,bids,valid,bool(opened),bool(gate),fee,spread,grid.ask_side_state(book),reason,grid.walk(asks,5,True),
            scheduled_age=scheduled_age,entry_set_complete=True,evidence_origin=ORIGIN,observation_status=status,market_open_observed=market_open,outcome_label=o["outcome_label"],verified_role=role.get("verified_role"),verified_team_name=role.get("verified_team_name"),role_evidence_scope=role.get("role_evidence_scope"),legacy_result_kind=slot[0],legacy_role_semantics="DIRECT_ARRAY_POSITION_NOT_VENUE_ROLE" if family!="soccer" else "SOURCE_SOCCER_RESULT_KIND")
        groups[key][a["run_id"]].append(snap);stats["valid_rows" if valid else "invalid_rows"]+=1;stats["sport:"+family]+=1
        if valid and mapping is not None:identities[key][o["condition_id"]]=mapping
    terminal_by_key, terminal_times = read_terminals(c, evidence, runs, info, identities, hi, stats)
    events=[]
    for key,byrun in groups.items():
        cfg_hash,digest,family,eid=key;cfg=decoded(info[key]["config"].get("config_json") or "{}")
        config={"config_hash":cfg_hash,"strategy_source_digest":digest,"mode":"sim","job_name":source["runtime_job"],"trading":cfg.get("trading",cfg),"cadence_seconds":300,"one_minute_replay_eligible":False,"observed_outcomes":"YES3" if family=="soccer" else "DIRECT2","synthetic_no":False,"season_phase":next(iter(info[key]["season_phases"])) if len(info[key]["season_phases"])==1 else "MIXED_OR_CHANGED","observed_season_phases":sorted(info[key]["season_phases"]),"evidence_origins":[ORIGIN]}
        normalized=[grid.normalize_group(ss,family,True) for ss in byrun.values()]
        existing_runs={g.run for g in normalized}
        for run_id,when in terminal_times.get(key,{}).items():
            if run_id not in existing_runs:normalized.append(grid.Group(when,run_id,[],False))
        failures=[(r["start"],r["finish"]) for r in runs.values() if r["config_hash"]==cfg_hash and r["source_digest"]==digest and not r["valid"] and r["start"] is not None and r["start"]<hi]
        events.append(grid.Event(source["id"],visual.identifier([source["id"],cfg_hash,digest,family]),family,eid,info[key]["title"],config,sorted(normalized,key=lambda g:g.time),failures,dict(terminal_by_key.get(key,{}))))
    return events,{"source":source["id"],"supported":True,"events":len(events),"stats":dict(stats),"cadence_seconds":300,"one_minute_replay_eligible":False,"canonical_book_batch_body_independently_reconstructible":False,"terminal_import":"exact same-cohort public CLOB closed/one-hot, soccer full-triad aligned; no cash redemption claim","schema":"historical-coconut-exact-public-canonical-v1"}



def read_terminals(c, evidence, runs, info, identities, cutoff, stats):
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'resolution_observations' not in tables:return {},{}
    cluster_keys=defaultdict(list)
    for key,details in info.items():cluster_keys[(key[0],key[1],details.get('event_cluster_id'))].append(key)
    grouped=defaultdict(dict)
    for rr in c.execute("SELECT * FROM resolution_observations WHERE resolution_status='RESOLVED' ORDER BY observed_at"):
        r=dict(rr);run=runs.get(r['run_id']);stats['terminal_rows_seen']+=1
        if not run or not run['valid']:stats['terminal_unpublished_run']+=1;continue
        keys=cluster_keys.get((run['config_hash'],run['source_digest'],r['event_cluster_id']),[])
        if len(keys)!=1:stats['terminal_unassociated_event']+=1;continue
        key=keys[0];mapping=identities[key].get(r['condition_id'])
        if not mapping:stats['terminal_unobserved_condition_identity']+=1;continue
        try:
            req=evidence.request(r['run_id'],r['logical_request_id'],{'clob_public_resolution'},'clob.polymarket.com')
            payloads=c.execute("SELECT raw_payload_id FROM raw_payloads WHERE run_id=? AND cycle_id=? AND logical_request_id=? AND payload_kind='CLOB_PUBLIC_RESOLUTION'",(r['run_id'],r['cycle_id'],r['logical_request_id'])).fetchall()
            if len(payloads)!=1:raise ValueError('resolution_raw_payload_ambiguous')
            raw,payload=evidence.payload(payloads[0][0])
            if req['method']!='GET' or raw['sha256']!=req['response_sha256'] or raw['sha256']!=r['raw_sha256']:
                raise ValueError('resolution_raw_receipt_hash_mismatch')
            times=[run['start'],stamp(req['started_at']),stamp(req['completed_at']),run['finish']]
            if any(t is None for t in times) or times!=sorted(times) or times[-1]>=cutoff:
                raise ValueError('resolution_time_invalid')
            if stamp(r['observed_at'])!=times[2] or stamp(raw['observed_at'])!=times[2]:
                raise ValueError('resolution_observation_receipt_mismatch')
            if payload!=decoded(r['evidence_json']) or payload.get('closed') is not True or str(payload.get('condition_id'))!=r['condition_id']:
                raise ValueError('resolution_condition_or_evidence_mismatch')
            tokens=payload.get('tokens')
            if not isinstance(tokens,list) or len(tokens)!=2 or {str(t.get('token_id')):t.get('outcome') for t in tokens}!=mapping:
                raise ValueError('resolution_token_mapping_failed')
            payouts={}
            for t in tokens:
                value=t.get('price')
                if isinstance(value,bool) or value not in (0,1) or t.get('winner') is not bool(value):raise ValueError('resolution_not_unique_onehot')
                payouts[str(t['token_id'])]=float(value)
            if sum(payouts.values())!=1:raise ValueError('resolution_not_unique_onehot')
            grouped[(key,r['cycle_id'],r['run_id'])][r['condition_id']]={'payouts':payouts,'time':times[-1]}
        except (ValueError,TypeError,KeyError,OSError,json.JSONDecodeError) as error:
            stats['invalid_terminal:'+str(error)]+=1
    terminal=defaultdict(lambda:defaultdict(list));times=defaultdict(dict)
    for (key,cycle,run_id),bycondition in grouped.items():
        expected=3 if key[2]=='soccer' else 1
        if len(bycondition)!=expected or set(bycondition)!=set(identities[key]):
            stats['terminal_incomplete_condition_set']+=1;continue
        if key[2]=='soccer':
            yes=[]
            for cid,proof in bycondition.items():
                mapping=identities[key][cid]
                ids=[t for t,label in mapping.items() if label=='Yes']
                if len(ids)!=1:break
                yes.append(proof['payouts'][ids[0]])
            if len(yes)!=3 or sum(yes)!=1:stats['terminal_inconsistent_soccer_triad']+=1;continue
        when=max(x['time'] for x in bycondition.values());times[key][run_id]=when
        for cid,proof in bycondition.items():
            for token,payout in proof['payouts'].items():
                terminal[key][(cid,token)].append({'payout':payout,'observed_at':grid.iso(when),'source':'VERIFIED_HISTORICAL_COCONUT_CLOB','observer_config':key[0]});stats['verified_terminal_token_facts']+=1
    return terminal,times

def read_source(source,start,end):
    path=Path(source["local_path"]).resolve()
    if not source.get("pinned") or not re.fullmatch(r"coconut-major-sports(?:-lifecycle)?-5m-v[1-7]",source.get("runtime_job","")):
        raise ValueError("explicit historical Coconut pin/runtime required")
    if any(Path(str(path)+suffix).exists() for suffix in ("-wal","-shm","-journal")):
        raise ValueError("standalone pin required")
    expected=source["local_sha256"]
    if sha(path)!=expected:raise ValueError("source SHA mismatch")
    manifest=Path(source.get("manifest") or path.with_name("manifest.json"))
    m=json.loads(manifest.read_text())
    if (m.get("sha256")!=expected or m.get("pinned_path")!=str(path) or m.get("source_key")!=source.get("source_key") or m.get("quick_check")!=["ok"]):
        raise ValueError("pin manifest identity mismatch")
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1",uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
        if c.execute("PRAGMA quick_check").fetchone()[0]!="ok":raise ValueError("pin SQLite integrity failure")
        metadata_rows=c.execute("SELECT * FROM schema_metadata").fetchall()
        version=source["runtime_job"].rsplit("-v",1)[1]
        expected_contract=("major-sports-inplay-moneyline-census-v1" if version=="1" else "major-sports-lifecycle-census-v"+version)
        if len(metadata_rows)!=1 or dict(metadata_rows[0]).get("data_contract")!=expected_contract:
            raise ValueError("historical runtime/schema contract mismatch")
        metadata=dict(metadata_rows[0])
        result=_load(c,source,start,end)
        result[1]["source_schema_metadata"]={k:metadata.get(k) for k in ("database_utc_date","data_contract","schema_profile","universe_profile","classifier_version","sports_registry_sha256")}
        for event in result[0]:event.config["historical_source_schema"]=result[1]["source_schema_metadata"]
    if sha(path)!=expected:raise ValueError("source changed during read")
    return result
