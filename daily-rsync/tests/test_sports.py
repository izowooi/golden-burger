from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from daily_rsync.sports import calculate
from daily_rsync.web import create_app


def evidence():
    return {
        "points": [
            [1000, 0, None, None, None, None, None, None, 0, 0],
            [1060, 0, None, None, None, None, None, None, 0, 0],
        ],
        "depth": [
            {
                "book": {
                    "asks": [{"price": ".5", "size": "4"}, {"price": ".75", "size": "20"}],
                    "bids": [],
                },
                "fee_market": {"feesEnabled": False},
                "market_open": True,
            },
            {
                "book": {
                    "asks": [],
                    "bids": [{"price": ".9", "size": "5"}, {"price": ".8", "size": "20"}],
                },
                "fee_market": {"feesEnabled": False},
                "market_open": True,
            },
        ],
        "gaps": [],
    }


def test_actual_holding_quantity_walks_both_full_books():
    # $2 gets 4 shares, $3 gets another 4. Sell 5 @ .9 and 3 @ .8.
    result = calculate(evidence(), 0, 1, 5)
    assert result["shares"] == 8
    assert result["proceeds"] == 6.9
    assert result["net_pnl"] == 1.9
    assert result["buy_vwap"] == 0.625


def test_gap_count_includes_selected_token_and_global_failures_only():
    data = evidence()
    data["gaps"] = [
        {"start": 1010, "end": 1040, "token": 0},
        {"start": 1010, "end": 1040, "token": 1},
        {"start": 1020, "end": 1030},
    ]
    assert calculate(data, 0, 1, 5)["intervening_gaps"] == 2


def test_unknown_fees_are_never_treated_as_zero():
    data = evidence()
    data["depth"][0]["fee_market"] = {}
    result = calculate(data, 0, 1, 5)
    assert result["net_pnl"] is None
    assert result["gross_pnl"] == 1.9
    assumed = calculate(data, 0, 1, 5, fee_rate=0.05)
    assert assumed["fee_basis"] == "ASSUMED"
    assert assumed["net_pnl"] == pytest.approx(1.766)


@pytest.mark.parametrize(
    "schedule",
    [[], {"exponent": 1, "takerOnly": True}, {"exponent": True, "takerOnly": True, "rate": 0.05}],
)
def test_incomplete_fee_schedule_preserves_gross_with_unknown_net(schedule):
    data = evidence()
    data["depth"][0]["fee_market"] = {"feesEnabled": True, "feeSchedule": schedule}
    result = calculate(data, 0, 1, 5)
    assert result["gross_pnl"] == 1.9
    assert result["net_pnl"] is None


@pytest.mark.parametrize(
    "mutation", ["partial", "failed", "closed", "different_token", "same_time"]
)
def test_unsupported_execution_is_rejected(mutation):
    data = deepcopy(evidence())
    if mutation == "partial":
        data["depth"][1]["book"]["bids"] = [{"price": ".9", "size": "1"}]
    elif mutation == "failed":
        data["points"][1][8] = 1
    elif mutation == "closed":
        data["depth"][1]["market_open"] = False
    elif mutation == "different_token":
        data["points"][1][1] = 1
    else:
        data["points"][1][0] = data["points"][0][0]
    with pytest.raises(ValueError):
        calculate(data, 0, 1, 5)


def test_sports_ui_never_starts_remote_scan_or_sync(app_config, monkeypatch):
    app = create_app(app_config)

    def forbidden(*args, **kwargs):
        raise AssertionError("remote access from sports page")

    monkeypatch.setattr(app.state.service, "scan", forbidden)
    monkeypatch.setattr(app.state.service, "execute", forbidden)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/sports").status_code == 200
    assert client.get("/api/sports/sources").json() == []
    assert client.get("/api/sports/index").json()["matches"] == []
    javascript = client.get("/static/sports.js").text
    assert "/api/jobs" not in javascript
    assert "/sync" not in javascript
    response = client.post(
        "/api/sports/import",
        headers={"Origin": "https://foreign.example"},
        json={"source_keys": ["x"], "start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"},
    )
    assert response.status_code == 403
    assert client.get("/api/sports/matches/not-a-valid-id").status_code == 400


def test_browser_cannot_calculate_using_replaced_point_indices(app_config, monkeypatch):
    app = create_app(app_config)
    monkeypatch.setattr(app.state.sports, "match", lambda *a, **k: {"view_version": "b" * 32})
    client = TestClient(app, base_url="http://localhost")
    response = client.post(
        "/api/sports/matches/" + "1" * 20 + "/calculate",
        json={"view_version": "a" * 32, "buy_index": 0, "sell_index": 1, "amount": 5},
    )
    assert response.status_code == 409
    assert (
        TestClient(app, base_url="http://foreign.example").get("/api/sports/index").status_code
        == 403
    )


def test_mismatched_book_token_and_duplicate_levels_are_rejected():
    data = evidence()
    data["tokens"] = [{"token_id": "correct"}]
    data["depth"][0]["book"]["token_id"] = "wrong"
    with pytest.raises(ValueError, match="token"):
        calculate(data, 0, 1, 5)
    data["depth"][0]["book"]["token_id"] = "correct"
    data["depth"][0]["book"]["asks"].append({"price": ".5", "size": "30"})
    with pytest.raises(ValueError, match="잔량"):
        calculate(data, 0, 1, 5)
