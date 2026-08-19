from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from polybot.api.gamma_client import GammaClient
from polybot.config import GammaConfig


class FakeTransport:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        payload = self.payloads.pop(0)
        return SimpleNamespace(
            payload=payload, raw=b"{}", request_id=f"r{len(self.calls)}",
            received_at="2026-08-20T00:00:01Z", response_sha256="a" * 64,
        )


def config(max_pages: int = 4) -> GammaConfig:
    return GammaConfig("https://gamma-api.polymarket.com", 500, max_pages, 10000, 5000, 6, 3.05, 30, 0, 1, 2)


def test_server_filters_and_terminal_cursor() -> None:
    transport = FakeTransport([{"events": [{"id": "1", "markets": []}]}])
    result = GammaClient(config(), transport).fetch_sports_events("run", observed_at=datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert result.cursor_complete is True
    params = transport.calls[0][2]["params"]
    assert params["tag_slug"] == "sports"
    assert params["liquidity_min"] == 10000
    assert params["volume_min"] == 5000
    assert params["limit"] == 500
    assert "end_date_min" in params and "end_date_max" in params
    assert "offset" not in params


def test_keyset_cursor_is_forwarded() -> None:
    transport = FakeTransport([
        {"events": [], "next_cursor": "next"},
        {"events": []},
    ])
    result = GammaClient(config(), transport).fetch_sports_events("run", observed_at=datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert result.cursor_complete is True
    assert transport.calls[1][2]["params"]["after_cursor"] == "next"


def test_page_cap_returns_incomplete() -> None:
    transport = FakeTransport([{"events": [], "next_cursor": "next"}])
    result = GammaClient(config(max_pages=1), transport).fetch_sports_events("run", observed_at=datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert result.cursor_complete is False
