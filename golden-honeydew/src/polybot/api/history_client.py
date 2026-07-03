"""CLOB prices-history 백필 클라이언트 (§3.6: cold start 해결).

`GET https://clob.polymarket.com/prices-history`는 public endpoint(인증 불필요)로
분 단위 가격 캔들을 반환한다. 이 endpoint는 레포 문서에 없는 외부 지식이므로,
스키마 변경/장애 등 **모든 예외는 조용히 None을 반환**한다 —
백필 실패는 단순히 "데이터 부족"으로 취급되고 봇은 스냅샷 축적으로 자연 회복한다.
"""
import logging
from datetime import datetime
from typing import List, Optional
import requests

from ..strategy.signals import SnapshotPoint

logger = logging.getLogger(__name__)


class HistoryClient:
    """Client for Polymarket CLOB price history (public, no auth)."""

    BASE_URL = "https://clob.polymarket.com"

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenHoneydew-PolyBot/1.0"
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
            token_id: CLOB token ID
            start_ts: 시작 unix timestamp (sec)
            end_ts: 종료 unix timestamp (sec)
            fidelity: 캔들 간격 (분 단위, 기본 10)

        Returns:
            SnapshotPoint 리스트 (volume_24h=None), 실패 시 None
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
                timeout=self.timeout,
            )
            response.raise_for_status()

            history = response.json().get("history") or []
            points = [
                SnapshotPoint(
                    timestamp=datetime.utcfromtimestamp(int(item["t"])),
                    probability=float(item["p"]),
                )
                for item in history
            ]
            logger.debug(f"히스토리 백필 {len(points)}개 조회 - token: {token_id[:16]}...")
            return points

        except Exception as e:
            # 백필은 best-effort. 실패해도 봇은 정상 동작해야 한다.
            logger.debug(f"히스토리 백필 실패 (무시) - token: {token_id[:16]}...: {e}")
            return None
