"""Simulation-only full-book/lifecycle archive independent of entry execution.

The raw publication is atomic but is not a claim that the enclosing run succeeded.
Queries use the active scheduling index; old observations are never scanned in a cycle.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math

from polybot_observability import current_run_id
from sqlalchemy import text

from ..db.models import (
    RawBookCycle,
    RawBookObservation,
    RawEventObservation,
    RawTrackedEvent,
)
from .filters import (
    get_event,
    get_event_metadata,
    get_match_result_sides,
    get_aligned_binary_outcomes,
    get_proven_resolution,
)

CONTRACT = "full-sports-raw-v1"
FULL_STATES = {"FULL", "EMPTY_ASKS", "EMPTY_BIDS", "EMPTY_BOOK"}
MAX_EVENT_FOLLOWUPS = 4


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def key(*parts):
    return hashlib.sha256(canonical(parts).encode()).hexdigest()


def utc(value):
    if value is None:
        return None
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (
        value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    )


def public_event(event):
    # Public sports fields only; no headers, credentials, account or wallet data.
    def safe(value):
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, dict):
            return {str(k): safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [safe(x) for x in value]
        return None

    fields = (
        "id",
        "title",
        "slug",
        "live",
        "ended",
        "closed",
        "active",
        "period",
        "elapsed",
        "score",
        "startTime",
        "startDate",
        "gameStartTime",
        "endDate",
        "updatedAt",
        "teams",
        "tags",
    )
    return {name: safe(event[name]) for name in fields if name in event}


class RawArchive:
    def __init__(self, scanner, now):
        self.scanner = scanner
        self.session = scanner.repo.session
        self.now = utc(now)
        self.run_id = current_run_id()
        if not self.run_id:
            raise RuntimeError("raw archive requires audited run identity")
        row = (
            self.session.execute(
                text(
                    "SELECT config_hash,job_name,mode FROM run_audits WHERE run_id=:run"
                ),
                {"run": self.run_id},
            )
            .mappings()
            .first()
        )
        if row is None or row["mode"] not in {"sim", "simulation"}:
            raise RuntimeError("raw archive requires simulation run provenance")
        self.config_hash = row["config_hash"]
        self.job_name = row["job_name"]
        self.family = scanner.config.sport_family
        self.digest = scanner.config.strategy_source_digest
        if not self.digest:
            raise RuntimeError("raw archive source digest missing")
        self.expected_slots = (
            [
                f"{result}:{side}"
                for result in ("HOME", "DRAW", "AWAY")
                for side in ("YES", "NO")
            ]
            if self.family == "soccer"
            else ["HOME:DIRECT", "AWAY:DIRECT"]
        )
        self.events = {}
        self.existing = self.session.get(RawBookCycle, self.run_id)
        self.discovery_error = None

    def prepare(self, markets, *, discovery_error=None):
        if self.existing is not None:
            return []
        self.discovery_error = discovery_error
        tracked = (
            self.session.query(RawTrackedEvent)
            .filter(
                RawTrackedEvent.job_name == self.job_name,
                RawTrackedEvent.state.in_(("LIVE", "WAIT_RESOLUTION")),
            )
            .order_by(RawTrackedEvent.last_checked_at, RawTrackedEvent.event_id)
            .all()
        )
        tracks = {row.event_id: row for row in tracked}
        groups = {}
        for market in markets:
            event_id = get_event_metadata(market)["event_id"]
            if not event_id:
                continue
            groups.setdefault(event_id, []).append(market)
        attempted = 0
        cache = getattr(self.scanner.gamma, "last_raw_sweep_events", {})
        ordered_groups = sorted(
            groups.items(),
            key=lambda item: (
                tracks[item[0]].identity_attempt_count if item[0] in tracks else 0,
                item[0],
            ),
        )
        for event_id, group in ordered_groups:
            track = tracks.pop(event_id, None) or self.session.get(
                RawTrackedEvent, key(self.job_name, self.family, event_id)
            )
            identity_source = "qualified_market_list"
            identity_attempted = False
            repair_error = None
            full_event = cache.get(event_id)
            if isinstance(full_event, dict) and str(full_event.get("id")) == event_id:
                full_group = self._direct_event_markets(full_event)
                if full_group:
                    group = full_group
                    identity_source = "same_sweep_full_event"
            if not self._complete_identity(group):
                budget = getattr(self.scanner.gamma, "cycle_budget", None)
                if attempted < MAX_EVENT_FOLLOWUPS and (
                    budget is None or budget.network_remaining_seconds >= 8
                ):
                    attempted += 1
                    identity_attempted = True
                    try:
                        event = self.scanner.gamma.get_event_by_id(event_id)
                    except Exception as error:
                        repair_error = "identity_lookup_" + type(error).__name__
                    else:
                        if isinstance(event, dict) and str(event.get("id")) == event_id:
                            full_group = self._direct_event_markets(event)
                            if full_group:
                                group = full_group
                                identity_source = "bounded_event_by_id"
                        if not self._complete_identity(group):
                            repair_error = "identity_lookup_still_incomplete"
                else:
                    repair_error = "identity_lookup_budget_deferred"
            self._current(event_id, group, track)
            context = self.events[event_id]
            context["identity_source"] = identity_source
            context["identity_attempted"] = identity_attempted
            if repair_error is not None and context["reason"] == "current_discovery":
                context["reason"] = repair_error
        for event_id, track in tracks.items():
            slots = json.loads(track.slots_json)
            context = {
                "track": track,
                "slots": slots,
                "state": track.state,
                "reason": "discovery_absent",
                "event": {},
                "markets": [],
                "proofs": [],
                "checked": False,
                "lookup_deferred": False,
            }
            self.events[event_id] = context
            if discovery_error:
                context.update(reason=discovery_error, lookup_deferred=True)
                continue
            # Completed games need resolution metadata, not another minute of books.
            if (
                track.state == "WAIT_RESOLUTION"
                and (self.now - track.last_checked_at).total_seconds() < 300
            ):
                context.update(reason="resolution_retry_not_due", lookup_deferred=True)
                continue
            budget = getattr(self.scanner.gamma, "cycle_budget", None)
            if attempted >= MAX_EVENT_FOLLOWUPS or (
                budget is not None and budget.network_remaining_seconds < 8
            ):
                context.update(reason="followup_budget_deferred", lookup_deferred=True)
                continue
            attempted += 1
            context["checked"] = True
            try:
                event = self.scanner.gamma.get_event_by_id(event_id)
            except Exception as error:
                context["reason"] = "event_lookup_" + type(error).__name__
                continue
            if not isinstance(event, dict) or str(event.get("id")) != event_id:
                context["reason"] = "event_source_missing_or_identity_gap"
                continue
            context["event"] = public_event(event)
            raw_markets = event.get("markets")
            known = {value["condition_id"] for value in slots.values()}
            aligned = {}
            if isinstance(raw_markets, list):
                for raw in raw_markets:
                    if (
                        not isinstance(raw, dict)
                        or str(raw.get("conditionId")) not in known
                    ):
                        continue
                    condition = str(raw["conditionId"])
                    if condition in aligned:
                        aligned[condition] = None
                    else:
                        enriched = dict(raw, sportFamily=self.family)
                        aligned[condition] = enriched
            context["markets"] = [
                market for market in aligned.values() if market is not None
            ]
            identity_ok = True
            for condition, market in aligned.items():
                expected_mapping = {
                    x["token_id"]: x["outcome"]
                    for x in slots.values()
                    if x["condition_id"] == condition
                }
                actual_mapping = {
                    x["token_id"]: x["outcome"]
                    for x in get_aligned_binary_outcomes(market or {})
                }
                if expected_mapping != actual_mapping:
                    identity_ok = False
                elif (proof := get_proven_resolution(market)) is not None:
                    context["proofs"].append(
                        {
                            "condition_id": condition,
                            "tokens": get_aligned_binary_outcomes(market),
                            "proof": proof,
                        }
                    )
            if not identity_ok:
                context["reason"] = "followup_token_identity_gap"
                continue
            proofs_complete = len(slots) == len(self.expected_slots) and len(
                context["proofs"]
            ) == len(known)
            if proofs_complete and self.family == "soccer":
                yes_payouts = sorted(
                    proof["proof"]["yes_payout"] for proof in context["proofs"]
                )
                proofs_complete = yes_payouts in ([0.0, 0.0, 1.0], [0.5, 0.5, 0.5])
            if proofs_complete:
                context.update(
                    state="RESOLVED", reason="exact_all_condition_terminal_proofs"
                )
            elif event.get("ended") is True:
                context.update(state="WAIT_RESOLUTION", reason="source_explicit_ended")
            elif event.get("live") is True and event.get("ended") is False:
                context.update(state="LIVE", reason="source_explicit_in_play_followup")
            else:
                context["reason"] = "source_clock_missing_or_not_live"
        # A terminal transition still attempts one final raw snapshot. Missing
        # metadata never terminates an already accepted game's book tracking.
        return list(
            dict.fromkeys(
                value["token_id"]
                for event in self.events.values()
                if self._needs_books(event)
                for value in event["slots"].values()
            )
        )

    def _direct_event_markets(self, event):
        markets = event.get("markets")
        if not isinstance(markets, list):
            return []
        result = []
        for raw_market in markets:
            if not isinstance(raw_market, dict):
                continue
            # The event was accepted already. Reuse its full semantic market
            # set while leaving liquidity/order-acceptance gates at entry only.
            market = dict(raw_market, events=[event], sportFamily=self.family)
            if get_match_result_sides(market):
                result.append(market)
        return result

    def _complete_identity(self, markets):
        sides = [side for market in markets for side in get_match_result_sides(market)]
        slots = {f"{side['result_kind']}:{side['outcome_side']}" for side in sides}
        return (
            len(sides) == len(self.expected_slots)
            and slots == set(self.expected_slots)
            and len({str(side["token_id"]) for side in sides}) == len(sides)
        )

    @staticmethod
    def _needs_books(context):
        track = context["track"]
        return track is None or track.state == "LIVE" or context["state"] == "LIVE"

    def _current(self, event_id, markets, track):
        slots = json.loads(track.slots_json) if track is not None else {}
        reason = "current_discovery"
        current = {}
        for market in markets:
            condition = str(market.get("conditionId") or "")
            for side in get_match_result_sides(market):
                slot = f"{side['result_kind']}:{side['outcome_side']}"
                value = {
                    "token_id": str(side["token_id"]),
                    "condition_id": condition,
                    "outcome": str(side["outcome"]),
                }
                if slot in current or (slot in slots and slots[slot] != value):
                    reason = "current_token_identity_gap"
                    continue
                current[slot] = value
        if len({v["token_id"] for v in current.values()}) != len(current):
            reason = "current_token_identity_gap"
        if reason == "current_discovery":
            slots.update(current)
        state = (
            "RESOLVED" if track is not None and track.state == "RESOLVED" else "LIVE"
        )
        if state == "RESOLVED":
            reason = "already_terminal_discovery_echo"
        self.events[event_id] = {
            "track": track,
            "slots": slots,
            "state": state,
            "reason": reason,
            "event": public_event(get_event(markets[0])),
            "markets": markets,
            "proofs": [],
            "checked": True,
            "lookup_deferred": False,
        }

    def publish(self):
        if self.existing is not None:
            return {"status": self.existing.status, "idempotent": True}
        stats = Counter()
        observations = []
        event_rows = []
        tracks = []
        for event_id, context in self.events.items():
            needs_books = self._needs_books(context)
            slots = context["slots"]
            event_states = Counter()
            if needs_books:
                for slot in self.expected_slots:
                    identity = slots.get(slot)
                    value = None
                    if identity and not self.discovery_error:
                        value = self.scanner.clob.get_cached_research_observation(
                            identity["token_id"]
                        )
                    if identity is None:
                        value = {
                            "status": "IDENTITY_MISSING",
                            "reason": "expected_slot_token_unidentified",
                        }
                    elif value is None:
                        value = {
                            "status": "NOT_ATTEMPTED",
                            "reason": self.discovery_error
                            or "no_batch_attempt_evidence",
                        }
                    status = value["status"]
                    payload = value.get("book_json")
                    if payload is not None:
                        book = json.loads(payload)
                        if str(book.get("token_id")) != identity["token_id"]:
                            raise ValueError("raw book token identity mismatch")
                    event_states[status] += 1
                    stats[status] += 1
                    observations.append(
                        RawBookObservation(
                            observation_id=key(self.run_id, event_id, slot),
                            run_id=self.run_id,
                            event_id=event_id,
                            slot=slot,
                            condition_id=identity["condition_id"] if identity else None,
                            token_id=identity["token_id"] if identity else None,
                            status=status,
                            reason=value["reason"],
                            requested_at=utc(value.get("requested_at")),
                            received_at=utc(value.get("received_at")),
                            book_json=payload,
                            book_sha256=hashlib.sha256(payload.encode()).hexdigest()
                            if payload
                            else None,
                        )
                    )
            evidence = {
                "contract": CONTRACT,
                "event": context["event"],
                "market_context": [
                    {
                        name: market.get(name)
                        for name in (
                            "conditionId",
                            "liquidityNum",
                            "liquidity",
                            "volumeNum",
                            "volume",
                            "volume24hr",
                            "feesEnabled",
                            "feeSchedule",
                            "feeType",
                            "feeRateBps",
                            "active",
                            "closed",
                            "enableOrderBook",
                            "acceptingOrders",
                            "updatedAt",
                        )
                        if name in market
                    }
                    for market in context["markets"]
                ],
                "slots": slots,
                "source_reason": context["reason"],
                "lookup_deferred": context["lookup_deferred"],
                "identity_source": context.get(
                    "identity_source", "tracked_condition_anchor"
                ),
                "book_status_counts": dict(event_states),
                "final_raw_book_attempted": needs_books and context["state"] != "LIVE",
                "final_raw_book_complete": needs_books
                and context["state"] != "LIVE"
                and sum(event_states[x] for x in FULL_STATES)
                == len(self.expected_slots),
                "terminal_proofs": context["proofs"],
            }
            event_rows.append(
                RawEventObservation(
                    observation_id=key(self.run_id, event_id),
                    run_id=self.run_id,
                    event_id=event_id,
                    status=context["state"],
                    reason=context["reason"],
                    expected_tokens=len(self.expected_slots) if needs_books else 0,
                    identified_tokens=len(slots),
                    observed_at=self.now,
                    evidence_json=canonical(evidence),
                )
            )
            track = context["track"]
            if track is None:
                track = RawTrackedEvent(
                    tracking_id=key(self.job_name, self.family, event_id),
                    job_name=self.job_name,
                    sport_family=self.family,
                    event_id=event_id,
                    first_seen_at=self.now,
                    last_checked_at=self.now,
                    state=context["state"],
                    slots_json=canonical(slots),
                    missing_count=0,
                    identity_attempt_count=0,
                )
            if context.get("identity_attempted"):
                track.identity_attempt_count += 1
            track.state = context["state"]
            track.slots_json = canonical(slots)
            if context["checked"]:
                track.last_checked_at = self.now
                if context["reason"] not in {
                    "current_discovery",
                    "source_explicit_in_play_followup",
                    "source_explicit_ended",
                    "exact_all_condition_terminal_proofs",
                }:
                    track.missing_count += 1
            tracks.append(track)
        expected = len(observations)
        actual = sum(stats[state] for state in FULL_STATES)
        metadata_gap = any(
            x["reason"]
            not in {
                "current_discovery",
                "source_explicit_in_play_followup",
                "source_explicit_ended",
                "exact_all_condition_terminal_proofs",
                "resolution_retry_not_due",
            }
            for x in self.events.values()
        )
        status = (
            "PARTIAL"
            if expected != actual or metadata_gap or self.discovery_error
            else "COMPLETE"
            if expected
            else "EMPTY"
        )
        cycle = RawBookCycle(
            run_id=self.run_id,
            config_hash=self.config_hash,
            strategy_source_digest=self.digest,
            job_name=self.job_name,
            sport_family=self.family,
            contract=CONTRACT,
            observed_at=self.now,
            published_at=utc(datetime.now(timezone.utc)),
            status=status,
            expected_events=len(event_rows),
            expected_tokens=expected,
            observed_tokens=actual,
            evidence_json=canonical(
                {
                    "book_status_counts": dict(stats),
                    "discovery_error": self.discovery_error,
                    "run_status_is_separate": True,
                    "max_event_followups": MAX_EVENT_FOLLOWUPS,
                }
            ),
        )
        try:
            self.session.add(cycle)
            self.session.flush()
            self.session.add_all([*observations, *event_rows, *tracks])
            self.session.commit()
        except BaseException:
            self.session.rollback()
            raise
        return {
            "status": status,
            "expected_tokens": expected,
            "observed_tokens": actual,
            "book_status_counts": dict(stats),
        }


def begin_raw_archive(scanner, now, markets, *, discovery_error=None):
    if getattr(scanner.clob, "simulation_mode", False) is not True:
        return None, []
    archive = RawArchive(scanner, now)
    tokens = archive.prepare(markets, discovery_error=discovery_error)
    return archive, tokens
