import hashlib
import json
from types import SimpleNamespace

import pytest


def test_public_batch_and_event_identifiers_are_exact_ids_not_slugs():
    from polybot_observability.market_data_capture import _ids
    assert _ids('POST', 'https://clob.polymarket.com/books', None, None,
                {'books':[{'assetId':'123'},{'asset_id':'456'}]}) == (['123','456'], [])
    assert _ids('GET', 'https://gamma-api.polymarket.com/events', None, None,
                [{'id':'17','markets':[{'clobTokenIds':'["123","456"]'}]}]) == (['123','456'], ['17'])
    assert _ids('GET', 'https://gamma-api.polymarket.com/events/slug/a-public-game', None, None,
                {'id':'17'}) == ([], ['17'])
    assert _ids('GET', 'https://gamma-api.polymarket.com/events/slug/a-public-game', None, None,
                None) == ([], [])

from polybot_observability import market_data_capture as capture
from polybot_observability.market_data_refs import PayloadReferences


@pytest.mark.parametrize('key', ['auth', 'oauth', 'api_passphrase', 'passphrase', 'AUTHENTICATION'])
def test_credential_query_aliases_cannot_enter_public_capture(key):
    assert capture.public_endpoint('GET', 'https://clob.polymarket.com/book',
                                   params={'token_id': 'public-token', key: 'not-public'}) is None
    assert capture.public_endpoint('GET', 'https://gamma-api.polymarket.com/events?' + key + '=not-public') is None


def test_credential_shaped_query_is_not_written_or_indexed(shared):
    result = capture.capture_public_bytes(b'{"asset_id":"public-token"}', strategy='golden-date',
        method='GET', url='https://clob.polymarket.com/book',
        params={'token_id': 'public-token', 'api_passphrase': 'not-public'})
    assert result == b'{"asset_id":"public-token"}'
    assert shared.payloads == {} and shared.observations == []


class MemoryStore:
    def __init__(self):
        self.payloads = {}
        self.calls = []
        self.observations = []
        self.last_readback = None

    def put_many(self, values):
        self.calls.append("put")
        hashes = [hashlib.sha256(value).hexdigest() for value in values]
        self.payloads.update(zip(hashes, values))
        return hashes

    def append_observations(self, values):
        self.calls.append("index")
        for value in values:
            value.row()
        self.observations.extend(values)

    def get_many(self, hashes):
        self.calls.append("read")
        self.last_readback = [bytes(bytearray(self.payloads[digest])) for digest in hashes]
        return self.last_readback


class HTTPFailure(RuntimeError):
    pass


class Response:
    def __init__(self, body, status=200, url=""):
        self._content, self.status_code, self.url = body, status, url

    @property
    def content(self):
        return self._content

    def json(self):
        return json.loads(self._content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPFailure(str(self.status_code))


class Session:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.response


@pytest.fixture
def shared(monkeypatch):
    store = MemoryStore()
    codec = PayloadReferences(store, store)
    monkeypatch.setenv("PUBLIC_MARKET_DATA_SOCKET", "/not-used-test-socket")
    monkeypatch.setenv("PUBLIC_MARKET_DATA_REQUIRED", "1")
    monkeypatch.setenv("PUBLIC_MARKET_DATA_SOURCE", "test-host")
    monkeypatch.setenv("JOB_NAME", "test-job")
    monkeypatch.delenv("PUBLIC_MARKET_DATA_DB", raising=False)
    monkeypatch.setattr(capture, "configured_references", lambda: codec)
    return store


@pytest.mark.parametrize("method,url,params,body", [
    ("GET", "https://clob.polymarket.com/data/orders", None, None),
    ("GET", "https://clob.polymarket.com/data/trades", None, None),
    ("GET", "https://clob.polymarket.com/balance-allowance", None, None),
    ("POST", "https://clob.polymarket.com/order", None, {"token_id": "t"}),
    ("DELETE", "https://clob.polymarket.com/order", None, None),
    ("GET", "https://clob.polymarket.com/auth/api-keys", None, None),
    ("GET", "https://data-api.polymarket.com/positions", {"user": "account"}, None),
    ("GET", "https://data-api.polymarket.com/activity", None, None),
    ("GET", "https://data-api.polymarket.com/trades", None, None),
    ("GET", "https://gamma-api.polymarket.com/markets?user=account", None, None),
    ("GET", "https://clob.polymarket.com/book", {"wallet": "account"}, None),
    ("GET", "https://gamma-api.polymarket.com/events", {"api_key": "private"}, None),
    ("GET", "https://private:password@clob.polymarket.com/book", None, None),
    ("GET", "https://clob.polymarket.com.evil.test/book", None, None),
    ("GET", "https://clob.polymarket.com/unknown-public-looking-get", None, None),
    ("POST", "https://clob.polymarket.com/books", None, [{"token_id": "t", "signature": "private"}]),
    ("WS_RECV", "wss://ws-subscriptions-clob.polymarket.com/ws/user", None, None),
])
def test_private_or_unreviewed_route_never_sends_to_shared_store(shared, method, url, params, body):
    original = b'{"private_response":"never-copy"}'
    assert capture.capture_public_bytes(original, strategy="golden-date", method=method, url=url, params=params, body=body) is original
    assert shared.calls == []


def test_raw_response_identity_exact_bytes_readback_and_public_trace(shared):
    raw = b'{ "asset_id":"123", "market":"condition", "timestamp":"123000", "bids":[{"price":".71","size":"900"}], "asks":[] }'
    response = Response(raw)
    session = Session(response)
    assert capture.install_session_capture(session, strategy="golden-date") is session
    headers = {"Authorization": "DO_NOT_CAPTURE_AUTH", "Cookie": "DO_NOT_CAPTURE_COOKIE"}
    result = session.request("GET", "https://clob.polymarket.com/book", params={"token_id": "123"}, headers=headers)
    assert result is response
    assert result.content is shared.last_readback[0]
    assert result.content == raw and result.json()["bids"][0]["size"] == "900"
    assert shared.calls == ["put", "index", "read"]
    assert session.requests[0][2]["headers"] is headers
    observation = shared.observations[0]
    assert observation.token_id == "123"
    metadata = json.loads(observation.metadata_json)
    assert metadata["serialization"] == "http-response-content-bytes"
    assert metadata["source_timestamp_raw"] == "123000"
    assert metadata["condition_id"] == "condition"
    assert "DO_NOT_CAPTURE" not in observation.metadata_json


def test_identical_source_bytes_still_have_distinct_receipts_without_quote_cache(shared):
    session = capture.install_session_capture(Session(Response(b'{"mid":"0.7"}')), strategy="golden-cherry")
    for _ in range(2):
        session.request("GET", "https://clob.polymarket.com/midpoint", params={"token_id": "t"})
    assert len(session.requests) == 2
    assert len(shared.payloads) == 1 and len(shared.observations) == 2
    assert len({row.observation_id for row in shared.observations}) == 2


def test_failed_http_body_is_retained_without_changing_http_status_semantics(shared):
    response = Response(b'{"error":"unavailable"}', status=503)
    result = capture.capture_response(response, strategy="golden-date", method="GET", url="https://gamma-api.polymarket.com/markets")
    assert result is response and result.status_code == 503
    with pytest.raises(HTTPFailure, match="503"):
        result.raise_for_status()
    assert json.loads(shared.observations[0].metadata_json)["http_status"] == 503


def test_redirect_to_private_target_does_not_capture_its_response(shared):
    response = Response(b"private", url="https://clob.polymarket.com/data/orders")
    assert capture.capture_response(response, strategy="golden-date", method="GET", url="https://clob.polymarket.com/book") is response
    assert shared.calls == []


def test_streaming_session_is_not_drained_before_transport_deadline_loop(shared):
    class StreamingResponse:
        @property
        def content(self):
            pytest.fail("stream was eagerly drained")
    response = StreamingResponse()
    session = capture.install_session_capture(Session(response), strategy="golden-coconut")
    assert session.request("GET", "https://gamma-api.polymarket.com/events", stream=True) is response
    assert shared.calls == []
    restored = capture.capture_public_bytes(b'{"events":[]}', strategy="golden-coconut", method="GET", url="https://gamma-api.polymarket.com/events")
    assert restored is shared.last_readback[0]


def test_writer_or_reader_failure_has_no_inline_fallback(shared, monkeypatch):
    response = Response(b'{"mid":"0.7"}')
    monkeypatch.setattr(shared, "get_many", lambda _: [b"corrupt"])
    with pytest.raises(capture.PublicCaptureError):
        capture.capture_response(response, strategy="golden-date", method="GET", url="https://clob.polymarket.com/midpoint")
    def fail(_):
        raise OSError("writer unavailable")
    monkeypatch.setattr(shared, "put_many", fail)
    with pytest.raises(capture.PublicCaptureError):
        capture.capture_response(response, strategy="golden-date", method="GET", url="https://clob.polymarket.com/midpoint")


def test_budget_exhausted_by_shared_read_is_not_returned_as_a_fresh_quote(shared, monkeypatch):
    remaining = [5.0]
    for name in ("put_many", "append_observations", "get_many"):
        original = getattr(shared, name)
        def delayed(values, original=original):
            remaining[0] -= 2
            return original(values)
        monkeypatch.setattr(shared, name, delayed)
    with pytest.raises(capture.PublicCaptureError, match="budget"):
        capture.capture_public_bytes(b'{}', strategy="golden-date", method="GET", url="https://clob.polymarket.com/book", budget=lambda: remaining[0])


def test_gamma_nested_market_identifiers_are_indexed_without_account_fields(shared):
    raw = json.dumps({"events": [{"id": "event", "markets": [{"clobTokenIds": '["1","2"]'}]}]}).encode()
    capture.capture_public_bytes(raw, strategy="golden-date", method="GET", url="https://gamma-api.polymarket.com/events/keyset", run_id="run", request_id="request")
    assert len(shared.observations) == 1
    assert json.loads(shared.observations[0].metadata_json)["token_ids"] == ["1", "2"]
    assert {r.event_id for r in shared.observations if r.event_id} == {"event"}
    assert all(json.loads(r.metadata_json)["run_id"] == "run" for r in shared.observations)


class OrderSummary:
    __module__ = "py_clob_client_v2.clob_types"
    def __init__(self, price=None, size=None):
        self.price, self.size = price, size


class OrderBookSummary:
    __module__ = "py_clob_client_v2.clob_types"
    def __init__(self, **kwargs):
        for key in capture._BOOK_FIELDS:
            setattr(self, key, kwargs.get(key))
        self.private_key = "NEVER_COPY_SDK_OBJECT_STATE"

    @property
    def __dict__(self):
        pytest.fail("SDK object state was dumped")


class SDK:
    host = "https://clob.polymarket.com"
    def __init__(self):
        self.private = object()
        self.calls = []

    def get_order_book(self, token_id):
        self.calls.append(("book", token_id))
        return OrderBookSummary(asset_id=token_id, bids=[OrderSummary(".7", "100.00")], asks=[OrderSummary(".71", "99.50")])

    def get_tick_size(self, token_id):
        return ".01"

    def get_api_creds(self):
        return self.private

    def post_order(self, order):
        return self.private


def test_sdk_preserves_client_and_private_result_identity_and_rebuilds_public_dto(shared):
    client = SDK()
    assert capture.install_sdk_capture(client, strategy="golden-plum") is client
    assert capture.install_sdk_capture(client, strategy="golden-plum") is client
    result = client.get_order_book("token")
    assert type(result) is OrderBookSummary
    assert type(result.bids[0]) is OrderSummary
    assert result.bids[0].price == ".7" and result.asks[0].size == "99.50"
    assert client.calls == [("book", "token")]
    count = len(shared.calls)
    assert client.get_api_creds() is client.private
    assert client.post_order({"private": "signed-order"}) is client.private
    assert len(shared.calls) == count
    assert all(b"NEVER_COPY_SDK_OBJECT_STATE" not in body for body in shared.payloads.values())
    assert all(json.loads(row.metadata_json)["serialization"] == "canonical-sdk-result-v1-not-http-bytes" for row in shared.observations)
    assert all(row.kind == "public-sdk-result" for row in shared.observations)


def test_sdk_unknown_objects_are_not_dumped(shared):
    client = SDK()
    client.get_order_book = lambda _: SimpleNamespace(secret="do-not-dump")
    capture.install_sdk_capture(client, strategy="golden-plum")
    with pytest.raises(capture.PublicCaptureError):
        client.get_order_book("token")
    assert shared.calls == []


def test_parallel_sdk_installation_does_not_double_capture(shared):
    from concurrent.futures import ThreadPoolExecutor
    client = SDK()
    with ThreadPoolExecutor(max_workers=8) as pool:
        installed = list(pool.map(lambda _: capture.install_sdk_capture(client, strategy="golden-plum"), range(32)))
    assert all(result is client for result in installed)
    client.get_order_book("token")
    assert len(shared.observations) == 1


def test_legacy_mode_keeps_api_objects_and_private_methods_unchanged(monkeypatch):
    for key in ("PUBLIC_MARKET_DATA_DB", "PUBLIC_MARKET_DATA_SOCKET", "PUBLIC_MARKET_DATA_REQUIRED"):
        monkeypatch.delenv(key, raising=False)
    response = Response(b"source")
    session = Session(response)
    original = session.request
    assert capture.install_session_capture(session, strategy="golden-date") is session
    assert session.request == original
    assert capture.capture_response(response, strategy="golden-date", method="GET", url="https://clob.polymarket.com/book") is response
    client = SDK()
    original = client.get_order_book
    assert capture.install_sdk_capture(client, strategy="golden-plum") is client
    assert client.get_order_book == original


def test_sdk_instance_transport_captures_internal_market_info_without_double_observations(shared):
    class TransportSDK:
        host = "https://clob.polymarket.com"
        private = object()

        def _get(self, endpoint, *, params=None, headers=None):
            if endpoint.endswith("/auth/api-keys"):
                return self.private
            return {"c": "condition", "t": [{"t": "token"}], "mts": "0.01", "fd": {"r": .05}}

        def _post(self, endpoint, data=None, headers=None):
            if endpoint.endswith("/order"):
                return self.private
            return [{"asset_id": "token", "bids": [], "asks": []}]

        def get_clob_market_info(self, condition_id):
            return self._get(self.host + "/clob-markets/" + condition_id)

        def get_order_books(self, params):
            return self._post(self.host + "/books", data=params)

        def private_order(self):
            # SDK's internal public metadata read is legitimate; the signed
            # private order itself must never be captured.
            self._get(self.host + "/markets-by-token/token")
            return self._post(self.host + "/order", data={"signature": "never-copy"})

    client = TransportSDK()
    assert capture.install_sdk_capture(client, strategy="golden-plum") is client
    result = client.get_clob_market_info("condition")
    assert result["t"] == [{"t": "token"}]
    assert len(shared.observations) == 1
    assert json.loads(shared.observations[0].metadata_json)["path"] == "/clob-markets/condition"
    assert client.get_order_books([{"token_id": "token"}])[0]["asset_id"] == "token"
    assert len(shared.observations) == 2
    before = len(shared.calls)
    assert client._get(client.host + "/auth/api-keys", headers={"Authorization": "private"}) is client.private
    assert client._post(client.host + "/order", data={"signature": "private"}) is client.private
    assert len(shared.calls) == before
    assert client.private_order() is client.private
    assert len(shared.observations) == 3
    assert all(b"never-copy" not in body for body in shared.payloads.values())


def test_public_batch_and_websocket_allowlists_are_explicit():
    assert capture.public_endpoint("POST", "https://clob.polymarket.com/books", body=[{"token_id": "t"}])
    assert capture.public_endpoint("POST", "https://clob.polymarket.com/batch-prices-history", body={"markets": ["t"], "fidelity": 10})
    assert capture.public_endpoint("WS_RECV", "wss://sports-api.polymarket.com/ws")
    assert capture.public_endpoint("GET", "https://gamma-api.polymarket.com/markets", body={"unreviewed": True}) is None


def test_existing_collector_cycle_budget_shapes_are_supported():
    assert capture.remaining_budget(SimpleNamespace(cycle_remaining_seconds=2.5)) == 2.5
    assert capture.remaining_budget(SimpleNamespace(hard_remaining_seconds=3.0, enforce_deadline=False)) is None
    with pytest.raises(capture.PublicCaptureError):
        capture.remaining_budget(SimpleNamespace(cycle_remaining_seconds=0))


def test_every_legacy_strategy_is_wired_and_sdk_read_names_are_reviewed():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    strategies = [p for p in root.glob("golden-*") if (p / "src/polybot/db/models.py").is_file()]
    assert len(strategies) == 23
    private_reads = {"get_balance_allowance", "get_order", "get_open_orders", "get_pre_migration_orders", "get_trades"}
    for project in strategies:
        for name in ("gamma_client.py", "history_client.py", "clob_client.py"):
            path = project / "src/polybot/api" / name
            if not path.exists():
                continue
            tree = ast.parse(path.read_text())
            installer = "install_sdk_capture" if name == "clob_client.py" else "install_session_capture"
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == installer]
            assert calls, path
            assert any(any(k.arg == "strategy" and isinstance(k.value, ast.Constant) and k.value.value == project.name for k in n.keywords) for n in calls), path
            if name == "clob_client.py":
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "client"
                            and node.func.attr.startswith("get_")):
                        assert node.func.attr in capture.SDK_PUBLIC_METHODS or node.func.attr in private_reads, (path, node.func.attr)
        wallet = project / "src/polybot/api/data_api_client.py"
        if wallet.exists():
            assert "market_data_capture" not in wallet.read_text(), wallet
