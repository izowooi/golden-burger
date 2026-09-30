"""Real SDK public result/metadata paths consume shared readback without auth."""
import hashlib
import json

from py_clob_client_v2 import ClobClient

from polybot_observability import market_data_capture as capture
from polybot_observability.market_data_refs import PayloadReferences


def test_real_v2_sdk_public_transport_and_internal_fee_cache_readback(monkeypatch):
    class Store:
        def __init__(self):
            self.data = {}
            self.observations = []
            self.reads = 0
        def put_many(self, values):
            hashes = [hashlib.sha256(v).hexdigest() for v in values]
            self.data.update(zip(hashes, values))
            return hashes
        def append_observations(self, values):
            self.observations.extend(values)
        def get_many(self, hashes):
            self.reads += 1
            return [bytes(bytearray(self.data[h])) for h in hashes]
    store = Store()
    monkeypatch.setenv("PUBLIC_MARKET_DATA_SOCKET", "/fixture-socket")
    monkeypatch.setenv("PUBLIC_MARKET_DATA_REQUIRED", "1")
    monkeypatch.setenv("PUBLIC_MARKET_DATA_SOURCE", "fixture")
    monkeypatch.setenv("JOB_NAME", "polybot-king")
    monkeypatch.setattr(capture, "configured_references", lambda: PayloadReferences(store, store))
    client = ClobClient("https://clob.polymarket.com", chain_id=137)
    original_book = {"asset_id": "token", "market": "condition", "bids": [{"price": "0.7000", "size": "123.4500"}], "asks": []}
    original_market = {"c": "condition", "t": [{"t": "token"}], "mts": "0.01", "nr": False, "fd": {"r": .05, "e": 1}}
    private = {"auth_only": "never-copy-private"}
    calls = []
    def get(endpoint, *, params=None, headers=None):
        calls.append(endpoint)
        if endpoint.endswith("/book"):
            return original_book
        if "/clob-markets/" in endpoint:
            return original_market
        return private
    monkeypatch.setattr(client, "_get", get)
    monkeypatch.setattr(client, "_post", lambda *a, **k: private)
    assert capture.install_sdk_capture(client, strategy="golden-plum") is client
    book = client.get_order_book("token")
    assert book == original_book and book is not original_book
    assert list(book) == list(original_book)
    assert book["bids"][0]["size"] == "123.4500"
    assert client.get_clob_market_info("condition") == original_market
    before = len(calls)
    assert client.get_tick_size("token") == "0.01"
    assert len(calls) == before  # keep the SDK's original metadata cache semantics
    assert len(store.observations) == 2 and store.reads == 2
    assert client._get(client.host + "/auth/api-keys", headers={"Authorization": "private"}) is private
    assert client._post(client.host + "/order", data={"signature": "private"}) is private
    assert len(store.observations) == 2
    assert all(b"never-copy-private" not in body for body in store.data.values())
    assert all(json.loads(o.metadata_json)["serialization"] == "canonical-sdk-json-value-v1-not-http-bytes" for o in store.observations)
