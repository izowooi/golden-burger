"""Network-free end-to-end checks for the public research collection contract."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest
from requests.exceptions import ChunkedEncodingError

from polybot.collector import ResearchCollector
from polybot.config import load_config
from polybot.db.repository import ResearchRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Response:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, payload):
        self._payload = payload
        self.content = json.dumps(payload, sort_keys=True).encode("utf-8")

    def json(self):
        return self._payload


class _GetSession:
    def __init__(self, callback):
        self.callback = callback
        self.calls: list[tuple[str, dict, tuple[float, float]]] = []
        self.headers: dict[str, str] = {}

    def get(self, url, *, params, timeout):
        copied = dict(params)
        self.calls.append((url, copied, timeout))
        result = self.callback(url, copied)
        if isinstance(result, BaseException):
            raise result
        return _Response(result)


class _PostSession:
    def __init__(self, error: BaseException | None = None):
        self.error = error
        self.calls: list[tuple[str, list[dict], tuple[float, float]]] = []
        self.headers: dict[str, str] = {}

    def post(self, url, *, json, timeout):
        body = [dict(item) for item in json]
        self.calls.append((url, body, timeout))
        if self.error is not None:
            raise self.error
        return _Response(
            [
                {
                    "asset_id": item["token_id"],
                    "market": "0xmarket",
                    "timestamp": "1786000000000",
                    "hash": f"hash-{item['token_id']}",
                    "bids": [{"price": "0.40", "size": "5"}],
                    "asks": [{"price": "0.42", "size": "7"}],
                    "last_trade_price": "0.41",
                    "tick_size": "0.01",
                    "min_order_size": "1",
                    "neg_risk": False,
                }
                for item in body
            ]
        )


def _market(number: int) -> dict:
    return {
        "id": f"market-{number}",
        "conditionId": f"condition-{number}",
        "question": f"Question {number}?",
        "outcomes": ["Yes", "No"],
        "clobTokenIds": [f"token-{number}-yes", f"token-{number}-no"],
        "outcomePrices": ["0.41", "0.59"],
        "volume": "1234.5",
        "volume24hr": "67.8",
        "active": False,
        "closed": False,
        "enableOrderBook": True,
        "redeemable": False,
    }


def _trade(**overrides) -> dict:
    row = {
        "proxyWallet": "0x0000000000000000000000000000000000000001",
        "side": "BUY",
        "asset": "token-1-yes",
        "conditionId": "condition-1",
        "size": 5,
        "price": 0.41,
        "timestamp": 1_999_000,
        "outcome": "Yes",
        "outcomeIndex": 0,
        "transactionHash": "0xeconomic-fill",
        "name": "display-only user name",
        "pseudonym": "display-only pseudonym",
        "title": "display-only market title",
        "profileImage": "https://display.invalid/profile.png",
    }
    row.update(overrides)
    return row


def _config(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    base = load_config(path, job_name="integration", env={})
    gamma = replace(
        base.trading.gamma,
        max_retries=1,
        retry_base_seconds=0,
        retry_max_seconds=1,
    )
    orderbook = replace(
        base.trading.orderbook,
        bucket_count=1,
        max_markets_per_cycle=10,
    )
    return replace(
        base, trading=replace(base.trading, gamma=gamma, orderbook=orderbook)
    )


def _collector(tmp_path: Path, data_callback):
    config = _config(tmp_path)
    repository = ResearchRepository(config.db_path)
    repository.initialize()
    collector = ResearchCollector(
        config,
        repository=repository,
        now_epoch=lambda: 2_000_000,
    )

    def gamma_callback(url, params):
        assert url.endswith("/markets/keyset")
        assert params["closed"] == "false"
        assert params["include_tag"] == "true"
        assert "active" not in params
        assert "liquidity" not in params
        if "after_cursor" not in params:
            return {"markets": [_market(1)], "next_cursor": "page-2"}
        assert params["after_cursor"] == "page-2"
        return {"markets": [_market(2)], "next_cursor": None}

    gamma_session = _GetSession(gamma_callback)
    clob_session = _PostSession()
    data_session = _GetSession(data_callback)
    collector.gamma.session = gamma_session
    collector.clob.session = clob_session
    collector.data.session = data_session
    return collector, repository, gamma_session, clob_session, data_session


def test_fake_multi_page_cycle_persists_exact_taker_only_economic_tape(tmp_path):
    def data_callback(url, params):
        assert url.endswith("/trades")
        return [_trade(timestamp=params["start"])]

    collector, repository, gamma_session, clob_session, data_session = _collector(
        tmp_path, data_callback
    )

    stats = collector.run_cycle("run-e2e")

    assert stats["markets_observed"] == 2
    assert stats["outcomes_observed"] == 4
    assert stats["orderbooks_observed"] == 4
    assert stats["trades_observed"] == 1
    assert stats["trade_tape_component_status"] == "SUCCESS"
    assert len(gamma_session.calls) == 2
    assert len(clob_session.calls) == 1
    assert data_session.calls[0][1] == {
        "start": 2_000_000 - 300 - 24 * 3600,
        "end": 2_000_000 - 300 - 24 * 3600 + 3_600,
        "limit": 10_000,
        "offset": 0,
        "takerOnly": "true",
    }

    with sqlite3.connect(repository.db_path) as connection:
        connection.row_factory = sqlite3.Row
        request = connection.execute(
            "SELECT params_json FROM api_requests "
            "WHERE request_kind = 'data_trades_window'"
        ).fetchone()
        trade = connection.execute(
            "SELECT proxy_wallet, sanitized_trade_json FROM trade_observations"
        ).fetchone()
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "market_sweeps",
                "market_observations",
                "market_sweep_memberships",
                "outcome_observations",
                "orderbook_snapshots",
                "trade_observations",
                "trade_tape_memberships",
            )
        }

    assert json.loads(request["params_json"])["takerOnly"] == "true"
    assert trade["proxy_wallet"] == _trade()["proxyWallet"]
    sanitized = json.loads(trade["sanitized_trade_json"])
    assert set(sanitized) == {
        "asset",
        "conditionId",
        "outcome",
        "outcomeIndex",
        "price",
        "proxyWallet",
        "side",
        "size",
        "timestamp",
        "transactionHash",
    }
    assert not {"name", "pseudonym", "title", "profileImage"} & set(sanitized)
    assert counts == {
        "market_sweeps": 1,
        "market_observations": 2,
        "market_sweep_memberships": 2,
        "outcome_observations": 4,
        "orderbook_snapshots": 4,
        "trade_observations": 1,
        "trade_tape_memberships": 1,
    }


def test_data_source_error_is_committed_without_rolling_back_complete_gamma(tmp_path):
    collector, repository, _gamma, _clob, _data = _collector(
        tmp_path,
        lambda _url, _params: ChunkedEncodingError("truncated public response"),
    )

    stats = collector.run_cycle("run-secondary-error")

    assert stats["markets_observed"] == 2
    assert stats["trade_tape_component_status"] == "ERROR"
    assert stats["trade_watermark_advanced_to"] is None
    with sqlite3.connect(repository.db_path) as connection:
        components = dict(
            connection.execute(
                "SELECT component, status FROM source_component_runs"
            ).fetchall()
        )
        gamma_count = connection.execute(
            "SELECT COUNT(*) FROM market_sweeps WHERE cursor_complete = 1"
        ).fetchone()[0]
        error_windows = connection.execute(
            "SELECT COUNT(*) FROM trade_tape_windows "
            "WHERE status = 'ERROR' AND possible_gap = 1"
        ).fetchone()[0]
        high_gaps = connection.execute(
            "SELECT COUNT(*) FROM data_quality_issues "
            "WHERE issue_code = 'possible_trade_tape_gap' AND severity = 'HIGH'"
        ).fetchone()[0]

    assert components["gamma_census"] == "SUCCESS"
    assert components["data_trade_tape"] == "ERROR"
    assert gamma_count == 1
    assert error_windows == 1
    assert high_gaps == 1


def test_clob_source_error_is_committed_without_rolling_back_complete_gamma(tmp_path):
    collector, repository, _gamma, _clob, _data = _collector(
        tmp_path,
        lambda url, params: (
            [_trade(timestamp=params["start"])] if url.endswith("/trades") else []
        ),
    )
    collector.clob.session = _PostSession(
        ChunkedEncodingError("truncated public book response")
    )

    stats = collector.run_cycle("run-book-error")

    assert stats["markets_observed"] == 2
    assert stats["orderbook_component_status"] == "ERROR"
    assert stats["trades_observed"] == 1
    with sqlite3.connect(repository.db_path) as connection:
        components = dict(
            connection.execute(
                "SELECT component, status FROM source_component_runs"
            ).fetchall()
        )
        gamma_count = connection.execute(
            "SELECT COUNT(*) FROM market_sweeps WHERE cursor_complete = 1"
        ).fetchone()[0]
        failed_selections = connection.execute(
            "SELECT COUNT(*) FROM orderbook_selections "
            "WHERE status = 'ERROR' AND coverage_ratio = 0"
        ).fetchone()[0]
        sample_gaps = connection.execute(
            "SELECT COUNT(*) FROM data_quality_issues "
            "WHERE issue_code = 'sample_coverage_gap'"
        ).fetchone()[0]

    assert components["gamma_census"] == "SUCCESS"
    assert components["clob_books"] == "ERROR"
    assert gamma_count == 1
    assert failed_selections == 2
    assert sample_gaps == 1


def test_unexpected_secondary_publish_failure_cannot_erase_complete_gamma(tmp_path):
    class FailingSecondaryRepository(ResearchRepository):
        def publish_secondary_cycle(self, _bundle):
            raise KeyboardInterrupt("simulated Jenkins termination")

    config = _config(tmp_path)
    repository = FailingSecondaryRepository(config.db_path)
    repository.initialize()
    collector = ResearchCollector(
        config,
        repository=repository,
        now_epoch=lambda: 2_000_000,
    )

    def gamma_callback(url, params):
        assert url.endswith("/markets/keyset")
        if "after_cursor" not in params:
            return {"markets": [_market(1)], "next_cursor": "page-2"}
        return {"markets": [_market(2)], "next_cursor": None}

    collector.gamma.session = _GetSession(gamma_callback)
    collector.clob.session = _PostSession()
    collector.data.session = _GetSession(lambda _url, _params: [_trade()])

    with pytest.raises(KeyboardInterrupt, match="Jenkins termination"):
        collector.run_cycle("run-interrupted-secondary")

    with sqlite3.connect(repository.db_path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM market_sweeps").fetchone()[0] == 1
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM source_component_runs WHERE component='gamma_census'"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM source_component_runs WHERE component='clob_books'"
            ).fetchone()[0]
            == 0
        )
