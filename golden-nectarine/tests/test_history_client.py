"""prices-history 백필 클라이언트 테스트 (네트워크 없이 mock 기반).

핵심 회귀 방지: naive UTC datetime을 로컬 타임존으로 해석해 startTs/endTs가
어긋나는 버그 (KST 머신에서 백필 윈도우가 9시간 과거로 밀림 - golden-lime의
to_unix_utc 패턴). 20일 룩백 전략은 백필이 생명선이라 특히 중요하다.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from polybot.api.history_client import HistoryClient, to_unix_utc
from polybot.config import TradingConfig
from polybot.strategy.scanner import MarketScanner

# 2026-07-03T12:00:00Z 의 unix epoch (타임존 무관 고정값)
EPOCH_20260703_1200_UTC = 1_783_080_000
NOW = datetime(2026, 7, 3, 12, 0, 0)  # naive UTC (scanner가 넘기는 형식)


def make_mock_client(history=None):
    client = HistoryClient()
    response = MagicMock()
    response.json.return_value = {"history": history or []}
    response.raise_for_status.return_value = None
    client.session = MagicMock()
    client.session.get.return_value = response
    return client


def test_to_unix_utc_treats_naive_as_utc():
    """naive datetime은 로컬 타임존이 아니라 UTC로 해석해야 한다.

    구현이 `.timestamp()`를 그대로 쓰면 KST 머신에서 9시간(32400초) 어긋난다.
    """
    assert to_unix_utc(NOW) == EPOCH_20260703_1200_UTC


def test_get_price_history_chunks_long_ranges():
    """20일 범위는 MAX_RANGE_HOURS(336h) 이하 조각으로 나눠 요청해야 한다.

    /prices-history는 범위가 ~15일(360h)을 넘으면 HTTP 400을 반환하므로
    (2026-07-06 실측), 단일 20일 요청은 백필 전면 실패였다 (회귀 방지).
    """
    client = make_mock_client()
    client.get_price_history(
        "token123", start=NOW - timedelta(days=20), end=NOW, fidelity=60
    )

    calls = client.session.get.call_args_list
    assert len(calls) == 2  # 480h -> 336h + 144h

    start_epoch = EPOCH_20260703_1200_UTC - 20 * 86400
    split_epoch = start_epoch + 336 * 3600
    first, second = (c.kwargs["params"] for c in calls)

    assert first["market"] == "token123" and first["fidelity"] == 60
    assert (first["startTs"], first["endTs"]) == (start_epoch, split_epoch)
    assert (second["startTs"], second["endTs"]) == (split_epoch, EPOCH_20260703_1200_UTC)
    # 조각별 범위가 상한 이하인지
    for params in (first, second):
        assert params["endTs"] - params["startTs"] <= 336 * 3600


def test_get_price_history_single_call_for_short_range():
    """상한 이하 범위(48h 등, 다른 봇들의 사용 패턴)는 기존처럼 1회 호출."""
    client = make_mock_client()
    client.get_price_history(
        "token123", start=NOW - timedelta(hours=48), end=NOW, fidelity=10
    )

    calls = client.session.get.call_args_list
    assert len(calls) == 1
    params = calls[0].kwargs["params"]
    assert params["startTs"] == EPOCH_20260703_1200_UTC - 48 * 3600
    assert params["endTs"] == EPOCH_20260703_1200_UTC
    assert params["fidelity"] == 10


def test_get_price_history_merges_partial_chunk_failure():
    """조각 일부가 실패해도 성공한 조각의 포인트는 반환한다 (best-effort)."""
    client = HistoryClient()
    ok_response = MagicMock()
    ok_response.json.return_value = {
        "history": [{"t": EPOCH_20260703_1200_UTC, "p": 0.20}]
    }
    ok_response.raise_for_status.return_value = None
    client.session = MagicMock()
    client.session.get.side_effect = [RuntimeError("400"), ok_response]

    points = client.get_price_history(
        "token123", start=NOW - timedelta(days=20), end=NOW, fidelity=60
    )
    assert points == [(NOW, 0.20)]


def test_get_price_history_parses_candles_as_naive_utc():
    """응답 캔들의 epoch → naive UTC datetime 변환 (스냅샷과 같은 기준)."""
    client = make_mock_client(history=[
        {"t": EPOCH_20260703_1200_UTC - 3600, "p": 0.21},
        {"t": EPOCH_20260703_1200_UTC, "p": 0.20},
    ])
    points = client.get_price_history(
        "token123", start=NOW - timedelta(days=20), end=NOW, fidelity=60
    )
    assert points == [
        (NOW - timedelta(hours=1), 0.21),
        (NOW, 0.20),
    ]


def test_get_price_history_returns_none_on_error():
    """모든 예외는 조용히 None - 백필 실패는 '데이터 부족'으로 취급 (§3.6)."""
    client = HistoryClient()
    client.session = MagicMock()
    client.session.get.side_effect = RuntimeError("network down")
    assert client.get_price_history(
        "token123", start=NOW - timedelta(days=20), end=NOW
    ) is None


def test_get_price_history_returns_none_on_empty_history():
    client = make_mock_client(history=[])
    assert client.get_price_history(
        "token123", start=NOW - timedelta(days=20), end=NOW
    ) is None


def test_batch_prefetch_chunks_tokens_and_long_range_without_get_fallback():
    client = HistoryClient()
    client.session = MagicMock()

    def post(_url, *, json, timeout):
        assert timeout == client.timeout
        assert len(json["markets"]) <= client.MAX_BATCH_MARKETS
        response = MagicMock()
        response.json.return_value = {
            "history": {
                token: [{"t": json["start_ts"], "p": "0.25"}]
                for token in json["markets"]
            }
        }
        return response

    client.session.post.side_effect = post
    tokens = [f"token-{index}" for index in range(21)]
    start = NOW - timedelta(days=20)

    result = client.prefetch_price_histories(tokens, start, NOW, fidelity=60)

    # 21 tokens => two batches; 20 days => 14d + 6d chunks.
    assert client.session.post.call_count == 4
    assert len(result["token-0"]) == 2
    assert result["token-0"][0][1] == 0.25
    assert client.get_price_history("token-0", start, NOW, fidelity=60) == result["token-0"]
    client.session.get.assert_not_called()


def test_batch_prefetch_keeps_successful_time_chunk_when_other_chunk_fails():
    client = HistoryClient()
    client.session = MagicMock()
    start = NOW - timedelta(days=20)
    response = MagicMock()
    response.json.return_value = {
        "history": {"token": [{"t": to_unix_utc(start), "p": "0.33"}]}
    }
    client.session.post.side_effect = [response, RuntimeError("chunk outage")]

    result = client.prefetch_price_histories(["token"], start, NOW, fidelity=60)

    assert result["token"] == [(start, 0.33)]


def test_scanner_batches_only_markets_reaching_history_stage():
    client = HistoryClient()
    client.session = MagicMock()

    def post(_url, *, json, timeout):
        response = MagicMock()
        response.json.return_value = {
            "history": {
                token: [{"t": json["start_ts"], "p": "0.25"}]
                for token in json["markets"]
            }
        }
        return response

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
            "outcomePrices": ["0.25", "0.75"],
            "clobTokenIds": [f"token-{index}", f"no-token-{index}"],
            "outcomes": ["Yes", "No"],
            "liquidity": "20000",
            "volume24hr": "1000",
            "endDate": "2030-01-01T00:00:00Z",
        }
        for index in range(21)
    ]

    scanner.scan_buy_candidates(markets)

    # 21 markets x two time chunks, batched at 20 tokens.
    assert client.session.post.call_count == 4
    client.session.get.assert_not_called()


def test_scanner_does_not_call_history_for_valid_local_window():
    client = HistoryClient()
    client.session = MagicMock()
    now = datetime.utcnow()

    class Snapshot:
        def __init__(self, timestamp, probability):
            self.timestamp = timestamp
            self.probability = probability

    class WarmRepo:
        def get_snapshots_since(self, condition_id, since):
            return [
                Snapshot(
                    now - timedelta(days=19, hours=23)
                    + timedelta(hours=(19 * 24 + 23) * index / 20),
                    0.25,
                )
                for index in range(21)
            ]

    scanner = MarketScanner(
        MagicMock(), TradingConfig(), repo=WarmRepo(), history_client=client
    )
    market = {
        "conditionId": "condition",
        "outcomePrices": ["0.25", "0.75"],
        "clobTokenIds": ["token", "no-token"],
        "outcomes": ["Yes", "No"],
        "liquidity": "20000",
        "volume24hr": "1000",
        "endDate": "2030-01-01T00:00:00Z",
    }

    scanner.scan_buy_candidates([market])

    client.session.post.assert_not_called()
    client.session.get.assert_not_called()
