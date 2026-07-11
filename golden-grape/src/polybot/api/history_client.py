"""CLOB prices-history 백필 클라이언트 (cold start 해결).

공식 public `GET /prices-history`와 `POST /batch-prices-history`로 과거 가격
캔들을 조회해 스냅샷 부족을 메운다. 모든 예외는 조용히 None으로 격리하고,
백필 실패는 "데이터 부족"으로 취급한다 (스냅샷 축적으로 자연 회복).
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple
import requests

from ..strategy.signals import SnapshotPoint

logger = logging.getLogger(__name__)


class HistoryClient:
    """CLOB /prices-history 조회 클라이언트."""

    BASE_URL = "https://clob.polymarket.com"
    MAX_BATCH_MARKETS = 20

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenGrape-PolyBot/1.0",
        })
        self._prefetched: Dict[
            Tuple[str, int, int, int], Optional[List[SnapshotPoint]]
        ] = {}

    @staticmethod
    def _parse_points(history) -> Optional[List[SnapshotPoint]]:
        """Parse one token without allowing malformed peer data to leak."""
        if not isinstance(history, list):
            return None
        try:
            points = [
                SnapshotPoint(
                    timestamp=datetime.fromtimestamp(
                        int(item["t"]), tz=timezone.utc
                    ).replace(tzinfo=None),
                    probability=float(item["p"]),
                )
                for item in history
            ]
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        points.sort(key=lambda point: point.timestamp)
        return points or None

    def prefetch_price_histories(
        self,
        token_ids: Iterable[str],
        start_ts: int,
        end_ts: int,
        fidelity: int = 10,
    ) -> Dict[str, Optional[List[SnapshotPoint]]]:
        """Batch-fetch invalid local windows, at most 20 tokens per POST."""
        start_ts = int(start_ts)
        end_ts = int(end_ts)
        fidelity = int(fidelity)
        unique_tokens = list(dict.fromkeys(str(token) for token in token_ids if token))
        self._prefetched.clear()
        batch_requests = 0

        for offset in range(0, len(unique_tokens), self.MAX_BATCH_MARKETS):
            batch = unique_tokens[offset : offset + self.MAX_BATCH_MARKETS]
            batch_requests += 1
            parsed: Dict[str, Optional[List[SnapshotPoint]]]
            try:
                response = self.session.post(
                    f"{self.BASE_URL}/batch-prices-history",
                    json={
                        "markets": batch,
                        "start_ts": start_ts,
                        "end_ts": end_ts,
                        "fidelity": fidelity,
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                history_map = (response.json() or {}).get("history")
                if not isinstance(history_map, dict):
                    raise ValueError("batch history response가 object가 아닙니다")
                parsed = {
                    token: self._parse_points(history_map.get(token) or [])
                    for token in batch
                }
            except Exception as exc:
                logger.debug(
                    "히스토리 배치 백필 실패 (무시) - token %d개: %s",
                    len(batch),
                    exc,
                )
                parsed = {token: None for token in batch}

            for token, points in parsed.items():
                self._prefetched[(token, start_ts, end_ts, fidelity)] = points

        if unique_tokens:
            logger.info(
                "히스토리 배치 백필 - token %d개, 요청 %d회",
                len(unique_tokens),
                batch_requests,
            )
        return {
            token: self._prefetched[(token, start_ts, end_ts, fidelity)]
            for token in unique_tokens
        }

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
        cache_key = (str(token_id), int(start_ts), int(end_ts), int(fidelity))
        if cache_key in self._prefetched:
            return self._prefetched[cache_key]

        try:
            response = self.session.get(
                f"{self.BASE_URL}/prices-history",
                params={
                    "market": token_id,
                    "startTs": start_ts,
                    "endTs": end_ts,
                    "fidelity": fidelity,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            history = (response.json() or {}).get("history") or []

            return self._parse_points(history)

        except Exception as e:
            # 백필 실패는 치명적이지 않다 - 데이터 부족으로 취급하고 넘어간다
            logger.debug(f"히스토리 백필 실패(무시) - token: {token_id}: {e}")
            return None
