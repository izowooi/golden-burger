from __future__ import annotations

import json

import requests

from polybot.api.transport import CycleBudget, PublicJsonTransport


class FakeResponse:
    status_code = 200
    headers = {}
    content = b'{"ok":true}'


class FakeSession:
    def __init__(self):
        self.trust_env = True
        self.headers = {"Authorization": "must-be-cleared"}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse()


def test_requests_session_disables_environment_and_auth_headers():
    session = FakeSession()
    receipts = []
    transport = PublicJsonTransport(
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_retries=0,
        retry_base_seconds=0,
        retry_max_seconds=0,
        receipt_sink=receipts.append,
        session=session,
        monotonic=lambda: 0,
    )
    assert session.trust_env is False
    assert "Authorization" not in session.headers
    response = transport.request_json(
        "GET",
        "https://gamma-api.polymarket.com/events/keyset",
        request_kind="test",
        run_id="run",
        budget=CycleBudget(0, monotonic=lambda: 0),
    )
    assert response.payload == {"ok": True}
    assert receipts[0]["method"] == "GET"
    assert "auth" not in json.dumps(session.calls).casefold()
