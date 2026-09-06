#!/usr/bin/env python3
"""Read-only health of one verified pinned Guava snapshot per runtime.

Repeat --db /absolute/.../pinned/<snapshot>/trades_sim.db; supply UTC --start
and exclusive --end. Requires adjacent daily-rsync pin manifest (SHA, quick_check).
Run-start scope is half-open; later terminal events are reported, not backdated.
quick_check/FK inspect each copy; gzip/hash checks inspect selected runs only.
No bot imports, network, P&L, database creation, or storage-growth estimates.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import sqlite3

RUNTIMES = {f"guava-research-{c}-v1": i for i, c in enumerate("abcd")}
CONTRACT = "guava-research-v1"
MAX_RAW = 32 * 1024 * 1024


def utc(value):
    if not isinstance(value, str):
        raise ValueError("UTC timestamp must be text")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("explicit UTC timestamps required")
    return result


def shard_for(event_id):
    return int.from_bytes(hashlib.sha256(str(event_id).encode()).digest()[:8], "big") % 4


def fingerprint(path):
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def verified_path(value):
    p = Path(value)
    if not p.is_absolute() or p != p.resolve(strict=True):
        raise ValueError("absolute canonical path required; aliases forbidden")
    if "pinned" not in p.parts or "latest" in p.parts or not p.is_file():
        raise ValueError("explicit pinned standalone file required, never latest/source")
    if any(Path(str(p) + s).exists() for s in ("-wal", "-shm", "-journal")):
        raise ValueError("snapshot has SQLite sidecars")
    manifest = json.loads(p.with_name("manifest.json").read_text())
    if not isinstance(manifest, dict): raise ValueError("pin manifest must be an object")
    if manifest.get("pinned_path") != str(p) or manifest.get("quick_check") != ["ok"]:
        raise ValueError("pin manifest identity/verification missing")
    source = Path(manifest.get("source", ""))
    if not source.is_absolute() or (source.exists() and source.samefile(p)):
        raise ValueError("snapshot aliases its source")
    before = fingerprint(p)
    with p.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != manifest.get("sha256") or before != fingerprint(p):
        raise ValueError("snapshot checksum/stat mismatch")
    return p, digest, before


def raw_check(blob, sha):
    if blob is None:
        if sha is not None:
            raise ValueError("hash without raw payload")
        return None
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as handle:
        raw = handle.read(MAX_RAW + 1)
    if len(raw) > MAX_RAW or hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError("raw size/hash mismatch")
    return json.loads(raw)


def cadence(runs, statuses, start, end):
    slots = Counter(int(utc(r["started_at"]).timestamp()) // 60 for r in runs)
    success = {int(utc(r["started_at"]).timestamp()) // 60 for r in runs
               if statuses[r["run_id"]] == "SUCCEEDED"}
    expected = math.ceil(end.timestamp() / 60) - math.floor(start.timestamp() / 60)
    return {"started": len(runs), "statuses": dict(Counter(statuses[r["run_id"]] for r in runs)),
            "observed_slots": len(slots), "successful_slots": len(success),
            "duplicate_slots": sum(n - 1 for n in slots.values()),
            "slots_intersecting_requested_range": expected,
            "unobserved_slots_requested_range": max(0, expected - len(slots)),
            "success_ratio_requested_range": len(success) / expected if expected else None,
            "unobserved_slots_in_observed_span": max(slots) - min(slots) + 1 - len(slots) if slots else None,
            "first_started_at": min((r["started_at"] for r in runs), default=None),
            "last_started_at": max((r["started_at"] for r in runs), default=None)}


def inspect_snapshot(path, start, end):
    p, sha, before = verified_path(path)
    errors, groups, statuses, warnings = [], defaultdict(list), {}, []
    pin_at = json.loads(p.with_name("manifest.json").read_text()).get("created_at")
    if not pin_at or end > utc(pin_at):
        warnings.append("SCOPE_END_AFTER_PIN_CREATION_OR_CUTOFF_UNKNOWN")
    c = sqlite3.connect(p.as_uri() + "?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA query_only=ON")
        contracts = c.execute("SELECT * FROM collection_contracts").fetchall()
        if len(contracts) != 1:
            raise ValueError("expected one collection contract")
        identity = dict(contracts[0]); job = identity["job_name"]
        if job not in RUNTIMES or identity["mode"] != "sim" or identity["data_contract"] != CONTRACT or identity["strategy_name"] != "golden-guava":
            raise ValueError("not an accountless Guava research runtime")
        check = [r[0] for r in c.execute("PRAGMA quick_check")]
        fk = [list(r) for r in c.execute("PRAGMA foreign_key_check")]
        if check != ["ok"] or fk:
            errors.append("SQLITE_INTEGRITY")
        runs = [dict(r) for r in c.execute("SELECT * FROM run_audits ORDER BY started_at")
                if start <= utc(r["started_at"]) < end]
        for run in runs:
            rid = run["run_id"]; contract = json.loads(run["contract_json"])
            if any(run[k] != identity[k] for k in ("job_name", "mode", "strategy_name", "data_contract")):
                errors.append("RUN_IDENTITY:" + rid)
            if contract.get("shard_index") != RUNTIMES[job] or contract.get("shard_count") != 4 or contract.get("trading", {}).get("cadence_seconds") != 60:
                errors.append("SHARD_OR_CADENCE_CONFIG:" + rid)
            config = c.execute("SELECT * FROM strategy_configs WHERE config_hash=? AND strategy_source_digest=?",
                               (run["config_hash"], run["strategy_source_digest"])).fetchone()
            if not config or hashlib.sha256(config["config_json"].encode()).hexdigest() != config["snapshot_sha256"] or json.loads(config["config_json"]) != contract:
                errors.append("CONFIG_HASH_OR_SNAPSHOT:" + rid)
            keys = ("strategy_name", "job_name", "mode", "data_contract", "config_hash", "strategy_source_digest")
            if any(contract.get(k) != run[k] for k in keys):
                errors.append("CONTRACT_RUN_MISMATCH:" + rid)
            canonical = json.dumps({k: run[k] for k in keys}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if hashlib.sha256(canonical.encode()).hexdigest() != run["cohort_key"]:
                errors.append("COHORT_KEY:" + rid)
            lifecycle = c.execute("SELECT * FROM run_events WHERE run_id=?", (rid,)).fetchall()
            terminal = [r for r in lifecycle if r["status"] != "STARTED"]
            if sum(r["status"] == "STARTED" for r in lifecycle) != 1 or len(terminal) > 1:
                errors.append("RUN_LIFECYCLE:" + rid)
            statuses[rid] = next((r["status"] for r in terminal if utc(r["occurred_at"]) < end), "PENDING_AT_END")
            cycle = c.execute("SELECT * FROM cycles WHERE run_id=?", (rid,)).fetchone()
            if bool(cycle) != any(r["status"] == "SUCCEEDED" for r in terminal):
                errors.append("ATOMIC_SUCCESS:" + rid)
            groups[(run["config_hash"], run["strategy_source_digest"], run["cohort_key"])].append(run)
        cohorts = []
        for (config, source, key), members in groups.items():
            counts, exclusions, book_status, raw_counts, census = Counter(), Counter(), Counter(), Counter(), Counter()
            times, current_events = [], {}
            for run in members:
                rid = run["run_id"]
                for table, blob, digest in (("source_requests", "payload_gzip", "payload_sha256"), ("events", "raw_gzip", "raw_sha256"), ("book_attempts", "raw_gzip", "raw_sha256")):
                    for row in c.execute(f"SELECT {blob},{digest} FROM {table} WHERE run_id=?", (rid,)):
                        try:
                            raw_check(row[0], row[1]); raw_counts[table + ("_checked" if row[0] is not None else "_absent")] += 1
                        except (ValueError, OSError, EOFError, TypeError):
                            errors.append("RAW_HASH_GZIP:" + table + ":" + rid)
                cycle = c.execute("SELECT * FROM cycles WHERE run_id=?", (rid,)).fetchone()
                if not cycle or not start <= utc(cycle["observed_at"]) < end:
                    continue
                if cycle["cohort_key"] != key:
                    errors.append("CYCLE_COHORT:" + rid)
                summary = json.loads(cycle["summary_json"])
                census[str(summary.get("census_complete"))] += 1
                if summary.get("census_complete") is not True:
                    errors.append("CENSUS_INCOMPLETE:" + rid)
                for event in c.execute("SELECT * FROM events WHERE run_id=?", (rid,)):
                    eid = event["event_id"]; times.append(event["observed_at"])
                    if shard_for(eid) != RUNTIMES[job] or event["cohort_key"] != key:
                        errors.append("EVENT_SHARD_OR_COHORT:" + rid + ":" + eid)
                    tokens = json.loads(event["expected_token_ids_json"])
                    expected = 6 if event["sport_family"] == "soccer" else 2
                    counts["events"] += 1; counts["eligible_events"] += event["eligible"]
                    if event["eligible"] and (event["sport_family"] not in ("soccer", "mlb", "nba", "nfl", "nhl") or len(tokens) != expected or len(set(tokens)) != expected):
                        errors.append("EXPECTED_TOKEN_SHAPE:" + rid + ":" + eid)
                    if not event["eligible"]:
                        exclusions[event["exclusion_reason"] or "UNEXPLAINED"] += 1
                    current_events[eid] = {"eligible": bool(event["eligible"]), "reason": event["exclusion_reason"], "observed_at": event["observed_at"]}
                    attempts = c.execute("SELECT * FROM book_attempts WHERE run_id=? AND event_id=?", (rid, eid)).fetchall()
                    if Counter(r["token_id"] for r in attempts) != Counter(tokens):
                        errors.append("BOOK_ATTEMPT_COVERAGE:" + rid + ":" + eid)
                    counts["expected_books"] += len(tokens); counts["attempt_rows"] += len(attempts)
                    for book in attempts:
                        observed = book["raw_gzip"] is not None
                        counts["observed_books"] += observed; book_status[book["status"]] += 1
                        counts["not_attempted"] += book["status"] == "NOT_ATTEMPTED"
                        try:
                            raw = raw_check(book["raw_gzip"], book["raw_sha256"])
                            if observed and not isinstance(raw, dict):
                                raise ValueError("book must be an object")
                            if isinstance(raw, dict):
                                for field, expected_value in (("asset_id", book["token_id"]), ("token_id", book["token_id"]), ("market", book["condition_id"]), ("condition_id", book["condition_id"])):
                                    if field in raw and str(raw[field]) != str(expected_value):
                                        errors.append("RAW_BOOK_IDENTITY:" + rid)
                                for side in ("bids", "asks"):
                                    counts["empty_" + side] += raw.get(side) == []
                                    counts["missing_" + side] += side not in raw
                                    counts["invalid_side_shape"] += side in raw and not isinstance(raw[side], list)
                        except (ValueError, OSError, EOFError, TypeError):
                            errors.append("RAW_BOOK_CONTENT:" + rid)
                        if not observed and str(book["status"]).upper() in ("OK", "SUCCESS", "SUCCEEDED", "200"):
                            errors.append("SUCCESS_WITHOUT_BOOK:" + rid)
            entry = {"config_hash": config, "strategy_source_digest": source, "cohort_key": key,
                     "cadence": cadence(members, statuses, start, end), "coverage": {k: counts[k] for k in ("events", "eligible_events", "expected_books", "attempt_rows", "observed_books", "not_attempted", "empty_bids", "empty_asks", "missing_bids", "missing_asks", "invalid_side_shape")},
                     "exclusions": dict(exclusions), "book_statuses": dict(book_status), "raw_checks": dict(raw_counts),
                     "census": dict(census), "current_events_within_scope": current_events,
                     "first_event_at": min(times, default=None), "last_event_at": max(times, default=None)}
            entry["coverage"]["book_observation_ratio"] = counts["observed_books"] / counts["expected_books"] if counts["expected_books"] else None
            cohorts.append(entry)
        timing = cadence(runs, statuses, start, end)
        if timing["duplicate_slots"] or timing["unobserved_slots_requested_range"] or any(v != "SUCCEEDED" for v in statuses.values()):
            warnings.append("MISSING_DUPLICATE_FAILED_OR_PENDING_RUN")
        if before != fingerprint(p):
            errors.append("SNAPSHOT_CHANGED")
        return {"path": str(p), "sha256": sha, "file_bytes": before[2], "runtime": job, "shard": RUNTIMES[job],
                "quick_check": check, "foreign_key_errors": fk, "cadence": timing, "cohorts": cohorts,
                "errors": errors, "warnings": warnings, "pin_created_at_not_source_cutoff": pin_at,
                "file_stat_unchanged": before == fingerprint(p)}
    finally:
        c.close()


def analyze(paths, start, end):
    start, end = utc(start), utc(end)
    if start >= end:
        raise ValueError("start must precede exclusive end")
    files, present, seen = [], {}, set()
    for path in paths:
        try:
            if str(path) in seen:
                raise ValueError("duplicate input snapshot")
            seen.add(str(path)); result = inspect_snapshot(path, start, end)
            if result["runtime"] in present:
                raise ValueError("supply only one snapshot per runtime")
            present[result["runtime"]] = result
            files.append(result)
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as error:
            files.append({"path": str(path), "errors": [str(error)]})
    required = [{"runtime": job, "shard": shard, "status": "MISSING" if job not in present else
                 ("NO_RUN_EVIDENCE" if not present[job]["cadence"]["started"] else "PRESENT")}
                for job, shard in RUNTIMES.items()]
    totals = Counter()
    for result in present.values():
        for cohort in result["cohorts"]:
            totals.update({k: v for k, v in cohort["coverage"].items() if k != "book_observation_ratio"})
    complete = all(r["status"] == "PRESENT" for r in required)
    integrity = bool(files) and not any(r["errors"] for r in files)
    checks = complete and integrity and not any(r.get("warnings") for r in files)
    evidence = totals["eligible_events"] > 0 and totals["expected_books"] > 0 and totals["observed_books"] == totals["expected_books"] and not any(totals[k] for k in ("empty_bids", "empty_asks", "missing_bids", "missing_asks", "not_attempted", "invalid_side_shape"))
    return {"schema": "guava-collection-health-v1", "start": start.isoformat(), "end_exclusive": end.isoformat(),
            "healthy": bool(checks and evidence), "integrity_pass": integrity, "checks_passed": checks,
            "verdict": "INCOMPLETE" if not complete else "INTEGRITY_FAILURE" if not integrity else "NO_ELIGIBLE_EVIDENCE" if not totals["eligible_events"] else "COLLECTION_GAPS" if not checks else "BOOK_GAPS" if not evidence else "PASS_OBSERVED_SCOPE_ONLY",
            "not_checked": ["features", "fee_completeness", "news_coverage", "executable_depth", "long_term_health"],
            "required_shards": required, "files": files, "totals": dict(totals),
            "storage": {"measured_input_file_bytes": sum(r.get("file_bytes", 0) for r in files), "growth_bytes": None},
            "limitations": ["Run-start half-open scope; post-end terminal is pending at end; raw checks include selected-run receipts.",
                            "Cohort requested-slot denominators overlap; never sum them across source changes.",
                            "No schedule provenance, long-term health, actual fills, P&L, zero-price imputation, or growth forecast."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", action="append", default=[])
    parser.add_argument("--start", required=True); parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output:
            out = args.output
            if out.suffix != ".json" or out.name == "manifest.json" or out.is_symlink():
                raise ValueError("output must be a JSON report, not a manifest/alias")
            if out.exists():
                with out.open("rb") as handle:
                    if handle.read(16) == b"SQLite format 3\0":
                        raise ValueError("cannot overwrite a SQLite database")
        report = analyze(args.db, args.start, args.end)
    except ValueError as error:
        parser.error(str(error))
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
