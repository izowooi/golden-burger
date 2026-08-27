from __future__ import annotations

import json

import pytest
import requests

from polybot.api.transport import CycleBudget, PublicApiError, PublicJsonTransport


class FakeResponse:
    status_code = 200
    headers = {}

    def iter_content(self, chunk_size):
        del chunk_size
        yield b'{"ok":true}'

    def close(self):
        pass


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
        attempt_wall_seconds=2,
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
    assert session.calls[0][2]["stream"] is True
    assert "auth" not in json.dumps(session.calls).casefold()


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class SlowStreamingResponse(FakeResponse):
    def __init__(self, clock):
        self.clock = clock
        self.closed = False

    def iter_content(self, chunk_size):
        del chunk_size
        self.clock.value = 1.0
        yield b'{"ok":'
        self.clock.value = 3.0
        yield b'true}'

    def close(self):
        self.closed = True


def test_total_attempt_wall_clock_rejects_slow_stream_and_records_partial_body():
    clock = Clock()
    response = SlowStreamingResponse(clock)
    session = FakeSession()
    session.request = lambda *args, **kwargs: response
    receipts = []
    transport = PublicJsonTransport(
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        attempt_wall_seconds=2,
        max_retries=0,
        retry_base_seconds=0,
        retry_max_seconds=0,
        receipt_sink=receipts.append,
        session=session,
        monotonic=clock,
    )

    with pytest.raises(PublicApiError, match="total attempt wall-clock boundary"):
        transport.request_json(
            "GET",
            "https://gamma-api.polymarket.com/events/keyset",
            request_kind="slow-test",
            run_id="run",
            budget=CycleBudget(0, monotonic=clock),
        )

    assert response.closed is True
    assert len(receipts) == 1
    assert receipts[0]["status"] == "ERROR"
    assert receipts[0]["error_type"] == "AttemptWallTimeout"
    assert receipts[0]["response_bytes"] == len(b'{"ok":')


def test_total_attempt_wall_timeout_is_retryable_even_after_http_200_headers():
    clock = Clock()
    slow = SlowStreamingResponse(clock)
    fast = FakeResponse()

    class SequencedSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.responses = [slow, fast]

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return self.responses.pop(0)

    session = SequencedSession()
    receipts = []
    transport = PublicJsonTransport(
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        attempt_wall_seconds=2,
        max_retries=1,
        retry_base_seconds=0,
        retry_max_seconds=0,
        receipt_sink=receipts.append,
        session=session,
        monotonic=clock,
    )

    result = transport.request_json(
        "GET",
        "https://gamma-api.polymarket.com/events/keyset",
        request_kind="slow-then-fast",
        run_id="run",
        budget=CycleBudget(0, monotonic=clock),
    )

    assert result.payload == {"ok": True}
    assert [row["status"] for row in receipts] == ["ERROR", "SUCCESS"]
    assert receipts[0]["error_type"] == "AttemptWallTimeout"
    assert len(session.calls) == 2
