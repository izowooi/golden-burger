"""Official batch prices-history regression tests (network-free)."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from polybot.api.history_client import HistoryClient, to_unix_utc
from polybot.config import TradingConfig
from polybot.strategy.scanner import MarketScanner


NOW = datetime(2026, 7, 12, 0, 0)
START = NOW - timedelta(days=7)


def _response(history):
    response = MagicMock()
    response.json.return_value = {"history": history}
    return response


def test_batch_chunks_at_twenty_and_cached_get_does_not_hit_network():
    client = HistoryClient()
    client.session = MagicMock()
    client.session.post.side_effect = lambda _url, *, json, timeout: _response({
        token: [{"t": json["start_ts"], "p": "0.20"}]
        for token in json["markets"]
    })

    result = client.prefetch_price_histories(
        [f"token-{index}" for index in range(41)], START, NOW
    )

    assert client.session.post.call_count == 3
    assert result["token-0"][0].probability == 0.20
    assert client.get_price_history("token-0", START, NOW) == result["token-0"]
    client.session.get.assert_not_called()


def test_batch_missing_and_malformed_token_are_isolated_and_cached():
    client = HistoryClient()
    client.session = MagicMock()
    client.session.post.return_value = _response({
        "good": [{"t": to_unix_utc(START), "p": "0.2"}],
        "bad": [{"t": "broken", "p": "0.2"}],
    })

    result = client.prefetch_price_histories(["good", "missing", "bad"], START, NOW)

    assert result["good"] is not None
    assert result["missing"] is None
    assert result["bad"] is None
    assert client.get_price_history("missing", START, NOW) is None
    client.session.get.assert_not_called()


def test_failed_batch_is_missing_without_individual_fallback():
    client = HistoryClient()
    client.session = MagicMock()
    client.session.post.side_effect = RuntimeError("outage")

    assert client.prefetch_price_histories(["token"], START, NOW) == {"token": None}
    assert client.get_price_history("token", START, NOW) is None
    client.session.get.assert_not_called()


def test_scanner_batches_invalid_windows_before_per_market_evaluation():
    client = HistoryClient()
    client.session = MagicMock()
    client.session.post.side_effect = lambda _url, *, json, timeout: _response({
        token: [{"t": json["start_ts"], "p": "0.20"}]
        for token in json["markets"]
    })

    class EmptyRepo:
        def get_recent_snapshots(self, condition_id, hours_back, now):
            return []

    scanner = MarketScanner(MagicMock(), TradingConfig(), EmptyRepo(), client)
    markets = [
        {
            "conditionId": f"condition-{index}",
            "outcomePrices": ["0.20", "0.80"],
            "clobTokenIds": [f"token-{index}", f"no-token-{index}"],
            "outcomes": ["Yes", "No"],
            "liquidity": "20000",
            "volume24hr": "1000",
            "endDate": "2030-01-01T00:00:00Z",
        }
        for index in range(21)
    ]

    scanner.scan_buy_candidates(markets)

    assert client.session.post.call_count == 2
    client.session.get.assert_not_called()
