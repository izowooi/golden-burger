"""Market scanner for finding trading opportunities."""
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Dict, Optional
from ..api.gamma_client import GammaClient
from ..config import GameStartConfig, TradingConfig
from ..db.repository import TradeRepository
from .filters import (
    is_sports_market,
    passes_liquidity_filter,
    get_high_probability_outcome,
    is_valid_buy_candidate,
)

logger = logging.getLogger(__name__)

_NUMERIC_REASON_PART = re.compile(r"^[+-]?\d[\d.]*[a-z%]*$")


def _reason_key(reason: str) -> str:
    """제외 사유의 수치 접미사를 떼고 집계 키로 정규화.

    예: too_early_350.3h → too_early, too_late_2.1h → too_late
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


def parse_market_datetime(value) -> Optional[datetime]:
    """Parse a Gamma market datetime into an aware UTC datetime.

    Args:
        value: ISO date/time string or datetime object

    Returns:
        datetime object or None if parsing fails
    """
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        value = value.strip()
        # Handle both formats: "2025-12-31T12:00:00Z" and "2025-12-31"
        if "T" not in value and " " not in value:
            value += "T00:00:00+00:00"
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def parse_end_date(end_date_str: Optional[str]) -> Optional[datetime]:
    """Backward-compatible endDate parser."""
    return parse_market_datetime(end_date_str)


@dataclass(frozen=True)
class GameStartEvaluation:
    """Normalized sports timing evidence from one Gamma market."""

    is_sports_timed: bool
    valid: bool
    reason: str
    game_start_time: Optional[datetime]
    minutes_until_game_start: Optional[float]
    sports_market_type: Optional[str]
    phase: str


def evaluate_game_start(
    market: Dict,
    config: GameStartConfig,
    *,
    now: Optional[datetime] = None,
) -> GameStartEvaluation:
    """Validate a sports market against its actual ``gameStartTime``.

    Gamma ``endDate`` can be a settlement/catalog deadline several days after
    a game. Before kickoff, ``gameStartTime`` controls the 120-hour admission
    window. After kickoff, an otherwise tradeable market remains eligible as
    ``in_play`` when explicitly enabled. A sports market type without a usable
    start time is rejected by default instead of guessing from endDate.
    """
    raw_game_start = market.get("gameStartTime")
    raw_sports_type = market.get("sportsMarketType")
    sports_market_type = (
        str(raw_sports_type).strip() if raw_sports_type is not None else None
    ) or None
    is_sports_timed = bool(raw_game_start or sports_market_type)

    if not config.enabled:
        return GameStartEvaluation(
            is_sports_timed=is_sports_timed,
            valid=True,
            reason="game_start_filter_disabled",
            game_start_time=parse_market_datetime(raw_game_start),
            minutes_until_game_start=None,
            sports_market_type=sports_market_type,
            phase="filter_disabled",
        )

    if not is_sports_timed:
        return GameStartEvaluation(
            is_sports_timed=False,
            valid=True,
            reason="not_sports_timed",
            game_start_time=None,
            minutes_until_game_start=None,
            sports_market_type=None,
            phase="not_sports",
        )

    if not raw_game_start:
        valid = not config.reject_sports_without_game_start
        return GameStartEvaluation(
            is_sports_timed=True,
            valid=valid,
            reason=(
                "sports_missing_game_start"
                if not valid
                else "sports_game_start_unavailable_allowed"
            ),
            game_start_time=None,
            minutes_until_game_start=None,
            sports_market_type=sports_market_type,
            phase="unknown",
        )

    game_start_time = parse_market_datetime(raw_game_start)
    if game_start_time is None:
        return GameStartEvaluation(
            is_sports_timed=True,
            valid=False,
            reason="invalid_game_start_time",
            game_start_time=None,
            minutes_until_game_start=None,
            sports_market_type=sports_market_type,
            phase="unknown",
        )

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    minutes_left = (
        game_start_time - current.astimezone(timezone.utc)
    ).total_seconds() / 60
    in_play = minutes_left <= 0
    valid = not in_play or config.allow_in_play
    if in_play:
        reason = (
            f"game_in_play_{abs(minutes_left):.1f}m"
            if valid
            else f"game_in_play_disabled_{abs(minutes_left):.1f}m"
        )
        phase = "in_play"
    else:
        reason = f"game_pregame_{minutes_left:.1f}m"
        phase = "pregame"
    return GameStartEvaluation(
        is_sports_timed=True,
        valid=valid,
        reason=reason,
        game_start_time=game_start_time,
        minutes_until_game_start=minutes_left,
        sports_market_type=sports_market_type,
        phase=phase,
    )


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


def is_valid_time_entry(
    end_date: Optional[datetime],
    entry_hours_max: int,
    entry_hours_min: int,
    exit_hours: int = 0,
) -> tuple[bool, str, Optional[float]]:
    """Check if market is within valid time window for entry.

    Args:
        end_date: Market resolution datetime
        entry_hours_max: Maximum hours until resolution (inclusive)
        entry_hours_min: Minimum hours until resolution (inclusive; 0 allowed)
        exit_hours: Active time-exit window. New entries inside it are rejected.

    Returns:
        Tuple of (is_valid, reason, hours_left)
    """
    hours_left = get_hours_until_resolution(end_date)

    if hours_left is None:
        return False, "no_end_date", None

    if hours_left <= 0:
        return False, "already_resolved", hours_left

    if hours_left > entry_hours_max:
        return False, f"too_early_{hours_left:.1f}h", hours_left

    if hours_left < entry_hours_min:
        return False, f"too_late_{hours_left:.1f}h", hours_left

    if exit_hours > 0 and hours_left <= exit_hours:
        return False, f"inside_exit_window_{hours_left:.1f}h", hours_left

    return True, f"time_based_{hours_left:.1f}h", hours_left


def format_entry_window(entry_hours_min: int, entry_hours_max: int) -> str:
    """Render the actual lower-bound semantics used by ``is_valid_time_entry``."""
    if entry_hours_min == 0:
        return f"0h < 남은시간 <= {entry_hours_max}h"
    return f"{entry_hours_min}h <= 남은시간 <= {entry_hours_max}h"


class MarketScanner:
    """Scans markets for buy candidates based on time-to-resolution strategy."""

    def __init__(
        self,
        gamma_client: GammaClient,
        config: TradingConfig,
        repo: Optional[TradeRepository] = None
    ):
        """Initialize scanner.

        Args:
            gamma_client: Gamma API client
            config: Trading configuration
            repo: Trade repository (optional)
        """
        self.gamma = gamma_client
        self.config = config
        self.repo = repo

    def _log_scan_summary(self, analysis: List[Dict]):
        """스캔 분석 요약 출력.

        Args:
            analysis: 분석 결과 리스트
        """
        if not analysis:
            logger.info("진입 대상 시장 없음")
            return

        logger.info("=" * 70)
        logger.info("시간 기반 스캔 요약 (Resolution Momentum Strategy)")
        logger.info("=" * 70)

        time_cfg = self.config.time_based
        logger.info(
            f"설정: 진입 기준시각 조건 {format_entry_window(time_cfg.entry_hours_min, time_cfg.entry_hours_max)}, "
            f"확률 {self.config.buy_threshold:.0%} ~ {self.config.sell_threshold:.0%}"
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
                f"진입 기준시각까지: {hours_str} | 사유: {item['reason']}"
            )
            logger.info(f"       {item['question']}...")

        logger.info("-" * 70)
        logger.info(f"요약: 총 {len(analysis)}개 시장 중 {entry_count}개 진입 가능")
        logger.info("=" * 70)

    def scan_buy_candidates(self) -> List[Dict]:
        """Scan for markets meeting buy criteria.

        Criteria (Resolution Momentum Strategy):
        1. Not in configured excluded categories (empty means no category exclusion)
        2. Liquidity >= min_liquidity
        3. Probability: buy_threshold <= prob <= sell_threshold (75-92%)
        4. Time: sports use gameStartTime; other markets use endDate

        Returns:
            List of candidate dictionaries with market info
        """
        # Scale liquidity with order size. At the default 0.2% ceiling a
        # $100 order requires at least $50,000 reported liquidity.
        effective_min_liquidity = self.config.effective_min_liquidity
        markets = self.gamma.get_all_tradable_markets(
            min_liquidity=effective_min_liquidity
        )
        logger.info(
            "시장 %d개 스캔 시작 (유동성 기준 $%s)",
            len(markets),
            f"{effective_min_liquidity:,.0f}",
        )

        candidates = []
        scan_analysis = []  # 분석 결과 저장
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

            # Filter: Liquidity (double check)
            if not passes_liquidity_filter(market, effective_min_liquidity):
                rejected["low_liquidity"] = rejected.get("low_liquidity", 0) + 1
                continue

            # Get high probability outcome
            outcome_info = get_high_probability_outcome(market, yes_only=self.config.yes_only_mode)
            if not outcome_info or not outcome_info.get("token_id"):
                rejected["no_price_data"] = rejected.get("no_price_data", 0) + 1
                continue

            probability = outcome_info["probability"]

            # Filter: Probability in valid buy range (75% <= prob <= 92%)
            if not is_valid_buy_candidate(
                probability,
                self.config.buy_threshold,
                self.config.sell_threshold,
            ):
                rejected["prob_out_of_range"] = rejected.get("prob_out_of_range", 0) + 1
                continue

            # Before kickoff, sports use gameStartTime for the 120-hour entry
            # window. Once the game starts, the Gamma universe's active/closed/
            # order-book/acceptingOrders contract controls availability and the
            # market remains eligible as in_play. Non-sports keep using endDate.
            entry_signal = True
            entry_reason = "probability_only"
            entry_hours_left = None
            end_date = parse_end_date(market.get("endDate"))
            resolution_hours_left = get_hours_until_resolution(end_date)
            game_start = evaluate_game_start(market, self.config.game_start)
            entry_time_reference = "end_date"
            entry_deadline = end_date

            if (
                self.config.game_start.enabled
                and game_start.game_start_time is not None
            ):
                entry_time_reference = "game_start_time"
                entry_deadline = game_start.game_start_time
            if not game_start.valid:
                entry_signal = False
                entry_reason = game_start.reason

            if entry_signal and game_start.phase == "in_play":
                entry_hours_left = (
                    game_start.minutes_until_game_start / 60
                    if game_start.minutes_until_game_start is not None
                    else None
                )
                entry_reason = game_start.reason
            elif entry_signal and self.config.time_based.enabled:
                entry_signal, time_reason, entry_hours_left = is_valid_time_entry(
                    entry_deadline,
                    self.config.time_based.entry_hours_max,
                    self.config.time_based.entry_hours_min,
                    (
                        0
                        if entry_time_reference == "game_start_time"
                        else self.config.time_based.exit_hours
                    ),
                )
                if entry_signal:
                    entry_reason = (
                        f"game_start_{entry_hours_left:.1f}h"
                        if entry_time_reference == "game_start_time"
                        else time_reason
                    )
                else:
                    entry_reason = time_reason
            elif entry_deadline is not None:
                entry_hours_left = get_hours_until_resolution(entry_deadline)

            # 분석 결과 저장 (진입 여부와 관계없이)
            scan_analysis.append({
                "question": market.get("question", "")[:50],
                "outcome": outcome_info["outcome"],
                "probability": probability,
                "hours_left": entry_hours_left,
                "entry_signal": entry_signal,
                "reason": entry_reason,
            })

            if not entry_signal:
                key = _reason_key(entry_reason)
                rejected[key] = rejected.get(key, 0) + 1
                logger.debug(
                    f"시간 조건 미충족: {condition_id[:20]}... ({entry_reason})"
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
                "entry_reason": entry_reason,
                "end_date": end_date,
                "hours_until_resolution": resolution_hours_left,
                "entry_time_reference": entry_time_reference,
                "hours_until_entry_deadline": entry_hours_left,
                "game_start_time": game_start.game_start_time,
                "minutes_until_game_start": game_start.minutes_until_game_start,
                "sports_market_type": game_start.sports_market_type,
                "is_sports_timed": game_start.is_sports_timed,
                "sports_phase": game_start.phase,
                "market_tags": market_tags,
            }
            candidates.append(candidate)
            entry_hours_text = (
                f"{entry_hours_left:.1f}h"
                if entry_hours_left is not None
                else "N/A"
            )
            logger.debug(
                f"매수 후보: {candidate['question'][:50]}... "
                f"({candidate['outcome']} @ {probability:.1%}, "
                f"진입 기준시각까지 {entry_hours_text}, 사유: {entry_reason})"
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
