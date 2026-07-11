"""Portfolio is valued from the authoritative accounting snapshot.

Summing /positions currentValue undercounts resolved-but-unredeemed winners
(they report currentValue=0 until claimed), which the resolution-momentum
strategy accumulates. The snapshot's positionsValue/equity is the source of
truth and is what Polymarket shows as the account Portfolio value.
"""

import csv
import io
import json
import zipfile

import requests

from polybot_reporter.api.data_api_client import DataAPIClient


def _zip_response(rows: list[dict]) -> requests.Response:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("equity.csv", buffer.getvalue())

    value = requests.Response()
    value.status_code = 200
    value._content = archive.getvalue()
    value.url = "https://data-api.polymarket.com/v1/accounting/snapshot"
    return value


def _json_response(payload) -> requests.Response:
    value = requests.Response()
    value.status_code = 200
    value._content = json.dumps(payload).encode()
    value.url = "https://data-api.polymarket.com/positions"
    value.headers["Content-Type"] = "application/json"
    return value


def test_portfolio_value_comes_from_snapshot_not_positions_sum(monkeypatch):
    # A resolved-but-unredeemed winner: 100 shares now worth $1 each, but the
    # /positions endpoint reports currentValue=0 until it is redeemed.
    positions = [
        {"outcome": "Yes", "currentValue": 50.0, "realizedPnl": 0, "cashPnl": 5.0},
        {"outcome": "Yes", "currentValue": 0.0, "size": 100, "realizedPnl": 0, "cashPnl": 0},
    ]
    # Snapshot (authoritative): positions worth 150 (= 50 live + 100 redeemable winner).
    snapshot_rows = [{"cashBalance": "10.00", "positionsValue": "150.00", "equity": "160.00"}]

    def fake_get(url, *args, **kwargs):
        if "/positions" in url:
            return _json_response(positions)
        if "/v1/accounting/snapshot" in url:
            return _zip_response(snapshot_rows)
        if "/trades" in url:
            return _json_response([])
        raise AssertionError(f"unexpected URL: {url}")

    client = DataAPIClient()
    monkeypatch.setattr(client.session, "get", fake_get)

    summary = client.get_portfolio_summary("0x" + "0" * 40)

    # Sourced from the snapshot, NOT from the /positions currentValue sum (which is 50).
    assert summary["position_value"] == 150.0
    assert summary["cash_balance"] == 10.0
    assert summary["total_value"] == 160.0
    # /positions is still used for the open-position count and P&L breakdown.
    assert summary["num_positions"] == 2


def test_equity_snapshot_parses_all_three_fields(monkeypatch):
    snapshot_rows = [
        {"cashBalance": "2892.055617", "positionsValue": "1349.706482", "equity": "4241.762099"}
    ]
    client = DataAPIClient()

    def fake_get(*_args, timeout, **_kwargs):
        assert timeout == (5, 30)
        return _zip_response(snapshot_rows)

    monkeypatch.setattr(client.session, "get", fake_get)

    snap = client.get_equity_snapshot("0x" + "0" * 40)

    assert snap["cash_balance"] == 2892.055617
    assert snap["position_value"] == 1349.706482
    assert snap["total_value"] == 4241.762099
    # Backward-compatible cash accessor still works.
    assert client.get_cash_balance("0x" + "0" * 40) == 2892.055617


def test_portfolio_accepts_small_equity_rounding_difference(monkeypatch):
    snapshot_rows = [
        {"cashBalance": "10.00", "positionsValue": "20.00", "equity": "30.019"}
    ]

    def fake_get(url, *args, **kwargs):
        if "/positions" in url:
            return _json_response([])
        return _zip_response(snapshot_rows)

    client = DataAPIClient()
    monkeypatch.setattr(client.session, "get", fake_get)

    assert client.get_portfolio_summary("0x" + "0" * 40)["total_value"] == 30.019


def test_portfolio_rejects_equity_breakdown_mismatch(monkeypatch):
    snapshot_rows = [
        {"cashBalance": "10.00", "positionsValue": "20.00", "equity": "31.00"}
    ]

    def fake_get(url, *args, **kwargs):
        if "/positions" in url:
            return _json_response([])
        return _zip_response(snapshot_rows)

    client = DataAPIClient()
    monkeypatch.setattr(client.session, "get", fake_get)

    import pytest

    with pytest.raises(ValueError, match=r"positionsValue \+ cashBalance"):
        client.get_portfolio_summary("0x" + "0" * 40)
