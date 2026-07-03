"""Market scanner for the Shock Follow strategy."""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from ..api.gamma_client import GammaClient
from ..api.history_client import HistoryClient
from ..config import TradingConfig
from ..db.repository import TradeRepository
from .filters import is_sports_market, passes_liquidity_filter, passes_volume_filter
from .signals import (
    PricePoint,
    ShockParams,
    evaluate_entry,
    get_window,
    is_window_valid,
    merge_price_points,
    to_price_points,
)

logger = logging.getLogger(__name__)


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


def shock_params_from_config(config: TradingConfig) -> ShockParams:
    """TradingConfig → 순수 함수용 ShockParams 변환."""
    s = config.shock
    return ShockParams(
        jump_window_hours=s.jump_window_hours,
        jump_min=s.jump_min,
        base_min=s.base_min,
        base_max=s.base_max,
        current_max=s.current_max,
        hold_window_minutes=s.hold_window_minutes,
        max_pullback=s.max_pullback,
        vol_mult_min=s.vol_mult_min,
        death_window_hours=s.death_window_hours,
    )


class MarketScanner:
    """Scans markets for Shock Follow buy candidates.

    Gamma 전체 sweep은 bot.py가 1회만 수행하고 그 결과(markets)를
    Phase 0(스냅샷 저장)과 Phase 2(스캔)가 공유한다 (banana의 2회 sweep 낭비 수정).
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
            repo: Trade repository (스냅샷 조회/저장)
            history_client: prices-history 백필 클라이언트 (optional)
        """
        self.gamma = gamma_client
        self.config = config
        self.repo = repo
        self.history = history_client
        self.params = shock_params_from_config(config)

    def save_market_snapshots(self, markets: List[Dict]) -> int:
        """Phase 0: 스캔 대상 시장 스냅샷 저장 (YES 가격 기준).

        Args:
            markets: gamma.get_all_tradable_markets() 결과 (유동성 필터 통과분)

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

            outcome_prices = market.get("outcomePrices") or []
            if len(outcome_prices) < 2:
                continue
            try:
                yes_price = float(outcome_prices[0])
            except (TypeError, ValueError):
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

    def _load_series(
        self,
        condition_id: str,
        yes_token_id: Optional[str],
        now: datetime,
    ) -> List[PricePoint]:
        """스냅샷 시계열 로드 + 필요 시 prices-history 백필 병합.

        점프 윈도우가 invalid하면 백필을 시도한다. 백필도 실패하면 그대로
        invalid 시계열을 반환하고, evaluate_entry가 진입을 거부한다.
        """
        since = now - timedelta(hours=max(self.params.vol_lookback_hours,
                                          self.params.jump_window_hours))
        series = to_price_points(self.repo.get_snapshots_since(condition_id, since))

        window = get_window(series, self.params.jump_window_hours, now)
        if is_window_valid(
            window,
            self.params.jump_window_hours,
            self.params.min_window_points,
            self.params.min_window_coverage,
        ):
            return series

        if not self.config.history_backfill or not self.history or not yes_token_id:
            return series

        # 백필은 YES(index 0) 토큰으로 조회해 스냅샷과 같은 YES 기준을 유지
        backfill = self.history.get_price_history(
            token_id=yes_token_id,
            start=now - timedelta(hours=self.params.vol_lookback_hours),
            end=now,
        )
        if not backfill:
            return series

        backfill_points = [PricePoint(ts, price) for ts, price in backfill]
        merged = merge_price_points(series, backfill_points)
        logger.debug(
            f"히스토리 백필 병합: {condition_id[:20]}... "
            f"(DB {len(series)}개 + 백필 {len(backfill_points)}개 → {len(merged)}개)"
        )
        return merged

    def _log_scan_summary(self, analysis: List[Dict]):
        """스캔 분석 요약 출력.

        Args:
            analysis: 분석 결과 리스트
        """
        if not analysis:
            logger.info("점프 감지 대상 시장 없음")
            return

        logger.info("=" * 70)
        logger.info("Shock Follow 스캔 요약 (충격 뉴스 과소반응 편승)")
        logger.info("=" * 70)
        logger.info(
            f"설정: 점프 >= +{self.params.jump_min:.2f} ({self.params.jump_window_hours:.0f}h 최저가 대비), "
            f"기준가 [{self.params.base_min:.2f}, {self.params.base_max:.2f}], "
            f"현재가 <= {self.params.current_max:.2f}, "
            f"되돌림 <= {self.params.max_pullback:.2f}, 거래량 x{self.params.vol_mult_min:.1f}"
        )
        logger.info("-" * 70)

        entry_count = 0
        for item in analysis:
            status = "[진입]" if item["entry_signal"] else "[제외]"
            if item["entry_signal"]:
                entry_count += 1

            jump_str = f"{item['jump_size']:+.3f}" if item["jump_size"] is not None else "N/A"
            logger.info(
                f"{status} {item['outcome']} @ {item['token_price']:.1%} | "
                f"점프: {jump_str} | 해결까지: {item['hours_left']:.1f}h | 사유: {item['reason']}"
            )
            logger.info(f"       {item['question']}...")

        logger.info("-" * 70)
        logger.info(f"요약: 분석 {len(analysis)}개 시장 중 {entry_count}개 진입 가능")
        logger.info("=" * 70)

    def scan_buy_candidates(self, markets: List[Dict]) -> List[Dict]:
        """Phase 2: Shock Follow 진입 후보 스캔.

        진입 조건 (모두 충족):
        1. liquidity >= min_liquidity, volume24hr >= min_volume_24h, hours_left >= entry_hours_min
        2. 점프: 6h 윈도우 최저가 대비 +0.10 이상, 기준가 [0.15, 0.70], 현재가 <= 0.85
        3. 고점 유지: 최근 60분 고점 대비 되돌림 <= 0.02
        4. 거래량 확인: 현재 volume24hr >= 24h 윈도우 평균 x 2.0
        5. 방향: YES 급등 → YES 매수, YES 급락 → NO 매수. 윈도우 유효성 통과.

        Args:
            markets: gamma.get_all_tradable_markets() 결과 (Phase 0과 공유)

        Returns:
            List of candidate dictionaries with market info
        """
        logger.info(f"시장 {len(markets)}개 스캔 시작")
        now = datetime.utcnow()

        candidates = []
        scan_analysis = []

        for market in markets:
            condition_id = market.get("conditionId")
            if not condition_id:
                continue

            # Filter: Excluded categories (기본 비활성)
            if is_sports_market(market, self.config.excluded_categories):
                logger.debug(f"제외 카테고리 시장 skip: {condition_id}")
                continue

            # Filter: Liquidity / 24h volume
            if not passes_liquidity_filter(market, self.config.min_liquidity):
                continue
            if not passes_volume_filter(market, self.config.min_volume_24h):
                continue

            # Filter: Time-based entry (hours_left >= entry_hours_min)
            end_date = parse_end_date(market.get("endDate"))
            hours_left = get_hours_until_resolution(end_date)
            if hours_left is None or hours_left < self.config.time_based.entry_hours_min:
                continue

            # Parse prices / tokens
            outcome_prices = market.get("outcomePrices") or []
            token_ids = market.get("clobTokenIds") or []
            outcomes = market.get("outcomes") or ["Yes", "No"]
            if len(outcome_prices) < 2 or len(token_ids) < 2:
                continue
            try:
                current_yes = float(outcome_prices[0])
            except (TypeError, ValueError):
                continue
            current_volume = float(market.get("volume24hr") or 0)

            # Load series (+backfill) and evaluate entry signal
            series = self._load_series(condition_id, token_ids[0], now)
            decision = evaluate_entry(
                yes_points=series,
                current_yes_price=current_yes,
                current_volume_24h=current_volume,
                params=self.params,
                now=now,
            )

            # 점프가 감지된 시장만 분석 요약에 기록 (노이즈 억제)
            if decision.jump_size is not None or decision.enter:
                scan_analysis.append({
                    "question": market.get("question", "")[:50],
                    "outcome": outcomes[decision.outcome_index] if decision.outcome_index is not None else "?",
                    "token_price": decision.token_price or current_yes,
                    "jump_size": decision.jump_size,
                    "hours_left": hours_left,
                    "entry_signal": decision.enter,
                    "reason": decision.reason,
                })

            if not decision.enter:
                logger.debug(
                    f"진입 조건 미충족: {condition_id[:20]}... ({decision.reason})"
                )
                continue

            # Valid candidate
            tags = market.get("tags") or []
            market_tags = ", ".join(
                t.get("label") or t.get("slug", "")
                for t in tags if isinstance(t, dict)
            )
            idx = decision.outcome_index
            candidate = {
                "condition_id": condition_id,
                "market_slug": market.get("slug", ""),
                "question": market.get("question", ""),
                "outcome": outcomes[idx] if len(outcomes) > idx else ("Yes" if idx == 0 else "No"),
                "token_index": idx,
                "probability": decision.token_price,
                "token_id": token_ids[idx],
                "liquidity": float(market.get("liquidity") or 0),
                "entry_reason": f"{decision.reason}_{decision.jump_size:.2f}",
                "jump_size": decision.jump_size,
                "base_price": decision.base_price,
                "end_date": end_date,
                "hours_until_resolution": hours_left,
                "market_tags": market_tags,
            }
            candidates.append(candidate)
            logger.debug(
                f"매수 후보: {candidate['question'][:50]}... "
                f"({candidate['outcome']} @ {decision.token_price:.1%}, "
                f"점프 +{decision.jump_size:.3f}, 해결까지 {hours_left:.1f}h)"
            )

        self._log_scan_summary(scan_analysis)

        logger.info(f"매수 후보 {len(candidates)}개 발견")
        return candidates
