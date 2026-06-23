"""Regression tests for upstream failures that must block DB snapshots."""

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


def test_positions_http_error_is_not_converted_to_empty_portfolio(monkeypatch):
    client = DataAPIClient()
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: response(400))

    with pytest.raises(requests.HTTPError):
        client.get_positions("0x0000000000000000000000000000000000000000")


def test_invalid_cash_snapshot_is_not_converted_to_zero(monkeypatch):
    client = DataAPIClient()
    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: response(200, b"bad zip"))

    with pytest.raises(zipfile.BadZipFile, match="File is not a zip file"):
        client.get_cash_balance("0x0000000000000000000000000000000000000000")
