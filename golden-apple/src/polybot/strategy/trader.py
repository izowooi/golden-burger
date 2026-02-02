"""Trading execution logic."""
import logging
from datetime import datetime
from typing import Optional
from ..db.repository import TradeRepository
from ..db.models import TradeStatus
from ..api.clob_client import ClobClientWrapper
from ..config import TradingConfig

logger = logging.getLogger(__name__)

# Polymarket minimum order size requirement
MIN_ORDER_SIZE = 5.0


class Trader:
    """Executes buy and sell orders based on strategy rules."""

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
                - condition_id
                - token_id
                - probability
                - outcome ("Yes" or "No")
                - question
                - market_slug
                - liquidity

        Returns:
            Trade ID if successful, None otherwise
        """
        condition_id = candidate["condition_id"]
        token_id = candidate["token_id"]

        # Check: Already traded?
        if self.repo.is_already_traded(condition_id):
            logger.info(f"Already traded: {condition_id}")
            return None

        # Check: Max positions limit
        if self.config.max_positions > 0:
            current_positions = self.repo.get_position_count()
            if current_positions >= self.config.max_positions:
                logger.info(f"Max positions ({self.config.max_positions}) reached")
                return None

        # Get current price (re-verify before buying)
        try:
            current_price = self.clob.get_midpoint(token_id)
        except Exception as e:
            logger.error(f"Failed to get price for {condition_id}: {e}")
            return None

        # Check: Price jumped above sell threshold?
        if current_price >= self.config.sell_threshold:
            logger.info(
                f"Rapid jump detected - skipping: {condition_id} "
                f"(price: {current_price:.1%} >= {self.config.sell_threshold:.1%})"
            )
            self.repo.mark_as_skipped(condition_id, "rapid_jump")
            return None

        # Check: Price dropped below buy threshold?
        if current_price < self.config.buy_threshold:
            logger.info(
                f"Price dropped below threshold: {condition_id} "
                f"(price: {current_price:.1%} < {self.config.buy_threshold:.1%})"
            )
            return None

        # Calculate order size
        # shares = USDC amount / price
        buy_shares = self.config.buy_amount_usdc / current_price

        # Check minimum order size (Polymarket requires at least 5 shares)
        if buy_shares < MIN_ORDER_SIZE:
            logger.warning(
                f"Order size {buy_shares:.2f} < minimum {MIN_ORDER_SIZE} for {condition_id}. "
                f"Consider increasing buy_amount_usdc or buying at lower price."
            )
            return None

        # Place order
        logger.info(
            f"Buying: {candidate['outcome']} for '{candidate['question'][:50]}...' "
            f"@ {current_price:.2%} ({buy_shares:.2f} shares for ${self.config.buy_amount_usdc})"
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
                status=TradeStatus.HOLDING,
            )

            logger.info(f"Buy order placed: Trade #{trade.id}, Order: {result.get('orderID')}")
            return trade.id
        else:
            logger.error(f"Buy order failed: {result}")
            return None

    def execute_sell(self, trade) -> bool:
        """Execute sell order for a holding position.

        Args:
            trade: Trade object from DB

        Returns:
            True if sell executed successfully
        """
        token_id = trade.token_id

        # Get current price
        try:
            current_price = self.clob.get_midpoint(token_id)
        except Exception as e:
            logger.error(f"Failed to get price for {trade.condition_id}: {e}")
            return False

        # Check sell condition
        if current_price < self.config.sell_threshold:
            logger.debug(
                f"Hold: {trade.condition_id} "
                f"(price: {current_price:.1%} < {self.config.sell_threshold:.1%})"
            )
            return False

        # Execute sell
        logger.info(
            f"Selling: {trade.outcome} for '{trade.question[:50]}...' "
            f"@ {current_price:.2%} ({trade.buy_shares:.2f} shares)"
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
            )

            logger.info(
                f"Sell order placed: Trade #{trade.id}, "
                f"P&L: ${realized_pnl:.4f} ({(current_price/trade.buy_price - 1)*100:.1f}%)"
            )
            return True
        else:
            logger.error(f"Sell order failed: {result}")
            return False

    def check_and_sell_holdings(self) -> int:
        """Check all holding positions and sell if threshold met.

        Returns:
            Number of positions sold
        """
        holdings = self.repo.get_holding_trades()
        sold_count = 0

        logger.info(f"Checking {len(holdings)} holding positions")

        for trade in holdings:
            if self.execute_sell(trade):
                sold_count += 1

        return sold_count
