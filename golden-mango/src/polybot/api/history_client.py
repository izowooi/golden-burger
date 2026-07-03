"""CLOB prices-history 백필 클라이언트 (§3.6 cold start 해결).

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
    DEFAULT_FIDELITY = 10  # 분 단위 캔들

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenMango-PolyBot/1.0",
        })

    def get_price_history(
        self,
        token_id: str,
        start: datetime,
        end: datetime,
        fidelity: int = DEFAULT_FIDELITY,
    ) -> Optional[List[Tuple[datetime, float]]]:
        """토큰 가격 이력 조회.

        Args:
            token_id: CLOB token id (clobTokenIds의 값, YES 토큰 기준으로 조회할 것)
            start: 시작 시각 (naive UTC 또는 aware)
            end: 종료 시각 (naive UTC 또는 aware)
            fidelity: 캔들 간격 (분 단위, 기본 10)

        Returns:
            [(naive UTC datetime, price), ...] 시간 오름차순. 실패/빈 응답 시 None.
        """
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
            history = (response.json() or {}).get("history") or []

            points: List[Tuple[datetime, float]] = []
            for candle in history:
                t = candle.get("t")
                p = candle.get("p")
                if t is None or p is None:
                    continue
                # epoch → naive UTC (DB 스냅샷 timestamp와 같은 기준)
                ts = datetime.fromtimestamp(int(t), tz=timezone.utc).replace(tzinfo=None)
                points.append((ts, float(p)))

            points.sort(key=lambda point: point[0])
            return points or None
        except Exception as e:
            # 백필은 best-effort - 실패는 "데이터 부족"으로 취급 (§3.6)
            logger.debug(f"prices-history 백필 실패 (무시) - token: {token_id}: {e}")
            return None

    def get_recent_history(
        self,
        token_id: str,
        hours_back: float,
        fidelity: int = DEFAULT_FIDELITY,
        now: Optional[datetime] = None,
    ) -> Optional[List[Tuple[datetime, float]]]:
        """now 기준 최근 hours_back 시간의 가격 이력 조회.

        Args:
            token_id: CLOB token ID
            hours_back: 조회할 시간 범위
            fidelity: 캔들 간격 (분)
            now: 기준 시각 (naive UTC, 기본: utcnow)

        Returns:
            [(timestamp, price), ...] 또는 실패 시 None
        """
        now = now or datetime.utcnow()
        return self.get_price_history(
            token_id, now - timedelta(hours=hours_back), now, fidelity
        )
