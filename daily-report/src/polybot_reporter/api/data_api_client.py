"""Data API client for user positions and portfolio data."""

import csv
import io
import logging
import zipfile
from datetime import datetime, timedelta
from typing import Optional

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
        self.session.headers.update(
            {"Accept": "application/json", "User-Agent": "GoldenApple-PolyBot/1.0"}
        )

    @rate_limit_handler(max_retries=3)
    def get_positions(self, address: str) -> list[dict]:
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
            params = {"user": address.lower()}
            response = self.session.get(f"{self.BASE_URL}/positions", params=params)
            response.raise_for_status()
            positions = response.json()
            logger.info(f"포지션 {len(positions)}개 조회 완료 - address: {address[:10]}...")
            return positions
        except requests.exceptions.RequestException as e:
            logger.error(f"포지션 조회 실패 - address: {address}: {e}")
            raise

    @rate_limit_handler(max_retries=3)
    def get_equity_snapshot(self, address: str) -> dict:
        """Get the authoritative equity snapshot for a wallet address.

        Uses the accounting snapshot endpoint, which returns a ZIP containing
        equity.csv with ``cashBalance``, ``positionsValue`` and ``equity``. This
        is the same source Polymarket uses for the account's Portfolio value, so
        the reporter always agrees with Polymarket — including resolved positions
        that are not yet redeemed, which the /positions endpoint can report with a
        ``currentValue`` of 0.

        Args:
            address: Wallet address

        Returns:
            Dict with ``cash_balance``, ``position_value`` and ``total_value`` (USDC).
        """
        try:
            params = {"user": address.lower()}
            response = self.session.get(f"{self.BASE_URL}/v1/accounting/snapshot", params=params)
            response.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                for name in zf.namelist():
                    if "equity" in name.lower():
                        with zf.open(name) as f:
                            reader = csv.DictReader(io.TextIOWrapper(f))
                            for row in reader:
                                cash = float(row.get("cashBalance", 0))
                                position = float(row.get("positionsValue", 0))
                                equity = float(row.get("equity", cash + position))
                                logger.info(
                                    f"Equity 스냅샷 조회 완료 - Position: ${position:.2f}, "
                                    f"Cash: ${cash:.2f}, Equity: ${equity:.2f}"
                                )
                                return {
                                    "cash_balance": cash,
                                    "position_value": position,
                                    "total_value": equity,
                                }
            raise ValueError("accounting snapshot에서 equity 행을 찾지 못했습니다")
        except requests.exceptions.RequestException as e:
            logger.error(f"Equity 스냅샷 조회 실패 - address: {address}: {e}")
            raise
        except (zipfile.BadZipFile, KeyError, ValueError) as e:
            logger.error(f"Equity 스냅샷 파싱 실패: {e}")
            raise

    def get_cash_balance(self, address: str) -> float:
        """Get USDC cash balance for a wallet address.

        Thin wrapper over :meth:`get_equity_snapshot`, kept for callers that only
        need cash.
        """
        return self.get_equity_snapshot(address)["cash_balance"]

    @rate_limit_handler(max_retries=3)
    def get_activity(
        self, address: str, limit: int = 100, condition_id: Optional[str] = None
    ) -> list[dict]:
        """Get user activity history (trades, deposits, withdrawals).

        Args:
            address: Wallet address
            limit: Maximum number of activities to return
            condition_id: Optional filter for specific market

        Returns:
            List of activity dictionaries sorted by timestamp (newest first)
        """
        try:
            params = {"address": address.lower(), "limit": limit}
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
        self, address: str, limit: int = 1000, after_timestamp: Optional[int] = None
    ) -> list[dict]:
        """Get trade history for an address.

        Args:
            address: Wallet address
            limit: Maximum trades to return
            after_timestamp: Unix timestamp - only return trades after this time

        Returns:
            List of trade dictionaries with price, size, timestamp, etc.
        """
        try:
            params = {"maker_address": address.lower(), "limit": limit}

            response = self.session.get(f"{self.BASE_URL}/trades", params=params)
            response.raise_for_status()
            trades = response.json()

            # Filter by timestamp if specified
            if after_timestamp:
                trades = [t for t in trades if t.get("timestamp", 0) >= after_timestamp]

            logger.info(f"거래 내역 {len(trades)}개 조회 완료")
            return trades
        except requests.exceptions.RequestException as e:
            logger.error(f"거래 내역 조회 실패: {e}")
            raise

    def calculate_pnl_for_period(self, address: str, days_ago: int = 7) -> dict:
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
        # Get current positions - API provides P&L fields directly
        positions = self.get_positions(address)

        realized_pnl = sum(float(pos.get("realizedPnl", 0)) for pos in positions)
        unrealized_pnl = sum(float(pos.get("cashPnl", 0)) for pos in positions)

        # Get trade count for the period
        cutoff_timestamp = int((datetime.now() - timedelta(days=days_ago)).timestamp())
        trades = self.get_trades_by_address(address, after_timestamp=cutoff_timestamp)

        return {
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": realized_pnl + unrealized_pnl,
            "num_trades": len(trades),
            "period_days": days_ago,
        }

    def get_portfolio_summary(self, address: str) -> dict:
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

        # Value the portfolio from the accounting snapshot — the same authoritative
        # source Polymarket uses for the Portfolio figure. Summing /positions
        # currentValue undercounts resolved-but-unredeemed winners (currentValue=0),
        # which the resolution-momentum strategy accumulates near market resolution.
        equity = self.get_equity_snapshot(address)
        position_value = equity["position_value"]
        cash_balance = equity["cash_balance"]
        # Keep total = position + cash so the Supabase writer's consistency guard
        # (|total - position - cash| <= 0.02) always holds; this equals the
        # snapshot's own equity because positionsValue + cashBalance == equity.
        total_value = position_value + cash_balance

        pnl_7d = self.calculate_pnl_for_period(address, days_ago=7)
        pnl_30d = self.calculate_pnl_for_period(address, days_ago=30)

        summary = {
            "address": address,
            "positions": positions,
            "position_value": position_value,
            "cash_balance": cash_balance,
            "total_value": total_value,
            "num_positions": len(positions),
            "pnl_7d": pnl_7d,
            "pnl_30d": pnl_30d,
            "timestamp": datetime.now().isoformat(),
        }

        logger.info(
            f"포트폴리오 요약 완료 - 포지션: {len(positions)}개, "
            f"포지션 가치: ${position_value:.2f}, Cash: ${cash_balance:.2f}, "
            f"총 가치: ${total_value:.2f}, 7d P&L: ${pnl_7d['total_pnl']:.2f}"
        )

        return summary
