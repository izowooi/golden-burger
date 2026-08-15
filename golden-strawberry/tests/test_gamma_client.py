from __future__ import annotations

import json

import pytest

from polybot.api.gamma_client import GammaClient
from polybot.utils.retry import JsonResponse


class Transport:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        payload = self.payloads.pop(0)
        raw = json.dumps(payload).encode()
        number = len(self.calls)
        return JsonResponse(
            payload=payload,
            raw=raw,
            request_id=f"request-{number}",
            request_hash=f"hash-{number}",
            started_at="2026-08-15T02:00:00Z",
            received_at=f"2026-08-15T02:0{number}:00Z",
            response_sha256=f"sha-{number}",
        )


def test_gamma_complete_zero_filter_pagination(config):
    transport = Transport(
        [
            {"markets": [{"id": "1"}], "next_cursor": "cursor-1"},
            {"markets": [{"id": "2"}]},
        ]
    )
    sweep = GammaClient(config.trading.gamma, transport).collect_market_sweep("run")
    assert sweep.cursor_complete is True
    assert len(sweep.pages) == 2
    assert [row["id"] for row in sweep.markets] == ["1", "2"]
    first = transport.calls[0][2]["params"]
    second = transport.calls[1][2]["params"]
    assert first["limit"] == 100
    assert first["liquidity_num_min"] == 0
    assert first["volume_num_min"] == 0
    assert first["include_tag"] == "true"
    assert "after_cursor" not in first
    assert second["after_cursor"] == "cursor-1"


def test_gamma_repeated_cursor_fails(config):
    transport = Transport(
        [
            {"markets": [], "next_cursor": "same"},
            {"markets": [], "next_cursor": "same"},
        ]
    )
    with pytest.raises(RuntimeError, match="repeated cursor"):
        GammaClient(config.trading.gamma, transport).collect_market_sweep("run")


def test_gamma_over_budget_fails_without_terminal(config):
    gamma = config.trading.gamma.__class__(
        **{**config.trading.gamma.__dict__, "max_pages": 2}
    )
    transport = Transport(
        [
            {"markets": [], "next_cursor": "one"},
            {"markets": [], "next_cursor": "two"},
        ]
    )
    with pytest.raises(RuntimeError, match="exceeded max_pages"):
        GammaClient(gamma, transport).collect_market_sweep("run")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"markets": {}},
        {"markets": ["not-object"]},
        {"markets": [], "next_cursor": 4},
    ],
)
def test_gamma_malformed_page_fails(config, payload):
    with pytest.raises(ValueError):
        GammaClient(config.trading.gamma, Transport([payload])).collect_market_sweep(
            "run"
        )


def test_resolution_lookup_explicit_missing_and_closed_filter(config):
    transport = Transport([[{"conditionId": "condition-a", "closed": True}]])
    rows = GammaClient(config.trading.gamma, transport).fetch_resolutions(
        "run", ["condition-a", "condition-b"]
    )
    assert [row.lookup_status for row in rows] == ["OBSERVED", "MISSING"]
    params = transport.calls[0][2]["params"]
    assert params["closed"] == "true"
    assert params["condition_ids"] == ["condition-a", "condition-b"]
