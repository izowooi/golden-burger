"""CLOB /prices-history 백필 클라이언트 (cold start 해결).

`GET https://clob.polymarket.com/prices-history` (public, 인증 불필요)로
과거 가격 캔들을 받아 스냅샷 부족을 메운다.

주의: 이 endpoint는 레포 문서에 없는 외부 지식 기반이므로 실패해도
봇이 정상 동작해야 한다. 모든 예외는 조용히 None을 반환하고,
백필 실패는 "데이터 부족"으로 취급된다 (스냅샷 축적으로 자연 회복).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional
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


@dataclass(frozen=True)
class HistoryPoint:
    """백필 포인트 1개. strategy.signals의 PricePoint와 attribute 호환 (duck typing).

    api 계층이 strategy 계층을 import하면 순환 참조가 생기므로 별도 정의한다.
    백필은 volume을 제공하지 않으므로 volume_24h는 항상 None이다.
    """
    timestamp: datetime  # naive UTC
    probability: float   # YES 가격 (0.0~1.0)
    volume_24h: Optional[float] = None


class HistoryClient:
    """Client for Polymarket CLOB price history (public endpoint)."""

    BASE_URL = "https://clob.polymarket.com"
    DEFAULT_FIDELITY = 10  # 분 단위 캔들

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenOrange-PolyBot/1.0",
        })

    def get_price_history(
        self,
        token_id: str,
        start: datetime,
        end: datetime,
        fidelity: int = DEFAULT_FIDELITY,
    ) -> Optional[List[HistoryPoint]]:
        """토큰의 과거 가격 이력 조회.

        Args:
            token_id: CLOB token ID (YES 토큰 - 스냅샷 단위와 일치)
            start: 시작 시각 (naive는 UTC로 간주)
            end: 종료 시각 (naive는 UTC로 간주)
            fidelity: 캔들 간격 (분)

        Returns:
            HistoryPoint 리스트 (naive UTC timestamp) 또는 실패/빈 응답 시 None
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
            points = []
            for item in history:
                t = item.get("t")
                p = item.get("p")
                if t is None or p is None:
                    continue
                # epoch → naive UTC (스냅샷 timestamp와 같은 기준)
                ts = datetime.fromtimestamp(int(t), tz=timezone.utc).replace(tzinfo=None)
                points.append(HistoryPoint(timestamp=ts, probability=float(p)))

            if not points:
                return None

            logger.debug(f"히스토리 백필 {len(points)}개 수신 - token: {token_id[:16]}...")
            return points

        except Exception as e:
            # 백필 실패는 치명적이지 않다 - 조용히 None (데이터 부족으로 취급)
            logger.debug(f"prices-history 백필 실패 - token: {token_id[:16]}...: {e}")
            return None
