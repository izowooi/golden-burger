"""The legacy Date quote path must not turn shared-storage failure into 0.50."""
import hashlib
import json
from types import SimpleNamespace

import pytest
import requests

from polybot.api.clob_client import ClobClientWrapper
from polybot.api.gamma_client import GammaClient
from polybot_observability import market_data_capture as capture
from polybot_observability.market_data_refs import PayloadReferences


class Store:
    def __init__(self):
        self.values = {}
        self.observations = []
        self.reads = 0
        self.fail = False

    def put_many(self, values):
        if self.fail:
            raise OSError("shared writer unavailable")
        hashes = [hashlib.sha256(value).hexdigest() for value in values]
        self.values.update(zip(hashes, values))
        return hashes

    def append_observations(self, values):
        self.observations.extend(values)

    def get_many(self, hashes):
        self.reads += 1
        return [bytes(bytearray(self.values[digest])) for digest in hashes]


@pytest.fixture
def store(monkeypatch):
    store = Store()
    monkeypatch.setenv("PUBLIC_MARKET_DATA_SOCKET", "/fixture-socket")
    monkeypatch.setenv("PUBLIC_MARKET_DATA_REQUIRED", "1")
    monkeypatch.setenv("PUBLIC_MARKET_DATA_SOURCE", "fixture")
    monkeypatch.setenv("JOB_NAME", "polybot-red")
    monkeypatch.setattr(capture, "configured_references", lambda: PayloadReferences(store, store))
    return store


def test_actual_gamma_response_identity_and_json_are_consumed_after_readback(store, monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response.url = "https://gamma-api.polymarket.com/markets"
    response._content = b'[{"conditionId":"c","clobTokenIds":"[\\"1\\",\\"2\\"]"}]'
    raw = response.content
    calls = []
    def request(self, method, url, **kwargs):
        calls.append((method, url))
        return response
    monkeypatch.setattr(requests.Session, "request", request)
    gamma = GammaClient()
    restored = gamma._get("/markets")
    assert restored is response
    assert restored.content == raw and restored.json() == json.loads(raw)
    assert store.reads == 1 and len(calls) == 1


def test_actual_legacy_clob_storage_failure_is_not_swallowed_or_refetched(store):
    class Client:
        host = "https://clob.polymarket.com"
        calls = 0
        def get_midpoint(self, token_id):
            self.calls += 1
            return {"mid": "0.73"}
    wrapper = ClobClientWrapper(SimpleNamespace(), simulation_mode=False)
    wrapper._client = Client()
    wrapper._initialized = True
    assert wrapper.get_midpoint("1") == .73
    store.fail = True
    with pytest.raises(capture.PublicCaptureError):
        wrapper.get_midpoint("1")
    assert wrapper._client.calls == 2  # storage failure does not retry upstream or return 0.50
