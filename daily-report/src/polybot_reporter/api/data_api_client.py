"""Data API client for user positions and portfolio data."""

import csv
import io
import logging
import math
import zipfile
from datetime import datetime, timedelta
from typing import Optional

import requests

from ..contracts import safe_error_message
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
    REQUEST_TIMEOUT = (5, 30)

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
            positions: list[dict] = []
            offset = 0
            page_size = 500
            while True:
                params = {
                    "user": address.lower(),
                    "limit": page_size,
                    "offset": offset,
                    "sizeThreshold": 0,
                }
                response = self.session.get(
                    f"{self.BASE_URL}/positions",
                    params=params,
                    timeout=self.REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                page = response.json()
                if not isinstance(page, list):
                    raise ValueError("positions API 응답이 list가 아닙니다")
                positions.extend(page)
                if len(page) < page_size:
                    break
                offset += page_size
                if offset > 10_000:
                    raise ValueError(
                        "positions API offset 상한(10000)을 넘어 전체 목록을 증명할 수 없습니다"
                    )
            logger.info("포지션 %d개 조회 완료 - address=[REDACTED]", len(positions))
            return positions
        except requests.exceptions.RequestException as e:
            logger.error(
                "포지션 조회 실패 - address=[REDACTED]: %s",
                safe_error_message(e),
            )
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
            response = self.session.get(
                f"{self.BASE_URL}/v1/accounting/snapshot",
                params=params,
                timeout=self.REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                for name in zf.namelist():
                    if "equity" in name.lower():
                        with zf.open(name) as f:
                            reader = csv.DictReader(io.TextIOWrapper(f))
                            for row in reader:
                                cash = float(row["cashBalance"])
                                position = float(row["positionsValue"])
                                equity = float(row["equity"])
                                if not all(
                                    math.isfinite(value)
                                    for value in (cash, position, equity)
                                ):
                                    raise ValueError("equity snapshot 금액이 유한하지 않습니다")
                                if min(cash, position, equity) < 0:
                                    raise ValueError("equity snapshot 금액이 음수입니다")
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
            logger.error(
                "Equity 스냅샷 조회 실패 - address=[REDACTED]: %s",
                safe_error_message(e),
            )
            raise
        except (zipfile.BadZipFile, KeyError, ValueError) as e:
            logger.error("Equity 스냅샷 파싱 실패: %s", safe_error_message(e))
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
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                raise ValueError("activity limit은 0 이상의 integer여야 합니다")
            if limit > 10_000:
                raise ValueError("activity limit은 검증 가능한 상한 10000 이하여야 합니다")
            if limit == 0:
                return []

            activities: list[dict] = []
            offset = 0
            while len(activities) < limit:
                page_size = min(500, limit - len(activities))
                params = {
                    "user": address.lower(),
                    "limit": page_size,
                    "offset": offset,
                }
                if condition_id:
                    params["market"] = condition_id

                response = self.session.get(
                    f"{self.BASE_URL}/activity",
                    params=params,
                    timeout=self.REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                page = response.json()
                if not isinstance(page, list):
                    raise ValueError("activity API 응답이 list가 아닙니다")
                activities.extend(page)
                if len(page) < page_size:
                    break
                offset += page_size
            logger.info(f"활동 내역 {len(activities)}개 조회 완료")
            return activities
        except requests.exceptions.RequestException as e:
            logger.error("활동 내역 조회 실패: %s", safe_error_message(e))
            raise

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
            if limit <= 0:
                raise ValueError("trades limit은 양수여야 합니다")
            requested_limit = min(limit, 10_000)
            if requested_limit != limit:
                logger.warning("trades limit을 API 증명 상한 10000으로 제한합니다")
            trades: list[dict] = []
            offset = 0
            while len(trades) < requested_limit:
                page_size = min(500, requested_limit - len(trades))
                params = {
                    "user": address.lower(),
                    "limit": page_size,
                    "offset": offset,
                }
                if after_timestamp is not None:
                    params["start"] = after_timestamp
                response = self.session.get(
                    f"{self.BASE_URL}/trades",
                    params=params,
                    timeout=self.REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                page = response.json()
                if not isinstance(page, list):
                    raise ValueError("trades API 응답이 list가 아닙니다")
                trades.extend(page)
                if len(page) < page_size:
                    break
                offset += page_size

            logger.info(f"거래 내역 {len(trades)}개 조회 완료")
            return trades
        except requests.exceptions.RequestException as e:
            logger.error("거래 내역 조회 실패: %s", safe_error_message(e))
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
        logger.info("포트폴리오 요약 생성 중 - address=[REDACTED]")

        positions = self.get_positions(address)

        # Value the portfolio from the accounting snapshot — the same authoritative
        # source Polymarket uses for the Portfolio figure. Summing /positions
        # currentValue undercounts resolved-but-unredeemed winners (currentValue=0),
        # which the resolution-momentum strategy accumulates near market resolution.
        equity = self.get_equity_snapshot(address)
        position_value = equity["position_value"]
        cash_balance = equity["cash_balance"]
        # Preserve the snapshot's authoritative equity value and verify its own
        # breakdown. Recomputing total would hide a malformed upstream snapshot.
        total_value = equity["total_value"]
        if abs(total_value - position_value - cash_balance) > 0.02:
            raise ValueError(
                "accounting snapshot equity가 positionsValue + cashBalance와 일치하지 않습니다"
            )

        # 7d/30d P&L is the change in total_value over the window. That needs
        # historical snapshots, which this single-point-in-time client does not
        # have, so the caller fills it from the stored daily snapshots. (Summing
        # per-position realizedPnl+cashPnl is NOT a period figure — it ignores the
        # window entirely and only reflects the currently-held positions.)
        summary = {
            "address": address,
            "positions": positions,
            "position_value": position_value,
            "cash_balance": cash_balance,
            "total_value": total_value,
            "num_positions": len(positions),
            "pnl_7d": {"total_pnl": None},
            "pnl_30d": {"total_pnl": None},
            "timestamp": datetime.now().isoformat(),
        }

        logger.info(
            f"포트폴리오 요약 완료 - 포지션: {len(positions)}개, "
            f"포지션 가치: ${position_value:.2f}, Cash: ${cash_balance:.2f}, "
            f"총 가치: ${total_value:.2f}"
        )

        return summary
