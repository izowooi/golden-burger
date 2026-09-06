"""Accountless REST reads, independent of main/config and all trading clients.

Only the frozen official hosts below are reachable; POST /books is a public
read. No redirects, proxies/netrc, retries, or alternative-host fallback. Raw
JSON decimal literals are kept as strings to avoid rounding economic evidence.
A failed census returns no events, but all received pages remain in the sink.
Sink exceptions propagate: unavailable durable evidence is never a success.

Timeouts bound connect/read inactivity; streaming checks the cycle/15-second
attempt deadline between single-read chunks (or bytes on older urllib3).
This is cooperative requests I/O, not a hard real-time DNS/header SLA; the
deployment runner must independently measure its whole-invocation deadline.
"""
from __future__ import annotations

import base64
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import re
import time
from uuid import uuid4

import requests


HOSTS = {"gamma": "https://gamma-api.polymarket.com", "clob": "https://clob.polymarket.com"}
FAMILY_TAGS = {"soccer": 100350, "mlb": 100381, "nba": 745, "nfl": 450, "nhl": 899}
MAX_BODY_BYTES = 32 * 1024 * 1024
ATTEMPT_SECONDS = 15.0


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _reject_json_constant(value):
    raise ValueError("nonfinite_json")


def _positive_int(value, key, ceiling):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
        raise ValueError(f"{key} must be an integer in [1, {ceiling}]")
    return value


class PublicClients:
    def __init__(self, config: Mapping, budget, receipt_sink):
        if not isinstance(config, Mapping):
            raise TypeError("config must be a Mapping")
        families = config.get("sport_families")
        if (not isinstance(families, (list, tuple)) or not families
                or any(not isinstance(f, str) or f not in FAMILY_TAGS for f in families)
                or len(set(families)) != len(families)):
            raise ValueError("sport_families must contain distinct supported families")
        self.families = tuple(families)
        self.gates = {}
        for key in ("min_liquidity", "min_volume"):
            value = config.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a finite nonnegative number")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{key} must be a finite nonnegative number")
            self.gates[key] = value
        self.page_size = _positive_int(config.get("page_size"), "page_size", 500)
        self.max_pages = _positive_int(config.get("max_pages_per_family"), "max_pages_per_family", 10000)
        self.book_batch_limit = _positive_int(config.get("book_batch_limit"), "book_batch_limit", 500)
        if not callable(getattr(budget, "require", None)) or not callable(receipt_sink):
            raise TypeError("budget.require and receipt_sink must be callable")
        self.budget, self.receipt_sink = budget, receipt_sink
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"Accept": "application/json", "Accept-Encoding": "identity",
                                     "User-Agent": "GoldenGuava-Research/1.0"})
        self._sports = None
        self._sports_request_id = None
        self._sports_result = None
        self._denied = False
        self._closed = False

    def close(self):
        if not self._closed:
            self.session.close()
            self._closed = True

    def _remaining(self, deadline):
        try:
            remaining = self.budget.require()
        except Exception as exc:
            raise TimeoutError("cycle budget unavailable") from exc
        if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
            raise TimeoutError("budget.require must return remaining seconds")
        if not math.isfinite(remaining):
            raise TimeoutError("budget.require must return finite seconds")
        remaining = min(float(remaining), deadline - time.monotonic())
        if not math.isfinite(remaining) or remaining <= 0:
            raise TimeoutError("request deadline exhausted")
        return remaining

    def _request(self, source, method, path, *, params=None, body=None):
        allowed = (
            source == "gamma" and method == "GET"
            and (path in {"/events/keyset", "/sports", "/markets"}
                 or re.fullmatch(r"/events/[0-9]+", path))
        ) or (
            source == "clob" and ((method == "POST" and path == "/books")
                or (method == "GET" and (path == "/markets" or re.fullmatch(r"/markets/[A-Za-z0-9_-]+", path))))
        )
        if not allowed:
            raise ValueError("public REST endpoint not allowed")
        if self._closed:
            raise RuntimeError("public clients are closed")
        receipt = {"request_id": str(uuid4()), "source": source, "method": method,
                   "path": path, "params": deepcopy(params or {}), "started_at": _utcnow(),
                   "received_at": None, "status": None, "error_type": None, "http_attempted": False}
        if body is not None:
            receipt["params"]["body"] = deepcopy(body)
        response, payload, chunks = None, None, bytearray()
        status = "ERROR"
        complete = False
        deadline = time.monotonic() + ATTEMPT_SECONDS
        try:
            if self._denied:
                status = "DENIED"
                raise PermissionError("access denial latched for this client")
            remaining = self._remaining(deadline)
            kwargs = {"params": params, "stream": True, "allow_redirects": False,
                      "timeout": (min(2.0, remaining / 4), min(5.0, remaining / 4))}
            if body is not None:
                kwargs["json"] = body
            receipt["http_attempted"] = True
            response = self.session.request(method, HOSTS[source] + path, **kwargs)
            receipt["status"] = response.status_code
            receipt["response_headers"] = {key: response.headers[key] for key in
                ("Date", "Content-Type", "Content-Encoding", "Retry-After") if key in response.headers}
            if response.status_code in {403, 451}:
                self._denied = True
                status = "DENIED"
            elif 300 <= response.status_code < 400:
                status = "REDIRECT_REFUSED"
            elif response.status_code != 200:
                status = "HTTP_ERROR"
            self._remaining(deadline)
            if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                raise ValueError("unexpected_content_encoding")
            for chunk in self._stream_chunks(response, deadline):
                self._remaining(deadline)
                if chunk:
                    chunks.extend(chunk)
                    if len(chunks) > MAX_BODY_BYTES:
                        raise ValueError("response_body_limit_exceeded")
            complete = True
            receipt["received_at"] = _utcnow()
            self._remaining(deadline)
            try:
                payload = json.loads(chunks, parse_float=str, parse_constant=_reject_json_constant)
            except (ValueError, UnicodeError):
                if response.status_code == 200:
                    raise ValueError("invalid_json_response") from None
            self._remaining(deadline)
            if response.status_code == 200:
                if not isinstance(payload, (dict, list)):
                    raise ValueError("JSON object or array required")
                status = "OK"
            else:
                receipt["error_type"] = f"HTTP_{response.status_code}"
        except (TimeoutError, requests.Timeout) as exc:
            status = "TIMEOUT"
            receipt["error_type"] = type(exc).__name__
        except (requests.RequestException, ValueError, PermissionError) as exc:
            receipt["error_type"] = type(exc).__name__
            receipt["error_detail"] = str(exc) if isinstance(exc, (ValueError, PermissionError)) else "public_transport_error"
        finally:
            if response is not None:
                try:
                    response.close()
                except (OSError, requests.RequestException) as exc:
                    status = "ERROR"
                    receipt["error_type"] = type(exc).__name__
            if receipt["received_at"] is None:
                receipt["received_at"] = _utcnow()
            if payload is None and chunks:
                payload = {"body_base64": base64.b64encode(chunks).decode("ascii"),
                           "complete": complete, "encoding": "raw_response_bytes"}
            receipt["result_status"] = status
            # Deliberately outside every catch: do not swallow evidence failure.
            self.receipt_sink(receipt, payload)
        return {"status": status, "raw": payload, "request_id": receipt["request_id"],
                "started_at": receipt["started_at"],
                "observed_at": receipt["received_at"], "error_type": receipt["error_type"]}

    def _stream_chunks(self, response, deadline):
        # read1 performs at most one underlying read instead of waiting for a
        # full chunk. Disable compression so a decoder cannot internally loop
        # over a trickle before returning control to the budget check.
        read1 = getattr(getattr(response, "raw", None), "read1", None)
        if callable(read1):
            while True:
                self._remaining(deadline)
                chunk = read1(64 * 1024, decode_content=False)
                self._remaining(deadline)
                if not chunk:
                    return
                yield chunk
        else:
            # Older urllib3 has no read1. A larger iter_content chunk could
            # hide unlimited progress-reset slow reads from our deadline.
            yield from response.iter_content(chunk_size=1)

    def _validate_family(self, family):
        if not isinstance(family, str) or family not in self.families:
            raise ValueError("unsupported or unconfigured sport family")

    def _load_sports(self):
        """One durable registry attempt per client/cycle, even without census.

        Cache failures as failures too: another caller must not silently retry
        the same registry request or pretend an unavailable registry is empty.
        The cache never aliases the raw response retained by receipt_sink.
        """
        cached = self._sports_result is not None
        if not cached:
            result = self._request("gamma", "GET", "/sports")
            if result["status"] == "OK":
                if not isinstance(result["raw"], list) or any(not isinstance(s, dict) for s in result["raw"]):
                    result = {**result, "status": "INVALID", "error_type": "invalid_sports_payload"}
                else:
                    self._sports = deepcopy(result["raw"])
            self._sports_request_id = result["request_id"]
            self._sports_result = {k: v for k, v in result.items() if k != "raw"}
        return {**self._sports_result, "cached": cached}

    def _enrich_event(self, raw, family, receipt):
        """Same exact source join for census and explicit-family follow-up.

        This is metadata enrichment, not league acceptance. Unknown/ambiguous
        joins remain explicit for identity.py to reject; embedded source sport
        is never relabelled to the requested family. Raw HTTP evidence is intact.
        """
        if "_guava" in raw:
            raise ValueError("reserved_enrichment_key_collision")
        event = deepcopy(raw)
        event["_guava"] = {"request_id": receipt["request_id"],
                           "observed_at": receipt["observed_at"],
                           "sports_request_id": self._sports_request_id,
                           "sport_enrichment": "SOURCE_EMBEDDED"}
        if not isinstance(event.get("sport"), dict) or not event["sport"]:
            series = event.get("series")
            series_ids = {str(s.get("id")) for s in series if isinstance(s, dict)} if isinstance(series, list) else set()
            matches = [s for s in self._sports if str(s.get("series")) in series_ids]
            if not matches and family != "soccer":
                # The sport registry points to a root series, while an
                # individual game may belong to its semantic season.
                season = str(event.get("seriesSlug") or "")
                tags = {str(t.get("id")) for t in event.get("tags", []) if isinstance(t, dict)} if isinstance(event.get("tags"), list) else set()
                if re.fullmatch(re.escape(family) + r"-[0-9]{4}", season) and str(FAMILY_TAGS[family]) in tags:
                    matches = [s for s in self._sports if s.get("sport") == family]
            if len(matches) == 1:
                event["_guava"]["original_sport"] = deepcopy(event.get("sport"))
                event["sport"] = deepcopy(matches[0])
                event["_guava"]["sport_enrichment"] = (
                    "EXACT_SERIES_JOIN" if str(matches[0].get("series")) in series_ids
                    else "SEMANTIC_SEASON_JOIN"
                )
            else:
                event["_guava"]["sport_enrichment"] = "MISSING_OR_AMBIGUOUS"
        return event

    def fetch_events(self, family):
        self._validate_family(family)
        att = {"sport_family": family, "status": "FAILED", "cursor_complete": False,
               "started_at": _utcnow(), "completed_at": None, "pages": 0,
               "raw_event_count": 0, "event_count": 0, "duplicate_event_count": 0,
               "request_ids": [], "error_type": None}

        def failed(reason):
            att.update(error_type=reason, completed_at=_utcnow())
            return [], att

        registry = self._load_sports()
        if not registry["cached"]:
            att["request_ids"].append(registry["request_id"])
        att["sports_request_id"] = self._sports_request_id
        if registry["status"] != "OK":
            return failed(registry["error_type"] or registry["status"])
        params = {"closed": "false", "live": "true", "tag_id": FAMILY_TAGS[family],
                  "related_tags": "false", "liquidity_min": self.gates["min_liquidity"],
                  "volume_min": self.gates["min_volume"], "limit": self.page_size}
        att["params"] = deepcopy(params)
        events, seen_ids, seen_cursors = [], set(), set()
        for _ in range(self.max_pages):
            result = self._request("gamma", "GET", "/events/keyset", params=deepcopy(params))
            att["request_ids"].append(result["request_id"])
            att["pages"] += 1
            if result["status"] != "OK":
                return failed(result["error_type"] or result["status"])
            payload = result["raw"]
            if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
                return failed("invalid_keyset_payload")
            # Gamma documents omission on the final short page. Never infer
            # completion when a full page is missing the required continuation.
            if "next_cursor" not in payload and len(payload["events"]) >= self.page_size:
                return failed("missing_cursor_on_full_page")
            if "next_cursor" in payload and not (payload["next_cursor"] is None or isinstance(payload["next_cursor"], str)):
                return failed("missing_or_invalid_next_cursor")
            for raw in payload["events"]:
                if not isinstance(raw, dict) or isinstance(raw.get("id"), bool) or not isinstance(raw.get("id"), (str, int)) or not str(raw["id"]).strip():
                    return failed("invalid_event_identity")
                att["raw_event_count"] += 1
                key = str(raw["id"])
                if key in seen_ids:
                    att["duplicate_event_count"] += 1
                    continue  # every duplicate raw remains in its page receipt
                seen_ids.add(key)
                try:
                    event = self._enrich_event(raw, family, result)
                except ValueError as error:
                    return failed(str(error))
                events.append(event)
            cursor = payload.get("next_cursor")
            if cursor in (None, ""):
                att.update(status="SUCCESS", cursor_complete=True, completed_at=_utcnow(), event_count=len(events),
                    terminal_basis="OMITTED_CURSOR_SHORT_PAGE" if "next_cursor" not in payload else "EXPLICIT_TERMINAL_CURSOR")
                return events, att
            if cursor in seen_cursors:
                return failed("repeated_cursor")
            if not payload["events"]:
                return failed("empty_nonterminal_page")
            seen_cursors.add(cursor)
            params["after_cursor"] = cursor
        return failed("max_pages_exceeded")

    def fetch_event(self, event_id, family=None):
        """Read a tracked numeric Gamma event, including its postgame markets.

        Default family=None preserves the direct, unmodified response contract.
        An explicit configured family loads/reuses this cycle's /sports registry
        and applies the SAME exact enrichment as census, including after entry
        collection ends. No cached event body, new census, or URL fallback is
        used. Registry failure prevents event HTTP and returns raw=None with
        failure_phase=sports_registry; its request_id belongs to that failure.
        OK attests exact event identity and successful registry retrieval when
        requested, not league eligibility or a verified sporting final.
        """
        if (isinstance(event_id, bool) or not isinstance(event_id, (str, int))
                or not re.fullmatch(r"[0-9]+", str(event_id))):
            raise ValueError("numeric Gamma event identifier required")
        if family is not None:
            self._validate_family(family)
            registry = self._load_sports()
            if registry["status"] != "OK":
                return {k: v for k, v in registry.items() if k != "cached"} | {
                    "raw": None, "failure_phase": "sports_registry",
                    "sports_request_id": registry["request_id"],
                }
        event_id = str(event_id)
        result = self._request("gamma", "GET", "/events/" + event_id)
        if result["status"] == "OK":
            raw = result["raw"]
            if (not isinstance(raw, dict) or isinstance(raw.get("id"), bool)
                    or not isinstance(raw.get("id"), (str, int))
                    or str(raw["id"]) != event_id or "next_cursor" in raw):
                result.update(status="INVALID", error_type="event_identity_or_envelope_mismatch")
            elif family is not None:
                try:
                    result = {**result, "raw": self._enrich_event(raw, family, result)}
                except ValueError as error:
                    result.update(status="INVALID", error_type=str(error))
        return result

    def fetch_markets(self, condition_ids):
        """Read exact condition IDs across both closed branches, fail closed.

        Gamma /markets is an offset-paginated array endpoint, not the event
        keyset protocol. Each branch/page has its own durable raw receipt.
        ``request_id``/``observed_at`` refer to the LAST request, not an atomic
        multi-market snapshot. ``market_receipts`` link selected bodies to their
        actual PIT; ``observations`` retain both versions when a market closes
        between requests. Latest observed body wins only within this invocation.

        Any failed/incomplete branch, duplicate page, unexpected identity, or
        missing requested condition returns markets=[] and a non-OK status.
        Received partial evidence remains in observations and the receipt sink.
        Existing book_batch_limit bounds ID batches; max_pages_per_family bounds
        pages per closed branch. No scan of the broader market universe occurs.
        """
        if (not isinstance(condition_ids, (list, tuple)) or not condition_ids
                or any(not isinstance(c, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", c)
                       for c in condition_ids)):
            raise ValueError("nonempty list of public condition identifiers required")
        ids = list(dict.fromkeys(condition_ids))
        result = {"markets": [], "request_id": None, "observed_at": None, "request_ids": [],
                  "status": "ERROR", "complete": False, "error_type": None,
                  "market_receipts": {}, "observations": [], "missing_condition_ids": ids[:],
                  "changed_condition_ids": [], "point_in_time_basis": "PER_REQUEST_NOT_ATOMIC"}
        selected, changed = {}, set()

        def failed(status, error):
            result.update(status=status, error_type=error, markets=[], complete=False,
                          missing_condition_ids=[c for c in ids if c not in selected],
                          changed_condition_ids=sorted(changed))
            return result

        for start in range(0, len(ids), self.book_batch_limit):
            batch = ids[start:start + self.book_batch_limit]
            for closed in (False, True):
                seen = set()
                for page in range(self.max_pages):
                    params = {"condition_ids": batch[:], "closed": str(closed).lower(),
                              "limit": self.page_size, "offset": page * self.page_size}
                    response = self._request("gamma", "GET", "/markets", params=params)
                    result.update(request_id=response["request_id"], observed_at=response["observed_at"])
                    result["request_ids"].append(response["request_id"])
                    if response["status"] != "OK":
                        return failed(response["status"], response["error_type"] or response["status"])
                    payload = response["raw"]
                    if (not isinstance(payload, list) or len(payload) > self.page_size
                            or any(not isinstance(m, dict) for m in payload)):
                        return failed("INVALID", "invalid_markets_array_or_page_size")
                    for raw in payload:
                        condition = raw.get("conditionId", raw.get("condition_id"))
                        if (not isinstance(condition, str) or condition not in batch
                                or ("conditionId" in raw and "condition_id" in raw
                                    and raw["conditionId"] != raw["condition_id"])):
                            return failed("INVALID", "market_condition_mismatch")
                        if "closed" in raw and raw["closed"] is not closed:
                            return failed("INVALID", "market_closed_branch_mismatch")
                        if condition in seen:
                            return failed("INVALID", "repeated_market_identity_or_page")
                        seen.add(condition)
                        provenance = {"request_id": response["request_id"],
                                      "observed_at": response["observed_at"],
                                      "closed_filter": closed, "offset": params["offset"]}
                        result["observations"].append({"condition_id": condition, "raw": deepcopy(raw), **provenance})
                        if condition in selected and selected[condition] != raw:
                            changed.add(condition)
                        selected[condition] = raw
                        result["market_receipts"][condition] = provenance
                    # A short page proves termination. Alternatively, all IDs
                    # in this bounded exact-ID query have already been seen;
                    # there can be no further distinct requested condition.
                    if len(payload) < self.page_size or seen == set(batch):
                        break
                else:
                    return failed("INCOMPLETE", "max_market_pages_exceeded")
        missing = [c for c in ids if c not in selected]
        if missing:
            return failed("MISSING", "missing_requested_conditions")
        result.update(markets=[selected[c] for c in ids], status="OK", complete=True,
                      missing_condition_ids=[], changed_condition_ids=sorted(changed))
        return result

    def fetch_books(self, token_ids):
        if isinstance(token_ids, (str, bytes)):
            raise ValueError("token_ids must be a sequence, not a string")
        ids = list(dict.fromkeys(token_ids))
        if any(not isinstance(t, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", t) for t in ids):
            raise ValueError("nonempty public token identifiers required")
        books = {}
        for start in range(0, len(ids), self.book_batch_limit):
            batch = ids[start:start + self.book_batch_limit]
            result = self._request("clob", "POST", "/books", body=[{"token_id": t} for t in batch])
            if result["status"] != "OK":
                for token in batch:
                    books[token] = {**result, "raw": None}
                continue
            payload = result["raw"]
            if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
                for token in batch:
                    books[token] = {**result, "raw": None, "status": "INVALID", "error_type": "invalid_books_payload"}
                continue
            for token in batch:
                matches = [row for row in payload if str(row.get("asset_id", "")) == token]
                if len(matches) == 1:
                    raw = matches[0]
                    valid = isinstance(raw.get("bids"), list) and isinstance(raw.get("asks"), list)
                    books[token] = {**result, "raw": raw, "status": "OK" if valid else "INVALID",
                                    "error_type": None if valid else "missing_book_sides"}
                else:
                    books[token] = {**result, "raw": None, "status": "MISSING" if not matches else "INVALID",
                                    "error_type": "missing_expected_token" if not matches else "duplicate_token_books"}
        return books

    def fetch_resolution(self, condition_id):
        if not isinstance(condition_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", condition_id):
            raise ValueError("nonempty public condition identifier required")
        result = self._request("clob", "GET", "/markets/" + condition_id)
        if result["status"] == "OK":
            raw = result["raw"]
            if not isinstance(raw, dict) or raw.get("condition_id") != condition_id:
                result.update(status="INVALID", error_type="resolution_condition_mismatch")
        # OK means exact HTTP metadata, never terminal/one-hot payout proof.
        return result
