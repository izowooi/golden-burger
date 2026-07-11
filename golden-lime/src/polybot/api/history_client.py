"""CLOB prices-history 백필 클라이언트 (cold start 해결).

공식 public `GET /prices-history`와 `POST /batch-prices-history`로 분 단위 캔들
이력을 반환한다. 모든 예외는 조용히 None으로 격리한다. 백필 실패는
"데이터 부족"으로 취급되고 봇은 스냅샷 축적으로 자연 회복한다.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple
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
    MAX_BATCH_MARKETS = 20

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenLime-PolyBot/1.0",
        })
        self._prefetched: Dict[
            Tuple[str, int, int, int], Optional[List[Tuple[datetime, float]]]
        ] = {}

    @staticmethod
    def _parse_points(history) -> Optional[List[Tuple[datetime, float]]]:
        """Parse one token independently from the other batch members."""
        if not isinstance(history, list):
            return None
        points: List[Tuple[datetime, float]] = []
        try:
            for candle in history:
                t = candle.get("t")
                p = candle.get("p")
                if t is None or p is None:
                    continue
                ts = datetime.fromtimestamp(
                    int(t), tz=timezone.utc
                ).replace(tzinfo=None)
                points.append((ts, float(p)))
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None
        return points or None

    def prefetch_price_histories(
        self,
        token_ids: Iterable[str],
        start: datetime,
        end: datetime,
        fidelity: int = 10,
    ) -> Dict[str, Optional[List[Tuple[datetime, float]]]]:
        """Batch-fetch invalid local windows, at most 20 tokens per POST."""
        start_ts = to_unix_utc(start)
        end_ts = to_unix_utc(end)
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
                    "prices-history 배치 백필 실패 (무시) - token %d개: %s",
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
        fidelity: int = 10,
    ) -> Optional[List[Tuple[datetime, float]]]:
        """토큰 가격 이력 조회.

        Args:
            token_id: CLOB token id (clobTokenIds의 값)
            start: 시작 시각 (UTC naive)
            end: 종료 시각 (UTC naive)
            fidelity: 캔들 간격 (분 단위, 기본 10)

        Returns:
            (UTC naive datetime, price) 튜플 리스트. 실패/빈 응답 시 None.
        """
        cache_key = (
            str(token_id),
            to_unix_utc(start),
            to_unix_utc(end),
            int(fidelity),
        )
        if cache_key in self._prefetched:
            return self._prefetched[cache_key]

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
            history = response.json().get("history") or []
            return self._parse_points(history)
        except Exception as e:
            # 백필은 best-effort - 실패는 "데이터 부족"으로 취급 (§3.6)
            logger.debug(f"prices-history 백필 실패 (무시) - token: {token_id}: {e}")
            return None
