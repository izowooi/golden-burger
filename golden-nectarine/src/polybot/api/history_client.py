"""CLOB prices-history 백필 클라이언트 (cold start 해결).

`GET https://clob.polymarket.com/prices-history`는 public endpoint(인증 불필요)로
분 단위 캔들 이력을 반환한다. 단, 레포 문서에 없는 외부 지식 기반 endpoint이므로
**모든 예외는 조용히 None을 반환**한다 - 백필 실패는 "데이터 부족"으로 취급되고
봇은 스냅샷 축적으로 자연 회복한다.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

# /prices-history는 startTs~endTs 범위가 약 15일(360h)을 넘으면 HTTP 400을
# 반환한다 (2026-07-06 실측: 360h OK, 368h부터 400). 20일 룩백은 단일 호출이
# 불가능하므로 이 상한 이하 조각으로 나눠 요청한 뒤 병합한다.
MAX_RANGE_HOURS = 336  # 14일 - 실측 상한(~15일)에 여유


def to_unix_utc(dt: datetime) -> int:
    """datetime → unix epoch (sec). naive datetime은 UTC로 간주한다.

    naive datetime의 `.timestamp()`는 로컬 타임존으로 해석되어 KST 등
    비UTC 머신에서 startTs/endTs가 수 시간 어긋난다 (백필 윈도우가 통째로
    과거로 밀려 최근 구간을 영원히 못 받는 버그) - 반드시 UTC로 고정한다.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class HistoryClient:
    """CLOB /prices-history 조회 클라이언트."""

    BASE_URL = "https://clob.polymarket.com"

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenNectarine-PolyBot/1.0",
        })

    def get_price_history(
        self,
        token_id: str,
        start: datetime,
        end: datetime,
        fidelity: int = 10,
    ) -> Optional[List[Tuple[datetime, float]]]:
        """토큰 가격 이력 조회.

        범위가 MAX_RANGE_HOURS를 넘으면 API 400을 피하기 위해 조각으로 나눠
        요청하고 timestamp 기준으로 병합한다 (조각 일부 실패는 무시 - best-effort).

        Args:
            token_id: CLOB token id (clobTokenIds의 값)
            start: 시작 시각 (UTC naive)
            end: 종료 시각 (UTC naive)
            fidelity: 캔들 간격 (분 단위, 기본 10)

        Returns:
            시간 오름차순 (UTC naive datetime, price) 튜플 리스트. 실패/빈 응답 시 None.
        """
        step = timedelta(hours=MAX_RANGE_HOURS)
        merged = {}
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + step, end)
            points = self._fetch_range(token_id, chunk_start, chunk_end, fidelity)
            for ts, price in points or []:
                merged[ts] = price
            chunk_start = chunk_end

        if not merged:
            return None
        return sorted(merged.items())

    def _fetch_range(
        self,
        token_id: str,
        start: datetime,
        end: datetime,
        fidelity: int,
    ) -> Optional[List[Tuple[datetime, float]]]:
        """단일 startTs~endTs 조각 조회 (MAX_RANGE_HOURS 이하 전제)."""
        try:
            response = self.session.get(
                f"{self.BASE_URL}/prices-history",
                params={
                    "market": token_id,
                    "startTs": to_unix_utc(start),
                    "endTs": to_unix_utc(end),
                    "fidelity": fidelity,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            history = response.json().get("history") or []

            points: List[Tuple[datetime, float]] = []
            for candle in history:
                t = candle.get("t")
                p = candle.get("p")
                if t is None or p is None:
                    continue
                # epoch → naive UTC (스냅샷 timestamp와 같은 기준)
                ts = datetime.fromtimestamp(int(t), tz=timezone.utc).replace(tzinfo=None)
                points.append((ts, float(p)))

            return points or None
        except Exception as e:
            # 백필은 best-effort - 실패는 "데이터 부족"으로 취급 (§3.6)
            logger.debug(f"prices-history 백필 실패 (무시) - token: {token_id}: {e}")
            return None
