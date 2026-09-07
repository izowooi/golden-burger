"""Sidecar source contracts: exact lookup, terminal alignment, source payloads."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from polybot.api.gamma_client import GammaClient
from polybot.db.raw_repository import RawRepository
from polybot.research_raw import _book_status, event_identity, terminal_proof
from test_collector import ROOT
from test_gamma_client import config
from test_raw_followup_isolation import _soccer_event


class Transport:
    def __init__(self, payloads):
        self.payloads, self.calls = iter(payloads), []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        payload = next(self.payloads)
        raw = json.dumps(payload).encode()
        return SimpleNamespace(payload=payload, raw=raw, request_id=f"r{len(self.calls)}",
            received_at="2026-09-07T00:00:01Z", response_sha256=hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize(("payload", "status"), [
    ([], "MISSING"), ({}, "MALFORMED"),
    ([{"id": "other"}], "IDENTITY_MISMATCH"),
    ([{"id": "1"}, {"id": "1"}], "IDENTITY_MISMATCH"),
    ([{"id": "1", "closed": True, "live": False}], "OBSERVED"),
])
def test_event_lookup_keeps_raw_without_live_closed_filters(payload, status):
    transport = Transport([payload])
    result = GammaClient(config(), transport).fetch_event_by_id("run", "1")
    assert result.status == status
    assert json.loads(result.raw) == payload
    assert transport.calls[0][2]["params"] == {"id": "1", "limit": 2}


def test_nonterminal_gamma_responses_are_available_to_sidecar():
    market = {"conditionId": "c", "closed": False}
    result = GammaClient(config(), Transport([[market]])).fetch_market_resolution("run", "c")
    assert result.status == "OPEN"
    assert json.loads(result.raw_responses[0]["raw"]) == [market]
    result = GammaClient(config(), Transport([[], [{"conditionId": "c", "closed": True}]])).fetch_market_resolution("run", "c")
    assert result.status == "CLOSED_UNRESOLVED"
    assert len(result.raw_responses) == 2
    assert json.loads(result.raw_responses[0]["raw"]) == []


def terminal_event():
    event = _soccer_event()
    for index, market in enumerate(event["markets"]):
        market.update(closed=True, outcomePrices=json.dumps([1, 0] if index == 0 else [0, 1]))
    return event


def test_terminal_requires_aligned_all_three_results_and_does_not_infer_ended():
    event = terminal_event()
    slots, valid = event_identity(event, "soccer", config())
    assert valid
    assert terminal_proof(event, slots, "soccer") is not None
    bad = deepcopy(event)
    bad["markets"][1]["outcomePrices"] = "[1,0]"
    assert terminal_proof(bad, slots, "soccer") is None
    bad = deepcopy(event)
    bad["markets"][0]["clobTokenIds"] = '["other","other-no"]'
    assert terminal_proof(bad, slots, "soccer") is None
    bad = deepcopy(event)
    bad["markets"][0]["outcomePrices"] = [True, False]
    assert terminal_proof(bad, slots, "soccer") is None
    bad = deepcopy(event)
    bad["markets"][0]["closed"] = False
    bad["ended"] = True
    assert terminal_proof(bad, slots, "soccer") is None


def test_void_requires_authoritative_status_for_all_conditions():
    event = terminal_event()
    slots, _ = event_identity(event, "soccer", config())
    for market in event["markets"]:
        market["outcomePrices"] = "[0.5,0.5]"
    assert terminal_proof(event, slots, "soccer") is None
    for market in event["markets"]:
        market["umaResolutionStatus"] = "resolved"
    assert terminal_proof(event, slots, "soccer") is not None


@pytest.mark.parametrize(("bids", "asks", "status"), [([], [], "EMPTY_BOOK"),
    ([], [{"price": ".5", "size": "1"}], "EMPTY_BIDS"),
    ([{"price": ".5", "size": "1"}], [], "EMPTY_ASKS"),
    ([{"price": True, "size": "1"}], [], "MALFORMED"),
    ([{"price": ".5", "size": "nan"}], [], "MALFORMED")])
def test_raw_book_preserves_empty_sides_but_rejects_malformed_levels(bids, asks, status):
    assert _book_status({"asset_id": "a", "bids": bids, "asks": asks}, "a") == status


def test_foreign_shadow_database_is_not_migrated(tmp_path):
    path = tmp_path / "shadow.db"
    path.write_bytes(b"existing other application")
    before = path.read_bytes()
    with pytest.raises(Exception):
        RawRepository(tmp_path / "trades_sim.db")
    assert path.read_bytes() == before
