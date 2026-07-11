"""prices-history 백필 클라이언트 테스트 (네트워크 없이 mock 기반).

핵심 회귀 방지: naive UTC datetime을 로컬 타임존으로 해석해 startTs/endTs가
어긋나는 버그 (KST 머신에서 백필 윈도우가 9시간 과거로 밀림).
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from polybot.api.history_client import HistoryClient, to_unix_utc
from polybot.config import TradingConfig
from polybot.strategy.scanner import MarketScanner

# 2026-07-03T12:00:00Z 의 unix epoch (타임존 무관 고정값)
EPOCH_20260703_1200_UTC = 1_783_080_000
NOW = datetime(2026, 7, 3, 12, 0, 0)  # naive UTC (scanner가 넘기는 형식)


def test_to_unix_utc_treats_naive_as_utc():
    """naive datetime은 로컬 타임존이 아니라 UTC로 해석해야 한다.

    구현이 `.timestamp()`를 그대로 쓰면 KST 머신에서 9시간(32400초) 어긋난다.
    """
    assert to_unix_utc(NOW) == EPOCH_20260703_1200_UTC


def test_get_price_history_sends_utc_epoch_params():
    """startTs/endTs가 UTC 기준 epoch로 전송되는지 검증."""
    client = HistoryClient()
    response = MagicMock()
    response.json.return_value = {"history": []}
    response.raise_for_status.return_value = None
    client.session = MagicMock()
    client.session.get.return_value = response

    client.get_price_history("token123", start=NOW - timedelta(hours=24), end=NOW)

    params = client.session.get.call_args.kwargs["params"]
    assert params["endTs"] == EPOCH_20260703_1200_UTC
    assert params["startTs"] == EPOCH_20260703_1200_UTC - 24 * 3600
    assert params["market"] == "token123"


def test_get_price_history_parses_candles_as_naive_utc():
    """응답 캔들의 t(epoch)는 naive UTC datetime으로 변환된다 (스냅샷과 동일 기준)."""
    client = HistoryClient()
    response = MagicMock()
    response.json.return_value = {
        "history": [
            {"t": EPOCH_20260703_1200_UTC - 600, "p": "0.42"},
            {"t": EPOCH_20260703_1200_UTC, "p": 0.55},
        ]
    }
    response.raise_for_status.return_value = None
    client.session = MagicMock()
    client.session.get.return_value = response

    points = client.get_price_history("token123", start=NOW - timedelta(hours=1), end=NOW)

    assert points is not None
    assert points[0] == (NOW - timedelta(minutes=10), 0.42)
    assert points[1] == (NOW, 0.55)
    assert points[1][0].tzinfo is None


def test_get_price_history_returns_none_on_error():
    """모든 예외는 조용히 None (§3.6 - 백필 실패는 '데이터 부족'으로 취급)."""
    client = HistoryClient()
    client.session = MagicMock()
    client.session.get.side_effect = ConnectionError("boom")

    assert client.get_price_history("token123", NOW - timedelta(hours=1), NOW) is None


def test_get_price_history_returns_none_on_empty_history():
    client = HistoryClient()
    response = MagicMock()
    response.json.return_value = {"history": []}
    response.raise_for_status.return_value = None
    client.session = MagicMock()
    client.session.get.return_value = response

    assert client.get_price_history("token123", NOW - timedelta(hours=1), NOW) is None


def _batch_response(history):
    response = MagicMock()
    response.json.return_value = {"history": history}
    return response


def test_batch_chunks_at_twenty_and_cached_get_does_not_hit_network():
    client = HistoryClient()
    client.session = MagicMock()
    client.session.post.side_effect = lambda _url, *, json, timeout: _batch_response({
        token: [{"t": json["start_ts"], "p": "0.42"}]
        for token in json["markets"]
    })
    start = NOW - timedelta(hours=24)

    result = client.prefetch_price_histories(
        [f"token-{index}" for index in range(41)], start, NOW
    )

    assert client.session.post.call_count == 3
    assert result["token-0"][0][1] == 0.42
    assert client.get_price_history("token-0", start, NOW) == result["token-0"]
    client.session.get.assert_not_called()


def test_batch_missing_and_malformed_token_are_isolated_and_cached():
    client = HistoryClient()
    client.session = MagicMock()
    client.session.post.return_value = _batch_response({
        "good": [{"t": EPOCH_20260703_1200_UTC, "p": "0.4"}],
        "bad": [{"t": "broken", "p": "0.5"}],
    })
    start = NOW - timedelta(hours=24)

    result = client.prefetch_price_histories(["good", "missing", "bad"], start, NOW)

    assert result["good"] is not None
    assert result["missing"] is None
    assert result["bad"] is None
    assert client.get_price_history("missing", start, NOW) is None
    client.session.get.assert_not_called()


def test_failed_batch_is_missing_without_individual_fallback():
    client = HistoryClient()
    client.session = MagicMock()
    client.session.post.side_effect = RuntimeError("outage")
    start = NOW - timedelta(hours=24)

    assert client.prefetch_price_histories(["token"], start, NOW) == {"token": None}
    assert client.get_price_history("token", start, NOW) is None
    client.session.get.assert_not_called()


def test_scanner_batches_invalid_windows_before_per_market_evaluation():
    client = HistoryClient()
    client.session = MagicMock()
    client.session.post.side_effect = lambda _url, *, json, timeout: _batch_response({
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
