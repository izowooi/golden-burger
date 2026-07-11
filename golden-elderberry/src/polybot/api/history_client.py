"""CLOB prices-history 백필 클라이언트 (cold start 해결).

공식 public `GET /prices-history`와 `POST /batch-prices-history`로 과거 가격
캔들을 조회해 자체 스냅샷이 부족한 시장의 윈도우를 채운다. 모든 예외는
조용히 None으로 격리한다. 백필 실패는 "데이터 부족"으로 취급되며 봇은
스냅샷 축적으로 자연 회복한다.
"""
import logging
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)


class HistoryClient:
    """CLOB prices-history API client (best-effort)."""

    BASE_URL = "https://clob.polymarket.com"
    DEFAULT_FIDELITY = 10  # 분 단위 캔들
    MAX_BATCH_MARKETS = 20

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenElderberry-PolyBot/1.0"
        })
        self._prefetched: Dict[
            Tuple[str, int, int, int], Optional[List[Tuple[datetime, float]]]
        ] = {}

    @staticmethod
    def _to_unix_utc(value: datetime) -> int:
        """Convert the scanner's naive-UTC datetimes without local TZ drift."""
        epoch = datetime(1970, 1, 1)
        return int((value - epoch).total_seconds())

    @staticmethod
    def _parse_points(history) -> Optional[List[Tuple[datetime, float]]]:
        """Parse one token without allowing malformed candles to poison peers."""
        if not isinstance(history, list):
            return None
        try:
            points = [
                (datetime.utcfromtimestamp(int(item["t"])), float(item["p"]))
                for item in history
            ]
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        return points or None

    def prefetch_price_histories(
        self,
        token_ids: Iterable[str],
        start: datetime,
        end: datetime,
        fidelity: int = DEFAULT_FIDELITY,
    ) -> Dict[str, Optional[List[Tuple[datetime, float]]]]:
        """Fetch at most 20 histories per official batch request.

        A failed/missing token remains cached as missing evidence. The later
        per-market call therefore cannot fan the failure back out into N GETs.
        """
        start_ts = self._to_unix_utc(start)
        end_ts = self._to_unix_utc(end)
        fidelity = int(fidelity)
        unique_tokens = list(dict.fromkeys(str(token) for token in token_ids if token))
        self._prefetched.clear()
        batch_requests = 0

        for offset in range(0, len(unique_tokens), self.MAX_BATCH_MARKETS):
            batch = unique_tokens[offset : offset + self.MAX_BATCH_MARKETS]
            batch_requests += 1
            parsed: Dict[str, Optional[List[Tuple[datetime, float]]]]
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

        cache_key = (
            str(token_id),
            self._to_unix_utc(start),
            self._to_unix_utc(end),
            int(fidelity),
        )
        if cache_key in self._prefetched:
            return self._prefetched[cache_key]

        try:
            params = {
                "market": token_id,
                "startTs": self._to_unix_utc(start),
                "endTs": self._to_unix_utc(end),
                "fidelity": fidelity,
            }
            response = self.session.get(
                f"{self.BASE_URL}/prices-history",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()

            history = response.json().get("history") or []
            points = self._parse_points(history) or []
            logger.debug(
                f"히스토리 백필 {len(points)}개 조회 - token: {token_id[:16]}..."
            )
            return points

        except Exception as e:
            # 백필은 best-effort. 실패해도 봇은 정상 동작해야 한다.
            logger.debug(f"히스토리 백필 실패 (무시) - token: {token_id}: {e}")
            return None
