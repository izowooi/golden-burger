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


def terminal_records(connection, white, runs, *, end=None):
    """Require successful observer, closed exact two-token one-hot/void evidence."""
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    records = defaultdict(list)
    candidates = ["resolution_observations"]
    if "tracked_resolution_observations" in tables:
        candidates.append("tracked_resolution_observations")
    for table in candidates:
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
    return records


def trading_rows(connection, start, end):
    catalogs = {r["condition_id"]: dict(r) for r in connection.execute("SELECT * FROM market_catalog")}
    for raw in connection.execute("SELECT * FROM market_snapshots WHERE timestamp>=? AND timestamp<? ORDER BY timestamp,id", (start.replace("T", " ").removesuffix("Z"), end.replace("T", " ").removesuffix("Z"))):
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
        yield row


def white_rows(connection, start, end):
    # Join by exact run AND token. Metadata from another minute is never forwarded.
    query = """SELECT b.*,o.condition_id,o.event_id,o.outcome_label,o.outcome_index,
      m.event_title,m.question,m.normalized_json,m.classification_evidence_json,
      e.league_code,e.event_slug
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
        yield {"id": r["snapshot_id"], "run_id": r["run_id"], "event_id": r["event_id"], "condition_id": r["condition_id"], "token_id": r["token_id"], "outcome": r["outcome_label"], "outcome_side": "DIRECT" if family != "soccer" else r["outcome_label"].upper(), "result_kind": result_kind, "timestamp": r["observed_at"], "sport_family": family, "title": r["event_title"], "slug": r["event_slug"], "question": r["question"], "league": r["league_code"], "game_start": norm.get("game_start_time"), "best_bid": r["best_bid"], "best_ask": r["best_ask"], "midpoint": (r["best_bid"]+r["best_ask"])/2 if r["best_bid"] is not None and r["best_ask"] is not None else None, "source_elapsed_minutes": minute, "clock": {k:clock.get(k) for k in ("source", "period", "elapsed_raw", "score", "received_at", "last_update", "join_status")}, "book": book}


def export_source(source, output, start, end, index):
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
    groups = {}
    get_rows = white_rows if white else trading_rows
    for row in get_rows(connection, start, end):
        run = runs.get(row["run_id"], {})
        config = configs.get(run.get("config_hash"), {})
        family = row.get("sport_family") or config.get("sport") or "unknown"
        cohort = {**config, "source_id": source["id"], "job_name": source["runtime_job"], "sport": family}
        cohort_id = identifier(cohort)
        index["cohorts"].setdefault(cohort_id, {"id": cohort_id, **cohort})
        key = (row["event_id"], cohort_id)
        if key not in groups:
            groups[key] = {"id": identifier([source["id"], *key]), "event_id": row["event_id"], "title": row["title"], "slug": row.get("slug"), "sport": family, "league": row.get("league") or family.upper(), "source_id": source["id"], "cohort_id": cohort_id, "tokens": {}, "rows": [], "clock_labels": [], "clock_lookup": {}}
        group = groups[key]
        token = row["token_id"]
        token_id = len(group["tokens"])
        if token not in group["tokens"]:
            label = f"{row.get('result_kind') or '?'} {row.get('outcome_side') or row['outcome']}" if family == "soccer" else row["outcome"]
            group["tokens"][token] = {"index": token_id, "token_id": token, "condition_id": row["condition_id"], "label": label, "result_kind": row.get("result_kind"), "outcome_side": row.get("outcome_side"), "question": row.get("question"), "payout": None, "payout_observed_at": None}
            candidates = terminals.get((row["condition_id"], token), [])
            if source["strategy"] == "golden-plum":
                candidates = [x for x in candidates if x["profile"] == cohort.get("sport_profile_version") and x["protocol"] == cohort.get("protocol_sha256") and x["classifier"] == cohort.get("classifier_version") and x["mapping"] == cohort.get("league_mapping_sha256")]
            if candidates and len({x["payout"] for x in candidates}) == 1:
                selected = min(candidates, key=lambda x: timestamp(x["observed_at"]))
                group["tokens"][token].update(payout=selected["payout"], payout_observed_at=selected["observed_at"], payout_source=selected["source"])
        token_id = group["tokens"][token]["index"]
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
        meta = {k:v for k,v in group.items() if k != "tokens"}
        meta.update(start=min(p[0] for p in points), end=max(p[0] for p in points), point_count=len(points), gap_count=len(gaps), invalid_count=sum(bool(p[8]&15) for p in points), token_count=len(group["tokens"]), resolved_tokens=sum(t["payout"] is not None for t in group["tokens"]), expected_token_count=expected)
        first, last = meta["start"], meta["end"]
        failure_intervals = [{"start": timestamp(r["started_at"]), "end": timestamp(r["finished_at"]) or last, "reason": "failed_or_incomplete_run"} for r in runs.values() if r.get("config_hash") == index["cohorts"][meta["cohort_id"]].get("config_hash") and r["status"] != "SUCCESS" and timestamp(r["started_at"]) <= last and (timestamp(r["finished_at"]) or last) >= first]
        filename = f"events/{meta['id']}.json"
        payload = {"match": meta, "tokens": group["tokens"], "columns": COLUMNS, "points": points, "gaps": gaps+failure_intervals, "clocks": clocks}
        text = compact(payload)
        (output/filename).write_text(text)
        meta["fragment"] = filename
        meta["fragment_bytes"] = len(text.encode())
        index["matches"].append(meta)
    connection.close()
    if sha256(path) != before:
        raise ValueError(f"source changed during export: {source['id']}")
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
