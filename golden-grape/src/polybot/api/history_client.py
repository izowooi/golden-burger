"""CLOB prices-history 백필 클라이언트 (cold start 해결).

`GET https://clob.polymarket.com/prices-history` (public, 인증 불필요)로
과거 가격 캔들을 조회해 스냅샷 부족을 메운다.

주의: 이 endpoint는 레포 문서에 없는 외부 지식이므로 실패해도 봇이
정상 동작해야 한다. 모든 예외는 조용히 None을 반환하고, 백필 실패는
"데이터 부족"으로 취급된다 (스냅샷 축적으로 자연 회복).
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional
import requests

from ..strategy.signals import SnapshotPoint

logger = logging.getLogger(__name__)


class HistoryClient:
    """CLOB /prices-history 조회 클라이언트."""

    BASE_URL = "https://clob.polymarket.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenGrape-PolyBot/1.0",
        })

    def get_price_history(
        self,
        token_id: str,
        start_ts: int,
        end_ts: int,
        fidelity: int = 10,
    ) -> Optional[List[SnapshotPoint]]:
        """토큰 가격 히스토리 조회 → SnapshotPoint 리스트 변환.

        Args:
            token_id: CLOB token id (YES 토큰을 넘기면 YES 가격 기준)
            start_ts: 시작 unix timestamp (초)
            end_ts: 종료 unix timestamp (초)
            fidelity: 캔들 해상도 (분 단위, 기본 10)

        Returns:
            시간 오름차순 SnapshotPoint 리스트 (naive UTC),
            실패/데이터 없음 시 None
        """
        try:
            response = self.session.get(
                f"{self.BASE_URL}/prices-history",
                params={
                    "market": token_id,
                    "startTs": start_ts,
                    "endTs": end_ts,
                    "fidelity": fidelity,
                },
                timeout=10,
            )
            response.raise_for_status()
            history = (response.json() or {}).get("history") or []

            points = [
                SnapshotPoint(
                    timestamp=datetime.fromtimestamp(
                        int(item["t"]), tz=timezone.utc
                    ).replace(tzinfo=None),
                    probability=float(item["p"]),
                )
                for item in history
            ]
            points.sort(key=lambda p: p.timestamp)
            return points or None

        except Exception as e:
            # 백필 실패는 치명적이지 않다 - 데이터 부족으로 취급하고 넘어간다
            logger.debug(f"히스토리 백필 실패(무시) - token: {token_id}: {e}")
            return None
