"""Every request is intercepted; these tests never open a socket."""
import importlib.util
import json
from pathlib import Path

import pytest
import requests

SPEC = importlib.util.spec_from_file_location(
    "guava_public_clients", Path(__file__).resolve().parents[1] / "src/polybot/public_clients.py"
)
clients = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clients)


class Budget:
    def __init__(self, remaining=30):
        self.remaining = remaining

    def require(self):
        if self.remaining <= 0:
            raise TimeoutError("fixture budget exhausted")
        return self.remaining


class Response:
    def __init__(self, payload, status=200, on_chunk=None):
        self.status_code = status
        self.body = json.dumps(payload).encode()
        self.on_chunk = on_chunk
        self.closed = False
        self.headers = {}

    def iter_content(self, chunk_size):
        assert chunk_size == 1  # cannot hide an unbounded slow stream in a full chunk
        for i in range(0, len(self.body), chunk_size):
            if self.on_chunk:
                self.on_chunk()
            yield self.body[i:i + chunk_size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert kwargs["stream"] is True
        assert kwargs["allow_redirects"] is False
        assert all(0 < t <= 5 for t in kwargs["timeout"])
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real HTTP is forbidden in unit tests")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)


def make(monkeypatch, responses, budget=None, **overrides):
    config = {"sport_families": ["soccer", "mlb", "nba", "nfl", "nhl"],
              "min_liquidity": 5000, "min_volume": 5000,
              "page_size": 500, "max_pages_per_family": 4, "book_batch_limit": 2}
    config.update(overrides)
    session = Session(responses)
    monkeypatch.setattr(clients.requests, "Session", lambda: session)
    receipts = []
    client = clients.PublicClients(config, budget or Budget(), lambda r, p: receipts.append((r, p)))
    return client, session, receipts


def test_lossless_books_missing_token_and_read_only_post(monkeypatch):
    raw = {"asset_id": "11", "timestamp": "1788656400123", "tick_size": "0.001",
           "min_order_size": "5.00", "feeSchedule": {"rate": "0.025"},
           "bids": [{"price": "0.4000", "size": "12.3400"}], "asks": []}
    response = Response([raw])
    client, session, receipts = make(monkeypatch, [response])
    rows = client.fetch_books(["11", "22", "11"])
    assert rows["11"]["status"] == "OK"
    assert rows["11"]["raw"] == raw
    assert rows["22"]["status"] == "MISSING"
    assert rows["22"]["raw"] is None
    assert rows["11"]["request_id"] == rows["22"]["request_id"] == receipts[0][0]["request_id"]
    assert session.calls[0][:2] == ("POST", "https://clob.polymarket.com/books")
    assert session.calls[0][2]["json"] == [{"token_id": "11"}, {"token_id": "22"}]
    assert receipts[0][1] == [raw]
    assert response.closed
    client.close()
    assert session.closed


@pytest.mark.parametrize("status", [403, 451, 302, 429, 500])
def test_denial_redirect_and_errors_never_retry(monkeypatch, status):
    client, session, receipts = make(monkeypatch, [Response({"error": "denied"}, status)])
    rows = client.fetch_books(["11", "22"])
    assert all(row["status"] != "OK" for row in rows.values())
    assert len(session.calls) == len(receipts) == 1
    assert receipts[0][0]["status"] == status
    assert receipts[0][0]["error_type"]


def test_expired_budget_no_http_and_explicit_attempts(monkeypatch):
    client, session, receipts = make(monkeypatch, [], Budget(0))
    rows = client.fetch_books(["11", "22"])
    assert not session.calls
    assert len(rows) == 2
    assert all(row["status"] == "TIMEOUT" for row in rows.values())
    assert receipts[0][0]["error_type"]


def test_stream_deadline_and_response_cleanup(monkeypatch):
    budget = Budget(2)
    response = Response([{"asset_id": "11"}], on_chunk=lambda: setattr(budget, "remaining", budget.remaining - 1))
    client, session, receipts = make(monkeypatch, [response], budget)
    assert client.fetch_books(["11"])["11"]["status"] == "TIMEOUT"
    assert response.closed
    assert len(session.calls) == 1
    assert receipts[0][0]["error_type"]


def test_sweep_complete_enrichment_preserves_payload_and_params(monkeypatch):
    sport = {"id": 8, "sport": "mlb", "name": "MLB", "primaryTagId": 100381, "series": "3"}
    event = {"id": "1", "series": [{"id": "3"}], "feeSchedule": {"rate": "0.0200"}}
    client, session, receipts = make(monkeypatch, [Response([sport]),
        Response({"events": [event], "next_cursor": "next"}),
        Response({"events": [], "next_cursor": None})])
    events, attestation = client.fetch_events("mlb")
    assert attestation["status"] == "SUCCESS"
    assert attestation["cursor_complete"] is True
    assert attestation["pages"] == 2
    assert events[0]["sport"] == sport
    assert receipts[1][1]["events"][0] == event
    params = session.calls[1][2]["params"]
    assert params == {"closed": "false", "live": "true", "tag_id": 100381,
                      "related_tags": "false", "liquidity_min": 5000,
                      "volume_min": 5000, "limit": 500}
    assert session.calls[2][2]["params"]["after_cursor"] == "next"


@pytest.mark.parametrize("pages,cap", [
    ([{"events": [{"id": "1"}], "next_cursor": "loop"}] * 2, 4),
    ([{"events": [{"id": "1"}], "next_cursor": "more"}], 1),
    ([{"events": []}], 4),
    ([{"events": [], "next_cursor": 42}], 4),
])
def test_incomplete_sweep_cannot_publish_partial_success(monkeypatch, pages, cap):
    client, session, receipts = make(monkeypatch, [Response([]), *map(Response, pages)], max_pages_per_family=cap)
    events, attestation = client.fetch_events("mlb")
    assert events == []
    assert attestation["status"] == "FAILED"
    assert attestation["cursor_complete"] is False
    assert attestation["error_type"]


def test_resolution_is_exact_public_market_not_price_inference(monkeypatch):
    raw = {"condition_id": "0xabc", "closed": True,
           "tokens": [{"token_id": "11", "winner": True}, {"token_id": "22", "winner": False}],
           "description": "Cancellation and void rules", "minimum_tick_size": "0.01"}
    client, session, receipts = make(monkeypatch, [Response(raw)])
    row = client.fetch_resolution("0xabc")
    assert row["status"] == "OK" and row["raw"] == raw
    assert session.calls[0][:2] == ("GET", "https://clob.polymarket.com/markets/0xabc")
    assert receipts[0][0]["path"] == "/markets/0xabc"


@pytest.mark.parametrize("path", ["/order", "/orders", "/auth/api-key", "/books/../order", "https://evil.invalid/books"])
def test_allowlist_rejects_order_and_other_paths_before_http(monkeypatch, path):
    client, session, receipts = make(monkeypatch, [])
    with pytest.raises(ValueError):
        client._request("clob", "POST", path)
    assert not session.calls


def test_receipt_sink_failure_propagates(monkeypatch):
    client, session, receipts = make(monkeypatch, [Response([])])
    def failed_sink(receipt, payload):
        raise RuntimeError("durable evidence unavailable")
    client.receipt_sink = failed_sink
    with pytest.raises(RuntimeError, match="durable evidence"):
        client.fetch_books(["11"])


def test_connection_timeout_has_one_receipt(monkeypatch):
    client, session, receipts = make(monkeypatch, [requests.Timeout("fixture")])
    assert client.fetch_books(["11"])["11"]["status"] == "TIMEOUT"
    assert len(session.calls) == len(receipts) == 1


@pytest.mark.parametrize("body", [b'not JSON', b'null', b'{"x": NaN}', b'{"x": Infinity}'])
def test_invalid_json_does_not_become_success(monkeypatch, body):
    response = Response({})
    response.body = body
    client, session, receipts = make(monkeypatch, [response])
    assert client.fetch_books(["11"])["11"]["status"] != "OK"
    assert receipts[0][0]["error_type"]
    assert response.closed


def test_numeric_decimal_lexemes_and_unknown_economic_fields_are_preserved(monkeypatch):
    response = Response({})
    response.body = b'[{"asset_id":"11","bids":[{"price":0.123456789012345678901,"size":12.3400}],"asks":[],"feeRate":0.0300,"timestamp":1788656400123,"new_metadata":{"x":"original"}}]'
    client, session, receipts = make(monkeypatch, [response])
    raw = client.fetch_books(["11"])["11"]["raw"]
    assert raw["bids"] == [{"price": "0.123456789012345678901", "size": "12.3400"}]
    assert raw["feeRate"] == "0.0300"
    assert raw["timestamp"] == 1788656400123
    assert raw["new_metadata"] == {"x": "original"}


@pytest.mark.parametrize("payload", [
    [{"asset_id": "11", "asks": [], "bids": []}] * 2,
    [{"asset_id": "11", "asks": []}],
    {"error": "not a batch"},
])
def test_malformed_or_duplicate_books_are_not_fills_or_missing_success(monkeypatch, payload):
    client, session, receipts = make(monkeypatch, [Response(payload)])
    row = client.fetch_books(["11"])["11"]
    assert row["status"] == "INVALID"
    assert row["error_type"]
    assert receipts[0][1] == payload


def test_batch_limit_and_denial_latch_cover_all_expected_tokens(monkeypatch):
    client, session, receipts = make(monkeypatch, [Response({}, 451)], book_batch_limit=1)
    rows = client.fetch_books(["11", "22", "33"])
    assert set(rows) == {"11", "22", "33"}
    assert {r["status"] for r in rows.values()} == {"DENIED"}
    assert len(session.calls) == 1
    assert len(receipts) == 3


def test_one_attempt_cap_independent_of_cycle_budget(monkeypatch):
    now = [0.0]
    response = Response([], on_chunk=lambda: now.__setitem__(0, now[0] + 16))
    monkeypatch.setattr(clients.time, "monotonic", lambda: now[0])
    client, session, receipts = make(monkeypatch, [response], Budget(1000))
    assert client.fetch_books(["11"])["11"]["status"] == "TIMEOUT"
    assert response.closed


@pytest.mark.parametrize("remaining", [float("inf"), float("nan"), False, None])
def test_invalid_budget_return_never_opens_http(monkeypatch, remaining):
    class InvalidBudget:
        def require(self):
            return remaining
    client, session, receipts = make(monkeypatch, [], InvalidBudget())
    assert client.fetch_books(["11"])["11"]["status"] == "TIMEOUT"
    assert not session.calls


def test_payload_body_limit_is_explicit_not_truncated_success(monkeypatch):
    monkeypatch.setattr(clients, "MAX_BODY_BYTES", 5)
    response = Response([{"asset_id": "11"}])
    client, session, receipts = make(monkeypatch, [response])
    assert client.fetch_books(["11"])["11"]["status"] != "OK"
    assert receipts[0][1]["complete"] is False
    assert response.closed


def test_sports_metadata_cache_and_semantic_season_join(monkeypatch):
    sport = {"id": 8, "sport": "mlb", "name": "MLB", "primaryTagId": 100381, "series": "3"}
    event = {"id": "e", "seriesSlug": "mlb-2026", "series": [{"id": "20003"}], "tags": [{"id": "100381"}]}
    client, session, receipts = make(monkeypatch, [Response([sport]),
        Response({"events": [event], "next_cursor": ""}),
        Response({"events": [], "next_cursor": None})])
    rows, att = client.fetch_events("mlb")
    assert rows[0]["sport"] == sport
    assert rows[0]["_guava"]["sport_enrichment"] == "SEMANTIC_SEASON_JOIN"
    assert client.fetch_events("soccer")[1]["status"] == "SUCCESS"
    assert [call[1].endswith("/sports") for call in session.calls].count(True) == 1


def test_no_duplicate_event_and_original_embedded_sport_not_overwritten(monkeypatch):
    event = {"id": "e", "sport": {"id": 999, "sport": "unrecognized"}, "markets": []}
    client, session, receipts = make(monkeypatch, [Response([]),
        Response({"events": [event, event], "next_cursor": None})])
    rows, att = client.fetch_events("mlb")
    assert len(rows) == 1
    assert rows[0]["sport"] == event["sport"]
    assert att["duplicate_event_count"] == 1
    assert receipts[1][1]["events"] == [event, event]


def test_resolution_mismatched_condition_is_invalid(monkeypatch):
    client, session, receipts = make(monkeypatch, [Response({"condition_id": "other", "closed": True})])
    row = client.fetch_resolution("requested")
    assert row["status"] == "INVALID"
    assert row["raw"]["condition_id"] == "other"


def test_missing_resolution_does_not_fallback_to_other_host(monkeypatch):
    client, session, receipts = make(monkeypatch, [Response({"error": "not found"}, 404)])
    assert client.fetch_resolution("requested")["status"] == "HTTP_ERROR"
    assert len(session.calls) == 1


@pytest.mark.parametrize("overrides", [
    {"sport_families": ["wnba"]}, {"sport_families": "soccer"},
    {"sport_families": ["soccer", "soccer"]}, {"min_volume": -1},
    {"min_liquidity": float("nan")}, {"page_size": True}, {"page_size": 501},
    {"book_batch_limit": 0}, {"max_pages_per_family": 0},
])
def test_mapping_validation_is_independent_of_main_config(monkeypatch, overrides):
    with pytest.raises(ValueError):
        make(monkeypatch, [], **overrides)


def test_default_session_has_no_environment_credentials_or_proxies(monkeypatch):
    client, session, receipts = make(monkeypatch, [])
    assert session.trust_env is False
    assert set(session.headers) == {"Accept", "Accept-Encoding", "User-Agent"}
    assert session.headers["Accept-Encoding"] == "identity"


def test_single_read_stream_does_not_wait_for_a_full_chunk(monkeypatch):
    response = Response([])
    class Raw:
        def __init__(self):
            self.chunks = [b"[", b"]", b""]
        def read1(self, size, decode_content):
            assert size == 64 * 1024 and decode_content is False
            return self.chunks.pop(0)
    response.raw = Raw()
    client, session, receipts = make(monkeypatch, [response])
    assert client.fetch_books(["11"])["11"]["status"] == "MISSING"
    assert not response.raw.chunks
    assert response.closed


def test_single_read_stream_checks_deadline_after_each_read(monkeypatch):
    budget = Budget(10)
    response = Response([])
    class Raw:
        def read1(self, size, decode_content):
            budget.remaining = 0
            return b"["
    response.raw = Raw()
    client, session, receipts = make(monkeypatch, [response], budget)
    assert client.fetch_books(["11"])["11"]["status"] == "TIMEOUT"
    assert response.closed


def test_compression_not_silently_allowed_to_hide_slow_decode_loop(monkeypatch):
    response = Response([])
    response.headers["Content-Encoding"] = "gzip"
    client, session, receipts = make(monkeypatch, [response])
    assert client.fetch_books(["11"])["11"]["status"] != "OK"
    assert receipts[0][0]["error_detail"] == "unexpected_content_encoding"


def test_source_has_no_trading_or_sibling_imports():
    import ast
    tree = ast.parse(Path(clients.__file__).read_text())
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any(any(word in name for word in ("polybot", "clob_client", "signer", "auth", "order")) for name in imports)


@pytest.mark.parametrize("event_id", ["12345", 12345])
def test_fetch_tracked_event_exact_numeric_identity_and_raw_flags(monkeypatch, event_id):
    raw = {"id": "12345", "live": False, "ended": True, "closed": True,
           "markets": [{"conditionId": "c0", "closed": True, "acceptingOrders": False}],
           "updatedAt": "2026-09-06T01:00:00Z", "score": "3-0"}
    client, session, receipts = make(monkeypatch, [Response(raw)])
    result = client.fetch_event(event_id)
    assert result["status"] == "OK" and result["raw"] == raw
    assert session.calls[0][:2] == ("GET", "https://gamma-api.polymarket.com/events/12345")
    assert result["request_id"] == receipts[0][0]["request_id"]
    assert result["observed_at"] == receipts[0][0]["received_at"]
    assert receipts[0][1] == raw


@pytest.mark.parametrize("event_id", [True, None, -1, 1.5, "", " 123", "1?closed=false",
    "1/../order", "https://evil.invalid/events/1", "slug-event", "１２３"])
def test_fetch_event_invalid_id_before_http(monkeypatch, event_id):
    client, session, receipts = make(monkeypatch, [])
    with pytest.raises(ValueError):
        client.fetch_event(event_id)
    assert not session.calls and not receipts


@pytest.mark.parametrize("payload", [{"id": "999"}, {"id": True}, {"id": 123.0},
    [{"id": "123"}], {"id": "123", "next_cursor": "more"}, {}])
def test_fetch_event_identity_or_envelope_mismatch_is_not_success(monkeypatch, payload):
    client, session, receipts = make(monkeypatch, [Response(payload)])
    result = client.fetch_event("123")
    assert result["status"] == "INVALID"
    assert result["raw"] == json.loads(json.dumps(payload), parse_float=str)
    assert len(session.calls) == 1


def test_markets_closed_subset_and_per_market_point_in_time(monkeypatch):
    open_market = {"conditionId": "c0", "closed": False, "acceptingOrders": True, "outcomePrices": '["0.93","0.07"]'}
    closed_market = {"conditionId": "c1", "closed": True, "acceptingOrders": False, "outcomePrices": '["1","0"]'}
    client, session, receipts = make(monkeypatch, [Response([open_market]), Response([closed_market])])
    result = client.fetch_markets(["c0", "c1", "c0"])
    assert result["status"] == "OK" and result["complete"] is True
    assert result["markets"] == [open_market, closed_market]
    assert not result["missing_condition_ids"]
    assert [call[:2] for call in session.calls] == [("GET", "https://gamma-api.polymarket.com/markets")] * 2
    for i, closed in enumerate(["false", "true"]):
        assert session.calls[i][2]["params"]["condition_ids"] == ["c0", "c1"]
        assert session.calls[i][2]["params"]["closed"] == closed
        assert result["market_receipts"][f"c{i}"]["request_id"] == receipts[i][0]["request_id"]
        assert result["market_receipts"][f"c{i}"]["observed_at"] == receipts[i][0]["received_at"]
        assert receipts[i][1] == [[open_market], [closed_market]][i]
    assert result["request_id"] == receipts[-1][0]["request_id"]


def test_market_transition_keeps_both_observations_not_simultaneous(monkeypatch):
    first = {"conditionId": "c0", "closed": False, "acceptingOrders": True}
    second = {"conditionId": "c0", "closed": True, "acceptingOrders": False}
    client, session, receipts = make(monkeypatch, [Response([first]), Response([second])])
    result = client.fetch_markets(["c0"])
    assert result["status"] == "OK"
    assert result["markets"] == [second]
    assert [o["raw"] for o in result["observations"]] == [first, second]
    assert result["changed_condition_ids"] == ["c0"]
    assert len(result["request_ids"]) == 2
    assert result["point_in_time_basis"] == "PER_REQUEST_NOT_ATOMIC"


@pytest.mark.parametrize("closed_response", [Response([], 500), Response([])])
def test_markets_no_partial_success_on_branch_failure_or_missing_ids(monkeypatch, closed_response):
    client, session, receipts = make(monkeypatch, [Response([{"conditionId": "c0", "closed": False}]), closed_response])
    result = client.fetch_markets(["c0", "c1"])
    assert result["status"] != "OK" and not result["complete"]
    assert result["markets"] == []
    assert result["missing_condition_ids"] == ["c1"]
    assert result["observations"][0]["raw"]["conditionId"] == "c0"


@pytest.mark.parametrize("payload", [[{"conditionId": "alien", "closed": False}],
    [{"conditionId": "c0", "condition_id": "other", "closed": False}],
    [{"conditionId": "c0", "closed": True}], [{"closed": False}],
    [{"conditionId": "c0", "closed": False}] * 2,
    {"markets": [], "next_cursor": "do-not-follow"}])
def test_markets_mismatch_duplicate_or_unknown_cursor_never_success(monkeypatch, payload):
    client, session, receipts = make(monkeypatch, [Response(payload)])
    result = client.fetch_markets(["c0"])
    assert result["status"] != "OK" and result["markets"] == []
    assert len(session.calls) == 1
    assert receipts[0][1] == payload


@pytest.mark.parametrize("ids", ["c0", [], [None], [True], [""], ["c0&closed=false"], ["../order"]])
def test_fetch_markets_invalid_ids_before_http(monkeypatch, ids):
    client, session, receipts = make(monkeypatch, [])
    with pytest.raises(ValueError):
        client.fetch_markets(ids)
    assert not session.calls


@pytest.mark.parametrize("source,method,path", [
    ("gamma", "POST", "/events/123"), ("gamma", "GET", "/events/slug"),
    ("gamma", "GET", "/events/123/markets"), ("gamma", "GET", "/events/123?x=1"),
    ("gamma", "GET", "/events/../order"), ("gamma", "POST", "/markets"),
    ("clob", "GET", "/events/123"), ("other", "GET", "/events/123"),
])
def test_followup_adds_only_numeric_gamma_get_endpoint(monkeypatch, source, method, path):
    client, session, receipts = make(monkeypatch, [])
    with pytest.raises(ValueError):
        client._request(source, method, path)
    assert not session.calls


@pytest.mark.parametrize("method,args", [("fetch_event", ("123",)), ("fetch_markets", (["c0"],))])
def test_followup_deadline_and_denial_do_not_open_new_paths(monkeypatch, method, args):
    client, session, receipts = make(monkeypatch, [], Budget(0))
    assert getattr(client, method)(*args)["status"] == "TIMEOUT"
    assert not session.calls
    client, session, receipts = make(monkeypatch, [Response({}, 451)])
    assert getattr(client, method)(*args)["status"] == "DENIED"
    assert len(session.calls) == 1


def test_markets_offset_pages_are_complete_and_pit_receipts_are_per_page(monkeypatch):
    rows = [{"conditionId": f"c{i}", "closed": False} for i in range(3)]
    client, session, receipts = make(monkeypatch, [Response(rows[:2]), Response(rows[2:]), Response([])],
                                      page_size=2, book_batch_limit=4)
    result = client.fetch_markets(["c0", "c1", "c2"])
    assert result["status"] == "OK" and result["markets"] == rows
    assert [(c[2]["params"]["closed"], c[2]["params"]["offset"]) for c in session.calls] == [
        ("false", 0), ("false", 2), ("true", 0)]
    assert result["market_receipts"]["c2"]["request_id"] == receipts[1][0]["request_id"]
    assert result["request_id"] == receipts[2][0]["request_id"]
    assert all("after_cursor" not in c[2]["params"] for c in session.calls)


def test_repeated_market_page_is_not_cursor_progress(monkeypatch):
    rows = [{"conditionId": f"c{i}", "closed": False} for i in range(2)]
    client, session, receipts = make(monkeypatch, [Response(rows), Response(rows)],
                                      page_size=2, book_batch_limit=4)
    result = client.fetch_markets(["c0", "c1", "c2"])
    assert result["status"] == "INVALID" and result["markets"] == []
    assert result["error_type"] == "repeated_market_identity_or_page"
    assert len(session.calls) == 2
    assert receipts[0][1] == receipts[1][1] == rows


def test_market_page_cap_never_reports_complete_subset(monkeypatch):
    rows = [{"conditionId": f"c{i}", "closed": False} for i in range(2)]
    client, session, receipts = make(monkeypatch, [Response(rows)],
                                      page_size=2, book_batch_limit=4, max_pages_per_family=1)
    result = client.fetch_markets(["c0", "c1", "c2"])
    assert result["status"] == "INCOMPLETE" and result["markets"] == []
    assert result["error_type"] == "max_market_pages_exceeded"
    assert len(result["observations"]) == 2


def test_markets_exact_id_batches_bound_all_query_ids(monkeypatch):
    open_rows = [{"conditionId": f"c{i}", "closed": False} for i in range(3)]
    client, session, receipts = make(monkeypatch,
        [Response(open_rows[:2]), Response([]), Response(open_rows[2:]), Response([])], book_batch_limit=2)
    result = client.fetch_markets(["c0", "c1", "c2"])
    assert result["status"] == "OK" and result["markets"] == open_rows
    assert [c[2]["params"]["condition_ids"] for c in session.calls] == [["c0", "c1"], ["c0", "c1"], ["c2"], ["c2"]]


def test_fetch_event_does_not_reuse_old_sweep_or_add_sport_requests(monkeypatch):
    live = {"id": "123", "live": True, "ended": False, "closed": False}
    final = {**live, "live": False, "ended": True, "closed": True}
    client, session, receipts = make(monkeypatch, [Response([]),
        Response({"events": [live], "next_cursor": None}), Response(final)])
    sweep, _ = client.fetch_events("soccer")
    result = client.fetch_event("123")
    assert sweep[0]["ended"] is False
    assert result["raw"] == final and result["status"] == "OK"
    assert len(session.calls) == 3


@pytest.mark.parametrize("method,args", [("fetch_event", ("123",)), ("fetch_markets", (["c0"],))])
def test_followup_receipt_sink_failure_is_never_swallowed(monkeypatch, method, args):
    client, session, receipts = make(monkeypatch, [Response({"id": "123"}) if method == "fetch_event" else Response([])])
    def broken_sink(receipt, payload):
        raise RuntimeError("followup evidence unavailable")
    client.receipt_sink = broken_sink
    with pytest.raises(RuntimeError, match="followup evidence unavailable"):
        getattr(client, method)(*args)
    assert len(session.calls) == 1
