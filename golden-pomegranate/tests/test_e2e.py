"""Fake-source end-to-end tests through clients, collector and SQLite."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import zlib

import pytest
from requests.exceptions import ConnectionError

from polybot.api.clob_client import ClobPublicClient
from polybot.api.data_client import DataApiClient
from polybot.api.gamma_client import GammaClient
from polybot.collector import ResearchCollector
from polybot.config import load_config
from polybot.db.repository import ResearchRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW_EPOCH = 2_000_000


class Response:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload
        self.content = json.dumps(payload, sort_keys=True).encode("utf-8")

    def json(self):
        return self.payload


class GetSequence:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, *, params, timeout):
        self.calls.append((url, dict(params), timeout))
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return Response(value)


class GetCallback:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []
        self.headers = {}

    def get(self, url, *, params, timeout):
        copied = dict(params)
        self.calls.append((url, copied, timeout))
        value = self.callback(copied)
        if isinstance(value, BaseException):
            raise value
        return Response(value)


class PostCallback:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []
        self.headers = {}

    def post(self, url, *, json, timeout):
        body = [dict(item) for item in json]
        self.calls.append((url, body, timeout))
        value = self.callback(body)
        if isinstance(value, BaseException):
            raise value
        return Response(value)


def _config(tmp_path: Path):
    loaded = load_config(PROJECT_ROOT / "config.yaml", env={})
    trading = replace(
        loaded.trading,
        gamma=replace(
            loaded.trading.gamma,
            max_retries=1,
            retry_base_seconds=0,
            retry_max_seconds=0.001,
        ),
        orderbook=replace(
            loaded.trading.orderbook,
            bucket_count=1,
            max_markets_per_cycle=20,
            batch_token_limit=100,
            normalized_levels=2,
        ),
    )
    return replace(
        loaded,
        trading=trading,
        db_path=tmp_path / "data" / "e2e-job" / "trades_sim.db",
        job_name="e2e-job",
    )


def _collector(tmp_path, gamma_session, clob_session, data_session):
    config = _config(tmp_path)
    repository = ResearchRepository(config.db_path)
    repository.initialize(
        contract_metadata={
            "strategy_source_digest": config.trading.strategy_source_digest,
            "data_trade_query_contract": {"takerOnly": True},
        }
    )
    collector = ResearchCollector(
        config,
        repository=repository,
        gamma=object(),  # replaced below after staged-raw sink exists
        clob=object(),
        data=object(),
        now_epoch=lambda: NOW_EPOCH,
    )
    collector.gamma = GammaClient(
        config.trading.gamma,
        session=gamma_session,
        evidence_sink=repository.record_api_request,
        raw_payload_sink=collector._stage_gamma_payload,
        raw_payload_every_cycles=1,
    )
    collector.clob = ClobPublicClient(
        config.trading.orderbook,
        session=clob_session,
        evidence_sink=repository.record_api_request,
        raw_payload_sink=repository.record_raw_payload,
    )
    collector.data = DataApiClient(
        config.trading.data_api,
        config.trading.gamma,
        session=data_session,
        evidence_sink=repository.record_api_request,
        raw_payload_sink=repository.record_raw_payload,
    )
    return config, repository, collector


def _count(connection, table):
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_multi_page_cycle_persists_full_census_books_trade_tape_and_lineage(
    monkeypatch, tmp_path
):
    market_a = {
        "id": "market-a",
        "conditionId": "condition-a",
        "closed": False,
        "active": True,
        "outcomes": ["Yes", "No", "Invalid"],
        "clobTokenIds": ["a-yes", "a-no", "a-invalid"],
        "outcomePrices": ["0.50", "0.45", "0.05"],
        "volume": "1234.5",
        "volume24hr": "67.8",
    }
    market_b = {
        "id": "market-b",
        "conditionId": "condition-b",
        "closed": False,
        "active": False,
        "outcomes": ["Up", "Down"],
        "clobTokenIds": ["b-up", "b-down"],
        "outcomePrices": ["0.6", "0.4"],
        "volume": "999",
        "volume24hr": None,
    }
    gamma_payloads = (
        {"markets": [market_a], "next_cursor": "next-page"},
        {"markets": [market_b], "next_cursor": None},
    )
    gamma = GetSequence(*gamma_payloads)

    def books(body):
        result = []
        for index, item in enumerate(body):
            token = item["token_id"]
            result.append(
                {
                    "asset_id": token,
                    "bids": []
                    if token == "a-invalid"
                    else [{"price": "0.4", "size": "10"}],
                    "asks": []
                    if token == "a-invalid"
                    else [{"price": "0.5", "size": "11"}],
                    "hash": f"hash-{index}",
                    "timestamp": "2026-08-06T00:00:00Z",
                }
            )
        return result

    clob = PostCallback(books)
    source_trade = {
        "proxyWallet": "0x0000000000000000000000000000000000000001",
        "side": "BUY",
        "asset": "a-yes",
        "conditionId": "condition-a",
        "size": "5",
        "price": "0.42",
        "timestamp": NOW_EPOCH - 1_000,
        "outcome": "Yes",
        "outcomeIndex": 0,
        "transactionHash": "0xeconomic-fill",
        "name": "must be removed",
        "title": "must be removed",
        "profileImage": "https://display.invalid/image.png",
    }
    data = GetCallback(
        lambda params: [
            {**source_trade, "timestamp": params["start"]},
            {**source_trade, "timestamp": params["start"]},
        ]
    )
    config, repository, collector = _collector(tmp_path, gamma, clob, data)
    monkeypatch.setattr("polybot.collector.assert_no_credentials", lambda: None)

    stats = collector.run_cycle("run-e2e")

    assert stats == {
        "cycle_number": 1,
        "market_sweeps": 1,
        "gamma_page_count": 2,
        "markets_observed": 2,
        "outcomes_observed": 5,
        "orderbook_component_status": "SUCCESS",
        "orderbooks_observed": 5,
        "trade_tape_component_status": "SUCCESS",
        "trade_tape_possible_gap": False,
        "trades_observed": 2,
        "trade_watermark_advanced_to": NOW_EPOCH - 300 - 24 * 3600 + 3_600,
        "resolution_component_status": "EMPTY",
        "resolution_observed": 0,
        "data_quality_issue_count": 1,
    }
    assert gamma.calls[0][1]["closed"] == "false"
    assert "active" not in gamma.calls[0][1]
    assert gamma.calls[1][1]["after_cursor"] == "next-page"
    assert data.calls[0][1]["takerOnly"] == "true"
    assert data.calls[0][1]["end"] == NOW_EPOCH - 300 - 24 * 3600 + 3_600
    assert len(clob.calls) == 1
    assert {item["token_id"] for item in clob.calls[0][1]} == {
        "a-yes",
        "a-no",
        "a-invalid",
        "b-up",
        "b-down",
    }

    with sqlite3.connect(repository.db_path) as connection:
        assert _count(connection, "market_sweeps") == 1
        assert _count(connection, "market_observations") == 2
        assert _count(connection, "market_sweep_memberships") == 2
        assert _count(connection, "outcome_observations") == 5
        assert _count(connection, "orderbook_selections") == 2
        assert _count(connection, "orderbook_token_attempts") == 5
        assert _count(connection, "orderbook_snapshots") == 5
        assert _count(connection, "trade_tape_sweeps") == 1
        trade_bounds = connection.execute(
            "SELECT bounded_target_end_epoch, source_target_end_epoch "
            "FROM trade_tape_sweeps"
        ).fetchone()
        assert trade_bounds is not None
        assert trade_bounds[1] == NOW_EPOCH - 300
        assert trade_bounds[0] < trade_bounds[1]
        assert _count(connection, "trade_tape_windows") == 1
        assert _count(connection, "trade_observations") == 2
        assert _count(connection, "trade_tape_memberships") == 2
        assert _count(connection, "data_quality_issues") == 1
        sweep = connection.execute(
            "SELECT page_count, raw_market_count, cursor_complete, raw_payload_page_count "
            "FROM market_sweeps"
        ).fetchone()
        assert sweep == (2, 2, 1, 2)
        clocks = connection.execute(
            "SELECT page_number, page_received_at FROM market_observations "
            "ORDER BY page_number"
        ).fetchall()
        assert [row[0] for row in clocks] == [1, 2]
        assert all(row[1].endswith("+00:00") for row in clocks)
        assert connection.execute(
            "SELECT volume_total_raw, volume_24h_raw FROM market_observations "
            "WHERE condition_id='condition-a'"
        ).fetchone() == ("1234.5", "67.8")
        selections = connection.execute(
            "SELECT sampler_slot, bucket_number, frame_market_count, "
            "bucket_candidate_count, sampled_market_count, truncated_count "
            "FROM orderbook_selections"
        ).fetchall()
        assert selections == [
            (NOW_EPOCH // (config.trading.cadence_minutes * 60), 0, 2, 2, 2, 0),
            (NOW_EPOCH // (config.trading.cadence_minutes * 60), 0, 2, 2, 2, 0),
        ]
        token_states = connection.execute(
            "SELECT token_id, status FROM orderbook_token_attempts ORDER BY token_id"
        ).fetchall()
        assert ("a-invalid", "EMPTY_BOOK") in token_states
        component_states = dict(
            connection.execute(
                "SELECT component, status FROM source_component_runs"
            ).fetchall()
        )
        assert component_states == {
            "gamma_census": "SUCCESS",
            "clob_books": "SUCCESS",
            "data_trade_tape": "SUCCESS",
            "resolution_watchlist": "EMPTY",
        }
        sanitized = [
            row[0]
            for row in connection.execute(
                "SELECT sanitized_trade_json FROM trade_observations"
            )
        ]
        assert all("must be removed" not in row for row in sanitized)
        assert all(
            "profileImage" not in row and '"name"' not in row for row in sanitized
        )
        raw_rows = connection.execute(
            "SELECT payload_kind, payload_blob FROM raw_payloads ORDER BY payload_kind"
        ).fetchall()

    kinds = [row[0] for row in raw_rows]
    assert kinds.count("gamma_markets_keyset_page") == 2
    assert kinds.count("clob_books_exact_batch") == 1
    assert kinds.count("data_trades_sanitized_window") == 1
    decoded_gamma = {
        zlib.decompress(blob)
        for kind, blob in raw_rows
        if kind == "gamma_markets_keyset_page"
    }
    assert decoded_gamma == {Response(payload).content for payload in gamma_payloads}


def test_repeated_gamma_cursor_rolls_back_staged_raw_and_never_calls_secondary_sources(
    monkeypatch, tmp_path
):
    gamma = GetSequence(
        {"markets": [{"conditionId": "one"}], "next_cursor": "again"},
        {"markets": [{"conditionId": "two"}], "next_cursor": "again"},
    )
    clob = PostCallback(lambda _body: pytest.fail("CLOB must not be called"))
    data = GetCallback(lambda _params: pytest.fail("Data API must not be called"))
    _, repository, collector = _collector(tmp_path, gamma, clob, data)
    monkeypatch.setattr("polybot.collector.assert_no_credentials", lambda: None)

    with pytest.raises(RuntimeError, match="cursor repeated"):
        collector.run_cycle("run-repeated")

    assert clob.calls == []
    assert data.calls == []
    with sqlite3.connect(repository.db_path) as connection:
        assert _count(connection, "market_sweeps") == 0
        assert _count(connection, "market_observations") == 0
        assert _count(connection, "outcome_observations") == 0
        assert _count(connection, "raw_payloads") == 0
        assert _count(connection, "api_requests") == 2
        issue = connection.execute(
            "SELECT severity, issue_code FROM data_quality_issues"
        ).fetchone()
        assert issue == ("CRITICAL", "incomplete_cursor_sweep")


def test_secondary_book_failure_is_visible_without_discarding_complete_gamma(
    monkeypatch, tmp_path
):
    gamma = GetSequence(
        {
            "markets": [
                {
                    "id": "market",
                    "conditionId": "condition",
                    "closed": False,
                    "outcomes": ["Yes", "No"],
                    "clobTokenIds": ["yes", "no"],
                    "outcomePrices": ["0.5", "0.5"],
                }
            ],
            "next_cursor": None,
        }
    )
    clob = PostCallback(lambda _body: ConnectionError("book endpoint unavailable"))
    data = GetCallback(lambda _params: [])
    _, repository, collector = _collector(tmp_path, gamma, clob, data)
    monkeypatch.setattr("polybot.collector.assert_no_credentials", lambda: None)

    stats = collector.run_cycle("run-secondary-error")

    assert stats["market_sweeps"] == 1
    assert stats["orderbook_component_status"] == "ERROR"
    assert stats["trade_tape_component_status"] == "EMPTY"
    with sqlite3.connect(repository.db_path) as connection:
        assert _count(connection, "market_sweeps") == 1
        assert _count(connection, "market_observations") == 1
        assert _count(connection, "orderbook_token_attempts") == 2
        states = dict(
            connection.execute(
                "SELECT component, status FROM source_component_runs"
            ).fetchall()
        )
        assert states["gamma_census"] == "SUCCESS"
        assert states["clob_books"] == "ERROR"
        assert states["data_trade_tape"] == "EMPTY"
        issues = {
            row[0]
            for row in connection.execute("SELECT issue_code FROM data_quality_issues")
        }
        assert "sample_coverage_gap" in issues
