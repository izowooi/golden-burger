"""CLOB /prices-history 백필 클라이언트 (cold start 해결).

`GET https://clob.polymarket.com/prices-history` (public, 인증 불필요)로
과거 가격 캔들을 받아 스냅샷 부족을 메운다.

주의: 이 endpoint는 레포 문서에 없는 외부 지식 기반이므로 실패해도
봇이 정상 동작해야 한다. 모든 예외는 조용히 None을 반환하고,
백필 실패는 "데이터 부족"으로 취급된다 (스냅샷 축적으로 자연 회복).
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryPoint:
    """백필 포인트 1개. strategy.signals의 PricePoint와 attribute 호환 (duck typing).

    api 계층이 strategy 계층을 import하면 순환 참조가 생기므로 별도 정의한다.
    """
    timestamp: datetime  # naive UTC
    probability: float   # YES 가격 (0.0~1.0)


class HistoryClient:
    """Client for Polymarket CLOB price history (public endpoint)."""

    BASE_URL = "https://clob.polymarket.com"
    DEFAULT_FIDELITY = 10  # 분 단위 캔들

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenFig-PolyBot/1.0"
        })

    def get_price_history(
        self,
        token_id: str,
        start_ts: int,
        end_ts: int,
        fidelity: int = DEFAULT_FIDELITY,
    ) -> Optional[List[HistoryPoint]]:
        """토큰의 과거 가격 이력 조회.

        Args:
            token_id: CLOB token ID (YES 토큰 - 스냅샷 단위와 일치)
            start_ts: 시작 unix timestamp (sec)
            end_ts: 종료 unix timestamp (sec)
            fidelity: 캔들 간격 (분)

        Returns:
            HistoryPoint 리스트 (naive UTC timestamp) 또는 실패/빈 응답 시 None
        """
        try:
            response = self.session.get(
                f"{self.BASE_URL}/prices-history",
                params={
                    "market": token_id,
                    "startTs": int(start_ts),
                    "endTs": int(end_ts),
                    "fidelity": fidelity,
                },
                timeout=10,
            )
            response.raise_for_status()

            history = (response.json() or {}).get("history") or []
            points = []
            for item in history:
                t = item.get("t")
                p = item.get("p")
                if t is None or p is None:
                    continue
                points.append(HistoryPoint(
                    timestamp=datetime.utcfromtimestamp(int(t)),
                    probability=float(p),
                ))

            if not points:
                return None

            logger.debug(f"히스토리 백필 {len(points)}개 수신 - token: {token_id[:16]}...")
            return points

        except Exception as e:
            # 백필 실패는 치명적이지 않다 - 조용히 None (데이터 부족으로 취급)
            logger.debug(f"prices-history 백필 실패 - token: {token_id[:16]}...: {e}")
            return None
