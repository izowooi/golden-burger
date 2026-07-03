"""Trading execution logic for the Night Watch strategy."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..db.repository import TradeRepository
from ..db.models import TradeStatus
from ..api.clob_client import ClobClientWrapper
from ..config import TradingConfig
from .scanner import get_hours_until_resolution, to_points
from .signals import ExitParams, compute_median_deviation, evaluate_exit, get_window

logger = logging.getLogger(__name__)

# Polymarket minimum order size requirement
MIN_ORDER_SIZE = 5.0

# §3.4: 해결된 시장 판정 - endDate가 이 시간 이상 지났으면 EXPIRED 처리
RESOLVED_GRACE_HOURS = 24.0

# DB 회고 로깅용 봇 식별 상수 (교차 봇 UNION 쿼리 계약, 부록 스펙 §D)
STRATEGY_NAME = "honeydew"


class Trader:
    """Executes buy and sell orders based on the Night Watch strategy."""

    def __init__(
        self,
        repo: TradeRepository,
        clob_client: ClobClientWrapper,
        config: TradingConfig,
        simulation_mode: bool = False,
    ):
        """Initialize trader.

        Args:
            repo: Trade repository for DB operations
            clob_client: CLOB client for order execution
            config: Trading configuration
            simulation_mode: 회고 로깅용 mode 컬럼 값 결정 ("sim"/"live")
        """
        self.repo = repo
        self.clob = clob_client
        self.config = config
        self.mode = "sim" if simulation_mode else "live"

    def _exit_params(self) -> ExitParams:
        """config → 순수 함수 파라미터 변환."""
        return ExitParams(
            take_profit_percent=self.config.take_profit_percent,
            stop_loss_percent=self.config.stop_loss_percent,
            max_holding_hours=float(self.config.time_based.max_holding_hours),
            exit_hours=float(self.config.time_based.exit_hours),
        )

    def execute_buy(self, candidate: dict) -> Optional[int]:
        """Execute a buy order for a candidate market.

        Args:
            candidate: Market candidate dictionary with:
                - condition_id
                - token_id
                - probability (매수 토큰 기준 가격)
                - outcome ("Yes" or "No")
                - question
                - market_slug
                - liquidity
                - entry_reason
                - deviation / median (YES 기준)
                - end_date (datetime)
                - hours_until_resolution (float)

        Returns:
            Trade ID if successful, None otherwise
        """
        condition_id = candidate["condition_id"]
        token_id = candidate["token_id"]

        # Check: 재진입 쿨다운 (§3.3 - 영구 one-shot 대신 쿨다운)
        can_enter, reason = self.repo.can_reenter(
            condition_id, self.config.reentry_cooldown_hours
        )
        if not can_enter:
            logger.info(f"재진입 불가 skip: {condition_id} (사유: {reason})")
            return None

        # Check: Max positions limit
        if self.config.max_positions > 0:
            current_positions = self.repo.get_position_count()
            if current_positions >= self.config.max_positions:
                logger.info(f"최대 포지션 수 ({self.config.max_positions}) 도달")
                return None

        # Get current price (re-verify before buying)
        try:
            current_price = self.clob.get_midpoint(token_id)
        except Exception as e:
            logger.warning(f"가격 조회 실패 - condition: {condition_id}: {e}")
            return None

        # Check: 스캔~주문 사이 가격이 진입 밴드를 벗어남?
        prob_min = self.config.signal.entry_prob_min
        prob_max = self.config.signal.entry_prob_max
        if current_price > prob_max:
            logger.info(
                f"급등 감지 - 매수 skip: {condition_id} "
                f"(가격: {current_price:.1%} > 상한 {prob_max:.1%})"
            )
            # 쿨다운 동안만 차단 (기존 봇의 영구 밴 아님)
            self.repo.mark_as_skipped(condition_id, "rapid_jump")
            return None

        if current_price < prob_min:
            logger.info(
                f"가격 하락으로 진입 밴드 이탈 - 매수 skip: {condition_id} "
                f"(가격: {current_price:.1%} < 하한 {prob_min:.1%})"
            )
            return None

        # Calculate order size
        buy_shares = self.config.buy_amount_usdc / current_price

        # Check minimum order size
        if buy_shares < MIN_ORDER_SIZE:
            logger.warning(
                f"주문 수량 {buy_shares:.2f}주 < 최소 {MIN_ORDER_SIZE}주 - {condition_id}. "
                f"buy_amount_usdc를 늘리거나 낮은 가격에서 매수하세요."
            )
            return None

        entry_reason = candidate.get("entry_reason", "unknown")
        end_date = candidate.get("end_date")
        hours_until_resolution = candidate.get("hours_until_resolution")
        hours_str = (
            f"{hours_until_resolution:.1f}h"
            if hours_until_resolution is not None else "N/A"
        )

        # Place order
        logger.info(
            f"매수: {candidate['outcome']} - '{candidate['question'][:50]}...' "
            f"@ {current_price:.2%} ({buy_shares:.2f}주, ${self.config.buy_amount_usdc}) "
            f"[사유: {entry_reason}, 해결까지 {hours_str}]"
        )

        result = self.clob.place_limit_order(
            token_id=token_id,
            price=current_price,
            size=buy_shares,
            side="BUY",
        )

        # Check result
        if result.get("success") or result.get("orderID"):
            # Record trade in DB
            trade = self.repo.create_trade(
                condition_id=condition_id,
                market_slug=candidate["market_slug"],
                question=candidate["question"],
                outcome=candidate["outcome"],
                token_id=token_id,
                buy_price=current_price,
                buy_amount=self.config.buy_amount_usdc,
                buy_shares=buy_shares,
                buy_order_id=result.get("orderID"),
                buy_timestamp=datetime.utcnow(),
                buy_probability=current_price,
                liquidity_at_buy=candidate["liquidity"],
                market_tags=candidate.get("market_tags", ""),
                status=TradeStatus.HOLDING,
                # 회고 로깅 공통 3컬럼 (부록 스펙 §D)
                strategy_name=STRATEGY_NAME,
                mode=self.mode,
                volume_24h_at_buy=candidate.get("volume_24h"),
                # Night watch strategy fields
                entry_reason=entry_reason,
                deviation_at_buy=candidate.get("deviation"),
                median_at_buy=candidate.get("median"),
                max_price=current_price,  # Initialize max_price with buy price
                market_end_date=self._to_naive_utc(end_date),
                hours_until_resolution_at_buy=hours_until_resolution,
            )

            logger.info(f"매수 주문 완료: Trade #{trade.id}, Order: {result.get('orderID')}")
            return trade.id
        else:
            logger.error(f"매수 주문 실패: {result}")
            return None

    @staticmethod
    def _to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
        """DB 저장용 naive UTC 변환 (기존 봇들의 utcnow 컨벤션과 통일)."""
        if dt is None:
            return None
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    def _deviation_at_exit(
        self, trade, current_price: float, now: datetime
    ) -> Optional[float]:
        """청산 시점 24h median 대비 편차 계산 (회고 로깅용, best-effort).

        스냅샷은 YES 가격 기준이므로 NO 포지션은 1-p로 환산해 비교한다.
        윈도우가 비었거나 조회에 실패하면 None — 청산 판정에는 영향 없음.
        """
        try:
            lookback = float(self.config.signal.median_lookback_hours)
            since = now - timedelta(hours=lookback)
            points = to_points(
                self.repo.get_snapshots_since(trade.condition_id, since)
            )
            window = get_window(points, lookback, now)
            yes_price = (
                1.0 - current_price if trade.outcome == "No" else current_price
            )
            _, deviation = compute_median_deviation(yes_price, window)
            return deviation
        except Exception as e:
            logger.debug(f"deviation_at_exit 계산 실패 - {trade.condition_id}: {e}")
            return None

    def _handle_midpoint_failure(self, trade) -> bool:
        """midpoint 조회 실패 시 해결된 시장 leak 처리 (§3.4).

        market_end_date가 RESOLVED_GRACE_HOURS 이상 지났으면 EXPIRED로 마감:
        - status=EXPIRED, exit_reason="resolved_unredeemed", realized_pnl=NULL
        - EXPIRED는 get_holding_trades에서 제외 → 좀비 포지션 방지

        Returns:
            EXPIRED 처리했으면 True
        """
        hours_left = get_hours_until_resolution(trade.market_end_date)
        if hours_left is None or hours_left > -RESOLVED_GRACE_HOURS:
            return False

        self.repo.update_trade(
            trade.id,
            status=TradeStatus.EXPIRED,
            exit_reason="resolved_unredeemed",
            realized_pnl=None,
        )
        logger.warning(
            f"해결된 시장 미청산 포지션 EXPIRED 처리: Trade #{trade.id} "
            f"'{trade.question[:50]}...' (endDate {-hours_left:.0f}h 경과) "
            f"- 수동 redeem 필요"
        )
        return True

    def execute_sell(self, trade) -> bool:
        """Execute sell order for a holding position.

        청산 조건 (우선순위 순, signals.evaluate_exit):
        1. 손절: P&L <= -6%
        2. 익절: 목표가 도달 (buy*(1+6%), 0.99 캡)
        3. 최대 보유 시간: 24h 초과 (복원 실패 → 회전)
        4. 시간: 해결 12시간 이내

        Args:
            trade: Trade object from DB

        Returns:
            True if sell executed successfully
        """
        token_id = trade.token_id
        condition_id = trade.condition_id

        # Get current price
        try:
            current_price = self.clob.get_midpoint(token_id)
        except Exception as e:
            logger.warning(f"가격 조회 실패 - condition: {condition_id}: {e}")
            # §3.4: 해결된 시장이면 EXPIRED로 마감 처리 (매도 아님 → False)
            self._handle_midpoint_failure(trade)
            return False

        # midpoint 0은 실제 가격이 아니라 조회 실패다 (tick 범위는 0.01~0.99).
        # 그대로 두면 P&L -100%로 오판 → 가짜 stop_loss로 0.01 매도가 나간다.
        if current_price <= 0:
            logger.warning(
                f"midpoint 0 반환 - 가격 신뢰 불가, 매도 판단 skip: {condition_id}"
            )
            self._handle_midpoint_failure(trade)
            return False

        # Update max_price if current price is higher (분석용 기록)
        max_price = trade.max_price or trade.buy_price
        if current_price > max_price:
            max_price = current_price
            self.repo.update_trade(trade.id, max_price=max_price)
            logger.debug(f"최고가 갱신: {condition_id} -> {max_price:.2%}")

        now = datetime.utcnow()
        should_sell, exit_reason = evaluate_exit(
            buy_price=trade.buy_price,
            current_price=current_price,
            buy_timestamp=trade.buy_timestamp,
            market_end_date=trade.market_end_date,
            now=now,
            params=self._exit_params(),
        )

        pnl_percent = 0.0
        if trade.buy_price and trade.buy_price > 0:
            pnl_percent = (current_price - trade.buy_price) / trade.buy_price

        if not should_sell:
            hours_left = get_hours_until_resolution(trade.market_end_date)
            hours_str = f"{hours_left:.1f}h" if hours_left is not None else "N/A"
            logger.debug(
                f"보유 유지: {condition_id} "
                f"(가격: {current_price:.2%}, P&L: {pnl_percent:.1%}, "
                f"해결까지: {hours_str})"
            )
            return False

        # Execute sell
        logger.info(
            f"매도: {trade.outcome} - '{trade.question[:50]}...' "
            f"@ {current_price:.2%} ({trade.buy_shares:.2f}주) "
            f"[사유: {exit_reason}, P&L: {pnl_percent:.1%}]"
        )

        result = self.clob.place_limit_order(
            token_id=token_id,
            price=current_price,
            size=trade.buy_shares,
            side="SELL",
        )

        # Check result
        if result.get("success") or result.get("orderID"):
            # Calculate P&L
            sell_value = current_price * trade.buy_shares
            buy_value = trade.buy_price * trade.buy_shares
            realized_pnl = sell_value - buy_value

            # Update trade record
            self.repo.update_trade(
                trade.id,
                sell_price=current_price,
                sell_shares=trade.buy_shares,
                sell_order_id=result.get("orderID"),
                sell_timestamp=datetime.utcnow(),
                sell_probability=current_price,
                realized_pnl=realized_pnl,
                status=TradeStatus.COMPLETED,
                exit_reason=exit_reason,
                # 회고 로깅: 청산 시점 24h median 대비 편차 (계산 불가 시 NULL)
                deviation_at_exit=self._deviation_at_exit(trade, current_price, now),
            )

            pnl_percent_display = (current_price / trade.buy_price - 1) * 100 if trade.buy_price > 0 else 0
            logger.info(
                f"매도 주문 완료: Trade #{trade.id}, "
                f"P&L: ${realized_pnl:.4f} ({pnl_percent_display:.1f}%), "
                f"사유: {exit_reason}"
            )
            return True
        else:
            logger.error(f"매도 주문 실패: {result}")
            return False

    def check_and_sell_holdings(self) -> int:
        """Check all holding positions and sell if conditions met.

        Returns:
            Number of positions sold
        """
        holdings = self.repo.get_holding_trades()
        sold_count = 0

        logger.info(f"보유 포지션 {len(holdings)}개 확인 중")

        for trade in holdings:
            if self.execute_sell(trade):
                sold_count += 1

        return sold_count
