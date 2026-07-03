"""Trading execution logic with Bottom Fisher strategy."""
import logging
from datetime import datetime
from typing import Optional
from ..db.repository import TradeRepository
from ..db.models import TradeStatus, STRATEGY_NAME
from ..api.clob_client import ClobClientWrapper
from ..config import TradingConfig
from .scanner import get_hours_until_resolution
from .signals import evaluate_exit

logger = logging.getLogger(__name__)

# Polymarket minimum order size requirement
MIN_ORDER_SIZE = 5.0

# 해결 후 이 시간이 지나도 midpoint 조회가 안 되면 EXPIRED 처리
RESOLVED_GRACE_HOURS = 24.0


class Trader:
    """Executes buy and sell orders based on bottom fisher strategy."""

    def __init__(
        self,
        repo: TradeRepository,
        clob_client: ClobClientWrapper,
        config: TradingConfig,
        mode: str = "live",
    ):
        """Initialize trader.

        Args:
            repo: Trade repository for DB operations
            clob_client: CLOB client for order execution
            config: Trading configuration
            mode: "live" 또는 "sim" (trades.mode에 기록 - §A.2)
        """
        self.repo = repo
        self.clob = clob_client
        self.config = config
        self.mode = mode

    def execute_buy(self, candidate: dict) -> Optional[int]:
        """Execute a buy order for a candidate market.

        Args:
            candidate: Market candidate dictionary with:
                - condition_id
                - token_id (YES 토큰 고정)
                - probability (YES 가격)
                - outcome ("Yes")
                - question / market_slug
                - liquidity / volume_24h
                - entry_reason
                - end_date (datetime)
                - hours_until_resolution (float)
                - rolling_min / lookback_days_covered

        Returns:
            Trade ID if successful, None otherwise
        """
        condition_id = candidate["condition_id"]
        token_id = candidate["token_id"]

        # Check: 재진입 차단 (HOLDING / 청산·skip 쿨다운 168h)
        blocked, block_reason = self.repo.is_reentry_blocked(
            condition_id, self.config.reentry_cooldown_hours
        )
        if blocked:
            logger.info(f"재진입 차단 ({block_reason}): {condition_id}")
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

        if current_price <= 0:
            logger.warning(f"유효하지 않은 가격 ({current_price}) - skip: {condition_id}")
            return None

        # Check: 스캔~주문 사이 반등으로 신저가 프리미엄 소멸 -> 쿨다운 skip
        if current_price > self.config.strategy.prob_max:
            logger.info(
                f"밴드 상단 이탈 - 매수 skip: {condition_id} "
                f"(가격: {current_price:.1%} > 상한 {self.config.strategy.prob_max:.1%})"
            )
            self.repo.mark_as_skipped(condition_id, "price_above_band")
            return None

        # Check: 추가 붕괴 진행 중 -> 이번 사이클만 보류 (쿨다운 없음)
        if current_price < self.config.strategy.prob_min:
            logger.info(
                f"밴드 하단 이탈 - 매수 보류: {condition_id} "
                f"(가격: {current_price:.1%} < 하한 {self.config.strategy.prob_min:.1%})"
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
            # Record trade in DB (회고 로깅 표준 §A: strategy_name/mode/시그널 컬럼)
            trade = self.repo.create_trade(
                strategy_name=STRATEGY_NAME,
                mode=self.mode,
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
                volume_24h_at_buy=candidate.get("volume_24h"),
                market_tags=candidate.get("market_tags", ""),
                status=TradeStatus.HOLDING,
                # Bottom fisher strategy fields
                entry_reason=entry_reason,
                rolling_min_at_buy=candidate.get("rolling_min"),
                lookback_days_at_buy=candidate.get("lookback_days_covered"),
                max_price=current_price,
                market_end_date=end_date,
                hours_until_resolution_at_buy=hours_until_resolution,
            )

            logger.info(f"매수 주문 완료: Trade #{trade.id}, Order: {result.get('orderID')}")
            return trade.id
        else:
            logger.error(f"매수 주문 실패: {result}")
            return None

    def _handle_resolved_market(self, trade) -> bool:
        """midpoint 조회 실패 시 해결된 시장 leak 처리 (§3.4).

        market_end_date가 RESOLVED_GRACE_HOURS 이상 지났으면 EXPIRED로 마감해
        영구 좀비 HOLDING을 방지한다. realized_pnl은 NULL로 두고
        수동 redeem을 요청한다.

        Returns:
            True if trade was marked EXPIRED
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
            f"해결된 시장 마감 처리 (EXPIRED): Trade #{trade.id} "
            f"'{trade.question[:50]}...' - 수동 redeem 필요 "
            f"(해결 후 {-hours_left:.1f}h 경과)"
        )
        return True

    def execute_sell(self, trade) -> bool:
        """Execute sell order for a holding position.

        청산 조건 (우선순위 순, 트레일링 없음):
        1. calendar exit: 보유 120h 도달 시 **무조건** 청산 (주 청산 경로 -
           QuantPedia 백테스트의 Y=5일 규칙 복제)
        2. 손절 안전판: P&L <= -30%
        3. 익절 안전판: P&L >= +30% (목표가 0.99 캡)
        4. 시간: 해결 24시간 이내

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
            # 해결된 시장 leak 수정: 해결 후 24h 지나면 EXPIRED 마감
            self._handle_resolved_market(trade)
            return False

        # midpoint 0.0은 조회 실패로 취급 - stop_loss 오발동으로 0.01에
        # 투매하는 것을 방지한다 (zero-midpoint 가드)
        if current_price <= 0:
            logger.warning(f"유효하지 않은 가격 ({current_price}) - condition: {condition_id}")
            self._handle_resolved_market(trade)
            return False

        # Update max_price for analysis (트레일링에는 사용하지 않음)
        max_price = trade.max_price or trade.buy_price
        if current_price > max_price:
            max_price = current_price
            self.repo.update_trade(trade.id, max_price=max_price)
            logger.debug(f"최고가 갱신: {condition_id} -> {max_price:.2%}")

        # Compute holding hours / hours left
        holding_hours = None
        if trade.buy_timestamp:
            holding_hours = (
                datetime.utcnow() - trade.buy_timestamp
            ).total_seconds() / 3600
        hours_left = get_hours_until_resolution(trade.market_end_date)

        should_sell, exit_reason = evaluate_exit(
            buy_price=trade.buy_price,
            current_price=current_price,
            take_profit_percent=self.config.take_profit_percent,
            stop_loss_percent=self.config.stop_loss_percent,
            holding_hours=holding_hours,
            hold_hours=self.config.strategy.hold_hours,
            hours_left=hours_left,
            exit_hours=self.config.time_based.exit_hours,
        )

        pnl_percent = 0.0
        if trade.buy_price > 0:
            pnl_percent = (current_price - trade.buy_price) / trade.buy_price

        if not should_sell:
            hours_str = f"{hours_left:.1f}h" if hours_left is not None else "N/A"
            holding_str = f"{holding_hours:.1f}h" if holding_hours is not None else "N/A"
            logger.debug(
                f"보유 유지: {condition_id} "
                f"(가격: {current_price:.2%}, P&L: {pnl_percent:.1%}, "
                f"보유: {holding_str}/{self.config.strategy.hold_hours:.0f}h, "
                f"해결까지: {hours_str})"
            )
            return False

        logger.info(
            f"청산 조건 충족 [{exit_reason}] - 매도: {trade.outcome} - "
            f"'{trade.question[:50]}...' @ {current_price:.2%} "
            f"({trade.buy_shares:.2f}주, P&L: {pnl_percent:.1%})"
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

            # Update trade record (청산 시그널 컬럼 hold_hours_at_exit 기록 - §A.5)
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
                hold_hours_at_exit=holding_hours,
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
