"""Market scanner for the Cascade Rider strategy.

Gamma 전체 sweep 결과(markets)는 bot.py가 1회만 조회해 Phase 0(스냅샷 저장)과
Phase 2(스캔)에 공유한다 (banana의 2회 sweep 낭비 수정).
"""
import logging
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
)
from .signals import (
    SnapshotPoint,
    evaluate_entry,
    get_window,
    is_window_valid,
    merge_snapshots,
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


class MarketScanner:
    """Scans markets for Cascade Rider buy candidates."""

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

    def save_market_snapshots(self, markets: List[Dict]) -> int:
        """스캔 대상 시장 스냅샷 저장 (Phase 0).

        liquidity 필터 통과 시장만, YES 가격(outcomePrices[0]) 기준으로 저장한다.

        Args:
            markets: bot.py가 조회한 Gamma sweep 결과 (공유)

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

            if not passes_liquidity_filter(market, self.config.min_liquidity):
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

        logger.info(f"스냅샷 {saved}개 저장 완료")
        return saved

    def _load_points_with_backfill(
        self,
        condition_id: str,
        yes_token_id: Optional[str],
        now: datetime,
    ) -> List[SnapshotPoint]:
        """DB 스냅샷 조회 후, 윈도우가 invalid면 히스토리 백필 시도.

        백필도 실패하면 그대로 반환한다 (evaluate_entry가 window_invalid로
        진입을 막는다 - banana의 관대한 cold-start 폴백 금지).
        """
        lookback = self.config.cascade.drift_lookback_hours
        since = now - timedelta(hours=lookback)

        db_snaps = self.repo.get_snapshots_since(condition_id, since)
        points = [
            SnapshotPoint(s.timestamp, s.probability, s.volume_24h)
            for s in db_snaps
        ]

        window = get_window(points, lookback, now)
        if is_window_valid(window, lookback):
            return points

        if not self.config.history_backfill or not self.history or not yes_token_id:
            return points

        # 백필: unix ts 계산은 aware UTC로 (naive utcnow().timestamp()는 로컬 해석됨)
        now_aware = datetime.now(timezone.utc)
        start_ts = int((now_aware - timedelta(hours=lookback)).timestamp())
        end_ts = int(now_aware.timestamp())

        backfill = self.history.get_price_history(yes_token_id, start_ts, end_ts)
        if not backfill:
            return points

        merged = merge_snapshots(points, backfill)
        logger.debug(
            f"히스토리 백필 병합: {condition_id[:20]}... "
            f"(DB {len(points)}개 + 백필 {len(backfill)}개 -> {len(merged)}개)"
        )
        return merged

    def _log_scan_summary(self, analysis: List[Dict]):
        """스캔 분석 요약 출력.

        Args:
            analysis: 분석 결과 리스트
        """
        if not analysis:
            logger.info("진입 대상 시장 없음")
            return

        logger.info("=" * 70)
        logger.info("드리프트 스캔 요약 (Cascade Rider Strategy)")
        logger.info("=" * 70)

        cascade = self.config.cascade
        logger.info(
            f"설정: 가격 {cascade.prob_min:.0%}~{cascade.prob_max:.0%}, "
            f"{cascade.drift_lookback_hours}h 드리프트 {cascade.drift_min:+.0%}~{cascade.drift_max:+.0%}, "
            f"일관성 >= {cascade.consistency_min:.0%}, "
            f"거래량 가속 >= {cascade.vol_accel_min:.1f}x"
        )
        logger.info("-" * 70)

        entry_count = 0
        for item in analysis:
            status = "✓ 진입" if item["entry_signal"] else "✗ 제외"
            if item["entry_signal"]:
                entry_count += 1

            drift_str = f"{item['drift']:+.3f}" if item["drift"] is not None else "N/A"
            price_str = f"{item['token_price']:.1%}" if item["token_price"] is not None else "N/A"

            logger.info(
                f"{status} | {item['side'] or '-'} @ {price_str} | "
                f"드리프트: {drift_str} | 사유: {item['reason']}"
            )
            logger.info(f"       {item['question']}...")

        logger.info("-" * 70)
        logger.info(f"요약: 총 {len(analysis)}개 시장 중 {entry_count}개 진입 가능")
        logger.info("=" * 70)

    def scan_buy_candidates(self, markets: List[Dict]) -> List[Dict]:
        """Scan for markets meeting Cascade Rider criteria (Phase 2).

        Criteria:
        1. liquidity >= min_liquidity, volume24hr >= min_volume_24h
        2. hours_left >= entry_hours_min (48h)
        3. evaluate_entry (드리프트 밴드 + 가격 밴드 + 일관성 + 거래량 가속)

        Args:
            markets: bot.py가 조회한 Gamma sweep 결과 (공유)

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

            # Filter: Liquidity (double check) + 24h volume
            if not passes_liquidity_filter(market, self.config.min_liquidity):
                continue
            if not passes_volume_filter(market, self.config.min_volume_24h):
                continue

            # Filter: Time - 해결까지 최소 시간 (러닝룸 확보)
            end_date = parse_end_date(market.get("endDate"))
            hours_left = get_hours_until_resolution(end_date)
            if hours_left is None or hours_left < self.config.entry_hours_min:
                continue

            # Parse prices/tokens
            outcome_prices = market.get("outcomePrices") or []
            token_ids = market.get("clobTokenIds") or []
            outcomes = market.get("outcomes") or ["Yes", "No"]
            if len(outcome_prices) < 2 or len(token_ids) < 2:
                continue

            try:
                yes_price = float(outcome_prices[0])
            except (TypeError, ValueError):
                continue
            volume_24h = float(market.get("volume24hr") or 0)

            # 스냅샷 로드 (+필요 시 히스토리 백필)
            points = self._load_points_with_backfill(
                condition_id, token_ids[0], now
            )

            cascade = self.config.cascade
            decision = evaluate_entry(
                points,
                yes_price,
                volume_24h,
                prob_min=cascade.prob_min,
                prob_max=cascade.prob_max,
                drift_lookback_hours=cascade.drift_lookback_hours,
                drift_min=cascade.drift_min,
                drift_max=cascade.drift_max,
                bucket_hours=cascade.bucket_hours,
                consistency_min=cascade.consistency_min,
                vol_accel_min=cascade.vol_accel_min,
                now=now,
            )

            # 분석 결과 저장 (진입 여부와 관계없이)
            scan_analysis.append({
                "question": market.get("question", "")[:50],
                "side": decision.side,
                "token_price": decision.token_price,
                "drift": decision.drift,
                "entry_signal": decision.should_enter,
                "reason": decision.reason,
            })

            if not decision.should_enter:
                logger.debug(
                    f"진입 조건 미충족: {condition_id[:20]}... ({decision.reason})"
                )
                continue

            token_index = 0 if decision.side == "Yes" else 1
            tags = market.get("tags") or []
            market_tags = ", ".join(
                t.get("label") or t.get("slug", "")
                for t in tags if isinstance(t, dict)
            )
            candidate = {
                "condition_id": condition_id,
                "market_slug": market.get("slug", ""),
                "question": market.get("question", ""),
                "outcome": outcomes[token_index] if len(outcomes) > token_index else decision.side,
                "token_index": token_index,
                "token_id": token_ids[token_index],
                "token_price": decision.token_price,
                "drift": decision.drift,
                "consistency": decision.consistency,
                "vol_accel": decision.vol_accel,
                "liquidity": float(market.get("liquidity") or 0),
                "volume_24h": volume_24h,
                "entry_reason": decision.reason,
                "end_date": end_date,
                "hours_until_resolution": hours_left,
                "market_tags": market_tags,
            }
            candidates.append(candidate)
            logger.debug(
                f"매수 후보: {candidate['question'][:50]}... "
                f"({candidate['outcome']} @ {decision.token_price:.1%}, "
                f"드리프트 {decision.drift:+.3f}, 일관성 {decision.consistency:.0%}, "
                f"거래량 x{decision.vol_accel:.1f}, 해결까지 {hours_left:.1f}h)"
            )

        # 스캔 분석 요약 출력
        self._log_scan_summary(scan_analysis)

        logger.info(f"매수 후보 {len(candidates)}개 발견")
        return candidates
