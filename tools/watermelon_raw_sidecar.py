#!/usr/bin/env python3
"""Read-only export of verified White parent/sidecar pins; no trading imports.

This exports missing and failed evidence too. evidence_valid is a provenance
check, not an entry signal, execution guarantee, or profitability verdict.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3

CONTRACT = "watermelon-independent-raw-lifecycle-v1"
APPLICATION_ID = 0x57525231


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def stamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.timestamp() if dt.tzinfo else None
    except (TypeError, ValueError):
        return None


def array(value):
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, list) else []


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def open_pin(path, expected_sha256):
    path = Path(path).resolve()
    if not expected_sha256 or sha256(path) != expected_sha256:
        raise ValueError("verified pin SHA mismatch")
    for suffix in ("-wal", "-journal"):
        companion = Path(str(path) + suffix)
        if companion.exists() and companion.stat().st_size:
            raise ValueError("pin must be a standalone SQLite snapshot")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        conn.close()
        raise ValueError("pin quick_check failed")
    return conn


def validate_contract(raw, parent_filename):
    if raw.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
        raise ValueError("not a White raw sidecar")
    if raw.execute("PRAGMA user_version").fetchone()[0] != 1:
        raise ValueError("unsupported sidecar schema")
    rows = raw.execute("SELECT * FROM raw_metadata").fetchall()
    if len(rows) != 1 or rows[0]["contract"] != CONTRACT or rows[0]["parent_filename"] != parent_filename:
        raise ValueError("sidecar contract/parent mismatch")
    schema = raw.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    digest = hashlib.sha256(json.dumps([tuple(r) for r in schema], separators=(",", ":")).encode()).hexdigest()
    if rows[0]["schema_sha256"] != digest:
        raise ValueError("sidecar schema fingerprint mismatch")


class Evidence:
    def __init__(self, parent, raw):
        self.parent, self.raw = parent, raw

    @lru_cache(maxsize=32)
    def payload(self, source_kind, ref_json, run_id):
        ref = json.loads(ref_json)
        if source_kind.startswith("PARENT_"):
            if ref.get("run_id") != run_id:
                raise ValueError("parent payload run mismatch")
            rows = self.parent.execute("SELECT * FROM raw_payloads WHERE run_id=? AND request_id=? AND payload_kind=? AND sha256=?",
                (run_id, ref.get("request_id"), ref.get("payload_kind"), ref.get("sha256"))).fetchall()
            time_key = "observed_at"
        else:
            rows = self.raw.execute("SELECT * FROM raw_payloads WHERE run_id=? AND payload_id=? AND sha256=?",
                (run_id, ref.get("payload_id"), ref.get("sha256"))).fetchall()
            time_key = "received_at"
        if len(rows) != 1:
            raise ValueError("missing or ambiguous raw payload reference")
        row = dict(rows[0])
        expected_kind = {"RAW_BOOK": "CLOB_BOOK_BATCH", "RAW_GAMMA_EVENT": "GAMMA_EVENT_FOLLOWUP"}.get(source_kind)
        if expected_kind is not None and row.get("kind") != expected_kind:
            raise ValueError("sidecar payload kind mismatch")
        body = gzip.decompress(row["payload_gzip"])
        if hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise ValueError("raw payload hash mismatch")
        return json.loads(body), row[time_key], row.get("request_id")

    def event(self, row):
        ref = json.loads(row["source_ref_json"])
        if row["source_kind"] not in {"PARENT_GAMMA_PAGE", "RAW_GAMMA_EVENT"}:
            raise ValueError("event source kind missing/unsupported")
        if row["source_kind"] == "PARENT_GAMMA_PAGE" and ref.get("payload_kind") != "GAMMA_EVENT_PAGE":
            raise ValueError("event payload kind mismatch")
        parsed, received, request = self.payload(row["source_kind"], row["source_ref_json"], row["run_id"])
        candidates = parsed if isinstance(parsed, list) else parsed.get("events", [parsed]) if isinstance(parsed, dict) else []
        matches = [x for x in candidates if isinstance(x, dict) and str(x.get("id")) == row["event_id"]]
        if len(matches) != 1 or matches[0] != json.loads(row["event_json"] or "null"):
            raise ValueError("raw event identity/content mismatch")
        if received != row["metadata_received_at"] or request != row["metadata_request_id"]:
            raise ValueError("raw event receipt mismatch")
        receipt = self.parent.execute("SELECT * FROM api_requests WHERE request_id=? AND run_id=?", (request, row["run_id"])).fetchone()
        ref = json.loads(row["source_ref_json"])
        request_start = stamp(receipt["started_at"]) if receipt else None
        if (receipt is None or receipt["status"] != "SUCCESS" or receipt["response_sha256"] != ref.get("sha256")
                or receipt["completed_at"] != received or request_start is None or stamp(received) is None or request_start > stamp(received)):
            raise ValueError("raw event HTTP receipt missing/mismatched")
        return matches[0], receipt["started_at"]

    def book(self, row):
        ref = json.loads(row["source_ref_json"])
        if row["source_kind"] not in {"PARENT_BOOK", "RAW_BOOK"} or ref.get("payload_kind") != "CLOB_BOOK_BATCH":
            raise ValueError("book source kind mismatch")
        parsed, received, request = self.payload(row["source_kind"], row["source_ref_json"], row["run_id"])
        if not isinstance(parsed, list) or any(not isinstance(x, dict) for x in parsed):
            raise ValueError("book batch shape mismatch")
        tokens = [str(x.get("asset_id") or "") for x in parsed]
        if len(set(tokens)) != len(tokens):
            raise ValueError("duplicate token in book batch")
        matches = [x for x in parsed if str(x.get("asset_id")) == row["token_id"]]
        if len(matches) != 1:
            raise ValueError("book token missing from raw batch")
        book = matches[0]
        digest = hashlib.sha256(canonical(book).encode()).hexdigest()
        if digest != row["book_sha256"] or received != row["received_at"] or request != row["request_id"]:
            raise ValueError("book hash/receipt mismatch")
        if book.get("market") is not None and str(book["market"]) != row["condition_id"]:
            raise ValueError("book condition mismatch")
        for side in ("asks", "bids"):
            if not isinstance(book.get(side), list):
                raise ValueError("book side missing")
            for level in book[side]:
                if not isinstance(level, dict):
                    raise ValueError("invalid book level")
                for key in ("price", "size"):
                    v = level.get(key)
                    if isinstance(v, bool) or not math.isfinite(float(v)) or float(v) <= 0 or (key == "price" and float(v) > 1):
                        raise ValueError("invalid book level value")
        status = ("EMPTY_BOOK" if not book["bids"] and not book["asks"] else "EMPTY_BIDS" if not book["bids"] else
                  "EMPTY_ASKS" if not book["asks"] else "FULL")
        if status != row["status"]:
            raise ValueError("book status disagrees with raw sides")
        ref = json.loads(row["source_ref_json"])
        if row["source_kind"] == "PARENT_BOOK" and ref.get("snapshot_id"):
            snap = self.parent.execute("SELECT * FROM orderbook_snapshots WHERE snapshot_id=?", (ref["snapshot_id"],)).fetchone()
            if snap is None or any(snap[k] != row[v] for k, v in (
                    ("run_id", "run_id"), ("token_id", "token_id"), ("request_id", "request_id"),
                    ("observed_at", "received_at"), ("raw_book_sha256", "book_sha256"))):
                raise ValueError("parent snapshot reference mismatch")
        requests = self.parent.execute("SELECT * FROM api_requests WHERE request_id=? AND run_id=?", (request, row["run_id"])).fetchall()
        if len(requests) != 1 or requests[0]["status"] != "SUCCESS" or requests[0]["completed_at"] != received or requests[0]["started_at"] != row["requested_at"] or requests[0]["response_sha256"] != ref.get("sha256"):
            raise ValueError("request start/hash evidence mismatch")
        return book


def iter_rows(parent, raw, start, end):
    """Export every expected token row; invalid evidence never becomes a signal."""
    lower, upper = stamp(start), stamp(end)
    if lower is None or upper is None or lower >= upper:
        raise ValueError("explicit UTC half-open range required")
    evidence = Evidence(parent, raw)
    for raw_cycle in raw.execute("SELECT * FROM raw_cycles ORDER BY reference_at,run_id"):
        cycle = dict(raw_cycle)
        ref_at = stamp(cycle["reference_at"])
        if ref_at is None or not lower <= ref_at < upper:
            continue
        published = stamp(cycle["published_at"])
        issues = []
        history = [dict(r) for r in parent.execute("SELECT * FROM research_run_events WHERE run_id=? ORDER BY observed_at,event_id", (cycle["run_id"],))]
        starts = [r for r in history if r["event_type"] == "STARTED"]
        successes = [r for r in history if r["event_type"] == "SUCCEEDED"]
        failed = any(r["event_type"] == "FAILED" for r in history)
        start_at = stamp(starts[0]["observed_at"]) if len(starts) == 1 else None
        finish_at = stamp(successes[0]["observed_at"]) if len(successes) == 1 else None
        parent_valid = (not failed and start_at is not None and finish_at is not None and published is not None
                        and start_at <= ref_at <= published <= finish_at < upper
                        and all(r["config_hash"] == cycle["config_hash"] and r["strategy_source_digest"] == cycle["source_digest"] for r in history))
        if not parent_valid:
            issues.append("PARENT_RUN_NOT_VERIFIED_SUCCESS")
        cfg = parent.execute("SELECT * FROM research_config_versions WHERE config_hash=?", (cycle["config_hash"],)).fetchone()
        if (cfg is None or cfg["strategy_source_digest"] != cycle["source_digest"] or cfg["job_name"] != cycle["job_name"]
                or cfg["mode"] != "sim" or cycle["contract"] != CONTRACT
                or cycle["parent_filename"] != raw.execute("SELECT parent_filename FROM raw_metadata").fetchone()[0]):
            issues.append("PARENT_CONFIG_OR_RAW_CONTRACT_MISMATCH")
        if cycle["status"] != "PUBLISHED" or published is None or published >= upper:
            issues.append("RAW_CYCLE_NOT_PUBLISHED_BY_CUTOFF")
        events = [dict(r) for r in raw.execute("SELECT * FROM raw_events WHERE run_id=? ORDER BY event_id", (cycle["run_id"],))]
        books = [dict(r) for r in raw.execute("SELECT * FROM raw_books WHERE run_id=? ORDER BY event_id,slot", (cycle["run_id"],))]
        if len(events) != cycle["expected_events"] or len(books) != cycle["expected_tokens"]:
            raise ValueError("atomic cycle expected/observed count mismatch")
        event_map = {r["event_id"]: r for r in events}
        for row in books:
            errors = list(issues)
            event_row = event_map.get(row["event_id"])
            if event_row is None:
                raise ValueError("book has no same-cycle raw event")
            event = None
            book = None
            metadata_request_started = None
            try:
                event, metadata_request_started = evidence.event(event_row)
            except (ValueError, TypeError, KeyError, OSError) as error:
                errors.append("EVENT_EVIDENCE: " + str(error))
            if row["source_kind"] != "NONE":
                try:
                    book = evidence.book(row)
                except (ValueError, TypeError, KeyError, OSError) as error:
                    errors.append("BOOK_EVIDENCE: " + str(error))
            if event_row["metadata_status"] != "OBSERVED" or not event_row["identity_valid"] or not row["point_in_time_identity_valid"]:
                errors.append("POINT_IN_TIME_IDENTITY_UNPROVEN")
            ms, mt, qt, rt = (stamp(metadata_request_started), stamp(event_row["metadata_received_at"]), stamp(row["requested_at"]), stamp(row["received_at"]))
            if any(t is None for t in (start_at, ms, mt, qt, rt, published)) or not start_at <= ms <= mt <= qt <= rt <= published:
                errors.append("RECEIPT_ORDER_UNPROVEN")
            slots = json.loads(event_row["slots_json"])
            matches = [s for s in slots if s.get("slot") == row["slot"] and s.get("condition_id") == row["condition_id"]
                       and s.get("token_id") == row["token_id"] and s.get("outcome") == row["outcome"]]
            if len(matches) != 1:
                errors.append("SLOT_IDENTITY_MISMATCH")
            expected = 3 if event_row["family"] == "soccer" else 2
            expected_names = {"HOME", "DRAW", "AWAY"} if expected == 3 else {"OUTCOME_0", "OUTCOME_1"}
            family_valid = event_row["family"] in {"soccer", "mlb", "nba", "nfl", "nhl"}
            observed_names = [b["slot"] for b in books if b["event_id"] == row["event_id"]]
            if (not family_valid or len(slots) != expected or {s.get("slot") for s in slots} != expected_names
                    or len({s.get("token_id") for s in slots}) != expected or any(not s.get("token_id") for s in slots)
                    or len({s.get("condition_id") for s in slots}) != (3 if expected == 3 else 1)
                    or any(not s.get("condition_id") for s in slots) or len(observed_names) != expected
                    or set(observed_names) != expected_names
                    or (expected == 3 and any(str(s.get("outcome")).casefold() != "yes" for s in slots))):
                errors.append("EXPECTED_SET_INVALID")
            market = None
            if event is not None:
                markets = [m for m in event.get("markets", []) if isinstance(m, dict) and str(m.get("conditionId") or m.get("condition_id")) == row["condition_id"]]
                if len(markets) == 1:
                    market = markets[0]
            if market is None:
                errors.append("CURRENT_MARKET_MISSING")
            else:
                try:
                    tokens, labels = array(market.get("clobTokenIds")), array(market.get("outcomes"))
                    slot = matches[0] if len(matches) == 1 else {}
                    if (len(tokens) != 2 or len(labels) != 2 or len(set(tokens)) != 2
                            or dict(zip(tokens, labels)) != dict(zip(slot.get("all_tokens", []), slot.get("all_outcomes", [])))
                            or dict(zip(tokens, labels)).get(row["token_id"]) != row["outcome"]):
                        errors.append("CURRENT_TOKEN_LABEL_MAPPING_MISMATCH")
                except (TypeError, ValueError):
                    errors.append("CURRENT_TOKEN_LABEL_MAPPING_MALFORMED")
            if row["status"] not in {"FULL", "EMPTY_BIDS", "EMPTY_ASKS", "EMPTY_BOOK"} or book is None:
                errors.append("BOOK_NOT_OBSERVED")
            yield {**row, "contract": CONTRACT, "job_name": cycle["job_name"], "config_hash": cycle["config_hash"],
                   "source_digest": cycle["source_digest"], "sport_family": event_row["family"],
                   "reference_at": cycle["reference_at"], "published_at": cycle["published_at"],
                   "metadata_received_at": event_row["metadata_received_at"], "metadata_request_started_at": metadata_request_started,
                   "metadata_status": event_row["metadata_status"], "producer_lifecycle_state": event_row["lifecycle_state"],
                   "event": event, "market": market, "book": book,
                   "unverified_terminal_annotation_json": event_row["terminal_json"], "terminal_evidence_verified": False,
                   "evidence_valid": not errors, "evidence_errors": errors,
                   "market_open_observed": bool(market and market.get("active") is True and market.get("closed") is False and market.get("acceptingOrders") is True),
                   "event_inplay_observed": bool(event and event.get("live") is True and event.get("ended") is False)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("sidecar", "sidecar-sha256", "parent", "parent-sha256", "start", "end", "output"):
        p.add_argument("--" + name, required=True)
    args = p.parse_args(argv)
    output = Path(args.output).resolve()
    inputs = {Path(args.parent).resolve(), Path(args.sidecar).resolve()}
    if len(inputs) != 2 or output in inputs or output.with_suffix(".audit.json") in inputs:
        raise ValueError("parent, sidecar and output paths must be distinct")
    if stamp(args.start) is None or stamp(args.end) is None or stamp(args.start) >= stamp(args.end):
        raise ValueError("explicit UTC half-open range required")
    output.parent.mkdir(parents=True, exist_ok=True)
    counts, errors, families = Counter(), Counter(), Counter()
    with closing(open_pin(args.parent, args.parent_sha256)) as parent, closing(open_pin(args.sidecar, args.sidecar_sha256)) as raw:
        if parent.execute("PRAGMA application_id").fetchone()[0] != 1196903732:
            raise ValueError("parent is not a White primary database")
        validate_contract(raw, Path(args.parent).name)
        cycle_counts = Counter()
        for cycle in raw.execute("SELECT * FROM raw_cycles"):
            if stamp(args.start) <= stamp(cycle["reference_at"]) < stamp(args.end):
                cycle_counts[cycle["status"]] += 1
                cycle_counts["expected_events"] += cycle["expected_events"]
                cycle_counts["expected_tokens"] += cycle["expected_tokens"]
        with gzip.open(output, "wt", encoding="utf-8") as handle:
            for row in iter_rows(parent, raw, args.start, args.end):
                counts[row["status"]] += 1
                counts["all_rows"] += 1
                counts["evidence_valid_rows"] += int(row["evidence_valid"])
                families[row["sport_family"]] += 1
                errors.update(row["evidence_errors"])
                handle.write(canonical(row) + "\n")
    report = {"contract": CONTRACT, "parent_sha256": args.parent_sha256, "sidecar_sha256": args.sidecar_sha256,
              "range": [args.start, args.end], "counts": dict(counts), "cycle_counts": dict(cycle_counts), "families": dict(families),
              "evidence_errors": dict(errors), "output_sha256": sha256(output), "actual_fills_or_pnl": False}
    output.with_suffix(".audit.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
