from __future__ import annotations

import json

import pytest

from requests import HTTPError
from requests.exceptions import ChunkedEncodingError

from polybot.utils.retry import (
    CooperativeDeadline,
    CycleDeadlineExceeded,
    PublicJsonTransport,
)


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


class Clock:
    def __init__(self):
        self.value = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


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


def test_deadline_caps_connect_and_read_timeout_for_each_attempt():
    clock = Clock()
    session = Session([Response(200, {"ok": True})])
    receipts = []
    transport = PublicJsonTransport(
        connect_timeout_seconds=3.05,
        read_timeout_seconds=30,
        max_retries=4,
        retry_base_seconds=1,
        retry_max_seconds=20,
        receipt_sink=receipts.append,
        session=session,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    deadline = CooperativeDeadline.after(4, monotonic=clock.monotonic)

    transport.request_json(
        "GET",
        "https://example.test/public",
        request_kind="deadline-timeout",
        run_id="run",
        deadline=deadline,
    )

    connect_timeout, read_timeout = session.calls[0][2]["timeout"]
    assert connect_timeout > 0
    assert read_timeout > 0
    assert connect_timeout + read_timeout <= 4
    assert receipts[0]["status"] == "SUCCESS"


def test_retry_after_that_cannot_fit_deadline_fails_without_sleep_or_retry():
    clock = Clock()
    session = Session(
        [
            Response(429, {"error": "slow"}, {"Retry-After": "20"}),
            Response(200, {"ok": True}),
        ]
    )
    receipts = []
    transport = PublicJsonTransport(
        connect_timeout_seconds=1,
        read_timeout_seconds=2,
        max_retries=4,
        retry_base_seconds=1,
        retry_max_seconds=20,
        receipt_sink=receipts.append,
        session=session,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    deadline = CooperativeDeadline.after(3, monotonic=clock.monotonic)

    with pytest.raises(CycleDeadlineExceeded, match="deadline"):
        transport.request_json(
            "GET",
            "https://example.test/public",
            request_kind="deadline-retry-after",
            run_id="run",
            deadline=deadline,
        )

    assert len(session.calls) == 1
    assert clock.sleeps == []
    assert receipts[0]["status"] == "ERROR"
    assert receipts[0]["retry_after_seconds"] == 20
