from __future__ import annotations

from dataclasses import replace

import pytest

from polybot.api.gamma_client import GammaClient
from polybot.config import PROJECT_ROOT, load_config
from polybot.utils.retry import JsonResponse


class FakeTransport:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        payload = self.payloads.pop(0)
        return JsonResponse(payload, b"{}", f"r{len(self.calls)}", "2026-08-13T00:00:00Z", "2026-08-13T00:00:01Z", "0" * 64)


def test_keyset_walks_to_terminal_cursor(monkeypatch):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    transport = FakeTransport(
        [
            {"markets": [{"id": "1"}], "next_cursor": "next"},
            {"markets": [{"id": "2"}], "next_cursor": None},
        ]
    )
    sweep = GammaClient(config.trading.gamma, transport).collect_market_sweep("run")
    assert sweep.cursor_complete is True
    assert [item["id"] for item in sweep.markets] == ["1", "2"]
    assert transport.calls[1][2]["params"]["after_cursor"] == "next"
    assert "order" not in transport.calls[0][2]["params"]


def test_repeated_cursor_fails(monkeypatch):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-v3-shard-0")
    transport = FakeTransport(
        [
            {"markets": [], "next_cursor": "same"},
            {"markets": [], "next_cursor": "same"},
        ]
    )
    with pytest.raises(RuntimeError, match="repeated cursor"):
        GammaClient(config.trading.gamma, transport).collect_market_sweep("run")
