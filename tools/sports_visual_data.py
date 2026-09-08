#!/usr/bin/env python3
"""Export verified, local sports books without interpolating quotes or combining cohorts.

No network, credentials, orders, or source-database writes are used. The small index
points to per-event JSON fragments, preserving every recorded observation.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ["t", "token", "ask5", "bid5", "mid", "best_bid", "best_ask", "minute", "flags", "clock"]
FLAGS = {"failed_run": 1, "depth_missing": 2, "incomplete_set": 4, "identity_gap": 8, "gap_before": 16}


def timestamp(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def identifier(value):
    return hashlib.sha256(compact(value).encode()).hexdigest()[:20]


ROLE_FIELDS = ("legacy_result_kind", "legacy_role_semantics", "verified_role", "verified_team_name",
               "role_evidence_scope", "role_verification_status", "role_evidence")
DIRECT_SPORTS = frozenset({"mlb", "nba", "nfl", "nhl"})


def _role_name(value):
    if not isinstance(value, str):return ""
    return " ".join("".join(c if c.isalnum() else " " for c in value).casefold().split())


def direct_role_metadata(row, teams, *, scope, identity_proven=True,
                         evidence_event_id=None, evidence_observation_id=None, evidence_observed_at=None):
    """Add descriptive venue-role evidence without changing the legacy slot.

    HOME/AWAY in the old producers denoted source team-array position. Explicit
    source ordering plus an exact token outcome label is required for a venue
    role. Unknown roles never fall back to the positional value. Consumers must
    not use these display fields to change candidate eligibility or cash paths.
    """
    if row.get("sport_family") not in DIRECT_SPORTS or row.get("outcome_side") != "DIRECT":return {}
    result={"legacy_result_kind":row.get("result_kind"),
            "legacy_role_semantics":"SOURCE_TEAM_ARRAY_POSITION_NOT_VERIFIED_VENUE_ROLE",
            "verified_role":"UNKNOWN","verified_team_name":None,"role_evidence_scope":scope,
            "role_verification_status":"UNKNOWN_NO_EXPLICIT_ORDERING",
            "role_evidence":{"event_id":row.get("event_id"),"condition_id":row.get("condition_id"),
                             "token_id":row.get("token_id"),"outcome_label":row.get("outcome"),
                             "event_observation_id":evidence_observation_id,"observed_at":evidence_observed_at,
                             "observed_at_basis":("RAW_EVENT_CYCLE_REFERENCE_NOT_HTTP_RECEIPT" if scope=="SAME_RAW_EVENT_OBSERVATION_EXPLICIT_ORDERING"
                                  else "WHITE_EVENT_OBSERVATION_TIMESTAMP_NOT_INDEPENDENT_HTTP_RECEIPT" if scope=="SAME_WHITE_EVENT_OBSERVATION_EXPLICIT_ORDERING"
                                  else "NO_SOURCE_EVENT_ORDERING_OBSERVATION" if scope=="LEGACY_NO_POINT_IN_TIME_TEAM_ORDERING"
                                  else "CALLER_SUPPLIED_OBSERVATION_REFERENCE")}}
    if not identity_proven or not row.get("token_id") or not row.get("condition_id") or not row.get("outcome"):
        result["role_verification_status"]="UNKNOWN_TOKEN_LABEL_IDENTITY";return result
    if evidence_event_id is not None and str(evidence_event_id)!=str(row.get("event_id")):
        result["role_verification_status"]="UNKNOWN_EVENT_IDENTITY";return result
    if not isinstance(teams,list) or len(teams)!=2 or any(not isinstance(t,dict) for t in teams):return result
    orderings=[str(t.get("ordering") or "").strip().casefold() for t in teams]
    if sorted(orderings)!=["away","home"]:return result
    ids=[str(t.get("id")) for t in teams if t.get("id") is not None]
    if len(ids)==2 and len(set(ids))!=2:
        result["role_verification_status"]="UNKNOWN_DUPLICATE_TEAM_IDENTITY";return result
    label=_role_name(row.get("outcome"));matches=[]
    for team,ordering in zip(teams,orderings):
        fields=[k for k in ("name","alias","abbreviation") if label and _role_name(team.get(k))==label]
        if fields:matches.append((team,ordering,fields))
    if len(matches)!=1:
        result["role_verification_status"]="UNKNOWN_AMBIGUOUS_OR_UNMATCHED_TEAM_LABEL";return result
    team,ordering,fields=matches[0]
    team_name=team.get("name")
    result.update(verified_role=ordering.upper(),verified_team_name=team_name if isinstance(team_name,str) and team_name.strip() else row["outcome"],
                  role_verification_status="VERIFIED_EXPLICIT_SOURCE_ORDERING")
    result["role_evidence"].update(team_id=team.get("id"),team_ordering=ordering,
        matched_source_fields=fields,teams_sha256=hashlib.sha256(compact(teams).encode()).hexdigest())
    return result


def _merge_role_metadata(token, row):
    """An event's conflicting observed roles stay unknown, never last-wins."""
    if "verified_role" not in row:return
    incoming={k:row[k] for k in ROLE_FIELDS}
    incoming["legacy_result_kind"]=token.get("result_kind",row.get("legacy_result_kind"))
    old=token.get("verified_role")
    if str(token.get("role_verification_status","")).startswith("CONFLICTING_"):return
    both_known=old in {"HOME","AWAY"} and incoming["verified_role"] in {"HOME","AWAY"}
    old_id=(token.get("role_evidence") or {}).get("team_id")
    new_id=incoming["role_evidence"].get("team_id")
    role_conflict=both_known and old!=incoming["verified_role"]
    same_explicit_id=old_id is not None and new_id is not None and str(old_id)==str(new_id)
    names_differ=(_role_name(token.get("verified_team_name")) and _role_name(incoming.get("verified_team_name"))
                  and _role_name(token.get("verified_team_name"))!=_role_name(incoming.get("verified_team_name")))
    team_conflict=both_known and ((old_id is not None and new_id is not None and str(old_id)!=str(new_id))
                                or (not same_explicit_id and names_differ))
    if role_conflict or team_conflict:
        token.update(verified_role="UNKNOWN",verified_team_name=None,
                     role_verification_status="CONFLICTING_EXPLICIT_TEAM_IDENTITIES" if team_conflict else "CONFLICTING_EXPLICIT_SOURCE_ROLES",
                     role_evidence_scope="CONFLICTING_SAME_EVENT_OBSERVATIONS",
                     role_evidence={"conflicting_proofs":[token.get("role_evidence"),incoming["role_evidence"]]})
    elif old not in {"HOME","AWAY"} or incoming["verified_role"] in {"HOME","AWAY"}:
        token.update(incoming)


def depth_prices(book, token):
    """Exact $5 ask walk and bid liquidation of those same shares, gross of fees."""
    if str(book.get("token_id", token)) != str(token):
        return None, None
    levels = {}
    for side in ("asks", "bids"):
        values = []
        for row in book.get(side, []):
            price, size = number(row.get("price")), number(row.get("size"))
            if price is None or size is None or not 0 < price < 1 or size <= 0:
                return None, None
            values.append((price, size))
        levels[side] = sorted(values, reverse=side == "bids")
    remaining, shares = 5.0, 0.0
    for price, size in levels["asks"]:
        spent = min(remaining, price * size)
        shares += spent / price
        remaining -= spent
        if remaining <= 1e-8:
            break
    if remaining > 1e-8 or shares <= 0:
        return None, None
    ask = 5.0 / shares
    remaining, proceeds = shares, 0.0
    for price, size in levels["bids"]:
        sold = min(remaining, size)
        proceeds += sold * price
        remaining -= sold
        if remaining <= 1e-8:
            break
    return ask, proceeds / shares if remaining <= 1e-8 else None


def read_configs(connection, white):
    table = "research_config_versions" if white else "strategy_configs"
    result = {}
    for row in connection.execute(f"SELECT * FROM {table}"):
        cfg = json.loads(row["config_json"])
        trading = cfg.get("trading", {})
        result[row["config_hash"]] = {
            "config_hash": row["config_hash"],
            "strategy_source_digest": trading.get("strategy_source_digest") or cfg.get("strategy_source_digest"),
            "mode": row["mode"],
            "sport": trading.get("sport_family"),
            "sport_profile_version": trading.get("sport_profile_version"),
            "protocol_sha256": trading.get("protocol_sha256") or trading.get("preregistration_sha256"),
            "classifier_version": trading.get("classifier_version"),
            "league_mapping_sha256": trading.get("league_mapping_sha256"),
            "entry": trading.get("entry"),
        }
        if white:
            result[row["config_hash"]]["strategy_source_digest"] = row["strategy_source_digest"]
    return result


def read_runs(connection, white):
    if not white:
        return {r["run_id"]: dict(r) for r in connection.execute("SELECT run_id,job_name,mode,config_hash,status,started_at,finished_at FROM run_audits")}
    rows = defaultdict(list)
    for r in connection.execute("SELECT * FROM research_run_events ORDER BY observed_at,event_id"):
        rows[r["run_id"]].append(dict(r))
    result = {}
    for run, events in rows.items():
        last = events[-1]
        result[run] = {"run_id": run, "config_hash": last["config_hash"], "mode": "sim", "status": "SUCCESS" if last["event_type"] == "SUCCEEDED" else last["event_type"], "started_at": events[0]["observed_at"], "finished_at": last["observed_at"]}
    return result


RAW_ARCHIVE_TABLES = frozenset({"raw_book_cycles", "raw_book_observations", "raw_event_observations"})
RAW_OBSERVED_BOOK_STATES = frozenset({"FULL", "EMPTY_ASKS", "EMPTY_BIDS", "EMPTY_BOOK"})


def has_raw_archive(connection):
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    present = tables & RAW_ARCHIVE_TABLES
    if present and present != RAW_ARCHIVE_TABLES:
        raise ValueError("incomplete full-sports-raw-v1 archive schema")
    return bool(present)


def _raw_timestamp(value):
    try:
        return timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _raw_expected_slots(family):
    if family == "soccer":
        return {f"{kind}:{side}" for kind in ("HOME", "DRAW", "AWAY") for side in ("YES", "NO")}
    if family in {"mlb", "nba", "nfl", "nhl"}:
        return {"HOME:DIRECT", "AWAY:DIRECT"}
    return set()


def _raw_context(connection, cycle, event, runs, configs):
    """Validate same-publication identity without mutable market_catalog joins."""
    issues = []
    run = runs.get(cycle["run_id"], {})
    config = configs.get(cycle["config_hash"], {})
    if (cycle["contract"] != "full-sports-raw-v1" or run.get("config_hash") != cycle["config_hash"]
        or cycle["strategy_source_digest"] != config.get("strategy_source_digest")
        or cycle["job_name"] != run.get("job_name") or cycle["sport_family"] != config.get("sport")
        or run.get("mode") not in {"sim", "simulation"}):
        issues.append("raw_cycle_lineage_gap")
    try:
        payload = json.loads(event["evidence_json"])
        slots = payload["slots"]
        if not isinstance(slots, dict) or payload.get("contract") != "full-sports-raw-v1":
            raise ValueError("event payload shape")
    except (TypeError, ValueError, KeyError):
        payload, slots = {}, {}
        issues.append("raw_event_payload_invalid")
    expected = _raw_expected_slots(cycle["sport_family"])
    identities = []
    for slot, identity in slots.items():
        if slot not in expected or not isinstance(identity, dict):
            issues.append("raw_slot_shape_invalid")
            continue
        if any(not isinstance(identity.get(k), str) or not identity[k] for k in ("token_id", "condition_id", "outcome")):
            issues.append("raw_slot_identity_missing")
            continue
        if cycle["sport_family"] == "soccer" and identity["outcome"].upper() != slot.split(":")[1]:
            issues.append("raw_slot_outcome_mismatch")
        identities.append(identity["token_id"])
    if len(set(identities)) != len(identities):
        issues.append("raw_duplicate_token_identity")
    well_formed = {slot: identity for slot, identity in slots.items()
                   if slot in expected and isinstance(identity, dict)
                   and all(isinstance(identity.get(k), str) and identity[k] for k in ("token_id", "condition_id", "outcome"))}
    if cycle["sport_family"] == "soccer":
        condition_results = defaultdict(set)
        for slot, identity in well_formed.items():
            condition_results[identity["condition_id"]].add(slot.split(":")[0])
        if any(len(results) != 1 for results in condition_results.values()):
            issues.append("raw_condition_shared_across_results")
        for result in ("HOME", "DRAW", "AWAY"):
            pair = [identity for slot, identity in well_formed.items() if slot.startswith(result+":")]
            if len(pair) == 2 and pair[0]["condition_id"] != pair[1]["condition_id"]:
                issues.append("raw_yes_no_condition_mismatch")
    elif len(well_formed) == 2:
        if len({x["condition_id"] for x in well_formed.values()}) != 1 or len({x["outcome"] for x in well_formed.values()}) != 2:
            issues.append("raw_direct_condition_or_outcome_mismatch")
    event_fields = payload.get("event") or {}
    if event_fields.get("id") is not None and str(event_fields["id"]) != event["event_id"]:
        issues.append("raw_event_id_mismatch")
    if event["run_id"] != cycle["run_id"] or event["identified_tokens"] != len(slots):
        issues.append("raw_event_publication_mismatch")
    reference = _raw_timestamp(cycle["observed_at"])
    if reference is None or _raw_timestamp(event["observed_at"]) != reference:
        issues.append("raw_event_reference_mismatch")
    books = connection.execute("SELECT COUNT(*) FROM raw_book_observations WHERE run_id=?", (cycle["run_id"],)).fetchone()[0]
    events = connection.execute("SELECT COUNT(*) FROM raw_event_observations WHERE run_id=?", (cycle["run_id"],)).fetchone()[0]
    event_books = connection.execute("SELECT COUNT(*) FROM raw_book_observations WHERE run_id=? AND event_id=?", (cycle["run_id"], event["event_id"])).fetchone()[0]
    if books != cycle["expected_tokens"] or events != cycle["expected_events"] or event_books != event["expected_tokens"]:
        issues.append("raw_atomic_publication_count_mismatch")
    complete = not issues and set(slots) == expected and bool(expected)
    return payload, slots, config, issues, complete


def _raw_book(row):
    """Return validated full levels, preserving no-response versus empty book."""
    payload = row["book_json"]
    if payload is None:
        if row["status"] in RAW_OBSERVED_BOOK_STATES:
            return {}, ["raw_book_payload_missing"]
        return {}, []
    issues = []
    if hashlib.sha256(payload.encode()).hexdigest() != row["book_sha256"]:
        return {}, ["raw_book_hash_mismatch"]
    try:
        book = json.loads(payload)
        if not isinstance(book, dict) or str(book.get("token_id")) != row["token_id"]:
            raise ValueError("book identity")
        for side in ("asks", "bids"):
            if not isinstance(book.get(side), list):
                raise ValueError("book side absent")
            for level in book[side]:
                if not isinstance(level, dict) or isinstance(level.get("price"), bool) or isinstance(level.get("size"), bool):
                    raise ValueError("invalid level shape")
                price, size = number(level.get("price")), number(level.get("size"))
                if price is None or size is None or not 0 < price <= 1 or size <= 0:
                    raise ValueError("invalid level domain")
        if row["status"] not in RAW_OBSERVED_BOOK_STATES:
            issues.append("raw_book_status_not_observed")
        empty_state = ("FULL" if book["asks"] and book["bids"] else "EMPTY_ASKS" if book["bids"] else "EMPTY_BIDS" if book["asks"] else "EMPTY_BOOK")
        if row["status"] != empty_state:
            issues.append("raw_empty_state_mismatch")
        if book["asks"] and book["bids"]:
            if max(number(x["price"]) for x in book["bids"]) > min(number(x["price"]) for x in book["asks"]) + 1e-9:
                issues.append("raw_crossed_book")
        return book, issues
    except (TypeError, ValueError, KeyError):
        return {}, ["raw_book_shape_invalid"]


def raw_archive_rows(connection, start, end):
    """One expected slot per raw observation, including failure/no-response rows.

    raw_point_in_time_identity_proven covers identity, never enclosing run success
    or liquidity. raw_entry_set_complete and raw_book_valid are separate gates.
    Empty raw cycles intentionally produce no invented token observations.
    """
    if not has_raw_archive(connection):
        return
    runs, configs = read_runs(connection, False), read_configs(connection, False)
    from functools import lru_cache

    @lru_cache(maxsize=64)
    def context(run_id, event_id):
        cycle = connection.execute("SELECT * FROM raw_book_cycles WHERE run_id=?", (run_id,)).fetchone()
        event = connection.execute("SELECT * FROM raw_event_observations WHERE run_id=? AND event_id=?", (run_id, event_id)).fetchall()
        if cycle is None or len(event) != 1:
            return cycle, None, {}, {}, {}, ["raw_event_anchor_missing_or_duplicate"], False
        payload, slots, config, issues, complete = _raw_context(connection, dict(cycle), dict(event[0]), runs, configs)
        return dict(cycle), dict(event[0]), payload, slots, config, issues, complete

    point_expression = """CASE WHEN julianday(b.received_at) >= julianday(b.requested_at)
        AND julianday(b.requested_at) >= julianday(c.observed_at)
        AND julianday(b.received_at) <= julianday(c.published_at)
        THEN b.received_at ELSE c.observed_at END"""
    query = f"""SELECT b.*,{point_expression} AS point_at
      FROM raw_book_observations b JOIN raw_book_cycles c ON c.run_id=b.run_id
      WHERE julianday({point_expression}) >= julianday(?)
        AND julianday({point_expression}) < julianday(?)
      ORDER BY julianday({point_expression}),b.observation_id"""
    for source in connection.execute(query, (start, end)):
        row = dict(source)
        cycle, event, payload, slots, config, errors, complete = context(row["run_id"], row["event_id"])
        issues = list(errors)
        identity = slots.get(row["slot"], {})
        if row["slot"] not in _raw_expected_slots(cycle["sport_family"]):
            issues.append("raw_observation_slot_invalid")
        if not identity or identity.get("token_id") != row["token_id"] or identity.get("condition_id") != row["condition_id"]:
            issues.append("raw_observation_anchor_mismatch")
        book, book_issues = _raw_book(row)
        quote_time_valid = None
        if row["status"] in RAW_OBSERVED_BOOK_STATES:
            try:
                run = runs.get(row["run_id"], {})
                times = [timestamp(value) for value in (run.get("started_at"), cycle["observed_at"], row["requested_at"], row["received_at"], cycle["published_at"])]
                quote_time_valid = all(value is not None for value in times) and all(a <= b for a,b in zip(times,times[1:]))
                if run.get("finished_at") is not None:
                    quote_time_valid = quote_time_valid and times[-1] <= timestamp(run["finished_at"])
                quote_time_valid = quote_time_valid and times[-1] < timestamp(end)
            except (TypeError, ValueError, OverflowError):
                quote_time_valid = False
            if not quote_time_valid:
                issues.append("raw_quote_time_or_publication_cutoff_invalid")
                book_issues.append("raw_quote_time_or_publication_cutoff_invalid")
        event_fields = payload.get("event") or {}
        market_fields = next((m for m in payload.get("market_context", []) if m.get("conditionId") == row["condition_id"]), {})
        result_kind, _, side = row["slot"].partition(":")
        bids = [number(x["price"]) for x in book.get("bids", [])]
        asks = [number(x["price"]) for x in book.get("asks", [])]
        best_bid, best_ask = max(bids, default=None), min(asks, default=None)
        midpoint = (best_bid+best_ask)/2 if best_bid is not None and best_ask is not None else None
        book_valid = not book_issues and row["status"] in RAW_OBSERVED_BOOK_STATES
        identity_proven = not issues
        # Preserve native source strings. A future consumer must interpret the
        # sport's clock; receipt time is not an inning or soccer elapsed minute.
        sport_context = {"sport_family": cycle["sport_family"], "fields": event_fields, "market_fields": market_fields,
                         "observation_basis": "RAW_EVENT_OBSERVATION_WITH_SEPARATE_BOOK_RECEIPT"}
        clock = {"source_sport_context": sport_context, "raw_archive": {
            "contract": cycle["contract"], "status": row["status"], "reason": row["reason"],
            "requested_at": row["requested_at"], "received_at": row["received_at"],
            "published_at": cycle["published_at"], "cycle_status": cycle["status"],
            "event_status": event["status"] if event else None,
            "identity_proven": identity_proven, "book_valid": book_valid,
            "issues": issues+book_issues,
            "timestamp_basis": (("BOOK_RECEIPT" if row["status"] in RAW_OBSERVED_BOOK_STATES else "BATCH_RECEIPT_NO_TOKEN_QUOTE") if row["received_at"] and row["point_at"] == row["received_at"] else "EXPECTED_SLOT_CYCLE_REFERENCE_NOT_QUOTE")}}
        projected = {"id": row["observation_id"], "run_id": row["run_id"], "event_id": row["event_id"],
            "condition_id": row["condition_id"], "token_id": row["token_id"],
            "outcome": identity.get("outcome"), "outcome_side": side, "result_kind": result_kind,
            "timestamp": row["point_at"], "sport_family": cycle["sport_family"],
            "title": event_fields.get("title") or row["event_id"], "slug": event_fields.get("slug"),
            "question": None, "league": cycle["sport_family"].upper(),
            "game_start": event_fields.get("startTime") or event_fields.get("gameStartTime"),
            "best_bid": best_bid if book_valid else None, "best_ask": best_ask if book_valid else None,
            "midpoint": midpoint if book_valid else None, "source_elapsed_minutes": None,
            "source_clock_reason": "RAW_NATIVE_CLOCK_UNINTERPRETED", "source_updated_at": event_fields.get("updatedAt"),
            "config_hash": cycle["config_hash"], "strategy_source_digest": cycle["strategy_source_digest"],
            "sport_profile_version": config.get("sport_profile_version"), "protocol_sha256": config.get("protocol_sha256"),
            "classifier_version": config.get("classifier_version"), "league_mapping_sha256": config.get("league_mapping_sha256"),
            "event_cycle_id": None,
            "raw_event_observation_id": event["observation_id"] if event else None,
            "raw_evidence_origin": "full-sports-raw-v1",
            "raw_publication_within_cutoff": _raw_timestamp(cycle["published_at"]) is not None and _raw_timestamp(cycle["published_at"]) < timestamp(end),
            "event_set_complete": int(complete and identity_proven),
            "raw_point_in_time_identity_proven": identity_proven,
            "raw_entry_set_complete": complete, "raw_book_valid": book_valid,
            "raw_book_integrity_error": bool(book_issues),
            "raw_quote_time_valid": quote_time_valid,
            "raw_event_lifecycle_status": event["status"] if event else None,
            "raw_tradable_observed": event_fields.get("active") is True and event_fields.get("closed") is False and event_fields.get("live") is True and event_fields.get("ended") is False and market_fields.get("active") is True and market_fields.get("closed") is False and market_fields.get("enableOrderBook") is True and market_fields.get("acceptingOrders") is True,
            "raw_observation_status": row["status"], "raw_observation_id": row["observation_id"],
            "raw_book_json": row["book_json"], "raw_book_sha256": row["book_sha256"],
            "point_in_time_market_fields": market_fields, "clock": clock,
            "book": book if book_valid else {}, "book_json": compact(book) if book_valid else None}
        projected.update(direct_role_metadata(projected,event_fields.get("teams"),
            scope="SAME_RAW_EVENT_OBSERVATION_EXPLICIT_ORDERING",identity_proven=identity_proven,
            evidence_event_id=event_fields.get("id"),evidence_observation_id=event["observation_id"] if event else None,
            evidence_observed_at=event["observed_at"] if event else None))
        if "role_evidence" in projected:
            projected["role_evidence"]["available_by_publication_utc"]=cycle["published_at"]
        yield projected


def raw_terminal_records(connection, runs, *, end=None):
    """Read independent terminal evidence tied to one immutable raw publication."""
    records = defaultdict(list)
    if not has_raw_archive(connection):
        return records
    configs = read_configs(connection, False)
    for raw_event in connection.execute("SELECT * FROM raw_event_observations WHERE status='RESOLVED' ORDER BY observed_at"):
        event = dict(raw_event)
        run = runs.get(event["run_id"], {})
        if run.get("status") != "SUCCESS":
            continue
        raw_cycle = connection.execute("SELECT * FROM raw_book_cycles WHERE run_id=?", (event["run_id"],)).fetchone()
        if raw_cycle is None:
            continue
        cycle = dict(raw_cycle)
        temporal = [_raw_timestamp(value) for value in (run.get("started_at"), cycle["observed_at"], event["observed_at"], cycle["published_at"], run.get("finished_at"))]
        if any(value is None for value in temporal) or any(a > b for a,b in zip(temporal,temporal[1:])):
            continue
        if end is not None and temporal[-1] >= timestamp(end):
            continue
        payload, slots, config, issues, complete = _raw_context(connection, cycle, event, runs, configs)
        if issues or not complete or event["reason"] != "exact_all_condition_terminal_proofs":
            continue
        conditions = defaultdict(dict)
        for identity in slots.values():
            conditions[identity["condition_id"]][identity["token_id"]] = identity["outcome"]
        closed = {m.get("conditionId") for m in payload.get("market_context", []) if m.get("closed") is True}
        proofs = payload.get("terminal_proofs", [])
        if not isinstance(proofs, list) or len(proofs) != len(conditions):
            continue
        accepted, seen, yes_values = [], set(), []
        try:
            for proof in proofs:
                condition = proof["condition_id"]
                if condition in seen or condition not in closed or condition not in conditions:
                    raise ValueError("terminal condition scope")
                seen.add(condition)
                tokens = proof["tokens"]
                if len(tokens) != 2 or {t["token_id"]:t["outcome"] for t in tokens} != conditions[condition]:
                    raise ValueError("terminal token mapping")
                values = [number(t["probability"]) for t in tokens]
                if sorted(values) not in ([0.0, 1.0], [0.5, 0.5]):
                    raise ValueError("terminal payout is not exact")
                if cycle["sport_family"] == "soccer":
                    yes_values.append(next(value for t,value in zip(tokens,values) if t["outcome"] == "Yes"))
                accepted.extend((condition,t["token_id"],value) for t,value in zip(tokens,values))
            if set(conditions) != seen or (cycle["sport_family"] == "soccer" and sorted(yes_values) not in ([0.0,0.0,1.0],[0.5,0.5,0.5])):
                raise ValueError("terminal result set inconsistent")
        except (KeyError, TypeError, ValueError, StopIteration):
            continue
        for condition, token, value in accepted:
            records[(condition, token)].append({"payout": value, "observed_at": cycle["published_at"],
                "observed_at_basis": "RAW_CYCLE_PUBLICATION_AFTER_LOOKUP_RECEIPT_UPPER_BOUND",
                "event_cycle_reference_at": event["observed_at"],
                "source": "FULL_SPORTS_RAW_GAMMA_TERMINAL", "observer_config": cycle["config_hash"],
                "profile": config.get("sport_profile_version"), "protocol": config.get("protocol_sha256"),
                "classifier": config.get("classifier_version"), "mapping": config.get("league_mapping_sha256"),
                "raw_event_observation_id": event["observation_id"]})
    return records


def terminal_records(connection, white, runs, *, end=None):
    """Require successful observer, closed exact two-token one-hot/void evidence."""
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    records = defaultdict(list)
    candidates = ["resolution_observations"]
    if "tracked_resolution_observations" in tables:
        candidates.append("tracked_resolution_observations")
    for table in candidates:
        if table not in tables:
            continue
        for raw in connection.execute(f"SELECT * FROM {table}"):
            row = dict(raw)
            if end is not None and timestamp(row.get("observed_at")) >= timestamp(end):
                continue
            if runs.get(row.get("run_id"), {}).get("status") != "SUCCESS":
                continue
            try:
                evidence = json.loads(row.get("evidence_json") or "{}")
                tokens = evidence.get("tokens", [])
                if evidence.get("closed") is not True or len(tokens) != 2:
                    continue
                payouts = {str(t["token_id"]): number(t.get("payout", t.get("price"))) for t in tokens}
                values = sorted(v for v in payouts.values() if v is not None)
                if len(payouts) != 2 or values not in ([0.0, 1.0], [0.5, 0.5]):
                    continue
                if any("winner" in t and t["winner"] is not (payouts[str(t["token_id"])] == 1.0) for t in tokens):
                    continue
                for token, payout in payouts.items():
                    records[(row["condition_id"], token)].append({"payout": payout, "observed_at": row["observed_at"], "source": row.get("source", "CLOB/GAMMA"), "observer_config": runs[row["run_id"]]["config_hash"], "profile": row.get("sport_profile_version"), "protocol": row.get("protocol_sha256"), "classifier": row.get("classifier_version"), "mapping": row.get("league_mapping_sha256")})
            except (KeyError, TypeError, ValueError):
                continue
    if not white:
        for key, values in raw_terminal_records(connection, runs, end=end).items():
            records[key].extend(values)
    return records


def _legacy_trading_rows(connection, start, end):
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "market_snapshots" not in tables:
        return
    catalogs = {r["condition_id"]: dict(r) for r in connection.execute("SELECT * FROM market_catalog")} if "market_catalog" in tables else {}
    exclude = " AND NOT EXISTS (SELECT 1 FROM raw_book_cycles raw_cycle WHERE raw_cycle.run_id=market_snapshots.run_id)" if has_raw_archive(connection) else ""
    for raw in connection.execute("SELECT * FROM market_snapshots WHERE timestamp>=? AND timestamp<?"+exclude+" ORDER BY timestamp,id", (start.replace("T", " ").removesuffix("Z"), end.replace("T", " ").removesuffix("Z"))):
        row = dict(raw)
        catalog = catalogs.get(row["condition_id"], {})
        row.update(title=catalog.get("event_title") or row["event_id"], slug=catalog.get("event_slug"), question=catalog.get("question"), league=row.get("league_code") or catalog.get("league_code"), game_start=None)
        row["clock"] = {"reason": row.get("source_clock_reason"), "source_updated_at": row.get("source_updated_at")}
        # Peach direct-sport elapsed is a scheduled-start age, not an inning/quarter.
        if row.get("source_clock_reason") == "SCHEDULED_START_AGE_SHADOW_ONLY":
            row["clock"]["scheduled_age_minutes"] = row.get("source_elapsed_minutes")
            row["source_elapsed_minutes"] = None
        row["book"] = json.loads(row.get("book_json") or "{}")
        if isinstance(row["book"].get("source_sport_context"), dict):
            row["clock"]["source_sport_context"] = row["book"]["source_sport_context"]
        row.update(direct_role_metadata(row,None,scope="LEGACY_NO_POINT_IN_TIME_TEAM_ORDERING"))
        yield row


def trading_rows(connection, start, end):
    """Raw publications supersede same-run legacy snapshots; old runs survive."""
    from heapq import merge
    yield from merge(_legacy_trading_rows(connection, start, end),
                     raw_archive_rows(connection, start, end),
                     key=lambda row: (timestamp(row["timestamp"]), str(row["id"])))


def white_rows(connection, start, end):
    # Join by exact run AND token. Metadata from another minute is never forwarded.
    columns={r[1] for r in connection.execute("PRAGMA table_info(event_observations)")}
    role_columns=", ".join((f"e.{name}" if name in columns else "NULL")+f" AS role_{name}"
        for name in ("teams_json","event_id","event_observation_id","observed_at","run_id"))
    market_columns={r[1] for r in connection.execute("PRAGMA table_info(market_observations)")}
    role_columns+=", "+", ".join((f"m.{name}" if name in market_columns else "NULL")+f" AS role_market_{name}"
        for name in ("run_id","observed_at"))
    query = """SELECT b.*,o.condition_id,o.event_id,o.outcome_label,o.outcome_index,
      m.event_title,m.question,m.normalized_json,m.classification_evidence_json,
      e.league_code,e.event_slug, """+role_columns+"""
      FROM orderbook_snapshots b
      JOIN outcome_observations o ON o.run_id=b.run_id AND o.token_id=b.token_id
      JOIN market_observations m ON m.observation_id=o.market_observation_id
      JOIN event_observations e ON e.event_observation_id=m.event_observation_id
      WHERE b.observed_at>=? AND b.observed_at<? AND m.eligible=1
      ORDER BY b.observed_at,b.snapshot_id"""
    for raw in connection.execute(query, (start, end)):
        r = dict(raw)
        norm = json.loads(r["normalized_json"])
        classification = json.loads(r["classification_evidence_json"])
        book = {"token_id": r["token_id"], "asks": [], "bids": []}
        for side, price, size in connection.execute("SELECT side,price,size FROM orderbook_levels WHERE snapshot_id=? ORDER BY side,level_index", (r["snapshot_id"],)):
            book["asks" if side == "ASK" else "bids"].append({"price": price, "size": size})
        family = classification.get("sport_family", "soccer")
        clock = norm.get("sports_clock", {})
        result_kind = classification.get("result_kind")
        if classification.get("result_kinds_by_index"):
            result_kind = classification["result_kinds_by_index"][r["outcome_index"]]
        minute = None
        # White preserves the source period/elapsed verbatim. A source-native
        # clock remains distinct from the normalized minutes in Silver/Grey.
        projected = {"id": r["snapshot_id"], "run_id": r["run_id"], "event_id": r["event_id"], "condition_id": r["condition_id"], "token_id": r["token_id"], "outcome": r["outcome_label"], "outcome_side": "DIRECT" if family != "soccer" else r["outcome_label"].upper(), "result_kind": result_kind, "timestamp": r["observed_at"], "sport_family": family, "title": r["event_title"], "slug": r["event_slug"], "question": r["question"], "league": r["league_code"], "game_start": norm.get("game_start_time"), "best_bid": r["best_bid"], "best_ask": r["best_ask"], "midpoint": (r["best_bid"]+r["best_ask"])/2 if r["best_bid"] is not None and r["best_ask"] is not None else None, "source_elapsed_minutes": minute, "clock": {k:clock.get(k) for k in ("source", "period", "elapsed_raw", "score", "received_at", "last_update", "join_status")}, "book": book}
        try:
            teams=json.loads(r.get("role_teams_json") or "null")
            token_index=r["outcome_index"];labels=norm.get("labels",[]);tokens=norm.get("tokens",[])
            event_time=timestamp(r.get("role_observed_at"));market_time=timestamp(r.get("role_market_observed_at"));book_time=timestamp(r["observed_at"])
            aligned=(not isinstance(token_index,bool) and isinstance(token_index,int) and token_index in (0,1)
                     and len(labels)==len(tokens)==2 and len(set(tokens))==2
                     and str(tokens[token_index])==str(r["token_id"]) and labels[token_index]==r["outcome_label"]
                     and str(norm.get("event_id"))==str(r["event_id"])
                     and str(norm.get("condition_id"))==str(r["condition_id"])
                     and bool(r.get("role_event_id")) and bool(r.get("role_event_observation_id"))
                     and r.get("role_run_id")==r["run_id"] and r.get("role_market_run_id")==r["run_id"]
                     and event_time is not None and market_time is not None and book_time is not None
                     and event_time<=book_time and market_time<=book_time)
        except (TypeError,ValueError,IndexError):teams=None;aligned=False
        projected.update(direct_role_metadata(projected,teams,
            scope="SAME_WHITE_EVENT_OBSERVATION_EXPLICIT_ORDERING",identity_proven=aligned,
            evidence_event_id=r.get("role_event_id"),evidence_observation_id=r.get("role_event_observation_id"),
            evidence_observed_at=r.get("role_observed_at")))
        yield projected


def export_source(source, output, start, end, index, *, include_depth=False):
    path = Path(source["local_path"])
    before = sha256(path)
    if before != source["local_sha256"]:
        raise ValueError(f"manifest checksum mismatch: {source['id']}")
    connection = sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ValueError("SQLite quick_check failed")
    white = source["strategy"] == "golden-watermelon"
    configs, runs = read_configs(connection, white), read_runs(connection, white)
    terminals = terminal_records(connection, white, runs, end=end)
    get_rows = white_rows if white else trading_rows
    try:
        export_rows(source, output, start, end, index, get_rows(connection, start, end),
                    configs, runs, terminals, include_depth=include_depth, white=white)
    finally:
        connection.close()
    if sha256(path) != before:
        raise ValueError(f"source changed during export: {source['id']}")


def export_rows(source, output, start, end, index, rows, configs, runs, terminals, *, include_depth=False, white=False):
    """Shared projection for explicitly verified reader contracts, no source I/O."""
    groups = {}
    for row in rows:
        run = runs.get(row["run_id"], {})
        config = row.get("resolved_cohort") or configs.get(run.get("config_hash"), {})
        family = row.get("sport_family") or config.get("sport") or "unknown"
        cohort = {**config, "source_id": source["id"], "job_name": source["runtime_job"], "sport": family}
        cohort_id = identifier(cohort)
        index["cohorts"].setdefault(cohort_id, {"id": cohort_id, **cohort})
        key = (row["event_id"], cohort_id)
        if key not in groups:
            groups[key] = {"id": identifier([source["id"], *key]), "event_id": row["event_id"], "title": row["title"], "slug": row.get("slug"), "sport": family, "league": row.get("league") or family.upper(), "source_id": source["id"], "cohort_id": cohort_id, "tokens": {}, "rows": [], "clock_labels": [], "clock_lookup": {}}
        group = groups[key]
        if include_depth:
            market = row.get("point_in_time_market_fields") or {}
            group.setdefault("depth", []).append({
                "book": row["book"], "run_id": row["run_id"],
                "observation_id": row["id"], "fee_market": market,
                "market_open": (all(market.get(k) is True for k in ("active", "enableOrderBook", "acceptingOrders")) and market.get("closed") is False) if market else None,
                "identity_valid": row.get("raw_point_in_time_identity_proven"),
            })
        token = row["token_id"]
        token_key = token if token is not None else ("unidentified_slot", row.get("result_kind"), row.get("outcome_side"), row.get("slot"))
        token_id = len(group["tokens"])
        if token_key not in group["tokens"]:
            label = row.get("display_label") or (f"{row.get('result_kind') or '?'} {row.get('outcome_side') or row['outcome']}" if family == "soccer" else row["outcome"])
            group["tokens"][token_key] = {"index": token_id, "token_id": token, "condition_id": row["condition_id"], "label": label, "result_kind": row.get("result_kind"), "outcome_side": row.get("outcome_side"), "question": row.get("question"), "payout": None, "payout_observed_at": None}
            candidates = terminals.get((row["condition_id"], token), [])
            if source["strategy"] == "golden-coconut":
                candidates = [x for x in candidates
                    if x.get("config_hash") == cohort.get("config_hash")
                    and x.get("strategy_source_digest") == cohort.get("strategy_source_digest")
                    and x.get("job_name") == source["runtime_job"]
                    and x.get("observation_mode") == "SCHEDULED"]
            if source["strategy"] == "golden-plum":
                candidates = [x for x in candidates if x["profile"] == cohort.get("sport_profile_version") and x["protocol"] == cohort.get("protocol_sha256") and x["classifier"] == cohort.get("classifier_version") and x["mapping"] == cohort.get("league_mapping_sha256")]
            if candidates and len({x["payout"] for x in candidates}) == 1:
                selected = min(candidates, key=lambda x: timestamp(x["observed_at"]))
                group["tokens"][token_key].update(payout=selected["payout"], payout_observed_at=selected["observed_at"], payout_source=selected["source"])
        _merge_role_metadata(group["tokens"][token_key],row)
        token_id = group["tokens"][token_key]["index"]
        flags = 0
        if run.get("status") != "SUCCESS":
            flags |= FLAGS["failed_run"]
        if not config.get("strategy_source_digest") or row.get("config_hash", run.get("config_hash")) != run.get("config_hash") or row.get("strategy_source_digest", config.get("strategy_source_digest")) != config.get("strategy_source_digest"):
            flags |= FLAGS["identity_gap"]
        ask, bid = depth_prices(row["book"], token)
        if ask is None or bid is None:
            flags |= FLAGS["depth_missing"]
        if row.get("event_set_complete") == 0:
            flags |= FLAGS["incomplete_set"]
        if row.get("raw_point_in_time_identity_proven") is False or row.get("raw_book_integrity_error") is True:
            flags |= FLAGS["identity_gap"]
        raw_clock = row.get("clock", {})
        clock_key = compact(raw_clock)
        if clock_key not in group["clock_lookup"]:
            group["clock_lookup"][clock_key] = len(group["clock_labels"])
            group["clock_labels"].append(raw_clock)
        def rounded(v):
            return round(v, 6) if number(v) is not None else None
        point = [round(timestamp(row["timestamp"]), 3), token_id, rounded(ask), rounded(bid), rounded(row.get("midpoint")), rounded(row.get("best_bid")), rounded(row.get("best_ask")), rounded(row.get("source_elapsed_minutes")), flags, group["clock_lookup"][clock_key]]
        group["rows"].append((point, row["run_id"]))
    for group in groups.values():
        expected = 3 if white and group["sport"] == "soccer" else (6 if group["sport"] == "soccer" else 2)
        run_sets = defaultdict(set)
        for point, run in group["rows"]:
            run_sets[run].add(point[1])
        previous = {}
        gaps = []
        points = []
        for point, run in group.pop("rows"):
            if len(run_sets[run]) != expected:
                point[8] |= FLAGS["incomplete_set"]
            token = point[1]
            if token in previous and point[0]-previous[token] >= 90:
                point[8] |= FLAGS["gap_before"]
                gaps.append({"start": previous[token], "end": point[0], "token": token, "reason": "observations_gap_ge_90s"})
            previous[token] = point[0]
            points.append(point)
        group["tokens"] = list(group["tokens"].values())
        del group["clock_lookup"]
        clocks = group.pop("clock_labels")
        depth = group.pop("depth", None)
        meta = {k:v for k,v in group.items() if k != "tokens"}
        meta.update(start=min(p[0] for p in points), end=max(p[0] for p in points), point_count=len(points), gap_count=len(gaps), invalid_count=sum(bool(p[8]&15) for p in points), token_count=len(group["tokens"]), resolved_tokens=sum(t["payout"] is not None for t in group["tokens"]), expected_token_count=expected)
        first, last = meta["start"], meta["end"]
        failure_intervals = [{"start": timestamp(r["started_at"]), "end": timestamp(r["finished_at"]) or last, "reason": "failed_or_incomplete_run"} for r in runs.values() if r.get("config_hash") == index["cohorts"][meta["cohort_id"]].get("config_hash") and (not r.get("strategy_source_digest") or r["strategy_source_digest"] == index["cohorts"][meta["cohort_id"]].get("strategy_source_digest")) and r["status"] != "SUCCESS" and timestamp(r["started_at"]) <= last and (timestamp(r["finished_at"]) or last) >= first]
        filename = f"events/{meta['id']}.json"
        payload = {"match": meta, "tokens": group["tokens"], "columns": COLUMNS, "points": points, "gaps": gaps+failure_intervals, "clocks": clocks}
        if include_depth:
            payload["depth"] = depth
        text = compact(payload)
        (output/filename).write_text(text)
        meta["fragment"] = filename
        meta["fragment_bytes"] = len(text.encode())
        index["matches"].append(meta)
    index["sources"].append({k:v for k,v in source.items() if k != "local_path"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True, help="JSON list of located and verified source DB manifests")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", required=True, help="UTC ISO inclusive")
    parser.add_argument("--end", required=True, help="UTC ISO exclusive")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/"events").mkdir(exist_ok=True)
    index = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "range": {"start": args.start, "end_exclusive": args.end}, "sources": [], "cohorts": {}, "matches": [], "columns": COLUMNS, "flags": FLAGS, "semantics": "Displayed exact $5 ask depth and bid liquidation of the same freshly purchased shares; not historical-position exit size, actual fill, investor belief, or realized P&L. No synthetic NO, resampling, interpolation, or cohort stitching.", "sports": [{"id": s, "label": label} for s,label in [("soccer","축구"),("mlb","야구 · MLB"),("nba","농구 · NBA"),("nfl","미식축구 · NFL"),("nhl","아이스하키 · NHL"),("ufc","UFC"),("boxing","복싱")]]}
    for source in json.loads(args.sources.read_text()):
        export_source(source, args.output, args.start, args.end, index)
        print(compact({"source": source["id"], "matches_so_far": len(index["matches"])}), flush=True)
    index["cohorts"] = list(index["cohorts"].values())
    index["matches"].sort(key=lambda m:(m["sport"],m["start"],m["title"],m["source_id"]))
    for sport in index["sports"]:
        rows = [m for m in index["matches"] if m["sport"] == sport["id"]]
        sport.update(match_count=len({m["event_id"] for m in rows}), event_cohort_count=len(rows), point_count=sum(m["point_count"] for m in rows), status="OBSERVED" if rows else ("NOT_REGISTERED" if sport["id"] in ("ufc","boxing") else "NO_MATCH_OBSERVATIONS"))
    (args.output/"index.json").write_text(compact(index))
    print(compact({"index_bytes": (args.output/"index.json").stat().st_size, "event_cohorts":len(index["matches"]), "sports":index["sports"], "largest_fragment":max((m["fragment_bytes"] for m in index["matches"]),default=0)}))


if __name__ == "__main__":
    main()
