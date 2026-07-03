"""prices-history 백필 클라이언트 테스트 (네트워크 없이 mock 기반).

핵심 회귀 방지: naive UTC datetime을 로컬 타임존으로 해석해 startTs/endTs가
어긋나는 버그 (KST 머신에서 백필 윈도우가 9시간 과거로 밀림 - golden-lime의
to_unix_utc 패턴). 20일 룩백 전략은 백필이 생명선이라 특히 중요하다.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from polybot.api.history_client import HistoryClient, to_unix_utc

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


def test_get_price_history_sends_utc_epoch_and_fidelity():
    """startTs/endTs가 UTC epoch로, fidelity가 그대로 전송되는지 검증.

    scanner는 20일 범위를 fidelity=60(시간 캔들)으로 요청한다.
    """
    client = make_mock_client()
    client.get_price_history(
        "token123", start=NOW - timedelta(days=20), end=NOW, fidelity=60
    )

    params = client.session.get.call_args.kwargs["params"]
    assert params["market"] == "token123"
    assert params["endTs"] == EPOCH_20260703_1200_UTC
    assert params["startTs"] == EPOCH_20260703_1200_UTC - 20 * 86400
    assert params["fidelity"] == 60


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
