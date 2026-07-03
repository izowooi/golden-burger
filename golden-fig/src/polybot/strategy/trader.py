"""Trading execution logic (Hope Crusher strategy)."""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from ..db.repository import TradeRepository
from ..db.models import TradeStatus
from ..api.clob_client import ClobClientWrapper
from ..config import TradingConfig
from .scanner import get_hours_until_resolution
from .signals import evaluate_exit, take_profit_target

logger = logging.getLogger(__name__)

# Polymarket minimum order size requirement
MIN_ORDER_SIZE = 5.0

# DB 회고 로깅용 봇 식별 상수 (교차 봇 UNION 쿼리 계약)
STRATEGY_NAME = "fig"

# 해결 후 이 시간이 지나도 가격 조회가 안 되면 EXPIRED 마감 처리
RESOLVED_GRACE_HOURS = 24.0


class Trader:
    """Executes buy and sell orders for the Hope Crusher strategy."""

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
            simulation_mode: True면 trades.mode에 "sim" 기록 (회고 분석용)
        """
        self.repo = repo
        self.clob = clob_client
        self.config = config
        self.mode = "sim" if simulation_mode else "live"

    def execute_buy(self, candidate: dict) -> Optional[int]:
        """Execute a buy order for a candidate market (NO 토큰).

        Args:
            candidate: Market candidate dictionary with:
                - condition_id
                - token_id (NO 토큰)
                - probability (NO 가격)
                - yes_price
                - outcome ("No")
                - question / market_slug / liquidity
                - entry_reason
                - end_date (datetime)
                - hours_until_resolution (float)

        Returns:
            Trade ID if successful, None otherwise
        """
        condition_id = candidate["condition_id"]
        token_id = candidate["token_id"]

        # Check: 이미 보유 중인 시장?
        if self.repo.has_holding(condition_id):
            logger.info(f"이미 보유 중인 시장 skip: {condition_id}")
            return None

        # Check: 재진입 쿨다운?
        if self.repo.is_in_reentry_cooldown(
            condition_id, self.config.reentry_cooldown_hours
        ):
            logger.info(
                f"재진입 쿨다운 중 skip: {condition_id} "
                f"(쿨다운: {self.config.reentry_cooldown_hours}h)"
            )
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
            logger.warning(f"유효하지 않은 가격 - 매수 skip: {condition_id}")
            return None

        # NO 매수 밴드 재검증: [1 - yes_max, 1 - yes_min]
        no_min = 1.0 - self.config.strategy.yes_max
        no_max = 1.0 - self.config.strategy.yes_min

        # NO 급등 = YES 붕괴. 밴드 상단 초과면 쿨다운 skip 기록
        if current_price > no_max:
            logger.info(
                f"급등 감지 - 매수 skip: {condition_id} "
                f"(NO 가격: {current_price:.1%} > 상한 {no_max:.1%})"
            )
            self.repo.mark_as_skipped(condition_id, "rapid_jump")
            return None

        # NO 하락 = YES 상승 = 사건 진행 신호. 이번 사이클만 skip
        if current_price < no_min:
            logger.info(
                f"가격 하락으로 매수 조건 미충족: {condition_id} "
                f"(NO 가격: {current_price:.1%} < 하한 {no_min:.1%})"
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
                entry_reason=entry_reason,
                max_price=current_price,  # Initialize max_price with buy price
                market_end_date=end_date,
                hours_until_resolution_at_buy=hours_until_resolution,
                yes_price_at_buy=candidate.get("yes_price"),
                volume_24h_at_buy=candidate.get("volume_24h"),
                strategy_name=STRATEGY_NAME,
                mode=self.mode,
            )

            logger.info(f"매수 주문 완료: Trade #{trade.id}, Order: {result.get('orderID')}")
            return trade.id
        else:
            logger.error(f"매수 주문 실패: {result}")
            return None

    def _expire_if_resolved(self, trade) -> bool:
        """가격 조회 실패 시 해결된 시장 leak 처리 (§3.4).

        market_end_date가 24시간 이상 지났으면 EXPIRED로 마감 처리하고
        수동 redeem 필요 경고를 남긴다. EXPIRED는 get_holding_trades에서
        제외되므로 좀비 포지션이 무한 반복 조회되지 않는다.

        Returns:
            True if trade was marked EXPIRED
        """
        end_date = trade.market_end_date
        if end_date is None:
            return False

        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        if now - end_date < timedelta(hours=RESOLVED_GRACE_HOURS):
            return False

        self.repo.update_trade(
            trade.id,
            status=TradeStatus.EXPIRED,
            exit_reason="resolved_unredeemed",
            realized_pnl=None,
        )
        logger.warning(
            f"해결된 시장 마감 처리(EXPIRED): Trade #{trade.id} - "
            f"'{trade.question[:50]}...' "
            f"(해결 {end_date.isoformat()} 후 {RESOLVED_GRACE_HOURS:.0f}h 경과, "
            f"midpoint 조회 불가). 수동 redeem 필요."
        )
        return True

    def execute_sell(self, trade) -> bool:
        """Execute sell order for a holding position.

        청산 조건 (우선순위 순, trailing 없음):
        1. 손절: P&L <= -10% (YES 급등 = 사건 발생 신호)
        2. 익절: 현재가 >= buy_price*(1+6%) [0.99 캡]
        3. 시간: 해결 2시간 이내

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
            # 해결된 시장 leak 수정: 24h 지난 시장은 EXPIRED 마감
            self._expire_if_resolved(trade)
            return False

        # midpoint가 예외 없이 0으로 오는 경우도 조회 실패로 취급한다.
        # 0을 그대로 쓰면 P&L -100% → stop_loss 판정 → 0.01에 투매된다.
        if current_price <= 0:
            logger.warning(
                f"유효하지 않은 midpoint({current_price}) - 매도 판정 skip: {condition_id}"
            )
            self._expire_if_resolved(trade)
            return False

        # Update max_price (분석용 - trailing stop은 없음)
        max_price = trade.max_price or trade.buy_price
        if current_price > max_price:
            max_price = current_price
            self.repo.update_trade(trade.id, max_price=max_price)
            logger.debug(f"최고가 갱신: {condition_id} -> {max_price:.2%}")

        hours_left = get_hours_until_resolution(trade.market_end_date)

        signal = evaluate_exit(
            buy_price=trade.buy_price,
            current_price=current_price,
            hours_left=hours_left,
            take_profit_percent=self.config.take_profit_percent,
            stop_loss_percent=self.config.stop_loss_percent,
            exit_hours=float(self.config.time_based.exit_hours),
        )

        pnl_percent = 0.0
        if trade.buy_price and trade.buy_price > 0:
            pnl_percent = (current_price - trade.buy_price) / trade.buy_price

        if not signal.should_sell:
            hours_str = f"{hours_left:.1f}h" if hours_left is not None else "N/A"
            target = take_profit_target(trade.buy_price, self.config.take_profit_percent)
            logger.debug(
                f"보유 유지: {condition_id} "
                f"(가격: {current_price:.2%}, P&L: {pnl_percent:.1%}, "
                f"익절 목표: {target:.2%}, 해결까지: {hours_str})"
            )
            return False

        if signal.reason == "stop_loss":
            logger.info(
                f"손절 조건 충족 - 매도: {condition_id} "
                f"(손실: {pnl_percent:.1%} <= {self.config.stop_loss_percent:.1%}, "
                f"NO 하락 = 사건 발생 신호)"
            )
        elif signal.reason == "take_profit":
            target = take_profit_target(trade.buy_price, self.config.take_profit_percent)
            logger.info(
                f"익절 조건 충족 - 매도: {condition_id} "
                f"(가격: {current_price:.2%} >= 목표 {target:.2%})"
            )
        elif signal.reason == "time_exit":
            hours_str = f"{hours_left:.1f}h" if hours_left is not None else "N/A"
            logger.info(
                f"시간 기반 청산 - 매도: {condition_id} "
                f"(해결까지: {hours_str} < {self.config.time_based.exit_hours}h)"
            )

        # Execute sell
        logger.info(
            f"매도: {trade.outcome} - '{trade.question[:50]}...' "
            f"@ {current_price:.2%} ({trade.buy_shares:.2f}주) [사유: {signal.reason}]"
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
                exit_reason=signal.reason,
                # 청산 시점 YES 가격 (1 - NO 매도가): YES 소멸 속도 회고용
                yes_price_at_exit=1.0 - current_price,
            )

            pnl_percent_display = (current_price / trade.buy_price - 1) * 100 if trade.buy_price > 0 else 0
            logger.info(
                f"매도 주문 완료: Trade #{trade.id}, "
                f"P&L: ${realized_pnl:.4f} ({pnl_percent_display:.1f}%), "
                f"사유: {signal.reason}"
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
