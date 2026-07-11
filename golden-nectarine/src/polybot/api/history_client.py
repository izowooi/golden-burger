"""CLOB prices-history 백필 클라이언트 (cold start 해결).

Polymarket의 public `GET /prices-history`와 `POST /batch-prices-history`를 사용해
분 단위 캔들 이력을 반환한다. **모든 예외는 조용히 None으로 격리**한다 -
백필 실패는 "데이터 부족"으로 취급되고 봇은 스냅샷 축적으로 자연 회복한다.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple
import requests

from ..utils.retry import rate_limit_handler

logger = logging.getLogger(__name__)

# /prices-history는 startTs~endTs 범위가 약 15일(360h)을 넘으면 HTTP 400을
# 반환한다 (2026-07-06 실측: 360h OK, 368h부터 400). 20일 룩백은 단일 호출이
# 불가능하므로 이 상한 이하 조각으로 나눠 요청한 뒤 병합한다.
MAX_RANGE_HOURS = 336  # 14일 - 실측 상한(~15일)에 여유


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
            "User-Agent": "GoldenNectarine-PolyBot/1.0",
        })
        self._prefetched: Dict[
            Tuple[str, int, int, int], Optional[List[Tuple[datetime, float]]]
        ] = {}

    @staticmethod
    def _parse_points(history) -> Optional[List[Tuple[datetime, float]]]:
        """Parse one token's candles without affecting other batch members."""
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
        start: datetime,
        end: datetime,
        fidelity: int = 10,
    ) -> Dict[str, Optional[List[Tuple[datetime, float]]]]:
        """Batch-fetch the long lookback while preserving chunk semantics.

        The official endpoint accepts at most 20 market asset ids. The time
        range is still split at ``MAX_RANGE_HOURS`` so the batch path has the
        same partial-chunk best-effort behavior as the legacy GET path.
        """
        unique_tokens = list(dict.fromkeys(str(token) for token in token_ids if token))
        overall_key = (to_unix_utc(start), to_unix_utc(end), int(fidelity))
        self._prefetched.clear()
        merged: Dict[str, Dict[datetime, float]] = {
            token: {} for token in unique_tokens
        }
        batch_requests = 0

        step = timedelta(hours=MAX_RANGE_HOURS)
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + step, end)
            for offset in range(0, len(unique_tokens), self.MAX_BATCH_MARKETS):
                batch = unique_tokens[offset : offset + self.MAX_BATCH_MARKETS]
                batch_requests += 1
                try:
                    response = self._post_batch(
                        {
                            "markets": batch,
                            "start_ts": to_unix_utc(chunk_start),
                            "end_ts": to_unix_utc(chunk_end),
                            "fidelity": int(fidelity),
                        }
                    )
                    history_map = response.json().get("history")
                    if not isinstance(history_map, dict):
                        raise ValueError("batch history response가 object가 아닙니다")
                    for token in batch:
                        for ts, price in self._parse_points(
                            history_map.get(token) or []
                        ) or []:
                            merged[token][ts] = price
                except Exception as exc:
                    logger.debug(
                        "prices-history 배치 백필 실패 (무시) - token %d개: %s",
                        len(batch),
                        exc,
                    )
            chunk_start = chunk_end

        for token in unique_tokens:
            points = sorted(merged[token].items()) or None
            self._prefetched[(token, *overall_key)] = points

        if unique_tokens:
            logger.info(
                "히스토리 배치 백필 - token %d개, 요청 %d회",
                len(unique_tokens),
                batch_requests,
            )
        return {
            token: self._prefetched[(token, *overall_key)]
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

        범위가 MAX_RANGE_HOURS를 넘으면 API 400을 피하기 위해 조각으로 나눠
        요청하고 timestamp 기준으로 병합한다 (조각 일부 실패는 무시 - best-effort).

        Args:
            token_id: CLOB token id (clobTokenIds의 값)
            start: 시작 시각 (UTC naive)
            end: 종료 시각 (UTC naive)
            fidelity: 캔들 간격 (분 단위, 기본 10)

        Returns:
            시간 오름차순 (UTC naive datetime, price) 튜플 리스트. 실패/빈 응답 시 None.
        """
        cache_key = (
            str(token_id),
            to_unix_utc(start),
            to_unix_utc(end),
            int(fidelity),
        )
        if cache_key in self._prefetched:
            return self._prefetched[cache_key]

        step = timedelta(hours=MAX_RANGE_HOURS)
        merged = {}
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + step, end)
            points = self._fetch_range(token_id, chunk_start, chunk_end, fidelity)
            for ts, price in points or []:
                merged[ts] = price
            chunk_start = chunk_end

        if not merged:
            return None
        return sorted(merged.items())

    def _fetch_range(
        self,
        token_id: str,
        start: datetime,
        end: datetime,
        fidelity: int,
    ) -> Optional[List[Tuple[datetime, float]]]:
        """단일 startTs~endTs 조각 조회 (MAX_RANGE_HOURS 이하 전제)."""
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
