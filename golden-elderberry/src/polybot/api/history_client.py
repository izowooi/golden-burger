"""CLOB prices-history 백필 클라이언트 (cold start 해결).

`GET https://clob.polymarket.com/prices-history` (public, 인증 불필요)로
과거 가격 캔들을 조회해 자체 스냅샷이 부족한 시장의 윈도우를 채운다.

이 endpoint는 공식 레포 문서에 없는 외부 지식이므로,
**모든 예외는 조용히 None을 반환**한다 - 백필 실패는 "데이터 부족"으로
취급되며 봇은 스냅샷 축적으로 자연 회복한다.
"""
import logging
from datetime import datetime
from typing import List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)


class HistoryClient:
    """CLOB prices-history API client (best-effort)."""

    BASE_URL = "https://clob.polymarket.com"
    DEFAULT_FIDELITY = 10  # 분 단위 캔들

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenElderberry-PolyBot/1.0"
        })

    def get_price_history(
        self,
        token_id: str,
        start: datetime,
        end: datetime,
        fidelity: int = DEFAULT_FIDELITY,
    ) -> Optional[List[Tuple[datetime, float]]]:
        """토큰의 과거 가격 이력 조회.

        Args:
            token_id: CLOB token ID (YES 토큰 기준으로 호출할 것)
            start: 시작 시각 (naive UTC)
            end: 종료 시각 (naive UTC)
            fidelity: 캔들 간격 (분)

        Returns:
            (naive UTC timestamp, price) 튜플 리스트 또는 실패 시 None
        """
        if not token_id:
            return None

        try:
            # naive UTC datetime -> unix seconds
            epoch = datetime(1970, 1, 1)
            params = {
                "market": token_id,
                "startTs": int((start - epoch).total_seconds()),
                "endTs": int((end - epoch).total_seconds()),
                "fidelity": fidelity,
            }
            response = self.session.get(
                f"{self.BASE_URL}/prices-history",
                params=params,
                timeout=10,
            )
            response.raise_for_status()

            history = response.json().get("history") or []
            points = [
                (datetime.utcfromtimestamp(int(item["t"])), float(item["p"]))
                for item in history
            ]
            logger.debug(
                f"히스토리 백필 {len(points)}개 조회 - token: {token_id[:16]}..."
            )
            return points

        except Exception as e:
            # 백필은 best-effort. 실패해도 봇은 정상 동작해야 한다.
            logger.debug(f"히스토리 백필 실패 (무시) - token: {token_id}: {e}")
            return None
