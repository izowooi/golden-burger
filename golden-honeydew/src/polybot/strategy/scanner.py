"""Market scanner for the Night Watch strategy."""
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, List, Dict, Optional

from ..api.gamma_client import GammaClient
from ..config import TradingConfig
from ..db.repository import TradeRepository

if TYPE_CHECKING:
    # 런타임 import 시 history_client → strategy → scanner 순환 import가 생기므로
    # 타입 힌트 용도로만 import 한다.
    from ..api.history_client import HistoryClient
from .filters import is_sports_market, passes_liquidity_filter
from .signals import (
    SnapshotPoint,
    NightWatchParams,
    evaluate_entry,
    is_quiet_time,
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


def to_points(snapshots) -> List[SnapshotPoint]:
    """DB MarketSnapshot ORM 객체 → 순수 SnapshotPoint 변환."""
    return [
        SnapshotPoint(
            timestamp=s.timestamp,
            probability=s.probability,
            volume_24h=s.volume_24h,
        )
        for s in snapshots
    ]


class MarketScanner:
    """Scans markets for Night Watch dislocation opportunities."""

    def __init__(
        self,
        gamma_client: GammaClient,
        config: TradingConfig,
        repo: Optional[TradeRepository] = None,
        history_client: Optional["HistoryClient"] = None,
    ):
        """Initialize scanner.

        Args:
            gamma_client: Gamma API client
            config: Trading configuration
            repo: Trade repository (스냅샷 조회/저장용)
            history_client: prices-history 백필 클라이언트 (§3.6)
        """
        self.gamma = gamma_client
        self.config = config
        self.repo = repo
        self.history = history_client

    def _signal_params(self) -> NightWatchParams:
        """config → 순수 함수 파라미터 변환."""
        return NightWatchParams(
            median_lookback_hours=float(self.config.signal.median_lookback_hours),
            dev_min=self.config.signal.dev_min,
            vol_spike_block=self.config.signal.vol_spike_block,
            entry_prob_min=self.config.signal.entry_prob_min,
            entry_prob_max=self.config.signal.entry_prob_max,
        )

    def fetch_markets(self) -> List[Dict]:
        """Gamma 전체 sweep — 사이클당 1회만 호출 (§5: banana의 2회 sweep 낭비 수정).

        Returns:
            유동성 필터 통과한 활성 시장 리스트
        """
        return self.gamma.get_all_tradable_markets(
            min_liquidity=self.config.min_liquidity
        )

    def save_market_snapshots(self, markets: List[Dict]) -> int:
        """스캔 대상 시장 스냅샷 저장 (Phase 0, banana 패턴).

        liquidity 필터 통과 시장만, YES 가격(outcomePrices[0]) 기준 저장.

        Args:
            markets: fetch_markets() 결과

        Returns:
            저장된 스냅샷 수
        """
        if not self.repo:
            logger.warning("Repository가 설정되지 않아 스냅샷 저장 불가")
            return 0

        saved = 0
        for market in markets:
            condition_id = market.get("conditionId")
            if not condition_id:
                continue

            if is_sports_market(market, self.config.excluded_categories):
                continue

            outcome_prices = market.get("outcomePrices", [])
            if not outcome_prices or len(outcome_prices) < 2:
                continue

            try:
                yes_price = float(outcome_prices[0])
            except (ValueError, TypeError):
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

    def _build_window_points(
        self,
        condition_id: str,
        yes_token_id: Optional[str],
        now: datetime,
    ) -> List[SnapshotPoint]:
        """DB 스냅샷 조회 + 윈도우 invalid 시 히스토리 백필 병합 (§3.6).

        백필까지 실패하면 그대로 부족한 윈도우를 반환한다 —
        evaluate_entry가 window_invalid로 진입을 거부한다
        (banana의 관대한 cold-start 폴백 금지).
        """
        lookback = float(self.config.signal.median_lookback_hours)
        since = now - timedelta(hours=lookback)
        points = to_points(self.repo.get_snapshots_since(condition_id, since))

        window = get_window(points, lookback, now)
        if is_window_valid(window, lookback):
            return points

        # 윈도우 부족 → prices-history 백필 시도 (best-effort)
        if self.config.history_backfill and self.history and yes_token_id:
            end_ts = int(now.replace(tzinfo=timezone.utc).timestamp())
            start_ts = end_ts - int(lookback * 3600)
            backfill = self.history.get_price_history(
                yes_token_id, start_ts, end_ts, fidelity=10
            )
            if backfill:
                points = merge_snapshots(points, backfill)
                logger.debug(
                    f"백필 병합 완료 - {condition_id[:20]}...: 총 {len(points)}개 포인트"
                )

        return points

    def _log_scan_summary(self, analysis: List[Dict]):
        """스캔 분석 요약 출력.

        Args:
            analysis: 분석 결과 리스트
        """
        if not analysis:
            logger.info("진입 대상 시장 없음")
            return

        logger.info("=" * 70)
        logger.info("Night Watch 스캔 요약 (한산 시간대 dislocation 복원)")
        logger.info("=" * 70)

        sig = self.config.signal
        logger.info(
            f"설정: |편차| >= {sig.dev_min:.2f} (24h median 대비), "
            f"매수가 {sig.entry_prob_min:.0%} ~ {sig.entry_prob_max:.0%}, "
            f"거래량 급증 차단 x{sig.vol_spike_block}"
        )
        logger.info("-" * 70)

        entry_count = 0
        for item in analysis:
            status = "✓ 진입" if item["entry_signal"] else "✗ 제외"
            if item["entry_signal"]:
                entry_count += 1

            dev_str = f"{item['deviation']:+.3f}" if item["deviation"] is not None else "N/A"
            hours_str = f"{item['hours_left']:.1f}h" if item["hours_left"] is not None else "N/A"

            logger.info(
                f"{status} | {item['outcome']} @ {item['probability']:.1%} | "
                f"편차: {dev_str} | 해결까지: {hours_str} | 사유: {item['reason']}"
            )
            logger.info(f"       {item['question']}...")

        logger.info("-" * 70)
        logger.info(f"요약: 총 {len(analysis)}개 시장 중 {entry_count}개 진입 가능")
        logger.info("=" * 70)

    def scan_buy_candidates(
        self,
        markets: List[Dict],
        now: Optional[datetime] = None,
    ) -> List[Dict]:
        """Scan for markets meeting Night Watch criteria.

        Criteria:
        1. 한산 시간대 (UTC quiet hours 또는 주말) — 사이클당 1회 판정
        2. 유동성/거래량/카테고리 필터 + 해결까지 >= entry_hours_min
        3. |현재 YES 가격 - 24h median| >= dev_min (윈도우 유효성 필수)
        4. 거래량 급증 아님 (진짜 뉴스 배제)
        5. 복원 방향 토큰 가격이 [0.30, 0.90] 구간

        Args:
            markets: fetch_markets() 결과 (Phase 0과 공유)
            now: 기준 시각 (테스트용, 기본 현재 UTC)

        Returns:
            List of candidate dictionaries with market info
        """
        if now is None:
            now = datetime.utcnow()

        # 한산 시간대 게이트: 진입 로직 전체를 사이클 단위로 차단
        quiet_range = (self.config.quiet.start_hour, self.config.quiet.end_hour)
        if not is_quiet_time(now, quiet_range, self.config.quiet.weekends):
            logger.info(
                f"한산 시간대 아님 - 진입 스캔 skip "
                f"(현재 UTC {now.hour:02d}시, quiet: {self.config.quiet.hours_utc}, "
                f"주말 포함: {self.config.quiet.weekends})"
            )
            return []

        logger.info(f"시장 {len(markets)}개 스캔 시작 (한산 시간대 확인됨)")

        params = self._signal_params()
        candidates = []
        scan_analysis = []  # 분석 결과 저장

        for market in markets:
            condition_id = market.get("conditionId")
            if not condition_id:
                continue

            # Filter: Excluded categories
            if is_sports_market(market, self.config.excluded_categories):
                logger.debug(f"제외 카테고리 시장 skip: {condition_id}")
                continue

            # Filter: Liquidity (double check)
            if not passes_liquidity_filter(market, self.config.min_liquidity):
                continue

            # Filter: 24h volume (0이면 비활성)
            volume_24h = float(market.get("volume24hr") or 0)
            if self.config.min_volume_24h > 0 and volume_24h < self.config.min_volume_24h:
                continue

            # Filter: Time - 해결까지 최소 entry_hours_min
            end_date = parse_end_date(market.get("endDate"))
            hours_left = get_hours_until_resolution(end_date)
            if hours_left is None or hours_left < self.config.time_based.entry_hours_min:
                continue

            # YES/NO 가격·토큰 파싱 (outcomePrices[0]=YES, [1]=NO, clobTokenIds 동일 인덱스)
            outcome_prices = market.get("outcomePrices", [])
            token_ids = market.get("clobTokenIds", [])
            outcomes = market.get("outcomes", ["Yes", "No"])
            if (
                not isinstance(outcome_prices, list)
                or not isinstance(token_ids, list)
                or len(outcome_prices) < 2
                or len(token_ids) < 2
            ):
                continue

            try:
                yes_price = float(outcome_prices[0])
                # NO 매수가는 1-p 근사가 아니라 실제 NO 토큰 가격을 쓴다
                no_price = float(outcome_prices[1])
            except (ValueError, TypeError):
                continue

            # 스냅샷 윈도우 구성 (DB + 필요 시 백필)
            points = self._build_window_points(condition_id, token_ids[0], now)

            # 진입 시그널 판정 (순수 함수)
            signal = evaluate_entry(
                yes_price, points, now, params, current_no_price=no_price
            )

            outcome_name = (
                outcomes[signal.side_index]
                if signal.side_index is not None and len(outcomes) > signal.side_index
                else "?"
            )
            buy_price = signal.buy_price if signal.buy_price is not None else yes_price

            # 분석 결과 저장 (진입 여부와 관계없이)
            scan_analysis.append({
                "question": market.get("question", "")[:50],
                "outcome": outcome_name,
                "probability": buy_price,
                "deviation": signal.deviation,
                "hours_left": hours_left,
                "entry_signal": signal.should_enter,
                "reason": signal.reason,
            })

            if not signal.should_enter:
                logger.debug(
                    f"진입 조건 미충족: {condition_id[:20]}... ({signal.reason})"
                )
                continue

            # Valid candidate
            tags = market.get("tags") or []
            market_tags = ", ".join(
                t.get("label") or t.get("slug", "")
                for t in tags if isinstance(t, dict)
            )
            candidate = {
                "condition_id": condition_id,
                "market_slug": market.get("slug", ""),
                "question": market.get("question", ""),
                "outcome": outcome_name,
                "probability": signal.buy_price,
                "token_id": token_ids[signal.side_index],
                "liquidity": float(market.get("liquidity") or 0),
                "volume_24h": volume_24h,
                "entry_reason": signal.reason,
                "deviation": signal.deviation,
                "median": signal.median,
                "end_date": end_date,
                "hours_until_resolution": hours_left,
                "market_tags": market_tags,
            }
            candidates.append(candidate)
            logger.debug(
                f"매수 후보: {candidate['question'][:50]}... "
                f"({candidate['outcome']} @ {signal.buy_price:.1%}, "
                f"편차 {signal.deviation:+.3f}, 해결까지 {hours_left:.1f}h)"
            )

        # 스캔 분석 요약 출력
        self._log_scan_summary(scan_analysis)

        logger.info(f"매수 후보 {len(candidates)}개 발견")
        return candidates

    def check_current_price(self, token_id: str, clob_client) -> float:
        """Get current price for a token.

        Args:
            token_id: Token ID
            clob_client: CLOB client for price queries

        Returns:
            Current midpoint price or 0.0 on error
        """
        try:
            return clob_client.get_midpoint(token_id)
        except Exception as e:
            logger.error(f"가격 조회 실패 - token: {token_id}: {e}")
            return 0.0
