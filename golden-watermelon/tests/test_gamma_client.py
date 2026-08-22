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
            payload=payload,
            raw=b"{}",
            request_id=f"r{len(self.calls)}",
            received_at="2026-08-22T15:31:01Z",
            response_sha256="a" * 64,
        )


def config(max_pages: int = 10) -> GammaConfig:
    return GammaConfig(
        "https://gamma-api.polymarket.com",
        100,
        max_pages,
        24,
        24,
        ("moneyline",),
        3.05,
        30,
        0,
        1,
        2,
    )


def test_server_filters_moneyline_without_volume_or_liquidity_gate() -> None:
    transport = FakeTransport([{"markets": [{"id": "1"}]}])
    result = GammaClient(config(), transport).fetch_moneyline_markets(
        "run", observed_at=datetime(2026, 8, 22, 15, 30, tzinfo=timezone.utc)
    )
    assert result.cursor_complete is True
    method, url, kwargs = transport.calls[0]
    params = kwargs["params"]
    assert method == "GET"
    assert url.endswith("/markets/keyset")
    assert params["sports_market_types"] == ["moneyline"]
    assert params["closed"] == "false"
    assert params["limit"] == 100
    assert "end_date_min" in params and "end_date_max" in params
    assert "liquidity_num_min" not in params
    assert "volume_num_min" not in params
    assert "offset" not in params


def test_keyset_cursor_is_forwarded() -> None:
    transport = FakeTransport(
        [
            {"markets": [], "next_cursor": "next"},
            {"markets": []},
        ]
    )
    result = GammaClient(config(), transport).fetch_moneyline_markets(
        "run", observed_at=datetime(2026, 8, 22, 15, 30, tzinfo=timezone.utc)
    )
    assert result.cursor_complete is True
    assert transport.calls[1][2]["params"]["after_cursor"] == "next"


def test_page_cap_returns_incomplete() -> None:
    transport = FakeTransport([{"markets": [], "next_cursor": "next"}])
    result = GammaClient(config(max_pages=1), transport).fetch_moneyline_markets(
        "run", observed_at=datetime(2026, 8, 22, 15, 30, tzinfo=timezone.utc)
    )
    assert result.cursor_complete is False
