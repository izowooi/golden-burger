from __future__ import annotations

import json

import pytest

from polybot.api.gamma_client import GammaClient, GammaFamilyPool
from polybot.api.transport import CycleBudget, JsonResponse


class FakeTransport:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        payload = self.payloads.pop(0)
        raw = json.dumps(payload).encode()
        return JsonResponse(
            request_id=f"request-{len(self.calls)}",
            received_at=f"2026-08-27T00:00:0{len(self.calls)}Z",
            response_sha256="a" * 64,
            raw=raw,
            payload=payload,
            http_status=200,
        )


def budget():
    return CycleBudget(started_monotonic=0, monotonic=lambda: 0)


def test_cursor_completion_and_exact_envelope(config):
    transport = FakeTransport(
        [
            {"events": [{"id": "1"}], "next_cursor": "next"},
            {"events": [{"id": "2"}], "next_cursor": None},
        ]
    )
    client = GammaClient(config.trading.gamma, transport)
    sweep = client.fetch_family_events(
        "run",
        config.registry.by_code["nba"],
        budget=budget(),
        slot_start="2026-08-27T00:00:00Z",
    )
    assert sweep.cursor_complete is True
    assert len(sweep.pages) == 2
    assert transport.calls[0][2]["params"] == {
        "limit": 500,
        "closed": "false",
        "include_children": "false",
        "tag_id": 745,
        "related_tags": "false",
        "start_time_min": "2026-08-26T00:00:00Z",
        "start_time_max": "2026-08-29T00:00:00Z",
    }
    assert sweep.start_time_min == "2026-08-26T00:00:00Z"
    assert sweep.start_time_max == "2026-08-29T00:00:00Z"
    assert not {
        "start_date_min",
        "start_date_max",
    } & transport.calls[0][2]["params"].keys()
    assert "live" not in transport.calls[0][2]["params"]
    assert transport.calls[1][2]["params"]["after_cursor"] == "next"


def test_repeated_cursor_fails(config):
    transport = FakeTransport(
        [
            {"events": [], "next_cursor": "same"},
            {"events": [], "next_cursor": "same"},
        ]
    )
    with pytest.raises(ValueError, match="cursor repeated"):
        GammaClient(config.trading.gamma, transport).fetch_family_events(
            "run",
            config.registry.by_code["nhl"],
            budget=budget(),
            slot_start="2026-08-27T00:00:00Z",
        )


def test_page_cap_returns_incomplete(config):
    gamma = config.trading.gamma
    object.__setattr__(gamma, "max_pages_per_family", 2)
    transport = FakeTransport(
        [
            {"events": [], "next_cursor": "a"},
            {"events": [], "next_cursor": "b"},
        ]
    )
    sweep = GammaClient(gamma, transport).fetch_family_events(
        "run",
        config.registry.by_code["mlb"],
        budget=budget(),
        slot_start="2026-08-27T00:00:00Z",
    )
    assert sweep.cursor_complete is False
    assert sweep.terminal_cursor == "tag_id=100381;after_cursor=b"


def test_soccer_uses_all_frozen_competition_query_tags(config):
    soccer = config.registry.by_code["soccer"]
    transport = FakeTransport(
        [{"events": [], "next_cursor": None} for _ in soccer.query_tag_ids]
    )
    sweep = GammaClient(config.trading.gamma, transport).fetch_family_events(
        "run",
        soccer,
        budget=budget(),
        slot_start="2026-08-27T00:00:00Z",
    )
    assert sweep.cursor_complete is True
    assert len(sweep.pages) == len(soccer.query_tag_ids) == 8
    assert tuple(call[2]["params"]["tag_id"] for call in transport.calls) == (
        306,
        1494,
        102070,
        780,
        100100,
        101962,
        100977,
        101787,
    )
    assert all("start_date_min" not in call[2]["params"] for call in transport.calls)


def test_malformed_page_rejected(config):
    transport = FakeTransport([{"events": "not-an-array", "next_cursor": None}])
    with pytest.raises(ValueError, match="array"):
        GammaClient(config.trading.gamma, transport).fetch_family_events(
            "run",
            config.registry.by_code["soccer"],
            budget=budget(),
            slot_start="2026-08-27T00:00:00Z",
        )


def test_followup_is_event_by_decimal_id(config):
    transport = FakeTransport([{"id": "910001", "closed": True}])
    result = GammaClient(config.trading.gamma, transport).fetch_event(
        "run", "910001", "soccer", budget=budget()
    )
    assert result.event_id == "910001"
    assert transport.calls[0][1].endswith("/events/910001")
    assert transport.calls[0][2]["params"] == {}


def test_family_pool_routes_followup_to_the_matching_isolated_client(config):
    transports = {
        family.code: FakeTransport(
            [{"id": "910001", "closed": True}]
            if family.code == "nba"
            else []
        )
        for family in config.registry.families
    }
    pool = GammaFamilyPool(
        {
            family.code: GammaClient(config.trading.gamma, transports[family.code])
            for family in config.registry.families
        },
        max_workers=len(config.registry.families),
    )

    result = pool.fetch_event(
        "run", "910001", "nba", budget=budget()
    )

    assert result.event_id == "910001"
    assert len(transports["nba"].calls) == 1
    assert all(
        not transport.calls
        for family, transport in transports.items()
        if family != "nba"
    )


def test_family_pool_rejects_unknown_followup_family(config):
    pool = GammaFamilyPool(
        {
            family.code: GammaClient(config.trading.gamma, FakeTransport([]))
            for family in config.registry.families
        },
        max_workers=len(config.registry.families),
    )

    with pytest.raises(ValueError, match="outside the frozen registry"):
        pool.fetch_event("run", "910001", "wnba", budget=budget())


def test_discovery_requires_exact_utc_slot(config):
    with pytest.raises(ValueError, match="exact UTC"):
        GammaClient(config.trading.gamma, FakeTransport([])).fetch_family_events(
            "run",
            config.registry.by_code["nba"],
            budget=budget(),
            slot_start="2026-08-27T00:00:00",
        )
