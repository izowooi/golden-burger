"""Market scanner for finding panic fade opportunities."""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from ..api.gamma_client import GammaClient
from ..api.history_client import HistoryClient
from ..config import TradingConfig
from ..db.repository import TradeRepository
from .filters import (
    is_sports_market,
    passes_liquidity_filter,
    passes_volume_filter,
    get_yes_price,
)
from .signals import (
    PricePoint,
    PanicFadeParams,
    evaluate_panic_fade,
    get_window,
    is_window_valid,
    merge_price_series,
)

logger = logging.getLogger(__name__)

_NUMERIC_REASON_PART = re.compile(r"^[+-]?\d[\d.]*[a-z%]*$")


def _reason_key(reason: str) -> str:
    """제외 사유의 수치 접미사를 떼고 집계 키로 정규화.

    예: drop_too_small_-0.05 → drop_too_small, ref_below_min_0.62 → ref_below_min
    """
    parts = [p for p in reason.split("_") if p and not _NUMERIC_REASON_PART.match(p)]
    return "_".join(parts) or reason


def _log_reject_summary(rejected: Dict[str, int]) -> None:
    """제외 사유별 집계를 개수 내림차순 한 줄로 출력."""
    if not rejected:
        return
    summary = ", ".join(
        f"{k}: {v}"
        for k, v in sorted(rejected.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    logger.info(f"제외 사유 요약 - {summary}")


def parse_end_date(end_date_str: Optional[str]) -> Optional[datetime]:
    """Parse endDate string from Gamma API to datetime.

    Args:
        end_date_str: ISO format date string (e.g., "2025-12-31T12:00:00Z")

    Returns:
        datetime object or None if parsing fails
    """
    if not end_date_str:
        return None
    try:
        # Handle both formats: "2025-12-31T12:00:00Z" and "2025-12-31"
        if "T" in end_date_str:
            return datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        else:
            return datetime.fromisoformat(end_date_str + "T00:00:00+00:00")
    except (ValueError, TypeError):
        return None


def get_hours_until_resolution(end_date: Optional[datetime]) -> Optional[float]:
    """Calculate hours until market resolution.

    Args:
        end_date: Market end datetime

    Returns:
        Hours until resolution or None if end_date is None
    """
    if not end_date:
        return None
    now = datetime.now(timezone.utc)
    # DB에서 가져온 datetime이 timezone-naive일 수 있음 -> UTC로 처리
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    delta = end_date - now
    return delta.total_seconds() / 3600


class MarketScanner:
    """Scans markets for panic fade candidates.

    Gamma 전체 sweep은 bot.py가 1회만 수행하고 Phase 0(스냅샷 저장)과
    Phase 2(스캔)가 markets 리스트를 공유한다 (banana의 2회 sweep 낭비 수정).
    """

    def __init__(
        self,
        gamma_client: GammaClient,
        config: TradingConfig,
        repo: TradeRepository,
        history_client: Optional[HistoryClient] = None,
    ):
        """Initialize scanner.

        Args:
            gamma_client: Gamma API client
            config: Trading configuration
            repo: Trade repository (snapshot 조회용)
            history_client: prices-history 백필 클라이언트 (optional)
        """
        self.gamma = gamma_client
        self.config = config
        self.repo = repo
        self.history = history_client

    def fetch_markets(self) -> List[Dict]:
        """유동성 필터를 통과한 활성 시장 전체 조회 (사이클당 1회)."""
        return self.gamma.get_all_tradable_markets(
            min_liquidity=self.config.min_liquidity
        )

    def save_market_snapshots(self, markets: List[Dict]) -> int:
        """Phase 0: 스캔 대상 시장 스냅샷 저장 (YES 가격 기준).

        liquidity 필터 통과 시장만 저장한다.

        Args:
            markets: fetch_markets()가 반환한 시장 리스트

        Returns:
            저장된 스냅샷 수
        """
        saved = 0
        for market in markets:
            condition_id = market.get("conditionId")
            if not condition_id:
                continue

            if is_sports_market(market, self.config.excluded_categories):
                continue

            yes_price = get_yes_price(market)
            if yes_price is None:
                continue

            self.repo.save_snapshot(
                condition_id=condition_id,
                probability=yes_price,
                liquidity=float(market.get("liquidity") or 0),
                volume_24h=float(market.get("volume24hr") or 0),
            )
            saved += 1

        logger.info(f"스냅샷 {saved}개 저장 완료 (YES 가격 기준)")
        return saved

    def _signal_params(self) -> PanicFadeParams:
        """config -> 순수 함수 파라미터 변환."""
        s = self.config.strategy
        return PanicFadeParams(
            ref_window_hours=s.ref_window_hours,
            ref_exclude_recent_hours=s.ref_exclude_recent_hours,
            ref_min=s.ref_min,
            drop_min=s.drop_min,
            current_min=s.current_min,
            current_max=s.current_max,
            stab_window_minutes=s.stab_window_minutes,
            stab_max_std=s.stab_max_std,
        )

    def _build_price_series(
        self,
        condition_id: str,
        yes_token_id: Optional[str],
        now: datetime,
    ) -> List[PricePoint]:
        """DB 스냅샷 + (필요 시) 히스토리 백필로 YES 가격 시계열 구성.

        윈도우가 invalid면 백필을 시도하고, 그래도 invalid면 그대로 반환한다
        (evaluate_panic_fade가 window_invalid로 진입을 막는다).
        """
        hours_back = self.config.strategy.ref_window_hours
        since = now - timedelta(hours=hours_back)

        db_points = [
            PricePoint(s.timestamp, s.probability)
            for s in self.repo.get_snapshots_since(condition_id, since)
        ]

        window = get_window(db_points, hours_back, now)
        if is_window_valid(window, hours_back):
            return db_points

        # cold start: 히스토리 백필 시도 (실패는 조용히 무시)
        if self.config.history_backfill and self.history and yes_token_id:
            raw = self.history.get_price_history(
                yes_token_id, start=since, end=now
            )
            if raw:
                backfill = [PricePoint(ts, price) for ts, price in raw]
                merged = merge_price_series(db_points, backfill)
                logger.debug(
                    f"히스토리 백필 병합: {condition_id[:20]}... "
                    f"(DB {len(db_points)} + 백필 {len(backfill)} -> {len(merged)})"
                )
                return merged

        return db_points

    def scan_buy_candidates(self, markets: List[Dict]) -> List[Dict]:
        """Phase 2: Panic Fade 매수 후보 스캔.

        진입 조건 (모두 충족):
        1. liquidity >= min_liquidity, volume24hr >= min_volume_24h,
           hours_left >= entry_hours_min
        2. 기준가 ref = 최근 48h 윈도우(최근 3h 제외)의 최고가; ref >= 0.70
        3. 낙폭: ref - p >= 0.12
        4. 붕괴 배제: 0.35 <= p <= 0.75
        5. 바닥 안정화: 최근 45분(>=3 스냅샷) std <= 0.02,
           현재가 >= 직전 스냅샷들(최신 제외) min (신저가 금지)
        6. 윈도우 유효성 통과

        Args:
            markets: fetch_markets()가 반환한 시장 리스트

        Returns:
            List of candidate dictionaries with market info
        """
        logger.info(f"시장 {len(markets)}개 스캔 시작 (Panic Fade)")
        now = datetime.utcnow()
        params = self._signal_params()

        candidates = []
        rejected = {}  # 사유 키 -> 개수 (요약 로그용)

        for market in markets:
            condition_id = market.get("conditionId")
            if not condition_id:
                continue

            # Filter: Excluded categories (sports)
            if is_sports_market(market, self.config.excluded_categories):
                logger.debug(f"스포츠 시장 제외: {condition_id}")
                rejected["excluded_category"] = rejected.get("excluded_category", 0) + 1
                continue

            # Filter: Liquidity + 24h volume
            if not passes_liquidity_filter(market, self.config.min_liquidity):
                rejected["low_liquidity"] = rejected.get("low_liquidity", 0) + 1
                continue
            if not passes_volume_filter(market, self.config.min_volume_24h):
                rejected["low_volume"] = rejected.get("low_volume", 0) + 1
                continue

            # Filter: 해결까지 충분히 먼 시장만 (마감 직전 급락 = 진짜 정보와 분리)
            end_date = parse_end_date(market.get("endDate"))
            hours_left = get_hours_until_resolution(end_date)
            if hours_left is None or hours_left < self.config.time_based.entry_hours_min:
                rejected["too_close_to_resolution"] = rejected.get("too_close_to_resolution", 0) + 1
                continue

            yes_price = get_yes_price(market)
            if yes_price is None:
                rejected["no_price_data"] = rejected.get("no_price_data", 0) + 1
                continue

            token_ids = market.get("clobTokenIds", [])
            yes_token_id = token_ids[0] if token_ids else None

            # 스냅샷 시계열 구성 (YES 기준) 후 순수 함수 시그널 판정
            series = self._build_price_series(condition_id, yes_token_id, now)
            signal = evaluate_panic_fade(series, yes_price, params, now)

            if not signal.entry:
                key = _reason_key(signal.reason)
                rejected[key] = rejected.get(key, 0) + 1
                logger.debug(
                    f"진입 조건 미충족: {condition_id[:20]}... ({signal.reason})"
                )
                continue

            # 매수 토큰 결정 (side는 signal이 판정)
            token_id = None
            if token_ids and signal.token_index is not None and len(token_ids) > signal.token_index:
                token_id = token_ids[signal.token_index]
            if not token_id:
                rejected["no_price_data"] = rejected.get("no_price_data", 0) + 1
                continue

            outcomes = market.get("outcomes", ["Yes", "No"])
            outcome = (
                outcomes[signal.token_index]
                if len(outcomes) > signal.token_index
                else signal.side
            )

            tags = market.get("tags") or []
            market_tags = ", ".join(
                t.get("label") or t.get("slug", "")
                for t in tags if isinstance(t, dict)
            )

            candidate = {
                "condition_id": condition_id,
                "market_slug": market.get("slug", ""),
                "question": market.get("question", ""),
                "outcome": outcome,
                "probability": signal.current_price,
                "token_id": token_id,
                "liquidity": float(market.get("liquidity") or 0),
                "volume_24h": float(market.get("volume24hr") or 0),
                "entry_reason": signal.reason,
                "end_date": end_date,
                "hours_until_resolution": hours_left,
                "market_tags": market_tags,
                "ref_price": signal.ref_price,
                "drop": signal.drop,
                "stab_range": signal.stab_range,
            }
            candidates.append(candidate)
            logger.info(
                f"매수 후보: {candidate['question'][:50]}... "
                f"({outcome} @ {signal.current_price:.1%}, "
                f"ref {signal.ref_price:.1%}, 낙폭 {signal.drop:.1%}, "
                f"해결까지 {hours_left:.1f}h)"
            )

        _log_reject_summary(rejected)

        logger.info(f"매수 후보 {len(candidates)}개 발견")
        return candidates
