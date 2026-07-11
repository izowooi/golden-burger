"""Bounded retry behavior for Data API requests."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
import requests

from polybot_reporter.retry import rate_limit_handler


def http_error(status: int, retry_after: str | None = None) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return requests.HTTPError(f"HTTP {status}", response=response)


@pytest.mark.parametrize(
    ("retry_after", "expected_wait"),
    [
        ("4", 4.0),
        ("http-date", 5.0),
        ("not-a-valid-retry-after", 1.0),
        ("3600", 5.0),
    ],
)
def test_retry_after_is_parsed_defensively_and_bounded(
    monkeypatch, retry_after, expected_wait
):
    if retry_after == "http-date":
        retry_after = format_datetime(
            datetime.now(timezone.utc) + timedelta(hours=1), usegmt=True
        )
    error = http_error(429, retry_after)
    attempts = 0

    @rate_limit_handler(max_retries=2, base_delay=1, max_delay=5)
    def request():
        nonlocal attempts
        attempts += 1
        raise error

    sleeps = []
    monkeypatch.setattr("polybot_reporter.retry.random.uniform", lambda *_args: 0.0)
    monkeypatch.setattr("polybot_reporter.retry.time.sleep", sleeps.append)

    with pytest.raises(requests.HTTPError) as raised:
        request()

    assert raised.value is error
    assert attempts == 2
    assert sleeps == [expected_wait]


@pytest.mark.parametrize(
    "error",
    [
        http_error(503),
        requests.ConnectionError("connection failed"),
        requests.Timeout("timed out"),
    ],
)
def test_final_failed_attempt_never_sleeps_and_preserves_original_error(
    monkeypatch, error
):
    attempts = 0

    @rate_limit_handler(max_retries=2, base_delay=1, max_delay=5)
    def request():
        nonlocal attempts
        attempts += 1
        raise error

    monkeypatch.setattr("polybot_reporter.retry.random.uniform", lambda *_args: 0.0)
    recorded_sleeps = []
    monkeypatch.setattr("polybot_reporter.retry.time.sleep", recorded_sleeps.append)

    with pytest.raises(type(error)) as raised:
        request()

    assert raised.value is error
    assert attempts == 2
    assert recorded_sleeps == [1.0]
