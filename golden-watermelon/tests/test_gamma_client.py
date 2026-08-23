from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from polybot.api.gamma_client import GammaClient
from polybot.config import GammaConfig, load_config


ROOT = Path(__file__).resolve().parents[1]


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


def config(max_pages: int = 4) -> GammaConfig:
    gamma = load_config(
        ROOT / "config.yaml", "watermelon-white-1m-v3a"
    ).trading.gamma
    return replace(
        gamma,
        max_pages=max_pages,
        max_retries=0,
    )


def test_server_filters_live_sports_events_without_volume_or_liquidity_gate() -> None:
    transport = FakeTransport([{"events": [{"id": "1", "markets": []}]}])
    result = GammaClient(config(), transport).fetch_live_events(
        "run", observed_at=datetime(2026, 8, 22, 15, 30, tzinfo=timezone.utc)
    )
    assert result.cursor_complete is True
    method, url, kwargs = transport.calls[0]
    params = kwargs["params"]
    assert method == "GET"
    assert url.endswith("/events/keyset")
    assert params["tag_id"] == 100350
    assert params["related_tags"] == "false"
    assert "tag_slug" not in params
    assert params["live"] == "true"
    assert params["closed"] == "false"
    assert params["limit"] == 500
    assert "liquidity_min" not in params
    assert "volume_min" not in params
    assert "offset" not in params


def test_keyset_cursor_is_forwarded() -> None:
    transport = FakeTransport(
        [
            {"events": [], "next_cursor": "next"},
            {"events": []},
        ]
    )
    result = GammaClient(config(), transport).fetch_live_events(
        "run", observed_at=datetime(2026, 8, 22, 15, 30, tzinfo=timezone.utc)
    )
    assert result.cursor_complete is True
    assert transport.calls[1][2]["params"]["after_cursor"] == "next"


def test_page_cap_returns_incomplete() -> None:
    transport = FakeTransport([{"events": [], "next_cursor": "next"}])
    result = GammaClient(config(max_pages=1), transport).fetch_live_events(
        "run", observed_at=datetime(2026, 8, 22, 15, 30, tzinfo=timezone.utc)
    )
    assert result.cursor_complete is False
