"""Market scanner for the Conviction Ladder strategy."""
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
    get_high_probability_outcome,
)
from .signals import (
    Snap,
    get_window,
    is_window_valid,
    merge_snapshots,
    check_ladder_entry,
    evaluate_entry,
)

logger = logging.getLogger(__name__)

_NUMERIC_REASON_PART = re.compile(r"^[+-]?\d[\d.]*[a-z%]*$")


def _reason_key(reason: str) -> str:
    """제외 사유의 수치 접미사를 떼고 집계 키로 정규화.

    예: too_early_350.3h → too_early, momentum_down_-0.023 → momentum_down
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
    """Scans markets for buy candidates (Conviction Ladder).

    Gamma 전체 sweep은 bot.py가 1회만 수행하고,
    Phase 0(스냅샷 저장)과 Phase 2(스캔)가 markets 리스트를 공유한다.
    """

    def __init__(
        self,
        gamma_client: GammaClient,
        config: TradingConfig,
        repo: Optional[TradeRepository] = None,
        history_client: Optional[HistoryClient] = None,
    ):
        """Initialize scanner.

        Args:
            gamma_client: Gamma API client
            config: Trading configuration
            repo: Trade repository (스냅샷 조회/저장용)
            history_client: prices-history 백필 클라이언트 (없으면 백필 생략)
        """
        self.gamma = gamma_client
        self.config = config
        self.repo = repo
        self.history = history_client

    # ------------------------------------------------------------------
    # Phase 0: 스냅샷 저장 (banana 패턴, YES 가격 기준)
    # ------------------------------------------------------------------

    def save_market_snapshots(self, markets: List[Dict]) -> int:
        """스캔 대상 시장의 스냅샷 저장 (모멘텀 게이트 데이터 축적).

        liquidity 필터 통과 시장만, YES 가격 기준으로 저장한다.

        Args:
            markets: bot.py가 sweep한 시장 리스트

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

        logger.info(f"스냅샷 {saved}개 저장 완료 (YES 가격 기준)")
        return saved

    # ------------------------------------------------------------------
    # 모멘텀 윈도우 준비 (DB 스냅샷 + 필요 시 prices-history 백필)
    # ------------------------------------------------------------------

    def _momentum_snapshots(
        self,
        condition_id: str,
        yes_token_id: Optional[str],
        now: datetime,
    ) -> List[Snap]:
        """모멘텀 판정용 스냅샷 리스트 준비.

        DB 윈도우가 invalid면 prices-history 백필을 시도해 병합한다 (§3.6).
        백필까지 실패하면 그대로 반환하고, 최종 판정은 evaluate_entry의
        윈도우 유효성 검사가 한다 (invalid → 진입 금지).
        """
        lookback = self.config.momentum_gate.lookback_hours
        since = now - timedelta(hours=lookback)

        snaps: List[Snap] = []
        if self.repo:
            snaps = [
                Snap(s.timestamp, s.probability)
                for s in self.repo.get_snapshots_since(condition_id, since)
            ]

        window = get_window(snaps, lookback, now)
        if is_window_valid(window, lookback):
            return snaps

        # 윈도우 부족 → 히스토리 백필 시도
        if self.config.history_backfill and self.history and yes_token_id:
            points = self.history.get_recent_history(yes_token_id, lookback, now=now)
            if points:
                backfill = [Snap(ts, price) for ts, price in points]
                snaps = merge_snapshots(snaps, backfill)
                logger.debug(
                    f"히스토리 백필 병합: {condition_id[:20]}... "
                    f"(백필 {len(backfill)}개, 병합 후 {len(snaps)}개)"
                )

        return snaps

    # ------------------------------------------------------------------
    # Phase 2: 매수 후보 스캔
    # ------------------------------------------------------------------

    def _log_scan_summary(self, analysis: List[Dict]):
        """스캔 분석 요약 출력.

        Args:
            analysis: 분석 결과 리스트
        """
        if not analysis:
            logger.info("진입 대상 시장 없음")
            return

        logger.info("=" * 70)
        logger.info("스캔 요약 (Conviction Ladder Strategy)")
        logger.info("=" * 70)

        ladder = self.config.ladder
        gate = self.config.momentum_gate
        logger.info(
            f"설정: 사다리 {ladder.entry_hours_min}h < 해결시간 <= {ladder.h3}h "
            f"(~{ladder.h1}h [{ladder.band1_min:.2f},{ladder.band1_max:.2f}] / "
            f"~{ladder.h2}h [{ladder.band2_min:.2f},{ladder.band2_max:.2f}] / "
            f"~{ladder.h3}h [{ladder.band3_min:.2f},{ladder.band3_max:.2f}]), "
            f"모멘텀 {gate.lookback_hours}h 변화 >= {gate.min_change:+.3f}"
        )
        logger.info("-" * 70)

        entry_count = 0
        for item in analysis:
            status = "✓ 진입" if item["entry_signal"] else "✗ 제외"
            if item["entry_signal"]:
                entry_count += 1

            hours_str = f"{item['hours_left']:.1f}h" if item['hours_left'] is not None else "N/A"

            logger.info(
                f"{status} | {item['outcome']} @ {item['probability']:.1%} | "
                f"해결까지: {hours_str} | 사유: {item['reason']}"
            )
            logger.info(f"       {item['question']}...")

        logger.info("-" * 70)
        logger.info(f"요약: 총 {len(analysis)}개 시장 중 {entry_count}개 진입 가능")
        logger.info("=" * 70)

    def scan_buy_candidates(self, markets: List[Dict]) -> List[Dict]:
        """Scan for markets meeting buy criteria.

        Criteria (Conviction Ladder Strategy):
        1. Not in excluded categories
        2. Liquidity >= min_liquidity AND volume24hr >= min_volume_24h
        3. Favorite side 선택 (YES/NO 중 높은 쪽, --yes-only 지원)
        4. 시간 사다리: 잔여 시간 구간별 확률 밴드
        5. 모멘텀 게이트: 최근 lookback 윈도우 favorite 변화 >= min_change
           (윈도우 invalid면 백필 시도, 그래도 invalid면 진입 금지)

        재진입 쿨다운은 Phase 3(bot)와 trader에서 검사한다.

        Args:
            markets: bot.py가 sweep한 시장 리스트

        Returns:
            List of candidate dictionaries with market info
        """
        logger.info(f"시장 {len(markets)}개 스캔 시작")

        now = datetime.utcnow()
        rungs = self.config.ladder.rungs()
        entry_hours_min = self.config.ladder.entry_hours_min

        candidates = []
        scan_analysis = []  # 분석 결과 저장
        rejected = {}  # 사유 키 -> 개수 (요약 로그용)

        for market in markets:
            condition_id = market.get("conditionId")
            if not condition_id:
                continue

            # Filter: Excluded categories
            if is_sports_market(market, self.config.excluded_categories):
                logger.debug(f"제외 카테고리 시장 skip: {condition_id}")
                rejected["excluded_category"] = rejected.get("excluded_category", 0) + 1
                continue

            # Filter: Liquidity (double check) + 24h volume
            if not passes_liquidity_filter(market, self.config.min_liquidity):
                rejected["low_liquidity"] = rejected.get("low_liquidity", 0) + 1
                continue
            if not passes_volume_filter(market, self.config.min_volume_24h):
                rejected["low_volume"] = rejected.get("low_volume", 0) + 1
                continue

            # Favorite side 선택
            outcome_info = get_high_probability_outcome(market, yes_only=self.config.yes_only_mode)
            if not outcome_info or not outcome_info.get("token_id"):
                rejected["no_price_data"] = rejected.get("no_price_data", 0) + 1
                continue

            probability = outcome_info["probability"]
            end_date = parse_end_date(market.get("endDate"))
            hours_left = get_hours_until_resolution(end_date)

            # 시간 사다리 선검사 (실패 시 스냅샷/백필 조회를 생략해 API 낭비 방지)
            ladder_ok, ladder_reason = check_ladder_entry(
                probability, hours_left, entry_hours_min, rungs
            )
            if not ladder_ok:
                key = _reason_key(ladder_reason)
                rejected[key] = rejected.get(key, 0) + 1
                scan_analysis.append({
                    "question": market.get("question", "")[:50],
                    "outcome": outcome_info["outcome"],
                    "probability": probability,
                    "hours_left": hours_left,
                    "entry_signal": False,
                    "reason": ladder_reason,
                })
                continue

            # 모멘텀 게이트 (윈도우 유효성 포함)
            token_ids = market.get("clobTokenIds") or []
            yes_token_id = token_ids[0] if token_ids else None
            snaps = self._momentum_snapshots(condition_id, yes_token_id, now)

            decision = evaluate_entry(
                price=probability,
                hours_left=hours_left,
                snapshots=snaps,
                favorite_index=outcome_info["token_index"],
                entry_hours_min=entry_hours_min,
                rungs=rungs,
                momentum_lookback_hours=self.config.momentum_gate.lookback_hours,
                momentum_min_change=self.config.momentum_gate.min_change,
                now=now,
            )

            scan_analysis.append({
                "question": market.get("question", "")[:50],
                "outcome": outcome_info["outcome"],
                "probability": probability,
                "hours_left": hours_left,
                "entry_signal": decision.entry,
                "reason": decision.reason,
            })

            if not decision.entry:
                key = _reason_key(decision.reason)
                rejected[key] = rejected.get(key, 0) + 1
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
            candidate = {
                "condition_id": condition_id,
                "market_slug": market.get("slug", ""),
                "question": market.get("question", ""),
                "outcome": outcome_info["outcome"],
                "probability": probability,
                "token_id": outcome_info["token_id"],
                "liquidity": float(market.get("liquidity") or 0),
                "volume_24h": float(market.get("volume24hr") or 0),
                "entry_reason": decision.reason,
                "end_date": end_date,
                "hours_until_resolution": hours_left,
                "momentum_change": decision.momentum_change,
                "ladder_band": decision.ladder_band,
                "market_tags": market_tags,
            }
            candidates.append(candidate)
            logger.debug(
                f"매수 후보: {candidate['question'][:50]}... "
                f"({candidate['outcome']} @ {probability:.1%}, "
                f"해결까지 {hours_left:.1f}h, 사유: {decision.reason})"
            )

        # 스캔 분석 요약 출력
        self._log_scan_summary(scan_analysis)
        _log_reject_summary(rejected)

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
