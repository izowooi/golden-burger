from __future__ import annotations

import json

import pytest
import requests

from polybot.utils.retry import (
    CycleBudget,
    CycleBudgetExceeded,
    PublicJsonTransport,
)


class FakeClock:
    def __init__(self, value: float = 0.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class RecordingSession:
    def __init__(self, responses, clock: FakeClock):
        self.responses = list(responses)
        self.clock = clock
        self.headers = {}
        self.timeouts = []

    def request(self, *args, **kwargs):
        self.timeouts.append(kwargs["timeout"])
        return self.responses.pop(0)


def _response(status: int, *, retry_after: str | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({"ok": status < 400}).encode()
    response.url = "https://example.test/public"
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return response


def test_remaining_budget_caps_connect_and_read_timeout():
    clock = FakeClock(190.0)
    budget = CycleBudget(
        cooperative_seconds=225,
        hard_limit_seconds=240,
        network_stop_margin_seconds=30,
        clock=clock,
        started_clock=0.0,
    )
    session = RecordingSession([_response(200)], clock)
    receipts = []
    transport = PublicJsonTransport(
        connect_timeout_seconds=3.05,
        read_timeout_seconds=30,
        max_retries=4,
        retry_base_seconds=1,
        retry_max_seconds=15,
        receipt_sink=receipts.append,
        session=session,
        sleep=clock.sleep,
        budget=budget,
        clock=clock,
    )

    transport.request_json(
        "GET",
        "https://example.test/public",
        request_kind="budget_test",
        run_id="run",
    )

    connect, read = session.timeouts[0]
    assert connect + read <= 5.0
    assert receipts[0]["budget_remaining_before_seconds"] == 5.0
    assert receipts[0]["timeout_connect_seconds"] == connect
    assert receipts[0]["timeout_read_seconds"] == read


def test_unaffordable_retry_after_records_error_and_sends_no_retry():
    clock = FakeClock(190.0)
    budget = CycleBudget(
        cooperative_seconds=225,
        hard_limit_seconds=240,
        network_stop_margin_seconds=30,
        clock=clock,
        started_clock=0.0,
    )
    session = RecordingSession([_response(429, retry_after="10")], clock)
    receipts = []
    transport = PublicJsonTransport(
        connect_timeout_seconds=3.05,
        read_timeout_seconds=30,
        max_retries=4,
        retry_base_seconds=1,
        retry_max_seconds=15,
        receipt_sink=receipts.append,
        session=session,
        sleep=clock.sleep,
        budget=budget,
        clock=clock,
    )

    with pytest.raises(CycleBudgetExceeded, match="Retry-After") as raised:
        transport.request_json(
            "GET",
            "https://example.test/public",
            request_kind="budget_test",
            run_id="run",
        )

    assert len(session.timeouts) == 1
    assert len(receipts) == 1
    assert receipts[0]["status"] == "ERROR"
    assert receipts[0]["retry_after_seconds"] == 10.0
    assert raised.value.evidence()["network_remaining_seconds"] == 5.0


def test_network_stops_at_margin_and_hard_limit_is_strictly_later():
    clock = FakeClock(195.0)
    budget = CycleBudget(
        cooperative_seconds=225,
        hard_limit_seconds=240,
        network_stop_margin_seconds=30,
        clock=clock,
        started_clock=0.0,
    )
    with pytest.raises(CycleBudgetExceeded, match="network stop margin"):
        budget.request_timeout(3.05, 30, phase="new_http")
    assert budget.cooperative_remaining_seconds == 30.0
    assert budget.hard_remaining_seconds == 45.0


def test_retry_reuses_one_logical_request_and_marks_first_start_once():
    clock = FakeClock()
    budget = CycleBudget(
        cooperative_seconds=225,
        hard_limit_seconds=240,
        network_stop_margin_seconds=30,
        clock=clock,
        started_clock=0.0,
    )
    session = RecordingSession([_response(429), _response(200)], clock)
    receipts = []
    starts = []
    transport = PublicJsonTransport(
        connect_timeout_seconds=3.05,
        read_timeout_seconds=30,
        max_retries=4,
        retry_base_seconds=1,
        retry_max_seconds=15,
        receipt_sink=receipts.append,
        session=session,
        sleep=clock.sleep,
        budget=budget,
        clock=clock,
    )

    response = transport.request_json(
        "GET",
        "https://example.test/public",
        request_kind="followup_retry_test",
        run_id="run",
        before_first_attempt=lambda logical_id, started_at: starts.append(
            (logical_id, started_at)
        ),
    )

    assert len(starts) == 1
    assert len(session.timeouts) == 2
    assert {receipt["logical_request_id"] for receipt in receipts} == {
        starts[0][0]
    }
    assert response.logical_request_id == starts[0][0]
