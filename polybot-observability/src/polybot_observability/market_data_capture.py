"""Explicit public acquisition capture, durable ACK, and verified readback.

No authentication header, cookie, private request kwargs or arbitrary SDK object
state is inspected or serialized. There is no quote cache or request coalescing:
each upstream invocation remains an independent observation. Streaming transports
call capture_public_bytes only after their existing bounded body-reading loop.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from functools import wraps
import hashlib
import inspect
import json
import math
import os
import re
from threading import RLock
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from .market_data_refs import configured_references
from .market_data_store import Observation, validate_payloads


class PublicCaptureError(RuntimeError):
    """Public evidence was not durably stored/read; never use an inline fallback."""


_ID = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_PRIVATE_QUERY = re.compile(
    r"(?:user|wallet|address|account|owner|funder|auth|cookie|credential|"
    r"private|secret|password|passphrase|signature|api[_-]?key|access[_-]?token|session)", re.I
)
_GAMMA_COLLECTIONS = frozenset({"/markets", "/markets/keyset", "/events", "/events/keyset", "/tags", "/sports", "/series"})
_GAMMA_RESOURCE = re.compile(r"/(?:markets|events|tags|series)/(?:slug/)?[A-Za-z0-9_-]{1,256}(?:/tags)?\Z")
_CLOB_GET = frozenset({
    "/book", "/price", "/midpoint", "/spread", "/last-trade-price",
    "/tick-size", "/neg-risk", "/fee-rate", "/prices-history", "/markets",
    "/sampling-markets", "/simplified-markets", "/sampling-simplified-markets",
})
_CLOB_POST = frozenset({"/books", "/prices", "/midpoints", "/spreads", "/last-trades-prices", "/batch-prices-history"})
_PUBLIC_REQUEST_KEYS = frozenset({
    "token_id", "token_ids", "market", "markets", "condition_id", "condition_ids", "id", "event_id",
    "side", "limit", "offset", "after_cursor", "next_cursor", "active", "closed", "include_tag",
    "tag_id", "tag_slug", "related_tags", "series_id", "slug", "order", "ascending",
    "liquidity_num_min", "volume_num_min", "liquidity_min", "volume_min", "start_time_min", "start_time_max",
    "end_date_min", "end_date_max", "startTs", "endTs", "start_ts", "end_ts", "fidelity", "interval",
})
_BOOK_FIELDS = ("market", "asset_id", "timestamp", "bids", "asks", "min_order_size", "neg_risk", "tick_size", "last_trade_price", "hash")
_DTO_FIELDS = {"OrderBookSummary": _BOOK_FIELDS, "OrderSummary": ("price", "size")}
_DTO_MODULES = frozenset({"py_clob_client.clob_types", "py_clob_client_v2.clob_types"})
SDK_PUBLIC_METHODS = {
    "get_order_book": ("GET", "/book", "token_id"),
    "get_order_books": ("POST", "/books", "tokens"),
    "get_midpoint": ("GET", "/midpoint", "token_id"),
    "get_midpoints": ("POST", "/midpoints", "tokens"),
    "get_price": ("GET", "/price", "token_id"),
    "get_prices": ("POST", "/prices", "tokens"),
    "get_spread": ("GET", "/spread", "token_id"),
    "get_spreads": ("POST", "/spreads", "tokens"),
    "get_last_trade_price": ("GET", "/last-trade-price", "token_id"),
    "get_last_trades_prices": ("POST", "/last-trades-prices", "tokens"),
    "get_market": ("GET", "/markets", "condition_id"),
    "get_clob_market_info": ("GET", "/clob-markets", "condition_id"),
    "get_markets": ("GET", "/markets", "next_cursor"),
    "get_sampling_markets": ("GET", "/sampling-markets", "next_cursor"),
    "get_simplified_markets": ("GET", "/simplified-markets", "next_cursor"),
    "get_sampling_simplified_markets": ("GET", "/sampling-simplified-markets", "next_cursor"),
    "get_tick_size": ("GET", "/tick-size", "token_id"),
    "get_neg_risk": ("GET", "/neg-risk", "token_id"),
    "get_fee_rate_bps": ("GET", "/fee-rate", "token_id"),
}
_INSTALL_LOCK = RLock()


def _serialized_install(function):
    @wraps(function)
    def install(*args, **kwargs):
        try:
            with _INSTALL_LOCK:
                return function(*args, **kwargs)
        except PublicCaptureError:
            raise
        except Exception as error:
            raise PublicCaptureError("public capture adapter installation failed") from error
    return install


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _configured():
    return any(os.environ.get(key) for key in ("PUBLIC_MARKET_DATA_SOCKET", "PUBLIC_MARKET_DATA_DB")) or os.environ.get("PUBLIC_MARKET_DATA_REQUIRED") not in (None, "", "0")


def _query_items(params):
    if params is None:
        return []
    if isinstance(params, Mapping):
        return list(params.items())
    if isinstance(params, (list, tuple)):
        return list(params)
    if isinstance(params, str):
        return parse_qsl(params, keep_blank_values=True)
    raise ValueError("unsupported public request parameter shape")


def public_endpoint(method, url, *, params=None, body=None):
    """Return host/path only for reviewed public endpoints and public queries."""
    try:
        parsed = urlsplit(url)
        if parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.port not in (None, 443):
            return None
        method = method.upper()
        if method == "GET" and body is not None:
            return None
        query = [*parse_qsl(parsed.query, keep_blank_values=True), *_query_items(params)]
        if any(not isinstance(key, str) or _PRIVATE_QUERY.search(key) for key, _ in query):
            return None
        if isinstance(body, Mapping) and any(_PRIVATE_QUERY.search(str(key)) for key in body):
            return None
        if parsed.scheme == "https" and parsed.hostname == "gamma-api.polymarket.com" and method == "GET":
            return (parsed.hostname, parsed.path) if parsed.path in _GAMMA_COLLECTIONS or _GAMMA_RESOURCE.fullmatch(parsed.path) else None
        if parsed.scheme == "https" and parsed.hostname == "clob.polymarket.com":
            if method == "GET" and (parsed.path in _CLOB_GET or re.fullmatch(r"/(?:markets|clob-markets|markets-by-token)/[A-Za-z0-9_-]{1,256}", parsed.path)):
                return parsed.hostname, parsed.path
            if method == "POST" and parsed.path in _CLOB_POST:
                if parsed.path == "/batch-prices-history":
                    valid = isinstance(body, Mapping) and set(body) <= {"markets", "start_ts", "end_ts", "startTs", "endTs", "fidelity", "interval"}
                else:
                    valid = isinstance(body, (list, tuple)) and all(isinstance(item, Mapping) and set(item) <= {"token_id", "side"} and "token_id" in item for item in body)
                return (parsed.hostname, parsed.path) if valid else None
        if method == "WS_RECV" and parsed.scheme == "wss" and (parsed.hostname, parsed.path) in {
            ("sports-api.polymarket.com", "/ws"), ("ws-subscriptions-clob.polymarket.com", "/ws/market")
        }:
            return parsed.hostname, parsed.path
    except (ValueError, TypeError):
        return None
    return None


def _codec(references):
    if references is None and not _configured():
        return None
    codec = references or configured_references()
    if codec.writer is None or codec.reader is None or not callable(getattr(codec.writer, "append_observations", None)):
        raise PublicCaptureError("shared public capture requires a writer, reader and observation index")
    return codec


def remaining_budget(budget):
    """Bound shared I/O by the caller's existing cooperative work budget."""
    if budget is None or getattr(budget, "enforce_deadline", None) is False:
        return None
    if callable(budget):
        value = budget()
    elif callable(getattr(budget, "require_commit", None)):
        value = budget.require_commit()
    elif hasattr(budget, "cooperative_remaining_seconds"):
        value = budget.cooperative_remaining_seconds
    elif hasattr(budget, "hard_remaining_seconds"):
        value = budget.hard_remaining_seconds
    elif hasattr(budget, "cycle_remaining_seconds"):
        value = budget.cycle_remaining_seconds
    elif callable(getattr(budget, "remaining_seconds", None)):
        value = budget.remaining_seconds()
    elif hasattr(budget, "cooperative_deadline") and callable(getattr(budget, "monotonic", None)):
        value = budget.cooperative_deadline - budget.monotonic()
    elif callable(getattr(budget, "require", None)):
        value = budget.require()
    else:
        raise PublicCaptureError("public capture received an unsupported cycle budget")
    if value is None:
        return None
    if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0:
        raise PublicCaptureError("cycle budget exhausted during public capture")
    return float(value)


def _io(provider, method, values, budget):
    available = remaining_budget(budget)
    if available is not None:
        from .market_data_client import StoreClient
        if isinstance(provider, StoreClient):
            # A new bounded client avoids mutating a shared object's timeout.
            provider = StoreClient(provider.socket_path, timeout=min(provider.timeout, available), max_frame_bytes=provider.max_frame_bytes)
    result = getattr(provider, method)(values)
    remaining_budget(budget)
    return result


def _identifier(value):
    return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) and _ID.fullmatch(str(value)) else None


def _ids(method, url, params, body, payload):
    tokens, events = set(), set()
    path = urlsplit(url).path
    items = [*parse_qsl(urlsplit(url).query, keep_blank_values=True), *_query_items(params)]
    for key, value in items:
        if key in {"token_id", "market"} and (ident := _identifier(value)):
            tokens.add(ident)
        if key == "event_id" and (ident := _identifier(value)):
            events.add(ident)
    if isinstance(body, (list, tuple)):
        for item in body:
            if isinstance(item, Mapping) and (ident := _identifier(item.get("token_id"))):
                tokens.add(ident)
    elif isinstance(body, Mapping) and isinstance(body.get("markets"), list):
        tokens.update(v for value in body["markets"] if (v := _identifier(value)))
    # Only known public identifier fields; no recursive traversal of arbitrary
    # SDK state, account objects or payload metadata.
    candidates = [(item, 0) for item in payload] if isinstance(payload, list) else [(payload, 0)]
    for item, depth in candidates:
        if not isinstance(item, Mapping):
            continue
        if ident := _identifier(item.get("asset_id", item.get("assetId"))):
            tokens.add(ident)
        if depth == 0 and (path in {'/events', '/events/keyset'} or path.startswith('/events/')):
            if ident := _identifier(item.get('id')):
                events.add(ident)
        if ident := _identifier(item.get("eventId")):
            events.add(ident)
        for key in ("events", "markets", "data", "books"):
            nested = item.get(key)
            if isinstance(nested, list):
                for entry in nested:
                    if not isinstance(entry, Mapping):
                        continue
                    if key == "events" and (ident := _identifier(entry.get("id"))):
                        events.add(ident)
                    if ident := _identifier(entry.get("eventId")):
                        events.add(ident)
                    if depth < 3:
                        candidates.append((entry, depth + 1))
        values = item.get("clobTokenIds")
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except ValueError:
                values = None
        if isinstance(values, list):
            tokens.update(v for value in values if (v := _identifier(value)))
        market_tokens = item.get("tokens", item.get("t"))
        if isinstance(market_tokens, list):
            for value in market_tokens:
                if isinstance(value, Mapping) and (ident := _identifier(value.get("token_id", value.get("t")))):
                    tokens.add(ident)
    if path.startswith("/markets-by-token/") and (ident := _identifier(path.rsplit("/", 1)[-1])):
        tokens.add(ident)
    if re.fullmatch(r'/events/[0-9]+', path) and (ident := _identifier(path.rsplit("/", 1)[-1])):
        events.add(ident)
    return sorted(tokens), sorted(events)


def capture_public_bytes(raw, *, strategy, method, url, params=None, body=None,
                         request_id=None, run_id=None, started_at=None, received_at=None,
                         http_status=None, complete=True, serialization="http-response-content-bytes",
                         sdk_method=None, budget=None, references=None, trace_payload=None):
    """Consume only bytes read back after the writer and index acknowledged them.

    HTTP bytes mean response.content / the existing transport's decoded body
    bytes, not TLS frames or compressed wire transfer bytes. No GET-by-default
    classification, stale cache or private endpoint fallback is used.
    """
    endpoint = public_endpoint(method, url, params=params, body=body)
    if endpoint is None:
        return raw
    try:
        codec = _codec(references)
        if codec is None:
            return raw
        if not isinstance(raw, bytes):
            raise TypeError("public source body must be bytes")
        validate_payloads([raw])
        received_at = received_at or utc_now()
        request_id = request_id or uuid4().hex
        if run_id is None:
            from .run_audit import current_run_id
            run_id = current_run_id()
        payload = trace_payload
        if payload is None:
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError):
                payload = None
        tokens, events = _ids(method, url, params, body, payload)
        request = {key: value for key, value in [*parse_qsl(urlsplit(url).query, keep_blank_values=True), *_query_items(params)] if key in _PUBLIC_REQUEST_KEYS}
        # Request values are hashed, never arbitrary headers/kwargs or account
        # parameters. A large batch receives one bounded receipt index entry.
        request_hash = hashlib.sha256(json.dumps([request, body], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        metadata = {"host": endpoint[0], "path": endpoint[1], "method": method.upper(),
                    "request_id": request_id, "public_request_sha256": request_hash,
                    "received_at": received_at, "raw_complete": bool(complete),
                    "serialization": serialization, "source_bytes": len(raw),
                    "evidence_scope": "public_acquisition_not_cycle_or_trade_confirmation"}
        for key, value in (("run_id", run_id), ("started_at", started_at), ("http_status", http_status), ("sdk_method", sdk_method)):
            if value is not None:
                metadata[key] = value
        if isinstance(payload, Mapping):
            source_stamp = payload.get("timestamp")
            if type(source_stamp) in (str, int, float) and len(str(source_stamp)) <= 256:
                metadata["source_timestamp_raw"] = str(source_stamp)
            condition = payload.get("condition_id", payload.get("conditionId", payload.get("market")))
            if _identifier(condition):
                metadata["condition_id"] = str(condition)
        source = os.environ.get("PUBLIC_MARKET_DATA_SOURCE", "source-unspecified")
        job = os.environ.get("JOB_NAME", "job-unspecified")
        if not all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_.@/-]{1,200}", v) for v in (source, strategy, job)):
            raise ValueError("invalid public observer identity")
        observer = f"{source}/{strategy}/{job}"
        expected = hashlib.sha256(raw).hexdigest()
        hashes = _io(codec.writer, "put_many", [raw], budget)
        if hashes != [expected]:
            raise ValueError("public capture acknowledgement hash mismatch")
        # One upstream result is ONE observation. Expanding a Gamma census or
        # 500-token midpoint response into per-token copies makes the index
        # larger than the body and overstates physical acquisition counts.
        metadata["identity_index_scope"] = "receipt-with-shared-subject-set"
        metadata["token_count"] = len(tokens)
        metadata["event_count"] = len(events)
        for key, values in (("token_ids", tokens), ("event_ids", events)):
            metadata[key + "_sha256"] = hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()
            if len(values) <= 8:
                metadata[key] = values
        observation = Observation(
            observer, uuid4().hex, received_at,
            "public-sdk-result" if sdk_method else "public-response", expected,
            event_id=events[0] if len(events) == 1 else None,
            token_id=tokens[0] if len(tokens) == 1 else None,
            metadata_json=json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            token_ids=tuple(tokens), event_ids=tuple(events),
        )
        _io(codec.writer, "append_observations", [observation], budget)
        restored = _io(codec.reader, "get_many", [expected], budget)
        if len(restored) != 1 or not isinstance(restored[0], bytes) or hashlib.sha256(restored[0]).hexdigest() != expected:
            raise ValueError("public capture readback integrity failure")
        remaining_budget(budget)
        return restored[0]
    except PublicCaptureError:
        raise
    except Exception as error:
        raise PublicCaptureError("shared public acquisition capture failed") from error


def capture_response(response, *, strategy, method, url, params=None, body=None, budget=None,
                     request_id=None, run_id=None, started_at=None, received_at=None, references=None):
    """Keep the original Response object; its body becomes verified readback."""
    final_url = getattr(response, "url", None)
    if isinstance(final_url, str) and final_url and public_endpoint(method, final_url, params=params, body=body) is None:
        return response
    if public_endpoint(method, url, params=params, body=body) is None or (references is None and not _configured()):
        return response
    raw = response.content
    restored = capture_public_bytes(raw, strategy=strategy, method=method, url=url, params=params, body=body,
        http_status=getattr(response, "status_code", None), budget=budget, request_id=request_id, run_id=run_id,
        started_at=started_at, received_at=received_at, references=references)
    response._content = restored
    return response


@_serialized_install
def install_session_capture(session, *, strategy, budget=None):
    """Install on one owned requests.Session; never patch a module/global pool."""
    if not _configured():
        return session
    marker = getattr(session, "_market_data_session_capture", None)
    if isinstance(marker, dict):
        if marker["strategy"] != strategy:
            raise PublicCaptureError("public HTTP session has a different owner")
        marker["budget"] = budget
        return session
    marker = {"strategy": strategy, "budget": budget}
    original = session.request

    @wraps(original)
    def request(method, url, **kwargs):
        allowed = public_endpoint(method, url, params=kwargs.get("params"), body=kwargs.get("json"))
        if allowed is None or kwargs.get("stream"):
            return original(method, url, **kwargs)
        started = utc_now()
        response = original(method, url, **kwargs)
        return capture_response(response, strategy=strategy, method=method, url=url,
                                params=kwargs.get("params"), body=kwargs.get("json"),
                                started_at=started, received_at=utc_now(), budget=marker["budget"])

    session.request = request
    session._market_data_session_capture = marker
    return session


def _sdk_value(value, types, depth=0):
    if depth > 40:
        raise ValueError("SDK public result exceeds nesting limit")
    if value is None or type(value) in (str, bool, int):
        return ["scalar", value]
    if type(value) is float:
        return ["float", repr(value)]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("SDK public JSON keys must be strings")
        return ["dict", [[key, _sdk_value(item, types, depth + 1)] for key, item in value.items()]]
    if isinstance(value, (list, tuple)):
        return ["tuple" if isinstance(value, tuple) else "list", [_sdk_value(item, types, depth + 1) for item in value]]
    cls = type(value)
    if cls.__module__ not in _DTO_MODULES or cls.__name__ not in _DTO_FIELDS:
        raise TypeError("unsupported public SDK DTO; arbitrary object state is forbidden")
    key = cls.__module__ + ":" + cls.__name__
    types[key] = cls
    return ["sdk", key, [[field, _sdk_value(getattr(value, field, None), types, depth + 1)] for field in _DTO_FIELDS[cls.__name__]]]


def _restore_sdk(value, types):
    kind = value[0]
    if kind == "scalar":
        return value[1]
    if kind == "float":
        return float(value[1])
    if kind == "decimal":
        return Decimal(value[1])
    if kind == "dict":
        return {key: _restore_sdk(item, types) for key, item in value[1]}
    if kind in {"list", "tuple"}:
        items = [_restore_sdk(item, types) for item in value[1]]
        return tuple(items) if kind == "tuple" else items
    if kind == "sdk":
        return types[value[1]](**{key: _restore_sdk(item, types) for key, item in value[2]})
    raise ValueError("unsupported SDK public serialization")


def _sdk_request(name, args, kwargs):
    method, path, key = SDK_PUBLIC_METHODS[name]
    value = args[0] if args else kwargs.get("params" if key == "tokens" else key)
    params, body = {}, None
    if key == "tokens":
        body = []
        for item in value or ():
            token = item.get("token_id") if isinstance(item, Mapping) else getattr(item, "token_id", None)
            side = item.get("side") if isinstance(item, Mapping) else getattr(item, "side", None)
            entry = {"token_id": token}
            if side:
                entry["side"] = side
            body.append(entry)
    elif key == "condition_id":
        if not _identifier(value):
            raise ValueError("public SDK market identity is invalid")
        path += "/" + str(value)
    elif value is not None:
        params[key] = value
    if name == "get_price":
        params["side"] = args[1] if len(args) > 1 else kwargs.get("side")
    return method, path, params, body


def _capture_sdk_result(result, *, name, strategy, method, url, params, body,
                        started, received, budget):
    try:
        types = {}
        plain = _plain_json_result(result)
        value = result if plain else {"schema": "public-sdk-result-v1", "value": _sdk_value(result, types)}
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        serialization = "canonical-sdk-json-value-v1-not-http-bytes" if plain else "canonical-sdk-result-v1-not-http-bytes"
        restored = capture_public_bytes(encoded, strategy=strategy, method=method,
            url=url, params=params, body=body,
            started_at=started, received_at=received, serialization=serialization,
            sdk_method=name, budget=budget, trace_payload=result if isinstance(result, (dict, list)) else None)
        decoded = json.loads(restored)
        result = decoded if plain else _restore_sdk(decoded["value"], types)
        remaining_budget(budget)
        return result
    except PublicCaptureError:
        raise
    except Exception as error:
        raise PublicCaptureError("shared public SDK capture failed") from error


def _plain_json_result(value, depth=0):
    """Keep normal SDK JSON compact and directly queryable; no object dumping."""
    if depth > 40:
        raise ValueError("SDK public result exceeds nesting limit")
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _plain_json_result(item, depth + 1) for key, item in value.items())
    if isinstance(value, list):
        return all(_plain_json_result(item, depth + 1) for item in value)
    return False


@_serialized_install
def install_sdk_capture(client, *, strategy, budget=None, host="https://clob.polymarket.com"):
    """Wrap public calls on this SDK instance, including internal metadata reads.

    The SDK client identity and every private method/result remain unchanged.
    Prefer v2 instance _get/_post interception with the endpoint allowlist. That
    catches private SDK helpers' public /markets-by-token and /clob-markets
    reads before their results enter SDK caches. Older SDKs without that pair
    use the explicit public-method fallback. Never install both capture layers.
    Results are canonical SDK readback, not original HTTP response bytes.
    """
    if not _configured():
        return client
    marker = getattr(client, "_market_data_sdk_capture", None)
    if isinstance(marker, dict):
        if marker["strategy"] != strategy:
            raise PublicCaptureError("public SDK client has a different owner")
        marker["budget"] = budget
        return client
    actual_host = getattr(client, "host", host)
    if not isinstance(actual_host, str) or public_endpoint("GET", actual_host.rstrip("/") + "/book") is None:
        return client
    marker = {"strategy": strategy, "budget": budget}

    def transport_wrap(name, original):
        @wraps(original)
        def call(*args, **kwargs):
            method = "GET" if name == "_get" else "POST"
            url = args[0] if args else kwargs.get("endpoint")
            params = kwargs.get("params")
            body = (args[1] if len(args) > 1 else kwargs.get("data")) if method == "POST" else None
            if isinstance(body, (str, bytes)):
                try:
                    body = json.loads(body)
                except (ValueError, UnicodeError):
                    return original(*args, **kwargs)
            if not isinstance(url, str) or public_endpoint(method, url, params=params, body=body) is None:
                return original(*args, **kwargs)
            started = utc_now()
            result = original(*args, **kwargs)
            if not _configured():
                return result
            return _capture_sdk_result(result, name=name, strategy=strategy, method=method,
                url=url, params=params, body=body, started=started, received=utc_now(), budget=marker["budget"])
        return call

    transport_methods = []
    for name in ("_get", "_post"):
        try:
            inspect.getattr_static(client, name)
        except AttributeError:
            continue
        if callable(getattr(client, name)):
            transport_methods.append(name)
    if len(transport_methods) == 2:
        for name in transport_methods:
            setattr(client, name, transport_wrap(name, getattr(client, name)))
        marker["mode"] = "endpoint-allowlisted-sdk-transport"
        client._market_data_sdk_capture = marker
        return client

    def wrap(name, original):
        @wraps(original)
        def call(*args, **kwargs):
            if any(_PRIVATE_QUERY.search(str(key)) for key in kwargs):
                return original(*args, **kwargs)
            started = utc_now()
            result = original(*args, **kwargs)
            received = utc_now()
            if not _configured():
                return result
            try:
                method, path, params, body = _sdk_request(name, args, kwargs)
                return _capture_sdk_result(result, name=name, strategy=strategy, method=method,
                    url=actual_host.rstrip("/") + path, params=params, body=body,
                    started=started, received=received, budget=marker["budget"])
            except PublicCaptureError:
                raise
            except Exception as error:
                raise PublicCaptureError("shared public SDK capture failed") from error
        return call

    for name in SDK_PUBLIC_METHODS:
        try:
            inspect.getattr_static(client, name)
        except AttributeError:
            continue
        original = getattr(client, name)
        if callable(original):
            setattr(client, name, wrap(name, original))
    client._market_data_sdk_capture = marker
    return client
