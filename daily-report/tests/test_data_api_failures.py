"""Regression tests for upstream failures that must block DB snapshots."""

import json
import logging
import zipfile

import pytest
import requests

from polybot_reporter.api.data_api_client import DataAPIClient


def response(status_code: int, content: bytes = b"") -> requests.Response:
    value = requests.Response()
    value.status_code = status_code
    value.url = "https://data-api.polymarket.com/test"
    value._content = content
    return value


def test_positions_http_error_is_not_converted_to_empty_portfolio(monkeypatch, caplog):
    client = DataAPIClient()
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: response(400))
    wallet = "0x0000000000000000000000000000000000000000"

    with caplog.at_level(logging.INFO), pytest.raises(requests.HTTPError):
        client.get_positions(wallet)

    assert wallet not in caplog.text
    assert wallet[:10] not in caplog.text
    assert "address=[REDACTED]" in caplog.text


def test_invalid_cash_snapshot_is_not_converted_to_zero(monkeypatch):
    client = DataAPIClient()
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: response(200, b"bad zip"))

    with pytest.raises(zipfile.BadZipFile, match="File is not a zip file"):
        client.get_cash_balance("0x0000000000000000000000000000000000000000")


def test_positions_uses_official_500_row_pagination(monkeypatch):
    client = DataAPIClient()
    calls = []

    def fake_get(_url, *, params, timeout):
        calls.append(params)
        assert timeout == (5, 30)
        payload = [{"conditionId": str(index)} for index in range(500)] if params["offset"] == 0 else [{"conditionId": "last"}]
        value = response(200, json.dumps(payload).encode())
        value.headers["Content-Type"] = "application/json"
        return value

    monkeypatch.setattr(client.session, "get", fake_get)

    positions = client.get_positions("0xABC")

    assert len(positions) == 501
    assert calls == [
        {"user": "0xabc", "limit": 500, "offset": 0, "sizeThreshold": 0},
        {"user": "0xabc", "limit": 500, "offset": 500, "sizeThreshold": 0},
    ]


def test_activity_uses_user_parameter(monkeypatch):
    client = DataAPIClient()
    captured = {}

    def fake_get(_url, *, params, timeout):
        captured.update(params)
        assert timeout == (5, 30)
        value = response(200, b"[]")
        value.headers["Content-Type"] = "application/json"
        return value

    monkeypatch.setattr(client.session, "get", fake_get)

    assert client.get_activity("0xABC", limit=12) == []
    assert captured == {"user": "0xabc", "limit": 12, "offset": 0}


def test_activity_uses_official_500_row_bounded_pagination(monkeypatch):
    client = DataAPIClient()
    calls = []

    def fake_get(_url, *, params, timeout):
        calls.append(params)
        assert timeout == (5, 30)
        count = 500 if params["offset"] == 0 else 1
        value = response(
            200, json.dumps([{"id": index} for index in range(count)]).encode()
        )
        value.headers["Content-Type"] = "application/json"
        return value

    monkeypatch.setattr(client.session, "get", fake_get)

    activities = client.get_activity("0xABC", limit=700, condition_id="0xmarket")

    assert len(activities) == 501
    assert calls == [
        {"user": "0xabc", "limit": 500, "offset": 0, "market": "0xmarket"},
        {"user": "0xabc", "limit": 200, "offset": 500, "market": "0xmarket"},
    ]


@pytest.mark.parametrize("limit", [-1, 1.5, True, 10_001])
def test_activity_rejects_out_of_contract_limit_without_request(monkeypatch, limit):
    client = DataAPIClient()
    requested = False

    def fake_get(*_args, **_kwargs):
        nonlocal requested
        requested = True

    monkeypatch.setattr(client.session, "get", fake_get)

    with pytest.raises(ValueError, match="activity limit"):
        client.get_activity("0xABC", limit=limit)

    assert requested is False


def test_activity_http_error_is_not_converted_to_empty_history(monkeypatch):
    client = DataAPIClient()
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: response(400))

    with pytest.raises(requests.HTTPError):
        client.get_activity("0x0000000000000000000000000000000000000000")


def test_trades_use_user_start_and_bounded_pagination(monkeypatch):
    client = DataAPIClient()
    calls = []

    def fake_get(_url, *, params, timeout):
        calls.append(params)
        assert timeout == (5, 30)
        count = 500 if params["offset"] == 0 else 1
        value = response(200, json.dumps([{"id": index} for index in range(count)]).encode())
        value.headers["Content-Type"] = "application/json"
        return value

    monkeypatch.setattr(client.session, "get", fake_get)

    trades = client.get_trades_by_address("0xABC", limit=700, after_timestamp=1234)

    assert len(trades) == 501
    assert calls == [
        {"user": "0xabc", "limit": 500, "offset": 0, "start": 1234},
        {"user": "0xabc", "limit": 200, "offset": 500, "start": 1234},
    ]
