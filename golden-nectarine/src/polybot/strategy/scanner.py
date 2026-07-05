"""Market scanner for finding rolling-min bottom fishing opportunities."""
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
    BottomFisherParams,
    evaluate_bottom_fisher,
    get_window,
    is_window_valid,
    merge_price_series,
)

logger = logging.getLogger(__name__)

# 20일 룩백 백필 캔들 간격 (분). 480h 범위를 시간 단위 캔들로 받는다.
# fidelity=10(기본)은 20일 범위에서 응답이 과도하게 커지므로 60으로 낮춘다.
BACKFILL_FIDELITY_MINUTES = 60

_NUMERIC_REASON_PART = re.compile(r"^[+-]?\d[\d.]*[a-z%]*$")


def _reason_key(reason: str) -> str:
    """제외 사유의 수치 접미사를 떼고 집계 키로 정규화.

    예: above_rolling_min_0.123 → above_rolling_min, base_too_high_-0.05 → base_too_high
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
    """Scans markets for bottom fisher candidates.

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

    def _signal_params(self) -> BottomFisherParams:
        """config -> 순수 함수 파라미터 변환."""
        s = self.config.strategy
        return BottomFisherParams(
            lookback_days=s.lookback_days,
            exclude_recent_hours=s.exclude_recent_hours,
            prob_min=s.prob_min,
            prob_max=s.prob_max,
        )

    def _build_price_series(
        self,
        condition_id: str,
        yes_token_id: Optional[str],
        now: datetime,
    ) -> List[PricePoint]:
        """DB 스냅샷 + (필요 시) 히스토리 백필로 YES 가격 시계열 구성.

        20일 룩백은 스냅샷 축적만으로는 사실상 채울 수 없으므로 백필이
        이 전략의 생명선이다: /prices-history를 startTs/endTs 20일 범위 +
        fidelity=60(시간 캔들)으로 조회한다. 윈도우가 invalid면 백필을
        시도하고, 그래도 invalid면 그대로 반환한다
        (evaluate_bottom_fisher가 window_invalid로 진입을 막는다).
        """
        hours_back = self.config.strategy.lookback_days * 24.0
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
                yes_token_id,
                start=since,
                end=now,
                fidelity=BACKFILL_FIDELITY_MINUTES,
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
        """Phase 2: Bottom Fisher 매수 후보 스캔.

        진입 조건 (모두 충족, YES 토큰 기준):
        1. liquidity >= min_liquidity (+ min_volume_24h가 설정된 경우 통과)
        2. hours_left >= 720 (30일+ 장기 시장만 - 룩백 20일 동안 theta 자연
           감쇠가 신저가를 계속 만드는 것을 차단)
        3. YES 가격 p ∈ [0.03, 0.50]
        4. p <= min(20일 룩백 윈도우, 최근 24h 제외 구간의 최저가)
        5. 룩백 윈도우 유효성 통과 (백필 포함, invalid면 진입 금지)
        6. 재진입 쿨다운 168h는 Phase 3/trader가 판정

        Args:
            markets: fetch_markets()가 반환한 시장 리스트

        Returns:
            List of candidate dictionaries with market info
        """
        logger.info(f"시장 {len(markets)}개 스캔 시작 (Bottom Fisher)")
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

            # Filter: Liquidity + 24h volume (volume 기본 0 = 비활성)
            if not passes_liquidity_filter(market, self.config.min_liquidity):
                rejected["low_liquidity"] = rejected.get("low_liquidity", 0) + 1
                continue
            if not passes_volume_filter(market, self.config.min_volume_24h):
                rejected["low_volume"] = rejected.get("low_volume", 0) + 1
                continue

            # Filter: 30일+ 장기 시장만 (theta 감쇠發 가짜 신저가 차단)
            end_date = parse_end_date(market.get("endDate"))
            hours_left = get_hours_until_resolution(end_date)
            if hours_left is None or hours_left < self.config.time_based.entry_hours_min:
                rejected["too_close_to_resolution"] = rejected.get("too_close_to_resolution", 0) + 1
                continue

            yes_price = get_yes_price(market)
            if yes_price is None:
                rejected["no_price_data"] = rejected.get("no_price_data", 0) + 1
                continue

            # 밴드 사전 필터: 명백히 밴드 밖이면 20일 백필 자체를 생략 (비용 절감)
            if not (params.prob_min <= yes_price <= params.prob_max):
                rejected["price_out_of_band"] = rejected.get("price_out_of_band", 0) + 1
                continue

            token_ids = market.get("clobTokenIds", [])
            yes_token_id = token_ids[0] if token_ids else None
            if not yes_token_id:
                rejected["no_price_data"] = rejected.get("no_price_data", 0) + 1
                continue

            # 스냅샷 시계열 구성 (YES 기준, 20일 백필 포함) 후 순수 함수 판정
            series = self._build_price_series(condition_id, yes_token_id, now)
            signal = evaluate_bottom_fisher(series, yes_price, params, now)

            if not signal.entry:
                key = _reason_key(signal.reason)
                rejected[key] = rejected.get(key, 0) + 1
                logger.debug(
                    f"진입 조건 미충족: {condition_id[:20]}... ({signal.reason})"
                )
                continue

            outcomes = market.get("outcomes", ["Yes", "No"])
            outcome = outcomes[0] if outcomes else "Yes"

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
                "probability": yes_price,
                "token_id": yes_token_id,
                "liquidity": float(market.get("liquidity") or 0),
                "volume_24h": float(market.get("volume24hr") or 0),
                "entry_reason": signal.reason,
                "end_date": end_date,
                "hours_until_resolution": hours_left,
                "market_tags": market_tags,
                "rolling_min": signal.rolling_min,
                "lookback_days_covered": signal.lookback_days_covered,
            }
            candidates.append(candidate)
            logger.info(
                f"매수 후보: {candidate['question'][:50]}... "
                f"(Yes @ {yes_price:.1%}, 20일 최저가 {signal.rolling_min:.1%}, "
                f"커버 {signal.lookback_days_covered:.1f}일, "
                f"해결까지 {hours_left:.1f}h)"
            )

        _log_reject_summary(rejected)

        logger.info(f"매수 후보 {len(candidates)}개 발견")
        return candidates
