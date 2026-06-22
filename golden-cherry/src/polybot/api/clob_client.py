"""CLOB API client wrapper for order execution.

Polymarket이 2026년 4월 CLOB v2로 마이그레이션함에 따라 본 모듈은
`py-clob-client-v2` (import: `py_clob_client_v2`) 를 사용한다.
구버전 `py-clob-client` 는 `order_version_mismatch` 오류로 더 이상 동작하지 않는다.
"""
import logging
from typing import Optional, Dict, Any
from ..config import ApiConfig
from ..utils.retry import rate_limit_handler

logger = logging.getLogger(__name__)


class ClobClientWrapper:
    """Wrapper for Polymarket CLOB v2 API client.

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
            from py_clob_client_v2 import ClobClient

            self._client = ClobClient(
                host=self.HOST,
                key=self.config.private_key,
                chain_id=self.config.chain_id,
                signature_type=self.config.signature_type,
                funder=self.config.funder_address,
            )

            # Create or derive API credentials (v2: create_or_derive_api_key)
            api_creds = self._client.create_or_derive_api_key()
            self._client.set_api_creds(api_creds)
            self._initialized = True
            logger.info("CLOB client 초기화 완료 (v2)")

        except Exception as e:
            logger.error(f"CLOB client 초기화 실패: {e}")
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
        rounded = round(round(price / tick_size) * tick_size, 2)
        # Clamp to tick-aligned bounds. Polymarket rejects prices outside
        # [tick_size, 1 - tick_size] with "invalid price (1.0)" / "(0.0)".
        return min(max(rounded, tick_size), round(1 - tick_size, 2))

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
            # 해결/비유동 시장은 orderbook이 없어 404가 흔하다. 정상 흐름이므로 debug로 낮춘다.
            if "No orderbook" in str(e):
                logger.debug(f"orderbook 없음 - token: {token_id}: {e}")
            else:
                logger.error(f"midpoint 조회 실패 - token: {token_id}: {e}")
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
            if "No orderbook" in str(e):
                logger.debug(f"orderbook 없음 - token: {token_id}: {e}")
            else:
                logger.error(f"best bid 조회 실패 - token: {token_id}: {e}")
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
            if "No orderbook" in str(e):
                logger.debug(f"orderbook 없음 - token: {token_id}: {e}")
            else:
                logger.error(f"best ask 조회 실패 - token: {token_id}: {e}")
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
            logger.info(f"[SIM] Market BUY - token: {token_id}, 금액: ${amount_usdc}")
            return {
                "success": True,
                "orderID": f"SIM_BUY_{token_id[:8]}",
                "simulated": True,
            }

        try:
            from py_clob_client_v2 import MarketOrderArgs, OrderType

            # v2: side는 MarketOrderArgs 안의 필드로 들어감 (v1 에서는 별도 인자였음)
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=amount_usdc,
                side="BUY",
            )
            response = self.client.create_and_post_market_order(
                order_args,
                order_type=OrderType.FOK,
            )
            logger.info(f"Market BUY 주문 완료: {response}")
            return response

        except Exception as e:
            logger.error(f"Market BUY 주문 실패: {e}")
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
            logger.info(f"[SIM] Limit {side} - {size:.2f}주 @ {rounded_price:.2f}, token: {token_id}")
            return {
                "success": True,
                "orderID": f"SIM_{side}_{token_id[:8]}",
                "simulated": True,
                "price": rounded_price,
            }

        try:
            from py_clob_client_v2 import OrderArgs, OrderType

            order_side = "BUY" if side.upper() == "BUY" else "SELL"

            order_args = OrderArgs(
                token_id=token_id,
                price=rounded_price,
                size=size,
                side=order_side,
            )

            # v2: create_order + post_order 두 단계 유지 (v1과 동일 패턴)
            signed_order = self.client.create_order(order_args)
            response = self.client.post_order(signed_order, OrderType.GTC)

            logger.info(f"Limit {side} 주문 완료 @ {rounded_price:.2f}: {response}")
            return response

        except Exception as e:
            logger.error(f"Limit 주문 실패: {e}")
            return {"success": False, "error": str(e)}

    @rate_limit_handler(max_retries=3)
    def get_open_orders(self) -> list:
        """Get all open orders.

        Returns:
            List of open orders
        """
        try:
            # v2: get_orders() 제거됨 → get_open_orders() 사용
            return self.client.get_open_orders()
        except Exception as e:
            logger.error(f"미체결 주문 조회 실패: {e}")
            return []

    @rate_limit_handler(max_retries=3)
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order.

        Args:
            order_id: Order ID (hash) to cancel

        Returns:
            Cancellation result
        """
        if self.simulation_mode:
            logger.info(f"[SIM] 주문 취소 - order: {order_id}")
            return {"success": True, "simulated": True}

        try:
            # v2: cancel(order_id=...) → cancel_orders([hash, ...])
            result = self.client.cancel_orders([order_id])
            logger.info(f"주문 취소 완료: {order_id}")
            return result
        except Exception as e:
            logger.error(f"주문 취소 실패 - order: {order_id}: {e}")
            return {"success": False, "error": str(e)}

    def test_connection(self) -> bool:
        """Test API connection and credentials.

        Returns:
            True if connection successful
        """
        try:
            self._ensure_initialized()
            # v2: 연결 확인용으로 get_open_orders() 사용
            self.client.get_open_orders()
            return True
        except Exception as e:
            logger.error(f"연결 테스트 실패: {e}")
            return False
