from __future__ import annotations

import json

from requests import HTTPError
from requests.exceptions import ChunkedEncodingError

from polybot.utils.retry import PublicJsonTransport


class Response:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self.content = json.dumps(payload).encode()
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(f"HTTP {self.status_code}", response=self)


class Session:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.trust_env = True
        self.headers = {}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_retry_handles_chunked_rate_limit_and_retry_after():
    session = Session(
        [
            ChunkedEncodingError("partial"),
            Response(429, {"error": "slow"}, {"Retry-After": "2"}),
            Response(200, {"ok": True}),
        ]
    )
    receipts = []
    sleeps = []
    transport = PublicJsonTransport(
        connect_timeout_seconds=1,
        read_timeout_seconds=2,
        max_retries=3,
        retry_base_seconds=0.5,
        retry_max_seconds=5,
        receipt_sink=receipts.append,
        session=session,
        sleep=sleeps.append,
    )
    result = transport.request_json(
        "GET",
        "https://example.test/public",
        request_kind="test",
        run_id="run",
    )
    assert result.payload == {"ok": True}
    assert [row["status"] for row in receipts] == ["ERROR", "ERROR", "SUCCESS"]
    assert sleeps == [0.5, 2.0]
    assert session.trust_env is False
