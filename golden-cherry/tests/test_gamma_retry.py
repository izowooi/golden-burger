from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from polybot.api.gamma_client import GammaClient
from polybot.utils.retry import rate_limit_handler


class Response:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class TransientForbiddenResponse:
    status_code = 403
    headers = {"Retry-After": "0"}

    def raise_for_status(self):
        raise requests.exceptions.HTTPError("edge forbidden", response=self)


class MidSweepForbiddenSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, dict(params), timeout))
        cursor = params.get("after_cursor")
        if cursor is None:
            return Response({"markets": [], "next_cursor": "cursor-1"})
        if len(self.calls) == 2:
            return TransientForbiddenResponse()
        return Response({"markets": []})


def test_gamma_retries_mid_sweep_forbidden_on_same_cursor(monkeypatch):
    sleeps = []
    monkeypatch.setattr("polybot.utils.retry.random.uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr("polybot.utils.retry.time.sleep", sleeps.append)
    monkeypatch.setattr("polybot.api.gamma_client.time.sleep", sleeps.append)
    client = GammaClient()
    client.session = MidSweepForbiddenSession()

    assert client.get_all_tradable_markets() == []

    cursors = [call[1].get("after_cursor") for call in client.session.calls]
    assert cursors == [None, "cursor-1", "cursor-1"]
    assert sleeps == [client.KEYSET_PAGE_INTERVAL_SECONDS, 2.0]
    assert client.last_sweep_attestation["pages"] == 2


def test_forbidden_is_not_retried_without_explicit_opt_in(monkeypatch):
    attempts = []
    sleeps = []
    monkeypatch.setattr("polybot.utils.retry.time.sleep", sleeps.append)
    response = SimpleNamespace(status_code=403, headers={})

    @rate_limit_handler(max_retries=3, base_delay=2.0)
    def forbidden_authenticated_request():
        attempts.append(1)
        raise requests.exceptions.HTTPError("forbidden", response=response)

    with pytest.raises(requests.exceptions.HTTPError):
        forbidden_authenticated_request()

    assert attempts == [1]
    assert sleeps == []
