"""Trading execution logic with momentum-based strategy."""
import logging
from datetime import datetime
from typing import Optional, List
from ..db.repository import TradeRepository
from ..db.models import TradeStatus, MarketSnapshot
from ..api.clob_client import ClobClientWrapper
from ..config import TradingConfig
from .momentum import MomentumCalculator

logger = logging.getLogger(__name__)

# Polymarket minimum order size requirement
MIN_ORDER_SIZE = 5.0


class Trader:
    """Executes buy and sell orders based on momentum strategy rules."""

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

        # Initialize momentum calculator if enabled
        self.momentum_calc = None
        if config.momentum.enabled:
            self.momentum_calc = MomentumCalculator(config.momentum)

    def _get_momentum_info(
        self,
        condition_id: str
    ) -> tuple[Optional[float], Optional[float]]:
        """Get momentum info for a market.

        Args:
            condition_id: Market condition ID

        Returns:
            (short_momentum, long_momentum) or (None, None)
        """
        if not self.momentum_calc:
            return None, None

        snapshots = self.repo.get_snapshots_for_condition(
            condition_id,
            limit=self.config.momentum.long_window + 10
        )
        return self.momentum_calc.get_momentum_info(snapshots)

    def execute_buy(self, candidate: dict) -> Optional[int]:
        """Execute a buy order for a candidate market.

        Args:
            candidate: Market candidate dictionary with:
                - condition_id
                - token_id
                - probability
                - outcome ("Yes" or "No")
                - question
                - market_slug
                - liquidity
                - entry_reason (optional)

        Returns:
            Trade ID if successful, None otherwise
        """
        condition_id = candidate["condition_id"]
        token_id = candidate["token_id"]

        # Check: Already traded?
        if self.repo.is_already_traded(condition_id):
            logger.info(f"이미 거래한 시장: {condition_id}")
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
            logger.error(f"가격 조회 실패 - condition: {condition_id}: {e}")
            return None

        # Check: Price jumped above sell threshold?
        # Note: sell_threshold 초과 시에만 skip (97% 이하는 진입 가능)
        if current_price > self.config.sell_threshold:
            logger.info(
                f"급등 감지 - 매수 skip: {condition_id} "
                f"(가격: {current_price:.1%} > 매도 기준 {self.config.sell_threshold:.1%})"
            )
            self.repo.mark_as_skipped(condition_id, "rapid_jump")
            return None

        # Check: Price dropped below buy threshold?
        if current_price < self.config.buy_threshold:
            logger.info(
                f"가격 하락으로 매수 조건 미충족: {condition_id} "
                f"(가격: {current_price:.1%} < 매수 기준 {self.config.buy_threshold:.1%})"
            )
            return None

        # Calculate order size
        # shares = USDC amount / price
        buy_shares = self.config.buy_amount_usdc / current_price

        # Check minimum order size (Polymarket requires at least 5 shares)
        if buy_shares < MIN_ORDER_SIZE:
            logger.warning(
                f"주문 수량 {buy_shares:.2f}주 < 최소 {MIN_ORDER_SIZE}주 - {condition_id}. "
                f"buy_amount_usdc를 늘리거나 낮은 가격에서 매수하세요."
            )
            return None

        # Get momentum info for logging and storage
        short_momentum, long_momentum = self._get_momentum_info(condition_id)
        entry_reason = candidate.get("entry_reason", "unknown")

        # Place order
        logger.info(
            f"매수: {candidate['outcome']} - '{candidate['question'][:50]}...' "
            f"@ {current_price:.2%} ({buy_shares:.2f}주, ${self.config.buy_amount_usdc}) "
            f"[사유: {entry_reason}]"
        )

        result = self.clob.place_limit_order(
            token_id=token_id,
            price=current_price,
            size=buy_shares,
            side="BUY",
        )

        # Check result
        if result.get("success") or result.get("orderID"):
            # Record trade in DB with momentum info
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
                status=TradeStatus.HOLDING,
                # New momentum fields
                entry_reason=entry_reason,
                short_momentum_at_buy=short_momentum,
                long_momentum_at_buy=long_momentum,
            )

            logger.info(f"매수 주문 완료: Trade #{trade.id}, Order: {result.get('orderID')}")
            return trade.id
        else:
            logger.error(f"매수 주문 실패: {result}")
            return None

    def execute_sell(self, trade) -> bool:
        """Execute sell order for a holding position.

        청산 조건 (우선순위 순):
        1. 확률 >= sell_threshold (97%)
        2. 진입가 대비 +7% (이익실현)
        3. 진입가 대비 -10% (손절)
        4. 데드크로스 (모멘텀 역전)

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
            logger.error(f"가격 조회 실패 - condition: {condition_id}: {e}")
            return False

        # Determine exit signal and reason
        should_sell = False
        exit_reason = "hold"

        # 1. Check probability threshold (기존 방식)
        if current_price >= self.config.sell_threshold:
            should_sell = True
            exit_reason = "threshold"
            logger.info(
                f"확률 기준 충족 - 매도: {condition_id} "
                f"(가격: {current_price:.1%} >= {self.config.sell_threshold:.1%})"
            )

        # 2-4. Check momentum-based exit conditions
        elif self.momentum_calc:
            snapshots = self.repo.get_snapshots_for_condition(
                condition_id,
                limit=self.config.momentum.long_window + 10
            )
            should_sell, exit_reason = self.momentum_calc.get_exit_signal(
                snapshots,
                entry_price=trade.buy_price,
                current_price=current_price,
                take_profit=self.config.take_profit_percent,
                stop_loss=self.config.stop_loss_percent,
            )
        else:
            # Momentum disabled: check price-based stop-loss and take-profit only
            if trade.buy_price > 0:
                pnl_percent = (current_price - trade.buy_price) / trade.buy_price
                if pnl_percent <= self.config.stop_loss_percent:
                    should_sell = True
                    exit_reason = "stop_loss"
                    logger.info(
                        f"손절 조건 충족 - 매도: {condition_id} "
                        f"(손실: {pnl_percent:.1%})"
                    )
                elif pnl_percent >= self.config.take_profit_percent:
                    should_sell = True
                    exit_reason = "take_profit"
                    logger.info(
                        f"이익실현 조건 충족 - 매도: {condition_id} "
                        f"(수익: {pnl_percent:.1%})"
                    )

        if not should_sell:
            logger.debug(
                f"보유 유지: {condition_id} "
                f"(가격: {current_price:.1%}, 사유: {exit_reason})"
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

            # Get momentum info at sell
            short_momentum, long_momentum = self._get_momentum_info(condition_id)

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
                # New momentum fields
                exit_reason=exit_reason,
                short_momentum_at_sell=short_momentum,
                long_momentum_at_sell=long_momentum,
            )

            pnl_percent = (current_price / trade.buy_price - 1) * 100 if trade.buy_price > 0 else 0
            logger.info(
                f"매도 주문 완료: Trade #{trade.id}, "
                f"P&L: ${realized_pnl:.4f} ({pnl_percent:.1f}%), "
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
