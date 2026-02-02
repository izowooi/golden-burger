"""CLOB API client wrapper for order execution."""
import logging
from typing import Optional, Dict, Any
from ..config import ApiConfig
from ..utils.retry import rate_limit_handler

logger = logging.getLogger(__name__)


class ClobClientWrapper:
    """Wrapper for Polymarket CLOB API client.

    Handles:
    - Authentication with L1 (wallet) and L2 (API key) credentials
    - Order placement and cancellation
    - Price and orderbook queries
    - Simulation mode for testing
    """

    HOST = "https://clob.polymarket.com"
    DEFAULT_TICK_SIZE = 0.01  # Polymarket default tick size

    def __init__(self, config: ApiConfig, simulation_mode: bool = False):
        """Initialize CLOB client.

        Args:
            config: API configuration with credentials
            simulation_mode: If True, don't execute real orders
        """
        self.config = config
        self.simulation_mode = simulation_mode
        self._client = None
        self._initialized = False

    def _ensure_initialized(self):
        """Lazy initialization of the CLOB client."""
        if self._initialized:
            return

        try:
            from py_clob_client.client import ClobClient

            self._client = ClobClient(
                host=self.HOST,
                key=self.config.private_key,
                chain_id=self.config.chain_id,
                signature_type=self.config.signature_type,
                funder=self.config.funder_address,
            )

            # Create or derive API credentials
            api_creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(api_creds)
            self._initialized = True
            logger.info("CLOB client initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize CLOB client: {e}")
            raise

    @property
    def client(self):
        """Get initialized CLOB client."""
        self._ensure_initialized()
        return self._client

    def _round_to_tick(self, price: float, tick_size: float = None) -> float:
        """Round price to tick size.

        Polymarket requires prices to be in tick_size increments (default 0.01).
        This prevents INVALID_ORDER_MIN_TICK_SIZE errors.

        Args:
            price: Raw price value
            tick_size: Tick size to round to (default: 0.01)

        Returns:
            Price rounded to nearest tick
        """
        if tick_size is None:
            tick_size = self.DEFAULT_TICK_SIZE
        # Round to avoid floating point precision issues
        return round(round(price / tick_size) * tick_size, 2)

    @rate_limit_handler(max_retries=3)
    def get_midpoint(self, token_id: str) -> float:
        """Get midpoint price for a token.

        Args:
            token_id: Token ID to query

        Returns:
            Midpoint price as float (0.0-1.0)
        """
        try:
            result = self.client.get_midpoint(token_id)
            # API returns dict like {'mid': '0.875'}
            if isinstance(result, dict):
                price = result.get("mid", 0)
            else:
                price = result
            return float(price) if price else 0.0
        except Exception as e:
            logger.error(f"Failed to get midpoint for {token_id}: {e}")
            raise

    @rate_limit_handler(max_retries=3)
    def get_best_bid(self, token_id: str) -> float:
        """Get best bid price.

        Args:
            token_id: Token ID

        Returns:
            Best bid price
        """
        try:
            return float(self.client.get_price(token_id, side="BUY"))
        except Exception as e:
            logger.error(f"Failed to get best bid for {token_id}: {e}")
            raise

    @rate_limit_handler(max_retries=3)
    def get_best_ask(self, token_id: str) -> float:
        """Get best ask price.

        Args:
            token_id: Token ID

        Returns:
            Best ask price
        """
        try:
            return float(self.client.get_price(token_id, side="SELL"))
        except Exception as e:
            logger.error(f"Failed to get best ask for {token_id}: {e}")
            raise

    @rate_limit_handler(max_retries=3)
    def place_market_buy(
        self,
        token_id: str,
        amount_usdc: float,
    ) -> Dict[str, Any]:
        """Place a market buy order.

        Args:
            token_id: Token to buy
            amount_usdc: Amount in USDC to spend

        Returns:
            Order result dictionary
        """
        if self.simulation_mode:
            logger.info(f"[SIM] Market BUY {token_id} for ${amount_usdc}")
            return {
                "success": True,
                "orderID": f"SIM_BUY_{token_id[:8]}",
                "simulated": True,
            }

        try:
            from py_clob_client.clob_types import MarketOrderArgs
            from py_clob_client.order_builder.constants import BUY

            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=amount_usdc,
            )
            response = self.client.create_and_post_market_order(order_args, BUY)
            logger.info(f"Market BUY order placed: {response}")
            return response

        except Exception as e:
            logger.error(f"Failed to place market buy: {e}")
            return {"success": False, "error": str(e)}

    @rate_limit_handler(max_retries=3)
    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
    ) -> Dict[str, Any]:
        """Place a limit order.

        Args:
            token_id: Token ID
            price: Limit price (0.0-1.0)
            size: Number of shares
            side: "BUY" or "SELL"

        Returns:
            Order result dictionary
        """
        # Round price to tick size to avoid INVALID_ORDER_MIN_TICK_SIZE error
        rounded_price = self._round_to_tick(price)

        if self.simulation_mode:
            logger.info(f"[SIM] Limit {side} {size:.2f} shares of {token_id} @ {rounded_price:.2f}")
            return {
                "success": True,
                "orderID": f"SIM_{side}_{token_id[:8]}",
                "simulated": True,
                "price": rounded_price,
            }

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL

            order_side = BUY if side.upper() == "BUY" else SELL

            order_args = OrderArgs(
                token_id=token_id,
                price=rounded_price,
                size=size,
                side=order_side,
            )

            signed_order = self.client.create_order(order_args)
            response = self.client.post_order(signed_order, OrderType.GTC)

            logger.info(f"Limit {side} order placed @ {rounded_price:.2f}: {response}")
            return response

        except Exception as e:
            logger.error(f"Failed to place limit order: {e}")
            return {"success": False, "error": str(e)}

    @rate_limit_handler(max_retries=3)
    def get_open_orders(self) -> list:
        """Get all open orders.

        Returns:
            List of open orders
        """
        try:
            return self.client.get_orders()
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            return []

    @rate_limit_handler(max_retries=3)
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order.

        Args:
            order_id: Order ID to cancel

        Returns:
            Cancellation result
        """
        if self.simulation_mode:
            logger.info(f"[SIM] Cancel order {order_id}")
            return {"success": True, "simulated": True}

        try:
            result = self.client.cancel(order_id=order_id)
            logger.info(f"Order cancelled: {order_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return {"success": False, "error": str(e)}

    def test_connection(self) -> bool:
        """Test API connection and credentials.

        Returns:
            True if connection successful
        """
        try:
            self._ensure_initialized()
            # Try to get orders as a connection test
            self.client.get_orders()
            return True
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False
