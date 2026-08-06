"""Cursor-complete, page-clocked Gamma census contract."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from requests.exceptions import ChunkedEncodingError

from polybot.api.gamma_client import GammaClient
from polybot.config import GammaConfig


class Response:
    def __init__(self, payload, *, status_code: int = 200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.content = json.dumps(payload, sort_keys=True).encode("utf-8")

    def json(self):
        return self._payload


class SequenceSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, *, params, timeout):
        self.calls.append((url, dict(params), timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _client(session, **overrides):
    values = {
        "max_retries": 3,
        "retry_base_seconds": 0,
        "retry_max_seconds": 0.001,
        **overrides,
    }
    return GammaClient(GammaConfig(**values), session=session)


def test_gamma_fetches_every_page_and_preserves_variable_outcomes_and_volumes(
    monkeypatch,
):
    first_market = {
        "conditionId": "condition-a",
        "active": True,
        "closed": False,
        "outcomes": ["Yes", "No", "Invalid"],
        "clobTokenIds": ["token-a", "token-b", "token-c"],
        "outcomePrices": ["0.5", "0.4", "0.1"],
        "volume": "123456.78",
        "volume24hr": "901.23",
    }
    second_market = {
        "conditionId": "condition-b",
        "active": False,
        "closed": True,
        "outcomes": ["Alpha", "Beta", "Gamma", "Delta"],
        "clobTokenIds": ["t1", "t2", "t3", "t4"],
        "volume": "42",
        "volume24hr": None,
    }
    session = SequenceSession(
        Response({"markets": [first_market], "next_cursor": "cursor-1"}),
        Response({"markets": [second_market], "next_cursor": None}),
    )
    client = _client(session)
    gamma_clocks = iter(["2026-08-06T00:00:00+00:00", "2026-08-06T00:00:03+00:00"])
    request_clocks = iter(
        [
            "2026-08-06T00:00:00.100000+00:00",
            "2026-08-06T00:00:01+00:00",
            "2026-08-06T00:00:01.100000+00:00",
            "2026-08-06T00:00:02+00:00",
        ]
    )
    monkeypatch.setattr("polybot.api.gamma_client.utc_now", lambda: next(gamma_clocks))
    monkeypatch.setattr("polybot.utils.retry.utc_now", lambda: next(request_clocks))

    sweep = client.fetch_complete_sweep("sweep-1", cycle_number=7, run_id="run-1")

    assert sweep.sweep_id == "sweep-1"
    assert len(sweep.pages) == 2
    assert [page.received_at for page in sweep.pages] == [
        "2026-08-06T00:00:01+00:00",
        "2026-08-06T00:00:02+00:00",
    ]
    assert session.calls[0][0].endswith("/markets/keyset")
    assert session.calls[0][1]["closed"] == "false"
    assert session.calls[0][1]["include_tag"] == "true"
    assert "liquidity_num_min" not in session.calls[0][1]
    assert "volume_num_min" not in session.calls[0][1]
    assert "active" not in session.calls[0][1]
    assert session.calls[1][1]["after_cursor"] == "cursor-1"
    assert sweep.pages[0].markets[0]["outcomes"] == ["Yes", "No", "Invalid"]
    assert sweep.pages[1].markets[0]["outcomes"] == [
        "Alpha",
        "Beta",
        "Gamma",
        "Delta",
    ]
    flattened = sweep.markets
    assert flattened[0]["volume"] == "123456.78"
    assert flattened[0]["volume24hr"] == "901.23"
    assert flattened[1]["volume"] == "42"
    assert flattened[1]["volume24hr"] is None
    assert flattened[0]["_page_received_at"] != sweep.completed_at
    assert len(sweep.request_attestation_sha256) == 64


def test_repeated_cursor_aborts_without_returning_a_partial_sweep():
    session = SequenceSession(
        Response({"markets": [{"conditionId": "one"}], "next_cursor": "again"}),
        Response({"markets": [{"conditionId": "two"}], "next_cursor": "again"}),
    )
    client = _client(session)

    with pytest.raises(RuntimeError, match="cursor repeated"):
        client.fetch_complete_sweep("repeated", cycle_number=1)

    assert len(session.calls) == 2


def test_chunked_encoding_error_retries_same_cursor_and_attests_both_attempts(
    monkeypatch,
):
    records = []
    session = SequenceSession(
        ChunkedEncodingError("truncated public response"),
        Response({"markets": [{"conditionId": "complete"}], "next_cursor": None}),
    )
    sleeps = []
    monkeypatch.setattr("polybot.utils.retry.time.sleep", sleeps.append)
    client = GammaClient(
        GammaConfig(
            max_retries=2,
            retry_base_seconds=0.25,
            retry_max_seconds=1,
        ),
        session=session,
        evidence_sink=records.append,
    )

    sweep = client.fetch_complete_sweep("retry", cycle_number=1, run_id="run")

    assert len(sweep.pages) == 1
    assert len(session.calls) == 2
    assert session.calls[0][1] == session.calls[1][1]
    assert sleeps == [0.25]
    assert [record["status"] for record in records] == [
        "REQUEST_ERROR",
        "SUCCESS",
    ]
    assert [record["attempt_number"] for record in records] == [1, 2]
    assert sweep.pages[0].request_id == records[-1]["request_id"]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"markets": None, "next_cursor": None},
        {"markets": ["not-a-mapping"], "next_cursor": None},
    ],
)
def test_malformed_page_never_becomes_a_complete_sweep(payload):
    client = _client(SequenceSession(Response(payload)))

    with pytest.raises(ValueError, match="payload|markets"):
        client.fetch_complete_sweep("malformed", cycle_number=1)


def test_omitted_next_cursor_is_the_documented_terminal_page():
    client = _client(
        SequenceSession(Response({"markets": [{"conditionId": "terminal"}]}))
    )

    sweep = client.fetch_complete_sweep("missing-cursor", cycle_number=1)

    assert len(sweep.pages) == 1
    assert sweep.pages[0].next_cursor is None
    assert sweep.pages[0].markets[0]["conditionId"] == "terminal"


def test_page_limit_fails_instead_of_publishing_a_truncated_census():
    client = _client(
        SequenceSession(
            Response({"markets": [], "next_cursor": "one"}),
            Response({"markets": [], "next_cursor": "two"}),
        ),
        max_pages=1,
    )

    with pytest.raises(RuntimeError, match="page safety limit"):
        client.fetch_complete_sweep("too-many-pages", cycle_number=1)


def test_raw_page_sink_receives_each_successful_page_with_request_identity():
    raw_calls = []

    def sink(**kwargs):
        raw_calls.append(kwargs)
        return f"raw-{len(raw_calls)}"

    session = SequenceSession(
        Response({"markets": [], "next_cursor": "one"}),
        Response({"markets": [], "next_cursor": None}),
    )
    client = GammaClient(
        GammaConfig(max_retries=1),
        session=session,
        raw_payload_sink=sink,
        raw_payload_every_cycles=1,
    )

    sweep = client.fetch_complete_sweep("raw", cycle_number=2)

    assert [page.raw_payload_id for page in sweep.pages] == ["raw-1", "raw-2"]
    assert [call["kind"] for call in raw_calls] == [
        "gamma_markets_keyset_page",
        "gamma_markets_keyset_page",
    ]
    assert all(call["store_blob"] is True for call in raw_calls)
    assert all(call["content"] for call in raw_calls)


def test_client_never_adds_authorization_headers():
    session = SimpleNamespace(headers={}, get=lambda *_args, **_kwargs: None)
    GammaClient(GammaConfig(), session=session)

    assert "Authorization" not in session.headers
    assert not any(name.startswith("POLY_") for name in session.headers)
    assert session.trust_env is False
