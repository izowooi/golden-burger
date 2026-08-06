"""Independent resolution/redeemable watcher semantics."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from requests.exceptions import ConnectionError

from polybot.api.gamma_client import GammaClient
from polybot.collector import _resolution_bundle
from polybot.config import GammaConfig
from polybot.db.repository import ResearchRepository


class Response:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload
        self.content = json.dumps(payload, sort_keys=True).encode()

    def json(self):
        return self.payload


class Session:
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


def _client(session, *, raw_payload_sink=None):
    return GammaClient(
        GammaConfig(
            max_retries=1,
            retry_base_seconds=0,
            retry_max_seconds=0.001,
        ),
        session=session,
        raw_payload_sink=raw_payload_sink,
    )


def test_resolution_lookup_batches_and_accounts_for_observed_and_missing_rows():
    session = Session(
        Response(
            [
                {
                    "id": "market-a",
                    "conditionId": "a",
                    "closed": True,
                    "redeemable": False,
                    "outcomes": ["Yes", "No"],
                    "outcomePrices": ["1", "0"],
                }
            ]
        ),
        Response(
            {
                "markets": [
                    {
                        "id": "market-c",
                        "conditionId": "c",
                        "closed": True,
                        "redeemable": True,
                    }
                ]
            }
        ),
    )
    raw = []
    client = _client(
        session,
        raw_payload_sink=lambda **kwargs: raw.append(kwargs) or f"raw-{len(raw)}",
    )

    rows = client.fetch_resolution_batch(
        ["a", "b", "c"],
        cycle_number=4,
        run_id="run",
        sweep_attempt_id="resolution-attempt",
        batch_size=2,
    )

    assert [(row["condition_id"], row["lookup_status"]) for row in rows] == [
        ("a", "OBSERVED"),
        ("b", "MISSING"),
        ("c", "OBSERVED"),
    ]
    assert session.calls[0][1] == {
        "condition_ids": ["a", "b"],
        "closed": "true",
        "limit": 2,
    }
    assert session.calls[1][1] == {
        "condition_ids": ["c"],
        "closed": "true",
        "limit": 1,
    }
    assert [item["kind"] for item in raw] == [
        "gamma_resolution_lookup",
        "gamma_resolution_lookup",
    ]


def test_resolution_network_error_creates_one_error_row_per_requested_condition():
    client = _client(Session(ConnectionError("public endpoint unavailable")))

    rows = client.fetch_resolution_batch(
        ["a", "b"],
        cycle_number=1,
        run_id="run",
        sweep_attempt_id="attempt",
        batch_size=2,
    )

    assert len(rows) == 2
    assert {row["lookup_status"] for row in rows} == {"ERROR"}
    assert {row["condition_id"] for row in rows} == {"a", "b"}
    assert all(row["raw_market"] is None for row in rows)


def test_one_hot_resolution_and_redeemable_are_never_inferred_from_each_other():
    normalized = _resolution_bundle(
        [
            {
                "condition_id": "one-hot-not-redeemable",
                "requested_at": "2026-08-06T00:00:00+00:00",
                "observed_at": "2026-08-06T00:00:01+00:00",
                "lookup_status": "OBSERVED",
                "request_id": "request-1",
                "raw_market": {
                    "id": "m1",
                    "closed": True,
                    "redeemable": False,
                    "outcomes": ["Yes", "No"],
                    "outcomePrices": ["1", "0"],
                },
            },
            {
                "condition_id": "redeemable-not-resolved",
                "requested_at": "2026-08-06T00:00:00+00:00",
                "observed_at": "2026-08-06T00:00:01+00:00",
                "lookup_status": "OBSERVED",
                "request_id": "request-2",
                "raw_market": {
                    "id": "m2",
                    "closed": True,
                    "redeemable": True,
                    "outcomes": ["A", "B", "C"],
                    "outcomePrices": ["0.7", "0.2", "0.1"],
                },
            },
        ],
        run_id="run",
        cycle_number=1,
    )

    first, second = normalized
    assert (first["one_hot"], first["one_hot_outcome_label"], first["redeemable"]) == (
        1,
        "Yes",
        0,
    )
    assert (
        second["one_hot"],
        second["one_hot_outcome_label"],
        second["redeemable"],
    ) == (
        0,
        None,
        1,
    )
    assert "fill" not in json.dumps(normalized).lower()
    assert "redeem transaction" not in json.dumps(normalized).lower()


def test_watchlist_becomes_terminal_only_when_resolution_and_redeemable_are_both_true(
    tmp_path: Path,
):
    repository = ResearchRepository(
        tmp_path / "trades_sim.db",
        clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
    )
    repository.initialize()
    observed_at = "2026-08-06T00:00:00+00:00"
    with sqlite3.connect(repository.db_path) as connection:
        for condition in ("resolved-only", "redeemable-only", "both"):
            connection.execute(
                "INSERT INTO resolution_watchlist "
                "(condition_id, source_market_key, first_seen_sweep_id, first_seen_at, "
                "selection_reason, terminal) VALUES (?, ?, 'sweep', ?, 'test', 0)",
                (condition, condition, observed_at),
            )
        for index, (condition, one_hot, redeemable) in enumerate(
            (
                ("resolved-only", 1, 0),
                ("redeemable-only", 0, 1),
                ("both", 1, 1),
            )
        ):
            connection.execute(
                "INSERT INTO resolution_observations "
                "(resolution_observation_id, run_id, cycle_number, condition_id, "
                "requested_at, observed_at, lookup_status, closed, one_hot, redeemable, "
                "outcome_prices_json) VALUES (?, 'run', 1, ?, ?, ?, 'OBSERVED', 1, ?, ?, '[]')",
                (
                    f"resolution-{index}",
                    condition,
                    observed_at,
                    observed_at,
                    one_hot,
                    redeemable,
                ),
            )

    due = repository.select_resolution_watchlist(
        10, now=datetime(2026, 8, 7, tzinfo=timezone.utc)
    )

    assert set(due) == {"resolved-only", "redeemable-only"}
