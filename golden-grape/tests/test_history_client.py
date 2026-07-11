"""Official batch prices-history regression tests (network-free)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from polybot.api.history_client import HistoryClient
from polybot.config import TradingConfig
from polybot.strategy.scanner import MarketScanner


START_TS = int(datetime(2026, 7, 10, tzinfo=timezone.utc).timestamp())
END_TS = int(datetime(2026, 7, 11, tzinfo=timezone.utc).timestamp())


def _response(history):
    response = MagicMock()
    response.json.return_value = {"history": history}
    return response


def test_batch_chunks_at_twenty_and_cached_get_does_not_hit_network():
    client = HistoryClient()
    client.session = MagicMock()
    client.session.post.side_effect = lambda _url, *, json, timeout: _response({
        token: [{"t": json["start_ts"], "p": "0.50"}]
        for token in json["markets"]
    })

    result = client.prefetch_price_histories(
        [f"token-{index}" for index in range(41)], START_TS, END_TS
    )

    assert client.session.post.call_count == 3
    assert result["token-0"][0].probability == 0.50
    assert client.get_price_history("token-0", START_TS, END_TS) == result["token-0"]
    client.session.get.assert_not_called()


def test_batch_missing_and_malformed_token_are_isolated_and_cached():
    client = HistoryClient()
    client.session = MagicMock()
    client.session.post.return_value = _response({
        "good": [{"t": START_TS, "p": "0.5"}],
        "bad": [{"t": "broken", "p": "0.5"}],
    })

    result = client.prefetch_price_histories(
        ["good", "missing", "bad"], START_TS, END_TS
    )

    assert result["good"] is not None
    assert result["missing"] is None
    assert result["bad"] is None
    assert client.get_price_history("missing", START_TS, END_TS) is None
    client.session.get.assert_not_called()


def test_failed_batch_is_missing_without_individual_fallback():
    client = HistoryClient()
    client.session = MagicMock()
    client.session.post.side_effect = RuntimeError("outage")

    result = client.prefetch_price_histories(["token"], START_TS, END_TS)

    assert result == {"token": None}
    assert client.get_price_history("token", START_TS, END_TS) is None
    client.session.get.assert_not_called()


def test_scanner_batches_invalid_windows_before_per_market_evaluation():
    client = HistoryClient()
    client.session = MagicMock()
    client.session.post.side_effect = lambda _url, *, json, timeout: _response({
        token: [{"t": json["start_ts"], "p": "0.50"}]
        for token in json["markets"]
    })

    class EmptyRepo:
        def get_snapshots_since(self, condition_id, since):
            return []

    scanner = MarketScanner(MagicMock(), TradingConfig(), EmptyRepo(), client)
    markets = [
        {
            "conditionId": f"condition-{index}",
            "outcomePrices": ["0.50", "0.50"],
            "clobTokenIds": [f"token-{index}", f"no-token-{index}"],
            "outcomes": ["Yes", "No"],
            "liquidity": "30000",
            "volume24hr": "20000",
            "endDate": "2030-01-01T00:00:00Z",
        }
        for index in range(21)
    ]

    scanner.scan_buy_candidates(markets)

    assert client.session.post.call_count == 2
    client.session.get.assert_not_called()
