from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Lock, get_ident
from types import SimpleNamespace

from polybot.api.gamma_client import GammaClient
from polybot.config import GammaConfig, load_config
from polybot.utils.retry import NetworkBudgetExceeded


ROOT = Path(__file__).resolve().parents[1]


class FakeTransport:
    def __init__(self, payloads=None, *, state=None):
        self.state = state or {
            "payloads": list(payloads or []),
            "calls": [],
            "lock": Lock(),
        }
        self.calls = self.state["calls"]

    def fork(self):
        return FakeTransport(state=self.state)

    def close(self):
        pass

    def request_json(self, method, url, **kwargs):
        with self.state["lock"]:
            self.calls.append((method, url, kwargs))
            payload = self.state["payloads"].pop(0)
        return SimpleNamespace(
            payload=payload,
            raw=b"{}",
            request_id=f"r{len(self.calls)}",
            received_at="2026-08-22T15:31:01Z",
            response_sha256="a" * 64,
        )


def config(max_pages: int = 4) -> GammaConfig:
    gamma = load_config(
        ROOT / "config.yaml", "watermelon-white-1m-v4b"
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


def test_five_families_use_independent_numeric_tag_cursors() -> None:
    transport = FakeTransport(
        [
            {"events": [{"id": "soccer"}]},
            {"events": [{"id": "mlb"}]},
            {"events": [{"id": "nba"}]},
            {"events": [{"id": "nfl"}]},
            {"events": [{"id": "nhl"}]},
        ]
    )
    result = GammaClient(config(), transport).fetch_live_families(
        "run", observed_at=datetime(2026, 8, 29, 0, 0, tzinfo=timezone.utc)
    )
    assert result.cursor_complete is True
    assert [page.sport_family for page in result.pages] == [
        "soccer", "mlb", "nba", "nfl", "nhl"
    ]
    assert {call[2]["params"]["tag_id"] for call in transport.calls} == {
        100350, 100381, 745, 450, 899
    }
    assert {call[2]["request_kind"] for call in transport.calls} == {
        "gamma_live_events_keyset:soccer",
        "gamma_live_events_keyset:mlb",
        "gamma_live_events_keyset:nba",
        "gamma_live_events_keyset:nfl",
        "gamma_live_events_keyset:nhl",
    }


def test_five_family_fanout_is_concurrent_but_results_stay_frozen_order() -> None:
    barrier = Barrier(5)
    thread_ids: set[int] = set()
    lock = Lock()

    class BarrierTransport:
        def fork(self):
            return BarrierTransport()

        def close(self):
            pass

        def request_json(self, method, url, **kwargs):
            del method, url
            with lock:
                thread_ids.add(get_ident())
            barrier.wait(timeout=2)
            family = kwargs["request_kind"].rsplit(":", 1)[-1]
            return SimpleNamespace(
                payload={"events": [{"id": family}]},
                raw=b"{}",
                request_id=f"request-{family}",
                received_at="2026-09-04T00:00:00Z",
                response_sha256="a" * 64,
            )

    result = GammaClient(config(), BarrierTransport()).fetch_live_families(
        "run", observed_at=datetime(2026, 9, 4, tzinfo=timezone.utc)
    )

    assert [page.sport_family for page in result.pages] == [
        "soccer", "mlb", "nba", "nfl", "nhl"
    ]
    assert len(thread_ids) == 5


def test_closed_gamma_resolution_fallback_preserves_token_aligned_void() -> None:
    transport = FakeTransport(
        [
            [],
            [{
                "conditionId": "condition-void",
                "closed": True,
                "umaResolutionStatus": "resolved",
                "outcomes": ["Home", "Away"],
                "clobTokenIds": ["home-token", "away-token"],
                "outcomePrices": [0.5, 0.5],
            }],
        ]
    )
    result = GammaClient(config(), transport).fetch_market_resolution(
        "run", "condition-void"
    )
    assert result.status == "RESOLVED_VOID"
    assert result.winner_index is None
    assert result.outcomes == ("Home", "Away")
    assert result.token_ids == ("home-token", "away-token")
    assert result.payouts == (0.5, 0.5)
    assert [call[2]["params"]["closed"] for call in transport.calls] == [
        "false", "true"
    ]


def test_closed_half_half_without_resolved_authority_remains_unresolved() -> None:
    transport = FakeTransport(
        [
            [],
            [{
                "conditionId": "condition-half",
                "closed": True,
                "umaResolutionStatus": "proposed",
                "outcomes": ["Home", "Away"],
                "clobTokenIds": ["home-token", "away-token"],
                "outcomePrices": [0.5, 0.5],
            }],
        ]
    )
    result = GammaClient(config(), transport).fetch_market_resolution(
        "run", "condition-half"
    )
    assert result.status == "CLOSED_UNRESOLVED"
    assert result.winner_index is None


def test_budget_exhaustion_marks_every_unfinished_family_and_never_succeeds() -> None:
    class ExhaustedTransport:
        def fork(self):
            return ExhaustedTransport()

        def close(self):
            pass

        def request_json(self, *_args, **_kwargs):
            raise NetworkBudgetExceeded("network_budget_exhausted")

    result = GammaClient(config(), ExhaustedTransport()).fetch_live_families(
        "run", observed_at=datetime(2026, 9, 4, tzinfo=timezone.utc)
    )
    assert result.cursor_complete is False
    assert result.pages == ()
    assert result.incomplete_families == (
        "soccer", "mlb", "nba", "nfl", "nhl"
    )
    assert len(result.incomplete_reasons) == 5
