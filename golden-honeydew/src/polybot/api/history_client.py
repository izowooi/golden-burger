"""CLOB prices-history 백필 클라이언트 (§3.6: cold start 해결).

Polymarket의 public `GET /prices-history`와 `POST /batch-prices-history`를 사용해
분 단위 가격 캔들을 반환한다. API 장애나 일부 응답 오류는 None으로 격리한다 —
백필 실패는 단순히 "데이터 부족"으로 취급되고 봇은 스냅샷 축적으로 자연 회복한다.
"""
import logging
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple
import requests

from ..strategy.signals import SnapshotPoint
from ..utils.retry import rate_limit_handler

logger = logging.getLogger(__name__)


class HistoryClient:
    """Client for Polymarket CLOB price history (public, no auth)."""

    BASE_URL = "https://clob.polymarket.com"
    MAX_BATCH_MARKETS = 20

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenHoneydew-PolyBot/1.0"
        })
        self._prefetched: Dict[
            Tuple[str, int, int, int], Optional[List[SnapshotPoint]]
        ] = {}

    @staticmethod
    def _parse_points(history) -> Optional[List[SnapshotPoint]]:
        """Parse one token's history without letting one token poison a batch."""
        if not isinstance(history, list):
            return None
        try:
            points = [
                SnapshotPoint(
                    timestamp=datetime.utcfromtimestamp(int(item["t"])),
                    probability=float(item["p"]),
                )
                for item in history
            ]
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        return points or None

    @rate_limit_handler(max_retries=3, base_delay=0.5)
    def _post_batch(self, payload: Dict):
        """POST one bounded batch with retry only for transient HTTP failures."""
        response = self.session.post(
            f"{self.BASE_URL}/batch-prices-history",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response

    def prefetch_price_histories(
        self,
        token_ids: Iterable[str],
        start_ts: int,
        end_ts: int,
        fidelity: int = 10,
    ) -> Dict[str, Optional[List[SnapshotPoint]]]:
        """Fetch up to 20 token histories per official batch request.

        Results are cached only for this process/cycle and consumed by
        :meth:`get_price_history`. A failed batch is left as missing evidence;
        it is not expanded into 20 individual requests, which keeps failures
        bounded and preserves the strategy's fail-closed entry behavior.
        """
        start_ts = int(start_ts)
        end_ts = int(end_ts)
        fidelity = int(fidelity)
        unique_tokens = list(dict.fromkeys(str(token) for token in token_ids if token))
        self._prefetched.clear()
        batch_requests = 0

        for offset in range(0, len(unique_tokens), self.MAX_BATCH_MARKETS):
            batch = unique_tokens[offset : offset + self.MAX_BATCH_MARKETS]
            batch_requests += 1
            parsed: Dict[str, Optional[List[SnapshotPoint]]] = {}
            try:
                response = self._post_batch(
                    {
                        "markets": batch,
                        "start_ts": start_ts,
                        "end_ts": end_ts,
                        "fidelity": fidelity,
                    }
                )
                history_map = response.json().get("history")
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
            token_id: CLOB token ID
            start_ts: 시작 unix timestamp (sec)
            end_ts: 종료 unix timestamp (sec)
            fidelity: 캔들 간격 (분 단위, 기본 10)

        Returns:
            SnapshotPoint 리스트 (volume_24h=None), 실패 시 None
        """
        cache_key = (str(token_id), int(start_ts), int(end_ts), int(fidelity))
        if cache_key in self._prefetched:
            return self._prefetched[cache_key]

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
            points = self._parse_points(history) or []
            logger.debug(f"히스토리 백필 {len(points)}개 조회 - token: {token_id[:16]}...")
            return points

        except Exception as e:
            # 백필은 best-effort. 실패해도 봇은 정상 동작해야 한다.
            logger.debug(f"히스토리 백필 실패 (무시) - token: {token_id[:16]}...: {e}")
            return None
