from __future__ import annotations

import json

import pytest

from polybot.api.gamma_client import GammaClient
from polybot.api.sampling_client import SamplingMarketClient
from polybot.config import SamplingConfig
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
            started_at="2026-08-15T04:00:00Z",
            received_at=f"2026-08-15T04:0{number}:00Z",
            response_sha256=f"sha-{number}",
        )


def test_sampling_complete_pagination_and_terminal_sentinel(config):
    transport = Transport(
        [
            {
                "limit": 1000,
                "count": 1,
                "data": [{"id": "1"}],
                "next_cursor": "cursor-1",
            },
            {"limit": 1000, "count": 1, "data": [{"id": "2"}], "next_cursor": "LTE="},
        ]
    )
    sweep = SamplingMarketClient(
        config.trading.sampling, transport
    ).collect_market_sweep("run")
    assert sweep.cursor_complete is True
    assert len(sweep.pages) == 2
    assert [row["id"] for row in sweep.markets] == ["1", "2"]
    first = transport.calls[0][2]["params"]
    second = transport.calls[1][2]["params"]
    assert first == {}
    assert second["next_cursor"] == "cursor-1"


def test_sampling_repeated_cursor_fails(config):
    transport = Transport(
        [
            {"limit": 1000, "count": 1, "data": [{"id": "1"}], "next_cursor": "same"},
            {"limit": 1000, "count": 1, "data": [{"id": "2"}], "next_cursor": "same"},
        ]
    )
    with pytest.raises(RuntimeError, match="repeated cursor"):
        SamplingMarketClient(config.trading.sampling, transport).collect_market_sweep(
            "run"
        )


def test_sampling_over_budget_fails_without_terminal(config):
    sampling = SamplingConfig(
        base_url=config.trading.sampling.base_url,
        page_size=1000,
        max_pages=2,
    )
    transport = Transport(
        [
            {"limit": 1000, "count": 1, "data": [{"id": "1"}], "next_cursor": "one"},
            {"limit": 1000, "count": 1, "data": [{"id": "2"}], "next_cursor": "two"},
        ]
    )
    with pytest.raises(RuntimeError, match="exceeded max_pages"):
        SamplingMarketClient(sampling, transport).collect_market_sweep("run")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"data": {}},
        {"data": ["not-object"]},
        {"data": [], "next_cursor": 4},
    ],
)
def test_sampling_malformed_page_fails(config, payload):
    with pytest.raises(ValueError):
        SamplingMarketClient(
            config.trading.sampling, Transport([payload])
        ).collect_market_sweep("run")


def test_sampling_full_terminal_page_is_rejected(config):
    payload = {
        "limit": 1000,
        "count": 1000,
        "data": [{"id": str(value)} for value in range(1000)],
        "next_cursor": "LTE=",
    }
    with pytest.raises(ValueError, match="continuation cursor"):
        SamplingMarketClient(
            config.trading.sampling, Transport([payload])
        ).collect_market_sweep("run")


def test_resolution_lookup_explicit_missing_and_closed_filter(config):
    transport = Transport([[{"conditionId": "condition-a", "closed": True}]])
    rows = GammaClient(config.trading.gamma, transport).fetch_resolutions(
        "run", ["condition-a", "condition-b"]
    )
    assert [row.lookup_status for row in rows] == ["OBSERVED", "MISSING"]
    params = transport.calls[0][2]["params"]
    assert params["closed"] == "true"
    assert params["condition_ids"] == ["condition-a", "condition-b"]


def test_metadata_lookup_is_open_and_tag_enriched(config):
    transport = Transport([[{"conditionId": "condition-a", "closed": False}]])
    rows = GammaClient(config.trading.gamma, transport).fetch_metadata(
        "run", ["condition-a"]
    )
    assert rows[0].lookup_status == "OBSERVED"
    params = transport.calls[0][2]["params"]
    assert params["closed"] == "false"
    assert params["include_tag"] == "true"
