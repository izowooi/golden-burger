"""Prospective raw lifecycle, isolated from White's hypothetical decisions.

Core books are immutable references into the parent DB. Only additional public
responses have payload bytes here. Consumers need verified pins of both DBs and
must require parent SUCCEEDED plus atomic raw publication; published != full book.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from typing import Mapping
from uuid import uuid4

from .db.raw_repository import RAW_CONTRACT, RawRepository
from .league_classifier import classify_sports_event
from .utils.retry import canonical_json, iso_utc

FOLLOWUP_LIMIT = 20
WAIT_RESOLUTION_SECONDS = 300
DISCOVERY_ONLY_REASON = "EXPLICIT_CHILD_OUTSIDE_WHOLE_GAME"


def _time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (TypeError, ValueError):
        return None


def _array(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def explicit_child(event):
    return isinstance(event, Mapping) and event.get("parentEventId") not in (None, "")


def child_registry_corrections(repo, current):
    """Bounded working-set repair, anchored to the first immutable observation.

    r5 never clears a nonempty slots_json, so empty slots prove that this registry
    row never acquired a structural 3/2-token anchor. A missing first observation
    or a top-level event is retained; neither is guessed to be a child.
    """
    corrections = {}
    rows = repo.connection.execute(
        "SELECT * FROM raw_tracked_events INDEXED BY raw_pending_idx "
        "WHERE state IN ('TRACKING','WAIT_RESOLUTION') AND slots_json='[]'"
    ).fetchall()
    for anchor in rows:
        cur = current.get(anchor["event_id"])
        if cur and cur["valid"] and not explicit_child(cur["event"]):
            continue
        first = repo.connection.execute(
            "SELECT * FROM raw_events WHERE run_id=? AND event_id=?",
            (anchor["first_run_id"], anchor["event_id"]),
        ).fetchone()
        if first is None or not first["event_json"]:
            continue
        try:
            event = json.loads(first["event_json"])
        except (ValueError, TypeError):
            continue
        if (not explicit_child(event)
                or str(event.get("id") or "") != anchor["event_id"]):
            continue
        corrections[anchor["event_id"]] = {
            "first": dict(first), "event": event,
            "proof": {"reason": DISCOVERY_ONLY_REASON,
                "basis": "FIRST_REGISTRY_OBSERVATION", "run_id": anchor["first_run_id"],
                "event_id": anchor["event_id"], "parent_event_id": event["parentEventId"],
                "event_json_sha256": hashlib.sha256(first["event_json"].encode()).hexdigest(),
                "previous_state": anchor["state"], "new_state": "DISCOVERY_ONLY"},
        }
    return corrections


def event_identity(event, family, gamma_config):
    """Structural identity only. Never promote old eligibility to current OPEN."""
    from .collector import classify_match_winner
    classification = classify_sports_event(event, gamma_config, family)
    if not classification.accepted or event.get("parentEventId") not in (None, ""):
        return [], False
    slots = []
    for market in event.get("markets", []) if isinstance(event.get("markets"), list) else []:
        if not isinstance(market, Mapping):
            continue
        labels, tokens = _array(market.get("outcomes")), _array(market.get("clobTokenIds"))
        original_prices = _array(market.get("outcomePrices"))
        if (any(isinstance(x, bool) for x in original_prices)
                or not all(isinstance(x, str) and x for x in labels + tokens)):
            continue
        try:
            probabilities = [float(x) for x in original_prices]
        except (ValueError, TypeError):
            continue
        kind, indices, evidence, _ = classify_match_winner(event, market, labels, tokens, probabilities, family)
        if kind == "REJECTED":
            continue
        condition = str(market.get("conditionId") or market.get("condition_id") or "")
        if not condition:
            continue
        for index in indices:
            slot = evidence["result_kinds_by_index"][index] if family == "soccer" else f"OUTCOME_{index}"
            slots.append({"slot": slot, "condition_id": condition, "token_id": str(tokens[index]),
                          "outcome": labels[index], "all_tokens": list(tokens), "all_outcomes": list(labels)})
    expected = {"HOME", "DRAW", "AWAY"} if family == "soccer" else {"OUTCOME_0", "OUTCOME_1"}
    valid = (len(slots) == len(expected) and {s["slot"] for s in slots} == expected
             and len({s["token_id"] for s in slots}) == len(expected)
             and len({s["condition_id"] for s in slots}) == (3 if family == "soccer" else 1))
    return sorted(slots, key=lambda s: s["slot"]) if valid else [], valid


def terminal_proof(event, slots, family):
    """Exact aligned Gamma payout, with whole-event soccer consistency."""
    if not slots or not isinstance(event.get("markets"), list):
        return None
    payouts, proofs = {}, []
    for slot in slots:
        matches = [m for m in event["markets"] if isinstance(m, Mapping)
                   and str(m.get("conditionId") or m.get("condition_id") or "") == slot["condition_id"]]
        if len(matches) != 1:
            return None
        market = matches[0]
        labels, tokens = _array(market.get("outcomes")), _array(market.get("clobTokenIds"))
        prices = _array(market.get("outcomePrices"))
        if (market.get("closed") is not True or len(labels) != 2 or len(tokens) != 2
                or len(prices) != 2 or len(set(tokens)) != 2
                or dict(zip(tokens, labels)) != dict(zip(slot["all_tokens"], slot["all_outcomes"]))):
            return None
        try:
            if any(isinstance(x, bool) for x in prices):
                return None
            values = [float(x) for x in prices]
            if not all(math.isfinite(x) for x in values):
                return None
        except (ValueError, TypeError):
            return None
        void = values == [0.5, 0.5] and str(market.get("umaResolutionStatus") or "").lower() == "resolved"
        if values not in ([1.0, 0.0], [0.0, 1.0]) and not void:
            return None
        payout = dict(zip(tokens, values))[slot["token_id"]]
        payouts[slot["token_id"]] = payout
        proofs.append({"condition_id": slot["condition_id"], "token_id": slot["token_id"], "payout": payout, "void": void})
    if family == "soccer":
        values = list(payouts.values())
        if not (sorted(values) == [0.0, 0.0, 1.0] or values == [0.5, 0.5, 0.5]):
            return None
    return {"source": "EXACT_GAMMA_EVENT_MARKETS", "token_payouts": proofs}


def _book_status(book, token):
    if not isinstance(book, Mapping) or str(book.get("asset_id") or "") != token:
        return "IDENTITY_MISMATCH"
    for side in ("bids", "asks"):
        if not isinstance(book.get(side), list):
            return "MALFORMED"
        for level in book[side]:
            if not isinstance(level, Mapping):
                return "MALFORMED"
            try:
                p, size = float(level["price"]), float(level["size"])
                if (isinstance(level["price"], bool) or isinstance(level["size"], bool)
                        or not math.isfinite(p) or not math.isfinite(size)
                        or not 0 < p <= 1 or size <= 0):
                    return "MALFORMED"
            except (KeyError, TypeError, ValueError):
                return "MALFORMED"
    return ("EMPTY_BOOK" if not book["bids"] and not book["asks"] else
            "EMPTY_BIDS" if not book["bids"] else "EMPTY_ASKS" if not book["asks"] else "FULL")


class ResearchRawCollector:
    def __init__(self, config, repository, gamma, clob):
        if not config.simulation_mode or config.trading.lifecycle_mode != "archive_only":
            raise ValueError("White raw lifecycle is research-only")
        self.config, self.parent, self.gamma, self.clob = config, repository, gamma, clob
        self._request_cache = {}

    def _request(self, request_id):
        if not request_id:
            return {}
        if request_id in self._request_cache:
            return self._request_cache[request_id]
        with self.parent.connect() as c:
            row = c.execute("SELECT * FROM api_requests WHERE request_id=?", (request_id,)).fetchone()
        result = dict(row) if row else {}
        self._request_cache[request_id] = result
        return result

    def _terminal_working_states(self, repo, reference):
        """Confirm derived terminal state only after the parent run publication.

        RESOLVED is the legacy r5 candidate state. Confirmed rows move to a
        distinct state so subsequent cycles never scan the resolved history.
        """
        checks, updates, retry, confirmed = [], [], {}, set()
        rows = repo.connection.execute(
            "SELECT * FROM raw_tracked_events INDEXED BY raw_pending_idx "
            "WHERE state IN ('RESOLVED','TERMINAL_PENDING_PUBLICATION')"
        ).fetchall()
        run_checks = {}
        for raw in rows:
            anchor = dict(raw)
            previous = anchor["last_run_id"]
            if previous not in run_checks:
                cycle = repo.connection.execute("SELECT * FROM raw_cycles WHERE run_id=?", (previous,)).fetchone()
                with self.parent.connect() as c:
                    parent_rows = [dict(r) for r in c.execute(
                        "SELECT * FROM research_run_events WHERE run_id=?", (previous,))]
                started = [r for r in parent_rows if r["event_type"] == "STARTED"]
                succeeded = [r for r in parent_rows if r["event_type"] == "SUCCEEDED"]
                valid = bool(cycle and cycle["status"] == "PUBLISHED" and len(started) == len(succeeded) == 1
                    and len(parent_rows) == 2 and cycle["job_name"] == self.config.job_name)
                if valid:
                    valid = all(r["config_hash"] == cycle["config_hash"]
                                and r["strategy_source_digest"] == cycle["source_digest"] for r in parent_rows)
                    times = [_time(started[0]["observed_at"]), _time(cycle["reference_at"]),
                             _time(cycle["published_at"]), _time(succeeded[0]["observed_at"])]
                    valid = (valid and all(t is not None for t in times) and times == sorted(times)
                             and _time(reference) is not None and times[-1] <= _time(reference))
                run_checks[previous] = {
                    "parent_and_raw_published": bool(valid),
                    "raw_status": cycle["status"] if cycle else None,
                    "parent_event_types": [r["event_type"] for r in parent_rows],
                }
            proof = dict(run_checks[previous])
            terminal = repo.connection.execute(
                "SELECT * FROM raw_events WHERE run_id=? AND event_id=?",
                (previous, anchor["event_id"]),
            ).fetchone()
            try:
                computed = terminal_proof(json.loads(terminal["event_json"]),
                    json.loads(anchor["slots_json"]), anchor["family"]) if terminal else None
                exact_terminal = bool(terminal and terminal["identity_valid"] == 1
                    and terminal["lifecycle_state"] == "RESOLVED" and terminal["terminal_json"]
                    and computed is not None and computed == json.loads(terminal["terminal_json"]))
            except (ValueError, TypeError, KeyError):
                exact_terminal = False
            proof["exact_terminal_event_record"] = exact_terminal
            proof["parent_and_raw_published"] = proof["parent_and_raw_published"] and exact_terminal
            state = "RESOLVED_CONFIRMED" if proof["parent_and_raw_published"] else "WAIT_RESOLUTION"
            reason = "PARENT_SUCCESS_CONFIRMED" if proof["parent_and_raw_published"] else "TERMINAL_RETRY_UNPUBLISHED_PARENT"
            checks.append({"event_id": anchor["event_id"], "evidence_run_id": previous,
                           "previous_state": anchor["state"], "new_state": state, "reason": reason, **proof})
            updates.append((state, anchor["slots_json"], previous, reference, anchor["missing_count"], reason, anchor["event_id"]))
            if proof["parent_and_raw_published"]:
                confirmed.add(anchor["event_id"])
            else:
                retry[anchor["event_id"]] = {**anchor, "state": state, "next_attempt_at": reference}
        return checks, updates, retry, confirmed

    def capture(self, *, run_id, now, budget, sweep, core_books, core_snapshots, core_resolution_responses=(), core_clock=None):
        repo = RawRepository(self.parent.path, busy_timeout_ms=self.config.trading.storage.busy_timeout_ms)
        try:
            published = repo.published(run_id)
            if published is not None:
                return published
            return self._capture(repo, run_id, now, budget, sweep, core_books, core_snapshots, core_resolution_responses, core_clock)
        finally:
            repo.close()

    def _capture(self, repo, run_id, now, budget, sweep, core_books, core_snapshots, core_resolution_responses, core_clock):
        reference = iso_utc(now)
        terminal_checks, terminal_updates, terminal_retry, terminal_confirmed = self._terminal_working_states(repo, reference)
        current = {}
        for page in sweep.pages:
            try:
                raw_page = json.loads(page.raw)
                raw_events = raw_page.get("events", []) if isinstance(raw_page, dict) else []
                page_valid = hashlib.sha256(page.raw).hexdigest() == page.response_sha256
            except (ValueError, TypeError):
                raw_events, page_valid = [], False
            for event in page.events:
                family = page.sport_family
                classification = classify_sports_event(event, self.config.trading.gamma, family)
                if not classification.accepted or not str(event.get("id") or ""):
                    continue
                event_id = str(event["id"])
                slots, valid = event_identity(event, family, self.config.trading.gamma)
                current[event_id] = {"event": event, "family": family, "slots": slots, "valid": valid,
                    "raw_valid": page_valid and sum(canonical_json(x) == canonical_json(event) for x in raw_events) == 1,
                    "received_at": page.received_at, "request_id": page.request_id,
                    "source_kind": "PARENT_GAMMA_PAGE", "source_ref": {"run_id": run_id, "request_id": page.request_id,
                        "payload_kind": "GAMMA_EVENT_PAGE", "sha256": page.response_sha256}}
                # Clock bytes already live in the parent. Preserve only exact
                # provider game-ID source links, never infer a join from names.
                clock_refs = []
                for raw in getattr(core_clock, "matched_raw_messages", ()):
                    try:
                        clock = json.loads(raw)
                        game_id = str(clock.get("gameId") or clock.get("game_id") or "")
                        expected_id = str(event.get("gameId") or event.get("game_id") or "")
                        if game_id and game_id == expected_id:
                            clock_refs.append({"run_id": run_id, "request_id": core_clock.request_id,
                                "payload_kind": "SPORTS_CLOCK_UPDATE", "sha256": hashlib.sha256(raw).hexdigest(),
                                "received_at": core_clock.completed_at, "provider_game_id": game_id})
                    except (ValueError, TypeError, AttributeError):
                        continue
                current[event_id]["source_ref"]["clock_refs"] = clock_refs
        corrections = child_registry_corrections(repo, current)
        # Keep discovery observations, but explicit children are not the strict
        # whole-game follow-up population. No price or $5-depth gate is added.
        with repo.transaction() as c:
            for event_id, cur in current.items():
                if c.execute("SELECT 1 FROM raw_tracked_events WHERE event_id=?", (event_id,)).fetchone() is None:
                    state = "DISCOVERY_ONLY" if explicit_child(cur["event"]) else "TRACKING"
                    c.execute("INSERT INTO raw_tracked_events VALUES(?,?,?,?,?,?,?,?,?)",
                        (event_id, cur["family"], state, canonical_json(cur["slots"]), run_id, run_id, reference, 0,
                         DISCOVERY_ONLY_REASON if state == "DISCOVERY_ONLY" else None))
        tracked = {r["event_id"]: r for r in repo.pending(reference)}
        tracked.update(terminal_retry)
        # A future-due child must also get its one-time classification audit now.
        for event_id in corrections:
            tracked[event_id] = dict(repo.connection.execute(
                "SELECT * FROM raw_tracked_events WHERE event_id=?", (event_id,)
            ).fetchone())
        # An already resolved event returned by live discovery stays terminal.
        for event_id in current:
            row = repo.connection.execute("SELECT * FROM raw_tracked_events WHERE event_id=?", (event_id,)).fetchone()
            if event_id in terminal_confirmed:
                continue
            if row is not None and row["state"] != "RESOLVED_CONFIRMED":
                tracked[event_id] = terminal_retry.get(event_id, dict(row))
        events, books, payloads, updates = [], [], [], list(terminal_updates)
        # Core Gamma OPEN/CLOSED_UNRESOLVED/empty views previously had receipts
        # but lost payload bytes. Terminal payloads already in the parent remain
        # parent-only: do not duplicate those source observations here.
        for response in core_resolution_responses:
            with self.parent.connect() as c:
                exists = c.execute("SELECT 1 FROM raw_payloads WHERE run_id=? AND request_id=? LIMIT 1",
                                   (run_id, response["request_id"])).fetchone()
            if not exists:
                payloads.append({"payload_id": uuid4().hex, "run_id": run_id,
                    "kind": "GAMMA_MARKET_FOLLOWUP", "request_id": response["request_id"],
                    "received_at": response["received_at"], "sha256": response["sha256"],
                    "payload_gzip": gzip.compress(response["raw"], mtime=0)})
        incomplete = False
        lookups = 0
        for event_id, anchor in sorted(tracked.items(), key=lambda item: (item[1]["next_attempt_at"], item[0])):
            family = anchor["family"]
            cur = current.get(event_id)
            correction = corrections.get(event_id)
            discovery_only = (correction is not None or (
                anchor["state"] == "DISCOVERY_ONLY" and not (cur and cur["valid"])))
            if discovery_only:
                first = correction["first"] if correction else None
                event = cur["event"] if cur else correction["event"]
                ref = dict(cur["source_ref"]) if cur else {
                    "run_id": first["run_id"], "event_id": event_id,
                    "source_ref": json.loads(first["source_ref_json"]),
                }
                if correction:
                    ref["registry_reclassification"] = correction["proof"]
                ref["discovery_only_reason"] = DISCOVERY_ONLY_REASON
                events.append({"run_id": run_id, "event_id": event_id, "family": family,
                    "metadata_status": "DISCOVERY_ONLY" if cur else "DISCOVERY_ONLY_RECLASSIFIED",
                    "metadata_received_at": cur["received_at"] if cur else first["metadata_received_at"],
                    "metadata_request_id": cur["request_id"] if cur else first["metadata_request_id"],
                    "source_kind": cur["source_kind"] if cur else "PRIOR_RAW_EVENT",
                    "source_ref_json": canonical_json(ref), "event_json": canonical_json(event),
                    "slots_json": "[]", "identity_valid": 0, "lifecycle_state": "DISCOVERY_ONLY",
                    "terminal_json": None, "missing_count": anchor["missing_count"]})
                updates.append(("DISCOVERY_ONLY", "[]", run_id, reference, anchor["missing_count"],
                                DISCOVERY_ONLY_REASON, event_id))
                continue
            status = "OBSERVED" if cur else "MISSING"
            if cur is None:
                if lookups >= FOLLOWUP_LIMIT or budget.network_remaining_seconds < 0.1 or budget.cycle_remaining_seconds < 2:
                    status, incomplete = "BUDGET_DEFERRED", True
                else:
                    fetch = getattr(self.gamma, "fetch_event_by_id", None)
                    if callable(fetch):
                        lookups += 1
                        result = fetch(run_id, event_id)
                        status = result.status
                        source_ref = {}
                        if result.raw is not None:
                            pid = uuid4().hex
                            payloads.append({"payload_id": pid, "run_id": run_id, "kind": "GAMMA_EVENT_FOLLOWUP",
                                "request_id": result.request_id, "received_at": result.received_at,
                                "sha256": hashlib.sha256(result.raw).hexdigest(), "payload_gzip": gzip.compress(result.raw, mtime=0)})
                            source_ref = {"payload_id": pid, "sha256": result.response_sha256}
                        event = result.event
                        if status == "OBSERVED" and (not isinstance(event, Mapping) or str(event.get("id") or "") != event_id):
                            status = "IDENTITY_MISMATCH"
                        cur = {"event": event, "received_at": result.received_at, "request_id": result.request_id,
                               "source_kind": "RAW_GAMMA_EVENT", "source_ref": source_ref}
                        try:
                            cur["raw_valid"] = (result.raw is not None
                                and hashlib.sha256(result.raw).hexdigest() == result.response_sha256
                                and json.loads(result.raw) == [event])
                        except (ValueError, TypeError):
                            cur["raw_valid"] = False
                        if status == "ERROR":
                            incomplete = True
                    else:
                        status = "LOOKUP_UNAVAILABLE"
                        incomplete = True
            cur = cur or {"event": None, "received_at": None, "request_id": None, "source_kind": "NONE", "source_ref": {}}
            event = cur["event"]
            fresh_slots, valid = event_identity(event, family, self.config.trading.gamma) if status == "OBSERVED" else ([], False)
            metadata_request = self._request(cur["request_id"])
            metadata_start = _time(metadata_request.get("started_at"))
            metadata_received = _time(cur["received_at"])
            metadata_valid = (cur.get("raw_valid", False) and metadata_request.get("run_id") == run_id
                and metadata_request.get("status") == "SUCCESS"
                and metadata_request.get("response_sha256") == cur["source_ref"].get("sha256")
                and _time(metadata_request.get("completed_at")) == metadata_received
                and metadata_start is not None and metadata_received is not None
                and now <= metadata_start <= metadata_received <= datetime.now(timezone.utc))
            valid = valid and metadata_valid
            prior_slots = json.loads(anchor["slots_json"])
            if prior_slots and fresh_slots != prior_slots:
                valid = False
                if status == "OBSERVED":
                    status = "IDENTITY_MISMATCH"
            slots = prior_slots or fresh_slots
            if not slots:
                names = ("HOME", "DRAW", "AWAY") if family == "soccer" else ("OUTCOME_0", "OUTCOME_1")
                slots = [{"slot": name, "condition_id": None, "token_id": None, "outcome": None} for name in names]
            proof = terminal_proof(event, slots, family) if valid else None
            missing = 0 if status == "OBSERVED" and valid else anchor["missing_count"] + 1
            ended = bool(valid and isinstance(event, Mapping) and event.get("ended") is True)
            state = "RESOLVED" if proof else "WAIT_RESOLUTION" if ended or anchor["state"] == "WAIT_RESOLUTION" else "TRACKING"
            events.append({"run_id": run_id, "event_id": event_id, "family": family, "metadata_status": status,
                "metadata_received_at": cur["received_at"], "metadata_request_id": cur["request_id"],
                "source_kind": cur["source_kind"], "source_ref_json": canonical_json(cur["source_ref"]),
                "event_json": canonical_json(event) if event is not None else None,
                "slots_json": canonical_json(slots), "identity_valid": int(valid), "lifecycle_state": state,
                "terminal_json": canonical_json(proof) if proof else None, "missing_count": missing})
            # A later by-ID response cannot retroactively validate a core quote.
            # Keep core economics intact and request a distinct raw quote instead.
            wanted = []
            for slot in slots:
                token = slot["token_id"]
                if not token:
                    continue
                prior_attempt = core_books.attempts.get(token)
                prior_request = self._request(prior_attempt.request_id) if prior_attempt else {}
                prior_start = _time(prior_request.get("started_at"))
                if (prior_attempt is None or (valid and (prior_start is None
                        or metadata_received > prior_start))):
                    wanted.append(token)
            extra = None
            if wanted and valid:
                if budget.network_remaining_seconds < 0.1 or budget.cycle_remaining_seconds < 2:
                    incomplete = True
                else:
                    extra = self.clob.fetch_books(run_id, wanted)
                    for payload in extra.raw_payloads:
                        payloads.append({"payload_id": uuid4().hex, "run_id": run_id, "kind": "CLOB_BOOK_BATCH",
                            "request_id": payload.request_id, "received_at": payload.received_at,
                            "sha256": hashlib.sha256(payload.raw).hexdigest(), "payload_gzip": gzip.compress(payload.raw, mtime=0)})
            event_book_start = len(books)
            for slot in slots:
                token = slot["token_id"]
                collection = extra if token in wanted else core_books
                source_kind = "RAW_BOOK" if token in wanted else "PARENT_BOOK"
                attempt = collection.attempts.get(token) if collection else None
                row_status = "IDENTITY_MISSING" if token is None else "NOT_ATTEMPTED"
                if token in wanted and extra is None and valid:
                    row_status = "BUDGET_DEFERRED"
                raw_book, source_ref, request, requested, received, digest = None, {}, None, None, None, None
                error = None
                if attempt:
                    request, received = attempt.request_id, attempt.received_at
                    req = self._request(request)
                    requested = req.get("started_at")
                    error = attempt.error_type
                    row_status = attempt.status
                    batch = next((p for p in collection.raw_payloads if p.request_id == request), None)
                    if batch:
                        source_ref = {"run_id": run_id, "request_id": request, "payload_kind": "CLOB_BOOK_BATCH", "sha256": hashlib.sha256(batch.raw).hexdigest()}
                        try:
                            parsed = json.loads(batch.raw)
                            expected_tokens = {t for t, a in collection.attempts.items() if a.request_id == request}
                            returned = [str(b.get("asset_id") or "") for b in parsed if isinstance(b, dict)] if isinstance(parsed, list) else []
                            strict = (isinstance(parsed, list) and len(parsed) == len(returned) and len(set(returned)) == len(returned)
                                      and set(returned) <= expected_tokens and hashlib.sha256(batch.raw).hexdigest() == batch.response_sha256)
                            if strict:
                                raw_book = next((b for b in parsed if str(b.get("asset_id")) == token), None)
                            else:
                                row_status = "MALFORMED"
                        except (ValueError, TypeError):
                            row_status = "MALFORMED"
                    if raw_book is not None:
                        row_status = _book_status(raw_book, token)
                        if raw_book.get("market") not in (None, slot["condition_id"]):
                            row_status = "IDENTITY_MISMATCH"
                        digest = hashlib.sha256(canonical_json(raw_book).encode()).hexdigest()
                    if source_kind == "PARENT_BOOK":
                        snap = core_snapshots.get(token)
                        if snap:
                            source_ref["snapshot_id"] = snap["snapshot_id"]
                            if digest != snap["raw_book_sha256"]:
                                row_status = "MALFORMED"
                    else:
                        match = next((p for p in payloads if p["kind"] == "CLOB_BOOK_BATCH" and p["request_id"] == request), None)
                        if match:
                            source_ref["payload_id"] = match["payload_id"]
                    if row_status == "ERROR":
                        incomplete = True
                mt, qt, rt = _time(cur["received_at"]), _time(requested), _time(received)
                pit = (valid and mt is not None and qt is not None and rt is not None and now <= mt <= qt <= rt <= datetime.now(timezone.utc)
                       and self._request(request).get("run_id") == run_id
                       and self._request(request).get("status") == "SUCCESS"
                       and self._request(request).get("response_sha256") == source_ref.get("sha256")
                       and _time(self._request(request).get("completed_at")) == rt
                       and row_status in {"FULL", "EMPTY_BIDS", "EMPTY_ASKS", "EMPTY_BOOK"})
                books.append({"run_id": run_id, "event_id": event_id, "slot": slot["slot"],
                    "condition_id": slot["condition_id"], "token_id": token, "outcome": slot["outcome"], "status": row_status,
                    "request_id": request, "requested_at": requested, "received_at": received, "book_sha256": digest,
                    "source_kind": source_kind if attempt else "NONE", "source_ref_json": canonical_json(source_ref),
                    "point_in_time_identity_valid": int(pit), "error_type": error})
            final_deferred = bool(proof and any(b["status"] in {"NOT_ATTEMPTED", "BUDGET_DEFERRED", "IDENTITY_MISSING"}
                                               for b in books[event_book_start:]))
            if final_deferred:
                # A terminal proof is retained, but do not remove a due final
                # book attempt from the registry merely because this slot ran
                # out of optional network budget.
                state = "WAIT_RESOLUTION"
                events[-1]["lifecycle_state"] = state
            next_at = now + timedelta(seconds=WAIT_RESOLUTION_SECONDS if state == "WAIT_RESOLUTION" and not final_deferred else 0)
            registry_state = "TERMINAL_PENDING_PUBLICATION" if state == "RESOLVED" else state
            updates.append((registry_state, canonical_json(prior_slots or fresh_slots), run_id, iso_utc(next_at), missing,
                            "FINAL_BOOK_DEFERRED" if final_deferred else "EXACT_TERMINAL" if proof else None, event_id))
        incomplete = incomplete or bool(budget.incomplete_reasons)
        if incomplete:
            budget.mark_incomplete("white_raw_lifecycle_incomplete")
            for event in events:
                if event["lifecycle_state"] == "RESOLVED":
                    event["lifecycle_state"] = "WAIT_RESOLUTION"
            updates = [("WAIT_RESOLUTION", slots, previous, reference, missing,
                        "TERMINAL_RETRY_AFTER_INCOMPLETE", event_id)
                       if state == "TERMINAL_PENDING_PUBLICATION" else
                       (state, slots, previous, next_at, missing, reason, event_id)
                       for state, slots, previous, next_at, missing, reason, event_id in updates]
        counts = dict(Counter(b["status"] for b in books))
        stats = {"contract": RAW_CONTRACT, "sidecar": repo.path.name, "expected_events": len(events),
                 "expected_tokens": len(books), "book_status_counts": counts, "followup_lookups": lookups,
                 "status": "FAILED" if incomplete else "PUBLISHED", "incomplete": incomplete,
                 "full_books": counts.get("FULL", 0), "parent_success_required": True}
        stats["trusted_full_books"] = sum(b["status"] == "FULL" and b["point_in_time_identity_valid"] for b in books)
        stats["metadata_status_counts"] = dict(Counter(e["metadata_status"] for e in events))
        stats["discovery_only_events"] = sum(e["lifecycle_state"] == "DISCOVERY_ONLY" for e in events)
        stats["required_events"] = len(events) - stats["discovery_only_events"]
        stats["registry_reclassifications"] = len(corrections)
        stats["terminal_publication_checks"] = terminal_checks
        stats["event_denominator_basis"] = "all_published_metadata_rows; required_events excludes explicit child discovery"
        cycle = {"run_id": run_id, "component_run_id": uuid4().hex, "contract": RAW_CONTRACT,
                 "parent_filename": self.parent.path.name, "config_hash": self.config.config_hash,
                 "source_digest": self.config.trading.strategy_source_digest, "job_name": self.config.job_name,
                 "reference_at": reference, "published_at": iso_utc(), "status": stats["status"],
                 "expected_events": len(events), "expected_tokens": len(books), "stats_json": canonical_json(stats)}
        with repo.transaction() as c:
            for table, rows in (("raw_payloads", payloads), ("raw_events", events), ("raw_books", books)):
                for row in rows:
                    repo.insert(c, table, row)
            for update in updates:
                c.execute("UPDATE raw_tracked_events SET state=?,slots_json=?,last_run_id=?,next_attempt_at=?,missing_count=?,terminal_reason=? WHERE event_id=?", update)
            repo.insert(c, "raw_cycles", cycle)
        return stats
