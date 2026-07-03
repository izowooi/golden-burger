"""Trading execution logic with Shock Follow strategy."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from ..db.repository import TradeRepository
from ..db.models import TradeStatus
from ..api.clob_client import ClobClientWrapper
from ..config import TradingConfig
from .scanner import get_hours_until_resolution
from .signals import (
    capped_take_profit_target,
    invert_series,
    is_momentum_dead,
    to_price_points,
)

logger = logging.getLogger(__name__)

# Polymarket minimum order size requirement
MIN_ORDER_SIZE = 5.0

# 해결된 시장 판정: endDate 이후 이 시간이 지나도 가격 조회가 안 되면 EXPIRED 처리
RESOLVED_GRACE_HOURS = 24.0


class Trader:
    """Executes buy and sell orders based on Shock Follow strategy."""

    def __init__(
        self,
        repo: TradeRepository,
        clob_client: ClobClientWrapper,
        config: TradingConfig,
    ):
        """Initialize trader.

        Args:
            repo: Trade repository for DB operations
            clob_client: CLOB client for order execution
            config: Trading configuration
        """
        self.repo = repo
        self.clob = clob_client
        self.config = config

    def execute_buy(self, candidate: dict) -> Optional[int]:
        """Execute a buy order for a candidate market.

        Args:
            candidate: Market candidate dictionary with:
                - condition_id, token_id, token_index
                - probability (매수 토큰 기준 가격)
                - outcome ("Yes" or "No")
                - question, market_slug, liquidity
                - entry_reason, jump_size, base_price
                - end_date (datetime), hours_until_resolution (float)

        Returns:
            Trade ID if successful, None otherwise
        """
        condition_id = candidate["condition_id"]
        token_id = candidate["token_id"]

        # Check: 재진입 쿨다운/보유 중 (Phase 3에서 확인하지만 방어적으로 이중 체크)
        can_enter, enter_reason = self.repo.can_enter(
            condition_id, self.config.reentry_cooldown_hours
        )
        if not can_enter:
            logger.info(f"재진입 불가 skip: {condition_id} ({enter_reason})")
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

        # Check: 스캔~주문 사이 추가 급등으로 러닝룸 소진?
        # 영구 밴이 아니라 skipped_at 기준 쿨다운으로만 재진입을 막는다.
        if current_price > self.config.shock.current_max:
            logger.info(
                f"러닝룸 소진 - 매수 skip: {condition_id} "
                f"(가격: {current_price:.1%} > 상한 {self.config.shock.current_max:.1%})"
            )
            self.repo.mark_as_skipped(condition_id, "post_scan_jump")
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
                token_index=candidate.get("token_index"),
                buy_price=current_price,
                buy_amount=self.config.buy_amount_usdc,
                buy_shares=buy_shares,
                buy_order_id=result.get("orderID"),
                buy_timestamp=datetime.utcnow(),
                buy_probability=current_price,
                liquidity_at_buy=candidate["liquidity"],
                market_tags=candidate.get("market_tags", ""),
                status=TradeStatus.HOLDING,
                # Shock follow strategy fields
                entry_reason=entry_reason,
                jump_size_at_buy=candidate.get("jump_size"),
                base_price_at_buy=candidate.get("base_price"),
                max_price=current_price,  # Initialize max_price with buy price
                market_end_date=end_date,
                hours_until_resolution_at_buy=hours_until_resolution,
            )

            logger.info(f"매수 주문 완료: Trade #{trade.id}, Order: {result.get('orderID')}")
            return trade.id
        else:
            logger.error(f"매수 주문 실패: {result}")
            return None

    def _handle_price_unavailable(self, trade) -> bool:
        """midpoint 조회 실패 시 해결된 시장 leak 처리 (§3.4).

        market_end_date가 24시간 이상 지났으면 EXPIRED로 마감 처리한다.
        realized_pnl은 NULL로 남기고 수동 redeem을 요구한다 - cherry의
        '영구 HOLDING 좀비 포지션' 버그 수정.

        Returns:
            항상 False (매도 성사 아님)
        """
        end_date = trade.market_end_date
        if end_date is None:
            return False

        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        if now - end_date >= timedelta(hours=RESOLVED_GRACE_HOURS):
            self.repo.update_trade(
                trade.id,
                status=TradeStatus.EXPIRED,
                exit_reason="resolved_unredeemed",
                realized_pnl=None,
            )
            logger.warning(
                f"해결된 시장 감지 - Trade #{trade.id} EXPIRED 처리: '{trade.question[:50]}...' "
                f"(endDate {trade.market_end_date} 이후 {RESOLVED_GRACE_HOURS:.0f}h 경과). "
                f"수동 redeem 필요 - realized_pnl은 기록하지 않음."
            )
        return False

    def _is_momentum_dead(self, trade, current_price: float) -> bool:
        """모멘텀 사망 판정: 최근 death_window 동안 매수 토큰 가격 변화 <= 0.

        스냅샷은 YES 가격 기준이므로 NO 포지션(token_index=1)은 1-p 변환한다.
        """
        death_hours = self.config.shock.death_window_hours
        now = datetime.utcnow()
        snapshots = self.repo.get_snapshots_since(
            trade.condition_id, now - timedelta(hours=death_hours)
        )
        points = to_price_points(snapshots)
        if trade.token_index == 1:
            points = invert_series(points)
        return is_momentum_dead(points, current_price, death_hours, now)

    def execute_sell(self, trade) -> bool:
        """Execute sell order for a holding position.

        청산 조건 (우선순위 순):
        1. 손절: P&L <= -8%
        2. 익절: 현재가 >= min(매수가 x 1.12, 0.99)
        3. 트레일링 스탑: 최고점 대비 -6%
        4. 모멘텀 사망: 최근 3h 가격 변화 <= 0
        5. 시간: 해결 12시간 이내

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
            return self._handle_price_unavailable(trade)

        if current_price <= 0:
            logger.warning(f"유효하지 않은 가격 ({current_price}) - condition: {condition_id}")
            return self._handle_price_unavailable(trade)

        # Update max_price if current price is higher
        max_price = trade.max_price or trade.buy_price
        if current_price > max_price:
            max_price = current_price
            self.repo.update_trade(trade.id, max_price=max_price)
            logger.debug(f"최고가 갱신: {condition_id} -> {max_price:.2%}")

        # Calculate P&L
        pnl_percent = 0.0
        if trade.buy_price > 0:
            pnl_percent = (current_price - trade.buy_price) / trade.buy_price

        # Determine exit signal and reason
        should_sell = False
        exit_reason = "hold"

        # 1. Stop Loss: P&L <= -8%
        if pnl_percent <= self.config.stop_loss_percent:
            should_sell = True
            exit_reason = "stop_loss"
            logger.info(
                f"손절 조건 충족 - 매도: {condition_id} "
                f"(손실: {pnl_percent:.1%} <= {self.config.stop_loss_percent:.1%})"
            )

        # 2. Take Profit: 목표가 = min(buy*(1+tp), 0.99) - 도달 불가 목표가 방지 (§3.5)
        elif current_price >= capped_take_profit_target(
            trade.buy_price, self.config.take_profit_percent
        ):
            should_sell = True
            exit_reason = "take_profit"
            logger.info(
                f"익절 조건 충족 - 매도: {condition_id} "
                f"(현재가: {current_price:.2%} >= 목표가 "
                f"{capped_take_profit_target(trade.buy_price, self.config.take_profit_percent):.2%})"
            )

        # 3. Trailing Stop: current_price < max_price * (1 - trailing_percent)
        elif self.config.trailing_stop.enabled and max_price > 0:
            trailing_trigger = max_price * (1 - self.config.trailing_stop.percent)
            if current_price < trailing_trigger:
                should_sell = True
                exit_reason = "trailing_stop"
                drawdown = (max_price - current_price) / max_price
                logger.info(
                    f"트레일링 스탑 충족 - 매도: {condition_id} "
                    f"(최고가: {max_price:.2%}, 현재가: {current_price:.2%}, "
                    f"하락폭: {drawdown:.1%})"
                )

        # 4. Momentum Death: 최근 3h 가격 변화 <= 0 (편승 근거 소멸)
        if not should_sell and self._is_momentum_dead(trade, current_price):
            should_sell = True
            exit_reason = "momentum_death"
            logger.info(
                f"모멘텀 사망 - 매도: {condition_id} "
                f"(최근 {self.config.shock.death_window_hours:.0f}h 상승분 소멸)"
            )

        # 5. Time-based Exit: hours_until_resolution < exit_hours
        if not should_sell:
            hours_left = get_hours_until_resolution(trade.market_end_date)
            if hours_left is not None and hours_left < self.config.time_based.exit_hours:
                should_sell = True
                exit_reason = "time_exit"
                logger.info(
                    f"시간 기반 청산 - 매도: {condition_id} "
                    f"(해결까지: {hours_left:.1f}h < {self.config.time_based.exit_hours}h)"
                )

        if not should_sell:
            hours_left = get_hours_until_resolution(trade.market_end_date)
            hours_str = f"{hours_left:.1f}h" if hours_left is not None else "N/A"
            logger.debug(
                f"보유 유지: {condition_id} "
                f"(가격: {current_price:.2%}, P&L: {pnl_percent:.1%}, "
                f"최고가: {max_price:.2%}, 해결까지: {hours_str})"
            )
            return False

        # Execute sell
        logger.info(
            f"매도: {trade.outcome} - '{trade.question[:50]}...' "
            f"@ {current_price:.2%} ({trade.buy_shares:.2f}주) [사유: {exit_reason}]"
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
