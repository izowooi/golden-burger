"""Official batch prices-history client tests (network-free)."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from polybot.api.history_client import HistoryClient
from polybot.config import TradingConfig
from polybot.strategy.scanner import MarketScanner
from polybot.strategy.signals import SnapshotPoint


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


def test_batch_prefetch_chunks_at_twenty_and_serves_get_from_memory():
    client = HistoryClient()
    client.session = MagicMock()

    def post(_url, *, json, timeout):
        assert timeout == client.timeout
        assert len(json["markets"]) <= client.MAX_BATCH_MARKETS
        return _response({
            "history": {
                token: [{"t": json["start_ts"], "p": "0.42"}]
                for token in json["markets"]
            }
        })

    client.session.post.side_effect = post
    tokens = [f"token-{index}" for index in range(41)]

    result = client.prefetch_price_histories(tokens, 100, 200, fidelity=10)

    assert client.session.post.call_count == 3
    assert set(result) == set(tokens)
    assert result["token-0"][0].timestamp == datetime.utcfromtimestamp(100)
    assert result["token-0"][0].probability == 0.42
    assert client.get_price_history("token-0", 100, 200, fidelity=10) == result["token-0"]
    client.session.get.assert_not_called()


def test_failed_batch_is_cached_as_missing_without_individual_fallback():
    client = HistoryClient()
    client.session = MagicMock()
    client.session.post.side_effect = RuntimeError("temporary outage")

    result = client.prefetch_price_histories(["token-a"], 100, 200)

    assert result == {"token-a": None}
    assert client.get_price_history("token-a", 100, 200) is None
    client.session.get.assert_not_called()


def test_scanner_prefetches_invalid_windows_before_per_market_evaluation():
    now = datetime(2026, 7, 8, 8, 0)  # quiet weekday hour
    client = HistoryClient()
    client.session = MagicMock()

    def post(_url, *, json, timeout):
        return _response({
            "history": {
                token: [
                    {"t": json["start_ts"], "p": "0.70"},
                    {"t": json["end_ts"], "p": "0.60"},
                ]
                for token in json["markets"]
            }
        })

    client.session.post.side_effect = post

    class EmptyRepo:
        def get_snapshots_since(self, condition_id, since):
            return []

    scanner = MarketScanner(
        MagicMock(), TradingConfig(), repo=EmptyRepo(), history_client=client
    )
    markets = [
        {
            "conditionId": f"condition-{index}",
            "outcomePrices": ["0.60", "0.40"],
            "clobTokenIds": [f"token-{index}", f"no-token-{index}"],
            "outcomes": ["Yes", "No"],
            "liquidity": "20000",
            "volume24hr": "1000",
            "endDate": (now + timedelta(days=365)).isoformat() + "Z",
        }
        for index in range(21)
    ]

    scanner.scan_buy_candidates(markets, now=now)

    assert client.session.post.call_count == 2
    client.session.get.assert_not_called()


def test_scanner_does_not_call_history_for_valid_local_window():
    now = datetime(2026, 7, 8, 8, 0)
    client = HistoryClient()
    client.session = MagicMock()

    class WarmRepo:
        def get_snapshots_since(self, condition_id, since):
            return [
                SnapshotPoint(
                    timestamp=now - timedelta(hours=hours),
                    probability=0.70,
                    volume_24h=100.0,
                )
                for hours in (20, 16, 12, 8, 4, 2, 0)
            ]

    scanner = MarketScanner(
        MagicMock(), TradingConfig(), repo=WarmRepo(), history_client=client
    )
    market = {
        "conditionId": "condition",
        "outcomePrices": ["0.60", "0.40"],
        "clobTokenIds": ["token", "no-token"],
        "outcomes": ["Yes", "No"],
        "liquidity": "20000",
        "volume24hr": "1000",
        "endDate": (now + timedelta(days=365)).isoformat() + "Z",
    }

    scanner.scan_buy_candidates([market], now=now)

    client.session.post.assert_not_called()
    client.session.get.assert_not_called()
