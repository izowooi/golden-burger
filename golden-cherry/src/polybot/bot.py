"""Main bot orchestrator with resolution momentum strategy."""
import logging
from .config import BotConfig
from .api.gamma_client import GammaClient
from .api.clob_client import ClobClientWrapper
from .strategy.scanner import MarketScanner
from .strategy.trader import Trader
from .db.models import init_database
from .db.repository import TradeRepository

logger = logging.getLogger(__name__)


class PolymarketBot:
    """Polymarket automated trading bot with resolution momentum strategy.

    Orchestrates the trading cycle:
    1. Check existing positions for sell conditions
    2. Scan markets for buy candidates (time-based filter)
    3. Execute buys for qualifying candidates
    """

    def __init__(self, config: BotConfig):
        """Initialize bot with configuration.

        Args:
            config: Bot configuration
        """
        self.config = config

        # Initialize database
        self.Session = init_database(str(config.db_path))

        # Initialize API clients
        self.gamma = GammaClient()
        self.clob = ClobClientWrapper(config.api, config.simulation_mode)

        logger.info(
            f"Bot 초기화 완료 - Job: {config.job_name}, "
            f"Simulation: {config.simulation_mode}, "
            f"Time-based: {config.trading.time_based.enabled}"
        )

    def run_cycle(self) -> dict:
        """Execute one trading cycle.

        Returns:
            Dictionary with cycle statistics:
            - checked_holdings: int
            - sold: int
            - buy_candidates: int
            - bought: int
        """
        session = self.Session()
        repo = TradeRepository(session)

        # Scanner for finding candidates
        scanner = MarketScanner(self.gamma, self.config.trading, repo)
        trader = Trader(repo, self.clob, self.config.trading)

        stats = {
            "checked_holdings": 0,
            "sold": 0,
            "buy_candidates": 0,
            "bought": 0,
        }

        try:
            # Log strategy configuration at cycle start
            time_cfg = self.config.trading.time_based
            if time_cfg.enabled:
                logger.info(
                    f"Resolution Momentum 전략 - "
                    f"진입: {time_cfg.entry_hours_min}h < 해결시간 <= {time_cfg.entry_hours_max}h, "
                    f"확률: {self.config.trading.buy_threshold:.0%} ~ {self.config.trading.sell_threshold:.0%}, "
                    f"청산: 해결 {time_cfg.exit_hours}h 전"
                )
                logger.info(
                    f"손익 설정 - 손절: {self.config.trading.stop_loss_percent:.0%}, "
                    f"익절: {self.config.trading.take_profit_percent:.0%}, "
                    f"트레일링: {self.config.trading.trailing_stop.percent:.0%}"
                )
            else:
                logger.info("시간 기반 필터 비활성화 (확률 조건만 사용)")

            # Phase 1: Check and sell holdings
            logger.info("=== Phase 1: 보유 포지션 매도 확인 ===")
            holdings = repo.get_holding_trades()
            stats["checked_holdings"] = len(holdings)

            for trade in holdings:
                if trader.execute_sell(trade):
                    stats["sold"] += 1

            # Phase 2: Scan for buy candidates
            logger.info("=== Phase 2: 매수 후보 스캔 ===")
            candidates = scanner.scan_buy_candidates()
            stats["buy_candidates"] = len(candidates)

            # Phase 3: Execute buys
            logger.info("=== Phase 3: 매수 실행 ===")
            for candidate in candidates:
                # Skip if already traded
                if repo.is_already_traded(candidate["condition_id"]):
                    logger.info(f"이미 거래한 시장 skip: {candidate['condition_id']}")
                    continue

                if trader.execute_buy(candidate):
                    stats["bought"] += 1

            # Log statistics
            db_stats = repo.get_stats()
            logger.info(f"=== 사이클 완료 ===")
            logger.info(f"보유 포지션 확인: {stats['checked_holdings']}개")
            logger.info(f"매도: {stats['sold']}건")
            logger.info(f"매수 후보: {stats['buy_candidates']}개")
            logger.info(f"매수: {stats['bought']}건")
            logger.info(f"총 포지션: {db_stats['holding']}개")
            logger.info(f"총 P&L: ${db_stats['total_pnl']:.4f}")

            return stats

        finally:
            session.close()

    def run(self):
        """Run a single trading cycle (for Jenkins)."""
        logger.info(f"트레이딩 사이클 시작 - {self.config.job_name}")

        try:
            stats = self.run_cycle()
            logger.info(f"사이클 성공적으로 완료: {stats}")
        except Exception as e:
            logger.exception(f"사이클 실패: {e}")
            raise

    def get_status(self) -> dict:
        """Get current bot status and statistics.

        Returns:
            Status dictionary
        """
        session = self.Session()
        repo = TradeRepository(session)

        try:
            stats = repo.get_stats()
            holdings = repo.get_holding_trades()

            return {
                "job_name": self.config.job_name,
                "simulation_mode": self.config.simulation_mode,
                "db_path": str(self.config.db_path),
                "statistics": stats,
                "holdings": [
                    {
                        "id": t.id,
                        "condition_id": t.condition_id,
                        "question": t.question[:50] + "..." if len(t.question) > 50 else t.question,
                        "outcome": t.outcome,
                        "buy_price": t.buy_price,
                        "buy_amount": t.buy_amount,
                        "buy_timestamp": t.buy_timestamp.isoformat() if t.buy_timestamp else None,
                        "entry_reason": t.entry_reason,
                        "max_price": t.max_price,
                        "market_end_date": t.market_end_date.isoformat() if t.market_end_date else None,
                    }
                    for t in holdings
                ],
                "config": {
                    "buy_threshold": self.config.trading.buy_threshold,
                    "sell_threshold": self.config.trading.sell_threshold,
                    "buy_amount_usdc": self.config.trading.buy_amount_usdc,
                    "min_liquidity": self.config.trading.min_liquidity,
                    "max_positions": self.config.trading.max_positions,
                    "take_profit_percent": self.config.trading.take_profit_percent,
                    "stop_loss_percent": self.config.trading.stop_loss_percent,
                    "trailing_stop_enabled": self.config.trading.trailing_stop.enabled,
                    "trailing_stop_percent": self.config.trading.trailing_stop.percent,
                    "time_based_enabled": self.config.trading.time_based.enabled,
                    "entry_hours_max": self.config.trading.time_based.entry_hours_max,
                    "entry_hours_min": self.config.trading.time_based.entry_hours_min,
                    "exit_hours": self.config.trading.time_based.exit_hours,
                },
            }
        finally:
            session.close()
