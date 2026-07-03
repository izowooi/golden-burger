"""CLOB prices-history 백필 클라이언트 (§3.6 cold start 해결).

CLOB public endpoint `GET /prices-history` (인증 불필요)로 과거 가격 캔들을
조회해 자체 스냅샷이 부족한 시장의 모멘텀 윈도우를 채운다.

이 endpoint는 레포 문서에 없는 외부 지식이므로 실패해도 봇이 정상 동작해야 한다.
모든 예외는 조용히 None을 반환하고, 백필 실패는 "데이터 부족"으로 취급한다
(스냅샷 축적으로 자연 회복).
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)


class HistoryClient:
    """CLOB /prices-history 조회 클라이언트."""

    BASE_URL = "https://clob.polymarket.com"
    DEFAULT_FIDELITY = 10  # 분 단위 캔들

    def __init__(self, timeout: float = 10.0):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenDate-PolyBot/1.0",
        })
        self.timeout = timeout

    def get_price_history(
        self,
        token_id: str,
        start_ts: float,
        end_ts: float,
        fidelity: int = DEFAULT_FIDELITY,
    ) -> Optional[List[Tuple[datetime, float]]]:
        """토큰의 과거 가격 이력 조회.

        Args:
            token_id: CLOB token ID (YES 토큰 기준으로 조회할 것)
            start_ts: 시작 unix timestamp (초)
            end_ts: 종료 unix timestamp (초)
            fidelity: 캔들 간격 (분)

        Returns:
            [(timestamp(naive UTC), price), ...] 시간 오름차순.
            실패 시 None (예외를 밖으로 던지지 않는다).
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

            history = (response.json() or {}).get("history") or []
            points = []
            for entry in history:
                ts = datetime.fromtimestamp(
                    int(entry["t"]), tz=timezone.utc
                ).replace(tzinfo=None)  # DB 스냅샷과 동일한 naive UTC
                points.append((ts, float(entry["p"])))
            points.sort(key=lambda p: p[0])
            return points

        except Exception as e:
            logger.debug(f"prices-history 백필 실패 - token: {token_id}: {e}")
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
        end_ts = now.replace(tzinfo=timezone.utc).timestamp()
        start_ts = end_ts - hours_back * 3600
        return self.get_price_history(token_id, start_ts, end_ts, fidelity)
