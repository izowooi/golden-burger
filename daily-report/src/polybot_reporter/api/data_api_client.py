"""Data API client for user positions and portfolio data."""
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import requests
from ..retry import rate_limit_handler

logger = logging.getLogger(__name__)


class DataAPIClient:
    """Client for Polymarket Data API (user-specific data).

    Data API provides:
    - User positions (current holdings)
    - Trade history
    - Portfolio performance data
    - Requires wallet address for queries
    """

    BASE_URL = "https://data-api.polymarket.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "GoldenApple-PolyBot/1.0"
        })

    @rate_limit_handler(max_retries=3)
    def get_positions(self, address: str) -> List[Dict]:
        """Get current positions for a wallet address.

        Args:
            address: Wallet address (funder address)

        Returns:
            List of position dictionaries with:
            - conditionId: Market identifier
            - outcome: "Yes" or "No"
            - size: Number of shares held
            - value: Current value in USDC
            - pnl: Unrealized profit/loss
        """
        try:
            params = {"address": address.lower()}
            response = self.session.get(f"{self.BASE_URL}/positions", params=params)
            response.raise_for_status()
            positions = response.json()
            logger.info(f"포지션 {len(positions)}개 조회 완료 - address: {address[:10]}...")
            return positions
        except requests.exceptions.RequestException as e:
            logger.error(f"포지션 조회 실패 - address: {address}: {e}")
            return []

    @rate_limit_handler(max_retries=3)
    def get_activity(
        self,
        address: str,
        limit: int = 100,
        condition_id: Optional[str] = None
    ) -> List[Dict]:
        """Get user activity history (trades, deposits, withdrawals).

        Args:
            address: Wallet address
            limit: Maximum number of activities to return
            condition_id: Optional filter for specific market

        Returns:
            List of activity dictionaries sorted by timestamp (newest first)
        """
        try:
            params = {
                "address": address.lower(),
                "limit": limit
            }
            if condition_id:
                params["market"] = condition_id

            response = self.session.get(f"{self.BASE_URL}/activity", params=params)
            response.raise_for_status()
            activities = response.json()
            logger.info(f"활동 내역 {len(activities)}개 조회 완료")
            return activities
        except requests.exceptions.RequestException as e:
            logger.error(f"활동 내역 조회 실패: {e}")
            return []

    @rate_limit_handler(max_retries=3)
    def get_trades_by_address(
        self,
        address: str,
        limit: int = 1000,
        after_timestamp: Optional[int] = None
    ) -> List[Dict]:
        """Get trade history for an address.

        Args:
            address: Wallet address
            limit: Maximum trades to return
            after_timestamp: Unix timestamp - only return trades after this time

        Returns:
            List of trade dictionaries with price, size, timestamp, etc.
        """
        try:
            params = {
                "maker_address": address.lower(),
                "limit": limit
            }

            response = self.session.get(f"{self.BASE_URL}/trades", params=params)
            response.raise_for_status()
            trades = response.json()

            # Filter by timestamp if specified
            if after_timestamp:
                trades = [
                    t for t in trades
                    if t.get("timestamp", 0) >= after_timestamp
                ]

            logger.info(f"거래 내역 {len(trades)}개 조회 완료")
            return trades
        except requests.exceptions.RequestException as e:
            logger.error(f"거래 내역 조회 실패: {e}")
            return []

    def calculate_pnl_for_period(
        self,
        address: str,
        days_ago: int = 7
    ) -> Dict:
        """Calculate profit/loss for a time period.

        Args:
            address: Wallet address
            days_ago: Number of days to look back

        Returns:
            Dictionary with:
            - realized_pnl: Profit from closed positions
            - unrealized_pnl: Current position values
            - total_pnl: Sum of realized + unrealized
            - num_trades: Number of trades in period
        """
        cutoff_timestamp = int((datetime.now() - timedelta(days=days_ago)).timestamp())

        # Get trades in the period
        trades = self.get_trades_by_address(address, after_timestamp=cutoff_timestamp)

        # Calculate realized P&L from trades
        realized_pnl = 0.0
        for trade in trades:
            # Trade P&L calculation (simplified - actual may be more complex)
            side = trade.get("side", "")
            price = float(trade.get("price", 0))
            size = float(trade.get("size", 0))

            if side.upper() == "SELL":
                realized_pnl += price * size
            elif side.upper() == "BUY":
                realized_pnl -= price * size

        # Get current positions for unrealized P&L
        positions = self.get_positions(address)
        unrealized_pnl = sum(float(pos.get("pnl", 0)) for pos in positions)

        return {
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": realized_pnl + unrealized_pnl,
            "num_trades": len(trades),
            "period_days": days_ago
        }

    def get_portfolio_summary(self, address: str) -> Dict:
        """Get complete portfolio summary for an address.

        Args:
            address: Wallet address

        Returns:
            Dictionary with:
            - positions: List of current positions
            - total_value: Current portfolio value
            - num_positions: Number of open positions
            - pnl_7d: P&L for last 7 days
            - pnl_30d: P&L for last 30 days
        """
        logger.info(f"포트폴리오 요약 생성 중 - address: {address[:10]}...")

        positions = self.get_positions(address)
        total_value = sum(float(pos.get("value", 0)) for pos in positions)

        pnl_7d = self.calculate_pnl_for_period(address, days_ago=7)
        pnl_30d = self.calculate_pnl_for_period(address, days_ago=30)

        summary = {
            "address": address,
            "positions": positions,
            "total_value": total_value,
            "num_positions": len(positions),
            "pnl_7d": pnl_7d,
            "pnl_30d": pnl_30d,
            "timestamp": datetime.now().isoformat()
        }

        logger.info(
            f"포트폴리오 요약 완료 - 포지션: {len(positions)}개, "
            f"총 가치: ${total_value:.2f}, 7d P&L: ${pnl_7d['total_pnl']:.2f}"
        )

        return summary
