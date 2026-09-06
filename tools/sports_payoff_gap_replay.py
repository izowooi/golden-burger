#!/usr/bin/env python3
"""H1 conditional_payoff_reference: direct NO ask versus two other YES bids.

Usage: python tools/sports_payoff_gap_replay.py --db /verified/pin.db \
  --start 2026-09-01T00:00:00Z --end 2026-09-06T00:00:00Z --output /new/report.json

Writes report.json and report.rows.csv (neither may already exist). No network,
signer, order SDK, source DB writes, synthetic NO, interpolation or realized P&L.
Inputs must be frozen standalone SQLite files, not active WAL databases. Hashes
and file identities are checked before/after; immutable=1 is not itself a pin.

Current Peach/Grey and Plum/Silver snapshots lack immutable per-book reception
and point-in-time fee receipts. Therefore all numeric gaps are observational,
not authenticated simultaneous executable arbitrage or live-promotion evidence.
Fee stress charges the specified rate*shares*price*(1-price) at EACH consumed
level on all three legs; 0/.03/.05 are assumptions, never observed fees.
NO_HOME = YES_DRAW + YES_AWAY holds only for a normal exhaustive regular-time
result. On a .5/.5 void, NO pays .5 but the two YES may pay 1 in total. Without
exact settlement/NegRisk converter authority this is NOT risk-free or proven
equal payoff. Retain void events, never remove them after observing the outcome.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any


UTC = timezone.utc
KINDS = ("HOME", "DRAW", "AWAY")
SIDES = ("YES", "NO")
RUNGS = (5, 10, 25, 50, 100)
HORIZONS = (1, 3, 5, 10)
FUTURE_TOLERANCE_SECONDS = 150
THRESHOLDS = ("0", "0.005", "0.01", "0.02", "0.03")
STRESS_RATES = ("0", "0.03", "0.05")
HASH = re.compile(r"[0-9a-f]{64}\Z")
REQUIRED_SNAPSHOT = {
    "id", "condition_id", "event_id", "token_id", "outcome", "outcome_side",
    "result_kind", "timestamp", "book_json", "run_id",
}
OPTIONAL_SNAPSHOT = {
    "config_hash", "strategy_source_digest", "sport_family", "sport_profile_version",
    "book_shape", "event_set_complete", "event_set_reason", "event_cycle_id",
}


class EvidenceError(ValueError):
    """Evidence is invalid, incomplete or cannot be frozen safely."""


def utc(value: str, *, sqlite_value: bool = False) -> datetime:
    text = str(value or "")
    if not sqlite_value and not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", text
    ):
        raise EvidenceError("CLI timestamps require absolute UTC seconds (Z or +00:00)")
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceError("invalid timestamp") from error
    if result.tzinfo is None:
        if not sqlite_value:
            raise EvidenceError("timezone is required")
        # These two SQLAlchemy schemas store datetime.utcnow() without tzinfo.
        result = result.replace(tzinfo=UTC)
    if result.utcoffset() != timedelta(0):
        raise EvidenceError("non-UTC timestamp")
    return result.astimezone(UTC)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def decoded(value: Any) -> Any:
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise EvidenceError("duplicate JSON key")
            result[key] = item
        return result
    try:
        return json.loads(value, object_pairs_hook=unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(
            EvidenceError("non-finite JSON number")
        ))
    except (TypeError, ValueError) as error:
        raise EvidenceError("invalid JSON evidence") from error


def number(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise EvidenceError("invalid numeric evidence")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise EvidenceError("invalid numeric evidence") from error
    if not result.is_finite():
        raise EvidenceError("non-finite numeric evidence")
    return result


def digest_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def file_identity(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise EvidenceError("source must be a regular file")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def assert_standalone(path: Path) -> None:
    for suffix in ("-wal", "-journal"):
        companion = Path(str(path) + suffix)
        if companion.exists() and companion.stat().st_size:
            raise EvidenceError("source has WAL/journal; provide a verified standalone pin")


@dataclass(frozen=True)
class Book:
    token: str
    condition: str
    asks: tuple[tuple[Decimal, Decimal], ...]
    bids: tuple[tuple[Decimal, Decimal], ...]


def parse_book(row: dict) -> Book:
    payload = decoded(row["book_json"])
    if not isinstance(payload, dict) or payload.get("token_id") != row["token_id"]:
        raise EvidenceError("book_token_identity_mismatch")

    def levels(side: str) -> tuple:
        raw = payload.get(side)
        if not isinstance(raw, list):
            raise EvidenceError("book_levels_missing")
        result = []
        seen = set()
        for item in raw:
            if not isinstance(item, dict):
                raise EvidenceError("book_level_invalid")
            price, size = number(item.get("price")), number(item.get("size"))
            if not 0 < price < 1 or size <= 0:
                raise EvidenceError("book_price_or_size_out_of_domain")
            if price in seen:
                raise EvidenceError("duplicate_book_price_level")
            seen.add(price)
            result.append((price, size))
        return tuple(sorted(result, reverse=side == "bids"))

    asks, bids = levels("asks"), levels("bids")
    if asks and bids and bids[0][0] > asks[0][0]:
        raise EvidenceError("crossed_book")
    return Book(row["token_id"], row["condition_id"], asks, bids)


@dataclass(frozen=True)
class Walk:
    shares: Decimal
    amount: Decimal
    fee_base: Decimal  # Sum(q_i * p_i * (1-p_i)), exponent exactly one.


def walk_asks(book: Book, notional: Decimal) -> Walk:
    remaining, shares, fee_base = notional, Decimal(0), Decimal(0)
    for price, available in book.asks:
        spent = min(remaining, price * available)
        taken = spent / price
        shares += taken
        fee_base += taken * price * (1 - price)
        remaining -= spent
        if remaining == 0:
            return Walk(shares, notional, fee_base)
    raise EvidenceError("insufficient_no_ask_depth")


def walk_bids(book: Book, shares: Decimal) -> Walk:
    remaining, proceeds, fee_base = shares, Decimal(0), Decimal(0)
    for price, available in book.bids:
        taken = min(remaining, available)
        proceeds += taken * price
        fee_base += taken * price * (1 - price)
        remaining -= taken
        if remaining == 0:
            return Walk(shares, proceeds, fee_base)
    raise EvidenceError("insufficient_bid_depth")


@dataclass
class Group:
    cohort: dict
    event: str
    run: str
    time: datetime
    rows: list[dict]
    issues: set[str] = field(default_factory=set)
    metadata_valid: bool = False
    books: dict[tuple[str, str], Book] = field(default_factory=dict)
    leg_issues: dict[tuple[str, str], str] = field(default_factory=dict)
    complete: bool = False

    @property
    def key(self) -> str:
        return json.dumps(self.cohort, sort_keys=True, separators=(",", ":"))


def columns(connection, table: str, required: set[str]) -> set[str]:
    observed = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    if not required <= observed:
        raise EvidenceError(f"unsupported {table} schema; missing {sorted(required-observed)}")
    return observed


def validate_group(group: Group, catalogs: dict, start: datetime, end: datetime) -> None:
    rows, first = group.rows, group.rows[0]
    try:
        config = decoded(first["cfg_json"])
        trading = config["trading"]
        source = trading["strategy_source_digest"]
        shape = trading["book_shape"]
        profile = trading["sport_profile_version"]
        if (
            not HASH.fullmatch(str(first["run_config"] or ""))
            or not HASH.fullmatch(str(source))
            or hashlib.sha256(first["cfg_json"].encode()).hexdigest() != first["run_config"]
            or first["run_config"] != first["cfg_hash"]
            or first["strategy"] not in {"golden-peach", "golden-plum"}
            or config.get("strategy_name") != first["strategy"]
            or first["cfg_strategy"] != first["strategy"]
            or config.get("mode") != first["run_mode"]
            or first["cfg_mode"] != first["run_mode"]
            or first["run_mode"] not in {"sim", "live"}
            or not first["runtime"]
            or not isinstance(profile, str) or not profile
        ):
            raise EvidenceError("source_config_runtime_identity_unverified")
        group.cohort.update(strategy_source_digest=source, sport_family=trading.get("sport_family"),
                            sport_profile_version=profile, book_shape=shape)
        if trading.get("sport_family") != "soccer" or shape != "direct-six-result-books":
            raise EvidenceError("unsupported_non_soccer_or_non_six_book_cohort")
        if first["run_status"] != "SUCCESS":
            raise EvidenceError("run_not_successful")
        run_start = utc(first["run_start"], sqlite_value=True)
        run_end = utc(first["run_end"], sqlite_value=True)
        if run_end < run_start or run_end > end:
            raise EvidenceError("run_completion_outside_review_evidence")
        for row in rows:
            time = utc(row["timestamp"], sqlite_value=True)
            if not start <= time < end or not run_start <= time <= run_end:
                raise EvidenceError("snapshot_outside_run_or_review_range")
            for column, expected in (
                ("config_hash", first["run_config"]), ("strategy_source_digest", source),
                ("sport_family", "soccer"), ("sport_profile_version", profile), ("book_shape", shape),
            ):
                if column in row and row[column] != expected:
                    raise EvidenceError("snapshot_cohort_metadata_mismatch")
        group.metadata_valid = True
    except (EvidenceError, KeyError, TypeError, AttributeError) as error:
        group.issues.add(str(error) if isinstance(error, EvidenceError) else "source_metadata_missing")

    slots = defaultdict(list)
    for row in rows:
        slots[(row["result_kind"], row["outcome_side"])].append(row)
    expected = {(kind, side) for kind in KINDS for side in SIDES}
    if len(rows) != 6 or set(slots) != expected or any(len(v) != 1 for v in slots.values()):
        group.issues.add("incomplete_or_duplicate_six_book_set")
    if not group.event or len({r["token_id"] for r in rows}) != len(rows):
        group.issues.add("event_or_duplicate_token_identity_invalid")
    conditions = {}
    for slot, members in slots.items():
        if slot not in expected or len(members) != 1:
            continue
        row = members[0]
        try:
            if row["outcome"] != slot[1].title():
                raise EvidenceError("snapshot_outcome_label_mismatch")
            catalog = catalogs.get(row["condition_id"])
            if catalog is None or catalog["event_id"] != group.event:
                raise EvidenceError("catalog_condition_event_mismatch")
            labels, tokens = decoded(catalog["outcomes_json"]), decoded(catalog["token_ids_json"])
            if (not isinstance(labels, list) or not isinstance(tokens, list)
                    or len(tokens) != 2 or len(set(map(str, tokens))) != 2
                    or sorted(labels) != ["No", "Yes"]
                    or any(not isinstance(t, str) or not t for t in tokens)):
                raise EvidenceError("catalog_binary_alignment_invalid")
            if tokens[labels.index(row["outcome"])] != row["token_id"]:
                raise EvidenceError("catalog_token_outcome_mismatch")
            if row["condition_id"] in conditions and conditions[row["condition_id"]] != slot[0]:
                raise EvidenceError("condition_assigned_to_multiple_results")
            conditions[row["condition_id"]] = slot[0]
            group.books[slot] = parse_book(row)
        except (EvidenceError, TypeError, ValueError) as error:
            group.leg_issues[slot] = str(error)
            group.issues.add("invalid_leg_identity_or_book")
    for kind in KINDS:
        yes, no = group.books.get((kind, "YES")), group.books.get((kind, "NO"))
        if yes and no and yes.condition != no.condition:
            group.issues.add("yes_no_different_conditions")
    if len(conditions) != 3 and len(rows) == 6:
        group.issues.add("triad_requires_three_distinct_conditions")
    if any("event_set_complete" in r and r["event_set_complete"] != 1 for r in rows):
        group.issues.add("collector_event_set_not_complete")
    group.complete = group.metadata_valid and not group.issues and set(group.books) == expected


def load_groups(connection, source_sha: str, start: datetime, end: datetime) -> list[Group]:
    available = columns(connection, "market_snapshots", REQUIRED_SNAPSHOT)
    columns(connection, "run_audits", {
        "run_id", "strategy_name", "job_name", "mode", "config_hash", "started_at", "finished_at", "status",
    })
    columns(connection, "strategy_configs", {"config_hash", "strategy_name", "mode", "config_json"})
    columns(connection, "market_catalog", {"condition_id", "event_id", "outcomes_json", "token_ids_json"})
    fields = sorted(REQUIRED_SNAPSHOT | (OPTIONAL_SNAPSHOT & available))
    # Broad index-friendly day fence, then exact microsecond UTC filtering below.
    # This accepts both SQLAlchemy's ' ' separator and ISO 'T...Z' storage.
    lower = start.date().isoformat()
    upper = (end.date() + timedelta(days=1)).isoformat()
    query = "SELECT " + ",".join(f's."{name}"' for name in fields) + """,
        r.strategy_name AS strategy,r.job_name AS runtime,r.mode AS run_mode,
        r.config_hash AS run_config,r.started_at AS run_start,r.finished_at AS run_end,
        r.status AS run_status,c.config_hash AS cfg_hash,c.strategy_name AS cfg_strategy,
        c.mode AS cfg_mode,c.config_json AS cfg_json
        FROM market_snapshots s LEFT JOIN run_audits r ON r.run_id=s.run_id
        LEFT JOIN strategy_configs c ON c.config_hash=r.config_hash
        WHERE s.timestamp >= ? AND s.timestamp < ? ORDER BY s.timestamp,s.id
    """
    buckets = defaultdict(list)
    for raw in connection.execute(query, (lower, upper)):
        row = dict(raw)
        utc(row["timestamp"], sqlite_value=True)  # Malformed time cannot disappear silently.
        buckets[(row["event_id"], row["run_id"])].append(row)
    catalogs, groups = {}, []
    for (event, run), rows in buckets.items():
        times = [utc(row["timestamp"], sqlite_value=True) for row in rows]
        if not any(start <= time < end for time in times):
            continue
        first = rows[0]
        cohort = {
            "source_sha256": source_sha, "strategy": first["strategy"],
            "runtime_job": first["runtime"], "mode": first["run_mode"],
            "config_hash": first["run_config"], "strategy_source_digest": None,
            "sport_family": None, "sport_profile_version": None, "book_shape": None,
        }
        for condition in {row["condition_id"] for row in rows}:
            if condition not in catalogs:
                matches = connection.execute(
                    "SELECT condition_id,event_id,outcomes_json,token_ids_json "
                    "FROM market_catalog WHERE condition_id=?", (condition,),
                ).fetchall()
                if len(matches) > 1:
                    raise EvidenceError("duplicate catalog condition identity")
                catalogs[condition] = dict(matches[0]) if matches else None
        group = Group(cohort, event, run, max(times), rows)
        validate_group(group, catalogs, start, end)
        groups.append(group)
    return groups


def forward_observation(anchor: Group, future: Group | None, kind: str, q: Decimal,
                        initial_ask: Decimal, initial_basket: Decimal | None,
                        initial_no_bid: Decimal | None, target: datetime, end: datetime) -> dict:
    result = {"target_utc": iso(target), "observed_utc": None, "run_id": None,
              "no_status": "missing_observation", "basket_status": "missing_observation",
              "no_bid_vwap": None, "no_bid_change_per_share": None,
              "no_bid_minus_entry_ask": None, "no_markout_gross_usdc": None,
              "basket_bid_per_share": None, "basket_bid_change_per_share": None,
              "basket_decline_per_share": None}
    if target >= end:
        result.update(no_status="outside_review_range", basket_status="outside_review_range")
        return result
    if future is None:
        return result
    result.update(observed_utc=iso(future.time), run_id=future.run,
                  delay_seconds=(future.time-target).total_seconds())
    if (not future.metadata_valid or future.key != anchor.key or future.event != anchor.event
            or future.run == anchor.run):
        result.update(no_status="future_metadata_invalid", basket_status="future_metadata_invalid")
        return result

    def same_leg(side, result_kind):
        old = anchor.books[(result_kind, side)]
        new = future.books.get((result_kind, side))
        if new is None or (old.token, old.condition) != (new.token, new.condition):
            raise EvidenceError("future_leg_identity_missing_or_changed")
        return walk_bids(new, q)

    try:
        no = same_leg("NO", kind)
        value = no.amount / q
        result.update(no_status="observed_displayed_bid", no_bid_vwap=float(value),
                      no_bid_change_per_share=float(value-initial_no_bid) if initial_no_bid is not None else None,
                      no_bid_minus_entry_ask=float(value-initial_ask),
                      no_markout_gross_usdc=float(no.amount-q*initial_ask))
    except EvidenceError as error:
        result["no_status"] = str(error)
    try:
        basket = sum((same_leg("YES", other).amount for other in KINDS if other != kind), Decimal(0)) / q
        result.update(basket_status="observed_displayed_bid", basket_bid_per_share=float(basket),
                      basket_bid_change_per_share=float(basket-initial_basket) if initial_basket is not None else None,
                      basket_decline_per_share=float(initial_basket-basket) if initial_basket is not None else None)
    except EvidenceError as error:
        result["basket_status"] = str(error)
    return result


def cases_for_group(group: Group, timeline: list[Group], times: list[datetime], end: datetime) -> list[dict]:
    rows = []
    for kind in KINDS:
        for rung in RUNGS:
            no = group.books.get((kind, "NO"))
            row = {
                **group.cohort, "event_id": group.event, "run_id": group.run,
                "observed_utc": iso(group.time), "result_kind": kind,
                "no_token_id": no.token if no else None, "notional_usdc": rung,
                "snapshot_span_seconds": (max(utc(r["timestamp"], sqlite_value=True) for r in group.rows)
                                          - min(utc(r["timestamp"], sqlite_value=True) for r in group.rows)).total_seconds(),
                "complete_six": group.complete, "status": "incomplete_or_invalid_six_book_set",
                "issues": sorted(group.issues), "leg_issues": {"/".join(k): v for k, v in group.leg_issues.items()},
                "simultaneity_evidence": "unverified_same_run_labels_not_raw_book_receipts",
                "fee_evidence": "point_in_time_raw_fee_receipts_missing",
                "payoff_scope_evidence": "collector_result_labels_and_unsigned_catalog_not_independent_settlement_proof",
                "payoff_reference_type": "conditional_payoff_reference",
                "promotion_ready": False, "live_promotion_allowed": False,
                "q_shares": None, "no_ask_cost": None,
                "no_ask_vwap": None, "basket_bid_proceeds": None, "gross_gap_per_share": None,
                "fee_stress": {}, "forward": {},
            }
            if group.complete:
                buy = None
                basket_per_share = None
                try:
                    buy = walk_asks(no, Decimal(rung))
                    row.update(q_shares=float(buy.shares), no_ask_cost=float(buy.amount),
                               no_ask_vwap=float(buy.amount/buy.shares))
                    other_walks = []
                    for other in KINDS:
                        if other != kind:
                            try:
                                other_walks.append(walk_bids(group.books[(other, "YES")], buy.shares))
                            except EvidenceError as error:
                                raise EvidenceError(f"insufficient_other_yes_bid_depth:{other}") from error
                    proceeds = sum((walk.amount for walk in other_walks), Decimal(0))
                    basket_per_share = proceeds/buy.shares
                    # Only suppress arithmetic noise well below the reported
                    # thresholds, never classify a Decimal division residue as an edge.
                    gap = ((proceeds-buy.amount)/buy.shares).quantize(Decimal("0.000000000001"))
                    fee_base = buy.fee_base + sum((walk.fee_base for walk in other_walks), Decimal(0))
                    row.update(status="observational_gross_only", basket_bid_proceeds=float(proceeds),
                               gross_gap_per_share=float(gap),
                               fee_stress={rate: {"assumed_rate": float(rate), "exponent": 1,
                                   "assumed_three_leg_fees_usdc": float(fee_base*Decimal(rate)),
                                   "gap_per_share_after_stress": float(gap-fee_base*Decimal(rate)/buy.shares)}
                                   for rate in STRESS_RATES})
                except EvidenceError as error:
                    row["status"] = str(error)
                # A missing basket quote does not erase the independently
                # observed NO path; its basket change stays explicitly null.
                if buy is not None:
                    try:
                        initial_no_bid = walk_bids(no, buy.shares).amount / buy.shares
                    except EvidenceError:
                        initial_no_bid = None
                    for horizon in HORIZONS:
                        target = group.time + timedelta(minutes=horizon)
                        index = bisect_left(times, target)
                        future = timeline[index] if index < len(timeline) else None
                        if future and (future.time >= end or (future.time-target).total_seconds() > FUTURE_TOLERANCE_SECONDS):
                            future = None
                        row["forward"][str(horizon)] = forward_observation(
                            group, future, kind, buy.shares, buy.amount/buy.shares,
                            basket_per_share, initial_no_bid, target, end,
                        )
            rows.append(row)
    return rows


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    def quantile(fraction):
        if not ordered:
            return None
        index = (len(ordered)-1)*fraction
        left = int(index)
        right = min(left+1, len(ordered)-1)
        return ordered[left]+(ordered[right]-ordered[left])*(index-left)
    return {"n": len(values), "min": min(values) if values else None,
            "p50": quantile(.5), "p90": quantile(.9), "p95": quantile(.95),
            "p99": quantile(.99), "max": max(values) if values else None}


def summaries(groups: list[Group], rows: list[dict]) -> list[dict]:
    by_cohort, case_cohorts = defaultdict(list), defaultdict(list)
    for group in groups:
        by_cohort[group.key].append(group)
    for row in rows:
        key = json.dumps({k: row[k] for k in groups[0].cohort}, sort_keys=True, separators=(",", ":"))
        case_cohorts[key].append(row)
    result = []
    for key, members in sorted(by_cohort.items()):
        cohort_rows = case_cohorts[key]
        events = sorted({group.event for group in members}, key=str)
        rungs = {}
        for rung in RUNGS:
            measured = [row for row in cohort_rows if row["notional_usdc"] == rung
                        and row["gross_gap_per_share"] is not None]
            peaks = defaultdict(list)
            for row in measured:
                peaks[row["event_id"]].append(row["gross_gap_per_share"])
            rungs[str(rung)] = {
                "event_denominator_all_observed": len(events),
                "event_denominator_measurable": len(peaks),
                "case_count": len(measured), "gap_distribution_observations": distribution([r["gross_gap_per_share"] for r in measured]),
                "event_max_gap_distribution": distribution([max(v) for v in peaks.values()]),
                "threshold_events_strictly_greater": {threshold: sum(max(v) > float(threshold) for v in peaks.values()) for threshold in THRESHOLDS},
                "authenticated_simultaneous_executable_events": 0,
                "fee_stress": {},
            }
            for rate in STRESS_RATES:
                stress_peaks = defaultdict(list)
                for row in measured:
                    stress_peaks[row["event_id"]].append(row["fee_stress"][rate]["gap_per_share_after_stress"])
                rungs[str(rung)]["fee_stress"][rate] = {
                    "semantics": "assumed_three_leg_fee_not_observed_net_profit",
                    "event_max_gap_distribution": distribution([max(v) for v in stress_peaks.values()]),
                    "threshold_events_strictly_greater": {
                        threshold: sum(max(v) > float(threshold) for v in stress_peaks.values()) for threshold in THRESHOLDS
                    },
                }
        complete = sum(group.complete for group in members)
        result.append({"cohort": json.loads(key), "event_count": len(events),
            "event_run_count": len(members), "complete_six_event_runs": complete,
            "complete_six_rate": complete/len(members), "rungs": rungs,
            "exclusions": dict(Counter(issue for g in members for issue in g.issues)),
            "case_status_counts": dict(Counter(row["status"] for row in cohort_rows)),
            "events": [{"event_id": event, "event_run_count": sum(g.event == event for g in members),
                        "complete_six_event_runs": sum(g.event == event and g.complete for g in members),
                        "case_count": sum(r["event_id"] == event for r in cohort_rows),
                        "measurable_case_count": sum(r["event_id"] == event and r["gross_gap_per_share"] is not None for r in cohort_rows),
                        "max_gap_by_rung": {str(rung): max((r["gross_gap_per_share"] for r in cohort_rows
                            if r["event_id"] == event and r["notional_usdc"] == rung and r["gross_gap_per_share"] is not None), default=None)
                            for rung in RUNGS}} for event in events]})
    return result


def analyze_sources(paths: list[Path], start: datetime, end: datetime) -> tuple[dict, list[dict]]:
    if start.tzinfo != UTC or end.tzinfo != UTC or start >= end:
        raise EvidenceError("review range must be UTC [start,end), start < end")
    if not paths:
        raise EvidenceError("at least one source is required")
    sources, seen, all_groups = [], {}, []
    for supplied in paths:
        supplied = Path(supplied)
        if not supplied.is_absolute() or supplied.is_symlink():
            raise EvidenceError("--db must be an absolute regular immutable pin path")
        path = supplied.resolve(strict=True)
        assert_standalone(path)
        before_stat = file_identity(path)
        before = digest_file(path)
        if file_identity(path) != before_stat:
            raise EvidenceError("source changed while hashing")
        groups = []
        try:
            if before not in seen:
                connection = sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1", uri=True)
                try:
                    connection.row_factory = sqlite3.Row
                    connection.execute("PRAGMA query_only=ON")
                    groups = load_groups(connection, before, start, end)
                finally:
                    connection.close()
        finally:
            assert_standalone(path)
            after = digest_file(path)
            if before != after or file_identity(path) != before_stat:
                raise EvidenceError("source changed during analysis; results must not be published")
        source = {"path": str(path), "sha256_before": before, "sha256_after": after,
                  "bytes": before_stat[2], "open_mode": "ro,immutable=1,query_only",
                  "duplicate_of": seen.get(before), "event_runs_loaded": len(groups)}
        sources.append(source)
        if before not in seen:
            seen[before] = str(path)
            all_groups.extend(groups)
    timelines = defaultdict(list)
    for group in all_groups:
        timelines[(group.key, group.event)].append(group)
    rows = []
    for timeline in timelines.values():
        timeline.sort(key=lambda g: (g.time, str(g.run)))
        # Multiple distinct runs with the same event timestamp are ambiguous;
        # do not pick whichever happens to sort first for a future observation.
        frequencies = Counter(g.time for g in timeline)
        for group in timeline:
            if frequencies[group.time] > 1:
                group.issues.add("ambiguous_duplicate_event_run_time")
                group.complete = group.metadata_valid = False
        times = [group.time for group in timeline]
        for group in timeline:
            rows.extend(cases_for_group(group, timeline, times, end))
    report = {
        "schema_version": 1, "hypothesis": "H1_NO_vs_other_two_YES_conditional_payoff_displayed_gap",
        "analyzer_sha256": digest_file(Path(__file__).resolve()),
        "payoff_reference_type": "conditional_payoff_reference",
        "review_start_utc": iso(start), "review_end_exclusive_utc": iso(end),
        "sources": sources, "cohorts": summaries(all_groups, rows), "row_count": len(rows),
        "unique_events_across_sources": len({(g.cohort["sport_family"], g.event) for g in all_groups if g.event}),
        "rungs_usdc": list(RUNGS), "horizons_minutes": list(HORIZONS),
        "future_join": "same source/config/runtime/sport/event; first run >= target, delay <=150s, before end; no interpolation or skipping incomplete first observations",
        "threshold_semantics": "strict > after rounding arithmetic to 1e-12 per share; independent event IDs within each source-cohort/rung, never row count",
        "promotion_ready": False, "live_promotion_allowed": False, "actual_fill_or_realized_pnl": False,
        "limitations": [
            "Input hashes prove unchanged bytes, not venue authentication or successful daily-rsync verification.",
            "Only events with at least one snapshot are in the denominator; zero-book events are not a complete Gamma census.",
            "Catalog token/outcome alignment is cross-checked independently of snapshot and book labels; catalog is mutable/unsigned and settlement-equivalence is not independently proven.",
            "conditional_payoff_reference only: normal regular-time HOME/DRAW/AWAY exhaustion is assumed, not independently proven. A .5/.5 void can pay NO=.5 but other two YES=1 combined. Exact NegRisk converter identity/rules are unverified; never call the gap risk-free/equal-payoff arbitrage. No post-hoc void-event exclusion is applied.",
            "Same run and snapshot timestamps are not proof of simultaneous venue books; raw per-leg receipt timing is unavailable in these adapters.",
            "Current catalog fee metadata is not a historical receipt. Only explicit exponent-1 stress rates 0/.03/.05 are reported, never actual fee-net profit.",
            "The rich YES basket requires inventory/financing and two executable sells; gap is not realizable by buying NO alone.",
            "Forward NO markout and basket change are separate displayed-price observations, not fill/P&L; gaps and unknown future stay missing.",
            "Source-cohort counts and overlapping collectors are not independent games and must not be summed as sample size.",
            "Naive source SQLite timestamps follow these producers' datetime.utcnow() convention; CLI timestamps must explicitly be UTC.",
        ],
    }
    return report, rows


def write_report(output: Path, report: dict, rows: list[dict], sources: list[Path]) -> None:
    output = output.absolute()
    csv_path = output.with_suffix(".rows.csv")
    source_paths = {path.resolve() for path in sources}
    if output.suffix != ".json" or output == csv_path:
        raise EvidenceError("--output must be a new .json file")
    for target in (output, csv_path):
        if target.exists() or target.is_symlink() or target.resolve() in source_paths:
            raise EvidenceError("refusing to overwrite an existing output or source")
    output.parent.mkdir(parents=True, exist_ok=True)
    # JSON is the completion marker, written after the complete CSV.
    with csv_path.open("x", encoding="utf-8", newline="") as handle:
        fieldnames = list(rows[0]) if rows else ["event_id", "run_id", "status"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, sort_keys=True, allow_nan=False)
                             if isinstance(value, (dict, list)) else value for key, value in row.items()})
    with output.open("x", encoding="utf-8") as handle:
        json.dump({**report, "rows_csv": str(csv_path)}, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, action="append", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True, help="new JSON report; sibling .rows.csv is also written")
    args = parser.parse_args(argv)
    try:
        report, rows = analyze_sources(args.db, utc(args.start), utc(args.end))
        write_report(args.output, report, rows, args.db)
    except (EvidenceError, OSError, sqlite3.Error) as error:
        parser.exit(2, f"Evidence error: {error}\n")
    print(json.dumps({"output": str(args.output), "rows": len(rows), "live_promotion_allowed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
