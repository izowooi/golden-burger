"""White parent/sidecar pair to fixed research paths, without parent fallback."""
from collections import Counter, defaultdict
from contextlib import closing
import json
import math
from pathlib import Path

import conservative_sports_grid as grid
import sports_visual_data as visual
import watermelon_raw_sidecar as raw_reader

ORIGIN = raw_reader.CONTRACT


def terminal_payouts(event, slots, sport):
    """Independently decode exact Gamma facts; never read terminal annotations."""
    expected = {"HOME", "DRAW", "AWAY"} if sport == "soccer" else {"OUTCOME_0", "OUTCOME_1"}
    if (not isinstance(event, dict) or event.get("parentEventId") not in (None, "")
            or sport not in {"soccer", "mlb", "nba", "nfl", "nhl"}
            or len(slots) != len(expected) or {s.get("slot") for s in slots} != expected
            or len({s.get("token_id") for s in slots}) != len(expected)
            or len({s.get("condition_id") for s in slots}) != (3 if sport == "soccer" else 1)):
        return None
    out = {}
    for slot in slots:
        markets = [m for m in event.get("markets", []) if isinstance(m, dict)
                   and str(m.get("conditionId") or m.get("condition_id")) == slot.get("condition_id")]
        if len(markets) != 1:
            return None
        m = markets[0]
        try:
            tokens, labels, prices = [raw_reader.array(m.get(k)) for k in ("clobTokenIds", "outcomes", "outcomePrices")]
            if (m.get("closed") is not True or len(tokens) != 2 or len(labels) != 2 or len(prices) != 2
                    or len(set(tokens)) != 2 or any(not isinstance(t, str) or not t for t in tokens)
                    or dict(zip(tokens, labels)) != dict(zip(slot["all_tokens"], slot["all_outcomes"]))
                    or dict(zip(tokens, labels)).get(slot["token_id"]) != slot["outcome"]
                    or any(isinstance(p, bool) for p in prices)):
                return None
            prices = [float(p) for p in prices]
            if not all(math.isfinite(p) for p in prices):
                return None
            void = prices == [.5, .5] and str(m.get("umaResolutionStatus", "")).casefold() == "resolved"
            if prices not in ([0., 1.], [1., 0.]) and not void:
                return None
            out[(slot["condition_id"], slot["token_id"])] = dict(zip(tokens, prices))[slot["token_id"]]
        except (KeyError, ValueError, TypeError):
            return None
    values = sorted(out.values())
    if sport == "soccer" and values not in ([0., 0., 1.], [.5, .5, .5]):
        return None
    if sport != "soccer" and values not in ([0., 1.], [.5, .5]):
        return None
    return out


def publication_valid(parent, raw, cycle, cutoff):
    history = [dict(r) for r in parent.execute("SELECT * FROM research_run_events WHERE run_id=?", (cycle["run_id"],))]
    starts = [r for r in history if r["event_type"] == "STARTED"]
    ends = [r for r in history if r["event_type"] == "SUCCEEDED"]
    if len(starts) != 1 or len(ends) != 1 or len(history) != 2 or cycle["status"] != "PUBLISHED":
        return False
    cfg = parent.execute("SELECT * FROM research_config_versions WHERE config_hash=?", (cycle["config_hash"],)).fetchone()
    if (cfg is None or cfg["mode"] != "sim" or cfg["job_name"] != cycle["job_name"]
            or cfg["strategy_source_digest"] != cycle["source_digest"] or cycle["contract"] != ORIGIN
            or cycle["parent_filename"] != raw.execute("SELECT parent_filename FROM raw_metadata").fetchone()[0]
            or any(r["config_hash"] != cycle["config_hash"] or r["strategy_source_digest"] != cycle["source_digest"] for r in history)):
        return False
    times = [raw_reader.stamp(v) for v in (starts[0]["observed_at"], cycle["reference_at"], cycle["published_at"], ends[0]["observed_at"])]
    return all(t is not None for t in times) and times[0] <= times[1] <= times[2] <= times[3] < cutoff


def decode_connections(source, parent, raw, start, end):
    cutoff = raw_reader.stamp(end)
    if cutoff is None or raw_reader.stamp(start) is None or raw_reader.stamp(start) >= cutoff:
        raise ValueError("invalid UTC interval")
    configs = visual.read_configs(parent, True)
    raw_configs = {r["config_hash"]: json.loads(r["config_json"]).get("trading", {})
                   for r in parent.execute("SELECT * FROM research_config_versions")}
    runs = visual.read_runs(parent, True)
    cycles = {r["run_id"]: dict(r) for r in raw.execute("SELECT * FROM raw_cycles")}
    publication = {key: publication_valid(parent, raw, value, cutoff) for key, value in cycles.items()}
    stats = Counter()
    groups = defaultdict(lambda: defaultdict(list))
    titles, structural_events = {}, set()
    for row in raw_reader.iter_rows(parent, raw, start, end):
        stats["raw_rows"] += 1
        stats["status_" + row["status"]] += 1
        event, market, book = row.get("event") or {}, row.get("market") or {}, row.get("book") or {}
        family = row["sport_family"]
        key = (row["config_hash"], family, row["event_id"])
        if (event and event.get("parentEventId") in (None, "") and row["token_id"] and row["condition_id"]):
            structural_events.add(key)
        valid = row["evidence_valid"] and publication.get(row["run_id"], False)
        try:
            asks, bids = grid.levels(book, "asks"), grid.levels(book, "bids")
            if asks and bids and bids[0][0] > asks[0][0] + grid.EPS:
                valid = False
        except ValueError:
            asks = bids = ()
            valid = False
        spread = asks[0][0] - bids[0][0] if asks and bids else None
        midpoint = (asks[0][0] + bids[0][0]) / 2 if asks and bids else None
        if family == "soccer":
            slot = (row["slot"], "YES")
        elif family in {"mlb", "nba", "nfl", "nhl"}:
            slot = ({"OUTCOME_0": "HOME", "OUTCOME_1": "AWAY"}.get(row["slot"]), "DIRECT")
        else:
            stats["unsupported_sport_rows"] += 1
            continue
        market_open = (str(market.get("conditionId") or market.get("condition_id")) == row["condition_id"]
                       and market.get("active") is True and market.get("closed") is False
                       and market.get("acceptingOrders") is True and market.get("enableOrderBook") is True)
        opened = (valid and market_open and event.get("active") is True and event.get("closed") is False
                  and event.get("live") is True and event.get("ended") is False)
        liquidity = visual.number(market.get("liquidityNum", market.get("liquidity")))
        volume = visual.number(market.get("volumeNum", market.get("volume")))
        gated = opened and liquidity is not None and liquidity >= 5000 and volume is not None and volume >= 5000
        # Invalid receipt times must not move an unknown barrier past a later
        # profitable quote. This is a missing observation at the actual cycle,
        # not a quote at a caller-supplied, unproved timestamp.
        observed_at = raw_reader.stamp(row["received_at"]) if valid else raw_reader.stamp(row["reference_at"])
        scheduled = raw_reader.stamp(event.get("startTime") or event.get("gameStartTime"))
        identity = {"sport_family": family, "outcome_side": slot[1], "result_kind": slot[0],
                    "event_id": row["event_id"], "condition_id": row["condition_id"], "token_id": row["token_id"], "outcome": row["outcome"]}
        role = visual.direct_role_metadata(identity, event.get("teams"), scope="WHITE_SIDECAR_EXPLICIT_SOURCE_ORDERING",
            identity_proven=bool(valid), evidence_event_id=event.get("id"), evidence_observed_at=row["metadata_received_at"])
        snap = grid.Snap(token=str(row["token_id"] or "unidentified:" + row["slot"]), condition=row["condition_id"], slot=slot,
            time=observed_at, run=row["run_id"], minute=grid.raw_source_minute(event, family), midpoint=midpoint,
            asks=asks, bids=bids, valid=bool(valid), open_observed=bool(opened), gate_observed=bool(gated),
            fee_rate=grid.raw_fee_rate(market), spread=spread, ask_state=grid.ask_side_state(book),
            source_reason="WHITE_RAW_GAMMA_CLOCK", buy=grid.walk(asks, 5, True),
            scheduled_age=(observed_at - scheduled) / 60 if valid and scheduled is not None else None,
            entry_set_complete=True, evidence_origin=ORIGIN, observation_status=row["status"],
            market_open_observed=bool(market_open), outcome_label=row["outcome"], verified_role=role.get("verified_role"),
            verified_team_name=role.get("verified_team_name"), role_evidence_scope=role.get("role_evidence_scope"),
            legacy_result_kind=slot[0], legacy_role_semantics=role.get("legacy_role_semantics"))
        groups[key][row["run_id"]].append(snap)
        titles[key] = event.get("title") or row["event_id"]
        stats["valid_rows" if valid else "invalid_rows"] += 1

    # Terminal metadata is independently verified even when no executable book
    # exists. A failed cycle's annotation never supplies payout evidence.
    terminal_by_key = defaultdict(lambda: defaultdict(list))
    terminal_cycle_times = {}
    evidence = raw_reader.Evidence(parent, raw)
    for stored in raw.execute("SELECT * FROM raw_events ORDER BY run_id,event_id"):
        r = dict(stored)
        cycle = cycles.get(r["run_id"])
        if not cycle or not publication.get(r["run_id"]) or not r["identity_valid"] or r["metadata_status"] != "OBSERVED":
            continue
        try:
            event, request_start = evidence.event(r)
            started = raw_reader.stamp(runs[r["run_id"]]["started_at"])
            qt, mt, pt = [raw_reader.stamp(t) for t in (request_start, r["metadata_received_at"], cycle["published_at"])]
            if any(t is None for t in (started, qt, mt, pt)) or not started <= qt <= mt <= pt < cutoff:
                continue
            payouts = terminal_payouts(event, json.loads(r["slots_json"]), r["family"])
        except (ValueError, TypeError, KeyError, OSError):
            continue
        if payouts:
            key = (cycle["config_hash"], r["family"], r["event_id"])
            terminal_cycle_times[(key, r["run_id"])] = raw_reader.stamp(cycle["published_at"])
            for token_key, payout in payouts.items():
                terminal_by_key[key][token_key].append({"payout": payout, "observed_at": cycle["published_at"],
                    "source": "VERIFIED_WHITE_RAW_GAMMA", "observer_config": cycle["config_hash"]})
                stats["verified_terminal_token_facts"] += 1

    events = []
    pair = [source["parent_source_key"], source["sidecar_source_key"], ORIGIN]
    for key, by_run in groups.items():
        if key not in structural_events:
            stats["unidentified_or_child_event_groups"] += 1
            continue
        cfg_hash, family, event_id = key
        failures = []
        for run_id, run in runs.items():
            if run["config_hash"] != cfg_hash:
                continue
            a, b = raw_reader.stamp(run["started_at"]), raw_reader.stamp(run.get("finished_at"))
            if a is not None and a < cutoff and not publication.get(run_id, False):
                failures.append((a, b))
        cfg = {**configs[cfg_hash], "trading": raw_configs[cfg_hash], "raw_point_in_time_archive": True,
               "evidence_origins": [ORIGIN], "paired_source_keys": pair, "raw_view": "WHITE_PAIR_NO_PARENT_FALLBACK"}
        normalized = []
        for run_id, ss in by_run.items():
            group = grid.normalize_group(ss, family, True)
            terminal_time = terminal_cycle_times.get((key, run_id))
            if terminal_time is not None:
                # A verified terminal publication is an independent observation
                # for this exact cycle. Its missing book is not a pre-terminal
                # unknown quote. Earlier runs' unknown barriers stay in place,
                # and actual individual book timestamps remain unchanged.
                group.time = max(group.time, terminal_time)
            normalized.append(group)
        events.append(grid.Event(source["id"], visual.identifier([source["id"], cfg_hash, family, pair]), family,
            event_id, titles[key], cfg, sorted(normalized, key=lambda g: g.time),
            failures, dict(terminal_by_key[key])))
    return events, {"source": source["id"], "view": "WHITE_PAIR_NO_PARENT_FALLBACK", "pair": pair,
                    "events": len(events), "stats": dict(stats), "cycles": len(cycles),
                    "verified_publications": sum(publication.values())}


def read_source(source, start, end):
    if not source.get("pinned") or not source.get("parent_source_key") or not source.get("sidecar_source_key"):
        raise ValueError("two explicit verified source identities required")
    if source["parent_source_key"] == source["sidecar_source_key"]:
        raise ValueError("parent and sidecar source identities must differ")
    parent_path, raw_path = Path(source["local_path"]).resolve(), Path(source["sidecar_path"]).resolve()
    if parent_path == raw_path:
        raise ValueError("parent and sidecar files must differ")
    with closing(raw_reader.open_pin(parent_path, source["local_sha256"])) as parent, closing(raw_reader.open_pin(raw_path, source["sidecar_sha256"])) as raw:
        if parent.execute("PRAGMA application_id").fetchone()[0] != 1196903732:
            raise ValueError("not a White primary database")
        raw_reader.validate_contract(raw, parent_path.name)
        result = decode_connections(source, parent, raw, start, end)
    if raw_reader.sha256(parent_path) != source["local_sha256"] or raw_reader.sha256(raw_path) != source["sidecar_sha256"]:
        raise ValueError("paired pin changed during replay")
    result[1].update(parent_sha256=source["local_sha256"], sidecar_sha256=source["sidecar_sha256"])
    return result
